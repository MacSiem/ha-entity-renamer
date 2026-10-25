"""Actual pinned HA classes in synthetic SSD trees; owner runs through FIFO.

This file is authored but deliberately not run by the patch-artifact worker.
"""
import asyncio
from copy import deepcopy
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixture

subject = importlib.import_module(fixture.PACKAGE + '.native_discovery_consumers')


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class NativeDiscoveryConsumersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant.core import HomeAssistant
        from homeassistant.const import __version__
        from homeassistant.helpers.storage import Store
        from homeassistant.components.lovelace import LovelaceData
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        from homeassistant.components.lovelace.dashboard import LovelaceStorage, LovelaceYAML
        from homeassistant.components.energy.data import async_get_manager
        self.version = __version__
        self.directory = fixture.scratch()
        self.root = Path(self.directory.name).resolve()
        self.hass = HomeAssistant(str(self.root))
        self.addAsyncCleanup(self.shutdown)
        self.calls = 0
        self.default = LovelaceStorage(self.hass, None)
        self.metadata = {'id': 'stable-private', 'url_path': 'private-board',
            'mode': 'storage', 'require_admin': True, 'show_in_sidebar': False,
            'title': 'Private native board'}
        self.named = LovelaceStorage(self.hass, self.metadata)
        self.yaml_metadata = {'mode': 'yaml', 'filename': 'boards/source.yaml',
            'require_admin': True, 'show_in_sidebar': False, 'title': 'YAML board'}
        (self.root / 'boards').mkdir()
        (self.root / 'boards/source.yaml').write_text('views: []\n')
        self.yaml = LovelaceYAML(self.hass, 'different-route', self.yaml_metadata)
        self.managers = {None: self.default, 'private-board': self.named,
                         'different-route': self.yaml}
        kwargs = {'resource_mode' if self.version == '2026.10.2' else 'mode': 'storage'}
        self.data = LovelaceData(**kwargs, dashboards=self.managers, resources=None,
            yaml_dashboards={'different-route': self.yaml_metadata})
        self.hass.data[LOVELACE_DATA] = self.data
        self.inventory_store = Store(self.hass, 1, 'lovelace_dashboards')
        await self.inventory_store.async_save({'items': [self.metadata]})
        self.config = {'views': [{'cards': [{'type': 'entity', 'entity': 'sensor.old'}]}],
                       'retained_foreign_metadata': {'ordered': ['one', 'two']}}
        # Save fixture bytes directly through native Store to avoid event bus UI.
        await self.named._store.async_save({'config': self.config})
        self.named._data = {'config': deepcopy(self.config)}
        self.manager = await async_get_manager(self.hass)  # actual singleton fixture setup
        self.preferences = self.manager.default_preferences()
        self.preferences['energy_sources'] = [{'type': 'solar', 'stat_energy_from': 'sensor.old'}]
        self.preferences['device_consumption'] = [{'stat_consumption': 'sensor.other'}]
        await self.manager.async_update(self.preferences)
        await self.manager._store.async_save(self.manager.data)
        await self.hass.async_block_till_done()

    async def shutdown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    def guard(self):
        self.calls += 1

    async def observe(self):
        return await subject.async_observe_discovery_consumers(self.hass, guard=self.guard)

    async def test_full_native_storage_default_admin_yaml_and_energy(self):
        captured = await self.observe()
        self.assertEqual(captured.version, self.version)
        self.assertEqual(captured.dashboard.status, 'COMPLETE')
        self.assertEqual(captured.energy.status, 'COMPLETE')
        snapshots = captured.dashboard.value.snapshots
        self.assertEqual({row.dashboard.route for row in snapshots}, {None, 'private-board'})
        default = next(row for row in snapshots if row.dashboard.route is None)
        named = next(row for row in snapshots if row.dashboard.route == 'private-board')
        self.assertIsNone(default.config)
        self.assertIsNone(default.file_identity)
        self.assertEqual(named.dashboard.key, 'lovelace.stable-private')
        self.assertTrue(named.file_identity)
        self.assertEqual(tuple(named.config['retained_foreign_metadata']['ordered']), ('one', 'two'))
        yaml, = captured.dashboard.value.yaml_sources
        self.assertEqual(yaml.route, 'different-route')
        self.assertEqual(yaml.filename, 'boards/source.yaml')
        self.assertTrue(yaml.file_identity)
        self.assertEqual(captured.energy.value.revision, subject.revision(self.manager.data))
        with self.assertRaises(TypeError):
            named.config['views'] = ()
        with self.assertRaises(TypeError):
            captured.energy.value.value['energy_sources'][0]['type'] = 'grid'
        self.config['retained_foreign_metadata']['ordered'].append('later')
        self.assertEqual(len(named.config['retained_foreign_metadata']['ordered']), 2)
        self.assertGreater(self.calls, 10)

    async def test_server_discovery_uses_native_families_and_actual_yaml_filename(self):
        discovery = importlib.import_module(fixture.PACKAGE + '.discovery')
        (self.root / 'configuration.yaml').write_text('script: {}\n')
        capture = await discovery.async_discover(self.hass,
            {'entities': [{'entity_id': 'sensor.old', 'proposed_entity_id': 'sensor.new'}]},
            self.guard)
        families = {row.name: row for row in capture.families}
        self.assertEqual(families['dashboard'].status, 'COMPLETE')
        self.assertEqual(families['energy'].status, 'COMPLETE')
        self.assertEqual(len(families['dashboard'].rows), 3)
        self.assertEqual(len(families['energy'].rows), 1)
        import json
        graph = json.loads(families['yaml_sources'].payload_json)
        self.assertIn('boards/source.yaml', {row['path'] for row in graph['files']})
        self.assertNotIn('different-route', {row['path'] for row in graph['files']})
        preview = discovery.compose_preview(capture)
        self.assertFalse(preview['migration_ready'])
        self.assertNotIn('sensor.old', json.dumps(preview))

    async def test_server_discovery_does_not_adopt_later_dashboard_image(self):
        discovery = importlib.import_module(fixture.PACKAGE + '.discovery')
        (self.root / 'configuration.yaml').write_text('script: {}\n')
        original, calls = subject.async_observe_discovery_consumers, 0
        async def foreign_edit(*args, **kwargs):
            nonlocal calls
            result = await original(*args, **kwargs)
            calls += 1
            if calls == 1:
                self.named._data['config']['foreign'] = 'retained'
                await self.named._store.async_save(self.named._data)
            return result
        with patch.object(subject, 'async_observe_discovery_consumers', foreign_edit):
            captured = await discovery.async_discover(self.hass,
                {'entities': [{'entity_id': 'sensor.old', 'proposed_entity_id': 'sensor.new'}]},
                self.guard)
        families = {row.name: row for row in captured.families}
        self.assertEqual(calls, 2)
        self.assertEqual(families['dashboard'].status, 'UNKNOWN')
        self.assertEqual(families['energy'].status, 'COMPLETE')
        self.assertEqual(self.named._data['config']['foreign'], 'retained')

    async def test_missing_energy_does_not_erase_dashboard(self):
        del self.hass.data['energy_manager']
        result = await self.observe()
        self.assertEqual(result.energy.status, 'UNKNOWN')
        self.assertIsNone(result.energy.value)
        self.assertEqual(result.dashboard.status, 'COMPLETE')

    async def test_real_energy_durable_absence_only(self):
        # A fresh real initialized singleton with no durable file proves absence.
        # Existing fixture file is quarantined rather than destructively deleted.
        path = Path(self.manager._store.path)
        path.rename(path.with_name('energy-fixture-quarantine'))
        self.manager.data = None
        result = await self.observe()
        self.assertEqual(result.energy.status, 'COMPLETE')
        self.assertIsNone(result.energy.value.value)
        self.assertIsNone(result.energy.value.file_identity)

    async def test_inflight_or_wrong_energy_type_not_awaited_or_adopted(self):
        for pending in (asyncio.Event(), asyncio.get_running_loop().create_future(), object()):
            self.hass.data['energy_manager'] = pending
            result = await self.observe()
            self.assertEqual(result.energy.status, 'UNKNOWN')
            self.assertEqual(result.dashboard.status, 'COMPLETE')
            self.assertIs(self.hass.data['energy_manager'], pending)

    async def test_wrong_schema_and_pending_energy(self):
        self.manager.data['unknown_secret_field'] = 'must-never-appear'
        await self.manager._store.async_save(self.manager.data)
        result = await self.observe()
        self.assertEqual(result.energy.status, 'UNKNOWN')
        self.assertNotIn('must-never-appear', repr(result))
        self.manager.data.pop('unknown_secret_field')
        await self.manager._store.async_save(self.manager.data)
        self.manager._store._data = {'pending': True}
        result = await self.observe()
        self.assertEqual(result.energy.status, 'UNKNOWN')
        self.manager._store._data = None

    async def test_half_loaded_collection_does_not_erase_energy(self):
        await self.inventory_store.async_save({'items': []})
        result = await self.observe()
        self.assertEqual(result.dashboard.status, 'UNKNOWN')
        self.assertEqual(result.energy.status, 'COMPLETE')

    async def test_foreign_member_added_during_energy_read(self):
        from homeassistant.components.lovelace.dashboard import LovelaceStorage
        original = subject.NativeEnergy.read
        async def changing(native):
            snapshot = await original(native)
            if 'foreign-board' not in self.managers:
                self.managers['foreign-board'] = LovelaceStorage(self.hass, {
                    **self.metadata, 'id': 'foreign', 'url_path': 'foreign-board'})
            return snapshot
        with patch.object(subject.NativeEnergy, 'read', changing):
            result = await self.observe()
        self.assertEqual(result.dashboard.status, 'UNKNOWN')
        self.assertEqual(result.energy.status, 'COMPLETE')

    async def test_registered_yaml_drop_and_unloaded_definition(self):
        self.managers.pop('different-route')
        result = await self.observe()
        self.assertEqual(result.dashboard.status, 'UNKNOWN')
        self.assertEqual(result.energy.status, 'COMPLETE')

    async def test_unchanged_native_dashboard_change_during_energy_read(self):
        original = subject.NativeEnergy.read
        changed = False
        async def changing(native):
            nonlocal changed
            snapshot = await original(native)
            if not changed:
                changed = True
                self.named._data['config']['foreign'] = 'retained'
                await self.named._store.async_save(self.named._data)
            return snapshot
        with patch.object(subject.NativeEnergy, 'read', changing):
            result = await self.observe()
        self.assertEqual(result.dashboard.status, 'UNKNOWN')
        self.assertEqual(result.energy.status, 'COMPLETE')

    async def test_yaml_relative_filename_and_symlink_refusal(self):
        for filename in ('../outside.yaml', '/absolute.yaml', 'boards/../source.yaml'):
            with self.subTest(filename=filename):
                self.yaml_metadata['filename'] = filename
                self.yaml.config = {**self.yaml_metadata, 'url_path': 'different-route'}
                self.yaml.path = self.hass.config.path(filename)
                result = await self.observe()
                self.assertEqual(result.dashboard.status, 'UNKNOWN')
        self.yaml_metadata['filename'] = 'boards/link.yaml'
        self.yaml.config = {**self.yaml_metadata, 'url_path': 'different-route'}
        self.yaml.path = self.hass.config.path('boards/link.yaml')
        (self.root / 'boards/link.yaml').symlink_to(self.root / 'boards/source.yaml')
        result = await self.observe()
        self.assertEqual(result.dashboard.status, 'UNKNOWN')

    async def test_legacy_default_yaml_and_modern_default_boundary(self):
        from homeassistant.components.lovelace.dashboard import LovelaceYAML
        self.managers[None] = LovelaceYAML(self.hass, None, None)
        (self.root / 'ui-lovelace.yaml').write_text('views: []\n')
        result = await self.observe()
        expected = 'COMPLETE' if self.version == '2025.6.0' else 'UNKNOWN'
        self.assertEqual(result.dashboard.status, expected)
        if expected == 'COMPLETE':
            source = next(row for row in result.dashboard.value.yaml_sources if row.route is None)
            self.assertEqual(source.filename, 'ui-lovelace.yaml')

    async def test_typed_permission_sanitized_even_when_wrapped(self):
        async def denied(native):
            try:
                raise PermissionError('/private/secret-configuration')
            except PermissionError as cause:
                raise subject.PersistenceError('private nested detail') from cause
        with patch.object(subject.NativeEnergy, 'read', denied):
            result = await self.observe()
        self.assertEqual(result.energy.status, 'DENIED')
        self.assertEqual(result.energy.reason, 'read_access_denied')
        self.assertIsNone(result.energy.value)
        self.assertNotIn('secret-configuration', repr(result.energy))
        self.assertEqual(result.dashboard.status, 'COMPLETE')

    async def test_guard_revocation_and_no_effect_or_initialization(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count > 6:
                raise PermissionError('revoked-private')
        with self.assertRaises(PermissionError):
            await subject.async_observe_discovery_consumers(self.hass, guard=guard)
        from homeassistant.components.energy.data import EnergyManager
        from homeassistant.helpers.storage import Store
        before = {path: path.read_bytes() for path in (self.root / '.storage').iterdir() if path.is_file()}
        def forbidden(*args, **kwargs):
            raise AssertionError('discovery attempted a native effect')
        with patch.object(EnergyManager, 'async_initialize', forbidden), \
             patch.object(EnergyManager, 'async_update', forbidden), \
             patch.object(Store, 'async_save', forbidden), \
             patch.object(Store, 'async_load', forbidden):
            result = await self.observe()
        self.assertEqual(result.dashboard.status, 'COMPLETE')
        self.assertEqual(result.energy.status, 'COMPLETE')
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    async def test_unsupported_runtime_and_store_schema_never_complete(self):
        import homeassistant.const
        with patch.object(homeassistant.const, '__version__', '2099.1.0'):
            result = await self.observe()
        self.assertEqual(result.dashboard.status, 'UNKNOWN')
        self.assertEqual(result.energy.status, 'UNKNOWN')
        self.named._store.minor_version = 99
        result = await self.observe()
        self.assertEqual(result.dashboard.status, 'UNKNOWN')
        self.assertEqual(result.energy.status, 'COMPLETE')
        self.named._store.minor_version = 1
        previous = self.manager._store.minor_version
        self.manager._store.minor_version = 99
        result = await self.observe()
        self.assertEqual(result.dashboard.status, 'COMPLETE')
        self.assertEqual(result.energy.status, 'UNKNOWN')
        self.manager._store.minor_version = previous

    async def test_cold_lovelace_native_cache_only_after_strict_read(self):
        self.named._data = None
        result = await self.observe()
        self.assertEqual(result.dashboard.status, 'COMPLETE')
        self.assertEqual(self.named._data, {'config': self.config})


class GuardBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_inner_await_revocation_precedes_resumed_native_body(self):
        loop = asyncio.get_running_loop()
        first, second = loop.create_future(), loop.create_future()
        events = []
        revoked = False
        def guard():
            if revoked:
                raise PermissionError('sanitized-caller-refusal')
        async def native_reader():
            events.append('entered')
            await first
            events.append('after-first')
            await second
            events.append('after-second')
        async def capture():
            return await subject._guarded(native_reader(), guard)
        task = asyncio.create_task(capture())
        await asyncio.sleep(0)
        first.set_result(None)
        await asyncio.sleep(0)
        self.assertEqual(events, ['entered', 'after-first'])
        revoked = True
        second.set_result(None)
        with self.assertRaises(PermissionError):
            await task
        self.assertEqual(events, ['entered', 'after-first'])
