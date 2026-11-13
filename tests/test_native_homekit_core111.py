"""Core 11.1 native filter and AID identity using synthetic registry IDs only."""
import unittest
import importlib.util

@unittest.skipUnless(importlib.util.find_spec('homeassistant'), 'native HA dependencies required')
class HomeKitNativeCore111(unittest.TestCase):
    def test_native_filter_and_aid_stability(self):
        from homeassistant.const import __version__
        from homeassistant.components.homekit import FILTER_SCHEMA
        from homeassistant.components.homekit.aidmanager import _generate_aids, get_system_unique_id
        from types import SimpleNamespace
        self.assertEqual(__version__, '2026.11.1')
        before = FILTER_SCHEMA({'exclude_entities':['sensor.example_old']}).get_filter()
        after = FILTER_SCHEMA({'exclude_entities':['sensor.example_new']}).get_filter()
        self.assertFalse(before('sensor.example_old'))
        self.assertFalse(after('sensor.example_new'))
        self.assertTrue(after('sensor.example_old'))
        entry = SimpleNamespace(platform='integration', domain='sensor')
        uid = get_system_unique_id(entry, 'stable-fixture-uuid')
        self.assertEqual(next(_generate_aids(uid, 'sensor.example_old')),
                         next(_generate_aids(uid, 'sensor.example_new')))
        self.assertNotEqual(next(_generate_aids(None, 'sensor.example_old')),
                            next(_generate_aids(None, 'sensor.example_new')))
