"""Removed file-import route stays absent from the real HA websocket server.

Uses synthetic input only. Ordinary queue proposals use the separate preview
API; a historical import request must never enter discovery or mutate stores.
"""
import asyncio
import unittest

import test_native_backend_auth as auth


@unittest.skipUnless(auth.backend_fixture.activation.fixtures.HAS_HA, 'actual HA required')
class NativeNoImportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = auth.NativeBackendAuthTests.asyncSetUp
    _before_loader = auth.NativeBackendAuthTests._before_loader
    _before_start = auth.NativeBackendAuthTests._before_start
    _loaded_backend = auth.NativeBackendAuthTests._loaded_backend
    _connection = auth.NativeBackendAuthTests._connection
    _flush_documents = auth.backend_fixture.NativeBackendLifecycleTests._flush_documents
    with_yaml = True

    async def test_removed_import_route_is_unknown_for_admin_and_household(self):
        await self._flush_documents(initialize=True)
        runtime = self.f.hass.data[self._loaded_backend().DATA_RUNTIME]
        paths = [self.f.root / '.storage' / key for key in
                 ('core.entity_registry', 'core.device_registry', 'core.config_entries',
                  'ha_entity_renamer.prepared', 'ha_entity_renamer.migrations')]
        before = {p: p.read_bytes() if p.exists() else None for p in paths}
        operations = await runtime.journal.all()
        for admin in (True, False):
            connection, user, output = await self._connection(admin=admin)
            self.assertNotIn('ha_entity_renamer/import/preview', connection.handlers)
            self.assertIn('ha_entity_renamer/preview', connection.handlers)
            connection.async_handle({'id': 1, 'type': 'ha_entity_renamer/import/preview',
                                     'manifest': '{}'})
            result = await asyncio.wait_for(output, 10)
            self.assertFalse(result['success'])
            self.assertEqual(result['error']['code'], 'unknown_command')
        self.assertFalse(runtime.operation_lock.locked())
        self.assertEqual(await runtime.journal.all(), operations)
        for p, raw in before.items():
            self.assertEqual(p.read_bytes() if p.exists() else None, raw)

    async def test_removed_import_is_unknown_over_authenticated_network(self):
        from aiohttp import ClientSession
        hass = self.f.hass
        connection, user, _ = await self._connection(admin=True)
        token = await hass.auth.async_create_refresh_token(user,
            client_id='https://renamer.invalid/no-import-network-qa')
        self.addCleanup(hass.auth.async_remove_refresh_token, token)
        access = hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
        runtime = hass.data[self._loaded_backend().DATA_RUNTIME]
        original = await runtime.journal.all()
        async with ClientSession() as client:
            async with client.ws_connect('http://127.0.0.1:' + str(hass.http.server_port)
                                         + '/api/websocket') as ws:
                self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_required')
                await ws.send_json({'type': 'auth', 'access_token': access})
                self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_ok')
                await ws.send_json({'id': 1, 'type': 'ha_entity_renamer/import/preview',
                                    'manifest': '{}'})
                response = await ws.receive_json(timeout=10)
                self.assertFalse(response.get('success'), response)
                self.assertEqual(response['error']['code'], 'unknown_command')
        self.assertEqual(await runtime.journal.all(), original)
        self.assertFalse(runtime.operation_lock.locked())


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativeNoImportTests)
