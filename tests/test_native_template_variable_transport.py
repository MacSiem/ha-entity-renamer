"""Actual native variable transport; data plane only, never root/AC admission."""
import asyncio
import importlib
import sys
import unittest

import test_native_template_state_binding as fixture


@unittest.skipUnless(fixture.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateVariableTransportTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = fixture.NativeTemplateStateBindingTests.asyncSetUp
    _before_start = fixture.NativeTemplateStateBindingTests._before_start
    _flush_documents = fixture.NativeTemplateStateBindingTests._flush_documents
    prepare = fixture.NativeTemplateStateBindingTests.prepare
    module = fixture.NativeTemplateStateBindingTests.module
    boot = fixture.NativeTemplateStateBindingTests.boot
    sample_original = fixture.NativeTemplateStateBindingTests.sample_original

    async def actual_pairs(self):
        validation, mapping = await self.boot(variables=True)
        snapshot = await self.sample_original(validation)
        transition = snapshot.transition_pairs(validation.state_config_program, frozenset(mapping))
        transport = self.module('native_template_binding_transport')
        configured = tuple(transport.BindingVariablePair(p.container, p.name, p.before, p.after,
            p.source_identity) for p in transition.configured_variables)
        rendered = tuple(transport.BindingVariablePair(p.variables, p.name, p.before, p.after,
            p.source_identity) for p in transition.rendered_variables)
        # Read-only pairs alone never authorize a root mutation. This fixture
        # exercises the data plane with its actual task/registry/source scope.
        old, new = next(iter(mapping.items()))
        identity = self.f.entity.async_get(old).id
        original = (self.f.root / 'template.yaml').read_bytes()
        def fence(phase, sources, entities):
            row = self.f.entity.async_get(self.producer.entity_id)
            return (entities == transition.entities and sources == transition.source_pairs
                and row is not None and row.id == identity
                and (self.f.root / 'template.yaml').read_bytes() == original)
        context = transport._BindingCallerContext(asyncio.current_task(), fence)
        kwargs = dict(source_pairs=transition.source_pairs, caller_context=context,
            configured_variable_pairs=configured, rendered_variable_pairs=rendered)
        return transport, transition, context, kwargs, old, new

    async def test_configured_and_rendered_aliases_preserve_native_snapshots_without_variable_render(self):
        module, transition, context, kwargs, old, new = await self.actual_pairs()
        from homeassistant.helpers.script_variables import ScriptVariables
        entity = transition.entities[0]
        prior = entity._template_result_info
        old_variables = [t.variables for t in prior._track_templates]
        snapshots = {name: old_variables[0][name] for name in ('this', 'stamp', 'frozen')}
        configured_old = entity._run_variables
        physical = self.f.hass.states.get(entity.entity_id)
        code = ScriptVariables.async_render.__code__
        calls = []
        tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(tool, 'actual native variables must not rerender at cutover')
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START,
                                        lambda actual, offset: calls.append(actual))
        sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
        try:
            transfer = module.NativeTemplateBindingTransport.capture(
                self.f.hass, transition.entities, **kwargs)
            transfer.install_before_registry(context)
            self.assertFalse(calls)
            self.assertIs(self.f.hass.states.get(entity.entity_id), physical)
            self.assertIsNot(entity._run_variables, configured_old)
            configured = entity._run_variables.as_dict() if type(entity._run_variables) is ScriptVariables else entity._run_variables
            self.assertEqual(configured['source'], new)
            for track in entity._template_result_info._track_templates:
                self.assertEqual((track.variables['source'], track.variables['alias']), (new, new))
                for name, value in snapshots.items():
                    self.assertIs(track.variables[name], value)
            for variables in old_variables:
                self.assertEqual((variables['source'], variables['alias']), (old, old))
            transfer.mark_registry_attempt(context)
            self.f.entity.async_update_entity(old, new_entity_id=new)
            await self.f.hass.async_block_till_done()
            for value in (5, 7):
                self.producer._attr_native_value = value
                self.producer.async_write_ha_state()
                await self.f.hass.async_block_till_done()
                from decimal import Decimal
                self.assertEqual(Decimal(self.f.hass.states.get(entity.entity_id).state), Decimal(value * 2))
                for track in entity._template_result_info._track_templates:
                    for name, initial in snapshots.items():
                        self.assertIs(track.variables[name], initial)
            self.assertFalse(calls)
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)

    async def test_pre_effect_rollback_restores_exact_configured_and_rendered_containers(self):
        module, transition, context, kwargs, old, new = await self.actual_pairs()
        entity = transition.entities[0]
        tracker, configured = entity._template_result_info, entity._run_variables
        variables = tuple(t.variables for t in tracker._track_templates)
        physical = self.f.hass.states.get(entity.entity_id)
        transfer = module.NativeTemplateBindingTransport.capture(self.f.hass, transition.entities, **kwargs)
        transfer.install_before_registry(context)
        transfer.rollback_before_registry(context)
        self.assertIs(entity._template_result_info, tracker)
        self.assertIs(entity._run_variables, configured)
        self.assertEqual(tuple(id(t.variables) for t in tracker._track_templates), tuple(map(id, variables)))
        self.assertIs(self.f.hass.states.get(entity.entity_id), physical)
        self.assertIsNotNone(self.f.entity.async_get(old))
        self.assertIsNone(self.f.entity.async_get(new))

    async def test_foreign_container_and_changed_before_value_refuse_before_binding_effect(self):
        module, transition, context, kwargs, old, new = await self.actual_pairs()
        entity = transition.entities[0]
        tracker, configured = entity._template_result_info, entity._run_variables
        physical = self.f.hass.states.get(entity.entity_id)
        pair = kwargs['configured_variable_pairs'][0]
        for container, before in (({'source': old}, old), (pair.container, 'sensor.wrong_before')):
            bad = dict(kwargs, configured_variable_pairs=(module.BindingVariablePair(
                container, pair.name, before, new, pair.source_identity),))
            with self.assertRaises(ValueError):
                module.NativeTemplateBindingTransport.capture(self.f.hass, transition.entities, **bad)
            self.assertIs(entity._template_result_info, tracker)
            self.assertIs(entity._run_variables, configured)
            self.assertIs(self.f.hass.states.get(entity.entity_id), physical)
