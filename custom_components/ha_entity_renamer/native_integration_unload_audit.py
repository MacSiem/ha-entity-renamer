"""One actual leased Integration unload and its exact immutable native data.

Default mode has no timer; optional typed own clock retains the same engine.
Witness authenticates capture only, never a plan/rename/recovery authorization.
Uses the Utility unload audit's pinned-code proof and native registry projection.
No native field/method replacement, synthetic event or expected counter input.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import hashlib
from importlib import import_module
from pathlib import Path
import sys
import threading

from . import native_restore_state_payload as codec
from .native_helper_activation_lease import NativeHelperActivationLease
from .native_unload_code_proof import prove_methods, unchanged
from .native_utility_setup_audit import _SOURCES
from .native_utility_unload_audit import _row

_INTEGRATION = {
    '2025.6.0': 'a95b49d6c3d9758856011c1ffc8d1b80567c367ccfb564b7b63d4298a65c7d60',
    '2026.10.2': '7cdc1d9b9ac994c240d6d1458d09c8666db835e2ab003e28f203869749fbb24f',
}
_INIT = {
    '2025.6.0': '282ec19412f1dc291e0e2bc983285266cfdc4eb41d416178e1b5c0a4a5262a7b',
    '2026.10.2': 'a1e9e24c684782287053eedea3d48c4852e0b30c7df8dbf56e0a72141a4de24b',
}


def _need(value, reason):
    if not value:
        raise ValueError('integration_unload_' + reason) from None


def _decimal(value):
    _need(type(value) is Decimal and value.is_finite(), 'finite_decimal_required')
    return str(value)


@dataclass(frozen=True)
class IntegrationUnloadCapture:
    """Data-only evidence. Reconstructing it never yields a live witness."""
    core_version: str
    entry_id: str
    helper_uuid: str
    entity_id: str
    full_state_text: str
    last_valid_state_text: str
    before_sha256: str


class NativeIntegrationUnloadWitness:
    __slots__ = ('_audit',)

    def __init__(self):
        _need(False, 'actual_unload_required')

    def __copy__(self):
        _need(False, 'ephemeral_only')

    def __deepcopy__(self, memo):
        _need(False, 'ephemeral_only')

    def __reduce_ex__(self, protocol):
        _need(False, 'ephemeral_only')

    @property
    def capture(self):
        self.check_live()
        return self._audit.capture

    def check_live(self, *, lease=None, data=None, stored=None):
        audit = self._audit
        _need(audit._witness is self and audit._finished and audit._error is None,
              'witness_identity')
        audit._check()
        _need((lease is None or lease is audit.lease)
              and (data is None or data is audit.data)
              and (stored is None or stored is audit._stored), 'witness_scope')
        audit._quiescent()
        expected = audit._post if audit._post is not None else audit._stored
        _need(audit.data.last_states.get(audit.capture.entity_id) is expected,
              'stored_object_changed')
        raw = codec._canonical(codec._capture(expected, codec._native()))
        _need(raw == (audit._payload.after_json if audit._post is not None
                     else audit._before), 'stored_payload_changed')
        if audit._target is not None:
            target = codec._parse(audit._payload.after_json)['state']['entity_id']
            _need(audit.data.last_states.get(target) is audit._target
                  and codec._canonical(codec._capture(audit._target, codec._native()))
                      == audit._payload.after_json, 'target_payload_changed')
        if audit.clock is None:
            _need(str(audit.entity._state) == audit.capture.full_state_text
                  and str(audit.entity._last_valid_state) == audit.capture.last_valid_state_text,
                  'departed_entity_changed')
        else:
            # Capture/stored payload stays immutable. Only the same owned native
            # engine may progress during escrow awaits; it is not a new capture.
            audit.clock._check(owner=True)
            _need(audit.clock.old is audit.entity and audit.clock.lease is audit.lease
                  and audit.clock._retained
                  and audit.clock._phase in ('unloading', 'retained'), 'retained_clock_lost')
            audit.clock._timer(audit.entity)
        return None

    @property
    def data(self):
        return self._audit._own_data

    def check_placeholder_live(self, *, hass, entry, lease, actual_state, expected_data=None):
        self.check_live(lease=lease)
        audit = self._audit
        from .native_utility_unload_audit import _state
        _need(hass is audit.hass and entry is audit.entry
              and actual_state is audit._placeholder
              and hass.states.get(audit.capture.entity_id) is actual_state
              and _state(actual_state) == audit._placeholder_json
              and (audit.capture.entity_id in hass.states._reservations) is audit._reserved
              and (expected_data is None or expected_data == self.data),
              'placeholder_live_continuity')
        integration_own_unload_doc(self.data)
        return True

    def _claim(self, io, payload):
        # One attempted operation, even if disk IO fails before mutation.
        from .native_restore_state_escrow_io import EscrowIO
        _need(type(io) is EscrowIO, 'native_io_required')
        self.check_live(lease=io.lease, data=io.data)
        audit = self._audit
        _need(io.entry is audit.entry and not audit._attempted, 'one_transfer_required')
        codec.validate_integration_capture(payload, self)
        audit._attempted, audit._io, audit._payload = True, io, payload

    def _written(self, io, clone):
        audit = self._audit
        target = codec._parse(audit._payload.after_json)['state']['entity_id']
        _need(audit._attempted and audit._io is io and audit._post is None
              and audit._target is None and audit.data.last_states.get(target) is clone,
              'actual_transfer_required')
        _need(codec._canonical(codec._capture(clone, codec._native()))
              == audit._payload.after_json, 'transfer_postimage')
        target = codec._parse(audit._payload.after_json)['state']['entity_id']
        if target == audit.capture.entity_id:
            audit._post = clone
        else:
            audit._target = clone
        self.check_live(lease=io.lease, data=io.data)


class NativeIntegrationUnloadAudit:
    """Fixed native class, fixed pins, actual getter/remove/unloader causality.

    Caller supplies its authenticated row and holds its own plan/admin fences.
    This prerequisite does not implement own-ID registry or source/time-gap AC.
    """
    def __init__(self, hass, entry, expected_row, *, clock=None):
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import entity_platform as ep
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
        from homeassistant.components.integration.sensor import IntegrationSensor
        _need(type(hass) is HomeAssistant and type(entry) is ConfigEntry
              and type(hass.config_entries) is ConfigEntries
              and hass.config_entries.async_get_entry(entry.entry_id) is entry
              and entry.domain == 'integration' and entry.state.value == 'loaded'
              and entry.disabled_by is None, 'actual_loaded_entry')
        registry = er.async_get(hass)
        row = registry.async_get(expected_row.get('entity_id'))
        _need(type(row) is er.RegistryEntry and _row(row) == expected_row
              and row.platform == 'integration' and row.config_entry_id == entry.entry_id
              and row.unique_id == entry.entry_id and row.config_subentry_id is None
              and row.disabled_by is None, 'registry_binding')
        rows = list(er.async_entries_for_config_entry(registry, entry.entry_id))
        entities = [(p, e) for p in ep.async_get_platforms(hass, 'integration')
                    if p.config_entry is entry for e in p.entities.values()]
        _need(len(rows) == 1 and len(entities) == 1, 'single_helper_required')
        platform, entity = entities[0]
        if clock is not None:
            from .native_integration_continuity import NativeIntegrationContinuity
            from .native_integration_own_clock import IntegrationOwnClockBinding
            _need(type(clock) is NativeIntegrationContinuity
                  and type(clock.source_binding) is IntegrationOwnClockBinding
                  and clock.hass is hass and clock.entry is entry and clock.old is entity
                  and clock.row is row and clock._phase == 'capture', 'own_clock_binding')
            clock._check(owner=True)
        self.clock = clock
        _need(type(entity) is IntegrationSensor and entity.entity_id == row.entity_id
              and entity.unique_id == entry.entry_id and entity.hass is hass
              and entity.platform is platform and platform.hass is hass
              and ((clock is None and entity._max_sub_interval is None)
                   or (clock is not None and entity._max_sub_interval is not None)),
              'native_no_timer_helper')
        data = hass.data.get(DATA_RESTORE_STATE)
        _need(type(data) is RestoreStateData and data.entities.get(row.entity_id) is entity,
              'native_restore_manager')
        self.hass, self.entry, self.registry, self.row = hass, entry, registry, row
        self.entity, self.data, self._expected_row = entity, data, codec._canonical(expected_row)
        self._started = self._finished = self._attempted = False
        self._error = self._witness = self._post = self._payload = self._io = None
        self._extra = self._stored = self._before = self.capture = None
        self._target = self._placeholder = self._writer_frame = None
        self._native_set = None
        self._placeholder_count = self._set_count = 0
        self._own_data = None
        self._getter_count = self._remove_count = 0
        self._manager_frame = None
        self._manager_done = False
        self._callback = self._profile

    def _install(self):
        from homeassistant.const import __version__
        from homeassistant.helpers.entity import Entity
        from homeassistant.helpers.restore_state import RestoreEntity, RestoreStateData
        from homeassistant.components.integration.sensor import (
            IntegrationSensor, IntegrationSensorExtraStoredData)
        from homeassistant.components.sensor import SensorExtraStoredData
        self.version = __version__
        _need(self.version in _INTEGRATION and sys.implementation.name == 'cpython', 'pin')
        self.root = Path(import_module('homeassistant').__file__).resolve().parent
        self.pins = {path: _SOURCES[self.version][path] for path in (
            'components/sensor/__init__.py', 'helpers/restore_state.py',
            'helpers/entity.py', 'config_entries.py', 'helpers/entity_platform.py',
            'helpers/entity_registry.py', 'core.py')}
        self.pins['components/integration/sensor.py'] = _INTEGRATION[self.version]
        self.pins['components/integration/__init__.py'] = _INIT[self.version]
        self.refs = ((IntegrationSensor, 'extra_restore_state_data'),
            (RestoreEntity, 'async_internal_will_remove_from_hass'),
            (RestoreStateData, 'async_restore_entity_removed'),
            (Entity, '_Entity__async_remove_impl'),
            (type(self.hass.config_entries), 'async_unload'),
            (IntegrationSensor, 'native_value'),
            (IntegrationSensorExtraStoredData, 'as_dict'),
            (IntegrationSensorExtraStoredData, 'from_dict'),
            (SensorExtraStoredData, 'as_dict'), (SensorExtraStoredData, 'from_dict'),
            (IntegrationSensor, '__init__'),
            (import_module('homeassistant.components.integration'), 'async_unload_entry'),
            (type(self.row), 'write_unavailable_state'),
            (type(self.hass.states), 'async_set'),
            (type(self.hass.states), 'async_set_internal'))
        self.methods, self.codes = prove_methods(self.root, self.pins, self.refs)
        self.extra_class = IntegrationSensorExtraStoredData
        self.class_refs = tuple((import_module('homeassistant.' + module), name, cls)
            for module, name, cls in (
                ('components.integration.sensor', 'IntegrationSensor', IntegrationSensor),
                ('components.integration.sensor', 'IntegrationSensorExtraStoredData',
                 IntegrationSensorExtraStoredData),
                ('helpers.restore_state', 'RestoreEntity', RestoreEntity),
                ('helpers.restore_state', 'RestoreStateData', RestoreStateData),
                ('components.sensor', 'SensorExtraStoredData', SensorExtraStoredData)))

    def _check(self):
        if self._error is not None:
            raise ValueError(self._error) from None
        _need(threading.get_ident() == self._thread
              and asyncio.current_task() is self._task
              and asyncio.get_running_loop() is self._loop, 'live_task_required')
        self.lease._task_check()
        self.lease.check()
        _need(self.lease.owns_entry(self.entry) and self.lease.manager is self.hass.config_entries
              and self.registry.async_get(self.row.entity_id) is self.row
              and codec._canonical(_row(self.row)) == self._expected_row,
              'lease_or_registry_changed')
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from homeassistant.helpers import entity_registry as er
        _need(self.hass.data.get(DATA_RESTORE_STATE) is self.data
              and self.hass.data.get(er.DATA_REGISTRY) is self.registry, 'manager_changed')
        unchanged(self.refs, self.methods, self.codes)
        _need(all(getattr(module, name) is cls for module, name, cls in self.class_refs),
              'native_class_changed')
        _need(import_module('homeassistant.const').__version__ == self.version, 'core_changed')
        for relative, digest in self.pins.items():
            path = self.root / relative
            _need(path.resolve() == path and hashlib.sha256(path.read_bytes()).hexdigest()
                  == digest, 'source_changed')

    def _quiescent(self):
        from .native_helper_transaction import _require_inactive
        from homeassistant.helpers.entity_platform import async_get_platforms
        from .transaction import Refused
        try:
            _require_inactive(self.entry, self.lease)
        except Refused:
            _need(False, 'entry_not_quiescent')
        _need(not any(p.config_entry is self.entry and p.entities
                      for p in async_get_platforms(self.hass, 'integration'))
              and self.row.entity_id not in self.data.entities
              and not self.entity._on_remove, 'entity_still_active')

    def _profile(self, frame, event, arg):
        if self._error is not None or event not in ('call', 'return'):
            return
        try:
            if frame.f_code is self.codes[14] and frame.f_locals.get('self') is self.hass.states:
                if frame.f_locals.get('entity_id') == self.row.entity_id:
                    parent = frame.f_back
                    _need(parent is not None and parent.f_code is self.codes[13]
                          and parent.f_back is self._writer_frame, 'placeholder_native_set_cause')
                    if event == 'call':
                        self._set_count += 1
                        _need(self._set_count == 1, 'one_placeholder_set')
                    else:
                        self._native_set = frame.f_locals.get('state')
                        _need(arg is None and self.hass.states.get(self.row.entity_id)
                              is self._native_set, 'placeholder_set_identity')
            if frame.f_code is self.codes[12] and frame.f_locals.get('self') is self.entity.registry_entry:
                if event == 'call':
                    parent = frame.f_back
                    _need(parent is not None and parent.f_code is self.codes[3]
                          and parent.f_locals.get('self') is self.entity
                          and parent.f_locals.get('force_remove') is False
                          and frame.f_locals.get('hass') is self.hass
                          and self._placeholder_count == 0, 'placeholder_writer_cause')
                    self._writer_frame = frame
                    self._writer_row = _row(frame.f_locals['self'])
                else:
                    from .native_utility_unload_audit import _expected_attrs, _state
                    state = self.hass.states.get(self.row.entity_id)
                    _need(frame is self._writer_frame and arg is None
                          and self._set_count == 1 and state is self._native_set
                          and state.state == 'unavailable'
                          and dict(state.attributes) == _expected_attrs(self.row, self.version, self.hass),
                          'placeholder_writer_result')
                    self._placeholder, self._placeholder_json = state, _state(state)
                    self._reserved = self.row.entity_id in self.hass.states._reservations
                    self._placeholder_count += 1
                    self._writer_frame = None
            if frame.f_code is self.codes[4]:
                if frame.f_locals.get('entry_id') != self.entry.entry_id:
                    return
                _need(frame.f_locals.get('self') is self.hass.config_entries
                      and frame.f_locals.get('_lock') is False, 'native_unloader')
                if event == 'call':
                    _need(self._manager_frame is None or self._manager_frame is frame,
                          'one_native_unloader')
                    self._manager_frame = frame
                elif arg is True:
                    _need(self._manager_frame is frame and not self._manager_done,
                          'native_unloader_return')
                    self._manager_done = True
            if frame.f_code is self.codes[0] and frame.f_locals.get('self') is self.entity:
                parent = frame.f_back
                outer = None if parent is None else parent.f_back
                _need(parent is not None and parent.f_code is self.codes[1]
                      and parent.f_locals.get('self') is self.entity
                      and outer is not None and outer.f_code is self.codes[3]
                      and outer.f_locals.get('self') is self.entity
                      and outer.f_locals.get('force_remove') is False, 'getter_cause')
                if event == 'return':
                    self._getter_count += 1
                    _need(self._getter_count == 1 and type(arg) is self.extra_class,
                          'one_native_extra')
                    self._full = _decimal(self.entity._state)
                    self._last = _decimal(self.entity._last_valid_state)
                    _need(_decimal(arg.last_valid_state) == self._last
                          and arg.source_entity == self.entity._source_entity
                          and arg.native_unit_of_measurement == self.entity.native_unit_of_measurement,
                          'actual_extra_capture')
                    self._extra = arg
            if frame.f_code is self.codes[2] and frame.f_locals.get('entity_id') == self.row.entity_id:
                parent = frame.f_back
                _need(parent is not None and parent.f_code is self.codes[1]
                      and parent.f_locals.get('self') is self.entity
                      and frame.f_locals.get('self') is self.data
                      and frame.f_locals.get('extra_data') is self._extra
                      and self._extra is not None, 'restore_remove_cause')
                if event == 'return':
                    self._remove_count += 1
                    stored = self.data.last_states.get(self.row.entity_id)
                    _need(self._remove_count == 1 and stored is not None
                          and stored.extra_data is self._extra, 'native_stored_extra_identity')
                    self._stored = stored
        except BaseException as error:
            # Retain only our value-free refusal, never arbitrary native data.
            reason = str(error) if type(error) is ValueError else ''
            self._error = (reason if reason.startswith('integration_unload_')
                           else 'integration_unload_native_capture_refused')

    async def async_unload(self, lease):
        _need(not self._started and type(lease) is NativeHelperActivationLease,
              'one_actual_lease_required')
        self._started, self.lease = True, lease
        lease._task_check()
        _need(lease.owns_entry(self.entry), 'leased_entry_required')
        self._task, self._loop, self._thread = (asyncio.current_task(),
            asyncio.get_running_loop(), threading.get_ident())
        self._install()
        self._check()
        _need(sys.getprofile() is None, 'existing_profiler')
        try:
            sys.setprofile(self._callback)
            result = await lease.async_unload(self.entry.entry_id)
            _need(sys.getprofile() is self._callback, 'profiler_changed')
            self._check()
            _need(result is True and self._manager_done and self._getter_count == 1
                  and self._remove_count == 1 and self._stored is not None,
                  'actual_completed_capture_required')
            self._quiescent()
            self._before = codec._canonical(codec._capture(self._stored, codec._native()))
            self.capture = IntegrationUnloadCapture(self.version, self.entry.entry_id,
                self.row.id, self.row.entity_id, self._full, self._last,
                hashlib.sha256(self._before).hexdigest())
            _need(self._placeholder_count == 1 and self._writer_frame is None,
                  'native_placeholder_required')
            from dataclasses import asdict
            self._own_data = IntegrationOwnUnloadData(self.entry.entry_id, codec._canonical({
                'schema': 1, 'domain': 'integration', 'core': self.version,
                'entry_id': self.entry.entry_id, 'source_row': _row(self.row),
                'writer_row': self._writer_row, 'state_json': self._placeholder_json,
                'reserved': self._reserved, 'capture': asdict(self.capture),
                'source_pins': self.pins,
                'call': {'getter': 1, 'restore_remove': 1, 'native_set': 1,
                         'placeholder_writer': 1, 'native_unload_true': True}}))
            integration_own_unload_doc(self._own_data)
            self._finished = True
            witness = object.__new__(NativeIntegrationUnloadWitness)
            witness._audit = self
            self._witness = witness
            witness.check_live()
            return witness
        except BaseException:
            self._error = 'unload_failed_or_cancelled'
            raise
        finally:
            if sys.getprofile() is self._callback:
                sys.setprofile(None)
            elif self._error is None:
                self._error = 'profiler_changed'
            self._manager_frame = None


@dataclass(frozen=True)
class IntegrationOwnUnloadData:
    """Read-only historical receipt data; cannot recreate either live witness."""
    entry_id: str
    payload_json: bytes


def integration_own_unload_doc(data):
    _need(type(data) is IntegrationOwnUnloadData, 'own_unload_type')
    doc = codec._parse(data.payload_json)
    keys = {'schema', 'domain', 'core', 'entry_id', 'source_row', 'writer_row',
            'state_json', 'reserved', 'capture', 'source_pins', 'call'}
    _need(type(doc) is dict and set(doc) == keys and type(doc['schema']) is int
          and doc['schema'] == 1 and doc['domain'] == 'integration'
          and doc['core'] in _INTEGRATION and doc['entry_id'] == data.entry_id
          and data.payload_json == codec._canonical(doc), 'own_unload_schema')
    pins = {path: _SOURCES[doc['core']][path] for path in (
        'components/sensor/__init__.py', 'helpers/restore_state.py', 'helpers/entity.py',
        'config_entries.py', 'helpers/entity_platform.py', 'helpers/entity_registry.py', 'core.py')}
    pins['components/integration/sensor.py'] = _INTEGRATION[doc['core']]
    pins['components/integration/__init__.py'] = _INIT[doc['core']]
    _need(doc['source_pins'] == pins, 'own_unload_pins')
    row, writer = doc['source_row'], doc['writer_row']
    _need(type(row) is dict and type(writer) is dict
          and row.get('platform') == 'integration'
          and row.get('config_entry_id') == row.get('unique_id') == data.entry_id
          and row.get('config_subentry_id') is None and row.get('disabled_by') is None
          and {k: v for k, v in row.items() if k not in ('options', 'modified_at')}
           == {k: v for k, v in writer.items() if k not in ('options', 'modified_at')},
          'own_unload_row')
    capture = doc['capture']
    _need(type(capture) is dict and set(capture) == {'core_version', 'entry_id', 'helper_uuid',
        'entity_id', 'full_state_text', 'last_valid_state_text', 'before_sha256'}
        and capture['core_version'] == doc['core'] and capture['entry_id'] == data.entry_id
        and capture['helper_uuid'] == row['id'] and capture['entity_id'] == row['entity_id'],
        'own_unload_capture')
    from decimal import InvalidOperation
    for key in ('full_state_text', 'last_valid_state_text'):
        try:
            valid = (type(capture[key]) is str and Decimal(capture[key]).is_finite()
                     and str(Decimal(capture[key])) == capture[key])
        except InvalidOperation:
            valid = False
        _need(valid, 'own_unload_decimal')
    digest = capture['before_sha256']
    _need(type(digest) is str and len(digest) == 64
          and all(c in '0123456789abcdef' for c in digest), 'own_unload_digest')
    state = codec._parse(doc['state_json'].encode())
    _need(state['entity_id'] == row['entity_id'] and state['state'] == 'unavailable'
          and state['attributes'].get('restored') is True and type(doc['reserved']) is bool,
          'own_unload_placeholder')
    _need(doc['call'] == {'getter': 1, 'restore_remove': 1, 'native_set': 1,
                         'placeholder_writer': 1, 'native_unload_true': True}
          and doc['call']['native_unload_true'] is True
          and all(type(doc['call'][key]) is int for key in
                  ('getter', 'restore_remove', 'native_set', 'placeholder_writer')),
          'own_unload_calls')
    return doc


def encode_integration_own_unload(data):
    return {'kind': 'integration_own_unload', 'schema': 1,
            'entry_id': data.entry_id, 'payload': integration_own_unload_doc(data)}


def decode_integration_own_unload(encoded):
    _need(type(encoded) is dict and set(encoded) == {'kind', 'schema', 'entry_id', 'payload'}
          and encoded['kind'] == 'integration_own_unload'
          and type(encoded['schema']) is int and encoded['schema'] == 1, 'own_unload_envelope')
    result = IntegrationOwnUnloadData(encoded['entry_id'], codec._canonical(encoded['payload']))
    integration_own_unload_doc(result)
    return result
