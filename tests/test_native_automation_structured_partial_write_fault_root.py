"""SOURCE ONLY / NOT RUN: actual Root configured dict partial-journal faults.

Observe original methods with fixture-local sys.monitoring. Raise only after an
original completed write; the foreign variants alter only actual dict data.
One real native pending period is retained without waiting for its expiration.
"""
import sys
import unittest
import test_native_automation_duration_prefix_root as literal
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationStructuredPartialWriteFaultRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_completed_dict_root_write_before_holder_restores_exact_zero_api(self):
        await self._partial_fault('root')

    async def test_completed_holder_write_before_approval_restores_exact_zero_api(self):
        await self._partial_fault('holder')

    async def test_completed_approval_write_restores_exact_zero_api(self):
        await self._partial_fault('approval')

    async def test_foreign_untouched_dict_sibling_after_root_write_refuses_zero_undo(self):
        await self._partial_fault('root', foreign='sibling')

    async def test_foreign_dict_order_after_root_write_refuses_zero_undo(self):
        await self._partial_fault('root', foreign='order')

    async def _partial_fault(self, seam, *, foreign=None):
        from homeassistant.core import Context
        from homeassistant.helpers import issue_registry as ir, template
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, _, original = await self.data()
        hass, path = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        self.assertEqual(len(mapping), 1)
        body = "{{ 600 + states('" + old + "')|int }}"
        target_body = "{{ 600 + states('" + new + "')|int }}"
        (path / 'mixed_automations.yaml').write_text(
            '- id: mixed_source\n  alias: Structured partial write fault\n  initial_state: true\n'
            '  variables:\n    source: '+old+'\n  triggers:\n'
            '  - trigger: state\n    entity_id: '+old+'\n    from: "16"\n    to: "17"\n'
            '    for:\n      seconds: "'+body+'"\n      milliseconds: 0\n'
            '  actions:\n  - event: structured_partial_action\n'
            '    event_data:\n      value: "{{ states(source) }}"\n'
            '  - action: script.mixed_source\n')
        (path / 'mixed_scripts.yaml').write_text(
            'mixed_source:\n  variables:\n    source: '+old+'\n  sequence:\n'
            '  - event: structured_partial_script\n'
            '    event_data:\n      value: "{{ states(source) }}"\n')
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
        for name in ('mixed_automations.yaml', 'mixed_scripts.yaml'):
            original[name] = (path / name).read_bytes()
        source = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities
                      if e.unique_id == 'mixed_source')
        script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities
                      if e.unique_id == 'mixed_source')
        duration = self.module('native_automation_duration_binding')
        configured_module = self.module('native_automation_configured_duration')
        transport_type = self.module('native_automation_config_transport').NativeAutomationConfigTransport
        graph_type = self.module('native_automation_owned_graph').OwnedAutomationGraph
        root_type = self.module('native_template_state_server').NativeTemplateStateServer
        graph = self.module('native_script_config_transport')._graph
        log_type = duration._PreEffectLog
        cap = await self._capability()
        configured = source._trigger_config[0]['for']
        self.assertIs(type(configured), dict)
        leaf = configured['seconds']
        self.assertIs(type(leaf), template.Template)
        self.assertEqual(leaf.template, body)
        root_before, leaf_before = graph(configured), graph(leaf)
        items_before = tuple(configured.items())
        self.assertEqual(tuple(configured), ('seconds', 'milliseconds'))
        static = configured['milliseconds']
        await self.stimulus(16)
        await self.stimulus(17)
        await hass.async_block_till_done()
        physical = hass.states.get(old)
        self.assertEqual(physical.state, '17')
        binding = cap.capture(source)
        self.assertIs(binding.config['for'], configured)
        self.assertIs(binding.listener_cells['time_delta'], configured)
        pending, = cap.pending(binding)[0]
        self.assertIsNone(pending['own'])
        self.assertEqual(binding.listener_cells['period'][old].total_seconds(), 617)

        def pending_image():
            history = duration._cells(pending['action'])
            return (pending['clear'], pending['cancel'], pending['keys'], tuple(pending['keys']),
                    pending['cancel_job'], pending['cancel_job'].target, pending['handle'],
                    pending['handle'].when(), pending['handle']._context,
                    pending['timer_job'], pending['same_job'], pending['action'],
                    history['from_s'], history['to_s'], history['event'], history['event'].context,
                    binding.listener_cells['period'][old])

        native_pending = pending_image()
        callbacks = binding.callbacks
        callback_before = tuple((key, members, tuple(members)) for key, members in callbacks.items())

        def native_graphs():
            return (graph(source.raw_config), graph(source._trigger_config),
                    graph(source.action_script.sequence), graph(source.action_script.variables),
                    graph(script.raw_config), graph(script.script.sequence), graph(script.script.variables))

        native_before = native_graphs()
        await self._flush_documents()
        actions, scripts = [], []
        self.addCleanup(hass.bus.async_listen('structured_partial_action', actions.append))
        self.addCleanup(hass.bus.async_listen('structured_partial_script', scripts.append))
        codes = {
            'root': root_type._install.__code__, 'install': transport_type.install.__code__,
            'graph_plan': graph_type.plan_retire.__code__, 'graph_undo': graph_type.apply_retire.__code__,
            'log_plan': log_type.plan.__code__, 'log_undo': log_type.undo.__code__,
            'view': configured_module.ConfiguredRollbackView.__init__.__code__,
            'handoff': self.module('native_source_handoff').NativeSourceHandoff.mutate.__code__,
            'emission': self.module('native_rename_emission_classifier').NativeRenameEmissions.mutate.__code__,
            'registry': self.module('native_registry').NativeRegistryIO.mutate.__code__,
        }
        write_code = log_type.write.__code__
        roots, transfers, injected, root_writes, api = [], [], [], [], []
        graph_undos, log_undos, plan_returns, views, graph_plans, refusals, order = [], [], [], [], [], [], []

        class FixtureStructuredPartialWriteFault(RuntimeError):
            pass

        def slots(log):
            return tuple((key, log_type.token(kind, log_type.read(kind, owner)))
                         for key, (kind, owner, _) in log.slots.items())

        def is_config_log(local):
            return bool(transfers) and local.get('self') is getattr(transfers[0], '_config_log', None)

        def started(code, offset):
            local = sys._getframe(1).f_locals
            actor = local.get('self')
            if code is codes['root'] and actor.hass is hass:
                roots.append(actor)
            elif code is codes['install'] and actor.observer.hass is hass:
                transfers.append(actor)
                self.assertEqual(len(transfers), 1)
                self.assertEqual(actor.phase, 'captured')
                edge, = actor.duration_edges
                self.assertIs(edge.before, configured)
                self.assertIs(edge.after, configured)
                self.assertEqual(graph(configured), root_before)
                self.assertTrue(edge.paths)
                self.assertEqual(len(edge.owners), 1, 'native pending has no historical OWN holder')
                self.assertTrue(any(kind == 'dict' and owner is configured
                                    for kind, owner, _, _ in actor.operations))
            elif transfers and code is codes['graph_undo'] and actor is transfers[0].graph:
                graph_undos.append(local['plan'])
                order.append('graph_undo')
            elif injected and code is codes['log_undo']:
                log_undos.append(local['plan'])
                order.append('log_undo')
            elif transfers and code is codes['graph_plan'] and actor is transfers[0].graph:
                self.assertIs(local['configured_view'], views[-1])
                self.assertTrue(local['before_attempt'])
                order.append('graph_plan_start')
            elif code in (codes['handoff'], codes['emission'], codes['registry']):
                if getattr(actor, 'hass', None) is hass:
                    api.append((code, actor))

        def returned(code, offset, result):
            local = sys._getframe(1).f_locals
            if code is codes['log_plan'] and is_config_log(local):
                shadow = local['shadow']
                transfer = transfers[0]
                self.assertEqual((graph_undos, log_undos), ([], []))
                for key, (kind, owner, before) in transfer._config_log.slots.items():
                    self.assertEqual(log_type.token(kind, shadow[key]), log_type.token(kind, before))
                self.assertEqual(log_type.token('dict', shadow[('dict', id(configured))]),
                                 log_type.token('dict', items_before))
                plan_returns.append((result, dict(shadow)))
                order.append('log_plan_return')
                return
            if code is codes['view'] and transfers:
                view = local['self']
                self.assertIs(type(view), configured_module.ConfiguredRollbackView)
                self.assertEqual(view.shadow, plan_returns[-1][1])
                self.assertEqual(log_type.token('dict', view.slots[id(configured)]),
                                 log_type.token('dict', items_before))
                for holder, before, _ in transfers[0].duration_edges[0].owners:
                    self.assertIs(view.images[id(holder)][0], holder)
                    self.assertIs(view.images[id(holder)][1], before)
                views.append(view)
                order.append('view_return')
                return
            if code is codes['graph_plan'] and transfers and local.get('self') is transfers[0].graph:
                graph_plans.append(result)
                order.append('graph_plan_return')
                return
            if code is not write_code or not is_config_log(local) or injected:
                return
            transfer = transfers[0]
            edge, = transfer.duration_edges
            holder, before, after = edge.owners[0]
            kind, owner = local['kind'], local['owner']
            self.assertIs(transfer._config_log.records[-1][3], True)
            if kind == 'dict' and owner is configured:
                root_writes.append((kind, owner))
            selected = (seam == 'root' and kind == 'dict' and owner is configured
                or seam == 'holder' and kind == 'image' and owner is holder
                or seam == 'approval' and kind == 'entry'
                    and owner[0] is holder.binding._duration.configured_images and owner[1] is holder)
            if not selected:
                return
            self.assertEqual(len(root_writes), 1)
            self.assertFalse(transfer.graph.live)
            self.assertTrue(all(route.duration_operations is None for route in transfer.graph.routes))
            self.assertEqual(graph(configured), edge.after_graph)
            replacement = configured['seconds']
            self.assertIsNot(replacement, leaf)
            self.assertEqual(replacement.template, target_body)
            self.assertIs(holder.config['for'], configured)
            self.assertIs(holder.cell.cell_contents, configured)
            self.assertIs(holder.snapshot['time_delta'], configured)
            self.assertIs(holder.image, before if seam == 'root' else after)
            self.assertIs(holder.binding._duration.configured_images[holder],
                          after if seam == 'approval' else before)
            if foreign == 'sibling':
                configured['milliseconds'] = 1
                self.assertIs(configured['seconds'], replacement)
            elif foreign == 'order':
                # Keep every exact key/value object, alter only insertion order.
                key = next(key for key in configured if key == 'seconds')
                value = configured.pop(key)
                configured[key] = value
                self.assertEqual(tuple(configured), ('milliseconds', 'seconds'))
            injected.append({'transfer': transfer, 'edge': edge, 'holder': holder,
                             'before': before, 'after': after, 'replacement': replacement,
                             'slots': slots(transfer._config_log), 'native': native_graphs(),
                             'root': graph(configured), 'replacement_graph': graph(replacement)})
            order.append('fault')
            raise FixtureStructuredPartialWriteFault('completed configured dict '+seam+' write')

        def raised(code, offset, error):
            local = sys._getframe(1).f_locals
            if code is codes['log_plan'] and is_config_log(local):
                refusals.append(str(error))

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'actual Root structured partial journal fault')
        error = result = None
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, raised)
            for code in codes.values():
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            for name in ('log_plan', 'view', 'graph_plan'):
                sys.monitoring.set_local_events(tool, codes[name],
                    sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
            sys.monitoring.set_local_events(tool, codes['log_plan'],
                sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
            sys.monitoring.set_events(tool, sys.monitoring.events.RAISE)
            sys.monitoring.set_local_events(tool, write_code, sys.monitoring.events.PY_RETURN)
            with mixed.native_diagnostics(self.module):
                monitors = tuple(sys.monitoring.get_tool(k) for k in range(6))
                try:
                    result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                except (RuntimeError, ValueError, self.module('transaction').Refused) as exc:
                    error = exc
                self.assertEqual(tuple(sys.monitoring.get_tool(k) for k in range(6)), monitors)
            self.assertEqual(len(roots), 1)
            self.assertEqual(len(transfers), 1, 'earlier admission error is not a reached partial write')
            self.assertEqual(len(injected), 1, 'original selected completed-write seam is mandatory')
            self.assertIsNone(result)
            self.assertIsNotNone(error)
            saved = injected[0]
            transfer, edge = saved['transfer'], saved['edge']
            self.assertEqual((api, actions, scripts), ([], [], []))
            self.assertIs(hass.states.get(old), physical)
            self.assertIsNone(hass.states.get(new))
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            for name, content in original.items():
                self.assertEqual((path / name).read_bytes(), content)
            self.assertEqual(pending_image(), native_pending)
            self.assertTrue(all(actual is expected for index, (actual, expected) in
                enumerate(zip(pending_image(), native_pending, strict=True)) if index not in (3, 7)))
            self.assertFalse(pending['handle'].cancelled())
            self.assertGreater(pending['handle'].when(), hass.loop.time(), 'actual native timer must remain pending')
            self.assertEqual(tuple(callbacks), tuple(key for key, _, _ in callback_before))
            for key, members, expected in callback_before:
                self.assertIs(callbacks[key], members)
                self.assertEqual(tuple(members), expected)
            self.assertIs(edge.binding.config['for'], configured)
            self.assertEqual(graph(leaf), leaf_before)
            if foreign:
                self.assertEqual(transfer.phase, 'failed_before_attempt_cleanup_unknown')
                self.assertIn('automation_state_binding_duration_rollback_slot_changed', refusals,
                              'actual whole-log preflight must reject the foreign root slot')
                self.assertEqual((graph_undos, log_undos, plan_returns, views, graph_plans),
                                 ([], [], [], [], []), 'zero undo, no admitted shadow view')
                self.assertEqual(order, ['fault'])
                self.assertEqual(slots(transfer._config_log), saved['slots'])
                self.assertEqual(native_graphs(), saved['native'], 'preserve foreign edit and every own prefix write')
                self.assertEqual(graph(configured), saved['root'])
                self.assertIs(configured['seconds'], saved['replacement'])
                self.assertEqual(graph(saved['replacement']), saved['replacement_graph'])
                self.assertIs(saved['holder'].image, saved['before'])
                self.assertIs(saved['holder'].binding._duration.configured_images[saved['holder']], saved['before'])
                if foreign == 'sibling':
                    self.assertEqual(configured['milliseconds'], 1)
                    self.assertEqual(tuple(configured), tuple(key for key, _ in items_before))
                else:
                    self.assertIs(configured['milliseconds'], static)
                    self.assertEqual(tuple(configured), ('milliseconds', 'seconds'))
                return
            self.assertEqual(transfer.phase, 'failed_before_attempt')
            self.assertEqual(refusals, [])
            self.assertEqual((len(plan_returns), len(views), len(graph_plans), len(graph_undos), len(log_undos)),
                             (1, 1, 1, 1, 1))
            self.assertEqual(order, ['fault', 'log_plan_return', 'view_return', 'graph_plan_start',
                                     'graph_plan_return', 'graph_undo', 'log_undo'])
            self.assertIs(log_undos[0], plan_returns[0][0])
            self.assertIs(graph_undos[0], graph_plans[0])
            for _, (kind, owner, before) in transfer._config_log.slots.items():
                self.assertEqual(log_type.token(kind, log_type.read(kind, owner)), log_type.token(kind, before))
            self.assertEqual(graph(configured), root_before)
            self.assertEqual(log_type.token('dict', tuple(configured.items())), log_type.token('dict', items_before))
            self.assertIs(configured['seconds'], leaf)
            self.assertIs(configured['milliseconds'], static)
            self.assertEqual(native_graphs(), native_before)
            for holder, before, _ in edge.owners:
                self.assertFalse(any(
                    kind == 'cell_contents' and owner is holder.cell
                    or kind == 'entry' and owner[0] is holder.snapshot and owner[1] == 'time_delta'
                    for key, _, _, _ in transfer._config_log.records
                    for kind, owner, _ in (transfer._config_log.slots[key],)),
                    'same-root dict uses its one nested operation, no independent cell/snapshot writes')
                self.assertIs(holder.image, before)
                self.assertIs(holder.config['for'], configured)
                self.assertIs(holder.cell.cell_contents, configured)
                self.assertIs(holder.snapshot['time_delta'], configured)
                self.assertIs(holder.binding._duration.configured_images[holder], before)
                holder.check()
            restored = cap.capture(source)
            self.assertEqual(restored.original_keys, [old])
            self.assertIs(restored.listener, binding.listener)
            self.assertIs(restored.keyed_job.target, restored.listener)
            restored_pending, = cap.pending(restored)[0]
            self.assertIs(restored_pending['handle'], pending['handle'])
            self.assertIs(restored.listener_cells['period'][old], native_pending[-1])
            phase = roots[0].consumers.phases
            normal = self.module('yaml_validation_worker')._normal
            observer = self.module('yaml_runtime_observer').NativeYAMLRuntimeReadback(hass, owned_automation=phase.graph)
            for domain, rows in phase.program.rows(frozenset()):
                self.assertEqual(normal(observer._projection(domain)[0]), rows)
        finally:
            sys.monitoring.set_events(tool, 0)
            for code in (*codes.values(), write_code):
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, None)
            sys.monitoring.free_tool_id(tool)
