"""One same-ORIGINAL GROUP/SUBTRACT head qualifier, actual current Core only.

Read-only native monitoring; expected values stay in test-owned dictionaries.
The initial heads and previous samples are real, not seeded. No vendor callback,
State/Event/Task, timer/cache or native field is replaced. Owner runs via FIFO.
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import sys
import unittest

import test_powercalc_operand_clock_native as direct


def _utc():
    return datetime.now(timezone.utc)


def _seconds(end, start):
    # Match the installed Core's timestamp conversion, independently of its
    # computed elapsed/area/scaled result. No product calculation is an oracle.
    return Decimal((end - start).total_seconds())


def _numeric(state, unit):
    if state is None:
        raise AssertionError('missing actual child State')
    assert state.attributes.get('unit_of_measurement') == unit
    value = Decimal(state.state)
    assert value.is_finite()
    return value


def _uncovered(start, end, intervals):
    """Exact datetime geometry, without measuring from product counters."""
    cursor, gaps = start, []
    for left, right in sorted(intervals):
        left, right = max(left, start), min(right, end)
        if right <= left:
            continue
        if left > cursor:
            gaps.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


class _GroupHeadFacts:
    """Source-pinned facts about the natural group/store/native integral paths."""
    def __init__(self, case):
        self.case, self.hass = case, case.f.hass
        self.old_group, self.old_subtract = case.sum_energy, case.subtract_energy
        self.group_id, self.subtract_id = self.old_group.entity_id, self.old_subtract.entity_id
        self.store = self.old_group._prev_state_store
        self.store_states = self.store.states
        self.members = frozenset((case.static_energy.entity_id, case.dynamic_energy.entity_id))
        assert frozenset(self.old_group._entities) == self.members
        self.rows, self.store_rows, self.group_heads, self.subtract_heads = [], [], [], []
        self.signed_rows = []
        self.delta_stack, self.integral_stack = [], []
        self.intervals, self.seen_intervals = [], set()
        self.baseline = self.reference = self.departure = self.first_new = None
        self.quiet_until = None
        self.new_integral = self.hass.loop.create_future()
        self.new_groups, self.checkpoints = [], []
        self.last_new_engine = None
        self.error, self.tool = None, None

    def _capture_observation_baseline(self):
        # All source-pin IO completed before this synchronous first observation.
        # Only test-owned facts are assigned; ORIGINAL receipt is never replaced.
        case = self.case
        assert self.old_group._sensor_config.get('group_energy_start_at_zero', True) is True
        assert self.old_group._attr_native_unit_of_measurement == 'kWh'
        assert type(self.old_group._native_value_exact) is Decimal and self.old_group._native_value_exact > 0
        self.group_ref = self.old_group._native_value_exact
        self.prior = dict(self.store.states[self.group_id])
        assert set(self.prior) == self.members
        self.prior_values = {key: _numeric(state, 'kWh') for key, state in self.prior.items()}
        self.initial_head = self.group_ref
        self.initial_prior = {key: str(value) for key, value in self.prior_values.items()}
        child_w = tuple(_numeric(self.hass.states.get(w.entity_id), 'W')
                        for w in (case.static_power, case.dynamic_power))
        # Signed source graph: (static + dynamic) - dynamic. Actual child inputs,
        # never the native subtract return or any product operand-area receipt.
        self.held_power = (child_w[0] + child_w[1]) - child_w[1]
        assert self.held_power > 0
        assert _numeric(self.hass.states.get(case.sum_power.entity_id), 'W') == sum(child_w)
        assert _numeric(self.hass.states.get(case.subtract_power.entity_id), 'W') == self.held_power
        assert self.old_subtract._sensor_source_id == case.subtract_power.entity_id
        assert self.old_subtract._unit_prefix == 1000 and self.old_subtract._unit_time == 3600
        assert not self.old_subtract._filter_outliers

    async def prepare(self):
        from homeassistant.core import EventBus
        from homeassistant.helpers.entity import Entity
        self.vendor = self.case.module('powercalc_source_binding').PowercalcVendor(self.hass, with_profiles=True)
        proof = self.case.module('native_unload_code_proof')
        self.unchanged = proof.unchanged
        classes = self.vendor.classes
        self.integration = self.vendor.integration.IntegrationSensor
        self.vrefs = ((classes['GroupedEnergySensor'], 'async_added_to_hass'),
            (classes['GroupedEnergySensor'], 'restore_last_state'),
            (classes['GroupedEnergySensor'], 'calculate_delta'),
            (classes['GroupedSensor'], '_set_native_value'),
            (classes['PreviousStateStore'], 'get_entity_state'),
            (classes['PreviousStateStore'], 'set_entity_state'),
            (classes['PreviousStateStore'], 'cleanup_entity_states'),
            (classes['SubtractGroupSensor'], 'get_summed_state'))
        self.hrefs = ((Entity, '_call_on_remove_callbacks'),
            (self.integration, '_update_integral'), (self.integration, '_integrate_on_state_change'),
            (self.integration, '_schedule_max_sub_interval_exceeded_if_state_is_numeric'),
            (EventBus, 'async_fire_internal'))
        pins = dict(self.case.module('native_utility_setup_audit')._SOURCES['2026.10.2'])
        pins.update(self.vendor.ha_pins)
        def prove():
            self.vendor.sources()
            vm, vc = proof.prove_methods(self.vendor.root,
                self.case.module('powercalc_source_binding')._pins(), self.vrefs)
            hm, hc = proof.prove_methods(self.vendor.ha_root, pins, self.hrefs)
            return vm, vc, hm, hc
        self.prove = prove
        self.vm, self.vc, self.hm, self.hc = await self.case.module('yaml_validation')._settled_executor(
            self.hass, prove)
        self.tags = dict(zip(self.vc + self.hc, ('added', 'restore', 'delta', 'group_set',
            'store_get', 'store_set', 'store_cleanup', 'subtract_w', 'remove', 'integral',
            'state_math', 'schedule', 'fire'), strict=True))
        timer = [c for c in self.case.module('native_utility_setup_audit')._code_objects(self.hc[3])
                 if c.co_name == '_integrate_on_max_sub_interval_exceeded_callback']
        assert len(timer) == 1
        self.timer_code = timer[0]
        assert type(self.old_subtract._method) is self.vendor.integration._Left

    def _ancestors(self, frame):
        node = frame.f_back
        try:
            while node is not None:
                yield node
                node = node.f_back
        finally:
            del node

    def _interval(self, frame, entity):
        for node in self._ancestors(frame):
            local = node.f_locals
            if node.f_code is self.timer_code and local.get('self') is entity:
                return entity._last_integration_time, local['now'], _numeric(local['source_state'], 'W'), 'timer'
            if node.f_code is self.hc[2] and local.get('self') is entity:
                end = local['new_timestamp']
                start = (entity._last_integration_time
                         if entity._last_integration_trigger.name == 'TimeElapsed' else local['old_timestamp'])
                power = Decimal(local['old_state_state'])
                assert power.is_finite() and start is not None and end is not None
                return start, end, power, 'source'
            owner_type = self.case.module('powercalc_subtract_clock')._SubtractClockOwner
            if node.f_code is owner_type._handoff.__code__:
                owner = local.get('self')
                assert type(owner) is owner_type and owner.scope.hass is self.hass
                assert owner.c.old is self.old_subtract and owner.c.engine is entity
                assert owner.target is entity and owner.phase == 'integrating' and owner.depth == 1
                assert owner.quiet() and owner.original_state.entity_id == self.case.subtract_power.entity_id
                assert _numeric(owner.original_state, 'W') == self.held_power
                # Native arithmetic's actual typed producer and observed endpoints
                # are qualified here. Expected area still comes from independent
                # child W and datetime geometry; no receipt/area/HEAD is an oracle.
                assert owner.c.value == self.held_power
                assert owner.c.old.entity_id in owner.scope.removed and not owner.c.old._on_remove
                end = local['end']
                assert type(end) is datetime and end >= owner.c.anchor
                return owner.c.anchor, end, self.held_power, 'retained_signed_quiet'
        raise AssertionError('unqualified subtract integral producer; no replacement oracle')

    def observe(self, frame, phase, returned=None):
        try:
            self.unchanged(self.vrefs, self.vm, self.vc)
            self.unchanged(self.hrefs, self.hm, self.hc)
            tag, local = self.tags[frame.f_code], frame.f_locals
            entity = local.get('self')
            if tag == 'fire':
                data = local.get('event_data')
                if (phase == 'start' and entity is self.hass.bus and type(data) is dict
                        and local.get('event_type') == 'state_changed'
                        and data.get('entity_id') == self.case.yaml_source):
                    state = data.get('new_state')
                    if state is not None and state.attributes.get('brightness') == 1:
                        self.quiet_until = state.last_updated
                return
            if tag == 'subtract_w':
                if phase == 'return' and getattr(entity, 'entity_id', None) == self.case.subtract_power.entity_id:
                    assert entity._base_entity_id == self.case.sum_power.entity_id
                    assert tuple(entity._subtract_entities) == (self.case.dynamic_power.entity_id,)
                    values = dict(entity._member_states)
                    base = values.get(entity._base_entity_id)
                    if base is not None:
                        expected = base - sum((values.get(key, Decimal(0))
                            for key in entity._subtract_entities), Decimal(0))
                        self.signed_rows.append({'actual': str(returned), 'expected': str(expected),
                            'inputs': {key: str(value) for key, value in values.items()}})
                return
            if tag.startswith('store_'):
                if entity is not self.store or local.get('group') != self.group_id:
                    return
                row = {'kind': tag, 'phase': phase, 'store': id(entity),
                       'states': id(entity.states), 'keys': sorted(entity.states.get(self.group_id, {}))}
                if tag == 'store_get' and phase == 'return':
                    key = local['entity_id']
                    row.update(child=key, expected_prior=id(self.prior.get(key)), actual_prior=id(returned))
                if tag == 'store_cleanup':
                    row['members'] = sorted(local['current_entities'])
                self.store_rows.append(row)
                return
            if tag == 'remove' and entity is self.old_subtract:
                if phase == 'start':
                    assert self.baseline is None, 'duplicate old subtract retirement'
                    assert type(entity._state) is Decimal and entity._state.is_finite()
                    self.baseline = entity._state
                    self.reference = self.baseline
                else:
                    assert not entity._on_remove
                    self.departure = _utc()
                return
            name = getattr(entity, 'entity_id', None)
            if name == self.group_id and type(entity) is self.vendor.classes['GroupedEnergySensor']:
                if tag == 'added' and phase == 'return' and entity is not self.old_group:
                    self.new_groups.append((entity, entity._prev_state_store, entity._prev_state_store.states))
                elif tag == 'restore' and phase == 'return':
                    self.rows.append({'kind': 'restore_head', 'actual': str(entity._native_value_exact),
                                      'expected': str(self.group_ref), 'entity': id(entity)})
                elif tag == 'delta':
                    state = local['state']
                    assert state.entity_id in self.members
                    if phase == 'start':
                        current = _numeric(state, 'kWh')
                        previous = self.prior_values.get(state.entity_id)
                        delta = Decimal(0) if previous is None else max(current - previous, Decimal(0))
                        self.delta_stack.append((entity, state, current, delta))
                    else:
                        owner, actual_state, value, delta = self.delta_stack.pop()
                        assert owner is entity and actual_state is state
                        self.group_ref += delta
                        self.prior[state.entity_id], self.prior_values[state.entity_id] = state, value
                        self.rows.append({'kind': 'child_input', 'child': state.entity_id,
                            'value': str(value), 'independent_delta': str(delta), 'expected_head': str(self.group_ref)})
                elif tag == 'group_set' and phase == 'return':
                    self.group_heads.append((entity._native_value_exact, self.group_ref))
                return
            if name != self.subtract_id or type(entity) is not self.vendor.classes['VirtualEnergySensor']:
                return
            if tag == 'integral' and self.baseline is not None:
                if phase == 'start':
                    start, end, power, kind = self._interval(frame, entity)
                    # Preserve signed native timestamps. Backward intervals do
                    # not cover the positive quiet window in the geometry oracle.
                    duplicate = (start, end, power) in self.seen_intervals
                    # Exact duplicate intervals are not counted again in the
                    # independent reference. This catches actual double-count.
                    self.seen_intervals.add((start, end, power))
                    increment = Decimal(0) if duplicate else power * _seconds(end, start) / Decimal(3600000)
                    self.integral_stack.append((entity, start, end, power, kind, increment, duplicate))
                else:
                    owner, start, end, power, kind, increment, duplicate = self.integral_stack.pop()
                    assert owner is entity
                    self.reference += increment
                    self.intervals.append((start, end))
                    row = {'kind': kind, 'entity': id(entity), 'start': start.isoformat(),
                        'end': end.isoformat(), 'actual_child_w': str(power),
                        'increment': str(increment), 'duplicate_interval': duplicate,
                        'actual': str(entity._state), 'reference_without_missing_gap': str(self.reference)}
                    self.subtract_heads.append(row)
                    if (entity is not self.old_subtract and self.first_new is None
                            and self.departure is not None and end > self.departure):
                        self.first_new = (end, entity._state, self.reference, tuple(self.intervals))
                        if not self.new_integral.done():
                            self.new_integral.set_result(None)
                    if entity is not self.old_subtract and self.departure is not None and end > self.departure:
                        self.last_new_engine = entity
                        self.checkpoints.append((end, entity._state, self.reference, tuple(self.intervals)))
        except BaseException as error:
            if self.error is None:
                self.error = (frame.f_code.co_qualname, frame.f_lineno, type(error).__name__, str(error))
            if not self.new_integral.done():
                self.new_integral.set_result(None)

    def install(self):
        self._capture_observation_baseline()
        monitor = sys.monitoring
        free = [i for i in range(6) if monitor.get_tool(i) is None]
        assert free, 'no monitoring slot; not a numerical product RED'
        self.tool = free[0]
        monitor.use_tool_id(self.tool, 'renamer.test.group_head')
        def start(code, offset):
            self.observe(sys._getframe(1), 'start')
        def done(code, offset, returned):
            self.observe(sys._getframe(1), 'return', returned)
        monitor.register_callback(self.tool, monitor.events.PY_START, start)
        monitor.register_callback(self.tool, monitor.events.PY_RETURN, done)
        for code in self.tags:
            monitor.set_local_events(self.tool, code, monitor.events.PY_START | monitor.events.PY_RETURN)

    def capture_final(self):
        # Native public platform inventory; no constructing/replacing entities.
        from homeassistant.helpers.entity_platform import async_get_platforms
        found = [entity for platform in async_get_platforms(self.hass, 'powercalc')
                 for entity in platform.entities.values()
                 if entity.entity_id == self.subtract_id
                 and type(entity) is self.vendor.classes['VirtualEnergySensor']]
        assert len(found) == 1 and found[0] is self.last_new_engine
        end = self.checkpoints[-1][0]
        self.checkpoints.append((end, found[0]._state, self.reference, tuple(self.intervals)))

    def close(self):
        if self.tool is None:
            return
        monitor = sys.monitoring
        assert monitor.get_tool(self.tool) == 'renamer.test.group_head'
        for code in self.tags:
            monitor.set_local_events(self.tool, code, 0)
        monitor.register_callback(self.tool, monitor.events.PY_START, None)
        monitor.register_callback(self.tool, monitor.events.PY_RETURN, None)
        monitor.free_tool_id(self.tool)
        self.tool = None

    def assert_oracles(self):
        case = self.case
        case.assertIsNone(self.error, 'source/observer qualification failure, not a numeric gap: ' + repr(self.error))
        case.assertIsNotNone(self.first_new, 'missing real subtract callback; no numeric PASS')
        end, actual, reference, intervals = self.first_new
        if self.quiet_until is not None:
            end = min(end, self.quiet_until)
        case.assertGreater(end, self.departure, 'no observed positive no-owner interval')
        gaps = _uncovered(self.departure, end, intervals)
        missing = sum((self.held_power * _seconds(right, left) / Decimal(3600000)
                       for left, right in gaps), Decimal(0))
        expected = reference + missing
        diagnostics = {'kind': 'powercalc_group_subtract_head_independent_oracle',
            'group_initial_head': str(self.initial_head), 'group_initial_prior': self.initial_prior,
            'group_expected_final': str(self.group_ref), 'group_heads': [(str(a), str(e)) for a, e in self.group_heads],
            'store': self.store_rows, 'group_inputs': self.rows, 'signed_power': self.signed_rows,
            'subtract_old_head': str(self.baseline), 'signed_actual_child_w': str(self.held_power),
            'departure': self.departure.isoformat(), 'quiet_endpoint': end.isoformat(),
            'uncovered_intervals': [(a.isoformat(), b.isoformat()) for a, b in gaps],
            'independent_missing_area_kwh': str(missing),
            'subtract_expected': str(expected), 'subtract_actual': str(actual),
            'subtract_difference_kwh': str(expected - actual), 'native_integral_inputs': self.subtract_heads,
            'later_and_final_heads': [(point.isoformat(), str(head), str(ref))
                for point, head, ref, covered in self.checkpoints]}
        print(json.dumps(diagnostics, sort_keys=True))
        # Numerical checks lead, so UNKNOWN/false flags cannot stand in for a gap.
        case.assertTrue(self.group_heads, 'no actual group head writes')
        for actual_group, expected_group in self.group_heads:
            case.assertEqual(actual_group, expected_group, 'group head lost/doubled an actual positive child delta')
        for row in self.rows:
            if row['kind'] == 'restore_head':
                case.assertEqual(Decimal(row['actual']), Decimal(row['expected']), 'native group restore changed prior full HEAD')
        case.assertEqual(actual, expected, 'subtract HEAD lost uncovered real reload time or counted an input interval twice')
        # Include all later real integrals and a final native object read. A later
        # field overwrite must not hide behind an earlier successful callback.
        for point, head, independent, covered in self.checkpoints:
            point = min(point, self.quiet_until) if self.quiet_until is not None else point
            missing_later = sum((self.held_power * _seconds(right, left) / Decimal(3600000)
                for left, right in _uncovered(self.departure, point, covered)), Decimal(0))
            case.assertEqual(head, independent + missing_later, 'later subtract full HEAD lost/doubled energy')
        case.assertTrue(self.signed_rows, 'no actual signed grouped W calculation')
        for row in self.signed_rows:
            case.assertEqual(Decimal(row['actual']), Decimal(row['expected']))
        case.assertTrue(self.new_groups, 'no actual new group added')
        for entity, store, states in self.new_groups:
            case.assertIs(store, self.store, 'normal reload must traverse the same native PreviousStateStore singleton')
            case.assertIs(states, self.store_states)
            case.assertEqual(frozenset(entity._entities), self.members)
        for row in self.store_rows:
            case.assertEqual(row['store'], id(self.store))
            case.assertEqual(row['states'], id(self.store_states))
            if row['kind'] == 'store_get' and row['phase'] == 'return':
                case.assertEqual(row['actual_prior'], row['expected_prior'], 'native prior State pointer lost/replaced')
            if row['kind'] == 'store_cleanup':
                case.assertEqual(frozenset(row['members']), self.members)
        case.assertEqual(frozenset(self.store.states[self.group_id]), self.members)
        for key, state in self.prior.items():
            case.assertIs(self.store.states[self.group_id][key], state)
        case.assertTrue(any(row['kind'] == 'restore_head' for row in self.rows), 'native group restore not traversed')
        case.assertTrue(any(row['kind'] == 'store_cleanup' for row in self.store_rows))
        case.assertTrue(any(row['kind'] == 'store_get' for row in self.store_rows))
        case.assertTrue(any(row['kind'] == 'store_set' for row in self.store_rows))


@unittest.skipUnless(direct.prior.accepted.fixture.HAS_HA, 'actual supported HA required; never a PASS')
class NativePowercalcGroupHeadTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = direct.NativePowercalcOperandClockRedTests.namespace_source_bytes
    asyncSetUp = direct.NativePowercalcOperandClockRedTests.asyncSetUp
    _setup_real_vendor = direct.NativePowercalcOperandClockRedTests._setup_real_vendor
    _flush_documents = direct.NativePowercalcOperandClockRedTests._flush_documents
    guard = direct.NativePowercalcOperandClockRedTests.guard
    module = direct.NativePowercalcOperandClockRedTests.module
    prepare_candidate = direct.NativePowercalcOperandClockRedTests.prepare_candidate
    chain_root = direct.NativePowercalcOperandClockRedTests.chain_root
    ready = direct.NativePowercalcOperandClockRedTests.ready

    async def test_group_previous_samples_and_subtract_full_head_survive_same_original_reload(self):
        await self.ready()
        # Wait only for the existing real native deadline, without changing it,
        # padding time, invoking its callback or creating another native clock.
        old = self.subtract_energy
        before = old._state
        handle = old._max_sub_interval_exceeded_callback.__self__
        self.assertIs(type(handle), asyncio.TimerHandle)
        self.assertFalse(handle.cancelled())
        await asyncio.sleep(max(0, handle.when() - self.f.hass.loop.time()))
        await self.f.hass.async_block_till_done()
        self.assertGreater(old._state, before, 'real warm-up timer did not advance the subtract head')
        facts = _GroupHeadFacts(self)
        await facts.prepare()
        original_head = self.chain.reconstruct_inputs().head
        original_receipt_json = self.chain.baseline.receipt_json
        result = None
        try:
            facts.install()
            result = await self.numeric.async_capture_powercalc_numeric_reload(
                self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard,
                exact_fields=True, retain_producers=True, operand_clock=True)
            # One normal public Light stimulus after native reload, not a direct
            # calculator call. It supplies real signed child input/time endpoints.
            await self.sources[0].async_turn_on(brightness=1)
            await self.f.hass.async_block_till_done()
            await asyncio.shield(facts.new_integral)
            self.assertIsNone(facts.error, 'observer/fixture failure: ' + repr(facts.error))
            executor = self.module('yaml_validation')._settled_executor
            self.assertEqual(await executor(self.f.hass, facts.prove),
                             (facts.vm, facts.vc, facts.hm, facts.hc))
            facts.capture_final()
        finally:
            facts.close()
        self.assertEqual(result.chain_head, original_head)
        self.assertEqual(self.chain.reconstruct_inputs().head, original_head)
        self.assertEqual(self.chain.baseline.receipt_json, original_receipt_json)
        self.assertEqual(result.evidence_sha256, self.evidence.sha256)
        self.assertEqual(result.original_runtime_token, self.original_runtime.structural_token)
        self.assertFalse(result.migration_ready)
        facts.assert_oracles()
        quiet = json.loads(result.operand_clock.scope_bindings_json)['subtract_quiet_handoff']
        self.assertEqual(quiet['phase'], 'native')
        self.assertEqual(len(quiet['steps']), 1)
        self.assertEqual(quiet['steps'][0]['old_engine'], id(facts.old_subtract))
        self.assertEqual(quiet['steps'][0]['new_engine'], id(facts.last_new_engine))


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativePowercalcGroupHeadTests)
