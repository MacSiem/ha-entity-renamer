"""One journal-bound native Powercalc population handoff.

This is an effect helper of the existing Coordinator, not a scheduler or a
recovery authority. A persisted intent without its actual readback is UNKNOWN.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from .transaction import PhaseContext, Refused
from .native_input_baseline import _drained, _loads
from .native_registry_receipts import RegistryReceiptChain, _digest, _event_id, _exact_json_equal, _freeze_json
from .powercalc_previous_state_original import _PinnedPSS, _disk_shape
from .powercalc_pss_handoff import _clone, _idle, _snapshot
from .controlled_powercalc_population import PowercalcPopulation, snapshot_removed


def _need(value, reason):
    if not value:
        raise Refused('controlled_powercalc_handoff_' + reason)


def _read_pinned(receipt, key, input_state=None):
    pin = _PinnedPSS(receipt, key=key, input_state=input_state)
    try:
        raw, identity = pin.capture()
        return raw, identity, pin.root_sig[:3], pin.storage_sig[:3]
    finally:
        pin.close()


def _restore_rows(raw):
    if raw is None:
        return {}
    value = _loads(raw, limit=64 * 1024 * 1024)
    _need(type(value) is dict and set(value) == {'version', 'minor_version', 'key', 'data'}
          and type(value['version']) is int and value['version'] == 1
          and type(value['minor_version']) is int and value['minor_version'] == 1
          and value['key'] == 'core.restore_state' and type(value['data']) is list, 'restore_envelope')
    rows = {}
    for row in value['data']:
        _need(type(row) is dict and type(row.get('state')) is dict
              and type(row['state'].get('entity_id')) is str, 'restore_row')
        name = row['state']['entity_id']
        _need(name not in rows, 'restore_duplicate')
        rows[name] = row
    return rows


def _native_restore_rows(data):
    from homeassistant.helpers.json import json_bytes
    rows = _loads(json_bytes([row.as_dict() for row in data.async_get_stored_states()]),
                  limit=64 * 1024 * 1024)
    result = {}
    for row in rows:
        name = row['state']['entity_id']
        _need(name not in result, 'native_restore_duplicate')
        result[name] = row
    return result


def _semantic(row):
    # Native dumping legitimately refreshes last_seen on active foreign rows.
    return {key: value for key, value in row.items() if key != 'last_seen'}


def _restore_housekeeping(before, current, native, owned, *, now=None):
    """Accept unchanged payloads, native last_seen refresh and real expiry."""
    from datetime import datetime, timedelta, timezone
    old_rows, new_rows = _restore_rows(before), _restore_rows(current)
    now = datetime.now(timezone.utc) if now is None else now
    if type(now) is not datetime or now.tzinfo is None:
        return False
    for key in old_rows.keys() | new_rows.keys():
        old, new = old_rows.get(key), new_rows.get(key)
        if old is not None and new is not None:
            if not _exact_json_equal(_semantic(new), _freeze_json(_semantic(old))):
                return False
            continue
        if old is None or new is not None or key in owned or key in native:
            return False
        try:
            last_seen = datetime.fromisoformat(old['last_seen'])
            if last_seen.tzinfo is None or last_seen >= now - timedelta(days=7):
                return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
    return True


def _pss_foreign_unchanged(before, current, active_groups):
    """Allow live changes only in the qualified, currently active groups."""
    if type(before) is not dict or type(current) is not dict or type(active_groups) is not set:
        return False
    left = {key: value for key, value in before.items() if key not in active_groups}
    right = {key: value for key, value in current.items() if key not in active_groups}
    return _exact_json_equal(right, _freeze_json(left))


def _store_writer(store):
    from homeassistant.const import __version__
    from homeassistant.helpers.storage import Store
    refs = ((Store, 'async_save'), (Store, '_async_handle_write_data'),
            (Store, '_async_write_data'))
    if __version__ == '2026.11.0':
        from homeassistant.helpers.storage import _FileStoreIO
        io = store._io
        _need(type(io) is _FileStoreIO and io.path == store.path
              and io._key == store.key and io._hass is store.hass, 'native_file_io_changed')
        return io, _FileStoreIO._write.__code__, refs + (
            (Store, '_prepare_data'), (_FileStoreIO, 'async_write'), (_FileStoreIO, '_write'))
    return store, Store._write_prepared_data.__code__, refs + (
        (Store, '_write_data'), (Store, '_write_prepared_data'))


class _NativePSSProgress:
    """Observe native PSS writes only while the qualified population unloads."""
    def __init__(self, owner, proof, refs, core_proof, core_refs):
        import sys
        from homeassistant.helpers.storage import Store
        self.owner, self.store = owner, owner.store
        self.proof, self.refs, self.core_proof, self.core_refs = proof, refs, core_proof, core_refs
        self.owner_code = type(owner).persist_states.__code__
        self.save_code = Store.async_save.__code__
        self.writer, self.writer_code, _ = _store_writer(self.store)
        self.owners, self.saves, self.writing = set(), set(), {}
        self.completed, self.contamination = [], []
        free = [index for index in range(6) if sys.monitoring.get_tool(index) is None]
        _need(bool(free), 'native_progress_observer_unavailable')
        self.tool = free[0]
        sys.monitoring.use_tool_id(self.tool, 'renamer.controlled_pss_progress')
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, self._start)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_RETURN, self._return)
        for code in (self.owner_code, self.save_code, self.writer_code):
            sys.monitoring.set_local_events(self.tool, code,
                sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)

    def _start(self, code, offset):
        import asyncio
        import sys
        import threading
        frame = sys._getframe(1)
        instance = frame.f_locals.get('self')
        if code is self.owner_code and instance is self.owner:
            task = asyncio.current_task()
            if self.owners or self.saves:
                self.contamination.append('overlapping_owner')
            self.owners.add(task)
        elif code is self.save_code and instance is self.store:
            task = asyncio.current_task()
            if task not in self.owners or self.saves:
                self.contamination.append('unowned_save')
            self.saves.add(task)
        elif code is self.writer_code and instance is self.writer:
            ident = threading.get_ident()
            raw = frame.f_locals.get('json_data')
            if len(self.owners) != 1 or self.saves != self.owners or ident in self.writing:
                self.contamination.append('unowned_writer')
            if type(raw) is str:
                raw = raw.encode('utf-8')
            if type(raw) is not bytes:
                self.contamination.append('writer_payload')
            self.writing[ident] = raw

    def _return(self, code, offset, result):
        import asyncio
        import sys
        import threading
        instance = sys._getframe(1).f_locals.get('self')
        if code is self.owner_code and instance is self.owner:
            self.owners.discard(asyncio.current_task())
        elif code is self.save_code and instance is self.store:
            self.saves.discard(asyncio.current_task())
        elif code is self.writer_code and instance is self.writer:
            raw = self.writing.pop(threading.get_ident(), None)
            if raw is None:
                self.contamination.append('writer_return')
            else:
                self.completed.append(raw)

    def accepts(self, before, before_identity, current, current_identity, active_groups):
        from .native_unload_code_proof import unchanged
        unchanged(self.refs, *self.proof)
        unchanged(self.core_refs, *self.core_proof)
        if self.contamination or self.owners or self.saves or self.writing:
            return False
        if (before, before_identity) == (current, current_identity):
            return True
        if not self.completed or self.completed[-1] != current:
            return False
        _disk_shape(before)
        _disk_shape(current)
        old = {} if before is None else _loads(before)['data']
        new = {} if current is None else _loads(current)['data']
        return _pss_foreign_unchanged(old, new, active_groups)

    def close(self):
        import sys
        if self.tool is None:
            return
        for code in (self.owner_code, self.save_code, self.writer_code):
            sys.monitoring.set_local_events(self.tool, code, 0)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, None)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_RETURN, None)
        sys.monitoring.free_tool_id(self.tool)
        self.tool = None


async def _native_store_write(hass, owner, method_name, receipt, input_state, key,
                              expected_raw, expected_identity, root_sig, storage_sig, guard):
    """R4 own-call witness plus a physical preimage check at the native write seam."""
    import asyncio
    from pathlib import Path
    import sys
    import homeassistant
    from homeassistant.helpers.storage import Store
    from .native_unload_code_proof import prove_methods, unchanged
    from .native_utility_setup_audit import _SOURCES
    from .powercalc_source_binding import PowercalcVendor, _pins
    store = owner.store
    _need(isinstance(store, Store) and store.hass is hass and store.key == key and _idle(owner),
          'native_write_store_unavailable')
    refs = ((type(owner), method_name),)
    writer, writer_code, core_refs = _store_writer(store)
    vendor = PowercalcVendor(hass, with_profiles=True) if key == 'powercalc_group' else None
    def prove():
        pins = dict(_SOURCES[receipt.version])
        core = prove_methods(Path(homeassistant.__file__).resolve().parent, pins, core_refs)
        owner_proof = (prove_methods(vendor.root, _pins(vendor.vendor_version), refs) if vendor is not None
                       else prove_methods(Path(homeassistant.__file__).resolve().parent, pins, refs))
        return owner_proof, core
    proof = await _drained(hass.async_add_executor_job, prove)
    guard()
    _need(_idle(owner), 'native_write_store_busy')
    free = [index for index in range(6) if sys.monitoring.get_tool(index) is None]
    _need(bool(free), 'native_write_observer_unavailable')
    tool = free[0]
    method_code = getattr(type(owner), method_name).__code__
    save_code = Store.async_save.__code__
    write_code = Store._async_write_data.__code__
    task, starts, returns, saves, contamination, writes = None, [], [], [], [], []
    write_owner = None
    sys.monitoring.use_tool_id(tool, 'renamer.controlled_native_store_write')
    def started(code, offset):
        nonlocal write_owner
        frame = sys._getframe(1)
        instance = frame.f_locals.get('self')
        if code is method_code and instance is owner:
            current = asyncio.current_task()
            starts.append(current)
            if current is not task:
                contamination.append('foreign_native_owner')
        elif code is save_code and instance is store:
            current = asyncio.current_task()
            saves.append(current)
            if current is not task:
                contamination.append('foreign_native_store')
        elif code is write_code and instance is store:
            # Store serializes _async_write_data under its native write lock.
            # Associate its executor callback with that actual task so a
            # competitor's later write is observed, never blocked by our fence.
            write_owner = asyncio.current_task()
        elif code is writer_code and instance is writer:
            if write_owner is not task:
                return
            # Runs in the native Store executor immediately before its writer.
            # This is a physical preimage fence, not an OS-wide atomic CAS.
            _need(not contamination, 'native_write_competition_before_writer')
            actual, identity, root, storage = _read_pinned(receipt, key, input_state)
            _need((actual, identity, root, storage) ==
                  (expected_raw, expected_identity, root_sig, storage_sig),
                  'native_write_physical_preimage_changed')
            raw = frame.f_locals.get('json_data')
            if type(raw) is str:
                raw = raw.encode('utf-8')
            _need(type(raw) is bytes, 'native_write_payload_unverified')
            writes.append(raw)
    def returned(code, offset, result):
        frame = sys._getframe(1)
        if code is method_code and frame.f_locals.get('self') is owner:
            returns.append(asyncio.current_task())
    try:
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
        for code in (method_code, save_code, write_code, writer_code):
            events = sys.monitoring.events.PY_START | (sys.monitoring.events.PY_RETURN if code is method_code else 0)
            sys.monitoring.set_local_events(tool, code, events)
        task = asyncio.create_task(getattr(owner, method_name)())
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
        guard()
        _need(starts == [task] and returns == [task] and saves == [task]
              and len(writes) == 1 and not contamination and _idle(owner),
              'native_write_or_competition_uncertain')
        unchanged(refs, *proof[0])
        unchanged(core_refs, *proof[1])
        actual, _, root, storage = await _drained(hass.async_add_executor_job,
            lambda: _read_pinned(receipt, key, input_state))
        guard()
        _need((root, storage) == (root_sig, storage_sig)
              and actual == writes[0] and not contamination and _idle(owner),
              'native_write_physical_readback_changed')
        return actual
    finally:
        for code in (method_code, save_code, write_code, writer_code):
            sys.monitoring.set_local_events(tool, code, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
        sys.monitoring.free_tool_id(tool)



async def _settle_yaml_discovery(hass):
    # The native reload awaits discovery dispatch, which schedules actual
    # platform setup separately. Await only this integration's exact sensor
    # discovery tasks; never drain HA or unrelated editor work.
    from homeassistant.components.sensor import DOMAIN
    from homeassistant.helpers.entity_component import DATA_INSTANCES, EntityComponent
    component = hass.data[DATA_INSTANCES].get(DOMAIN)
    _need(type(component) is EntityComponent and component.hass is hass,
          'setup_sensor_component_changed')
    discovery = EntityComponent._async_component_platform_discovered.__code__
    async with asyncio.timeout(10):
        while True:
            pending = []
            for task in asyncio.all_tasks(hass.loop):
                coro = task.get_coro()
                frame = getattr(coro, 'cr_frame', None)
                if (frame is not None and coro.cr_code is discovery
                        and frame.f_locals.get('self') is component
                        and frame.f_locals.get('platform') == 'powercalc'):
                    pending.append(task)
            if not pending:
                return
            await asyncio.gather(*(asyncio.shield(task) for task in pending))


def _watch_restored_counters(hass, population):
    """Capture the first native state publication after a qualified setup."""
    from decimal import Decimal
    from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
    from homeassistant.core import callback
    from homeassistant.helpers.entity_platform import async_get_platforms
    from custom_components.powercalc.sensors.energy import VirtualEnergySensor
    from custom_components.powercalc.sensors.group.custom import GroupedEnergySensor
    expected = {row['after_id']: row for row in population}
    _need(len(expected) == len(population), 'setup_watch_population')
    observed = {}
    @callback
    def state_changed(event):
        name = event.data.get('entity_id')
        state = event.data.get('new_state')
        if name not in expected or name in observed or state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        row = expected[name]
        matches = [entity for platform in async_get_platforms(hass, 'powercalc')
                   for entity_id, entity in platform.entities.items() if entity_id == name]
        if len(matches) != 1:
            return
        entity = matches[0]
        cls = GroupedEnergySensor if row['kind'] == 'group' else VirtualEnergySensor
        if type(entity) is not cls or entity.registry_entry is None or entity.registry_entry.id != row['uuid']:
            return
        value = entity._native_value_exact if row['kind'] == 'group' else entity._state
        last = None if row['kind'] == 'group' else entity._last_valid_state
        if (type(value) is not Decimal or not value.is_finite()
                or row['kind'] != 'group' and (type(last) is not Decimal or not last.is_finite())):
            return
        observed[name] = {'uuid': row['uuid'], 'entity_id': name, 'kind': row['kind'],
                          'value': str(value), 'last_valid': None if last is None else str(last)}
    return observed, hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)


def _sha(raw):
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _identity(value):
    return None if value is None else list(value)


class ControlledPowercalcHandoff:
    """A single directional unload and native-store handoff under one intent."""
    def __init__(self, root, population):
        from .native_template_state_server import NativeTemplateStateServer
        _need(type(root) is NativeTemplateStateServer and type(root.chain) is RegistryReceiptChain
              and root.chain.writable and type(population) is PowercalcPopulation,
              'live_original_required')
        self.root, self.hass, self.chain, self.population = root, root.hass, root.chain, population
        self.intent = self.receipt = None
        self.direction = None
        self.entries = self.pss_expected = self.restore_expected = None
        self.setup_reference = None

    def _scope(self):
        self.root._scope()
        _need(self.root.chain is self.chain and self.chain.writable and not self.root.closed,
              'original_chain_changed')

    def require_inactive(self):
        from homeassistant.config_entries import ConfigEntryState
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
        self._scope()
        _need(self.receipt is not None and self.entries is not None, 'completed_handoff_required')
        saved = self.chain.store.read(self.receipt[0])
        _need(saved is not None and saved['digest'] == self.receipt[1] == _digest(saved['payload'])
              and saved['payload']['root'] == self.chain.root_id
              and saved['payload']['direction'] == self.direction,
              'completed_receipt_changed')
        _need(tuple(self.hass.config_entries.async_entries('powercalc')) == self.entries
              and all(entry.disabled_by is not None or entry.state is ConfigEntryState.NOT_LOADED
                      for entry in self.entries)
              and all(not platform.entities for platform in async_get_platforms(self.hass, 'powercalc')),
              'population_not_inactive')
        pss = self.hass.data.get('powercalc_group_storage')
        data = self.hass.data.get(DATA_RESTORE_STATE)
        _need(pss is not None and type(data) is RestoreStateData and data.hass is self.hass
              and _idle(pss) and _idle(data), 'native_store_busy')
        actual_pss, actual_restore = _snapshot(pss), _native_restore_rows(data)
        _need(all(key in actual_pss and _exact_json_equal(actual_pss[key], _freeze_json(value))
                  for key, value in self.pss_expected.items())
              and all(key in actual_restore and _exact_json_equal(_semantic(actual_restore[key]),
                      _freeze_json(_semantic(value))) for key, value in self.restore_expected.items()),
              'owned_handoff_changed')

    def require_active(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        self._scope()
        _need(self.setup_reference is not None, 'completed_setup_required')
        saved = self.chain.store.read(self.setup_reference[0])
        _need(saved is not None and saved['digest'] == self.setup_reference[1] == _digest(saved['payload'])
              and saved['payload']['root'] == self.chain.root_id
              and saved['payload']['direction'] == self.direction, 'setup_receipt_changed')
        entities = {name: entity for platform in async_get_platforms(self.hass, 'powercalc')
                    for name, entity in platform.entities.items()}
        expected = saved['payload']['population']
        _need(set(entities) == {row[0] for row in expected}
              and all(entities[name].registry_entry is not None
                      and entities[name].registry_entry.id == uid for name, uid, _ in expected),
              'setup_population_changed')

    async def start(self, context):
        from pathlib import Path
        import homeassistant
        from .native_unload_code_proof import prove_methods
        from .native_utility_setup_audit import _SOURCES
        from .powercalc_source_binding import PowercalcVendor, _pins
        self._scope()
        pss = self.hass.data.get('powercalc_group_storage')
        _need(pss is not None and _idle(pss), 'native_store_busy')
        refs = ((type(pss), 'persist_states'),)
        _, _, core_refs = _store_writer(pss.store)
        vendor = PowercalcVendor(self.hass, with_profiles=True)
        def prove():
            return (prove_methods(vendor.root, _pins(vendor.vendor_version), refs),
                    prove_methods(Path(homeassistant.__file__).resolve().parent,
                                  dict(_SOURCES[self.chain.original_receipt.version]), core_refs))
        proof, core_proof = await _drained(self.hass.async_add_executor_job, prove)
        self._scope()
        _need(_idle(pss), 'native_store_busy')
        progress = _NativePSSProgress(pss, proof, refs, core_proof, core_refs)
        try:
            return await self._start(context, progress)
        finally:
            progress.close()

    async def _start(self, context, progress):
        from homeassistant.const import __version__
        from homeassistant.config_entries import ConfigEntryState
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
        from .native_restore_state_payload import materialize_integration_payload, serialize_integration_payload
        from .powercalc_group_restore import PowercalcGroupRestorePayload, materialize as materialize_group
        from .helper_native import read_entry
        _need(type(context) is PhaseContext and self.intent is None and self.receipt is None,
              'one_attempt_required')
        direction = context.record.direction
        context.require_effect(context.step, direction)
        self._scope()
        _need(context.record.operation_id == self.chain.operation_id and context.record.plan == self.chain.plan
              and await self.root.journal.load(self.chain.operation_id) == context.record,
              'durable_phase_required')
        self._scope()
        _need(direction in ('forward', 'rollback') and self.chain.active is None, 'direction')
        entries = tuple(self.hass.config_entries.async_entries('powercalc'))
        _need(all(entry.disabled_by is not None or entry.state is ConfigEntryState.LOADED for entry in entries),
              'unloaded_enabled_entry_unsupported')
        pss = self.hass.data.get('powercalc_group_storage')
        data = self.hass.data.get(DATA_RESTORE_STATE)
        _need(type(data) is RestoreStateData and data.hass is self.hass
              and pss is not None and _idle(data) and _idle(pss), 'native_store_busy')
        previous = self.root.composition.previous
        pss_before, pss_identity, root_sig, storage_sig = await _drained(self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'powercalc_group', self.root.observation_state))
        restore_before, restore_identity, root2, storage2 = await _drained(self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'core.restore_state', self.root.observation_state))
        self._scope()
        _need(root_sig == root2 == previous.root_identity and storage_sig == storage2 == previous.storage_identity
              and _idle(data) and _idle(pss), 'store_root_changed')
        _disk_shape(pss_before)
        _restore_rows(restore_before)
        before_unload = _snapshot(pss)
        aliases = None
        if direction == 'rollback':
            from .controlled_powercalc_setup_receipts import validate_handoff
            key = _event_id(['controlled_pc_population', self.chain.root_id, 'forward', 'receipt'])
            saved = self.chain.store.read(key)
            _need(saved is not None, 'forward_alias_receipt_missing')
            aliases = validate_handoff(self.chain, [key, saved['digest']], 'forward')

        def check_inverse_aliases(pss_raw, restore_raw):
            if aliases is None:
                return
            pss_disk = {} if pss_raw is None else _loads(pss_raw)['data']
            pss_memory = _snapshot(pss)
            restore_disk = _restore_rows(restore_raw)
            restore_memory = _native_restore_rows(data)
            for counter in self.population.counters:
                target = counter.target_id
                if target == counter.entity_id:
                    continue
                for actual in (restore_disk, restore_memory):
                    if target in actual:
                        expected = aliases['restore_owned'].get(target)
                        _need(expected is not None and _exact_json_equal(_semantic(actual[target]),
                              _freeze_json(_semantic(expected))), 'foreign_restore_alias')
                if counter.kind == 'group':
                    for actual in (pss_disk, pss_memory):
                        if target in actual:
                            expected = aliases['pss_owned'].get(target)
                            _need(expected is not None and _exact_json_equal(actual[target], _freeze_json(expected)),
                                  'foreign_pss_alias')

        check_inverse_aliases(pss_before, restore_before)
        entry_images = {entry.entry_id: read_entry(self.hass, entry.entry_id) for entry in entries}
        # Confirm native serialization is available before any unload.
        _native_restore_rows(data)
        slot = _event_id(['controlled_pc_population', self.chain.root_id, direction, 'intent'])
        _need(self.chain.store.read(slot) is None, 'intent_already_consumed')
        intent = {'kind': 'controlled_powercalc_population_intent', 'schema': 1,
                  'root': self.chain.root_id, 'operation_id': self.chain.operation_id,
                  'fingerprint': self.chain.plan.fingerprint, 'direction': direction,
                  'phase_index': context.index, 'source_head': self.chain.reconstruct_inputs().head,
                  'population': [list(row) for row in self.population.descriptors],
                  'pss_before_sha256': _sha(pss_before), 'pss_before_identity': _identity(pss_identity),
                  'restore_before_sha256': _sha(restore_before), 'restore_before_identity': _identity(restore_identity),
                  'entries': entry_images}
        self.intent = await self.chain.store.create(slot, intent)
        self.direction = direction
        self._scope()
        _need(await self.root.journal.load(self.chain.operation_id) == context.record,
              'journal_changed_after_intent')
        self._scope()
        pss_check, pss_check_id, root_check, storage_check = await _drained(self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'powercalc_group', self.root.observation_state))
        restore_check, restore_check_id, root_check2, storage_check2 = await _drained(self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'core.restore_state', self.root.observation_state))
        self._scope()
        active_groups = {counter.entity_id for counter in self.population.counters if counter.kind == 'group'}
        _need(progress.accepts(pss_before, pss_identity, pss_check, pss_check_id, active_groups)
              and (restore_check, restore_check_id) == (restore_before, restore_identity)
              and root_check == root_check2 == root_sig and storage_check == storage_check2 == storage_sig
              and _pss_foreign_unchanged(before_unload, _snapshot(pss), active_groups)
              and tuple(self.hass.config_entries.async_entries('powercalc')) == entries
              and all(_exact_json_equal(read_entry(self.hass, entry.entry_id), _freeze_json(entry_images[entry.entry_id]))
                      for entry in entries) and _idle(data) and _idle(pss), 'source_changed_after_intent')
        pss_before, pss_identity = pss_check, pss_check_id
        group_entries = {counter.entry_id for counter in self.population.counters if counter.kind == 'group'}
        ordered = sorted((entry for entry in entries if entry.disabled_by is None),
                         key=lambda entry: (entry.entry_id not in group_entries, entry.entry_id))
        async def fence_entries():
            self._scope()
            _need(await self.root.journal.load(self.chain.operation_id) == context.record,
                  'journal_changed_during_unload')
            self._scope()
            _need(tuple(self.hass.config_entries.async_entries('powercalc')) == entries
                  and all(_exact_json_equal(read_entry(self.hass, entry.entry_id),
                                            _freeze_json(entry_images[entry.entry_id])) for entry in entries),
                  'entry_changed_during_unload')
        for entry in ordered:
            await fence_entries()
            _need(entry.state is ConfigEntryState.LOADED and await self.hass.config_entries.async_unload(entry.entry_id),
                  'native_unload_unconfirmed')
            self._scope()
        for platform in tuple(async_get_platforms(self.hass, 'powercalc')):
            await fence_entries()
            await platform.async_reset()
            self._scope()
        await fence_entries()
        _need(all(not platform.entities for platform in async_get_platforms(self.hass, 'powercalc')),
              'native_population_still_active')
        payloads = snapshot_removed(self.hass, self.population,
            allowed_targets=() if aliases is None else tuple(aliases['restore_owned']))
        _need(_idle(data) and _idle(pss), 'store_busy_after_unload')
        # Unload can yield.  Refuse a concurrent physical edit before the
        # native stores are changed; an uncertain unload is never replayed.
        pss_current, pss_current_id, root_current, storage_current = await _drained(
            self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'powercalc_group', self.root.observation_state))
        restore_current, restore_current_id, root_current2, storage_current2 = await _drained(
            self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'core.restore_state', self.root.observation_state))
        self._scope()
        check_inverse_aliases(pss_current, restore_current)
        restore_native_after_unload = _native_restore_rows(data)
        owned_restore_ids = {name for counter in self.population.counters
                             for name in (counter.entity_id, counter.target_id)}
        _need(progress.accepts(pss_before, pss_identity, pss_current, pss_current_id, active_groups)
              and _restore_housekeeping(restore_before, restore_current, restore_native_after_unload,
                                        owned_restore_ids)
              and root_current == root_current2 == root_sig
              and storage_current == storage_current2 == storage_sig
              and _idle(data) and _idle(pss), 'store_changed_after_unload')
        progress.close()
        prior = _snapshot(pss)
        group_counters = [counter for counter in self.population.counters if counter.kind == 'group']
        owned = {counter.entity_id for counter in group_counters} | {counter.target_id for counter in group_counters}
        _need(_pss_foreign_unchanged(before_unload, prior,
              {counter.entity_id for counter in group_counters}), 'foreign_pss_changed')
        live_states = pss.states
        targets = {}
        directional_mapping = (self.root.mapping if direction == 'forward'
                               else {new: old for old, new in self.root.mapping.items()})
        for counter in group_counters:
            old, new, entity = counter.entity_id, counter.target_id, counter.entity
            rows = live_states.get(old)
            _need(type(rows) is dict and set(rows) <= set(entity._entities)
                  and (new == old or new not in live_states
                       or aliases is not None and new in aliases['pss_owned']), 'pss_group_source_changed')
            target = {name: None if state is None else _clone(state, name) for name, state in rows.items()}
            for old_member in entity._entities:
                new_member = directional_mapping.get(old_member, old_member)
                if new_member != old_member and old_member in rows:
                    _need(new_member not in target, 'pss_member_target_occupied')
                    state = rows[old_member]
                    target[new_member] = None if state is None else _clone(state, new_member)
            targets[new] = target
        for name, rows in targets.items():
            live_states[name] = rows
        pss_after_memory = _snapshot(pss)
        _need(_pss_foreign_unchanged(prior, pss_after_memory, owned), 'foreign_pss_changed')
        # The payloads were built from actual post-unload native objects.
        owned_restore = set()
        staged_restore = []
        for uuid, old, new, kind, payload in payloads:
            _need(old not in data.entities and new not in data.entities, 'restore_target_changed')
            if __version__ == '2026.11.0':
                from .native_restore_uuid import owned_slot
                from . import native_restore_state_payload as codec
                from custom_components.powercalc.sensors.energy import VirtualEnergyExtraStoredData
                source = owned_slot(data, old, new, uuid, required=True)
                extra = VirtualEnergyExtraStoredData if kind == 'integration' else None
                before = codec._canonical(codec._capture(source, codec._native(), powercalc_extra=extra))
                _need(source.state.entity_id == old and before == payload.before_json,
                      'restore_uuid_preimage_changed')
            else:
                _need(new == old or new not in data.last_states
                      or aliases is not None and new in aliases['restore_owned'], 'restore_target_changed')
            clone = materialize_integration_payload(payload) if kind == 'integration' else materialize_group(payload)
            if __version__ == '2026.11.0':
                _need(clone.entity_registry_id == uuid, 'restore_uuid_payload_changed')
                owned_restore.add(new)
            else:
                owned_restore.update((old, new))
            staged_restore.append((uuid, new, clone))
        for uuid, new, clone in staged_restore:
            if __version__ == '2026.11.0':
                data.last_states_by_entity_registry_id[uuid] = clone
            else:
                data.last_states[new] = clone
        expected_restore = _native_restore_rows(data)
        _need(owned_restore <= expected_restore.keys() and _idle(data) and _idle(pss),
              'store_busy_or_owned_restore_expired')
        await _native_store_write(self.hass, pss, 'persist_states', self.chain.original_receipt,
            self.root.observation_state, 'powercalc_group', pss_current, pss_current_id,
            root_sig, storage_sig, self._scope)
        self._scope()
        _need(_idle(pss) and _exact_json_equal(_snapshot(pss), _freeze_json(pss_after_memory)),
              'pss_native_write_uncertain')
        restore_gate, restore_gate_id, root_gate, storage_gate = await _drained(
            self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'core.restore_state', self.root.observation_state))
        self._scope()
        restore_memory_gate = _native_restore_rows(data)
        _need(_restore_housekeeping(restore_current, restore_gate, restore_memory_gate, owned_restore_ids)
              and root_gate == root_sig and storage_gate == storage_sig
              and _idle(data) and _idle(pss)
              and all(key in restore_memory_gate
                      and _exact_json_equal(_semantic(restore_memory_gate[key]),
                                            _freeze_json(_semantic(expected_restore[key])))
                      for key in owned_restore),
              'restore_source_changed_before_write')
        expected_restore = restore_memory_gate
        restore_written = await _native_store_write(self.hass, data, 'async_dump_states', self.chain.original_receipt,
            self.root.observation_state, 'core.restore_state', restore_gate, restore_gate_id,
            root_sig, storage_sig, self._scope)
        self._scope()
        _need(_idle(data) and _idle(pss), 'restore_native_write_uncertain')
        pss_after, pss_after_identity, root3, storage3 = await _drained(self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'powercalc_group', self.root.observation_state))
        restore_after, restore_after_identity, root4, storage4 = await _drained(self.hass.async_add_executor_job,
            lambda: _read_pinned(self.chain.original_receipt, 'core.restore_state', self.root.observation_state))
        self._scope()
        _need(root3 == root4 == previous.root_identity and storage3 == storage4 == previous.storage_identity
              and pss_after is not None and restore_after == restore_written and _idle(data) and _idle(pss),
              'native_readback_uncertain')
        _disk_shape(pss_after)
        _need(_exact_json_equal(_loads(pss_after)['data'], _freeze_json(pss_after_memory)),
              'pss_readback_changed')
        actual_restore = _restore_rows(restore_after)
        _need(owned_restore <= actual_restore.keys()
              and all(_exact_json_equal(_semantic(actual_restore[key]),
                                        _freeze_json(_semantic(expected_restore[key])))
                      for key in owned_restore), 'restore_readback_changed')
        encoded = []
        for uuid, old, new, kind, payload in payloads:
            if kind == 'integration':
                body = json.loads(serialize_integration_payload(payload))
            else:
                _need(type(payload) is PowercalcGroupRestorePayload, 'group_payload_type')
                body = {'before': json.loads(payload.before_json), 'after': json.loads(payload.after_json),
                        'full_total': payload.full_total, 'precision': payload.precision, 'unit': payload.unit}
            encoded.append({'uuid': uuid, 'before_id': old, 'after_id': new, 'kind': kind, 'payload': body})
        receipt = {'kind': 'controlled_powercalc_population_receipt',
                   'schema': 2 if __version__ == '2026.11.0' else 1,
                   'root': self.chain.root_id, 'operation_id': self.chain.operation_id,
                   'fingerprint': self.chain.plan.fingerprint, 'direction': direction,
                   'intent': list(self.intent), 'source_head': intent['source_head'],
                   'population': encoded, 'pss_after_sha256': _sha(pss_after),
                   'pss_before_write_sha256': _sha(pss_current),
                   'pss_before_write_identity': _identity(pss_current_id),
                   'pss_owned': {name: pss_after_memory[name] for name in owned if name in pss_after_memory},
                   'restore_owned': {name: expected_restore[name] for name in owned_restore},
                   'restore_after_unload_sha256': _sha(restore_current),
                   'restore_after_unload_identity': _identity(restore_current_id),
                   'restore_before_write_sha256': _sha(restore_gate),
                   'restore_before_write_identity': _identity(restore_gate_id),
                   'pss_after_identity': _identity(pss_after_identity), 'restore_after_sha256': _sha(restore_after),
                   'restore_after_identity': _identity(restore_after_identity)}
        key = _event_id(['controlled_pc_population', self.chain.root_id, direction, 'receipt'])
        self.receipt = await self.chain.store.create(key, receipt)
        self._scope()
        _need(self.chain.store.read(key) == {'digest': _digest(receipt), 'payload': receipt}, 'receipt_readback')
        self.entries = entries
        self.pss_expected = {name: pss_after_memory[name] for name in targets}
        self.restore_expected = {name: expected_restore[name] for name in owned_restore}
        self.require_inactive()
        return self.receipt

    async def activate(self, context, yaml_intent, live_state, *, service_context):
        """Perform one vendor reload, then sample its actual rebuilt graph."""
        from dataclasses import replace
        from decimal import Decimal
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        from .native_discovery_inputs import async_observe_discovery_inputs, assert_discovery_managers_current
        from .native_powercalc_accepted_consumers import _accepted_sources, _documents, _prepare_profiles
        from .powercalc_profile_binding import runtime_current
        from .powercalc_runtime_observer import capture_powercalc_runtime
        from .powercalc_source_binding import PowercalcVendor, _sample
        from .yaml_validation import _settled_executor

        _need(type(context) is PhaseContext and context.step.adapter == 'yaml'
              and type(service_context) is Context and callable(live_state)
              and self.direction == context.record.direction and self.chain.active is yaml_intent,
              'setup_phase_required')
        yaml_reference = list(yaml_intent.reference)
        context.require_effect(context.step, self.direction)

        def check():
            self._scope()
            _need(self.chain.active is yaml_intent, 'yaml_intent_changed')
            live_state().assert_current(self.chain.original_receipt)

        self.require_inactive()
        check()
        _need(await self.root.journal.load(self.chain.operation_id) == context.record,
              'setup_journal_changed')
        check()
        _need(self.hass.services.has_service('powercalc', 'reload'), 'native_reload_unavailable')
        key = _event_id(['controlled_pc_setup', self.chain.root_id, self.direction, 'intent'])
        _need(self.chain.store.read(key) is None, 'setup_intent_already_consumed')
        source_head = live_state().head
        payload = {'kind': 'controlled_powercalc_setup_intent', 'schema': 1,
                   'root': self.chain.root_id, 'operation_id': self.chain.operation_id,
                   'fingerprint': self.chain.plan.fingerprint, 'direction': self.direction,
                   'phase_index': context.index, 'yaml_intent': yaml_reference,
                   'handoff': list(self.receipt), 'source_head': source_head}
        setup_intent = await self.chain.store.create(key, payload)
        self.require_inactive()
        check()
        _need(live_state().head == source_head, 'setup_source_head_changed')
        _need(await self.root.journal.load(self.chain.operation_id) == context.record,
              'setup_journal_changed')
        self.require_inactive()
        check()
        from .controlled_powercalc_setup_receipts import validate_handoff
        handoff = validate_handoff(self.chain, list(self.receipt), self.direction)
        restored, stop_watch = _watch_restored_counters(self.hass, handoff['population'])
        try:
            await self.hass.services.async_call('powercalc', 'reload', {}, blocking=True, context=service_context)
            await _settle_yaml_discovery(self.hass)
        finally:
            stop_watch()
        _need(set(restored) == {row['after_id'] for row in handoff['population']},
              'setup_restore_observation_incomplete')
        check()
        evidence = self.root.evidence
        candidate = evidence.powercalc_candidate_sources
        _need(candidate is not None, 'candidate_sources_required')
        candidate.assert_original(self.chain.original_receipt, evidence.powercalc_sources)
        selected = (replace(evidence, powercalc_sources=candidate.sources)
                    if self.direction == 'forward' else evidence)
        captured = capture_powercalc_runtime(self.hass, guard=check)
        vendor = PowercalcVendor(self.hass, with_profiles=True)
        await _settled_executor(self.hass, vendor.sources)
        check()
        prepared = await _settled_executor(self.hass, _prepare_profiles, self.chain.original_receipt, selected)
        check()
        state = live_state()
        await _settled_executor(self.hass, _accepted_sources, self.chain, state,
                                self.chain.original_receipt)
        check()
        observed = await async_observe_discovery_inputs(self.hass, guard=check)
        check()
        _documents(state, observed)
        vendor.bindings()
        from .controlled_powercalc_population import removed_group_indexes
        removed = removed_group_indexes(self.hass, captured, handoff, vendor)
        first = _sample(self.hass, vendor, selected, observed, captured, prepared,
                        removed_group_ids=removed)
        _need(first and all(row.source_verified for row in first), 'setup_graph_unverified')
        runtime_current(self.hass, captured, first, guard=check)
        await _settled_executor(self.hass, _accepted_sources, self.chain, state,
                                self.chain.original_receipt, vendor)
        check()
        assert_discovery_managers_current(self.hass, observed, guard=check)
        _documents(state, observed)
        vendor.bindings()
        _need(removed == removed_group_indexes(self.hass, captured, handoff, vendor),
              'removed_group_index_changed')
        rows = _sample(self.hass, vendor, selected, observed, captured, prepared,
                       removed_group_ids=removed)
        runtime_current(self.hass, captured, rows, guard=check)
        _need(first == rows, 'setup_graph_changed')
        directional_mapping = (self.root.mapping if self.direction == 'forward'
                               else {new: old for old, new in self.root.mapping.items()})
        expected = tuple(sorted((directional_mapping.get(name, name), uid, kind)
                                for name, uid, kind in self.population.descriptors))
        _need(tuple(sorted((row.entity_id, row.registry_id, row.relation_kind) for row in rows)) == expected,
              'setup_population_changed')
        entities = {identity: entity for platform in async_get_platforms(self.hass, 'powercalc')
                    for identity, entity in platform.entities.items()}
        handoff = self.chain.store.read(self.receipt[0])['payload']
        totals = []
        for row in handoff['population']:
            entity = entities.get(row['after_id'])
            _need(entity is not None and entity.registry_entry is not None
                  and entity.registry_entry.id == row['uuid'], 'setup_counter_identity')
            if row['kind'] == 'group':
                value = entity._native_value_exact
                expected_value = row['payload']['full_total']
                last_valid = None
            else:
                value, last_valid = entity._state, entity._last_valid_state
                expected_value = row['payload']['capture']['full_state_text']
                _need(type(last_valid) is Decimal and last_valid.is_finite(), 'setup_last_valid_invalid')
            seen = restored[row['after_id']]
            _need(Decimal(seen['value']).as_tuple() == Decimal(expected_value).as_tuple()
                  and (row['kind'] == 'group' or Decimal(seen['last_valid']).as_tuple() ==
                       Decimal(row['payload']['capture']['last_valid_state_text']).as_tuple()),
                  'setup_restore_not_exact')
            _need(type(value) is Decimal and value.is_finite(), 'setup_counter_invalid')
            totals.append({'uuid': row['uuid'], 'entity_id': row['after_id'], 'kind': row['kind'],
                           'value': str(value), 'last_valid': None if last_valid is None else str(last_valid)})
        result = {'kind': 'controlled_powercalc_setup_receipt', 'schema': 1,
                  'root': self.chain.root_id, 'operation_id': self.chain.operation_id,
                  'fingerprint': self.chain.plan.fingerprint, 'direction': self.direction,
                  'phase_index': context.index, 'yaml_intent': yaml_reference,
                  'intent': list(setup_intent), 'handoff': list(self.receipt),
                  'source_head': source_head,
                  'population': [list(row) for row in expected],
                  'restored': [restored[row['after_id']] for row in handoff['population']], 'totals': totals}
        receipt_key = _event_id(['controlled_pc_setup', self.chain.root_id, self.direction, 'receipt'])
        reference = await self.chain.store.create(receipt_key, result)
        check()
        _need(self.chain.store.read(receipt_key) == {'digest': _digest(result), 'payload': result},
              'setup_receipt_readback')
        self.setup_reference = reference
        return reference

    def claim_activation(self, chain, intent, context):
        self._scope()
        _need(chain is self.chain and intent is chain.active and type(context) is PhaseContext
              and context.record.direction == self.direction and context.step.adapter == 'yaml'
              and self.setup_reference is not None, 'setup_claim_scope')
        from .controlled_powercalc_setup_receipts import validate_controlled_setup
        setup = self.chain.store.read(self.setup_reference[0])
        _need(setup is not None, 'setup_receipt_missing')
        validate_controlled_setup(chain, {'binding': chain._context(context),
            'intent': list(intent.reference), 'powercalc_setup': list(self.setup_reference),
            'source_chain': setup['payload']['source_head']},
            chain._original_change(context.index))
        return self.setup_reference
