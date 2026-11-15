"""Actual own-ID+timer composite. Owner executes both pinned Cores through FIFO.

Reuse the complete own-ID Coordinator/escrow/YAML/setup/fresh-reverse fixture.
The oracle observes native calls and real timestamps; it never feeds a total,
State, Event, Task, clock, cache or callback result into the product.
"""
import asyncio
from decimal import Decimal
import importlib
import json
import sys
import unittest

import test_native_integration_own_lifecycle as quiet

PACKAGE = quiet.PACKAGE


@unittest.skipUnless(quiet.fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationOwnTimerLifecycleTests(quiet.NativeIntegrationOwnLifecycleTests):
    method = 'left'
    saved_uuid = False

    async def prepare(self, *, zero=False):
        await super().prepare(zero=zero)
        hass = self.f.hass
        # Ordinary native configuration+reload, before capturing ORIGINAL.
        self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
        options = dict(self.entry.options, method=self.method, max_sub_interval={'seconds': 1})
        if self.saved_uuid:
            options['source'] = self.f.entity.async_get(self.producer.entity_id).id
        hass.config_entries.async_update_entry(self.entry, options=options)
        self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
        await hass.async_block_till_done()
        self.old = self.current()
        self.oracle_expected = self.old._state
        self.oracle_error = None
        self.oracle_updates = self.timer_returns = 0
        self.getter_captures = []
        self.seam_checks = []
        self.waiters = []
        self.pending_areas = {}
        self.method_intervals = []
        self.pending_origin = {}
        self.oracle_origins = {}
        self.frozen_oracle_updates = 0
        self.nested_reports_armed = False
        self.nested_report_events = []
        self.source_oracle = None
        self.active_burst = None
        self.clock_scopes = []
        self.timer_chain = None
        self._install_numeric_observer()
        self.addCleanup(self._remove_numeric_observer)
        await self._next_actual_tick()
        self.assertEqual(self.old._last_integration_trigger.name, 'TimeElapsed')
        self.assertIs(type(self.old._state), Decimal)
        self.assertIs(type(self.old._last_valid_state), Decimal)
        if zero:
            self.assertEqual(self.old._state, Decimal(0))
        else:
            self.assertGreater(self.old._state, 0)
            self.assertNotEqual(self.old._state, Decimal(str(self.old.native_value)))

    def _install_numeric_observer(self):
        sensor = importlib.import_module('homeassistant.components.integration.sensor')
        cls = sensor.IntegrationSensor
        from homeassistant.core import EventBus, StateMachine, callback
        from homeassistant.helpers.event import async_track_state_report_event
        shared = importlib.import_module(PACKAGE + '.native_integration_source_clock')
        emissions = importlib.import_module(PACKAGE + '.native_utility_source_emissions')
        self.consume_code = shared.IntegrationSourceClockBinding._consume.__code__
        self.interval_class = shared.SourceInterval
        self.method_class = {'left': sensor._Left, 'right': sensor._Right,
                             'trapezoidal': sensor._Trapezoidal}[self.method]
        self.method_refs = ((self.method_class, 'validate_states'),
                            (self.method_class, 'calculate_area_with_two_states'),
                            (sensor._IntegrationMethod, 'calculate_area_with_one_state'))
        self.method_codes = tuple(getattr(cls, name).__code__
                                  for cls, name in self.method_refs)
        self.fire_code = EventBus.async_fire_internal.__code__
        self.source_set_code = StateMachine.async_set_internal.__code__
        self.addCleanup(self._close_source_oracle)
        self.source_oracle = emissions.NativeSourceEmissions(self.fire_code, self.f.hass.bus,
            self._source_origin, self._source_oracle_failure)

        @callback
        def nested_report_driver(event):
            # A separate real producer driver, never the readonly oracle. It is
            # installed before the clock bridge and acts only when explicitly
            # armed in the retained window. No manufactured Event/State.
            if self.nested_reports_armed:
                self.nested_reports_armed = False
                self.nested_report_events.append(event)
                self.producer.async_write_ha_state()
                self.producer.async_write_ha_state()
        self.addCleanup(async_track_state_report_event(self.f.hass,
            self.producer.entity_id, nested_report_driver))
        handle = self.current()._max_sub_interval_exceeded_callback.__self__
        action = handle._args[1].target
        self.timer_code = action.__code__
        self.change_code = cls._integrate_on_state_change.__code__
        self.update_code = cls._update_integral.__code__
        self.getter_code = cls.extra_restore_state_data.fget.__code__
        self.added_code = cls.async_added_to_hass.__code__
        self.observed_codes = (self.timer_code, self.change_code, self.update_code,
                               self.getter_code, self.added_code)
        self.observer_tool = next((k for k in range(6) if sys.monitoring.get_tool(k) is None), None)
        self.assertIsNotNone(self.observer_tool)
        key = self.observer_tool
        sys.monitoring.use_tool_id(key, 'renamer_own_timer_numeric_oracle')
        sys.monitoring.register_callback(key, sys.monitoring.events.PY_START,
                                        lambda code, offset: self._native_observation('start'))
        sys.monitoring.register_callback(key, sys.monitoring.events.PY_RETURN,
                                        lambda code, offset, result: self._native_observation('return'))
        for code in self.observed_codes:
            sys.monitoring.set_local_events(key, code,
                sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)

    def _assert_native_method(self, entity, scope=None):
        self.assertIs(type(entity._method), self.method_class)
        self.assertEqual((entity._unit_prefix, entity._unit_time), (1, 1))
        for ref, code in zip(self.method_refs, self.method_codes, strict=True):
            self.assertIs(getattr(*ref).__code__, code)
            if scope is not None:
                index = scope.source_binding.refs.index(ref)
                self.assertIs(scope.source_binding.codes[index], code)

    def _two_state_area(self, elapsed, left, right):
        # Independent Decimal arithmetic from real scalar inputs, never native
        # calculate() or the area argument of _update_integral. Preserve the
        # pinned native operator order, including trapezoidal division by 2.
        left, right = Decimal(left), Decimal(right)
        if left != right and elapsed != 0:
            self.method_intervals.append((elapsed, left, right))
        if self.method == 'left':
            return left * elapsed
        if self.method == 'right':
            return right * elapsed
        self.assertEqual(self.method, 'trapezoidal')
        return elapsed * (left + right) / 2

    def _remove_numeric_observer(self):
        key = self.observer_tool
        for code in self.observed_codes:
            sys.monitoring.set_local_events(key, code, 0)
        sys.monitoring.register_callback(key, sys.monitoring.events.PY_START, None)
        sys.monitoring.register_callback(key, sys.monitoring.events.PY_RETURN, None)
        sys.monitoring.free_tool_id(key)
        self.observer_tool = None

    def _source_oracle_failure(self, reason):
        if self.oracle_error is None:
            self.oracle_error = ('source_origin', reason)

    def _close_source_oracle(self):
        if self.source_oracle is not None:
            self.source_oracle.close()
            self.source_oracle = None
        self.oracle_origins.clear()

    def _source_origin(self, frame):
        # This is the REAL native EventBus frame, independently observed at
        # emission before later report mutations or nested keyed delivery.
        parent = None
        try:
            if self.oracle_error is not None:
                return
            data, kind = frame.f_locals.get('event_data'), frame.f_locals.get('event_type')
            if (kind not in ('state_changed', 'state_reported') or type(data) is not dict
                    or data.get('entity_id') != self.producer.entity_id):
                return
            self.assertIs(frame.f_code, self.fire_code)
            self.assertIs(frame.f_locals.get('self'), self.f.hass.bus)
            parent = frame.f_back
            self.assertIsNotNone(parent)
            self.assertIs(parent.f_code, self.source_set_code)
            self.assertIs(parent.f_locals.get('self'), self.f.hass.states)
            old, new = data.get('old_state'), data.get('new_state')
            self.assertIsNotNone(new)
            if kind == 'state_changed':
                self.assertIsNotNone(old)  # This own-ID fixture never moves source.
                self.assertIs(parent.f_locals.get('state_changed_data'), data)
                self.assertIs(parent.f_locals.get('state'), new)
                left, old_time, predecessor = old.state, old.last_reported, old
                new_time = new.last_updated if self.f.version == '2026.10.2' else new.last_reported
            else:
                self.assertIs(parent.f_locals.get('old_state'), new)
                self.assertEqual(data['old_last_reported'], parent.f_locals.get('old_last_reported'))
                left, old_time, predecessor = new.state, data['old_last_reported'], None
                new_time = data['last_reported'] if self.f.version == '2026.10.2' else new.last_reported
            self.assertTrue(Decimal(left).is_finite())
            self.assertTrue(Decimal(new.state).is_finite())
            self.assertNotIn(id(data), self.oracle_origins)
            # Store independently frozen actual scalars plus exact native refs;
            # never derive expected operands from the product's SourceInterval.
            self.oracle_origins[id(data)] = (data, kind, left, new.state,
                                              old_time, new_time, predecessor, new)
            if self.active_burst is not None:
                self.active_burst['emissions'].append(data)
        except BaseException as error:
            self._source_oracle_failure((type(error).__name__, str(error)))
        finally:
            del parent

    def _frozen_update_start(self, frame, entity):
        parent = frame.f_back
        try:
            self.assertIs(parent.f_code, self.consume_code)
            scope = parent.f_locals['scope']
            self.assertIn(scope, self.clock_scopes)
            self.assertIs(scope.old, entity)
            self.assertTrue(scope._retained)
            # The actual native IDs must be the ones proved by this scope.
            self.assertIs(frame.f_code, scope.source_binding.codes[1])
            self.assertIs(scope.codes['fire'], self.fire_code)
            self.assertIs(scope.codes['source_set'], self.source_set_code)
            self._assert_native_method(entity, scope)
            event, interval = parent.f_locals['event'], parent.f_locals['interval']
            self.assertIs(type(interval), self.interval_class)
            record = self.oracle_origins[id(event.data)]
            data, kind, left, right, old_time, new_time, predecessor, state = record
            self.assertIs(data, event.data)
            self.assertEqual(event.event_type, kind)
            self.assertIs(scope._emitted[id(data)][9], interval)
            self.assertEqual((interval.kind, interval.name, interval.left, interval.right,
                              interval.old_time, interval.new_time, interval.transport),
                             (kind, self.producer.entity_id, left, right, old_time, new_time, None))
            self.assertIs(interval.predecessor, predecessor)
            self.assertIs(interval.state, state)
            self.assertEqual(entity._state, self.oracle_expected)
            origin = (old_time if entity._last_integration_trigger.name == 'StateEvent'
                      else entity._last_integration_time)
            elapsed = Decimal((new_time - origin).total_seconds())
            # Signed elapsed follows native arithmetic; no area argument is read.
            self.assertNotIn(id(entity), self.pending_areas)
            self.pending_areas[id(entity)] = ((Decimal(0) if self.oracle_expected is None
                                              else self.oracle_expected)
                                             + self._two_state_area(elapsed, left, right))
            self.pending_origin[id(entity)] = id(data)
        finally:
            del parent

    def _native_observation(self, phase):
        # Called via the monitoring lambda: one more frame is the actual native
        # frame. Observation errors are recorded, never thrown into native code.
        frame = sys._getframe(2)
        try:
            entity = frame.f_locals.get('self')
            if getattr(entity, 'unique_id', None) != self.entry.entry_id:
                return
            code = frame.f_code
            if code is self.timer_code and phase == 'return':
                self.timer_returns += 1
                for waiter in tuple(self.waiters):
                    if not waiter.done():
                        waiter.set_result(self.timer_returns)
                return
            if self.oracle_error is not None:
                return
            if code is self.added_code and phase == 'return':
                scope = self.clock_scopes[-1]
                self.assertIs(scope._new, entity)
                self.assertTrue(scope._new_timer_bound)
                handle = entity._max_sub_interval_exceeded_callback.__self__
                self.assertEqual(handle.when(), scope._transferred.deadline)
                self.assertTrue(scope._old_cancelled.cancelled())
                self.assertEqual(entity._state.as_tuple(), Decimal(scope._transferred.full).as_tuple())
                self.assertEqual(entity._last_valid_state.as_tuple(),
                                 Decimal(scope._transferred.last_valid).as_tuple())
                self.assertEqual(entity._last_integration_time.isoformat(), scope._transferred.time)
                self.assertEqual(entity._last_integration_trigger.name, scope._transferred.trigger)
                self.seam_checks.append(entity)
            if code is self.getter_code and phase == 'start':
                self.getter_captures.append((entity.entity_id, entity._state.as_tuple(),
                                             entity._last_valid_state.as_tuple()))
            if code in (self.timer_code, self.change_code) and phase == 'start':
                self._assert_native_method(entity)
                self.assertEqual(entity._state, self.oracle_expected)
                local = frame.f_locals
                if code is self.timer_code:
                    origin, end = entity._last_integration_time, local['now']
                    left = local['source_state'].state
                else:
                    old, new = local['old_state'], local['new_state']
                    self.assertIsNotNone(new)
                    left = old.state if old is not None else new.state
                    right = new.state
                    if self.f.version == '2026.10.2':
                        origin = old.last_reported if old is not None else local['old_timestamp']
                        end = new.last_updated if old is not None else local['new_timestamp']
                    else:
                        origin = old.last_reported if old is not None else local['old_last_reported']
                        end = new.last_reported
                    if entity._last_integration_trigger.name == 'TimeElapsed':
                        origin = entity._last_integration_time
                    if origin is None:  # Native initial report performs no integration.
                        return
                elapsed = Decimal((end - origin).total_seconds())
                area = (Decimal(left) * elapsed if code is self.timer_code
                        else self._two_state_area(elapsed, left, right))
                self.assertNotIn(id(entity), self.pending_areas)
                self.pending_areas[id(entity)] = ((Decimal(0) if self.oracle_expected is None
                                                  else self.oracle_expected)
                                                 + area)
            if code is self.update_code and phase == 'start' and id(entity) not in self.pending_areas:
                self._frozen_update_start(frame, entity)
            if code is self.update_code and phase == 'return':
                expected = self.pending_areas.pop(id(entity))
                self.assertEqual(entity._state, expected)
                self.assertEqual(entity._last_valid_state, expected)
                self.oracle_expected = expected
                self.oracle_updates += 1
                if id(entity) in self.pending_origin:
                    data_key = self.pending_origin.pop(id(entity))
                    self.oracle_origins.pop(data_key)
                    self.frozen_oracle_updates += 1
        except BaseException as error:
            self.oracle_error = (type(error).__name__, str(error))
        finally:
            del frame

    async def _next_actual_tick(self):
        waiter = self.f.hass.loop.create_future()
        self.waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, 10)
            self.assertIsNone(self.oracle_error, self.oracle_error)
        finally:
            self.waiters.remove(waiter)

    async def _start_own_lifecycle(self, *args, **kwargs):
        from test_own_helper_unload_lifecycle import lm
        rr = importlib.import_module(PACKAGE + '.native_registry_receipts')
        continuity = importlib.import_module(PACKAGE + '.native_integration_continuity')
        # Import absence is a real RED, not skip or a fabricated successful scope.
        own_clock = importlib.import_module(PACKAGE + '.native_integration_own_clock')
        store, manager, operation, plan, receipt, digests = args
        if self.timer_chain is None:
            chain_store = rr.native_registry_receipt_store(self.f.hass, store.preparations)
            await chain_store.initialize()
            self.timer_chain = await rr.RegistryReceiptChain.start(
                chain_store, operation, plan, receipt, digests)
        else:
            await self._next_actual_tick()
        chain = self.timer_chain
        self.assertEqual((chain.operation_id, chain.plan, chain.original_receipt),
                         (operation, plan, receipt))
        binding = own_clock.IntegrationOwnClockBinding(chain, self.entry.entry_id,
            reverse=kwargs.get('source_authorization') is not None)
        scope = await continuity.NativeIntegrationContinuity.start(self.f.hass, self.entry,
            self.lease, chain.store, guard=lambda: (self.guard(), True)[1], source_binding=binding)
        self.clock_scopes.append(scope)
        try:
            return await lm.NativeHelperLifecycle.start(*args,
                **dict(kwargs, integration_clocks={self.entry.entry_id: scope}))
        except BaseException:
            scope.close()
            raise

    async def _start_own_chain(self, chain_store, life):
        self.assertIs(chain_store.preparations, self.timer_chain.store.preparations)
        self.assertEqual((life.plan, life.operation_id),
                         (self.timer_chain.plan, self.timer_chain.operation_id))
        return self.timer_chain

    def _assert_own_counter(self, entity):
        self.assertIsNone(self.oracle_error, self.oracle_error)
        self.assertIs(type(entity._state), Decimal)
        self.assertIs(type(entity._last_valid_state), Decimal)
        self.assertEqual(entity._state, self.oracle_expected)
        self.assertEqual(entity._state, entity._last_valid_state)

    async def _after_own_escrow(self, life, escrow, counter_before):
        scope = self.clock_scopes[-1]
        self.assertTrue(scope._retained)
        capture = json.loads(escrow._payload.capture_json)
        self.assertTrue(self.getter_captures, 'must observe actual native restore getter')
        name, full, last = self.getter_captures[-1]
        self.assertEqual(name, capture['entity_id'])
        self.assertEqual((Decimal(capture['full_state_text']).as_tuple(),
                          Decimal(capture['last_valid_state_text']).as_tuple()), (full, last))
        immutable = escrow._payload
        # Actual changed + same-value report, then an actual elapsed callback,
        # all after immutable escrow was copied and while the old engine is owned.
        for value in (('0', '0') if self.zero else ('123456.800', '123456.800')):
            await self.emit(value)
        from homeassistant.core import callback
        from homeassistant.helpers.event import async_track_state_report_event
        frozen_before = self.frozen_oracle_updates
        nested_before = len(self.nested_report_events)
        burst = {'emissions': [], 'deliveries': set(), 'done': self.f.hass.loop.create_future()}
        self.active_burst = burst
        @callback
        def delivered_after_bridge(event):
            # Installed AFTER the actual bridge. In minimum Core emission and
            # keyed delivery need not be synchronous or in the same stack.
            ids = {id(data) for data in burst['emissions']}
            if id(event.data) in ids:
                burst['deliveries'].add(id(event.data))
            if (len(burst['emissions']) == 3 and ids <= burst['deliveries']
                    and self.frozen_oracle_updates > frozen_before and not burst['done'].done()):
                burst['done'].set_result(None)
        unsubscribe = async_track_state_report_event(self.f.hass,
            self.producer.entity_id, delivered_after_bridge)
        try:
            self.nested_reports_armed = True
            await self.emit('0' if self.zero else '123456.800')
            await asyncio.wait_for(burst['done'], 10)
            self.assertFalse(self.nested_reports_armed, 'actual native report driver was not reached')
            self.assertEqual(len(self.nested_report_events), nested_before + 1)
            self.assertEqual(len(burst['emissions']), 3)
            self.assertEqual(len(burst['deliveries']), 3)
            self.assertIsNone(self.oracle_error, self.oracle_error)
            scope._check(owner=True)
            self.assertGreater(self.frozen_oracle_updates, frozen_before,
                               'must observe REAL frozen native updates, not only stable callbacks')
        finally:
            self.nested_reports_armed = False
            self.active_burst = None
            unsubscribe()
        await self._next_actual_tick()
        self.assertIs(escrow._payload, immutable)
        if not self.zero:
            self.assertGreater(scope.old._state, Decimal(capture['full_state_text']))
        self.assertIsNone(self.oracle_error, self.oracle_error)

    async def _before_own_restore(self, life):
        scope = self.clock_scopes[-1]
        self.assertEqual(scope.source_binding.transport_calls, 0)
        self.assertTrue(scope._retained)
        await self._next_actual_tick()

    async def _after_own_restore(self, life):
        scope = self.clock_scopes[-1]
        self.assertEqual(scope.source_binding.transport_calls, 0)
        row = scope.store.read(scope.receipt[0])['payload']
        self.assertEqual(row['transfer_readback'], {k: row['actual_transfer'][k]
            for k in ('full', 'last_valid', 'time', 'trigger')})
        self.assertTrue(scope._old_cancelled.cancelled())
        self.assertEqual(scope._timer_rearms, 1)
        self.assertEqual(self.seam_checks.count(scope._new), 1)
        self.assertEqual(self.f.entity.async_get(scope._new.entity_id).id, self.uuid)
        self._assert_own_counter(scope._new)
        await self._next_actual_tick()  # Natural new-owner timer AFTER scope close.
        self._assert_own_counter(scope._new)
        self.assertGreater(self.oracle_updates, 0)
        if self.method != 'left':
            # A constant-state timer alone cannot distinguish the methods.
            self.assertTrue(self.method_intervals, 'no real unequal source operands')


class NativeIntegrationOwnZeroTimerLifecycleTests(NativeIntegrationOwnTimerLifecycleTests):
    zero = True


class NativeIntegrationOwnRightTimerLifecycleTests(NativeIntegrationOwnTimerLifecycleTests):
    method = 'right'


class NativeIntegrationOwnTrapezoidalTimerLifecycleTests(NativeIntegrationOwnTimerLifecycleTests):
    method = 'trapezoidal'


class NativeIntegrationOwnSavedUUIDTimerLifecycleTests(NativeIntegrationOwnTimerLifecycleTests):
    saved_uuid = True
