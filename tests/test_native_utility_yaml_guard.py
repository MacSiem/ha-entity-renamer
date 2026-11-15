import unittest
from custom_components.ha_entity_renamer.native_utility_yaml_guard import selected_yaml_sources
from custom_components.ha_entity_renamer.transaction import Refused

class UtilityYAMLGuardTests(unittest.TestCase):
    def test_reports_only_selected_yaml_sources_not_config_entries(self):
        data = {'meter1': {'source':'sensor.old'}, 'meter2': {'source':'sensor.old'},
                'ui-entry': {'source':'sensor.old'}, 'foreign': {'source':'sensor.other'}}
        self.assertEqual(selected_yaml_sources(data, {'ui-entry'}, {'sensor.old':'sensor.new'}),
                         ({'entity_id':'sensor.old', 'reason':'utility_yaml_native_activation_unavailable',
                           'consumer_count':2},))
    def test_unknown_or_malformed_inventory_is_not_accepted(self):
        for data in (None, {'meter':{}}, {'meter': {'source':None}}):
            with self.subTest(data=data), self.assertRaises(Refused):
                selected_yaml_sources(data, set(), {'sensor.old':'sensor.new'})
