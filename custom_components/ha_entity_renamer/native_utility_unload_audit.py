"""DRAFT: observe one actual leased UtilityMeter unload; never mutate HA state.

Data is historical evidence; only the retained ephemeral witness can check object
continuity. Installed caller binds ORIGINAL/accepted row and durable intent/ref.
No plans, journals, recovery, grants, generic entity-ID removal or baseline capture.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from importlib import import_module
import inspect
import json
from pathlib import Path
import sys
import threading

from .native_utility_setup_audit import _SOURCES, _code_objects, _canonical, _load


class UtilityUnloadAuditError(ValueError):
    """Sanitized fail-closed refusal."""


def _need(value, reason):
    if not value:
        raise UtilityUnloadAuditError('utility_unload_audit_' + reason) from None


def _same(a, b):
    return _canonical(a) == _canonical(b)


def _native(value):
    encoder = import_module('homeassistant.helpers.json')._orjson_bytes_default_encoder
    return encoder(value).decode('utf-8')


def _row(row):
    return json.loads(_native(row.as_storage_fragment))


def _state(state):
    _need(state is not None, 'missing_state')
    result = state.as_dict_json
    _need(type(result) is bytes, 'native_state_json')
    return result.decode('utf-8')


def _expected_attrs(row, core, hass):
    # Exact transcription of pinned native writer, no simulated write/capture.
    attrs = {'restored': True}
    if row.capabilities is not None:
        attrs.update(row.capabilities)
    for name, value in (
            ('device_class', row.device_class or row.original_device_class),
            ('icon', row.icon or row.original_icon)):
        if value is not None:
            attrs[name] = value
    if core == '2025.6.0':
        name = row.name or row.original_name
        if name is not None:
            attrs['friendly_name'] = name
    else:
        er = import_module('homeassistant.helpers.entity_registry')
        name = er.async_get_legacy_friendly_name(hass, row)
        if name:
            attrs['friendly_name'] = name
    for name in ('supported_features', 'unit_of_measurement'):
        value = getattr(row, name)
        if value is not None:
            attrs[name] = value
    return json.loads(_native(attrs))



def _exact_json(raw):
    # Validate bounded native JSON without normalizing its bytes/timestamps.
    _need(type(raw) is str and len(raw.encode()) <= 1048576, 'native_json_bounds')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, 'native_json_duplicate')
            result[key] = value
        return result
    try:
        result = json.loads(raw, object_pairs_hook=unique)
        _canonical(result)
        return result
    except (ValueError, TypeError, RecursionError):
        _need(False, 'native_json_invalid')


def _bind(row, entry_id):
    _need(type(row) is dict and row.get('platform') == 'utility_meter'
          and row.get('config_entry_id') == row.get('unique_id') == entry_id
          and row.get('config_subentry_id') is None and row.get('disabled_by') is None
          and type(row.get('id')) is str and bool(row['id'])
          and type(row.get('entity_id')) is str and row['entity_id'].startswith('sensor.'),
          'original_row_binding')
    return deepcopy(row)


@dataclass(frozen=True)
class NativeUtilityUnloadAuditData:
    """Detached data, never deserializable to live authority."""
    entry_id: str
    payload_json: str


def _payload(data):
    _need(type(data) is NativeUtilityUnloadAuditData, 'typed_data_required')
    doc = _load(data.payload_json)
    _need(type(doc) is dict and set(doc) == {'schema', 'core', 'entry_id', 'source_row',
        'source_pins', 'state_json', 'reserved', 'call', 'writer_row'}, 'data_layout')
    _need(type(doc['schema']) is int and doc['schema'] == 1 and doc['core'] in _SOURCES
          and doc['entry_id'] == data.entry_id, 'data_binding')
    _bind(doc['source_row'], data.entry_id)
    _bind(doc['writer_row'], data.entry_id)
    ignored = {'options', 'modified_at'}
    _need(_same({k: v for k, v in doc['source_row'].items() if k not in ignored},
                {k: v for k, v in doc['writer_row'].items() if k not in ignored}),
          'writer_row_binding')
    _need(_same(doc['source_pins'], _SOURCES[doc['core']]), 'data_source_pins')
    _need(type(doc['reserved']) is bool and doc['reserved'], 'reservation')
    call = doc['call']
    _need(type(call) is dict and set(call) == {'count', 'path', 'row_json', 'return_none',
        'same_actor', 'same_hass', 'same_row', 'same_state_at_finish',
        'native_unloader_return_true', 'same_native_set_state'}, 'call_layout')
    _need(type(call['count']) is int and call['count'] == 1
          and call['path'] == ['RegistryEntry.write_unavailable_state',
                              'Entity.__async_remove_impl', 'Entity.async_remove']
          and all(call[k] is True for k in ('return_none', 'same_actor', 'same_hass',
                   'same_row', 'same_state_at_finish', 'native_unloader_return_true',
                   'same_native_set_state')), 'call_proof')
    _need(type(call['row_json']) is str and _same(_exact_json(call['row_json']), doc['writer_row']),
          'call_row')
    _need(type(doc['state_json']) is str and len(doc['state_json'].encode()) <= 1048576,
          'state_json')
    state = _exact_json(doc['state_json'])
    _need(type(state) is dict and set(state) == {'entity_id', 'state', 'attributes',
          'last_changed', 'last_reported', 'last_updated', 'context'}
          and state['entity_id'] == doc['source_row']['entity_id']
          and state['state'] == 'unavailable' and type(state['attributes']) is dict
          and state['attributes'].get('restored') is True, 'state_scope')
    from datetime import datetime
    for key in ('last_changed', 'last_reported', 'last_updated'):
        _need(type(state[key]) is str and datetime.fromisoformat(state[key]).tzinfo is not None,
              'state_timestamp')
    _need(type(state['context']) is dict and set(state['context']) ==
          {'id', 'parent_id', 'user_id'} and type(state['context']['id']) is str
          and all(state['context'][k] is None or type(state['context'][k]) is str
                  for k in ('parent_id', 'user_id')), 'state_context')
    return doc


def encode_unload_audit(data):
    return {'entry_id': data.entry_id, 'payload': _payload(data)}


def decode_unload_audit(value):
    _need(type(value) is dict and set(value) == {'entry_id', 'payload'}
          and type(value['entry_id']) is str, 'envelope_layout')
    data = NativeUtilityUnloadAuditData(value['entry_id'], _canonical(value['payload']))
    _payload(data)
    return data


def validate_unload_audit(data, *, expected_core, expected_entry_id, expected_row,
                          expected_attrs):
    """Pure exact-scope validator; caller authenticates durable provenance.

    expected_row/attrs come independently from ORIGINAL plus accepted source.
    Does not prove live object continuity or create an unload/cleanup capability.
    """
    doc = _payload(data)
    _need(doc['core'] == expected_core and doc['entry_id'] == expected_entry_id
          and _same(doc['source_row'], _bind(expected_row, expected_entry_id))
          and _same(_exact_json(doc['state_json'])['attributes'], expected_attrs),
          'expected_scope')
    return True


class NativeUtilityUnloadWitness:
    """EPHEMERAL, same-task, exact actual State identity. No reopen/copy/pickle."""
    __slots__ = ('_audit', '_data')

    def __init__(self):
        _need(False, 'live_unload_required')

    @property
    def data(self):
        return self._data

    def __copy__(self):
        _need(False, 'ephemeral_only')

    def __deepcopy__(self, memo):
        _need(False, 'ephemeral_only')

    def __reduce_ex__(self, protocol):
        _need(False, 'ephemeral_only')

    def check_live(self, *, hass, entry, lease, actual_state, expected_data=None):
        try:
            return self._check_live(hass=hass, entry=entry, lease=lease,
                actual_state=actual_state, expected_data=expected_data)
        except BaseException:
            self._audit._poison('live_continuity_check_failed')
            raise

    def _check_live(self, *, hass, entry, lease, actual_state, expected_data):
        audit = self._audit
        _need(audit._witness is self and audit._finished and audit._error is None,
              'witness_scope')
        _need(hass is audit.hass and entry is audit.entry and lease is audit.lease
              and actual_state is audit._captured, 'live_binding')
        audit._check_runtime(owner=True)
        audit._quiescent()
        audit._continuity()
        _need(self._data == audit._data and (expected_data is None
              or type(expected_data) is NativeUtilityUnloadAuditData
              and expected_data == self._data), 'typed_data_changed')
        validate_unload_audit(self._data, expected_core=audit.core,
            expected_entry_id=entry.entry_id, expected_row=audit.expected_row,
            expected_attrs=audit._attrs)
        return True


class NativeUtilityUnloadAudit:
    """Single-use wrapper of actual lease.async_unload; no HA monkeypatch.

    Construct from the caller-authenticated ORIGINAL/accepted full row, immediately
    before the native API. async_unload returns a live witness before any other await.
    Profile callback never propagates any BaseException into Home Assistant.
    """
    def __init__(self, hass, actual_entry, expected_row, *, guard):
        _need(callable(guard), 'installed_guard_required')
        self.hass, self.entry, self.guard = hass, actual_entry, guard
        self._installed_guard = guard
        self.expected_row = _bind(expected_row, actual_entry.entry_id)
        self._callback = self._profile
        self._error = None
        self._entered = self._active = self._finished = False
        self._captured = self._data = self._witness = None
        self._write_frame = None
        self._count = 0
        self._return_none = False
        self._manager_frame = None
        self._manager_returned = False
        self._native_set_state = None
        self._set_count = 0

    def _poison(self, reason):
        if self._error is None:
            self._error = reason

    def _guard(self):
        _need(self.guard is self._installed_guard, 'guard_changed')
        result = self.guard()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            _need(False, 'sync_guard_required')
        _need(result is None or result is True, 'guard_lost')

    def _install_scope(self):
        # Existing installer verifies all 13 exact source pins. Additional methods
        # must match code compiled from these same installed full source files.
        from .native_utility_setup_audit import _installed
        self.core, self.classes, _ = _installed()
        import homeassistant
        self._root = Path(homeassistant.__file__).resolve().parent
        self._modules = {name: import_module('homeassistant.' + name) for name in (
            'helpers.entity_registry', 'helpers.entity', 'helpers.entity_platform',
            'helpers.restore_state', 'core', 'config_entries', 'components.utility_meter.sensor')}
        self.er = self._modules['helpers.entity_registry']
        entity = self._modules['helpers.entity'].Entity
        self._refs = [(self.er.RegistryEntry, 'write_unavailable_state'),
            (entity, '_Entity__async_remove_impl'), (entity, 'async_remove'),
            (self._modules['config_entries'].ConfigEntries, 'async_unload'),
            (self._modules['core'].StateMachine, 'async_set'),
            (self._modules['core'].StateMachine, 'get'),
            (self._modules['core'].StateMachine, 'async_set_internal'),
            (self._modules['core'].State, '__init__')]
        if self.core == '2026.10.2':
            self._refs.append((self.er, 'async_get_legacy_friendly_name'))
        self._methods = tuple(getattr(obj, name) for obj, name in self._refs)
        compiled = {}
        for (owner, method_name), method in zip(self._refs, self._methods, strict=True):
            code = getattr(method, '__code__', None)
            _need(code is not None, 'native_code_required')
            path = Path(code.co_filename)
            _need(path.resolve() == path and path.is_relative_to(self._root), 'code_path')
            if str(path) not in compiled:
                compiled[str(path)] = tuple(_code_objects(compile(path.read_bytes(), str(path),
                    'exec', dont_inherit=True, optimize=sys.flags.optimize)))
            expected_name = ('Entity.__async_remove_impl' if method_name ==
                '_Entity__async_remove_impl' else (method_name if inspect.ismodule(owner)
                else owner.__name__ + '.' + method_name))
            _need(path.relative_to(self._root).as_posix() in _SOURCES[self.core]
                  and any(code == item and item.co_qualname == expected_name
                          for item in compiled[str(path)]), 'native_code_changed')
        self._codes = tuple(method.__code__ for method in self._methods)
        self._pins = deepcopy(_SOURCES[self.core])
        self._pins_live()
        self._lease_methods = (type(self.lease).check, type(self.lease).owns_entry,
                               type(self.lease).async_unload)
        self._class_refs = tuple((module, name, getattr(module, name)) for module, name in (
            (self.er, 'RegistryEntry'), (self.er, 'EntityRegistry'),
            (self._modules['helpers.entity_platform'], 'EntityPlatform'),
            (self._modules['components.utility_meter.sensor'], 'UtilityMeterSensor'),
            (self._modules['core'], 'State'), (self._modules['core'], 'StateMachine')))
        self._native_descriptors = tuple((cls, name, getattr(cls, name)) for cls, name in (
            (self._modules['core'].State, 'as_dict_json'),
            (self.er.RegistryEntry, 'as_storage_fragment')))

    def _pins_live(self):
        _need(import_module('homeassistant.const').__version__ == self.core, 'core_changed')
        for relative, digest in self._pins.items():
            path = self._root / relative
            _need(path.resolve() == path and hashlib.sha256(path.read_bytes()).hexdigest()
                  == digest, 'source_changed')

    def _check_runtime(self, *, owner):
        _need(self._error is None, self._error or 'poisoned')
        _need(threading.get_ident() == self._thread and self.hass.loop is self._loop
              and asyncio.get_running_loop() is self._loop
              and not self._task.done(), 'task_loop_changed')
        if owner:
            _need(asyncio.current_task() is self._task, 'same_task_required')
        self._guard()
        self.lease.check()
        _need(self.lease.owns_entry(self.entry) and self.lease.manager is self._manager
              and self.hass.config_entries is self._manager
              and self._manager.async_get_entry(self.entry.entry_id) is self.entry,
              'lease_manager_entry_changed')
        _need(tuple(getattr(obj, name) for obj, name in self._refs) == self._methods
              and all(method.__code__ is code for method, code in
                      zip(self._methods, self._codes, strict=True)), 'native_method_changed')
        _need((type(self.lease).check, type(self.lease).owns_entry,
               type(self.lease).async_unload) == self._lease_methods,
              'lease_method_changed')
        _need(all(getattr(cls, name) is value for cls, name, value in self._native_descriptors),
              'native_descriptor_changed')
        _need(self.hass.states is self._machine and self.hass.data.get(self.er.DATA_REGISTRY)
              is self._registry, 'native_manager_changed')
        _need(self._registry.async_get(self.expected_row['entity_id']) is self._manager_row
              and _same(_row(self._manager_row), self.expected_row), 'source_row_changed')
        _need(self._entity.registry_entry is self._row_object
              and _same(_row(self._row_object), self._writer_row), 'writer_row_changed')
        _need(all(getattr(module, name) is cls for module, name, cls in self._class_refs),
              'native_class_changed')
        for obj, name, method in ((self._row_object, 'write_unavailable_state', self._methods[0]),
                (self._entity, 'async_remove', self._methods[2]),
                (self._manager, 'async_unload', self._methods[3]),
                (self._machine, 'async_set', self._methods[4]),
                (self._machine, 'get', self._methods[5]),
                (self._machine, 'async_set_internal', self._methods[6])):
            bound = getattr(obj, name)
            _need(getattr(bound, '__self__', None) is obj
                  and getattr(bound, '__func__', None) is method, 'bound_method_changed')
        self._pins_live()

    def _continuity(self):
        _need(type(self._captured) is self._modules['core'].State
              and self._machine.get(self.expected_row['entity_id']) is self._captured
              and _state(self._captured) == self._state_json
              and self.expected_row['entity_id'] in self._machine._reservations,
              'state_object_continuity_lost')

    def _quiescent(self):
        from .native_helper_transaction import _require_inactive
        _require_inactive(self.entry, self.lease)
        ep = self._modules['helpers.entity_platform']
        _need(not any(p.config_entry is self.entry and p.entities
                      for p in ep.async_get_platforms(self.hass, 'utility_meter')),
              'platform_still_active')
        restore = self._modules['helpers.restore_state']
        data = self.hass.data.get(restore.DATA_RESTORE_STATE)
        _need(type(data) is restore.RestoreStateData and data is self._restore
              and self.expected_row['entity_id'] not in data.entities
              and not any(value is self._entity for value in data.entities.values()),
              'restore_entity_active')
        _need(self._entity._collecting is None, 'collecting_callback_active')
        utility = import_module('homeassistant.components.utility_meter.const')
        _need(self.entry.entry_id not in self.hass.data.get(utility.DATA_UTILITY, {}),
              'utility_runtime_active')
        event = import_module('homeassistant.helpers.event')
        tracker = self.hass.data.get(event._TRACK_STATE_CHANGE_DATA)
        if tracker is not None:
            _need(not any(getattr(getattr(job.target, '__self__', None), '_parent_meter', None)
                          == self.entry.entry_id for jobs in tracker.callbacks.values()
                          for job in jobs), 'native_callback_active')

    def _begin(self, lease):
        _need(not self._entered, 'single_use')
        self._entered = True
        _need(sys.getprofile() is None, 'existing_profile')
        from .native_helper_activation_lease import NativeHelperActivationLease
        _need(type(lease) is NativeHelperActivationLease, 'actual_lease_required')
        self.lease = lease
        self._task, self._loop, self._thread = (asyncio.current_task(),
            asyncio.get_running_loop(), threading.get_ident())
        _need(self._task is not None, 'task_required')
        self._guard()
        lease.check()
        _need(lease.owns_entry(self.entry), 'lease_entry')
        self._manager = self.hass.config_entries
        self._install_scope()
        _need(type(self.hass) is self._modules['core'].HomeAssistant
              and type(self.entry) is self._modules['config_entries'].ConfigEntry
              and type(self._manager) is self._modules['config_entries'].ConfigEntries
              and self.entry.domain == 'utility_meter' and self.entry.state.value == 'loaded'
              and self.entry.disabled_by is None, 'actual_loaded_entry_required')
        self._registry = self.hass.data.get(self.er.DATA_REGISTRY)
        self._machine = self.hass.states
        _need(type(self._registry) is self.er.EntityRegistry
              and type(self._machine) is self._modules['core'].StateMachine,
              'actual_managers_required')
        rows = list(self.er.async_entries_for_config_entry(self._registry, self.entry.entry_id))
        _need(len(rows) == 1 and type(rows[0]) is self.er.RegistryEntry
              and _same(_row(rows[0]), self.expected_row), 'accepted_registry_row')
        self._manager_row = rows[0]
        ep = self._modules['helpers.entity_platform']
        matches = [(p, e) for p in ep.async_get_platforms(self.hass, 'utility_meter')
            if p.config_entry is self.entry for e in p.entities.values()]
        _need(len(matches) == 1, 'bound_entity_count')
        self._platform, self._entity = matches[0]
        cls = self._modules['components.utility_meter.sensor'].UtilityMeterSensor
        _need(type(self._entity) is cls and self._platform.hass is self.hass
              and self._entity.hass is self.hass
              and self._entity.platform is self._platform
              and type(self._entity.registry_entry) is self.er.RegistryEntry
              and self._entity.entity_id == self.expected_row['entity_id']
              and self._entity.unique_id == self.entry.entry_id
              and self._entity._parent_meter == self.entry.entry_id, 'bound_entity_identity')
        self._row_object = self._entity.registry_entry
        self._writer_row = _bind(_row(self._row_object), self.entry.entry_id)
        # Both pinned Cores can retain an older row after native sensor display
        # options updates. The actual unload writer reads no options or audit
        # timestamp. Preserve BOTH complete rows; every writer-relevant field
        # and the actual produced attributes must still match independently.
        ignored = {'options', 'modified_at'}
        _need(_same({k: v for k, v in self._writer_row.items() if k not in ignored},
                    {k: v for k, v in self.expected_row.items() if k not in ignored}),
              'bound_writer_row')
        restore = self._modules['helpers.restore_state']
        self._restore = self.hass.data.get(restore.DATA_RESTORE_STATE)
        _need(type(self._restore) is restore.RestoreStateData, 'restore_manager')
        self._attrs = _expected_attrs(self._manager_row, self.core, self.hass)
        _need(_same(_expected_attrs(self._row_object, self.core, self.hass), self._attrs),
              'bound_writer_attributes')
        self._check_runtime(owner=True)
        _need(sys.getprofile() is None, 'existing_profile')
        self._active = True
        sys.setprofile(self._callback)

    def _profile(self, frame, event, arg):
        try:
            if not self._active or event not in ('call', 'return'):
                return
            if frame.f_code is self._codes[3]:
                _need(frame.f_locals.get('self') is self._manager
                      and frame.f_locals.get('entry_id') == self.entry.entry_id
                      and frame.f_locals.get('_lock') is False, 'actual_unloader_scope')
                if event == 'call':
                    _need(self._manager_frame is None or self._manager_frame is frame,
                          'multiple_native_unloaders')
                    self._manager_frame = frame
                elif arg is True:
                    _need(frame is self._manager_frame and not self._manager_returned,
                          'native_unloader_return')
                    self._manager_returned = True
                return
            if frame.f_code is self._codes[6]:
                # Capture the exact State constructed and assigned by native core,
                # not a same-JSON replacement from a synchronous event listener.
                parent = frame.f_back
                outer = None if parent is None else parent.f_back
                if (frame.f_locals.get('self') is self._machine and
                        frame.f_locals.get('entity_id') == self.expected_row['entity_id']):
                    _need(parent is not None and parent.f_code is self._codes[4]
                          and parent.f_locals.get('self') is self._machine
                          and outer is self._write_frame, 'native_set_cause')
                    if event == 'call':
                        self._set_count += 1
                        _need(self._set_count == 1, 'multiple_native_sets')
                    else:
                        created = frame.f_locals.get('state')
                        _need(arg is None and type(created) is self._modules['core'].State
                              and self._machine.get(self.expected_row['entity_id']) is created,
                              'native_set_object_replaced_or_noop')
                        self._native_set_state = created
                return
            if frame.f_code is not self._codes[0]:
                return
            row, hass = frame.f_locals.get('self'), frame.f_locals.get('hass')
            # Unrelated native entries are outside this bounded audit.
            if row is not self._row_object:
                parent = frame.f_back
                if parent is not None and parent.f_locals.get('self') is self._entity:
                    self._poison('actor_foreign_row')
                return
            _need(hass is self.hass, 'writer_hass')
            if event == 'call':
                _need(self._write_frame is None and self._count == 0, 'duplicate_write')
                parent = frame.f_back
                outer = None if parent is None else parent.f_back
                _need(parent is not None and parent.f_code is self._codes[1]
                      and parent.f_locals.get('self') is self._entity
                      and parent.f_locals.get('force_remove') is False
                      and outer is not None and outer.f_code is self._codes[2]
                      and outer.f_locals.get('self') is self._entity
                      and outer.f_locals.get('force_remove') is False,
                      'native_causal_frame')
                _need(self._entity.registry_entry is row and self._entity.hass is hass
                      and self._registry.async_get(row.entity_id) is self._manager_row
                      and _same(_row(row), self._writer_row), 'writer_row_binding')
                self._write_frame = frame
                self._count += 1
                self._row_json = _native(row.as_storage_fragment)
            else:
                _need(frame is self._write_frame and arg is None, 'writer_return')
                state = self._machine.get(self.expected_row['entity_id'])
                _need(state is self._native_set_state and self._set_count == 1
                      and type(state) is self._modules['core'].State
                      and state.state == 'unavailable'
                      and _same(json.loads(_state(state))['attributes'], self._attrs),
                      'native_unavailable_state')
                _need(self.expected_row['entity_id'] in self._machine._reservations,
                      'native_reservation')
                self._captured, self._state_json = state, _state(state)
                self._return_none = True
                self._write_frame = None
        except BaseException as error:
            # Never throw into HA's writer. Retain only a fixed public reason,
            # never arbitrary exception text, row values, paths or native data.
            reason = 'profile_observation_refused'
            known = {'actual_unloader_scope', 'multiple_native_unloaders',
                'native_unloader_return', 'native_set_cause', 'multiple_native_sets',
                'native_set_object_replaced_or_noop', 'writer_hass', 'duplicate_write',
                'native_causal_frame', 'writer_row_binding', 'writer_return',
                'native_unavailable_state', 'native_reservation', 'missing_state', 'native_state_json'}
            if (type(error) is UtilityUnloadAuditError and len(error.args) == 1
                    and type(error.args[0]) is str):
                prefix = 'utility_unload_audit_'
                code = error.args[0][len(prefix):] if error.args[0].startswith(prefix) else ''
                if code in known:
                    reason = 'profile_' + code
            self._poison(reason)

    def _cleanup(self):
        if self._active:
            if sys.getprofile() is self._callback:
                # Disable first: profile sees sys.setprofile's c_call/c_return.
                self._active = False
                sys.setprofile(None)
            else:
                self._active = False
                self._poison('foreign_profiler_replaced')
        self._write_frame = None
        self._manager_frame = None

    async def async_unload(self, lease):
        """Execute exactly one actual lease unload and synchronously seal proof."""
        try:
            self._begin(lease)
            _need(sys.getprofile() is self._callback, 'profile_lost')
            result = await lease.async_unload(self.entry.entry_id)
            # No settle, guard await, receipt save or idle barrier before finish.
            _need(sys.getprofile() is self._callback, 'profile_lost')
            self._check_runtime(owner=True)
            _need(result is True and self._count == 1 and self._return_none
                  and self._write_frame is None and self._manager_returned,
                  'one_completed_native_write_required')
            self._quiescent()
            self._continuity()
            doc = {'schema': 1, 'core': self.core, 'entry_id': self.entry.entry_id,
                'source_row': self.expected_row, 'writer_row': self._writer_row,
                'source_pins': self._pins,
                'state_json': self._state_json, 'reserved': True,
                'call': {'count': 1, 'path': ['RegistryEntry.write_unavailable_state',
                    'Entity.__async_remove_impl', 'Entity.async_remove'],
                    'row_json': self._row_json, 'return_none': True, 'same_actor': True,
                    'same_hass': True, 'same_row': True, 'same_state_at_finish': True,
                    'native_unloader_return_true': True, 'same_native_set_state': True}}
            self._data = NativeUtilityUnloadAuditData(self.entry.entry_id, _canonical(doc))
            validate_unload_audit(self._data, expected_core=self.core,
                expected_entry_id=self.entry.entry_id, expected_row=self.expected_row,
                expected_attrs=self._attrs)
            self._finished = True
            witness = object.__new__(NativeUtilityUnloadWitness)
            witness._audit, witness._data = self, self._data
            self._witness = witness
            return witness
        except BaseException:
            self._poison('unload_failed_or_cancelled')
            raise
        finally:
            self._cleanup()
