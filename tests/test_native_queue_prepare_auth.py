"""Ordinary selection through installed backend and native HA authorization.

One case per fresh native probe. No network-login or full UI claim.
"""
import unittest
import asyncio
from contextlib import asynccontextmanager
import json

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
                yield ws, user

    async def _request(self, ws, ident, action, **payload):
        await ws.send_json({'id': ident, 'type': 'ha_entity_renamer/' + action, **payload})
        result = await ws.receive_json(timeout=180)
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
            restore = await self._request(ws, 8, 'recovery/restore', **binding)
            self.assertEqual(restore['error']['code'], 'feature_unavailable')
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

    async def test_registered_mixed_apply_with_empty_scene_and_fresh_inverse(self):
        await self._registered_mixed_apply(with_scene=True)

    async def _registered_mixed_apply(self, *, with_scene=False):
        import test_native_template_mixed_yaml_server as mixed
        from homeassistant.core import Context
        runtime, mapping, uuids, dependents, original = await (
            mixed.NativeTemplateMixedYAMLServerTests.data(self))
        hass = self.f.hass
        if with_scene:
            from homeassistant.setup import async_setup_component
            self.assertTrue(await async_setup_component(hass, 'scene', {}))
            await hass.async_block_till_done()
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
            with mixed.native_diagnostics(self.module):
                result = await self._request(ws, 2, 'apply', **binding)
            self.assertTrue(result['success'], result)
            self.assertEqual(result['result']['state'], 'applied')
            self.assertEqual(events, {'automation': [], 'script': []})
            record = await runtime.journal.load(binding['operation_id'])
            self.assertEqual(record.plan, plan)
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
            # Fresh controller/session after the request and its source fence closed.
            async def refresh():
                return None
            restored = await runtime.recovery_restore(hass, binding['operation_id'],
                binding['fingerprint'], guard=lambda: None, refresh=refresh)
            self.assertEqual(restored['state'], 'rolled_back', restored)
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
            self.assertIsNone(await runtime.journal.load(binding['operation_id']))
            duplicate = await self._request(ws, 3, 'apply', **binding)
            self.assertEqual(duplicate['error']['code'], 'apply_refused')
            self.assertEqual((self.f.root / 'template.yaml').read_bytes(), edited)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual(runtime.preparations.resolve_composition(plan.native_inputs)[0].receipt_json, receipt)
            self.assertNotIn('template.yaml', json.dumps(denied))
            self.assertNotIn(str(self.f.root), json.dumps(denied))
