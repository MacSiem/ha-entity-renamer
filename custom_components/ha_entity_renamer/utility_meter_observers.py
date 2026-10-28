"""Read-only BASIC UtilityMeter observations on two exact installed Core pins.

Historical runtime evidence is distinct from original configuration receipts.
No HA import at module import; the durable codec and binding work offline.
No state, services, restore cache, native activation or configuration writes.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from importlib import import_module
import json

from .helper_native import read_entry
from .helper_plan import plan_helper, snapshot_equal
from .helper_observers import RegistryMappingProof, registry_identity, require

PINS = {'2025.6.0': (2, 1), '2026.10.2': (2, 2), '2026.10.4': (2, 2)}
_BASIC = {'cycle': 'none', 'offset': 0, 'tariffs': [], 'net_consumption': False,
          'delta_values': False, 'periodically_resetting': False,
          'always_available': False}
_IDENTITY = {'entry_id', 'unique_id', 'title', 'version', 'minor_version', 'source',
             'disabled_by', 'pref_disable_new_entities', 'pref_disable_polling'}
_STATIC = ('_last_period', '_last_reset', '_input_device_class',
           '_attr_native_unit_of_measurement', '_period', '_cron_pattern',
           '_next_reset', '_tariff', '_tariff_entity', '_sensor_delta_values',
           '_sensor_net_consumption', '_sensor_periodically_resetting',
           '_sensor_always_available')
_ROW_KEYS = {'aliases', 'area_id', 'categories', 'capabilities', 'config_entry_id',
             'config_subentry_id', 'created_at', 'device_class', 'device_id',
             'disabled_by', 'entity_category', 'entity_id', 'hidden_by', 'icon', 'id',
             'has_entity_name', 'labels', 'modified_at', 'name', 'options',
             'original_device_class', 'original_icon', 'original_name', 'platform',
             'suggested_object_id', 'supported_features', 'translation_key', 'unique_id',
             'previous_unique_id', 'unit_of_measurement'}
_MAX_BYTES = 131072


def _decimal(value):
    require(type(value) in (str, int, Decimal), 'utility_decimal_type')
    require(len(str(value)) <= 256, 'utility_decimal_size')
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        require(False, 'utility_decimal_invalid')
    require(result.is_finite() and len(result.as_tuple().digits) <= 96
            and abs(result.as_tuple().exponent) <= 96, 'utility_decimal_range')
    return result


def _number(value):
    value = _decimal(value)
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if value == 0 else text


def _plain(value):
    if isinstance(value, datetime):
        require(value.tzinfo is not None, 'utility_datetime_naive')
        return value.isoformat()
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, str):
        return str(value)
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        require(value == value and abs(value) != float('inf'), 'utility_json_nonfinite')
        return value
    if isinstance(value, dict):
        require(all(type(k) is str for k in value), 'utility_json_key')
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    require(False, 'utility_json_type')


def _canonical(value):
    # Bounded tree, no object hooks or unbounded recursion in persisted evidence.
    nodes = [0]
    def check(item, depth=0):
        nodes[0] += 1
        require(depth <= 16 and nodes[0] <= 4096, 'utility_baseline_bounds')
        require(item is None or type(item) in (str, bool, int, float, dict, list),
                'utility_baseline_json_type')
        if isinstance(item, str):
            require(len(item) <= 8192, 'utility_baseline_string_size')
        if isinstance(item, dict):
            require(all(type(k) is str for k in item), 'utility_baseline_json_key')
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
    check(value)
    try:
        text = json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError):
        require(False, 'utility_baseline_json_invalid')
    require(len(text.encode()) <= _MAX_BYTES, 'utility_baseline_size')
    return text


def _options(snapshot, core):
    require(type(snapshot) is dict and set(snapshot) ==
            {'domain', 'identity', 'data', 'options', 'subentries'}, 'utility_snapshot_layout')
    require(snapshot['domain'] == 'utility_meter' and snapshot['data'] == {}
            and snapshot['subentries'] == {}, 'utility_layout_unsupported')
    identity = snapshot['identity']
    require(type(identity) is dict and set(identity) == _IDENTITY,
            'utility_identity_layout')
    require(type(core) is str and core in PINS and (identity['version'], identity['minor_version']) == PINS[core]
            and type(identity['version']) is int and type(identity['minor_version']) is int,
            'utility_schema_version_unsupported')
    require(all(type(identity[k]) is str and bool(identity[k])
                for k in ('entry_id', 'title', 'source'))
            and all(identity[k] is None or type(identity[k]) is str
                    for k in ('unique_id', 'disabled_by'))
            and all(type(identity[k]) is bool for k in
                    ('pref_disable_polling', 'pref_disable_new_entities')),
            'utility_identity_invalid')
    options = snapshot['options']
    require(type(options) is dict and set(options) in
            (set(_BASIC) | {'source'}, set(_BASIC) | {'source', 'name'}),
            'utility_options_layout_unsupported')
    require(type(options['source']) is str and bool(options['source']), 'utility_source_invalid')
    for key, expected in _BASIC.items():
        # Native UI NumberSelector may persist offset as 0.0; this is exact zero.
        valid_type = type(options[key]) is type(expected)
        if key == 'offset':
            valid_type = type(options[key]) in (int, float)
        require(valid_type and options[key] == expected, 'utility_mode_unsupported_' + key)
    if 'name' in options:
        require(type(options['name']) is str, 'utility_name_invalid')
    return options


@dataclass(frozen=True)
class UtilityMeterRuntimeBaseline:
    """Detached historical observation, never an authority or Plan input receipt."""
    entry_id: str
    payload_json: str


def _payload(baseline):
    require(type(baseline) is UtilityMeterRuntimeBaseline
            and type(baseline.entry_id) is str and type(baseline.payload_json) is str
            and len(baseline.payload_json.encode()) <= _MAX_BYTES, 'utility_baseline_type')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'utility_baseline_duplicate_key')
            result[key] = value
        return result
    try:
        value = json.loads(baseline.payload_json, object_pairs_hook=unique)
    except (ValueError, TypeError, RecursionError):
        require(False, 'utility_baseline_json_invalid')
    require(_canonical(value) == baseline.payload_json, 'utility_baseline_noncanonical')
    require(type(value) is dict and set(value) == {'version', 'domain', 'core', 'entry_id',
            'before', 'after', 'source_identity', 'source_before', 'source_after',
            'source_unit', 'source_class', 's0', 'a0', 'static', 'helper_rows'},
            'utility_baseline_layout')
    require(type(value['version']) is int and value['version'] == 1
            and value['domain'] == 'utility_meter'
            and value['entry_id'] == baseline.entry_id, 'utility_baseline_identity')
    for snapshot in (value['before'], value['after']):
        _options(snapshot, value['core'])
        require(snapshot['identity']['entry_id'] == baseline.entry_id,
                'utility_baseline_entry_mismatch')
    before, after = value['before'], value['after']
    expected = deepcopy(before)
    expected['options']['source'] = after['options']['source']
    require(expected == after, 'utility_baseline_config_mismatch')
    for key in ('a0', 's0'):
        require(type(value[key]) is str and value[key] == _number(value[key])
                and _decimal(value[key]) >= 0, 'utility_baseline_decimal')
    require(_decimal(value['s0']) > 0, 'utility_zero_last_valid_restore_unsupported')
    require(type(value['source_identity']) is list and len(value['source_identity']) == 5
            and all(v is None or type(v) is str for v in value['source_identity'])
            and all(value['source_identity'][i] for i in (0, 1, 2)),
            'utility_baseline_source_identity')
    for key in ('source_before', 'source_after'):
        require(type(value[key]) is str and value[key].startswith('sensor.')
                and len(value[key]) <= 256, 'utility_baseline_source_id')
    old, new = value['source_before'], value['source_after']
    uuid = value['source_identity'][0]
    require(before['options']['source'] in (old, uuid)
            and after['options']['source'] ==
            (new if before['options']['source'] == old else uuid),
            'utility_baseline_source_binding')
    require(type(value['source_unit']) is str and bool(value['source_unit'])
            and (value['source_class'] is None or type(value['source_class']) is str),
            'utility_baseline_source_attributes')
    static = value['static']
    require(type(static) is dict and set(static) == set(_STATIC), 'utility_baseline_static_layout')
    require(static['_last_period'] == _number(static['_last_period'])
            and static['_input_device_class'] == value['source_class']
            and static['_attr_native_unit_of_measurement'] == value['source_unit'],
            'utility_baseline_static_mismatch')
    for key in ('_period', '_cron_pattern', '_next_reset', '_tariff', '_tariff_entity'):
        require(static[key] is None, 'utility_baseline_schedule_unsupported')
    for key in ('_sensor_delta_values', '_sensor_net_consumption',
                '_sensor_periodically_resetting', '_sensor_always_available'):
        require(static[key] is False, 'utility_baseline_flags_unsupported')
    stamp = static['_last_reset']
    require(type(stamp) is str, 'utility_baseline_reset_missing')
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        require(False, 'utility_baseline_reset_invalid')
    require(parsed.tzinfo is not None and parsed.isoformat() == stamp,
            'utility_baseline_reset_invalid')
    rows = value['helper_rows']
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict,
            'utility_baseline_helper_rows')
    row = rows[0]
    expected_keys = _ROW_KEYS | ({'aliases_v2', 'object_id_base'}
                                 if value['core'] in ('2026.10.2', '2026.10.4') else set())
    require(set(row) == expected_keys, 'utility_baseline_helper_row_layout')
    require(before['identity']['disabled_by'] is None,
            'utility_baseline_inactive_not_runtime')
    require(row.get('config_entry_id') == baseline.entry_id
            and row.get('config_subentry_id') is None
            and row.get('platform') == 'utility_meter'
            and row.get('unique_id') == baseline.entry_id
            and type(row.get('id')) is str and bool(row['id'])
            and type(row.get('entity_id')) is str and row['entity_id'].startswith('sensor.')
            and row.get('disabled_by') is None, 'utility_baseline_helper_binding')
    return value


def encode_runtime_baseline(value):
    """JSON object for lifecycle's immutable root; validate before serialization."""
    payload = _payload(value)
    return {'entry_id': value.entry_id, 'payload': payload}


def decode_runtime_baseline(value):
    require(type(value) is dict and set(value) == {'entry_id', 'payload'}
            and type(value['entry_id']) is str, 'utility_baseline_envelope')
    result = UtilityMeterRuntimeBaseline(value['entry_id'], _canonical(value['payload']))
    _payload(result)
    return result


def verify_baseline_binding(baseline, prepared_before, prepared_after):
    """Pure strict binding to ORIGINAL PreparedHelper.before/after dictionaries.

    The caller authenticates the lifecycle root and original registry preparation.
    This validates structure/binding, not historical authenticity from arbitrary JSON.
    """
    value = _payload(baseline)
    require(snapshot_equal(value['before'], prepared_before)
            and snapshot_equal(value['after'], prepared_after), 'utility_baseline_prepared_mismatch')
    return True


class NativeUtilityMeterObservers:
    """One immutable capture per transaction; current samples may evolve legally."""

    @classmethod
    async def capture(cls, hass, snapshots, mapping_proof, *, settle_timeout=15, activation_lease=None):
        from homeassistant.const import __version__
        from homeassistant.helpers import entity_registry as er
        require(__version__ in PINS, 'utility_ha_version_unsupported')
        snapshots = tuple(deepcopy(s) for s in snapshots)
        require(bool(snapshots) and all(s['domain'] == 'utility_meter' for s in snapshots),
                'utility_mixed_scope_unsupported')
        require(type(settle_timeout) in (int, float) and 0 < settle_timeout <= 60,
                'utility_settle_timeout_invalid')
        proof = tuple(mapping_proof)
        require(all(type(p) is RegistryMappingProof for p in proof), 'utility_mapping_proof_type')
        require(len({p.old for p in proof}) == len(proof)
                and len({p.new for p in proof}) == len(proof), 'utility_mapping_proof_ambiguous')
        registry = er.async_get(hass)
        for p in proof:
            row = registry.async_get(p.old)
            require(row is not None and row.id == p.registry_id, 'utility_mapping_proof_not_native')
        self = cls()
        self.hass, self.core, self.settle_timeout = hass, __version__, settle_timeout
        self.before, self.after, self.source_rows, self.helper_rows, self.modes = {}, {}, {}, {}, {}
        self._baselines, self._objects = {}, {}
        self._activation_lease = activation_lease
        self._all_entry_ids = frozenset(e.entry_id for e in hass.config_entries.async_entries())
        mapping = {p.old: p.new for p in proof}
        self.source_names = {}
        for snapshot in snapshots:
            _options(snapshot, __version__)
            key = snapshot['identity']['entry_id']
            require(key not in self.before, 'utility_duplicate_entry')
            require(snapshot_equal(read_entry(hass, key), snapshot), 'utility_capture_stale')
            plan = plan_helper(snapshot, mapping)
            require(not plan.blocked, 'utility_plan_blocked')
            self.before[key], self.after[key] = snapshot, plan.after
            entry = hass.config_entries.async_get_entry(key)
            require(entry.state.value in ('loaded', 'not_loaded'), 'utility_capture_unsettled')
            require(entry.disabled_by is None or entry.state.value == 'not_loaded',
                    'utility_disabled_loaded')
            self.modes[key] = entry.state.value
            source = er.async_validate_entity_id(registry, snapshot['options']['source'])
            row = registry.async_get(source)
            require(row is not None and source.startswith('sensor.'), 'utility_source_unregistered')
            self.source_rows[key] = registry_identity(row)
            self.source_names[key] = (source, mapping.get(source, source))
            self.helper_rows[key] = self._rows(key)
        await self.settle()
        for snapshot in self.before.values():
            await self.validate_snapshot(snapshot)
        # Final capture is synchronous for the ENTIRE scope after all schema awaits.
        for snapshot in self.before.values():
            key = snapshot['identity']['entry_id']
            entity, source = self._activation(snapshot)
            if entity is None:
                continue
            current, accumulator, static = self._sample(entity, source)
            require(current > 0, 'utility_zero_last_valid_restore_unsupported')
            value = {'version': 1, 'domain': 'utility_meter', 'core': self.core,
                     'entry_id': key, 'before': self.before[key], 'after': self.after[key],
                     'source_identity': list(self.source_rows[key]),
                     'source_before': self.source_names[key][0],
                     'source_after': self.source_names[key][1],
                     'source_unit': source.attributes.get('unit_of_measurement'),
                     'source_class': source.attributes.get('device_class'),
                     's0': _number(current), 'a0': _number(accumulator),
                     'static': static, 'helper_rows': self.helper_rows[key]}
            baseline = UtilityMeterRuntimeBaseline(key, _canonical(value))
            _payload(baseline)
            # Native serializer/parser roundtrip must preserve supported restore fields.
            restored = type(entity.extra_restore_state_data).from_dict(
                deepcopy(entity.extra_restore_state_data.as_dict()))
            require(restored is not None and _decimal(restored.native_value) == accumulator
                    and restored.last_valid_state == current
                    and _number(restored.last_period) == static['_last_period']
                    and _plain(restored.last_reset) == static['_last_reset']
                    and restored.native_unit_of_measurement == value['source_unit']
                    and restored.input_device_class == value['source_class']
                    and restored.status == 'collecting', 'utility_native_restore_loss')
            self._baselines[key] = baseline
            self._objects[key] = entity
        return self

    def bind_activation_lease(self, lease):
        from .native_helper_activation_lease import NativeHelperActivationLease
        require(self._activation_lease is None and type(lease) is NativeHelperActivationLease
                and lease.manager is self.hass.config_entries
                and all(lease.owns_entry(self.hass.config_entries.async_get_entry(key))
                        for key in self.before), 'utility_activation_lease_binding')
        self._activation_lease = lease

    def bind_native_chain(self, chain):
        """Use ONLY ORIGINAL + accepted native effects for expected registry rows.

        Counter A0/S0 and original rows stay immutable; no live manager image is
        captured or adopted. Both loaded and inactive observer scopes may bind.
        """
        from .native_registry_receipts import RegistryReceiptChain, _config_projection
        require(type(chain) is RegistryReceiptChain and getattr(self, '_native_chain', None) is None
                and chain.version == self.core
                and chain.original_receipt.config_root == self.hass.config.config_dir,
                'utility_native_chain_binding')
        docs = {row.key: json.loads(row.raw) for row in chain.baseline.files}
        entities = docs['core.entity_registry']['data']['entities']
        configs = docs['core.config_entries']['data']['entries']
        for key in self.before:
            original_rows = sorted((row for row in entities if row.get('config_entry_id') == key),
                                   key=lambda row: row['id'])
            entries = [row for row in configs if row['entry_id'] == key]
            require(original_rows == self.helper_rows[key] and len(entries) == 1
                    and snapshot_equal(_config_projection(entries[0]), self.before[key]),
                    'utility_original_chain_rows')
            changes = [change for step, change in zip(chain.plan.steps, chain.changes)
                       if step.adapter == 'helpers'
                       and change.before['identity']['entry_id'] == key]
            require((not changes and snapshot_equal(self.before[key], self.after[key]))
                    or len(changes) == 1 and snapshot_equal(changes[0].before, self.before[key])
                    and snapshot_equal(changes[0].after, self.after[key]),
                    'utility_original_chain_preparation')
        self._native_chain = chain
        return self

    def expected_registry_rows(self, entry_id):
        require(entry_id in self.before, 'utility_outside_prepared_scope')
        chain = getattr(self, '_native_chain', None)
        if chain is None:
            return deepcopy(self.helper_rows[entry_id])
        document = chain.accepted_documents()['core.entity_registry']
        return sorted((deepcopy(row) for row in document['data']['entities']
                       if row.get('config_entry_id') == entry_id), key=lambda row: row['id'])

    def runtime_baselines(self):
        return tuple(self._baselines[k] for k in sorted(self._baselines))

    async def settle(self):
        try:
            async with asyncio.timeout(self.settle_timeout):
                await self.hass.async_block_till_done()
        except TimeoutError as err:
            from .helper_observers import ObservationUnavailable
            raise ObservationUnavailable('utility_settle_timeout') from err
        for key in self.before:
            entry = self.hass.config_entries.async_get_entry(key)
            require(entry is not None and entry.state.value in ('loaded', 'not_loaded'),
                    'utility_entry_not_settled')
        return True

    async def validate_snapshot(self, snapshot):
        options = _options(snapshot, self.core)
        key = snapshot['identity']['entry_id']
        require(key in self.before and (snapshot_equal(snapshot, self.before[key])
                or snapshot_equal(snapshot, self.after[key])), 'utility_outside_prepared_scope')
        flow = import_module('homeassistant.components.utility_meter.config_flow')
        # CONFIG_SCHEMA covers the full creation layout. A missing optional legacy
        # name is validated with the preserved entry title, never OPTIONS_SCHEMA.
        cleaned = dict(options)
        if 'name' not in cleaned:
            cleaned['name'] = snapshot['identity']['title']
        flow.CONFIG_SCHEMA(cleaned)
        return True

    def _rows(self, key):
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        registry = er.async_get(self.hass)
        return sorted((json.loads(_orjson_bytes_default_encoder(row.as_storage_fragment)) for row in
                       er.async_entries_for_config_entry(registry, key)), key=lambda row: row['id'])

    def _source(self, snapshot, *, values):
        from homeassistant.helpers import entity_registry as er
        key = snapshot['identity']['entry_id']
        registry = er.async_get(self.hass)
        resolved = er.async_validate_entity_id(registry, snapshot['options']['source'])
        require(resolved in self.source_names[key], 'utility_source_mapping_unproven')
        row = registry.async_get(resolved)
        require(row is not None and registry_identity(row) == self.source_rows[key],
                'utility_source_registry_identity_changed')
        if not values:
            return resolved
        require(row.disabled_by is None, 'utility_source_disabled')
        state = self.hass.states.get(resolved)
        require(state is not None and state.state not in ('unknown', 'unavailable')
                and state.attributes.get('state_class') == 'total_increasing',
                'utility_source_unavailable_or_class')
        _decimal(state.state)
        return state

    def _jobs(self, key):
        from homeassistant.helpers import event
        tracker = self.hass.data.get(event._TRACK_STATE_CHANGE_DATA)
        if tracker is None:
            return []
        require(type(tracker.callbacks) is dict or hasattr(tracker.callbacks, 'items'),
                'utility_event_tracker_unsupported')
        result = []
        for source, jobs in tracker.callbacks.items():
            for job in jobs:
                target = job.target
                entity = getattr(target, '__self__', None)
                if getattr(entity, '_parent_meter', None) == key:
                    result.append((source, entity, getattr(target, '__func__', None)))
        return result

    def _activation(self, snapshot):
        from homeassistant.helpers import entity_platform as ep
        from homeassistant.components.utility_meter.const import DATA_UTILITY, DATA_TARIFF_SENSORS
        from homeassistant.components.utility_meter.sensor import UtilityMeterSensor
        key = snapshot['identity']['entry_id']
        require(key in self.before and (snapshot_equal(snapshot, self.before[key])
                or snapshot_equal(snapshot, self.after[key])), 'utility_outside_prepared_scope')
        require(snapshot_equal(read_entry(self.hass, key), snapshot), 'utility_manager_snapshot_mismatch')
        require(self._rows(key) == self.expected_registry_rows(key), 'utility_helper_registry_changed')
        entry = self.hass.config_entries.async_get_entry(key)
        require(entry.state.value == self.modes[key], 'utility_lifecycle_changed')
        owned_lock = False
        if self._activation_lease is not None:
            from .native_helper_activation_lease import NativeHelperActivationLease
            require(type(self._activation_lease) is NativeHelperActivationLease
                    and self._activation_lease.owns_entry(entry), 'utility_activation_lease_binding')
            owned_lock = True
        require((not entry.setup_lock.locked() or owned_lock) and entry._async_cancel_retry_setup is None
                and not any(not task.done() for task in (*entry._tasks, *entry._background_tasks)),
                'utility_native_pending_or_busy')
        candidates = [entity for platform in ep.async_get_platforms(self.hass, 'utility_meter')
                      if platform.config_entry is entry for entity in platform.entities.values()]
        info = self.hass.data.get(DATA_UTILITY, {}).get(key)
        if self.modes[key] == 'not_loaded':
            require(not candidates and info is None and not self._jobs(key),
                    'utility_inactive_has_runtime')
            return None, None
        require(entry.disabled_by is None and len(self.helper_rows[key]) == 1
                and self.helper_rows[key][0].get('disabled_by') is None,
                'utility_loaded_entity_disabled_or_missing')
        require(len(candidates) == 1 and type(candidates[0]) is UtilityMeterSensor,
                'utility_native_class_or_count')
        entity = candidates[0]
        row = self.expected_registry_rows(key)[0]
        require(entity.hass is self.hass and entity.entity_id == row['entity_id']
                and entity.unique_id == key == row['unique_id']
                and entity.registry_entry is not None and entity.registry_entry.id == row['id'],
                'utility_native_registry_binding')
        source = self._source(snapshot, values=True)
        require(entity._parent_meter == key and entity._sensor_source_id == source.entity_id
                and type(info) is dict and info.get('source') == snapshot['options']['source']
                and info.get('tariff_entity') is None
                and info.get(DATA_TARIFF_SENSORS) == [entity], 'utility_native_data_binding')
        require(callable(entity._collecting) and self._jobs(key) ==
                [(source.entity_id, entity, UtilityMeterSensor.async_reading)],
                'utility_native_callback_ownership')
        old = self._objects.get(key)
        if old is not None and old is not entity:
            require(old._collecting is None, 'utility_original_object_still_collecting')
        require(entity.scheduler is None and all(getattr(entity, field) is None
                for field in ('_period', '_cron_pattern', '_next_reset', '_tariff', '_tariff_entity'))
                and all(getattr(entity, field) is False for field in
                        ('_sensor_delta_values', '_sensor_net_consumption',
                         '_sensor_periodically_resetting', '_sensor_always_available')),
                'utility_native_mode_unsupported')
        return entity, source

    def _sample(self, entity, source):
        current = _decimal(source.state)
        accumulator = _decimal(entity.native_value)
        require(current >= 0 and accumulator >= 0 and entity._last_valid_state == current
                and entity.available, 'utility_native_counter_incoherent')
        static = {key: _plain(getattr(entity, key)) for key in _STATIC}
        output = self.hass.states.get(entity.entity_id)
        require(output is not None and _decimal(output.state) == accumulator
                and output.attributes.get('unit_of_measurement') == entity.native_unit_of_measurement
                and output.attributes.get('status') == 'collecting'
                and _decimal(output.attributes.get('last_valid_state')) == current
                and _decimal(output.attributes.get('last_period')) == entity._last_period
                and output.attributes.get('last_reset') == static['_last_reset'],
                'utility_public_counter_mismatch')
        return current, accumulator, static

    async def verify_activation(self, snapshot):
        await self.validate_snapshot(snapshot)
        self._activation(snapshot)
        return True

    async def verify_runtime(self, snapshot):
        await self.settle()
        await self.validate_snapshot(snapshot)
        # No await between dependency, subscription, source, native and output reads.
        entity, source = self._activation(snapshot)
        if entity is None:
            # An inactive saga may temporarily have old options while the source
            # registry already holds the new ID (and vice versa on reverse).
            # Prove the ORIGINAL physical source at exactly one prepared name;
            # activation checks alone must not demand an active dependency.
            from homeassistant.helpers import entity_registry as er
            key = snapshot['identity']['entry_id']
            registry = er.async_get(self.hass)
            rows = [registry.async_get(name) for name in set(self.source_names[key])]
            rows = [row for row in rows if row is not None]
            require(len(rows) == 1 and registry_identity(rows[0]) == self.source_rows[key],
                    'utility_inactive_source_identity_changed')
            return True  # Preserved absence only: never an accumulator proof.
        key = snapshot['identity']['entry_id']
        baseline = _payload(self._baselines[key])
        current, accumulator, static = self._sample(entity, source)
        require(source.attributes.get('unit_of_measurement') == baseline['source_unit']
                and source.attributes.get('device_class') == baseline['source_class']
                and static == baseline['static'], 'utility_retained_runtime_changed')
        with localcontext() as context:
            context.prec = 384  # bounded <=96-digit inputs, including fractional exponents
            expected = _decimal(baseline['a0']) + current - _decimal(baseline['s0'])
        require(current >= _decimal(baseline['s0']) and accumulator == expected,
                'utility_accumulator_delta_mismatch')
        return True

    async def _verify_inventory(self, snapshots):
        await self.settle()
        require(frozenset(e.entry_id for e in self.hass.config_entries.async_entries()) ==
                self._all_entry_ids, 'utility_inventory_membership_changed')
        for snapshot in snapshots.values():
            await self.verify_runtime(snapshot)
        return True

    async def verify_inventory(self, plan):
        return await self._verify_inventory(self.after)

    async def verify_original_inventory(self, plan):
        return await self._verify_inventory(self.before)
