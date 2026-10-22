"""Serialized, recoverable coordination; adapters supply real guarded effects.

This is a saga, not a distributed atomic transaction. Never persist private
configuration in telemetry. Journal implementations must durably and atomically
replace complete records and enforce exclusive creation by operation_id.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from typing import Callable, Protocol


class Refused(Exception):
    """Invalid/incomplete/stale request, or lost authorization/lifecycle."""


class JournalFailure(Exception):
    """Durability is uncertain: caller must read status before doing anything."""


class Position(str, Enum):
    BEFORE = "before"  # Definitively settled: no in-flight effect remains.
    AFTER = "after"  # Identity, expected revision AND content verified.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Step:
    key: str
    adapter: str
    identity: str
    before_revision: str
    after_revision: str
    # Opaque LOCAL durable prepared patch/backup reference, never telemetry.
    prepared_ref: str


@dataclass(frozen=True)
class Plan:
    plan_id: str
    required_scopes: tuple[str, ...]
    covered_scopes: tuple[str, ...]
    steps: tuple[Step, ...]
    # Nonempty only after ALL adapters prove enumeration, validation and backups.
    coverage_evidence: str
    verification_adapters: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        from dataclasses import asdict
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def validate(self) -> None:
        if (not self.plan_id or not self.coverage_evidence or not self.required_scopes
                or set(self.required_scopes) != set(self.covered_scopes)
                or not self.verification_adapters
                or not {s.adapter for s in self.steps}.issubset(self.verification_adapters)
                or not self.steps or len({s.key for s in self.steps}) != len(self.steps)):
            raise Refused("Incomplete plan coverage")
        for step in self.steps:
            if not all((step.key, step.adapter, step.identity, step.before_revision,
                        step.after_revision, step.prepared_ref)):
                raise Refused("Incomplete prepared step")
            if step.before_revision == step.after_revision:
                raise Refused("Step requires distinguishable revisions")


@dataclass(frozen=True)
class Record:
    operation_id: str
    plan: Plan
    status: str = "prepared"
    # pending -> intent -> verified; undo_intent -> undone. Intent is unresolved.
    phases: tuple[str, ...] = ()
    reason: str = ""
    direction: str = "forward"


class Journal(Protocol):
    async def load(self, operation_id: str) -> Record | None: ...
    async def active(self) -> tuple[Record, ...]:
        """All records except durably terminal applied/rolled_back operations."""
    async def create(self, record: Record) -> None:
        """Atomic durable exclusive create; duplicate IDs must raise."""
    async def save(self, record: Record) -> None:
        """Atomic durable replace; success means disk barrier has completed."""


class Adapter(Protocol):
    async def inspect(self, step: Step) -> Position:
        """Independent identity/revision/content read, including durable/runtime state.

        BEFORE/AFTER must also prove no outstanding native listener/write remains.
        Never report AFTER using only an ACK or cached expected patch.
        """
    async def apply(self, step: Step, guard: Callable[[], None]) -> None:
        """Atomically guard immutable identity + before revision before writing.

        Call guard after every internal await, immediately before EVERY mutation.
        Must include activation, durable flush and native barriers as needed.
        An exception may mean an effect happened. Do not retry inside adapter.
        """
    async def compensate(self, step: Step, guard: Callable[[], None]) -> None:
        """Guard identity + after revision at actual write; preserve foreign edits.

        Call guard after every internal await immediately before every mutation.
        """
    async def verify_rollback(self, plan: Plan) -> bool:
        """Prove restored configuration/runtime and preserved historical continuity."""
    async def verify(self, plan: Plan) -> bool:
        """Independent full-scope postcondition, continuity and durability proof."""


class Coordinator:
    """Use ONE shared lock/coordinator per runtime, including recovery/rollback.

    Multi-process use additionally requires an external exclusive lease spanning
    every call. Durable IDs alone do not prevent cross-process execution.
    Guard is synchronous, trusted, and raises when admin or lifecycle is invalid.
    """

    def __init__(self, adapters: dict[str, Adapter], journal: Journal,
                 guard: Callable[[], None], *, required_scopes: tuple[str, ...],
                 lock: asyncio.Lock | None = None):
        if not required_scopes:
            raise ValueError("Server must define required migration scope")
        self.required_scopes = frozenset(required_scopes)
        self.adapters = dict(adapters)
        self.journal = journal
        self.guard = guard
        self.lock = lock or asyncio.Lock()

    async def _load(self, operation_id: str) -> Record | None:
        self.guard()
        result = await self.journal.load(operation_id)
        self.guard()
        return result

    async def _save(self, record: Record) -> Record:
        self.guard()
        try:
            await self.journal.save(record)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise JournalFailure("Read durable status; do not retry effects") from err
        self.guard()
        return record

    async def status(self, operation_id: str) -> Record | None:
        async with self.lock:
            return await self._load(operation_id)

    def _adapter(self, step: Step) -> Adapter:
        try:
            return self.adapters[step.adapter]
        except KeyError as err:
            raise Refused("Missing adapter") from err

    async def apply(self, plan: Plan, fingerprint: str, operation_id: str) -> Record:
        async with self.lock:
            self.guard()
            if not operation_id or plan.fingerprint != fingerprint:
                raise Refused("Invalid operation ID/fingerprint")
            existing = await self._load(operation_id)
            if existing:
                if existing.plan.fingerprint != fingerprint:
                    raise Refused("Operation ID already bound to another plan")
                return existing  # NO replay, even after restart or lost response.
            active = await self.journal.active()
            self.guard()
            if active:
                raise Refused("Finish or reconcile existing operation first")
            plan.validate()
            if set(plan.required_scopes) != self.required_scopes:
                raise Refused("Plan omits server-required scope")
            if any(name not in self.adapters for name in plan.verification_adapters):
                raise Refused("Missing coverage verifier")
            # Validate EVERY revision/capability before the first mutation.
            for step in plan.steps:
                position = await self._adapter(step).inspect(step)
                self.guard()
                if position != Position.BEFORE:
                    raise Refused("Stale or unverified plan")
            record = Record(operation_id, plan, phases=("pending",) * len(plan.steps))
            self.guard()
            try:
                await self.journal.create(record)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                raise JournalFailure("Read durable status before retry") from err
            self.guard()
            return await self._forward(record)

    async def _phase(self, record: Record, index: int, phase: str,
                     status: str = "applying") -> Record:
        phases = list(record.phases)
        phases[index] = phase
        return await self._save(replace(record, phases=tuple(phases), status=status, reason=""))

    async def _unknown(self, record: Record, reason: str) -> Record:
        return await self._save(replace(record, status="unknown", reason=reason))

    async def _forward(self, record: Record) -> Record:
        # Recovery must validate ALL revisions again before any resumed effect.
        for index, step in enumerate(record.plan.steps):
            expected = Position.AFTER if record.phases[index] == "verified" else Position.BEFORE
            position = await self._adapter(step).inspect(step)
            self.guard()
            if position != expected:
                return await self._unknown(record, "preflight_revision_conflict")
        for index, step in enumerate(record.plan.steps):
            if record.phases[index] == "verified":
                continue
            adapter = self._adapter(step)
            position = await adapter.inspect(step)
            self.guard()
            if position != Position.BEFORE:
                return await self._unknown(record, "revision_conflict")
            record = await self._phase(record, index, "intent")
            self.guard()  # Rechecked AFTER durable intent await, before effect.
            try:
                await adapter.apply(step, self.guard)
                self.guard()
                position = await adapter.inspect(step)
                self.guard()
            except asyncio.CancelledError:
                # Durable intent survives cancellation; recovery must inspect.
                raise
            except Exception:
                return await self._unknown(record, "effect_or_verification_uncertain")
            if position != Position.AFTER:
                return await self._unknown(record, "effect_unverified")
            record = await self._phase(record, index, "verified")
        for name in dict.fromkeys(record.plan.verification_adapters):
            try:
                verified = await self.adapters[name].verify(record.plan)
                self.guard()
            except asyncio.CancelledError:
                raise
            except Exception:
                return await self._unknown(record, "final_verification_uncertain")
            if verified is not True:
                return await self._unknown(record, "final_verification_failed")
        return await self._save(replace(record, status="applied", reason=""))

    async def reconcile(self, operation_id: str) -> Record:
        """Read-only effects reconciliation; NEVER reissues an uncertain effect.

        A settled BEFORE permits explicit resume. AFTER can recover a lost ACK.
        Any unknown/conflict stops. Terminal applied status is not inferred here.
        """
        async with self.lock:
            record = await self._load(operation_id)
            if record is None:
                raise Refused("Unknown operation")
            if record.status in ("applied", "rolled_back"):
                return record
            undoing = record.direction == "rollback"
            for index, step in enumerate(record.plan.steps):
                phase = record.phases[index]
                try:
                    position = await self._adapter(step).inspect(step)
                    self.guard()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return await self._unknown(record, "reconciliation_uncertain")
                if phase == "undo_intent":
                    if position == Position.BEFORE:
                        record = await self._phase(record, index, "undone", "rolling_back")
                    elif position == Position.AFTER:
                        record = await self._phase(record, index, "verified", "rolling_back")
                    else:
                        return await self._unknown(record, "compensation_unresolved")
                elif phase == "undone":
                    if position != Position.BEFORE:
                        return await self._unknown(record, "compensation_conflict")
                elif position == Position.AFTER and phase in ("intent", "verified"):
                    record = await self._phase(record, index, "verified")
                elif position == Position.BEFORE and phase in ("pending", "intent"):
                    record = await self._phase(record, index, "pending")
                else:
                    return await self._unknown(record, "reconciliation_conflict")
            status = "rollback_ready" if undoing else "ready"
            return await self._save(replace(record, status=status, reason=""))

    async def resume(self, operation_id: str) -> Record:
        """Only after reconciliation proved every uncertain effect settled."""
        async with self.lock:
            record = await self._load(operation_id)
            if record is None or record.status != "ready" or record.direction != "forward":
                raise Refused("Reconcile operation before resume")
            return await self._forward(record)

    async def rollback(self, operation_id: str) -> Record:
        async with self.lock:
            record = await self._load(operation_id)
            if record is None:
                raise Refused("Unknown operation")
            if record.status == "rolled_back":
                return record
            if any(p in ("intent", "undo_intent") for p in record.phases):
                raise Refused("Reconcile unresolved effect before compensation")
            # Persist recovery direction before compensation, even if its ACK is lost.
            record = await self._save(replace(record, direction="rollback", status="rolling_back"))
            for index in reversed(range(len(record.plan.steps))):
                if record.phases[index] in ("pending", "undone"):
                    continue
                step = record.plan.steps[index]
                adapter = self._adapter(step)
                position = await adapter.inspect(step)
                self.guard()
                if position != Position.AFTER:
                    return await self._unknown(record, "compensation_revision_conflict")
                record = await self._phase(record, index, "undo_intent", "rolling_back")
                self.guard()
                try:
                    await adapter.compensate(step, self.guard)
                    self.guard()
                    position = await adapter.inspect(step)
                    self.guard()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return await self._unknown(record, "compensation_uncertain")
                if position != Position.BEFORE:
                    return await self._unknown(record, "compensation_unverified")
                record = await self._phase(record, index, "undone", "rolling_back")
            for step in record.plan.steps:
                position = await self._adapter(step).inspect(step)
                self.guard()
                if position != Position.BEFORE:
                    return await self._unknown(record, "rollback_final_conflict")
            for name in dict.fromkeys(record.plan.verification_adapters):
                try:
                    verified = await self.adapters[name].verify_rollback(record.plan)
                    self.guard()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return await self._unknown(record, "rollback_verification_uncertain")
                if verified is not True:
                    return await self._unknown(record, "rollback_verification_failed")
            return await self._save(replace(record, status="rolled_back", reason=""))
