"""Whole actual Root Automation+Script+Template forward / fresh inverse.

Fixture-only source mutation at genuine handoff via sys.monitoring. No Core
method/return patch, fake State/Event, direct async_trigger or request mode flag.
Run through the owner's FIFO against BOTH pinned Core images.
"""
import sys
import unittest
import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationPrefixRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_meaningful_17_to_29_native_actions_once_and_fresh_inverse(self):
        await self._root_case(meaningful=True)

    async def test_neutral_native_rename_has_no_actions_and_fresh_inverse(self):
        await self._root_case(meaningful=False)

    async def test_foreign_native_script_service_replacement_after_effect_is_unknown(self):
        await self._root_case(meaningful=False, foreign_service=True)

    async def _root_case(self, *, meaningful, foreign_service=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        automation_path = root / 'mixed_automations.yaml'
        automation_path.write_text(
            '- id: mixed_source\n  alias: Prefix synthetic observer\n  initial_state: true\n'
            '  variables:\n    source: ' + old + '\n    alias: "{{ source }}"\n'
            '  triggers:\n  - trigger: state\n    id: physical_pair\n    entity_id: ' + old + '\n'
            '    from: "17"\n    to: "29"\n'
            '  actions:\n  - event: automation_prefix_pair\n    event_data:\n'
            '      previous: "{{ trigger.from_state.state }}"\n'
            '      current: "{{ trigger.to_state.state }}"\n'
            '      configured: "{{ states(alias) }}"\n'
            '  - action: script.mixed_source\n')
        script_path = root / 'mixed_scripts.yaml'
        script_path.write_text(
            'mixed_source:\n  variables:\n    source: ' + old + '\n'
            '  sequence:\n  - event: automation_prefix_script\n    event_data:\n'
            '      value: "{{ states(source) }}"\n')
        for domain in ('script', 'automation'):
            await hass.services.async_call(domain, 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        for name in ('mixed_automations.yaml', 'mixed_scripts.yaml'):
            original[name] = (root / name).read_bytes()
        await self.stimulus(17)
        await self._flush_documents()
        physical = hass.states.get(old)
        self.assertEqual(physical.state, '17')
        automation, = hass.data[DATA_INSTANCES]['automation'].entities
        script_entity, = hass.data[DATA_INSTANCES]['script'].entities
        action_script, root_script = automation.action_script, script_entity.script
        raw, config = automation.raw_config, automation._trigger_config
        binding = (await self.module('native_automation_state_binding').async_capture_state_bindings(
            hass, automation, guard=lambda: None))[0]
        native_objects = (binding.keyed_job, binding.listener, binding.action_job, binding.unsubscribe,
                          binding.detach, binding.remove, binding.original_keys, binding.callbacks)
        service = hass.services._services['script']['mixed_source']
        actions, scripts, pairs, handoffs, roots = [], [], [], [], []
        self.addCleanup(hass.bus.async_listen('automation_prefix_pair', actions.append))
        self.addCleanup(hass.bus.async_listen('automation_prefix_script', scripts.append))
        handoff_type = self.module('native_source_handoff').NativeSourceHandoff
        ingress_type = self.module('native_automation_owned_graph').OwnedAutomationGraph
        root_type = self.module('native_template_state_server').NativeTemplateStateServer
        phase_type = self.module('native_automation_prefix_phase').NativeAutomationPrefixPhase
        codes = (handoff_type.mutate.__code__, ingress_type._action.__code__, root_type._install.__code__,
                 phase_type.finish_native.__code__)
        replaced = []
        tool = next(key for key in range(6) if sys.monitoring.get_tool(key) is None)
        sys.monitoring.use_tool_id(tool, 'whole Automation prefix native acceptance')
        def started(code, offset):
            frame = sys._getframe(1)
            actor = frame.f_locals.get('self')
            if code is codes[0] and actor.hass is hass:
                handoffs.append(actor)
                if len(handoffs) == 1:
                    self.assertIs(hass.states.get(old), physical)
                    self.assertEqual(physical.state, '17')
                    actor.source._attr_native_value = 29 if meaningful else 17
                    self.assertIs(hass.states.get(old), physical, 'configured value stays unemitted')
            elif code is codes[1] and actor.hass is hass:
                change = frame.f_locals['change']
                pairs.append(change)
                self.assertIs(change.event.data['old_state'], None, 'genuine ADD remains None to NEW')
                self.assertIs(change.prior_state, physical)
                self.assertIs(change.new_state, change.event.data['new_state'])
                self.assertIsNone(hass.states.get(old), 'OLD namespace removed before own delivery')
            elif code is codes[2] and actor.hass is hass:
                roots.append(actor)
            elif code is codes[3] and actor.root.hass is hass and foreign_service and not replaced:
                # Real native ServiceRegistry API, same legitimate handler and
                # schema, different Service/HassJob. No verification substitute.
                hass.services.async_register('script', 'mixed_source', script_entity._service_handler,
                    schema=service.schema, supports_response=service.supports_response)
                replaced.append(hass.services._services['script']['mixed_source'])
                self.assertIsNot(replaced[0], service)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            with mixed.native_diagnostics(self.module):
                result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                if foreign_service:
                    self.assertEqual(len(replaced), 1)
                    self.assertEqual(result['state'], 'unknown')
                    self.assertEqual(len(handoffs), 1, 'uncertain effect must never be replayed')
                    self.assertIs(hass.services._services['script']['mixed_source'], replaced[0])
                    self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
                    self.assertIsNone(self.f.entity.async_get(old))
                    for name, content in original.items():
                        self.assertEqual((root / name).read_bytes(), content)
                    self.assertFalse(runtime.operation_lock.locked())
                    return
                self.assertEqual(result['state'], 'applied')
                record = await runtime.journal.load(result['operation_id'])
                baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
                self.assertIs(type(composition), self.module('native_automation_program_composition').AutomationTemplateStateComposition)
                self.assertEqual(composition.dependent_config_program.source_order, (old,))
                self.assertIs(composition.migration_ready, False)
                self.assertIs(result['family_complete'], False)
                self.assertIs(automation.action_script, action_script)
                self.assertIs(automation.raw_config, raw)
                self.assertIs(automation._trigger_config, config)
                self.assertIs(script_entity.script, root_script)
                self.assertIs(hass.services._services['script']['mixed_source'], service)
                current = (await self.module('native_automation_state_binding').async_capture_state_bindings(
                    hass, automation, guard=lambda: None))[0]
                self.assertEqual((current.keyed_job, current.listener, current.action_job, current.unsubscribe,
                    current.detach, current.remove, current.original_keys, current.callbacks), native_objects)
                self.assertIsNot(current.config_ids, current.original_keys)
                self.assertEqual(current.original_keys, [new])
                self.assertNotIn(old, current.callbacks)
                self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(old))
                self.assertEqual(hass.states.get(new).state, '29' if meaningful else '17')
                if meaningful:
                    self.assertEqual(len(actions), 1)
                    self.assertEqual({key: str(value) for key, value in actions[0].data.items()},
                        dict(previous='17', current='29', configured='29'))
                    self.assertEqual([str(event.data['value']) for event in scripts], ['29'])
                    self.assertEqual(len(pairs), 1)
                    self.assertEqual(actions[0].context.parent_id, pairs[0].event.context.id)
                else:
                    self.assertEqual((actions, scripts, pairs), ([], [], []))
                for platform in async_get_platforms(hass, 'template'):
                    for entity in platform.entities.values():
                        expected = 'off' if platform.domain == 'binary_sensor' else str((29 if meaningful else 17) * 2)
                        self.assertEqual(hass.states.get(entity.entity_id).state, expected)
                original_ref, receipt_json = record.plan.native_inputs, baseline.receipt_json
                reverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                self.assertEqual(reverse['state'], 'rolled_back')
                self.assertEqual(len(handoffs), 2)
                self.assertIsNot(handoffs[0], handoffs[1])
                # The inverse is a fresh Root reader/writer, never same live chain replay.
                self.assertEqual(len(roots), 2)
                self.assertIsNot(roots[0], roots[1])
                self.assertIsNot(roots[0].chain, roots[1].chain)
                reopened, restored = runtime.preparations.resolve_composition(original_ref)
                self.assertEqual((reopened.receipt_json, restored), (receipt_json, composition))
                self.assertEqual((len(actions), len(scripts)), (1, 1) if meaningful else (0, 0))
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
                self.assertIs(automation.action_script, action_script)
                self.assertIs(script_entity.script, root_script)
                for name, content in original.items():
                    self.assertEqual((root / name).read_bytes(), content)
                self.assertFalse(runtime.operation_lock.locked())
        finally:
            for code in codes:
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)

    async def test_opaque_service_call_is_refused_before_registry_effect(self):
        from homeassistant.core import Context
        runtime, mapping, uuids, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        path = root / 'mixed_scripts.yaml'
        path.write_text('mixed_source:\n  sequence:\n  - action: automation.trigger\n'
            '    target: {entity_id: automation.mixed_source_observer}\n'
            '  - event: automation_prefix_script\n    event_data:\n'
            '      value: "{{ states(' + repr(old) + ') }}"\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        original['mixed_scripts.yaml'] = path.read_bytes()
        await self.stimulus(17)
        await self._flush_documents()
        code = self.module('native_registry').NativeRegistryIO.mutate.__code__
        calls = []
        tool = next(key for key in range(6) if sys.monitoring.get_tool(key) is None)
        sys.monitoring.use_tool_id(tool, 'opaque Automation call zero-effect admission')
        def started(code, offset):
            actor = sys._getframe(1).f_locals.get('self')
            if actor.hass is hass:
                calls.append(actor)
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            async def refresh():
                pass
            with self.assertRaises(self.module('transaction').Refused):
                await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            self.assertEqual(calls, [])
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            for name, content in original.items():
                self.assertEqual((root / name).read_bytes(), content)
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
