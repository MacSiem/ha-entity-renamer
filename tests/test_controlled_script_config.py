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
