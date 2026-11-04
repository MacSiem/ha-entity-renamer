"""Registered HA WebSocket on installed backend for a numeric Template entry."""
import unittest
import test_native_queue_prepare_auth as queue
import test_native_template_forward as forward

@unittest.skipUnless(forward.fixtures.HAS_HA, 'actual pinned HA required')
class RegisteredEntryTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = queue.NativeQueuePrepareAuthTests.asyncSetUp
    _before_loader = queue.NativeQueuePrepareAuthTests._before_loader
    _before_start = queue.NativeQueuePrepareAuthTests._before_start
    _loaded_backend = queue.NativeQueuePrepareAuthTests._loaded_backend
    _flush_documents = queue.NativeQueuePrepareAuthTests._flush_documents
    _same_native_documents = forward.NativeTemplateForwardTests._same_native_documents
    _fixture = forward.NativeTemplateForwardTests._fixture
    emit = forward.NativeTemplateForwardTests.emit
    _connection = queue.NativeQueuePrepareAuthTests._connection
    _transport = queue.NativeQueuePrepareAuthTests._transport
    _request = queue.NativeQueuePrepareAuthTests._request
    module = queue.NativeQueuePrepareAuthTests.module

    async def test_registered_numeric_entry_round_trip(self):
        from homeassistant.helpers import issue_registry as ir
        from homeassistant.core import Context
        from homeassistant.helpers.event import async_track_state_change_event
        _, entry, mapping, identity, original, _ = await self._fixture()
        hass = self.f.hass
        if getattr(self, "with_empty_scene", False):
            from homeassistant.setup import async_setup_component
            self.assertTrue(await async_setup_component(hass, "scene", {"scene": []}))
            await hass.async_block_till_done()
            self.assertIn("scene", hass.config.components)
            self.assertEqual(hass.states.async_entity_ids("scene"), [])
        runtime = hass.data[self._loaded_backend().DATA_RUNTIME]
        # Stabilize the actual repairs Store before ORIGINAL; do not fake the
        # manager or touch any retained operation receipt.
        issues = ir.async_get(hass)
        ir.async_create_issue(hass, 'ha_entity_renamer', 'qa_entry_startup', is_fixable=False,
                              severity=ir.IssueSeverity.WARNING, translation_key='qa_entry_startup')
        ir.async_delete_issue(hass, 'ha_entity_renamer', 'qa_entry_startup')
        await issues._store.async_save(issues._data_to_save())
        self.assertTrue(__import__('pathlib').Path(issues._store.path).is_file())
        old, new = next(iter(mapping.items()))
        source_uuid = self.f.entity.async_get(old).id
        foreign_events = []
        unsub = async_track_state_change_event(hass, [identity[0]], foreign_events.append)
        self.addCleanup(unsub)
        async with self._transport() as (ws, user):
            prepared = await self._request(ws, 1, 'prepare', entities=[{'entity_id': old, 'new_entity_id': new}], devices=[])
            self.assertTrue(prepared['success'], prepared)
            review = prepared['result']
            self.assertEqual(review['dependencies']['config_entries'], [{'entity_id': identity[0], 'before': old, 'after': new}])
            binding = {key: review[key] for key in ('operation_id', 'fingerprint')}
            plan = runtime.reviews[binding['operation_id']].plan
            baseline, composition = runtime.preparations.resolve_composition(plan.native_inputs)
            original_digest = baseline.digest
            applied = await self._request(ws, 2, 'apply', **binding)
            self.assertTrue(applied['success'], applied)
            self.assertEqual(applied['result']['state'], 'applied')
            self.assertEqual(self.f.entity.async_get(new).id, source_uuid)
            context = Context()
            self.producer.async_set_context(context)
            await self.emit(9)
            self.assertEqual(hass.states.get(new).context.id, context.id)
            self.assertEqual(hass.states.get(identity[0]).context.id, context.id)
            self.assertEqual(hass.states.get(identity[0]).state, '18')
            self.assertTrue(await self.module('history_guard').NativeHistoryGuard(
                self.module('recorder_guard').NativeRecorderGuard(hass)).verify(composition.source_history))
            inspected = await self._request(ws, 3, 'recovery/inspect', **binding)
            self.assertEqual(inspected['result']['state'], 'applied')
            self.assertEqual(inspected['result']['frozen_review']['dependencies'], review['dependencies'])
            restored = await self._request(ws, 4, 'recovery/restore', **binding)
            self.assertEqual(restored['result']['state'], 'rolled_back')
            verified = await self._request(ws, 5, 'recovery/inspect', **binding)
            self.assertEqual(verified['result']['state'], 'rolled_back')
            duplicate = await self._request(ws, 6, 'recovery/restore', **binding)
            self.assertEqual(duplicate['result']['state'], 'refused')
        self.assertEqual(self.f.entity.async_get(old).id, source_uuid)
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual(self.f.entity.async_get(identity[0]).id, identity[1])
        self.assertEqual(self.module('helper_native').read_entry(hass, entry.entry_id), original)
        context = Context()
        self.producer.async_set_context(context)
        await self.emit(11)
        self.assertEqual(hass.states.get(old).context.id, context.id)
        self.assertEqual(hass.states.get(identity[0]).context.id, context.id)
        self.assertEqual(hass.states.get(identity[0]).state, '22')
        import asyncio
        async with asyncio.timeout(5):
            while not any(event.context.id == context.id for event in foreign_events):
                await asyncio.sleep(0.01)  # Minimum Core queues real foreign dispatch.
        self.assertTrue(any(event.context.id == context.id for event in foreign_events))
        fresh = self.module('prepared').native_prepared_store(hass)
        await fresh.initialize()
        self.assertEqual(fresh.resolve_composition(plan.native_inputs)[0].digest, original_digest)
        history = self.module('history_guard').NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass))
        self.assertTrue(await history.verify(composition.source_history))
        self.assertTrue(await history.verify(composition.consumer_history))


class RegisteredEntryEmptySceneTests(RegisteredEntryTests):
    with_empty_scene = True

    async def test_scene_added_after_review_refuses_apply(self):
        from homeassistant.helpers import issue_registry as ir
        from homeassistant.setup import async_setup_component
        _, entry, mapping, identity, original, _ = await self._fixture()
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'scene', {'scene': []}))
        await hass.async_block_till_done()
        issues = ir.async_get(hass)
        ir.async_create_issue(hass, 'ha_entity_renamer', 'qa_entry_startup', is_fixable=False,
                              severity=ir.IssueSeverity.WARNING, translation_key='qa_entry_startup')
        ir.async_delete_issue(hass, 'ha_entity_renamer', 'qa_entry_startup')
        await issues._store.async_save(issues._data_to_save())
        old, new = next(iter(mapping.items()))
        source_uuid = self.f.entity.async_get(old).id
        async with self._transport() as (ws, user):
            prepared = await self._request(ws, 1, 'prepare', entities=[{'entity_id': old, 'new_entity_id': new}], devices=[])
            self.assertTrue(prepared['success'], prepared)
            binding = {key: prepared['result'][key] for key in ('operation_id', 'fingerprint')}
            await hass.services.async_call('scene', 'create', {'scene_id': 'qa_foreign', 'snapshot_entities': [old]}, blocking=True)
            await hass.async_block_till_done()
            self.assertIn('scene.qa_foreign', hass.states.async_entity_ids('scene'))
            result = await self._request(ws, 2, 'apply', **binding)
            self.assertFalse(result['success'], result)
        self.assertEqual(self.f.entity.async_get(old).id, source_uuid)
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual(self.f.entity.async_get(identity[0]).id, identity[1])
        self.assertEqual(self.module('helper_native').read_entry(hass, entry.entry_id), original)
