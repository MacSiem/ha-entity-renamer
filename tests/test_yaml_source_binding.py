"""Pure server evidence layout/binding; synthetic digests are not native proof."""
from copy import deepcopy
from dataclasses import replace
import unittest

import test_yaml_source_ledger as fixture
from custom_components.ha_entity_renamer.yaml_validation import ValidationReceipt
from custom_components.ha_entity_renamer.yaml_source_ledger import extract_source_ledger
from custom_components.ha_entity_renamer.yaml_source_binding import (
    seal_original_sources, SourceBindingError)


class SourceEvidenceBindingTests(unittest.TestCase):
    def fixture(self):
        plan, original = fixture.SourceLedgerTests.fixture(self, {
            'configuration.yaml': 'script:\n  one: {sequence: []}\n'})
        ledger = extract_source_ledger(plan, original)
        receipt = ValidationReceipt('/isolated-config', tuple(
            (row.path, row.sha256, row.device, row.inode) for row in plan.files),
            (('.', (), None),), (), ('script',), (), '2026.10.2',
            (('script', 'a' * 64, 'e' * 64),))
        native = {'version': '2026.10.2', 'domains': [
            ['automation', 'd' * 64, []],
            ['script', 'a' * 64, [['one', 'b' * 64, 'c' * 64]]]]}
        return receipt, plan, ledger, native

    def test_original_receipt_and_native_rows_remain_detached(self):
        receipt, plan, ledger, native = self.fixture()
        evidence = seal_original_sources(receipt, plan, ledger, native)
        self.assertEqual(evidence.domain_projection('script'), 'a' * 64)
        native['domains'][1][2][0][0] = 'foreign'
        self.assertEqual(evidence.domains[1].rows, (('one', 'b' * 64, 'c' * 64),))
        evidence.assert_receipt(receipt)
        with self.assertRaises(AttributeError):
            evidence.receipt_json = b'{}'

    def test_changed_receipt_version_and_original_digest_are_not_rebound(self):
        receipt, plan, ledger, native = self.fixture()
        evidence = seal_original_sources(receipt, plan, ledger, native)
        for changed in (replace(receipt, config_root='/foreign'),
                        replace(receipt, version='2025.6.0'),
                        replace(receipt, runtime_expected=(('script', 'e' * 64, 'a' * 64),))):
            with self.assertRaises(SourceBindingError):
                evidence.assert_receipt(changed)
        for native_change in ({**native, 'version': '2025.6.0'},
                              {**native, 'domains': [['automation', 'd' * 64, []],
                                                    ['script', 'f' * 64, []]]}):
            with self.assertRaises(SourceBindingError):
                seal_original_sources(receipt, plan, ledger, native_change)

    def test_same_keys_with_foreign_graph_or_input_receipt_are_refused(self):
        receipt, plan, ledger, native = self.fixture()
        foreign_plan = replace(plan, files=(replace(plan.files[0], inode=plan.files[0].inode + 1),))
        with self.assertRaises(SourceBindingError):
            seal_original_sources(receipt, foreign_plan, ledger, native)
        with self.assertRaises(SourceBindingError):
            seal_original_sources(replace(receipt, source_files=()), plan, ledger, native)

    def test_missing_duplicate_unknown_and_invalid_native_rows_are_refused(self):
        receipt, plan, ledger, native = self.fixture()
        variants = []
        for domains in ([], native['domains'][:1], native['domains'] * 2,
                        [['automation', 'd' * 64, []], ['script', 'a' * 64, []]],
                        [['unknown', 'a' * 64, []], native['domains'][1]],
                        [['automation', 'd' * 64, []], ['script', 'a' * 64,
                            [['one', 'b' * 64, 'c' * 64]] * 2]],
                        [['automation', 'd' * 64, []], ['script', 'a' * 64,
                            [['one', 'not-a-digest', 'c' * 64]]]]):
            variants.append({**native, 'domains': domains})
        variants.append({**native, 'authority': True})
        for value in variants:
            with self.subTest(value=value), self.assertRaises(SourceBindingError):
                seal_original_sources(receipt, plan, ledger, value)

    def test_binding_digest_includes_receipt_and_native_rows(self):
        receipt, plan, ledger, native = self.fixture()
        original = seal_original_sources(receipt, plan, ledger, native)
        different_receipt = seal_original_sources(replace(receipt, config_root='/other-config'),
                                                  plan, ledger, native)
        changed = deepcopy(native)
        changed['domains'][1][2][0][1] = 'f' * 64
        different_rows = seal_original_sources(receipt, plan, ledger, changed)
        self.assertNotEqual(original.sha256, different_receipt.sha256)
        self.assertNotEqual(original.sha256, different_rows.sha256)

    def test_unknown_native_projection_is_preserved_without_authority(self):
        receipt, plan, ledger, native = self.fixture()
        unknown = deepcopy(native)
        unknown['domains'][0] = ['automation', None, None]
        evidence = seal_original_sources(receipt, plan, ledger, unknown)
        self.assertIsNone(evidence.domain_projection('automation'))
        self.assertFalse(evidence.migration_ready)
