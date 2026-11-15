"""New genuine baseline REDs for the next direct operand-clock prerequisite.

TESTS ONLY. Existing retained-W API is executed, not a missing future API. No
native method/clock/state/counter/cache replacement. Observer reads real native
frames; oracle output is assert-only and never enters product/native writers.
The two REDs cover the short interval before the inherited native deadline.
"""
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import sys
import unittest

import test_powercalc_numeric_continuity_native as prior


@dataclass(frozen=True)
class _EngineFacts:
    entity: object
    full: Decimal
    last_valid: Decimal
    anchor: object
    trigger: str
    source_state: object
    source_reported: object
    source_power: Decimal | None
    handle: object
    deadline: float | None
    loop_time: float
    platform_state: str
    unit_prefix: object
    unit_time: object


class _ClockFacts:
    """Pinned read-only observer. All mutable test data stays in this instance."""
    def __init__(self, case):
        self.case, self.hass = case, case.f.hass
        self.old_e = {e.entity_id: e for e in (case.static_energy, case.dynamic_energy)}
        self.old_w = {w.entity_id: w for w in (case.static_power, case.dynamic_power)}
        self.energy_by_power = {e._sensor_source_id: e.entity_id for e in self.old_e.values()}
        self.baselines, self.transport, self.after_transport = {}, {}, {}
        self.numeric, self.cutovers, self.first_base = {}, {}, {}
        self.timer_deliveries = []
        self.keep, self.error, self.tool = {}, None, None

    async def prepare(self):
        from homeassistant.core import EventBus, StateMachine
        from homeassistant.helpers import event
        from homeassistant.helpers.entity import Entity
        from homeassistant.helpers.entity_registry import RegistryEntry
        subject = self.case.module('powercalc_source_binding')
        proof = self.case.module('native_unload_code_proof')
        audit = self.case.module('native_utility_setup_audit')
        executor = self.case.module('yaml_validation')._settled_executor
        self.vendor = subject.PowercalcVendor(self.hass, with_profiles=True)
        self.cls = self.vendor.classes['VirtualEnergySensor']
        integration = self.vendor.integration.IntegrationSensor
        self.refs = ((Entity, '_call_on_remove_callbacks'),
                     (Entity, '_Entity__async_remove_impl'),
                     (RegistryEntry, 'write_unavailable_state'),
                     (StateMachine, 'async_set_internal'),
                     (EventBus, 'async_fire_internal'),
                     (integration, '_integrate_on_state_change_with_max_sub_interval'),
                     (integration, '_schedule_max_sub_interval_exceeded_if_state_is_numeric'),
                     (event, '_run_async_call_action'))
        pins = dict(audit._SOURCES[self.case.f.version])
        pins.update(self.vendor.ha_pins)
        def prove():
            self.vendor.sources()
            return proof.prove_methods(self.vendor.ha_root, pins, self.refs)
        self.prove = prove
        self.methods, self.codes = await executor(self.hass, prove)
        self.tags = {self.codes[0]: 'remove', self.codes[4]: 'fire',
                     self.codes[5]: 'changed', self.codes[6]: 'base'}
        self.timer_action = self.case.module('powercalc_operand_clock')._OperandTimerAction
        self.tags[self.timer_action.__call__.__code__] = 'operand_timer'
        self.unchanged = proof.unchanged

    def _ancestors(self, frame, code):
        current = frame.f_back
        try:
            while current is not None:
                if current.f_code is code:
                    yield dict(current.f_locals)
                current = current.f_back
        finally:
            del current

    def _engine(self, entity, *, endpoint=None, strict_timer=False):
        from homeassistant.core import State
        callback = entity._max_sub_interval_exceeded_callback
        handle = getattr(callback, '__self__', None)
        if strict_timer:
            assert type(handle) is asyncio.TimerHandle and not handle.cancelled()
            job = handle._args[1]
            action = job.target
            cells = dict(zip(action.__code__.co_freevars,
                             (cell.cell_contents for cell in action.__closure__), strict=True))
            assert cells['self'] is entity
            assert type(cells['source_state']) is State
            assert cells['source_state_dec'] == Decimal(cells['source_state'].state)
            if endpoint is not None:
                assert cells['source_state'] is endpoint
            endpoint = cells['source_state']
        if endpoint is None:
            endpoint = self.hass.states.get(entity._sensor_source_id)
        power = None
        if type(endpoint) is State:
            try:
                power = Decimal(endpoint.state)
            except Exception:
                pass
        assert type(entity._state) is Decimal and entity._state.is_finite()
        assert type(entity._last_valid_state) is Decimal and entity._last_valid_state.is_finite()
        return _EngineFacts(entity, entity._state, entity._last_valid_state,
            entity._last_integration_time, entity._last_integration_trigger.name,
            endpoint, None if endpoint is None else endpoint.last_reported, power,
            handle, None if type(handle) is not asyncio.TimerHandle else handle.when(),
            self.hass.loop.time(), entity._platform_state.name,
            entity._unit_prefix, entity._unit_time)

    def _capture_baseline(self, name, *, endpoint=None):
        if name not in self.baselines:
            self.baselines[name] = self._engine(self.old_e[name], endpoint=endpoint, strict_timer=True)

    def observe(self, frame, phase):
        try:
            self.unchanged(self.refs, self.methods, self.codes)
            tag = self.tags[frame.f_code]
            entity = frame.f_locals.get('self')
            if tag == 'operand_timer':
                if phase == 'start':
                    action = frame.f_locals['self']
                    assert type(action) is self.timer_action and action.owner.scope.hass is self.hass
                    dispatch = list(self._ancestors(frame, self.codes[7]))
                    assert any(row.get('hass') is self.hass
                               and getattr(row.get('job'), 'target', None) is action for row in dispatch)
                    self.timer_deliveries.append((action.name, self.hass.loop.time(), frame.f_locals['now']))
                return
            if tag == 'remove':
                name = getattr(entity, 'entity_id', None)
                if phase == 'start' and self.old_e.get(name) is entity:
                    self._capture_baseline(name)
                return
            if tag == 'fire':
                if phase != 'start' or entity is not self.hass.bus:
                    return
                data = frame.f_locals.get('event_data')
                if (frame.f_locals.get('event_type') != 'state_changed'
                        or type(data) is not dict or data.get('entity_id') not in self.energy_by_power):
                    return
                parent = frame.f_back
                assert parent is not None and parent.f_code is self.codes[3]
                assert parent.f_locals.get('self') is self.hass.states
                assert parent.f_locals.get('state_changed_data') is data
                state = data.get('new_state')
                if state is None:
                    return
                assert parent.f_locals.get('state') is state
                power_id, energy_id = data['entity_id'], self.energy_by_power[data['entity_id']]
                old = self.old_w[power_id]
                self.keep[id(data)] = data
                if state.state == 'unavailable' and state.attributes.get('restored') is True:
                    # Actual lifecycle proof, not the visible unavailable token.
                    registries = list(self._ancestors(frame, self.codes[2]))
                    removals = list(self._ancestors(frame, self.codes[1]))
                    if not registries and not removals:
                        return  # A different writer is never classified transport.
                    assert any(row.get('self') is old.registry_entry and row.get('hass') is self.hass
                               for row in registries)
                    assert any(row.get('self') is old for row in removals)
                    assert old._platform_state.name == 'REMOVED'
                    self._capture_baseline(energy_id, endpoint=data.get('old_state'))
                    self.transport[id(data)] = (energy_id, data, state)
                    return
                try:
                    value = Decimal(state.state)
                except Exception:
                    return
                if not value.is_finite() or energy_id in self.numeric:
                    return
                from homeassistant.helpers.entity_platform import async_get_platforms
                current = [w for p in async_get_platforms(self.hass, 'powercalc')
                           for w in p.entities.values() if w.entity_id == power_id]
                assert len(current) == 1 and current[0] is not old
                assert type(current[0]) is type(old)
                assert current[0].registry_entry.id == old.registry_entry.id
                producers = list(self._ancestors(frame,
                    self.vendor.classes['VirtualPowerSensor']._update_power_and_write_state.__code__))
                assert any(row.get('self') is current[0] for row in producers)
                # Keep authentic emission metadata frozen before any consumer.
                self.numeric[energy_id] = (data, state, state.last_updated,
                                           self.hass.loop.time(), current[0])
                return
            name = getattr(entity, 'entity_id', None)
            if name not in self.old_e or type(entity) is not self.cls:
                return
            if tag == 'base' and phase == 'start' and entity is not self.old_e[name]:
                parents = []
                current = frame.f_back
                try:
                    while current is not None:
                        parents.append(current.f_code.co_qualname)
                        current = current.f_back
                finally:
                    del current
                if 'IntegrationSensor.async_added_to_hass' in parents:
                    self.first_base.setdefault(name, (entity, frame.f_locals.get('source_state')))
                return
            if tag == 'changed' and phase == 'return':
                event = frame.f_locals['event']
                transport = self.transport.get(id(event.data))
                if entity is self.old_e[name] and transport is not None:
                    assert transport[0] == name and transport[1] is event.data
                    self.after_transport[name] = self._engine(entity)
                numeric = self.numeric.get(name)
                if (entity is not self.old_e[name] and numeric is not None
                        and event.data is numeric[0] and name not in self.cutovers):
                    assert event.data.get('new_state') is numeric[1]
                    assert entity.registry_entry.id == self.old_e[name].registry_entry.id
                    self.cutovers[name] = (self._engine(entity), event)
        except BaseException as error:
            if self.error is None:
                self.error = (frame.f_code.co_qualname, frame.f_lineno, type(error).__name__, str(error))

    def install(self):
        monitor = sys.monitoring
        free = [key for key in range(6) if monitor.get_tool(key) is None]
        assert free, 'no native monitoring slot; not a numerical RED'
        self.tool = free[0]
        monitor.use_tool_id(self.tool, 'renamer.test.direct_operand_clock')
        def started(code, offset):
            self.observe(sys._getframe(1), 'start')
        def returned(code, offset, result):
            self.observe(sys._getframe(1), 'return')
        monitor.register_callback(self.tool, monitor.events.PY_START, started)
        monitor.register_callback(self.tool, monitor.events.PY_RETURN, returned)
        for code in self.tags:
            monitor.set_local_events(self.tool, code, monitor.events.PY_START | monitor.events.PY_RETURN)

    def close(self):
        if self.tool is None:
            return
        monitor = sys.monitoring
        assert monitor.get_tool(self.tool) == 'renamer.test.direct_operand_clock'
        for code in self.tags:
            monitor.set_local_events(self.tool, code, 0)
        monitor.register_callback(self.tool, monitor.events.PY_START, None)
        monitor.register_callback(self.tool, monitor.events.PY_RETURN, None)
        monitor.free_tool_id(self.tool)
        self.tool = None


@unittest.skipUnless(prior.accepted.fixture.HAS_HA, 'actual supported HA required; never a PASS')
class NativePowercalcOperandClockRedTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = prior.PowercalcNumericContinuityNativeTests.namespace_source_bytes
    asyncSetUp = prior.PowercalcNumericContinuityNativeTests.asyncSetUp
    _setup_real_vendor = prior.PowercalcNumericContinuityNativeTests._setup_real_vendor
    _flush_documents = prior.PowercalcNumericContinuityNativeTests._flush_documents
    guard = prior.PowercalcNumericContinuityNativeTests.guard
    module = prior.PowercalcNumericContinuityNativeTests.module
    prepare_candidate = prior.PowercalcNumericContinuityNativeTests.prepare_candidate
    chain_root = prior.PowercalcNumericContinuityNativeTests.chain_root
    ready = prior.PowercalcNumericContinuityNativeTests.ready


    def _assert_operand_trace(self, receipt, *, name, calc, event_data, native_state):
        """Test-only causal readback, never an HA writer."""
        pairs = json.loads(receipt.scope_bindings_json)["direct_pairs"]
        expected_pairs = [
            [self.static_energy.entity_id, self.static_power.entity_id],
            [self.dynamic_energy.entity_id, self.dynamic_power.entity_id],
        ]
        self.assertCountEqual(pairs, expected_pairs,
            "both native fixture Energy-to-W edges must match the receipt")
        self.assertIn([name, calc.producer.entity_id], pairs)
        steps = json.loads(receipt.steps_json)
        operands = [(i, r) for i, r in enumerate(steps)
                    if r.get("kind") == "operand" and r.get("entity_id") == name]
        handoffs = [(i, r) for i, r in enumerate(steps)
                    if r.get("kind") == "native_handoff" and r.get("entity_id") == name]
        self.assertEqual(len(operands), 1)
        self.assertEqual(len(handoffs), 1)
        self.assertLess(operands[0][0], handoffs[0][0])
        row = operands[0][1]
        self.assertEqual(row["result"], id(calc))
        self.assertEqual(row["endpoint"], calc.update_returned_at.isoformat())
        self.assertEqual(row["power_before"], str(calc.previous_native_power))
        self.assertEqual(row["power_after"], str(calc.native_power))
        self.assertEqual(handoffs[0][1]["event_data"], id(event_data))
        self.assertEqual(handoffs[0][1]["native_source_state"], id(native_state))
        return {"energy_entity_id":name, "power_entity_id":calc.producer.entity_id}

    async def _actual_short_gap(self, *, selector):
        if selector:
            await self.selector.async_select_option('25%')
            await self.f.hass.async_block_till_done()
            await self.selector.async_select_option('Disabled')
            await self.f.hass.async_block_till_done()
        await self.ready()  # SAME actual chain; never call ready twice.
        facts = _ClockFacts(self)
        await facts.prepare()
        completed = self.f.hass.loop.create_future()
        completed.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        old_w = self.dynamic_power if selector else self.static_power
        seen = False
        async def actual_source_input(event):
            nonlocal seen
            state = event.data.get('new_state')
            if (seen or event.data.get('entity_id') != old_w.entity_id
                    or state is None or state.state != 'unavailable'
                    or state.attributes.get('restored') is not True):
                return
            seen = True
            try:
                if selector:
                    await self.selector.async_select_option('25%')
                else:
                    await self.sources[0].async_turn_on(brightness=1)
            except BaseException as error:
                completed.set_exception(error)
            else:
                completed.set_result(None)
        remove = self.f.hass.bus.async_listen('state_changed', actual_source_input)
        try:
            facts.install()
            result = await self.numeric.async_capture_powercalc_numeric_reload(
                self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard,
                exact_fields=True, retain_producers=True, operand_clock=True)
            self.assertIsNotNone(result.operand_clock,
                'two direct native tests must run the cumulative operand clock candidate')
            self.assertTrue(result.operand_clock.direct_operand_clock_verified)
            async with asyncio.timeout(8):
                await completed
            self.assertTrue(seen)
            self.assertIsNone(facts.error, 'observer failure is not a numerical RED: ' + repr(facts.error))
            executor = self.module('yaml_validation')._settled_executor
            self.assertEqual(await executor(self.f.hass, facts.prove), (facts.methods, facts.codes))
        finally:
            remove()
            facts.close()
        return result, facts

    async def _assert_direct_prefix(self, *, selector):
        result, facts = await self._actual_short_gap(selector=selector)
        old_e = self.dynamic_energy if selector else self.static_energy
        old_w = self.dynamic_power if selector else self.static_power
        name = old_e.entity_id
        self.assertEqual(set(facts.old_e), {self.static_energy.entity_id, self.dynamic_energy.entity_id})
        self.assertIn(name, facts.baselines)
        self.assertIn(name, facts.first_base)
        self.assertIn(name, facts.cutovers, 'fixture has no actual numeric emission/native consumer pair')
        first_source = facts.first_base[name][1]
        self.assertTrue(first_source is None or first_source.state in ('unknown', 'unavailable'),
                        'this RED isolates nonnumeric first BASE; numeric-setup cutover needs its own oracle')
        baseline = facts.baselines[name]
        observed, event = facts.cutovers[name]
        _, native_state, cutover_time, cutover_loop, _ = facts.numeric[name]
        self.assertIs(event.data['new_state'], native_state)
        self.assertIsNot(observed.entity, old_e)
        self.assertEqual(baseline.unit_prefix, Decimal(1000))
        self.assertEqual(baseline.unit_time, Decimal(3600))
        self.assertEqual(type(old_e._method).__name__, '_Left')
        self.assertTrue(baseline.source_power.is_finite())
        # A changed real W operand rearms its timer. Crossing the inherited
        # deadline is harmless when that obsolete callback never actually ran.
        # This two-segment oracle still refuses any genuine timer partition.
        self.assertFalse([row for row in facts.timer_deliveries
                          if row[0] == name and row[1] <= cutover_loop],
            'actual timer partition requires the extended independent interval oracle')
        outcomes = [row for row in result.retained_producers.calculations if row.producer is old_w]
        self.assertEqual(len(outcomes), 1)
        outcome = outcomes[0]
        self.assertTrue(outcome.native_value_changed and outcome.native_write_suppressed)
        self.assertEqual(outcome.previous_native_power, baseline.source_power)
        self.assertLessEqual(outcome.update_returned_at, cutover_time)
        self._assert_operand_trace(
            result.operand_clock, name=name, calc=outcome,
            event_data=event.data, native_state=native_state)
        # Declared observational operand clock: normalized W changes take effect
        # at actual native update-return; this is NOT a fictitious W State time.
        origin = baseline.anchor if baseline.trigger == 'TimeElapsed' else baseline.source_reported
        self.assertLess(origin, outcome.update_returned_at)
        # Independent left-rectangle oracle. Constants come from fixture W/kWh
        # configuration. Never use native calculate_area/_update_integral or a
        # product-reported area/elapsed to produce this expected result.
        expected = baseline.full
        expected += (Decimal((outcome.update_returned_at - origin).total_seconds())
                     * baseline.source_power) / Decimal(3600000)
        expected += (Decimal((cutover_time - outcome.update_returned_at).total_seconds())
                     * outcome.native_power) / Decimal(3600000)
        self.assertGreater(expected, baseline.full)
        print(json.dumps({'kind': 'direct_operand_prefix_short_window', 'entity_id': name,
            'baseline_full': str(baseline.full), 'baseline_anchor': baseline.anchor.isoformat(),
            'baseline_trigger': baseline.trigger, 'baseline_deadline': baseline.deadline,
            'update_returned_at': outcome.update_returned_at.isoformat(),
            'cutover_time': cutover_time.isoformat(), 'cutover_loop': cutover_loop,
            'old_power': str(baseline.source_power), 'retained_power': str(outcome.native_power),
            'expected': str(expected), 'actual': str(observed.full),
            'first_base_source': (None if facts.first_base[name][1] is None
                                  else facts.first_base[name][1].state)}))
        self.assertEqual(observed.full, expected,
            'lost actual operand prefix before first real new-W numeric native consumer')
        self.assertEqual(observed.last_valid, expected)
        self.assertFalse(result.continuity_verified)  # No family/group claim.

    async def test_static_direct_operand_prefix_at_actual_numeric_cutover_RED(self):
        await self._assert_direct_prefix(selector=False)

    async def test_selector_direct_operand_prefix_at_actual_numeric_cutover_RED(self):
        await self._assert_direct_prefix(selector=True)
