"""SOURCE-ONLY DRAFT, NOT RUN: configured scalar rollback boundaries.

Actual Root and native producer, fixture-only synchronous fault injection after
an actual journal write. No native methods, clock, Handle or verifier patched.
Run only after the source-bearing positive and source review are resolved.
"""
import sys
import unittest
import test_native_automation_source_duration_root as source_fixture
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationConfiguredDurationFaultRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_interrupted_configured_cell_write_exact_restore_zero_api(self):
        await self._configured_fault('cell')

    async def test_interrupted_configured_snapshot_entry_exact_restore_zero_api(self):
        await self._configured_fault('snapshot')

    async def test_interrupted_configured_holder_write_exact_restore_zero_api(self):
        await self._configured_fault('holder')

    async def test_interrupted_configured_approval_entry_exact_restore_zero_api(self):
        await self._configured_fault('approval')

    async def test_shared_callbacks_two_unstarted_source_routes_restore_after_snapshot_write(self):
        await self._configured_fault('snapshot', shared=True)

    async def test_foreign_original_template_body_refuses_zero_undo(self):
        await self._configured_fault('approval', foreign='original_body')

    async def test_foreign_original_template_hass_refuses_zero_undo(self):
        await self._configured_fault('approval', foreign='original_hass')

    async def test_foreign_replacement_template_body_refuses_zero_undo(self):
        await self._configured_fault('approval', foreign='replacement_body')

    async def test_foreign_replacement_template_hass_refuses_zero_undo(self):
        await self._configured_fault('approval', foreign='replacement_hass')

    async def test_foreign_time_delta_cell_refuses_zero_undo(self):
        await self._configured_fault('approval', foreign='cell')

    async def test_foreign_configured_holder_value_refuses_zero_undo(self):
        await self._configured_fault('approval', foreign='holder')

    async def _configured_fault(self, seam, *, foreign=None, shared=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers import issue_registry as ir
        runtime, mapping, uuids, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        body = "{{ 300 + states('" + old + "')|int }}"
        target_body = "{{ 300 + states('" + new + "')|int }}"
        def automation(unique_id):
            return ('- id: '+unique_id+'\n  alias: Configured duration fault fixture\n'
                '  initial_state: true\n  variables:\n    source: '+old+'\n'
                '  triggers:\n  - trigger: state\n    entity_id: '+old+'\n'
                '    from: "16"\n    to: "17"\n    for: "'+body+'"\n'
                '  actions:\n  - event: configured_duration_fault_result\n'
                '    event_data:\n      actor: '+unique_id+'\n'
                '      value: "{{ states(source) }}"\n  - action: script.mixed_source\n')
        identifiers = ('mixed_source', 'zz_configured_second') if shared else ('mixed_source',)
        (root/'mixed_automations.yaml').write_text(''.join(automation(key) for key in identifiers))
        (root/'mixed_scripts.yaml').write_text(
            'mixed_source:\n  variables:\n    source: '+old+'\n  sequence:\n'
            '  - event: configured_duration_fault_script\n'
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
            original[name] = (root/name).read_bytes()
        entities = hass.data[DATA_INSTANCES]['automation'].entities
        sources = tuple(next(entity for entity in entities if entity.unique_id == key)
                        for key in identifiers)
        cap = await self._capability()
        duration_module = self.module('native_automation_duration_binding')
        transport_type = self.module('native_automation_config_transport').NativeAutomationConfigTransport
        graph_type = self.module('native_automation_owned_graph').OwnedAutomationGraph
        root_type = self.module('native_template_state_server').NativeTemplateStateServer
        handoff_type = self.module('native_source_handoff').NativeSourceHandoff
        emission_type = self.module('native_rename_emission_classifier').NativeRenameEmissions
        graph = self.module('native_script_config_transport')._graph
        log_type = duration_module._PreEffectLog
        await self.stimulus(16)
        await self.stimulus(17)
        await hass.async_block_till_done()
        physical = hass.states.get(old)
        self.assertEqual(physical.state, '17')
        bindings = tuple(cap.capture(entity) for entity in sources)
        pendings = tuple(cap.pending(binding)[0][0] for binding in bindings)
        def pending_image(binding, pending):
            history = duration_module._cells(pending['action'])
            return (pending['clear'], pending['cancel'], pending['keys'], tuple(pending['keys']),
                pending['cancel_job'], pending['cancel_job'].target, pending['handle'],
                pending['handle'].when(), pending['handle']._context,
                pending['timer_job'], pending['same_job'], pending['action'],
                history['from_s'], history['to_s'], history['event'], history['event'].context,
                binding.listener_cells['period'][old])
        pending_before = tuple(pending_image(b, p) for b, p in zip(bindings, pendings))
        native_before = tuple((graph(entity.raw_config), graph(entity._trigger_config),
                               graph(entity.action_script.sequence)) for entity in sources)
        callbacks = bindings[0].callbacks
        callback_before = tuple((key, value, tuple(value)) for key, value in callbacks.items())
        if shared:
            self.assertIs(bindings[1].callbacks, callbacks)
            self.assertIsNot(bindings[0].listener, bindings[1].listener)
            self.assertTrue(all(binding.keyed_job in callbacks[old] for binding in bindings))
        await self._flush_documents()
        actions, scripts = [], []
        self.addCleanup(hass.bus.async_listen('configured_duration_fault_result', actions.append))
        self.addCleanup(hass.bus.async_listen('configured_duration_fault_script', scripts.append))
        roots, transfers, handoffs, emission_attempts = [], [], [], []
        graph_undos, log_undos, observed_writes = [], [], []
        injected = []
        write_code = log_type.write.__code__
        codes = {'root': root_type._install.__code__, 'transport': transport_type.install.__code__,
                 'handoff': handoff_type.mutate.__code__, 'emission': emission_type.mutate.__code__,
                 'graph_undo': graph_type.apply_retire.__code__, 'log_undo': log_type.undo.__code__}
        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'configured duration actual Root journal fault')

        class FixtureConfiguredWriteFault(RuntimeError):
            pass

        def slot_image(log):
            return tuple((key, log_type.token(kind, log_type.read(kind, owner)))
                         for key, (kind, owner, _) in log.slots.items())

        def start(code, offset):
            local = sys._getframe(1).f_locals
            actor = local.get('self')
            if code is codes['root'] and actor.hass is hass:
                roots.append(actor)
            elif code is codes['transport'] and actor.observer.hass is hass:
                transfers.append(actor)
                self.assertEqual(len(actor.duration_edges), len(sources))
                self.assertFalse(actor.graph.live)
                self.assertTrue(all(route.duration_operations is None for route in actor.graph.routes))
            elif code is codes['handoff'] and actor.hass is hass:
                handoffs.append(actor)
            elif code is codes['emission'] and actor.hass is hass:
                emission_attempts.append(actor)
            elif code is codes['graph_undo'] and transfers and actor is transfers[-1].graph:
                graph_undos.append(local.get('plans', local.get('plan')))
            elif code is codes['log_undo'] and transfers:
                # All routes are unstarted: even another journal's undo call
                # would violate the foreign-conflict zero-undo boundary.
                log_undos.append(tuple(local['plan']))

        def returned(code, offset, value):
            if code is not write_code or injected or not transfers:
                return
            local = sys._getframe(1).f_locals
            transfer = transfers[-1]
            if local.get('self') is not getattr(transfer, '_config_log', None):
                return
            edge = transfer.duration_edges[-1] if shared else transfer.duration_edges[0]
            holder, before_image, after_image = edge.owners[0]
            kind, owner = local['kind'], local['owner']
            selected = (seam == 'cell' and kind == 'cell_contents' and owner is holder.cell
                or seam == 'snapshot' and kind == 'entry'
                    and owner[0] is holder.snapshot and owner[1] == 'time_delta'
                or seam == 'holder' and kind == 'image' and owner is holder
                or seam == 'approval' and kind == 'entry'
                    and owner[0] is holder.binding._duration.configured_images and owner[1] is holder)
            observed_writes.append((kind, owner))
            if not selected:
                return
            self.assertIs(transfer._config_log.records[-1][3], True)
            self.assertFalse(transfer.graph.live)
            self.assertTrue(all(route.duration_operations is None for route in transfer.graph.routes))
            self.assertIs(holder.cell.cell_contents, edge.after)
            self.assertEqual(edge.after.template, target_body)
            self.assertIsNot(edge.after, edge.before)
            foreign_value = None
            if foreign in ('original_body', 'replacement_body', 'original_hass', 'replacement_hass'):
                victim = edge.before if foreign.startswith('original_') else edge.after
                attribute = 'template' if foreign.endswith('_body') else 'hass'
                foreign_value = victim.template + ' {# deliberate foreign edit #}' if attribute == 'template' else None
                setattr(victim, attribute, foreign_value)
                foreign_target = (victim, attribute)
            elif foreign == 'cell':
                foreign_value = object()
                holder.cell.cell_contents = foreign_value
                foreign_target = (holder.cell, 'cell_contents')
            elif foreign == 'holder':
                foreign_value = object()
                holder.image = foreign_value
                foreign_target = (holder, 'image')
            else:
                foreign_target = None
            injected.append((transfer, edge, holder, before_image, after_image, foreign_target,
                             foreign_value, slot_image(transfer._config_log),
                             tuple((item, graph(item)) for item in (edge.before, edge.after))))
            raise FixtureConfiguredWriteFault('fixture interrupted completed configured '+seam+' write')

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, start)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            for code in codes.values():
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            sys.monitoring.set_local_events(tool, write_code, sys.monitoring.events.PY_RETURN)
            with mixed.native_diagnostics(self.module):
                monitors = tuple(sys.monitoring.get_tool(k) for k in range(6))
                with self.assertRaises((FixtureConfiguredWriteFault, ValueError,
                                        self.module('transaction').Refused)):
                    await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                self.assertEqual(tuple(sys.monitoring.get_tool(k) for k in range(6)), monitors)
                self.assertEqual(len(roots), 1)
                self.assertEqual(len(transfers), 1)
                self.assertEqual(len(injected), 1, 'must reach actual selected completed write')
                transfer, edge, holder, before_image, after_image, target, foreign_value, slots, pins = injected[0]
                self.assertEqual((handoffs, emission_attempts, actions, scripts), ([], [], [], []))
                self.assertIs(hass.states.get(old), physical)
                self.assertIsNone(hass.states.get(new))
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
                for name, content in original.items():
                    self.assertEqual((root/name).read_bytes(), content)
                for b, p, expected in zip(bindings, pendings, pending_before):
                    self.assertEqual(pending_image(b, p), expected)
                    self.assertFalse(p['handle'].cancelled())
                self.assertEqual(tuple(callbacks), tuple(key for key, _, _ in callback_before))
                for key, members, expected in callback_before:
                    self.assertIs(callbacks[key], members)
                    self.assertEqual(tuple(members), expected)
                if foreign:
                    self.assertEqual(transfer.phase, 'failed_before_attempt_cleanup_unknown')
                    self.assertEqual((graph_undos, log_undos), ([], []), 'no undo before global preflight')
                    self.assertIs(getattr(*target), foreign_value)
                    self.assertEqual(slot_image(transfer._config_log), slots,
                                     'every journaled slot remains exactly at refused boundary')
                    for item, expected in pins:
                        self.assertEqual(graph(item), expected, 'preserve foreign and all retained Template graphs')
                    self.assertIs(edge.binding.config['for'], edge.after)
                    self.assertEqual(edge.binding.config['entity_id'], [new])
                    return
                self.assertEqual(transfer.phase, 'failed_before_attempt')
                self.assertEqual((len(graph_undos), len(log_undos)), (1, 1))
                for _, (kind, owner, expected) in transfer._config_log.slots.items():
                    self.assertEqual(log_type.token(kind, log_type.read(kind, owner)),
                                     log_type.token(kind, expected), 'exact slot identity restoration')
                for configured_edge in transfer.duration_edges:
                    for retained_holder, original_image, _ in configured_edge.owners:
                        self.assertIs(retained_holder.image, original_image)
                        self.assertIs(retained_holder.cell.cell_contents, original_image.template)
                        self.assertIs(retained_holder.snapshot['time_delta'], original_image.template)
                        self.assertIs(retained_holder.binding._duration.configured_images[retained_holder], original_image)
                        retained_holder.check()
                for entity, expected in zip(sources, native_before):
                    self.assertEqual((graph(entity.raw_config), graph(entity._trigger_config),
                                      graph(entity.action_script.sequence)), expected)
                    restored = cap.capture(entity)
                    self.assertEqual(restored.original_keys, [old])
                    self.assertIs(restored.keyed_job.target, restored.listener)
                phase = roots[0].consumers.phases
                normal = self.module('yaml_validation_worker')._normal
                observer = self.module('yaml_runtime_observer').NativeYAMLRuntimeReadback(hass, owned_automation=phase.graph)
                for domain, rows in phase.program.rows(frozenset()):
                    self.assertEqual(normal(observer._projection(domain)[0]), rows)
        finally:
            for code in (*codes.values(), write_code):
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
