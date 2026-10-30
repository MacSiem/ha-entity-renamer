"""SOURCE ONLY / UNEXECUTED: actual Root configured-dict foreign-cache faults.

One real automation, one source-bearing Script and one unrelated idle Script.
Original method code is observed via fixture-owned sys.monitoring. The only
foreign edit is one cache entry; the post-write case also raises a fixture fault.
No Core method, verifier, State/Event, timer, clock or callback is replaced.
"""
import sys
import unittest
import test_native_automation_duration_prefix_root as literal
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationStructuredDurationAliasRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_unadmitted_script_cache_alias_at_original_install_entry_refuses_before_first_write(self):
        await self._alias_fault(after_write=False)

    async def test_unadmitted_script_cache_alias_after_completed_configured_writes_refuses_global_preundo(self):
        await self._alias_fault(after_write=True)

    async def _alias_fault(self, *, after_write):
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
            '- id: mixed_source\n  alias: Configured root alias fault\n  initial_state: true\n'
            '  variables:\n    source: '+old+'\n  triggers:\n'
            '  - trigger: state\n    entity_id: '+old+'\n    from: "17"\n    to: "29"\n'
            '    for:\n      seconds: "'+body+'"\n      milliseconds: 0\n'
            '  actions:\n  - event: structured_alias_action\n'
            '    event_data:\n      value: "{{ states(source) }}"\n'
            '  - action: script.mixed_source\n')
        (path / 'mixed_scripts.yaml').write_text(
            'mixed_source:\n  variables:\n    source: '+old+'\n  sequence:\n'
            '  - event: structured_alias_script\n'
            '    event_data:\n      value: "{{ states(source) }}"\n'
            'zz_structured_unrelated:\n  alias: Independent idle alias target\n'
            '  sequence:\n  - delay: "00:00:01"\n')
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
        source = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities
                      if e.unique_id == 'mixed_source')
        unrelated = next(e for e in hass.data[DATA_INSTANCES]['script'].entities
                         if e.unique_id == 'zz_structured_unrelated')
        foreign_script = unrelated.script
        transport = self.module('native_automation_config_transport').NativeAutomationConfigTransport
        duration = self.module('native_automation_duration_binding')
        log_type = duration._PreEffectLog
        config_module = self.module('native_script_config_transport')
        graph = config_module._graph
        cache_names = config_module._cache_names()
        cache_name = cache_names[0]
        self.assertIn(cache_name, ('_condition_cache', '_config_cache'))
        cache = getattr(foreign_script, cache_name)
        self.assertIs(type(cache), dict)
        alias_key = 'fixture_foreign_configured_root_alias'
        self.assertNotIn(alias_key, cache)
        configured = source._trigger_config[0]['for']
        self.assertIs(type(configured), dict)
        leaf = configured['seconds']
        self.assertIs(type(leaf), template.Template)
        self.assertEqual(leaf.template, body)
        before_root = graph(configured)
        before_leaf = graph(leaf)
        before_keys = tuple(configured)
        static = configured['milliseconds']
        foreign_before = (graph(unrelated.raw_config), graph(foreign_script.sequence),
                          graph(foreign_script.variables))
        caches_before = tuple((name, getattr(foreign_script, name),
                               tuple(getattr(foreign_script, name).items()),
                               graph(getattr(foreign_script, name))) for name in cache_names)
        cap = await self._capability()
        await self.stimulus(15)
        await self.stimulus(17)
        await hass.async_block_till_done()
        physical = hass.states.get(old)
        self.assertEqual(physical.state, '17')
        binding = cap.capture(source)
        self.assertIs(binding.config['for'], configured)
        self.assertEqual(cap.pending(binding)[0], ())
        # This profile has a native listener but no pending period and no timer
        # render is needed before the genuine Root reaches its transport seam.
        await self._flush_documents()
        for name in ('mixed_automations.yaml', 'mixed_scripts.yaml'):
            original[name] = (path / name).read_bytes()
        root_type = self.module('native_template_state_server').NativeTemplateStateServer
        graph_type = self.module('native_automation_owned_graph').OwnedAutomationGraph
        codes = {'root': root_type._install.__code__, 'install': transport.install.__code__,
                 'graph_undo': graph_type.apply_retire.__code__, 'log_undo': log_type.undo.__code__,
                 'handoff': self.module('native_source_handoff').NativeSourceHandoff.mutate.__code__,
                 'emission': self.module('native_rename_emission_classifier').NativeRenameEmissions.mutate.__code__,
                 'registry': self.module('native_registry').NativeRegistryIO.mutate.__code__}
        write_code = log_type.write.__code__
        alias_check_code = transport._check_duration_aliases.__code__
        alias_refusals = []
        roots, transfers, injected, writes, root_writes = [], [], [], [], []
        graph_undos, log_undos, api = [], [], []
        actions, scripts = [], []
        self.addCleanup(hass.bus.async_listen('structured_alias_action', actions.append))
        self.addCleanup(hass.bus.async_listen('structured_alias_script', scripts.append))

        class FixtureAliasAfterWriteFault(RuntimeError):
            pass

        def slots(log):
            return tuple((key, log_type.token(kind, log_type.read(kind, owner)))
                         for key, (kind, owner, _) in log.slots.items())

        def add_alias(transfer, edge):
            self.assertIs(edge.before, configured)
            self.assertIs(edge.after, configured)
            self.assertTrue(all(a.entity is not unrelated and a.script is not foreign_script
                                for a in transfer.actors), 'Script B must be outside admitted scope')
            self.assertIsNotNone(getattr(transfer, 'duration_alias_inventory', None),
                                 'capture must have completed its original alias census')
            self.assertFalse(transfer.graph.live)
            self.assertTrue(all(r.duration_operations is None for r in transfer.graph.routes))
            self.assertNotIn(alias_key, cache)
            cache[alias_key] = configured
            injected.append({'transfer': transfer, 'edge': edge, 'root': graph(configured),
                             'cache': graph(cache),
                             'slots': slots(transfer._config_log) if after_write else None})

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
                self.assertEqual(graph(configured), before_root)
                self.assertIsNone(getattr(actor, '_config_log', None))
                self.assertTrue(edge.paths, 'must reach actual authenticated dict duration edge')
                self.assertTrue(any(kind == 'dict' and owner is configured
                                    for kind, owner, _, _ in actor.operations))
                if not after_write:
                    add_alias(actor, edge)
            elif transfers and code is codes['graph_undo'] and actor is transfers[0].graph:
                graph_undos.append(actor)
            elif transfers and code is codes['log_undo']:
                log_undos.append(actor)
            elif code in (codes['handoff'], codes['emission'], codes['registry']):
                if getattr(actor, 'hass', None) is hass:
                    api.append((code, actor))

        def returned(code, offset, value):
            if code is not write_code or not transfers:
                return
            local = sys._getframe(1).f_locals
            transfer = transfers[0]
            if local.get('self') is not getattr(transfer, '_config_log', None):
                return
            kind, owner = local['kind'], local['owner']
            self.assertIs(transfer._config_log.records[-1][3], True)
            writes.append((kind, owner))
            if kind == 'dict' and owner is configured:
                root_writes.append((kind, owner))
                self.assertEqual(configured['seconds'].template, target_body)
                self.assertIsNot(configured['seconds'], leaf)
            if not after_write or injected:
                return
            edge, = transfer.duration_edges
            holder, _, after = edge.owners[-1]
            # Final write of the last ConfiguredEdge owner: all dict operations
            # and holder writes completed; graph installation has not begun.
            if not (kind == 'entry' and owner[0] is holder.binding._duration.configured_images
                    and owner[1] is holder):
                return
            self.assertEqual(len(root_writes), 1)
            self.assertIs(holder.image, after)
            self.assertIs(holder.binding._duration.configured_images[holder], after)
            self.assertEqual(graph(configured), edge.after_graph)
            self.assertEqual(configured['seconds'].template, target_body)
            edge.check_after()
            add_alias(transfer, edge)
            raise FixtureAliasAfterWriteFault('foreign alias after completed configured journal prefix')

        def raised(code, offset, error):
            frame = sys._getframe(1)
            if (code is alias_check_code and transfers
                    and frame.f_locals.get('self') is transfers[0]
                    and str(error) == 'automation_duration_foreign_mutable_alias'):
                alias_refusals.append((frame.f_back.f_code.co_name, error))

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'actual Root configured dict foreign cache alias')
        error = result = None
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, raised)
            for code in codes.values():
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            sys.monitoring.set_local_events(tool, write_code, sys.monitoring.events.PY_RETURN)
            sys.monitoring.set_local_events(tool, alias_check_code, sys.monitoring.events.RAISE)
            with mixed.native_diagnostics(self.module):
                monitors = tuple(sys.monitoring.get_tool(k) for k in range(6))
                try:
                    result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                except (RuntimeError, ValueError, self.module('transaction').Refused) as exc:
                    error = exc
                self.assertEqual(tuple(sys.monitoring.get_tool(k) for k in range(6)), monitors)
            self.assertEqual(len(roots), 1)
            self.assertEqual(len(transfers), 1, 'earlier profile/admission refusal is not this repro')
            self.assertEqual(len(injected), 1, 'exact original install / completed-write seam required')
            saved = injected[0]
            transfer, edge = saved['transfer'], saved['edge']
            self.assertEqual((graph_undos, log_undos), ([], []), 'global preundo must precede every undo')
            if after_write:
                self.assertEqual(len(root_writes), 1)
                self.assertEqual(transfer.phase, 'failed_before_attempt_cleanup_unknown')
                self.assertEqual(slots(transfer._config_log), saved['slots'])
            else:
                self.assertEqual(writes, [], 'foreign alias must refuse before first config write')
                self.assertEqual(transfer.phase, 'captured')
                self.assertIsNone(getattr(transfer, '_config_log', None))
            self.assertIsNone(result)
            self.assertIsNotNone(error, 'actual native Root must refuse this foreign alias')
            # The server may report apply_unresolved after persisting a refusal,
            # without retaining its Python exception chain. Observe the original
            # guard at the exact transfer and required boundary instead.
            self.assertTrue(any(caller == ('cleanup_plan' if after_write else 'check_before')
                                for caller, _ in alias_refusals),
                            'the original alias guard must refuse at the required boundary')
            self.assertEqual((api, actions, scripts), ([], [], []))
            self.assertIs(hass.states.get(old), physical)
            self.assertIsNone(hass.states.get(new))
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            for name, content in original.items():
                self.assertEqual((path / name).read_bytes(), content)
            self.assertIs(unrelated.script, foreign_script)
            self.assertFalse(foreign_script.is_running)
            self.assertEqual((graph(unrelated.raw_config), graph(foreign_script.sequence),
                              graph(foreign_script.variables)), foreign_before)
            for name, native_cache, entries, expected in caches_before:
                self.assertIs(getattr(foreign_script, name), native_cache)
                if name == cache_name:
                    self.assertEqual(tuple(native_cache.items()), (*entries, (alias_key, configured)))
                    self.assertIs(native_cache[alias_key], configured)
                    self.assertEqual(graph(native_cache), saved['cache'])
                else:
                    self.assertEqual(graph(native_cache), expected)
            self.assertEqual(graph(configured), saved['root'])
            self.assertEqual(graph(leaf), before_leaf)
            self.assertEqual(tuple(configured), before_keys)
            self.assertTrue(all(a is b for a, b in zip(configured, before_keys, strict=True)))
            self.assertIs(configured['milliseconds'], static)
            self.assertIs(edge.binding.config['for'], configured)
            if after_write:
                self.assertEqual(configured['seconds'].template, target_body)
            else:
                self.assertIs(configured['seconds'], leaf)
                self.assertEqual(graph(configured), before_root)
        finally:
            for code in (*codes.values(), write_code, alias_check_code):
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, None)
            sys.monitoring.free_tool_id(tool)
