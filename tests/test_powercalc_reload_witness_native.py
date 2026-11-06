"""Official current-only full reload witness; owner native FIFO executes.

Test-only integration RED is missing product API AFTER genuine prerequisites.
No fake native positives, profile substitution, seed, clock or arithmetic patch.
"""
import asyncio
from dataclasses import FrozenInstanceError
from decimal import Decimal
import importlib
import json
import unittest

import test_powercalc_profile_energy_native as profiles

try:
    subject=importlib.import_module(profiles.fixtures.PACKAGE+'.powercalc_reload_witness')
except ModuleNotFoundError as error:
    if error.name!=profiles.fixtures.PACKAGE+'.powercalc_reload_witness':
        raise
    subject=None


@unittest.skipUnless(profiles.fixtures.HAS_HA,'requires actual pinned HA')
class PowercalcReloadWitnessNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes=profiles.PowercalcProfileEnergyNativeTests.namespace_source_bytes
    asyncSetUp=profiles.PowercalcProfileEnergyNativeTests.asyncSetUp
    _setup_real_vendor=profiles.PowercalcProfileEnergyNativeTests._setup_real_vendor
    _flush_documents=profiles.PowercalcProfileEnergyNativeTests._flush_documents
    guard=profiles.PowercalcProfileEnergyNativeTests.guard
    prepare=profiles.PowercalcProfileEnergyNativeTests.prepare
    bind=profiles.PowercalcProfileEnergyNativeTests.bind
    assert_bound=profiles.PowercalcProfileEnergyNativeTests.assert_bound

    async def ready_original(self):
        self.assert_bound(await self.prepare())
        await self.sources[0].async_turn_on(brightness=255)
        await self.f.hass.async_block_till_done()
        # Only real wall-clock Integration timers initialize actual native data.
        energies=(self.static_energy,self.dynamic_energy,self.subtract_energy)
        async with asyncio.timeout(8):
            while not all(type(e._state) is Decimal and e._state.is_finite()
                    and type(e._last_valid_state) is Decimal
                    and e._last_valid_state.is_finite() for e in energies):
                await asyncio.sleep(0.02)
        self.assert_bound(await self.bind())
        self.assertIsNotNone(subject,'actual unchanged-ORIGINAL Powercalc reload witness is missing')

    async def run_witness(self):
        return await subject.async_witness_unchanged_powercalc_reload(self.f.hass,
            self.receipt,self.evidence,self.first,guard=self.guard)

    async def test_actual_reload_captures_energy_but_refuses_full_global_ownership(self):
        await self.ready_original()
        original=(self.evidence.sha256,self.first.structural_token,self.first.payload_json)
        old={e.entity_id:id(e) for e in self.graph}
        result=await self.run_witness()
        self.assertIs(type(result),subject.PowercalcReloadWitness)
        self.assertTrue(result.entity_lifecycle_verified)
        self.assertFalse(result.reload_ownership_verified)
        self.assertIn('capture_only_no_full_reload_ownership',result.reload_blockers)
        self.assertIn('vendor_global_rename_listeners_retained',result.reload_blockers)
        global_before=json.loads(result.global_rename_before_json)
        global_after=json.loads(result.global_rename_after_json)
        self.assertTrue(global_before)
        # Official GUI dynamic + subtract setups leak their original callback
        # pairs; native reload installs one additional actual pair per source.
        # No callback/filter is invoked by this assertion or by the product.
        self.assertEqual({row['entry_id'] for row in global_before.values()},
                         {self.entry.entry_id,self.subtract.entry_id})
        self.assertTrue(set(global_before)<set(global_after))
        for key,row in global_before.items():
            self.assertEqual(global_after[key],row)
            matching=[actual for actual in global_after.values()
                      if (actual['entry_id'],actual['source_entity_id'])
                         ==(row['entry_id'],row['source_entity_id'])]
            self.assertEqual(len(matching),2)
            self.assertEqual(len({actual['callback_process_id'] for actual in matching}),2)
        self.assertFalse(result.counter_ownership_verified)
        self.assertFalse(result.continuity_verified)
        self.assertFalse(result.migration_ready)
        self.assertFalse(result.family_complete)
        self.assertEqual(result.status,'UNKNOWN')
        self.assertEqual(result.original_evidence_sha256,original[0])
        self.assertEqual(result.original_runtime_token,original[1])
        self.assertNotEqual(result.actual_runtime_token,original[1])
        self.assertEqual((self.evidence.sha256,self.first.structural_token,self.first.payload_json),original)
        before=json.loads(result.actual_before_json)
        after=json.loads(result.actual_after_json)
        departed=json.loads(result.actual_departures_json)
        calls=json.loads(result.native_calls_json)
        self.assertEqual(set(before),set(old))
        self.assertEqual(set(departed['removed']),set(old))
        self.assertEqual(set(after),set(old))
        self.assertEqual(calls.count(['root','start']),1)
        self.assertEqual(calls.count(['root','return']),1)
        for entry in (self.entry,self.group,self.subtract):
            for stage in ('unload','setup'):
                self.assertEqual(calls.count([stage,'return',entry.entry_id]),1)
        self.assertEqual({key:old_id for key,old_id,_ in result.instance_pairs},old)
        for key,old_id,new_id in result.instance_pairs:
            self.assertNotEqual(old_id,new_id)
            self.assertEqual(before[key]['process_id'],old_id)
            self.assertEqual(after[key]['process_id'],new_id)
            self.assertEqual((before[key]['uuid'],before[key]['unique_id']),
                             (after[key]['uuid'],after[key]['unique_id']))
        energies={e.entity_id for e in (self.static_energy,self.dynamic_energy,self.subtract_energy)}
        self.assertEqual(set(departed['before_cancel']),energies)
        self.assertEqual(set(departed['last_active']),energies)
        for eid in energies:
            self.assertEqual(before[eid]['class'],'VirtualEnergySensor')
            for snapshot in (before[eid],departed['removed'][eid],after[eid]):
                self.assertTrue(Decimal(snapshot['full']).is_finite())
                self.assertTrue(Decimal(snapshot['last_valid']).is_finite())
                self.assertIn(snapshot['trigger'],('StateEvent','TimeElapsed'))
                self.assertIs(type(snapshot['timer']['cancelled']),bool)
            self.assertTrue(departed['removed'][eid]['timer']['cancelled'])
            self.assertFalse(departed['last_active'][eid]['timer']['cancelled'])
        group=self.sum_energy.entity_id
        self.assertIn(['store_clean','return',group],calls)
        self.assertEqual(before[group]['class'],'GroupedEnergySensor')
        self.assertTrue(Decimal(before[group]['full']).is_finite())
        self.assertEqual(before[group]['store_process_id'],after[group]['store_process_id'])
        self.assertTrue(before[group]['start_at_zero'])
        self.assertTrue(before[group]['previous_present'])
        self.assertTrue(before[group]['previous'])
        with self.assertRaises(FrozenInstanceError):
            result.migration_ready=True
        # A completed own-call witness does NOT turn ORIGINAL instance evidence
        # into a live current baseline, even though source/config bytes match.
        with self.assertRaises(ValueError):
            await self.bind()

    async def test_actual_foreign_profile_drift_refuses_before_reload(self):
        await self.ready_original()
        path=self.profile_root/'signify/LWA001/unselected.json'
        path.write_bytes(b'{}\n')
        try:
            with self.assertRaises(ValueError):
                await self.run_witness()
            self.runtime.assert_powercalc_runtime_current(self.f.hass,self.first,guard=self.guard)
        finally:
            path.unlink()  # Restore only after negative assertions.

    async def test_actual_external_reload_cannot_adopt_stale_original_instances(self):
        await self.ready_original()
        await self.f.hass.services.async_call('powercalc','reload',{},blocking=True)
        await self.f.hass.async_block_till_done()
        actual=self.runtime.capture_powercalc_runtime(self.f.hass,guard=self.guard)
        self.assertNotEqual(actual.structural_token,self.first.structural_token)
        with self.assertRaises(ValueError):
            await self.run_witness()
