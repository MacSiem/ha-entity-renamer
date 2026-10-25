"""Actual upstream characterization; these cases are NOT migration acceptance.

No helper fields, RestoreState cache, calibration, storage or time are injected.
The producer alone emits real readings through the existing native fixture.
"""
from copy import deepcopy
from decimal import Decimal
import unittest

import test_utility_meter_observers as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'native HA dependencies required')
class NativeUtilityIdentityRestoreTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ActualUtilityMeterObserverTests.asyncSetUp
    _before_start = fixture.ActualUtilityMeterObserverTests._before_start
    _fixture = fixture.ActualUtilityMeterObserverTests._fixture
    _emit = fixture.ActualUtilityMeterObserverTests._emit

    def current(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        entries = [entity for platform in async_get_platforms(self.f.hass, 'utility_meter')
                   if platform.config_entry is self.entry
                   for entity in platform.entities.values()]
        self.assertEqual(len(entries), 1)
        return entries[0]

    async def test_unload_before_helper_id_rename_loses_native_counter(self):
        await self._fixture()
        hass = self.f.hass
        old, target = self.entity.entity_id, 'sensor.renamed_utility_helper'
        uuid = self.f.entity.async_get(old).id
        original = deepcopy(self.entity.extra_restore_state_data.as_dict())
        self.assertEqual(self.entity.native_value, Decimal('11'))
        self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
        await hass.async_block_till_done()
        self.f.entity.async_update_entity(old, new_entity_id=target)
        self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
        await hass.async_block_till_done()
        restored = self.current()
        self.assertEqual(self.f.entity.async_get(target).id, uuid)
        self.assertIsNot(restored, self.entity)
        self.assertIsNone(restored.native_value)
        self.assertNotEqual(restored.extra_restore_state_data.as_dict(), original)

    async def test_live_rename_keeps_counter_but_reverse_uses_stale_target_cache(self):
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        await self._fixture()
        hass = self.f.hass
        original_entity = self.entity
        old, target = original_entity.entity_id, 'sensor.live_renamed_utility_helper'
        uuid = self.f.entity.async_get(old).id
        self.assertNotIn(target, hass.data[DATA_RESTORE_STATE].last_states)
        original = deepcopy(original_entity.extra_restore_state_data.as_dict())
        self.f.entity.async_update_entity(old, new_entity_id=target)
        await hass.async_block_till_done()
        self.assertIs(self.current(), original_entity)
        self.assertEqual(self.f.entity.async_get(target).id, uuid)
        self.assertIsNone(hass.states.get(old))
        self.assertEqual(original_entity.extra_restore_state_data.as_dict(), original)
        await self._emit('115')
        self.assertEqual(original_entity.native_value, Decimal('15'))
        self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
        await hass.async_block_till_done()
        self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
        await hass.async_block_till_done()
        self.entity = self.current()
        self.assertIsNot(self.entity, original_entity)
        self.assertEqual(self.entity.native_value, Decimal('15'))
        await self._emit('119')
        self.assertEqual(self.entity.native_value, Decimal('19'))
        live = self.entity
        self.f.entity.async_update_entity(target, new_entity_id=old)
        await hass.async_block_till_done()
        self.assertIs(self.current(), live)
        self.assertEqual(self.f.entity.async_get(old).id, uuid)
        self.assertIsNone(hass.states.get(target))
        # Native RestoreSensor reads the old cache produced by the first rename.
        self.assertEqual(live.native_value, Decimal('11'))
