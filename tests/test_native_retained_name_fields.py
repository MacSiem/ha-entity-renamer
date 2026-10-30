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
        self.assertEqual(review['entity_count'], 1)
        self.assertEqual(review['changes'], [{'entity_id': old, 'fields': {
            'entity_id': {'before': old, 'after': new},
            'name': {'before': before['name'], 'after': 'Reviewed synthetic source'}}}])
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

    async def test_id_name_name_only_and_device_in_one_retained_plan(self):
        from types import MappingProxyType
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        extra = type(self.producer)()
        extra._attr_unique_id = 'review-namespace-extra'
        extra._attr_name = 'Namespace extra'
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([extra])
        extra_id = extra.entity_id
        # A real unrelated native entry provides the device's HA ownership.
        # Its actual source is outside the ID mapping and must stay unchanged.
        entry = ConfigEntry(domain='threshold', title='Namespace device owner', data={},
            options={'name': 'Namespace helper', 'entity_id': extra_id,
                     'lower': 20, 'upper': None, 'hysteresis': 0},
            discovery_keys=MappingProxyType({}), source='user', version=1,
            minor_version=1, subentries_data=[], unique_id='namespace-device-owner')
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        device = self.f.device.async_get_or_create(config_entry_id=entry.entry_id,
            identifiers={('threshold', 'namespace-device')}, name='Namespace device')
        self.f.entity.async_update_entity(extra_id, device_id=device.id)
        await hass.async_block_till_done()
        await self._flush_documents()
        io = self.module('native_registry').NativeRegistryIO(hass)
        before_entities, before_devices = io.document('entity'), io.document('device')
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        before_entries = json.loads(_orjson_bytes_default_encoder(hass.config_entries._data_to_save()))
        names = {old: 'Reviewed source', extra_id: 'Reviewed extra'}
        devices = {device.id: 'Reviewed device'}
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names=names, device_names=devices,
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual((review['entity_count'], review['device_count']), (2, 1))
        self.assertEqual(review['changes'], [
            {'entity_id': old, 'fields': {
                'entity_id': {'before': old, 'after': new},
                'name': {'before': None, 'after': 'Reviewed source'}}},
            {'entity_id': extra_id, 'fields': {'name': {'before': None, 'after': 'Reviewed extra'}}},
            {'device_id': device.id, 'fields': {'name_by_user': {'before': None, 'after': 'Reviewed device'}}}])
        retained = runtime.reviews[review['operation_id']].plan
        self.assertEqual(tuple(step.adapter for step in retained.steps), ('registry',) * 3 + ('yaml',))
        names[old], names[extra_id] = 'Unreviewed source', 'Unreviewed extra'
        devices[device.id] = 'Unreviewed device'
        from test_native_template_mixed_yaml_server import native_diagnostics
        with native_diagnostics(self.module):
            try:
                result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
            finally:
                outcome = await runtime.journal.load(review['operation_id'])
                print('NAMESPACE_RESULT', None if outcome is None else
                      (outcome.status, outcome.reason, outcome.direction, outcome.phases))
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(self.f.entity.async_get(new).name, 'Reviewed source')
        self.assertEqual(self.f.entity.async_get(extra_id).name, 'Reviewed extra')
        self.assertEqual(self.f.device.async_get(device.id).name_by_user, 'Reviewed device')
        self.assertEqual(hass.states.get(new).attributes['friendly_name'], 'Reviewed source')
        self.assertEqual(hass.states.get(extra_id).attributes['friendly_name'], 'Reviewed extra')
        inverse = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(inverse['state'], 'rolled_back')
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        def normalized(document, family, owned):
            document = json.loads(json.dumps(document))
            for row in document[family]:
                if row['id'] in owned:
                    row.pop('modified_at', None)
            return document
        owned = {uuids[old], self.f.entity.async_get(extra_id).id}
        actual_entities = normalized(io.document('entity'), 'entities', owned)
        expected_entities = normalized(before_entities, 'entities', owned)
        if actual_entities != expected_entities:
            baseline, _ = runtime.preparations.resolve_composition(retained.native_inputs)
            baseline_entities = json.loads(next(row.manager_json for row in baseline.files
                                               if row.key == 'core.entity_registry'))
            def differences(before, after):
                old = {row['id']: row for row in before['entities']}
                new = {row['id']: row for row in after['entities']}
                return [(identity, key, old.get(identity, {}).get(key), new.get(identity, {}).get(key))
                        for identity in sorted(set(old) | set(new))
                        for key in sorted(set(old.get(identity, {})) | set(new.get(identity, {})))
                        if old.get(identity, {}).get(key) != new.get(identity, {}).get(key)]
            print('NAMESPACE_BEFORE_TO_ORIGINAL', differences(before_entities, baseline_entities))
            print('NAMESPACE_BEFORE_TO_FINAL', differences(expected_entities, actual_entities))
        self.assertEqual(actual_entities, expected_entities)
        self.assertEqual(normalized(io.document('device'), 'devices', {device.id}),
                         normalized(before_devices, 'devices', {device.id}))
        self.assertEqual(json.loads(_orjson_bytes_default_encoder(hass.config_entries._data_to_save())),
                         before_entries)
        self.assertEqual((await runtime.journal.load(review['operation_id'])).plan, retained)
        await self.stimulus(11)
