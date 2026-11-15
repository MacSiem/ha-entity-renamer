"""A staged Script configuration accepts only typed, sealed raw YAML."""
import unittest

from custom_components.ha_entity_renamer.controlled_script_config import _raw
from custom_components.ha_entity_renamer.transaction import Refused


class ControlledScriptConfigTests(unittest.TestCase):
    def test_raw_nested_data_preserves_order_and_values(self):
        value = {'$map': [['alias', 'Scene'], ['sequence', [{'$map': [['event', 'saved'],
                                                                         ['event_data', {'$map': [['v', 0]]}]]}]]]}
        self.assertEqual(_raw(value), {'alias': 'Scene', 'sequence': [{'event': 'saved', 'event_data': {'v': 0}}]})

    def test_rejects_opaque_markers_duplicate_keys_and_malformed_pairs(self):
        for value in ({'$template': '{{ x }}'}, {'$map': [['x', 1], ['x', 2]]},
                      {'$map': [['x']]}, {'$map': [[1, 'value']]}, {'arbitrary': 'mapping'}):
            with self.subTest(value=value), self.assertRaises(Refused):
                _raw(value)

    def test_integer_keys_round_trip_without_string_collisions(self):
        value = {'$map': [[{'$int': 1}, 'number'], ['1', 'string']]}
        self.assertEqual(list(_raw(value).items()), [(1, 'number'), ('1', 'string')])
        for key in ({'$int': True}, {'$int': 1.0}, {'$int': 1, 'extra': 0},
                    {'$other': 1}, True):
            with self.subTest(key=key), self.assertRaises(Refused):
                _raw({'$map': [[key, 'value']]})
        with self.assertRaises(Refused):
            _raw({'$map': [[{'$int': 1}, 'a'], [{'$int': 1}, 'b']]})
