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

    async def collect(self, *, gap_event=False, exact_fields=False,
                      retain_producers=False, gap_selector=False):
        if gap_selector:
            # Real native preparation: warm a profile through the public Select
            # producer and return it to ORIGINAL before chain capture. No cache
            # entry/strategy replacement. Cold/in-flight handoff may refuse.
            await self.selector.async_select_option('25%')
            await self.f.hass.async_block_till_done()
            await self.selector.async_select_option('Disabled')
            await self.f.hass.async_block_till_done()
        await self.ready()
        completion = self.f.hass.loop.create_future() if gap_event else None
        if completion is not None:
            # Consume an error even if the product call independently refuses;
            # awaiting this same real completion still raises the original error.
            completion.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        started = False
        removed_power_id = (self.dynamic_power if gap_selector else self.static_power).entity_id
        async def real_producer_after_power_removal(event):
            nonlocal started
            state = event.data.get('new_state')
            # Native Entity.async_remove keeps a registered entity as an
            # unavailable restored State; it does not necessarily remove it.
            departed = (state is None or (state.state == 'unavailable'
                                          and state.attributes.get('restored') is True))
            if (not gap_event or started or event.data.get('entity_id') != removed_power_id
                    or not departed):
                return
            started = True
            try:
                # Real Light API, scheduled normally by native EventBus. Never
                # invoke a retained calculator/bridge/monitor or create a State.
                if gap_selector:
                    await self.selector.async_select_option('25%')
                else:
                    await self.sources[0].async_turn_on(brightness=1)
            except BaseException as error:
                completion.set_exception(error)
            else:
                completion.set_result(None)
        remove = self.f.hass.bus.async_listen('state_changed', real_producer_after_power_removal)
        try:
            result = await self.numeric.async_capture_powercalc_numeric_reload(
                self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard,
                exact_fields=exact_fields, retain_producers=retain_producers)
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

    async def test_actual_source_change_keeps_native_W_calculator_live_during_reload_RED(self):
        # Actual Light change in the native unload gap must still reach the
        # original real W calculator; recomputing only after setup loses time.
        result, rows = await self.collect(gap_event=True)
        old_id, new_id = next((old, new) for name, old, new in result.lifecycle.instance_pairs
                              if name == self.static_power.entity_id)
        emissions = [r for r in rows if r['tag'] == 'fire' and r['phase'] == 'start'
                     and r.get('entity_id') == self.yaml_source
                     and r.get('new') is not None
                     and r['new']['attributes'].get('brightness') == 1]
        self.assertEqual(len(emissions), 1, 'one actual Light brightness change')
        source = emissions[0]
        new_calls = [r for r in rows if r['tag'] == 'power_source'
                     and r['phase'] == 'start' and r['snapshot']['process_id'] == new_id]
        self.assertTrue(new_calls, 'actual replacement calculator was not invoked')
        retained = [r for r in rows if r['tag'] == 'power_calculate'
                    and r['phase'] == 'return' and r['snapshot']['process_id'] == old_id
                    and source['seq'] < r['seq'] < new_calls[0]['seq']
                    and r.get('state', {}).get('process_id') == source['new']['process_id']]
        self.assertTrue(retained, 'actual source event had no retained native W calculation in unload gap')
        self.assertTrue(all(Decimal(r['returned_decimal']['decimal']).is_finite() for r in retained))

    async def test_typed_exact_fields_match_live_engine_before_first_BASE_schedule(self):
        result, rows = await self.collect(exact_fields=True)
        self.assertIsNotNone(result.exact_fields)
        receipt = result.exact_fields
        self.assertTrue(receipt.exact_fields_transfer_verified)
        for flag in ('continuity_verified', 'counter_ownership_verified',
                     'reload_ownership_verified', 'migration_ready', 'family_complete'):
            self.assertFalse(getattr(receipt, flag), flag)
        self.assertEqual(receipt.evidence_sha256, self.evidence.sha256)
        self.assertEqual(receipt.original_runtime_token, self.original_runtime.structural_token)
        self.assertEqual(receipt.chain_head, self.chain.reconstruct_inputs().head)
        transferred = {row['entity_id']: row for row in json.loads(receipt.transfers_json)}
        pairs = {name: (old, new) for name, old, new in result.lifecycle.instance_pairs}
        for entity in (self.static_energy, self.dynamic_energy, self.subtract_energy):
            name = entity.entity_id
            old_id, new_id = pairs[name]
            seams = [row for row in rows if row['tag'] == 'schedule'
                     and row['phase'] == 'start'
                     and row['snapshot']['process_id'] == new_id
                     and 'IntegrationSensor.async_added_to_hass' in row['parents']]
            self.assertTrue(seams, name)
            old, new = seams[0]['actual_old_engine'], seams[0]['snapshot']
            self.assertEqual(old['process_id'], old_id)
            self.assertEqual(Decimal(new['full']['decimal']), Decimal(old['full']['decimal']))
            self.assertEqual(new['last_valid'], old['last_valid'])
            self.assertEqual(new['anchor'], old['anchor'])
            self.assertEqual(new['trigger'], old['trigger'])
            local = transferred[name]
            self.assertEqual(local['old_live'], local['new_after'])
            self.assertEqual(local['new_after'], local['base_start'])
            self.assertEqual(local['base_start'], local['base_return'])
        static = transferred[self.static_energy.entity_id]
        self.assertNotEqual(Decimal(static['old_live']['full']),
                            round(Decimal(static['old_live']['full']), 6))
        # This is a local effect receipt. It cannot close G5 continuity.
        self.assertFalse(result.continuity_verified)

    async def test_typed_exact_fields_does_not_claim_W_gap_continuity(self):
        result, rows = await self.collect(gap_event=True, exact_fields=True)
        self.assertTrue(result.exact_fields.exact_fields_transfer_verified)
        self.assertFalse(result.continuity_verified)
        self.assertFalse(result.counter_ownership_verified)
        self.assertFalse(result.migration_ready)
        old_id, new_id = next((old, new) for name, old, new in result.lifecycle.instance_pairs
                              if name == self.static_power.entity_id)
        source = [row for row in rows if row['tag'] == 'fire' and row['phase'] == 'start'
                  and row.get('entity_id') == self.yaml_source and row.get('new') is not None
                  and row['new']['attributes'].get('brightness') == 1]
        self.assertEqual(len(source), 1)
        first_new = min(row['seq'] for row in rows if row['tag'] == 'power_source'
                        and row['phase'] == 'start' and row['snapshot']['process_id'] == new_id)
        retained = [row for row in rows if row['tag'] == 'power_calculate'
                    and row['phase'] == 'return' and row['snapshot']['process_id'] == old_id
                    and source[0]['seq'] < row['seq'] < first_new
                    and row.get('state', {}).get('process_id') == source[0]['new']['process_id']]
        self.assertFalse(retained, 'this prerequisite must not introduce retained W calculation')

    async def test_exact_fields_rejects_guard_revocation_after_real_transfer(self):
        await self.ready()
        exact = self.module('powercalc_exact_fields')
        from homeassistant.helpers import entity_platform as ep
        old = self.static_energy
        def lease():
            self.guard()
            for platform in ep.async_get_platforms(self.f.hass, 'powercalc'):
                candidate = platform.entities.get(old.entity_id)
                if candidate is not None and candidate is not old and candidate._state is old._state:
                    raise ValueError('test_existing_admin_lease_revoked_after_transfer')
        with self.assertRaises(ValueError) as caught:
            await exact.async_reload_with_powercalc_exact_fields(
                self.f.hass, self.chain, self.evidence, self.original_runtime, guard=lease)
        # The effect is reported; no rollback of native data is asserted.
        notes = getattr(caught.exception, '__notes__', ())
        effects = [note for note in notes if note.startswith('powercalc_exact_fields_effects=')]
        self.assertTrue(effects)
        self.assertFalse(any(note.startswith('powercalc_exact_fields_effects=0;') for note in effects))

    async def test_exact_fields_rejects_untyped_policy_before_native_effect(self):
        await self.ready()
        witness = self.module('powercalc_reload_witness')
        with self.assertRaisesRegex(ValueError, 'typed_exact_fields_requires_global_owner'):
            await witness.async_witness_unchanged_powercalc_reload(
                self.f.hass, self.chain.original_receipt, self.evidence, self.original_runtime,
                guard=self.guard, _manage_global_rename=True, _exact_fields=object())

    async def _assert_retained_result(self, *, selector=False):
        result, rows = await self.collect(gap_event=True, exact_fields=True,
                                          retain_producers=True, gap_selector=selector)
        receipt = result.retained_producers
        self.assertIsNotNone(receipt)
        self.assertTrue(receipt.retained_calculations_verified)
        self.assertFalse(receipt.continuity_verified)
        self.assertFalse(receipt.counter_ownership_verified)
        self.assertFalse(receipt.migration_ready)
        old = self.dynamic_power if selector else self.static_power
        source = self.selector if selector else self.sources[0]
        old_id, new_id = next((a, b) for name, a, b in result.lifecycle.instance_pairs
                              if name == old.entity_id)
        outcomes = [outcome for outcome in receipt.calculations if outcome.producer is old
                    and outcome.input_state.entity_id == source.entity_id]
        self.assertEqual(len(outcomes), 1)
        outcome = outcomes[0]
        self.assertIs(outcome.event.data['new_state'], outcome.input_state)
        self.assertIs(outcome.old_profile, old._power_profile)
        self.assertIs(outcome.actual_strategy, old._strategy_instance)
        self.assertEqual(outcome.ownership_phase, 'retained_removed')
        self.assertEqual(outcome.native_power, old._power)
        self.assertTrue(outcome.available)
        self.assertTrue(outcome.calculated_value.is_finite())
        self.assertTrue(outcome.native_power.is_finite())
        self.assertLessEqual(outcome.calculation_returned_at, outcome.update_started_at)
        self.assertLessEqual(outcome.update_started_at, outcome.update_returned_at)
        self.assertLessEqual(outcome.update_returned_at, outcome.completed_at)
        self.assertTrue(outcome.native_value_changed)
        self.assertTrue(outcome.native_write_attempted)
        self.assertIsNotNone(outcome.native_write_returned_at)
        first_new = min(row['seq'] for row in rows if row['tag'] == 'power_source'
                        and row['phase'] == 'start' and row['snapshot']['process_id'] == new_id)
        retained = [row for row in rows if row['tag'] == 'power_calculate'
                    and row['phase'] == 'return' and row['snapshot']['process_id'] == old_id
                    and row['seq'] < first_new
                    and row.get('state', {}).get('process_id') == id(outcome.input_state)]
        self.assertEqual(len(retained), 1, 'actual retained calculation before new producer')
        if selector:
            self.assertEqual(outcome.input_state.state, '25%')
            self.assertIsNot(outcome.input_state, outcome.resolved_light_state)
            self.assertEqual(outcome.resolved_light_state.entity_id, self.gui_source)
        else:
            self.assertEqual(outcome.input_state.attributes['brightness'], 1)
            self.assertIs(outcome.input_state, outcome.resolved_light_state)
        operations = json.loads(receipt.native_operations_json)
        for name in (self.static_power.entity_id, self.dynamic_power.entity_id):
            self.assertEqual(sum(row[:2] == ['bridge_add', name] for row in operations), 1)
            self.assertEqual(sum(row[:2] == ['bridge_remove', name] for row in operations), 1)
            self.assertEqual(sum(row[:2] == ['native_handoff', name] for row in operations), 1)
        self.assertFalse(result.continuity_verified)
        self.assertFalse(result.counter_ownership_verified)
        return outcome

    async def test_typed_retained_producer_actual_brightness_calculates_before_new_W(self):
        await self._assert_retained_result()

    async def test_typed_retained_producer_actual_selector_calculates_before_new_W(self):
        await self._assert_retained_result(selector=True)

    def _assert_no_retained_bridges_or_tasks(self):
        from homeassistant.helpers import event
        data = self.f.hass.data.get(event._TRACK_STATE_CHANGE_DATA)
        if data is not None:
            for jobs in data.callbacks.values():
                for job in jobs:
                    self.assertNotEqual(getattr(job.target, '__qualname__', ''),
                        '_RetainedProducerOwner.removed.<locals>.bridge')
        self.assertFalse([task for task in asyncio.all_tasks(self.f.hass.loop)
                          if not task.done() and task.get_name().startswith('renamer.powercalc.retained.')])

    async def test_typed_retained_producer_cancellation_after_actual_gap_input_cleans_scope(self):
        await self.ready()
        completed = self.f.hass.loop.create_future()
        seen = False
        task = None
        async def actual_input_then_cancel(event):
            nonlocal seen
            state = event.data.get('new_state')
            if (seen or event.data.get('entity_id') != self.static_power.entity_id
                    or state is None or state.state != 'unavailable'
                    or state.attributes.get('restored') is not True):
                return
            seen = True
            try:
                await self.sources[0].async_turn_on(brightness=1)
                task.cancel()
            except BaseException as error:
                completed.set_exception(error)
            else:
                completed.set_result(None)
        remove = self.f.hass.bus.async_listen('state_changed', actual_input_then_cancel)
        try:
            task = asyncio.create_task(self.numeric.async_capture_powercalc_numeric_reload(
                self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard,
                exact_fields=True, retain_producers=True))
            with self.assertRaises(asyncio.CancelledError) as caught:
                await task
            async with asyncio.timeout(8):
                await completed
            self.assertTrue(seen)
            self.assertTrue(any(note.startswith('powercalc_retained_producer_partial_results=')
                                for note in getattr(caught.exception, '__notes__', ())))
            self._assert_no_retained_bridges_or_tasks()
        finally:
            remove()

    async def test_typed_retained_producer_foreign_physical_identity_refuses_and_cleans_scope(self):
        await self.ready()
        completed = self.f.hass.loop.create_future()
        seen = False
        before = self.static_power._power
        async def actual_foreign_identity(event):
            nonlocal seen
            state = event.data.get('new_state')
            if (seen or event.data.get('entity_id') != self.static_power.entity_id
                    or state is None or state.state != 'unavailable'
                    or state.attributes.get('restored') is not True):
                return
            seen = True
            try:
                # A real registry takeover, followed by a real Light write.
                # No vendor method, bridge, Event or W State is invoked/created.
                self.f.entity.async_update_entity(self.yaml_source, new_unique_id='foreign-native-input')
                await self.sources[0].async_turn_on(brightness=1)
            except BaseException as error:
                completed.set_exception(error)
            else:
                completed.set_result(None)
        remove = self.f.hass.bus.async_listen('state_changed', actual_foreign_identity)
        try:
            with self.assertRaises(ValueError):
                await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard,
                    exact_fields=True, retain_producers=True)
            async with asyncio.timeout(8):
                await completed
            self.assertTrue(seen)
            self.assertEqual(self.static_power._power, before)
            self._assert_no_retained_bridges_or_tasks()
        finally:
            remove()

    async def test_typed_retained_producer_quiet_reload_has_no_calculation_claim(self):
        result, rows = await self.collect(exact_fields=True, retain_producers=True)
        receipt = result.retained_producers
        self.assertTrue(receipt.retained_producer_lifecycle_verified)
        self.assertEqual(receipt.calculations, ())
        self.assertFalse(receipt.retained_calculations_verified)
        self.assertFalse(receipt.continuity_verified)
        self._assert_no_retained_bridges_or_tasks()

    async def test_typed_retained_producer_foreign_instance_dispatch_refuses_before_call(self):
        await self.ready()
        completed = self.f.hass.loop.create_future()
        seen, calls = False, []
        original = self.static_power._has_valid_state
        def foreign_dispatch(*args):
            calls.append(args)
            return True
        async def actual_input_after_dispatch_drift(event):
            nonlocal seen
            state = event.data.get('new_state')
            if (seen or event.data.get('entity_id') != self.static_power.entity_id
                    or state is None or state.state != 'unavailable'
                    or state.attributes.get('restored') is not True):
                return
            seen = True
            try:
                # Negative only: a foreign instance method must never run.
                # The input itself still comes from the real native Light.
                self.static_power._has_valid_state = foreign_dispatch
                await self.sources[0].async_turn_on(brightness=1)
            except BaseException as error:
                completed.set_exception(error)
            else:
                completed.set_result(None)
        remove = self.f.hass.bus.async_listen('state_changed', actual_input_after_dispatch_drift)
        try:
            with self.assertRaises(ValueError):
                await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard,
                    exact_fields=True, retain_producers=True)
            async with asyncio.timeout(8):
                await completed
            self.assertTrue(seen)
            self.assertEqual(calls, [])
            self._assert_no_retained_bridges_or_tasks()
        finally:
            remove()
            self.static_power._has_valid_state = original
