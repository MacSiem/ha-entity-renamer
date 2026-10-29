"""Owner FIFO real-native tests; author has NOT run them.

Sampler tests construct an expected ORIGINAL coordinate only, not a Plan,
receipt, registry state, native source result, render result or authority. Full
accepted-chain effect integration is the root server's separate responsibility.
"""
import copy
import importlib
import hashlib
import json
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateStateBindingTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    module = server.NativeTemplateYAMLServerTests.module

    async def boot(self, *, variables=False, uidless=False, unrelated_trigger=False):
        from homeassistant.core import Context
        await self.prepare('yaml', variant='sensor', validate=False, uidless=uidless)
        old = self.source_id
        from homeassistant.const import __version__
        minimum_entity_variables = variables and __version__ == '2025.6.0'
        body = '- unique_id: scope\n'
        if variables and not minimum_entity_variables:
            body += (f'  variables:\n    source: {old}\n'
                     '    alias: "{{ source }}"\n'
                     '    stamp: "{{ now() }}"\n'
                     '    frozen: "{{ states(source) }}"\n')
        body += '  sensor:\n  - name: Runtime consumer\n'
        if minimum_entity_variables:
            body += (f'    variables:\n      source: {old}\n'
                     '      alias: "{{ source }}"\n'
                     '      stamp: "{{ now() }}"\n'
                     '      frozen: "{{ states(source) }}"\n')
        if not uidless:
            body += '    unique_id: value\n'
        source = 'alias' if variables else repr(old)
        body += (f'    state: "{{{{ states({source}) | float(0) * 2 }}}}"\n'
                 f'    availability: "{{{{ not is_state({source}, \'unavailable\') }}}}"\n'
                 '    attributes:\n      identity: "{{ this.entity_id }}"\n'
                 '      tick: "{{ now().minute }}"\n')
        if unrelated_trigger:
            body += ('- unique_id: unrelated\n  triggers:\n'
                     '  - trigger: event\n    event_type: state_binding_unrelated\n'
                     '  sensor:\n  - unique_id: untouched\n    name: Untouched trigger\n'
                     '    state: "{{ trigger.event.event_type }}"\n')
        (self.f.root / 'template.yaml').write_text(body)
        await self.f.hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        mapping = {old: 'sensor.state_binding_next'}
        plan = self.module('yaml_plan').plan_yaml(self.f.root, mapping)
        self.assertTrue(plan.ready, plan.issues)
        validation = await self.module('yaml_validation').async_validate_proposed_with_template_state_sources(
            self.f.hass, plan, mapping, self.f.root.parent, with_state_program=True)
        return validation, mapping

    async def sample_original(self, validation):
        module = self.module('native_template_state_binding')
        program = validation.state_config_program
        observed = await self.module('native_discovery_inputs').async_observe_discovery_inputs(
            self.f.hass, guard=lambda: None)
        documents = {row.key: {'data': json.loads(row.manager_json)} for row in observed.files}
        prefix = module.StateBindingPrefix(program.sha256, 'sampler-only', 'sampler-only',
            'sampler-only', frozenset(), ())
        return module.sample_state_bindings(module._StateRuntime(self.f.hass), program,
            prefix, 'original', documents)

    async def test_real_same_checker_full_descriptors_and_distinct_prefix_projection(self):
        validation, mapping = await self.boot()
        program = validation.state_config_program
        self.assertEqual(program.original_sha256, validation.original_sources.sha256)
        self.assertEqual(program.candidate_sha256, validation.candidate_sources.sha256)
        before = program.rows(frozenset())
        after = program.rows(frozenset(mapping))
        self.assertNotEqual(before, after)
        # Reversing always starts from immutable ORIGINAL, not prior output.
        self.assertEqual(program.rows(frozenset()), before)
        self.assertFalse(program.migration_ready)
        snapshot = await self.sample_original(validation)
        self.assertEqual(len(snapshot.rows), 1)
        transition = snapshot.transition_pairs(program, frozenset(mapping))
        self.assertEqual(len(transition.entities), 1)
        self.assertGreaterEqual(len(transition.source_pairs), 2)
        for pair in transition.source_pairs:
            self.assertIs(pair.template.hass, self.f.hass)
            self.assertEqual(pair.template.template, pair.before)
            self.assertIn(next(iter(mapping.values())), pair.after)
        self.assertEqual(hashlib.sha256((self.f.root / 'template.yaml').read_bytes()).hexdigest(),
                         next(p[1] for p in validation.receipt.source_files if p[0] == 'template.yaml'))

    async def test_variables_alias_snapshot_time_and_live_this_are_not_recomputed(self):
        validation, mapping = await self.boot(variables=True)
        program = validation.state_config_program
        snapshot = await self.sample_original(validation)
        entity = snapshot.rows[0].entity
        tracker = entity._template_result_info
        before = [(track.variables, dict(track.variables)) for track in tracker._track_templates]
        transition = snapshot.transition_pairs(program, frozenset(mapping))
        self.assertEqual(len(transition.configured_variables), 1)
        self.assertEqual({pair.name for pair in transition.rendered_variables}, {'source', 'alias'})
        self.assertTrue(all(pair.before == next(iter(mapping))
                            and pair.after == next(iter(mapping.values()))
                            for pair in transition.rendered_variables))
        for container, values in before:
            self.assertEqual(container, values)
            for name in ('this', 'stamp', 'frozen'):
                self.assertIs(container[name], values[name])
        self.assertIs(entity._template_result_info, tracker)
        # The frozen result has no producer locator / rendered rewrite pair.
        self.assertFalse(any(pair.name in ('stamp', 'frozen', 'this')
                             for pair in transition.rendered_variables))

    async def test_uidless_full_native_bijection_has_physical_source_identity(self):
        validation, mapping = await self.boot(uidless=True)
        snapshot = await self.sample_original(validation)
        self.assertIsNone(snapshot.rows[0].registry_id)
        self.assertIn((snapshot.rows[0].domain, snapshot.rows[0].source_key),
                      validation.state_config_program.physical_keys)
        transition = snapshot.transition_pairs(validation.state_config_program, frozenset(mapping))
        self.assertTrue(all(pair.source_identity[3] == 'template.yaml'
                            for pair in transition.source_pairs))

    async def test_config_tamper_is_not_adopted_as_source(self):
        validation, mapping = await self.boot()
        snapshot = await self.sample_original(validation)
        entity = snapshot.rows[0].entity
        # Controlled test fault; restoration is local and exact.
        template = next(t for t, paths in snapshot.rows[0].templates
                        if any(path == ('state',) for path in paths))
        original = template.template
        try:
            template.template = '{{ 123456 }}'
            with self.assertRaises(ValueError):
                await self.sample_original(validation)
        finally:
            template.template = original

    async def test_descriptor_tamper_cannot_reseal_against_v3_full_config_hash(self):
        validation, mapping = await self.boot()
        program = validation.state_config_program
        source = self.module('template_state_program')
        before, after = json.loads(program.original_json), json.loads(program.candidate_json)
        altered = copy.deepcopy(before)
        altered['rows'][0][2][0] = 'forged_state_semantics'
        plan = self.module('yaml_plan').plan_yaml(self.f.root, mapping)
        with self.assertRaises(ValueError):
            source.seal_state_config_program(validation.original_sources,
                validation.candidate_sources, altered, after, mapping=mapping, plan=plan)

    async def test_unrelated_trigger_population_is_visible_and_has_zero_pairs(self):
        validation, mapping = await self.boot(unrelated_trigger=True)
        snapshot = await self.sample_original(validation)
        self.assertTrue(any(row[0] == 'coordinator' for row in snapshot.inventory))
        untouched = [row for row in snapshot.inventory if row[0] == 'unaffected']
        self.assertEqual(len(untouched), 1)
        transition = snapshot.transition_pairs(validation.state_config_program, frozenset(mapping))
        self.assertTrue(all(entity.unique_id == 'scope-value' for entity in transition.entities))
        self.assertFalse(any(pair.source_identity[2] == 'unrelated-untouched'
                             for pair in transition.source_pairs))
