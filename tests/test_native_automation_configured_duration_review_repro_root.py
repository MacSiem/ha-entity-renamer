"""SOURCE ONLY, NOT RUN: exact two configured-duration source review findings.

Actual two native producers, one Root, two accepted-program source edges. The
second edge changes only the duration dependency, never the trigger ID. Only
fixture producer data and synchronous fault injection at actual completed writes
are changed; no product/native function, verifier, Handle or clock is patched.
"""
import sys
import unittest
import test_native_automation_source_duration_root as source_fixture
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationConfiguredDurationReviewReproRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_retained_own_snapshot_foreign_edit_after_dependency_map_update_refuses_zero_undo(self):
        await self._review_repro('retained_sibling')

    async def test_same_root_second_edge_configured_cell_fault_restores_prefix_without_stale_detach(self):
        await self._review_repro('second_cell')

    async def _review_repro(self, mode):
        from homeassistant.core import Context
        from homeassistant.helpers import config_validation as cv, issue_registry as ir, template
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, _, _ = await self.data()
        hass, path = self.f.hass, self.f.root
        first, first_next = next(iter(mapping.items()))
        # Exact native second-producer construction used by the existing
        # NativeScriptPrefixRootTests.test_script_prefix_two_sources_... .
        other = type(self.producer)()
        other._attr_unique_id = 'configured-review-second-native-producer'
        other._attr_name = 'Zz configured review other source'
        other._attr_native_value = 7
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([other])
        second = other.entity_id
        second_next = 'sensor.zz_configured_review_other_next'
        mapping[second] = second_next
        uuids[second] = self.f.entity.async_get(second).id
        order = tuple(sorted(mapping))
        self.assertEqual(order, (first, second), 'real sealed program must attempt trigger source first')
        template_path = path / 'template.yaml'
        template_path.write_text(template_path.read_text() +
            '- unique_id: configured_review_other\n  sensor:\n  - unique_id: value\n'
            '    name: Configured review other consumer\n'
            '    state: "{{ states(' + repr(second) + ') | int(0) * 3 }}"\n')
        body = "{{ 900 + states('" + second + "')|int }}"
        after_body = "{{ 900 + states('" + second_next + "')|int }}"
        (path / 'mixed_automations.yaml').write_text(
            '- id: mixed_source\n  alias: Configured source review reproducer\n  initial_state: true\n'
            '  triggers:\n  - trigger: state\n    entity_id: '+first+'\n'
            '    from: "17"\n    to: "29"\n    for: "'+body+'"\n'
            '  actions:\n  - event: configured_review_action\n'
            '    event_data:\n      seconds: "{{ trigger.for.total_seconds() }}"\n'
            '  - action: script.mixed_source\n')
        (path / 'mixed_scripts.yaml').write_text(
            'mixed_source:\n  sequence:\n  - event: configured_review_script\n'
            '    event_data:\n      value: "{{ states(\''+second+'\') }}"\n')
        for domain in ('template', 'script', 'automation'):
            await hass.services.async_call(domain, 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        self.identities = {(p.domain, e.unique_id):
            (e.entity_id, None if (r := self.f.entity.async_get(e.entity_id)) is None else r.id)
            for p in async_get_platforms(hass, 'template') for e in p.entities.values()}
        issues = ir.async_get(hass)
        store = issues._store
        if any(value is not None for value in
               (store._data, store._delay_handle, store._unsub_final_write_listener)):
            await store.async_save(issues._data_to_save())
        await hass.async_block_till_done()
        self.assertTrue(all(value is None for value in
            (store._data, store._delay_handle, store._unsub_final_write_listener)))
        source = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities
                      if e.unique_id == 'mixed_source')
        cap = await self._capability()
        await self.stimulus(15)
        await self.stimulus(17)
        await hass.async_block_till_done()
        physical = hass.states.get(first)
        other_physical = hass.states.get(second)
        self.assertEqual(physical.state, '17')
        self.assertEqual(other_physical.state, '7')
        self.assertEqual(cap.pending(cap.capture(source))[0], ())
        await self._flush_documents()
        duration = self.module('native_automation_duration_binding')
        log_type = duration._PreEffectLog
        graph_fn = self.module('native_script_config_transport')._graph
        root_type = self.module('native_template_state_server').NativeTemplateStateServer
        phase_type = self.module('native_automation_prefix_phase').NativeAutomationPrefixPhase
        transfer_type = self.module('native_automation_config_transport').NativeAutomationConfigTransport
        graph_type = self.module('native_automation_owned_graph').OwnedAutomationGraph
        expiry_type = self.module('native_automation_duration_expiry').NativeDurationExpiry
        handoff_type = self.module('native_source_handoff').NativeSourceHandoff
        emission_type = self.module('native_rename_emission_classifier').NativeRenameEmissions
        registry_type = self.module('native_registry').NativeRegistryIO
        codes = {'root': root_type._install.__code__, 'accepted': phase_type.accepted.__code__,
                 'transfer': transfer_type.install.__code__, 'graph_check': graph_type.check.__code__,
                 'graph_undo': graph_type.apply_retire.__code__, 'log_undo': log_type.undo.__code__,
                 'detach': expiry_type.detach.__code__, 'handoff': handoff_type.mutate.__code__,
                 'emission': emission_type.mutate.__code__, 'registry': registry_type.mutate.__code__}
        write_code = log_type.write.__code__
        render_codes = (template.render_complex.__code__, template.Template.async_render.__code__,
                        type(cv.positive_time_period).__call__.__code__)
        roots, transfers, accepted, handoffs, emissions, registry = [], [], [], [], [], []
        graph_undos, log_undos, detaches, checks = [], [], [], []
        renders, template_calls, validations, actions, scripts = [], [], [], [], []
        first_state, injected = [], []
        self.addCleanup(hass.bus.async_listen('configured_review_action', actions.append))
        self.addCleanup(hass.bus.async_listen('configured_review_script', scripts.append))

        class FixtureConfiguredReviewFault(RuntimeError):
            pass

        def source_template(value):
            return (type(value) is template.Template and value.hass is hass
                    and value.template in (body, after_body))

        def api_image():
            return len(handoffs), len(emissions), len(registry), len(renders), len(template_calls), len(validations)

        def slots(log):
            return tuple((key, log_type.token(kind, log_type.read(kind, owner)))
                         for key, (kind, owner, _) in log.slots.items())

        def occurrence_image(pending, binding):
            own = pending['own']
            history = own.history()
            h = pending['handle']
            return (own, own.binding, own.binding._duration_scalar_image, own.approved_entity,
                    pending['clear'], pending['cancel'], pending['cancel_job'], pending['cancel_job'].target,
                    pending['keys'], tuple(pending['keys']), pending['entity_cell'], pending['entity_cell'].cell_contents,
                    h, h.when(), h._context, h._callback, h._args, pending['timer_job'], pending['same_job'],
                    pending['action'], history['from_s'], history['to_s'], history['event'],
                    history['event'].context, binding.listener_cells['period'][pending['key']])

        def started(code, offset):
            local = sys._getframe(1).f_locals
            actor = local.get('self')
            if code is codes['root'] and actor.hass is hass:
                roots.append(actor)
            elif code is codes['transfer'] and actor.observer.hass is hass:
                transfers.append(actor)
                if len(transfers) == 2:
                    self.assertEqual(len(accepted), 1, 'first edge must already be accepted')
                    self.assertEqual(len(roots), 1, 'both edges must share one actual Root')
                    self.assertIs(actor.graph, transfers[0].graph)
                    self.assertIs(actor.chain, transfers[0].chain)
                    self.assertIs(roots[0].consumers.phases.graph, actor.graph)
                    self.assertEqual(roots[0].consumers.phases.program.source_order, order)
                    self.assertFalse(actor.graph.live)
                    self.assertIs(actor.graph.witness, first_state[0]['witness'])
                    self.assertIsNot(actor.graph.duration.expiry.witness, actor.graph.witness)
                    self.assertIsNotNone(actor.graph.duration.expiry.witness)
                    self.assertEqual(len(actor.duration_edges), 1)
                    route, = actor.graph.routes
                    self.assertEqual((route.before, route.target), ((first_next,), (first_next,)))
                    self.assertIsNone(route.duration_operations)
                    edge, = actor.duration_edges
                    self.assertEqual((edge.before.template, edge.after.template), (body, after_body))
                    own = first_state[0]['pending']['own']
                    self.assertIsNot(edge.binding, own.binding, 'retained OWN needs a fresh binding')
                    self.assertTrue(any(holder is own.binding._duration_config_owner
                                        for holder, _, _ in edge.owners))
                    self.assertGreaterEqual(len(edge.owners), 2)
                    first_state[0]['second_api'] = api_image()
                    first_state[0]['second_checks'] = len(checks)
                    first_state[0]['native_prefix'] = (graph_fn(source.raw_config), graph_fn(source._trigger_config),
                                                       graph_fn(source.action_script.sequence))
            elif code is codes['handoff'] and actor.hass is hass:
                handoffs.append(actor)
                if len(handoffs) == 1:
                    self.assertIs(hass.states.get(first), physical)
                    actor.source._attr_native_value = 29
            elif code is codes['emission'] and actor.hass is hass:
                emissions.append(actor)
            elif code is codes['registry'] and getattr(actor, 'hass', None) is hass:
                registry.append(actor)
            elif transfers and code is codes['graph_check'] and actor is transfers[-1].graph:
                checks.append(actor)
            elif transfers and code is codes['graph_undo'] and actor is transfers[-1].graph:
                # Count entry before writes; retain the argument opaquely so the
                # fixture also works when a typed retirement plan is introduced.
                graph_undos.append((actor, local.get('plans', local.get('plan')),
                                    local.get('before_attempt', False)))
            elif transfers and code is codes['log_undo']:
                log_undos.append(tuple(local['plan']))
            elif transfers and code is codes['detach'] and actor is transfers[-1].graph.duration.expiry:
                detaches.append((actor, local['witness'], actor.witness))

        def returned(code, offset, result):
            frame = sys._getframe(1)
            local = frame.f_locals
            actor = local.get('self')
            if code is codes['accepted'] and actor.root.hass is hass:
                accepted.append((actor, frozenset(actor.mask)))
                self.assertEqual(len(accepted), 1, 'second registry edge must not be accepted')
                self.assertEqual(actor.mask, frozenset({first}))
                self.assertFalse(actor.graph.live)
                self.assertEqual(actor.graph.routes, [])
                self.assertIsNone(actor.graph.duration.expiry.witness)
                witness = actor.graph.witness
                self.assertTrue(witness.finished)
                self.assertTrue(witness.closed)
                pending, = cap.pending(cap.capture(source))[0]
                own = pending['own']
                self.assertIs(type(own), duration._OwnDuration)
                own.check()
                history = own.history()
                self.assertIs(history['from_s'], physical)
                self.assertEqual((history['from_s'].state, history['to_s'].state), ('17', '29'))
                self.assertEqual(history['to_s'].entity_id, first_next)
                self.assertEqual(own.binding.listener_cells['period'][first_next].total_seconds(), 907)
                self.assertEqual(own.binding.config['for'].template, body)
                self.assertFalse(pending['handle'].cancelled())
                self.assertEqual(api_image(), (1, 1, 1, 1, 1, 1))
                first_state.append({'witness': witness, 'pending': pending, 'binding': own.binding,
                    'occurrence': occurrence_image(pending, own.binding)})
            elif code is render_codes[0] and source_template(local.get('value')):
                renders.append((local['value'], result))
            elif code is render_codes[1] and source_template(actor):
                template_calls.append((actor, result))
            elif code is render_codes[2] and actor is cv.positive_time_period:
                caller = frame.f_back
                binding = caller.f_locals.get('binding')
                configured = caller.f_locals.get('time_delta')
                if binding is not None and binding.hass is hass:
                    configured = binding.config['for']
                if source_template(configured):
                    validations.append((local.get('value', local.get('v')), result))
            if code is not write_code or len(transfers) != 2 or injected:
                return
            transfer = transfers[1]
            edge, = transfer.duration_edges
            route, = transfer.graph.routes
            log = local.get('self')
            kind, owner = local['kind'], local['owner']
            if mode == 'second_cell':
                selected = (log is getattr(transfer, '_config_log', None)
                            and kind == 'cell_contents' and owner is edge.binding._duration_config_owner.cell)
            else:
                caller = frame.f_back
                # The exact FINAL map_update of duration.transport, after its
                # real dict.update completed, before wrapper/wrap/graph.check.
                selected = (log is route.duration_operations and kind == 'dict'
                    and owner is route.binding.listener_cells['unsub_track_same']
                    and caller.f_code.co_name == 'write' and caller.f_locals.get('kind') == 'map_update'
                    and caller.f_back.f_code is duration.NativeAutomationDurationCapability.transport.__code__)
            if not selected:
                return
            self.assertIs(log.records[-1][3], True, 'fault follows completed actual log.write')
            self.assertEqual(api_image(), first_state[0]['second_api'])
            self.assertEqual(len(checks), first_state[0]['second_checks'])
            self.assertIs(edge.binding.config['for'], edge.after)
            self.assertIs(edge.binding._duration_config_owner.cell.cell_contents, edge.after)
            self.assertEqual(edge.binding.config['entity_id'], [first_next])
            self.assertEqual((actions, scripts), ([], []))
            pending = first_state[0]['pending']
            own = pending['own']
            self.assertEqual(occurrence_image(pending, own.binding), first_state[0]['occurrence'])
            if mode == 'second_cell':
                self.assertFalse(transfer.graph.live)
                self.assertTrue(all(r.duration_operations is None for r in transfer.graph.routes))
                self.assertIs(transfer.graph.witness, first_state[0]['witness'])
                self.assertIsNot(transfer.graph.duration.expiry.witness, transfer.graph.witness)
                foreign_value = None
            else:
                self.assertTrue(transfer.graph.live)
                self.assertIsNotNone(route.duration_operations)
                self.assertFalse(route.wrapper_written)
                self.assertFalse(route.duration_transported)
                self.assertIs(route.binding.keyed_job.target, route.binding.listener)
                own.check()
                for holder, _, after in edge.owners:
                    self.assertIs(holder.image, after)
                    holder.check()
                for key in ('period', 'unsub_track_same'):
                    values = route.binding.listener_cells[key]
                    self.assertEqual(tuple(values), (first_next,))
                retained_holder = own.binding._duration_config_owner
                self.assertIsNot(retained_holder.snapshot, edge.binding.listener_cells)
                self.assertIs(retained_holder.snapshot['job'], own.binding.action_job)
                foreign_value = object()
                retained_holder.snapshot['job'] = foreign_value
                # Deliberate foreign edit is outside every journaled slot.
                edge.binding._duration_config_owner.check()
            logs = (transfer._config_log,) + tuple(r.duration_operations for r in transfer.graph.routes
                                                  if r.duration_operations is not None)
            injected.append({'transfer': transfer, 'edge': edge, 'logs': logs,
                'slots': tuple(slots(item) for item in logs), 'foreign': foreign_value,
                'undos': (len(graph_undos), len(log_undos)), 'detaches': len(detaches),
                'pins': tuple((value, graph_fn(value)) for value, _ in edge.pins)})
            raise FixtureConfiguredReviewFault('fixture interrupted actual second edge '+mode)

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'configured source review exact two-edge faults')
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            for key, code in codes.items():
                sys.monitoring.set_local_events(tool, code,
                    sys.monitoring.events.PY_RETURN if key == 'accepted' else sys.monitoring.events.PY_START)
            for code in (write_code, *render_codes):
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
            with mixed.native_diagnostics(self.module):
                with self.assertRaises((FixtureConfiguredReviewFault, ValueError,
                                        self.module('transaction').Refused)):
                    await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                self.assertEqual((len(roots), len(transfers), len(accepted), len(injected)), (1, 2, 1, 1),
                                 'must reach the exact second edge after real first acceptance/retirement')
                fault = injected[0]
                transfer, edge = fault['transfer'], fault['edge']
                saved = first_state[0]
                self.assertEqual(api_image(), saved['second_api'], 'zero source/registry/render/CV API on second edge')
                self.assertEqual((actions, scripts), ([], []))
                self.assertEqual(self.f.entity.async_get(first_next).id, uuids[first])
                self.assertIsNone(self.f.entity.async_get(first))
                self.assertEqual(self.f.entity.async_get(second).id, uuids[second])
                self.assertIsNone(self.f.entity.async_get(second_next))
                self.assertIs(hass.states.get(second), other_physical)
                self.assertEqual(occurrence_image(saved['pending'], saved['binding']), saved['occurrence'])
                self.assertFalse(saved['pending']['handle'].cancelled())
                if mode == 'retained_sibling':
                    self.assertEqual(transfer.phase, 'failed_before_attempt_cleanup_unknown')
                    self.assertIs(saved['binding'].listener_cells['job'], fault['foreign'])
                    self.assertEqual((len(graph_undos), len(log_undos)), fault['undos'],
                                     'all retained OWN holders must pass preflight before ANY undo')
                    self.assertEqual(len(detaches), fault['detaches'])
                    self.assertEqual(tuple(slots(item) for item in fault['logs']), fault['slots'],
                                     'no journaled configuration or occurrence slot may be rewritten')
                    for value, image in fault['pins']:
                        self.assertEqual(graph_fn(value), image)
                else:
                    self.assertEqual(transfer.phase, 'failed_before_attempt', 'recognized own prefix must restore')
                    self.assertEqual(len(graph_undos), fault['undos'][0] + 1)
                    self.assertEqual(len(log_undos), fault['undos'][1] + 1)
                    self.assertFalse(any(given is saved['witness']
                        for _, given, _ in detaches[fault['detaches']:]),
                        'pre-graph cleanup must never detach the retired first witness')
                    for _, (kind, owner, before) in transfer._config_log.slots.items():
                        self.assertEqual(log_type.token(kind, log_type.read(kind, owner)),
                                         log_type.token(kind, before), 'exact identity restoration of own prefix')
                    self.assertEqual((graph_fn(source.raw_config), graph_fn(source._trigger_config),
                                      graph_fn(source.action_script.sequence)), saved['native_prefix'])
                    for holder, before, _ in edge.owners:
                        self.assertIs(holder.image, before)
                        holder.check()
                    saved['pending']['own'].check()
                    self.assertEqual(source._trigger_config[0]['entity_id'], [first_next])
                    self.assertEqual(source._trigger_config[0]['for'].template, body)
        finally:
            for code in (*codes.values(), write_code, *render_codes):
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
