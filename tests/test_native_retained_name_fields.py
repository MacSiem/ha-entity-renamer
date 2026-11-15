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

    async def restored_source(self, *, registry_only=True, multiple=False, live_dependent=False):
        from types import MappingProxyType
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True, multiple=multiple)
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        if registry_only:
            path = self.f.root / 'template.yaml'
            text = path.read_text()
            for source in mapping:
                if not live_dependent or source == old:
                    text = text.replace(source, 'sensor.unrelated_fixture')
            path.write_text(text)
            await hass.services.async_call('template', 'reload', {}, blocking=True)
            await hass.async_block_till_done()
            original = path.read_bytes()
            if live_dependent:
                live = next(source for source in mapping if source != old)
                config = self.f.root / 'configuration.yaml'
                config.write_text(config.read_text().replace('variables: {retained: unrelated}',
                    'variables: {retained: "{{ states(\'' + live + '\') }}"}', 1))
                await hass.services.async_call('automation', 'reload', {}, blocking=True)
                await hass.async_block_till_done()
                await self._flush_documents()
        entry = ConfigEntry(domain='threshold', title='Restored fixture owner', data={},
            options={'name': 'Restored fixture helper', 'entity_id': 'sensor.unrelated_fixture',
                     'lower': 20, 'upper': None, 'hysteresis': 0},
            discovery_keys=MappingProxyType({}), source='user', version=1,
            minor_version=1, subentries_data=[], unique_id='restored-fixture-owner')
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        device = self.f.device.async_get_or_create(config_entry_id=entry.entry_id,
            identifiers={('threshold', 'restored-fixture-device')}, name='Restored fixture device')
        await self.producer.platform.async_remove_entity(old)
        row = self.f.entity.async_update_entity(old, config_entry_id=entry.entry_id, device_id=device.id)
        row.write_unavailable_state(hass)
        await hass.async_block_till_done()
        await self._flush_documents()
        self.assertEqual(hass.states.get(old).attributes.get('restored'), True)
        return runtime, mapping, uuids, original, device

    async def test_loaded_restored_source_id_name_apply_inverse(self):
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names={old: 'Restored reviewed source'}, actor_id='synthetic-admin',
            guard=lambda: None, refresh=refresh)
        plan = runtime.reviews[review['operation_id']].plan
        baseline, _ = runtime.preparations.resolve_composition(plan.native_inputs)
        self.assertEqual(baseline.receipt.changed_domains, ())
        self.assertEqual(tuple(step.adapter for step in plan.steps), ('registry',))
        events = []
        from homeassistant.core import callback
        @callback
        def observed(event):
            if event.data['entity_id'] in (old, new):
                events.append((event.data['entity_id'], event.data['old_state'], event.data['new_state']))
        self.addCleanup(hass.bus.async_listen('state_changed', observed))
        try:
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        except Exception:
            record = await runtime.journal.load(review['operation_id'])
            print('RESTORED_FAILURE', record.status, record.reason, record.phases)
            raise
        self.assertEqual(result['state'], 'applied')
        row = self.f.entity.async_get(new)
        self.assertEqual((row.id, row.device_id, row.name),
                         (uuids[old], device.id, 'Restored reviewed source'))
        self.assertIsNone(hass.states.get(old))
        self.assertEqual(hass.states.get(new).state, 'unavailable')
        self.assertTrue(hass.states.get(new).attributes['restored'])
        self.assertEqual(hass.states.get(new).attributes['friendly_name'],
                         'Restored fixture device Restored reviewed source')
        self.assertTrue(any(key == old and before is not None and after is None
                            for key, before, after in events))
        self.assertTrue(any(key == new and before is None and after is not None
                            and after.state == 'unavailable' and after.attributes.get('restored') is True
                            for key, before, after in events))
        reverse = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(reverse['state'], 'rolled_back')
        row = self.f.entity.async_get(old)
        self.assertEqual((row.id, row.device_id, row.name), (uuids[old], device.id, None))
        self.assertIsNone(hass.states.get(new))
        self.assertEqual(hass.states.get(old).state, 'unavailable')
        self.assertTrue(hass.states.get(old).attributes['restored'])
        self.assertEqual(hass.states.get(old).attributes['friendly_name'],
                         'Restored fixture device Template runtime producer')
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_loaded_restored_and_live_multiple_apply_inverse(self):
        runtime, mapping, uuids, original, device = await self.restored_source(multiple=True, live_dependent=True)
        hass = self.f.hass
        restored, restored_new = next(iter(mapping.items()))
        live = next(source for source in mapping if source != restored)
        live_new = mapping[live]
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        second = type(self.producer)()
        second._attr_unique_id = 'restored-second-producer'
        second._attr_name = 'Z second restored'
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([second])
        second_id = second.entity_id
        await second.platform.async_remove_entity(second_id)
        entry_id = self.f.entity.async_get(restored).config_entry_id
        second_device = self.f.device.async_get_or_create(config_entry_id=entry_id,
            identifiers={('threshold', 'restored-second-device')}, name='Second fixture device')
        row = self.f.entity.async_update_entity(second_id, config_entry_id=entry_id, device_id=second_device.id)
        row.write_unavailable_state(hass)
        mapping[second_id] = 'sensor.accepted_second_restored'
        uuids[second_id] = row.id
        self.assertEqual(sorted(mapping), [live, restored, second_id])
        self.assertIsNotNone(hass.states.get(live))
        from homeassistant.helpers.entity_registry import RegistryEntryDisabler
        disabled = self.f.entity.async_get_or_create('sensor', 'threshold', 'restored-disabled-peer',
            config_entry=self.f.hass.config_entries.async_get_entry(self.f.entity.async_get(restored).config_entry_id),
            device_id=device.id, disabled_by=RegistryEntryDisabler.USER)
        await hass.async_block_till_done()
        await self._flush_documents()
        self.assertIsNone(hass.states.get(disabled.entity_id))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names={restored: 'Restored reviewed source', live: 'Live reviewed source',
                          second_id: 'Second reviewed source'},
            device_names={device.id: 'Reviewed fixture device', second_device.id: 'Second reviewed device'}, actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        plan = runtime.reviews[review['operation_id']].plan
        self.assertEqual(tuple(step.adapter for step in plan.steps), ('registry', 'registry', 'registry', 'registry', 'registry', 'yaml'))
        from homeassistant.core import callback
        events = []
        @callback
        def state_changed(event):
            if event.data['entity_id'] in (restored, restored_new):
                events.append((event.data['entity_id'], event.data['old_state'], event.data['new_state']))
        self.addCleanup(hass.bus.async_listen('state_changed', state_changed))
        result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual((self.f.entity.async_get(restored_new).id,
                          self.f.entity.async_get(restored_new).device_id), (uuids[restored], device.id))
        self.assertTrue(hass.states.get(restored_new).attributes['restored'])
        self.assertEqual(hass.states.get(restored_new).attributes['friendly_name'],
                         'Reviewed fixture device Restored reviewed source')
        self.assertIsNone(hass.states.get(disabled.entity_id))
        self.assertEqual(hass.states.get(mapping[second_id]).attributes['friendly_name'],
                         'Second reviewed device Second reviewed source')
        self.assertTrue(any(key == restored_new and before is not None and after is not None
                            and after.attributes.get('friendly_name') ==
                                'Reviewed fixture device Restored reviewed source'
                            for key, before, after in events))
        self.assertEqual(self.f.entity.async_get(live_new).id, uuids[live])
        result = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'rolled_back')
        for source, target in mapping.items():
            self.assertEqual(self.f.entity.async_get(source).id, uuids[source])
            self.assertIsNone(self.f.entity.async_get(target))
        self.assertTrue(hass.states.get(restored).attributes['restored'])
        self.assertEqual(hass.states.get(restored).attributes['friendly_name'],
                         'Restored fixture device Template runtime producer')
        self.assertTrue(any(key == restored_new and before is not None and after is not None
                            and after.attributes.get('friendly_name') ==
                                'Restored fixture device Restored reviewed source'
                            for key, before, after in events))
        self.assertIsNone(hass.states.get(disabled.entity_id))
        self.assertEqual(hass.states.get(second_id).attributes['friendly_name'],
                         'Second fixture device Z second restored')
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_restored_apply_inverse_with_unconfigured_template_peer(self):
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        ghost = self.f.entity.async_get_or_create('sensor', 'template', 'old-unconfigured-peer')
        ghost.write_unavailable_state(hass)
        await hass.async_block_till_done()
        await self._flush_documents()
        before = hass.states.get(ghost.entity_id).as_dict_json
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names={old: 'Restored reviewed source'}, actor_id='synthetic-admin',
            guard=lambda: None, refresh=refresh)
        result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(hass.states.get(ghost.entity_id).as_dict_json, before)
        result = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'rolled_back')
        self.assertEqual(hass.states.get(ghost.entity_id).as_dict_json, before)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))

    async def test_loaded_restored_foreign_attributes_refused_before_review(self):
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        state = hass.states.get(old)
        hass.states.async_set(old, state.state, dict(state.attributes, foreign='preserve'))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaisesRegex((ValueError, self.module('transaction').Refused),
                                    'name_restored_native_attributes_changed'):
            await runtime.native_yaml_prepare(hass, mapping,
                entity_names={old: 'Restored reviewed source'},
                actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual(hass.states.get(old).attributes['foreign'], 'preserve')
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual(self.f.device.async_get(device.id).name_by_user, None)

    async def test_loaded_restored_dependent_template_refuses_before_effect(self):
        runtime, mapping, uuids, original, device = await self.restored_source(registry_only=False)
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaisesRegex((ValueError, self.module('transaction').Refused),
                                    'restored_source_dependent_lifecycle_unsupported'):
            await runtime.native_yaml_prepare(hass, mapping,
                entity_names={old: 'Restored reviewed source'}, actor_id='synthetic-admin',
                guard=lambda: None, refresh=refresh)
        self.assertEqual((self.f.entity.async_get(old).id, self.f.entity.async_get(old).device_id),
                         (uuids[old], device.id))
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_loaded_restored_source_object_appearance_refuses(self):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, original, device = await self.restored_source()
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names={old: 'Restored reviewed source'}, actor_id='synthetic-admin',
            guard=lambda: None, refresh=refresh)
        appeared = type(self.producer)()
        appeared._attr_unique_id = self.producer.unique_id
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([appeared])
        await hass.async_block_till_done()
        await self._flush_documents()
        before_apply = self.f.entity.async_get(old)
        before_identity = (before_apply.id, before_apply.device_id)
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual((self.f.entity.async_get(old).id, self.f.entity.async_get(old).device_id),
                         before_identity)
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)

    async def test_selected_name_and_id_retained_fresh_apply_inverse(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True)
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
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True)
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
        record = await runtime.journal.load(review['operation_id'])
        self.assertEqual((record.status, record.reason, record.phases),
                         ('refused', 'prewrite_refused', ('pending', 'pending')))
        with self.assertRaises(self.module('transaction').Refused):
            await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        # Restore only this fixture's explicit foreign-edit fault through HA.
        self.f.entity.async_update_entity(old, name=original_name)
        await hass.async_block_till_done()
        await self._flush_documents()

    async def test_dependent_automation_name_and_id_uses_controlled_reload(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor', source_consumers=True)
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        path = self.f.root / 'configuration.yaml'
        text = path.read_text().replace('variables: {retained: unrelated}',
            "variables: {retained: \"{{ states('" + old + "') }}\"}", 1)
        path.write_text(text)
        await hass.services.async_call('automation', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        await self._flush_documents()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping,
            entity_names={old: 'Reviewed source'}, actor_id='synthetic-admin',
            guard=lambda: None, refresh=refresh)
        plan = runtime.reviews[review['operation_id']].plan
        baseline, composition = runtime.preparations.resolve_composition(plan.native_inputs)
        self.assertIn('automation', baseline.receipt.changed_domains)
        self.assertEqual(type(composition).__name__, 'ControlledReloadComposition')
        result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
            actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual((self.f.entity.async_get(new).id, self.f.entity.async_get(new).name),
                         (uuids[old], 'Reviewed source'))
        self.assertIsNone(self.f.entity.async_get(old))
        self.assertIn(new, path.read_text())
        self.assertNotIn(old, path.read_text())
        self.assertEqual(hass.states.get(new).attributes['friendly_name'], 'Reviewed source')

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
        from homeassistant.const import __version__
        expected_extra = ('Reviewed device Reviewed extra'
                          if __version__ in ('2026.11.0', '2026.11.1') else 'Reviewed extra')
        self.assertEqual(hass.states.get(extra_id).attributes['friendly_name'], expected_extra)
        applied = await runtime.journal.load(review['operation_id'])
        inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual((inspected['state'], inspected['eligible']), ('applied', True))
        self.assertEqual(inspected['change_summary']['registry_changes'], [
            {'entity_id': new, 'applied': True, 'fields': {
                'entity_id': {'current': new, 'original': old},
                'name': {'current': 'Reviewed source', 'original': None}}},
            {'entity_id': extra_id, 'applied': True, 'fields': {
                'name': {'current': 'Reviewed extra', 'original': None}}},
            {'device_id': device.id, 'applied': True, 'fields': {
                'name_by_user': {'current': 'Reviewed device', 'original': None}}}])
        self.assertEqual(await runtime.journal.load(review['operation_id']), applied)
        inverse = await runtime.recovery_restore(hass, review['operation_id'], review['fingerprint'],
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
            # Native rename pops/reinserts the same UUID. Keep every complete
            # row and compare populations independently of dictionary order.
            document[family].sort(key=lambda row: row['id'])
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
