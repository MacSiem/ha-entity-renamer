"""Inactive Powercalc metadata effect in the existing receipt chain."""
from __future__ import annotations

from .helper_transaction import HelperTransactionAdapter
from .native_helper_transaction import ReceiptedHelperTransactionAdapter, _require_inactive
from .powercalc_group_metadata import PreparedPowercalcGroupMetadata, assert_original_group
from .transaction import Refused


class _GroupHelper(HelperTransactionAdapter):
    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        if (type(change) is not PreparedPowercalcGroupMetadata or step.caused_by
                or (change.identity, change.before_revision, change.after_revision)
                != (step.identity, step.before_revision, step.after_revision)):
            raise Refused('controlled_powercalc_group_metadata_step')
        change.plan()
        return change


class ControlledPowercalcHelperAdapter(ReceiptedHelperTransactionAdapter):
    """A native ConfigEntry update, guarded by the population handoff."""

    def __init__(self, root, handoff, resolver):
        self.root, self.handoff = root, handoff

        async def guard_only(*_):
            self._guard()

        helper = _GroupHelper(root.hass, resolver, resolve_step=lambda _: None,
            settle=guard_only, validate_snapshot=guard_only, verify_activation=guard_only,
            verify_runtime=guard_only, verify_inventory=lambda _: root._full(False),
            verify_original_inventory=lambda _: root._full(True))
        super().__init__(helper, root.chain, root._observe, root.journal.load, root._scope,
                         full_verify=lambda _: root._full(False),
                         full_verify_rollback=lambda _: root._full(True))
        self.bind_controlled_powercalc(handoff)

    def _guard(self):
        self.authorization_guard()
        # Original review occurs while entries are active.  Before any update,
        # the shared first-step wrapper must have completed the handoff.
        if self.handoff.receipt is None:
            return
        if self.handoff.setup_reference is not None:
            self.handoff.require_active()
            return
        self.handoff.require_inactive()
        for index, step in enumerate(self.chain.plan.steps):
            if step.adapter != 'helpers':
                continue
            change = self.resolver(step.prepared_ref)
            if type(change) is not PreparedPowercalcGroupMetadata:
                continue
            change = self._change(step)
            assert_original_group(self.chain, change)
            entry = self.helper.hass.config_entries.async_get_entry(change.before['identity']['entry_id'])
            if entry is None:
                raise Refused('controlled_powercalc_group_entry_missing')
            _require_inactive(entry)

    async def _effect(self, step, caller_guard, direction):
        if self.handoff.receipt is None:
            raise Refused('controlled_powercalc_handoff_required')
        self._guard()
        return await super()._effect(step, caller_guard, direction)


class ControlledMixedHelperAdapter:
    """Dispatch typed helper steps to their existing receipt adapters."""
    def __init__(self, resolver, powercalc, template):
        self.resolver, self.powercalc, self.template = resolver, powercalc, template
        # Both typed helpers write ConfigEntry receipts on the same PC chain.
        # The native population handoff must therefore accompany either one.
        self.template.bind_controlled_powercalc(self.powercalc.handoff)

    def bind_phase_context(self, context):
        self.powercalc.bind_phase_context(context)
        self.template.bind_phase_context(context)

    def _adapter(self, step):
        from .helper_transaction import PreparedHelper
        change = self.resolver(step.prepared_ref)
        if type(change) is PreparedPowercalcGroupMetadata:
            return self.powercalc
        if type(change) is PreparedHelper and change.before.get('domain') == 'template':
            return self.template
        raise Refused('controlled_mixed_helper_type_unqualified')

    async def inspect(self, step):
        return await self._adapter(step).inspect(step)

    async def apply(self, step, guard):
        return await self._adapter(step).apply(step, guard)

    async def compensate(self, step, guard):
        return await self._adapter(step).compensate(step, guard)

    async def verify(self, plan):
        return await self.powercalc.verify(plan)

    async def verify_rollback(self, plan):
        return await self.powercalc.verify_rollback(plan)
