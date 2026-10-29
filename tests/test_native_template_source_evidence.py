"""Native ORIGINAL source checker only; runtime binding/activation remain OPEN."""
import importlib
import unittest
import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class NativeTemplateSourceEvidenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start
    _flush_documents = fixture.NativeHelperQuiescenceTests._flush_documents

    async def prepare(self, *, extra=False, duplicate=False):
        root = self.f.root
        (root / 'configuration.yaml').write_text('template: !include template.yaml\n')
        text = '- unique_id: section\n  sensor:\n  - unique_id: value\n    state: "{{ states(\'sensor.original\') }}"\n'
        if extra:
            # A delayed binary stays outside the new literal state-only subset.
            text += '  binary_sensor:\n  - unique_id: flag\n    state: "{{ is_state(\'sensor.original\', \'on\') }}"\n    delay_on: "00:00:01"\n'
        if duplicate:
            text += '- unique_id: section\n  sensor:\n  - unique_id: value\n    state: "{{ states(\'sensor.original\') }}"\n'
        (root / 'template.yaml').write_text(text)
        await self._flush_documents(initialize=True)
        planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')
        validation = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_validation')
        plan = planner.plan_yaml(root, {'sensor.original': 'sensor.changed'})
        self.assertTrue(plan.ready, plan.issues)
        receipt, evidence = await validation.async_validate_proposed_with_sources(
            self.f.hass, plan, root.parent)
        self.assertTrue(hasattr(evidence, "template_sources"),
                        "Template source evidence is not implemented")
        return receipt, evidence

    async def test_same_checker_inventories_native_sensor_and_other_platform(self):
        receipt, evidence = await self.prepare(extra=True)
        source = evidence.template_sources
        self.assertIsNotNone(source)
        self.assertTrue(source.source_matched)
        self.assertFalse(source.runtime_verified)
        self.assertFalse(source.migration_ready)
        self.assertEqual({(r[0], r[1]) for r in source.rows}, {
            ('template.state', 'id:section'), ('template.sensor', 'section-value'),
            ('template.binary_sensor', 'section-flag')})
        # No runtime projection or Template activation gate is loosened.
        self.assertIn('template', receipt.unsupported_domains)
        self.assertIsNone(next(r[1] for r in receipt.runtime_expected if r[0] == 'template'))

    async def test_duplicate_physical_sources_remain_visible_and_unmatched(self):
        _, evidence = await self.prepare(duplicate=True)
        source = evidence.template_sources
        self.assertFalse(source.source_matched)
        self.assertTrue(source.ledger.findings)
        self.assertEqual(len([r for r in source.ledger.definitions if r.domain == 'template.sensor']), 2)

    async def test_foreign_native_keys_and_late_physical_source_refuse(self):
        receipt, evidence = await self.prepare()
        binding = importlib.import_module(fixture.fixtures.PACKAGE + '.template_source_evidence')
        validation = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_validation')
        source = evidence.template_sources
        native = {'version': receipt.version, 'projection': source.projection,
                  'rows': [list(r) for r in source.rows]}
        native['rows'][0][1] = 'foreign'
        with self.assertRaises(ValueError):
            binding.seal_template_sources(receipt, source.ledger, native)
        (self.f.root / 'template.yaml').write_text('# foreign edit\n')
        with self.assertRaises(ValueError):
            validation.assert_validation_current(receipt)

    async def test_source_digest_changes_for_native_semantics_without_ready_claim(self):
        _, evidence = await self.prepare()
        before = evidence.template_sources
        root = self.f.root
        path = root / 'template.yaml'
        path.write_text(path.read_text().replace('unique_id: value',
            'unique_id: value\n    unit_of_measurement: W'))
        planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')
        validation = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_validation')
        plan = planner.plan_yaml(root, {'sensor.original': 'sensor.changed'})
        _, second = await validation.async_validate_proposed_with_sources(
            self.f.hass, plan, root.parent)
        self.assertTrue(second.template_sources.source_matched)
        self.assertNotEqual(before.projection, second.template_sources.projection)
        self.assertNotEqual(before.sha256, second.template_sources.sha256)
        self.assertFalse(second.template_sources.runtime_verified)
