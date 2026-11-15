"""One-shot native Store compensation for a confirmed Powercalc prefix.

This private primitive does not roll back registries, reactivate producers or
grant public recovery eligibility. The caller must finish and verify those
separate phases; an interrupted intent is never replayed.
"""
from __future__ import annotations

import asyncio
import hashlib

from .transaction import Refused


async def restore_prefix_stores(hass, runtime, journal, record, chain, *, guard, refresh):
    from . import Runtime
    from .const import DATA_RUNTIME
    from .journal import DurableJournal
    from .native_registry_receipts import RegistryReceiptChain, _digest, _event_id, _exact_json_equal, _freeze_json
    from .native_input_baseline import _drained, _loads
    from .controlled_powercalc_handoff import (_native_restore_rows, _native_store_write,
                                                _read_pinned, _restore_rows, _semantic)
    from .controlled_powercalc_setup_receipts import validate_handoff
    from .controlled_powercalc_prefix_readback import observe_inactive_prefix
    from .controlled_powercalc_prefix_images import reverse_images, materialize_reverse_images
    from .powercalc_pss_handoff import _idle, _snapshot
    from homeassistant.helpers.restore_state import DATA_RESTORE_STATE

    def need(value):
        if not value:
            raise Refused('controlled_powercalc_prefix_inverse_unverified')

    need(callable(guard) and callable(refresh))
    owner = asyncio.current_task()
    def scope():
        guard()
        need(asyncio.current_task() is owner and type(runtime) is Runtime
             and hass.data.get(DATA_RUNTIME) is runtime and runtime.loaded and not runtime.unloading
             and runtime.operation_lock.locked() and type(journal) is DurableJournal
             and type(chain) is RegistryReceiptChain and not chain.writable and chain.active is None
             and record.restore_attempted is True)
    async def current_record():
        scope()
        need(await journal.load(record.operation_id) == record)
        scope()

    await current_record()
    observed = await observe_inactive_prefix(hass, chain, record, guard=scope)
    await current_record()
    handoff_key = _event_id(['controlled_pc_population', chain.root_id, 'forward', 'receipt'])
    saved = chain.store.read(handoff_key)
    need(saved is not None and saved['digest'] == observed['handoff_digest'])
    handoff = validate_handoff(chain, [handoff_key, saved['digest']], 'forward')
    pss, restore = hass.data['powercalc_group_storage'], hass.data[DATA_RESTORE_STATE]
    current_pss, current_restore = _snapshot(pss), _native_restore_rows(restore)
    previous_pss, previous_restore, clones = reverse_images(handoff, current_pss, current_restore)
    groups, stored = materialize_reverse_images(previous_pss, clones)
    state = chain.reconstruct_inputs()
    need(state.head == observed['head'] and _idle(pss) and _idle(restore))
    def physical():
        state.assert_current(chain.original_receipt)
        return (_read_pinned(chain.original_receipt, 'powercalc_group', state),
                _read_pinned(chain.original_receipt, 'core.restore_state', state))
    pss_before, restore_before = await _drained(hass.async_add_executor_job, physical)
    await current_record()
    need(await observe_inactive_prefix(hass, chain, record, guard=scope) == observed)
    intent_key = _event_id(['controlled_pc_prefix_store_inverse', chain.root_id, 'intent'])
    need(chain.store.read(intent_key) is None)
    payload = {'kind': 'controlled_powercalc_prefix_store_inverse_intent', 'schema': 1,
               'root': chain.root_id, 'operation_id': record.operation_id,
               'fingerprint': record.plan.fingerprint, 'source_head': state.head,
               'handoff': [handoff_key, saved['digest']],
               'pss_before': hashlib.sha256(pss_before[0]).hexdigest(),
               'restore_before': hashlib.sha256(restore_before[0]).hexdigest(),
               'pss_expected': _digest(previous_pss), 'restore_expected': _digest(previous_restore)}
    intent = await chain.store.create(intent_key, payload)
    await current_record()
    await refresh()
    await current_record()
    need(chain.store.read(intent_key) == {'digest': intent[1], 'payload': payload}
         and await observe_inactive_prefix(hass, chain, record, guard=scope) == observed)
    # No await between the final native preimage fence and the bounded in-memory
    # replacement. Foreign groups and Restore UUIDs keep their original objects.
    owned = set(handoff['pss_owned']) | set(handoff['pss_owned_before'])
    need(set(stored) == {
        item['uuid'] for item in handoff['population']})
    for group in owned:
        if group in handoff['pss_owned_before']:
            pss.states[group] = groups[group]
        else:
            pss.states.pop(group, None)
    for uuid, clone in stored.items():
        restore.last_states_by_entity_registry_id[uuid] = clone
    need(_exact_json_equal(_snapshot(pss), _freeze_json(previous_pss))
         and _exact_json_equal(_native_restore_rows(restore), _freeze_json(previous_restore)))
    pss_raw, pss_id, root_id, storage_id = pss_before
    restore_raw, restore_id, root2, storage2 = restore_before
    need(root_id == root2 and storage_id == storage2)
    written_pss = await _native_store_write(hass, pss, 'persist_states', chain.original_receipt,
        state, 'powercalc_group', pss_raw, pss_id, root_id, storage_id, scope)
    await current_record()
    written_restore = await _native_store_write(hass, restore, 'async_dump_states', chain.original_receipt,
        state, 'core.restore_state', restore_raw, restore_id, root_id, storage_id, scope)
    await current_record()
    def final_physical():
        state.assert_current(chain.original_receipt)
        return (_read_pinned(chain.original_receipt, 'powercalc_group', state),
                _read_pinned(chain.original_receipt, 'core.restore_state', state))
    final = await _drained(hass.async_add_executor_job, final_physical)
    await current_record()
    final_again = await _drained(hass.async_add_executor_job, final_physical)
    await current_record()
    native_restore = _native_restore_rows(restore)
    disk_restore = _restore_rows(written_restore)
    need(final == final_again and final[0][0] == written_pss and final[1][0] == written_restore
         and final[0][2:] == final[1][2:] == (root_id, storage_id)
         and _idle(pss) and _idle(restore)
         and _exact_json_equal(_snapshot(pss), _freeze_json(previous_pss))
         and set(native_restore) == set(previous_restore)
         and all(_exact_json_equal(_semantic(native_restore[name]), _freeze_json(_semantic(previous_restore[name])))
                 for name in native_restore)
         and _exact_json_equal(_loads(written_pss)['data'], _freeze_json(previous_pss))
         and set(disk_restore) == set(previous_restore)
         and all(_exact_json_equal(_semantic(disk_restore[name]), _freeze_json(_semantic(previous_restore[name])))
                 for name in disk_restore))
    receipt_key = _event_id(['controlled_pc_prefix_store_inverse', chain.root_id, 'receipt'])
    need(chain.store.read(receipt_key) is None)
    receipt = {'kind': 'controlled_powercalc_prefix_store_inverse_receipt', 'schema': 1,
               'intent': list(intent), 'root': chain.root_id, 'source_head': state.head,
               'pss_after': hashlib.sha256(written_pss).hexdigest(),
               'restore_after': hashlib.sha256(written_restore).hexdigest()}
    result = await chain.store.create(receipt_key, receipt)
    await current_record()
    need(chain.store.read(receipt_key) == {'digest': result[1], 'payload': receipt})
    return result
