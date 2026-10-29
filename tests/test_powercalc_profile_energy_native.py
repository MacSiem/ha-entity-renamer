"""Real public profiles/native producers, fresh process per case, owner FIFO only.

No profile or calculation replacement. Public W assertions never enter caches,
receipts or producer power fields. The SAME ORIGINAL is reused after every event.
"""
import asyncio
from decimal import Decimal
import importlib
import logging
import os
from pathlib import Path
from datetime import timedelta
from types import MappingProxyType
import threading
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixtures
import test_powercalc_runtime_native as existing
from test_powercalc_profile_inputs import ProfileLight, InfraredSelector, stage_public_profiles


@unittest.skipUnless(fixtures.HAS_HA, 'requires actual pinned HA')
class PowercalcProfileEnergyNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = b'# ORIGINAL public profile native fixture namespace\n'
    asyncSetUp = existing.PowercalcRuntimeNativeTests.asyncSetUp
    custom_profile_directories = False
    _flush_documents = existing.PowercalcRuntimeNativeTests._flush_documents
    guard = existing.PowercalcRuntimeNativeTests.guard

    async def _setup_real_vendor(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component
        from custom_components.powercalc.config_flow import PowercalcConfigFlow
        from custom_components.powercalc.sensors.power import VirtualPowerSensor
        from custom_components.powercalc.sensors.energy import VirtualEnergySensor
        from custom_components.powercalc.sensors.group.custom import GroupedPowerSensor, GroupedEnergySensor
        from custom_components.powercalc.sensors.group.subtract import SubtractGroupSensor
        supplied = os.environ.get('POWERCALC_PROFILE_DIR')
        if not supplied:
            self.skipTest('requires exact public binary profile bundle; not a PASS')
        self.profile_root = stage_public_profiles(self.f.root, supplied)
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'network', {}))
        self.sources = [ProfileLight('Public static'), ProfileLight('Public dynamic', dynamic=True)]
        component = EntityComponent(logging.getLogger(__name__), 'light', hass, timedelta(seconds=60))
        await component.async_add_entities(self.sources)
        self.yaml_source, self.gui_source = [row.entity_id for row in self.sources]
        self.selector = InfraredSelector(self.gui_source.split('.', 1)[1])
        selects = EntityComponent(logging.getLogger(__name__), 'select', hass, timedelta(seconds=60))
        await selects.async_add_entities([self.selector])
        config = ('powercalc:\n  discovery:\n    enabled: false\n'
            '  disable_library_download: true\n  enable_analytics: false\n'
            '  create_energy_sensors: true\n  create_standby_group: false\n'
            '  create_cost_sensors: false\n  create_utility_meters: false\n'
            '  energy_sensor_unit_prefix: k\n  energy_integration_method: left\n'
            '  energy_sensor_precision: 6\n  energy_update_interval: 1\n'
            '  group_power_update_interval: 0\n  group_energy_update_interval: 0\n'
            '  sensors:\n    - entity_id: ' + self.yaml_source + '\n'
            '      unique_id: public-profile-static\n      manufacturer: signify\n'
            '      model: LWA001\n      create_energy_sensor: true\n')
        custom = getattr(self, 'custom_profile_directories', False)
        self.assertIs(type(custom), bool)
        if custom:
            config += '      custom_model_directory: powercalc/profiles/signify/LWA001\n'
        (self.f.root / 'configuration.yaml').write_text(config)
        self.assertTrue(await async_setup_component(hass, 'powercalc', await async_hass_config_yaml(hass)))
        def entry(name, unique_id, data):
            return ConfigEntry(domain='powercalc', title=name, unique_id=unique_id,
                data={'name': name, **data}, options={}, discovery_keys=MappingProxyType({}),
                source='user', version=PowercalcConfigFlow.VERSION,
                minor_version=PowercalcConfigFlow.MINOR_VERSION, subentries_data=[], pref_disable_polling=True)
        async def add(row):
            await hass.config_entries.async_add(row)
            await hass.async_block_till_done()
            self.assertEqual(row.state.value, 'loaded')
        def entity(cls, uid):
            found = [e for p in async_get_platforms(hass, 'powercalc') for e in p.entities.values()
                     if type(e) is cls and e.unique_id == uid]
            self.assertEqual(len(found), 1, (cls.__name__, uid))
            return found[0]
        self.entry = entry('Public dynamic', 'public-profile-dynamic', {
            'entity_id': self.gui_source, 'sensor_type': 'virtual_power',
            'manufacturer': 'lifx', 'model': 'LHB30E27UC10P', 'create_energy_sensor': True,
            **({'custom_model_directory': 'powercalc/profiles/lifx/LHB30E27UC10P'} if custom else {})})
        await add(self.entry)
        self.static_power = entity(VirtualPowerSensor, 'public-profile-static')
        self.dynamic_power = entity(VirtualPowerSensor, 'public-profile-dynamic')
        self.static_energy = entity(VirtualEnergySensor, 'public-profile-static_energy')
        self.dynamic_energy = entity(VirtualEnergySensor, 'public-profile-dynamic_energy')
        self.group = entry('Public sum', 'public-profile-sum', {
            'sensor_type': 'group', 'group_type': 'custom', 'create_energy_sensor': True,
            'force_calculate_group_energy': False,
            'group_power_entities': [self.static_power.entity_id, self.dynamic_power.entity_id],
            'group_energy_entities': [self.static_energy.entity_id, self.dynamic_energy.entity_id]})
        await add(self.group)
        self.sum_power = entity(GroupedPowerSensor, 'public-profile-sum')
        self.sum_energy = entity(GroupedEnergySensor, 'public-profile-sum_energy')
        self.subtract = entry('Public subtract', 'public-profile-subtract', {
            'sensor_type': 'group', 'group_type': 'subtract', 'create_energy_sensor': True,
            'entity_id': self.sum_power.entity_id, 'subtract_entities': [self.dynamic_power.entity_id]})
        await add(self.subtract)
        self.subtract_power = entity(SubtractGroupSensor, 'public-profile-subtract')
        self.subtract_energy = entity(VirtualEnergySensor, 'public-profile-subtract_energy')
        # Native Energy settings are configuration input, not energy counter data.
        from homeassistant.components.energy.data import async_get_manager
        manager = await async_get_manager(hass)
        preferences = manager.default_preferences()
        preferences['device_consumption'] = [{'stat_consumption': self.sum_energy.entity_id}]
        await manager.async_update(preferences)
        await manager._store.async_save(manager.data)
        self.graph = (self.static_power, self.dynamic_power, self.static_energy, self.dynamic_energy,
                      self.sum_power, self.sum_energy, self.subtract_power, self.subtract_energy)

    async def prepare(self):
        self.subject = importlib.import_module(fixtures.PACKAGE + '.powercalc_source_binding')
        self.runtime = importlib.import_module(fixtures.PACKAGE + '.powercalc_runtime_observer')
        planner = importlib.import_module(fixtures.PACKAGE + '.yaml_plan')
        inputs = importlib.import_module(fixtures.PACKAGE + '.powercalc_profile_inputs')
        validator = importlib.import_module(fixtures.PACKAGE + '.yaml_validation')
        plan = planner.plan_yaml(self.f.root, {self.yaml_source: 'light.public_static_target'})
        self.assertTrue(plan.ready, plan.issues)
        self.plan = await inputs.async_add_powercalc_source_inputs(self.f.hass, plan, guard=self.guard)
        self.receipt, self.evidence = await validator.async_validate_proposed_with_powercalc_sources(
            self.f.hass, self.plan, self.f.root.parent)
        self.first = self.runtime.capture_powercalc_runtime(self.f.hass, guard=self.guard)
        return await self.bind()

    async def bind(self):
        return await self.subject.async_bind_powercalc_sources(self.f.hass,
            self.receipt, self.evidence, self.first, guard=self.guard)

    def assert_bound(self, result):
        verified = {row.entity_id: row for row in result.rows if row.source_verified}
        self.assertEqual(set(verified), {row.entity_id for row in self.graph})
        self.assertEqual(verified[self.static_power.entity_id].source_registry_id,
                         self.f.entity.async_get(self.yaml_source).id)
        dynamic = verified[self.dynamic_power.entity_id]
        self.assertIn((self.selector.entity_id, dynamic.entity_id, 0), dynamic.edges)
        subtract = verified[self.subtract_power.entity_id]
        self.assertEqual(subtract.edges, tuple(sorted(((self.sum_power.entity_id, subtract.entity_id, 1),
                                          (self.dynamic_power.entity_id, subtract.entity_id, -1)))))
        self.assertTrue(result.relationships)
        self.assertFalse(result.family_complete)
        self.assertFalse(result.migration_ready)
        self.assertEqual(result.status, 'UNKNOWN')
        return verified

    async def test_real_public_lut_selector_signed_groups_and_native_energy(self):
        inputs = importlib.import_module(fixtures.PACKAGE + '.powercalc_profile_inputs')
        native_read, loop_thread, reads = inputs.read_bundle, threading.get_ident(), []
        def observe_actual_read(root):
            # Observe only Renamer's real read. Never return a substitute bundle,
            # invoke a vendor calculator or change a native producer/counter.
            self.assertNotEqual(threading.get_ident(), loop_thread)
            value = native_read(root)
            reads.append(threading.get_ident())
            return value
        with patch.object(inputs, 'read_bundle', observe_actual_read):
            self.assert_bound(await self.prepare())
            evidence, capture = self.evidence.sha256, self.first.structural_token
            original_strategy = self.dynamic_power._strategy_instance
            await self.sources[0].async_turn_on(brightness=255)
            for option, watts in [('25%', '2.71'), ('50%', '4.72'), ('100%', '8.52'), ('Disabled', '0.73')]:
                await self.selector.async_select_option(option)
                await self.f.hass.async_block_till_done()
                def power(entity):
                    return Decimal(self.f.hass.states.get(entity.entity_id).state)
                self.assertEqual(power(self.static_power), Decimal('8.51'))
                self.assertEqual(power(self.dynamic_power), Decimal(watts))
                self.assertEqual(power(self.sum_power), Decimal('8.51') + Decimal(watts))
                self.assertEqual(power(self.subtract_power), Decimal('8.51'))
                self.assert_bound(await self.bind())
                self.assertEqual((self.evidence.sha256, self.first.structural_token), (evidence, capture))
            self.assertIsNot(self.dynamic_power._strategy_instance, original_strategy)
            # Wait for real wall-clock native integration, with no synthetic time event.
            before = self.static_energy._state
            async with asyncio.timeout(8):
                while self.static_energy._state is None or self.static_energy._state == before:
                    await asyncio.sleep(0.02)
            self.assertGreater(self.static_energy._state, before or Decimal(0))
            await self.f.hass.async_block_till_done()
            for entity in (self.static_energy, self.dynamic_energy, self.sum_energy, self.subtract_energy):
                value = Decimal(self.f.hass.states.get(entity.entity_id).state)
                self.assertTrue(value.is_finite())
                self.assertGreater(value, Decimal(0))
            self.assert_bound(await self.bind())
        self.assertTrue(reads, 'positive must traverse the actual executor profile read')

    async def test_inactive_raw_gzip_drift_is_not_adopted(self):
        self.assert_bound(await self.prepare())
        path = self.profile_root / 'lifx/LHB30E27UC10P/infrared_100/hs.csv.gz'
        raw = path.read_bytes()
        try:
            path.write_bytes(raw + b'\x00')
            with self.assertRaises(ValueError):
                await self.bind()
        finally:
            path.write_bytes(raw)

    async def test_added_profile_member_invalidates_same_receipt(self):
        self.assert_bound(await self.prepare())
        path = self.profile_root / 'signify/LWA001/unselected.json'
        path.write_bytes(b'{}\n')
        try:
            with self.assertRaises(ValueError):
                await self.bind()
        finally:
            path.unlink()

    async def test_real_selector_listener_detach_cannot_keep_graph_proof(self):
        from homeassistant.helpers import event
        self.assert_bound(await self.prepare())
        tracker = self.f.hass.data[event._TRACK_STATE_CHANGE_DATA]
        tracker.listener()  # Actual native shared EventBus unsubscribe, negative only.
        await self.selector.async_select_option('25%')
        await self.f.hass.async_block_till_done()
        with self.assertRaises(ValueError):
            await self.bind()  # Energy settings cannot close through the detached graph.

    async def test_actual_loaded_lut_code_drift_is_rejected(self):
        self.assert_bound(await self.prepare())
        from custom_components.powercalc.strategy.lut import LutStrategy
        method, code = LutStrategy.calculate, LutStrategy.calculate.__code__
        try:
            method.__code__ = code.replace(co_firstlineno=code.co_firstlineno + 1)
            with self.assertRaises(ValueError):
                await self.bind()
        finally:
            method.__code__ = code

    async def test_actual_loaded_lookup_mode_code_drift_is_rejected(self):
        self.assert_bound(await self.prepare())
        from custom_components.powercalc.strategy.lut import LookupMode
        method = LookupMode.from_color_mode
        code = method.__code__
        original = self.evidence, self.first
        original_bytes = self.evidence.sha256, self.first.structural_token
        try:
            # Negative only: the same real dispatch function, no substitute
            # class/callback and no calculation or producer event under fault.
            method.__code__ = code.replace(co_firstlineno=code.co_firstlineno + 1)
            with self.assertRaises(ValueError):
                await self.bind()
        finally:
            method.__code__ = code
        self.assertIs(self.evidence, original[0])
        self.assertIs(self.first, original[1])
        self.assertEqual((self.evidence.sha256, self.first.structural_token), original_bytes)
        self.assert_bound(await self.bind())

    async def test_actual_gui_source_drift_is_not_rebaselined(self):
        self.assert_bound(await self.prepare())
        self.f.hass.config_entries.async_update_entry(self.entry,
            data={**self.entry.data, 'model': 'LWA001'})
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        with self.assertRaises(ValueError):
            await self.bind()

    async def test_profile_edit_after_final_code_read_is_rejected(self):
        self.assert_bound(await self.prepare())
        path = self.profile_root / 'lifx/LHB30E27UC10P/infrared_100/model.json'
        raw, native_sources, calls = path.read_bytes(), self.subject.PowercalcVendor.sources, []
        def read_then_edit(vendor):
            value = native_sources(vendor)
            calls.append(vendor)
            if len(calls) == 2:
                path.write_bytes(raw + b'\n')
            return value
        try:
            with patch.object(self.subject.PowercalcVendor, 'sources', read_then_edit):
                with self.assertRaises(ValueError):
                    await self.bind()
        finally:
            path.write_bytes(raw)
        self.assertEqual(len(calls), 2)
