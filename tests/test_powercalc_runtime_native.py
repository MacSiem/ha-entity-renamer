"""Authored native-only inventory tests; owner executes through FIFO.

Vendor test requires a FULL public v1.26.1 integration checkout and installed
requirements. No substitute vendor modules/cache, no private HA configuration.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixtures
import test_native_helper_quiescence as quiescence

try:
    subject = importlib.import_module(fixtures.PACKAGE + '.powercalc_runtime_observer')
except ModuleNotFoundError as error:
    if error.name != fixtures.PACKAGE + '.powercalc_runtime_observer':
        raise
    subject = None  # Owner initial RED follows actual native preparation.

discovery = importlib.import_module(fixtures.PACKAGE + '.discovery')


@unittest.skipUnless(fixtures.HAS_HA, 'requires actual pinned HA')
class PowercalcAbsentNativeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = quiescence.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = quiescence.NativeHelperQuiescenceTests._before_start

    async def test_no_vendor_does_not_create_manager_or_claim_complete(self):
        hass = self.f.hass
        self.assertNotIn('powercalc', hass.data)
        self.assertIsNotNone(subject, 'Powercalc native runtime inventory is not implemented')
        first = subject.capture_powercalc_runtime(hass, guard=lambda: None)
        subject.assert_powercalc_runtime_current(hass, first, guard=lambda: None)
        body = json.loads(first.payload_json)
        self.assertEqual(first.status, 'UNKNOWN')
        self.assertIsNone(body['vendor_version'])
        self.assertEqual(body['entries'], [])
        self.assertTrue(any(row['disposition'] == 'vendor_runtime_unavailable'
            for row in body['unsupported_rows']))
        self.assertNotIn('powercalc', hass.data)


@unittest.skipUnless(fixtures.HAS_HA, 'requires actual pinned HA')
class PowercalcRuntimeNativeTests(unittest.IsolatedAsyncioTestCase):
    _flush_documents = quiescence.NativeHelperQuiescenceTests._flush_documents

    async def asyncSetUp(self):
        from homeassistant.const import __version__
        if __version__ != '2026.10.2':
            self.skipTest('v1.26.1 native fixture targets compatible exact Core 2026.10.2')
        supplied = os.environ.get('POWERCALC_COMPONENT_DIR')
        if not supplied:
            self.skipTest('requires full public v1.26.1 POWERCALC_COMPONENT_DIR; not a PASS')
        vendor = Path(supplied).resolve()
        # Pin public executable source; never read a private migration manifest.
        expected_tree = '6f21338bcb02d2b488f8b1e9851b4d20300a888d7a493fdb753f78603f27c91c'
        files = {}
        for file in vendor.rglob('*'):
            if '__pycache__' in file.parts or file.suffix == '.pyc':
                continue
            self.assertFalse(file.is_symlink(), 'public fixture must not follow symlinks')
            if file.is_file():
                files[file.relative_to(vendor).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
        self.assertEqual(hashlib.sha256(json.dumps(files, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest(), expected_tree)
        if 'custom_components.powercalc' in sys.modules:
            self.skipTest('vendor already imported; owner must use a fresh native process')
        from homeassistant import loader
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        directory = fixtures.scratch()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        components = root / 'custom_components'
        components.mkdir()
        (components / '__init__.py').write_text('')
        shutil.copytree(vendor, components / 'powercalc',
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
        # Standard Python namespace search path for the real public package.
        # No vendor module/class/native method is replaced or fabricated.
        import custom_components
        original_search_path = custom_components.__path__
        custom_components.__path__ = [*original_search_path, str(components)]
        self.addCleanup(setattr, custom_components, '__path__', original_search_path)
        (root / 'configuration.yaml').write_text('script: {}\n')
        hass = HomeAssistant(str(root))
        self.addAsyncCleanup(hass.async_stop, force=True)
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await loader.async_get_custom_components(hass)
        self.assertIsNot(await async_load_base_functionality(hass), False)
        from types import SimpleNamespace
        self.f = SimpleNamespace(hass=hass, root=root, version=__version__,
            entity=er.async_get(hass), device=dr.async_get(hass))
        self.f.managers = (self.f.entity, self.f.device, hass.config_entries)
        self.modules = {name: importlib.import_module(fixtures.PACKAGE + '.' + name)
                        for name in ('native_registry', 'native_registry_transition',
                                     'helper_native', 'helper_transaction', 'native_config_transition')}
        await self._setup_real_vendor()
        await hass.async_start()
        await hass.async_block_till_done()
        await self._flush_documents(initialize=True)

    async def _setup_real_vendor(self):
        from datetime import timedelta
        import logging
        from types import MappingProxyType
        from homeassistant.components.switch import SwitchEntity
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.setup import async_setup_component
        from custom_components.powercalc.config_flow import PowercalcConfigFlow
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'network', {}))
        class Source(SwitchEntity):
            _attr_should_poll = False
            _attr_is_on = True
            def __init__(self, name):
                self._attr_name = name
                self._attr_unique_id = name
        self.sources = [Source('Powercalc YAML source'), Source('Powercalc GUI source')]
        component = EntityComponent(logging.getLogger(__name__), 'switch', hass, timedelta(seconds=60))
        await component.async_add_entities(self.sources)
        self.yaml_source, self.gui_source = [row.entity_id for row in self.sources]
        config = ('powercalc:\n  discovery:\n    enabled: false\n'
            '  disable_library_download: true\n  enable_analytics: false\n'
            '  create_energy_sensors: false\n  create_standby_group: false\n'
            '  sensors:\n    - entity_id: ' + self.yaml_source + '\n'
            '      unique_id: pc-readonly-yaml\n      fixed:\n        power: 7\n'
            '      create_energy_sensor: false\n')
        (self.f.root / 'configuration.yaml').write_text(config)
        self.assertTrue(await async_setup_component(hass, 'powercalc', await async_hass_config_yaml(hass)))
        def entry(title, unique_id, data):
            return ConfigEntry(domain='powercalc', title=title, unique_id=unique_id,
                data=data, options={}, discovery_keys=MappingProxyType({}), source='user',
                version=PowercalcConfigFlow.VERSION, minor_version=PowercalcConfigFlow.MINOR_VERSION,
                subentries_data=[], pref_disable_polling=True)
        self.entry = entry('Readonly GUI', 'pc-readonly-gui', {
            'name': 'Readonly GUI', 'entity_id': self.gui_source, 'sensor_type': 'virtual_power',
            'fixed': {'power': 10}, 'create_energy_sensor': False})
        await hass.config_entries.async_add(self.entry)
        await hass.async_block_till_done()
        self.assertEqual(self.entry.state.value, 'loaded')
        self.group = entry('Readonly group', 'pc-readonly-group', {
            'name': 'Readonly group', 'sensor_type': 'group', 'group_type': 'custom',
            'group_member_sensors': [self.entry.entry_id], 'create_energy_sensor': False})
        await hass.config_entries.async_add(self.group)
        await hass.async_block_till_done()
        self.assertEqual(self.group.state.value, 'loaded')

    def guard(self):
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id), self.entry)

    async def test_actual_yaml_gui_group_inventory_readonly_binding_and_invalidation(self):
        from homeassistant.helpers.storage import Store
        hass = self.f.hass
        self.assertIsNotNone(subject, 'Powercalc native runtime inventory is not implemented')
        original_files = {Path(manager._store.path): Path(manager._store.path).read_bytes()
                          for manager in self.f.managers}
        original_data = dict(self.entry.data)
        def forbidden(*args, **kwargs):
            raise AssertionError('read-only collector attempted native Store load/save')
        with patch.object(Store, 'async_save', forbidden), patch.object(Store, 'async_load', forbidden):
            first = subject.capture_powercalc_runtime(hass, guard=self.guard)
            subject.assert_powercalc_runtime_current(hass, first, guard=self.guard)
        body = json.loads(first.payload_json)
        self.assertEqual(first.status, 'UNKNOWN')
        self.assertEqual(body['vendor_version'], 'v1.26.1')
        self.assertEqual(set(body['indexes']), set(hass.data['powercalc']))
        self.assertEqual({row['entry_id'] for row in body['entries']},
                         {self.entry.entry_id, self.group.entry_id})
        self.assertTrue({'sensor', 'select'} <= {row['domain'] for row in body['platforms']})
        actual = {entity_id for row in body['platforms'] for entity_id in row['entity_ids']}
        captured = {row['entity_id'] for row in body['objects'] if row['active_platforms']}
        self.assertEqual(captured, actual)
        self.assertTrue(any('yaml_runtime_index' in row['source_origins']
            and any(self.yaml_source in ref for ref in row['references'])
            for row in body['objects']))
        self.assertTrue(any(row['fields'].get('_source_entity', {}).get('entity_id') == self.gui_source
            for row in body['objects'] if isinstance(row['fields'].get('_source_entity'), dict)))
        group_row, = [row for row in body['objects']
            if row['active_platforms'] and row['type'].endswith('.GroupedPowerSensor')]
        self.assertEqual(group_row['fields']['_entities']['$set'], [self.entry.data['_power_entity']])
        self.assertTrue(any(row['type'] == 'custom_components.powercalc.measure.MeasureAppCoordinator'
            and row['disposition'] == 'unsupported_value_type' for row in body['unsupported_rows']))
        self.assertTrue(all(row['source_disposition'] == 'runtime_only_source_binding_unverified'
                            for row in body['objects']))
        self.assertEqual(dict(self.entry.data), original_data)
        for path, raw in original_files.items():
            self.assertEqual(path.read_bytes(), raw)
        proposal = {'entities': [{'entity_id': self.gui_source,
            'proposed_entity_id': 'switch.readonly_source_changed',
            'device_id': self.f.entity.async_get(self.gui_source).device_id}]}
        found = await discovery.async_discover(hass, proposal, self.guard)
        family = next(row for row in found.families if row.name == 'powercalc')
        self.assertEqual(family.status, 'UNKNOWN')
        self.assertEqual(family.reason, 'powercalc_runtime_inventory_partial')
        enriched = json.loads(family.payload_json)
        self.assertEqual(set(enriched['runtime']['indexes']), set(hass.data['powercalc']))
        summary = json.dumps(found.summary())
        for private in (self.entry.entry_id, self.gui_source, str(self.f.root)):
            self.assertNotIn(private, summary)
        self.assertFalse(discovery.compose_preview(found)['migration_ready'])
        # A real producer sample is not mistaken for new source configuration.
        self.sources[1]._attr_is_on = False
        self.sources[1].async_write_ha_state()
        await hass.async_block_till_done()
        subject.assert_powercalc_runtime_current(hass, first, guard=self.guard)
        # A genuine native lifecycle change must invalidate the old inventory.
        self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
        await hass.async_block_till_done()
        with self.assertRaisesRegex(ValueError, 'runtime_changed_during_capture'):
            subject.assert_powercalc_runtime_current(hass, first, guard=self.guard)
        after = subject.capture_powercalc_runtime(hass, guard=self.guard)
        rows = json.loads(after.payload_json)
        self.assertEqual(after.status, 'UNKNOWN')
        inactive, = [row for row in rows['entries'] if row['entry_id'] == self.entry.entry_id]
        self.assertEqual(inactive['activation'], 'not_loaded')
        self.assertFalse(any(row['active_platforms'] for row in rows['objects']
            if row['entry_id'] == self.entry.entry_id))
