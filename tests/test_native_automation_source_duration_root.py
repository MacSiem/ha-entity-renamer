"""DRAFT NOT RUN: two source-bearing full Root cases for current and minimum.

Real producer/State/Event and independent native control. One fixture monitoring
slot also records native API inputs/results. Nothing calls a renderer to verify
an existing period, alters Handles/clocks, or invokes an expiry callback.
"""
import asyncio
import sys
import unittest
import test_native_automation_duration_prefix_root as literal
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationSourceDurationRootTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = literal.NativeAutomationDurationPrefixRootTests.asyncSetUp
    _before_start = literal.NativeAutomationDurationPrefixRootTests._before_start
    _flush_documents = literal.NativeAutomationDurationPrefixRootTests._flush_documents
    prepare = literal.NativeAutomationDurationPrefixRootTests.prepare
    emit = literal.NativeAutomationDurationPrefixRootTests.emit
    _runtime = literal.NativeAutomationDurationPrefixRootTests._runtime
    module = literal.NativeAutomationDurationPrefixRootTests.module
    stimulus = literal.NativeAutomationDurationPrefixRootTests.stimulus
    data = literal.NativeAutomationDurationPrefixRootTests.data
    async def _capability(self):
        native = await self.f.hass.async_add_executor_job(
            self.module('native_automation_state_binding')._Native, self.f.hass)
        return await self.f.hass.async_add_executor_job(
            self.module('native_automation_duration_binding').NativeAutomationDurationCapability,
            self.f.hass, native, True)

    async def test_source_scalar_pending_forward_fresh_inverse_natural_expiry_and_native_control(self):
        await self._scalar_case(meaningful=False)

    async def test_source_scalar_meaningful_17_to_29_retained_own_fresh_inverse_and_natural_expiry(self):
        await self._scalar_case(meaningful=True)

    async def _scalar_case(self, *, meaningful):
        from homeassistant.core import Context
        from homeassistant.helpers import template, config_validation as cv
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers import issue_registry as ir
        runtime, mapping, uuids, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        # Measured first full source-config forward/inverse: 326.594 s.
        # Retain the actual timer through BOTH directions with measured headroom;
        # active-expiry qualification remains a separate real natural path.
        duration = 449 if meaningful else 437
        first, last = ('17', '29') if meaningful else ('16', '17')
        body = "{{ 420 + states('" + old + "')|int }}"
        target_body = "{{ 420 + states('" + new + "')|int }}"
        control_body = "{{ 420 + states('sensor.duration_control')|int }}"
        filters = '    from: "'+first+'"\n    to: "'+last+'"\n'
        path = root / 'mixed_automations.yaml'
        path.write_text(
            '- id: mixed_source\n  alias: Scalar native duration source\n  initial_state: true\n'
            '  variables:\n    source: '+old+'\n    alias: "{{ source }}"\n'
            '  triggers:\n  - trigger: state\n    entity_id: '+old+'\n'+filters+
            '    for: "'+body+'"\n'
            '  actions:\n  - event: duration_source_result\n    event_data:\n'
            '      bound: "{{ trigger.entity_id }}"\n'
            '      previous: "{{ trigger.from_state.state }}"\n'
            '      current: "{{ trigger.to_state.state }}"\n'
            '      from_id: "{{ trigger.from_state.entity_id }}"\n'
            '      to_id: "{{ trigger.to_state.entity_id }}"\n'
            '      seconds: "{{ trigger.for.total_seconds() }}"\n'
            '      configured: "{{ states(alias) }}"\n'
            '  - action: script.mixed_source\n'
            '- id: duration_control\n  alias: Independent native scalar control\n  initial_state: true\n'
            '  triggers:\n  - trigger: state\n    entity_id: sensor.duration_control\n'+filters+
            '    for: "'+control_body+'"\n'
            '  actions:\n  - event: duration_control_result\n    event_data:\n'
            '      previous: "{{ trigger.from_state.state }}"\n'
            '      current: "{{ trigger.to_state.state }}"\n'
            '      seconds: "{{ trigger.for.total_seconds() }}"\n')
        (root/'mixed_scripts.yaml').write_text(
            'mixed_source:\n  variables:\n    source: '+old+'\n  sequence:\n'
            '  - event: duration_script_result\n    event_data:\n      value: "{{ states(source) }}"\n')
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
            original[name] = (root/name).read_bytes()
        entities = hass.data[DATA_INSTANCES]['automation'].entities
        source = next(e for e in entities if e.unique_id == 'mixed_source')
        control = next(e for e in entities if e.unique_id == 'duration_control')
        cap = await self._capability()
        cells = self.module('native_automation_duration_binding')._cells
        configured = source._trigger_config[0]['for']
        control_configured = control._trigger_config[0]['for']
        graph = self.module('native_script_config_transport')._graph
        original_configured_graph = graph(configured)
        def source_template(value):
            return (type(value) is template.Template and value.hass is hass
                    and value.template in (body, target_body))
        def observed_template(value):
            return source_template(value) or value is control_configured
        actions, scripts, controls, pairs, roots, handoffs = [], [], [], [], [], []
        renders, validations, template_calls = [], [], []
        for name, events in (('duration_source_result', actions), ('duration_script_result', scripts),
                             ('duration_control_result', controls)):
            self.addCleanup(hass.bus.async_listen(name, events.append))
        handoff_type = self.module('native_source_handoff').NativeSourceHandoff
        graph_type = self.module('native_automation_owned_graph').OwnedAutomationGraph
        root_type = self.module('native_template_state_server').NativeTemplateStateServer
        codes = (handoff_type.mutate.__code__, graph_type._action.__code__, root_type._install.__code__)
        render_codes = (template.render_complex.__code__, template.Template.async_render.__code__,
                        type(cv.positive_time_period).__call__.__code__)
        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'actual Root scalar for acceptance')
        physical = None

        def start(code, offset):
            frame = sys._getframe(1)
            actor = frame.f_locals.get('self')
            if code is codes[0] and actor.hass is hass:
                handoffs.append(actor)
                if len(handoffs) == 1 and meaningful:
                    self.assertIs(hass.states.get(old), physical)
                    # Genuine producer value at the authorized native handoff,
                    # as in the existing actual-Root meaningful-pair fixture.
                    actor.source._attr_native_value = 29
            elif code is codes[1] and actor.hass is hass:
                change = frame.f_locals['change']
                pairs.append(change)
                self.assertIs(change.prior_state, physical)
                self.assertIsNone(change.event.data['old_state'])
                self.assertIs(change.new_state, change.event.data['new_state'])
            elif code is codes[2] and actor.hass is hass:
                roots.append(actor)

        def returned(code, offset, value):
            frame = sys._getframe(1)
            local = frame.f_locals
            if code is render_codes[0] and observed_template(local.get('value')):
                renders.append((local['value'], dict(local['variables']), value))
            elif code is render_codes[1] and observed_template(local.get('self')):
                template_calls.append((local['self'], dict(local['variables']), value))
            elif code is render_codes[2] and local.get('self') is cv.positive_time_period:
                # Capture the actual native callable-object result and its real
                # input. Do not call either validator or renderer from monitor.
                caller = frame.f_back
                owner_config = caller.f_locals.get('time_delta')
                binding = caller.f_locals.get('binding')
                if binding is not None and binding.hass is hass:
                    owner_config = binding.config['for']
                if observed_template(owner_config):
                    validations.append((local.get('value', local.get('v')), value))

        def source_renders():
            return [row for row in renders if source_template(row[0])]

        def pending_image(pending, binding):
            history = pending['own'].history() if pending['own'] is not None else cells(pending['action'])
            return (pending['clear'], pending['cancel'], pending['keys'], pending['cancel_job'],
                    pending['handle'], pending['handle'].when(), pending['handle']._context,
                    pending['timer_job'], pending['same_job'], pending['action'],
                    history['from_s'], history['to_s'], history['event'], history['event'].context,
                    binding.listener_cells['period'][pending['key']])

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        async def check_rows(bound, active):
            phase = bound.consumers.phases
            sample = phase.observer.sample(bound.chain)
            self.assertEqual(sample.prefix.active, frozenset(active))
            observer = self.module('yaml_runtime_observer').NativeYAMLRuntimeReadback(hass, owned_automation=phase.graph)
            normal = self.module('yaml_validation_worker')._normal
            for domain, rows in phase.program.rows(frozenset(active)):
                self.assertEqual(normal(observer._projection(domain)[0]), rows)

        retained = control_image = None
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, start)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            for code in render_codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
            await self.stimulus(15 if meaningful else 16)
            await self.stimulus(17)
            await hass.async_block_till_done()
            physical = hass.states.get(old)
            before = cap.capture(source)
            attach_vars = before.listener_cells['_variables']
            self.assertEqual(len(source_renders()), 0 if meaningful else 1)
            saved = cap.pending(before)[0]
            self.assertEqual(len(saved), 0 if meaningful else 1)
            retained = pending_image(saved[0], before) if saved else None
            hass.states.async_set('sensor.duration_control', first)
            hass.states.async_set('sensor.duration_control', last)
            await hass.async_block_till_done()
            control_binding = cap.capture(control)
            control_pending, = cap.pending(control_binding)[0]
            control_image = pending_image(control_pending, control_binding)
            strict = self.module('yaml_runtime_observer')._automation_registrations
            with self.assertRaises(ValueError):
                strict(hass, source)
            await self._flush_documents()
            with mixed.native_diagnostics(self.module):
                result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                self.assertEqual(result['state'], 'applied')
                self.assertIs(result['family_complete'], False)
                self.assertEqual(len(roots), 1)
                await check_rows(roots[0], {old})
                current = cap.capture(source)
                pending, = cap.pending(current)[0]
                forward_configured = current.config['for']
                self.assertIsNot(forward_configured, configured)
                self.assertEqual(forward_configured.template, target_body)
                self.assertEqual(graph(configured), original_configured_graph)
                self.assertIs(cells(current.listener)['_variables'], attach_vars)
                self.assertIs(self.module('native_automation_duration_binding')._cell(current.listener, 'time_delta').cell_contents, forward_configured)
                self.assertEqual(current.original_keys, [new])
                self.assertEqual(pending['keys'], [new])
                actual = pending_image(pending, current)
                if meaningful:
                    own = pending['own']
                    self.assertIs(type(own), self.module('native_automation_duration_binding')._OwnDuration)
                    self.assertIs(own.change, pairs[0])
                    retained_own_binding = own.binding
                    retained_origin = retained_own_binding._duration_scalar_image
                    self.assertIs(retained_own_binding._duration_config_owner.image.template, forward_configured)
                    self.assertIs(retained_own_binding.listener_cells['time_delta'], forward_configured)
                    retained = actual
                else:
                    self.assertEqual(actual, retained)
                    self.assertTrue(all(actual[i] is retained[i] for i in range(15) if i != 5))
                self.assertEqual((actions, scripts), ([], []))
                source_rows = source_renders()
                self.assertEqual(len(source_rows), 1)
                _, variables, rendered = source_rows[0]
                self.assertEqual(rendered, duration)
                trigger = variables['trigger']
                self.assertEqual(set(trigger), {'platform', 'entity_id', 'from_state', 'to_state'})
                self.assertEqual(trigger['platform'], 'state')
                self.assertIs(trigger['from_state'], pairs[0].prior_state if meaningful else retained[10])
                self.assertIs(trigger['to_state'], pairs[0].new_state if meaningful else retained[11])
                self.assertEqual(trigger['entity_id'], new if meaningful else old)
                self.assertEqual(set(variables) - {'trigger'}, set(attach_vars) - {'trigger'})
                for key in attach_vars:
                    if key != 'trigger':
                        self.assertIs(variables[key], attach_vars[key])
                self.assertNotIn('source', variables)
                self.assertNotIn('alias', variables)
                self.assertEqual(len([row for row in template_calls if source_template(row[0])]), 1)
                self.assertTrue(any(value == duration and result is retained[14]
                                    for value, result in validations),
                                'period must be the actual native CV return object')
                control_rows = [row for row in renders if row[0] is control_configured]
                self.assertEqual(len(control_rows), 1)
                self.assertEqual(control_rows[0][2], rendered)
                self.assertEqual((control_rows[0][1]['trigger']['from_state'].state,
                                  control_rows[0][1]['trigger']['to_state'].state), (first, last))
                count = len(renders), len(template_calls), len(validations)
                reverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                self.assertEqual(reverse['state'], 'rolled_back')
                self.assertEqual(len(roots), 2)
                self.assertIsNot(roots[0], roots[1])
                self.assertIsNot(roots[0].chain, roots[1].chain)
                await check_rows(roots[1], set())
                self.assertEqual((len(renders), len(template_calls), len(validations)), count,
                                 'fresh inverse must not recompute a retained period')
                back = cap.capture(source)
                restored, = cap.pending(back)[0]
                restored_image = pending_image(restored, back)
                self.assertEqual(restored_image, retained)
                self.assertTrue(all(restored_image[i] is retained[i] for i in range(15) if i != 5))
                self.assertEqual(restored['keys'], [old])
                inverse_configured = back.config['for']
                self.assertIsNot(inverse_configured, configured)
                self.assertIsNot(inverse_configured, forward_configured)
                self.assertEqual(inverse_configured.template, body)
                self.assertEqual(graph(configured), original_configured_graph)
                if meaningful:
                    self.assertIs(restored['own'], own)
                    self.assertIs(own.binding, retained_own_binding)
                    self.assertIs(own.binding._duration_scalar_image, retained_origin)
                    self.assertIs(own.binding._duration_config_owner.image.template, inverse_configured)
                    self.assertIs(own.binding.listener_cells['time_delta'], inverse_configured)
                    own.check()
                self.assertEqual(back.original_keys, [old])
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
                for name, content in original.items():
                    self.assertEqual((root/name).read_bytes(), content)
                current_control, = cap.pending(cap.capture(control))[0]
                self.assertEqual(pending_image(current_control, control_binding), control_image)
                deadline = max(restored['handle'].when(), current_control['handle'].when())
                await asyncio.sleep(max(0, deadline-hass.loop.time()) + 0.05)
                await hass.async_block_till_done()
                self.assertEqual((len(actions), len(scripts), len(controls)), (1, 1, 1))
                self.assertEqual(str(actions[0].data['bound']), old)
                self.assertEqual(str(actions[0].data['previous']), first)
                self.assertEqual(str(actions[0].data['current']), last)
                self.assertEqual(str(actions[0].data['from_id']), old)
                self.assertEqual(str(actions[0].data['to_id']), new if meaningful else old)
                self.assertEqual(float(actions[0].data['seconds']), duration)
                self.assertEqual(float(controls[0].data['seconds']), duration)
                self.assertEqual(str(actions[0].data['configured']), last)
                self.assertEqual(str(scripts[0].data['value']), last)
                self.assertEqual(actions[0].context.parent_id, retained[12].context.id)
                self.assertEqual(cap.pending(cap.capture(source))[0], ())
                # A future genuine producer transition must use the ORIGINAL
                # native listener/evaluator after both Roots have retired.
                self.assertIs(source._trigger_config[0]['for'], inverse_configured)
                await self.stimulus(int(first))
                await self.stimulus(int(last))
                await hass.async_block_till_done()
                future_binding = cap.capture(source)
                future, = cap.pending(future_binding)[0]
                self.assertIsNone(future['own'])
                self.assertEqual(len(source_renders()), 2)
                self.assertEqual(source_renders()[-1][2], duration)
                self.assertEqual(future_binding.listener_cells['period'][old].total_seconds(), duration)
                await self.stimulus(31)
                await hass.async_block_till_done()
                self.assertTrue(future['handle'].cancelled())
                self.assertEqual((len(actions), len(scripts), len(controls)), (1, 1, 1))
        finally:
            print('NATIVE_SOURCE_DURATION_TIMING', {
                'meaningful': meaningful,
                'now': hass.loop.time(),
                'retained_deadline': None if retained is None else retained[5],
                'control_deadline': None if control_image is None else control_image[5],
                'actions': (len(actions), len(scripts), len(controls)),
                'roots': len(roots),
            })
            for code in (*codes, *render_codes):
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
