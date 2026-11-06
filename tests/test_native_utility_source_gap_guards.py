"""Actual source-stream refusals; component tests, not lifecycle authority."""
import asyncio
from contextlib import asynccontextmanager
import importlib
import unittest
from decimal import Decimal

import test_utility_meter_observers as fixture

gap_module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_utility_source_gap')
leases = importlib.import_module(fixture.fixtures.PACKAGE + '.native_helper_activation_lease')


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class NativeUtilitySourceGapGuardTests(unittest.IsolatedAsyncioTestCase):
    mapped_source = False
    asyncSetUp = fixture.ActualUtilityMeterObserverTests.asyncSetUp
    _before_start = fixture.ActualUtilityMeterObserverTests._before_start
    _fixture = fixture.ActualUtilityMeterObserverTests._fixture
    _emit = fixture.ActualUtilityMeterObserverTests._emit

    def guard(self):
        self.assertTrue(self.allowed)
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id), self.entry)
        self.assertEqual(self.f.entity.async_get(self.old).id, self.source_uuid)
        self.assertEqual(self.f.entity.async_get(self.entity.entity_id).id, self.helper_uuid)
        return True  # Only after the actual component identity checks above.

    @asynccontextmanager
    async def captured(self):
        await self._fixture()
        self.allowed = True
        self.helper_uuid = self.f.entity.async_get(self.entity.entity_id).id
        proof = (fixture.h.RegistryMappingProof(self.old, 'sensor.source_gap_renamed',
            self.source_uuid),) if self.mapped_source else ()
        original = await fixture.h.NativeHelperObservers.capture(self.f.hass, [self.before], proof)
        baseline, = original.runtime_baselines()
        self.lease = await leases.NativeHelperActivationLease.acquire(self.f.hass,
            self.f.hass.config_entries, (self.entry,), guard=self.guard)
        self.gap = None
        try:
            self.gap = gap_module.NativeUtilitySourceGap.capture(self.f.hass, baseline,
                self.before, self.lease, guard=self.guard)
            self.assertTrue(await self.lease.async_unload(self.entry.entry_id))
            await self.f.hass.async_block_till_done()
            self.helper_placeholder = self.f.hass.states.get(self.entity.entity_id)
            self.assertEqual(self.helper_placeholder.state, 'unavailable')
            yield
        finally:
            if self.gap is not None:
                self.gap.close()
            self.lease.release()

    def unchanged_unloaded_helper(self):
        self.assertIs(self.f.hass.states.get(self.entity.entity_id), self.helper_placeholder)
        self.assertEqual(self.entity.native_value, Decimal('11'))
        self.assertIsNone(self.entity._collecting)
        self.assertEqual(self.entry.state.value, 'not_loaded')

    async def test_real_source_reset_poisoned_without_counter_repair(self):
        async with self.captured():
            await self._emit('115')
            await self._emit('110')
            with self.assertRaises(gap_module.UtilitySourceGapError):
                self.gap.check()
            self.assertEqual(self.f.hass.states.get(self.old).state, '110')
            self.unchanged_unloaded_helper()

    async def test_real_source_unit_change_poisoned_without_counter_repair(self):
        async with self.captured():
            await self._emit('115', unit='Wh')
            with self.assertRaises(gap_module.UtilitySourceGapError):
                self.gap.check()
            self.assertEqual(self.f.hass.states.get(self.old).attributes['unit_of_measurement'], 'Wh')
            self.unchanged_unloaded_helper()

    async def test_guard_loss_does_not_throw_into_source_emission_or_block_cleanup(self):
        async with self.captured():
            self.allowed = False
            await self._emit('115')
            with self.assertRaises(gap_module.UtilitySourceGapError):
                self.gap.check()
            self.unchanged_unloaded_helper()
            callback = self.gap._callback
            def registered():
                return any(job.target is callback for job, _ in
                    self.f.hass.bus._listeners.get('state_changed', ()))
            self.assertTrue(registered())
            self.gap.close()  # Guard loss must not keep the monitor registered.
            self.assertFalse(registered())

    async def test_another_task_cannot_use_monitor_and_cannot_regain_it_after_refusal(self):
        async with self.captured():
            async def foreign():
                with self.assertRaisesRegex(gap_module.UtilitySourceGapError, 'task_or_loop_lost'):
                    self.gap.check()
            await asyncio.create_task(foreign())
            with self.assertRaises(gap_module.UtilitySourceGapError):
                self.gap.check()
            self.unchanged_unloaded_helper()

    async def test_public_bus_event_without_native_source_write_cannot_authorize_handoff(self):
        async with self.captured():
            source = self.f.hass.states.get(self.old)
            # Deliberately adversarial notification, never a positive source event
            # or a manufactured successful accounting/restore effect.
            self.f.hass.bus.async_fire('state_changed', {'entity_id': self.old,
                'old_state': source, 'new_state': source})
            await self.f.hass.async_block_till_done()
            with self.assertRaises(gap_module.UtilitySourceGapError):
                self.gap.check()
            self.assertIs(self.f.hass.states.get(self.old), source)
            self.unchanged_unloaded_helper()


class NativeMappedUtilitySourceGapGuardTests(NativeUtilitySourceGapGuardTests):
    mapped_source = True

    async def emit_nested(self, values):
        from homeassistant.core import callback
        @callback
        def emit(event):
            for value in values:
                self.source._attr_native_value = Decimal(value)
                self.source.async_write_ha_state()
        unsubscribe = self.f.hass.bus.async_listen('renamer_source_emission_probe', emit)
        try:
            self.f.hass.bus.async_fire('renamer_source_emission_probe')
            await self.f.hass.async_block_till_done()
        finally:
            unsubscribe()

    async def test_actual_nested_emissions_bind_latest_queued_event(self):
        async with self.captured():
            await self.emit_nested(('115', '119'))
            self.gap.check()
            self.assertIs(self.gap._latest_event.data['new_state'],
                          self.f.hass.states.get(self.old))
            self.assertEqual(self.gap._latest_value, Decimal('119'))
            self.unchanged_unloaded_helper()

    async def test_reset_between_queued_emissions_cannot_be_erased_by_good_head(self):
        async with self.captured():
            await self.emit_nested(('115', '110', '119'))
            with self.assertRaises(gap_module.UtilitySourceGapError):
                self.gap.check()
            self.assertEqual(self.f.hass.states.get(self.old).state, '119')
            self.unchanged_unloaded_helper()

    async def test_emission_monitor_loss_refuses_and_frees_own_tool(self):
        import sys
        em = importlib.import_module(fixture.fixtures.PACKAGE + '.native_utility_source_emissions')
        async with self.captured():
            key, code = em._tool, em._code
            profile = sys.getprofile()
            sys.monitoring.set_local_events(key, code, 0)
            with self.assertRaises(ValueError):
                self.gap.check()
            self.gap.close()
            self.assertIsNone(sys.monitoring.get_tool(key))
            self.assertIs(sys.getprofile(), profile)
