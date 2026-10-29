"""Actual YAML/Jinja planner regressions; artifact authored, execution owned by parent.

No native consumer/runtime capability follows from these source-only assertions.
"""
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from custom_components.ha_entity_renamer.yaml_plan import plan_yaml


class TemplateStateBlockVariablePlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='renamer-template-vars-',
            dir='/Volumes/Samsung_SSD' if Path('/Volumes/Samsung_SSD').is_dir() else None)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mapping = {'sensor.foo': 'sensor.new', 'light.old': 'light.new'}

    def put(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())

    def plan(self, source):
        self.put('configuration.yaml', source)
        return plan_yaml(self.root, self.mapping)

    def assert_proposal(self, source, expected, *, path='configuration.yaml'):
        plan = self.plan(source)
        self.assertTrue(plan.ready, plan.issues)
        self.assertEqual(len(plan.patches), 1)
        self.assertEqual(plan.patches[0].path, path)
        self.assertEqual(plan.patches[0].proposed_bytes, expected.encode())
        self.assertEqual((self.root / 'configuration.yaml').read_bytes(), source.encode())
        self.assertTrue(plan.variable_bindings)
        self.assertTrue(all(p.approved for p in plan.variable_bindings))
        self.assertTrue(all(p.yaml_occurrences == p.accounted_occurrences for p in plan.variable_bindings))
        return plan

    def assert_blocked(self, source, *, code=None):
        plan = self.plan(source)
        self.assertFalse(plan.ready)
        self.assertFalse(plan.patches)
        self.assertFalse(any(p.approved for p in plan.template_variable_bindings))
        if code:
            self.assertIn(code, {issue.code for issue in plan.issues})
        return plan

    def test_scalar_alias_closure_spans_all_sensor_binary_slots_and_snapshots(self):
        source = '''# retain comment and quoting
 template:
 - unique_id: shared
   variables:
     source: 'sensor.foo' # only changed scalar
     alias: "{{ source }}"
     frozen: "{{ states(source) }}"
     stamp: "{{ now() }}"
     memory: "{{ this.attributes.get('count', 0) }}"
   sensor:
   - unique_id: output
     state: "{{ states(alias) | float(0) }}"
     availability: "{{ not is_state(source, 'unavailable') }}"
     name: "{{ states(source) }}"
     icon: "{{ state_attr(alias, 'icon') }}"
     picture: "{{ state_attr(source, 'entity_picture') }}"
     attributes:
       value: "{{ states(source) }}"
       frozen_value: "{{ frozen }}"
       timestamp: "{{ stamp }}"
       memory: "{{ memory }}"
   binary_sensor:
   - unique_id: delayed
     state: "{{ states(alias) | float(0) > 0 }}"
     delay_on: "{{ states(source) | int(0) }}"
     delay_off: "{{ states(alias) | int(0) }}"
'''
        expected = source[:source.index("'sensor.foo'")] + "'sensor.new'" + source[source.index("'sensor.foo'") + len("'sensor.foo'"):]
        # Compile/render must never be a substitute for producer/source analysis.
        with patch('jinja2.Environment.compile', side_effect=AssertionError('source planner compiled a template')):
            plan = self.assert_proposal(source, expected)
        self.assertEqual(len(plan.patches[0].edits), 1)
        specs = {row.name: row for row in plan.template_variable_bindings}
        self.assertEqual(set(specs), {'source', 'alias'})
        self.assertEqual({r.before for r in specs.values()}, {'sensor.foo'})
        self.assertEqual({r.after for r in specs.values()}, {'sensor.new'})
        self.assertEqual(specs['source'].kind, 'literal')
        self.assertEqual(specs['alias'].kind, 'alias')
        self.assertTrue(all(r.approved for r in specs.values()))
        self.assertEqual(specs['source'].producer_start, specs['alias'].producer_start)
        self.assertEqual(specs['source'].producer_end, specs['alias'].producer_end)
        data = source.encode()
        self.assertEqual(data[specs['source'].source_start:specs['source'].source_end], b"'sensor.foo'")
        self.assertEqual(data[specs['alias'].source_start:specs['alias'].source_end], b'"{{ source }}"')
        for row in specs.values():
            self.assertEqual(row.source_sha256, hashlib.sha256(data[row.source_start:row.source_end]).hexdigest())
        with self.assertRaises(FrozenInstanceError):
            specs['source'].approved = False

    def test_scalar_alias_chain_preserves_configured_expressions(self):
        source = '''template:
- variables:
    source: sensor.foo
    first: '{{ source }}'
    second: '{{ first }}'
  sensor:
  - state: '{{ states(second) }}'
'''
        expected = '''template:
- variables:
    source: sensor.new
    first: '{{ source }}'
    second: '{{ first }}'
  sensor:
  - state: '{{ states(second) }}'
'''
        plan = self.assert_proposal(source, expected)
        self.assertEqual({r.name for r in plan.template_variable_bindings}, {'source', 'first', 'second'})
        self.assertEqual({r.producer_start for r in plan.template_variable_bindings}, {source.encode().index(b'sensor.foo')})

    def test_list_and_map_producers_reuse_existing_reference_dataflow(self):
        cases = (
            ('''    refs: [sensor.foo, light.old]
''', '''{{ expand(refs) | list }}''', '''    refs: [sensor.new, light.new]
'''),
            ('''    refs:
      sensor.foo: Kitchen
      light.old: Lamp
''', '''{% for ref in refs %}{{ states(ref) }}{% endfor %}''', '''    refs:
      sensor.new: Kitchen
      light.new: Lamp
'''),
        )
        for definitions, expression, proposed in cases:
            with self.subTest(expression=expression):
                source = 'template:\n- variables:\n' + definitions + '  sensor:\n  - state: "' + expression + '"\n'
                expected = 'template:\n- variables:\n' + proposed + '  sensor:\n  - state: "' + expression + '"\n'
                plan = self.assert_proposal(source, expected)
                # Scalar-only locators do not claim order/element proofs for containers.
                self.assertEqual(plan.template_variable_bindings, ())

    def test_second_sibling_unsafe_label_blocks_shared_producer(self):
        source = '''template:
- variables:
    source: sensor.foo
  sensor:
  - state: '{{ states(source) }}'
  binary_sensor:
  - state: '{{ is_state(source, "on") }}'
    name: '{{ source }}'
'''
        self.assert_blocked(source, code='ambiguous_reference_context')

    def test_attribute_named_entity_id_is_an_output_not_a_reference_field(self):
        source = '''template:
- variables:
    source: sensor.foo
  sensor:
  - state: '{{ states(source) }}'
    attributes:
      entity_id: '{{ source }}'
'''
        self.assert_blocked(source, code='ambiguous_reference_context')

    def test_template_variable_name_does_not_authorize_literal_slot_output(self):
        source = '''template:
- variables:
    source: sensor.foo
  sensor:
  - state: '{{ states(source) }}'
    attributes:
      entity_id: sensor.foo
'''
        plan = self.plan(source)
        self.assertFalse(plan.ready)
        self.assertIn('ambiguous_reference_context', {i.code for i in plan.issues})
        self.assertFalse(any(r.approved for r in plan.template_variable_bindings))

    def test_unknown_alias_and_dynamic_sink_are_refused(self):
        for expression in ("{{ states(runtime_name) }}", "{{ states(source ~ suffix) }}"):
            with self.subTest(expression=expression):
                source = 'template:\n- variables:\n    source: sensor.foo\n  sensor:\n  - state: "' + expression + '"\n'
                self.assert_blocked(source)

    def test_forward_alias_cannot_learn_identity_from_later_definition(self):
        source = '''template:
- variables:
    alias: '{{ source }}'
    source: sensor.foo
  sensor:
  - state: '{{ states(alias) }}'
'''
        self.assert_blocked(source)

    def test_alias_unsafe_use_in_configured_variables_is_in_closure(self):
        source = '''template:
- variables:
    source: sensor.foo
    label: 'source={{ source }}'
  sensor:
  - state: '{{ states(source) }}'
'''
        self.assert_blocked(source)

    def test_native_global_shadow_is_explicitly_refused(self):
        for name in ('states', 'this', 'now'):
            with self.subTest(name=name):
                source = 'template:\n- variables:\n    source: sensor.foo\n    ' + name + ': 0\n  sensor:\n  - state: "{{ states(source) }}"\n'
                self.assert_blocked(source, code='yaml_template_variable_shadow_unsupported')

    def test_trigger_action_condition_and_other_platform_are_explicitly_refused(self):
        for extra in ('  triggers: []\n', '  trigger: []\n', '  actions: []\n',
                      '  action: []\n', '  conditions: []\n', '  button: []\n'):
            with self.subTest(extra=extra):
                source = 'template:\n- variables:\n    source: sensor.foo\n' + extra + '  sensor:\n  - state: "{{ states(source) }}"\n'
                self.assert_blocked(source, code='yaml_template_variable_runtime_scope_unsupported')

    def test_entity_local_variables_do_not_inherit_block_authorization(self):
        source = '''template:
- variables:
    source: sensor.foo
  sensor:
  - variables:
      source: light.old
    state: '{{ states(source) }}'
'''
        self.assert_blocked(source, code='yaml_template_variable_entity_scope_unsupported')

    def test_entity_include_with_block_variables_is_explicitly_refused(self):
        self.put('entities.yaml', '- state: "{{ states(source) }}"\n')
        self.assert_blocked('template:\n- variables:\n    source: sensor.foo\n  sensor: !include entities.yaml\n',
            code='yaml_variable_include_scope_unsupported')

    def test_included_variable_definition_is_not_a_scope_proof(self):
        self.put('variables.yaml', 'source: sensor.foo\n')
        self.assert_blocked('template:\n- variables: !include variables.yaml\n  sensor:\n  - state: "{{ states(source) }}"\n',
            code='yaml_variable_definition_unsupported')

    def test_merge_and_shared_anchor_metadata_cannot_hide_consumers(self):
        for source in ('''template:
- <<: &defaults {unique_id: block}
  variables:
    source: sensor.foo
  sensor:
  - state: '{{ states(source) }}'
''', '''template:
- variables:
    source: &ref sensor.foo
  sensor:
  - state: '{{ states(source) }}'
metadata: *ref
'''):
            with self.subTest(source=source):
                self.assert_blocked(source)

    def test_complete_template_file_include_has_own_valid_block_scope(self):
        child = '''- variables:
    source: sensor.foo
    alias: '{{ source }}'
  binary_sensor:
  - state: '{{ is_state(alias, "on") }}'
'''
        expected = '''- variables:
    source: sensor.new
    alias: '{{ source }}'
  binary_sensor:
  - state: '{{ is_state(alias, "on") }}'
'''
        self.put('templates.yaml', child)
        plan = self.assert_proposal('template: !include templates.yaml\n', expected, path='templates.yaml')
        self.assertEqual({r.path for r in plan.template_variable_bindings}, {'templates.yaml'})
        self.assertEqual({r.block_line for r in plan.template_variable_bindings}, {1})

    def test_packages_and_separate_blocks_have_separate_variable_scopes(self):
        source = '''homeassistant:
  packages:
    room:
      template:
      - variables:
          source: sensor.foo
        sensor:
        - state: '{{ states(source) }}'
      - variables:
          source: light.old
        binary_sensor:
        - state: '{{ is_state(source, "on") }}'
'''
        expected = '''homeassistant:
  packages:
    room:
      template:
      - variables:
          source: sensor.new
        sensor:
        - state: '{{ states(source) }}'
      - variables:
          source: light.new
        binary_sensor:
        - state: '{{ is_state(source, "on") }}'
'''
        plan = self.assert_proposal(source, expected)
        self.assertEqual(len({r.block_start for r in plan.template_variable_bindings}), 2)
        self.assertEqual({r.before for r in plan.template_variable_bindings}, {'sensor.foo', 'light.old'})

    def test_nested_unknown_template_metadata_does_not_acquire_scope(self):
        source = '''metadata:
  template:
  - variables:
      source: sensor.foo
    sensor:
    - state: '{{ states(source) }}'
'''
        plan = self.assert_blocked(source)
        self.assertEqual(plan.template_variable_bindings, ())

    def test_unrelated_graph_issue_disables_every_locator_approval(self):
        source = '''template:
- variables:
    source: sensor.foo
  sensor:
  - state: '{{ states(source) }}'
other_label: sensor.foo
'''
        plan = self.plan(source)
        self.assertFalse(plan.ready)
        self.assertTrue(plan.template_variable_bindings)
        self.assertFalse(any(r.approved for r in plan.template_variable_bindings))

    def test_unicode_crlf_and_comments_preserve_exact_byte_spans(self):
        source = '# żółć\r\ntemplate:\r\n- variables:\r\n    source: "sensor.foo" # keep\r\n    alias: "{{ source }}"\r\n  sensor:\r\n  - state: "{{ states(alias) }}"\r\n'
        expected = '# żółć\r\ntemplate:\r\n- variables:\r\n    source: "sensor.new" # keep\r\n    alias: "{{ source }}"\r\n  sensor:\r\n  - state: "{{ states(alias) }}"\r\n'
        plan = self.assert_proposal(source, expected)
        for row in plan.template_variable_bindings:
            self.assertEqual(source.encode()[row.producer_start:row.producer_end], b'"sensor.foo"')

    def test_existing_automation_and_script_variable_behavior_has_no_template_locators(self):
        source = '''automation:
- variables:
    source: sensor.foo
  actions:
  - value_template: '{{ states(source) }}'
script:
  demo:
    variables:
      source: light.old
    sequence:
    - value_template: '{{ states(source) }}'
'''
        expected = '''automation:
- variables:
    source: sensor.new
  actions:
  - value_template: '{{ states(source) }}'
script:
  demo:
    variables:
      source: light.new
    sequence:
    - value_template: '{{ states(source) }}'
'''
        plan = self.assert_proposal(source, expected)
        self.assertEqual(plan.template_variable_bindings, ())


if __name__ == '__main__':
    unittest.main()
