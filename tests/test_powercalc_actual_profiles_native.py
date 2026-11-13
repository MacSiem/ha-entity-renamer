"""Synthetic topology of explicit YAML pairs and local LUT, with real HA/Powercalc."""
from datetime import timedelta
import gzip
import importlib
import json
import logging
import unittest

import test_native_input_baseline as fixtures
import test_powercalc_runtime_native as existing
import test_powercalc_profile_energy_native as graph_fixture
from test_powercalc_profile_inputs import ProfileLight


@unittest.skipUnless(fixtures.HAS_HA, 'requires exact native HA and Powercalc')
class ActualProfileGraphNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = b'# Synthetic Powercalc namespace\n'
    asyncSetUp = existing.PowercalcRuntimeNativeTests.asyncSetUp
    _flush_documents = existing.PowercalcRuntimeNativeTests._flush_documents
    prepare = graph_fixture.PowercalcProfileEnergyNativeTests.prepare
    bind = graph_fixture.PowercalcProfileEnergyNativeTests.bind

    async def _setup_real_vendor(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.discovery import async_load_platform
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component
        from types import MappingProxyType
        from custom_components.powercalc.config_flow import PowercalcConfigFlow

        self.assertEqual(self.f.version, '2026.11.1')
        self.assertTrue(await async_setup_component(self.f.hass, 'network', {}))
        hass, root = self.f.hass, self.f.root
        self.sources = [ProfileLight(f'Synthetic appliance {i}') for i in range(13)]
        component = EntityComponent(logging.getLogger(__name__), 'light', hass, timedelta(seconds=60))
        await component.async_add_entities(self.sources)
        self.yaml_source = self.sources[0].entity_id
        locator = 'powercalc/profiles/fixture/local'
        model = root / locator
        remote = root / '.storage/powercalc_profiles'
        self.remote = remote
        model.mkdir(parents=True)
        (remote / 'fixture/local').mkdir(parents=True)
        (model / 'model.json').write_text(json.dumps({
            'name': 'Synthetic LUT', 'device_type': 'light',
            'calculation_strategy': 'lut', 'standby_power': 1.2}))
        (model / 'brightness.csv.gz').write_bytes(gzip.compress(b'bri,power\n1,2.0\n255,12.0\n'))
        (model / 'color_temp.csv.gz').write_bytes(gzip.compress(
            b'bri,ct,power\n1,200,3.0\n255,200,13.0\n'))
        for key, raw in {
            'library.json': b'{',
            '.profile_hashes': b'{',
            'fixture/local/model.json': b'{"standby_power":99}',
            'fixture/local/brightness.csv.gz': b'not gzip',
            'fixture/local/color_temp.csv.gz': b'not gzip',
        }.items():
            (remote / key).write_bytes(raw)
        self.remote_original = self.remote_bytes()
        self.config = ('homeassistant:\n  packages: !include_dir_named packages\n')
        (root / 'configuration.yaml').write_text(self.config)
        (root / 'packages').mkdir()
        self.package = root / 'packages' / 'synthetic.yaml'
        self.controls = {'create_energy_sensor': True, 'create_utility_meters': False,
                         'energy_integration_method': 'left', 'ignore_unavailable_state': False}
        leaves = []
        for i, source in enumerate(self.sources[:10]):
            config = {'entity_id': source.entity_id, 'name': f'Synthetic meter {i}',
                      'unique_id': f'fixture-meter-{i}', **self.controls}
            if i < 3:
                config.update(mode='linear', linear={'min_power': 2 + i, 'max_power': 12 + i,
                                                     'gamma_curve': 1}, standby_power=0.3)
            elif i < 9:
                config.update(mode='fixed', fixed={'power': i}, standby_power=0)
            else:
                config.update(manufacturer='fixture', model='local', custom_model_directory=locator)
            leaves.append(config)
        self.package_globals = {'discovery': {'enabled': False}, 'disable_library_download': True,
            'enable_analytics': False, 'create_energy_sensors': True, 'create_standby_group': False,
            'create_cost_sensors': False, 'create_utility_meters': False,
            'energy_sensor_unit_prefix': 'k', 'energy_integration_method': 'left',
            'energy_sensor_precision': 6, 'group_power_update_interval': 0,
            'group_energy_update_interval': 0}
        self.write_package(leaves)
        self.assertTrue(await async_setup_component(hass, 'homeassistant', {}))
        # The complete on-disk package is used by the isolated checker. Load
        # actual producer entities before the groups that reference their IDs.
        self.assertTrue(await async_setup_component(hass, 'powercalc', {
            'powercalc': {**self.package_globals, 'sensors': [dict(row) for row in leaves]}}))
        await hass.async_block_till_done()
        self.entries = []
        for i, source in enumerate(self.sources[10:], 10):
            entry = ConfigEntry(domain='powercalc', title=f'Synthetic meter {i}',
                unique_id=f'fixture-meter-{i}', data={'entity_id': source.entity_id,
                    'name': f'Synthetic meter {i}', 'sensor_type': 'virtual_power',
                    'mode': 'fixed', 'fixed': {'power': i}, 'standby_power': 0.0,
                    'create_standby_energy_sensor': False, 'energy_filter_outlier_enabled': False,
                    **self.controls}, options={}, discovery_keys=MappingProxyType({}),
                source='user', version=PowercalcConfigFlow.VERSION,
                minor_version=PowercalcConfigFlow.MINOR_VERSION, subentries_data=[],
                pref_disable_polling=True)
            await hass.config_entries.async_add(entry)
            self.assertEqual(entry.state.value, 'loaded')
            self.entries.append(entry)
        self.entities = {e.unique_id: e for p in async_get_platforms(hass, 'powercalc')
                         for e in p.entities.values()}
        self.members = {'fixture-group-a': (0, 1, 3, 4, 9, 10),
                        'fixture-group-b': (2, 5, 6, 7, 8, 9, 11)}
        groups = [{'create_group': uid, 'unique_id': uid, 'create_energy_sensor': True,
                   'hide_members': False, 'entities': [
                       {'power_sensor_id': self.entities[f'fixture-meter-{i}'].entity_id,
                        'energy_sensor_id': self.entities[f'fixture-meter-{i}_energy'].entity_id}
                       for i in members]} for uid, members in self.members.items()]
        self.leaves, self.groups = leaves, groups
        self.write_package([*leaves, *groups])
        config = await async_hass_config_yaml(hass)
        for row in groups:
            await async_load_platform(hass, 'sensor', 'powercalc',
                                      {**row, 'discovery_type': 'user_yaml'}, config)
        await hass.async_block_till_done()
        self.entities = {e.unique_id: e for p in async_get_platforms(hass, 'powercalc')
                         for e in p.entities.values()}

    def guard(self):
        for entry in self.entries:
            self.assertIs(self.f.hass.config_entries.async_get_entry(entry.entry_id), entry)

    def write_package(self, rows):
        # JSON is YAML; all source/member IDs come from native registrations.
        self.package.write_text(json.dumps({'powercalc': {**self.package_globals, 'sensors': rows}},
                                           indent=2) + '\n')

    def remote_bytes(self):
        return {p.relative_to(self.remote): p.read_bytes()
                for p in self.remote.rglob('*') if p.is_file()}

    async def test_actual_shapes_reopen_and_refuse_foreign_members(self):
        result = await self.prepare()
        self.assertEqual(result.core_version, '2026.11.1')
        self.assertEqual(result.vendor_version, 'v1.27.0')
        self.assertEqual(len(self.entities), 30)
        rows = {row.entity_id: row for row in result.rows}
        self.assertEqual({row.entity_id for row in result.rows if row.source_verified},
                         {e.entity_id for e in self.entities.values()},
                         {key: row.reason for key, row in rows.items() if not row.source_verified})
        for uid, members in self.members.items():
            for suffix in ('', '_energy'):
                group = self.entities[uid + suffix]
                self.assertEqual(rows[group.entity_id].registry_id, group.registry_entry.id)
                self.assertEqual(rows[group.entity_id].edges, tuple(sorted(
                    (self.entities[f'fixture-meter-{i}' + suffix].entity_id, group.entity_id, 1)
                    for i in members)))
        for i, watts in [(0, '0.3'), (3, '0'), (9, '1.2')]:
            self.assertEqual(str(self.entities[f'fixture-meter-{i}']._standby_power), watts)
        self.assertEqual(self.remote_bytes(), self.remote_original)
        self.assertFalse(any('/powercalc_profiles/' in row[0] for row in self.receipt.source_files))
        self.assertFalse(result.migration_ready)
        self.assertEqual(result.status, 'UNKNOWN')
        from copy import deepcopy
        original = importlib.import_module(fixtures.PACKAGE + '.yaml_source_binding')
        codec = importlib.import_module(fixtures.PACKAGE + '.powercalc_composition')
        encoded = codec._typed(self.evidence, original.OriginalSourceEvidence, encode=True)
        self.evidence = codec._typed(json.loads(json.dumps(encoded)),
                                     original.OriginalSourceEvidence, encode=False)
        # Remote contents changing cannot invalidate the explicit local read-set.
        (self.remote / 'library.json').write_bytes(b'changed foreign remote data')
        self.assertEqual(await self.bind(), result)
        self.assertEqual((self.remote / 'library.json').read_bytes(), b'changed foreign remote data')
        owner = self.entities['fixture-group-a']
        removed = self.entities['fixture-meter-0'].entity_id
        owner._entities.remove(removed)
        with self.assertRaises(ValueError):
            await self.bind()
        owner._entities.add(removed)
        self.assertEqual(await self.bind(), result)
        broken = deepcopy(self.groups)
        broken[0]['entities'][0]['energy_sensor_id'] = self.entities['fixture-meter-12_energy'].entity_id
        self.write_package([*self.leaves, *broken])
        with self.assertRaisesRegex(ValueError, 'native_config_rejected') as error:
            await self.prepare()
        self.assertIn('yaml_group_pair_unverified', ' '.join(error.exception.__notes__))
        unsupported = deepcopy(self.leaves)
        del unsupported[9]['custom_model_directory']
        self.write_package([*unsupported, *self.groups])
        with self.assertRaises(ValueError):
            await self.prepare()
