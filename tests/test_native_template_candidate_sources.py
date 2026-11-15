"""Same real proposed checker; no native mutation or product acceptance claim."""
from dataclasses import replace
import importlib
import unittest
import test_native_template_source_evidence as source_fixture

fixture = source_fixture.fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateCandidateSourceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = source_fixture.NativeTemplateSourceEvidenceTests.asyncSetUp
    _before_start = source_fixture.NativeTemplateSourceEvidenceTests._before_start
    _flush_documents = source_fixture.NativeTemplateSourceEvidenceTests._flush_documents
    prepare = source_fixture.NativeTemplateSourceEvidenceTests.prepare

    def candidate(self, evidence):
        self.assertTrue(hasattr(evidence, 'template_candidate_sources'),
                        'same proposed checker Template seal is not implemented')
        self.assertIsNotNone(evidence.template_candidate_sources)
        return evidence.template_candidate_sources

    async def test_proposed_semantics_are_from_same_native_checker_with_all_inventory(self):
        receipt, evidence = await self.prepare(extra=True)
        original = evidence.template_sources
        candidate = self.candidate(evidence)
        candidate.assert_original(receipt, original)
        self.assertEqual(candidate.proposed_files, receipt.proposed_files)
        self.assertEqual(candidate.sources.ledger, original.ledger)
        self.assertEqual([row[:2] for row in candidate.sources.rows],
                         [row[:2] for row in original.rows])
        self.assertEqual(len(candidate.sources.rows), 3)
        self.assertNotEqual(candidate.sources.projection, original.projection)
        self.assertNotEqual(candidate.sources.rows, original.rows)
        self.assertTrue(candidate.sources.source_matched)
        self.assertFalse(candidate.migration_ready)
        self.assertIn('template', receipt.unsupported_domains)
        self.assertFalse(receipt.ready_for_apply)

    async def test_original_receipt_and_original_semantics_cannot_be_rebound(self):
        receipt, evidence = await self.prepare()
        candidate = self.candidate(evidence)
        with self.assertRaises(ValueError):
            candidate.assert_original(replace(receipt, proposed_files=()), evidence.template_sources)
        with self.assertRaises(ValueError):
            replace(candidate, original_sources_sha256='0' * 64).assert_original(
                receipt, evidence.template_sources)
        with self.assertRaises(ValueError):
            replace(candidate, original_receipt_json=b'{}').assert_original(
                receipt, evidence.template_sources)

    async def test_foreign_proposed_source_keys_refuse_instead_of_adopting_candidate(self):
        receipt, evidence = await self.prepare()
        candidate = self.candidate(evidence)
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.template_source_evidence')
        native = {'version': receipt.version, 'projection': candidate.sources.projection,
                  'rows': [list(row) for row in candidate.sources.rows]}
        native['rows'][0][1] = 'foreign'
        with self.assertRaises(ValueError):
            module.seal_template_candidate(receipt, evidence.template_sources, native)

    async def test_duplicate_sources_are_retained_unmatched_in_both_images(self):
        _, evidence = await self.prepare(duplicate=True)
        candidate = self.candidate(evidence)
        self.assertFalse(candidate.sources.source_matched)
        self.assertTrue(candidate.sources.ledger.findings)
        self.assertEqual(candidate.sources.ledger, evidence.template_sources.ledger)
        self.assertFalse(candidate.migration_ready)
