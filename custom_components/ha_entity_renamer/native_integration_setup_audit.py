"""Own-ID Integration setup: real native restore consumer, no source/timer gap claim.

The existing lifecycle and registry chain remain the sole durable graph. This
narrow mode permits NO registry row changes during setup; a native metadata
change is refused, never adopted from an observed result.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import hashlib
from importlib import import_module
from pathlib import Path
import sys

from . import native_restore_state_payload as codec
from .native_integration_unload_audit import _INTEGRATION, _INIT
from .native_utility_setup_audit import _SOURCES
from .native_utility_unload_audit import _row
from .native_unload_code_proof import prove_methods, unchanged


def _need(value, reason):
    if not value:
        raise ValueError('integration_setup_' + reason) from None


def activation_baseline(chain, lifecycle, entry_id):
    """Derive data transfer exclusively from copied ORIGINAL+accepted receipts."""
    if entry_id in lifecycle.root.get('integration_clocks', {}):
        from .native_integration_clock_lifecycle import baseline
        return baseline(chain,lifecycle,entry_id)
    from .native_restore_state_escrow_receipts import _parse_root, inspect_escrow
    change = lifecycle.own_registry_changes.get(entry_id)
    _need(change is not None and change.before['platform'] == 'integration', 'own_id_required')
    before, after = lifecycle.helper_snapshots[entry_id]
    _need(before == after and before['domain'] == 'integration', 'unchanged_config_required')
    indices = [i for i, step in enumerate(chain.plan.steps)
               if step.adapter == 'registry' and chain._original_change(i) == change]
    _need(len(indices) == 1, 'original_registry_step')
    direction = 'rollback' if lifecycle.source_authorization is not None else 'forward'
    row = chain.store.read(chain._slot('receipt', indices[0], direction))
    _need(row is not None and row['payload'].get('kind') == 'registry_receipt', 'registry_receipt')
    ref = row['payload']['own_helper_placeholder']['escrow_root']
    context = chain.operation_id, chain.plan, chain.original_receipt, lifecycle.binding['preparations']
    root, payload, own = _parse_root(chain.store, context, ref)
    status = inspect_escrow(chain.store, *context, ref)
    _need(type(payload) is codec.PreparedIntegrationPayload and status['status'] == 'verified'
          and root['direction'] == direction and own.root_id == lifecycle.root_id
          and root['binding']['entry_id'] == entry_id, 'escrow_lifecycle_binding')
    return {'kind': 'integration_exact_restore_v1', 'escrow_root': list(ref),
            'payload': codec._parse(codec.serialize(payload))}


def _source_row(lifecycle, accepted_rows, entry_id):
    # Configuration may store source entity_id or registry UUID. Bind BOTH to
    # ORIGINAL full-three; reject source rename/mutation in this own-ID mode.
    snapshot = lifecycle.helper_snapshots[entry_id][0]
    source = snapshot['options']['source']
    original = lifecycle.original_entities
    matches = [row for row in original if row.get('id') == source or row.get('entity_id') == source]
    _need(len(matches) == 1, 'original_physical_source')
    row = matches[0]
    current = [item for item in accepted_rows if item.get('id') == row['id']]
    _need(current == [row] and row.get('unique_id') is not None, 'source_changed')
    return row


@dataclass(frozen=True)
class IntegrationSetupAuditData:
    payload_json: bytes


def encode_setup_audit(data):
    _need(type(data) is IntegrationSetupAuditData, 'typed_audit_required')
    return codec._parse(data.payload_json)


def decode_setup_audit(raw):
    _need(type(raw) is dict and raw.get('kind') in ('integration_own_setup','integration_source_clock_setup'), 'audit_kind')
    return IntegrationSetupAuditData(codec._canonical(raw))


def validate_setup_audit(baseline, source_rows, data, *, core, entry_id, chain=None):
    doc = encode_setup_audit(data)
    if baseline.get('kind') == 'integration_source_clock_restore_v1':
        from .native_integration_clock_lifecycle import validate_audit
        return validate_audit(baseline,source_rows,doc,chain=chain,core=core,entry_id=entry_id)
    keys = {'kind', 'schema', 'core', 'entry_id', 'baseline', 'source_rows', 'result_rows',
            'parser_input', 'full_state_text', 'last_valid_state_text', 'source_pins', 'call'}
    _need(set(doc) == keys and type(doc['schema']) is int and doc['schema'] == 1
          and doc['kind'] == 'integration_own_setup' and doc['core'] == core
          and doc['entry_id'] == entry_id and doc['baseline'] == baseline
          and doc['source_rows'] == source_rows and doc['result_rows'] == source_rows,
          'audit_binding_or_native_metadata_delta')
    pins = dict(_SOURCES[core])
    pins['components/integration/sensor.py'] = _INTEGRATION[core]
    pins['components/integration/__init__.py'] = _INIT[core]
    _need(doc['source_pins'] == pins, 'setup_source_pins')
    payload = codec.deserialize(codec._canonical(baseline['payload']))
    _need(type(payload) is codec.PreparedIntegrationPayload, 'exact_payload_required')
    capture = codec._parse(payload.capture_json)
    after = codec._parse(payload.after_json)
    _need(doc['parser_input'] == after['extra_data']
          and doc['full_state_text'] == capture['full_state_text']
          and doc['last_valid_state_text'] == capture['last_valid_state_text']
          and doc['call'] == {'native_parser': 1, 'native_added': 1,
                              'same_stored_object': True, 'native_setup_true': True}
          and type(doc['call']['native_parser']) is int
          and type(doc['call']['native_added']) is int,
          'native_restore_proof')
    return source_rows


class NativeIntegrationSetupAudit:
    def __init__(self, hass, lifecycle, chain, entry_id, source_rows, *, guard):
        self.hass, self.life, self.chain, self.entry_id = hass, lifecycle, chain, entry_id
        self.guard = guard
        self.baseline = activation_baseline(chain, lifecycle, entry_id)
        self.payload = codec.deserialize(codec._canonical(self.baseline['payload']))
        self.after = codec._parse(self.payload.after_json)
        self.capture = codec._parse(self.payload.capture_json)
        self.source_rows = codec._parse(codec._canonical(source_rows))
        self.target = self.after['state']['entity_id']
        _need(len(source_rows) == 1 and source_rows[0]['entity_id'] == self.target
              and source_rows[0]['id'] == self.capture['helper_uuid'], 'registry_uuid')
        from homeassistant.helpers import entity_registry as er
        self.registry = er.async_get(hass)
        accepted = chain.accepted_documents()['core.entity_registry']['data']['entities']
        self.source = _source_row(lifecycle, accepted, entry_id)
        self.entry = lifecycle.manager.async_get_entry(entry_id)
        self.lease = lifecycle.activation_lease
        self.task = asyncio.current_task()
        self.error = self.entity = self.data = None
        self.parser_count = self.added_count = 0
        self.setup_true = False
        self.callback = self._profile

    def _check(self):
        _need(self.error is None and asyncio.current_task() is self.task, 'live_scope')
        _need(self.guard() in (None, True), 'guard')
        self.lease._task_check()
        self.lease.check()
        _need(self.lease.owns_entry(self.entry) and self.lease.manager is self.hass.config_entries,
              'lease')
        unchanged(self.refs, self.methods, self.codes)
        for rel, digest in self.pins.items():
            _need(hashlib.sha256((self.root / rel).read_bytes()).hexdigest() == digest, 'source_pin')
        rows = [_row(row) for row in self.registry.entities.values()
                if row.config_entry_id == self.entry_id]
        _need(sorted(rows, key=lambda row: row['id']) == sorted(self.source_rows, key=lambda row: row['id']),
              'native_metadata_delta')
        _need(_row(self.registry.async_get(self.source['entity_id'])) == self.source,
              'physical_source_changed')

    def __enter__(self):
        from homeassistant.components.integration.sensor import (
            IntegrationSensor, IntegrationSensorExtraStoredData)
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from homeassistant.const import __version__
        self.core = __version__
        _need(self.core == self.payload.core_version and self.core in _INTEGRATION,
              'core')
        self.root = Path(import_module('homeassistant').__file__).resolve().parent
        self.pins = dict(_SOURCES[self.core])
        self.pins['components/integration/sensor.py'] = _INTEGRATION[self.core]
        self.pins['components/integration/__init__.py'] = _INIT[self.core]
        self.refs = ((IntegrationSensorExtraStoredData, 'from_dict'),
            (IntegrationSensor, 'async_get_last_sensor_data'),
            (IntegrationSensor, 'async_added_to_hass'),
            (type(self.hass.config_entries), 'async_setup'))
        self.methods, self.codes = prove_methods(self.root, self.pins, self.refs)
        self.entity_class, self.extra_class = IntegrationSensor, IntegrationSensorExtraStoredData
        self.restore = self.hass.data[DATA_RESTORE_STATE]
        self.stored = self.restore.last_states[self.target]
        _need(codec._canonical(codec._capture(self.stored, codec._native())) == self.payload.after_json,
              'actual_escrow_target')
        self._check()
        _need(sys.getprofile() is None, 'existing_profiler')
        sys.setprofile(self.callback)
        return self

    def _profile(self, frame, event, arg):
        if self.error is not None or event != 'return':
            return
        try:
            if frame.f_code is self.codes[0]:
                parent = frame.f_back
                outer = None if parent is None else parent.f_back
                if parent is None or parent.f_code is not self.codes[1]:
                    return
                entity = parent.f_locals.get('self')
                if getattr(entity, 'unique_id', None) != self.entry_id:
                    return
                _need(outer is not None and outer.f_code is self.codes[2]
                      and outer.f_locals.get('self') is entity
                      and type(entity) is self.entity_class
                      and entity.entity_id == self.target and entity.hass is self.hass
                      and entity.platform.config_entry is self.entry
                      and self.restore.last_states.get(self.target) is self.stored
                      and frame.f_locals['restored'] == self.after['extra_data']
                      and type(arg) is self.extra_class
                      and type(arg.native_value) is str
                      and arg.native_value == self.capture['full_state_text']
                      and str(arg.last_valid_state) == self.capture['last_valid_state_text'],
                      'native_parser_cause_or_value')
                self.entity = entity
                self.parser_count += 1
                _need(self.parser_count == 1, 'one_parser')
            if frame.f_code is self.codes[2] and frame.f_locals.get('self') is self.entity:
                if self.entity is not None and arg is None:
                    self.added_count += 1
                    _need(self.added_count == 1, 'one_native_added')
                    self._counter()
            if (frame.f_code is self.codes[3] and frame.f_locals.get('entry_id') == self.entry_id
                    and arg is True):
                _need(frame.f_locals.get('self') is self.hass.config_entries
                      and frame.f_locals.get('_lock') is False and not self.setup_true,
                      'native_setup_call')
                self.setup_true = True
        except BaseException:
            self.error = 'native_setup_observation_refused'

    def _counter(self):
        _need(self.entity is not None and type(self.entity._state) is Decimal
              and type(self.entity._last_valid_state) is Decimal
              and self.entity._state.as_tuple() == Decimal(self.capture['full_state_text']).as_tuple()
              and self.entity._last_valid_state.as_tuple()
                  == Decimal(self.capture['last_valid_state_text']).as_tuple()
              and self.entity._sensor_source_id == self.source['entity_id']
              and self.entity._max_sub_interval is None
              and self.entity.native_unit_of_measurement
                  == self.after['extra_data']['native_unit_of_measurement'], 'native_restored_fields')

    def finish(self):
        self._check()
        self._counter()
        _need(self.entry.state.value == 'loaded' and self.parser_count == self.added_count == 1
              and self.setup_true, 'actual_setup_required')
        from homeassistant.helpers.entity_platform import async_get_platforms
        entities = [e for p in async_get_platforms(self.hass, 'integration')
                    if p.config_entry is self.entry for e in p.entities.values()]
        _need(entities == [self.entity], 'actual_setup_entity')
        doc = {'kind': 'integration_own_setup', 'schema': 1, 'core': self.core,
            'entry_id': self.entry_id, 'baseline': self.baseline,
            'source_rows': self.source_rows, 'result_rows': self.source_rows,
            'parser_input': self.after['extra_data'], 'source_pins': self.pins,
            'full_state_text': str(self.entity._state),
            'last_valid_state_text': str(self.entity._last_valid_state),
            'call': {'native_parser': self.parser_count, 'native_added': self.added_count,
                     'same_stored_object': True, 'native_setup_true': self.setup_true}}
        result = IntegrationSetupAuditData(codec._canonical(doc))
        validate_setup_audit(self.baseline, self.source_rows, result,
                            core=self.core, entry_id=self.entry_id)
        return result

    def __exit__(self, exc_type, exc, traceback):
        if sys.getprofile() is self.callback:
            sys.setprofile(None)
        else:
            self.error = 'profiler_changed'
            _need(False, self.error)
