"""Actual HA mixed fixture: independent census, real stimuli and blockers."""
import json
import os
from pathlib import Path
import unittest
import tempfile
import shutil

import test_native_template_mixed_yaml_server as mixed
from mixed_fixture_census import collect_native, consumer_evidence, census_coverage, fresh_inverse_after_apply
from batch_readback import Edit, JSONStoreEdit, RowChange, capture_tree, compare, discovery_coverage, native_location_snapshot, native_empty_auth_snapshot


class NativeMixedFixtureCensusTest(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    async def _before_start(self, hass, root):
        if not os.environ.get('RENAMER_RENDER_HANDSHAKE'):
            return await mixed.NativeTemplateMixedYAMLServerTests._before_start(self, hass, root)
        # Full frontend loads config itself. Set up the real config component
        # once, instead of also manually registering the three editor views.
        import socket
        from homeassistant import auth
        from homeassistant.helpers.recorder import async_initialize_recorder
        from homeassistant.setup import async_setup_component
        async_initialize_recorder(hass)
        self.assertTrue(await async_setup_component(hass, 'recorder', {'recorder': {
            'db_url': 'sqlite:///' + str(root / 'fixture.db'), 'commit_interval': 0,
            'auto_purge': False, 'auto_repack': False}}))
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.assertTrue(await async_setup_component(hass, 'http', {'http': {
            'server_host': '127.0.0.1', 'server_port': port}}))
        self.assertTrue(await async_setup_component(hass, 'config', {}))
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

    async def _fixture_with_dashboard(self, *, custom=False, named=False):
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
        if named and os.environ.get('RENAMER_RENDER_HANDSHAKE'):
            # Completing onboarding after Lovelace setup would create an
            # additional live dynamic map strategy; initialize it first.
            from homeassistant.components.onboarding import OnboardingStorage, STORAGE_KEY, STORAGE_VERSION
            from homeassistant.components.onboarding.const import DOMAIN as ONBOARDING, STEPS
            from homeassistant.components.onboarding.views import _BaseOnboardingStepView
            self.assertTrue(await async_setup_component(hass, ONBOARDING, {}))
            onboarding = hass.data.get(ONBOARDING)
            if onboarding is not None and not onboarding.onboarded:
                store = OnboardingStorage(hass, STORAGE_VERSION, STORAGE_KEY, private=True)
                step_view = _BaseOnboardingStepView(onboarding.steps, store)
                for step in STEPS:
                    if step not in onboarding.steps['done']:
                        step_view.step = step
                        await step_view._async_mark_done(hass)
                self.assertTrue(onboarding.onboarded)
            from homeassistant.components.onboarding import async_is_onboarded
            self.assertTrue(async_is_onboarded(hass))
        if named:
            from homeassistant.helpers.storage import Store
            metadata = {'id': 'r6_map', 'url_path': 'r6-map', 'mode': 'storage',
                        'require_admin': False, 'show_in_sidebar': True, 'title': 'Synthetic storage map',
                        'icon': 'mdi:map'}
            await Store(hass, 1, 'lovelace_dashboards').async_save({'items': [metadata]})
        config = await async_hass_config_yaml(hass)
        self.assertTrue(await async_setup_component(hass, 'lovelace', config))
        self.assertIn('lovelace', hass.config.components)
        native = hass.data[LOVELACE_DATA]
        if named:
            await native.dashboards[None].async_save({'views': [{'title': 'Unchanged', 'cards': []}]})
        dashboard = native.dashboards['r6-map' if named else None]
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

    async def test_generic_custom_dashboard_native_review_receipt_and_unchanged_peer(self):
        runtime, mapping, uuids, dashboard = await self._fixture_with_dashboard(custom=True, named=True)
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        peer = hass.data[LOVELACE_DATA].dashboards[None]
        original_peer = await peer.async_load(False)
        original_peer_bytes = Path(peer._store.path).read_bytes()
        original_dashboard = await dashboard.async_load(False)
        observed = await self.module('native_discovery_consumers').async_observe_discovery_consumers(
            hass, guard=lambda: None)
        self.assertEqual(observed.dashboard.status, 'COMPLETE')
        changes = self.module('dashboard_storage_semantics').qualify_dashboard_storage(observed, mapping)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].before.dashboard.route, 'r6-map')
        self.assertEqual(changes[0].after['views'][0]['cards'][0]['entity'], new)
        from homeassistant.helpers import area_registry, floor_registry, label_registry, issue_registry
        for module, member in ((floor_registry, 'floors'), (label_registry, 'labels'), (area_registry, 'areas')):
            manager = hass.data.get(module.DATA_REGISTRY)
            if manager is None:
                await module.async_load(hass)
                manager = hass.data[module.DATA_REGISTRY]
            elif not hasattr(manager, member):
                await manager.async_load()
            await manager._store.async_save(manager._data_to_save())
        await hass.auth._store._store.async_save(hass.auth._store._data_to_save())
        issues = issue_registry.async_get(hass)
        await issues._store.async_save(issues._data_to_save())
        await self._flush_documents()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='generic-dashboard',
                                                   guard=lambda: None, refresh=refresh)
        self.assertEqual(review['dependencies']['dashboards'], [{'route': 'r6-map',
            'references': [{'before': old, 'after': new, 'occurrences': 1}]}])
        applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
            actor_id='generic-dashboard', guard=lambda: None, refresh=refresh)
        self.assertEqual(applied['state'], 'applied')
        self.assertEqual((await dashboard.async_load(False))['views'][0]['cards'][0]['entity'], new)
        self.assertEqual(await peer.async_load(False), original_peer)
        self.assertEqual(Path(peer._store.path).read_bytes(), original_peer_bytes)
        self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                                                    guard=lambda: None, refresh=refresh)
        self.assertTrue(inspected['eligible'])
        self.assertEqual(inspected['state'], 'applied')
        await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                                          guard=lambda: None, refresh=refresh)
        self.assertEqual(await dashboard.async_load(False), original_dashboard)
        self.assertEqual(await peer.async_load(False), original_peer)
        self.assertEqual(Path(peer._store.path).read_bytes(), original_peer_bytes)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])

    async def test_active_native_map_forward_and_fresh_inverse(self):
        diagnostics = mixed.native_diagnostics(self.module)
        diagnostics.__enter__()
        self.addCleanup(diagnostics.__exit__, None, None, None)
        runtime, mapping, uuids, dashboard = await self._fixture_with_dashboard(named=True)
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        named_id, named_uuid = self.identities[('sensor', 'scope-value')]
        original_name = self.f.entity.async_get(named_id).name
        original_yaml = (root / 'boards/r8-map.yaml').read_bytes()
        original_dashboard = await dashboard.async_load(False)
        render_dir = os.environ.get('RENAMER_RENDER_HANDSHAKE')
        if render_dir:
            from homeassistant.config import async_hass_config_yaml
            from homeassistant.setup import async_setup_component
            config = await async_hass_config_yaml(hass)
            for domain in ('frontend', 'api'):
                self.assertTrue(await async_setup_component(hass, domain, config))
            await hass.async_block_till_done()
            # A YAML-configured HTTP port starts a native five-minute trial on
            # this Core. Settle it before ORIGINAL so its timer cannot mutate
            # the foreign HTTP Store during the long independent proof.
            from homeassistant.components.http.config import DATA_STORE
            http_store = hass.data[DATA_STORE]
            if http_store._pending is not None:
                await http_store.async_promote_pending()
            self.assertIsNone(http_store._pending)
            self.assertIsNone(http_store._revert_unsub)
            refresh_token = hass.auth.async_get_refresh_token(hass.data['http.auth'])
            self.assertIsNotNone(refresh_token)
            token = hass.auth.async_create_access_token(refresh_token, remote_ip='127.0.0.1')
            token_path = Path(render_dir) / 'render-token'
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(token)
            del token

        async def render(label, source):
            if render_dir:
                from dashboard_render_handshake import observe_render
                state = hass.states.get(source)
                if state is None:
                    raise ValueError('render_source_missing')
                result = await observe_render(render_dir, label, source, state.state, hass.http.server_port)
                print('MAP_RENDER', label, json.dumps(result, sort_keys=True), flush=True)
        from homeassistant.helpers import area_registry, floor_registry, label_registry
        for module, member in ((floor_registry, 'floors'), (label_registry, 'labels'), (area_registry, 'areas')):
            manager = hass.data.get(module.DATA_REGISTRY)
            if manager is None:
                await module.async_load(hass)
                manager = hass.data[module.DATA_REGISTRY]
            elif not hasattr(manager, member):
                await manager.async_load()
            await manager._store.async_save(manager._data_to_save())
        await hass.auth._store._store.async_save(hass.auth._store._data_to_save())
        self.assertEqual(native_empty_auth_snapshot(hass, root)['status'], 'COMPLETE')
        # HA may schedule its first repairs Store write during bootstrap. Bind
        # the real, settled store to ORIGINAL rather than weakening the fence.
        from homeassistant.helpers import issue_registry
        issues = issue_registry.async_get(hass)
        await issues._store.async_save(issues._data_to_save())
        self.assertIsNone(issues._store._data)
        self.assertIsNone(issues._store._delay_handle)
        self.assertEqual(json.loads(Path(issues._store.path).read_bytes())['data'], issues._data_to_save())
        editors = self.module('yaml_editor_locks').NativeYAMLEditorLocks(hass).enumerate()
        print('MAP_NATIVE_EDITOR_LOCKS', [(path, name) for path, name, _, _ in editors], flush=True)
        self.assertTrue(editors)
        snapshots = tempfile.TemporaryDirectory(dir=self.scratch_parent)
        self.addCleanup(snapshots.cleanup)

        async def capture(label, current_mapping):
            census = await collect_native(hass, root, self.producer, current_mapping)
            if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
                (Path(evidence) / (label + '-census.json')).write_text(
                    json.dumps(census, sort_keys=True, indent=2))
            await self._flush_documents()
            graph = self.module('yaml_plan').plan_yaml(root, current_mapping)
            row = self.f.entity.async_get(old) or self.f.entity.async_get(new)
            proposal = {'entities': [{'entity_id': old, 'proposed_entity_id': new,
                                      'device_id': row.device_id}], 'devices': [], 'statistics': []}
            discovery = await self.module('discovery').async_discover(hass, proposal, guard=lambda: None)
            families, _ = discovery_coverage(discovery)
            families, external = census_coverage(census, families)
            destination = Path(snapshots.name) / label
            info = capture_tree(root, destination, inventory_files=[(r.path, r.sha256) for r in graph.files],
                families=families, external_consumers=external,
                external_inventory_complete=census['external']['empty_in_bounded_fixture'],
                recorder_databases=('fixture.db',), native_locations=native_location_snapshot(hass, root),
                native_auth=native_empty_auth_snapshot(hass, root))
            if evidence := os.environ.get('RENAMER_BATCH_EVIDENCE'):
                directory = Path(evidence)
                shutil.copytree(destination, directory / label)
                (directory / (label + '-census.json')).write_text(json.dumps(census, sort_keys=True, indent=2))
                (directory / (label + '-inventory.json')).write_text(json.dumps(info, sort_keys=True, indent=2))
            self.assert_fixture_samples(census)
            return census, destination, info

        try:
            await render('before', old)
        finally:
            census, before, before_info = await capture('map-before', mapping)
        self.assertIn(('dashboard:r6-map', 'active_card_render_not_observed:map'), census['unverified'])
        self.assertEqual(set(map(tuple, census['unverified'])), {
            ('dashboard:r6-map', 'active_card_render_not_observed:map'),
            ('dashboard:r8-map', 'active_card_render_not_observed:entity')})
        self.assertTrue(census['external']['empty_in_bounded_fixture'])
        edits = [(JSONStoreEdit if path == '.storage/lovelace.r6_map' else Edit)(
                    path, old.encode(), new.encode(), (before / path).read_bytes().count(old.encode()))
                 for path in ('template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml',
                              'boards/r8-map.yaml', '.storage/lovelace.r6_map')]
        self.assertTrue(all(edit.count > 0 for edit in edits))
        row_changes = [RowChange('.storage/core.entity_registry', named_uuid, 'name',
                                 original_name, 'Reviewed map consumer')]
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, entity_names={named_id: 'Reviewed map consumer'},
                                                   actor_id='census-map', guard=lambda: None, refresh=refresh)
        self.assertIn('dashboard', review['dependencies']['domains'])
        self.assertEqual(review['dependencies']['dashboards'], [{'route': 'r6-map',
            'references': [{'before': old, 'after': new, 'occurrences': 1}]}])
        self.assertEqual(next(row for row in review['changes'] if row.get('entity_id') == named_id)
                         ['fields']['name']['after'], 'Reviewed map consumer')
        applied = None
        try:
            applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='census-map', guard=lambda: None, refresh=refresh)
            self.assertEqual(applied['state'], 'applied')
            self.assertEqual((await dashboard.async_load(False))['views'][0]['cards'][0]['entities'], [new])
            self.assertIn(new.encode(), (root / 'boards/r8-map.yaml').read_bytes())
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            self.assertEqual(self.f.entity.async_get(named_id).name, 'Reviewed map consumer')
            try:
                await render('after', new)
            finally:
                after_census, after, after_info = await capture('map-after', {new: old})
        finally:
            try:
                inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                                                           guard=lambda: None, refresh=refresh)
                print('MAP_FRESH_INSPECT', json.dumps(inspected, sort_keys=True), flush=True)
                inverse = None
                if inspected.get('eligible') and inspected.get('state') == 'applied':
                    inverse = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                                                               guard=lambda: None, refresh=refresh)
                    print('MAP_INVERSE_RESULT', json.dumps(inverse, sort_keys=True), flush=True)
                elif applied is not None and applied.get('state') == 'applied':
                    raise RuntimeError('applied operation was not eligible for fresh inverse')
            finally:
                record = await runtime.journal.load(review['operation_id'])
                print('MAP_JOURNAL', None if record is None else
                      (record.status, record.reason, record.direction, record.phases), flush=True)
                old_row, new_row = self.f.entity.async_get(old), self.f.entity.async_get(new)
                current = (old if old_row is not None and new_row is None and old_row.id == uuids[old]
                           else new if new_row is not None and old_row is None and new_row.id == uuids[old]
                           else None)
                if current is not None:
                    direction = mapping if current == old else {new: old}
                    try:
                        try:
                            await render('restore' if current == old else 'final', current)
                        finally:
                            restored_census, restored, restored_info = await capture('map-final-readback', direction)
                    except Exception as error:
                        print('MAP_FINAL_READBACK_FAILED', type(error).__name__, str(error), flush=True)
                        raise
                else:
                    print('MAP_FINAL_READBACK_FAILED', 'source_identity_ambiguous', flush=True)
        self.assertTrue(inspected['eligible'])
        self.assertEqual(await dashboard.async_load(False), original_dashboard)
        self.assertEqual((root / 'boards/r8-map.yaml').read_bytes(), original_yaml)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertEqual(self.f.entity.async_get(named_id).name, original_name)
        inventory, checks, bindings = consumer_evidence(census, after_census, allow_unrendered=True)
        forward = compare(before, after, old=old, new=new, uuid=uuids[old], edits=edits,
            row_changes=row_changes, captures=(before_info, after_info), consumer_inventory=inventory,
            consumer_inventory_complete=False, consumer_checks=checks, source_consumers=bindings)
        print('MAP_BOUND_FORWARD', json.dumps(forward, sort_keys=True), flush=True)
        self.assertEqual(forward['findings'], [])
        omitted = compare(before, after, old=old, new=new, uuid=uuids[old], edits=edits[:-1],
                          row_changes=row_changes)
        self.assertIn(('unexpected_change', '.storage/lovelace.r6_map'), omitted['findings'])
        foreign = Path(snapshots.name) / 'foreign-negative'
        shutil.copytree(after, foreign)
        foreign_file = foreign / 'configuration.yaml'
        foreign_file.write_bytes(foreign_file.read_bytes() + b'\n# independent foreign edit\n')
        foreign_result = compare(before, foreign, old=old, new=new, uuid=uuids[old], edits=edits,
                                 row_changes=row_changes)
        self.assertIn(('unexpected_change', 'configuration.yaml'), foreign_result['findings'])
        inventory, checks, bindings = consumer_evidence(census, restored_census, allow_unrendered=True)
        reverse = compare(before, restored, old=old, new=new, uuid=uuids[old], edits=edits,
            row_changes=row_changes, reverse=True, captures=(before_info, restored_info),
            consumer_inventory=inventory, consumer_inventory_complete=False,
            consumer_checks=checks, source_consumers=bindings)
        print('MAP_BOUND_RESTORE', json.dumps(reverse, sort_keys=True), flush=True)
        self.assertEqual(reverse['findings'], [])

    async def test_real_yaml_dashboard_metadata_produces_native_evidence(self):
        _, mapping, _, _ = await self._fixture_with_dashboard()
        from homeassistant.const import __version__
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        module = self.module('native_discovery_consumers')
        native = self.module('lovelace_native').NativeLovelace(self.f.hass)
        token = module._inventory(self.f.hass, native, __version__)
        evidence = await module._dashboards(self.f.hass, native, __version__, token, lambda: None)
        self.assertEqual(len(evidence.yaml_sources), 1)
        self.assertEqual(len(evidence.snapshots), 1)
        source, = evidence.yaml_sources
        raw = (self.f.root / source.filename).read_bytes()
        graph = self.module('yaml_plan').plan_yaml(self.f.root, mapping)
        before, after = self.module('dashboard_yaml_semantics').qualify_dashboard_yaml(
            graph, source.filename, raw, mapping)
        manager = self.f.hass.data[LOVELACE_DATA].dashboards[source.route]
        self.assertEqual(await manager.async_load(False), before)
        self.assertEqual(before['views'][0]['cards'][0]['entity'], next(iter(mapping)))
        self.assertEqual(after['views'][0]['cards'][0]['entity'], next(iter(mapping.values())))
        observed = await module.async_observe_discovery_consumers(self.f.hass, guard=lambda: None)
        changes = self.module('dashboard_storage_semantics').qualify_dashboard_storage(observed, mapping)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].before.config['views'][0]['cards'][0]['entities'], [next(iter(mapping))])
        self.assertEqual(changes[0].after['views'][0]['cards'][0]['entities'], [next(iter(mapping.values()))])
        storage = await self.module('dashboard_storage_semantics').async_prepare_dashboard_storage(
            self.f.hass, observed, mapping, guard=lambda: None)
        self.assertEqual(storage.changes, changes)
        storage.check()
        qualify = self.module('dashboard_yaml_semantics').async_qualify_registered_dashboard_yaml
        admission = await qualify(self.f.hass, observed, graph, mapping, guard=lambda: None)
        self.assertEqual(admission.paths, (source.filename,))
        admission.check()
        validate = self.module('yaml_validation').async_validate_proposed_with_sources
        with self.assertRaises(ValueError):
            await validate(self.f.hass, graph, self.f.root.parent)
        receipt, _ = await validate(self.f.hass, graph, self.f.root.parent,
                                    dashboard_yaml=admission, dashboard_storage=storage)
        self.assertIn(source.filename, {path for path, _ in receipt.proposed_files})
        self.assertIn('.storage/lovelace', {row[0] for row in receipt.source_files})
        from homeassistant.components.lovelace.dashboard import LovelaceYAML
        data = self.f.hass.data[LOVELACE_DATA]
        data.dashboards[source.route] = LovelaceYAML(
            self.f.hass, source.route, data.yaml_dashboards[source.route])
        try:
            with self.assertRaises(ValueError):
                await qualify(self.f.hass, observed, graph, mapping, guard=lambda: None)
        finally:
            data.dashboards[source.route] = manager
        path = self.f.root / source.filename
        foreign = raw + b'# foreign edit\n'
        path.write_bytes(foreign)
        with self.assertRaises(ValueError):
            await qualify(self.f.hass, observed, graph, mapping, guard=lambda: None)
        self.assertEqual(path.read_bytes(), foreign)

    async def test_native_dashboard_storage_foreign_edit_is_not_adopted(self):
        _, mapping, _, dashboard = await self._fixture_with_dashboard()
        observed = await self.module('native_discovery_consumers').async_observe_discovery_consumers(
            self.f.hass, guard=lambda: None)
        foreign = {'views': [{'title': 'Foreign retained', 'cards': [{'type': 'map',
                    'entities': [next(iter(mapping))]}]}]}
        await dashboard.async_save(foreign)
        before = Path(dashboard._store.path).read_bytes()
        with self.assertRaises(ValueError):
            await self.module('dashboard_storage_semantics').async_prepare_dashboard_storage(
                self.f.hass, observed, mapping, guard=lambda: None)
        self.assertEqual(Path(dashboard._store.path).read_bytes(), before)
        self.assertEqual(dashboard._data['config'], foreign)

    async def test_mixed_same_source_name_and_id_refuses_before_effect(self):
        runtime, mapping, _, dashboard = await self._fixture_with_dashboard()
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        original = await self._flush_documents()
        storage = Path(dashboard._store.path).read_bytes()
        source = (root / 'boards/r8-map.yaml').read_bytes()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_prepare(hass, mapping, entity_names={old: 'Same source name'},
                actor_id='same-source-negative', guard=lambda: None, refresh=refresh)
        self.assertEqual(await self._flush_documents(), original)
        self.assertEqual(Path(dashboard._store.path).read_bytes(), storage)
        self.assertEqual((root / 'boards/r8-map.yaml').read_bytes(), source)
