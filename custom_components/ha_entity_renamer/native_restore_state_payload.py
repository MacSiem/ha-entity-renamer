"""Exact captured RestoreState payload codec; NO cache/storage/lifecycle effects.

The envelope is data, never rename authority. Callers must separately bind its
capture to their original plan, UUID and native unload receipt. Native unload
itself may already have lost Context.parent_id; we preserve what was captured.
Only HA 2025.6.0 and 2026.10.2, RestoreState storage version/minor 1/1.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
import sys

PINS = ('2025.6.0', '2026.10.2')
MAX_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 32768
MAX_STRING = 262144
_ENTITY = re.compile(r'[a-z0-9_]+\.[a-z0-9_]+\Z')
_STORE = {'key': 'core.restore_state', 'version': 1, 'minor_version': 1}


class PayloadRejected(ValueError):
    """Unsupported, changed or unrepresentable payload; no fallback conversion."""


def _require(ok, reason):
    if not ok:
        raise PayloadRejected(reason)


def _tree(value):
    """Validate a bounded strict JSON tree, without user-class conversion."""
    remaining = MAX_NODES

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        _require(remaining >= 0 and depth <= MAX_DEPTH, 'payload_complexity')
        kind = type(item)
        if kind is str:
            _require(len(item) <= MAX_STRING, 'payload_string_size')
        elif kind in (bool, int, type(None)):
            pass
        elif kind is float:
            _require(math.isfinite(item), 'payload_nonfinite')
        elif kind is list:
            for child in item:
                visit(child, depth + 1)
        elif kind is dict:
            for key, child in item.items():
                _require(type(key) is str, 'payload_nonstring_key')
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            raise PayloadRejected('payload_unknown_type')
    visit(value, 0)


def _canonical(value):
    _tree(value)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=True, allow_nan=False).encode('ascii')
    _require(len(raw) <= MAX_BYTES, 'payload_size')
    return raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'payload_duplicate_key')
        result[key] = value
    return result


def _parse(raw):
    _require(type(raw) is bytes and len(raw) <= MAX_BYTES, 'payload_bytes')
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               PayloadRejected('payload_nonfinite')))
        _tree(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise PayloadRejected('payload_invalid_json') from error


def _timestamp(value):
    _require(type(value) is str, 'payload_timestamp')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise PayloadRejected('payload_timestamp') from error
    # Reject normalization, including Z/+00:00, missing seconds, and naive time.
    _require(parsed.tzinfo is not None and parsed.isoformat() == value,
             'payload_timestamp_not_exact')
    return parsed


def _shape(payload):
    _require(type(payload) is dict and set(payload) ==
             {'state', 'extra_data', 'last_seen'}, 'stored_state_schema')
    state = payload['state']
    _require(type(state) is dict and set(state) == {
        'entity_id', 'state', 'attributes', 'context',
        'last_changed', 'last_updated', 'last_reported'}, 'state_schema')
    _require(type(state['entity_id']) is str and
             _ENTITY.fullmatch(state['entity_id']), 'state_entity_id')
    _require(type(state['state']) is str and type(state['attributes']) is dict,
             'state_value_or_attributes')
    context = state['context']
    _require(type(context) is dict and set(context) ==
             {'id', 'user_id', 'parent_id'}, 'context_schema')
    _require(type(context['id']) is str and bool(context['id']), 'context_id')
    for key in ('user_id', 'parent_id'):
        _require(context[key] is None or type(context[key]) is str, 'context_field')
    _require(payload['extra_data'] is None or type(payload['extra_data']) is dict,
             'extra_data_schema')
    for key in ('last_changed', 'last_updated', 'last_reported'):
        _timestamp(state[key])
    _timestamp(payload['last_seen'])


def assert_only_entity_id_changed(before, after, old_id, new_id):
    """Full serialized equality proof, with precisely one permitted field delta."""
    _shape(before)
    _shape(after)
    _require(type(old_id) is str and type(new_id) is str and old_id != new_id
             and old_id.split('.', 1)[0] == new_id.split('.', 1)[0], 'rename_endpoints')
    _require(before['state']['entity_id'] == old_id and
             after['state']['entity_id'] == new_id, 'rename_endpoints')
    expected = _parse(_canonical(before))
    expected['state']['entity_id'] = new_id
    _require(_canonical(expected) == _canonical(after), 'payload_changed_outside_entity_id')


@dataclass(frozen=True)
class PreparedPayload:
    """Immutable, detached canonical bytes; does not grant permission to apply."""
    core_version: str
    before_json: bytes
    after_json: bytes

    def __post_init__(self):
        _require(type(self.core_version) is str and self.core_version in PINS,
                 'ha_version_unsupported')
        before, after = _parse(self.before_json), _parse(self.after_json)
        _shape(before)
        _shape(after)
        _require(self.before_json == _canonical(before) and
                 self.after_json == _canonical(after), 'payload_not_canonical')
        assert_only_entity_id_changed(before, after, before['state']['entity_id'],
                                      after['state']['entity_id'])

    @property
    def before_digest(self):
        return hashlib.sha256(self.before_json).hexdigest()

    @property
    def after_digest(self):
        return hashlib.sha256(self.after_json).hexdigest()


def serialize(payload):
    _require(type(payload) is PreparedPayload, 'prepared_payload_type')
    return _canonical({'kind': 'native_restore_state_payload', 'schema': 1,
                       'core_version': payload.core_version, 'store': dict(_STORE),
                       'before': _parse(payload.before_json),
                       'after': _parse(payload.after_json),
                       'before_digest': payload.before_digest,
                       'after_digest': payload.after_digest})


def deserialize(raw):
    envelope = _parse(raw)
    _require(type(envelope) is dict and set(envelope) == {
        'kind', 'schema', 'core_version', 'store', 'before', 'after',
        'before_digest', 'after_digest'}, 'envelope_schema')
    _require(envelope['kind'] == 'native_restore_state_payload' and
             type(envelope['schema']) is int and envelope['schema'] == 1,
             'envelope_schema')
    store = envelope['store']
    _require(type(store) is dict and set(store) == set(_STORE) and
             type(store['version']) is int and type(store['minor_version']) is int and
             store == _STORE, 'restore_store_schema')
    payload = PreparedPayload(envelope['core_version'], _canonical(envelope['before']),
                              _canonical(envelope['after']))
    _require(envelope['before_digest'] == payload.before_digest and
             envelope['after_digest'] == payload.after_digest, 'payload_digest_mismatch')
    return payload


def _native():
    # Lazy: pure envelope validation works without Home Assistant installed.
    from homeassistant.const import __version__
    from homeassistant.core import Context, State
    from homeassistant.helpers.json import json_bytes, json_fragment
    from homeassistant.helpers.restore_state import StoredState, RestoredExtraData, ExtraStoredData
    from homeassistant.util.read_only_dict import ReadOnlyDict
    _require(__version__ in PINS, 'ha_version_unsupported')
    return (__version__, Context, State, json_bytes, json_fragment,
            StoredState, RestoredExtraData, ExtraStoredData, ReadOnlyDict)


def _native_tree(value, api):
    """Whitelist native serialization inputs, excluding arbitrary as_dict/repr."""
    from datetime import date, time
    _, _, _, json_bytes, fragment, _, _, _, readonly = api
    remaining = MAX_NODES

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        _require(remaining >= 0 and depth <= MAX_DEPTH, 'payload_complexity')
        kind = type(item)
        if kind in (dict, readonly):
            for key, child in item.items():
                _require(type(key) is str, 'payload_nonstring_key')
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif kind in (list, tuple, set):
            for child in item:
                visit(child, depth + 1)
        elif kind is fragment:
            _tree(_parse(json_bytes(item)))
        elif isinstance(item, str):
            # Native JSON encodes the underlying string; never call __str__.
            _tree(str.__str__(item))
        elif kind is datetime or (api[0] == '2026.10.2' and kind in (date, time)):
            pass
        else:
            _tree(item)  # exact JSON scalar types only; unknown class refused
    visit(value, 0)


def _capture(stored, api):
    _, _, State, json_bytes, _, StoredState, _, ExtraStoredData, _ = api
    _require(type(stored) is StoredState and type(stored.state) is State,
             'native_stored_state_type')
    # Only exact installed native classes may run conversion. Do not import a
    # component merely to admit a payload, or invoke a foreign subclass.
    known = {api[6]}
    for module_name, class_name in (
        ('homeassistant.components.sensor', 'SensorExtraStoredData'),
        ('homeassistant.components.utility_meter.sensor', 'UtilitySensorExtraStoredData'),
        ('homeassistant.components.integration.sensor', 'IntegrationSensorExtraStoredData'),
    ):
        module = sys.modules.get(module_name)
        native_class = None if module is None else vars(module).get(class_name)
        if isinstance(native_class, type) and issubclass(native_class, ExtraStoredData):
            known.add(native_class)
    _require(stored.extra_data is None or type(stored.extra_data) in known,
             'native_extra_data_type')
    _require(stored.extra_data is None or bool(stored.extra_data),
             'native_falsey_extra_data_unsupported')
    extra = None if stored.extra_data is None else stored.extra_data.as_dict()
    _require(extra is None or type(extra) is dict, 'native_extra_data_schema')
    _native_tree(stored.state.attributes, api)
    _native_tree(extra, api)
    # This is the native orjson encoder (including its Fragment handling), NOT
    # stdlib JSONEncoder, which cannot consume State.json_fragment on these pins.
    raw = {'state': stored.state.json_fragment, 'extra_data': extra,
           'last_seen': stored.last_seen}
    _require(type(stored.last_seen) is datetime, 'native_last_seen_type')
    result = _parse(json_bytes(raw))
    _shape(result)
    return result


def prepare_payload(stored, target_entity_id):
    """Read-only capture of actual StoredState, and proof of intended one-field delta."""
    api = _native()
    before = _capture(stored, api)
    after = _parse(_canonical(before))
    after['state']['entity_id'] = target_entity_id
    payload = PreparedPayload(api[0], _canonical(before), _canonical(after))
    # Prove constructors can faithfully represent the capture before returning it.
    materialize(payload)
    return payload


def materialize(payload):
    """New native objects only; exact postimage proof before returning clone."""
    _require(type(payload) is PreparedPayload, 'prepared_payload_type')
    api = _native()
    version, Context, State, _, _, StoredState, RestoredExtraData, _, _ = api
    _require(version == payload.core_version, 'ha_version_mismatch')
    row = _parse(payload.after_json)
    state, context = row['state'], row['state']['context']
    native_state = State(state['entity_id'], state['state'], state['attributes'],
                         last_changed=_timestamp(state['last_changed']),
                         last_updated=_timestamp(state['last_updated']),
                         last_reported=_timestamp(state['last_reported']),
                         context=Context(id=context['id'], user_id=context['user_id'],
                                         parent_id=context['parent_id']))
    extra = None if row['extra_data'] is None else RestoredExtraData(row['extra_data'])
    clone = StoredState(native_state, extra, _timestamp(row['last_seen']))
    _require(_canonical(_capture(clone, api)) == payload.after_json,
             'native_clone_not_exact')
    return clone


# Explicit Integration data migration. Utility schema 1 above is unchanged.
@dataclass(frozen=True)
class PreparedIntegrationPayload:
    """Detached data only; applying it also requires the live native witness."""
    core_version: str
    before_json: bytes
    after_json: bytes
    capture_json: bytes

    def __post_init__(self):
        from decimal import Decimal, InvalidOperation
        _require(self.core_version in PINS, 'ha_version_unsupported')
        before, after, capture = (_parse(raw) for raw in
            (self.before_json, self.after_json, self.capture_json))
        _shape(before)
        _shape(after)
        _require(all(raw == _canonical(value) for raw, value in (
            (self.before_json, before), (self.after_json, after),
            (self.capture_json, capture))), 'payload_not_canonical')
        keys = {'core_version', 'entry_id', 'helper_uuid', 'entity_id',
                'full_state_text', 'last_valid_state_text', 'before_sha256'}
        _require(type(capture) is dict and set(capture) == keys
                 and all(type(v) is str and bool(v) for v in capture.values()),
                 'integration_capture_schema')
        _require(capture['core_version'] == self.core_version
                 and capture['entity_id'] == before['state']['entity_id']
                 and capture['before_sha256'] == self.before_digest,
                 'integration_capture_binding')
        for key in ('full_state_text', 'last_valid_state_text'):
            try:
                value = Decimal(capture[key])
            except InvalidOperation:
                raise PayloadRejected('integration_decimal') from None
            _require(value.is_finite() and str(value) == capture[key], 'integration_decimal')
        extra = before['extra_data']
        _require(type(extra) is dict and set(extra) == {'native_value',
                 'native_unit_of_measurement', 'source_entity', 'last_valid_state'},
                 'integration_extra_schema')
        last = capture['last_valid_state_text']
        _require(extra['last_valid_state'] == (last if Decimal(last) else None),
                 'integration_original_last_valid')
        target = after['state']['entity_id']
        _require(target.startswith('sensor.') and before['state']['entity_id'].startswith('sensor.'),
                 'integration_sensor_endpoint')
        expected = _parse(self.before_json)
        expected['state']['entity_id'] = target
        expected['extra_data']['native_value'] = capture['full_state_text']
        expected['extra_data']['last_valid_state'] = last
        _require(_canonical(expected) == self.after_json,
                 'integration_changed_outside_transfer')

    @property
    def before_digest(self):
        return hashlib.sha256(self.before_json).hexdigest()

    @property
    def after_digest(self):
        return hashlib.sha256(self.after_json).hexdigest()


def validate_integration_capture(payload, witness):
    """Historical bytes never substitute for actual live native removal."""
    from dataclasses import asdict
    from .native_integration_unload_audit import NativeIntegrationUnloadWitness
    _require(type(payload) is PreparedIntegrationPayload
             and type(witness) is NativeIntegrationUnloadWitness,
             'integration_live_witness_required')
    witness.check_live()
    capture = witness.capture
    _require(payload.core_version == capture.core_version
             and payload.capture_json == _canonical(asdict(capture))
             and payload.before_json == witness._audit._before,
             'integration_live_capture_mismatch')


def prepare_integration_payload(stored, target_entity_id, *, witness):
    """Actual post-unload source plus the original native getter witness."""
    from dataclasses import asdict
    from .native_integration_unload_audit import NativeIntegrationUnloadWitness
    _require(type(witness) is NativeIntegrationUnloadWitness,
             'integration_live_witness_required')
    witness.check_live(stored=stored)
    api = _native()
    before = _capture(stored, api)
    capture = witness.capture
    after = _parse(_canonical(before))
    after['state']['entity_id'] = target_entity_id
    after['extra_data']['native_value'] = capture.full_state_text
    after['extra_data']['last_valid_state'] = capture.last_valid_state_text
    result = PreparedIntegrationPayload(api[0], _canonical(before), _canonical(after),
                                        _canonical(asdict(capture)))
    validate_integration_capture(result, witness)
    materialize_integration_payload(result)
    witness.check_live(stored=stored)
    return result


def serialize_integration_payload(payload):
    _require(type(payload) is PreparedIntegrationPayload, 'integration_payload_type')
    return _canonical({'kind': 'native_restore_state_payload', 'schema': 2,
        'adapter': 'integration_exact_v1', 'store': dict(_STORE),
        'core_version': payload.core_version, 'before': _parse(payload.before_json),
        'after': _parse(payload.after_json), 'capture': _parse(payload.capture_json),
        'before_digest': payload.before_digest, 'after_digest': payload.after_digest})


def deserialize_integration_payload(raw):
    row = _parse(raw)
    _require(type(row) is dict and set(row) == {'kind', 'schema', 'adapter', 'store',
        'core_version', 'before', 'after', 'capture', 'before_digest', 'after_digest'},
        'integration_envelope_schema')
    _require(row['kind'] == 'native_restore_state_payload'
             and type(row['schema']) is int and row['schema'] == 2
             and row['adapter'] == 'integration_exact_v1'
             and type(row['store']) is dict and row['store'] == _STORE
             and type(row['store']['version']) is int
             and type(row['store']['minor_version']) is int, 'integration_envelope_schema')
    result = PreparedIntegrationPayload(row['core_version'], _canonical(row['before']),
        _canonical(row['after']), _canonical(row['capture']))
    _require(result.before_digest == row['before_digest']
             and result.after_digest == row['after_digest'], 'payload_digest_mismatch')
    return result


def materialize_integration_payload(payload):
    """Exact native clone, plus real native parser validation; no cache writes."""
    from decimal import Decimal
    from homeassistant.components.integration.sensor import IntegrationSensorExtraStoredData
    _require(type(payload) is PreparedIntegrationPayload, 'integration_payload_type')
    api = _native()
    version, Context, State, _, _, StoredState, RestoredExtraData, _, _ = api
    _require(version == payload.core_version, 'ha_version_mismatch')
    row, capture = _parse(payload.after_json), _parse(payload.capture_json)
    state, context = row['state'], row['state']['context']
    clone = StoredState(State(state['entity_id'], state['state'], state['attributes'],
        last_changed=_timestamp(state['last_changed']),
        last_updated=_timestamp(state['last_updated']),
        last_reported=_timestamp(state['last_reported']),
        context=Context(id=context['id'], user_id=context['user_id'],
                        parent_id=context['parent_id'])),
        RestoredExtraData(row['extra_data']), _timestamp(row['last_seen']))
    _require(_canonical(_capture(clone, api)) == payload.after_json, 'native_clone_not_exact')
    parsed = IntegrationSensorExtraStoredData.from_dict(clone.extra_data.as_dict())
    _require(type(parsed) is IntegrationSensorExtraStoredData
             and type(parsed.native_value) is str
             and parsed.native_value == capture['full_state_text']
             and parsed.last_valid_state.as_tuple()
                 == Decimal(capture['last_valid_state_text']).as_tuple(),
             'integration_native_parser_not_exact')
    return clone
