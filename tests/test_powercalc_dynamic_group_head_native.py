"""One real dynamic SAME ORIGINAL GROUP/SUBTRACT qualifier; owner runs native.

Public Light changes the removed native producer while genuine custom JSON IO
is pending. No State/Event/clock/producer/timer/counter is fabricated or patched.
Missing stimulus/provenance is qualification failure, never a numeric RED.
"""
import asyncio
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

import test_powercalc_group_head_native as group
import test_powercalc_operand_timer_native as timers

_SUBTRACT_SHA256 = "f14231d960275963bfbb376745562a9308d8e2a330f2b247070d68b5986618d5"


class _DynamicIOFacts(timers._CustomTimerFacts):
    """Reuse actual executor/JSON/thread proof, with a dynamic IO prerequisite.

The unchanged timer fixture requires BOTH FIRED before read resume. This new
case changes W before then, legitimately cancelling/rearming its old issuance;
it instead requires an actual retained native result before this same resume.
The shared fixture and its accepted quiet timer assertions are not modified.
"""
    def _observe_io(self, frame, phase, result):
        tag = self.tags[frame.f_code]
        if tag == 'custom_load' and phase == 'resume' and self._io_loader(frame.f_locals.get('self')):
            self._runtime_unchanged()
            row = self._io_row(frame.f_locals['self'])
            assert asyncio.current_task() is row['task']
            row['resumes'].append(self.hass.loop.time())
            if row['qualifying']:
                assert row.get('yielded') is row.get('future')
                assert self.case.stimulus_task is not None and self.case.stimulus_task.done()
                assert self.case.stimulus_task.exception() is None
                scope = self.deadline_wait['scope']
                outcomes = [r for r in scope.producer_owner.results
                            if r.producer is self.case.static_power and r.native_value_changed
                            and r.input_state is self.case.stimulus_state]
                assert len(outcomes) == 1, 'qualification: no actual retained result during pending native IO'
                row['dynamic_result'] = outcomes[0]
                assert not self.numeric and not self.cutovers
            return
        super()._observe_io(frame, phase, result)
        if tag == 'custom_load' and phase == 'start' and self._io_loader(frame.f_locals.get('self')):
            row = self._io_row(frame.f_locals['self'])
            if row['qualifying'] and self.case.stimulus_task is None:
                # Queue before the native function submits its executor job.
                # This normal task runs outside monitoring, after its genuine
                # pending PY_YIELD, so native producer bodies stay observable.
                self.case.stimulus_task = self.hass.async_create_task(
                    self.case.change_during_real_io(self, row),
                    'test.dynamic-group-public-Light', eager_start=False)


class _DynamicGroupFacts(group._GroupHeadFacts):
    async def prepare(self):
        await super().prepare()
        cls = self.vendor.classes['VirtualPowerSensor']
        self.vrefs += ((cls, 'calculate_power'), (cls, '_update_power_and_write_state'))
        self.vm, self.vc, self.hm, self.hc = await self.case.module('yaml_validation')._settled_executor(
            self.hass, self.prove)
        self.tags.update({self.vc[-2]: 'dynamic_calculate', self.vc[-1]: 'dynamic_update'})
        self.calculations, self.updates, self.open_updates = [], [], {}
        self.native_source_boundary = None
        module = self.case.module('powercalc_subtract_clock')
        path = Path(module.__file__).resolve()
        proof = self.case.module('native_unload_code_proof')
        self.dynamic_refs = ((module._SubtractClockOwner, '_dynamic_area'),)
        def dynamic_prove():
            return proof.prove_methods(path.parent, {path.name: _SUBTRACT_SHA256}, self.dynamic_refs)
        self.dynamic_prove = dynamic_prove
        self.dynamic_methods, self.dynamic_codes = await self.case.module('yaml_validation')._settled_executor(
            self.hass, dynamic_prove)

    def _capture_observation_baseline(self):
        super()._capture_observation_baseline()
        self.initial_children = {w.entity_id: group._numeric(self.hass.states.get(w.entity_id), 'W')
            for w in (self.case.static_power, self.case.dynamic_power)}

    def _interval(self, frame, entity):
        for node in self._ancestors(frame):
            if node.f_code is self.dynamic_codes[0]:
                self.unchanged(self.dynamic_refs, self.dynamic_methods, self.dynamic_codes)
                local, module = node.f_locals, self.case.module('powercalc_subtract_clock')
                owner = local['self']
                assert type(owner) is module._SubtractClockOwner and owner.c.engine is entity
                assert owner.c.old is self.old_subtract and owner.depth == 1
                start, end = local['start'], local['end']
                result = local['result']
                if result is not None:
                    assert result is owner.scope.producer_owner.results[-1]
                    assert end is result.update_returned_at
                else:
                    assert end is local['source_state'].last_updated
                # Operand values come from actual original children and native
                # retained results, not the product's computed signed operand.
                values = dict(self.initial_children)
                for actual in owner.scope.producer_owner.results:
                    if actual.update_returned_at <= start and actual.producer.entity_id in values:
                        values[actual.producer.entity_id] = actual.native_power
                power = sum(values.values(), Decimal(0)) - values[self.case.dynamic_power.entity_id]
                return start, end, power, 'actual_retained_signed_operand'
        return super()._interval(frame, entity)

    def observe(self, frame, phase, returned=None):
        tag, entity = self.tags[frame.f_code], frame.f_locals.get('self')
        try:
            if tag in ('dynamic_calculate', 'dynamic_update'):
                self.unchanged(self.vrefs, self.vm, self.vc)
                if entity is not self.case.static_power:
                    return
                if tag == 'dynamic_calculate' and phase == 'return':
                    self.calculations.append((frame.f_locals.get('state'), returned))
                elif tag == 'dynamic_update':
                    if phase == 'start':
                        self.open_updates[id(frame)] = (frame.f_locals['power'], entity._power)
                    else:
                        argument, previous = self.open_updates.pop(id(frame))
                        self.updates.append((argument, previous, entity._power, entity._attr_available))
                return
            if (entity is not self.old_subtract and type(entity) is self.vendor.classes['VirtualEnergySensor']
                    and entity.entity_id == self.subtract_id and phase == 'start'
                    and tag in ('schedule', 'state_math') and self.native_source_boundary is None):
                state = frame.f_locals.get('source_state' if tag == 'schedule' else 'new_state')
                if state is not None and state.state not in ('unknown', 'unavailable'):
                    group._numeric(state, 'W')
                    assert state.entity_id == self.case.subtract_power.entity_id
                    self.native_source_boundary = (state.last_updated, state, entity)
        except BaseException as error:
            self.error = ('dynamic_source_qualification', type(error).__name__, str(error))
            if not self.new_integral.done():
                self.new_integral.set_result(None)
            return
        super().observe(frame, phase, returned)

    def qualify(self, result, io):
        case = self.case
        case.assertIsNone(self.error, 'qualification failure, not product RED: ' + repr(self.error))
        io.assert_custom_read()
        case.assertIsNotNone(self.first_new, 'qualification: missing actual new subtract integral')
        case.assertIsNotNone(self.native_source_boundary, 'qualification: no genuine first native source clock')
        scope = io.deadline_wait['scope']
        clock = scope.clock_owner.subtract
        self.retired = clock.c.anchor  # Actual recorded retirement UTC, no counter/area.
        self.boundary, source, engine = self.native_source_boundary
        case.assertIs(clock.c.old, self.old_subtract)
        case.assertIs(engine, self.last_new_engine)
        case.assertIs(scope.original, case.original_runtime)
        case.assertIs(scope.evidence, case.evidence)
        case.assertEqual(result.chain_head, case.original_head)
        row = io.io_rows[0]
        case.assertLessEqual(row['yield_loop'], row['stimulus_pending_start_loop'])
        case.assertLessEqual(row['stimulus_pending_start_loop'], row['stimulus_pending_end_loop'])
        case.assertLessEqual(row['stimulus_pending_end_loop'], row['resumes'][0])
        outcome = row['dynamic_result']
        case.assertTrue(any(r is outcome for r in result.retained_producers.calculations))
        case.assertIs(outcome.producer, case.static_power)
        case.assertIs(outcome.input_state, case.stimulus_state)
        case.assertEqual(outcome.ownership_phase, 'retained_removed')
        case.assertTrue(outcome.available and outcome.native_value_changed and outcome.native_write_suppressed)
        case.assertNotEqual(outcome.previous_native_power, outcome.native_power)
        case.assertEqual(outcome.previous_native_power, self.initial_children[case.static_power.entity_id])
        case.assertTrue(any(state is outcome.input_state and value is outcome.calculated_value
                            for state, value in self.calculations), 'qualification: native calculate_power body missing')
        case.assertTrue(any(argument is outcome.update_argument and previous == outcome.previous_native_power
                            and power is outcome.native_power and available for argument, previous, power, available
                            in self.updates), 'qualification: native update body missing')
        case.assertLess(self.retired, outcome.update_returned_at)
        case.assertLess(outcome.update_returned_at, self.boundary)
        case.assertLessEqual(case.stimulus_state.last_updated, outcome.update_returned_at)
        self.changes = [(outcome.update_returned_at, outcome.producer.entity_id, outcome.native_power)]
        case.assertNotEqual(self.signed(self.retired), self.signed(outcome.update_returned_at),
                            'qualification: changed child did not change signed reload power')
        self.typed_interval = result.operand_clock.subtract_interval
        actual_intervals = result.operand_clock.subtract_intervals
        if actual_intervals:
            case.assertIsNone(self.typed_interval)
            case.assertEqual(clock.phase, 'native')
            case.assertIs(clock.dynamic_source_state, source)
            module = case.module('powercalc_subtract_clock')
            prefix_rows, pairs = [], []
            prior_end = self.retired
            for fact in actual_intervals:
                case.assertIs(type(fact), module.PowercalcSubtractOperandInterval)
                case.assertIs(fact.owner, clock)
                case.assertTrue(any(fact is issued for issued in clock.dynamic_intervals))
                case.assertIs(fact.start, prior_end)
                case.assertLessEqual(fact.end, self.boundary)
                case.assertIs(fact.native_update, self.integration._update_integral)
                case.assertIs(fact.native_one_state, self.vendor.integration._IntegrationMethod.calculate_area_with_one_state)
                case.assertTrue(all(any(r is actual for actual in result.retained_producers.calculations)
                                    for r in fact.operand_results))
                case.assertIs(dict(fact.physical_inputs)[case.stimulus_state.entity_id], case.stimulus_state)
                if fact.result is not None:
                    case.assertIs(fact.result, outcome)
                    case.assertIs(fact.end, outcome.update_returned_at)
                    case.assertIs(fact.engine, self.old_subtract)
                else:
                    case.assertIs(fact.source_state, source)
                    case.assertIs(fact.end, source.last_updated)
                    case.assertIs(fact.engine, engine)
                power = self.signed(fact.start)
                key = (fact.start, fact.end, power)
                observed = [r for r in self.subtract_heads
                    if datetime.fromisoformat(r['start']) == fact.start
                    and datetime.fromisoformat(r['end']) == fact.end
                    and Decimal(r['actual_child_w']) == power]
                case.assertLessEqual(len(observed), 1, 'actual interval was observed more than once')
                prefix_rows.append(observed[0] if observed else {
                    'kind': 'actual_retained_signed_operand', 'entity': id(fact.engine),
                    'start': fact.start.isoformat(), 'end': fact.end.isoformat(),
                    'actual_child_w': str(power), 'duplicate_interval': False,
                    'provenance': 'completed_native_operand_interval'})
                self.seen_intervals.add(key)
                pairs.append((fact.start, fact.end))
                prior_end = fact.end
            case.assertIs(prior_end, self.boundary)
            case.assertIs(clock.c.anchor, self.retired, 'retirement anchor was rebased')
            case.assertIs(clock.cursor, self.boundary)
            remaining = [r for r in self.subtract_heads
                         if (datetime.fromisoformat(r['start']), datetime.fromisoformat(r['end'])) not in pairs]
            case.assertTrue(all(datetime.fromisoformat(r['start']) >= self.boundary for r in remaining),
                            'qualification: partial native/owned overlap requires separate proof')
            self.subtract_heads = prefix_rows + remaining
            def coverage(covered):
                return (*pairs, *(pair for pair in covered if pair not in pairs))
            self.intervals = list(coverage(self.intervals))
            point, head, ref, covered = self.first_new
            self.first_new = point, head, ref, coverage(covered)
            self.checkpoints = [(p, h, r, coverage(c)) for p, h, r, c in self.checkpoints]
        elif self.typed_interval is not None:
            # Read-only completed interval provenance; no full_after/area/HEAD.
            module = case.module('powercalc_subtract_clock')
            fact = self.typed_interval
            case.assertIs(type(fact), module.PowercalcSubtractClockInterval)
            case.assertIs(fact.owner.interval, fact)
            case.assertIs(fact.old_engine, self.old_subtract)
            case.assertIs(fact.new_engine, engine)
            case.assertIs(fact.start, clock.c.anchor)
            case.assertIs(fact.end, clock.c.endpoint)
            case.assertIs(fact.native_update, self.integration._update_integral)
            case.assertIs(fact.native_one_state, self.vendor.integration._IntegrationMethod.calculate_area_with_one_state)
            # A real quiet interval can precede the physical change, never
            # masquerade as dynamic coverage across it.
            case.assertLessEqual(fact.end, case.stimulus_state.last_updated)
            case.assertTrue(all(left >= fact.end for left, right in self.intervals),
                            'qualification: overlapping typed/native execution requires separate proof')
            self.subtract_heads.insert(0, {'kind': 'actual_typed_interval', 'start': fact.start.isoformat(),
                'end': fact.end.isoformat(), 'actual_child_w': str(group._numeric(fact.original_source_state, 'W')),
                'duplicate_interval': False})
            self.intervals.insert(0, (fact.start, fact.end))
            point, head, ref, covered = self.first_new
            self.first_new = point, head, ref, ((fact.start, fact.end), *covered)
            self.checkpoints = [(p, h, r, ((fact.start, fact.end), *c)) for p, h, r, c in self.checkpoints]
        else:
            case.assertEqual(clock.phase, 'unqualified_physical_change',
                             'qualification: absent dynamic owner must not be called continuity proof')

    def signed(self, at):
        values = dict(self.initial_children)
        for when, child, power in self.changes:
            if when <= at:
                values[child] = power
        # Independently reconstruct the actual ORIGINAL graph: sum - dynamic.
        return sum(values.values(), Decimal(0)) - values[self.case.dynamic_power.entity_id]

    def pieces(self, left, right):
        cuts = sorted({left, right, *(at for at, child, value in self.changes if left < at < right)})
        return [(a, b, self.signed(a)) for a, b in zip(cuts, cuts[1:])]

    def expected(self, covered):
        rows = self.subtract_heads[:len(covered)]
        assert [(datetime.fromisoformat(r['start']), datetime.fromisoformat(r['end'])) for r in rows] == list(covered)
        window = self.retired, self.boundary
        # Do not claim arbitrary partial overlap support. Reject that fixture
        # shape as unqualified; exact repeated intervals remain numerical proof.
        positive = sorted((max(a, window[0]), min(b, window[1])) for a, b in covered
                          if min(b, window[1]) > max(a, window[0]))
        for previous, current in zip(positive, positive[1:]):
            assert current[0] >= previous[1] or current == previous, 'qualification: partial overlap outside this case'
        missing = group._uncovered(*window, covered)
        reference = self.baseline
        # Missing reload slices use the real retained native update boundary,
        # not one constant quiet W across the public brightness change.
        missing_rows = []
        for left, right in missing:
            for a, b, power in self.pieces(left, right):
                increment = power * group._seconds(b, a) / Decimal(3600000)
                reference += increment
                missing_rows.append((a.isoformat(), b.isoformat(), str(power), str(increment)))
        for row in rows:
            if row['duplicate_interval']:
                continue
            a, b = datetime.fromisoformat(row['start']), datetime.fromisoformat(row['end'])
            native_power = Decimal(row['actual_child_w'])
            if b <= a or b <= window[0] or a >= window[1]:
                area = native_power * group._seconds(b, a)
            else:
                # A native catch-up is credited, but cannot hide using one W
                # across a retained child change. Outside the reload window,
                # preserve the actual native State/LEFT semantics and its gaps.
                cuts = sorted({a, b, *(x for x in window if a < x < b),
                               *(at for at, child, value in self.changes if a < at < b)})
                area = sum(((self.signed(left) if window[0] <= left < window[1] else native_power)
                            * group._seconds(right, left) for left, right in zip(cuts, cuts[1:])), Decimal(0))
            reference += area / Decimal(3600000)
        return reference, missing_rows

    def assert_dynamic_oracles(self):
        case = self.case
        # Qualification/source/IO checks precede numerical verdicts.
        case.assertIsNone(self.error, 'qualification failure, not product RED: ' + repr(self.error))
        observations = []
        for point, actual, ignored_receipt_reference, covered in [self.first_new, *self.checkpoints]:
            expected, missing = self.expected(covered)
            observations.append({'point': point.isoformat(), 'actual': str(actual), 'independent': str(expected),
                                 'difference': str(expected - actual), 'uncovered_dynamic_slices': missing,
                                 'native_bookkeeping_gaps_after_first_clock': [(a.isoformat(), b.isoformat())
                                     for a, b in group._uncovered(self.boundary, point, covered)]})
        print(json.dumps({'kind': 'real_dynamic_group_subtract_qualifier',
            'retirement_actual_clock': self.retired.isoformat(), 'retirement_test_read': self.departure.isoformat(),
            'first_replacement_source_clock': self.boundary.isoformat(),
            'retained_changes': [(t.isoformat(), key, str(value)) for t, key, value in self.changes],
            'initial_children': {key: str(value) for key, value in self.initial_children.items()},
            'actual_native_inputs': self.subtract_heads, 'heads': observations,
            'quiet_typed_interval_actually_issued': self.typed_interval is not None}, sort_keys=True))
        for actual, expected in self.group_heads:
            case.assertEqual(actual, expected, 'dynamic GROUP full HEAD lost/doubled child delta')
        case.assertTrue(self.group_heads and self.signed_rows and self.new_groups)
        for row in self.rows:
            if row['kind'] == 'restore_head':
                case.assertEqual(Decimal(row['actual']), Decimal(row['expected']))
        for row in self.signed_rows:
            case.assertEqual(Decimal(row['actual']), Decimal(row['expected']))
        for entity, store, states in self.new_groups:
            case.assertIs(store, self.store)
            case.assertIs(states, self.store_states)
            case.assertEqual(frozenset(entity._entities), self.members)
        for row in self.store_rows:
            case.assertEqual(row['store'], id(self.store))
            case.assertEqual(row['states'], id(self.store_states))
            if row['kind'] == 'store_get' and row['phase'] == 'return':
                case.assertEqual(row['actual_prior'], row['expected_prior'])
            if row['kind'] == 'store_cleanup':
                case.assertEqual(frozenset(row['members']), self.members)
        case.assertEqual(frozenset(self.store.states[self.group_id]), self.members)
        for child, state in self.prior.items():
            case.assertIs(self.store.states[self.group_id][child], state)
        for kind in ('restore_head', 'child_input'):
            case.assertTrue(any(row['kind'] == kind for row in self.rows))
        for kind in ('store_cleanup', 'store_get', 'store_set'):
            case.assertTrue(any(row['kind'] == kind for row in self.store_rows))
        for row in observations:
            case.assertEqual(Decimal(row['actual']), Decimal(row['independent']),
                             'dynamic SUBTRACT HEAD lost/doubled signed LEFT reload energy')


@unittest.skipUnless(group.direct.prior.accepted.fixture.HAS_HA, 'actual supported HA required; not a PASS')
class NativePowercalcDynamicGroupHeadTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = group.NativePowercalcGroupHeadTests.namespace_source_bytes
    _setup_real_vendor = group.NativePowercalcGroupHeadTests._setup_real_vendor
    _flush_documents = group.NativePowercalcGroupHeadTests._flush_documents
    guard = group.NativePowercalcGroupHeadTests.guard
    module = group.NativePowercalcGroupHeadTests.module
    prepare_candidate = group.NativePowercalcGroupHeadTests.prepare_candidate
    chain_root = group.NativePowercalcGroupHeadTests.chain_root
    ready = group.NativePowercalcGroupHeadTests.ready

    async def asyncSetUp(self):
        self.custom_profile_directories = True
        self.stimulus_task = self.stimulus_state = None
        await group.direct.NativePowercalcOperandClockRedTests.asyncSetUp(self)

    async def change_during_real_io(self, io, row):
        self.assertIs(row.get('yielded'), row.get('future'), 'qualification: no genuine native IO yield')
        self.assertFalse(row['future'].done(), 'qualification: IO completed before public stimulus')
        row['stimulus_pending_start_loop'] = self.f.hass.loop.time()
        self.assertFalse(io.numeric or io.cutovers, 'qualification: replacement numeric came before stimulus')
        scope = io.deadline_wait['scope']
        self.assertEqual(scope.started, 1)
        self.assertEqual(scope.returned, 0)
        self.assertEqual(self.static_power._platform_state.name, 'REMOVED')
        await self.sources[0].async_turn_on(brightness=1)
        self.stimulus_state = self.f.hass.states.get(self.yaml_source)
        self.assertEqual(self.stimulus_state.attributes.get('brightness'), 1)
        self.assertFalse(row['future'].done(), 'qualification: native result did not finish while IO pending')
        row['stimulus_pending_end_loop'] = self.f.hass.loop.time()
        self.assertFalse(io.numeric or io.cutovers)

    async def test_dynamic_retained_child_during_real_json_io_preserves_group_and_subtract_heads(self):
        await self.ready()
        self.original_head = self.chain.reconstruct_inputs().head
        original_receipt = self.chain.baseline.receipt_json
        io, facts = _DynamicIOFacts(self), _DynamicGroupFacts(self)
        await io.prepare()
        await facts.prepare()
        try:
            facts.install()
            io.install()
            try:
                result = await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard,
                    exact_fields=True, retain_producers=True, operand_clock=True)
            except Exception as error:
                error.add_note('dynamic_group_qualification_or_runtime_refusal;no_numeric_HEAD_verdict;'
                               + repr({'io_error': io.error, 'observer_error': facts.error,
                                       'stimulus_scheduled': self.stimulus_task is not None}))
                raise
            self.assertIsNotNone(self.stimulus_task, 'qualification: no public stimulus task')
            await asyncio.shield(self.stimulus_task)
            self.assertIsNone(io.error, 'qualification failure, not product RED: ' + repr(io.error))
            # Public follow-up supplies an actual new source endpoint and later
            # native HEAD. No callbacks, deadlines or source States are invoked.
            await self.sources[0].async_turn_on(brightness=128)
            await self.f.hass.async_block_till_done()
            await asyncio.shield(facts.new_integral)
            facts.qualify(result, io)
            executor = self.module('yaml_validation')._settled_executor
            self.assertEqual(await executor(self.f.hass, facts.prove), (facts.vm, facts.vc, facts.hm, facts.hc))
            self.assertEqual(await executor(self.f.hass, io.io_prove), (io.io_methods, io.io_codes))
            self.assertEqual(await executor(self.f.hass, io.runtime_prove), (io.runtime_methods, io.runtime_codes))
            self.assertEqual(await executor(self.f.hass, facts.dynamic_prove),
                             (facts.dynamic_methods, facts.dynamic_codes))
            self.assertEqual(self.chain.reconstruct_inputs().head, self.original_head)
            self.assertEqual(self.chain.baseline.receipt_json, original_receipt)
            self.assertEqual(result.original_runtime_token, self.original_runtime.structural_token)
            self.assertEqual(result.evidence_sha256, self.evidence.sha256)
            self.assertFalse(result.migration_ready)
            facts.capture_final()
            facts.assert_dynamic_oracles()
        finally:
            try:
                if self.stimulus_task is not None and not self.stimulus_task.done():
                    await asyncio.shield(self.stimulus_task)
            finally:
                try:
                    io.close()
                finally:
                    facts.close()


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativePowercalcDynamicGroupHeadTests)
