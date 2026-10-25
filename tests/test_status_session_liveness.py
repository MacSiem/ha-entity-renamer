"""Native WS decorators and HA runtime; explicit fake read-only journal/auth IO.

This invokes the actual handler, not a public WebSocket transport or real login.
The persisted user and connection session are deliberately independent objects.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import test_native_input_baseline as fixture
import test_discovery_websocket as websocket_fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class SessionStatusTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = websocket_fixture.DiscoveryWebsocketTests.asyncSetUp
    call_handler = websocket_fixture.DiscoveryWebsocketTests.call

    async def setup_status(self):
        self.session = self.user
        self.persisted = SimpleNamespace(id=self.user.id, is_admin=True, is_active=True)
        self.f.hass.auth.async_get_user = AsyncMock(return_value=self.persisted)
        self.active = [SimpleNamespace(operation_id='private-opaque-operation',
                                      status='pending', direction='forward')]
        self.runtime.journal = SimpleNamespace(active=AsyncMock(return_value=self.active))

    async def call_status(self):
        return await self.call_handler('status')

    async def test_inactive_session_cannot_receive_operations(self):
        await self.setup_status()
        self.session.is_active = False
        output = await self.call_status()
        self.assertEqual(output[:2], ('error', 'unauthorized'))
        self.assertNotIn('private-opaque-operation', str(output))

    async def test_session_deactivated_during_journal_await(self):
        await self.setup_status()
        async def read():
            self.session.is_active = False
            return self.active
        self.runtime.journal.active.side_effect = read
        output = await self.call_status()
        self.assertEqual(output[:2], ('error', 'unauthorized'))
        self.assertNotIn('private-opaque-operation', str(output))

    async def test_valid_admin_status_remains_readonly(self):
        await self.setup_status()
        output = await self.call_status()
        self.assertEqual(output[0], 'result')
        self.assertIs(output[1]['migration_ready'], False)
        self.assertEqual(output[1]['operations'], [vars(self.active[0])])

    async def test_persisted_admin_revoked(self):
        await self.setup_status()
        self.persisted.is_admin = False
        self.assertEqual((await self.call_status())[:2], ('error', 'unauthorized'))

    async def test_session_admin_revoked_during_await(self):
        await self.setup_status()
        async def read():
            self.session.is_admin = False
            return self.active
        self.runtime.journal.active.side_effect = read
        self.assertEqual((await self.call_status())[:2], ('error', 'unauthorized'))

    async def test_identity_changed_during_await(self):
        await self.setup_status()
        async def read():
            self.session.id = 'different-session-user'
            return self.active
        self.runtime.journal.active.side_effect = read
        self.assertEqual((await self.call_status())[:2], ('error', 'unauthorized'))

    async def test_runtime_unloaded_during_await(self):
        await self.setup_status()
        async def read():
            self.runtime.unloading = True
            return self.active
        self.runtime.journal.active.side_effect = read
        self.assertEqual((await self.call_status())[:2], ('error', 'unavailable'))

    async def test_read_errors_are_sanitized(self):
        await self.setup_status()
        self.runtime.journal.active.side_effect = RuntimeError('private-storage-path')
        output = await self.call_status()
        self.assertEqual(output[:2], ('error', 'recovery_unavailable'))
        self.assertNotIn('private-storage-path', str(output))
