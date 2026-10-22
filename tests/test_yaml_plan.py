"""Synthetic fixtures only; run from this folder with unittest discovery."""
from pathlib import Path
import hashlib
import tempfile
import unittest

from custom_components.ha_entity_renamer.yaml_plan import Limits, YAMLPlanError, plan_yaml


class YAMLPlannerTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
