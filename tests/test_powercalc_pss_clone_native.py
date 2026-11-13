"""Native State clone preserves the serialized preimage and live report clock."""
from datetime import timedelta
import json
import unittest


class PowercalcPSSCloneNativeTests(unittest.TestCase):
    def test_moved_and_unmoved_native_states_preserve_both_report_views(self):
        from homeassistant.core import State
        from homeassistant.helpers.json import json_bytes
        from custom_components.ha_entity_renamer.powercalc_pss_handoff import _clone

        for moved in (False, True):
            for reports in (0, 2):
                with self.subTest(moved=moved, reports=reports):
                    info = object()
                    source = State('sensor.old', '3', {'unit': 'kWh'}, state_info=info)
                    before = dict(source.as_dict())
                    before_json = json_bytes(source.json_fragment)
                    for _ in range(reports):
                        source.last_reported += timedelta(seconds=60)
                        source._cache['last_reported_timestamp'] = source.last_reported.timestamp()
                    live = source.last_reported
                    clone = _clone(source, 'sensor.new' if moved else 'sensor.old')
                    expected = dict(before, entity_id='sensor.new' if moved else 'sensor.old')
                    self.assertEqual(dict(clone.as_dict()), expected)
                    self.assertEqual(json.loads(clone.as_dict_json), expected)
                    self.assertEqual(json.loads(json_bytes(clone.json_fragment)), expected)
                    self.assertEqual(clone.last_reported, live)
                    self.assertEqual(clone.last_reported_timestamp, live.timestamp())
                    self.assertIs(clone.context, source.context)
                    self.assertIs(clone.state_info, info)
                    self.assertEqual(clone.last_updated_timestamp, source.last_updated_timestamp)
                    self.assertIsNot(clone, source)
                    self.assertIsNot(clone._cache, source._cache)
                    self.assertEqual(dict(source.as_dict()), before)
                    self.assertEqual(json_bytes(source.json_fragment), before_json)
                    clone._cache['_as_dict']['state'] = 'changed only in clone'
                    self.assertEqual(dict(source.as_dict()), before)
                    self.assertEqual(json_bytes(source.json_fragment), before_json)
