"""Proposed pure tests; worker did not execute them. No Home Assistant runtime."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from custom_components.ha_entity_renamer.yaml_plan import plan_yaml
from custom_components.ha_entity_renamer.yaml_source_ledger import (
    extract_source_ledger, SourceLedgerError)


class SourceLedgerTests(unittest.TestCase):
    def fixture(self, files):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for path, text in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            # Existing graph builder is the only include discovery pass.
            plan = plan_yaml(root, {})
            original = {snap.path: (root / snap.path).read_bytes() for snap in plan.files}
        # Files are gone: extractor cannot accidentally rely on a fresh scan.
        return plan, original

    def ledger(self, root, **files):
        plan, original = self.fixture({'configuration.yaml': root, **files})
        return extract_source_ledger(plan, original)

    def codes(self, ledger):
        return {finding.code for finding in ledger.findings}

    def test_inline_original_inventories_all_unchanged_entries(self):
        ledger = self.ledger('automation:\n- id: alpha\n  actions: []\n- id: beta\n  actions: []\nscript:\n  idle:\n    sequence: []\n')
        self.assertEqual(ledger.keys_for_native_projection('automation'), ('alpha', 'beta'))
        self.assertEqual(ledger.keys_for_native_projection('script'), ('idle',))
        self.assertEqual(ledger.definitions[0].origin.route, ('v:0', 'i:0'))
        self.assertEqual(ledger.definitions[0].origin.start_line, 2)
        self.assertFalse(ledger.findings)

    def test_native_key_matching_rejects_dropped_extra_and_duplicate_rows(self):
        ledger = self.ledger('script:\n  one: {sequence: []}\n  two: {sequence: []}\n')
        ledger.match_native_keys('script', ('two', 'one'))
        for keys in (('one',), ('one', 'two', 'extra'), ('one', 'two', 'two')):
            with self.assertRaisesRegex(SourceLedgerError, 'native_source_inventory_mismatch'):
                ledger.match_native_keys('script', keys)

    def test_two_duplicate_script_mapping_pairs_are_both_preserved(self):
        ledger = self.ledger('script:\n  same: {sequence: []}\n  same: {sequence: []}\n')
        self.assertEqual(len(ledger.definitions), 2)
        self.assertEqual([row.origin.route for row in ledger.definitions], [('v:0', 'v:0'), ('v:0', 'v:1')])
        self.assertEqual({row.disposition for row in ledger.definitions}, {'duplicate'})
        self.assertIn('source_duplicate_native_key', self.codes(ledger))
        with self.assertRaises(SourceLedgerError):
            ledger.keys_for_native_projection('script')

    def test_duplicate_automation_ids_in_distinct_bodies_remain_two_rows(self):
        ledger = self.ledger('automation:\n- id: same\n  alias: First\n  actions: []\n- id: same\n  alias: Last\n  actions: []\n')
        self.assertEqual(len(ledger.definitions), 2)
        self.assertEqual({row.disposition for row in ledger.definitions}, {'duplicate'})
        self.assertNotEqual(ledger.definitions[0].source_ast_sha256, ledger.definitions[1].source_ast_sha256)

    def test_labeled_script_sections_do_not_hide_first_wins_duplicate(self):
        ledger = self.ledger('script first:\n  same: {sequence: []}\nscript second:\n  same: {sequence: []}\n')
        self.assertEqual([row.section for row in ledger.definitions], ['script first', 'script second'])
        self.assertEqual({row.disposition for row in ledger.definitions}, {'duplicate'})

    def test_inline_and_nested_include_origins_and_hops(self):
        ledger = self.ledger('script: !include scripts.yaml\n', **{
            'scripts.yaml': 'one: !include bodies/one.yaml\n',
            'bodies/one.yaml': 'sequence: []\n'})
        row = ledger.definitions[0]
        self.assertEqual(row.origin.path, 'bodies/one.yaml')
        self.assertEqual(row.key_origin.path, 'scripts.yaml')
        self.assertEqual(len(row.origin.includes), 2)
        self.assertEqual(row.origin.includes[1].route, ('v:0',))
        self.assertFalse(ledger.findings)

    def test_repeated_include_same_file_keeps_occurrence_routes(self):
        ledger = self.ledger('automation: !include shared.yaml\nautomation: !include shared.yaml\n',
                             **{'shared.yaml': '- id: repeated\n  actions: []\n'})
        self.assertEqual(len(ledger.definitions), 2)
        first, second = ledger.definitions
        self.assertEqual(first.origin.path, second.origin.path)
        self.assertNotEqual(first.origin.includes, second.origin.includes)
        self.assertEqual(first.disposition, 'duplicate')

    def test_merge_named_retains_scripts_loader_would_update_away(self):
        ledger = self.ledger('script: !include_dir_merge_named scripts\n', **{
            'scripts/a.yaml': 'same: {sequence: []}\n',
            'scripts/sub/b.yaml': 'same: {sequence: []}\n'})
        self.assertEqual({row.origin.path for row in ledger.definitions}, {'scripts/a.yaml', 'scripts/sub/b.yaml'})
        self.assertEqual({row.disposition for row in ledger.definitions}, {'duplicate'})

    def test_dir_named_basename_collision_across_subdirs(self):
        ledger = self.ledger('script: !include_dir_named scripts\n', **{
            'scripts/a/same.yaml': 'sequence: []\n',
            'scripts/b/same.yaml': 'sequence: []\n'})
        self.assertEqual([row.native_key for row in ledger.definitions], ['same', 'same'])
        self.assertIn('source_duplicate_native_key', self.codes(ledger))

    def test_directory_merge_list_and_disjoint_labeled_package_sources(self):
        ledger = self.ledger('automation: !include_dir_merge_list autos\nscript extra:\n  inline: {sequence: []}\nhomeassistant:\n  packages: !include_dir_named packages\n', **{
            'autos/a.yaml': '- id: alpha\n  actions: []\n',
            'autos/nested/b.yaml': '- id: beta\n  actions: []\n',
            'packages/p.yaml': 'automation label:\n- id: gamma\n  actions: []\nscript:\n  packaged: {sequence: []}\n'})
        self.assertFalse(ledger.findings)
        self.assertEqual(ledger.keys_for_native_projection('automation'), ('alpha', 'beta', 'gamma'))
        self.assertEqual(ledger.keys_for_native_projection('script'), ('inline', 'packaged'))
        self.assertEqual([row.package for row in ledger.definitions if row.native_key == 'packaged'], ['p'])

    def test_package_same_script_key_recursive_merge_is_explicit_conflict(self):
        ledger = self.ledger('script:\n  same: {sequence: []}\nhomeassistant:\n  packages:\n    other:\n      script:\n        same: {alias: Additional}\n')
        self.assertEqual(len(ledger.definitions), 2)
        self.assertEqual({row.disposition for row in ledger.definitions}, {'duplicate'})

    def test_nonlist_merge_list_fragment_is_not_silently_skipped(self):
        ledger = self.ledger('automation: !include_dir_merge_list autos\n', **{
            'autos/list.yaml': '- id: kept\n  actions: []\n',
            'autos/dropped.yaml': 'id: silently_dropped\nactions: []\n'})
        self.assertEqual([row.native_key for row in ledger.definitions], ['kept'])
        self.assertIn('source_container_layout_unsupported', self.codes(ledger))
        with self.assertRaises(SourceLedgerError):
            ledger.match_native_keys('automation', ('kept',))

    def test_nonmapping_merge_named_fragment_is_explicit_unknown(self):
        ledger = self.ledger('script: !include_dir_merge_named scripts\n', **{
            'scripts/dropped.yaml': '- wrong_shape\n',
            'scripts/kept.yaml': 'one: {sequence: []}\n'})
        self.assertIn('source_container_layout_unsupported', self.codes(ledger))

    def test_missing_numeric_and_duplicate_id_are_unknown(self):
        for body in ('actions: []', 'id: 123\n  actions: []', 'id: one\n  id: two\n  actions: []'):
            with self.subTest(body=body):
                ledger = self.ledger('automation:\n- ' + body + '\n')
                self.assertIn('source_automation_stable_id_missing_or_ambiguous', self.codes(ledger))
                self.assertEqual(ledger.definitions[0].disposition, 'unsupported')

    def test_nested_duplicate_body_keys_and_blueprint_are_not_candidates(self):
        for body in ('sequence: []\n    sequence: []', 'use_blueprint: {path: x.yaml}'):
            ledger = self.ledger('script:\n  one:\n    ' + body + '\n')
            self.assertEqual(ledger.definitions[0].disposition, 'unsupported')

    def test_environment_and_body_directory_include_are_explicit_unsupported(self):
        ledger = self.ledger('script:\n  one:\n    sequence: !env_var ACTIONS\n')
        self.assertIn('source_body_tag_unsupported', self.codes(ledger))
        ledger = self.ledger('script:\n  one:\n    variables: !include_dir_merge_named vars\n    sequence: []\n',
                             **{'vars/a.yaml': 'x: first\n', 'vars/b.yaml': 'x: second\n'})
        self.assertIn('source_body_directory_include_unsupported', self.codes(ledger))

    def test_secret_tag_is_never_constructed_and_digest_is_deterministic(self):
        plan, original = self.fixture({'configuration.yaml': 'script:\n  one:\n    variables: {token: !secret private_token}\n    sequence: []\n'})
        one = extract_source_ledger(plan, original)
        two = extract_source_ledger(plan, dict(reversed(tuple(original.items()))))
        self.assertEqual(one, two)
        self.assertFalse(one.findings)
        self.assertNotIn('private_token', repr(one))

    def test_original_bytes_changed_or_membership_changed_fail_closed(self):
        plan, original = self.fixture({'configuration.yaml': 'script: {}\n'})
        for changed in ({}, {**original, 'extra.yaml': b'[]'}, {'configuration.yaml': b'script: []\n'}):
            with self.assertRaises(SourceLedgerError):
                extract_source_ledger(plan, changed)

    def test_include_edges_are_reused_not_rediscovered_or_guessed(self):
        plan, original = self.fixture({'configuration.yaml': 'script: !include scripts.yaml\n',
                                       'scripts.yaml': 'one: {sequence: []}\n'})
        with self.assertRaisesRegex(SourceLedgerError, 'source_include_edge_mismatch'):
            extract_source_ledger(replace(plan, edges=()), original)

    def test_only_configuration_reachability_is_native_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'configuration.yaml').write_text('script: {}\n')
            (root / 'detached.yaml').write_text('script:\n  detached: {sequence: []}\n')
            plan = plan_yaml(root, {}, yaml_dashboards=('detached.yaml',))
            original = {row.path: (root / row.path).read_bytes() for row in plan.files}
        self.assertFalse(extract_source_ledger(plan, original).definitions)

    def test_include_dir_list_inventories_each_physical_automation(self):
        ledger = self.ledger('automation: !include_dir_list autos\n', **{
            'autos/first.yaml': 'id: first\nactions: []\n',
            'autos/second.yaml': 'id: second\nactions: []\n'})
        self.assertEqual(ledger.keys_for_native_projection('automation'), ('first', 'second'))
        self.assertEqual({row.origin.path for row in ledger.definitions}, {'autos/first.yaml', 'autos/second.yaml'})

    def test_identical_bodies_under_distinct_stable_keys_stay_distinct(self):
        ledger = self.ledger('script:\n  first: {sequence: []}\n  second: {sequence: []}\n')
        self.assertEqual(ledger.keys_for_native_projection('script'), ('first', 'second'))
        self.assertEqual(ledger.definitions[0].source_ast_sha256, ledger.definitions[1].source_ast_sha256)
