"""Native dual-key group RestoreSensor escrow under receipted Powercalc unload.

This phase alone cannot rename a registry entry, set up a group, or replay an
unknown write. It copies a native StoredState and exact actual full group total
before target activation, retaining the source alias for a fresh inverse.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

from . import native_restore_state_payload as codec
from .native_input_baseline import _drained, _loads
from .native_registry_receipts import _digest, _event_id
from .powercalc_group_restore import prepare, materialize
from .powercalc_lifecycle import PowercalcLifecycle, _moves
from .powercalc_previous_state_original import _PinnedPSS
from .powercalc_pss_handoff import _idle, _unloads


class PowercalcRestoreHandoffError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise PowercalcRestoreHandoffError('powercalc_restore_handoff_' + reason) from None


def _rows(raw, ids, *, owned_uuid=None):
    result = {name: None for name in ids}
    if raw is None:
        return result
    value = _loads(raw, limit=64 * 1024 * 1024)
    _need(type(value) is dict and set(value) == {'version', 'minor_version', 'key', 'data'}
          and type(value['version']) is int and value['version'] == 1
          and type(value['minor_version']) is int and value['minor_version'] == 1
          and value['key'] == 'core.restore_state' and type(value['data']) is list, 'disk_envelope')
    seen, seen_uuids = set(), set()
    for row in value['data']:
        _need(type(row) is dict and type(row.get('state')) is dict
              and type(row['state'].get('entity_id')) is str, 'disk_row')
        name = row['state']['entity_id']
        _need(name not in seen, 'duplicate_disk_entity')
        seen.add(name)
        uuid = row.get('entity_registry_id')
        if uuid is not None:
            _need(type(uuid) is str and uuid not in seen_uuids, 'duplicate_disk_uuid')
            seen_uuids.add(uuid)
            if owned_uuid is not None and uuid == owned_uuid:
                _need(name in ids, 'foreign_disk_uuid')
        if owned_uuid is not None and name in ids and 'entity_registry_id' in row:
            _need(uuid == owned_uuid, 'foreign_disk_entity')
        if name in result:
            codec._shape(row)
            result[name] = codec._canonical(row)
    return result


def _captured(stored):
    return codec._canonical(codec._capture(stored, codec._native()))


async def async_handoff_group_restore(lifecycle, group_id):
    """Handoff a live unloaded group's exact total under its directional root."""
    from homeassistant.core import CoreState
    from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
    from homeassistant.helpers.storage import Store
    from .native_unload_code_proof import prove_methods, unchanged
    from .native_utility_setup_audit import _SOURCES
    import homeassistant

    _need(type(lifecycle) is PowercalcLifecycle, 'typed_lifecycle_required')
    lifecycle.lease._task_check()
    lifecycle.check()
    lease, chain = lifecycle.lease, lifecycle.chain
    reverse = lifecycle.direction == 'rollback'
    _need(lifecycle.direction in ('forward', 'rollback'), 'handoff_direction')
    matches = [move for move in lifecycle.moves if move.before == group_id]
    _need(len(matches) == 1 and matches[0].before != matches[0].after, 'group_rename_required')
    move = matches[0]
    uuid_mode = chain.original_receipt.version == '2026.11.0'
    group_rows = [group for group in lifecycle.groups if group.entity_id == group_id]
    _need(len(group_rows) == 1, 'group_identity_required')
    group_uuid = group_rows[0].registry_id
    accepted = _unloads(lifecycle)
    terminal = accepted[group_id][2]
    pss_label = 'pss_reverse' if reverse else 'pss_forward'
    pss_key = _event_id([lifecycle.root_reference[0], pss_label, 'receipt'])
    pss = chain.store.read(pss_key)
    _need(pss is not None and pss['digest'] == _digest(pss['payload'])
          and pss['payload'].get('kind') == 'powercalc_' + pss_label + '_receipt'
          and pss['payload'].get('root') == list(lifecycle.root_reference)
          and pss['payload'].get('source_chain') == lease.state.head
          and pss['payload'].get('group_totals', {}).get(group_id) == terminal['full_total'],
          'accepted_pss_handoff_required')
    data = lease.hass.data.get(DATA_RESTORE_STATE)
    _need(type(data) is RestoreStateData and data.hass is lease.hass and type(data.store) is Store
          and data.store.hass is lease.hass and data.store.key == 'core.restore_state'
          and data.store.version == 1 and data.store.minor_version == 1
          and Path(data.store.path) == Path(lease.hass.config.path('.storage', 'core.restore_state'))
          and lease.hass.state not in (CoreState.stopping, CoreState.final_write, CoreState.not_running)
          and not data.store._read_only and not data.store._load_empty
          and type(data.store._write_lock) is asyncio.Lock, 'native_store_identity')
    # The original target remains as a retained alias after forward. Only the
    # exact accepted alias may be overwritten during a live inverse lifecycle.
    retained = None
    retained_body = None
    if reverse:
        from .powercalc_reverse_admission import PowercalcReverseAdmission
        admission = lease.reverse_admission
        _need(type(admission) is PowercalcReverseAdmission and admission.chain is chain,
              'reverse_admission_required')
        admission._scope()
        forward_key = _event_id([admission.forward.root_reference[0], 'restore_forward', move.after, 'intent'])
        forward = chain.store.read(forward_key)
        _need(forward is not None and forward['digest'] == _digest(forward['payload'])
              and forward['payload'].get('kind') == 'powercalc_group_restore_intent'
              and forward['payload'].get('root') == list(admission.forward.root_reference),
              'forward_restore_alias_required')
        retained_body = codec._canonical(forward['payload']['before'])
        if not uuid_mode:
            retained = data.last_states.get(move.after)
    def target_current():
        if uuid_mode:
            from .native_restore_uuid import owned_slot
            item = owned_slot(data, move.before, move.after, group_uuid, required=True)
            return item.state.entity_id == move.before
        if reverse:
            return (retained is not None and data.last_states.get(move.after) is retained
                    and _captured(retained) == retained_body)
        return move.after not in data.last_states
    # _idle expects a vendor owner with .store, which RestoreStateData supplies.
    _need(_idle(data) and move.before not in data.entities and move.after not in data.entities
          and (uuid_mode or move.before in data.last_states) and target_current()
          and lease.hass.states.get(move.after) is None, 'inactive_source_and_target_required')
    if uuid_mode:
        from .native_restore_uuid import owned_slot
        source = owned_slot(data, move.before, move.after, group_uuid, required=True)
    else:
        source = data.last_states[move.before]
    payload = prepare(source, move.after, full_total=terminal['full_total'],
                      precision=terminal['precision'], unit=terminal['unit'])
    native = codec._native()
    _need(_captured(source) == payload.before_json, 'native_source_changed')
    def source_current():
        if uuid_mode:
            return (owned_slot(data, move.before, move.after, group_uuid, required=True) is source
                    and _captured(source) == payload.before_json)
        return data.last_states.get(move.before) is source and _captured(source) == payload.before_json
    refs = ((RestoreStateData, 'async_dump_states'), (RestoreStateData, 'async_get_stored_states'),
            (Store, 'async_save'), (Store, '_async_handle_write_data'), (Store, '_async_write_data'))
    pins = dict(_SOURCES[chain.original_receipt.version])
    def prove():
        return prove_methods(Path(homeassistant.__file__).resolve().parent, pins, refs)
    proof = await _drained(lease.hass.async_add_executor_job, prove)
    lifecycle.check()
    _need(_unloads(lifecycle) == accepted and _idle(data)
          and source_current()
          and target_current() and move.after not in data.entities,
          'preflight_changed')
    pinned = _PinnedPSS(chain.original_receipt, key='core.restore_state',
                        input_state=lease.state if reverse else None)
    try:
        raw, identity = await _drained(lease.hass.async_add_executor_job, pinned.capture)
        lifecycle.check()
        disk = _rows(raw, (move.before, move.after), owned_uuid=group_uuid if uuid_mode else None)
        _need(pinned.root_sig[:3] == lifecycle.composition.previous.root_identity
              and pinned.storage_sig[:3] == lifecycle.composition.previous.storage_identity
              and disk[move.after] == (retained_body if reverse and not uuid_mode else None) and _idle(data)
              and source_current(),
              'preimage_changed')
    finally:
        pinned.close()
    free = [index for index in range(6) if sys.monitoring.get_tool(index) is None]
    _need(len(free) >= 2, 'dump_observer_unavailable')
    label = 'restore_reverse' if reverse else 'restore_forward'
    key = _event_id([lifecycle.root_reference[0], label, group_id, 'intent'])
    intent = {'kind': 'powercalc_group_restore_reverse_intent' if reverse else 'powercalc_group_restore_intent', 'schema': 2 if uuid_mode else 1,
              'root': list(lifecycle.root_reference), 'source_chain': lease.state.head,
              'unload': [accepted[group_id][0], accepted[group_id][1]], 'pss': [pss_key, pss['digest']],
              'before': codec._parse(payload.before_json), 'after': codec._parse(payload.after_json),
              'full_total': payload.full_total, 'precision': payload.precision, 'unit': payload.unit,
              'disk_before_sha256': None if raw is None else hashlib.sha256(raw).hexdigest(),
              'disk_before_identity': None if identity is None else list(identity)}
    ref = await chain.store.create(key, intent)
    lifecycle.check()
    _need(_unloads(lifecycle) == accepted and _idle(data)
          and source_current()
          and target_current() and move.after not in data.entities,
          'post_intent_memory_changed')
    pinned = _PinnedPSS(chain.original_receipt, key='core.restore_state',
                        input_state=lease.state if reverse else None)
    try:
        reread, observed_identity = await _drained(lease.hass.async_add_executor_job, pinned.capture)
        lifecycle.check()
        _need(reread == raw and observed_identity == identity and _idle(data)
              and source_current()
              and target_current(), 'post_intent_disk_changed')
    finally:
        pinned.close()
    clone = materialize(payload)
    lifecycle.check()
    _need(all(sys.monitoring.get_tool(index) is None for index in free[:2]) and _idle(data)
          and source_current() and target_current(),
          'final_preimage_changed')
    if uuid_mode:
        _need(clone.entity_registry_id == group_uuid, 'restore_uuid_payload_changed')
        data.last_states_by_entity_registry_id[group_uuid] = clone
    else:
        data.last_states[move.after] = clone
    def effect_current():
        if uuid_mode:
            return (owned_slot(data, move.before, move.after, group_uuid, required=True) is clone
                    and _captured(clone) == payload.after_json)
        return (source_current() and data.last_states.get(move.after) is clone
                and _captured(clone) == payload.after_json)
    code = RestoreStateData.async_dump_states.__code__
    tool, starts, returns, contamination, task = free[0], [], [], [], None
    sys.monitoring.use_tool_id(tool, 'renamer.powercalc_group_restore')
    def started(code_, offset):
        frame = sys._getframe(1)
        if frame.f_locals.get('self') is data:
            current = asyncio.current_task(); starts.append(current)
            if current is not task:
                contamination.append('foreign_dump')
    def returned(code_, offset, result):
        frame = sys._getframe(1)
        if frame.f_locals.get('self') is data:
            returns.append(asyncio.current_task())
    from .native_restore_dump_receipt import DumpReceipt
    save_receipt = DumpReceipt(data.store)
    save_receipt.__enter__()
    try:
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
        sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
        task = asyncio.create_task(data.async_dump_states())
        save_receipt.task = task
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if task.done() and not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        task.result()
        lifecycle.check()
        _need(starts == [task] and returns == [task] and not contamination and _idle(data)
              and effect_current(),
              'native_dump_or_memory_unknown')
        readback = _PinnedPSS(chain.original_receipt, key='core.restore_state',
                              input_state=lease.state if reverse else None)
        try:
            actual, actual_identity = await _drained(lease.hass.async_add_executor_job, readback.capture)
            lifecycle.check()
            save_receipt.verify(actual)
            _need(readback.root_sig[:3] == lifecycle.composition.previous.root_identity
                  and readback.storage_sig[:3] == lifecycle.composition.previous.storage_identity
                  and _rows(actual, (move.before, move.after), owned_uuid=group_uuid if uuid_mode else None) ==
                      {move.before: None if uuid_mode else payload.before_json, move.after: payload.after_json}
                  and _idle(data) and not contamination, 'native_disk_readback_unknown')
            await _drained(lease.hass.async_add_executor_job, readback.verify)
            lifecycle.check()
            _need(not contamination and _idle(data) and effect_current(),
                  'native_disk_changed')
        finally:
            readback.close()
        unchanged(refs, *proof)
    finally:
        save_receipt.__exit__(None, None, None)
        sys.monitoring.set_local_events(tool, code, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
        sys.monitoring.free_tool_id(tool)
    key = _event_id([lifecycle.root_reference[0], label, group_id, 'receipt'])
    receipt = {'kind': 'powercalc_group_restore_reverse_receipt' if reverse else 'powercalc_group_restore_receipt', 'schema': 2 if uuid_mode else 1,
               'root': list(lifecycle.root_reference), 'intent': list(ref),
               'source_chain': lease.state.head, 'unload': [accepted[group_id][0], accepted[group_id][1]],
               'pss': [pss_key, pss['digest']], 'before_sha256': hashlib.sha256(payload.before_json).hexdigest(),
               'after_sha256': hashlib.sha256(payload.after_json).hexdigest(), 'full_total': payload.full_total,
               'disk_after_sha256': hashlib.sha256(actual).hexdigest(), 'disk_after_identity': list(actual_identity)}
    result_ref = await chain.store.create(key, receipt)
    lifecycle.check()
    _need(chain.store.read(key) == {'digest': _digest(receipt), 'payload': receipt}, 'receipt_readback')
    return result_ref
