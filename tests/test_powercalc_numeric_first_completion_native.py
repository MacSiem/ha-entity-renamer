"""Numeric-first native continuity; independent LEFT W/kWh calculation.

Runs only with genuine pinned Core/Powercalc and the original public Light.
"""
from decimal import Decimal
import asyncio
import sys
import traceback
import unittest

import test_powercalc_operand_timer_native as timers


@unittest.skipUnless(timers.baseline.prior.accepted.fixture.HAS_HA, 'actual supported HA required')
class NumericFirstContinuityTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = timers.NativePowercalcActualTimerTests.namespace_source_bytes
    asyncSetUp = timers.NativePowercalcActualTimerTests.asyncSetUp
    _setup_real_vendor = timers.NativePowercalcActualTimerTests._setup_real_vendor
    _flush_documents = timers.NativePowercalcActualTimerTests._flush_documents
    guard = timers.NativePowercalcActualTimerTests.guard
    module = timers.NativePowercalcActualTimerTests.module
    prepare_candidate = timers.NativePowercalcActualTimerTests.prepare_candidate
    chain_root = timers.NativePowercalcActualTimerTests.chain_root
    ready = timers.NativePowercalcActualTimerTests.ready
    _no_owned = timers.NativePowercalcActualTimerTests._no_owned

    async def test_public_numeric_first_observed_before_energy_setup(self):
        await self.ready()
        facts = timers._TimerFacts(self)
        await facts.prepare()
        facts.normal_ownership_checks = False
        facts.allow_numeric_first = True
        attempted = False
        source_task = None
        published_state = published_power = None
        stimulus_error = None
        remove = None
        from homeassistant.core import callback
        @callback
        def actual_initial_power(event):
            nonlocal attempted, source_task, published_state, published_power, stimulus_error
            state = event.data.get('new_state')
            if (attempted or event.data.get('entity_id') != self.static_power.entity_id
                    or state is None or state.state != 'unknown'):
                return
            try:
                from homeassistant.helpers.entity_platform import async_get_platforms
                powers = [e for platform in async_get_platforms(self.f.hass, 'powercalc')
                          for e in platform.entities.values() if e.entity_id == self.static_power.entity_id]
                if not powers or powers[0] is self.static_power:
                    return
                producer = powers[0]
                assert producer.registry_entry is self.static_power.registry_entry
                assert producer._platform_state.name == 'ADDED' and producer._power is None
                attempted = True
                source_task = self.f.hass.async_create_task(
                    self.sources[0].async_turn_on(brightness=1),
                    'test.public-source-from-native-state-change', eager_start=True)
                assert source_task.done() and source_task.exception() is None
            except BaseException as error:
                stimulus_error = traceback.format_exc()
        try:
            facts.install()
            remove = self.f.hass.bus.async_listen('state_changed', actual_initial_power)
            result = await self.numeric.async_capture_powercalc_numeric_reload(
                self.f.hass, self.chain, self.evidence, self.original_runtime,
                guard=self.guard, exact_fields=True, retain_producers=True, operand_clock=True)
            self.assertIsNone(stimulus_error, repr(stimulus_error))
            self.assertTrue(attempted, 'fixture did not expose the real numeric-first setup seam')
            self.assertIsNotNone(source_task)
            self.assertTrue(source_task.done(), 'native source calculation remained inconclusive')
            self.assertIsNone(source_task.exception())
            self.assertIsNone(facts.error, repr(facts.error))
            name = self.static_energy.entity_id
            self.assertIn(name, facts.numeric, 'actual numeric W emission was not observed')
            published_state = facts.numeric[name][1]
            published_power = facts.numeric[name][4]._power
            from homeassistant.core import State
            self.assertIs(type(published_state), State)
            self.assertIs(type(published_power), Decimal)
            self.assertTrue(published_power.is_finite())
            self.assertEqual(Decimal(published_state.state), published_power,
                             'public source must finish a real native numeric W write')
            self.assertIn(name, facts.first_base)
            self.assertIs(facts.first_base[name][1], published_state,
                          'the public API-produced numeric State must exist before first BASE')
            baseline = facts.baselines[name]
            self.assertFalse([row for row in facts.timer_deliveries if row[0] == name],
                             'timer partition requires a separate oracle')
            self.assertEqual((baseline.unit_prefix, baseline.unit_time),
                             (Decimal(1000), Decimal(3600)))
            origin = baseline.anchor if baseline.trigger == 'TimeElapsed' else baseline.source_reported
            end = published_state.last_updated
            self.assertLessEqual(origin, end)
            expected = baseline.full + (Decimal((end - origin).total_seconds())
                                        * baseline.source_power / Decimal(3600000))
            from homeassistant.helpers.entity_platform import async_get_platforms
            current = [entity for platform in async_get_platforms(self.f.hass, 'powercalc')
                       for entity in platform.entities.values() if entity.entity_id == name]
            self.assertEqual(len(current), 1)
            self.assertIsNot(current[0], self.static_energy)
            self.assertIs(current[0].registry_entry, self.static_energy.registry_entry)
            self.assertEqual(current[0]._state, expected)
            self.assertEqual(current[0]._last_valid_state, expected)
            self.assertTrue(result.operand_clock.direct_operand_clock_verified)
            # A subsequent real Light service call must reach the replacement
            # producer and energy listener with the original previous State.
            next_events = []
            @callback
            def record_next(event):
                if event.data.get('entity_id') == self.static_power.entity_id:
                    next_events.append(event)
            remove_next = self.f.hass.bus.async_listen('state_changed', record_next)
            try:
                await self.sources[0].async_turn_on(brightness=64)
                await self.f.hass.async_block_till_done()
            finally:
                remove_next()
            self.assertEqual(len(next_events), 1)
            next_event = next_events[0]
            self.assertIs(next_event.data['old_state'], published_state)
            next_state = next_event.data['new_state']
            self.assertEqual(next_state.entity_id, self.static_power.entity_id)
            self.assertNotEqual(Decimal(next_state.state), published_power)
            self.assertFalse([row for row in facts.timer_deliveries if row[0] == name],
                             'timer partition requires a separate oracle')
            later = expected + (Decimal((next_state.last_updated - end).total_seconds())
                                * published_power / Decimal(3600000))
            self.assertEqual(current[0]._state, later)
            self.assertEqual(current[0]._last_valid_state, later)
            self._no_owned(facts)
        finally:
            if remove is not None:
                remove()
            facts.close()

    async def test_two_public_numeric_samples_before_energy_setup(self):
        await self.ready()
        facts = timers._TimerFacts(self)
        await facts.prepare()
        facts.normal_ownership_checks = False
        facts.allow_numeric_first = True
        attempted = False
        source_tasks = []
        observed = []
        published_state = published_power = None
        stimulus_error = None
        remove = None
        from homeassistant.core import callback
        @callback
        def actual_initial_power(event):
            nonlocal attempted, published_state, published_power, stimulus_error
            state = event.data.get('new_state')
            if (attempted or event.data.get('entity_id') != self.static_power.entity_id
                    or state is None or state.state != 'unknown'):
                return
            try:
                from homeassistant.helpers.entity_platform import async_get_platforms
                powers = [e for platform in async_get_platforms(self.f.hass, 'powercalc')
                          for e in platform.entities.values() if e.entity_id == self.static_power.entity_id]
                if not powers or powers[0] is self.static_power:
                    return
                producer = powers[0]
                assert producer.registry_entry is self.static_power.registry_entry
                assert producer._platform_state.name == 'ADDED' and producer._power is None
                attempted = True
                for brightness in (1, 64):
                    task = self.f.hass.async_create_task(
                        self.sources[0].async_turn_on(brightness=brightness),
                        'test.public-source-before-energy-setup', eager_start=True)
                    assert task.done() and task.exception() is None
                    source_tasks.append(task)
            except BaseException as error:
                stimulus_error = traceback.format_exc()
        @callback
        def observe_power(event):
            state = event.data.get('new_state')
            if (event.data.get('entity_id') == self.static_power.entity_id
                    and state is not None and state.state not in ('unknown', 'unavailable')):
                observed.append(event)
        audit_remove = None
        try:
            facts.install()
            remove = self.f.hass.bus.async_listen('state_changed', actual_initial_power)
            audit_remove = self.f.hass.bus.async_listen('state_changed', observe_power)
            try:
                result = await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime,
                    guard=self.guard, exact_fields=True, retain_producers=True, operand_clock=True)
            except ValueError:
                self.assertIsNone(stimulus_error, repr(stimulus_error))
                self.assertTrue(attempted and len(source_tasks) == 2)
                self.assertTrue(all(task.done() and task.exception() is None for task in source_tasks))
                self.assertEqual(len(observed), 2, 'qualification requires two real W events')
                self.assertIs(observed[1].data['old_state'], observed[0].data['new_state'])
                self.assertNotEqual(observed[0].data['new_state'].state,
                                    observed[1].data['new_state'].state)
                self.assertIsNone(facts.error, repr(facts.error))
                print('QUALIFIED_TWO_W', [(event.data['new_state'].state,
                      event.data['new_state'].last_updated.isoformat()) for event in observed])
                raise
            self.assertIsNone(stimulus_error, repr(stimulus_error))
            self.assertTrue(attempted, 'fixture did not expose the real numeric-first setup seam')
            self.assertEqual(len(source_tasks), 2)
            self.assertTrue(all(task.done() and task.exception() is None for task in source_tasks))
            self.assertEqual(len(observed), 2, 'two genuine W emissions are required')
            self.assertIs(observed[1].data['old_state'], observed[0].data['new_state'])
            self.assertIsNone(facts.error, repr(facts.error))
            name = self.static_energy.entity_id
            self.assertIn(name, facts.numeric, 'actual numeric W emission was not observed')
            first_state = observed[0].data['new_state']
            published_state = observed[1].data['new_state']
            published_power = facts.numeric[name][4]._power
            from homeassistant.core import State
            self.assertIs(type(published_state), State)
            self.assertIs(type(published_power), Decimal)
            self.assertTrue(published_power.is_finite())
            self.assertEqual(Decimal(published_state.state), published_power,
                             'public source must finish a real native numeric W write')
            self.assertIn(name, facts.first_base)
            self.assertIs(facts.first_base[name][1], published_state,
                          'the public API-produced numeric State must exist before first BASE')
            baseline = facts.baselines[name]
            self.assertFalse([row for row in facts.timer_deliveries if row[0] == name],
                             'timer partition requires a separate oracle')
            self.assertEqual((baseline.unit_prefix, baseline.unit_time),
                             (Decimal(1000), Decimal(3600)))
            origin = baseline.anchor if baseline.trigger == 'TimeElapsed' else baseline.source_reported
            end = published_state.last_updated
            self.assertLessEqual(origin, end)
            expected = baseline.full + (Decimal((first_state.last_updated - origin).total_seconds())
                                        * baseline.source_power / Decimal(3600000))
            expected += (Decimal((end - first_state.last_updated).total_seconds())
                         * Decimal(first_state.state) / Decimal(3600000))
            from homeassistant.helpers.entity_platform import async_get_platforms
            current = [entity for platform in async_get_platforms(self.f.hass, 'powercalc')
                       for entity in platform.entities.values() if entity.entity_id == name]
            self.assertEqual(len(current), 1)
            self.assertIsNot(current[0], self.static_energy)
            self.assertIs(current[0].registry_entry, self.static_energy.registry_entry)
            self.assertEqual(current[0]._state, expected)
            self.assertEqual(current[0]._last_valid_state, expected)
            self.assertTrue(result.operand_clock.direct_operand_clock_verified)
            # A subsequent real Light service call must reach the replacement
            # producer and energy listener with the original previous State.
            next_events = []
            @callback
            def record_next(event):
                if event.data.get('entity_id') == self.static_power.entity_id:
                    next_events.append(event)
            remove_next = self.f.hass.bus.async_listen('state_changed', record_next)
            try:
                await self.sources[0].async_turn_on(brightness=128)
                await self.f.hass.async_block_till_done()
            finally:
                remove_next()
            self.assertEqual(len(next_events), 1)
            next_event = next_events[0]
            self.assertIs(next_event.data['old_state'], published_state)
            next_state = next_event.data['new_state']
            self.assertEqual(next_state.entity_id, self.static_power.entity_id)
            self.assertNotEqual(Decimal(next_state.state), published_power)
            self.assertFalse([row for row in facts.timer_deliveries if row[0] == name],
                             'timer partition requires a separate oracle')
            later = expected + (Decimal((next_state.last_updated - end).total_seconds())
                                * published_power / Decimal(3600000))
            self.assertEqual(current[0]._state, later)
            self.assertEqual(current[0]._last_valid_state, later)
            self._no_owned(facts)
        finally:
            if remove is not None:
                remove()
            if audit_remove is not None:
                audit_remove()
            facts.close()

    async def test_unobserved_first_w_followed_by_observed_w_refuses_and_cleans(self):
        """Two real Light inputs; the earlier nested W has no emission receipt."""
        from homeassistant.core import callback
        from homeassistant.helpers.entity import Entity
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers import event as native_event

        await self.ready()
        facts = timers._TimerFacts(self)
        await facts.prepare()
        facts.normal_ownership_checks = False
        facts.allow_numeric_first = True
        facts.refs += ((Entity, '_async_write_ha_state'),)
        executor = self.module('yaml_validation')._settled_executor
        facts.methods, facts.codes = await executor(self.f.hass, facts.prove)
        write_code = facts.codes[-1]
        attempted = False
        stimulus_error = None
        first_state = None
        tasks = []
        observed = []
        triggered_second = False

        @callback
        def audit(event):
            state = event.data.get('new_state')
            if (event.data.get('entity_id') == self.static_power.entity_id and state is not None
                    and state.state not in ('unknown', 'unavailable')):
                observed.append(event)

        @callback
        def on_report(event):
            nonlocal triggered_second, stimulus_error
            if (triggered_second or first_state is None
                    or event.data.get('entity_id') != self.static_power.entity_id
                    or event.data.get('old_state') is not first_state
                    or event.data.get('new_state') is None
                    or event.data['new_state'].state != first_state.state):
                return
            try:
                triggered_second = True
                task = self.f.hass.async_create_task(self.sources[0].async_turn_on(brightness=64),
                    'test.second-public-input', eager_start=True)
                assert task.done() and task.exception() is None
                tasks.append(task)
            except BaseException:
                stimulus_error = traceback.format_exc()

        def hidden_first(code, offset):
            nonlocal attempted, first_state, stimulus_error
            try:
                frame = sys._getframe(1)
                producer = frame.f_locals.get('self')
                if (attempted or type(producer) is not type(self.static_power)
                        or producer is self.static_power or producer.entity_id != self.static_power.entity_id):
                    return
                parent = frame.f_back
                while parent is not None and parent.f_code is not Entity.add_to_platform_finish.__code__:
                    parent = parent.f_back
                if parent is None or parent.f_locals.get('self') is not producer:
                    return
                assert code is write_code and producer._platform_state.name == 'ADDED'
                assert producer.registry_entry is self.static_power.registry_entry and producer._power is None
                attempted = True
                task = self.f.hass.async_create_task(self.sources[0].async_turn_on(brightness=1),
                    'test.first-public-input-reentrant', eager_start=True)
                assert task.done() and task.exception() is None
                tasks.append(task)
                first_state = self.f.hass.states.get(self.static_power.entity_id)
                assert Decimal(first_state.state) == producer._power
            except BaseException:
                stimulus_error = traceback.format_exc()

        hook_tool = None
        remove_audit = remove_report = None
        try:
            facts.install()
            remove_audit = self.f.hass.bus.async_listen('state_changed', audit)
            remove_report = self.f.hass.bus.async_listen('state_changed', on_report)
            free = [i for i in range(6) if sys.monitoring.get_tool(i) is None]
            self.assertTrue(free)
            hook_tool = free[0]
            sys.monitoring.use_tool_id(hook_tool, 'renamer.test.hidden-first-real-source')
            sys.monitoring.register_callback(hook_tool, sys.monitoring.events.PY_START, hidden_first)
            sys.monitoring.set_local_events(hook_tool, write_code, sys.monitoring.events.PY_START)
            with self.assertRaises(ValueError) as caught:
                await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime,
                    guard=self.guard, exact_fields=True, retain_producers=True, operand_clock=True)
            print('HIDDEN_FIXTURE_DIAGNOSTIC', {'attempted': attempted,
                  'triggered_second': triggered_second, 'task_count': len(tasks),
                  'W': [event.data['new_state'].state for event in observed],
                  'failure': str(caught.exception), 'notes': list(getattr(caught.exception, '__notes__', ()))})
            self.assertIsNone(stimulus_error, repr(stimulus_error))
            self.assertTrue(attempted and triggered_second)
            self.assertEqual(len(tasks), 2)
            self.assertTrue(all(task.done() and task.exception() is None for task in tasks))
            self.assertEqual(len(observed), 3, 'actual W1, native republish and W2 required')
            self.assertIs(observed[0].data['new_state'], first_state)
            self.assertIs(observed[1].data['old_state'], first_state)
            self.assertIs(observed[2].data['old_state'], observed[1].data['new_state'])
            self.assertNotEqual(first_state.state, observed[2].data['new_state'].state)
            notes = str(caught.exception) + '\n'.join(getattr(caught.exception, '__notes__', ()))
            self.assertIn('source_delivery_without_emission', notes)
            self._no_owned(facts)
            for key in (native_event._TRACK_STATE_CHANGE_DATA, native_event._TRACK_STATE_REPORT_DATA):
                tracker = self.f.hass.data.get(key)
                if tracker is not None:
                    self.assertFalse([job for members in tracker.callbacks.values() for job in members
                        if '_OperandClockOwner._observe_source.<locals>.make_observer' in
                        getattr(job.target, '__qualname__', '')], 'owned source observer leaked')
            print('QUALIFIED_HIDDEN_THEN_OBSERVED_W', [event.data['new_state'].state for event in observed])
        finally:
            if hook_tool is not None:
                sys.monitoring.set_local_events(hook_tool, write_code, 0)
                sys.monitoring.register_callback(hook_tool, sys.monitoring.events.PY_START, None)
                sys.monitoring.free_tool_id(hook_tool)
            if remove_audit is not None:
                remove_audit()
            if remove_report is not None:
                remove_report()
            facts.close()


    async def test_foreign_tracker_rebound_quiesces_only_issued_source_job(self):
        """Rebind real trackers immediately before our native cleanup."""
        from homeassistant.core import callback
        from homeassistant.helpers import event as native_event

        await self.ready()
        original = foreign = foreign_remove = original_listener = None
        issued_jobs = []
        triggered = False
        stimulus_error = None
        module = self.module('powercalc_operand_clock')
        close_code = module._OperandClockOwner._close_source_observers.__code__
        @callback
        def foreign_callback(_event):
            pass
        def at_actual_cleanup(code, offset):
            nonlocal original, foreign, foreign_remove, original_listener, triggered, stimulus_error
            try:
                frame = sys._getframe(1)
                owner = frame.f_locals.get('self')
                if triggered or type(owner) is not module._OperandClockOwner or owner.scope.hass is not self.f.hass:
                    return
                assert code is close_code
                tracker = self.f.hass.data[native_event._TRACK_STATE_CHANGE_DATA]
                matches = [job for jobs in tracker.callbacks.values() for job in jobs
                           if '_OperandClockOwner._observe_source.<locals>.make_observer' in
                           getattr(job.target, '__qualname__', '')]
                assert matches
                issued_jobs.extend(matches)
                original, original_listener = tracker, tracker.listener
                assert self.f.hass.data.pop(native_event._TRACK_STATE_CHANGE_DATA) is original
                foreign_remove = native_event.async_track_state_change_event(
                    self.f.hass, 'sensor.synthetic_foreign_observer', foreign_callback)
                foreign = self.f.hass.data[native_event._TRACK_STATE_CHANGE_DATA]
                assert foreign is not original and foreign_remove.args[3].target is foreign_callback
                triggered = True
            except BaseException:
                stimulus_error = traceback.format_exc()
        hook_tool = None
        try:
            free = [i for i in range(6) if sys.monitoring.get_tool(i) is None]
            self.assertTrue(free)
            hook_tool = free[0]
            sys.monitoring.use_tool_id(hook_tool, 'renamer.test.foreign-tracker-rebound')
            sys.monitoring.register_callback(hook_tool, sys.monitoring.events.PY_START, at_actual_cleanup)
            sys.monitoring.set_local_events(hook_tool, close_code, sys.monitoring.events.PY_START)
            with self.assertRaises(ValueError) as caught:
                await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime,
                    guard=self.guard, exact_fields=True, retain_producers=True, operand_clock=True)
            self.assertIsNone(stimulus_error, repr(stimulus_error))
            self.assertTrue(triggered, 'actual cleanup was not reached')
            notes = str(caught.exception) + '\n'.join(getattr(caught.exception, '__notes__', ()))
            self.assertTrue('source_observer_tracker_changed' in notes
                            or 'operand_clock_cleanup_refused' in notes)
            self.assertIs(self.f.hass.data.get(native_event._TRACK_STATE_CHANGE_DATA), foreign)
            self.assertEqual(sum(job is foreign_remove.args[3]
                                 for jobs in foreign.callbacks.values() for job in jobs), 1)
            self.assertIs(foreign_remove.args[3].target, foreign_callback)
            self.assertIs(original.listener, original_listener)
            self.assertFalse([job for jobs in original.callbacks.values() for job in jobs
                              if any(job is issued for issued in issued_jobs)],
                             'issued observer survived in original dispatcher')
            verifier = self.module('yaml_runtime_observer')
            verifier._registered_bus_remove(self.f.hass, original_listener, 'state_changed')
            verifier._registered_bus_remove(self.f.hass, foreign.listener, 'state_changed')
            print('NATIVE_FOREIGN_TRACKER_REBOUND_CLEANUP',
                  {'old_jobs_removed': len(issued_jobs), 'foreign_tracker_preserved': True})
        finally:
            if hook_tool is not None:
                sys.monitoring.set_local_events(hook_tool, close_code, 0)
                sys.monitoring.register_callback(hook_tool, sys.monitoring.events.PY_START, None)
                sys.monitoring.free_tool_id(hook_tool)
            if foreign is not None:
                self.assertIs(self.f.hass.data.get(native_event._TRACK_STATE_CHANGE_DATA), foreign)
                foreign_remove()
                self.assertNotIn(native_event._TRACK_STATE_CHANGE_DATA, self.f.hass.data)
                self.f.hass.data[native_event._TRACK_STATE_CHANGE_DATA] = original


    async def test_foreign_tracker_rebound_during_reload_preserves_foreign(self):
        """Trace genuine native listener removers during an in-flight rebound."""
        from homeassistant.core import callback
        from homeassistant.helpers import event as native_event
        await self.ready()
        original = foreign = foreign_remove = None
        triggered = False
        calls = []
        @callback
        def foreign_callback(_event):
            pass
        def rebind_guard():
            nonlocal original, foreign, foreign_remove, triggered
            self.guard()
            try:
                if asyncio.get_running_loop() is not self.f.hass.loop:
                    return
            except RuntimeError:
                return
            if triggered:
                return
            tracker = self.f.hass.data.get(native_event._TRACK_STATE_CHANGE_DATA)
            if tracker is None:
                return
            matches = [job for jobs in tracker.callbacks.values() for job in jobs
                       if '_OperandClockOwner._observe_source.<locals>.make_observer' in
                       getattr(job.target, '__qualname__', '')]
            if not matches:
                return
            original = tracker
            assert self.f.hass.data.pop(native_event._TRACK_STATE_CHANGE_DATA) is original
            foreign_remove = native_event.async_track_state_change_event(
                self.f.hass, 'sensor.synthetic_foreign_observer', foreign_callback)
            foreign = self.f.hass.data[native_event._TRACK_STATE_CHANGE_DATA]
            assert foreign is not original
            triggered = True
        code = native_event._remove_listener.__code__
        tool = None
        def record(phase):
            frame = sys._getframe(2)
            tracker, callbacks, job = (frame.f_locals.get('tracker'), frame.f_locals.get('callbacks'),
                                       frame.f_locals.get('job'))
            if not triggered or tracker is not native_event._KEYED_TRACK_STATE_CHANGE:
                return
            parent, parents = frame.f_back, []
            while parent is not None and len(parents) < 8:
                parents.append(parent.f_code.co_qualname)
                parent = parent.f_back
            current = self.f.hass.data.get(native_event._TRACK_STATE_CHANGE_DATA)
            target = getattr(job, 'target', None)
            calls.append({'phase': phase, 'old_map': callbacks is original.callbacks,
                          'foreign_map': callbacks is foreign.callbacks,
                          'current_foreign': current is foreign, 'current_absent': current is None,
                          'job': getattr(target, '__qualname__', type(target).__name__),
                          'parents': parents})
        try:
            free = [i for i in range(6) if sys.monitoring.get_tool(i) is None]
            self.assertTrue(free)
            tool = free[0]
            sys.monitoring.use_tool_id(tool, 'renamer.test.native-tracker-removal')
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START,
                                             lambda code, offset: record('start'))
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN,
                                             lambda code, offset, result: record('return'))
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
            with self.assertRaises(ValueError):
                await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime,
                    guard=rebind_guard, exact_fields=True, retain_producers=True, operand_clock=True)
            self.assertTrue(triggered)
            import json
            print('NATIVE_TRACKER_REBOUND_TRACE', json.dumps(calls, sort_keys=True))
            self.assertIs(self.f.hass.data.get(native_event._TRACK_STATE_CHANGE_DATA), foreign,
                          'foreign native tracker must not be removed during unload')
            foreign_job = foreign_remove.args[3]
            self.assertIs(foreign_job.target, foreign_callback)
            self.assertEqual(sum(job is foreign_job for jobs in foreign.callbacks.values() for job in jobs), 1)
            self.module('yaml_runtime_observer')._registered_bus_remove(
                self.f.hass, foreign.listener, 'state_changed')
            for data in (original, foreign):
                self.assertFalse([job for jobs in data.callbacks.values() for job in jobs
                    if '_RetainedProducerOwner.removed.<locals>.bridge' in
                       getattr(job.target, '__qualname__', '')
                    or '_OperandClockOwner._observe_source.<locals>.make_observer' in
                       getattr(job.target, '__qualname__', '')], 'owned bridge survived in tracker')
            self.assertFalse([row for row in calls if row['old_map']
                              and '_RetainedProducerOwner._remove_bridge' in row['parents']],
                             'native remover must not act on rebound old map')
        finally:
            if tool is not None:
                sys.monitoring.set_local_events(tool, code, 0)
                sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
                sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
                sys.monitoring.free_tool_id(tool)
            if foreign is not None and self.f.hass.data.get(native_event._TRACK_STATE_CHANGE_DATA) is foreign:
                foreign_remove()
                if native_event._TRACK_STATE_CHANGE_DATA not in self.f.hass.data:
                    self.f.hass.data[native_event._TRACK_STATE_CHANGE_DATA] = original


@unittest.skipUnless(timers.baseline.prior.accepted.fixture.HAS_HA, 'actual supported HA required')
class NativeCancellationLargeCustomIOTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = timers.NativePowercalcActualTimerTests.namespace_source_bytes
    asyncSetUp = timers.NativePowercalcActualTimerTests.asyncSetUp
    _flush_documents = timers.NativePowercalcActualTimerTests._flush_documents
    guard = timers.NativePowercalcActualTimerTests.guard
    module = timers.NativePowercalcActualTimerTests.module
    prepare_candidate = timers.NativePowercalcActualTimerTests.prepare_candidate
    chain_root = timers.NativePowercalcActualTimerTests.chain_root
    ready = timers.NativePowercalcActualTimerTests.ready
    _no_owned = timers.NativePowercalcActualTimerTests._no_owned
    _drive = timers.NativePowercalcActualTimerTests._drive
    _oracle_and_handoff = timers.NativePowercalcActualTimerTests._oracle_and_handoff
    test_cancel_after_actual_timer_delivery_settles_without_leaking_or_cancelling_native_handoff = (
        timers.NativePowercalcActualTimerTests.test_cancel_after_actual_timer_delivery_settles_without_leaking_or_cancelling_native_handoff)

    async def _setup_real_vendor(self):
        # A valid, large synthetic custom-profile input, created before the
        # ORIGINAL capture. Trailing JSON whitespace preserves the public
        # model semantics while exercising genuine executor-backed file I/O.
        import json
        import hashlib
        await timers.NativePowercalcActualTimerTests._setup_real_vendor(self)
        path = self.profile_root / 'signify/LWA001/model.json'
        original = path.read_bytes()
        expanded = original + b' ' * 1_800_000
        self.assertLess(len(expanded), 2_000_000)
        self.assertEqual(json.loads(expanded), json.loads(original))
        path.write_bytes(expanded)
        print('SYNTHETIC_CUSTOM_MODEL_INPUT', len(expanded), hashlib.sha256(expanded).hexdigest())


@unittest.skipUnless(timers.baseline.prior.accepted.fixture.HAS_HA, 'actual supported HA required')
class NumericAfterRealTimerTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = timers.NativePowercalcActualTimerTests.namespace_source_bytes
    _flush_documents = timers.NativePowercalcActualTimerTests._flush_documents
    guard = timers.NativePowercalcActualTimerTests.guard
    module = timers.NativePowercalcActualTimerTests.module
    prepare_candidate = timers.NativePowercalcActualTimerTests.prepare_candidate
    chain_root = timers.NativePowercalcActualTimerTests.chain_root
    ready = timers.NativePowercalcActualTimerTests.ready
    _no_owned = timers.NativePowercalcActualTimerTests._no_owned
    _setup_real_vendor = NativeCancellationLargeCustomIOTests._setup_real_vendor

    async def asyncSetUp(self):
        self.custom_profile_directories = True
        await timers.baseline.NativePowercalcOperandClockRedTests.asyncSetUp(self)

    async def test_real_fired_then_numeric_w_then_first_base(self):
        await self.ready()
        facts = timers._CustomTimerFacts(self)
        await facts.prepare()
        facts.normal_ownership_checks = False
        facts.allow_numeric_first = True
        attempted = False
        source_task = None
        published_state = published_power = None
        stimulus_error = None
        remove = None
        from homeassistant.core import callback
        @callback
        def actual_initial_power(event):
            nonlocal attempted, source_task, published_state, published_power, stimulus_error
            state = event.data.get('new_state')
            if (attempted or event.data.get('entity_id') != self.static_power.entity_id
                    or state is None or state.state != 'unknown'):
                return
            try:
                from homeassistant.helpers.entity_platform import async_get_platforms
                powers = [e for platform in async_get_platforms(self.f.hass, 'powercalc')
                          for e in platform.entities.values() if e.entity_id == self.static_power.entity_id]
                if not powers or powers[0] is self.static_power:
                    return
                producer = powers[0]
                assert producer.registry_entry is self.static_power.registry_entry
                assert producer._platform_state.name == 'ADDED' and producer._power is None
                attempted = True
                source_task = self.f.hass.async_create_task(
                    self.sources[0].async_turn_on(brightness=1),
                    'test.public-source-from-native-state-change', eager_start=True)
                assert source_task.done() and source_task.exception() is None
            except BaseException as error:
                stimulus_error = traceback.format_exc()
        try:
            facts.install()
            remove = self.f.hass.bus.async_listen('state_changed', actual_initial_power)
            result = await self.numeric.async_capture_powercalc_numeric_reload(
                self.f.hass, self.chain, self.evidence, self.original_runtime,
                guard=self.guard, exact_fields=True, retain_producers=True, operand_clock=True)
            self.assertIsNone(stimulus_error, repr(stimulus_error))
            self.assertTrue(attempted, 'fixture did not expose the real numeric-first setup seam')
            self.assertIsNotNone(source_task)
            self.assertTrue(source_task.done(), 'native source calculation remained inconclusive')
            self.assertIsNone(source_task.exception())
            self.assertIsNone(facts.error, repr(facts.error))
            name = self.static_energy.entity_id
            self.assertIn(name, facts.numeric, 'actual numeric W emission was not observed')
            published_state = facts.numeric[name][1]
            published_power = facts.numeric[name][4]._power
            from homeassistant.core import State
            self.assertIs(type(published_state), State)
            self.assertIs(type(published_power), Decimal)
            self.assertTrue(published_power.is_finite())
            self.assertEqual(Decimal(published_state.state), published_power,
                             'public source must finish a real native numeric W write')
            self.assertIn(name, facts.first_base)
            self.assertIs(facts.first_base[name][1], published_state,
                          'the public API-produced numeric State must exist before first BASE')
            baseline = facts.baselines[name]
            self.assertEqual((baseline.unit_prefix, baseline.unit_time),
                             (Decimal(1000), Decimal(3600)))
            facts.assert_custom_read()
            deliveries = [row for row in facts.deliveries if row['issue']['name'] == name]
            self.assertTrue(deliveries, 'real FIRED before numeric W required')
            origin = baseline.anchor if baseline.trigger == 'TimeElapsed' else baseline.source_reported
            expected = baseline.full
            for delivery in deliveries:
                self.assertIn('return_utc', delivery)
                self.assertLessEqual(delivery['return_utc'], published_state.last_updated)
                self.assertGreaterEqual(delivery['entry_loop'], delivery['issue']['deadline'])
                self.assertEqual(len(delivery['areas']), 1)
                self.assertEqual(len(delivery['integrals']), 1)
                expected += (Decimal((delivery['now'] - origin).total_seconds())
                             * baseline.source_power / Decimal(3600000))
                self.assertEqual(delivery['full_after'], expected)
                origin = delivery['anchor_after']
            end = published_state.last_updated
            self.assertLessEqual(origin, end)
            expected += (Decimal((end - origin).total_seconds())
                         * baseline.source_power / Decimal(3600000))
            from homeassistant.helpers.entity_platform import async_get_platforms
            current = [entity for platform in async_get_platforms(self.f.hass, 'powercalc')
                       for entity in platform.entities.values() if entity.entity_id == name]
            self.assertEqual(len(current), 1)
            self.assertIsNot(current[0], self.static_energy)
            self.assertIs(current[0].registry_entry, self.static_energy.registry_entry)
            self.assertEqual(current[0]._state, expected)
            self.assertEqual(current[0]._last_valid_state, expected)
            self.assertTrue(result.operand_clock.direct_operand_clock_verified)
            # A subsequent real Light service call must reach the replacement
            # producer and energy listener with the original previous State.
            next_events = []
            @callback
            def record_next(event):
                if event.data.get('entity_id') == self.static_power.entity_id:
                    next_events.append(event)
            remove_next = self.f.hass.bus.async_listen('state_changed', record_next)
            try:
                await self.sources[0].async_turn_on(brightness=64)
                await self.f.hass.async_block_till_done()
            finally:
                remove_next()
            self.assertEqual(len(next_events), 1)
            next_event = next_events[0]
            self.assertIs(next_event.data['old_state'], published_state)
            next_state = next_event.data['new_state']
            self.assertEqual(next_state.entity_id, self.static_power.entity_id)
            self.assertNotEqual(Decimal(next_state.state), published_power)
            self.assertEqual(len([row for row in facts.deliveries if row['issue']['name'] == name]),
                             len(deliveries), 'unexpected later timer in oracle window')
            later = expected + (Decimal((next_state.last_updated - end).total_seconds())
                                * published_power / Decimal(3600000))
            self.assertEqual(current[0]._state, later)
            self.assertEqual(current[0]._last_valid_state, later)
            self._no_owned(facts)
        finally:
            if remove is not None:
                remove()
            facts.close()

