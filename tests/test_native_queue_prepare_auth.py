"""Ordinary selection through installed backend and native HA authorization.

One case per fresh native probe. No network-login or full UI claim.
"""
import unittest
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path

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

    async def test_registered_prepare_admits_own_card_but_refuses_foreign_card(self):
        from homeassistant.setup import async_setup_component
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        from homeassistant.components.lovelace.dashboard import LovelaceStorage
        runtime, old, new, uuids, original, payload = await self.selection()
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'lovelace', {'lovelace': {'mode': 'storage'}}))
        await hass.async_block_till_done()
        dashboard = hass.data[LOVELACE_DATA].dashboards[None]
        self.assertIs(type(dashboard), LovelaceStorage)
        config = {'views': [{'cards': [{'type': 'custom:ha-entity-renamer',
            'title': old, 'show_support': False, 'tabs': ['devices', 'queue'],
            'grid_options': {'columns': 12, 'rows': 'auto'}}]}]}
        await dashboard.async_save(config)
        await hass.async_block_till_done()
        path = self.f.root / '.storage/lovelace'
        before = path.read_bytes()
        async with self._transport() as (ws, _):
            response = await self._request(ws, 1, 'prepare', **payload)
            self.assertTrue(response['success'], response)
            self.assertEqual(path.read_bytes(), before, 'display options must remain byte-for-byte unchanged')
            config['views'][0]['cards'][0] = {'type': 'custom:unknown-consumer'}
            await dashboard.async_save(config)
            await hass.async_block_till_done()
            foreign = path.read_bytes()
            denied = await self._request(ws, 2, 'prepare', **payload)
            self.assertFalse(denied['success'], denied)
            self.assertEqual(denied['error']['code'], 'unsupported_selection')
            self.assertEqual(path.read_bytes(), foreign)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual(await runtime.journal.all(), ())

    async def test_registered_prepare_with_loaded_empty_native_scene(self):
        from homeassistant.setup import async_setup_component
        from homeassistant.components.scene import DATA_COMPONENT
        runtime, old, new, uuids, original, payload = await self.selection()
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'scene', {}))
        await hass.async_block_till_done()
        self.assertIn('scene', hass.config.components)
        self.assertEqual(tuple(hass.data[DATA_COMPONENT].entities), ())
        async with self._transport() as (ws, user):
            response = await self._request(ws, 1, 'prepare', **payload)
            self.assertTrue(response['success'], response)
            self.assertEqual(len(runtime.reviews), 1)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
            self.assertEqual(await runtime.journal.active(), ())

    async def test_native_empty_scene_latches_create_delete_and_reload(self):
        from homeassistant.setup import async_setup_component
        from homeassistant.components.scene import DATA_COMPONENT
        await self.selection()
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'scene', {}))
        await hass.async_block_till_done()
        module = self.module('native_empty_scene')
        for operation in ('create_delete', 'reload'):
            fence = module.NativeEmptySceneFence(hass)
            try:
                fence.check()
                if operation == 'create_delete':
                    await hass.services.async_call('scene', 'create', {
                        'scene_id': 'renamer_race', 'entities': {'sensor.synthetic': '7'}}, blocking=True)
                    await hass.async_block_till_done()
                    self.assertIsNotNone(hass.states.get('scene.renamer_race'))
                    await hass.services.async_call('scene', 'delete', {
                        'entity_id': 'scene.renamer_race'}, blocking=True)
                else:
                    await hass.services.async_call('scene', 'reload', {}, blocking=True)
                await hass.async_block_till_done()
                self.assertEqual(tuple(hass.data[DATA_COMPONENT].entities), ())
                with self.assertRaises(self.module('transaction').Refused):
                    fence.check()
            finally:
                fence.close()
            self.assertEqual(fence.removers, [])

    async def test_native_empty_scene_refuses_existing_scene_and_pending_addition(self):
        from homeassistant.setup import async_setup_component
        from homeassistant.components.scene import DATA_COMPONENT
        from homeassistant.helpers.entity_platform import EntityPlatform
        from unittest.mock import patch
        await self.selection()
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'scene', {}))
        await hass.async_block_till_done()
        module = self.module('native_empty_scene')
        ready, release = asyncio.Event(), asyncio.Event()
        original = EntityPlatform._async_add_entity
        async def held(platform, *args, **kwargs):
            if platform.domain == 'scene':
                ready.set()
                await release.wait()
            return await original(platform, *args, **kwargs)
        with patch.object(EntityPlatform, '_async_add_entity', held):
            await hass.services.async_call('scene', 'create', {
                'scene_id': 'renamer_pending', 'entities': {'sensor.synthetic': '7'}}, blocking=True)
            await asyncio.wait_for(ready.wait(), 5)
            try:
                self.assertEqual(tuple(hass.data[DATA_COMPONENT].entities), ())
                with self.assertRaises(self.module('transaction').Refused):
                    module.NativeEmptySceneFence(hass)
            finally:
                release.set()
                await hass.async_block_till_done()
        self.assertIsNotNone(hass.states.get('scene.renamer_pending'))
        with self.assertRaises(self.module('transaction').Refused):
            module.NativeEmptySceneFence(hass)

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

    async def test_script_state_trigger_refusal_identifies_dependency_without_write(self):
        from unittest.mock import patch
        runtime, old, new, uuids, original, payload = await self.selection()
        hass = self.f.hass
        native = self.module('native_registry').NativeRegistryIO(hass)
        before = (native.document('entity'), native.document('device'))
        connection, _, output = await self._connection(admin=True)
        refused = self.module('transaction').Refused
        async def unsupported(*args, **kwargs):
            raise refused('own_id_script_state_trigger_unqualified:automation.watcher:script.selected')
        with patch.object(type(runtime), 'native_yaml_prepare', unsupported):
            connection.async_handle({'id': 1, 'type': 'ha_entity_renamer/prepare', **payload})
            response = await output
        self.assertFalse(response['success'])
        self.assertEqual(response['error']['code'], 'unsupported_script_state_trigger')
        self.assertEqual(json.loads(response['error']['message']),
            {'automation': 'automation.watcher', 'script': 'script.selected'})
        self.assertEqual((native.document('entity'), native.document('device')), before)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual(await runtime.journal.active(), ())
        self.assertFalse(runtime.reviews)

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

    async def test_unrecognized_chain_evidence_without_journal_survives_reclaim(self):
        """Unknown sidecar data is protected; this is not an actual intent/effect."""
        import hashlib
        runtime, old, new, uuids, original, payload = await self.selection()
        hass = self.f.hass
        connection, user, output = await self._connection(admin=True)
        connection.async_handle({'id': 1, 'type': 'ha_entity_renamer/prepare', **payload})
        response = await output
        self.assertTrue(response['success'], response)
        receipts = self.module('native_registry_receipts').native_registry_receipt_store(hass, runtime.preparations)
        await receipts.initialize()
        self.assertEqual(len(receipts.rows), 1)
        root = next(iter(receipts.rows))
        await receipts.create('rr-' + hashlib.sha256(b'synthetic-retention-unknown').hexdigest(),
            {'kind': 'synthetic_unrecognized_evidence', 'chain': root})
        keys = ('ha_entity_renamer.prepared', 'ha_entity_renamer.registry_receipts')
        before = {key: (self.f.root / '.storage' / key).read_bytes() for key in keys}
        runtime.reviews.clear()  # An abandoned handle does not authorize evidence deletion.
        async with runtime.operation_lock:
            runtime.preparations, runtime.journal = await self.module('review_retention').async_reclaim_reviews(
                hass, guard=lambda: None)
        self.assertEqual(await runtime.journal.all(), ())
        self.assertEqual({key: (self.f.root / '.storage' / key).read_bytes() for key in keys}, before)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))


    @asynccontextmanager
    async def _transport(self, *, admin=True):
        """Actual HA WebSocket dispatch/auth, with reusable response IDs."""
        from aiohttp import ClientSession
        connection, user, unused = await self._connection(admin=admin)
        self.assertIs(connection.handlers['ha_entity_renamer/apply'][0],
                      self.module('websocket_api').ws_apply)
        token = await self.f.hass.auth.async_create_refresh_token(user,
            client_id='https://renamer.invalid/retained-plan-transport-qa')
        self.addCleanup(self.f.hass.auth.async_remove_refresh_token, token)
        access = self.f.hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
        async with ClientSession() as client:
            async with client.ws_connect('http://127.0.0.1:' + str(self.f.hass.http.server_port)
                                         + '/api/websocket') as ws:
                self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_required')
                await ws.send_json({'type': 'auth', 'access_token': access})
                self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_ok')
                # Current Core migrates fixture HTTP YAML to a pending config.
                # Confirm it only after real network authentication worked, using
                # HA's registered admin command before taking a migration baseline.
                if admin and 'http/config/promote' in connection.handlers:
                    from homeassistant.components.http.config import async_get_and_load_store
                    store = await async_get_and_load_store(self.f.hass)
                    if store.pending is not None:
                        connection.async_handle({'id': 1, 'type': 'http/config/promote'})
                        confirmed = await asyncio.wait_for(unused, 10)
                        self.assertTrue(confirmed['success'], confirmed)
                        self.assertIsNone(store.pending)
                        self.assertIsNone(store.revert_deadline)
                yield ws, user

    async def _request(self, ws, ident, action, **payload):
        await ws.send_json({'id': ident, 'type': 'ha_entity_renamer/' + action, **payload})
        result = await ws.receive_json(timeout=600 if action == 'recovery/restore' else 180)
        self.assertEqual(result['id'], ident)
        return result

    async def test_registered_apply_actor_schema_and_duplicate_preserve_native_data(self):
        runtime, old, new, uuids, original, payload = await self.selection()
        async with self._transport() as (ws, user):
            response = await self._request(ws, 1, 'prepare', **payload)
            self.assertTrue(response['success'], response)
            review = response['result']
            binding = {key: review[key] for key in ('operation_id', 'fingerprint')}
            self.assertEqual(runtime.reviews[binding['operation_id']].actor_id, user.id)
            self.assertEqual(review['dependencies']['domains'], ['template'])
            self.assertEqual(review['dependencies']['yaml_file_count'], 1)
            self.assertEqual([row['path'] for row in review['dependencies']['files']], ['template.yaml'])
            for row in review['dependencies']['files']:
                self.assertGreater(row['edit_count'], 0)
                self.assertTrue(row['references'])
                for reference in row['references']:
                    self.assertEqual((reference['before'], reference['after']), (old, new))
                    self.assertGreater(reference['occurrences'], 0)
            self.assertGreater(review['dependencies']['template_consumer_count'], 0)
            for ident, extra in enumerate(({'actor_id': user.id}, {'roots': []}, {'plan': {}}), 2):
                denied = await self._request(ws, ident, 'apply', **binding, **extra)
                self.assertEqual(denied['error']['code'], 'invalid_format')
            async with self._transport() as (other, other_user):
                self.assertNotEqual(other_user.id, user.id)
                denied = await self._request(other, 1, 'apply', **binding)
                self.assertEqual(denied['error']['code'], 'apply_refused')
                self.assertNotIn('result', denied)
            async with self._transport(admin=False) as (household, _):
                denied = await self._request(household, 1, 'apply', **binding)
                self.assertEqual(denied['error']['code'], 'unauthorized')
            self.assertIn(binding['operation_id'], runtime.reviews)
            self.assertIsNone(await runtime.journal.load(binding['operation_id']))
            self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            applied = await self._request(ws, 5, 'apply', **binding)
            self.assertTrue(applied['success'], applied)
            self.assertEqual(applied['result']['state'], 'applied')
            self.assertFalse(applied['result']['migration_ready'])
            self.assertFalse(applied['result']['family_complete'])
            self.assertNotIn(binding['operation_id'], runtime.reviews)
            record = await runtime.journal.load(binding['operation_id'])
            self.assertEqual(record.phases, ('verified',) * len(record.plan.steps))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(old))
            await self.stimulus(7)
            protected = {name: (self.f.root / name).read_bytes() for name in (
                'template.yaml', '.storage/ha_entity_renamer.migrations',
                '.storage/ha_entity_renamer.prepared', '.storage/ha_entity_renamer.registry_receipts')}
            duplicate = await self._request(ws, 6, 'apply', **binding)
            self.assertEqual(duplicate['error']['code'], 'apply_refused')
            status = await self._request(ws, 7, 'status')
            self.assertTrue(status['success'], status)
            row, = [row for row in status['result']['operations']
                    if row['operation_id'] == binding['operation_id']]
            self.assertEqual(row['phases'], [{'adapter': step.adapter, 'phase': phase}
                for step, phase in zip(record.plan.steps, record.phases, strict=True)])
            self.assertEqual(row['progress'], {'total_steps': len(record.plan.steps),
                'verified_steps': len(record.plan.steps), 'undone_steps': 0})
            self.assertFalse(row['eligible'])
            restore = await self._request(ws, 8, 'recovery/restore',
                operation_id=binding['operation_id'], fingerprint='0' * 64)
            self.assertTrue(restore['success'], restore)
            self.assertEqual((restore['result']['state'], restore['result']['reason']),
                             ('refused', 'operation_mismatch'))
            self.assertEqual({name: (self.f.root / name).read_bytes() for name in protected}, protected)
            self.assertEqual(await runtime.journal.load(binding['operation_id']), record)
            serialized = json.dumps((review, applied, status))
            for private in ('receipt_json', 'prepared_ref', 'native_inputs', 'configuration.yaml',
                            '{{', 'access_token'):
                self.assertNotIn(private, serialized)

    async def test_registered_apply_revocation_during_real_runtime_lock_wait(self):
        from homeassistant.auth.const import GROUP_ID_USER
        runtime, old, new, uuids, original, payload = await self.selection()
        async with self._transport() as (ws, user):
            response = await self._request(ws, 1, 'prepare', **payload)
            self.assertTrue(response['success'], response)
            binding = {key: response['result'][key] for key in ('operation_id', 'fingerprint')}
            await runtime.operation_lock.acquire()
            try:
                await ws.send_json({'id': 2, 'type': 'ha_entity_renamer/apply', **binding})
                # Wait for actual native Lock contention, never replace apply/auth.
                for _ in range(1000):
                    if runtime.operation_lock._waiters:
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(runtime.operation_lock._waiters)
                await self.f.hass.auth.async_update_user(user, group_ids=[GROUP_ID_USER])
                self.assertFalse(user.is_admin)
                self.assertIs(await self.f.hass.auth.async_get_user(user.id), user)
            finally:
                runtime.operation_lock.release()
            denied = await ws.receive_json(timeout=30)
            self.assertEqual(denied['id'], 2)
            self.assertEqual(denied['error']['code'], 'unauthorized')
            self.assertNotIn('result', denied)
            self.assertIn(binding['operation_id'], runtime.reviews)
            self.assertIsNone(await runtime.journal.load(binding['operation_id']))
            self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))

    async def test_registered_mixed_apply_keeps_actual_consumers_and_history(self):
        await self._registered_mixed_apply()

    async def test_registered_mixed_apply_with_empty_scene_and_public_inverse(self):
        await self._registered_mixed_apply(with_scene=True)

    async def test_registered_mixed_final_verifier_unknown_fresh_inspect_and_public_inverse(self):
        await self._registered_mixed_apply(with_scene=True, uncertain_complete=True)

    async def _registered_mixed_apply(self, *, with_scene=False, uncertain_complete=False):
        import test_native_template_mixed_yaml_server as mixed
        from homeassistant.core import Context
        runtime, mapping, uuids, dependents, original = await (
            mixed.NativeTemplateMixedYAMLServerTests.data(self))
        hass = self.f.hass
        if with_scene:
            from homeassistant.setup import async_setup_component
            self.assertTrue(await async_setup_component(hass, 'scene', {}))
            await hass.async_block_till_done()
            # Scene setup schedules a native delayed repairs Store write. Settle
            # bootstrap IO before capturing the immutable input graph, exactly
            # as the fixture does for entity/device/config-entry Stores.
            from homeassistant.helpers import issue_registry
            issues = issue_registry.async_get(hass)
            await issues._store.async_save(issues._data_to_save())
            self.assertIsNone(issues._store._data)
            self.assertIsNone(issues._store._delay_handle)
            self.assertEqual(json.loads(Path(issues._store.path).read_bytes())['data'],
                             issues._data_to_save())
        history = self.module('history_guard').NativeHistoryGuard(
            self.module('recorder_guard').NativeRecorderGuard(hass))
        source_history = await history.capture(mapping)
        consumers = {entity_id: entity_id for entity_id, _ in self.identities.values()}
        consumers.update({entity_id: entity_id for _, _, entity_id, _ in dependents})
        consumer_history = await history.capture(consumers)
        self.assertTrue(consumer_history['state_ids'])
        events = {'automation': [], 'script': []}
        for domain in events:
            def observed(event, domain=domain):
                events[domain].append(str(event.data['value']))
            self.addCleanup(hass.bus.async_listen('mixed_yaml_' + domain + '_result', observed))
        async with self._transport() as (ws, user):
            response = await self._request(ws, 1, 'prepare', entities=[
                {'entity_id': old, 'new_entity_id': new} for old, new in mapping.items()])
            self.assertTrue(response['success'], response)
            review = response['result']
            self.assertEqual(review['dependencies']['domains'], ['automation', 'script', 'template'])
            self.assertEqual(review['dependencies']['yaml_file_count'], 3)
            self.assertEqual({row['path'] for row in review['dependencies']['files']},
                             {'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'})
            self.assertTrue(all(row['references'] for row in review['dependencies']['files']))
            binding = {key: review[key] for key in ('operation_id', 'fingerprint')}
            plan = runtime.reviews[binding['operation_id']].plan
            original_receipt = runtime.preparations.resolve_composition(plan.native_inputs)[0].receipt_json
            if uncertain_complete:
                from unittest.mock import patch
                subject = self.module('native_template_state_server').NativeTemplateStateServer
                failures = []
                actual_full = subject._full
                async def fail_final_once(session, rollback):
                    current = await session.journal.load(session.operation_id)
                    if not rollback and not failures:
                        self.assertEqual(current.phases, ('verified',) * len(current.plan.steps))
                        self.assertEqual(current.direction, 'forward')
                        failures.append(current)
                        # Only fail the verifier boundary. Native effects, receipts,
                        # source/runtime/history and all later checks remain real.
                        raise OSError('synthetic one-time final verifier unavailable')
                    return await actual_full(session, rollback)
                with patch.object(subject, '_full', fail_final_once):
                    result = await self._request(ws, 2, 'apply', **binding)
                self.assertEqual(len(failures), 1)
                self.assertFalse(result['success'], result)
                self.assertEqual(result['error']['code'], 'apply_refused')
                self.assertNotIn(binding['operation_id'], runtime.reviews)
            else:
                with mixed.native_diagnostics(self.module):
                    result = await self._request(ws, 2, 'apply', **binding)
                self.assertTrue(result['success'], result)
                self.assertEqual(result['result']['state'], 'applied')
            self.assertEqual(events, {'automation': [], 'script': []})
            record = await runtime.journal.load(binding['operation_id'])
            self.assertEqual(record.plan, plan)
            if uncertain_complete:
                self.assertEqual((record.status, record.reason), ('unknown', 'final_verification_uncertain'))
                self.assertEqual(record.phases, ('verified',) * len(plan.steps))
            self.assertEqual(runtime.preparations.resolve_composition(plan.native_inputs)[0].receipt_json,
                             original_receipt)
            for old, new in mapping.items():
                self.assertIsNone(self.f.entity.async_get(old))
                self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
                for name in ('template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'):
                    self.assertNotIn(old.encode(), (self.f.root / name).read_bytes())
                    self.assertIn(new.encode(), (self.f.root / name).read_bytes())
            self.assertEqual(tuple(sorted((row.platform, row.unique_id, row.entity_id, row.id)
                for row in self.f.entity.entities.values()
                if row.platform in ('automation', 'script'))), dependents)
            for value in (11, 13):
                await self.stimulus(value)
                await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
                await hass.async_block_till_done()
                self.assertEqual(events['automation'][-1], str(value))
                self.assertEqual(events['script'][-1], str(value))
            self.assertTrue(await history.verify(source_history))
            self.assertTrue(await history.verify(consumer_history))
            status = await self._request(ws, 3, 'status')
            self.assertTrue(status['success'], status)
            row, = [row for row in status['result']['operations'] if row['operation_id'] == binding['operation_id']]
            self.assertEqual(row['progress']['verified_steps'], len(record.plan.steps))
            self.assertFalse(status['result']['migration_ready'])
            self.assertFalse(status['result']['family_complete'])

        if with_scene:
            # New authenticated connection and fresh native inverse after forward
            # source fences closed; neither client Plan nor private Runtime call.
            async with self._transport(admin=False) as (household, _):
                denied = await self._request(household, 1, 'recovery/restore', **binding)
                self.assertEqual(denied['error']['code'], 'unauthorized')
            async with self._transport() as (reverse_ws, _):
                if uncertain_complete:
                    before_inspect = {p: (self.f.root / p).read_bytes() for p in original}
                    durable_paths = [self.f.root / '.storage' / ('ha_entity_renamer.' + name)
                                     for name in ('migrations', 'prepared', 'registry_receipts')]
                    durable_before = {path: path.read_bytes() for path in durable_paths}
                    inspected = await self._request(reverse_ws, 3, 'recovery/inspect', **binding)
                    self.assertTrue(inspected['success'], inspected)
                    self.assertEqual(inspected['result']['state'], 'recoverable_complete', inspected)
                    self.assertIs(inspected['result']['eligible'], True)
                    self.assertIs(inspected['result']['restore_attempt_available'], True)
                    review = inspected['result']['frozen_review']
                    self.assertEqual(len(review['identities']), len(review['changes']))
                    self.assertTrue(all(change['fields'] for change in review['changes']))
                    self.assertEqual(review['dependencies']['yaml_file_count'],
                                     len(review['dependencies']['files']))
                    self.assertFalse(inspected['result']['migration_ready'])
                    self.assertEqual(await runtime.journal.load(binding['operation_id']), record)
                    self.assertEqual({p: (self.f.root / p).read_bytes() for p in original}, before_inspect)
                    self.assertEqual({path: path.read_bytes() for path in durable_paths}, durable_before)
                    # Explicit inverse must independently reject a foreign source
                    # edit after inspection, without consuming/fabricating receipts.
                    path = self.f.root / 'mixed_scripts.yaml'
                    protected = path.read_bytes()
                    try:
                        path.write_bytes(protected + b'\n# foreign edit after UNKNOWN inspection\n')
                        denied = await self._request(reverse_ws, 4, 'recovery/restore', **binding)
                        self.assertTrue(denied['success'], denied)
                        self.assertIs(denied['result']['eligible'], False)
                        self.assertEqual(denied['result']['state'], 'UNKNOWN', denied)
                        self.assertEqual(await runtime.journal.load(binding['operation_id']), record)
                        self.assertEqual({path: path.read_bytes() for path in durable_paths}, durable_before)
                        self.assertTrue(path.read_bytes().endswith(b'# foreign edit after UNKNOWN inspection\n'))
                    finally:
                        path.write_bytes(protected)
                restored = await self._request(reverse_ws, 5, 'recovery/restore', **binding)
                self.assertTrue(restored['success'], restored)
                self.assertEqual(restored['result']['state'], 'rolled_back', restored)
                self.assertEqual(restored['result']['operation_id'], binding['operation_id'])
                self.assertEqual(restored['result']['fingerprint'], binding['fingerprint'])
                protected = {p: (self.f.root / p).read_bytes() for p in original}
                journal = await runtime.journal.load(binding['operation_id'])
                duplicate = await self._request(reverse_ws, 6, 'recovery/restore', **binding)
                self.assertTrue(duplicate['success'], duplicate)
                self.assertEqual((duplicate['result']['state'], duplicate['result']['reason']),
                                 ('refused', 'already_finished'))
                self.assertEqual(await runtime.journal.load(binding['operation_id']), journal)
                self.assertEqual({p: (self.f.root / p).read_bytes() for p in original}, protected)
                self.assertIs(journal.restore_attempted, True)
                # A foreign source edit cannot be promoted to restored success.
                path = self.f.root / 'mixed_scripts.yaml'
                original_script = path.read_bytes()
                try:
                    path.write_bytes(original_script + b'\n# foreign after rollback\n')
                    blocked = await self._request(reverse_ws, 7, 'recovery/inspect', **binding)
                    self.assertTrue(blocked['success'], blocked)
                    self.assertEqual(blocked['result']['state'], 'UNKNOWN')
                    self.assertEqual(await runtime.journal.load(binding['operation_id']), journal)
                finally:
                    path.write_bytes(original_script)
                # Fresh independent inspection proves original state after a lost ACK.
                inspected_restore = await self._request(reverse_ws, 8, 'recovery/inspect', **binding)
                self.assertTrue(inspected_restore['success'], inspected_restore)
                self.assertEqual((inspected_restore['result']['state'], inspected_restore['result']['reason']),
                                 ('rolled_back', 'restored_verified'))
                self.assertEqual(len(inspected_restore['result']['frozen_review']['changes']), len(journal.plan.steps) - 1)
                self.assertEqual(await runtime.journal.load(binding['operation_id']), journal)
                self.assertEqual({p: (self.f.root / p).read_bytes() for p in original}, protected)
            for path, raw in original.items():
                self.assertEqual((self.f.root / path).read_bytes(), raw)
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
            for value in (17, 19):
                await self.stimulus(value)
                await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
                await hass.async_block_till_done()
                self.assertEqual(events['automation'][-1], str(value))
                self.assertEqual(events['script'][-1], str(value))
            self.assertTrue(await history.verify(source_history))
            self.assertTrue(await history.verify(consumer_history))


    async def test_registered_stale_review_consumed_without_replacing_original_or_replay(self):
        runtime, old, new, uuids, original, payload = await self.selection()
        async with self._transport() as (ws, user):
            response = await self._request(ws, 1, 'prepare', **payload)
            self.assertTrue(response['success'], response)
            binding = {key: response['result'][key] for key in ('operation_id', 'fingerprint')}
            plan = runtime.reviews[binding['operation_id']].plan
            receipt = runtime.preparations.resolve_composition(plan.native_inputs)[0].receipt_json
            edited = original + b'\n# synthetic foreign edit after review\n'
            await self.f.hass.async_add_executor_job((self.f.root / 'template.yaml').write_bytes, edited)
            denied = await self._request(ws, 2, 'apply', **binding)
            self.assertFalse(denied['success'])
            self.assertIn(denied['error']['code'], ('apply_refused', 'apply_unavailable'))
            self.assertNotIn('result', denied)
            self.assertNotIn(binding['operation_id'], runtime.reviews)
            refusal = await runtime.journal.load(binding['operation_id'])
            self.assertEqual((refusal.status, refusal.reason), ('refused', 'prewrite_refused'))
            self.assertEqual(refusal.phases, ('pending',) * len(refusal.plan.steps))
            inspected = await self._request(ws, 4, 'recovery/inspect', **binding)
            self.assertTrue(inspected['success'], inspected)
            self.assertEqual((inspected['result']['state'], inspected['result']['reason']), ('refused', 'prewrite_refused'))
            duplicate = await self._request(ws, 5, 'apply', **binding)
            self.assertEqual(duplicate['error']['code'], 'apply_refused')
            self.assertEqual((self.f.root / 'template.yaml').read_bytes(), edited)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual(runtime.preparations.resolve_composition(plan.native_inputs)[0].receipt_json, receipt)
            self.assertNotIn('template.yaml', json.dumps(denied))
            self.assertNotIn(str(self.f.root), json.dumps(denied))

    async def _registered_unsupported_template(self, trigger):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, old, new, uuids, _, payload = await self.selection()
        if trigger:
            body = ('- unique_id: scope\n  triggers:\n  - trigger: state\n'
                    '    entity_id: ' + old + '\n  sensor:\n  - unique_id: value\n'
                    '    name: Runtime consumer\n    state: "{{ trigger.to_state.state }}"\n')
            code = 'unsupported_template_family'
        else:
            suffix = old.split('.', 1)[1]
            body = ('- unique_id: scope\n  sensor:\n  - unique_id: value\n'
                    '    name: Runtime consumer\n    state: "{{ (states(\'' + old + '\') | int(0)) + '
                    '(states(\'sensor.\' ~ \'' + suffix + '\') | int(0)) }}"\n')
            code = 'unsupported_template_reference'
        path = self.f.root / 'template.yaml'
        path.write_text(body)
        await self.f.hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        consumer = next(e for p in async_get_platforms(self.f.hass, 'template')
                        for e in p.entities.values() if e.unique_id == 'scope-value')
        self.producer._attr_native_value = 7
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()
        self.assertEqual(self.f.hass.states.get(consumer.entity_id).state, '7' if trigger else '14')
        async with self._transport() as (ws, _):
            response = await self._request(ws, 1, 'prepare', **payload)
            self.assertFalse(response['success'], response)
            self.assertEqual(response['error']['code'], code)
            self.assertFalse(runtime.reviews)
            self.assertFalse(await runtime.journal.active())
        self.assertEqual(path.read_text(), body)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.producer._attr_native_value = 9
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()
        self.assertEqual(self.f.hass.states.get(consumer.entity_id).state, '9' if trigger else '18')

    async def test_registered_dynamic_template_refuses_with_specific_reason(self):
        await self._registered_unsupported_template(False)

    async def test_registered_trigger_template_refuses_with_specific_reason(self):
        await self._registered_unsupported_template(True)
