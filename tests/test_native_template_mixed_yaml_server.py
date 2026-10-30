"""Actual server-owned Template/automation/script forward and fresh reverse.

The fixture supplies native data, never a Plan or a positive verifier. Owner
runs this acceptance target through FIFO on the supported actual Core versions.
"""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import unittest

import test_native_template_yaml_server as server


@contextmanager
def native_diagnostics(module):
    """Observe sanitized product failures/positions without replacing a seam."""
    failures, positions, graph_diffs, source_issues, duration_frames = [], [], [], [], []
    tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
    sys.monitoring.use_tool_id(tool, 'mixed native YAML acceptance diagnosis')
    def raised(code, offset, error):
        if ('/custom_components/ha_entity_renamer/' not in code.co_filename
                or isinstance(error, (StopIteration, StopAsyncIteration, GeneratorExit))
                or str(error) == 'template_composition_rejected'):
            return
        row = (Path(code.co_filename).name, code.co_name, type(error).__name__, str(error)[:160])
        if row not in failures:
            failures.append(row)
            del failures[:-120]
        if code.co_name == '_need' and any(reason in str(error) for reason in
                ('duration_pending_changed_during_root', 'duration_rollback_slot_changed')):
            frame = sys._getframe(1).f_back
            stack = []
            for _ in range(8):
                if frame is None:
                    break
                route = frame.f_locals.get('route')
                if route is not None and hasattr(route, 'binding'):
                    log = route.duration_operations
                    stack.append({'function':frame.f_code.co_qualname,
                        'entity':route.binding.entity.unique_id,
                        'before_attempt':frame.f_locals.get('before_attempt'),
                        'wrapper_written':route.wrapper_written,
                        'log_present':log is not None,
                        'records':[(r[0][0],r[3]) for r in getattr(log,'records',())]})
                frame=frame.f_back
            duration_frames.append(stack)
            del duration_frames[:-6]
        if code.co_name == '_fail' and str(error) == 'registry_receipt_input_graph_changed':
            caller = sys._getframe(1).f_back
            if caller is not None and caller.f_code.co_name == 'assert_current':
                values = caller.f_locals
                before = {row[0]: row[1:] for row in values.get('expected', ())}
                after = {row[0]: row[1:] for row in values.get('current', ())}
                graph_diffs.append({'directory': values.get('directory'), 'entries': [
                    (name, before.get(name), after.get(name)) for name in sorted(before.keys() | after.keys())
                    if before.get(name) != after.get(name)]})
                del graph_diffs[:-3]
        if code.co_name == '_need' and str(error).endswith('yaml_proposal_required'):
            caller = sys._getframe(1).f_back
            plan = caller.f_locals.get('yaml') if caller is not None else None
            if plan is not None:
                source_issues.append([(issue.path, issue.line, issue.code) for issue in plan.issues])
                del source_issues[:-3]
    def returned(code, offset, value):
        row = {'owner': code.co_qualname}
        if type(value).__name__ == 'RegistryEvidence':
            row.update(position=value.position, reason=value.reason)
        else:
            row['position'] = getattr(value, 'value', type(value).__name__)
        positions.append(row)
        del positions[:-20]
    codes = [module('native_registry').NativeRegistryBridge.inspect.__code__,
             module('native_registry_transaction').ReceiptedRegistryTransactionAdapter.inspect.__code__,
             module('native_yaml_transaction').ReceiptedYAMLTransactionAdapter.inspect.__code__]
    try:
        sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, raised)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
        sys.monitoring.set_events(tool, sys.monitoring.events.RAISE)
        for code in codes:
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
        yield
    finally:
        sys.monitoring.set_events(tool, 0)
        for code in codes:
            sys.monitoring.set_local_events(tool, code, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, None)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
        sys.monitoring.free_tool_id(tool)
        print('MIXED_NATIVE_FAILURES', json.dumps(failures))
        print('MIXED_NATIVE_POSITIONS', json.dumps(positions))
        if graph_diffs:
            print('MIXED_NATIVE_INPUT_GRAPH_DIFF', json.dumps(graph_diffs))
        if source_issues:
            print('MIXED_NATIVE_YAML_SOURCE_ISSUES', json.dumps(source_issues))
        if duration_frames:
            print('MIXED_NATIVE_DURATION_FRAMES', json.dumps(duration_frames))


class CompositeGuardThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_physical_worker_does_not_read_dependent_ha_runtime(self):
        import importlib
        import threading
        runtime = importlib.import_module(server.activation.fixture.fixtures.PACKAGE + '.mixed_yaml_runtime')
        consumers = object.__new__(runtime.MixedYAMLConsumers)
        consumers.thread = threading.get_ident()
        class LoopOnly:
            def check(self):
                raise AssertionError('physical worker read the HA runtime')
        consumers.phases = LoopOnly()
        await asyncio.to_thread(consumers.check)


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateMixedYAMLServerTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    stimulus = server.NativeTemplateYAMLServerTests.stimulus

    async def data(self, *, with_automation=True):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        # This helper sets up native data only (validate=False). It constructs
        # no transaction Plan/receipt/chain and no positive verifier.
        runtime, mapping, uuids, _ = await server.NativeTemplateYAMLServerTests.data(
            self, variant='mixed', source_consumers=False)
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        (root / 'mixed_automations.yaml').write_text(
            '- id: mixed_source\n'
            '  alias: Mixed source observer\n'
            '  initial_state: true\n'
            '  triggers:\n'
            '  - trigger: state\n'
            '    entity_id: ' + old + '\n'
            '  actions:\n'
            '  - event: mixed_yaml_automation_result\n'
            '    event_data:\n'
            '      value: "{{ states(\'' + old + '\') }}"\n')
        (root / 'mixed_scripts.yaml').write_text(
            'mixed_source:\n'
            '  sequence:\n'
            '  - event: mixed_yaml_script_result\n'
            '    event_data:\n'
            '      value: "{{ states(\'' + old + '\') }}"\n')
        with (root / 'configuration.yaml').open('a') as handle:
            if with_automation:
                handle.write('\nautomation: !include mixed_automations.yaml\n')
            handle.write('\nscript: !include mixed_scripts.yaml\n')
        config = await async_hass_config_yaml(hass)
        for domain in (('automation', 'script') if with_automation else ('script',)):
            self.assertTrue(await async_setup_component(hass, domain, config))
        await hass.async_block_till_done()
        await self._flush_documents()
        # Keep registry UUID and all consumer IDs; native reload is expected to
        # replace objects/listener tokens while preserving this logical graph.
        dependents = tuple(sorted((row.platform, row.unique_id, row.entity_id, row.id)
            for row in self.f.entity.entities.values()
            if row.platform in ('automation', 'script')))
        expected = {('script', 'mixed_source')}
        if with_automation:
            expected.add(('automation', 'mixed_source'))
        self.assertEqual({(domain, key) for domain, key, _, _ in dependents}, expected)
        paths = ('configuration.yaml', 'template.yaml',
                 'mixed_automations.yaml', 'mixed_scripts.yaml')
        original = {path: (root / path).read_bytes() for path in paths}
        return runtime, mapping, uuids, dependents, original

    async def _native_staging_case(self, enabled):
        runtime, mapping, _, dependents, original = await self.data()
        hass, root = self.f.hass, self.f.root
        component = hass.data['automation']
        entity, = component.entities
        if not enabled:
            await entity.async_turn_off()
        events = []
        remove = hass.bus.async_listen('mixed_yaml_automation_result',
                                      lambda event: events.append(str(event.data['value'])))
        self.addCleanup(remove)
        native = self.module('native_automation_transition')
        transition = native.NativeAutomationTransition(hass)
        self.assertEqual(transition.enabled, (('mixed_source', enabled),))
        before_ids = {(key, row.id) for key, row in self.f.entity.entities.items()}
        await transition.quiesce()
        transition.assert_disabled()
        # A native reload would obey YAML initial_state:true. The staged native
        # constructor must stay disabled even after a changed configuration.
        path = root / 'mixed_automations.yaml'
        proposed = original['mixed_automations.yaml'].replace(
            b"states('", b"1 + states('").replace(b"') }}", b"')|int }}")
        self.assertNotEqual(proposed, original['mixed_automations.yaml'])
        path.write_bytes(proposed)
        await transition.stage()
        await hass.async_block_till_done()
        transition.assert_disabled()
        staged, = component.entities
        self.assertIsNot(staged, entity)
        self.assertEqual(staged.raw_config['initial_state'], True)
        self.assertEqual(staged._initial_state, False)
        self.assertEqual(events, [])
        self.assertEqual({(key, row.id) for key, row in self.f.entity.entities.items()}, before_ids)
        await transition.restore()
        await hass.async_block_till_done()
        self.assertIs(staged._is_enabled, enabled)
        self.assertEqual(events, [])
        await self.stimulus(11)
        await hass.async_block_till_done()
        self.assertEqual(events, ['12'] if enabled else [])
        events.clear()
        # Fresh capability, same native source and original physical bytes.
        reverse = native.NativeAutomationTransition(hass)
        await reverse.quiesce()
        path.write_bytes(original['mixed_automations.yaml'])
        await reverse.stage()
        reverse.assert_disabled()
        await reverse.restore()
        await hass.async_block_till_done()
        restored, = component.entities
        self.assertIs(restored._is_enabled, enabled)
        self.assertEqual(restored.raw_config, entity.raw_config)
        self.assertEqual(events, [])
        await self.stimulus(13)
        await hass.async_block_till_done()
        self.assertEqual(events, ['13'] if enabled else [])
        self.assertEqual({(key, row.id) for key, row in self.f.entity.entities.items()}, before_ids)

    async def test_native_staging_preserves_enabled_with_yaml_initial_true(self):
        await self._native_staging_case(True)

    async def test_native_staging_preserves_disabled_with_yaml_initial_true(self):
        await self._native_staging_case(False)

    async def test_template_values_have_no_synthetic_transition_during_rename(self):
        runtime, mapping, _, _, _ = await self.data()
        hass = self.f.hass
        baseline = {entity_id: hass.states.get(entity_id).state
                    for entity_id, _ in self.identities.values()}
        events = []
        def observed(event):
            entity_id, state = event.data.get('entity_id'), event.data.get('new_state')
            if entity_id in baseline:
                events.append((entity_id, None if state is None else state.state))
        self.addCleanup(hass.bus.async_listen('state_changed', observed))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual([row for row in events if row[1] != baseline[row[0]]], [],
            'a stable real source must not create zero/unavailable/removal Template transitions')
        events.clear()
        reverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(reverse['state'], 'rolled_back')
        self.assertEqual([row for row in events if row[1] != baseline[row[0]]], [])

    async def test_template_script_without_automation_forward_fresh_reverse(self):
        from homeassistant.core import Context
        runtime, mapping, uuids, _, original = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        self.assertIsNone(hass.data.get('automation'), 'fixture must not manufacture an empty native component')
        events = []
        self.addCleanup(hass.bus.async_listen('mixed_yaml_script_result',
            lambda event: events.append(str(event.data['value']))))
        history = self.module('history_guard').NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass))
        before = await history.capture(mapping)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        record = await runtime.journal.load(result['operation_id'])
        baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self.assertEqual(baseline.receipt.changed_domains, ('script', 'template'))
        original_receipt = baseline.receipt_json
        await self.stimulus(13)
        await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
        self.assertEqual(events, ['13'])
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        reverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(reverse['state'], 'rolled_back')
        for name, raw in original.items():
            self.assertEqual((root / name).read_bytes(), raw)
        reopened, restored = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self.assertEqual(reopened.receipt_json, original_receipt)
        self.assertEqual(restored, composition)
        await self.stimulus(17)
        await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
        self.assertEqual(events, ['13', '17'])
        self.assertTrue(await history.verify(before))
        self.assertTrue(await history.verify(composition.consumer_history))
        self.assertIsNone(hass.data.get('automation'))
        self.assertFalse(runtime.operation_lock.locked())
        async with runtime.operation_lock:
            opened = await self.module('native_template_yaml_server').NativeTemplateYAMLServer._open(
                hass, guard=lambda: None, refresh=refresh)
            opened.close()

    async def test_unrelated_automation_remains_live_during_owned_transition(self):
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        path.write_text(path.read_text() +
            '- id: foreign_idle\n  alias: Foreign unrelated automation\n  initial_state: true\n'
            '  triggers:\n  - trigger: event\n    event_type: renamer_unrelated_fixture\n'
            '  actions:\n  - event: renamer_foreign_result\n    event_data:\n      value: ok\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        entities = {entity.unique_id: entity for entity in hass.data['automation'].entities}
        target, foreign = entities['mixed_source'], entities['foreign_idle']
        listener = foreign._async_detach_triggers
        registry_id = self.f.entity.async_get(foreign.entity_id).id
        events, sampled = [], []
        remove = hass.bus.async_listen('renamer_foreign_result',
            lambda event: events.append(event.data['value']))
        self.addCleanup(remove)
        def guard():
            if not sampled and target._is_enabled is False:
                sampled.append(foreign._is_enabled)
                # A real unrelated event while only our target is detached.
                hass.bus.async_fire('renamer_unrelated_fixture')
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        result = await runtime.native_forward(hass, mapping, guard=guard, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(sampled, [True], 'migration must not disable an unrelated automation')
        self.assertEqual(events, ['ok'], 'unrelated real event must execute exactly once')
        self.assertIs(foreign._async_detach_triggers, listener)
        self.assertEqual(self.f.entity.async_get(foreign.entity_id).id, registry_id)
        inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(inverse['state'], 'rolled_back')
        self.assertIs(foreign._async_detach_triggers, listener)
        hass.bus.async_fire('renamer_unrelated_fixture')
        await hass.async_block_till_done()
        self.assertEqual(events, ['ok', 'ok'])
        self.assertIs(foreign._is_enabled, True)

    async def test_unrelated_running_automation_keeps_native_wait_and_deadline(self):
        from homeassistant.core import Context, callback
        from homeassistant.helpers.script import _ScriptRun
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        path.write_text(path.read_text() +
            '- id: foreign_wait\n  alias: Foreign native wait\n  initial_state: true\n'
            '  triggers:\n  - trigger: event\n    event_type: renamer_foreign_wait_start\n'
            '  actions:\n  - event: renamer_foreign_wait_started\n'
            '  - wait_for_trigger:\n    - trigger: event\n      event_type: renamer_foreign_wait_release\n'
            '    timeout: "00:05:00"\n    continue_on_timeout: false\n'
            '  - event: renamer_foreign_wait_finished\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        foreign = next(entity for entity in hass.data['automation'].entities if entity.unique_id == 'foreign_wait')
        script, detach = foreign.action_script, foreign._async_detach_triggers
        ready, finished = hass.loop.create_future(), hass.loop.create_future()
        events = []
        @callback
        def actual_event(event):
            events.append(event.event_type)
            if event.event_type == 'renamer_foreign_wait_finished' and not finished.done():
                finished.set_result(None)
        for kind in ('renamer_foreign_wait_started', 'renamer_foreign_wait_finished'):
            self.addCleanup(hass.bus.async_listen(kind, actual_event))
        code = _ScriptRun._async_wait_with_optional_timeout.__code__
        monitor = sys.monitoring
        tool = next(key for key in range(6) if monitor.get_tool(key) is None)
        monitor.use_tool_id(tool, 'unrelated actual native wait')
        held = {}
        def started(code_arg, offset):
            frame = sys._getframe(1)
            run = frame.f_locals.get('self')
            if getattr(run, '_script', None) is script and not ready.done():
                held.update(run=run, task=asyncio.current_task(), stop=run._stop,
                    handle=frame.f_locals['timeout_handle'], future=frame.f_locals['timeout_future'],
                    unsub=frame.f_locals['unsub'])
                held['deadline'], held['context'] = held['handle'].when(), held['handle']._context
                ready.set_result(None)
        monitor.register_callback(tool, monitor.events.PY_START, started)
        monitor.set_local_events(tool, code, monitor.events.PY_START)
        def same_wait():
            self.assertIs(foreign.action_script, script)
            self.assertIs(foreign._async_detach_triggers, detach)
            self.assertTrue(script.is_running)
            self.assertTrue(any(run is held['run'] for run in script._runs))
            self.assertIs(held['run']._stop, held['stop'])
            self.assertFalse(held['task'].done())
            self.assertFalse(held['handle'].cancelled())
            self.assertFalse(held['future'].done())
            self.assertEqual(held['handle'].when(), held['deadline'])
            self.assertIs(held['handle']._context, held['context'])
            node = held['task'].get_coro()
            for _ in range(128):
                frame = getattr(node, 'cr_frame', None)
                if frame is not None and frame.f_code is code and frame.f_locals.get('self') is held['run']:
                    self.assertIs(frame.f_locals['timeout_handle'], held['handle'])
                    self.assertIs(frame.f_locals['timeout_future'], held['future'])
                    self.assertIs(frame.f_locals['unsub'], held['unsub'])
                    return
                node = getattr(node, 'cr_await', None) or getattr(node, 'gi_yieldfrom', None)
                if node is None:
                    break
            self.fail('original native wait frame is no longer suspended')
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            hass.bus.async_fire('renamer_foreign_wait_start')
            await asyncio.wait_for(ready, 5)
            same_wait()
            result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
            same_wait()
            # Readable configuration does not authorize a generic reload of
            # the running automation. Use the actual server preparation.
            record = await runtime.journal.load(result['operation_id'])
            step = next(item for item in record.plan.steps if item.adapter == 'yaml')
            digest = runtime.preparations.plans[step.prepared_ref]['digest']
            change = self.module('prepared').decode_change(runtime.preparations.resolve(step.prepared_ref, digest))
            observer_module = self.module('yaml_runtime_observer')
            observer = observer_module.NativeYAMLRuntimeReadback(hass)
            rows, _ = observer._projection('automation')
            self.assertIn('foreign_wait', [row[0] for row in rows])
            self.assertFalse(observer_module._pending_native_work(hass, change.receipt.changed_domains))
            self.assertEqual(observer._sample(change), (self.module('transaction').Position.UNKNOWN, ()))
            inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertEqual(inverse['state'], 'rolled_back')
            same_wait()
        finally:
            monitor.set_local_events(tool, code, 0)
            monitor.register_callback(tool, monitor.events.PY_START, None)
            monitor.free_tool_id(tool)
            hass.bus.async_fire('renamer_foreign_wait_release')
            if held:
                await asyncio.wait_for(finished, 5)
        self.assertEqual(events, ['renamer_foreign_wait_started', 'renamer_foreign_wait_finished'])

    async def test_real_source_change_in_detached_interval_never_succeeds(self):
        from attr import asdict
        runtime, mapping, _, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        entity, = hass.data['automation'].entities
        before = {key: asdict(row) for key, row in self.f.entity.entities.items()}
        changed = False
        def guard():
            nonlocal changed
            if not changed and entity._is_enabled is False:
                changed = True
                # Real source event, no replacement observer/baseline or fake ACK.
                self.producer._attr_native_value = 17
                self.producer.async_write_ha_state()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaises(self.module('transaction').Refused):
            await runtime.native_forward(hass, mapping, guard=guard, refresh=refresh)
        self.assertTrue(changed)
        self.assertEqual({key: asdict(row) for key, row in self.f.entity.entities.items()}, before)
        for path, raw in original.items():
            self.assertEqual((root / path).read_bytes(), raw)
        records = await runtime.journal.active()
        self.assertEqual(len(records), 1)
        self.assertNotEqual(records[0].status, 'applied')
        store = self.module('native_registry_receipts').native_registry_receipt_store(hass, runtime.preparations)
        await store.initialize()
        key = self.module('native_automation_lifecycle')._key(
            records[0].operation_id, records[0].plan.fingerprint, 'forward', 'completed')
        self.assertIsNone(store.read(key))
        self.assertFalse(runtime.operation_lock.locked())

    async def test_running_automation_action_refuses_before_effects(self):
        from attr import asdict
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        path.write_text(path.read_text() + '  - delay: 60\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        entity, = hass.data['automation'].entities
        self.producer._attr_native_value = 5
        self.producer.async_write_ha_state()
        for _ in range(100):
            if entity.action_script.is_running:
                break
            await asyncio.sleep(0.001)
        self.assertTrue(entity.action_script.is_running)
        before = {key: asdict(row) for key, row in self.f.entity.entities.items()}
        files = {name: (root / name).read_bytes() for name in (
            'configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')}
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            self.assertEqual({key: asdict(row) for key, row in self.f.entity.entities.items()}, before)
            for name, raw in files.items():
                self.assertEqual((root / name).read_bytes(), raw)
            self.assertTrue(entity.action_script.is_running, 'admission must not cancel user actions')
            self.assertFalse(runtime.operation_lock.locked())
        finally:
            await entity.async_turn_off()

    async def test_dependent_source_identity_refuses_before_effects(self):
        from attr import asdict
        runtime, mapping, _, dependents, original = await self.data()
        hass, root = self.f.hass, self.f.root
        script_id = next(row[2] for row in dependents if row[0] == 'script')
        mapping[script_id] = 'script.mixed_source_renamed'
        before = {key: asdict(row) for key, row in self.f.entity.entities.items()}
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaisesRegex(self.module('transaction').Refused,
                                    'dependent_source_identity_unsupported'):
            await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual({key: asdict(row) for key, row in self.f.entity.entities.items()}, before)
        for path, raw in original.items():
            self.assertEqual((root / path).read_bytes(), raw)
        self.assertFalse(await runtime.journal.active())
        self.assertFalse(runtime.operation_lock.locked())

    async def test_one_mapping_actual_dependents_forward_and_fresh_reverse(self):
        await self._mixed_flow()

    async def test_retained_review_foreign_source_refuses_without_new_original(self):
        from attr import asdict
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
            guard=lambda: None, refresh=refresh)
        plan = runtime.reviews[review['operation_id']].plan
        original = runtime.preparations.resolve_composition(plan.native_inputs)[0].receipt_json
        before = {key: asdict(row) for key, row in self.f.entity.entities.items()}
        path = root / 'mixed_automations.yaml'
        path.write_bytes(path.read_bytes() + b'\n# foreign review edit\n')
        edited = {name: (root / name).read_bytes() for name in (
            'configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')}
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='native-admin', guard=lambda: None, refresh=refresh)
        self.assertNotIn(review['operation_id'], runtime.reviews)
        self.assertIsNone(await runtime.journal.load(review['operation_id']))
        self.assertEqual({key: asdict(row) for key, row in self.f.entity.entities.items()}, before)
        for name, raw in edited.items():
            self.assertEqual((root / name).read_bytes(), raw)
        self.assertEqual(runtime.preparations.resolve_composition(plan.native_inputs)[0].receipt_json, original)
        with self.assertRaises(self.module('transaction').Refused):
            await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='native-admin', guard=lambda: None, refresh=refresh)
        self.assertFalse(runtime.operation_lock.locked())

    async def test_retained_review_actual_dependents_forward_and_fresh_reverse(self):
        await self._mixed_flow(retained=True)

    async def _mixed_flow(self, *, retained=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, dependents, original = await self.data()
        hass, root = self.f.hass, self.f.root
        events = {'automation': [], 'script': []}
        for domain in events:
            def observed(event, domain=domain):
                events[domain].append(str(event.data['value']))
            remove = hass.bus.async_listen('mixed_yaml_' + domain + '_result', observed)
            self.addCleanup(remove)

        def assert_identities():
            self.assertEqual(tuple(sorted((row.platform, row.unique_id, row.entity_id, row.id)
                for row in self.f.entity.entities.values()
                if row.platform in ('automation', 'script'))), dependents)
            self.assertEqual({(p.domain, e.unique_id):
                (e.entity_id, self.f.entity.async_get(e.entity_id).id)
                for p in async_get_platforms(hass, 'template') for e in p.entities.values()},
                self.identities)

        async def stimulus(value):
            events['automation'].clear()
            events['script'].clear()
            await self.stimulus(value)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True,
                                           context=Context())
            await hass.async_block_till_done()
            self.assertEqual(events['automation'], [str(value)])
            self.assertEqual(events['script'], [str(value)])
            assert_identities()

        for value in (5, 7):
            await stimulus(value)
        history = self.module('history_guard').NativeHistoryGuard(
            self.module('recorder_guard').NativeRecorderGuard(hass))
        before_sources = await history.capture(mapping)
        consumer_ids = {entity_id: entity_id for entity_id, _ in self.identities.values()}
        consumer_ids.update({entity_id: entity_id for _, _, entity_id, _ in dependents})
        before_consumers = await history.capture(consumer_ids)
        self.assertTrue(before_consumers['state_ids'], 'real consumer Recorder rows required')

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        events['automation'].clear()
        events['script'].clear()
        # The ONLY caller input is the ordinary source mapping. In particular,
        # do not supply a fixture Plan, receipt, callback or alternate baseline.
        async def forward():
            if not retained:
                return await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
                guard=lambda: None, refresh=refresh)
            self.assertIsNone(await runtime.journal.load(review['operation_id']))
            self.assertEqual(review['entity_count'], len(mapping))
            for actor, fingerprint in (('different-admin', review['fingerprint']),
                                        ('native-admin', '0' * 64)):
                with self.assertRaises(self.module('transaction').Refused):
                    await runtime.native_yaml_apply(hass, review['operation_id'], fingerprint,
                        actor_id=actor, guard=lambda: None, refresh=refresh)
            # A genuine ordinary producer event between requests must remain
            # visible; it is not a replacement ORIGINAL or an admission token.
            await stimulus(9)
            events['automation'].clear()
            events['script'].clear()
            applied = await hass.async_create_task(runtime.native_yaml_apply(hass,
                review['operation_id'], review['fingerprint'], actor_id='native-admin',
                guard=lambda: None, refresh=refresh), 'fresh request applies retained plan', eager_start=False)
            self.assertEqual((applied['operation_id'], applied['fingerprint']),
                             (review['operation_id'], review['fingerprint']))
            with self.assertRaises(self.module('transaction').Refused):
                await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='native-admin', guard=lambda: None, refresh=refresh)
            return applied
        with native_diagnostics(self.module):
            result = await asyncio.wait_for(hass.async_create_task(
                forward(), 'actual mixed YAML forward', eager_start=False), 180)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(events['automation'], [], 'ID rename must not fire actions without a real source-value change')
        self.assertEqual(events['script'], [], 'migration must not invoke the user script')
        record = await runtime.journal.load(result['operation_id'])
        self.assertEqual(record.phases, ('verified',) * len(record.plan.steps))
        self.assertEqual(tuple(row.adapter for row in record.plan.steps),
                         ('registry',) * len(mapping) + ('yaml',))
        baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
        reference, original_json = record.plan.native_inputs, baseline.receipt_json
        self.assertEqual(baseline.receipt.changed_domains, ('automation', 'script', 'template'))
        self.assertEqual({row[0] for row in baseline.receipt.runtime_expected},
                         {'automation', 'script', 'template'})
        self.assertEqual({row[0] for row in baseline.receipt.proposed_files},
                         {'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'})
        self.assertEqual(set(dict(composition.prepared_digests)),
                         {row.prepared_ref for row in record.plan.steps})
        # Proposed typed mixed profile's consumer_history covers the complete
        # Template AND automation/script population, not a Template-only prefix.
        self.assertTrue(set(before_consumers['state_ids']) <=
                        set(composition.consumer_history['state_ids']))
        self.assertTrue(set(before_consumers['statistic_ids']) <=
                        set(composition.consumer_history['statistic_ids']))
        self.assertTrue(await history.verify(composition.source_history))
        self.assertTrue(await history.verify(composition.consumer_history))
        for old, new in mapping.items():
            self.assertIsNone(self.f.entity.async_get(old))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            for path in ('template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml'):
                self.assertNotIn(old.encode(), (root / path).read_bytes())
                self.assertIn(new.encode(), (root / path).read_bytes())
        for value in (11, 13):
            await stimulus(value)
        events['automation'].clear()
        events['script'].clear()
        from attr import asdict
        receipts_path = root / '.storage/ha_entity_renamer.registry_receipts'
        receipt_bytes = receipts_path.read_bytes()
        lifecycle = self.module('native_automation_lifecycle')
        protected = {name: (root / name).read_bytes() for name in original}
        before_registry = {key: asdict(row) for key, row in self.f.entity.entities.items()}
        journal_bytes = (root / '.storage/ha_entity_renamer.migrations').read_bytes()
        for missing in ('completed', 'stage_receipt'):
            with self.subTest(missing_outer_lifecycle=missing):
                damaged = json.loads(receipt_bytes)
                key = lifecycle._key(result['operation_id'], result['fingerprint'], 'forward', missing)
                self.assertIsNotNone(damaged['data']['events'].pop(key))
                try:
                    # Deliberate owned fixture loss, never a recovery permission.
                    receipts_path.write_text(json.dumps(damaged))
                    inspected = await runtime.recovery_inspect(hass, result['operation_id'],
                        result['fingerprint'], guard=lambda: None, refresh=refresh)
                    self.assertFalse(inspected['eligible'], 'inner applied does not prove completed automation restoration')
                    self.assertNotEqual(inspected['state'], 'applied')
                    second_mapping = {new: new + '_next' for new in mapping.values()}
                    with self.assertRaises(self.module('transaction').Refused):
                        await runtime.native_forward(hass, second_mapping,
                            guard=lambda: None, refresh=refresh)
                    with self.assertRaises(self.module('transaction').Refused):
                        await runtime.native_yaml_reverse(hass, result['operation_id'],
                            result['fingerprint'], guard=lambda: None, refresh=refresh)
                    self.assertEqual({key: asdict(row) for key, row in self.f.entity.entities.items()}, before_registry)
                    for name, raw in protected.items():
                        self.assertEqual((root / name).read_bytes(), raw)
                    self.assertEqual((root / '.storage/ha_entity_renamer.migrations').read_bytes(), journal_bytes)
                    self.assertEqual(events, {'automation': [], 'script': []})
                finally:
                    receipts_path.write_bytes(receipt_bytes)
        prior_stores = runtime.preparations, runtime.journal
        # Fresh native factory receives only opid/fingerprint; none of the live
        # forward session's objects or effects are handed into reverse.
        reverse = await asyncio.wait_for(hass.async_create_task(
            runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh),
            'actual fresh mixed YAML reverse', eager_start=False), 180)
        self.assertEqual(reverse['state'], 'rolled_back')
        self.assertEqual(events['automation'], [], 'reverse ID rename must not fire actions without a real source-value change')
        self.assertEqual(events['script'], [], 'reverse must not invoke the user script')
        self.assertIsNot(runtime.preparations, prior_stores[0])
        self.assertIsNot(runtime.journal, prior_stores[1])
        final = await runtime.journal.load(result['operation_id'])
        self.assertEqual(final.plan, record.plan)
        self.assertEqual(final.plan.native_inputs, reference)
        self.assertEqual(final.phases, ('undone',) * len(final.plan.steps))
        reopened, restored = runtime.preparations.resolve_composition(reference)
        self.assertEqual(reopened.receipt_json, original_json)
        self.assertEqual(restored, composition)
        for path, raw in original.items():
            self.assertEqual((root / path).read_bytes(), raw)
        for old, new in mapping.items():
            self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        for value in (17, 19):
            await stimulus(value)
        for snapshot in (before_sources, before_consumers,
                         composition.source_history, composition.consumer_history):
            self.assertTrue(await history.verify(snapshot))
        self.assertFalse(runtime.operation_lock.locked())
        for status in (result, reverse):
            self.assertIs(status['migration_ready'], False)
            self.assertIs(status['family_complete'], False)
        # A fully completed reverse must release new admission. This is a
        # read-only open; the already accepted mutations are not repeated.
        async with runtime.operation_lock:
            opened = await self.module('native_template_yaml_server').NativeTemplateYAMLServer._open(
                hass, guard=lambda: None, refresh=refresh)
            opened.close()
