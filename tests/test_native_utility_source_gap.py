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
