"""Exact inactive group ConfigEntry metadata effect on the existing chain."""
from __future__ import annotations

from .helper_transaction import HelperTransactionAdapter
from .native_helper_transaction import ReceiptedHelperTransactionAdapter
from .powercalc_group_metadata import PreparedPowercalcGroupMetadata, assert_original_group
from .transaction import Refused


class _GroupHelper(HelperTransactionAdapter):
    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        if (type(change) is not PreparedPowercalcGroupMetadata or step.caused_by
                or (change.identity, change.before_revision, change.after_revision)
                != (step.identity, step.before_revision, step.after_revision)):
            raise Refused('powercalc_group_metadata_step')
        change.plan()
        return change


class PowercalcGroupMetadataAdapter(ReceiptedHelperTransactionAdapter):
    def __init__(self, phase, resolver, observer, phase_loader, guard, *, full_verify):
        self.phase = phase
        async def check(*_):
            self._guard()
        helper = _GroupHelper(phase.lease.hass, resolver, resolve_step=lambda _: None,
            settle=check, validate_snapshot=check, verify_activation=check,
            verify_runtime=check, verify_inventory=full_verify,
            verify_original_inventory=lambda _: False)
        super().__init__(helper, phase.lease.chain, observer, phase_loader, guard,
                         full_verify=full_verify, full_verify_rollback=lambda _: False)

    def _guard(self):
        self.authorization_guard()
        # NativeHelperPersistence drains a child task for its actual Store
        # write. The live lifecycle and inherited causal marker remain owned
        # by the one phase; only intent/receipt methods authorize effects.
        self.phase.lifecycle.check()
        self.phase.scope.check()
        if (self.phase.closed or self.phase.owner is None or self.phase.owner.done()
                or self.phase.token is None
                or self.phase.scope.causal.get(None) is not self.phase.scope.marker):
            raise Refused('powercalc_group_metadata_live_owner_required')
        if self.phase.phase != 'sources_inactive':
            raise Refused('powercalc_group_metadata_inactive_required')
        for step in self.chain.plan.steps:
            if step.adapter != 'helpers':
                continue
            change = self._change(step)
            assert_original_group(self.chain, change)
            entry = self.phase.lease.manager.async_get_entry(change.before['identity']['entry_id'])
            if (entry is None or not any(entry is owned for owned in self.phase.lease.entries)
                    or not any(entry.setup_lock is lock for lock in self.phase.lease.owned)
                    or entry.state.value != 'not_loaded' or entry.update_listeners
                    or entry._on_unload or entry._async_cancel_retry_setup is not None
                    or any(not task.done() for task in (*entry._tasks, *entry._background_tasks))):
                raise Refused('powercalc_group_metadata_not_quiescent')

    def _intent_started(self, intent):
        self.phase.lease.track_intent(intent)

    def _receipt_completed(self, reference):
        self.phase.lease.accept_receipt(reference)
