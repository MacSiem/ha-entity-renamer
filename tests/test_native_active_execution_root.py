"""Desired full Root behavior for a running nested wait and accepted queue.

Qualification target, not evidence of a passing implementation. Run on each
supported pinned Core under the owner's build/test resource queue.
"""
import asyncio
import unittest

import test_native_template_mixed_yaml_server as mixed


def _frames(coro):
    """Observe coroutine chains only; do not alter scheduling or callbacks."""
    seen = set()
    while coro is not None and id(coro) not in seen:
        seen.add(id(coro))
        frame = getattr(coro, 'cr_frame', None) or getattr(coro, 'gi_frame', None)
        if frame is not None:
            yield frame
        coro = getattr(coro, 'cr_await', None) or getattr(coro, 'gi_yieldfrom', None)


def _wait_snapshot(run):
    from homeassistant.helpers.script import _ScriptRun
    code = _ScriptRun._async_wait_with_optional_timeout.__code__
    found = [(task, frame) for task in asyncio.all_tasks() for frame in _frames(task.get_coro())
             if frame.f_code is code and frame.f_locals.get('self') is run]
    if len(found) != 1:
        return None
    task, frame = found[0]
    handle = frame.f_locals['timeout_handle']
    future = frame.f_locals['timeout_future']
    if handle is None or future is None or handle.cancelled() or future.done():
        return None
    return (task, handle, handle.when(), future, frame.f_locals['unsub'], run._stop,
            run._context, run._variables, run._action, run._step)


async def _until(probe):
    async def poll():
        while not (result := probe()):
            await asyncio.sleep(0.01)
        return result
    return await asyncio.wait_for(poll(), 5)


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeActiveScriptRootTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_start = mixed.NativeTemplateMixedYAMLServerTests._before_start
    _flush_documents = mixed.NativeTemplateMixedYAMLServerTests._flush_documents
    prepare = mixed.NativeTemplateMixedYAMLServerTests.prepare
    emit = mixed.NativeTemplateMixedYAMLServerTests.emit
    _runtime = mixed.NativeTemplateMixedYAMLServerTests._runtime
    module = mixed.NativeTemplateMixedYAMLServerTests.module
    stimulus = mixed.NativeTemplateMixedYAMLServerTests.stimulus
    data = mixed.NativeTemplateMixedYAMLServerTests.data

    async def _before_loader(self, hass, root):
        """An independent listener exists before the minimum Core is started."""
        from homeassistant.const import __version__
        if (__version__ != '2025.6.0'
                or self._testMethodName != 'test_automation_native_wait_releases_during_actual_source_drain'):
            return
        from homeassistant.core import callback
        self._minimum_source_added = hass.loop.create_future()
        @callback
        def source_changed(event):
            data = event.data
            if (data.get('entity_id') == 'sensor.accepted_next'
                    and data.get('old_state') is None and data.get('new_state') is not None
                    and not self._minimum_source_added.done()):
                self._minimum_source_added.set_result(event)
        self._minimum_source_remove = hass.bus.async_listen('state_changed', source_changed)
        self.addCleanup(self._minimum_source_remove)

    async def test_foreign_automation_trace_constructor_globals_refused(self):
        """Identical native code with foreign globals must not gain trace ownership."""
        from types import FunctionType
        from homeassistant.components.automation.trace import AutomationTrace
        original = AutomationTrace.__init__
        foreign_globals = dict(original.__globals__)
        foreign_globals['super'] = lambda: None
        forged = FunctionType(original.__code__, foreign_globals, original.__name__,
                              original.__defaults__, original.__closure__)
        forged.__kwdefaults__ = original.__kwdefaults__
        AutomationTrace.__init__ = forged
        try:
            with self.assertRaisesRegex(self.module('transaction').Refused,
                                        'active_trace_native_alias_changed'):
                await self.test_active_nested_wait_and_queue_forward_then_fresh_inverse()
        finally:
            AutomationTrace.__init__ = original

    async def test_active_nested_wait_and_queue_forward_then_fresh_inverse(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, _, original = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        path = root / 'mixed_scripts.yaml'
        path.write_text('mixed_source:\n  mode: queued\n  max: 4\n  sequence:\n'
            '  - sequence:\n'
            '    - wait_for_trigger:\n      - trigger: event\n'
            '        event_type: active_transport_release\n'
            '      timeout: "00:05:00"\n      continue_on_timeout: false\n'
            '    - event: active_transport_result\n      event_data:\n'
            '        token: "{{ token }}"\n        value: "{{ states(' + repr(old) + ') }}"\n'
            'control_script:\n  sequence:\n  - event: active_transport_control\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        original['mixed_scripts.yaml'] = path.read_bytes()
        await self.stimulus(17)
        await self._flush_documents()
        history = self.module('history_guard').NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass))
        before_history = await history.capture(mapping)
        entity = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        script, service = entity.script, hass.services._services['script']['mixed_source']
        source = self.producer
        results, releases, tasks, controls = [], [], [], []
        self.addCleanup(hass.bus.async_listen('active_transport_result', results.append))
        self.addCleanup(hass.bus.async_listen('active_transport_release', releases.append))
        self.addCleanup(hass.bus.async_listen('active_transport_control', controls.append))
        await hass.services.async_call('script', 'control_script', {}, blocking=True, context=Context())
        await _until(lambda: len(controls) >= 1)
        self.assertEqual(len(controls), 1)

        async def pair(label):
            contexts = (Context(), Context())
            for number, context in enumerate(contexts):
                tasks.append(hass.async_create_task(hass.services.async_call(
                    'script', 'mixed_source', {'token': f'{label}-{number}'},
                    blocking=True, context=context), f'active transport {label}-{number}'))
                await _until(lambda: len(script._runs) == number + 1)
            root_runs = tuple(script._runs)
            self.assertEqual(tuple(run._context for run in root_runs), contexts)
            self.assertTrue(root_runs[0].lock_acquired)
            self.assertFalse(root_runs[1].lock_acquired)
            child = await _until(lambda: script._sequence_scripts.get(0))
            self.assertIs(child.sequence, script.sequence[0]['sequence'])
            child_run = await _until(lambda: child._runs[0] if len(child._runs) == 1 else None)
            wait = await _until(lambda: _wait_snapshot(child_run))
            pins = self.module('native_active_execution_pins').ActiveWaitPins.capture(script)
            return contexts, root_runs, child, child_run, wait, pins

        def same(pinned):
            contexts, runs, child, child_run, wait, _ = pinned
            self.assertIs(entity.script, script)
            self.assertIs(hass.services._services['script']['mixed_source'], service)
            self.assertIs(self.producer, source)
            self.assertEqual(tuple(script._runs), runs)
            self.assertIs(script._sequence_scripts[0], child)
            self.assertIs(child.sequence, script.sequence[0]['sequence'])
            self.assertEqual(tuple(child._runs), (child_run,))
            self.assertEqual(_wait_snapshot(child_run), wait)
            self.assertEqual(tuple(run._context for run in runs), contexts)
            self.assertFalse(runs[1].lock_acquired)

        def trace_snapshot(contexts):
            from copy import deepcopy
            from homeassistant.components.script.trace import ScriptTrace
            from homeassistant.components.trace import DATA_TRACE
            from homeassistant.const import __version__
            bucket = hass.data[DATA_TRACE]['script.mixed_source']
            records = bucket.runs if __version__ != '2025.6.0' else bucket
            traces = tuple(next(trace for trace in records.values()
                                if type(trace) is ScriptTrace and trace.context is context)
                           for context in contexts)
            self.assertEqual(len({id(trace) for trace in traces}), 2)
            self.assertTrue(all(trace._config is entity.raw_config for trace in traces))
            return tuple((trace, trace.context, trace._config, deepcopy(trace._config)) for trace in traces)

        def trace_history_unchanged(snapshot):
            for trace, context, _, original_config in snapshot:
                self.assertIs(trace.context, context)
                self.assertEqual(trace._config, original_config)

        async def drain(pinned, expected):
            _, runs, child, child_run, _, _ = pinned
            hass.bus.async_fire('active_transport_release')
            await _until(lambda: len(results) == expected + 1)
            await _until(lambda: len(child._runs) == 1 and child._runs[0] is not child_run
                         and _wait_snapshot(child._runs[0]))
            self.assertTrue(runs[1].lock_acquired)
            hass.bus.async_fire('active_transport_release')
            await _until(lambda: len(results) == expected + 2)
            await asyncio.wait_for(asyncio.gather(*tasks[-2:]), 5)

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        try:
            first = await pair('forward')
            first_traces = trace_snapshot(first[0])
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='active-root-admin',
                guard=lambda: None, refresh=refresh)
            same(first)
            # Exercise real native pre-attempt installation and planned undo
            # while both accepted runs and their wait remain suspended.
            plan = runtime.reviews[review['operation_id']].plan
            baseline, composition = runtime.preparations.resolve_composition(plan.native_inputs)
            receipts = self.module('native_registry_receipts')
            store = receipts.native_registry_receipt_store(hass, runtime.preparations)
            await store.initialize()
            chain = receipts.RegistryReceiptChain.reopen(store, review['operation_id'], plan,
                baseline.receipt, dict(composition.prepared_digests))
            observer = self.module('dependent_config_binding').NativeDependentConfigReadback(
                hass, composition.dependent_config_program, composition.evidence,
                baseline.receipt, composition.yaml_plan)
            transport = self.module('native_active_execution_transport').ActiveScriptTransport.capture(
                observer, chain, old, guard=lambda: None)
            # A mutation after capture must not become an owned constructor environment.
            from homeassistant.components.automation import trace as automation_trace
            self.assertNotIn('super', vars(automation_trace))
            vars(automation_trace)['super'] = lambda: None
            try:
                with self.assertRaisesRegex(self.module('transaction').Refused,
                                            'active_trace_native_alias_changed'):
                    transport.trace_protection.check_before()
            finally:
                del vars(automation_trace)['super']
            # The effective builtin is checked, not merely the mutable dict identity.
            import builtins
            native_super = builtins.super
            refused = False
            try:
                builtins.super = object
                try:
                    transport.trace_protection._native_aliases()
                except self.module('transaction').Refused:
                    refused = True
            finally:
                builtins.super = native_super
            self.assertTrue(refused, 'foreign builtin super was accepted')
            graph = self.module('native_script_config_transport')._graph
            before_config = graph(entity.raw_config), graph(script.sequence)
            transport.install()
            same(first)
            trace_history_unchanged(first_traces)
            self.assertTrue(all(trace._config is not original_config
                                for trace, _, original_config, _ in first_traces))
            self.assertEqual(results, [])
            changed_graph = graph(entity.raw_config), graph(script.sequence)
            trace, _, _, _ = first_traces[0]
            owned_trace_config = trace._config
            foreign_trace_config = {'foreign': 'retained edit'}
            trace._config = foreign_trace_config
            try:
                with self.assertRaises((ValueError, self.module('transaction').Refused)):
                    transport.restore_before_attempt()
                self.assertIs(trace._config, foreign_trace_config)
                self.assertEqual((graph(entity.raw_config), graph(script.sequence)), changed_graph)
                self.assertEqual(transport.phase, 'installed')
            finally:
                trace._config = owned_trace_config
            original_trace_storage = trace.__dict__
            foreign_trace_storage = dict(original_trace_storage)
            trace.__dict__ = foreign_trace_storage
            try:
                with self.assertRaises((ValueError, self.module('transaction').Refused)):
                    transport.restore_before_attempt()
                self.assertIs(trace.__dict__, foreign_trace_storage)
                self.assertEqual((graph(entity.raw_config), graph(script.sequence)), changed_graph)
                self.assertEqual(transport.phase, 'installed')
            finally:
                trace.__dict__ = original_trace_storage
            owner = transport.projector.operations[0][0]
            self.assertNotIn('active_foreign_sibling', owner)
            foreign_value = object()
            owner['active_foreign_sibling'] = foreign_value
            try:
                with self.assertRaises((ValueError, self.module('transaction').Refused)):
                    transport.restore_before_attempt()
                self.assertIs(owner['active_foreign_sibling'], foreign_value)
                self.assertEqual(transport.phase, 'installed')
            finally:
                del owner['active_foreign_sibling']
            transport.restore_before_attempt()
            self.assertEqual((graph(entity.raw_config), graph(script.sequence)), before_config)
            self.assertTrue(all(trace._config is original_config
                                for trace, _, original_config, _ in first_traces))
            trace_history_unchanged(first_traces)
            same(first)
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='active-root-admin', guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
            record = await runtime.journal.load(result['operation_id'])
            baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
            original_ref, receipt = record.plan.native_inputs, baseline.receipt_json
            # A foreign replacement with equal config and Context is not the
            # trace held by the suspended native frame and must not qualify.
            import copy
            from homeassistant.components.trace import DATA_TRACE
            original_trace = first_traces[0][0]
            bucket = hass.data[DATA_TRACE][original_trace.key]
            groups = (bucket,) if isinstance(bucket, dict) else (bucket.runs, bucket.not_triggered)
            group, = [group for group in groups if group.get(original_trace.run_id) is original_trace]
            foreign = copy.copy(original_trace)
            foreign._trace = {}
            group[original_trace.run_id] = foreign
            try:
                foreign_inspect = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                self.assertEqual(foreign_inspect['state'], 'UNKNOWN')
            finally:
                group[original_trace.run_id] = original_trace
            original_run_id = original_trace.run_id
            foreign_run_id = original_run_id + 'foreign'
            del group[original_run_id]
            original_trace.run_id = foreign_run_id
            group[foreign_run_id] = original_trace
            try:
                foreign_id_inspect = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                self.assertEqual(foreign_id_inspect['state'], 'UNKNOWN')
            finally:
                del group[foreign_run_id]
                original_trace.run_id = original_run_id
                group[original_run_id] = original_trace
            original_config = original_trace._config
            original_trace._config = copy.deepcopy(original_config)
            try:
                foreign_config_inspect = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                self.assertEqual(foreign_config_inspect['state'], 'UNKNOWN')
            finally:
                original_trace._config = original_config
            original_trace._config = entity.raw_config
            try:
                reattached_inspect = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                self.assertEqual(reattached_inspect['state'], 'UNKNOWN')
            finally:
                original_trace._config = original_config
            inspected = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            print('ACTIVE_PUBLIC_INSPECTION', inspected.get('state'), inspected.get('reason'), flush=True)
            if inspected.get('state') != 'applied':
                import importlib
                factory = importlib.import_module(runtime.__class__.__module__)._yaml_factory_for_plan(
                    runtime.preparations, record.plan)
                async with runtime.operation_lock:
                    session = await factory.bind_reverse(hass, result['operation_id'], result['fingerprint'],
                        guard=lambda: None, refresh=refresh)
                    session.close()
                self.fail('active public inspection did not qualify')
            same(first)
            trace_history_unchanged(first_traces)
            self.assertEqual(results, [])
            self.assertEqual(len(controls), 1)
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(old))
            # Inverse uses the exact same retained runs and original trace history.
            inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertEqual(inverse['state'], 'rolled_back')
            reopened, restored = runtime.preparations.resolve_composition(record.plan.native_inputs)
            self.assertEqual((record.plan.native_inputs, reopened.receipt_json, restored),
                             (original_ref, receipt, composition))
            same(first)
            trace_history_unchanged(first_traces)
            self.assertEqual(results, [])
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            restored_inspect = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertEqual(restored_inspect['state'], 'rolled_back')
            same(first)
            await drain(first, 0)
            self.assertEqual([(e.data['token'], str(e.data['value']), e.context.id) for e in results],
                [('forward-0', '17', first[0][0].id), ('forward-1', '17', first[0][1].id)])
            self.assertEqual(len(releases), 2)
            # The previously anchored trace remains historical in the native
            # store after natural completion, while a new pair runs.
            third = await pair('history')
            self.assertIsNone(original_trace._dict)
            old_config = original_trace._config
            original_trace._config = entity.raw_config
            try:
                history_inspect = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                self.assertEqual(history_inspect['state'], 'UNKNOWN')
            finally:
                original_trace._config = old_config
            await drain(third, 2)
            self.assertEqual([(e.data['token'], str(e.data['value']), e.context.id) for e in results[2:]],
                [('history-0', '17', third[0][0].id), ('history-1', '17', third[0][1].id)])
            for name, content in original.items():
                self.assertEqual((root / name).read_bytes(), content)
            self.assertTrue(await history.verify(before_history))
            self.assertTrue(await history.verify(composition.consumer_history))
        finally:
            # Normal HA stop service cleans up test runs if an assertion fails.
            await hass.services.async_call('script', 'turn_off', {'entity_id': entity.entity_id}, blocking=True)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_automation_nested_wait_and_queue_forward_then_fresh_inverse(self):
        await self._automation_case(during_drain=False)

    async def test_automation_native_wait_releases_during_actual_source_drain(self):
        await self._automation_case(during_drain=True)

    async def _automation_case(self, *, during_drain):
        from homeassistant.const import __version__
        from homeassistant.core import Context, callback
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        minimum_deferred_dispatch = during_drain and __version__ == '2025.6.0'
        runtime, mapping, uuids, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        control = type(self.producer)()
        control._attr_unique_id = 'active-transport-independent-control'
        control._attr_name = 'Active transport independent control'
        control._attr_native_value = 0
        sensors = [control]
        release_sensor = None
        if during_drain:
            release_sensor = type(self.producer)()
            release_sensor._attr_unique_id = 'active-transport-independent-release'
            release_sensor._attr_name = 'Active transport independent release'
            release_sensor._attr_native_value = 0
            sensors.append(release_sensor)
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities(sensors)
        await hass.async_block_till_done()
        script_path = root / 'mixed_scripts.yaml'
        script_path.write_text('mixed_source:\n  sequence:\n  - event: active_unused_script\n')
        path = root / 'mixed_automations.yaml'
        release_trigger = ('trigger: state\n        entity_id: ' + release_sensor.entity_id
            if during_drain else 'trigger: event\n        event_type: active_automation_release')
        path.write_text('- id: mixed_source\n  alias: Active queued automation\n'
            '  initial_state: true\n  mode: queued\n  max: 4\n  triggers:\n'
            '  - trigger: state\n    entity_id: ' + control.entity_id + '\n'
            '  actions:\n  - sequence:\n    - wait_for_trigger:\n      - ' + release_trigger + '\n'
            '      timeout: "00:05:00"\n'
            '      continue_on_timeout: false\n    - event: active_automation_result\n'
            '      event_data:\n        token: "{{ trigger.to_state.state }}"\n'
            '        value: "{{ states(' + repr(old) + ') }}"\n')
        for domain in ('script', 'automation'):
            await hass.services.async_call(domain, 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        for file in (path, script_path):
            original[file.name] = file.read_bytes()
        await self.stimulus(17)
        await self._flush_documents()
        history = self.module('history_guard').NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass))
        before_history = await history.capture(mapping)
        entity = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities
                      if e.unique_id == 'mixed_source')
        script, source = entity.action_script, self.producer
        binding, = await self.module('native_automation_state_binding').async_capture_state_bindings(
            hass, entity, guard=lambda: None)
        native_listener = binding.listener
        keyed_job, native_keys, native_callbacks, native_unsubscribe = (
            binding.keyed_job, binding.original_keys, binding.callbacks, binding.unsubscribe)
        native_registrations = self.module('yaml_runtime_observer')._automation_registrations
        registrations_before = native_registrations(hass, entity)
        results, foreign = [], []
        self.addCleanup(hass.bus.async_listen('active_automation_result', results.append))
        self.addCleanup(hass.bus.async_listen('state_changed',
            lambda event: foreign.append(event) if event.data.get('entity_id') == control.entity_id else None))
        releases = [0]
        def release_wait():
            if release_sensor is None:
                hass.bus.async_fire('active_automation_release')
            else:
                releases[0] += 1
                release_sensor._attr_native_value = releases[0]
                release_sensor.async_write_ha_state()

        async def pair(first):
            for number in (first, first + 1):
                control._attr_native_value = number
                control.async_write_ha_state()
                await _until(lambda: len(script._runs) == number - first + 1)
            runs = tuple(script._runs)
            self.assertTrue(runs[0].lock_acquired)
            self.assertFalse(runs[1].lock_acquired)
            child = await _until(lambda: script._sequence_scripts.get(0))
            run = await _until(lambda: child._runs[0] if child._runs else None)
            wait = await _until(lambda: _wait_snapshot(run))
            pins = self.module('native_active_execution_pins').ActiveWaitPins.capture(script)
            return runs, child, run, wait, pins

        def same(pinned):
            runs, child, run, wait, _ = pinned
            self.assertIs(entity.action_script, script)
            self.assertIs(self.producer, source)
            self.assertEqual(tuple(script._runs), runs)
            self.assertIs(script._sequence_scripts[0], child)
            self.assertIs(child.sequence, script.sequence[0]['sequence'])
            self.assertEqual(tuple(child._runs), (run,))
            self.assertEqual(_wait_snapshot(run), wait)
            self.assertFalse(runs[1].lock_acquired)

        async def drain(pinned, count):
            runs, child, first_run, _, _ = pinned
            release_wait()
            await _until(lambda: len(results) == count + 1)
            await _until(lambda: len(child._runs) == 1 and child._runs[0] is not first_run
                         and _wait_snapshot(child._runs[0]))
            self.assertTrue(runs[1].lock_acquired)
            release_wait()
            await _until(lambda: len(results) == count + 2)
            await _until(lambda: not script._runs)

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        relay = None
        try:
            first = await pair(1)
            release_observations, owned_graphs = [], []
            if during_drain:
                first_action = hass.loop.create_future()
                source_added = (self._minimum_source_added if minimum_deferred_dispatch
                                else hass.loop.create_future())
                @callback
                def source_changed(event):
                    data = event.data
                    if (data.get('entity_id') != new or data.get('old_state') is not None
                            or data.get('new_state') is None or source_added.done()):
                        return
                    source_added.set_result(event)
                @callback
                def action_completed(event):
                    if str(event.data.get('token')) == '1' and not first_action.done():
                        first_action.set_result(None)
                if minimum_deferred_dispatch:
                    from homeassistant.helpers import event as event_helper
                    self.assertEqual(new, 'sensor.accepted_next')
                    self.assertFalse(source_added.done())
                    tracker = hass.data[event_helper._KEYED_TRACK_STATE_CHANGE.key]
                    registered = self.module('yaml_runtime_observer')._registered_bus_remove
                    early_entry, _ = registered(hass, self._minimum_source_remove, 'state_changed')
                    keyed_entry, _ = registered(hass, tracker.listener, 'state_changed')
                    listeners = hass.bus._listeners['state_changed']
                    self.assertLess(next(i for i, item in enumerate(listeners) if item is early_entry),
                                    next(i for i, item in enumerate(listeners) if item is keyed_entry))

                    # The independent listener is registered after the real
                    # wait trigger. Core dispatches them in their native order;
                    # it observes the wait's real state, never calls its action.
                    callbacks = tracker.callbacks[release_sensor.entity_id]
                    prior = tuple(callbacks)
                    self.assertTrue(prior, 'native wait listener must already be registered')
                    @callback
                    def release_dispatched(event):
                        if not release_observations or release_observations[0].get('minimum_dispatch_seen'):
                            return
                        graph, witness, _ = owned_graphs[0]
                        waiting = first[2]._variables['wait']
                        trigger = waiting.get('trigger')
                        witness_type = self.module('native_rename_emission_classifier').NativeRenameEmissions
                        frames = [(task, frame) for task in asyncio.all_tasks()
                            for frame in _frames(task.get_coro())
                            if frame.f_code is witness_type.finish.__code__
                            and frame.f_locals.get('self') is witness]
                        release_observations[0]['minimum_dispatch_seen'] = True
                        release_observations[0]['wait_released_before_retire'] = (
                            release_observations[0]['wait_was_pending_before_release']
                            and waiting['completed'] is True and type(trigger) is dict
                            and trigger.get('entity_id') == release_sensor.entity_id
                            and trigger.get('from_state') is event.data['old_state']
                            and trigger.get('to_state') is event.data['new_state']
                            and event.data['new_state'] is hass.states.get(release_sensor.entity_id)
                            and graph.live and not witness.finished
                            and len(frames) == 1 and frames[0][0] is witness.owner)
                    self.addCleanup(event_helper.async_track_state_change_event(
                        hass, release_sensor.entity_id, release_dispatched))
                    self.assertEqual(tuple(callbacks[:-1]), prior)
                    self.assertIs(callbacks[-1].target, release_dispatched)
                else:
                    self.addCleanup(hass.bus.async_listen('state_changed', source_changed))
                self.addCleanup(hass.bus.async_listen('active_automation_result', action_completed))
                async def release_queued_run():
                    event = await source_added
                    # Observe the actual suspended Root witness, not the
                    # independent control trigger's keyed listener.
                    witness_type = self.module('native_rename_emission_classifier').NativeRenameEmissions
                    node_type = self.module('native_template_state_registry_effect').NativeTemplateStateRegistryEffect
                    active = [(task, frame.f_locals.get('self')) for task in asyncio.all_tasks()
                        for frame in _frames(task.get_coro()) if frame.f_code is witness_type.finish.__code__
                        and type(frame.f_locals.get('self')) is witness_type
                        and frame.f_locals['self'].hass is hass
                        and frame.f_locals['self'].source is source]
                    task, witness = active[0] if len(active) == 1 else (None, None)
                    graph = getattr(witness, 'automation_graph', None)
                    node = getattr(graph, 'node', None)
                    row = self.f.entity.async_get(new)
                    routes = tuple((route.binding, route.wrapper, route.target,
                        route.binding.keyed_job, route.binding.listener,
                        route.binding.original_keys, route.binding.callbacks,
                        route.binding.unsubscribe) for route in getattr(graph, 'routes', ()))
                    routes_owned = bool(routes) and any(route[0].entity is entity for route in routes)
                    routes_owned = routes_owned and all(job.target is wrapper
                        for _, wrapper, _, job, _, _, _, _ in routes)
                    owned_graphs.append((graph, witness, routes))
                    waiting = first[2]._variables['wait']
                    was_waiting = waiting['completed'] is False
                    release_wait()
                    mutation_type = self.module('native_registry').NativeRegistryMutation
                    release_observations.append({
                        'source_uuid': row is not None and row.id == uuids[old],
                        'source_entity': source.entity_id == new,
                        'original_state_event': event.data['new_state'] is hass.states.get(new),
                        'owning_task': len(active) == 1 and task is getattr(witness, 'owner', None),
                        'registry_node': type(node) is node_type and node.witness is witness
                            and node.phase == 'registry_attempted' and node.change.identity == uuids[old],
                        'graph_live': bool(getattr(graph, 'live', False)),
                        'api_attempted': bool(getattr(witness, 'attempted', False)),
                        'native_mutation': type(getattr(witness, 'mutation', None)) is mutation_type,
                        'api_returned': not bool(getattr(witness, 'in_mutate', True)),
                        'drain_unfinished': not bool(getattr(witness, 'finished', True)),
                        'owned_routes': routes_owned,
                        'wait_released_before_retire': (not minimum_deferred_dispatch
                                                        and was_waiting and waiting['completed'] is True),
                    })
                    if minimum_deferred_dispatch:
                        release_observations[0]['wait_was_pending_before_release'] = was_waiting
                    await first_action
                    child, previous = first[1], first[2]
                    while not (len(child._runs) == 1 and child._runs[0] is not previous
                               and _wait_snapshot(child._runs[0])):
                        await asyncio.sleep(0)
                    release_wait()
                relay = asyncio.create_task(release_queued_run())
                # The producer has a real, not-yet-published reading. Its
                # native handoff publishes 29 after the old State was 17,
                # creating normal meaningful template delivery to drain.
                source._attr_native_value = 29
            result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
            record = await runtime.journal.load(result['operation_id'])
            baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
            original_ref, receipt = record.plan.native_inputs, baseline.receipt_json
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            if during_drain:
                await asyncio.wait_for(relay, 5)
                await _until(lambda: len(results) == 2 and not script._runs)
                self.assertEqual(len(release_observations), 1)
                self.assertTrue(all(release_observations[0].values()), release_observations[0])
                owned_graph, witness, routes = owned_graphs[0]
                self.assertFalse(owned_graph.live)
                self.assertFalse(owned_graph.routes)
                self.assertTrue(witness.finished)
                self.assertTrue(witness.closed)
                for route_binding, _, target, job, listener, keys, callbacks, unsubscribe in routes:
                    self.assertIs(job.target, listener)
                    self.assertIs(route_binding.original_keys, keys)
                    self.assertIs(route_binding.callbacks, callbacks)
                    self.assertIs(route_binding.unsubscribe, unsubscribe)
                    self.assertEqual(tuple(keys), target)
                    for route_key in target:
                        self.assertEqual(sum(member is job for member in callbacks.get(route_key, ())), 1)
                self.assertIs(entity.action_script, script)
                self.assertIs(self.producer, source)
                self.assertIs(binding.keyed_job, keyed_job)
                self.assertIs(keyed_job.target, native_listener)
                self.assertIs(binding.original_keys, native_keys)
                self.assertIs(binding.callbacks, native_callbacks)
                self.assertIs(binding.unsubscribe, native_unsubscribe)
                self.assertEqual(native_registrations(hass, entity), registrations_before)
            else:
                same(first)
                self.assertEqual(results, [])
                await drain(first, 0)
            expected_value = '29' if during_drain else '17'
            self.assertEqual([(str(e.data['token']), str(e.data['value']), e.context.id)
                              for e in results], [('1', expected_value, first[0][0]._context.id),
                                                  ('2', expected_value, first[0][1]._context.id)])
            second = await pair(3)
            inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertEqual(inverse['state'], 'rolled_back')
            reopened, restored = runtime.preparations.resolve_composition(record.plan.native_inputs)
            self.assertEqual((record.plan.native_inputs, reopened.receipt_json, restored),
                             (original_ref, receipt, composition))
            same(second)
            self.assertEqual(len(results), 2)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            await drain(second, 2)
            self.assertEqual([(str(e.data['token']), str(e.data['value']), e.context.id)
                              for e in results[2:]], [('3', expected_value, second[0][0]._context.id),
                                                       ('4', expected_value, second[0][1]._context.id)])
            self.assertEqual([str(event.data['new_state'].state) for event in foreign], ['1', '2', '3', '4'])
            for name, contents in original.items():
                self.assertEqual((root / name).read_bytes(), contents)
            self.assertTrue(await history.verify(before_history))
            self.assertTrue(await history.verify(composition.consumer_history))
        finally:
            if relay is not None and not relay.done():
                relay.cancel()
                await asyncio.gather(relay, return_exceptions=True)
            await script.async_stop()

    async def test_read_only_pins_refuse_a_late_real_queue_entry(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        _, mapping, _, _, _ = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        (root / 'mixed_scripts.yaml').write_text(
            'mixed_source:\n  mode: queued\n  max: 4\n  sequence:\n'
            '  - sequence:\n    - wait_for_trigger:\n      - trigger: event\n'
            '        event_type: active_transport_release\n      timeout: "00:05:00"\n'
            '    - event: active_transport_result\n      event_data:\n'
            '        value: "{{ states(' + repr(old) + ') }}"\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        entity = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        script, tasks = entity.script, []
        try:
            for count in (1, 2):
                tasks.append(hass.async_create_task(hass.services.async_call(
                    'script', 'mixed_source', {}, blocking=True, context=Context()),
                    f'actual queue entry {count}'))
                await _until(lambda: len(script._runs) == count)
            child = await _until(lambda: script._sequence_scripts.get(0))
            run = await _until(lambda: child._runs[0] if child._runs else None)
            await _until(lambda: _wait_snapshot(run))
            pins = self.module('native_active_execution_pins').ActiveWaitPins.capture(script)
            pins.check()
            original_sequence = tuple(child.sequence)
            tasks.append(hass.async_create_task(hass.services.async_call(
                'script', 'mixed_source', {}, blocking=True, context=Context()), 'late queue entry'))
            await _until(lambda: len(script._runs) == 3)
            with self.assertRaises(self.module('transaction').Refused):
                pins.check()
            self.assertEqual(tuple(child.sequence), original_sequence)
            self.assertEqual(tuple(child._runs), (run,))
        finally:
            await hass.services.async_call('script', 'turn_off', {'entity_id': entity.entity_id}, blocking=True)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_review_rejects_late_real_queue_without_overwriting_it(self):
        await self._negative_review_case('late_queue')

    async def test_review_rejects_foreign_native_child_without_overwriting_it(self):
        await self._negative_review_case('foreign_child')

    async def test_review_rejects_a_naturally_finished_wait(self):
        await self._negative_review_case('finished')

    async def test_queued_run_variable_foreign_alias_refuses_before_source_write(self):
        await self._negative_review_case('foreign_alias')

    async def test_foreign_listener_closure_alias_is_preserved_and_refused(self):
        await self._negative_review_case('foreign_listener')

    async def test_preexisting_queued_variable_alias_refuses_review(self):
        await self._negative_review_case('preexisting_alias')

    async def _negative_review_case(self, failure):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, _, original = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        path = root / 'mixed_scripts.yaml'
        path.write_text('mixed_source:\n  mode: queued\n  max: 4\n  sequence:\n'
            '  - sequence:\n    - wait_for_trigger:\n      - trigger: event\n'
            '        event_type: active_negative_release\n      timeout: "00:05:00"\n'
            '    - event: active_negative_result\n      event_data:\n'
            '        value: "{{ states(' + repr(old) + ') }}"\n'
            'other_script:\n  sequence:\n  - event: active_other_result\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        original['mixed_scripts.yaml'] = path.read_bytes()
        await self.stimulus(17)
        await self._flush_documents()
        entities = {e.unique_id: e for e in hass.data[DATA_INSTANCES]['script'].entities}
        entity, foreign = entities['mixed_source'], entities['other_script']
        script, tasks, results, foreign_events = entity.script, [], [], []
        self.addCleanup(hass.bus.async_listen('active_negative_result', results.append))
        async def start(data=None):
            tasks.append(hass.async_create_task(hass.services.async_call(
                'script', 'mixed_source', data or {}, blocking=True, context=Context()),
                'actual active negative queued call'))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        child = None
        try:
            await start()
            await _until(lambda: len(script._runs) == 1)
            child = await _until(lambda: script._sequence_scripts.get(0))
            run = await _until(lambda: child._runs[0] if child._runs else None)
            await _until(lambda: _wait_snapshot(run))
            aliased = child.sequence[1]['event_data']
            await start({'foreign_alias': aliased} if failure == 'preexisting_alias' else None)
            await _until(lambda: len(script._runs) == 2)
            if failure == 'preexisting_alias':
                self.assertIs(script._runs[1]._variables['foreign_alias'], aliased)
                with self.assertRaisesRegex(self.module('transaction').Refused, 'active_config_foreign_alias'):
                    await runtime.native_yaml_prepare(hass, mapping, actor_id='active-negative-admin',
                        guard=lambda: None, refresh=refresh)
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
                self.assertIs(script._runs[1]._variables['foreign_alias'], aliased)
                self.assertIs(child.sequence[1]['event_data'], aliased)
                return
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='active-negative-admin',
                guard=lambda: None, refresh=refresh)
            if failure == 'late_queue':
                await start()
                await _until(lambda: len(script._runs) == 3)
            elif failure == 'foreign_child':
                script._sequence_scripts[0] = foreign.script
            elif failure == 'finished':
                plan = runtime.reviews[review['operation_id']].plan
                baseline, composition = runtime.preparations.resolve_composition(plan.native_inputs)
                receipts = self.module('native_registry_receipts')
                store = receipts.native_registry_receipt_store(hass, runtime.preparations)
                await store.initialize()
                chain = receipts.RegistryReceiptChain.reopen(store, review['operation_id'], plan,
                    baseline.receipt, dict(composition.prepared_digests))
                observer = self.module('dependent_config_binding').NativeDependentConfigReadback(
                    hass, composition.dependent_config_program, composition.evidence,
                    baseline.receipt, composition.yaml_plan)
                transfer = self.module('native_active_execution_transport').ActiveScriptTransport.capture(
                    observer, chain, old, guard=lambda: None)
                graph = self.module('native_script_config_transport')._graph
                before_config = graph(entity.raw_config), graph(script.sequence)
                hass.bus.async_fire('active_negative_release')
                await _until(lambda: len(results) == 1)
                await _until(lambda: len(child._runs) == 1 and child._runs[0] is not run
                             and _wait_snapshot(child._runs[0]))
                hass.bus.async_fire('active_negative_release')
                await _until(lambda: len(results) == 2 and not script._runs)
                with self.assertRaises(self.module('transaction').Refused):
                    transfer.install()
                self.assertEqual((graph(entity.raw_config), graph(script.sequence)), before_config)
            elif failure == 'foreign_alias':
                # Install a real foreign reference in the accepted run's own
                # variables; the alias fence must preserve it and refuse.
                script._runs[1]._variables['foreign_alias'] = aliased
            elif failure == 'foreign_listener':
                def foreign_listener(event):
                    foreign_events.append((event, aliased))
                self.addCleanup(hass.bus.async_listen('active_foreign_probe', foreign_listener))
            count = len(results)
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='active-negative-admin', guard=lambda: None, refresh=refresh)
            self.assertEqual(len(results), count)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            for name, content in original.items():
                self.assertEqual((root / name).read_bytes(), content)
            if failure == 'foreign_child':
                self.assertIs(script._sequence_scripts[0], foreign.script)
            if failure == 'late_queue':
                self.assertEqual(len(script._runs), 3)
            if failure == 'foreign_alias':
                self.assertIs(script._runs[1]._variables['foreign_alias'], aliased)
                self.assertIs(child.sequence[1]['event_data'], aliased)
            if failure == 'foreign_listener':
                hass.bus.async_fire('active_foreign_probe')
                await _until(lambda: len(foreign_events) == 1)
                self.assertIs(foreign_events[0][1], aliased)
        finally:
            if failure == 'foreign_child' and child is not None:
                script._sequence_scripts[0] = child
            await hass.services.async_call('script', 'turn_off', {'entity_id': entity.entity_id}, blocking=True)
            await asyncio.gather(*tasks, return_exceptions=True)
