"""Real selected entity name + ID in one server-retained migration review."""
import unittest
import json

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeRetainedNameFieldsTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    data = server.NativeTemplateYAMLServerTests.data
    stimulus = server.NativeTemplateYAMLServerTests.stimulus

    async def test_selected_name_and_id_retained_fresh_apply_inverse(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        before = json.loads(_orjson_bytes_default_encoder(self.f.entity.async_get(old).as_storage_fragment))
        original_friendly = hass.states.get(old).attributes['friendly_name']
        names = {old: 'Reviewed synthetic source'}
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, entity_names=names,
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        retained = runtime.reviews[review['operation_id']].plan
        # Editing the original request after review cannot alter the server Plan.
        names[old] = 'Unreviewed client edit'
        await self.stimulus(7)
        result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertIsNone(self.f.entity.async_get(old))
        row = self.f.entity.async_get(new)
        self.assertEqual((row.id, row.name), (uuids[old], 'Reviewed synthetic source'))
        self.assertEqual(hass.states.get(new).attributes['friendly_name'], 'Reviewed synthetic source')
        self.assertEqual((await runtime.journal.load(review['operation_id'])).plan, retained)
        inverse = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(inverse['state'], 'rolled_back')
        restored = self.f.entity.async_get(old)
        self.assertEqual((restored.id, restored.name), (uuids[old], before['name']))
        self.assertIsNone(self.f.entity.async_get(new))
        after = json.loads(_orjson_bytes_default_encoder(restored.as_storage_fragment))
        self.assertEqual({k:v for k,v in after.items() if k != 'modified_at'},
                         {k:v for k,v in before.items() if k != 'modified_at'})
        self.assertEqual(hass.states.get(old).attributes['friendly_name'], original_friendly)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        await self.stimulus(11)

    async def test_source_name_edit_after_review_refuses_without_overwriting(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        original_name = self.f.entity.async_get(old).name
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names={old: 'Reviewed synthetic source'}, actor_id='synthetic-admin',
            guard=lambda: None, refresh=refresh)
        self.f.entity.async_update_entity(old, name='Synthetic foreign edit')
        await hass.async_block_till_done()
        await self._flush_documents()
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        row = self.f.entity.async_get(old)
        self.assertEqual((row.id, row.name), (uuids[old], 'Synthetic foreign edit'))
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertIsNone(await runtime.journal.load(review['operation_id']))
        with self.assertRaises(self.module('transaction').Refused):
            await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        # Restore only this fixture's explicit foreign-edit fault through HA.
        self.f.entity.async_update_entity(old, name=original_name)
        await hass.async_block_till_done()
        await self._flush_documents()
