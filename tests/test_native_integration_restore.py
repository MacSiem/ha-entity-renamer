"""Actual pinned Core restore limitations, not migration acceptance.

No helper internals are assigned. A native producer emits real state events;
Core performs the unload/setup and restore through its own lifecycle.
"""
from datetime import timedelta
from decimal import Decimal
import logging
from types import MappingProxyType
import unittest

import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class NativeIntegrationRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixture.NativeBaselineTests('test_actual_native_serialization_and_store_full_baseline')
        await self.f.asyncSetUp()
        self.addAsyncCleanup(self.f.asyncTearDown)

    async def test_native_rounded_restore_cannot_prove_full_precision_continuity(self):
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.components.integration.config_flow import ConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.helpers import entity_platform as ep
        hass = self.f.hass
        self.assertIsNot(await async_load_base_functionality(hass), False)
        class Producer(SensorEntity):
            _attr_name = 'Integration restore producer'
            _attr_unique_id = 'integration-restore-producer'
            _attr_should_poll = False
            _attr_native_unit_of_measurement = 'W'
            _attr_native_value = Decimal('123456.789')
        component = EntityComponent(logging.getLogger(__name__), 'sensor', hass,
                                    timedelta(seconds=60))
        producer = Producer()
        await component.async_add_entities([producer])
        entry = ConfigEntry(domain='integration', title='Native restore probe', data={},
            options={'name': 'Native restore probe', 'source': producer.entity_id,
                     'method': 'left', 'unit_time': 's', 'round': 2},
            version=ConfigFlowHandler.VERSION, minor_version=ConfigFlowHandler.MINOR_VERSION,
            source='user', discovery_keys=MappingProxyType({}), subentries_data=[],
            unique_id='native-integration-restore')
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        def entity():
            rows = [sensor for platform in ep.async_get_platforms(hass, 'integration')
                    if platform.config_entry is entry for sensor in platform.entities.values()]
            self.assertEqual(len(rows), 1)
            return rows[0]
        before = entity()
        for value in (Decimal('123456.790'), Decimal('123456.791')):
            producer._attr_native_value = value
            producer.async_write_ha_state()
            await hass.async_block_till_done()
        full = before._state
        displayed = Decimal(str(before.native_value))
        self.assertGreater(full, Decimal(0))
        self.assertGreater(displayed, Decimal(0))
        self.assertNotEqual(full, displayed)
        helper_id, unique_id = before.entity_id, before.unique_id
        self.assertTrue(await hass.config_entries.async_unload(entry.entry_id))
        self.assertTrue(await hass.config_entries.async_setup(entry.entry_id))
        await hass.async_block_till_done()
        after = entity()
        self.assertIsNot(after, before)
        self.assertEqual((after.entity_id, after.unique_id), (helper_id, unique_id))
        self.assertEqual(after._state, displayed)
        self.assertNotEqual(after._state, full)
        # A migration requiring exact counter continuity must refuse this case
        # before unloading. Visible value equality alone would miss the loss.
        self.assertEqual(Decimal(str(after.native_value)), displayed)

    async def test_native_zero_extra_data_roundtrip_is_rejected(self):
        from homeassistant.components.integration.sensor import IntegrationSensorExtraStoredData
        record = IntegrationSensorExtraStoredData(
            native_value=Decimal(0), native_unit_of_measurement='Wh',
            source_entity='sensor.synthetic_source', last_valid_state=Decimal(0))
        raw = record.as_dict()
        self.assertIsNone(raw['last_valid_state'])
        self.assertIsNone(IntegrationSensorExtraStoredData.from_dict(raw))
