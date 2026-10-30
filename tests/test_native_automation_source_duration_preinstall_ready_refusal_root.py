"""SOURCE-ONLY DRAFT; NOT RUN. Actual Root preinstall-ready refusal.

The 30 s natural schedule previously refused without raw queue evidence. This
distinct case must prove exact H membership at original location/raise, zero
native API/transport effects, then the same natural F and independent control.
No timing/admission claim follows from this source or an ordinary exception.
"""
import asyncio
import sys
import unittest

import test_native_automation_source_duration_root as source_fixture
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA,
                     'actual pinned HA required')
class NativeAutomationSourceDurationPreinstallReadyRefusalRootTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = source_fixture.NativeAutomationSourceDurationRootTests.asyncSetUp
    _before_start = source_fixture.NativeAutomationSourceDurationRootTests._before_start
    _flush_documents = source_fixture.NativeAutomationSourceDurationRootTests._flush_documents
    prepare = source_fixture.NativeAutomationSourceDurationRootTests.prepare
    emit = source_fixture.NativeAutomationSourceDurationRootTests.emit
    _runtime = source_fixture.NativeAutomationSourceDurationRootTests._runtime
    module = source_fixture.NativeAutomationSourceDurationRootTests.module
    stimulus = source_fixture.NativeAutomationSourceDurationRootTests.stimulus
    data = source_fixture.NativeAutomationSourceDurationRootTests.data
    _capability = source_fixture.NativeAutomationSourceDurationRootTests._capability

    async def test_actual_root_preinstall_ready_refuses_without_effect_then_same_native_f(self):
        from homeassistant.core import Context
        from homeassistant.helpers import template, config_validation as cv, issue_registry as ir
        from homeassistant.helpers.entity_component import DATA_INSTANCES

        runtime, mapping, uuids, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        body = "{{ 13 + states('" + old + "')|int }}"
        target_body = "{{ 13 + states('" + new + "')|int }}"
        control_body = "{{ 13 + states('sensor.duration_control')|int }}"
        (root / 'mixed_automations.yaml').write_text(
            '- id: mixed_source\n  alias: Source duration active expiry\n  initial_state: true\n'
            '  variables:\n    source: '+old+'\n    alias: "{{ source }}"\n'
            '  triggers:\n  - trigger: state\n    entity_id: '+old+'\n'
            '    from: "16"\n    for: "'+body+'"\n'
            '  actions:\n  - event: duration_source_result\n    event_data:\n'
            '      bound: "{{ trigger.entity_id }}"\n'
            '      previous: "{{ trigger.from_state.state }}"\n'
            '      current: "{{ trigger.to_state.state }}"\n'
            '      from_id: "{{ trigger.from_state.entity_id }}"\n'
            '      to_id: "{{ trigger.to_state.entity_id }}"\n'
            '      seconds: "{{ trigger.for.total_seconds() }}"\n'
            '      configured: "{{ states(alias) }}"\n'
            '  - action: script.mixed_source\n'
            '- id: duration_control\n  alias: Independent native source duration control\n'
            '  initial_state: true\n  triggers:\n  - trigger: state\n'
            '    entity_id: sensor.duration_control\n    from: "16"\n    to: "17"\n'
            '    for: "'+control_body+'"\n'
            '  actions:\n  - event: duration_control_result\n    event_data:\n'
            '      previous: "{{ trigger.from_state.state }}"\n'
            '      current: "{{ trigger.to_state.state }}"\n'
            '      seconds: "{{ trigger.for.total_seconds() }}"\n'
            '- id: duration_slow\n  alias: Native action expiry overlap\n  initial_state: true\n'
            '  triggers:\n  - trigger: state\n    entity_id: '+old+'\n'
            '    from: "17"\n    to: "29"\n'
            '  actions:\n  - delay: {seconds: 40}\n'
            '  - event: duration_slow_result\n    event_data:\n'
            '      value: "{{ states(\''+old+'\') }}"\n')
        (root / 'mixed_scripts.yaml').write_text(
            'mixed_source:\n  variables:\n    source: '+old+'\n  sequence:\n'
            '  - event: duration_script_result\n    event_data:\n'
            '      value: "{{ states(source) }}"\n')
        for domain in ('script', 'automation'):
            await hass.services.async_call(domain, 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        issues = ir.async_get(hass)
        store = issues._store
        if any(value is not None for value in
               (store._data, store._delay_handle, store._unsub_final_write_listener)):
            await store.async_save(issues._data_to_save())
        await hass.async_block_till_done()
        self.assertTrue(all(value is None for value in
            (store._data, store._delay_handle, store._unsub_final_write_listener)))
        await self._flush_documents()
        for name in ('mixed_automations.yaml', 'mixed_scripts.yaml'):
            original[name] = (root / name).read_bytes()

        entities = hass.data[DATA_INSTANCES]['automation'].entities
        source = next(e for e in entities if e.unique_id == 'mixed_source')
        control = next(e for e in entities if e.unique_id == 'duration_control')
        cap = await self._capability()
        duration_module = self.module('native_automation_duration_binding')
        cells, cell = duration_module._cells, duration_module._cell
        configured, control_configured = source._trigger_config[0]['for'], control._trigger_config[0]['for']
        configured_graph = self.module('native_script_config_transport')._graph
        original_configured_graph = configured_graph(configured)
        actions, scripts, controls, slow = [], [], [], []
        for name, events in (('duration_source_result', actions), ('duration_script_result', scripts),
                             ('duration_control_result', controls), ('duration_slow_result', slow)):
            self.addCleanup(hass.bus.async_listen(name, events.append))

        expiry_type = self.module('native_automation_duration_expiry').NativeDurationExpiry
        graph_type = self.module('native_automation_owned_graph').OwnedAutomationGraph
        root_type = self.module('native_template_state_server').NativeTemplateStateServer
        registry_type = self.module('native_registry').NativeRegistryIO
        need = duration_module._need
        code = {
            'root': root_type._install.__code__,
            'graph': graph_type.__init__.__code__,
            'location': expiry_type.location.__code__,
            'registry': registry_type.mutate.__code__,
            'handoff': self.module('native_source_handoff').NativeSourceHandoff.mutate.__code__,
            'install': graph_type.install.__code__,
            'config_install': self.module('native_automation_config_transport').NativeAutomationConfigTransport.install.__code__,
            'attach': cap.native.event.async_track_same_state.__code__,
        }
        render_codes = (template.render_complex.__code__, template.Template.async_render.__code__,
                        type(cv.positive_time_period).__call__.__code__)
        roots, graphs, entries, raised, timeline = [], [], [], [], []
        effects = {key: [] for key in ('registry', 'handoff', 'install', 'config_install')}
        renders, template_calls, validations, attachments = [], [], [], []
        starts, returns = {'source': [], 'control': []}, {'source': [], 'control': []}
        pending = {}
        expiry_code = None
        before = native_pending = retained = None
        reason = 'automation_state_binding_duration_timer_not_scheduled'

        def membership(handle):
            # Raw read of the ACTUAL queues, never a deadline/flag proxy.
            return (sum(item is handle for item in hass.loop._scheduled),
                    sum(item is handle for item in hass.loop._ready))

        def image(p, binding):
            history = cells(p['action'])
            return (p['clear'], p['cancel'], p['keys'], p['cancel_job'], p['handle'],
                    p['handle'].when(), p['handle']._context, p['timer_job'], p['same_job'],
                    p['action'], history['from_s'], history['to_s'], history['event'],
                    history['event'].context, binding.listener_cells['period'][p['key']])

        def observed_template(value):
            return value is configured or value is control_configured

        def location_image(actor, binding, handle):
            # Only read existing objects. No product methods or native stimuli.
            matching = tuple(graph for graph in graphs if graph.duration is actor.capability)
            return dict(actor=actor, binding=binding, handle=handle, count=membership(handle),
                        graphs=matching, live=tuple(graph.live for graph in matching),
                        roots=tuple(roots), records=tuple(actor.records), witness=actor.witness,
                        owners=tuple(bound for bound in roots
                            if getattr(bound.consumers, 'owned_automation', None) in matching),
                        history=image(native_pending, before),
                        configured=binding.config['for'],
                        listener=cell(binding.listener, 'time_delta').cell_contents,
                        keys=tuple(binding.original_keys),
                        effect_counts=tuple(len(effects[key]) for key in effects))

        def start(actual, offset):
            frame = sys._getframe(1)
            try:
                local, actor = frame.f_locals, frame.f_locals.get('self')
                if actual is code['root'] and actor.hass is hass:
                    roots.append(actor)
                elif actual is code['graph'] and local.get('hass') is hass:
                    # Store identity at entry; __init__ completes normally before
                    # any location census can use graph.duration.
                    graphs.append(actor)
                elif actual is code['location'] and native_pending is not None:
                    if local.get('handle') is native_pending['handle']:
                        row = location_image(actor, local['binding'], local['handle'])
                        entries.append(row)
                        timeline.append(('location', row['count']))
                elif actual is code['attach'] and local.get('hass') is hass:
                    entity_ids = local.get('entity_ids')
                    if entity_ids in (old, new, 'sensor.duration_control'):
                        attachments.append(entity_ids)
                elif actual is expiry_code:
                    for label, (p, binding, saved) in pending.items():
                        if local.get('job') is p['same_job']:
                            stack, parent = [], frame.f_back
                            while parent is not None:
                                stack.append((parent.f_code, parent.f_locals.get('self'),
                                              parent.f_locals.get('handle')))
                                parent = parent.f_back
                            starts[label].append(dict(count=membership(p['handle']), stack=stack,
                                history=image(p, binding), clear=local.get('clear_listener'),
                                job=local.get('job'), hass=local.get('hass'),
                                live=tuple(graph.live for graph in graphs)))
                            timeline.append(('F', label))
                else:
                    for key in effects:
                        if (actual is code[key] and (getattr(actor, 'hass', None) is hass
                                or getattr(getattr(actor, 'observer', None), 'hass', None) is hass)):
                            effects[key].append(actor)
            finally:
                del frame

        def returned(actual, offset, value):
            frame = sys._getframe(1)
            try:
                local = frame.f_locals
                if actual is expiry_code:
                    for label, (p, binding, saved) in pending.items():
                        if local.get('job') is p['same_job']:
                            returns[label].append(value)
                elif actual is render_codes[0] and observed_template(local.get('value')):
                    renders.append((local['value'], dict(local['variables']), value))
                elif actual is render_codes[1] and observed_template(local.get('self')):
                    template_calls.append((local['self'], dict(local['variables']), value))
                elif actual is render_codes[2] and local.get('self') is cv.positive_time_period:
                    caller = frame.f_back
                    owner = caller.f_locals.get('time_delta')
                    binding = caller.f_locals.get('binding')
                    if binding is not None and binding.hass is hass:
                        owner = binding.config['for']
                    if observed_template(owner):
                        validations.append((local.get('value', local.get('v')), value))
            finally:
                del frame

        def on_raise(actual, offset, exception):
            # RAISE is not a local CPython event. Filter this existing _need
            # code AND its immediate original location frame, never a generic
            # product error. All other global occurrences return immediately.
            if actual is not need.__code__ or native_pending is None:
                return
            frame = sys._getframe(1)
            try:
                caller = frame.f_back
                if (caller is None or caller.f_code is not code['location']
                        or caller.f_locals.get('handle') is not native_pending['handle']
                        or frame.f_locals.get('reason') != 'duration_timer_not_scheduled'):
                    return
                local = caller.f_locals
                row = location_image(local['self'], local['binding'], local['handle'])
                row['exception'] = exception
                raised.append(row)
                timeline.append(('raise', row['count']))
            finally:
                del frame

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        def assert_image(actual, saved):
            self.assertEqual(actual, saved)
            for index, expected in enumerate(saved):
                if index != 5:
                    self.assertIs(actual[index], expected)

        monitor = sys.monitoring
        tools_before = tuple(monitor.get_tool(key) for key in range(6))
        tool = next(key for key, value in enumerate(tools_before) if value is None)
        name = 'actual Root preinstall-ready refusal draft'
        monitor.use_tool_id(tool, name)
        installed_codes = set(code.values()) | set(render_codes)
        try:
            monitor.register_callback(tool, monitor.events.PY_START, start)
            monitor.register_callback(tool, monitor.events.PY_RETURN, returned)
            monitor.register_callback(tool, monitor.events.RAISE, on_raise)
            monitor.set_events(tool, monitor.events.RAISE)
            for actual in code.values():
                monitor.set_local_events(tool, actual, monitor.events.PY_START)
            for actual in render_codes:
                monitor.set_local_events(tool, actual, monitor.events.PY_RETURN)
            await self.stimulus(16)
            await self.stimulus(17)
            await hass.async_block_till_done()
            physical = hass.states.get(old)
            self.assertEqual(physical.state, '17')
            before = cap.capture(source)
            native_pending, = cap.pending(before)[0]
            self.assertIsNone(native_pending['own'])
            retained = image(native_pending, before)
            self.assertEqual(retained[14].total_seconds(), 30)
            self.assertEqual(membership(retained[4]), (1, 0))
            pending['source'] = native_pending, before, retained
            expiry_code = native_pending['timer_job'].target.__code__
            installed_codes.add(expiry_code)
            monitor.set_local_events(tool, expiry_code,
                                     monitor.events.PY_START | monitor.events.PY_RETURN)
            source_render, = [row for row in renders if row[0] is configured]
            self.assertEqual(source_render[2], 30)
            self.assertIs(source_render[1]['trigger']['from_state'], retained[10])
            self.assertIs(source_render[1]['trigger']['to_state'], retained[11])
            self.assertEqual((retained[10].state, retained[11].state), ('16', '17'))
            self.assertIs(retained[11], physical)
            self.assertTrue(any(value == 30 and result is retained[14]
                                for value, result in validations))
            hass.states.async_set('sensor.duration_control', '16')
            hass.states.async_set('sensor.duration_control', '17')
            await hass.async_block_till_done()
            control_binding = cap.capture(control)
            control_pending, = cap.pending(control_binding)[0]
            control_image = image(control_pending, control_binding)
            self.assertEqual(control_image[14].total_seconds(), 30)
            self.assertIs(control_pending['timer_job'].target.__code__, expiry_code)
            pending['control'] = control_pending, control_binding, control_image
            self.assertEqual(attachments, [old, 'sensor.duration_control'])
            await self._flush_documents()
            history = self.module('history_guard').NativeHistoryGuard(
                self.module('recorder_guard').NativeRecorderGuard(hass))
            original_history = await history.capture(mapping)
            # Accepted private input documents, not preparatory product journals.
            private_paths = tuple(root / '.storage' / key for key in
                                  ('core.entity_registry', 'core.device_registry', 'core.config_entries'))
            private_before = {path: path.read_bytes() for path in private_paths}
            counts = len(renders), len(template_calls), len(validations)
            roots.clear()
            error = None
            try:
                await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            except ValueError as caught:
                error = caught
            self.assertIsNotNone(error, 'must refuse; an applied/unknown return is not this proof')
            self.assertIs(type(error), ValueError)
            self.assertEqual(str(error), reason)
            self.assertTrue(roots, 'must reach the real NativeTemplateStateServer install')
            self.assertTrue(all(type(bound) is root_type for bound in roots))
            ready_entries = [row for row in entries if row['count'] == (0, 1) and row['roots']]
            ready_raises = [row for row in raised if row['count'] == (0, 1) and row['roots']]
            self.assertTrue(ready_entries, 'UNPROVED TIMING: exact native H never reached preinstall ready')
            self.assertTrue(ready_raises, 'generic duration error does not prove ready refusal')
            self.assertTrue(any(row['exception'] is error for row in ready_raises),
                            'the observed original ready refusal must propagate to the API caller')
            for row in ready_entries + ready_raises:
                self.assertIs(row['handle'], retained[4])
                self.assertTrue(row['graphs'], 'must belong to the actual Root graph capability')
                self.assertTrue(row['owners'], 'the exact capability must be owned by this actual Root')
                self.assertEqual(row['live'], (False,) * len(row['graphs']))
                self.assertEqual(row['records'], ())
                self.assertEqual(row['keys'], (old,))
                self.assertIs(row['configured'], configured)
                self.assertIs(row['listener'], configured)
                self.assertEqual(row['effect_counts'], (0, 0, 0, 0))
                assert_image(row['history'], retained)
            # Root may await during refusal unwinding: native F may already have
            # run by this return. Order is attested below from actual entries.
            await asyncio.sleep(max(0, max(retained[5], control_image[5]) - hass.loop.time()) + 0.05)
            await hass.async_block_till_done()
            self.assertFalse(runtime.operation_lock.locked())
            self.assertEqual(effects, {key: [] for key in effects})
            self.assertEqual((len(actions), len(scripts), len(controls), len(slow)), (1, 1, 1, 0))
            self.assertEqual(attachments, [old, 'sensor.duration_control'])
            first_refusal = timeline.index(('raise', (0, 1)))
            self.assertGreater(timeline.index(('F', 'source')), first_refusal)
            self.assertGreater(timeline.index(('F', 'control')), first_refusal)
            for label, (p, binding, saved) in pending.items():
                occurrence, = starts[label]
                self.assertEqual(returns[label], [None])
                self.assertEqual(occurrence['count'], (0, 0))
                self.assertIs(occurrence['clear'], p['clear'])
                self.assertIs(occurrence['job'], p['same_job'])
                self.assertIs(occurrence['hass'], hass)
                self.assertFalse(any(occurrence['live']))
                assert_image(occurrence['history'], saved)
                self.assertTrue(any(c is asyncio.Handle._run.__code__ and actor is p['handle']
                                    for c, actor, handle in occurrence['stack']))
                self.assertTrue(any(c is asyncio.BaseEventLoop._run_once.__code__
                                    and actor is hass.loop and handle is p['handle']
                                    for c, actor, handle in occurrence['stack']))
                self.assertEqual(p['handle'].when(), saved[5])
                self.assertIs(p['handle']._context, saved[6])
                self.assertEqual(membership(p['handle']), (0, 0))
                self.assertEqual(cap.pending(cap.capture(binding.entity))[0], ())
            self.assertEqual(tuple(str(actions[0].data[key]) for key in
                ('bound', 'previous', 'current', 'from_id', 'to_id', 'configured')),
                (old, '16', '17', old, old, '17'))
            self.assertEqual(float(actions[0].data['seconds']), 30)
            self.assertEqual(actions[0].context.parent_id, retained[12].context.id)
            self.assertEqual(str(scripts[0].data['value']), '17')
            self.assertEqual(tuple(str(controls[0].data[key]) for key in ('previous', 'current')), ('16', '17'))
            self.assertEqual(float(controls[0].data['seconds']), 30)
            self.assertEqual(controls[0].context.parent_id, control_image[12].context.id)
            self.assertEqual((len(renders), len(template_calls), len(validations)), counts)
            current = cap.capture(source)
            self.assertIs(current.config['for'], configured)
            self.assertIs(cell(current.listener, 'time_delta').cell_contents, configured)
            self.assertIs(current.listener, before.listener)
            self.assertIs(current.keyed_job, before.keyed_job)
            self.assertIs(current.keyed_job.target, before.listener)
            self.assertEqual(current.original_keys, [old])
            self.assertEqual(configured_graph(configured), original_configured_graph)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            self.assertIs(hass.states.get(old), physical)
            self.assertIsNone(hass.states.get(new))
            for path, content in private_before.items():
                self.assertEqual(path.read_bytes(), content)
            for path, content in original.items():
                self.assertEqual((root / path).read_bytes(), content)
            self.assertTrue(await history.verify(original_history))
            for bound in roots:
                self.assertTrue(bound.closed)
                self.assertFalse(bound.bound)
            for graph in graphs:
                self.assertFalse(graph.live)
                self.assertEqual(graph.routes, [])
                if graph.duration is not None:
                    self.assertEqual(graph.duration.expiry.records, {})
                    self.assertIsNone(graph.duration.expiry.witness)
            # Every other tool must have exactly its original owner after the
            # failed real Root has performed its ordinary cleanup.
            self.assertEqual(tuple(monitor.get_tool(key) for key in range(6)),
                             tuple(name if key == tool else value
                                   for key, value in enumerate(tools_before)))
        finally:
            print('NATIVE_PREINSTALL_READY_REFUSAL', {
                'entry_counts': [row['count'] for row in entries],
                'raise_counts': [row['count'] for row in raised],
                'timeline': timeline,
                'effects': {key: len(value) for key, value in effects.items()},
                'F': {key: len(value) for key, value in starts.items()},
                'actions': (len(actions), len(scripts), len(controls), len(slow)),
            })
            monitor.set_events(tool, 0)
            for actual in installed_codes:
                monitor.set_local_events(tool, actual, 0)
            for event in (monitor.events.PY_START, monitor.events.PY_RETURN, monitor.events.RAISE):
                monitor.register_callback(tool, event, None)
            monitor.free_tool_id(tool)
        self.assertIsNone(monitor.get_tool(tool))
        self.assertEqual(monitor.get_events(tool), 0)
        self.assertTrue(all(monitor.get_local_events(tool, actual) == 0 for actual in installed_codes))
        self.assertEqual(tuple(monitor.get_tool(key) for key in range(6)), tools_before)
