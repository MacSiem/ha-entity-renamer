"""Actual FIRED typed timers: add-only tests against frozen clock candidate166.

No old short-window/G4/exact/retained cases are inherited or rerun. The oracle
uses actual scheduler now, real W baseline and observed Energy bookkeeping
anchors. Product-reported area/elapsed/counters are never expected inputs.
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import time
import unittest

import test_powercalc_operand_clock_native as baseline
from test_powercalc_custom_profile_io import CustomProfileIOProof

_CLOCK_SHA256 = '3549e96a59cfe45b0464ea6555f161213b187f388604a79f2ba272816ec24597'


def _utc():
    return datetime.now(timezone.utc)


def _seconds(end, start):
    return Decimal((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds())


class _TimerFacts(baseline._ClockFacts):
    """Read-only monitor; handle/area/integral records are actual object refs."""
    def __init__(self, case):
        super().__init__(case)
        self.issues, self.deliveries = [], []
        self.active = {}
        self.base_ownership, self.numeric_ownership = {}, {}
        self.normal_ownership_checks = True
        self.allow_numeric_first = False

    async def prepare(self):
        await super().prepare()
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import event
        integration = self.vendor.integration
        self.refs += ((integration._IntegrationMethod, 'calculate_area_with_one_state'),
                      (integration.IntegrationSensor, '_update_integral'),
                      (event, 'async_call_at'), (event, 'async_call_later'),
                      (HomeAssistant, 'async_run_hass_job'))
        executor = self.case.module('yaml_validation')._settled_executor
        self.methods, self.codes = await executor(self.hass, self.prove)
        self.tags.update({self.codes[8]: 'one_state', self.codes[9]: 'integral',
                          self.codes[10]: 'issue', self.codes[11]: 'issue'})
        clock = self.case.module('powercalc_operand_clock')
        proof = self.case.module('native_unload_code_proof')
        path = Path(clock.__file__).resolve()
        self.product_refs = ((clock._OperandTimerAction, '__call__'),)
        def product_proof():
            return proof.prove_methods(path.parent, {path.name: _CLOCK_SHA256}, self.product_refs)
        self.product_prove = product_proof
        self.product_methods, self.product_codes = await executor(self.hass, product_proof)
        self.native_timer_codes = tuple(c for c in
            self.case.module('native_utility_setup_audit')._code_objects(self.codes[6])
            if c.co_name == '_integrate_on_max_sub_interval_exceeded_callback')
        assert len(self.native_timer_codes) == 1
        self.event = event

    def live_typed(self):
        # Initial arm can run inside the product monitoring callback. CPython
        # does not recursively monitor callbacks: discover the actual native
        # scheduled/ready handle, without claiming an observed helper return.
        for handle in tuple(self.hass.loop._scheduled) + tuple(self.hass.loop._ready):
            if type(handle) is asyncio.TimerHandle and not handle.cancelled():
                self._remember_handle(handle, 'observed_loop_handle')
        latest = {}
        for issue in self.issues:
            if issue['kind'] == 'typed' and not issue['handle'].cancelled():
                assert issue['name'] not in latest, 'duplicate actual live typed issuance'
                latest[issue['name']] = issue
        return latest

    def _issue(self, cancel):
        handle = getattr(cancel, '__self__', None)
        if type(handle) is not asyncio.TimerHandle:
            return
        args = getattr(handle, '_args', None)
        if type(args) is not tuple or len(args) != 2 or args[0] is not self.hass:
            return
        assert cancel == handle.cancel
        self._remember_handle(handle, 'native_helper_return')

    def _remember_handle(self, handle, provenance):
        from homeassistant.core import HassJob, HassJobType
        if type(handle) is not asyncio.TimerHandle or handle.cancelled():
            return
        args = getattr(handle, '_args', None)
        if (type(args) is not tuple or len(args) != 2 or args[0] is not self.hass
                or handle._callback is not self.event._run_async_call_action):
            return
        assert handle._loop is self.hass.loop
        job = args[1]
        assert type(job) is HassJob
        action = job.target
        if type(action) is self.timer_action:
            assert action.owner.scope.hass is self.hass
            assert action.owner.scope.evidence is self.case.evidence
            assert action.owner.scope.original is self.case.original_runtime
            assert handle._context.get(action.owner.scope.causal, None) is action.owner.scope.marker
            name, kind = action.name, 'typed'
            assert action.__call__.__func__.__code__ is self.product_codes[0]
            engine = action.owner.states[name].engine  # identity/routing only
            source = None
        elif getattr(action, '__code__', None) in self.native_timer_codes:
            cells = dict(zip(action.__code__.co_freevars,
                             (cell.cell_contents for cell in action.__closure__), strict=True))
            engine = cells['self']
            name = engine.entity_id
            if name not in self.old_e:
                return
            kind, source = 'native', cells['source_state']
        else:
            return
        assert job.job_type is HassJobType.Callback
        assert name in self.old_e and type(engine) is self.cls
        assert engine.registry_entry.id == self.old_e[name].registry_entry.id
        prior = next((i for i in self.issues if i['handle'] is handle), None)
        if prior is not None:
            assert prior['action'] is action and prior['job'] is job
            if provenance == 'native_helper_return':
                prior['native_return_loop'] = self.hass.loop.time()
            return
        row = {'kind': kind, 'name': name, 'handle': handle, 'job': job,
               'action': action, 'engine': engine, 'source': source,
               'deadline': handle.when(), 'loop': self.hass.loop.time(),
               'provenance': provenance}
        if provenance == 'native_helper_return':
            row['native_return_loop'] = row['loop']
        self.issues.append(row)

    def _active_delivery(self, frame):
        for row in self._ancestors(frame, self.product_codes[0]):
            action = row.get('self')
            delivery = self.active.get(id(action))
            if delivery is not None and delivery['issue']['action'] is action:
                return delivery
        return None

    def _timer(self, frame, phase):
        action = frame.f_locals['self']
        if type(action) is self.timer_action and action.owner.scope.hass is not self.hass:
            return
        if phase == 'start':
            assert type(action) is self.timer_action
            matches = [r for r in self.issues if r['kind'] == 'typed' and r['action'] is action]
            if not matches:
                # A real first arm inside another monitoring callback has no
                # recursive issue event. The debug loop retains the ACTUAL
                # currently dispatching handle after removing it from ready.
                # Read it; never construct one or use the product issuance ledger.
                assert self.hass.loop.get_debug(), 'native dispatch handle unavailable'
                handle = self.hass.loop._current_handle
                assert type(handle) is asyncio.TimerHandle
                self._remember_handle(handle, 'observed_native_dispatch_handle')
                matches = [r for r in self.issues if r['kind'] == 'typed' and r['action'] is action]
            assert len(matches) == 1, 'an independently observed native handle must precede delivery'
            issue = matches[0]
            handle, job, now = issue['handle'], issue['job'], frame.f_locals['now']
            assert not handle.cancelled() and handle._args == (self.hass, job)
            assert handle.when() == issue['deadline']
            dispatch = list(self._ancestors(frame, self.codes[7]))
            assert any(r.get('hass') is self.hass and r.get('job') is job for r in dispatch)
            runs = list(self._ancestors(frame, self.codes[12]))
            assert any(r.get('self') is self.hass and r.get('hassjob') is job
                       and r.get('args') == (now,) for r in runs)
            assert type(now) is datetime and now.tzinfo is not None
            engine = action.owner.states[action.name].engine
            assert type(engine) is self.cls and engine.entity_id == action.name
            row = {'issue': issue, 'now': now, 'entry_utc': _utc(),
                   'entry_loop': self.hass.loop.time(), 'engine': engine,
                   'entry_full': engine._state, 'areas': [], 'integrals': [],
                   'native_original_cancelled': self.baselines[action.name].handle.cancelled()}
            assert id(action) not in self.active
            self.active[id(action)] = row
            self.deliveries.append(row)
            self.timer_deliveries.append((action.name, row['entry_loop'], now))
        else:
            row = self.active.pop(id(action))
            engine = row['engine']
            assert action.owner.states[action.name].engine is engine
            row.update(return_utc=_utc(), return_loop=self.hass.loop.time(),
                       anchor_after=engine._last_integration_time,
                       trigger_after=engine._last_integration_trigger.name,
                       full_after=engine._state, last_valid_after=engine._last_valid_state)

    def observe(self, frame, phase, result=None):
        try:
            self.unchanged(self.refs, self.methods, self.codes)
            self.unchanged(self.product_refs, self.product_methods, self.product_codes)
            tag = self.tags[frame.f_code]
            if tag == 'issue':
                if phase == 'return':
                    self._issue(result)
                return
            if tag == 'operand_timer':
                self._timer(frame, phase)
                return
            if tag in ('one_state', 'integral'):
                row = self._active_delivery(frame)
                if row is None:
                    return
                items = row['areas'] if tag == 'one_state' else row['integrals']
                if phase == 'start':
                    item = {'frame': id(frame)}
                    if tag == 'one_state':
                        item.update(method=frame.f_locals['self'],
                                    actual_elapsed=frame.f_locals['elapsed_time'],
                                    actual_power=frame.f_locals['constant_state'])
                    else:
                        engine = frame.f_locals['self']
                        assert engine is row['engine']
                        item.update(engine=engine, actual_area=frame.f_locals['area'],
                                    before=engine._state)
                    items.append(item)
                else:
                    item = next(i for i in items if i['frame'] == id(frame))
                    if tag == 'one_state':
                        item['actual_return'] = result
                    else:
                        item.update(after=row['engine']._state,
                                    last_valid=row['engine']._last_valid_state,
                                    return_utc=_utc())
                return
            super().observe(frame, phase)
            if self.error is not None:
                return
            if tag == 'changed' and phase == 'return':
                entity = frame.f_locals.get('self')
                if getattr(entity, 'entity_id', None) in self.cutovers:
                    handle = getattr(entity._max_sub_interval_exceeded_callback, '__self__', None)
                    self._remember_handle(handle, 'observed_consumer_handle')
            if tag == 'base':
                entity = frame.f_locals.get('self')
                name = getattr(entity, 'entity_id', None)
                if (phase == 'return' and name in self.first_base
                        and self.first_base[name][0] is entity and name not in self.base_ownership):
                    source = self.first_base[name][1]
                    if source is not None and source.state not in ('unknown', 'unavailable'):
                        assert self.allow_numeric_first, 'unexpected numeric-first fixture branch'
                        return
                    if not self.normal_ownership_checks:
                        return
                    issue = self.live_typed().get(name)
                    assert issue is not None, 'unknown BASE must keep a real typed timer alive'
                    self.base_ownership[name] = issue
            if tag == 'fire' and phase == 'start' and self.normal_ownership_checks:
                for name in self.numeric:
                    if name not in self.numeric_ownership:
                        issue = self.live_typed().get(name)
                        assert issue is not None, 'typed ownership must survive until real numeric emission'
                        self.numeric_ownership[name] = issue
        except BaseException as error:
            if self.error is None:
                self.error = (frame.f_code.co_qualname, frame.f_lineno, type(error).__name__, str(error))

    def install(self):
        monitor = sys.monitoring
        free = [key for key in range(6) if monitor.get_tool(key) is None]
        assert free, 'no native monitoring slot; qualification failure'
        self.tool = free[0]
        monitor.use_tool_id(self.tool, 'renamer.test.direct_operand_clock')
        def started(code, offset):
            self.observe(sys._getframe(1), 'start')
        def returned(code, offset, result):
            self.observe(sys._getframe(1), 'return', result)
        monitor.register_callback(self.tool, monitor.events.PY_START, started)
        monitor.register_callback(self.tool, monitor.events.PY_RETURN, returned)
        for code in self.tags:
            monitor.set_local_events(self.tool, code, monitor.events.PY_START | monitor.events.PY_RETURN)


class _CustomTimerFacts(CustomProfileIOProof, _TimerFacts):
    pass


@unittest.skipUnless(baseline.prior.accepted.fixture.HAS_HA, 'actual supported HA required')
class NativePowercalcActualTimerTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = baseline.NativePowercalcOperandClockRedTests.namespace_source_bytes
    async def asyncSetUp(self):
        # Ordinary fixture selection must precede native setup and ORIGINAL.
        self.custom_profile_directories = self._testMethodName in (
            'test_both_direct_actual_timers_fire_rearm_and_handoff_with_independent_oracle',
            'test_cancel_after_actual_timer_delivery_settles_without_leaking_or_cancelling_native_handoff')
        await baseline.NativePowercalcOperandClockRedTests.asyncSetUp(self)

    _setup_real_vendor = baseline.NativePowercalcOperandClockRedTests._setup_real_vendor
    _flush_documents = baseline.NativePowercalcOperandClockRedTests._flush_documents
    guard = baseline.NativePowercalcOperandClockRedTests.guard
    module = baseline.NativePowercalcOperandClockRedTests.module
    prepare_candidate = baseline.NativePowercalcOperandClockRedTests.prepare_candidate
    chain_root = baseline.NativePowercalcOperandClockRedTests.chain_root
    ready = baseline.NativePowercalcOperandClockRedTests.ready

    def _no_owned(self, facts):
        for row in facts.issues:
            if row['kind'] == 'typed':
                self.assertTrue(row['handle'].cancelled(), 'issued typed timer leaked')
        for h in tuple(self.f.hass.loop._scheduled) + tuple(self.f.hass.loop._ready):
            if h.cancelled() or len(h._args) != 2:
                continue
            action = getattr(h._args[1], 'target', None)
            self.assertFalse(type(action) is facts.timer_action and action.owner.scope.hass is self.f.hass)

    async def _drive(self, *, cancel=False):
        self.assertTrue(self.custom_profile_directories)
        await self.ready()
        facts = _CustomTimerFacts(self)
        await facts.prepare()
        cancelled = False
        task = None
        def actual_slow_guard():
            nonlocal cancelled
            self.guard()
            if (cancel and not cancelled and facts.deliveries
                    and 'return_utc' in facts.deliveries[0] and len(facts.cutovers) < 2):
                cancelled = True
                task.cancel()  # Actual caller Task; native reload remains shielded.
        result, error = None, None
        try:
            facts.install()
            task = asyncio.create_task(self.numeric.async_capture_powercalc_numeric_reload(
                self.f.hass, self.chain, self.evidence, self.original_runtime, guard=actual_slow_guard,
                exact_fields=True, retain_producers=True, operand_clock=True))
            if cancel:
                with self.assertRaises(asyncio.CancelledError) as caught:
                    await task
                error = caught.exception
                self.assertTrue(cancelled, 'cancel boundary did not occur; no protocol PASS')
            else:
                result = await task
            self.assertIsNone(facts.error, 'native IO observer: ' + repr(facts.error))
            self.assertIsNotNone(facts.deadline_wait, 'both real typed issuances at native IO required')
            facts.assert_custom_read()
            self.assertIsNone(facts.error, 'observer/fixture failure: ' + repr(facts.error))
            executor = self.module('yaml_validation')._settled_executor
            self.assertEqual(await executor(self.f.hass, facts.prove), (facts.methods, facts.codes))
            self.assertEqual(await executor(self.f.hass, facts.product_prove),
                             (facts.product_methods, facts.product_codes))
            self.assertEqual(await executor(self.f.hass, facts.runtime_prove),
                             (facts.runtime_methods, facts.runtime_codes))
            self.assertEqual(await executor(self.f.hass, facts.io_prove),
                             (facts.io_methods, facts.io_codes))
        finally:
            facts.close()
        return result, error, facts

    def _oracle_and_handoff(self, facts):
        from homeassistant.helpers.entity_platform import async_get_platforms
        actual = {e.entity_id: e for p in async_get_platforms(self.f.hass, 'powercalc')
                  for e in p.entities.values() if e.entity_id in facts.old_e}
        self.assertEqual(set(actual), set(facts.old_e))
        diagnostics = []
        for name, old in facts.old_e.items():
            b = facts.baselines[name]
            self.assertEqual(b.unit_prefix, Decimal(1000))
            self.assertEqual(b.unit_time, Decimal(3600))
            self.assertTrue(b.handle.cancelled(), 'original native clock was not removed')
            self.assertIn(name, facts.base_ownership)
            self.assertIn(name, facts.numeric_ownership)
            self.assertIn(name, facts.cutovers)
            observed, event = facts.cutovers[name]
            data, state, cutover, cutover_loop, new_w = facts.numeric[name]
            self.assertIs(event.data, data)
            self.assertIs(event.data['new_state'], state)
            self.assertEqual(Decimal(state.state), b.source_power, 'quiet fixture W changed')
            deliveries = [r for r in facts.deliveries if r['issue']['name'] == name
                          and r['entry_loop'] <= cutover_loop]
            self.assertTrue(deliveries, 'actual FIRED typed timer required for EACH direct pair')
            reference, anchor = b.full, b.anchor
            for r in deliveries:
                self.assertIn('return_utc', r)
                self.assertGreaterEqual(r['entry_loop'], r['issue']['deadline'])
                self.assertTrue(r['native_original_cancelled'])
                self.assertEqual(r['entry_full'], reference)
                self.assertEqual(len(r['areas']), 1, 'one native one-state area per real delivery')
                self.assertEqual(len(r['integrals']), 1, 'no double native integral write')
                area, integral = r['areas'][0], r['integrals'][0]
                # Independent expected values: only observed real scheduler UTC,
                # previous REAL engine anchor and unchanged actual W baseline.
                elapsed = _seconds(r['now'], anchor)
                expected_area = b.source_power * elapsed
                self.assertIs(area['method'], r['engine']._method)
                self.assertIs(type(area['method']), facts.vendor.integration._Left)
                self.assertEqual(area['actual_power'], b.source_power)
                self.assertEqual(area['actual_elapsed'], elapsed)  # diagnostic comparison only
                self.assertEqual(area['actual_return'], expected_area)
                self.assertIs(integral['actual_area'], area['actual_return'])
                self.assertIs(integral['engine'], r['engine'])
                self.assertEqual(integral['before'], reference)
                reference += expected_area / Decimal(3600000)
                self.assertEqual(integral['after'], reference)
                self.assertEqual(integral['last_valid'], reference)
                self.assertEqual(r['full_after'], reference)
                self.assertEqual(r['last_valid_after'], reference)
                self.assertEqual(r['trigger_after'], 'TimeElapsed')
                self.assertLessEqual(integral['return_utc'], r['anchor_after'])
                self.assertLessEqual(r['anchor_after'], r['return_utc'])
                anchor = r['anchor_after']
                self.assertTrue(r['issue']['handle'].cancelled(), 'fired issuance must be retired')
                next_issues = [i for i in facts.issues if i['kind'] == 'typed' and i['name'] == name
                               and 'native_return_loop' in i
                               and r['entry_loop'] <= i['native_return_loop'] <= r['return_loop']]
                self.assertEqual(len(next_issues), 1, 'real native call_at rearm must occur once')
                self.assertIsNot(next_issues[0]['handle'], r['issue']['handle'])
                interval = r['engine']._max_sub_interval.total_seconds()
                self.assertGreaterEqual(next_issues[0]['deadline'], r['entry_loop'] + interval)
                self.assertLessEqual(next_issues[0]['deadline'], r['return_loop'] + interval)
            # First authentic numeric consumer must finish the held operand once.
            reference += b.source_power * _seconds(cutover, anchor) / Decimal(3600000)
            self.assertEqual(observed.full, reference, 'timer/resume prefix lost or integrated twice')
            self.assertEqual(observed.last_valid, reference)
            prior = facts.numeric_ownership[name]
            self.assertIs(facts.base_ownership[name]['action'].owner, prior['action'].owner)
            native = [i for i in facts.issues if i['kind'] == 'native' and i['name'] == name
                      and i['source'] is state and i['action'].__code__ is facts.native_timer_codes[0]]
            self.assertGreaterEqual(len(native), 2, 'candidate native timer plus call_at handoff required')
            delegated = next(i for i in native if i is not native[0]
                             and i['handle'].when() == prior['deadline']
                             and i['action'] is native[0]['action'])
            self.assertTrue(native[0]['handle'].cancelled(), 'candidate deadline must be replaced')
            self.assertTrue(prior['handle'].cancelled(), 'last typed issuance not relinquished')
            current = actual[name]._max_sub_interval_exceeded_callback.__self__
            self.assertFalse(current.cancelled(), 'legitimate native clock must survive cleanup')
            self.assertIs(current._callback, facts.event._run_async_call_action)
            action = current._args[1].target
            self.assertIs(action.__code__, facts.native_timer_codes[0])
            cells = dict(zip(action.__code__.co_freevars,
                             (cell.cell_contents for cell in action.__closure__), strict=True))
            self.assertIs(cells['self'], actual[name])
            self.assertIs(cells['source_state'], state)
            self.assertIsNot(actual[name], old)
            diagnostics.append({'name': name, 'actual_deliveries': len(deliveries),
                                'expected': str(reference), 'actual': str(observed.full),
                                'delegated_deadline': delegated['deadline']})
        self._no_owned(facts)
        print(json.dumps({'kind': 'actual_timer_native_oracle', 'pairs': diagnostics}))

    async def test_both_direct_actual_timers_fire_rearm_and_handoff_with_independent_oracle(self):
        result, _, facts = await self._drive()
        self.assertTrue(result.operand_clock.direct_operand_clock_verified)
        self.assertEqual(result.retained_producers.calculations, (), 'quiet timer oracle has no W deltas')
        self.assertFalse(result.continuity_verified)
        self.assertFalse(result.counter_ownership_verified)
        self.assertFalse(result.operand_clock.reload_ownership_verified)
        self.assertFalse(result.migration_ready)
        self.assertFalse(result.operand_clock.family_complete)
        self._oracle_and_handoff(facts)

    async def test_cancel_after_actual_timer_delivery_settles_without_leaking_or_cancelling_native_handoff(self):
        _, error, facts = await self._drive(cancel=True)
        self.assertTrue(any(n.startswith('powercalc_operand_clock_partial=')
                            for n in getattr(error, '__notes__', ())))
        self._oracle_and_handoff(facts)

    async def test_foreign_cancellation_of_observed_issued_handle_refuses_and_cleans_actual_scope(self):
        await self.ready()
        facts = _TimerFacts(self)
        await facts.prepare()
        facts.normal_ownership_checks = False
        complete = self.f.hass.loop.create_future()
        complete.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        seen = False
        async def take_real_timer(event):
            nonlocal seen
            state = event.data.get('new_state')
            if (seen or event.data.get('entity_id') != self.static_power.entity_id or state is None
                    or state.state != 'unavailable' or state.attributes.get('restored') is not True):
                return
            seen = True
            try:
                issue = facts.live_typed().get(self.static_energy.entity_id)
                self.assertIsNotNone(issue, 'actual native loop handle must be observed before takeover')
                issue['handle'].cancel()  # Actual native scheduled TimerHandle, never a synthetic stand-in.
                complete.set_result(None)
            except BaseException as error:
                complete.set_exception(error)
        remove = self.f.hass.bus.async_listen('state_changed', take_real_timer)
        try:
            facts.install()
            with self.assertRaises(ValueError) as caught:
                await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime, guard=self.guard,
                    exact_fields=True, retain_producers=True, operand_clock=True)
            async with asyncio.timeout(8):
                await complete
            self.assertTrue(seen)
            # The source-defined abort cleanup also refuses an already cancelled
            # independently issued handle. It drains all other owned handles;
            # no continuity or successful takeover is inferred from this refusal.
            self.assertEqual(str(caught.exception),
                             'powercalc_operand_clock_cleanup_foreign_timer')
            self.assertIsNone(facts.error, repr(facts.error))
            self._no_owned(facts)
        finally:
            remove()
            facts.close()

    async def test_genuine_numeric_first_setup_refuses_without_inventing_a_counter_oracle(self):
        await self.ready()
        facts = _TimerFacts(self)
        await facts.prepare()
        facts.normal_ownership_checks = False
        facts.allow_numeric_first = True
        attempted = False
        source_task = None
        published_state = published_power = None
        # Core async_added_to_hass reads source_state before calling the vendor
        # schedule override. Stimulating from that later guard changes HA's live
        # State but cannot change the argument already captured by the caller.
        integration = facts.vendor.integration.IntegrationSensor
        facts.refs += ((integration, 'async_added_to_hass'),)
        executor = self.module('yaml_validation')._settled_executor
        facts.methods, facts.codes = await executor(self.f.hass, facts.prove)
        added_code = facts.codes[-1]
        stimulus_error = None
        stimulus_tool = None
        def real_source_before_native_added(code, offset):
            nonlocal attempted, source_task, published_state, published_power, stimulus_error
            try:
                facts.unchanged(facts.refs, facts.methods, facts.codes)
                frame = sys._getframe(1)
                entity = frame.f_locals.get('self')
                if (attempted or type(entity) is not facts.cls or entity is self.static_energy
                        or entity.entity_id != self.static_energy.entity_id):
                    return
                from homeassistant.helpers.entity_platform import async_get_platforms
                powers = [e for p in async_get_platforms(self.f.hass, 'powercalc')
                          for e in p.entities.values() if e.entity_id == self.static_power.entity_id]
                assert code is added_code and frame.f_code is added_code
                assert 'source_state' not in frame.f_locals and entity.entity_id not in facts.first_base
                assert entity._platform_state.name == 'ADDING'
                assert entity.registry_entry is self.static_energy.registry_entry
                assert (len(powers) == 1 and powers[0] is not self.static_power
                        and type(powers[0]) is type(self.static_power)
                        and powers[0].registry_entry is self.static_power.registry_entry
                        and powers[0]._platform_state.name == 'ADDED' and powers[0]._power is None), \
                    'genuine replacement W must be present before native added entry'
                attempted = True
                # Actual public fixture API; the cached native LUT calculation
                # finishes eagerly before this source-pinned PY_START returns.
                # Core has not yet executed its source_state = hass.states.get.
                source_task = self.f.hass.async_create_task(
                    self.sources[0].async_turn_on(brightness=1),
                    'test.real-source-before-native-added', eager_start=True)
                assert source_task.done() and source_task.exception() is None
                published_state = self.f.hass.states.get(self.static_power.entity_id)
                published_power = powers[0]._power
                assert type(published_power) is Decimal and published_power.is_finite()
                assert Decimal(published_state.state) == published_power
            except BaseException as error:
                stimulus_error = (type(error).__name__, str(error))
        try:
            facts.install()
            free = [i for i in range(6) if sys.monitoring.get_tool(i) is None]
            self.assertTrue(free, 'no monitoring slot for the actual native entry stimulus')
            stimulus_tool = free[0]
            sys.monitoring.use_tool_id(stimulus_tool, 'renamer.test.before_native_added')
            sys.monitoring.register_callback(stimulus_tool, sys.monitoring.events.PY_START,
                                             real_source_before_native_added)
            sys.monitoring.set_local_events(stimulus_tool, added_code, sys.monitoring.events.PY_START)
            with self.assertRaises(ValueError) as caught:
                await self.numeric.async_capture_powercalc_numeric_reload(
                    self.f.hass, self.chain, self.evidence, self.original_runtime,
                    guard=self.guard,
                    exact_fields=True, retain_producers=True, operand_clock=True)
            self.assertIsNone(stimulus_error, repr(stimulus_error))
            self.assertTrue(attempted, 'fixture did not expose the real numeric-first setup seam')
            self.assertIsNotNone(source_task)
            self.assertTrue(source_task.done(), 'native source calculation remained inconclusive')
            self.assertIsNone(source_task.exception())
            self.assertIsNone(facts.error, repr(facts.error))
            name = self.static_energy.entity_id
            from homeassistant.core import State
            self.assertIs(type(published_state), State)
            self.assertIs(type(published_power), Decimal)
            self.assertTrue(published_power.is_finite())
            self.assertEqual(Decimal(published_state.state), published_power,
                             'public source must finish a real native numeric W write')
            self.assertIn(name, facts.first_base)
            self.assertIs(facts.first_base[name][1], published_state,
                          'the public API-produced numeric State must exist before first BASE')
            reasons = '\n'.join(getattr(caught.exception, '__notes__', ())) + str(caught.exception)
            self.assertTrue('numeric_before_unknown_setup_or_overlap_unsupported' in reasons
                            or 'numeric_first_setup_unsupported' in reasons,
                            'an unrelated refusal cannot qualify numeric-first handling')
            self._no_owned(facts)
        finally:
            if stimulus_tool is not None:
                sys.monitoring.set_local_events(stimulus_tool, added_code, 0)
                sys.monitoring.register_callback(stimulus_tool, sys.monitoring.events.PY_START, None)
                sys.monitoring.free_tool_id(stimulus_tool)
            facts.close()
