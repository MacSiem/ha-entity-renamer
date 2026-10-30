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
import inspect
import json
import re
from typing import Callable, Protocol


class Refused(Exception):
    """Invalid/incomplete/stale request, or lost authorization/lifecycle."""


class JournalFailure(Exception):
    """Durability is uncertain: caller must read status before doing anything."""


class Position(str, Enum):
    BEFORE = "before"  # Definitively settled: no in-flight effect remains.
    AFTER = "after"  # Identity, expected revision AND content verified.
    # Exact native listener subset; not a baseline, never initial authorization.
    NATIVE_PARTIAL = "native_partial"
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
    # Explicit preceding steps whose native listeners may satisfy all or a
    # checked subset of this step. AFTER still requires the adapter's independent durable/runtime proof.
    caused_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    plan_id: str
    required_scopes: tuple[str, ...]
    covered_scopes: tuple[str, ...]
    steps: tuple[Step, ...]
    # Nonempty only after ALL adapters prove enumeration, validation and backups.
    coverage_evidence: str
    verification_adapters: tuple[str, ...]
    # Immutable private batch baseline: prepared ID, payload digest, codec digest.
    # Empty on legacy plans; never enrich an already journaled plan in place.
    native_inputs: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        from dataclasses import asdict
        payload = asdict(self)
        if self.native_inputs == ():
            payload.pop('native_inputs')  # Preserve all legacy fingerprints.
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def validate(self) -> None:
        if self.native_inputs != ():
            validate_native_input_ref(self.native_inputs)
        elif type(self.native_inputs) is not tuple:
            raise Refused('Invalid native input reference')
        if (not self.plan_id or not self.coverage_evidence or not self.required_scopes
                or set(self.required_scopes) != set(self.covered_scopes)
                or not self.verification_adapters
                or not {s.adapter for s in self.steps}.issubset(self.verification_adapters)
                or not self.steps or len({s.key for s in self.steps}) != len(self.steps)):
            raise Refused("Incomplete plan coverage")
        preceding = set()
        for step in self.steps:
            if not all((step.key, step.adapter, step.identity, step.before_revision,
                        step.after_revision, step.prepared_ref)):
                raise Refused("Incomplete prepared step")
            if (type(step.caused_by) is not tuple or any(type(k) is not str for k in step.caused_by)
                    or len(set(step.caused_by)) != len(step.caused_by)
                    or not set(step.caused_by) <= preceding):
                raise Refused("Invalid native effect dependency")
            preceding.add(step.key)
            if step.before_revision == step.after_revision:
                raise Refused("Step requires distinguishable revisions")


def validate_native_input_ref(reference):
    """Typed nonempty reference; possession is not transition authorization."""
    if (type(reference) is not tuple or len(reference) != 3
            or type(reference[0]) is not str
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', reference[0])
            or any(type(value) is not str or not re.fullmatch(r'[0-9a-f]{64}', value)
                   for value in reference[1:])):
        raise Refused('Invalid native input reference')


@dataclass(frozen=True)
class Record:
    operation_id: str
    plan: Plan
    status: str = "prepared"
    # pending -> intent -> verified; undo_intent -> undone. Intent is unresolved.
    phases: tuple[str, ...] = ()
    reason: str = ""
    direction: str = "forward"


@dataclass(frozen=True)
class PhaseContext:
    """Coordinator-owned read-only snapshot of a successfully durable Record.

    This is not a client credential, standalone transition proof or substitute
    for the shared coordinator/editor locks. Native adapters resolve immutable
    baseline/receipts against this exact plan and phase, never overall status.
    """
    record: Record
    index: int

    def __post_init__(self):
        if (type(self.record) is not Record or type(self.index) is not int
                or not 0 <= self.index < len(self.record.plan.steps)
                or type(self.record.phases) is not tuple
                or len(self.record.phases) != len(self.record.plan.steps)):
            raise Refused('Invalid durable phase context')

    @property
    def step(self):
        return self.record.plan.steps[self.index]

    def require_effect(self, step: Step, direction: str):
        expected = {'forward': 'intent', 'rollback': 'undo_intent'}.get(direction)
        if (expected is None or step != self.step or self.record.direction != direction
                or self.record.phases[self.index] != expected):
            raise Refused('Durable phase does not authorize this effect')


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
        NATIVE_PARTIAL is only an exact, settled listener subset, possibly with
        a delayed save; it is never an initial baseline or arbitrary conflict.
        Never report AFTER using only an ACK or cached expected patch.
        """
    async def apply(self, step: Step, guard: Callable[[], None]) -> None:
        """Atomically guard immutable identity + before revision before writing.

        Call guard after every internal await, immediately before EVERY mutation.
        Must include activation, durable flush and native barriers as needed.
        An exception may mean an effect happened. Do not retry inside adapter.
        """
    async def apply_native(self, step: Step, guard: Callable[[], None]) -> None:
        """Complete an independently inspected exact native-listener subset.

        Called only after verified declared causes and durable intent. Recompare
        the subset at write time; arbitrary UNKNOWN must never enter this path.
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
                 lock: asyncio.Lock | None = None,
                 record_admission: Callable[[str], None] | None = None):
        if not required_scopes:
            raise ValueError("Server must define required migration scope")
        self.required_scopes = frozenset(required_scopes)
        self.adapters = dict(adapters)
        self.journal = journal
        self.guard = guard
        self.lock = lock or asyncio.Lock()
        if record_admission is not None and not callable(record_admission):
            raise ValueError("Server record admission must be callable")
        self.record_admission = record_admission

    def _admit_record(self, direction):
        """Trusted synchronous admission immediately before durable mutation."""
        if self.record_admission is None:
            return
        self.guard()
        result = self.record_admission(direction)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise Refused("Record admission must be synchronous")
        if result is not None:
            raise Refused("Record admission must raise or return None")

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

    def _adapter(self, step: Step, record: Record | None = None,
                 index: int | None = None) -> Adapter:
        try:
            adapter = self.adapters[step.adapter]
        except KeyError as err:
            raise Refused("Missing adapter") from err
        binder = getattr(adapter, 'bind_phase_context', None)
        if binder is not None:
            # Initial preflight has no durable Record. Clear any stale binding
            # when this shared adapter is reused for another operation.
            binder(None if record is None else PhaseContext(record, index))
        return adapter

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
            self._admit_record("forward")
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

    @staticmethod
    def _native_effect_proven(record: Record, step: Step) -> bool:
        verified = {item.key for item, phase in zip(record.plan.steps, record.phases)
                    if phase == "verified"}
        return bool(step.caused_by) and set(step.caused_by) <= verified

    async def _forward(self, record: Record) -> Record:
        # Recovery must validate ALL revisions again before any resumed effect.
        for index, step in enumerate(record.plan.steps):
            expected = Position.AFTER if record.phases[index] == "verified" else Position.BEFORE
            position = await self._adapter(step, record, index).inspect(step)
            self.guard()
            causal = (record.phases[index] == "pending"
                      and position in (Position.AFTER, Position.NATIVE_PARTIAL)
                      and self._native_effect_proven(record, step))
            if position != expected and not causal:
                return await self._unknown(record, "preflight_revision_conflict")
        for index, step in enumerate(record.plan.steps):
            if record.phases[index] == "verified":
                continue
            adapter = self._adapter(step, record, index)
            position = await adapter.inspect(step)
            self.guard()
            if position == Position.AFTER and self._native_effect_proven(record, step):
                # The initiating step already has a durable intent and verified
                # outcome. Record the observed native result without replaying it.
                record = await self._phase(record, index, "verified")
                continue
            native = (position == Position.NATIVE_PARTIAL
                      and self._native_effect_proven(record, step)
                      and callable(getattr(adapter, "apply_native", None)))
            if position != Position.BEFORE and not native:
                return await self._unknown(record, "revision_conflict")
            record = await self._phase(record, index, "intent")
            self.guard()  # Rechecked AFTER durable intent await, before effect.
            try:
                adapter = self._adapter(step, record, index)
                if native:
                    await adapter.apply_native(step, self.guard)
                else:
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
                    position = await self._adapter(step, record, index).inspect(step)
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
                elif (position == Position.AFTER and (phase in ("intent", "verified")
                      or (phase == "pending" and not undoing and self._native_effect_proven(record, step)))):
                    record = await self._phase(record, index, "verified")
                elif (position == Position.NATIVE_PARTIAL and phase in ("pending", "intent")
                      and not undoing and self._native_effect_proven(record, step)):
                    # Inspection proves the exact listener subset; no uncertain
                    # adapter-owned change is admitted by this position.
                    record = await self._phase(record, index, "pending")
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
            self._admit_record("rollback")
            # Persist recovery direction before compensation, even if its ACK is lost.
            record = await self._save(replace(record, direction="rollback", status="rolling_back"))
            for index in reversed(range(len(record.plan.steps))):
                if record.phases[index] in ("pending", "undone"):
                    continue
                step = record.plan.steps[index]
                adapter = self._adapter(step, record, index)
                position = await adapter.inspect(step)
                self.guard()
                if position != Position.AFTER:
                    return await self._unknown(record, "compensation_revision_conflict")
                record = await self._phase(record, index, "undo_intent", "rolling_back")
                self.guard()
                try:
                    adapter = self._adapter(step, record, index)
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
            for index, step in enumerate(record.plan.steps):
                position = await self._adapter(step, record, index).inspect(step)
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
