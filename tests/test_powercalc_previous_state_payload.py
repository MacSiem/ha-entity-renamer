"""Detached payload checks; these do not prove native PSS or cold restore."""
import json
import unittest

from custom_components.ha_entity_renamer import powercalc_previous_state_payload as codec
from custom_components.ha_entity_renamer.powercalc_pss_handoff import _same


def state(entity_id, value, attributes):
    timestamp = '2026-11-04T00:00:00+00:00'
    return {'entity_id': entity_id, 'state': value, 'attributes': attributes,
            'last_changed': timestamp, 'last_updated': timestamp, 'last_reported': timestamp,
            'context': {'id': '01SYNTHETIC', 'parent_id': None, 'user_id': None}}


def envelope():
    return {'version': 2, 'minor_version': 1, 'key': 'powercalc_group', 'data': {
        'sensor.group': {
            'sensor.a': state('sensor.a', '0.0100000000001', {'unit_of_measurement': 'kWh'}),
            'sensor.b': state('sensor.b', '0.11', {}),
        },
        'sensor.foreign': {'sensor.foreign_child': {'arbitrary': ['preserve', 1]}},
    }}


def raw(value):
    return json.dumps(value, separators=(',', ':')).encode()


class PreviousStatePayloadTests(unittest.TestCase):
    def test_exact_alias_distinguishes_nested_types_and_signed_zero(self):
        original = {'member': state('sensor.a', '1', {'nested': [0, -0.0]})}
        self.assertTrue(_same(json.loads(json.dumps(original)), original))
        for value in ([False, -0.0], [0, 0.0]):
            changed = json.loads(json.dumps(original))
            changed['member']['attributes']['nested'] = value
            self.assertEqual(changed, original)  # Python equality misses both changes.
            self.assertFalse(_same(changed, original))

    def test_retains_aliases_and_foreign_data_for_both_kinds_of_rename(self):
        original = envelope()
        move = codec.GroupMove('sensor.group', 'sensor.new_group',
                               (('sensor.a', 'sensor.new_a'), ('sensor.b', 'sensor.b')))
        result = codec.prepare(raw(original), (move,))
        result_data = json.loads(result.after)['data']
        self.assertEqual(result_data['sensor.group'], original['data']['sensor.group'])
        self.assertEqual(result_data['sensor.foreign'], original['data']['sensor.foreign'])
        self.assertEqual(result_data['sensor.new_group']['sensor.b'], original['data']['sensor.group']['sensor.b'])
        changed = dict(original['data']['sensor.group']['sensor.a'], entity_id='sensor.new_a')
        self.assertEqual(result_data['sensor.new_group']['sensor.new_a'], changed)
        self.assertEqual(json.loads(result.before), original)

    def test_refuses_collisions_missing_members_null_and_duplicate_json_keys(self):
        valid = (codec.GroupMove('sensor.group', 'sensor.group',
                                (('sensor.a', 'sensor.new_a'), ('sensor.b', 'sensor.b'))),)
        cases = []
        missing = envelope(); missing['data']['sensor.group'].pop('sensor.b'); cases.append(raw(missing))
        null = envelope(); null['data']['sensor.group']['sensor.a'] = None; cases.append(raw(null))
        partial = envelope(); partial['data']['sensor.group']['sensor.a'].pop('last_reported'); cases.append(raw(partial))
        collision = envelope(); collision['data']['sensor.group']['sensor.new_a'] = {}; cases.append(raw(collision))
        normalized = envelope(); normalized['data']['sensor.group']['sensor.a']['last_updated'] = '2026-11-04T00:00:00Z'; cases.append(raw(normalized))
        cases.append(b'{"version":2,"version":2,"minor_version":1,"key":"powercalc_group","data":{}}')
        for document in cases:
            with self.subTest(document=document), self.assertRaises(codec.PreviousStatePayloadError):
                codec.prepare(document, valid)

    def test_refuses_cross_group_inconsistent_or_cross_domain_mapping(self):
        original = envelope()
        original['data']['sensor.group2'] = {'sensor.a': state('sensor.a', '1', {})}
        first = codec.GroupMove('sensor.group', 'sensor.group',
                                (('sensor.a', 'sensor.renamed'), ('sensor.b', 'sensor.b')))
        bad = codec.GroupMove('sensor.group2', 'sensor.group2', (('sensor.a', 'sensor.other'),))
        cross = codec.GroupMove('sensor.group2', 'sensor.group2', (('sensor.a', 'switch.a'),))
        for second in (bad, cross):
            with self.subTest(second=second), self.assertRaises(codec.PreviousStatePayloadError):
                codec.prepare(raw(original), (first, second))
