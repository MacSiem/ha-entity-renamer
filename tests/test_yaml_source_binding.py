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


class RuntimeSourceSamplerBindingTests(unittest.TestCase):
    fixture = SourceEvidenceBindingTests.fixture
    def sampler_fixture(self):
        import json
        from custom_components.ha_entity_renamer import yaml_source_binding as binding
        from custom_components.ha_entity_renamer.native_discovery_inputs import (
            DiscoveryNativeFile, DiscoveryNativeInputs)
        receipt, plan, ledger, native = self.fixture()
        evidence = binding.seal_original_sources(receipt, plan, ledger, native)
        row = {'id': 'script-registry-id', 'platform': 'script', 'entity_id': 'script.one',
               'unique_id': 'one', 'opaque': {'nested': [1]}}
        document = {'entities': [row,
            {'platform': 'sensor', 'entity_id': 'sensor.unrelated', 'unique_id': 'foreign'},
            {'platform': 'script', 'entity_id': 'sensor.wrong_domain', 'unique_id': 'wrong'}],
            'deleted_entities': [{'unrelated': 'large deleted corpus'}]}
        manager_json = json.dumps(document).encode()
        file = DiscoveryNativeFile('core.entity_registry', b'raw', (1, 2, 0o100600),
                                   1, 1, manager_json)
        observed = DiscoveryNativeInputs('2026.10.2', '/isolated-config', (file,))
        return binding, evidence, observed, document

    def test_corpus_keeps_only_immutable_detached_canonical_eligible_rows(self):
        import json
        binding, evidence, observed, source = self.sampler_fixture()
        sampler = binding._RuntimeSourceSampler.bind(evidence, observed)
        self.assertIs(type(sampler._rows), tuple)
        self.assertEqual(len(sampler._rows), 1)
        row = source['entities'][0]
        canonical = json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(sampler._rows, (canonical,))
        self.assertIs(type(sampler._rows[0]), bytes)
        row['opaque']['nested'].append(2)
        detached = json.loads(sampler._rows[0])
        detached['opaque']['nested'].append(3)
        self.assertEqual(sampler._rows, (canonical,))
        for field, value in (('_rows', (b'{}',)), ('_manager_json', b'{}')):
            with self.assertRaises(AttributeError):
                setattr(sampler, field, value)

    def test_exact_evidence_observation_file_tuple_and_bytes_are_pinned(self):
        from unittest.mock import patch
        binding, evidence, observed, _ = self.sampler_fixture()
        sampler = binding._RuntimeSourceSampler.bind(evidence, observed)
        sentinel = ((), (), ())
        # This pure case verifies pinning only. Actual native live cases below
        # use the real backend without any positive verifier substitution.
        with patch.object(binding, '_runtime_rows_sample', return_value=sentinel) as backend:
            self.assertIs(sampler.sample(None, evidence, observed), sentinel)
            for changed_evidence, changed_observed in (
                    (replace(evidence), observed), (evidence, replace(observed))):
                with self.assertRaises(binding.SourceBindingError):
                    sampler.sample(None, changed_evidence, changed_observed)
            self.assertEqual(backend.call_count, 1)
            old_files = observed.files
            object.__setattr__(observed, 'files', tuple(list(old_files)))
            try:
                with self.assertRaises(binding.SourceBindingError):
                    sampler.sample(None, evidence, observed)
            finally:
                object.__setattr__(observed, 'files', old_files)
            native = old_files[0]
            old_bytes = native.manager_json
            same_bytes = bytes(bytearray(old_bytes))
            self.assertEqual(same_bytes, old_bytes)
            self.assertIsNot(same_bytes, old_bytes)
            # Deliberate frozen-object corruption injection: even byte-equal
            # replacement of the pinned manager buffer must refuse before IO.
            object.__setattr__(native, 'manager_json', same_bytes)
            try:
                with self.assertRaises(binding.SourceBindingError):
                    sampler.sample(None, evidence, observed)
            finally:
                object.__setattr__(native, 'manager_json', old_bytes)
            self.assertEqual(backend.call_count, 1)

    def test_each_sample_gives_the_backend_fresh_nested_rows(self):
        from unittest.mock import patch
        import json
        binding, evidence, observed, _ = self.sampler_fixture()
        sampler = binding._RuntimeSourceSampler.bind(evidence, observed)
        seen = []
        def mutating_reader(hass, current_evidence, rows):
            row, = tuple(rows)
            seen.append(deepcopy(row))
            row['opaque']['nested'].append('reader mutation')
            return ((), (), ())
        with patch.object(binding, '_runtime_rows_sample', mutating_reader):
            sampler.sample(None, evidence, observed)
            sampler.sample(None, evidence, observed)
        self.assertEqual(seen, [json.loads(sampler._rows[0])] * 2)

    def test_mutable_manager_buffer_and_sampler_subclass_are_refused(self):
        binding, evidence, observed, _ = self.sampler_fixture()
        native = replace(observed.files[0], manager_json=bytearray(observed.files[0].manager_json))
        with self.assertRaises(binding.SourceBindingError):
            binding._RuntimeSourceSampler.bind(evidence, replace(observed, files=(native,)))
        class ForeignSampler(binding._RuntimeSourceSampler):
            pass
        with self.assertRaises(binding.SourceBindingError):
            ForeignSampler.bind(evidence, observed)
