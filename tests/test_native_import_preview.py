"""Registered import preview, real AuthManager and read-only actual discovery."""
import asyncio
import json
import unittest

import test_native_backend_auth as auth


@unittest.skipUnless(auth.backend_fixture.activation.fixtures.HAS_HA, 'actual HA required')
class NativeImportPreviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = auth.NativeBackendAuthTests.asyncSetUp
    _before_loader = auth.NativeBackendAuthTests._before_loader
    _before_start = auth.NativeBackendAuthTests._before_start
    _loaded_backend = auth.NativeBackendAuthTests._loaded_backend
    _connection = auth.NativeBackendAuthTests._connection
    with_yaml = True

    async def request(self, content, *, admin=True, extra=None):
        connection, user, output = await self._connection(admin=admin)
        command = 'ha_entity_renamer/import/preview'
        self.assertIn(command, connection.handlers)
        connection.async_handle({'id': 1, 'type': command, 'manifest': content, **(extra or {})})
        return await asyncio.wait_for(output, 30)

    async def test_registered_import_preview_uses_actual_discovery_without_writes(self):
        backend = self._loaded_backend()
        runtime = self.f.hass.data[backend.DATA_RUNTIME]
        paths = [self.f.root / '.storage' / key for key in
                 ('core.entity_registry', 'core.device_registry', 'core.config_entries',
                  'ha_entity_renamer.prepared', 'ha_entity_renamer.migrations')]
        original = {p: p.read_bytes() if p.exists() else None for p in paths}
        document = {'at': '2026-10-20', 'checks': {'ha_writes': 0}, 'entities': [
            {'entity_id': self.source_id, 'proposed_entity_id': 'sensor.import_preview_target',
             'device_id': self.f.entity.async_get(self.source_id).device_id,
             'action': 'Zmiana ID i nazwy'}]}
        result = await self.request(json.dumps(document))
        self.assertTrue(result['success'], result)
        summary = result['result']
        self.assertEqual(summary['changes']['entities'], 1)
        self.assertFalse(summary['migration_ready'])
        self.assertEqual(len(summary['families']), 13)
        self.assertNotIn(self.source_id, json.dumps(summary))
        self.assertFalse(runtime.operation_lock.locked())
        for p, raw in original.items():
            self.assertEqual(p.read_bytes() if p.exists() else None, raw)

    async def test_invalid_import_household_and_client_roots_never_enter_discovery(self):
        result = await self.request('{"entities":[],"root":"/private"}')
        self.assertFalse(result['success'])
        self.assertEqual(result['error']['code'], 'invalid_proposal')
        result = await self.request('{}', admin=False)
        self.assertFalse(result['success'])
        self.assertEqual(result['error']['code'], 'unauthorized')
        result = await self.request('{}', extra={'prepared_ref': 'untrusted'})
        self.assertFalse(result['success'])
        self.assertEqual(result['error']['code'], 'invalid_format')
