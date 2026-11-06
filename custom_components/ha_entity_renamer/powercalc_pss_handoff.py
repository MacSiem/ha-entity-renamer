"""Native PSS ID handoff after typed, receipted group unload.

The same RegistryReceiptStore holds intent and receipt. Only an exact live
PowercalcLifecycle may call this phase; a matching disk postimage never repairs
an unresolved intent. Group setup and counter restoration remain separate.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import json
import sys
from pathlib import Path

from .native_input_baseline import _drained, _json, _loads
from .native_registry_receipts import _digest, _event_id, _exact_json_equal, _freeze_json
from .powercalc_lifecycle import PowercalcLifecycle, _moves
from .powercalc_previous_state_original import _PinnedPSS, _disk_shape
from .powercalc_source_binding import PowercalcVendor, _pins
from .native_unload_code_proof import prove_methods, unchanged


class PowercalcPSSHandoffError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise PowercalcPSSHandoffError('powercalc_pss_handoff_' + reason) from None


def _same(left, right):
    return _exact_json_equal(left, _freeze_json(right))


def _snapshot(store):
    from homeassistant.core import State
    from homeassistant.helpers.json import json_bytes
    _need(type(store.states) is dict, 'native_states_required')
    data = {}
    for group, members in store.states.items():
        _need(type(group) is str and type(members) is dict, 'native_group_shape')
        data[group] = {}
        for name, state in members.items():
            _need(type(name) is str and (state is None or type(state) is State
                  and state.entity_id == name), 'native_previous_state')
            data[group][name] = None if state is None else state.json_fragment
    return _loads(json_bytes(data))


def _clone(state, new_id):
    from homeassistant.core import State
    _need(type(state) is State, 'native_clone_required')
    if new_id == state.entity_id:
        # Core updates last_reported on a reported state while its serialized
        # cache may retain the earlier value. Preserve both native observations
        # without sharing the mutable State or its cache across aliases.
        from copy import copy
        result = copy(state)
        result._cache = dict(state._cache)
    else:
        result = State(new_id, state.state, state.attributes, last_changed=state.last_changed,
                       last_updated=state.last_updated, last_reported=state.last_reported,
                       context=state.context)
    expected = dict(state.as_dict())
    expected['entity_id'] = new_id
    actual = result.as_dict()
    _need(actual == expected, 'native_clone_changed_fields:' + ','.join(
        sorted(key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key))))
    _need(result is not state and result.last_reported == state.last_reported,
          'native_clone_live_report_changed')
    return result


def _unloads(lifecycle, *, yaml_hooks=None):
    lifecycle.lease._task_check()
    lifecycle.check()
    # A pending YAML intent is usable only through the actual live writer;
    # persisted pending/unknown intents never become execution authority.
    if yaml_hooks is None:
        lifecycle.chain.reconstruct_inputs()
    else:
        from .powercalc_yaml_adapter import _PowercalcYAMLHooks
        _need(type(yaml_hooks) is _PowercalcYAMLHooks
              and yaml_hooks.adapter.phase.lifecycle is lifecycle
              and yaml_hooks.adapter._active_hooks is yaml_hooks
              and lifecycle.lease.pending is yaml_hooks.intent
              and lifecycle.chain.active is yaml_hooks.intent
              and yaml_hooks.context.record.operation_id == lifecycle.chain.operation_id,
              'live_yaml_intent_required')
        yaml_hooks.state()  # Exact current prefix, including issued file calls.
        lifecycle.lease.check()  # Reconstructs and checks this pending intent.
    rows = {}
    for group in lifecycle.groups:
        intent_key = _event_id([lifecycle.root_reference[0], 'unload', group.registry_id, 'intent'])
        receipt_key = _event_id([lifecycle.root_reference[0], 'unload', group.registry_id, 'receipt'])
        intent, receipt = lifecycle.chain.store.read(intent_key), lifecycle.chain.store.read(receipt_key)
        _need(intent is not None and receipt is not None
              and intent['digest'] == _digest(intent['payload'])
              and receipt['digest'] == _digest(receipt['payload'])
              and intent['payload'].get('kind') == 'powercalc_group_unload_intent'
              and receipt['payload'].get('kind') == 'powercalc_group_unload_receipt'
              and intent['payload'].get('root') == list(lifecycle.root_reference)
              and receipt['payload'].get('root') == list(lifecycle.root_reference)
              and receipt['payload'].get('intent') == [intent_key, intent['digest']]
              and receipt['payload'].get('source_chain') == intent['payload'].get('source_chain')
              and receipt['payload'].get('source_chain') in lifecycle.chain._setup_positions
              and receipt['payload'].get('native_state') == 'not_loaded'
              and receipt['payload'].get('entry_id') == intent['payload']['before']['entry_id']
              and receipt['payload']['after']['group_uuid'] == group.registry_id
              and receipt['payload']['after']['group_id'] == group.entity_id,
              'accepted_group_unload_required')
        entry = lifecycle.lease.manager.async_get_entry(receipt['payload']['entry_id'])
        issued = lifecycle._unloaded.get(group.entity_id)
        _need(entry is not None and any(entry is item for item in lifecycle.lease.entries)
              and entry.state.value == 'not_loaded'
              and type(issued) is tuple and len(issued) == 3
              and issued[0] == (receipt_key, receipt['digest'])
              and issued[1] == receipt['payload']['source_chain'] and issued[2] is entry,
              'group_entry_must_remain_inactive')
        rows[group.entity_id] = (receipt_key, receipt['digest'], receipt['payload']['after'])
    return rows


def _store(lifecycle, vendor):
    from homeassistant.core import CoreState
    from homeassistant.helpers.storage import Store
    hass = lifecycle.lease.hass
    store = hass.data.get('powercalc_group_storage')
    _need(type(store) is vendor.classes['PreviousStateStore'] and store.hass is hass
          and type(store.store) is vendor.classes['PreviousStateStoreStore']
          and isinstance(store.store, Store) and store.store.hass is hass
          and store.store.key == 'powercalc_group' and store.store.version == 2
          and store.store.minor_version == 1
          and Path(store.store.path) == Path(hass.config.path('.storage', 'powercalc_group'))
          and hass.state not in (CoreState.stopping, CoreState.final_write, CoreState.not_running)
          and not store.store._read_only and not store.store._load_empty
          and type(store.store._write_lock) is asyncio.Lock, 'native_store_identity')
    return store


def _idle(store):
    lock = store.store._write_lock
    return (not lock.locked() and (lock._waiters is None or all(waiter.cancelled() for waiter in lock._waiters))
            and store.store._data is None and store.store._delay_handle is None)


async def async_handoff_previous_states(lifecycle):
    """Copy exact last native group/child State to mapped keys and persist once.

    Forward refuses occupied targets. A fresh inverse requires the live
    reverse admission and exact retained forward alias in memory and on disk.
    All groups must already have proven unload receipts under the same lease.
    """
    from homeassistant.core import State
    from homeassistant.helpers.storage import Store
    import homeassistant
    from .native_utility_setup_audit import _SOURCES

    _need(type(lifecycle) is PowercalcLifecycle, 'typed_lifecycle_required')
    lifecycle.lease._task_check()
    lifecycle.check()
    chain, lease = lifecycle.chain, lifecycle.lease
    original = lifecycle.composition.previous
    reverse = lifecycle.direction == 'rollback'
    moves = lifecycle.moves
    _need(lifecycle.direction in ('forward', 'rollback'), 'handoff_direction')
    accepted_aliases = {}
    if reverse:
        from .powercalc_reverse_admission import PowercalcReverseAdmission
        admission = lease.reverse_admission
        _need(type(admission) is PowercalcReverseAdmission and admission.chain is chain,
              'reverse_admission_required')
        admission._scope()
        forward_root = admission.forward.root_reference[0]
        forward_key = _event_id([forward_root, 'pss_forward', 'intent'])
        forward = chain.store.read(forward_key)
        _need(forward is not None and forward['digest'] == _digest(forward['payload'])
              and forward['payload'].get('kind') == 'powercalc_pss_forward_intent'
              and forward['payload'].get('root') == list(admission.forward.root_reference),
              'forward_alias_intent_required')
        accepted_aliases = forward['payload']['source']
    _need(any(move.before != move.after or any(a != b for a, b in move.members) for move in moves),
          'id_handoff_required')
    accepted = _unloads(lifecycle)
    vendor = PowercalcVendor(lease.hass, with_profiles=True)
    store = _store(lifecycle, vendor)
    refs = ((type(store), 'persist_states'),)
    core_refs = ((Store, 'async_save'), (Store, '_async_handle_write_data'), (Store, '_async_write_data'))
    pins = dict(_SOURCES[chain.original_receipt.version]); pins.update(vendor.ha_pins)
    def prove():
        vendor.sources()
        return (prove_methods(vendor.root, _pins(vendor.vendor_version), refs),
                prove_methods(Path(homeassistant.__file__).resolve().parent, pins, core_refs))
    proof = await _drained(lease.hass.async_add_executor_job, prove)
    lifecycle.check()
    _need(_store(lifecycle, vendor) is store and _idle(store), 'native_writer_busy')
    prior = _snapshot(store)
    for move in moves:
        previous = accepted[move.before][2]['previous']
        _need(_same(prior.get(move.before, {}), previous), 'unloaded_previous_changed')
        if reverse:
            _need(move.after in accepted_aliases and _same(prior.get(move.after), accepted_aliases[move.after]),
                  'reverse_memory_alias_changed')
        else:
            _need(move.before == move.after or move.after not in prior, 'memory_group_target_occupied')
        old = {name for name, _ in move.members}
        _need(set(prior.get(move.before, {})) <= old, 'unexpected_group_member')
        _need(not any(a != b and b in old for a, b in move.members), 'overlapping_member_mapping')

    pinned = _PinnedPSS(chain.original_receipt, input_state=lease.state if reverse else None)
    try:
        raw, identity = await _drained(lease.hass.async_add_executor_job, pinned.capture)
        lifecycle.check()
        _need(pinned.root_sig[:3] == original.root_identity and
              pinned.storage_sig[:3] == original.storage_identity and
              _store(lifecycle, vendor) is store and _idle(store) and _same(_snapshot(store), prior),
              'preimage_changed')
        _disk_shape(raw)
        disk = {} if raw is None else _loads(raw)['data']
        owned = {move.before for move in moves}
        if reverse:
            owned.update(move.after for move in moves)
        for move in moves:
            if reverse:
                _need(_same(disk.get(move.after), accepted_aliases[move.after]), 'reverse_disk_alias_changed')
            else:
                _need(move.before == move.after or move.after not in disk, 'disk_group_target_occupied')
        _need(_same({key: value for key, value in disk.items() if key not in owned},
                    {key: value for key, value in prior.items() if key not in owned}), 'foreign_disk_memory_disagree')
        await _drained(lease.hass.async_add_executor_job, pinned.verify)
        lifecycle.check()
        _need(_idle(store) and _same(_snapshot(store), prior), 'preimage_changed')
    finally:
        pinned.close()

    source = {move.before: prior.get(move.before, {}) for move in moves}
    owned = {move.before for move in moves}
    if reverse:
        owned.update(move.after for move in moves)
    foreign = {key: value for key, value in prior.items() if key not in owned}
    foreign_sha = hashlib.sha256(_json(foreign)).hexdigest()
    free = [index for index in range(6) if sys.monitoring.get_tool(index) is None]
    _need(free, 'persist_observer_unavailable')
    label = 'pss_reverse' if reverse else 'pss_forward'
    key = _event_id([lifecycle.root_reference[0], label, 'intent'])
    intent = {'kind': 'powercalc_' + label + '_intent', 'schema': 1,
              'root': list(lifecycle.root_reference), 'source_chain': lease.state.head,
              'unloads': [[row[0], row[1]] for _, row in sorted(accepted.items())],
              'source': source, 'foreign_sha256': foreign_sha,
              'disk_before_sha256': None if raw is None else hashlib.sha256(raw).hexdigest(),
              'disk_before_identity': None if identity is None else list(identity),
              'moves': lifecycle.root['moves']}
    ref = await chain.store.create(key, intent)
    lifecycle.check()
    _need(_unloads(lifecycle) == accepted and _store(lifecycle, vendor) is store
          and _idle(store) and _same(_snapshot(store), prior), 'post_intent_preimage_changed')
    # Recheck the physical preimage after the durable intent's await.
    verify_before = _PinnedPSS(chain.original_receipt, input_state=lease.state if reverse else None)
    try:
        actual_before, current_identity = await _drained(lease.hass.async_add_executor_job, verify_before.capture)
        lifecycle.check()
        _need(actual_before == raw and current_identity == identity
              and verify_before.root_sig[:3] == original.root_identity
              and verify_before.storage_sig[:3] == original.storage_identity
              and _idle(store) and _same(_snapshot(store), prior), 'post_intent_disk_changed')
    finally:
        verify_before.close()
    lifecycle.check()
    _need(sys.monitoring.get_tool(free[0]) is None, 'persist_observer_no_longer_available')

    # No await between the final source check and the detached native-map edit.
    live_states = store.states
    after_states = dict(live_states)
    for move in moves:
        old_rows = store.states.get(move.before, {})
        # A retained old alias must not share mutable State.last_reported
        # with the active target group across later native source reports.
        target = ({name: None if state is None else _clone(state, name)
                   for name, state in old_rows.items()} if move.before != move.after else dict(old_rows))
        for before, after in move.members:
            if before != after and before in old_rows:
                _need(after not in old_rows, 'member_target_occupied')
                target[after] = None if old_rows[before] is None else _clone(old_rows[before], after)
        after_states[move.after] = target
    for move in moves:
        live_states[move.after] = after_states[move.after]
    post = _snapshot(store)
    _need(all(_same(post.get(key), value) for key, value in foreign.items())
          and all(_same(post.get(move.before), prior.get(move.before)) for move in moves
                  if move.before != move.after), 'foreign_or_source_memory_changed')

    # Catch overlapping native periodic/shutdown persist rather than adopting a
    # coincidentally matching readback as our own success.
    code = type(store).persist_states.__code__
    tool, starts, returns, contamination, task = free[0], [], [], [], None
    sys.monitoring.use_tool_id(tool, 'renamer.powercalc_pss_handoff')
    def started(code_, offset):
        frame = sys._getframe(1)
        if frame.f_locals.get('self') is store:
            current = asyncio.current_task()
            starts.append(current)
            if current is not task:
                contamination.append('foreign_persist')
    def returned(code_, offset, result):
        frame = sys._getframe(1)
        if frame.f_locals.get('self') is store:
            returns.append(asyncio.current_task())
    try:
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
        sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
        task = asyncio.create_task(store.persist_states())
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
        _need(starts == [task] and returns == [task] and not contamination
              and _store(lifecycle, vendor) is store and _idle(store)
              and store.states is live_states and _same(_snapshot(store), post), 'native_persist_or_memory_unknown')
        readback = _PinnedPSS(chain.original_receipt, input_state=lease.state if reverse else None)
        try:
            actual_raw, actual_identity = await _drained(lease.hass.async_add_executor_job, readback.capture)
            lifecycle.check()
            _need(readback.root_sig[:3] == original.root_identity and
                  readback.storage_sig[:3] == original.storage_identity and actual_raw is not None,
                  'postimage_identity')
            _disk_shape(actual_raw)
            _need(_same(_loads(actual_raw)['data'], post) and _same(_snapshot(store), post) and _idle(store)
                  and starts == [task] and returns == [task] and not contamination,
                  'postimage_or_competing_persist_unknown')
            await _drained(lease.hass.async_add_executor_job, readback.verify)
            lifecycle.check()
            _need(_same(_snapshot(store), post) and _idle(store) and not contamination,
                  'postimage_changed')
        finally:
            readback.close()
        unchanged(refs, *proof[0]); unchanged(core_refs, *proof[1])
    finally:
        sys.monitoring.set_local_events(tool, code, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
        sys.monitoring.free_tool_id(tool)
    # Retain old keys deliberately, so inverse needs an exact accepted alias.
    result_key = _event_id([lifecycle.root_reference[0], label, 'receipt'])
    receipt = {'kind': 'powercalc_' + label + '_receipt', 'schema': 1,
               'root': list(lifecycle.root_reference), 'intent': list(ref),
               'source_chain': lease.state.head, 'foreign_sha256': foreign_sha,
               'disk_after_sha256': hashlib.sha256(actual_raw).hexdigest(),
               'disk_after_identity': list(actual_identity),
               'target': {move.after: post[move.after] for move in moves},
               'group_totals': {name: row[2]['full_total'] for name, row in accepted.items()}}
    result_ref = await chain.store.create(result_key, receipt)
    lifecycle.check()
    _need(chain.store.read(result_key) == {'digest': _digest(receipt), 'payload': receipt}, 'receipt_readback')
    return result_ref
