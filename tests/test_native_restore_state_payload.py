"""Codec unit tests; synthetic payloads do NOT prove helper consumer acceptance.

Native-type cases require an actual installed pinned Home Assistant. They create
isolated State/StoredState values only: no HA cache, config, API or DB access.
Worker delivers tests without running them; owner runs through FIFO.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
import sys
import unittest

# Works for the additive delivery and after placing files together in tests.
_path = Path(__file__).with_name('native_restore_state_payload.py')
if _path.exists():
    _spec = importlib.util.spec_from_file_location('restore_payload_draft', _path)
    codec = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = codec
    _spec.loader.exec_module(codec)
else:
    from custom_components.ha_entity_renamer import native_restore_state_payload as codec


def sample():
    return {'state': {
        'entity_id': 'sensor.old', 'state': '17.000000000001',
        'attributes': {'unit_of_measurement': 'kWh', 'nested': [{'none': None}, []]},
        'context': {'id': '01CONTEXT', 'user_id': None, 'parent_id': '01PARENT'},
        'last_changed': '2026-10-25T10:00:00.000001+01:00',
        'last_updated': '2026-10-25T10:00:01.000002+01:00',
        'last_reported': '2026-10-25T10:00:02.000003+01:00'},
        'extra_data': {'last_valid_state': '17.000000000001', 'nested': {}},
        'last_seen': '2026-10-25T10:00:03.000004+01:00'}


def prepared(before=None, pin='2026.10.2'):
    before = sample() if before is None else before
    after = deepcopy(before)
    after['state']['entity_id'] = 'sensor.new'
    return codec.PreparedPayload(pin, codec._canonical(before), codec._canonical(after))


class PureCodecTests(unittest.TestCase):
    def test_both_pins_roundtrip_detached_frozen_bytes(self):
        for pin in codec.PINS:
            before = sample()
            payload = prepared(before, pin)
            before['extra_data']['nested']['later'] = 1
            self.assertEqual(codec.deserialize(codec.serialize(payload)), payload)
            self.assertNotIn(b'later', payload.before_json)
            with self.assertRaises(FrozenInstanceError):
                payload.core_version = 'bad'

    def test_empty_extra_and_null_are_distinct(self):
        rows = []
        for extra in (None, {}):
            value = sample()
            value['extra_data'] = extra
            rows.append(codec.deserialize(codec.serialize(prepared(value))))
        self.assertNotEqual(rows[0].after_digest, rows[1].after_digest)

    def test_full_delta_proof_refuses_each_other_field_change(self):
        original = sample()
        changes = [
            ('state', 'state', '18'),
            ('state', 'attributes', {}),
            ('state', 'context', {'id': '01CONTEXT', 'user_id': None, 'parent_id': None}),
            ('state', 'last_changed', '2026-10-25T10:00:00.000002+01:00'),
            ('state', 'last_updated', '2026-10-25T10:00:01.000003+01:00'),
            ('state', 'last_reported', '2026-10-25T10:00:02.000004+01:00'),
            (None, 'extra_data', {}),
            (None, 'last_seen', '2026-10-25T10:00:03.000005+01:00'),
        ]
        for parent, key, replacement in changes:
            after = deepcopy(original)
            after['state']['entity_id'] = 'sensor.new'
            (after if parent is None else after[parent])[key] = replacement
            with self.subTest(field=key), self.assertRaises(codec.PayloadRejected):
                codec.assert_only_entity_id_changed(original, after, 'sensor.old', 'sensor.new')

    def test_reject_unknown_pin_schema_digest_duplicate_and_bool_schema(self):
        raw = codec.serialize(prepared())
        envelope = json.loads(raw)
        cases = []
        for key, value in [('core_version', '2026.10.3'), ('schema', True),
                           ('before_digest', '0' * 64), ('surprise', 1)]:
            changed = deepcopy(envelope)
            changed[key] = value
            cases.append(json.dumps(changed).encode())
        changed = deepcopy(envelope)
        changed['store']['version'] = True
        cases.append(json.dumps(changed).encode())
        cases.extend([b'{"schema":1,"schema":1}', b'{"x":NaN}',
                      b'[' * 10000 + b']' * 10000, b'x' * (codec.MAX_BYTES + 1)])
        for value in cases:
            with self.subTest(length=len(value)), self.assertRaises(codec.PayloadRejected):
                codec.deserialize(value)

    def test_reject_schema_extension_unknown_class_nonfinite_depth_and_key(self):
        class Unknown:
            def as_dict(self):
                return {'silently': 'converted'}
        for value in (Unknown(), float('nan'), {1: 'nonstring'},
                      'x' * (codec.MAX_STRING + 1)):
            with self.assertRaises(codec.PayloadRejected):
                codec._canonical(value)
        nested = None
        for _ in range(codec.MAX_DEPTH + 1):
            nested = [nested]
        with self.assertRaises(codec.PayloadRejected):
            codec._canonical(nested)
        row = sample()
        row['state']['future'] = 1
        with self.assertRaises(codec.PayloadRejected):
            prepared(row)

    def test_timestamp_spelling_and_offset_are_not_normalized(self):
        for value in ('2026-10-25T09:00:00Z', '2026-10-25T09:00:00',
                      '2026-10-25T09:00+00:00'):
            row = sample()
            row['last_seen'] = value
            with self.assertRaises(codec.PayloadRejected):
                prepared(row)
        self.assertIn(b'+01:00', prepared().after_json)


class NativeTypeTests(unittest.TestCase):
    """Actual installed HA types, still NOT runtime/lifecycle acceptance."""
    @classmethod
    def setUpClass(cls):
        try:
            from homeassistant.const import __version__
        except ImportError:
            raise unittest.SkipTest('requires actual installed Home Assistant')
        if __version__ not in codec.PINS:
            raise unittest.SkipTest('requires HA 2025.6.0 or 2026.10.2')

    def make_native(self, extra):
        from homeassistant.core import Context, State
        from homeassistant.helpers.restore_state import StoredState, RestoredExtraData
        instant = datetime(2026, 10, 25, 10, 1, 2, 345678,
                           tzinfo=timezone(timedelta(hours=1)))
        state = State('sensor.old', '17.000000000001',
                      {'nested': [{'empty': {}, 'null': None}], 'set': {'a', 'b'}},
                      last_changed=instant, last_updated=instant + timedelta(seconds=1),
                      last_reported=instant + timedelta(seconds=2),
                      context=Context(id='01CONTEXT', user_id='01USER', parent_id='01PARENT'))
        return StoredState(state, None if extra is None else RestoredExtraData(extra),
                           instant + timedelta(seconds=3))

    def test_actual_cached_fragment_clone_preserves_all_fields_without_mutation(self):
        from homeassistant.helpers.json import json_bytes
        for extra in (None, {}, {'last_valid_state': '17.000000000001', 'nested': [None, {}]}):
            source = self.make_native(extra)
            before = json_bytes(source.as_dict())  # populate real native JSON cache
            source_state, source_attrs = source.state, source.state.attributes
            payload = codec.prepare_payload(source, 'sensor.new')
            reopened = codec.deserialize(codec.serialize(payload))
            clone = codec.materialize(reopened)
            self.assertIs(source.state, source_state)
            self.assertIs(source.state.attributes, source_attrs)
            self.assertEqual(json_bytes(source.as_dict()), before)
            self.assertIsNot(clone, source)
            self.assertIsNot(clone.state, source.state)
            self.assertEqual(clone.state.entity_id, 'sensor.new')
            self.assertEqual((clone.state.domain, clone.state.object_id), ('sensor', 'new'))
            self.assertEqual(clone.state.context.parent_id, '01PARENT')
            self.assertEqual(clone.state.context.user_id, '01USER')
            self.assertEqual(clone.state.context.id, '01CONTEXT')
            self.assertEqual(codec._canonical(json.loads(json_bytes(clone.as_dict()))), payload.after_json)
            self.assertEqual(clone.last_seen.isoformat(), source.last_seen.isoformat())
            self.assertEqual(clone.extra_data is None, extra is None)
            if clone.extra_data is not None:
                self.assertEqual(clone.extra_data.as_dict(), extra)
            clone.state.attributes['nested'][0]['empty']['changed'] = True
            self.assertEqual(source_attrs['nested'][0]['empty'], {})

    def test_native_rejects_unknown_objects_instead_of_calling_as_dict_or_repr(self):
        class Unknown:
            def as_dict(self):
                raise AssertionError('must not call unknown conversion')
            def __repr__(self):
                raise AssertionError('must not repr unknown class')
        source = self.make_native({'unknown': Unknown()})
        with self.assertRaises(codec.PayloadRejected):
            codec.prepare_payload(source, 'sensor.new')

    def test_noop_invalid_target_and_native_nan_are_refused(self):
        for target in ('sensor.old', 'bad target', 'sensor.UPPER'):
            with self.assertRaises(codec.PayloadRejected):
                codec.prepare_payload(self.make_native(None), target)
        with self.assertRaises(codec.PayloadRejected):
            codec.prepare_payload(self.make_native({'bad': float('nan')}), 'sensor.new')


if __name__ == '__main__':
    unittest.main()
