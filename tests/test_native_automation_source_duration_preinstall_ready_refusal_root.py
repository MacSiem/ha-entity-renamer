"""SOURCE-ONLY DRAFT; NOT RUN. Structured actual Root preinstall-ready refusal.

The 30 s natural schedule previously refused without raw queue evidence. This
distinct case must prove exact H membership at original location/raise, zero
native API/transport effects for a configured dict with a source Template leaf
and static sibling, then the same natural F and independent structured control.
No timing/admission claim follows from this source or an ordinary exception.
"""
import asyncio
import sys
from types import CodeType, FunctionType
import unittest
from unittest.mock import patch

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
    async def _capability(self):
        native = await self.f.hass.async_add_executor_job(
            self.module('native_automation_state_binding')._Native, self.f.hass)
        return await self.f.hass.async_add_executor_job(
            self.module('native_automation_duration_binding').NativeAutomationDurationCapability,
            self.f.hass, native, True, True)

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
            '    from: "16"\n    for:\n      seconds: "'+body+'"\n      milliseconds: 0\n'
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
            '    for:\n      seconds: "'+control_body+'"\n      milliseconds: 0\n'
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
        self.assertIs(type(configured), dict)
        self.assertIs(type(control_configured), dict)
        leaf, control_leaf = configured['seconds'], control_configured['seconds']
        for actual, expected_body in ((leaf, body), (control_leaf, control_body)):
            self.assertIs(type(actual), template.Template)
            self.assertEqual(actual.template, expected_body)
            self.assertIs(actual.hass, hass)
        self.assertEqual(tuple(configured), ('seconds', 'milliseconds'))
        self.assertEqual(tuple(control_configured), ('seconds', 'milliseconds'))
        self.assertIs(type(configured['milliseconds']), int)
        self.assertIs(type(control_configured['milliseconds']), int)
        self.assertEqual(configured['milliseconds'], 0)
        self.assertEqual(control_configured['milliseconds'], 0)
        configured_items = tuple(configured.items())
        control_items = tuple(control_configured.items())
        original_configured_graph = configured_graph(configured)
        original_control_graph = configured_graph(control_configured)
        configured_module = self.module('native_automation_configured_duration')
        original_leaf_pin = configured_module._template_pin(leaf)
        original_control_leaf_pin = configured_module._template_pin(control_leaf)
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
            'prepare': root_type._prepare.__code__,
            'graph': graph_type.__init__.__code__,
            'location': expiry_type.location.__code__,
            'registry': registry_type.mutate.__code__,
            'handoff': self.module('native_source_handoff').NativeSourceHandoff.mutate.__code__,
            'install': graph_type.install.__code__,
            'config_install': self.module('native_automation_config_transport').NativeAutomationConfigTransport.install.__code__,
            'template_install': self.module('native_template_binding_transport').NativeTemplateBindingTransport.install_before_registry.__code__,
            'effect_mutate': self.module('native_template_state_registry_effect').NativeTemplateStateRegistryEffect.mutate.__code__,
            'attach': cap.native.event.async_track_same_state.__code__,
        }
        render_codes = (template.render_complex.__code__, template.Template.async_render.__code__,
                        type(cv.positive_time_period).__call__.__code__)
        roots, root_installs, graphs, entries, raised, timeline, gate = [], [], [], [], [], [], []
        root_times, expiry_times = [], []
        effects = {key: [] for key in ('registry', 'handoff', 'install', 'config_install',
                                       'template_install', 'effect_mutate')}
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

        def observed_root(value):
            return value is configured or value is control_configured

        def observed_leaf(value):
            return value is leaf or value is control_leaf

        def owning_scope_stack(frame, owner):
            """Read the real caller chain: location -> this scope -> this _prepare."""
            scope_seen = False
            while frame is not None:
                if (frame.f_code is owner._scope.__code__
                        and frame.f_locals.get('obj') is owner):
                    scope_seen = True
                if (frame.f_code is code['prepare']
                        and frame.f_locals.get('self') is owner):
                    return scope_seen, True
                frame = frame.f_back
            return scope_seen, False

        def location_image(actor, binding, handle, frame):
            # Only read existing objects. No product methods or native stimuli.
            matching = tuple(graph for graph in graphs if graph.duration is actor.capability)
            owner = gate[0]['owner'] if gate else None
            stack = owning_scope_stack(frame, owner) if owner is not None else (False, False)
            return dict(actor=actor, binding=binding, handle=handle, count=membership(handle),
                        observed_at=hass.loop.time(),
                        graphs=matching, live=tuple(graph.live for graph in matching),
                        roots=tuple(roots), records=tuple(actor.records), witness=actor.witness,
                        owners=tuple(bound for bound in roots
                            if getattr(getattr(bound, 'consumers', None), 'owned_automation', None) in matching),
                        owning_stack=stack,
                        gate_owner=owner,
                        post_start=bool(gate and getattr(owner, 'chain', None) is gate[0]['chain']),
                        owner_task=bool(gate and asyncio.current_task() is gate[0]['task']
                                        and owner.task is gate[0]['task']),
                        preinstall=bool(owner is not None and not owner.bound and not root_installs),
                        history=image(native_pending, before),
                        configured=binding.config['for'],
                        configured_items=tuple(binding.config['for'].items()),
                        listener=cell(binding.listener, 'time_delta').cell_contents,
                        keys=tuple(binding.original_keys),
                        effect_counts=tuple(len(effects[key]) for key in effects))

        def start(actual, offset):
            frame = sys._getframe(1)
            try:
                local, actor = frame.f_locals, frame.f_locals.get('self')
                if actual is code['prepare'] and actor.hass is hass:
                    roots.append(actor)
                    root_times.append(hass.loop.time())
                elif actual is code['root'] and actor.hass is hass:
                    root_installs.append(actor)
                elif actual is code['graph'] and local.get('hass') is hass:
                    # Store identity at entry; __init__ completes normally before
                    # any location census can use graph.duration.
                    graphs.append(actor)
                elif actual is code['location'] and native_pending is not None:
                    if local.get('handle') is native_pending['handle']:
                        row = location_image(actor, local['binding'], local['handle'], frame)
                        entries.append(row)
                        timeline.append(('location', row['count']))
                elif actual is code['attach'] and local.get('hass') is hass:
                    entity_ids = local.get('entity_ids')
                    if entity_ids in (old, new, 'sensor.duration_control'):
                        attachments.append(entity_ids)
                elif actual is expiry_code:
                    for label, (p, binding, saved) in pending.items():
                        if local.get('job') is p['same_job']:
                            expiry_times.append((label, hass.loop.time()))
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
                                or getattr(getattr(actor, 'observer', None), 'hass', None) is hass
                                or getattr(getattr(actor, 'root', None), 'hass', None) is hass)):
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
                elif actual is render_codes[0] and (observed_root(local.get('value'))
                        or observed_leaf(local.get('value'))):
                    renders.append((local['value'], dict(local['variables']), value))
                elif actual is render_codes[1] and observed_leaf(local.get('self')):
                    template_calls.append((local['self'], dict(local['variables']), value))
                elif actual is render_codes[2] and local.get('self') is cv.positive_time_period:
                    caller = frame.f_back
                    owner = caller.f_locals.get('time_delta')
                    binding = caller.f_locals.get('binding')
                    if binding is not None and binding.hass is hass:
                        owner = binding.config['for']
                    if observed_root(owner):
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
                row = location_image(local['self'], local['binding'], local['handle'], caller)
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

        def assert_configured_items(actual, saved):
            self.assertEqual(actual, saved)
            for (key, value), (saved_key, saved_value) in zip(actual, saved, strict=True):
                self.assertIs(key, saved_key)
                self.assertIs(value, saved_value)

        def assert_native_render(config, template_leaf, saved):
            root_render, = [row for row in renders if row[0] is config]
            leaf_render, = [row for row in renders if row[0] is template_leaf]
            template_call, = [row for row in template_calls if row[0] is template_leaf]
            rendered = root_render[2]
            self.assertIs(type(rendered), dict)
            self.assertEqual(tuple(rendered), ('seconds', 'milliseconds'))
            self.assertEqual(rendered['seconds'], 30)
            self.assertIs(rendered['milliseconds'], config['milliseconds'])
            self.assertEqual(leaf_render[2], 30)
            self.assertEqual(template_call[2], 30)
            self.assertIs(rendered['seconds'], leaf_render[2])
            self.assertIs(leaf_render[2], template_call[2])
            for row in (root_render, leaf_render, template_call):
                self.assertIs(row[1]['trigger']['from_state'], saved[10])
                self.assertIs(row[1]['trigger']['to_state'], saved[11])
            self.assertTrue(any(value is rendered and result is saved[14]
                                for value, result in validations),
                            'native CV must receive the exact rendered dict and return the retained period')

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
            self.assertIs(cell(before.listener, 'time_delta').cell_contents, configured)
            self.assertIs(before.listener_cells['time_delta'], configured)
            assert_configured_items(tuple(configured.items()), configured_items)
            assert_native_render(configured, leaf, retained)
            self.assertEqual((retained[10].state, retained[11].state), ('16', '17'))
            self.assertIs(retained[11], physical)
            hass.states.async_set('sensor.duration_control', '16')
            hass.states.async_set('sensor.duration_control', '17')
            await hass.async_block_till_done()
            control_binding = cap.capture(control)
            control_pending, = cap.pending(control_binding)[0]
            control_image = image(control_pending, control_binding)
            self.assertEqual(control_image[14].total_seconds(), 30)
            self.assertIs(cell(control_binding.listener, 'time_delta').cell_contents, control_configured)
            self.assertIs(control_binding.listener_cells['time_delta'], control_configured)
            assert_configured_items(tuple(control_configured.items()), control_items)
            assert_native_render(control_configured, control_leaf, control_image)
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
            root_installs.clear()
            # Pause only after the authentic last awaited preparation has
            # completed. Yield to the real loop until its unmodified timer is
            # naturally promoted; this task's continuation is already ready
            # ahead of H. The actual Root _prepare then resumes synchronously,
            # executing its original owning _scope before any _install/effect.
            # No clock, job, Handle, queue or product guard is changed.
            chain_type = self.module('native_registry_receipts').RegistryReceiptChain
            original_start = chain_type.__dict__['start'].__func__

            async def start_at_native_ready(cls, *args, **kwargs):
                result = await original_start(cls, *args, **kwargs)
                self.assertEqual(len(roots), 1, 'the actual Root _prepare must have entered once')
                owner, = roots
                self.assertIs(type(owner), root_type)
                self.assertIs(owner.hass, hass)
                self.assertIs(owner.runtime, runtime)
                self.assertIs(owner.task, asyncio.current_task())
                self.assertEqual(len(args), 5)
                self.assertFalse(kwargs)
                self.assertIs(args[0], owner.receipt_store)
                self.assertEqual(args[1], owner.operation_id)
                self.assertIs(args[2], owner.plan)
                self.assertIs(args[3], owner.receipt)
                self.assertIs(args[4], owner.digests)
                self.assertIs(result.store, owner.receipt_store)
                self.assertIs(result.plan, owner.plan)
                self.assertIs(result.original_receipt, owner.receipt)
                self.assertEqual(result.operation_id, owner.operation_id)
                scope = owner._scope
                self.assertIs(type(scope), FunctionType)
                yaml_server = self.module('native_template_yaml_server')
                open_code = yaml_server.NativeTemplateYAMLServer._open.__func__.__code__
                scope_codes = tuple(value for value in open_code.co_consts if type(value) is CodeType
                    and value.co_qualname == 'NativeTemplateYAMLServer._open.<locals>.scope')
                self.assertEqual(len(scope_codes), 1)
                self.assertIs(scope.__code__, scope_codes[0])
                self.assertIs(scope.__globals__, vars(yaml_server))
                closure = dict(zip(scope.__code__.co_freevars,
                    (value.cell_contents for value in scope.__closure__ or ()), strict=True))
                self.assertIs(closure['obj'], owner)
                self.assertIs(closure['hass'], hass)
                self.assertIs(closure['runtime'], runtime)
                self.assertIs(closure['guard'], owner.guard)
                self.assertEqual(membership(retained[4]), (1, 0),
                                 'native timer expired before final preparation')
                remaining = retained[5] - hass.loop.time()
                if remaining > 1:
                    await asyncio.sleep(remaining - 1)
                while membership(retained[4]) == (1, 0):
                    await asyncio.sleep(0)
                ready = membership(retained[4])
                self.assertEqual(ready, (0, 1), 'native H must be ready, not run')
                gate.append({'owner': owner, 'chain': result, 'task': asyncio.current_task(),
                             'count': ready, 'observed_at': hass.loop.time()})
                timeline.append(('gate', ready))
                return result

            error = None
            try:
                with patch.object(chain_type, 'start', classmethod(start_at_native_ready)):
                    await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            except ValueError as caught:
                error = caught
            self.assertEqual(len(gate), 1)
            self.assertEqual(gate[0]['count'], (0, 1))
            self.assertIsNotNone(error, 'must refuse; an applied/unknown return is not this proof')
            self.assertIs(type(error), ValueError)
            self.assertEqual(str(error), reason)
            self.assertEqual(len(roots), 1, 'must enter the real NativeTemplateStateServer _prepare once')
            self.assertTrue(all(type(bound) is root_type for bound in roots))
            self.assertIs(gate[0]['owner'], roots[0])
            self.assertEqual(root_installs, [], 'preinstall refusal must precede the real _install')
            ready_entries = [row for row in entries if row['count'] == (0, 1) and row['post_start']]
            ready_raises = [row for row in raised if row['count'] == (0, 1) and row['post_start']]
            self.assertTrue(ready_entries, 'UNPROVED TIMING: exact native H never reached preinstall ready')
            self.assertTrue(ready_raises, 'generic duration error does not prove ready refusal')
            self.assertEqual(len(ready_entries), 1)
            self.assertEqual(len(ready_raises), 1)
            self.assertTrue(any(row['exception'] is error for row in ready_raises),
                            'the observed original ready refusal must propagate to the API caller')
            for row in ready_entries + ready_raises:
                self.assertIs(row['handle'], retained[4])
                self.assertTrue(row['graphs'], 'must belong to the actual Root graph capability')
                self.assertTrue(row['owners'], 'the exact capability must be owned by this actual Root')
                self.assertEqual(row['owners'], (roots[0],))
                self.assertIs(row['gate_owner'], roots[0])
                self.assertEqual(row['owning_stack'], (True, True),
                                 'actual owning _scope and its Root _prepare must both be on the stack')
                self.assertTrue(row['post_start'], 'this exact returned chain must already be bound')
                self.assertTrue(row['owner_task'], 'scope and chain must run in the same owning task')
                self.assertTrue(row['preinstall'], 'no install or binding may precede this refusal')
                self.assertEqual(row['live'], (False,) * len(row['graphs']))
                self.assertEqual(row['records'], ())
                self.assertIsNone(row['witness'])
                self.assertEqual(row['keys'], (old,))
                self.assertIs(row['configured'], configured)
                assert_configured_items(row['configured_items'], configured_items)
                self.assertIs(row['listener'], configured)
                self.assertEqual(row['effect_counts'], (0,) * len(effects))
                assert_image(row['history'], retained)
            # Root may await during refusal unwinding: native F may already have
            # run by this return. Order is attested below from actual entries.
            await asyncio.sleep(max(0, max(retained[5], control_image[5]) - hass.loop.time()) + 0.05)
            await hass.async_block_till_done()
            self.assertFalse(runtime.operation_lock.locked())
            self.assertEqual(root_installs, [])
            self.assertEqual(effects, {key: [] for key in effects})
            self.assertEqual((len(actions), len(scripts), len(controls), len(slow)), (1, 1, 1, 0))
            self.assertEqual(attachments, [old, 'sensor.duration_control'])
            first_refusal = timeline.index(('raise', (0, 1)))
            self.assertLess(timeline.index(('gate', (0, 1))), timeline.index(('location', (0, 1))))
            self.assertLess(timeline.index(('location', (0, 1))), first_refusal)
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
            self.assertIs(current.listener_cells['time_delta'], configured)
            assert_configured_items(tuple(configured.items()), configured_items)
            self.assertEqual(configured_graph(configured), original_configured_graph)
            self.assertEqual(configured_module._template_pin(leaf), original_leaf_pin)
            current_control = cap.capture(control)
            self.assertIs(current_control.config['for'], control_configured)
            self.assertIs(cell(current_control.listener, 'time_delta').cell_contents, control_configured)
            self.assertIs(current_control.listener_cells['time_delta'], control_configured)
            self.assertIs(current_control.listener, control_binding.listener)
            self.assertIs(current_control.keyed_job, control_binding.keyed_job)
            self.assertIs(current_control.keyed_job.target, control_binding.listener)
            self.assertEqual(current_control.original_keys, ['sensor.duration_control'])
            assert_configured_items(tuple(control_configured.items()), control_items)
            self.assertEqual(configured_graph(control_configured), original_control_graph)
            self.assertEqual(configured_module._template_pin(control_leaf), original_control_leaf_pin)
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
            # Diagnostic only: all times are reads from the existing loop and
            # actual Handle. Keep preinstall and live-route samples distinct.
            if retained is not None:
                deadline = retained[5]
                preinstall = [row['observed_at'] for row in entries
                              if row['post_start'] and row['owners'] and row['live']
                              and not any(row['live'])]
                active = [row['observed_at'] for row in entries
                          if row['post_start'] and row['owners'] and any(row['live'])]
                def relative_span(values):
                    return None if not values else (len(values),
                        round(min(values) - deadline, 6), round(max(values) - deadline, 6))
                print('NATIVE_PREINSTALL_READY_TIMING', {
                    'root_prepare_vs_deadline': [round(at - deadline, 6) for at in root_times],
                    'gate_vs_deadline': [round(row['observed_at'] - deadline, 6) for row in gate],
                    'preinstall_span_vs_deadline': relative_span(preinstall),
                    'active_span_vs_deadline': relative_span(active),
                    'expiry_vs_source_deadline': [(label, round(at - deadline, 6))
                                                  for label, at in expiry_times],
                })
            print('NATIVE_PREINSTALL_READY_REFUSAL', {
                'entry_counts': [row['count'] for row in entries],
                'raise_counts': [row['count'] for row in raised],
                'post_start_entries': [(row['count'], row['owning_stack'], row['owner_task'], row['preinstall'])
                                       for row in entries if row['post_start']],
                'post_start_raises': [(row['count'], row['owning_stack'], row['owner_task'], row['preinstall'])
                                      for row in raised if row['post_start']],
                'prepare_roots': len(roots), 'root_installs': len(root_installs),
                'gate': [(id(row['owner']), id(row['chain']), id(row['task']), row['count']) for row in gate],
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
