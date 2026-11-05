"""Actual HA mixed fixture: independent census, real stimuli and blockers."""
import json
import os
from pathlib import Path
import unittest
import tempfile
import shutil

import test_native_template_mixed_yaml_server as mixed
from mixed_fixture_census import collect_native, consumer_evidence, census_coverage, fresh_inverse_after_apply
from batch_readback import Edit, capture_tree, compare, discovery_coverage, native_location_snapshot, native_empty_auth_snapshot


class NativeMixedFixtureCensusTest(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_start = mixed.NativeTemplateMixedYAMLServerTests._before_start
    _flush_documents = mixed.NativeTemplateMixedYAMLServerTests._flush_documents
    prepare = mixed.NativeTemplateMixedYAMLServerTests.prepare
    emit = mixed.NativeTemplateMixedYAMLServerTests.emit
    _runtime = mixed.NativeTemplateMixedYAMLServerTests._runtime
    module = mixed.NativeTemplateMixedYAMLServerTests.module
    stimulus = mixed.NativeTemplateMixedYAMLServerTests.stimulus
    data = mixed.NativeTemplateMixedYAMLServerTests.data

    def assert_fixture_samples(self, census):
        self.assertEqual([sample['source'] for sample in census['samples']], [5, 13])
        self.assertEqual([sample['automation'] for sample in census['samples']], [['5'], ['13']])
        self.assertEqual([sample['script'] for sample in census['samples']], [['5'], ['13']])
        self.assertEqual([sample['template'] for sample in census['samples']], [
            {'sensor:scope-value': '10', 'binary_sensor:scope-value': 'on'},
            {'sensor:scope-value': '26', 'binary_sensor:scope-value': 'off'}])
        for domain in ('sensor', 'binary_sensor'):
            entity_id, uuid = self.identities[(domain, 'scope-value')]
            self.assertEqual(census['bindings']['template'][domain + ':scope-value']['entity_id'], entity_id)
            self.assertEqual(self.f.entity.async_get(entity_id).id, uuid)

    async def test_actual_private_auth_baseline_and_foreign_edit(self):
        _, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        manager = hass.auth._store
        await manager._store.async_save(manager._data_to_save())
        original = (root / '.storage/auth').read_bytes()
        baseline = native_empty_auth_snapshot(hass, root)
        safe_document = json.loads(original)
        safe_data = safe_document['data']
        print('PRIVATE_AUTH_SAFE_SHAPE', json.dumps({
            'loaded': manager._loaded, 'path_matches': Path(manager._store.path) == root / '.storage/auth',
            'version_matches': safe_document.get('version') == 1,
            'key_matches': safe_document.get('key') == 'auth',
            'data_keys_expected': set(safe_data) == {'users', 'groups', 'credentials', 'refresh_tokens'},
            'counts': {key: len(safe_data.get(key, [])) for key in ('users', 'groups', 'credentials', 'refresh_tokens')},
            'group_keys_expected': all(set(row) == {'id', 'name'} for row in safe_data['groups']),
            'system_group_ids': {row.get('id') for row in safe_data['groups']} == {'system-admin', 'system-users', 'system-read-only'},
            'memory_matches': safe_data == manager._data_to_save(),
            'http_fixture_links_match': (len(safe_data['users']) == len(safe_data['refresh_tokens']) == 1
                and safe_data['users'][0].get('name') == 'Home Assistant Content'
                and safe_data['users'][0].get('system_generated') is True
                and safe_data['users'][0].get('group_ids') == ['system-read-only']
                and safe_data['refresh_tokens'][0].get('user_id') == safe_data['users'][0].get('id')
                and hass.data.get('http.auth') == safe_data['refresh_tokens'][0].get('id')),
        }, sort_keys=True), flush=True)
        self.assertEqual(baseline['status'], 'COMPLETE')
        document = json.loads(original)
        document['data']['groups'][0]['name'] = next(iter(mapping))
        changed = json.dumps(document).encode()
        path = root / '.storage/auth'
        try:
            path.write_bytes(changed)
            self.assertEqual(native_empty_auth_snapshot(hass, root)['status'], 'UNKNOWN:private_auth_unverified')
        finally:
            if path.read_bytes() != changed:
                raise RuntimeError('foreign authority changed; cleanup refused')
            path.write_bytes(original)
        self.assertEqual(native_empty_auth_snapshot(hass, root), baseline)
        print('PRIVATE_AUTH_NATIVE_BASELINE_AND_FOREIGN_EDIT_OK', flush=True)

    async def test_census_bound_to_ordinary_forward_and_fresh_inverse(self):
        runtime, mapping, uuids, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        from homeassistant.helpers import area_registry, floor_registry, label_registry
        managers = []
        for module, member in ((floor_registry, 'floors'), (label_registry, 'labels'), (area_registry, 'areas')):
            manager = hass.data.get(module.DATA_REGISTRY)
            if manager is None:
                await module.async_load(hass)
                manager = hass.data[module.DATA_REGISTRY]
            elif not hasattr(manager, member):
                await manager.async_load()
            managers.append(manager)
        floors, labels, areas = managers
        floor = floors.async_create('Synthetic census floor', level=2)
        label = labels.async_create('Synthetic census label', color='blue')
        area = areas.async_create('Synthetic census room', floor_id=floor.floor_id, labels={label.label_id})
        self.f.entity.async_update_entity(old, area_id=area.id, labels={label.label_id})
        for manager in managers:
            await manager._store.async_save(manager._data_to_save())
        # Materialize the already-loaded, empty native authority before the
        # original snapshot. Never rewrite auth on later observations.
        auth_manager = hass.auth._store
        await auth_manager._store.async_save(auth_manager._data_to_save())
        self.assertEqual(native_empty_auth_snapshot(hass, root)['status'], 'COMPLETE')
        snapshots = tempfile.TemporaryDirectory(dir=self.scratch_parent)
        self.addCleanup(snapshots.cleanup)

        async def capture(name, current_mapping):
            census = await collect_native(hass, root, self.producer, current_mapping)
            self.assert_fixture_samples(census)
            await self._flush_documents()
            graph = self.module('yaml_plan').plan_yaml(str(root), current_mapping)
            row = self.f.entity.async_get(old) or self.f.entity.async_get(new)
            proposal = {'entities': [{'entity_id': old, 'proposed_entity_id': new,
                                      'device_id': row.device_id}], 'devices': [], 'statistics': []}
            discovery = await self.module('discovery').async_discover(hass, proposal, guard=lambda: None)
            families, external = discovery_coverage(discovery)
            families, external_consumers = census_coverage(census, families)
            destination = Path(snapshots.name) / name
            info = capture_tree(root, destination, inventory_files=[(row.path, row.sha256) for row in graph.files],
                families=families, external_consumers=external_consumers,
                external_inventory_complete=census['external']['empty_in_bounded_fixture'],
                recorder_databases=('fixture.db',), native_locations=native_location_snapshot(hass, root),
                native_auth=native_empty_auth_snapshot(hass, root))
            print('CENSUS_BOUND_DISCOVERY_EXTERNAL', external, flush=True)
            if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
                directory = Path(evidence)
                shutil.copytree(destination, directory / name)
                (directory / (name + '-census.json')).write_text(json.dumps(census, sort_keys=True, indent=2))
                (directory / (name + '-inventory.json')).write_text(json.dumps(info, sort_keys=True, indent=2))
            return census, destination, info

        before_census, before, before_info = await capture('census-before', mapping)
        self.assertTrue(before_census['complete'], before_census['unverified'])
        edits = [Edit(path, old.encode(), new.encode(), (before / path).read_bytes().count(old.encode()))
                 for path in ('template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')]
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='census-bound', guard=lambda: None, refresh=refresh)
        applied = None
        inspected = None
        try:
            applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                                                       actor_id='census-bound', guard=lambda: None, refresh=refresh)
            self.assertEqual(applied['state'], 'applied')
            after_census, after, after_info = await capture('census-after', {new: old})
            self.assertTrue(after_census['complete'], after_census['unverified'])
            inventory, checks, bindings = consumer_evidence(before_census, after_census)
            forward = compare(before, after, old=old, new=new, uuid=uuids[old], edits=edits,
                              captures=(before_info, after_info), consumer_inventory=inventory,
                              consumer_inventory_complete=True, consumer_checks=checks, source_consumers=bindings)
            print('CENSUS_BOUND_FORWARD', json.dumps(forward, sort_keys=True), flush=True)
            self.assertEqual(forward['findings'], [])
            self.assertTrue(forward['complete'], forward['unverified'])
        finally:
            # Inspect even if apply raised or returned an unexpected/UNKNOWN state.
            # A fresh eligible inspection, never a guessed state, gates inverse.
            inspected, inverse = await fresh_inverse_after_apply(runtime, hass, review['operation_id'],
                                                                 review['fingerprint'], applied, refresh)
            print('CENSUS_BOUND_FRESH_INSPECT', json.dumps(inspected, sort_keys=True), flush=True)
        self.assertTrue(inspected['eligible'])
        restored_census, restored, restored_info = await capture('census-restored', mapping)
        self.assertTrue(restored_census['complete'], restored_census['unverified'])
        inventory, checks, bindings = consumer_evidence(before_census, restored_census)
        reverse = compare(before, restored, old=old, new=new, uuid=uuids[old], edits=edits,
                          reverse=True, captures=(before_info, restored_info), consumer_inventory=inventory,
                          consumer_inventory_complete=True, consumer_checks=checks, source_consumers=bindings)
        print('CENSUS_BOUND_RESTORE', json.dumps(reverse, sort_keys=True), flush=True)
        self.assertEqual(reverse['findings'], [])
        self.assertTrue(reverse['complete'], reverse['unverified'])

    async def test_actual_empty_external_and_inactive_blueprints_are_derived(self):
        _, mapping, _, _, _ = await self.data()
        result = await collect_native(self.f.hass, self.f.root, self.producer, mapping)
        print('MIXED_CENSUS_EMPTY_EXTERNAL', json.dumps(result, sort_keys=True), flush=True)
        network = result['external']['network']
        self.assertEqual(network['interfaces_up'], ['lo'])
        self.assertFalse(network['unreadable'])
        self.assertEqual(network['nonloopback_sockets'], 0)
        self.assertEqual(network['loopback_connections'], 0)
        self.assertEqual(network['unix_sockets'], 0)
        self.assertEqual(network['foreign_processes'], [])
        self.assertEqual(result['external']['outside_consumers'], [])
        self.assertTrue(result['external']['empty_in_bounded_fixture'])
        self.assertTrue(result['inactive_blueprints'])
        self.assertFalse(any(reason == 'active_blueprint_unverified' for _, reason in result['unverified']))
        self.assert_fixture_samples(result)
        if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
            directory = Path(evidence)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'empty-external-census.json').write_text(json.dumps(result, sort_keys=True, indent=2))

    async def test_actual_outside_source_and_integration_configuration_are_not_empty(self):
        from types import MappingProxyType
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.setup import async_setup_component
        _, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        source = next(iter(mapping))
        (root / 'outside_reference.yaml').write_text('entity_id: ' + source + '\n')
        (root / 'outside_escaped.yaml').write_text('entity_id: "sensor.\\u0074emplate_runtime_producer"\n')
        with (root / 'configuration.yaml').open('a') as stream:
            stream.write('\ninput_boolean:\n  r8_census_foreign:\n    name: Census foreign\n')
        self.assertTrue(await async_setup_component(hass, 'input_boolean', await async_hass_config_yaml(hass)))
        await hass.async_block_till_done()
        self.assertIsNotNone(hass.states.get('input_boolean.r8_census_foreign'))
        entry = ConfigEntry(version=1, minor_version=1, domain='input_boolean', title='Outside synthetic',
            source='user', unique_id='census-outside', data={'entity_id': source}, options={},
            discovery_keys=MappingProxyType({}), subentries_data=[])
        hass.config_entries._entries[entry.entry_id] = entry
        await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
        result = await collect_native(hass, root, self.producer, mapping)
        print('MIXED_CENSUS_OUTSIDE', json.dumps(result, sort_keys=True), flush=True)
        self.assertFalse(result['complete'])
        self.assertFalse(result['external']['empty_in_bounded_fixture'])
        self.assertIn(('outside:outside_reference.yaml', 'reference_outside_active_graph'), result['unverified'])
        self.assertIn(('outside_escaped.yaml', 'dynamic_outside_source_unverified'), result['unverified'])
        self.assertIn(('integration:input_boolean', 'unclassified_loaded_integration'), result['unverified'])
        self.assertIn(('config:input_boolean', 'unclassified_integration_root'), result['unverified'])
        self.assertIn(('runtime:input_boolean.r8_census_foreign', 'unclassified_loaded_entity'), result['unverified'])
        self.assertIn(('config_entry:' + entry.entry_id, 'configured_reference_requires_integration_adapter'),
                      result['unverified'])
        if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
            directory = Path(evidence)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'outside-census.json').write_text(json.dumps(result, sort_keys=True, indent=2))

    async def _fixture_with_dashboard(self, *, custom=False):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        from homeassistant.setup import async_setup_component
        runtime, mapping, uuids, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        source = next(iter(mapping))
        boards = root / 'boards'
        boards.mkdir()
        (boards / 'r8-map.yaml').write_text(
            'views:\n- title: Map\n  cards:\n  - type: entity\n    entity: ' + source + '\n')
        with (root / 'configuration.yaml').open('a') as stream:
            stream.write('\nlovelace:\n  dashboards:\n    r8-map:\n      mode: yaml\n'
                         '      title: Synthetic map\n      filename: boards/r8-map.yaml\n')
        config = await async_hass_config_yaml(hass)
        self.assertTrue(await async_setup_component(hass, 'lovelace', config))
        self.assertIn('lovelace', hass.config.components)
        native = hass.data[LOVELACE_DATA]
        dashboard = native.dashboards[None]
        card = {'type': 'custom:r8-unknown', 'entity': source} if custom else {
            'type': 'map', 'entities': [source]}
        await dashboard.async_save({'views': [{'title': 'Live map', 'cards': [card]}]})
        await native.resources.async_get_info()
        self.producer._attr_extra_state_attributes = {'latitude': 52.0, 'longitude': 21.0}
        self.producer.async_write_ha_state()
        await hass.async_block_till_done()
        await self._flush_documents()
        return runtime, mapping, uuids, dashboard

    async def test_actual_loaded_census_includes_dashboard_map_and_consumers(self):
        runtime, mapping, _, dashboard = await self._fixture_with_dashboard()
        hass, root = self.f.hass, self.f.root
        original = self.producer.native_value
        result = await collect_native(hass, root, self.producer, mapping)
        print('MIXED_CENSUS_ACTUAL', json.dumps(result, sort_keys=True), flush=True)
        self.assertEqual(self.producer.native_value, original)
        self.assertIn('boards/r8-map.yaml', result['graph'])
        self.assertEqual({row['route'] for row in result['dashboards']}, {'<default>', 'r8-map'})
        default = next(row for row in result['dashboards'] if row['route'] == '<default>')
        self.assertEqual(default['status'], 'COMPLETE')
        self.assertEqual(default['cards'][0]['type'], 'map')
        self.assertEqual(default['cards'][0]['state_readback'][next(iter(mapping))]['latitude'], 52.0)
        self.assertIn(('dashboard:<default>', 'active_card_render_not_observed:map'), result['unverified'])
        self.assert_fixture_samples(result)
        self.assertTrue(result['bindings']['automation'])
        self.assertTrue(result['bindings']['script'])
        self.assertTrue(result['bindings']['template'])
        self.assertFalse(result['complete'], 'a dashboard render is not a census-only proof')
        # Native config is read back directly and is not derived from product review.
        self.assertEqual(dashboard._data['config']['views'][0]['cards'][0]['type'], 'map')
        if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
            directory = Path(evidence)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'census.json').write_text(json.dumps(result, sort_keys=True, indent=2))

    async def test_active_custom_card_is_identified_and_product_refuses_prepare(self):
        runtime, mapping, _, dashboard = await self._fixture_with_dashboard(custom=True)
        hass, root = self.f.hass, self.f.root
        census = await collect_native(hass, root, self.producer, mapping)
        print('MIXED_CENSUS_CUSTOM', json.dumps(census, sort_keys=True), flush=True)
        self.assertIn(('dashboard:<default>', 'custom_card_unsupported:custom:r8-unknown'), census['unverified'])
        before = await self._flush_documents()
        original_dashboard = Path(dashboard._store.path).read_bytes()
        original_sources = {name: (root / name).read_bytes() for name in
                            ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml',
                             'mixed_scripts.yaml', 'boards/r8-map.yaml')}
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            await runtime.native_yaml_prepare(hass, mapping, actor_id='census-custom-card',
                                               guard=lambda: None, refresh=refresh)
        except (ValueError, self.module('transaction').Refused) as error:
            print('CENSUS_CUSTOM_CARD_REFUSAL', type(error).__name__, str(error), flush=True)
        else:
            self.fail('active unqualified custom card was accepted by prepare')
        self.assertEqual(await self._flush_documents(), before)
        self.assertEqual(Path(dashboard._store.path).read_bytes(), original_dashboard)
        for name, raw in original_sources.items():
            self.assertEqual((root / name).read_bytes(), raw)

    async def test_active_native_map_prepare_is_refused_without_side_effects(self):
        runtime, mapping, _, dashboard = await self._fixture_with_dashboard()
        hass, root = self.f.hass, self.f.root
        census = await collect_native(hass, root, self.producer, mapping)
        self.assertIn(('dashboard:<default>', 'active_card_render_not_observed:map'), census['unverified'])
        before = await self._flush_documents()
        files = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml',
                 'mixed_scripts.yaml', 'boards/r8-map.yaml', '.storage/lovelace')
        original = {name: (root / name).read_bytes() for name in files}
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaises(self.module('transaction').Refused) as caught:
            await runtime.native_yaml_prepare(hass, mapping, actor_id='census-map', guard=lambda: None, refresh=refresh)
        print('CENSUS_NATIVE_MAP_REFUSAL', str(caught.exception), flush=True)
        self.assertEqual(str(caught.exception), 'template_forward_dashboard_evidence_unavailable')
        self.assertEqual(await self._flush_documents(), before)
        self.assertEqual({name: (root / name).read_bytes() for name in files}, original)
