"""SOURCE-ONLY / UNEXECUTED: four actual-Root structured-duration cases.

Reuse the real producer, Runtime, fresh Root, checker and native listener. One
read-only fixture monitoring slot observes native render/CV inputs and returns.
No direct render/CV invocation, synthetic State/Event, Handle/clock edit or Core
patch. Current post-50 proof comes from 55 real native listener start/cancel
cycles after both Roots retired and the retained OWN naturally expired.
"""
import asyncio
import sys
import unittest
from types import FunctionType
import test_native_automation_duration_prefix_root as literal
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationStructuredDurationRootTests(unittest.IsolatedAsyncioTestCase):
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
            self.f.hass, native, True, True)

    async def test_template_dict_trigger_conditional_meaningful_own_inverse_expiry_native_future_55(self):
        await self._structured_case(configured_dict=False, source_bearing=False, meaningful=True)

    async def test_template_dict_source_17_to_29_own_inverse_expiry_native_future_55(self):
        await self._structured_case(configured_dict=False, source_bearing=True, meaningful=True)

    async def test_configured_dict_trigger_conditional_meaningful_own_inverse_expiry_native_future_55(self):
        await self._structured_case(configured_dict=True, source_bearing=False, meaningful=True)

    async def test_configured_dict_source_17_to_29_own_inverse_expiry_native_future_55(self):
        await self._structured_case(configured_dict=True, source_bearing=True, meaningful=True)

    async def _structured_case(self, *, configured_dict, source_bearing, meaningful):
        from homeassistant.core import Context
        from homeassistant.helpers import template, config_validation as cv
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers import issue_registry as ir
        self.assertIs(meaningful, True)
        runtime, mapping, uuids, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        duration = 629
        # Native source roundtrip measured 326.594 s. This is real headroom,
        # not an accelerated or extended live Handle. Unit values are identical
        # in the independent control, including the unchanged static sibling.
        def expression(entity_id):
            if source_bearing:
                return "600 + states('" + entity_id + "')|int"
            return "617 if trigger.to_state.state == '17' else 629"

        def body_for(entity_id):
            expr = expression(entity_id)
            if configured_dict:
                return '{{ ' + expr + ' }}'
            return "{{ {'seconds': " + expr + ", 'milliseconds': 0} }}"

        body, target_body = body_for(old), body_for(new)
        control_body = body_for('sensor.duration_control')
        def duration_yaml(body):
            if configured_dict:
                return '    for:\n      seconds: "' + body + '"\n      milliseconds: 0\n'
            return '    for: "' + body + '"\n'

        filters = '    from: "17"\n    to: "29"\n'
        (root / 'mixed_automations.yaml').write_text(
            '- id: mixed_source\n  alias: Structured native duration source\n  initial_state: true\n'
            '  variables:\n    source: '+old+'\n    alias: "{{ source }}"\n'
            '  triggers:\n  - trigger: state\n    entity_id: '+old+'\n'+filters+
            duration_yaml(body)+
            '  actions:\n  - event: duration_source_result\n    event_data:\n'
            '      bound: "{{ trigger.entity_id }}"\n'
            '      previous: "{{ trigger.from_state.state }}"\n'
            '      current: "{{ trigger.to_state.state }}"\n'
            '      from_id: "{{ trigger.from_state.entity_id }}"\n'
            '      to_id: "{{ trigger.to_state.entity_id }}"\n'
            '      seconds: "{{ trigger.for.total_seconds() }}"\n'
            '      configured: "{{ states(alias) }}"\n'
            '  - action: script.mixed_source\n'
            '- id: duration_control\n  alias: Independent native structured control\n  initial_state: true\n'
            '  triggers:\n  - trigger: state\n    entity_id: sensor.duration_control\n'+filters+
            duration_yaml(control_body)+
            '  actions:\n  - event: duration_control_result\n    event_data:\n'
            '      previous: "{{ trigger.from_state.state }}"\n'
            '      current: "{{ trigger.to_state.state }}"\n'
            '      seconds: "{{ trigger.for.total_seconds() }}"\n')
        (root / 'mixed_scripts.yaml').write_text(
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
            original[name] = (root / name).read_bytes()
        entities = hass.data[DATA_INSTANCES]['automation'].entities
        source = next(e for e in entities if e.unique_id == 'mixed_source')
        control = next(e for e in entities if e.unique_id == 'duration_control')
        cap = await self._capability()
        dict_cap = cap.scalar.dict_native
        duration_module = self.module('native_automation_duration_binding')
        cells = duration_module._cells
        configured = source._trigger_config[0]['for']
        control_configured = control._trigger_config[0]['for']
        leaf = configured['seconds'] if configured_dict else configured
        control_leaf = control_configured['seconds'] if configured_dict else control_configured
        self.assertIs(type(leaf), template.Template)
        self.assertIs(type(control_leaf), template.Template)
        self.assertEqual(leaf.template, body)
        self.assertEqual(control_leaf.template, control_body)
        if configured_dict:
            self.assertIs(type(configured), dict)
            self.assertIs(type(control_configured), dict)
            static_sibling = configured['milliseconds']
            original_keys = tuple(configured.keys())
        graph = self.module('native_script_config_transport')._graph
        original_leaf_graph = graph(leaf)
        configured_module = self.module('native_automation_configured_duration')
        original_leaf_pin = configured_module._template_pin(leaf)
        actions, scripts, controls, pairs, roots, handoffs = [], [], [], [], [], []
        # Rows preserve original native return object identity. Only variables
        # get a shallow observation snapshot; rendered dicts are never copied.
        renders, template_calls, validations, listener_calls = [], [], [], []
        original_listener = None
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
        sys.monitoring.use_tool_id(tool, 'actual Root structured duration acceptance')
        physical = None

        def source_leaf(value):
            return (type(value) is template.Template and value is not control_leaf
                    and value.hass is hass and value.template in (body, target_body))

        def source_root(value):
            return value is configured if configured_dict else source_leaf(value)

        def control_root(value):
            return value is control_configured

        def classify(value):
            # Root first: configured dict recursion also emits a leaf row.
            if source_root(value):
                return 'source_root'
            if control_root(value):
                return 'control_root'
            if source_leaf(value):
                return 'source_leaf'
            if value is control_leaf:
                return 'control_leaf'
            return None

        def start(code, offset):
            frame = sys._getframe(1)
            actor = frame.f_locals.get('self')
            if original_listener is not None and code is original_listener.__code__:
                if (frame.f_locals.get('config') is source._trigger_config[0]
                        and frame.f_locals.get('hass') is hass):
                    listener_calls.append(frame.f_locals.get('event'))
                return
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

        def returned(code, offset, value):
            frame = sys._getframe(1)
            local = frame.f_locals
            if code is render_codes[0]:
                kind = classify(local.get('value'))
                if kind is not None:
                    renders.append((kind, local['value'], dict(local['variables']), value))
            elif code is render_codes[1]:
                observed = local.get('self')
                if source_leaf(observed) or observed is control_leaf:
                    template_calls.append((observed, dict(local['variables']), value))
            elif code is render_codes[2] and local.get('self') is cv.positive_time_period:
                caller = frame.f_back
                owner_config = caller.f_locals.get('time_delta')
                binding = caller.f_locals.get('binding')
                if binding is not None and binding.hass is hass:
                    owner_config = binding.config['for']
                kind = classify(owner_config)
                if kind in ('source_root', 'control_root'):
                    validations.append((kind, local.get('value', local.get('v')), value))

        def source_renders():
            return [row for row in renders if row[0] == 'source_root']

        def pending_image(pending, binding):
            history = pending['own'].history() if pending['own'] is not None else cells(pending['action'])
            return (pending['clear'], pending['cancel'], pending['keys'], pending['cancel_job'],
                    pending['handle'], pending['handle'].when(), pending['handle']._context,
                    pending['timer_job'], pending['same_job'], pending['action'],
                    history['from_s'], history['to_s'], history['event'], history['event'].context,
                    binding.listener_cells['period'][pending['key']])

        def same_pending(actual, expected):
            self.assertEqual(actual, expected)
            self.assertTrue(all(actual[i] is expected[i] for i in range(15) if i != 5))

        def native_result(row, period, expected):
            kind, _, variables, rendered = row
            self.assertIsInstance(rendered, dict)
            self.assertEqual(tuple(rendered), ('seconds', 'milliseconds'))
            self.assertEqual(rendered['seconds'], expected)
            self.assertEqual(rendered['milliseconds'], 0)
            if not configured_dict:
                self.assertIs(type(rendered), dict_cap.wrappers[0],
                              'one-Template dict result must be the actual native wrapper')
            else:
                self.assertIs(type(rendered), dict)
            self.assertTrue(any(k == kind and value is rendered and result is period
                                for k, value, result in validations),
                            'the original CV must receive this exact render result and return this period')
            self.assertEqual(period.total_seconds(), expected)
            return variables

        def configured_image(binding, expected_body):
            actual = binding.config['for']
            actual_leaf = actual['seconds'] if configured_dict else actual
            self.assertIs(type(actual_leaf), template.Template)
            self.assertEqual(actual_leaf.template, expected_body)
            self.assertIs(actual_leaf.hass, hass)
            self.assertIs(duration_module._cell(binding.listener, 'time_delta'), original_cell)
            self.assertIs(original_cell.cell_contents, actual)
            self.assertIs(binding.listener_cells['time_delta'], actual)
            if configured_dict:
                self.assertIs(actual, configured)
                self.assertEqual(tuple(actual), original_keys)
                self.assertTrue(all(a is b for a, b in zip(actual.keys(), original_keys, strict=True)))
                self.assertIs(actual['milliseconds'], static_sibling)
            self.assertEqual(graph(leaf), original_leaf_graph)
            self.assertEqual(configured_module._template_pin(leaf), original_leaf_pin)
            return actual, actual_leaf

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
        hot_before = hot_after = None
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, start)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            for code in render_codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
            await self.stimulus(15)
            await self.stimulus(17)
            await hass.async_block_till_done()
            physical = hass.states.get(old)
            self.assertEqual(physical.state, '17')
            before = cap.capture(source)
            original_listener = before.listener
            original_cell = duration_module._cell(original_listener, 'time_delta')
            sys.monitoring.set_local_events(tool, original_listener.__code__, sys.monitoring.events.PY_START)
            attach_vars = before.listener_cells['_variables']
            self.assertEqual(source_renders(), [])
            self.assertEqual(cap.pending(before)[0], ())
            hass.states.async_set('sensor.duration_control', '17')
            hass.states.async_set('sensor.duration_control', '29')
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
                forward_configured, forward_leaf = configured_image(current, target_body)
                self.assertIs(current.listener, original_listener)
                self.assertIs(cells(current.listener)['_variables'], attach_vars)
                self.assertEqual(current.original_keys, [new])
                self.assertEqual(pending['keys'], [new])
                own = pending['own']
                self.assertIs(type(own), duration_module._OwnDuration)
                self.assertIs(own.change, pairs[0])
                own_binding = own.binding
                frozen_origin = own_binding._duration_scalar_image
                own_owner = own_binding._duration_config_owner
                frozen_canonical = own_owner.canonical
                frozen_canonical_fields = (frozen_canonical.root_id, frozen_canonical.graph, frozen_canonical.normal)
                self.assertIs(own_owner.image.template, forward_configured)
                self.assertIs(own_binding.listener_cells['time_delta'], forward_configured)
                self.assertTrue(any(value is leaf for value, _ in own_owner.image.pins))
                if source_bearing:
                    self.assertIsNot(forward_leaf, leaf)
                    self.assertTrue(any(value is forward_leaf for value, _ in own_owner.image.pins))
                    if not configured_dict:
                        self.assertIsNot(forward_configured, configured)
                else:
                    # No source in the conditional: the actual duration config
                    # has no local operation and stays the same native object.
                    self.assertIs(forward_leaf, leaf)
                    self.assertIs(forward_configured, configured)
                retained = pending_image(pending, current)
                self.assertGreater(retained[5], hass.loop.time(), 'retain pending across actual Root timing')
                self.assertEqual((actions, scripts), ([], []))
                source_rows = source_renders()
                self.assertEqual(len(source_rows), 1)
                variables = native_result(source_rows[0], retained[14], duration)
                trigger = variables['trigger']
                self.assertEqual(set(trigger), {'platform', 'entity_id', 'from_state', 'to_state'})
                self.assertEqual(trigger['platform'], 'state')
                self.assertEqual(trigger['entity_id'], new)
                self.assertIs(trigger['from_state'], pairs[0].prior_state)
                self.assertIs(trigger['to_state'], pairs[0].new_state)
                self.assertEqual((trigger['from_state'].state, trigger['to_state'].state), ('17', '29'))
                self.assertEqual(set(variables) - {'trigger'}, set(attach_vars) - {'trigger'})
                for key in attach_vars:
                    if key != 'trigger':
                        self.assertIs(variables[key], attach_vars[key])
                self.assertNotIn('source', variables)
                self.assertNotIn('alias', variables)
                self.assertEqual(len([row for row in template_calls if source_leaf(row[0])]), 1)
                if configured_dict:
                    self.assertEqual(len([row for row in renders if row[0] == 'source_leaf']), 1)
                control_rows = [row for row in renders if row[0] == 'control_root']
                self.assertEqual(len(control_rows), 1)
                control_vars = native_result(control_rows[0], control_image[14], duration)
                self.assertEqual((control_vars['trigger']['from_state'].state,
                                  control_vars['trigger']['to_state'].state), ('17', '29'))
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
                same_pending(pending_image(restored, back), retained)
                self.assertEqual(restored['keys'], [old])
                inverse_configured, inverse_leaf = configured_image(back, body)
                self.assertIs(restored['own'], own)
                self.assertIs(own.binding, own_binding)
                self.assertIs(own_binding._duration_scalar_image, frozen_origin)
                self.assertIs(own_owner.canonical, frozen_canonical)
                self.assertEqual((frozen_canonical.root_id, frozen_canonical.graph, frozen_canonical.normal),
                                 frozen_canonical_fields)
                self.assertIs(own_owner.image.template, inverse_configured)
                self.assertIs(own_binding.listener_cells['time_delta'], inverse_configured)
                if source_bearing:
                    self.assertIsNot(inverse_leaf, leaf)
                    self.assertIsNot(inverse_leaf, forward_leaf)
                    self.assertTrue(all(any(value is expected for value, _ in own_owner.image.pins)
                                        for expected in (leaf, forward_leaf, inverse_leaf)))
                    if not configured_dict:
                        self.assertIsNot(inverse_configured, configured)
                        self.assertIsNot(inverse_configured, forward_configured)
                else:
                    self.assertIs(inverse_leaf, leaf)
                own.check()
                self.assertEqual(back.original_keys, [old])
                self.assertIs(back.listener, original_listener)
                self.assertIs(back.keyed_job.target, original_listener)
                for bound in roots:
                    self.assertIs(bound.consumers.phases.graph.live, False)
                    self.assertEqual(bound.consumers.phases.graph.routes, [])
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
                for name, content in original.items():
                    self.assertEqual((root / name).read_bytes(), content)
                current_control, = cap.pending(cap.capture(control))[0]
                same_pending(pending_image(current_control, control_binding), control_image)
                self.assertGreater(restored['handle'].when(), hass.loop.time())
                deadline = max(restored['handle'].when(), current_control['handle'].when())
                await asyncio.sleep(max(0, deadline - hass.loop.time()) + 0.05)
                await hass.async_block_till_done()
                self.assertEqual((len(actions), len(scripts), len(controls)), (1, 1, 1))
                self.assertEqual(str(actions[0].data['bound']), old)
                self.assertEqual(str(actions[0].data['previous']), '17')
                self.assertEqual(str(actions[0].data['current']), '29')
                self.assertEqual(str(actions[0].data['from_id']), old)
                self.assertEqual(str(actions[0].data['to_id']), new)
                self.assertEqual(float(actions[0].data['seconds']), duration)
                self.assertEqual(float(controls[0].data['seconds']), duration)
                self.assertEqual(str(actions[0].data['configured']), '29')
                self.assertEqual(str(scripts[0].data['value']), '29')
                self.assertEqual(actions[0].context.parent_id, retained[12].context.id)
                self.assertEqual(cap.pending(cap.capture(source))[0], ())
                self.assertEqual(cap.pending(cap.capture(control))[0], ())
                self.assertEqual((len(renders), len(template_calls), len(validations)), count)

                # Future handoff semantics: every input below comes from the
                # ORIGINAL producer's normal HA write, not the ER rename path.
                # Every matched 17->29 computes a native period; genuine 31
                # cancels it. No test call of render_complex/positive/CV.
                hot_before = dict_cap.schema._compiled
                if not dict_cap.minimum:
                    self.assertIs(type(hot_before), FunctionType)
                    self.assertEqual(hot_before.__qualname__, 'Schema._auto_counter.<locals>.validate')
                    auto_before = cells(hot_before)
                    self.assertIs(auto_before['self'], dict_cap.schema)
                    self.assertGreater(auto_before['calls'], 0)
                    self.assertLess(auto_before['calls'], 50)
                future_handles, future_periods, future_events = [], [], []
                for index in range(55):
                    before_renders = len(source_renders())
                    before_templates = len([row for row in template_calls if source_leaf(row[0])])
                    before_validations = len([row for row in validations if row[0] == 'source_root'])
                    before_listener_calls = len(listener_calls)
                    await self.stimulus(17)
                    prior = hass.states.get(old)
                    self.assertEqual(prior.state, '17')
                    self.assertEqual(cap.pending(cap.capture(source))[0], ())
                    await self.stimulus(29)
                    current_state = hass.states.get(old)
                    future_binding = cap.capture(source)
                    self.assertIs(future_binding.listener, original_listener)
                    self.assertIs(future_binding.keyed_job.target, original_listener)
                    self.assertIs(future_binding.config['for'], inverse_configured)
                    self.assertIs(future_binding.listener_cells['time_delta'], inverse_configured)
                    future, = cap.pending(future_binding)[0]
                    self.assertIsNone(future['own'])
                    self.assertEqual(future['keys'], [old])
                    history = cells(future['action'])
                    self.assertIs(history['from_s'], prior)
                    self.assertIs(history['to_s'], current_state)
                    self.assertIs(history['event'].data['old_state'], prior)
                    self.assertIs(history['event'].data['new_state'], current_state)
                    self.assertIsNot(history['event'], retained[12])
                    self.assertTrue(any(event is history['event']
                                        for event in listener_calls[before_listener_calls:]))
                    self.assertTrue(all(history['event'] is not previous for previous in future_events))
                    future_events.append(history['event'])
                    self.assertTrue(all(future['handle'] is not previous for previous in future_handles))
                    future_handles.append(future['handle'])
                    period = future_binding.listener_cells['period'][old]
                    self.assertTrue(all(period is not previous for previous in future_periods))
                    future_periods.append(period)
                    self.assertEqual(len(source_renders()), before_renders + 1)
                    self.assertEqual(len([row for row in template_calls if source_leaf(row[0])]), before_templates + 1)
                    self.assertEqual(len([row for row in validations if row[0] == 'source_root']), before_validations + 1)
                    native_vars = native_result(source_renders()[-1], period, duration)
                    self.assertIs(native_vars['trigger']['from_state'], prior)
                    self.assertIs(native_vars['trigger']['to_state'], current_state)
                    self.assertEqual(native_vars['trigger']['entity_id'], old)
                    if configured_dict:
                        leaf_rows = [row for row in renders if row[0] == 'source_leaf']
                        self.assertEqual(len(leaf_rows), index + 2)
                        self.assertIs(leaf_rows[-1][1], inverse_leaf)
                        self.assertEqual(leaf_rows[-1][3], duration)
                    self.assertFalse(future['handle'].cancelled())
                    self.assertGreater(future['handle'].when(), hass.loop.time())
                    await self.stimulus(31)
                    await hass.async_block_till_done()
                    self.assertTrue(future['handle'].cancelled())
                    after_cancel = cap.capture(source)
                    self.assertEqual(cap.pending(after_cancel)[0], ())
                    # Core retains terminal period/clear map slots after
                    # cancellation. Its real clear_listener closure has both
                    # remover cells set to None; absence is not the oracle.
                    self.assertIs(after_cancel.listener_cells['unsub_track_same'][old], future['clear'])
                    self.assertIs(after_cancel.listener_cells['period'][old], period)
                    cancelled_cells = cells(future['clear'])
                    self.assertEqual(set(cancelled_cells),
                                     {'async_remove_state_for_cancel', 'async_remove_state_for_listener'})
                    self.assertTrue(all(value is None for value in cancelled_cells.values()))
                    self.assertNotIn(future['cancel_job'], after_cancel.callbacks[old])
                    # This native listener code has actual source config/hass
                    # closure locals; three real producer events hit it.
                    self.assertEqual(len(listener_calls), before_listener_calls + 3)
                    self.assertEqual(len(source_renders()), before_renders + 1)
                    self.assertEqual((len(actions), len(scripts), len(controls)), (1, 1, 1))
                    self.assertEqual(len(roots), 2)
                hot_after = dict_cap.schema._compiled
                # Source-authenticated sibling helper checks the actual current
                # state. The fixture deliberately implements no codegen proof.
                dict_cap.check()
                if not dict_cap.minimum:
                    self.assertIsNot(hot_after, hot_before)
                    self.assertIs(type(hot_after), FunctionType)
                    self.assertEqual(hot_after.__code__.co_filename, '<probatio-generated>')
                    self.assertNotEqual(hot_after.__qualname__, 'Schema._auto_counter.<locals>.validate')
                self.assertEqual(len(future_handles), 55)
                self.assertTrue(all(handle.cancelled() for handle in future_handles))
                self.assertEqual(len(source_renders()), 56)
                self.assertEqual(len([row for row in validations if row[0] == 'source_root']), 56)
                self.assertEqual((len(actions), len(scripts), len(controls)), (1, 1, 1))
                self.assertEqual(graph(leaf), original_leaf_graph)
                self.assertEqual(configured_module._template_pin(leaf), original_leaf_pin)
                own_owner.check()
                self.assertIs(own_binding._duration_scalar_image, frozen_origin)
                self.assertIs(own_owner.canonical, frozen_canonical)
                self.assertEqual((frozen_canonical.root_id, frozen_canonical.graph, frozen_canonical.normal),
                                 frozen_canonical_fields)
        finally:
            print('NATIVE_STRUCTURED_DURATION_TIMING', {
                'configured_dict': configured_dict,
                'source_bearing': source_bearing,
                'meaningful': meaningful,
                'now': hass.loop.time(),
                'retained_deadline': None if retained is None else retained[5],
                'control_deadline': None if control_image is None else control_image[5],
                'actions': (len(actions), len(scripts), len(controls)),
                'roots': len(roots),
                'source_root_renders': len(source_renders()),
                'future_listener_calls': len(listener_calls),
                'dict_cv_before_future': None if hot_before is None else type(hot_before).__name__,
                'dict_cv_after_future': None if hot_after is None else type(hot_after).__name__,
                'dict_cv_after_filename': getattr(getattr(hot_after, '__code__', None), 'co_filename', None),
            })
            observed_codes = (*codes, *render_codes)
            if original_listener is not None:
                observed_codes += (original_listener.__code__,)
            for code in observed_codes:
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
