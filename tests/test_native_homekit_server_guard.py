"""Unqualified HomeKit rollback is denied within the authenticated scope."""
from types import SimpleNamespace
import unittest
from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
from custom_components.ha_entity_renamer.transaction import Refused

class HomeKitServerGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_checked_before_unsupported_rollback(self):
        root = object.__new__(NativeTemplateStateServer)
        root.receipt = SimpleNamespace(changed_domains=('homekit',))
        events=[]
        root._scope = lambda: events.append('scope')
        with self.assertRaisesRegex(Refused, 'homekit_native_rollback_lifecycle_required'):
            await root.rollback()
        self.assertEqual(events, ['scope'])
        def deny(): raise Refused('scope_denied')
        root._scope = deny
        with self.assertRaisesRegex(Refused, 'scope_denied'):
            await root.rollback()
