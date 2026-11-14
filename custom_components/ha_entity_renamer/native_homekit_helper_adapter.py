"""Build a native ConfigEntry receipt adapter for a qualified stopped bridge.

The caller must durably quiesce and later reactivate the HomeKit lifecycle,
retain the same original source/registry receipt and hold all native locks.
This factory is not an admission path or a replacement for runtime verification.
"""
import asyncio

from .helper_transaction import HelperTransactionAdapter
from .native_helper_transaction import ReceiptedHelperTransactionAdapter
from .transaction import Refused


def create_adapter(root, resolver, binding, lease):
    from .native_helper_activation_lease import NativeHelperActivationLease
    from .native_homekit_yaml_binding import HomeKitYAMLBinding
    if not (type(binding) is HomeKitYAMLBinding and binding.hass is root.hass
            and binding.receipt == root.receipt and binding.mapping == root.mapping
            and type(lease) is NativeHelperActivationLease
            and lease.entry_ids == tuple(sorted(binding.observer.before))):
        raise Refused('homekit_helper_receipt_binding_required')
    lease.check()
    observer = binding.observer
    observer.bind_activation_lease(lease)
    async def settle():
        async with asyncio.timeout(30):
            await root.hass.async_block_till_done()
        await observer.settle()
        root._scope()
        return True
    helper = HelperTransactionAdapter(root.hass, resolver,
        resolve_step=lambda key: next(step for step in root.plan.steps if step.key == key),
        settle=settle, validate_snapshot=observer.validate_snapshot,
        verify_activation=observer.verify_activation, verify_runtime=observer.verify_runtime,
        verify_inventory=observer.verify_inventory,
        verify_original_inventory=observer.verify_original_inventory)
    async def verify(plan, rollback):
        root._scope(); lease.check()
        if await (observer.verify_original_inventory(plan) if rollback
                  else observer.verify_inventory(plan)) is not True:
            return False
        root._scope(); lease.check()
        return await root._full(rollback)
    return ReceiptedHelperTransactionAdapter(helper, root.chain, root._observe,
        root.journal.load, root._scope, activation_lease=lease,
        full_verify=lambda plan: verify(plan, False),
        full_verify_rollback=lambda plan: verify(plan, True))


def prepare_entries(binding):
    """Produce the exact native ConfigEntry steps from the same bound source."""
    from .helper_plan import snapshot_equal
    from .helper_transaction import prepare_helper
    from .native_homekit_yaml_binding import HomeKitYAMLBinding
    if type(binding) is not HomeKitYAMLBinding:
        raise Refused('homekit_helper_receipt_binding_required')
    result = []
    for entry_id in sorted(binding.observer.before):
        before = binding.observer.before[entry_id]
        change = prepare_helper(before, binding.mapping)
        if not snapshot_equal(change.after, binding.observer.after[entry_id]):
            raise Refused('homekit_helper_candidate_mismatch')
        result.append(change)
    if not result:
        raise Refused('homekit_helper_population_required')
    return tuple(result)
