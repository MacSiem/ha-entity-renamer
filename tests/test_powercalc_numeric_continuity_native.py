"""G5 diagnostic flow + exact full-Decimal RED; owner current-only FIFO.

Normal profile producers and the existing unchanged-ORIGINAL G4 reload only.
No native field assignment, synthetic Event/State/Task or expected counter seed.
"""
import asyncio
from decimal import Decimal
import json
import unittest

import test_native_powercalc_accepted_candidate as accepted


@unittest.skipUnless(accepted.fixture.HAS_HA, 'requires actual supported HA')
class PowercalcNumericContinuityNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = accepted.NativePowercalcAcceptedCandidateTests.namespace_source_bytes
    asyncSetUp = accepted.NativePowercalcAcceptedCandidateTests.asyncSetUp
    _setup_real_vendor = accepted.NativePowercalcAcceptedCandidateTests._setup_real_vendor
    _flush_documents = accepted.NativePowercalcAcceptedCandidateTests._flush_documents
    guard = accepted.NativePowercalcAcceptedCandidateTests.guard
    module = accepted.NativePowercalcAcceptedCandidateTests.module
    prepare_candidate = accepted.NativePowercalcAcceptedCandidateTests.prepare_candidate
    chain_root = accepted.NativePowercalcAcceptedCandidateTests.chain_root

    async def ready(self):
        await self.chain_root()
        await self.sources[0].async_turn_on(brightness=255)
        await self.f.hass.async_block_till_done()
        # Qualify an actual nonzero native tick with more precision than the
        # public rounded counter. No calibration, timer/time or State seeding.
        energies = (self.static_energy, self.dynamic_energy, self.subtract_energy)
        async with asyncio.timeout(8):
            while not (all(type(e._state) is Decimal and e._state.is_finite()
                           and type(e._last_valid_state) is Decimal and e._last_valid_state.is_finite()
                           for e in energies)
                    and type(self.static_energy._state) is Decimal
                    and self.static_energy._state.is_finite()
                    and self.static_energy._state > 0
                    and self.static_energy.native_value
                    and self.static_energy._state != round(self.static_energy._state, 6)
                    and self.static_energy._last_integration_trigger.name == 'TimeElapsed'):
                await asyncio.sleep(0.02)
        # Missing product API is an explicit RED after genuine preparation.
        self.numeric = self.module('powercalc_numeric_continuity_probe')

    async def collect(self, *, gap_event=False):
        await self.ready()
        completion = self.f.hass.loop.create_future() if gap_event else None
        if completion is not None:
            # Consume an error even if the product call independently refuses;
            # awaiting this same real completion still raises the original error.
            completion.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        started = False
        async def real_producer_after_power_removal(event):
            nonlocal started
            if (not gap_event or started or event.data.get('entity_id') != self.static_power.entity_id
                    or event.data.get('new_state') is not None):
                return
            started = True
            try:
                # Real Light API, scheduled normally by native EventBus. Never
                # invoke a retained calculator/bridge/monitor or create a State.
                await self.sources[0].async_turn_on(brightness=1)
            except BaseException as error:
                completion.set_exception(error)
            else:
                completion.set_result(None)
        remove = self.f.hass.bus.async_listen('state_changed', real_producer_after_power_removal)
        try:
            result = await self.numeric.async_capture_powercalc_numeric_reload(
                self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard)
            if gap_event:
                async with asyncio.timeout(8):
                    await asyncio.shield(completion)
                self.assertTrue(started)
        finally:
            remove()
        self.assertEqual(result.evidence_sha256, self.evidence.sha256)
        self.assertEqual(result.original_runtime_token, self.original_runtime.structural_token)
        self.assertEqual(result.chain_head, self.chain.reconstruct_inputs().head)
        self.assertFalse(result.continuity_verified)
        self.assertFalse(result.counter_ownership_verified)
        self.assertFalse(result.migration_ready)
        rows = json.loads(result.trace_json)
        print(json.dumps({'kind': 'powercalc_numeric_observation', 'trace': rows,
                          'departures': json.loads(result.lifecycle.actual_departures_json),
                          'instance_pairs': result.lifecycle.instance_pairs,
                          'continuity_verified': False}))
        return result, rows

    async def test_actual_group_cleanup_restore_delta_and_native_clocks_observation(self):
        result, rows = await self.collect(gap_event=True)
        self.assertTrue(result.lifecycle.entity_lifecycle_verified)
        self.assertFalse(result.lifecycle.reload_ownership_verified)
        self.assertTrue(rows)
        self.assertEqual([r['seq'] for r in rows], list(range(len(rows))))
        for tag in ('store_cleanup', 'group_restore', 'group_delta', 'store_set',
                    'schedule', 'integration_added', 'fire'):
            self.assertTrue(any(r['tag'] == tag for r in rows), tag)
        clean = [r for r in rows if r['tag'] == 'store_cleanup']
        self.assertTrue(any(r['phase'] == 'start' for r in clean))
        self.assertTrue(any(r['phase'] == 'return' for r in clean))
        pairs = {eid: (old, new) for eid, old, new in result.lifecycle.instance_pairs}
        for entity in (self.static_energy, self.dynamic_energy, self.subtract_energy):
            entries = [r for r in rows if r['tag'] == 'schedule' and r['phase'] == 'start'
                       and r['snapshot']['process_id'] == pairs[entity.entity_id][1]
                       and 'IntegrationSensor.async_added_to_hass' in r['parents']]
            self.assertTrue(entries, entity.entity_id)
            self.assertEqual(entries[0]['actual_old_engine']['process_id'], pairs[entity.entity_id][0])

    async def test_exact_full_decimal_is_transferred_before_first_native_schedule_RED(self):
        # Deliberate actual numerical RED until the typed retained-engine
        # transfer exists. This is NOT expectedFailure and has no tolerance.
        result, rows = await self.collect()
        eid = self.static_energy.entity_id
        old_id, new_id = next((old, new) for name, old, new in result.lifecycle.instance_pairs if name == eid)
        entries = [r for r in rows if r['tag'] == 'schedule' and r['phase'] == 'start'
                   and r['snapshot']['process_id'] == new_id
                   and 'IntegrationSensor.async_added_to_hass' in r['parents']]
        self.assertTrue(entries, 'missing actual first BASE schedule seam')
        seam = entries[0]
        old, new = seam['actual_old_engine'], seam['snapshot']
        self.assertEqual(old['process_id'], old_id)
        full = Decimal(old['full']['decimal'])
        self.assertNotEqual(full, round(full, 6), 'qualifying full-precision native engine was not retained')
        self.assertEqual(Decimal(new['full']['decimal']), full,
                         'native restore lost exact actual old-engine Decimal before first schedule')
        self.assertEqual(new['last_valid'], old['last_valid'])
        self.assertEqual(new['anchor'], old['anchor'])
        self.assertEqual(new['trigger'], old['trigger'])
