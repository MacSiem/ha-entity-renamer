"""Actual queued nested Script with own-ID and source-ID; owner FIFO only."""
import asyncio
import unittest

import test_native_active_execution_root as active
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeOwnIDActiveRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_script_stable_service_own_and_source_during_two_accepted_runs(self):
        await self._script_active(moving_service=False)

    async def test_script_native_service_move_and_source_during_two_accepted_runs(self):
        await self._script_active(moving_service=True)

    async def test_script_native_service_move_only_during_two_accepted_runs(self):
        await self._script_active(moving_service=True, include_source=False)

    async def test_script_native_service_move_without_alias_during_two_accepted_runs(self):
        await self._script_active(moving_service=True, script_alias=False)

    async def test_script_native_service_move_with_real_idle_caller_during_two_runs(self):
        await self._script_active(moving_service=True, with_caller=True)

    async def _script_active(self, *, moving_service, include_source=True, script_alias=True, with_caller=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, source_mapping, _, _, original = await self.data(with_automation=with_caller)
        hass, root = self.f.hass, self.f.root
        source_old, source_new = next(iter(source_mapping.items()))
        path = root / 'mixed_scripts.yaml'
        path.write_text('mixed_source:\n' + ('  alias: Own active script\n' if script_alias else '')
            + '  mode: queued\n  max: 4\n  sequence:\n'
            '  - sequence:\n    - wait_for_trigger:\n      - trigger: event\n'
            '        event_type: own_active_release\n      timeout: "00:05:00"\n'
            '      continue_on_timeout: false\n    - event: own_active_result\n'
            '      event_data:\n        token: "{{ token }}"\n'
            '        self_id: "{{ this.entity_id }}"\n'
            '        value: "{{ states(' + repr(source_old) + ') }}"\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        if with_caller:
            control = type(self.producer)()
            control._attr_unique_id = 'own-script-active-caller-control'
            control._attr_name = 'Own script active caller control'
            control._attr_native_value = 0
            await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([control])
            await hass.async_block_till_done()
            automation_path = root / 'mixed_automations.yaml'
            automation_path.write_text('- id: own_script_active_caller\n  alias: Own active caller\n'
                '  initial_state: true\n  triggers:\n  - trigger: state\n'
                '    entity_id: ' + control.entity_id + '\n  actions:\n'
                '  - action: script.mixed_source\n    data:\n      token: "caller"\n')
            await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            caller = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities
                          if e.unique_id == 'own_script_active_caller')
            original[automation_path.name] = automation_path.read_bytes()
        entity = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        if not moving_service:
            self.f.entity.async_update_entity(entity.entity_id, new_entity_id='script.own_active_alias')
            await hass.async_block_till_done()
            entity = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        own_old = entity.entity_id
        own_new = 'script.own_active_service_new' if moving_service else 'script.mixed_source'
        mapping = ({source_old: source_new, own_old: own_new} if include_source
                   else {own_old: own_new})
        uuids = {old: self.f.entity.async_get(old).id for old in mapping}
        original[path.name] = path.read_bytes()
        await self.stimulus(17)
        await self._flush_documents()
        history = self.module('history_guard').NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass))
        before_history = await history.capture(mapping)
        script, service = entity.script, hass.services._services['script']['mixed_source']
        events, foreign, tasks = [], [], []
        self.addCleanup(hass.bus.async_listen('own_active_result', events.append))
        self.addCleanup(hass.bus.async_listen('own_active_release', foreign.append))

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        async def call_native_caller():
            if not with_caller:
                return
            context, count = Context(), len(events)
            task = hass.async_create_task(hass.services.async_call('automation', 'trigger',
                {'entity_id': caller.entity_id}, blocking=True, context=context), 'actual automation caller')
            tasks.append(task)
            await active._until(lambda: len(script._runs) == 1)
            child = await active._until(lambda: script._sequence_scripts.get(0))
            await active._until(lambda: bool(child._runs) and active._wait_snapshot(child._runs[0]))
            hass.bus.async_fire('own_active_release')
            await active._until(lambda: len(events) == count + 1)
            await asyncio.wait_for(task, 5)
            self.assertEqual(events[-1].data['token'], 'caller')
            self.assertEqual(events[-1].context.parent_id, context.id)

        async def pair(label):
            contexts = (Context(), Context())
            for index, context in enumerate(contexts):
                tasks.append(hass.async_create_task(hass.services.async_call('script', entity.unique_id,
                    {'token': label + str(index)}, blocking=True, context=context),
                    'actual own-ID accepted queued call'))
                await active._until(lambda: len(script._runs) == index + 1)
            runs = tuple(script._runs)
            child = await active._until(lambda: script._sequence_scripts.get(0))
            run = await active._until(lambda: child._runs[0] if child._runs else None)
            wait = await active._until(lambda: active._wait_snapshot(run))
            return contexts, runs, child, run, wait

        def unchanged(pins):
            contexts, runs, child, run, wait = pins
            self.assertIs(entity.script, script)
            native_service = hass.services._services['script'][entity.unique_id]
            self.assertIs(native_service.job.target.__self__, entity)
            if not moving_service:
                self.assertIs(native_service, service)
            self.assertEqual(tuple(script._runs), runs)
            self.assertIs(script._sequence_scripts[0], child)
            self.assertIs(child.sequence, script.sequence[0]['sequence'])
            self.assertEqual(tuple(child._runs), (run,))
            self.assertEqual(active._wait_snapshot(run), wait)
            self.assertEqual(tuple(item._context for item in runs), contexts)
            self.assertFalse(runs[1].lock_acquired)

        def traces(contexts):
            from homeassistant.components.trace import DATA_TRACE
            from homeassistant.components.script.trace import ScriptTrace
            from homeassistant.const import __version__
            from copy import deepcopy
            # Use the actual native trace store and actual accepted Contexts.
            # Current uses TraceBuckets; minimum stores runs directly.
            bucket = hass.data[DATA_TRACE]['script.' + entity.unique_id]
            runs = bucket.runs if __version__ != '2025.6.0' else bucket
            selected = tuple(next(trace for trace in runs.values()
                                  if type(trace) is ScriptTrace and trace.context is context)
                             for context in contexts)
            self.assertEqual(len({id(trace) for trace in selected}), 2)
            for trace in selected:
                self.assertIs(trace._config, entity.raw_config)
            return tuple((trace, trace.context, deepcopy(trace._config)) for trace in selected)

        def historical_traces_unchanged(snapshots):
            for trace, context, original_config in snapshots:
                self.assertIs(trace.context, context)
                self.assertEqual(trace._config, original_config)

        async def release(pins, count):
            _, runs, child, run, _ = pins
            hass.bus.async_fire('own_active_release')
            await active._until(lambda: len(events) == count + 1)
            await active._until(lambda: len(child._runs) == 1 and child._runs[0] is not run
                                and active._wait_snapshot(child._runs[0]))
            self.assertTrue(runs[1].lock_acquired)
            hass.bus.async_fire('own_active_release')
            await active._until(lambda: len(events) == count + 2)
            await asyncio.wait_for(asyncio.gather(*tasks[-2:]), 5)

        try:
            await call_native_caller()
            forward_count = len(events)
            first = await pair('forward-')
            first_traces = traces(first[0])
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='own-active-admin',
                guard=lambda: None, refresh=refresh)
            unchanged(first)
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='own-active-admin', guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
            record = await runtime.journal.load(result['operation_id'])
            baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
            retained = record.plan.native_inputs, baseline.receipt_json
            unchanged(first)
            historical_traces_unchanged(first_traces)
            self.assertEqual(len(events), forward_count)
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            await release(first, forward_count)
            self.assertEqual([(e.data['token'], str(e.data['value']), e.context.id) for e in events[forward_count:]],
                [('forward-0', '17', first[0][0].id), ('forward-1', '17', first[0][1].id)])
            # Accepted runs keep their own historical ``this`` State. It is
            # data captured by native Script, not a mutable rename alias.
            self.assertEqual([e.data['self_id'] for e in events[forward_count:]], [own_old, own_old])
            await call_native_caller()
            inverse_count = len(events)
            second = await pair('inverse-')
            second_traces = traces(second[0])
            inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertEqual(inverse['state'], 'rolled_back')
            reopened, restored = runtime.preparations.resolve_composition(record.plan.native_inputs)
            self.assertEqual((record.plan.native_inputs, reopened.receipt_json), retained)
            self.assertEqual(restored, composition)
            unchanged(second)
            historical_traces_unchanged(first_traces)
            historical_traces_unchanged(second_traces)
            self.assertEqual(len(events), inverse_count)
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
            await release(second, inverse_count)
            self.assertEqual([(e.data['token'], str(e.data['value']), e.context.id) for e in events[inverse_count:]],
                [('inverse-0', '17', second[0][0].id), ('inverse-1', '17', second[0][1].id)])
            self.assertEqual([e.data['self_id'] for e in events[inverse_count:]], [own_new, own_new])
            await call_native_caller()
            self.assertEqual(len(foreign), 7 if with_caller else 4)
            for name, content in original.items():
                self.assertEqual((root / name).read_bytes(), content)
            self.assertTrue(await history.verify(before_history))
        finally:
            await hass.services.async_call('script', 'turn_off', {'entity_id': entity.entity_id}, blocking=True)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_automation_own_and_source_during_two_accepted_runs(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, source_mapping, _, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        source_old, source_new = next(iter(source_mapping.items()))
        control = type(self.producer)()
        control._attr_unique_id = 'own-active-independent-control'
        control._attr_name = 'Own active independent control'
        control._attr_native_value = 0
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([control])
        await hass.async_block_till_done()
        script_path = root / 'mixed_scripts.yaml'
        script_path.write_text('mixed_source:\n  sequence:\n  - event: own_active_unused_script\n')
        path = root / 'mixed_automations.yaml'
        path.write_text('- id: mixed_source\n  alias: Own active automation\n'
            '  initial_state: true\n  mode: queued\n  max: 4\n  triggers:\n'
            '  - trigger: state\n    entity_id: ' + control.entity_id + '\n'
            '  actions:\n  - sequence:\n    - wait_for_trigger:\n      - trigger: event\n'
            '        event_type: own_automation_release\n      timeout: "00:05:00"\n'
            '      continue_on_timeout: false\n    - event: own_automation_result\n'
            '      event_data:\n        token: "{{ trigger.to_state.state }}"\n'
            '        value: "{{ states(' + repr(source_old) + ') }}"\n')
        for domain in ('script', 'automation'):
            await hass.services.async_call(domain, 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        for file in (path, script_path):
            original[file.name] = file.read_bytes()
        await self.stimulus(17)
        await self._flush_documents()
        entity = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities if e.unique_id == 'mixed_source')
        own_old, own_new = entity.entity_id, 'automation.own_active_target'
        mapping = {source_old: source_new, own_old: own_new}
        uuids = {old: self.f.entity.async_get(old).id for old in mapping}
        history = self.module('history_guard').NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass))
        before_history = await history.capture(mapping)
        script = entity.action_script
        service = hass.services._services['automation']['trigger']
        results, foreign = [], []
        self.addCleanup(hass.bus.async_listen('own_automation_result', results.append))
        self.addCleanup(hass.bus.async_listen('state_changed',
            lambda event: foreign.append(event) if event.data.get('entity_id') == control.entity_id else None))

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        async def pair(first):
            for value in (first, first + 1):
                control._attr_native_value = value
                control.async_write_ha_state()
                await active._until(lambda: len(script._runs) == value - first + 1)
            runs = tuple(script._runs)
            child = await active._until(lambda: script._sequence_scripts.get(0))
            run = await active._until(lambda: child._runs[0] if child._runs else None)
            wait = await active._until(lambda: active._wait_snapshot(run))
            return runs, child, run, wait

        def unchanged(pins):
            runs, child, run, wait = pins
            self.assertIs(entity.action_script, script)
            self.assertIs(hass.services._services['automation']['trigger'], service)
            self.assertEqual(tuple(script._runs), runs)
            self.assertIs(script._sequence_scripts[0], child)
            self.assertIs(child.sequence, script.sequence[0]['sequence'])
            self.assertEqual(tuple(child._runs), (run,))
            self.assertEqual(active._wait_snapshot(run), wait)
            self.assertFalse(runs[1].lock_acquired)

        def traces(runs):
            from copy import deepcopy
            from homeassistant.components.automation.trace import AutomationTrace
            from homeassistant.components.trace import DATA_TRACE
            from homeassistant.const import __version__
            bucket = hass.data[DATA_TRACE]['automation.mixed_source']
            records = bucket.runs if __version__ != '2025.6.0' else bucket
            selected = tuple(next(trace for trace in records.values()
                                  if type(trace) is AutomationTrace and trace.context is run._context)
                             for run in runs)
            self.assertEqual(len({id(trace) for trace in selected}), 2)
            for trace in selected:
                self.assertIs(trace._config, entity.raw_config)
            return tuple((trace, trace.context, deepcopy(trace._config)) for trace in selected)

        def historical_traces_unchanged(snapshots):
            for trace, context, original_config in snapshots:
                self.assertIs(trace.context, context)
                self.assertEqual(trace._config, original_config)

        async def release(pins, count):
            runs, child, run, _ = pins
            hass.bus.async_fire('own_automation_release')
            await active._until(lambda: len(results) == count + 1)
            await active._until(lambda: len(child._runs) == 1 and child._runs[0] is not run
                                and active._wait_snapshot(child._runs[0]))
            self.assertTrue(runs[1].lock_acquired)
            hass.bus.async_fire('own_automation_release')
            await active._until(lambda: len(results) == count + 2 and not script._runs)

        try:
            first = await pair(1)
            first_traces = traces(first[0])
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='own-active-auto',
                guard=lambda: None, refresh=refresh)
            unchanged(first)
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='own-active-auto', guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
            record = await runtime.journal.load(result['operation_id'])
            baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
            retained = record.plan.native_inputs, baseline.receipt_json
            unchanged(first)
            historical_traces_unchanged(first_traces)
            self.assertEqual(results, [])
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            await release(first, 0)
            self.assertEqual([(str(e.data['token']), str(e.data['value']), e.context.id) for e in results],
                [('1', '17', first[0][0]._context.id), ('2', '17', first[0][1]._context.id)])
            second = await pair(3)
            second_traces = traces(second[0])
            inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertEqual(inverse['state'], 'rolled_back')
            reopened, restored = runtime.preparations.resolve_composition(record.plan.native_inputs)
            self.assertEqual((record.plan.native_inputs, reopened.receipt_json), retained)
            self.assertEqual(restored, composition)
            unchanged(second)
            historical_traces_unchanged(first_traces)
            historical_traces_unchanged(second_traces)
            self.assertEqual(len(results), 2)
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
            await release(second, 2)
            self.assertEqual([(str(e.data['token']), str(e.data['value']), e.context.id) for e in results[2:]],
                [('3', '17', second[0][0]._context.id), ('4', '17', second[0][1]._context.id)])
            self.assertEqual([str(event.data['new_state'].state) for event in foreign], ['1', '2', '3', '4'])
            for name, content in original.items():
                self.assertEqual((root / name).read_bytes(), content)
            self.assertTrue(await history.verify(before_history))
        finally:
            await script.async_stop()

    async def test_script_own_review_refuses_late_actual_queue_entry(self):
        await self._late_script_queue(with_source=True)

    async def test_script_own_only_review_refuses_late_queue_without_yaml_step(self):
        await self._late_script_queue(with_source=False)

    async def test_script_foreign_trace_listener_refuses_before_write(self):
        await self._late_script_queue(with_source=True, foreign_trace=True)

    async def _late_script_queue(self, *, with_source, foreign_trace=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, source_mapping, _, _, original = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        source_old, source_new = next(iter(source_mapping.items()))
        path = root / 'mixed_scripts.yaml'
        path.write_text('mixed_source:\n  mode: queued\n  max: 4\n  sequence:\n'
            '  - sequence:\n    - wait_for_trigger:\n      - trigger: event\n'
            '        event_type: own_late_release\n      timeout: "00:05:00"\n'
            '    - event: own_late_result\n      event_data:\n'
            '        value: "' + ('{{ states(' + repr(source_old) + ') }}' if with_source else '17') + '"\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        entity = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        self.f.entity.async_update_entity(entity.entity_id, new_entity_id='script.own_late_alias')
        await hass.async_block_till_done()
        entity = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        own_old, own_new = entity.entity_id, 'script.mixed_source'
        mapping = {source_old: source_new, own_old: own_new} if with_source else {own_old: own_new}
        uuids = {old: self.f.entity.async_get(old).id for old in mapping}
        original[path.name] = path.read_bytes()
        await self.stimulus(17)
        await self._flush_documents()
        script, events, tasks = entity.script, [], []
        self.addCleanup(hass.bus.async_listen('own_late_result', events.append))
        async def start():
            tasks.append(hass.async_create_task(hass.services.async_call('script', 'mixed_source', {},
                blocking=True, context=Context()), 'own-ID late accepted run'))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        remove_foreign = None
        try:
            for number in (1, 2):
                await start()
                await active._until(lambda: len(script._runs) == number)
            child = await active._until(lambda: script._sequence_scripts.get(0))
            run = await active._until(lambda: child._runs[0] if child._runs else None)
            wait = await active._until(lambda: active._wait_snapshot(run))
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='own-late-admin',
                guard=lambda: None, refresh=refresh)
            if not with_source:
                self.assertEqual(review['dependencies']['yaml_file_count'], 0)
            if foreign_trace:
                from homeassistant.components.trace import DATA_TRACE
                from homeassistant.const import __version__
                from homeassistant.core import callback
                from copy import deepcopy
                bucket = hass.data[DATA_TRACE]['script.mixed_source']
                records = bucket.runs if __version__ != '2025.6.0' else bucket
                trace = next(item for item in records.values() if item.context is script._runs[0]._context)
                self.assertIs(trace._config, entity.raw_config)
                original_trace_config = deepcopy(trace._config)
                foreign_seen = []
                @callback
                def foreign_trace_listener(event):
                    foreign_seen.append((event, trace))
                remove_foreign = hass.bus.async_listen('own_foreign_trace_alias', foreign_trace_listener)
                expected_runs = 2
            else:
                await start()
                await active._until(lambda: len(script._runs) == 3)
                expected_runs = 3
            with self.assertRaises((self.module('transaction').Refused, ValueError)):
                await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='own-late-admin', guard=lambda: None, refresh=refresh)
            self.assertEqual(len(script._runs), expected_runs)
            self.assertEqual(active._wait_snapshot(run), wait)
            if foreign_trace:
                self.assertIs(trace._config, entity.raw_config)
                self.assertEqual(trace._config, original_trace_config)
                self.assertEqual(foreign_seen, [])
                self.assertTrue(any(job.target is foreign_trace_listener for job, _ in
                                    hass.bus._listeners['own_foreign_trace_alias']))
            self.assertEqual(events, [])
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
            for name, content in original.items():
                self.assertEqual((root / name).read_bytes(), content)
            for expected in range(1, expected_runs + 1):
                previous = child._runs[0]
                hass.bus.async_fire('own_late_release')
                await active._until(lambda: len(events) == expected)
                if expected < expected_runs:
                    await active._until(lambda: len(child._runs) == 1 and child._runs[0] is not previous
                                        and active._wait_snapshot(child._runs[0]))
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
            self.assertEqual([str(event.data['value']) for event in events], ['17'] * expected_runs)
        finally:
            if remove_foreign is not None:
                remove_foreign()
            await hass.services.async_call('script', 'turn_off', {'entity_id': entity.entity_id}, blocking=True)
            await asyncio.gather(*tasks, return_exceptions=True)
