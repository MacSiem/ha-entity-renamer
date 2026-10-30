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

    async def test_invalid_or_unsupported_selection_preserves_native_inputs(self):
        runtime, old, new, uuids, original, payload = await self.selection()
        native = self.module('native_registry').NativeRegistryIO(self.f.hass)
        before = (native.document('entity'), native.document('device'))
        selections = [
            ({**payload, 'actor_id': 'client-supplied'}, 'invalid_format'),
            ({'entities': [{**payload['entities'][0], 'roots': []}]}, 'invalid_format'),
            ({'entities': [payload['entities'][0]] * 2}, 'invalid_selection'),
            ({'entities': [{'entity_id': old, 'new_entity_id': 'light.changed'}]}, 'invalid_selection'),
            ({'entities': [{'entity_id': old, 'name': 'bad\nname'}]}, 'invalid_selection'),
            ({'entities': [{'entity_id': old, 'name': 'Name only'}]}, 'unsupported_selection'),
        ]
        for selected, code in selections:
            with self.subTest(code=code, keys=tuple(selected)):
                connection, user, output = await self._connection(admin=True)
                connection.async_handle({'id': 1, 'type': 'ha_entity_renamer/prepare', **selected})
                response = await output
                self.assertFalse(response['success'])
                self.assertEqual(response['error']['code'], code, response)
                self.assertNotIn('result', response)
                self.assertFalse(runtime.reviews)
                self.assertEqual(await runtime.journal.active(), ())
                self.assertEqual((native.document('entity'), native.document('device')), before)
                self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))

    async def test_network_review_replacement_and_reload_reclaim_abandoned_preparations(self):
        from aiohttp import ClientSession
        runtime, old, new, uuids, original, payload = await self.selection()
        hass = self.f.hass
        connection, user, unused = await self._connection(admin=True)
        token = await hass.auth.async_create_refresh_token(user,
            client_id='https://renamer.invalid/ordinary-selection-network-qa')
        self.addCleanup(hass.auth.async_remove_refresh_token, token)
        access = hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
        foreign = 'synthetic-unowned-preparation'
        await runtime.preparations.create(foreign, {'kind': 'synthetic-unowned', 'keep': True})
        foreign_row = runtime.preparations.plans[foreign]
        async with ClientSession() as client:
            async with client.ws_connect('http://127.0.0.1:' + str(hass.http.server_port)
                                         + '/api/websocket') as ws:
                self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_required')
                await ws.send_json({'type': 'auth', 'access_token': access})
                self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_ok')
                first_count = None
                for ident in (1, 2, 3):
                    await ws.send_json({'id': ident, 'type': 'ha_entity_renamer/prepare', **payload})
                    response = await ws.receive_json(timeout=60)
                    self.assertTrue(response.get('success'), response)
                    self.assertFalse(response['result']['migration_ready'])
                    self.assertEqual(len(runtime.reviews), 1)
                    current_count = len(runtime.preparations.plans)
                    if first_count is None:
                        first_count = current_count
                    self.assertEqual(current_count, first_count)
        self.assertTrue(await hass.config_entries.async_reload(self.backend_entry_id))
        await hass.async_block_till_done()
        fresh = hass.data[self._loaded_backend().DATA_RUNTIME]
        self.assertIsNot(fresh, runtime)
        self.assertFalse(fresh.reviews)
        self.assertEqual(fresh.preparations.plans, {foreign: foreign_row})
        receipts = self.module('native_registry_receipts').native_registry_receipt_store(hass, fresh.preparations)
        await receipts.initialize()
        self.assertEqual(receipts.rows, {})
        self.assertEqual(await fresh.journal.all(), ())
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        await self.stimulus(7)

    async def test_failed_native_prepare_reclaims_only_its_lease_and_preserves_foreign_yaml(self):
        from unittest.mock import patch
        runtime, old, new, uuids, original, payload = await self.selection()
        hass = self.f.hass
        factory = self.module('native_template_state_server').NativeTemplateStateServer
        actual_common = factory._common
        edited = original + b'\n# synthetic foreign edit during final review admission\n'
        injected = []
        async def change_before_final_admission(session, rollback, **kwargs):
            if kwargs.get('initial') and not injected:
                injected.append(True)
                await hass.async_add_executor_job((self.f.root / 'template.yaml').write_bytes, edited)
            return await actual_common(session, rollback, **kwargs)
        connection, user, output = await self._connection(admin=True)
        with patch.object(factory, '_common', change_before_final_admission):
            connection.async_handle({'id': 1, 'type': 'ha_entity_renamer/prepare', **payload})
            response = await output
        self.assertTrue(injected, 'fault must follow actual durable preparations and root creation')
        self.assertFalse(response['success'])
        self.assertFalse(runtime.reviews)
        self.assertEqual(runtime.preparations.plans, {})
        receipts = self.module('native_registry_receipts').native_registry_receipt_store(hass, runtime.preparations)
        await receipts.initialize()
        self.assertEqual(receipts.rows, {})
        self.assertEqual(await runtime.journal.all(), ())
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), edited)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        await hass.async_add_executor_job((self.f.root / 'template.yaml').write_bytes, original)
        await self.stimulus(7)
