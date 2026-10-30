"""Ordinary selection through installed backend and native HA authorization.

One case per fresh native probe. No network-login or full UI claim.
"""
import unittest

import test_native_backend_auth as auth
import test_native_backend_lifecycle as backend_fixture
import test_native_template_yaml_server as server


@unittest.skipUnless(backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativeQueuePrepareAuthTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = backend_fixture.NativeBackendLifecycleTests.asyncSetUp
    _before_loader = backend_fixture.NativeBackendLifecycleTests._before_loader
    _before_start = backend_fixture.NativeBackendLifecycleTests._before_start
    _loaded_backend = backend_fixture.NativeBackendLifecycleTests._loaded_backend
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    data = server.NativeTemplateYAMLServerTests.data
    stimulus = server.NativeTemplateYAMLServerTests.stimulus
    _connection = auth.NativeBackendAuthTests._connection
    _revocation_while_waiting = auth.NativeBackendAuthTests._revocation_while_waiting

    def module(self, name):
        return backend_fixture.installed_module(name)

    async def _runtime(self, module):
        backend = self._loaded_backend()
        runtime = self.f.hass.data[backend.DATA_RUNTIME]
        self.assertIs(type(runtime), backend.Runtime)
        return runtime

    async def selection(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        old, new = next(iter(mapping.items()))
        return runtime, old, new, uuids, original, {
            'entities': [{'entity_id': old, 'new_entity_id': new,
                          'name': 'Reviewed native selection'}]}

    async def test_admin_prepare_retains_server_review_without_mutation(self):
        runtime, old, new, uuids, original, payload = await self.selection()
        hass = self.f.hass
        native = self.module('native_registry').NativeRegistryIO(hass)
        before = (native.document('entity'), native.document('device'))
        connection, user, output = await self._connection(admin=True)
        command = 'ha_entity_renamer/prepare'
        self.assertTrue(command in connection.handlers, 'installed prepare command missing')
        self.assertIs(connection.handlers[command][0], self.module('websocket_api').ws_prepare)
        connection.async_handle({'id': 1, 'type': command, **payload})
        response = await output
        self.assertTrue(response['success'], response)
        review = response['result']
        self.assertFalse(review['migration_ready'])
        self.assertFalse(review['family_complete'])
        self.assertEqual(review['changes'], [{'entity_id': old, 'fields': {
            'entity_id': {'before': old, 'after': new},
            'name': {'before': None, 'after': 'Reviewed native selection'}}}])
        self.assertEqual(runtime.reviews[review['operation_id']].actor_id, user.id)
        self.assertEqual((native.document('entity'), native.document('device')), before)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual(await runtime.journal.active(), ())
        self.assertIsNone(await runtime.journal.load(review['operation_id']))
        await self.stimulus(7)

    async def test_household_prepare_denied_without_review(self):
        runtime, old, new, uuids, original, payload = await self.selection()
        connection, user, output = await self._connection(admin=False)
        connection.async_handle({'id': 1, 'type': 'ha_entity_renamer/prepare', **payload})
        response = await output
        self.assertFalse(response['success'])
        self.assertEqual(response['error']['code'], 'unauthorized')
        self.assertFalse(runtime.reviews)
        self.assertEqual(await runtime.journal.active(), ())
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))

    async def test_prepare_revocation_during_real_runtime_lock_wait(self):
        runtime, old, new, uuids, original, payload = await self.selection()
        await self._revocation_while_waiting('prepare', payload=payload,
                                               runtime_method='native_yaml_prepare')
        self.assertFalse(runtime.reviews)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
