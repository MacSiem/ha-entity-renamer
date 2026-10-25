"""Read-only CPython native call evidence for BASIC UtilityMeter setup.

No setup/save/unload/reload, state, helper field, function or restore-cache writes.
This evidence is NOT readiness, authority, or a durable lifecycle receipt. The
installed caller must authenticate ORIGINAL baseline + accepted full-three source,
keep exclusion/physical-source fences, and authenticate durable result provenance.
Only CPython 3.13/3.14 and two exact installed HA sources are supported. Profiling
cost is not qualified for product use. No HA dependencies at module import time.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
from importlib import import_module
import json
from pathlib import Path
import sys
import threading
from types import CodeType

from .helper_native import read_entry
from .helper_plan import snapshot_equal
from .utility_meter_observers import encode_runtime_baseline, decode_runtime_baseline


class UtilitySetupAuditError(ValueError):
    """Sanitized refusal; never contains native data or exception text."""


def _need(condition, reason):
    if not condition:
        raise UtilitySetupAuditError('utility_setup_audit_' + reason) from None


_MAX_CALLS = 64
_MAX_EVENTS = 128
_MAX_BYTES = 1048576
_EVENT = 'entity_registry_updated'
# SHA-256 of full installed public source, SOURCE-READBACK.json (2026-10-25).
_SOURCES = {'2026.10.2': {'components/utility_meter/sensor.py': 'e70eab00470d87551bbba68516d46b61a07e44422f39d7e3b3ea5ce2eb67d7bb', 'components/utility_meter/__init__.py': '0a29f1ec1f14b4445c5e96401187cbbe3cc166ef49d4d1c350c5f8acc9b1d7dc', 'components/sensor/__init__.py': 'c3cbda48f1cdfae2401e800ab0dd77399b12a64b654b728d0de28f2c07b5fd09', 'helpers/entity_platform.py': '776e21f2ef16715b17e34fe27da33a3d30042b27988d75aaf5461ddcf736b09e', 'helpers/entity_registry.py': 'f77b7ae946a0902136faadb3cfe45d17aca24fa639c43189295e101f5e47f425', 'helpers/restore_state.py': '9f6fcdca1a1b0b7301e2bb9d9ede036241ac06330bd041c54397b0c9eee78128', 'helpers/entity.py': '89d26ab379b47e68c02f3c816a5db7ad830610d39ad022395fe02e7d0b83a424', 'helpers/storage.py': '4d6b26e730eeea198196e2eb01ffc55432d9e79c06a65dca307ce664192e8025', 'core.py': '1fe2fcfb91fbb6edfb4450dad3ae5ebc4e325664770fedbd607d103d91ce4f19', 'config_entries.py': '221e2672e9a8f0ec12fa62edbfdc2ef6d986331855797615c81ecbe99f8840db', 'helpers/registry.py': 'a481648fdca6f27cb19a72ea44e0c42b204674a8f5c10ed52bcedb07ec220cae', 'helpers/helper_integration.py': 'eedfca79e0ac213fcf45cd4bcab6fa82ddd0a511e3723485388ab55657fb8972', 'helpers/device.py': '47cc4afc5d13def0f63fb854a74ae388cb0a877b25c6b717001abae6ca19d4a5'}, '2025.6.0': {'components/utility_meter/sensor.py': '054032cf80af12fcfa79da0b7f32875993b321d1f34b44592f53a9225e04dfed', 'components/utility_meter/__init__.py': '38f388087538a251e300fb83646601c742d5bfb2f1a308f1321aa699029563f0', 'components/sensor/__init__.py': 'c8144af56e98f34adf6b46c78bd81284b870c52573a4ca2f6a9e4e53b7ab5dc9', 'helpers/entity_platform.py': 'e1c976af7fb673fe3b5cf1c62be032c0d0e1740253bff65a528a9503ffca0ed6', 'helpers/entity_registry.py': 'f37dbf51693b7295f27a2d8356142be5aedc6bca8b9d4427405e3d49e25e4d76', 'helpers/restore_state.py': 'e3fad462c178587e3f2d669d8b5fc58c3ebea64af3d477dfacd1109432fb53d3', 'helpers/entity.py': '2c1e501decd5325763ca75314452bf10786aeddfbabfd9b11c3c5ae4ea4e3e44', 'helpers/storage.py': '26671506953b11a9bffc446193205f3ac909e58be13a71387aafdf01c212bfc0', 'core.py': '1b4eee1e4fb701669a4ed941f32bb36a977ce446dfa2c8d1ffb0beadc5a031a2', 'config_entries.py': '306340f94f1387bd6b73162cbc2c37ae1c095025d34b318fcf10b8b6c40dcffb', 'helpers/registry.py': '963a6f6c1042f959c63ee14603243223e4aa76a6f26e731a5848d6be5339cff9', 'helpers/helper_integration.py': '16549a97b848ce3f0e48a87a14dc2c01bb0d9a16289c182aadcc4c13e6186cd1', 'helpers/device.py': 'd74325a0ae07ba29f851d7f8e388e9b3506430b8bf0339d5f161b6c75ff3e841'}}

_COMMON_ARGS = frozenset(('aliases', 'area_id', 'categories', 'capabilities',
    'config_entry_id', 'config_subentry_id', 'device_class', 'device_id',
    'disabled_by', 'entity_category', 'hidden_by', 'icon', 'has_entity_name',
    'labels', 'name', 'new_entity_id', 'new_unique_id', 'options',
    'original_device_class', 'original_icon', 'original_name', 'platform',
    'supported_features', 'translation_key', 'unit_of_measurement'))
_EXTRA_ARGS = frozenset(('object_id_base', 'original_name_unprefixed',
                         'suggested_object_id'))
# Every frame between writer and nearest cause must be one of these exact,
# installed class methods; arbitrary wrappers/callbacks are refused.
_METHODS = {
    'writer': ('helpers.entity_registry', 'EntityRegistry', '_async_update_entity'),
    'update': ('helpers.entity_registry', 'EntityRegistry', 'async_update_entity'),
    'get_or_create': ('helpers.entity_registry', 'EntityRegistry', 'async_get_or_create'),
    'options': ('helpers.entity_registry', 'EntityRegistry', 'async_update_entity_options'),
    'precision': ('components.sensor', 'SensorEntity', '_update_suggested_precision'),
    'sensor_add': ('components.sensor', 'SensorEntity', 'async_internal_added_to_hass'),
    'sensor_update': ('components.sensor', 'SensorEntity', 'async_registry_entry_updated'),
    'finish_add': ('helpers.entity', 'Entity', 'add_to_platform_finish'),
    'state': ('helpers.entity', 'Entity', '_async_write_ha_state'),
    'write_state': ('helpers.entity', 'Entity', 'async_write_ha_state'),
    'add': ('helpers.entity_platform', 'EntityPlatform', '_async_add_entity'),
    'registry_callback': ('helpers.entity', 'Entity', '_async_process_registry_update_or_remove'),
    'fire': ('core', 'EventBus', 'async_fire_internal'),
    'event': ('core', 'Event', '__init__'),
    'utility_init': ('components.utility_meter.sensor', 'UtilityMeterSensor', '__init__'),
    'utility_add': ('components.utility_meter.sensor', 'UtilityMeterSensor', 'async_added_to_hass'),
    'utility_reading': ('components.utility_meter.sensor', 'UtilityMeterSensor', 'async_reading'),
}
_PATHS = (
    ('update', 'get_or_create', 'add'),
    ('get_or_create', 'add_impl'),
    ('options', 'precision', 'sensor_add', 'finish_add', 'add'),
    ('update', 'state', 'write_state', 'finish_add', 'add'),
    ('options', 'precision', 'sensor_update', 'registry_callback'),
    ('update', 'state', 'write_state', 'registry_callback'),
)


def _canonical(value):
    count = [0]
    def check(item, depth=0):
        count[0] += 1
        _need(depth <= 20 and count[0] <= 65536, 'bounds')
        _need(item is None or type(item) in (str, bool, int, float, list, dict), 'json_type')
        if type(item) is str:
            _need(len(item) <= 8192, 'string_bounds')
        if type(item) is dict:
            _need(all(type(k) is str and len(k) <= 256 for k in item), 'json_keys')
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
    try:
        check(value)
        result = json.dumps(value, sort_keys=True, separators=(',', ':'),
                            ensure_ascii=True, allow_nan=False)
        _need(len(result.encode()) <= _MAX_BYTES, 'byte_bounds')
        return result
    except (TypeError, ValueError, RecursionError):
        _need(False, 'json_invalid')


def _load(raw):
    _need(type(raw) is str and len(raw.encode()) <= _MAX_BYTES, 'encoded_type')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, 'duplicate_key')
            result[key] = value
        return result
    try:
        result = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError, RecursionError):
        _need(False, 'json_invalid')
    _need(_canonical(result) == raw, 'noncanonical')
    return result


def _same(left, right):
    return _canonical(left) == _canonical(right)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _stamp(value):
    _need(type(value) is str, 'timestamp')
    try:
        parsed = datetime.fromisoformat(value)
        _need(parsed.tzinfo is not None and parsed.isoformat() == value, 'timestamp')
    except ValueError:
        _need(False, 'timestamp')


def _without_stamp(row):
    return {key: value for key, value in row.items() if key != 'modified_at'}


def _bind(baseline, snapshot, rows):
    # Roundtrip ORIGINAL codec; no capture/adoption from current manager.
    original = encode_runtime_baseline(baseline)
    decode_runtime_baseline(original)
    doc = original['payload']
    _need(type(snapshot) is dict and any(snapshot_equal(snapshot, doc[side])
                                        for side in ('before', 'after')), 'snapshot_binding')
    _need(type(rows) in (tuple, list) and len(rows) == 1 and type(rows[0]) is dict,
          'source_rows')
    rows = deepcopy(list(rows))
    _canonical(rows)
    _need(_same(_without_stamp(rows[0]), _without_stamp(doc['helper_rows'][0])),
          'original_rows_binding')
    _stamp(rows[0]['modified_at'])
    _need(rows[0]['device_id'] is None, 'device_rebinding_unsupported')
    return doc, rows, {'baseline_digest': _digest(original),
        'snapshot': deepcopy(snapshot), 'source_rows': rows}


@dataclass(frozen=True)
class NativeUtilitySetupAuditData:
    """Immutable JSON value, authenticated by the owning durable receipt chain."""
    entry_id: str
    payload_json: str


def _payload(audit):
    _need(type(audit) is NativeUtilitySetupAuditData and type(audit.entry_id) is str,
          'data_type')
    value = _load(audit.payload_json)
    _shape(value)
    _need(value['entry_id'] == audit.entry_id, 'envelope_binding')
    return value


def _shape(value):
    _need(type(value) is dict and set(value) == {'schema', 'core', 'python', 'entry_id',
        'source_pins', 'binding', 'calls', 'events', 'final_rows'}, 'layout')
    _need(type(value['schema']) is int and value['schema'] == 1, 'schema')
    _need(type(value['core']) is str and value['core'] in _SOURCES, 'unsupported_core')
    _need(value['python'] in ('3.13', '3.14'), 'unsupported_python')
    _need(type(value['entry_id']) is str and bool(value['entry_id']), 'entry_id')
    _need(_same(value['source_pins'], _SOURCES[value['core']]), 'source_pins')
    binding = value['binding']
    _need(type(binding) is dict and set(binding) ==
          {'baseline_digest', 'snapshot', 'source_rows'}, 'binding_layout')
    digest = binding['baseline_digest']
    _need(type(digest) is str and len(digest) == 64
          and all(c in '0123456789abcdef' for c in digest), 'binding_digest')
    calls, events = value['calls'], value['events']
    _need(type(calls) is list and 1 <= len(calls) <= _MAX_CALLS, 'call_bounds')
    _need(type(events) is list and len(events) <= _MAX_EVENTS, 'event_bounds')
    for index, call in enumerate(calls):
        _need(type(call) is dict and set(call) == {'index', 'before', 'after',
            'arguments', 'undefined', 'cause', 'path', 'emission'}, 'call_layout')
        _need(type(call['index']) is int and call['index'] == index, 'call_order')
        _need(type(call['before']) is dict and type(call['after']) is dict, 'call_rows')
        _need(type(call['arguments']) is dict and type(call['undefined']) is list,
              'arguments_layout')
        args = _COMMON_ARGS | (_EXTRA_ARGS if value['core'] == '2026.10.2' else set())
        undefined = call['undefined']
        _need(all(type(k) is str for k in undefined)
              and undefined == sorted(set(undefined))
              and not set(undefined) & set(call['arguments'])
              and set(undefined) | set(call['arguments']) == args, 'arguments_scope')
        path, cause = call['path'], call['cause']
        _need(type(path) is list and all(type(k) is str for k in path)
              and tuple(path) in _PATHS
              and ('add_impl' not in path or value['core'] == '2026.10.2')
              and (path != ['update', 'get_or_create', 'add']
                   or value['core'] == '2025.6.0'), 'caller_path')
        _need(type(cause) is dict and set(cause) == {'kind', 'call'}, 'cause_layout')
        if path[-1] in ('add', 'add_impl'):
            _need(cause == {'kind': 'platform_add', 'call': None}, 'add_cause')
        else:
            _need(cause['kind'] == 'registry_event' and type(cause['call']) is int
                  and 0 <= cause['call'] < index, 'event_cause')
        if index == 0:
            _need(cause['kind'] == 'platform_add', 'first_cause')
        emission = call['emission']
        _need(emission is None or type(emission) is dict and set(emission) ==
              {'action', 'entity_id', 'changes'}, 'emission_layout')
    for event in events:
        _need(type(event) is dict and set(event) == {'call', 'payload'}, 'event_layout')
        _need(type(event['call']) is int and 0 <= event['call'] < len(calls), 'event_call')


def encode_setup_audit(audit):
    return {'entry_id': audit.entry_id, 'payload': _payload(audit)}


def decode_setup_audit(value):
    _need(type(value) is dict and set(value) == {'entry_id', 'payload'}
          and type(value['entry_id']) is str, 'envelope_layout')
    result = NativeUtilitySetupAuditData(value['entry_id'], _canonical(value['payload']))
    _payload(result)
    return result


def validate_setup_audit(baseline, expected_snapshot, source_rows, audit):
    """Pure replay from ORIGINAL + caller-accepted source; returns detached final rows.

    JSON cannot authenticate its own history. The caller authenticates audit bytes
    and bindings in the durable intent/result chain, as for the baseline codec.
    No capture/current-manager fallback, no readiness/publication authorization.
    """
    doc, rows, binding = _bind(baseline, expected_snapshot, source_rows)
    value = _payload(audit)
    _need(value['core'] == doc['core'] and value['entry_id'] == doc['entry_id']
          and _same(value['binding'], binding), 'original_binding')
    previous = rows[0]
    mutations = set()
    seen_events = set()
    for event in value['events']:
        index = event['call']
        _need(index not in seen_events, 'duplicate_event')
        seen_events.add(index)
        call = value['calls'][index]
        _need(call['emission'] is not None and _same(event['payload'], call['emission']),
              'event_payload')
    for call in value['calls']:
        before, after = call['before'], call['after']
        _need(_same(before, previous) and set(after) == set(before), 'row_chain')
        args = call['arguments']
        expected = deepcopy(before)
        if call['path'][0] == 'options':
            _need(set(args) == {'options'}, 'options_arguments')
        elif 'get_or_create' in call['path']:
            _need('original_device_class' in args and 'options' not in args, 'add_arguments')
        else:
            state_args = {'original_device_class', 'capabilities', 'supported_features'}
            if value['core'] == '2026.10.2':
                state_args.add('original_name')
            _need('original_device_class' in args and set(args) <= state_args,
                  'state_arguments')
        for name, arg in args.items():
            if name in ('original_device_class', 'options'):
                expected[name] = arg
            else:
                # Pinned async_get_or_create explicitly forwards preserved
                # metadata. Do not confuse a defined no-change argument with a
                # mutation allowance. Other paths must use UNDEFINED.
                native_state_arg = (call['path'][0] == 'update' and 'state' in call['path']
                    and name in ({'capabilities', 'supported_features', 'original_name'}
                        if value['core'] == '2026.10.2' else {'capabilities', 'supported_features'}))
                _need(('get_or_create' in call['path'] or native_state_arg)
                      and name in before and _same(before[name], arg), 'foreign_argument')
        _need(args.keys() & {'original_device_class', 'options'}, 'empty_arguments')
        _need(expected['original_device_class'] in (None, doc['source_class']),
              'device_class_scope')
        # The native precision writer either removes the one suggestion (and
        # drops an empty sensor domain) or restores the exact ORIGINAL options.
        # Empty sensor domains, explicit None and arbitrary precision are not
        # interchangeable with the native argument.
        options = rows[0]['options']
        _need(type(options) is dict and type(options.get('sensor', {})) is dict,
              'options_scope')
        stripped = deepcopy(options)
        if 'sensor' in stripped:
            stripped['sensor'].pop('suggested_display_precision', None)
            if not stripped['sensor']:
                stripped.pop('sensor')
        _need(_same(expected['options'], options) or _same(expected['options'], stripped),
              'options_scope')
        changed = {k for k in before if not _same(before[k], expected[k])}
        _need(changed <= {'original_device_class', 'options'}, 'mutation_scope')
        if changed:
            _stamp(after['modified_at'])
            _need(after['modified_at'] != before['modified_at'], 'missing_native_timestamp')
            expected['modified_at'] = after['modified_at']
            payload = {'action': 'update', 'entity_id': before['entity_id'],
                       'changes': {k: before[k] for k in sorted(changed)}}
            _need(_same(call['emission'], payload), 'emission_old_values')
            mutations.add(call['index'])
        else:
            _need(call['emission'] is None, 'noop_emission')
        _need(_same(expected, after), 'return_mismatch')
        cause = call['cause']
        if cause['kind'] == 'registry_event':
            _need(cause['call'] in mutations and cause['call'] in seen_events, 'event_lineage')
        previous = after
    _need(seen_events == mutations, 'missing_or_extra_event')
    _need(_same(_without_stamp(previous), _without_stamp(rows[0])), 'final_preservation')
    _need(_same(value['final_rows'], [previous]), 'final_rows')
    return deepcopy([previous])


def _code_objects(code):
    yield code
    for const in code.co_consts:
        if type(const) is CodeType:
            yield from _code_objects(const)


def _installed_native():
    """Resolve only installed package paths, then match actual methods to source."""
    _need(sys.implementation.name == 'cpython' and sys.version_info[:2] in
          ((3, 13), (3, 14)), 'unsupported_interpreter')
    const = import_module('homeassistant.const')
    version = const.__version__
    _need(version in _SOURCES, 'unsupported_core')
    package = import_module('homeassistant')
    root = Path(package.__file__).resolve().parent
    compiled, modules = {}, {}
    for relative, expected in _SOURCES[version].items():
        path = root / relative
        _need(path.resolve() == path and path.is_file(), 'installed_path')
        raw = path.read_bytes()
        _need(hashlib.sha256(raw).hexdigest() == expected, 'installed_source')
        # Compile without execution; equality validates actual bytecode, filename,
        # constants, names, line table, flags, etc., against the trusted source.
        compiled[relative] = tuple(_code_objects(compile(raw, str(path), 'exec',
                                      dont_inherit=True, optimize=sys.flags.optimize)))
    methods = dict(_METHODS)
    if version == '2026.10.2':
        methods['add_impl'] = ('helpers.entity_platform', 'EntityPlatform', '_async_add_entity_impl')
        methods['dispatch'] = ('core', 'EventBus', '_async_dispatch')
    objects, codes = {}, {}
    for name, (module_name, class_name, method_name) in methods.items():
        module = modules.setdefault(module_name, import_module('homeassistant.' + module_name))
        relative = module_name.replace('.', '/') + '.py'
        if not (root / relative).is_file():
            relative = module_name.replace('.', '/') + '/__init__.py'
        _need(Path(module.__file__).resolve() == root / relative, 'module_path')
        cls = getattr(module, class_name)
        method = getattr(cls, method_name)
        code = getattr(method, '__code__', None)
        expected = [c for c in compiled[relative]
                    if c.co_qualname == class_name + '.' + method_name]
        _need(len(expected) == 1 and code == expected[0]
              and method.__module__ == 'homeassistant.' + module_name, 'native_code_identity')
        objects[class_name] = cls
        codes[name] = code
    return version, objects, codes


def _installed():
    try:
        return _installed_native()
    except UtilitySetupAuditError:
        raise
    except Exception:
        _need(False, 'installed_source_unavailable')


class NativeUtilitySetupAudit:
    """Temporary, single-thread, fail-closed call/return collector.

    guard() is synchronous installed exclusion/authorization, never a cached
    approval. Keep this context active through final full-three/physical fences
    and durable result append. finish() may be called again to revalidate; after
    finish any subsequent registry call/event poisons this closed evidence. The
    context itself never executes a lifecycle API. It observes one NEW native
    UtilityMeterSensor object, bound by the actual platform-add stack.
    """

    def __init__(self, hass, baseline, expected_snapshot, source_rows, *, guard):
        _need(callable(guard), 'guard_required')
        self._doc, self._source, self._binding = _bind(baseline, expected_snapshot, source_rows)
        self._baseline = baseline
        self._snapshot = deepcopy(expected_snapshot)
        self._hass, self._guard = hass, guard
        self._active = self._entered = self._sealed = False
        self._removing_profile = False
        self._error = None
        self._unsubscribe = None
        self._calls, self._events, self._active_calls = [], [], {}
        self._data, self._event_objects, self._constructing = {}, {}, {}
        self._keepalive = []
        self._new_entity = self._platform = None
        self._created_entity = None
        self._init_frame = None
        self._head = deepcopy(self._source[0])
        # A bound method lookup creates a new object; hold one exact callback.
        self._profile_callback = self._profile

    def _check(self):
        _need(self._error is None, self._error or 'poisoned')
        _need(self._active and threading.get_ident() == self._thread, 'inactive_or_thread')
        _need(sys.getprofile() is self._profile_callback, 'profile_lost')
        self._guard_check()

    def _guard_check(self):
        try:
            accepted = self._guard()
        except Exception:
            _need(False, 'guard_failed')
        _need(accepted is True, 'guard_lost')

    def _poison(self, reason):
        if self._error is None:
            self._error = reason

    def _native(self, value, depth=0, budget=None):
        # ReadOnlyDict is the actual native options converter on both pins.
        # Only this known native container is accepted in addition to JSON.
        if budget is None:
            budget = [8192]
        budget[0] -= 1
        _need(depth <= 16 and budget[0] >= 0, 'native_bounds')
        if type(value) in (dict, self._er.ReadOnlyDict):
            _need(len(value) <= 4096 and all(isinstance(k, str) for k in value), 'native_keys')
            # Core uses native StrEnum keys (for example Platform.SENSOR).
            # Read the underlying str without invoking user conversion hooks,
            # and refuse any collision in the detached JSON representation.
            result = {}
            for key, child in value.items():
                plain_key = str.__str__(key)
                _need(plain_key not in result, 'native_key_collision')
                result[plain_key] = self._native(child, depth + 1, budget)
            return result
        if type(value) in (list, tuple):
            _need(len(value) <= 4096, 'native_bounds')
            return [self._native(child, depth + 1, budget) for child in value]
        if isinstance(value, str):
            value = str.__str__(value)
        _canonical(value)
        return value

    def _row(self, native):
        _need(type(native) is self._classes['RegistryEntry'], 'native_row_type')
        value = json.loads(self._encoder(native.as_storage_fragment))
        _canonical(value)
        return value

    def _rows(self):
        return sorted((self._row(row) for row in self._er.async_entries_for_config_entry(
            self._registry, self._doc['entry_id'])), key=lambda row: row['id'])

    def _bind_source(self):
        _need(self._hass.data.get(self._er.DATA_REGISTRY) is self._registry, 'registry_replaced')
        source = self._er.async_validate_entity_id(self._registry, self._snapshot['options']['source'])
        configured = self._snapshot['options']['source']
        names = (self._doc['source_before'], self._doc['source_after'])
        _need(source in names and (configured == self._doc['source_identity'][0]
              or source == configured), 'source_name')
        row = self._registry.async_get(source)
        _need(row is not None and [getattr(row, k) for k in ('id', 'unique_id', 'platform',
               'config_entry_id', 'config_subentry_id')] == self._doc['source_identity'],
              'source_identity')
        _need(snapshot_equal(read_entry(self._hass, self._doc['entry_id']), self._snapshot),
              'config_snapshot')
        _need(self._hass.config_entries.async_get_entry(self._doc['entry_id']) is self._entry,
              'entry_replaced')

    def __enter__(self):
        _need(not self._entered, 'single_use')
        self._entered = True
        # Refuse before adding a monitor or changing any interpreter profile.
        _need(sys.getprofile() is None, 'existing_profile')
        self._guard_check()
        self._core, self._classes, self._codes = _installed()
        _need(self._core == self._doc['core'], 'baseline_core')
        self._thread = threading.get_ident()
        self._er = import_module('homeassistant.helpers.entity_registry')
        self._classes['RegistryEntry'] = self._er.RegistryEntry
        core = import_module('homeassistant.core')
        self._encoder = import_module('homeassistant.helpers.json')._orjson_bytes_default_encoder
        self._registry = self._hass.data.get(self._er.DATA_REGISTRY)
        _need(type(self._registry) is self._classes['EntityRegistry']
              and type(self._hass.bus) is self._classes['EventBus'], 'manager_type')
        self._entry = self._hass.config_entries.async_get_entry(self._doc['entry_id'])
        _need(self._entry is not None and self._entry.state.value == 'not_loaded', 'entry_not_inactive')
        self._bind_source()
        _need(_same(self._rows(), self._source), 'source_manager_rows')
        self._native_before = self._registry.async_get(self._source[0]['entity_id'])
        self._keepalive.append(self._native_before)
        @core.callback
        def monitor(event):
            self._monitor(event)
        self._monitor_callback = monitor
        try:
            self._unsubscribe = self._hass.bus.async_listen(_EVENT, monitor)
            _need(sys.getprofile() is None, 'existing_profile')
            self._active = True
            sys.setprofile(self._profile_callback)
            self._check()
            return self
        except BaseException:
            self._cleanup()
            raise

    def _cleanup(self):
        # Never overwrite a profiler installed by someone else after loss.
        if self._active:
            if sys.getprofile() is self._profile_callback:
                self._removing_profile = True
                try:
                    sys.setprofile(None)
                finally:
                    self._removing_profile = False
            else:
                self._poison('profile_lost')
        self._active = False
        try:
            if self._unsubscribe is not None:
                self._unsubscribe()
        finally:
            self._unsubscribe = None
            self._active_calls.clear()
            self._data.clear()
            self._event_objects.clear()
            self._constructing.clear()
            self._keepalive.clear()
            self._new_entity = self._platform = self._native_before = None
            self._created_entity = self._init_frame = None

    def __exit__(self, exc_type, exc, traceback):
        lost = self._active and sys.getprofile() is not self._profile_callback
        self._cleanup()
        if exc_type is None:
            _need(not lost and self._error is None, self._error or 'profile_lost')
        return False

    def _cause(self, frame):
        path = []
        frame = frame.f_back
        while frame is not None and len(path) < 12:
            found = [name for name, code in self._codes.items() if code is frame.f_code]
            _need(len(found) == 1, 'unknown_caller')
            name = found[0]
            path.append(name)
            local = frame.f_locals
            self._keepalive.append(frame)
            if name == 'registry_callback':
                entity, event = local.get('self'), local.get('event')
                _need(entity is self._new_entity and entity is not None, 'foreign_entity')
                item = self._event_objects.get(id(event))
                # Native keyed registry dispatch can run this callback before
                # our bus monitor in the same synchronous dispatch. Constructor
                # identity already proves the cause; finish still requires that
                # the monitor actually received every owned Event exactly once.
                _need(item is not None and item['event'] is event and item['ready']
                      and event.data is item['data'] and _same(self._native(event.data), item['payload']),
                      'unknown_event_cause')
                _need(tuple(path) in _PATHS, 'caller_path')
                return {'kind': 'registry_event', 'call': item['call']}, path
            if name in ('add', 'add_impl'):
                platform, entity = local.get('self'), local.get('entity')
                _need(type(platform) is self._classes['EntityPlatform']
                      and platform.config_entry is self._entry and platform.hass is self._hass
                      and local.get('entity_registry') is self._registry
                      and type(entity) is self._classes['UtilityMeterSensor']
                      and entity.hass is self._hass and entity.unique_id == self._doc['entry_id']
                      and entity is self._created_entity and self._init_frame is None,
                      'platform_entity_binding')
                if self._new_entity is None:
                    self._new_entity, self._platform = entity, platform
                    self._keepalive.extend((entity, platform))
                _need(self._new_entity is entity and self._platform is platform, 'foreign_entity')
                _need(tuple(path) in _PATHS, 'caller_path')
                return {'kind': 'platform_add', 'call': None}, path
            if name in ('precision', 'sensor_add', 'sensor_update', 'finish_add', 'state', 'write_state'):
                entity = local.get('self')
                _need(type(entity) is self._classes['UtilityMeterSensor']
                      and (self._new_entity is None or entity is self._new_entity), 'foreign_entity')
            frame = frame.f_back
        _need(False, 'missing_cause')

    def _writer_call(self, frame):
        _need(not self._sealed, 'effect_after_finish')
        _need(len(self._calls) < _MAX_CALLS, 'call_overflow')
        self._check()
        local = frame.f_locals
        _need(local.get('self') is self._registry
              and local.get('entity_id') == self._source[0]['entity_id'], 'foreign_writer')
        before = self._row(self._registry.async_get(local['entity_id']))
        _need(_same(before, self._head), 'unobserved_row_change')
        self._bind_source()
        cause, path = self._cause(frame)
        _need(self._new_entity.hass is self._hass
              and self._new_entity.unique_id == self._doc['entry_id']
              and self._new_entity.platform is self._platform
              and self._platform.hass is self._hass
              and self._platform.config_entry is self._entry, 'entity_binding_lost')
        names = _COMMON_ARGS | (_EXTRA_ARGS if self._core == '2026.10.2' else set())
        _need(set(local) == names | {'self', 'entity_id'}, 'writer_arguments_layout')
        defined, undefined = {}, []
        for name in sorted(names):
            arg = local[name]
            if arg is self._er.UNDEFINED:
                undefined.append(name)
            else:
                # Native BASIC actual args are JSON values. Reject opaque types;
                # serializers must never stringify/repr arbitrary native objects.
                defined[name] = self._native(arg)
        call = {'index': len(self._calls), 'before': before, 'after': None,
                'arguments': defined, 'undefined': undefined, 'cause': cause,
                'path': path, 'emission': None}
        self._calls.append(call)  # CALL order, before nested synchronous callbacks.
        self._active_calls[id(frame)] = (frame, call)
        self._keepalive.append(frame)

    def _writer_return(self, frame, result):
        item = self._active_calls.pop(id(frame), None)
        _need(item is not None and item[0] is frame, 'unmatched_return')
        call = item[1]
        after = self._row(result)  # Actual return, never substituted by manager.
        self._keepalive.append(result)
        if call['emission'] is None:
            _need(_same(after, call['before']), 'mutation_without_emission')
        else:
            emitted = self._data_by_call(call['index'])
            _need(_same(after, emitted['after']) and result is emitted['native_after'],
                  'emission_return_mismatch')
        call['after'] = after

    def _data_by_call(self, index):
        found = [item for item in self._data.values() if item['call'] == index]
        _need(len(found) == 1, 'emission_count')
        return found[0]

    def _fire(self, frame):
        local = frame.f_locals
        if local.get('event_type') != _EVENT:
            return
        _need(not self._sealed, 'event_after_finish')
        data = local.get('event_data')
        # All registry emissions in this single-helper window must be owned.
        parent = frame.f_back
        _need(local.get('self') is self._hass.bus and parent is not None
              and parent.f_code is self._codes['writer'], 'foreign_event')
        item = self._active_calls.get(id(parent))
        _need(item is not None and item[0] is parent, 'unowned_emission')
        call = item[1]
        _need(call['emission'] is None and type(data) is dict and id(data) not in self._data,
              'duplicate_emission')
        _need(len(self._data) < _MAX_EVENTS, 'event_overflow')
        payload = self._native(data)
        _canonical(payload)
        _need(data.get('entity_id') == self._source[0]['entity_id'], 'foreign_event')
        native = self._registry.async_get(data['entity_id'])
        after = self._row(native)
        # Manager was already assigned by this native writer before emission.
        _need(_same(call['before'], self._head), 'emission_chain')
        call['emission'] = deepcopy(payload)
        self._data[id(data)] = {'data': data, 'payload': deepcopy(payload),
            'call': call['index'], 'after': after, 'native_after': native}
        self._head = deepcopy(after)
        self._keepalive.extend((frame, data, native))

    def _event_call(self, frame):
        local = frame.f_locals
        if local.get('event_type') != _EVENT:
            return
        _need(not self._sealed, 'event_after_finish')
        data, event, parent = local.get('data'), local.get('self'), frame.f_back
        item = self._data.get(id(data))
        _need(item is not None and item['data'] is data, 'unowned_event_object')
        _need(parent is not None and parent.f_code is self._codes.get('dispatch', self._codes['fire'])
              and parent.f_locals.get('self') is self._hass.bus
              and parent.f_locals.get('event_data') is data, 'event_constructor_caller')
        _need(type(event) is self._classes['Event'] and id(event) not in self._event_objects
              and not any(e['call'] == item['call'] for e in self._event_objects.values()),
              'duplicate_event_object')
        _need(len(self._event_objects) < _MAX_EVENTS, 'event_overflow')
        record = dict(item, event=event, ready=False, observed=False)
        self._event_objects[id(event)] = record
        self._constructing[id(frame)] = (frame, record)
        self._keepalive.extend((frame, event))

    def _event_return(self, frame):
        item = self._constructing.pop(id(frame), None)
        if item is None:
            return
        record = item[1]
        event = record['event']
        _need(event.event_type == _EVENT and event.data is record['data']
              and _same(self._native(event.data), record['payload']), 'event_data_changed')
        record['ready'] = True

    def _monitor(self, event):
        try:
            _need(not self._sealed, 'event_after_finish')
            self._check()
            record = self._event_objects.get(id(event))
            _need(record is not None and record['event'] is event and record['ready']
                  and not record['observed'] and event.data is record['data']
                  and _same(self._native(event.data), record['payload']), 'unowned_or_duplicate_bus_event')
            record['observed'] = True
        except UtilitySetupAuditError as error:
            self._poison(error.args[0].removeprefix('utility_setup_audit_'))
        except BaseException:
            self._poison('monitor_failed')

    def _profile(self, frame, action, result):
        # Never raise into HA, including cancellation/memory/overflow failures.
        if action == 'c_call' and result is sys.setprofile and not self._removing_profile:
            self._poison('profile_changed')
        if self._error is not None or action not in ('call', 'return'):
            return
        try:
            code = frame.f_code
            if code is self._codes['utility_init']:
                local = frame.f_locals
                if action == 'call' and local.get('parent_meter') == self._doc['entry_id']:
                    _need(not self._sealed and self._created_entity is None
                          and self._init_frame is None
                          and local.get('unique_id') == self._doc['entry_id']
                          and type(local.get('self')) is self._classes['UtilityMeterSensor'],
                          'new_object_binding')
                    self._created_entity, self._init_frame = local['self'], frame
                    self._keepalive.extend((local['self'], frame))
                elif action == 'return' and self._init_frame is frame:
                    self._init_frame = None
            elif code is self._codes['writer']:
                if action == 'call':
                    self._writer_call(frame)
                else:
                    self._writer_return(frame, result)
            elif code is self._codes['fire'] and action == 'call':
                self._fire(frame)
            elif code is self._codes['event']:
                if action == 'call':
                    self._event_call(frame)
                else:
                    self._event_return(frame)
        except UtilitySetupAuditError as error:
            self._poison(error.args[0].removeprefix('utility_setup_audit_'))
        except BaseException:
            self._poison('profile_callback_failed')

    def finish(self):
        """Freeze observed calls; must run while context/profiler remain active."""
        self._check()
        self._bind_source()
        _need(not self._active_calls and not self._constructing, 'pending_calls')
        _need(self._entry.state.value == 'loaded', 'entry_not_loaded')
        _need(self._new_entity is not None, 'missing_new_entity')
        _need(self._new_entity.hass is self._hass
              and self._new_entity.unique_id == self._doc['entry_id']
              and self._new_entity.platform is self._platform
              and self._platform.config_entry is self._entry
              and list(self._platform.entities.values()) == [self._new_entity]
              and self._new_entity.entity_id == self._source[0]['entity_id']
              and self._new_entity.registry_entry is not None
              and self._new_entity.registry_entry.id == self._source[0]['id'],
              'final_entity_binding')
        version, classes, codes = _installed()
        _need(version == self._core and classes == {k: v for k, v in self._classes.items()
               if k != 'RegistryEntry'} and all(codes[k] is self._codes[k] for k in codes),
              'native_identity_lost')
        events = []
        for record in self._event_objects.values():
            _need(record['ready'] and record['observed'] and record['event'].data is record['data']
                  and _same(self._native(record['data']), record['payload']), 'event_identity_lost')
            events.append({'call': record['call'], 'payload': deepcopy(record['payload'])})
        _need(len(events) == len(self._data), 'event_missing')
        for item in self._data.values():
            _need(_same(self._native(item['data']), item['payload']), 'emission_data_changed')
        rows = self._rows()
        _need(_same(rows, [self._head]), 'final_manager_rows')
        value = {'schema': 1, 'core': self._core,
            'python': '.'.join(str(n) for n in sys.version_info[:2]),
            'entry_id': self._doc['entry_id'], 'source_pins': _SOURCES[self._core],
            'binding': self._binding, 'calls': self._calls,
            'events': events, 'final_rows': rows}
        audit = NativeUtilitySetupAuditData(self._doc['entry_id'], _canonical(value))
        validate_setup_audit(self._baseline, self._snapshot, self._source, audit)
        self._check()
        self._sealed = True
        return audit
