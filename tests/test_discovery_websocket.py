"""Native websocket decorators, with explicit fake read-only capture IO."""
import asyncio
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class DiscoveryWebsocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixture.NativeBaselineTests('test_actual_native_serialization_and_store_full_baseline')
        await self.f.asyncSetUp()
        self.addAsyncCleanup(self.f.asyncTearDown)
        self.backend = importlib.import_module('custom_components.ha_entity_renamer')
        self.ws = importlib.import_module(self.backend.__name__ + '.websocket_api')
        self.runtime = self.backend.Runtime('fixture', object(), object())
        self.f.hass.data[self.backend.DATA_RUNTIME] = self.runtime
        self.user = SimpleNamespace(id='fixture-admin', is_admin=True, is_active=True)
        self.f.hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=self.user))
        self.result = {'migration_ready': False, 'families': [], 'capture_id': 'opaque'}
        self.runtime.discover = AsyncMock(return_value=SimpleNamespace(summary=lambda: self.result))
        self.runtime.preview = AsyncMock(return_value=self.result)

    async def call(self, name):
        class Connection:
            def __init__(self, user):
                self.user, self.output, self.done = user, None, asyncio.Event()
            def send_result(self, ident, result):
                self.output = ('result', result)
                self.done.set()
            def send_error(self, ident, code, message):
                self.output = ('error', code, message)
                self.done.set()
            def async_handle_exception(self, message, error):
                self.output = ('exception', type(error).__name__)
                self.done.set()
        connection = Connection(self.user)
        handler = getattr(self.ws, 'ws_' + name)
        message = {'id': 1, 'type': 'ha_entity_renamer/' + name,
                   'proposal': {'entities': []}}
        handler(self.f.hass, connection, message)
        await asyncio.wait_for(connection.done.wait(), timeout=3)
        return connection.output

    async def test_both_commands_use_runtime_and_return_only_sanitized_output(self):
        self.assertEqual(await self.call('discover'), ('result', self.result))
        self.runtime.discover.assert_awaited_once()
        self.assertEqual(await self.call('preview'), ('result', self.result))
        self.runtime.preview.assert_awaited_once()
        self.assertGreaterEqual(self.f.hass.auth.async_get_user.await_count, 4)

    async def test_role_revoked_after_native_capture_does_not_return_result(self):
        async def source(*args, **kwargs):
            self.user.is_admin = False
            return SimpleNamespace(summary=lambda: {'private': 'must not leave'})
        self.runtime.discover.side_effect = source
        result = await self.call('discover')
        self.assertEqual(result[:2], ('error', 'unauthorized'))
        self.assertNotIn('private', str(result))

    async def test_runtime_replacement_after_capture_refuses(self):
        async def source(*args, **kwargs):
            self.f.hass.data[self.backend.DATA_RUNTIME] = object()
            return SimpleNamespace(summary=lambda: self.result)
        self.runtime.discover.side_effect = source
        self.assertEqual((await self.call('discover'))[:2], ('error', 'unavailable'))

    async def test_read_failure_text_is_never_sent_to_the_client(self):
        self.runtime.discover.side_effect = OSError('private secret YAML path')
        result = await self.call('discover')
        self.assertEqual(result[:2], ('error', 'discovery_unavailable'))
        self.assertNotIn('private', str(result))

    async def test_household_and_inactive_admin_refuse(self):
        from homeassistant.exceptions import Unauthorized
        self.user.is_admin = False
        with self.assertRaises(Unauthorized):
            await self.call('discover')
        self.user.is_admin = True
        self.user.is_active = False
        self.assertEqual((await self.call('discover'))[:2], ('error', 'unauthorized'))
        self.runtime.discover.assert_not_awaited()
