"""Native ConfigEntry update/reload under the same durable ordinary journal."""
from copy import deepcopy
import asyncio

from .helper_native import read_entry
from .helper_plan import snapshot_equal
from .helper_transaction import HelperTransactionAdapter
from .native_helper_transaction import ReceiptedHelperTransactionAdapter
from .transaction import Refused
from .yaml_validation_worker import template_entry_semantic


class ControlledEntryReceiptAdapter(ReceiptedHelperTransactionAdapter):
    """Reuse whole-document intent/receipt without the inactive-only lease.

    Home Assistant owns the ConfigEntry setup lock while async_reload runs.
    The loaded entry is checked before and after each native mutation.
    """
    def _guard(self):
        self.authorization_guard()
        handoff = self._controlled_handoff
        if handoff is not None and handoff.receipt is not None:
            if handoff.setup_reference is None:
                handoff.require_inactive()
            else:
                handoff.require_active()


def create_adapter(root, resolver):
    change = root.entry_change
    entry_id = change.before['identity']['entry_id']
    async def settle():
        root._scope()
        async with asyncio.timeout(30):
            await root.hass.async_block_till_done()
        root._scope()
        return True
    async def validate(snapshot):
        template_entry_semantic(snapshot)
        return True
    async def activation(snapshot):
        root._scope()
        entry = root.hass.config_entries.async_get_entry(entry_id)
        if (entry is not root.entry_witness.entry or entry.state.value != 'loaded'
                or not snapshot_equal(read_entry(root.hass, entry_id), snapshot)):
            raise Refused('controlled_entry_not_loaded_or_changed')
        docs = deepcopy(root.chain.accepted_documents())
        rows = [row for row in docs['core.config_entries']['data']['entries']
                if row['entry_id'] == entry_id]
        if len(rows) != 1:
            raise Refused('controlled_entry_inventory_changed')
        rows[0]['options'] = deepcopy(snapshot['options'])
        root.entry_witness.sample(docs)
        return True
    async def runtime(snapshot):
        return await activation(snapshot)
    async def forward(_):
        return await root._common(False)
    async def reverse(_):
        return await root._common(True)
    helper = HelperTransactionAdapter(root.hass, resolver,
        resolve_step=lambda key: next(step for step in root.plan.steps if step.key == key),
        settle=settle, validate_snapshot=validate, verify_activation=activation,
        verify_runtime=runtime, verify_inventory=forward, verify_original_inventory=reverse,
        controlled_template_reload=True)
    return ControlledEntryReceiptAdapter(helper, root.chain, root._observe, root.journal.load,
        root._scope, full_verify=lambda _: root._full(False), full_verify_rollback=lambda _: root._full(True))
