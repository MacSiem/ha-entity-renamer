"""Read-only qualification of an inactive, receipted Powercalc prefix.

This is not rollback authority. A fresh inverse must separately prove the
registry graph, source identities and every native write it will perform.
"""
from .transaction import Refused


async def observe_inactive_prefix(hass, chain, record, *, guard):
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import async_get_platforms
    from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
    from .controlled_powercalc_handoff import _native_restore_rows, _read_pinned, _restore_rows, _semantic
    from .controlled_powercalc_setup_receipts import validate_handoff
    from .native_input_baseline import _drained, _loads
    from .native_registry_receipts import _event_id, _exact_json_equal, _freeze_json
    from .native_template_prefix_recovery import acknowledged_powercalc_prefix_receipts
    from .powercalc_pss_handoff import _idle, _snapshot
    import hashlib

    def need(value):
        if not value:
            raise Refused('controlled_powercalc_prefix_readback_unverified')

    need(type(hass) is HomeAssistant and callable(guard))
    guard()
    need(acknowledged_powercalc_prefix_receipts(chain, record)
         and chain.original_receipt.version in ('2026.11.0', '2026.11.1'))
    key = _event_id(['controlled_pc_population', chain.root_id, 'forward', 'receipt'])
    stored = chain.store.read(key)
    need(stored is not None)
    handoff = validate_handoff(chain, [key, stored['digest']], 'forward')
    entries = tuple(hass.config_entries.async_entries('powercalc'))
    pss, restore = hass.data.get('powercalc_group_storage'), hass.data.get(DATA_RESTORE_STATE)
    need(pss is not None and type(restore) is RestoreStateData and restore.hass is hass
         and all(entry.disabled_by is not None or entry.state is ConfigEntryState.NOT_LOADED for entry in entries)
         and all(not platform.entities for platform in async_get_platforms(hass, 'powercalc'))
         and _idle(pss) and _idle(restore))
    state = chain.reconstruct_inputs()
    memory_pss, memory_restore = _snapshot(pss), _native_restore_rows(restore)
    def physical():
        state.assert_current(chain.original_receipt)
        return (_read_pinned(chain.original_receipt, 'powercalc_group', state),
                _read_pinned(chain.original_receipt, 'core.restore_state', state))
    first = await _drained(hass.async_add_executor_job, physical)
    guard()
    second = await _drained(hass.async_add_executor_job, physical)
    guard()
    need(first == second and tuple(hass.config_entries.async_entries('powercalc')) == entries
         and _idle(pss) and _idle(restore) and _snapshot(pss) == memory_pss
         and _native_restore_rows(restore) == memory_restore
         and acknowledged_powercalc_prefix_receipts(chain, record))
    (pss_raw, pss_id, root_id, storage_id), (restore_raw, restore_id, root2, storage2) = first
    need(pss_raw is not None and restore_raw is not None and root_id == root2 and storage_id == storage2
         and hashlib.sha256(pss_raw).hexdigest() == handoff['pss_after_sha256']
         and hashlib.sha256(restore_raw).hexdigest() == handoff['restore_after_sha256']
         and list(pss_id) == handoff['pss_after_identity']
         and list(restore_id) == handoff['restore_after_identity']
         and _exact_json_equal(_loads(pss_raw)['data'], _freeze_json(memory_pss)))
    disk_restore = _restore_rows(restore_raw)
    need(set(disk_restore) == set(memory_restore) and all(
        _exact_json_equal(_semantic(disk_restore[name]), _freeze_json(_semantic(memory_restore[name])))
        for name in disk_restore))
    need(all(name in memory_pss and _exact_json_equal(memory_pss[name], _freeze_json(value))
             for name, value in handoff['pss_owned'].items())
         and all(name in memory_restore and _exact_json_equal(_semantic(memory_restore[name]),
                 _freeze_json(_semantic(value))) for name, value in handoff['restore_owned'].items()))
    return {'head': state.head, 'handoff_digest': stored['digest'], 'entries': len(entries)}
