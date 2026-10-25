"""Prepared native helper saga adapter; no inspection path writes configuration.

Callbacks are trusted installed-runtime implementations, never client input.
No generic fallback infers activation from options or a successful reload ACK.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .helper_native import apply_entry, read_entry
from .helper_plan import plan_helper, rebase_after_listeners, snapshot_equal
from .helper_persistence import NativeHelperPersistence
from .native_registry import PreparedRegistryChange
from .lovelace_native import revision
from .prepared import canonical
from .transaction import Position, Refused


@dataclass(frozen=True)
class PreparedHelper:
    before: dict
    after: dict
    mapping: dict[str, str]

    @property
    def identity(self):
        return revision({'domain': self.before['domain'], 'identity': self.before['identity']})

    @property
    def before_revision(self):
        return revision(self.before)

    @property
    def after_revision(self):
        return revision(self.after)

    def plan(self):
        # Recompute schema scopes and listener paths after resolving private bytes.
        # Never trust serialized paths/changes supplied by a client or stale codec.
        plan = plan_helper(canonical(self.before)[0], canonical(self.mapping)[0])
        if (plan.blocked or not snapshot_equal(plan.after, self.after) or snapshot_equal(self.before, self.after)
                or self.before['domain'] != self.after['domain']
                or not snapshot_equal(self.before['identity'], self.after['identity'])
                or not snapshot_equal(self.before['subentries'], self.after['subentries'])):
            raise Refused('helper_preparation_mismatch')
        return plan


def prepare_helper(before, mapping):
    before, _ = canonical(before)
    mapping, _ = canonical(mapping)
    plan = plan_helper(before, mapping)
    change = PreparedHelper(before, plan.after, mapping)
    change.plan()
    return change


class HelperTransactionAdapter:
    """One adapter per hass, sharing the owner's coordinator lock and persistence.

    settle: read-only bounded listener/reload completion barrier, raises if unknown.
    validate_snapshot: installed HA/Powercalc version + actual domain schema check.
    verify_activation: independently checks loaded dependency configuration and
      immutable helper entities; raises unless exact. Does not require a source
      value to exist (during reverse saga the source ID may still be renamed).
    verify_runtime: independent final source resolution/value/continuity check.
    Inventory verifiers must cover unchanged, disabled, unloaded and subentries.
    Every callback raises on missing evidence; a false return is also rejected.
    """
    def __init__(self, hass, resolver, *, resolve_step, settle, validate_snapshot,
                 verify_activation, verify_runtime, verify_inventory,
                 verify_original_inventory, persistence=None, registry_adapter="registry"):
        self.hass, self.resolver = hass, resolver
        self.resolve_step = resolve_step
        self.registry_adapter = registry_adapter
        self.settle = settle
        self.validate_snapshot = validate_snapshot
        self.verify_activation = verify_activation
        self.verify_runtime = verify_runtime
        self.verify_inventory = verify_inventory
        self.verify_original_inventory = verify_original_inventory
        self.persistence = persistence if persistence is not None else NativeHelperPersistence(hass)

    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        if (type(change) is not PreparedHelper or change.identity != step.identity
                or change.before_revision != step.before_revision
                or change.after_revision != step.after_revision):
            raise Refused('helper_step_mismatch')
        plan = change.plan()
        if step.caused_by:
            listener_map = {}
            for path in plan.listener_paths:
                old, new = change.before, change.after
                for key in path:
                    old, new = old[key], new[key]
                listener_map[old] = new
            proven = {}
            for key in step.caused_by:
                cause_step = self.resolve_step(key)
                cause = self.resolver(cause_step.prepared_ref)
                if (cause_step.key != key or cause_step.adapter != self.registry_adapter
                        or type(cause) is not PreparedRegistryChange
                        or cause.kind != 'entity' or cause.identity != cause_step.identity
                        or cause.before_revision != cause_step.before_revision
                        or cause.after_revision != cause_step.after_revision
                        or not cause.mapping or any(listener_map.get(k) != v
                                                   for k, v in cause.mapping.items())):
                    raise Refused('helper_native_cause_mismatch')
                proven.update(cause.mapping)
            if proven != listener_map:
                raise Refused('helper_native_cause_incomplete')
        return change

    @staticmethod
    async def _proof(callback, *args):
        if await callback(*args) is False:
            raise Refused('helper_callback_rejected')

    async def inspect(self, step, *, guard=None):
        """Read managers, native disk and actual runtime; never flush or reload.

        NATIVE_PARTIAL includes an exact listener result whose native delayed
        save still needs finalization. It is NOT BEFORE or AFTER and cannot pass
        initial preflight. Only a coordinator with verified causes may finalize.
        """
        guard = guard if guard is not None else lambda: None
        guard()
        change = self._change(step)
        try:
            await self._proof(self.settle)
            guard()
            current = read_entry(self.hass, change.before['identity']['entry_id'])
            await self._proof(self.validate_snapshot, current)
            guard()
            await self._proof(self.verify_activation, current)
            guard()
            candidate = False
            if not snapshot_equal(current, change.before):
                try:
                    rebase_after_listeners(change.plan(), current)
                    candidate = True
                except ValueError:
                    pass
            # adapter-owned references must still be BEFORE for a partial result.
            if not any(snapshot_equal(current, value) for value in (change.before, change.after)) and not candidate:
                return Position.UNKNOWN
            durable = await self.persistence.inspect_current(current)
            guard()
            if snapshot_equal(current, change.after):
                await self._proof(self.verify_runtime, current)
                guard()
            if not snapshot_equal(read_entry(self.hass, current['identity']['entry_id']), current):
                return Position.UNKNOWN
            if durable and snapshot_equal(current, change.before):
                return Position.BEFORE
            if durable and snapshot_equal(current, change.after):
                return Position.AFTER
            if candidate:
                return Position.NATIVE_PARTIAL
            return Position.UNKNOWN
        except Exception:
            return Position.UNKNOWN

    async def _write(self, change, guard, *, native=False, reverse=False,
                     before_mutation=None):
        plan = change.plan()
        guard()
        await self._proof(self.settle)
        guard()
        current = read_entry(self.hass, change.before['identity']['entry_id'])
        expected = change.after if reverse else change.before
        if native:
            if not plan.listener_paths or snapshot_equal(current, change.before):
                raise Refused('helper_native_effect_missing')
            rebase_after_listeners(plan, current)
            expected = current
        if not snapshot_equal(current, expected):
            raise Refused('helper_revision_conflict')
        desired = change.before if reverse else change.after
        # An exact reverse compare preserves all foreign edits. Listener paths
        # are NOT a rollback whitelist. Source-value verification is deferred
        # until registry causes have also been compensated.
        from .helper_plan import HelperPlan
        write_plan = HelperPlan(deepcopy(current), deepcopy(desired), (), (), ())

        async def already_journaled(before, after):
            # Coordinator durably records intent before entering this method.
            guard()
            if not snapshot_equal(before, expected) or not snapshot_equal(after, desired):
                raise Refused('helper_intent_mismatch')

        async def checked_validation(snapshot):
            await self._proof(self.validate_snapshot, snapshot)
            guard()

        async def checked_activation(snapshot):
            await self._proof(self.verify_activation, snapshot)
            guard()
            if not reverse:
                await self._proof(self.verify_runtime, snapshot)
                guard()

        async def checked_settle():
            await self._proof(self.settle)
            guard()

        result = await apply_entry(self.hass, write_plan, guard=guard, settle=checked_settle,
            record_intent=already_journaled, verify_runtime=checked_activation,
            validate_snapshot=checked_validation, before_mutation=before_mutation)
        guard()
        await self.persistence.flush(desired, guard)
        guard()
        if not snapshot_equal(read_entry(self.hass, desired['identity']['entry_id']), desired):
            raise Refused('helper_changed_after_flush')
        # Private trusted IO result; never put storage rows into a UI response.
        return result

    async def apply(self, step, guard, *, before_mutation=None):
        return await self._write(self._change(step), guard, before_mutation=before_mutation)

    async def apply_native(self, step, guard):
        """Coordinator-only path after durable intent + verified declared causes."""
        if not step.caused_by:
            raise Refused('helper_missing_native_cause')
        return await self._write(self._change(step), guard, native=True)

    async def compensate(self, step, guard, *, before_mutation=None):
        return await self._write(self._change(step), guard, reverse=True,
                                 before_mutation=before_mutation)

    async def verify(self, plan):
        return await self.verify_inventory(plan)

    async def verify_rollback(self, plan):
        return await self.verify_original_inventory(plan)
