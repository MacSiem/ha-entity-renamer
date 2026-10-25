"""Characterize genuine upstream source-gap accounting; no product acceptance."""
from decimal import Decimal
import unittest

import test_utility_meter_observers as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class NativeUtilitySourceGapTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ActualUtilityMeterObserverTests.asyncSetUp
    _before_start = fixture.ActualUtilityMeterObserverTests._before_start
    _fixture = fixture.ActualUtilityMeterObserverTests._fixture
    _emit = fixture.ActualUtilityMeterObserverTests._emit

    async def test_native_gap_is_accounted_on_next_real_source_event(self):
        await self._fixture()
        hass = self.f.hass
        helper_id = self.entity.entity_id
        row_id = self.f.entity.async_get(helper_id).id
        self.assertEqual(self.entity.native_value, Decimal('11'))
        self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
        await hass.async_block_till_done()
        await self._emit('115')
        await self._emit('119')
        self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
        await hass.async_block_till_done()
        from homeassistant.helpers.entity_platform import async_get_platforms
        entities = [e for p in async_get_platforms(hass, 'utility_meter')
                    if p.config_entry is self.entry for e in p.entities.values()]
        self.assertEqual(len(entities), 1)
        restored = entities[0]
        # Native setup alone retains the pre-gap value. It is NOT yet an exact
        # A0+(current_source-S0) runtime proof; product settle remains OPEN.
        self.assertEqual(restored.native_value, Decimal('11'))
        self.assertEqual(restored._last_valid_state, Decimal('111'))
        self.assertEqual(hass.states.get(self.old).state, '119')
        await self._emit('123')
        # Real native callback uses persisted last_valid_state, not event.old=119.
        # Thus it accounts the entire actual gap plus the first post-setup delta.
        self.assertEqual(restored.native_value, Decimal('23'))
        self.assertEqual(restored._last_valid_state, Decimal('123'))
        self.assertEqual(self.f.entity.async_get(helper_id).id, row_id)

    async def test_latest_real_event_can_catch_up_owned_native_job_without_replay(self):
        """Characterize the callback seam; no product witness/READY claim."""
        from homeassistant.core import callback
        from homeassistant.helpers import event as event_helper
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.components.utility_meter.sensor import UtilityMeterSensor
        await self._fixture()
        hass = self.f.hass
        helper_id = self.entity.entity_id
        uuid = self.f.entity.async_get(helper_id).id
        latest = None
        @callback
        def observe(event):
            nonlocal latest
            if event.data.get('entity_id') == self.old:
                latest = event
        unsubscribe = hass.bus.async_listen('state_changed', observe)
        try:
            self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
            await hass.async_block_till_done()
            await self._emit('115')
            await self._emit('119')
            retained = latest
            self.assertIsNotNone(retained)
            self.assertIs(retained.data['new_state'], hass.states.get(self.old))
            self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
            await hass.async_block_till_done()
            entity, = [e for p in async_get_platforms(hass, 'utility_meter')
                       if p.config_entry is self.entry for e in p.entities.values()]
            self.assertIs(type(entity), UtilityMeterSensor)
            self.assertEqual(entity.native_value, Decimal('11'))
            tracker = hass.data[event_helper._TRACK_STATE_CHANGE_DATA]
            jobs = [job for job in tracker.callbacks[self.old]
                    if getattr(job.target, '__self__', None) is entity]
            self.assertEqual(len(jobs), 1)
            job, = jobs
            self.assertIs(job.target.__func__, UtilityMeterSensor.async_reading)
            self.assertIsNone(self.entity._collecting)

            def catch_up(actual_event):
                # No await between the current-state decision, delivery to the
                # existing native callback, and its independent output check.
                source = hass.states.get(self.old)
                expected = Decimal('11') + Decimal(source.state) - Decimal('111')
                if (entity.native_value == expected
                        and entity._last_valid_state == Decimal(source.state)):
                    return False
                self.assertIs(actual_event.data['new_state'], source)
                self.assertIs(job.target.__self__, entity)
                self.assertIs(job.target.__func__, UtilityMeterSensor.async_reading)
                hass.async_run_hass_job(job, actual_event)
                self.assertEqual(entity.native_value, expected)
                self.assertEqual(entity._last_valid_state, Decimal(source.state))
                self.assertEqual(Decimal(hass.states.get(helper_id).state), expected)
                return True
            self.assertTrue(catch_up(retained))
            self.assertEqual(entity.native_value, Decimal('19'))
            self.assertFalse(catch_up(retained))
            await self._emit('123')
            self.assertEqual(entity.native_value, Decimal('23'))
            # Native listener already consumed 123. Never replay retained119.
            self.assertFalse(catch_up(retained))
            self.assertEqual(entity.native_value, Decimal('23'))
            self.assertEqual(entity._last_valid_state, Decimal('123'))
            self.assertEqual(self.f.entity.async_get(helper_id).id, uuid)
        finally:
            unsubscribe()
