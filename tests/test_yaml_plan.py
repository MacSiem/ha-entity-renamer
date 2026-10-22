"""Synthetic fixtures only; run from this folder with unittest discovery."""
from pathlib import Path
import hashlib
import tempfile
import unittest

from custom_components.ha_entity_renamer.yaml_plan import Limits, YAMLPlanError, plan_yaml


class YAMLPlannerTests(unittest.TestCase):
    def test_named_source_replacement_during_read_is_never_a_valid_snapshot(self):
        import os
        from unittest.mock import patch
        from custom_components.ha_entity_renamer import yaml_plan
        self.put('configuration.yaml', 'entity_id: sensor.foo\n')
        replacement = self.root / 'replacement.yaml'
        replacement.write_bytes((self.root / 'configuration.yaml').read_bytes())
        original = os.fstat
        observed = []
        def fstat(fd):
            result = original(fd)
            observed.append(True)
            if len(observed) == 3:  # after-read metadata captured, before named re-open
                replacement.replace(self.root / 'configuration.yaml')
            return result
        with patch.object(yaml_plan.os, 'fstat', fstat):
            with self.assertRaisesRegex(YAMLPlanError, 'file name changed during read'):
                self.plan()

    def test_customize_entity_key_migrates_and_retains_display_settings(self):
        source = 'homeassistant:\n  customize:\n    sensor.foo:\n      friendly_name: Unchanged label\n      icon: mdi:flash\n'
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertTrue(plan.ready, plan.issues)
        self.assertEqual(plan.patches[0].proposed_bytes, source.replace('sensor.foo', 'sensor.new').encode())
        self.put('configuration.yaml', source + '    sensor.new: {}\n')
        self.assertIn('entity_key_collision', self.codes(self.plan()))

    def test_customization_glob_change_cannot_silently_drop_attributes(self):
        for pattern, blocked in (('sensor.foo*', True), ('sensor.*', False), ('light.*', False)):
            self.put('configuration.yaml', f'homeassistant:\n  customize_glob:\n    "{pattern}":\n      icon: mdi:flash\n')
            plan = self.plan()
            self.assertEqual('customization_glob_membership_changed' in self.codes(plan), blocked)

    def test_primitive_keys_preserved_with_nested_reference_coverage(self):
        source = 'modes:\n  on:\n    entity_id: sensor.foo\n  1: label\n  null: value\n'
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertTrue(plan.ready, plan.issues)
        self.assertEqual(plan.patches[0].proposed_bytes, source.replace('sensor.foo', 'sensor.new').encode())
        self.put('configuration.yaml', 'modes:\n  1: sensor.foo\n')
        self.assertIn('ambiguous_reference_context', self.codes(self.plan()))
        self.put('configuration.yaml', 'entities:\n  1: on\n')
        self.assertIn('unsupported_mapping_key', self.codes(self.plan()))

    def test_native_entity_ids_and_typed_source(self):
        source = 'sensor:\n- platform: integration\n  source: sensor.foo\n- platform: min_max\n  entity_ids: [sensor.foo, light.old]\n'
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertTrue(plan.ready, plan.issues)
        self.assertEqual(plan.patches[0].proposed_bytes, source.replace('sensor.foo', 'sensor.new').replace('light.old', 'light.new').encode())
        self.put('configuration.yaml', 'unknown:\n  source: sensor.foo\n')
        self.assertIn('ambiguous_reference_context', self.codes(self.plan()))

    def test_utility_meter_source_is_typed_through_include_forms(self):
        fixtures = (
            ('utility_meter:\n  daily:\n    source: sensor.foo\n', {}),
            ('utility_meter: !include meters.yaml\n',
             {'meters.yaml': 'daily:\n  source: sensor.foo\n'}),
            ('utility_meter: !include_dir_named meters\n',
             {'meters/daily.yaml': 'source: sensor.foo\n'}),
            ('utility_meter: !include_dir_merge_named meters\n',
             {'meters/daily.yaml': 'daily:\n  source: sensor.foo\n'}),
        )
        for root, children in fixtures:
            with self.subTest(root=root):
                self.put('configuration.yaml', root)
                for path, source in children.items(): self.put(path, source)
                plan = self.plan()
                self.assertTrue(plan.ready, plan.issues)
                self.assertEqual(len(plan.patches), 1)
                self.assertIn(b'source: sensor.new', plan.patches[0].proposed_bytes)

    def test_utility_meter_context_does_not_leak_to_nested_unknown_fields(self):
        self.put('configuration.yaml', 'utility_meter:\n  daily:\n    unrelated:\n      source: sensor.foo\n')
        self.assertIn('ambiguous_reference_context', self.codes(self.plan()))

    def test_merge_anchor_references_preserved_and_changed_once(self):
        source = 'defaults: &base\n  entity_id: sensor.foo\naction:\n  <<: *base\n  action: light.turn_on\n'
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertTrue(plan.ready, plan.issues)
        self.assertEqual(plan.patches[0].proposed_bytes, source.replace('sensor.foo', 'sensor.new').encode())
        self.assertEqual(len(plan.patches[0].edits), 1)

    def test_merge_entity_keys_cannot_hide_destination_collision(self):
        self.put('configuration.yaml', 'entities:\n  <<: &base\n    sensor.foo: on\n  sensor.new: off\n')
        self.assertIn('entity_key_collision', self.codes(self.plan()))

    def test_merge_cycle_and_invalid_scalar_source_rejected(self):
        for source in ('a: &loop\n  <<: *loop\n', 'a:\n  <<: scalar\n'):
            self.put('configuration.yaml', source)
            with self.assertRaises(YAMLPlanError): self.plan()

    def test_multiline_single_quoted_template_preserves_decoded_newlines(self):
        import yaml
        source = "value_template: '{{ states(''sensor.foo'') }}\n\n  next paragraph' # keep\n"
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertTrue(plan.ready, plan.issues)
        proposed = plan.patches[0].proposed_bytes.decode()
        self.assertEqual(yaml.safe_load(proposed)['value_template'],
                         yaml.safe_load(source)['value_template'].replace('sensor.foo', 'sensor.new'))
        self.assertIn('# keep', proposed)

    def setUp(self):
        # Owner must set TMPDIR to SSD before executing these tests.
        self.tmp = tempfile.TemporaryDirectory(prefix='renamer-yaml-test-', dir='/Volumes/Samsung_SSD' if Path('/Volumes/Samsung_SSD').is_dir() else None)
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.mapping = {'sensor.foo': 'sensor.new', 'light.old': 'light.new'}

    def put(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode('utf-8'))
        return p

    def plan(self, **kwargs):
        return plan_yaml(self.root, self.mapping, **kwargs)

    def codes(self, plan):
        return {issue.code for issue in plan.issues}

    def test_preserves_source_comments_quotes_crlf_unicode_and_anchor(self):
        source = '# żółć\r\na: &ref sensor.foo # comment\r\nentity_id: *ref\r\nentity: "light.old"\r\n'
        self.put('configuration.yaml', source)
        plan = self.plan()
        # Shared anchor has a non-reference consumer: must block and preserve it.
        self.assertIn('ambiguous_reference_context', self.codes(plan))
        self.assertEqual(plan.patches[0].proposed_bytes,
                         source.replace('"light.old"', '"light.new"').encode())
        self.assertFalse(plan.ready)
        self.assertEqual((self.root / 'configuration.yaml').read_bytes(), source.encode())

    def test_anchor_and_alias_in_reference_fields(self):
        source = 'entity: &ref sensor.foo\nentity_id: *ref\n'
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertTrue(plan.ready)
        self.assertEqual(plan.patches[0].proposed_bytes, source.replace('sensor.foo', 'sensor.new').encode())
        self.assertEqual(len(plan.patches[0].edits), 1)

    def test_include_alias_cannot_cross_reference_and_text_contexts(self):
        for source in ('entity_id: &inc !include ids.yaml\nlabel: *inc\n',
                       'label: &inc !include ids.yaml\nentity_id: *inc\n'):
            self.put('configuration.yaml', source)
            self.put('ids.yaml', 'sensor.foo\n')
            with self.assertRaises(YAMLPlanError):
                self.plan()

    def test_recursive_packages_and_all_directory_tags(self):
        tags = ('list', 'named', 'merge_list', 'merge_named')
        self.put('configuration.yaml', 'homeassistant:\n  packages: !include packages.yaml\n')
        self.put('packages.yaml', '\n'.join(f'p{i}: !include_dir_{t} dir{i}' for i, t in enumerate(tags)))
        for i in range(len(tags)):
            self.put(f'dir{i}/nested/a.yaml', 'entity_id: sensor.foo\n')
            self.put(f'dir{i}/ignored.txt', 'not yaml')
        plan = self.plan()
        self.assertEqual(len(plan.files), 6)
        self.assertEqual(len(plan.edges), 5)
        self.assertEqual(len(plan.patches), 4)
        self.assertEqual(len(plan.directories), 8)
        self.assertTrue(plan.ready)

    def test_include_inherits_reference_list_context(self):
        self.put('configuration.yaml', 'entity_id: !include ids.yaml\n')
        self.put('ids.yaml', '- sensor.foo\n- light.old\n')
        plan = self.plan()
        self.assertTrue(plan.ready)
        self.assertEqual(plan.patches[0].proposed_bytes, b'- sensor.new\n- light.new\n')

    def test_yaml_dashboards_declared_default_and_explicit(self):
        self.put('configuration.yaml', 'lovelace:\n  mode: yaml\n  dashboards: !include dashboards.yaml\n')
        self.put('dashboards.yaml', 'panel:\n  mode: yaml\n  filename: panels/main.yaml\n')
        self.put('ui-lovelace.yaml', 'cards:\n- entity: sensor.foo\n')
        self.put('panels/main.yaml', 'cards:\n- entities: [light.old]\n')
        self.put('runtime.yaml', 'entity: sensor.foo\n')
        plan = self.plan(yaml_dashboards=('runtime.yaml',))
        self.assertTrue(plan.ready)
        self.assertEqual(len(plan.files), 5)
        self.assertEqual(len(plan.patches), 3)

    def test_boundary_url_label_and_comma_list(self):
        self.put('configuration.yaml', 'entity_id: "sensor.foo, light.old, sensor.foobar"\nurl: https://example/sensor.foo\nname: sensor.foobar\n')
        plan = self.plan()
        self.assertTrue(plan.ready)
        self.assertEqual(plan.patches[0].proposed_bytes,
                         b'entity_id: "sensor.new, light.new, sensor.foobar"\nurl: https://example/sensor.foo\nname: sensor.foobar\n')

    def test_entity_keys_collision_and_duplicate_keys(self):
        self.put('configuration.yaml', 'entities:\n  sensor.foo: {}\n  sensor.new: {}\nentity_id: sensor.foo\nentity_id: light.old\n')
        plan = self.plan()
        self.assertFalse(plan.ready)
        self.assertIn('entity_key_collision', self.codes(plan))
        self.assertIn('duplicate_mapping_keys', self.codes(plan))

    def test_scene_entity_key_and_reference_values(self):
        self.put('configuration.yaml', 'scene:\n- id: unchanged\n  entities:\n    sensor.foo: "on"\n')
        plan = self.plan()
        self.assertTrue(plan.ready)
        self.assertIn(b'sensor.new:', plan.patches[0].proposed_bytes)

    def test_secret_and_dynamic_template_are_unknown_and_unchanged(self):
        source = 'entity_id: !secret entity\nvalue_template: "{{ states(prefix ~ name) }}"\n'
        self.put('configuration.yaml', source)
        self.put('secrets.yaml', 'invalid: [ never read')
        plan = self.plan()
        self.assertFalse(plan.ready)
        self.assertEqual(len(plan.files), 1)
        self.assertFalse(plan.patches)
        self.assertEqual(self.codes(plan), {'secret_reference_unresolved', 'template_requires_reference_parser'})

    def test_static_template_preserves_yaml_source_and_decoded_value(self):
        for source in [
            '''value_template: "{{ states('sensor.foo') }}" # retain\n''',
            '''value_template: |-\n  {{ states('sensor.foo') }}\n''',
            '''value_template: '{{ states(''sensor.foo'') }}' # retain\n''',
        ]:
            self.put('configuration.yaml', source)
            plan = self.plan()
            self.assertTrue(plan.ready, plan.issues)
            self.assertEqual(plan.patches[0].proposed_bytes, source.replace('sensor.foo', 'sensor.new').encode())

    def test_non_reference_secret_is_preserved_without_opening_store(self):
        source = 'password: !secret token\nentity_id: sensor.foo\n'
        self.put('configuration.yaml', source)
        self.put('secrets.yaml', 'invalid: [ intentionally unreadable')
        plan = self.plan()
        self.assertTrue(plan.ready)
        self.assertEqual(len(plan.files), 1)
        self.assertEqual(plan.patches[0].proposed_bytes, source.replace('sensor.foo', 'sensor.new').encode())

    def test_escaped_scalar_custom_tag_and_alias_cycle(self):
        self.put('configuration.yaml', 'entity_id: "sensor.\\u0066oo"\nfoo: !custom value\n')
        plan = self.plan()
        self.assertIn('escaped_or_folded_scalar_requires_span_mapper', self.codes(plan))
        self.assertIn('unsupported_yaml_tag', self.codes(plan))
        self.put('configuration.yaml', 'foo: &loop [*loop]\n')
        with self.assertRaises(YAMLPlanError):
            self.plan()

    def test_cycle_missing_traversal_and_absolute_include(self):
        for include in ('configuration.yaml', 'missing.yaml', '../outside.yaml', '/etc/passwd'):
            with self.subTest(include=include):
                self.put('configuration.yaml', f'foo: !include {include}\n')
                with self.assertRaises(YAMLPlanError):
                    self.plan()

    def test_symlink_file_directory_and_symlink_parent(self):
        self.put('actual.yaml', 'entity_id: sensor.foo\n')
        self.put('actual/x.yaml', 'entity_id: sensor.foo\n')
        (self.root / 'alias.yaml').symlink_to('actual.yaml')
        (self.root / 'aliasdir').symlink_to('actual', target_is_directory=True)
        for directive in ('!include alias.yaml', '!include aliasdir/x.yaml', '!include_dir_list aliasdir'):
            self.put('configuration.yaml', f'foo: {directive}\n')
            with self.assertRaises(YAMLPlanError):
                self.plan()
        self.put('configuration.yaml', 'foo: !include_dir_list actual\n')
        (self.root / 'actual' / 'hidden-link').symlink_to('../actual.yaml')
        with self.assertRaises(YAMLPlanError):
            self.plan()

    def test_limits_sha_and_byte_offsets(self):
        source = '# ąć\nentity_id: sensor.foo\n'
        self.put('configuration.yaml', source)
        plan = self.plan()
        patch = plan.patches[0]
        self.assertEqual(patch.expected_sha256, hashlib.sha256(source.encode()).hexdigest())
        for edit in patch.edits:
            self.assertEqual(source.encode()[edit.start:edit.end], edit.before)
        for limits in (Limits(max_file_bytes=5), Limits(max_total_bytes=5), Limits(max_nodes=1)):
            with self.assertRaises(YAMLPlanError):
                self.plan(limits=limits)

    def test_empty_graph_parse_error_has_no_source(self):
        self.put('configuration.yaml', '')
        self.assertTrue(self.plan().ready)
        self.put('configuration.yaml', 'private_value: [secret-value')
        with self.assertRaises(YAMLPlanError) as caught:
            self.plan()
        self.assertNotIn('secret-value', str(caught.exception))

    def test_repeated_include_context_mismatch_fails_closed(self):
        self.put('configuration.yaml', 'entity_id: !include ids.yaml\nlabel: !include ids.yaml\n')
        self.put('ids.yaml', 'sensor.foo\n')
        with self.assertRaises(YAMLPlanError):
            self.plan()

    def test_secret_store_cannot_be_included_and_custom_card_is_unknown(self):
        self.put('configuration.yaml', 'foo: !include secrets.yaml\n')
        self.put('secrets.yaml', 'private: never-read\n')
        with self.assertRaises(YAMLPlanError):
            self.plan()
        self.put('configuration.yaml', 'cards:\n- type: custom:unknown\n  entity: sensor.foo\n')
        plan = self.plan()
        self.assertFalse(plan.ready)
        self.assertIn('custom_card_semantics_unknown', self.codes(plan))

    def test_file_count_and_depth_caps(self):
        self.put('configuration.yaml', 'a: !include one.yaml\n')
        self.put('one.yaml', 'b: !include two.yaml\n')
        self.put('two.yaml', 'entity: sensor.foo\n')
        for limits in (Limits(max_files=1), Limits(max_depth=1)):
            with self.assertRaises(YAMLPlanError):
                self.plan(limits=limits)

    def test_mapping_validation(self):
        self.put('configuration.yaml', '')
        for mapping in ({'sensor.foo': 'light.new'}, {'sensor.foo': 'sensor.new', 'sensor.new': 'sensor.foo'},
                        {'sensor.foo': 'sensor.new', 'sensor.other': 'sensor.new'}):
            with self.assertRaises(YAMLPlanError):
                plan_yaml(self.root, mapping)


    def assert_variable_patch(self, source, expected=None):
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertTrue(plan.ready, plan.issues)
        expected = expected if expected is not None else source.replace('sensor.foo', 'sensor.new').replace('light.old', 'light.new')
        self.assertEqual(plan.patches[0].proposed_bytes, expected.encode())
        self.assertTrue(plan.variable_bindings)
        self.assertTrue(all(proof.approved for proof in plan.variable_bindings))
        for proof in plan.variable_bindings:
            self.assertEqual(proof.yaml_occurrences, proof.accounted_occurrences)
            self.assertTrue(proof.consumer_counts)
        return plan

    def test_yaml_scalar_variable_to_jinja(self):
        source = "automation:\n- variables:\n    ref: sensor.foo\n  action:\n  - value_template: \"{{ states(ref) }}\"\n"
        self.assert_variable_patch(source)

    def test_yaml_list_variable_to_expand_and_aliased_entity_ids(self):
        source = "script:\n  demo:\n    variables:\n      refs: &refs [sensor.foo, light.old]\n    sequence:\n    - value_template: \"{{ expand(refs) | list }}\"\n    - target:\n        entity_ids: *refs\n"
        plan = self.assert_variable_patch(source)
        self.assertEqual(len(plan.patches[0].edits), 2)

    def test_yaml_entity_field_can_return_native_list(self):
        source = 'script:\n  demo:\n    variables:\n      refs: [sensor.foo, light.old]\n    sequence:\n    - target:\n        entity_id: "{{ refs }}"\n'
        self.assert_variable_patch(source)

    def test_yaml_entity_key_map_requires_iteration_consumer_and_preserves_metadata(self):
        source = "script:\n  demo:\n    variables:\n      entities:\n        sensor.foo:\n          name: 'Kitchen label'\n          icon: mdi:flash\n        light.old: 'Lamp label'\n    sequence:\n    - value_template: '{% for e in entities %}{{ states(e) }}{% endfor %}'\n"
        self.assert_variable_patch(source)
        unused = source[:source.index('    sequence:')] + '    sequence: []\n'
        self.put('configuration.yaml', unused)
        plan = self.plan()
        self.assertFalse(plan.ready)
        self.assertFalse(plan.patches)
        self.assertFalse(any(p.approved for p in plan.variable_bindings))

    def test_yaml_map_key_target_collision_blocks(self):
        source = "script:\n  demo:\n    variables:\n      refs:\n        sensor.foo: Kitchen\n        sensor.new: Other\n    sequence:\n    - value_template: '{% for e in refs %}{{ states(e) }}{% endfor %}'\n"
        self.put('configuration.yaml', source)
        self.assertIn('entity_key_collision', self.codes(self.plan()))

    def test_yaml_variable_unsafe_output_prevents_origin_rewrite(self):
        source = "script:\n  demo:\n    variables:\n      ref: sensor.foo\n    sequence:\n    - value_template: '{{ states(ref) }}'\n    - data:\n        message: '{{ ref }}'\n"
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertFalse(plan.ready)
        self.assertFalse(plan.patches)
        self.assertFalse(plan.variable_bindings[0].approved)

    def test_yaml_variable_anchor_shared_with_label_blocks(self):
        source = "script:\n  demo:\n    variables:\n      ref: &ref sensor.foo\n    sequence:\n    - target:\n        entity_id: *ref\n    - alias: *ref\n    - value_template: '{{ states(ref) }}'\n"
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertFalse(plan.ready)
        self.assertFalse(plan.patches)

    def test_yaml_variable_alias_outside_supported_scope_is_accounted_as_unknown(self):
        source = "script:\n  demo:\n    variables:\n      ref: &ref sensor.foo\n    sequence:\n    - value_template: '{{ states(ref) }}'\nmetadata: *ref\n"
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertFalse(plan.ready)
        self.assertFalse(plan.patches)
        proof = plan.variable_bindings[0]
        self.assertGreater(proof.yaml_occurrences, proof.accounted_occurrences)

    def test_yaml_variable_rebinding_has_separate_origins(self):
        source = "script:\n  demo:\n    variables:\n      ref: sensor.foo\n    sequence:\n    - value_template: '{{ states(ref) }}'\n    - variables:\n        ref: light.old\n    - value_template: '{{ states(ref) }}'\n"
        self.assert_variable_patch(source)

    def test_yaml_script_scopes_do_not_leak(self):
        source = "script:\n  first:\n    variables:\n      ref: sensor.foo\n    sequence:\n    - value_template: '{{ states(ref) }}'\n  second:\n    variables:\n      ref: light.old\n    sequence:\n    - value_template: '{{ states(ref) }}'\n"
        self.assert_variable_patch(source)

    def test_yaml_empty_control_scopes_preserve_known_readonly_binding(self):
        for branch in ('choose: []', 'if: []\n      then: []',
                       'repeat: {count: 2, sequence: []}', 'parallel: []', 'sequence: []'):
            source = "script:\n  demo:\n    variables:\n      ref: sensor.foo\n    sequence:\n    - " + branch + "\n    - value_template: '{{ states(ref) }}'\n"
            self.assert_variable_patch(source)

    def test_yaml_variable_caller_include_scope_is_explicitly_unsupported(self):
        self.put('configuration.yaml', "script:\n  demo:\n    variables:\n      ref: sensor.foo\n    sequence: !include steps.yaml\n")
        self.put('steps.yaml', "- value_template: '{{ states(ref) }}'\n")
        plan = self.plan()
        self.assertIn('yaml_variable_include_scope_unsupported', self.codes(plan))
        self.assertFalse(plan.ready)
        self.assertFalse(plan.patches)

    def test_yaml_automation_include_preserves_file_provenance(self):
        self.put('configuration.yaml', 'automation: !include automations.yaml\n')
        source = "- variables:\n    ref: sensor.foo\n  actions:\n  - value_template: '{{ states(ref) }}'\n"
        self.put('automations.yaml', source)
        plan = self.plan()
        self.assertTrue(plan.ready, plan.issues)
        self.assertEqual(plan.patches[0].path, 'automations.yaml')
        self.assertEqual(plan.variable_bindings[0].path, 'automations.yaml')

    def test_yaml_variable_merge_scope_is_explicitly_unsupported(self):
        source = "script:\n  demo:\n    <<: &base {description: Keep}\n    variables:\n      ref: sensor.foo\n    sequence:\n    - value_template: '{{ states(ref) }}'\n"
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertIn('yaml_variable_merge_scope_unsupported', self.codes(plan))
        self.assertFalse(plan.patches)

    def test_yaml_unknown_nested_automation_key_does_not_create_a_scope(self):
        source = "metadata:\n  automation:\n    variables:\n      ref: sensor.foo\n    action:\n    - value_template: '{{ states(ref) }}'\n"
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertFalse(plan.ready)
        self.assertFalse(plan.patches)

    def test_yaml_unknown_variable_key_maps_cannot_use_generic_entities_context(self):
        for prefix in ('metadata:', 'script:\n  demo:\n    sequence:\n    - choose:\n      - conditions: []\n        sequence:'):
            if prefix == 'metadata:':
                source = prefix + '\n  variables:\n    entities:\n      sensor.foo: Label\n'
            else:
                source = prefix + '\n        - variables:\n            entities:\n              sensor.foo: Label\n'
            self.put('configuration.yaml', source)
            plan = self.plan()
            self.assertFalse(plan.ready)
            self.assertFalse(plan.patches)

    def test_yaml_response_variable_rebinding_is_explicit(self):
        source = "script:\n  demo:\n    variables:\n      ref: sensor.foo\n    sequence:\n    - action: example.lookup\n      response_variable: ref\n    - value_template: '{{ states(ref) }}'\n"
        self.put('configuration.yaml', source)
        plan = self.plan()
        self.assertIn('yaml_response_variable_rebinding_unsupported', self.codes(plan))
        self.assertFalse(plan.patches)

    def control_source(self, steps, definitions='ref: sensor.foo'):
        from textwrap import dedent, indent
        return ('script:\n  demo:\n    variables:\n'
                + indent(definitions + '\n', '      ')
                + '    sequence:\n' + indent(dedent(steps).strip() + '\n', '    '))

    def assert_control_blocked(self, source, code=None):
        self.put('configuration.yaml', source)
        result = self.plan()
        self.assertFalse(result.ready)
        if code:
            self.assertIn(code, self.codes(result))
        for proof in result.variable_bindings:
            if proof.entity_id == 'sensor.foo':
                self.assertFalse(proof.approved)
        return result

    def test_choose_and_if_union_static_branch_writes_with_entry(self):
        for steps in ('''
            - choose:
              - conditions: []
                sequence:
                - variables:
                    ref: light.old
              default: []
            - value_template: '{{ states(ref) }}'
        ''', '''
            - if: []
              then:
              - variables:
                  ref: light.old
              else: []
            - value_template: '{{ states(ref) }}'
        '''):
            result = self.assert_variable_patch(self.control_source(steps))
            self.assertEqual(len(result.variable_bindings), 2)
            self.assertTrue(all(p.producer_occurrences == 1 for p in result.variable_bindings))

    def test_branch_defined_variable_is_visible_in_later_steps_when_proven(self):
        source = self.control_source('''
            - choose:
              - conditions: []
                sequence:
                - variables:
                    inner: light.old
                - value_template: '{{ states(inner) }}'
            - value_template: '{{ states(ref) }}'
        ''')
        self.assert_variable_patch(source)

    def test_branch_defined_name_may_be_undefined_after_join(self):
        source = self.control_source('''
            - choose:
              - conditions: []
                sequence:
                - variables:
                    inner: sensor.foo
            - value_template: '{{ states(inner) }}'
        ''', definitions='untouched: 1')
        self.assert_control_blocked(source)

    def test_dynamic_branch_write_keeps_old_origin_unsafe(self):
        source = self.control_source('''
            - value_template: '{{ states(ref) }}'
            - if: []
              then:
              - variables:
                  ref: '{{ runtime_value }}'
            - value_template: '{{ states(ref) }}'
        ''')
        self.assert_control_blocked(source)

    def test_label_escape_in_one_branch_blocks_shared_origin(self):
        source = self.control_source('''
            - if: []
              then:
              - value_template: '{{ states(ref) }}'
              else:
              - data:
                  message: '{{ ref }}'
        ''')
        self.assert_control_blocked(source)

    def test_shape_mismatch_at_branch_join_retains_both_origins(self):
        source = self.control_source('''
            - if: []
              then:
              - variables:
                  ref: [light.old]
            - value_template: '{{ expand(ref) }}'
        ''')
        result = self.assert_control_blocked(source)
        self.assertEqual(len(result.variable_bindings), 2)
        self.assertFalse(any(p.approved for p in result.variable_bindings))

    def test_nested_sequence_writes_propagate_through_choose_and_if(self):
        source = self.control_source('''
            - choose:
              - conditions: []
                sequence:
                - if: []
                  then:
                  - sequence:
                    - variables:
                        ref: light.old
            - value_template: '{{ states(ref) }}'
        ''')
        self.assert_variable_patch(source)

    def test_alias_shared_with_branch_label_remains_blocked(self):
        source = self.control_source('''
            - if: []
              then:
              - value_template: '{{ states(ref) }}'
              else:
              - alias: *ref
        ''', definitions='ref: &ref sensor.foo')
        self.assert_control_blocked(source)

    def test_readonly_repeat_count_and_while_support_known_outer_reference(self):
        for header in ('count: 2', 'while: []', 'until: []'):
            source = self.control_source('''
                - repeat:
                    HEADER
                    sequence:
                    - value_template: '{{ states(ref) }}'
            '''.replace('HEADER', header))
            self.assert_variable_patch(source)

    def test_repeat_invariant_sees_previous_iteration_static_write(self):
        source = self.control_source('''
            - repeat:
                count: 2
                sequence:
                - value_template: '{{ states(ref) }}'
                - variables:
                    ref: light.old
            - value_template: '{{ states(ref) }}'
        ''')
        self.assert_variable_patch(source)

    def test_repeat_invariant_catches_label_escape_before_later_iteration_write(self):
        source = self.control_source('''
            - repeat:
                count: 2
                sequence:
                - data:
                    message: '{{ ref }}'
                - variables:
                    ref: light.old
                - value_template: '{{ states(ref) }}'
        ''')
        result = self.assert_control_blocked(source)
        self.assertFalse(any(p.approved for p in result.variable_bindings))

    def test_repeat_unknown_write_blocks_old_origin(self):
        source = self.control_source('''
            - repeat:
                count: 2
                sequence:
                - variables:
                    ref: '{{ runtime_value }}'
            - value_template: '{{ states(ref) }}'
        ''')
        self.assert_control_blocked(source)

    def test_repeat_runtime_counters_do_not_block_static_entity_migration(self):
        for expression in ("repeat.index", "repeat.first", "repeat.last", 'repeat["index"]'):
            source = self.control_source("""
                - repeat:
                    count: 2
                    sequence:
                    - value_template: '{{ EXPRESSION }}:{{ states(ref) }}'
            """.replace('EXPRESSION', expression))
            self.assert_variable_patch(source)

    def test_runtime_repeat_item_and_dynamic_access_have_explicit_block(self):
        for expression in ('states(repeat.item)', 'repeat.item', 'repeat["item"]', 'repeat[key]', 'repeat'):
            source = self.control_source('''
                - repeat:
                    count: 2
                    sequence:
                    - value_template: '{{ EXPRESSION }}'
                - value_template: '{{ states(ref) }}'
            '''.replace('EXPRESSION', expression))
            self.put('configuration.yaml', source)
            result = self.plan()
            self.assertFalse(result.ready)
            self.assertIn('yaml_runtime_repeat_variable_unsupported', self.codes(result))

    def test_repeat_runtime_object_shadows_and_restores_outer_same_name(self):
        source = self.control_source('''
            - repeat:
                count: 2
                sequence: []
            - value_template: '{{ states(repeat) }}'
        ''', definitions='repeat: sensor.foo')
        self.assert_variable_patch(source)

    def test_parallel_readonly_branches_support_known_outer_reference(self):
        source = self.control_source('''
            - parallel:
              - value_template: '{{ states(ref) }}'
              - sequence:
                - target:
                    entity_id: '{{ ref }}'
        ''')
        self.assert_variable_patch(source)

    def test_parallel_static_writes_union_all_interleavings(self):
        source = self.control_source('''
            - parallel:
              - variables:
                  ref: light.old
              - sequence:
                - value_template: '{{ states(ref) }}'
            - value_template: '{{ states(ref) }}'
        ''')
        self.assert_variable_patch(source)

    def test_parallel_interference_after_own_assignment_catches_sibling_origin_escape(self):
        source = self.control_source('''
            - parallel:
              - sequence:
                - variables:
                    ref: sensor.foo
                - data:
                    message: '{{ ref }}'
              - sequence:
                - variables:
                    ref: light.old
                - value_template: '{{ states(ref) }}'
        ''')
        result = self.assert_control_blocked(source)
        self.assertFalse(any(p.approved for p in result.variable_bindings))

    def test_parallel_dynamic_write_blocks_prior_reference_producer(self):
        source = self.control_source('''
            - parallel:
              - variables:
                  ref: '{{ runtime_value }}'
              - value_template: '{{ states(ref) }}'
        ''')
        self.assert_control_blocked(source)

    def test_disabled_assignment_keeps_entry_value_possible(self):
        source = self.control_source('''
            - variables:
                ref: light.old
              enabled: false
            - value_template: '{{ states(ref) }}'
        ''')
        self.assert_variable_patch(source)

    def test_enabled_expression_reads_binding_before_assignment(self):
        source = self.control_source('''
            - value_template: '{{ states(ref) }}'
            - variables:
                ref: light.old
              enabled: '{{ ref }}'
            - value_template: '{{ states(ref) }}'
        ''')
        self.assert_control_blocked(source)

    def test_parallel_response_write_is_unknown_with_origin_preserved(self):
        source = self.control_source('''
            - parallel:
              - action: example.lookup
                response_variable: ref
              - value_template: '{{ states(ref) }}'
        ''')
        self.assert_control_blocked(source, 'yaml_response_variable_rebinding_unsupported')

    def test_branch_include_still_fails_closed_for_caller_bindings(self):
        source = self.control_source('''
            - if: []
              then: !include steps.yaml
            - value_template: '{{ states(ref) }}'
        ''')
        self.put('steps.yaml', "- value_template: '{{ states(ref) }}'\n")
        self.assert_control_blocked(source, 'yaml_variable_include_scope_unsupported')


    def test_wait_runtime_overwrite_preserves_origin_and_stays_explicit(self):
        source = self.control_source("""
            - value_template: '{{ states(wait) }}'
            - wait_template: '{{ true }}'
            - value_template: '{{ states(wait) }}'
        """, definitions='wait: sensor.foo')
        self.assert_control_blocked(source, 'yaml_runtime_variable_rebinding_unsupported')

if __name__ == '__main__':
    unittest.main()
