"""Actual same-ID clock handoff prerequisite; owner runs via FIFO on both pins.

Only real producer/timer/lifecycle calls create arithmetic inputs. Expected
counts exist only in assertions. This is not source-ID or complete product AC.
"""
import asyncio
from datetime import timedelta
from decimal import Decimal
import importlib
import logging
from types import MappingProxyType
import unittest

import test_native_input_baseline as fixture
import test_native_integration_restore as native_fixture

try:
    continuity = importlib.import_module(fixture.PACKAGE + '.native_integration_continuity')
except ModuleNotFoundError as error:
    if error.name != fixture.PACKAGE + '.native_integration_continuity':
        raise
    continuity = None  # The initial RED is the missing actual product API.


@unittest.skipUnless(fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationContinuityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native_fixture.NativeIntegrationRestoreTests.asyncSetUp

    async def test_actual_timer_handoff_preserves_continuous_anchor_and_deadline(self):
        await self._run(gap_report=False)

    async def test_retained_native_callback_consumes_real_gap_report_once(self):
        await self._run(gap_report=True)

    async def test_mutated_actual_changed_old_state_refuses_transfer(self):
        await self._run(gap_report=False, regression='old_state')

    async def test_mutated_actual_report_old_timestamp_refuses_transfer(self):
        await self._run(gap_report=False, regression='old_last_reported')

    async def test_mutated_actual_report_new_timestamp_refuses_transfer(self):
        if importlib.import_module('homeassistant.const').__version__ != '2026.10.2':
            self.skipTest('minimum native handler has no explicit last_reported input')
        await self._run(gap_report=False, regression='last_reported')

    async def test_duplicate_actual_unsubscribe_still_releases_owned_resources(self):
        await self._run(gap_report=False, regression='partial_unsubscribe')

    async def test_emission_close_error_still_releases_owned_monitor(self):
        await self._run(gap_report=False, regression='emission_close')

    async def _run(self, *, gap_report, regression=None):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.components.integration.config_flow import ConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.core import callback
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.event import (
            async_track_state_change_event, async_track_state_report_event)
        activation = importlib.import_module(fixture.PACKAGE + '.native_helper_activation_lease')
        prepared = importlib.import_module(fixture.PACKAGE + '.prepared')
        lifecycle = importlib.import_module(fixture.PACKAGE + '.native_helper_lifecycle')
        hass = self.f.hass

        class Producer(SensorEntity):
            _attr_name = 'Continuity clock producer'
            _attr_unique_id = 'continuity-clock-producer'
            _attr_should_poll = False
            _attr_native_unit_of_measurement = 'W'
            _attr_native_value = Decimal('10')

        component = EntityComponent(logging.getLogger(__name__), 'sensor', hass,
                                    timedelta(seconds=60))
        producer = Producer()
        await component.async_add_entities([producer])
        entry = ConfigEntry(domain='integration', title='Continuity clock prerequisite',
            data={}, options={'name': 'Continuity clock prerequisite',
                'source': producer.entity_id, 'method': 'left', 'unit_time': 's',
                'round': None, 'max_sub_interval': {'seconds': 1}},
            version=ConfigFlowHandler.VERSION, minor_version=ConfigFlowHandler.MINOR_VERSION,
            source='user', discovery_keys=MappingProxyType({}), subentries_data=[],
            unique_id='continuity-clock-prerequisite')
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()

        def current():
            rows = [e for p in async_get_platforms(hass, 'integration')
                    if p.config_entry is entry for e in p.entities.values()]
            self.assertEqual(len(rows), 1)
            return rows[0]

        old = current()
        first_tick = asyncio.get_running_loop().create_future()
        @callback
        def observe_timer(event):
            if old._state is not None and old._state > 0 and not first_tick.done():
                first_tick.set_result(True)
        unsubscribe = async_track_state_change_event(hass, old.entity_id, observe_timer)
        try:
            await asyncio.wait_for(first_tick, timeout=10)
        finally:
            unsubscribe()
        await hass.async_block_till_done()
        self.assertEqual(old._last_integration_trigger.name, 'TimeElapsed')
        self.assertIsNotNone(continuity, 'actual Integration clock adapter missing')
        initial_full, initial_anchor = old._state, old._last_integration_time
        registry = er.async_get(hass)
        helper_row, source_row = registry.async_get(old.entity_id), registry.async_get(producer.entity_id)
        self.assertGreater(initial_full, 0)
        self.assertEqual(initial_full, old.native_value)
        preparations = prepared.native_prepared_store(hass)
        await preparations.initialize()
        store = lifecycle.native_helper_lifecycle_store(hass, preparations)
        await store.initialize()

        def guard():
            self.assertIs(hass.config_entries.async_get_entry(entry.entry_id), entry)
            self.assertIs(registry.async_get(old.entity_id), helper_row)
            self.assertIs(registry.async_get(producer.entity_id), source_row)
            return True

        lease = await activation.NativeHelperActivationLease.acquire(
            hass, hass.config_entries, (entry,), guard=guard)
        scope = None
        emergency = None
        mutation_unsubscribe = None
        mutation = {'armed': False, 'seen': []}
        # Freeze only a genuine producer timestamp. No synthesized clock input.
        original_reported = hass.states.get(producer.entity_id).last_reported
        if regression == 'old_state':
            class OtherProducer(Producer):
                _attr_name = 'Continuity independent endpoint'
                _attr_unique_id = 'continuity-independent-endpoint'
                _attr_native_value = Decimal('999')
            other = OtherProducer()
            await component.async_add_entities([other])
            other_state = hass.states.get(other.entity_id)
            self.assertIsNotNone(other_state)
            self.assertNotEqual(other_state.state, hass.states.get(producer.entity_id).state)
        if regression in ('old_state', 'old_last_reported', 'last_reported'):
            @callback
            def mutate_actual_event(event):
                if not mutation['armed']:
                    return
                data = event.data
                replacement = other_state if regression == 'old_state' else original_reported
                mutation['seen'].append((event, data[regression], replacement))
                data[regression] = replacement
            track = (async_track_state_change_event if regression == 'old_state'
                     else async_track_state_report_event)
            # Real keyed listener registered BEFORE the product bridge. Native
            # emission/dispatch and the Event itself are never replaced.
            mutation_unsubscribe = track(hass, producer.entity_id, mutate_actual_event)
        first_new_states = []
        profile = __import__('sys').getprofile()
        @callback
        def observe_new(event):
            if (scope is not None and scope._new is not None
                    and event.data.get('new_state') is not None
                    and event.data['new_state'].state not in ('unknown', 'unavailable')):
                first_new_states.append(event.data['new_state'].state)
        unsubscribe = async_track_state_change_event(hass, old.entity_id, observe_new)
        try:
            scope = await continuity.NativeIntegrationContinuity.start(
                hass, entry, lease, store, guard=guard)
            await scope.unload()
            self.assertEqual(entry.state.value, 'not_loaded')
            placeholder = hass.states.get(old.entity_id)
            self.assertEqual(placeholder.state, 'unavailable')
            if regression is not None:
                emergency = self._capture_cleanup(scope)
                if regression in ('old_state', 'old_last_reported', 'last_reported'):
                    if regression != 'old_state':
                        # First real same-value report moves native arithmetic
                        # from TimeElapsed to StateEvent, on both pinned Core.
                        producer.async_write_ha_state()
                        await hass.async_block_till_done()
                        self.assertEqual(old._last_integration_trigger.name, 'StateEvent')
                        self.assertGreater(hass.states.get(producer.entity_id).last_reported,
                                           original_reported)
                    mutation['armed'] = True
                    if regression == 'old_state':
                        producer._attr_native_value = Decimal('11')
                    producer.async_write_ha_state()
                    await hass.async_block_till_done()
                    self.assertEqual(len(mutation['seen']), 1)
                    event, original, replacement = mutation['seen'][0]
                    if regression == 'old_state':
                        self.assertIs(replacement, hass.states.get(other.entity_id))
                        self.assertIsNot(original, replacement)
                        self.assertEqual(original.entity_id, producer.entity_id)
                    else:
                        self.assertGreater(original, replacement)
                    self.assertIs(event.data[regression], replacement)
                    # Baseline wrongly accepts the same authentic Event after
                    # its unchecked arithmetic input has been changed.
                    with self.assertRaises(ValueError):
                        await scope.setup()
                    self.assertIsNotNone(scope._error)
                    self.assertIsNone(scope._transferred)
                    self.assertIsNone(scope._new)
                    self.assertFalse(hasattr(scope, 'receipt'))
                    self.assertFalse(any(
                        store.read(key)['payload'].get('kind') == 'integration_clock_receipt'
                        for key in store.rows))
                    self.assertEqual(entry.state.value, 'not_loaded')
                elif regression == 'partial_unsubscribe':
                    # Actual native removal succeeds once. Retain that exact
                    # unsubscribe in the product list, as after a partial cut.
                    scope._unsubscribes[0]()
                    with self.assertRaisesRegex(ValueError, r'list\.remove'):
                        scope.close()
                    self._assert_owned_resources_released(scope, emergency)
                else:
                    emissions = scope._emissions
                    original_close = emissions.close
                    def close_then_raise():
                        original_close()
                        raise RuntimeError('regression_emission_close_after_release')
                    # Only this Renamer witness instance is wrapped; HA/native
                    # functions and all native event/timer writers are intact.
                    emissions.close = close_then_raise
                    try:
                        with self.assertRaisesRegex(RuntimeError,
                                'regression_emission_close_after_release'):
                            scope.close()
                        self._assert_owned_resources_released(scope, emergency)
                    finally:
                        emissions.close = original_close
                return
            if gap_report:
                before_report = old._state
                producer.async_write_ha_state()  # Real same-value state_reported.
                await hass.async_block_till_done()
                self.assertGreater(old._state, before_report)
                self.assertEqual(scope._bridge_calls, 1)
                self.assertIs(hass.states.get(old.entity_id), placeholder)
            restored = await scope.setup()
            await hass.async_block_till_done()
            self.assertIsNot(restored, old)
            self.assertEqual((restored.entity_id, restored.unique_id),
                             (old.entity_id, old.unique_id))
            self.assertIs(registry.async_get(restored.entity_id), helper_row)
            transfer = scope._transferred
            self.assertEqual(Decimal(transfer.full), old._state)
            self.assertEqual(Decimal(transfer.last_valid), old._last_valid_state)
            self.assertEqual(transfer.time, old._last_integration_time.isoformat())
            self.assertEqual(transfer.trigger, old._last_integration_trigger.name)
            self.assertEqual(Decimal(first_new_states[0]), Decimal(transfer.full))
            self.assertTrue(scope._old_cancelled.cancelled())
            # Compare actual native TimerHandle deadline, never loop.time()+1.
            native_handle, native_action, endpoint = scope._timer(restored)
            if restored._last_integration_time.isoformat() == transfer.time:
                self.assertEqual(native_handle.when(), transfer.deadline)
            receipt = store.read(scope.receipt[0])['payload']
            self.assertEqual(receipt['actual_transfer'], continuity.asdict(transfer))
            self.assertEqual(receipt['new_timer_rearms'], 1)
            self.assertEqual(receipt['bridge_calls'], 1 if gap_report else 0)
            # Close only observation/retained scope. The new native timer stays
            # owned by restored's native on_remove cancel callback.
            scope.close()
            self.assertIs(__import__('sys').getprofile(), profile)
            full_before = restored._state
            anchor_before = restored._last_integration_time
            trigger_before = restored._last_integration_trigger.name
            source_before = hass.states.get(producer.entity_id)
            previous_report = source_before.last_reported
            producer._attr_native_value = Decimal('11')
            producer.async_write_ha_state()
            await hass.async_block_till_done()
            source_after = hass.states.get(producer.entity_id)
            actual_delta = restored._state - full_before
            left_time = anchor_before if trigger_before == 'TimeElapsed' else previous_report
            continuous_delta = Decimal((source_after.last_reported - left_time).total_seconds()) * 10
            # Native Decimal addition rounds in its current context; subtraction
            # of that accumulated value is not the exact inverse operation.
            self.assertEqual(restored._state, full_before + continuous_delta)
            self.assertEqual(restored._last_valid_state, restored._state)
            self.assertEqual(Decimal(hass.states.get(restored.entity_id).state), restored._state)
            if not gap_report and trigger_before == 'TimeElapsed':
                # Initial already-integrated interval cannot be charged again.
                naive_reload_delta = Decimal((source_after.last_reported - previous_report).total_seconds()) * 10
                self.assertGreater(naive_reload_delta, actual_delta)
                self.assertGreaterEqual(anchor_before, initial_anchor)
        finally:
            unsubscribe()
            if mutation_unsubscribe is not None:
                mutation_unsubscribe()
            try:
                if scope is not None:
                    if regression is None:
                        scope.close()
                    else:
                        # Runs AFTER the negative assertions, including RED.
                        self._cleanup_regression(scope, emergency)
            finally:
                lease.release()

    def _capture_cleanup(self, scope):
        emissions = importlib.import_module(fixture.PACKAGE + '.native_utility_source_emissions')
        return {
            'handle': scope._timer(scope.old)[0],
            'unsubscribes': tuple(scope._unsubscribes),
            'tool': scope._tool,
            'watched': tuple(scope._watched),
            'emissions_close': scope._emissions.close,
            'emission_tool': emissions._tool,
        }

    def _assert_owned_resources_released(self, scope, owned):
        monitor = __import__('sys').monitoring
        with self.subTest(resource='retained_native_timer'):
            self.assertTrue(owned['handle'].cancelled())
        for kind, key in (('changed', scope.event._TRACK_STATE_CHANGE_DATA),
                          ('reported', scope.event._TRACK_STATE_REPORT_DATA)):
            tracker = scope.hass.data.get(key)
            targets = ([] if tracker is None else
                       [job.target for job in tracker.callbacks.get(scope.source, ())])
            with self.subTest(resource='bridge_' + kind):
                self.assertNotIn(scope._bridges[kind], targets)
        for name in ('tool', 'emission_tool'):
            with self.subTest(resource=name):
                self.assertIsNone(monitor.get_tool(owned[name]))

    def _cleanup_regression(self, scope, owned):
        # A broken baseline must not leak process-global monitor slots or an
        # orphaned retained timer into later tests. None of these operations
        # supplies authority, arithmetic, native fields or a successful oracle.
        try:
            scope.close()
        except (ValueError, RuntimeError):
            pass
        if owned is None:
            return
        # Reports may have naturally rearmed a newer retained native handle.
        if scope._retained:
            handle = getattr(scope.old._max_sub_interval_exceeded_callback, '__self__', None)
            if isinstance(handle, asyncio.TimerHandle):
                handle.cancel()
        owned['handle'].cancel()
        for unsubscribe in owned['unsubscribes']:
            try:
                unsubscribe()
            except (ValueError, KeyError):
                pass  # An already-removed native keyed job, cleanup only.
        owned['emissions_close']()
        monitor = __import__('sys').monitoring
        tool = owned['tool']
        if monitor.get_tool(tool) == continuity._NAME:
            for code in owned['watched']:
                monitor.set_local_events(tool, code, 0)
            monitor.register_callback(tool, monitor.events.PY_START, None)
            monitor.register_callback(tool, monitor.events.PY_RETURN, None)
            monitor.free_tool_id(tool)
