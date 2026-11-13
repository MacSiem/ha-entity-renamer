"""Installed HomeKit AID/reload code must match its pinned source."""
import importlib
import unittest
from unittest.mock import patch
import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class HomeKitSourceAuditTests(unittest.TestCase):
    def test_aid_and_reload_monkeypatches_fail_closed(self):
        from homeassistant.components.homekit import aidmanager, services
        audit = importlib.import_module(fixture.PACKAGE + '.native_homekit_helper_observers')
        refused = importlib.import_module(fixture.PACKAGE + '.transaction').Refused
        audit._installed()
        async def foreign_reload(service): pass
        with patch.object(services, '_handle_homekit_reload', foreign_reload):
            with self.assertRaises(refused): audit._installed()
        with patch.object(aidmanager, '_generate_aids', lambda *args: ()):
            with self.assertRaises(refused): audit._installed()
        audit._installed()
