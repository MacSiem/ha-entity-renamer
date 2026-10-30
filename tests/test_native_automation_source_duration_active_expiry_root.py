"""Owner-run actual Root source-bearing pending native expiry.

Built against repo HEAD 42177e9eb9d65f12f0f5e89522912178f3a6a134.
Reuse existing setup; one positive case, one read-only monitoring slot. The only
monitor-side fixture stimulus is the accepted producer value at native handoff.
No fake State/Event, renderer/validator invocation, clock or Handle mutation,
callback invocation, product method replacement, or substitute verifier.
"""
import sys
import unittest

import test_native_automation_source_duration_root as source_fixture
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA,
                     'actual pinned HA required')
class NativeAutomationSourceDurationActiveExpiryRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_source_pending_natural_expiry_inside_active_root_then_terminal_fresh_inverse(self):
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
        pairs, roots, handoffs, expiries, expiry_returns, terminal_records = [], [], [], [], [], []
        renders, template_calls, validations = [], [], []
        timer_attachments = []
        for name, events in (('duration_source_result', actions), ('duration_script_result', scripts),
                             ('duration_control_result', controls), ('duration_slow_result', slow)):
            self.addCleanup(hass.bus.async_listen(name, events.append))

        codes = (
            self.module('native_source_handoff').NativeSourceHandoff.mutate.__code__,
            self.module('native_automation_owned_graph').OwnedAutomationGraph._action.__code__,
            self.module('native_template_state_server').NativeTemplateStateServer._install.__code__,
            self.module('native_automation_duration_expiry').NativeDurationExpiry.detach.__code__,
            cap.native.event.async_track_same_state.__code__,
        )
        render_codes = (template.render_complex.__code__, template.Template.async_render.__code__,
                        type(cv.positive_time_period).__call__.__code__)
        physical = native_pending = before = retained = None
        expiry_code = None

        def source_template(value):
            return (type(value) is template.Template and value.hass is hass
                    and value.template in (body, target_body))

        def observed_template(value):
            return source_template(value) or value is control_configured

        def source_rows(rows):
            return [row for row in rows if source_template(row[0])]

        def pending_image(p, binding):
            history = cells(p['action'])
            return (p['clear'], p['cancel'], p['keys'], p['cancel_job'], p['handle'],
                    p['handle'].when(), p['handle']._context, p['timer_job'], p['same_job'],
                    p['action'], history['from_s'], history['to_s'], history['event'],
                    history['event'].context, binding.listener_cells['period'][p['key']])

        def assert_image(p, binding):
            actual = pending_image(p, binding)
            self.assertEqual(actual, retained)
            for i in range(len(actual)):
                if i != 5:
                    self.assertIs(actual[i], retained[i])

        def start(code, offset):
            frame = sys._getframe(1)
            actor = frame.f_locals.get('self')
            if code is codes[0] and actor.hass is hass:
                handoffs.append(actor)
                if len(handoffs) == 1:
                    self.assertIs(hass.states.get(old), physical)
                    actor.source._attr_native_value = 29
            elif code is codes[1] and actor.hass is hass:
                change = frame.f_locals['change']
                pairs.append(change)
                self.assertIs(change.prior_state, physical)
                self.assertIsNone(change.event.data['old_state'])
                self.assertIs(change.new_state, change.event.data['new_state'])
            elif code is codes[2] and actor.hass is hass:
                roots.append(actor)
            elif code is codes[3] and actor.capability.hass is hass:
                # The normal product guard has finished F and is about to discard
                # its records. Observe before detach, without adding a verifier.
                record = actor.records.get(native_pending['timer_job'])
                if record is not None:
                    self.assertEqual(record.state, 'terminal')
                    self.assertIsNone(record.frame)
                    terminal_records.append(record)
            elif code is codes[4] and frame.f_locals.get('hass') is hass:
                entity_ids = frame.f_locals.get('entity_ids')
                if entity_ids in (old, new, 'sensor.duration_control'):
                    timer_attachments.append(entity_ids)
            elif code is expiry_code and frame.f_locals.get('job') is native_pending['same_job']:
                expiries.append(tuple(r.consumers.phases.graph.live for r in roots))
                live = [r for r in roots if r.consumers.phases.graph.live]
                self.assertEqual(len(live), 1)
                node = live[0].consumers.phases.active[0]
                self.assertIs(live[0].chain.active, node.intent)
                self.assertEqual(node.phase, 'registry_attempted')
                # Read only: configuration has moved, historical native timer has not.
                target = before.config['for']
                self.assertIsNot(target, configured)
                self.assertEqual(target.template, target_body)
                self.assertIs(cell(before.listener, 'time_delta').cell_contents, target)
                self.assertEqual(native_pending['keys'], [new])
                self.assertIs(cells(native_pending['action'])['time_delta'], target)
                self.assertIsNone(native_pending['own'])
                # p['key'] is the original immutable readback key; transport moves the live map.
                transported = dict(native_pending, key=new)
                assert_image(transported, before)
                self.assertEqual(len(source_rows(renders)), 1)

        def returned(code, offset, value):
            frame = sys._getframe(1)
            local = frame.f_locals
            if code is expiry_code and local.get('job') is native_pending['same_job']:
                expiry_returns.append(value)
            elif code is render_codes[0] and observed_template(local.get('value')):
                renders.append((local['value'], dict(local['variables']), value))
            elif code is render_codes[1] and observed_template(local.get('self')):
                template_calls.append((local['self'], dict(local['variables']), value))
            elif code is render_codes[2] and local.get('self') is cv.positive_time_period:
                caller = frame.f_back
                owner_config = caller.f_locals.get('time_delta')
                binding = caller.f_locals.get('binding')
                if binding is not None and binding.hass is hass:
                    owner_config = binding.config['for']
                if observed_template(owner_config):
                    validations.append((local.get('value', local.get('v')), value))

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        async def check_rows(bound, active):
            phase = bound.consumers.phases
            self.assertEqual(phase.observer.sample(bound.chain).prefix.active, frozenset(active))
            observer = self.module('yaml_runtime_observer').NativeYAMLRuntimeReadback(
                hass, owned_automation=phase.graph)
            normal = self.module('yaml_validation_worker')._normal
            for domain, rows in phase.program.rows(frozenset(active)):
                self.assertEqual(normal(observer._projection(domain)[0]), rows)

        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'actual Root source active expiry acceptance')
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, start)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            for code in render_codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
            await self.stimulus(16)
            await self.stimulus(17)
            await hass.async_block_till_done()
            physical = hass.states.get(old)
            self.assertEqual(physical.state, '17')
            before = cap.capture(source)
            native_pending, = cap.pending(before)[0]
            self.assertIsNone(native_pending['own'])
            retained = pending_image(native_pending, before)
            self.assertEqual(retained[14].total_seconds(), 30)
            expiry_code = native_pending['timer_job'].target.__code__
            sys.monitoring.set_local_events(tool, expiry_code,
                sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
            attach_vars = before.listener_cells['_variables']
            source_render, = source_rows(renders)
            self.assertIs(source_render[0], configured)
            self.assertEqual(source_render[2], 30)
            trigger = source_render[1]['trigger']
            self.assertEqual(set(trigger), {'platform', 'entity_id', 'from_state', 'to_state'})
            self.assertEqual((trigger['platform'], trigger['entity_id']), ('state', old))
            self.assertIs(trigger['from_state'], retained[10])
            self.assertIs(trigger['to_state'], retained[11])
            self.assertEqual(set(source_render[1]) - {'trigger'}, set(attach_vars) - {'trigger'})
            for key in attach_vars:
                if key != 'trigger':
                    self.assertIs(source_render[1][key], attach_vars[key])
            self.assertNotIn('source', source_render[1])
            self.assertNotIn('alias', source_render[1])
            self.assertEqual(len(source_rows(template_calls)), 1)
            self.assertTrue(any(value == 30 and result is retained[14] for value, result in validations))
            hass.states.async_set('sensor.duration_control', '16')
            hass.states.async_set('sensor.duration_control', '17')
            await hass.async_block_till_done()
            control_binding = cap.capture(control)
            control_pending, = cap.pending(control_binding)[0]
            control_image = pending_image(control_pending, control_binding)
            self.assertEqual(control_image[14].total_seconds(), 30)
            self.assertEqual(timer_attachments, [old, 'sensor.duration_control'])
            control_render, = [r for r in renders if r[0] is control_configured]
            self.assertEqual(control_render[2], 30)
            self.assertEqual((control_render[1]['trigger']['from_state'].state,
                              control_render[1]['trigger']['to_state'].state), ('16', '17'))
            with self.assertRaises(ValueError):
                self.module('yaml_runtime_observer')._automation_registrations(hass, source)
            await self._flush_documents()
            history = self.module('history_guard').NativeHistoryGuard(
                self.module('recorder_guard').NativeRecorderGuard(hass))
            original_history = await history.capture(mapping)
            counts = len(renders), len(template_calls), len(validations)
            result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
            self.assertIs(result['family_complete'], False)
            self.assertEqual(len(roots), 1)
            await check_rows(roots[0], {old})
            self.assertEqual(expiries, [(True,)])
            self.assertEqual(expiry_returns, [None])
            self.assertEqual((len(actions), len(scripts), len(controls), len(slow)), (1, 1, 1, 1))
            self.assertEqual((len(renders), len(template_calls), len(validations)), counts)
            self.assertEqual(timer_attachments, [old, 'sensor.duration_control'])
            current = cap.capture(source)
            forward_configured = current.config['for']
            self.assertEqual(forward_configured.template, target_body)
            self.assertIsNot(forward_configured, configured)
            self.assertIs(cell(current.listener, 'time_delta').cell_contents, forward_configured)
            self.assertIs(source._trigger_config[0]['for'], forward_configured)
            self.assertEqual(current.original_keys, [new])
            self.assertEqual(cap.pending(current)[0], ())
            record, = terminal_records
            self.assertEqual(record.state, 'terminal', 'normal product expiry guard must attest the native F')
            self.assertIs(record.pending['handle'], retained[4])
            self.assertEqual(retained[4].when(), retained[5])
            self.assertIs(retained[4]._context, retained[6])
            self.assertIs(control_pending['handle'], control_image[4])
            self.assertEqual(control_pending['handle'].when(), control_image[5])
            self.assertIs(control_pending['handle']._context, control_image[6])
            control_history = cells(control_pending['action'])
            for name, expected in zip(('from_s', 'to_s', 'event'), control_image[10:13]):
                self.assertIs(control_history[name], expected)
            self.assertEqual(cap.pending(cap.capture(control))[0], ())
            event = actions[0]
            self.assertEqual(tuple(str(event.data[k]) for k in
                ('bound', 'previous', 'current', 'from_id', 'to_id', 'configured')),
                (new, '16', '17', old, old, '29'))
            self.assertEqual(float(event.data['seconds']), 30)
            self.assertEqual(event.context.parent_id, retained[12].context.id)
            self.assertEqual(str(scripts[0].data['value']), '29')
            self.assertEqual(str(slow[0].data['value']), '29')
            self.assertEqual(tuple(str(controls[0].data[k]) for k in ('previous', 'current')), ('16', '17'))
            self.assertEqual(float(controls[0].data['seconds']), 30)
            self.assertEqual(controls[0].context.parent_id, control_image[12].context.id)
            self.assertEqual(len(pairs), 1)
            self.assertEqual((pairs[0].prior_state.state, pairs[0].new_state.state), ('17', '29'))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            self.assertIsNone(hass.states.get(old))

            reverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                                                        guard=lambda: None, refresh=refresh)
            self.assertEqual(reverse['state'], 'rolled_back')
            self.assertEqual(len(roots), 2)
            self.assertIsNot(roots[0], roots[1])
            self.assertIsNot(roots[0].chain, roots[1].chain)
            await check_rows(roots[1], set())
            back = cap.capture(source)
            self.assertEqual(back.original_keys, [old])
            self.assertEqual(cap.pending(back)[0], ())
            inverse_configured = back.config['for']
            self.assertIsNot(inverse_configured, configured)
            self.assertIsNot(inverse_configured, forward_configured)
            self.assertEqual(inverse_configured.template, body)
            self.assertIs(source._trigger_config[0]['for'], inverse_configured)
            self.assertIs(cell(back.listener, 'time_delta').cell_contents, inverse_configured)
            self.assertEqual(configured_graph(configured), original_configured_graph)
            self.assertEqual((len(renders), len(template_calls), len(validations)), counts,
                             'terminal fresh inverse must neither render nor validate again')
            self.assertEqual(expiries, [(True,)])
            self.assertEqual(expiry_returns, [None])
            self.assertEqual((len(actions), len(scripts), len(controls), len(slow)), (1, 1, 1, 1))
            self.assertEqual(record.state, 'terminal')
            self.assertEqual(terminal_records, [record])
            self.assertEqual(timer_attachments, [old, 'sensor.duration_control'],
                             'fresh inverse must not attach another duration timer')
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            for name, content in original.items():
                self.assertEqual((root / name).read_bytes(), content)
            self.assertTrue(await history.verify(original_history))
        finally:
            for code in (*codes, *render_codes, *((expiry_code,) if expiry_code is not None else ())):
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
