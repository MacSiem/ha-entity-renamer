"""Actual timer reload diagnostic, never migration acceptance or QA seeding.

Only a real producer, real scheduled timer and native unload/setup mutate HA.
The observed overcount documents why exact counter restore alone is insufficient.
"""
import asyncio
from datetime import timedelta
from decimal import Decimal
import logging
from types import MappingProxyType
import unittest

import test_native_input_baseline as fixture
import test_native_integration_restore as native_fixture


@unittest.skipUnless(fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationTimerRestoreTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native_fixture.NativeIntegrationRestoreTests.asyncSetUp

    async def test_actual_timer_then_native_reload_recounts_old_source_interval(self):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.components.integration.config_flow import ConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.event import async_track_state_change_event
        hass = self.f.hass

        class Producer(SensorEntity):
            _attr_name = 'Timer continuity producer'
            _attr_unique_id = 'timer-continuity-producer'
            _attr_should_poll = False
            _attr_native_unit_of_measurement = 'W'
            _attr_native_value = Decimal('10')

        component = EntityComponent(logging.getLogger(__name__), 'sensor', hass,
                                    timedelta(seconds=60))
        producer = Producer()
        await component.async_add_entities([producer])
        entry = ConfigEntry(domain='integration', title='Timer continuity diagnostic',
            data={}, options={'name': 'Timer continuity diagnostic',
                'source': producer.entity_id, 'method': 'left', 'unit_time': 's',
                'round': None, 'max_sub_interval': {'seconds': 1}},
            version=ConfigFlowHandler.VERSION, minor_version=ConfigFlowHandler.MINOR_VERSION,
            source='user', discovery_keys=MappingProxyType({}), subentries_data=[],
            unique_id='timer-continuity-diagnostic')
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()

        def current():
            rows = [e for p in async_get_platforms(hass, 'integration')
                    if p.config_entry is entry for e in p.entities.values()]
            self.assertEqual(len(rows), 1)
            return rows[0]

        old = current()
        first_tick = asyncio.get_running_loop().create_future()

        def observe_timer(event):
            # A real timer changes helper state while the source stays constant.
            if old._state is not None and old._state > 0 and not first_tick.done():
                first_tick.set_result((old._state, old._last_integration_time,
                                       old._last_integration_trigger))

        unsubscribe = async_track_state_change_event(hass, old.entity_id, observe_timer)
        try:
            await asyncio.wait_for(first_tick, timeout=10)
        finally:
            unsubscribe()
        await hass.async_block_till_done()
        full = old._state
        self.assertGreater(full, 0)
        self.assertEqual(old._last_integration_trigger.name, 'TimeElapsed')
        old_anchor = old._last_integration_time
        source_before = hass.states.get(producer.entity_id)
        source_time = source_before.last_reported
        self.assertGreater(old_anchor, source_time)
        self.assertEqual(old.native_value, full)  # No rounding loss in this diagnostic.
        helper_id, unique_id = old.entity_id, old.unique_id
        self.assertTrue(await hass.config_entries.async_unload(entry.entry_id))
        self.assertTrue(await hass.config_entries.async_setup(entry.entry_id))
        await hass.async_block_till_done()
        restored = current()
        self.assertIsNot(restored, old)
        self.assertEqual((restored.entity_id, restored.unique_id), (helper_id, unique_id))
        self.assertEqual(restored._state, full)
        self.assertEqual(restored._last_integration_trigger.name, 'StateEvent')
        self.assertGreater(restored._last_integration_time, old_anchor)
        producer._attr_native_value = Decimal('11')
        producer.async_write_ha_state()
        await hass.async_block_till_done()
        source_after = hass.states.get(producer.entity_id)
        # Read only actual source timestamps. Both pins use the latest new State
        # time for a real changed value; changed has last_reported=last_updated.
        actual_delta = restored._state - full
        native_reload_interval = Decimal((source_after.last_reported - source_time).total_seconds()) * 10
        continuous_interval = Decimal((source_after.last_reported - old_anchor).total_seconds()) * 10
        self.assertEqual(actual_delta, native_reload_interval)
        self.assertGreater(actual_delta, continuous_interval)
        self.assertEqual(actual_delta - continuous_interval,
                         native_reload_interval - continuous_interval)
