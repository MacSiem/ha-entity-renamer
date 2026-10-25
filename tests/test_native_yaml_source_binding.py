"""Actual ORIGINAL source/native/runtime bijection; no user actions executed."""
import importlib
import unittest

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'native HA dependencies required')
class NativeYAMLSourceBindingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start
    _flush_documents = fixture.NativeHelperQuiescenceTests._flush_documents

    async def prepare(self, *, foreign_runtime=False, duplicate=False):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        hass, root = self.f.hass, self.f.root
        (root / 'scripts').mkdir()
        (root / 'configuration.yaml').write_text('script: !include_dir_merge_named scripts\n')
        (root / 'scripts/one.yaml').write_text('source_probe:\n  sequence:\n    - variables:\n        sample: "{{ states(\'sensor.original\') }}"\n')
        if duplicate:
            (root / 'scripts/two.yaml').write_bytes((root / 'scripts/one.yaml').read_bytes())
        config = await async_hass_config_yaml(hass)
        if foreign_runtime:
            config['script']['source_probe']['sequence'] = [{'variables': {'sample': 'foreign'}}]
        self.assertTrue(await async_setup_component(hass, 'script', config))
        await hass.async_block_till_done()
        await self._flush_documents(initialize=True)
        planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')
        plan = planner.plan_yaml(root, {'sensor.original': 'sensor.changed'})
        self.assertTrue(plan.ready, plan.issues)
        validation = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_validation')
        receipt, evidence = await validation.async_validate_proposed_with_sources(
            hass, plan, root.parent)
        return receipt, evidence

    async def test_same_original_checker_binds_all_script_keys_and_runtime_without_actions(self):
        receipt, evidence = await self.prepare()
        binding = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_source_binding')
        self.assertEqual(evidence.ledger.keys_for_native_projection('script'), ('source_probe',))
        self.assertEqual(evidence.ledger.keys_for_native_projection('automation'), ())
        before = next(row[1] for row in receipt.runtime_expected if row[0] == 'script')
        self.assertEqual(evidence.domain_projection('script'), before)
        result = await binding.async_bind_original_runtime(self.f.hass, receipt, evidence,
                                                          guard=lambda: None)
        self.assertTrue(result.verified)
        self.assertEqual([(row.domain, row.native_key, row.disposition) for row in result.rows],
                         [('script', 'source_probe', 'loaded')])
        self.assertFalse(result.blockers)
        self.assertFalse(result.migration_ready)

    async def test_valid_source_cannot_bless_foreign_runtime_body(self):
        receipt, evidence = await self.prepare(foreign_runtime=True)
        binding = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_source_binding')
        result = await binding.async_bind_original_runtime(self.f.hass, receipt, evidence,
                                                          guard=lambda: None)
        self.assertFalse(result.verified)
        self.assertIn('runtime_source_projection_mismatch', result.blockers)

    async def test_native_loader_shadowing_keeps_duplicate_source_blocker(self):
        receipt, evidence = await self.prepare(duplicate=True)
        binding = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_source_binding')
        self.assertEqual(len(evidence.ledger.definitions), 2)
        self.assertTrue(evidence.ledger.findings)
        result = await binding.async_bind_original_runtime(self.f.hass, receipt, evidence,
                                                          guard=lambda: None)
        self.assertFalse(result.verified)
        self.assertIn('runtime_source_inventory_unverified', result.blockers)

    async def test_late_source_edit_and_revoked_guard_do_not_rebase_evidence(self):
        receipt, evidence = await self.prepare()
        binding = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_source_binding')
        path = self.f.root / 'scripts/one.yaml'
        path.write_text(path.read_text() + '# foreign edit\n')
        result = await binding.async_bind_original_runtime(self.f.hass, receipt, evidence,
                                                          guard=lambda: None)
        self.assertFalse(result.verified)
        self.assertIn('runtime_source_inputs_changed', result.blockers)
        def revoked():
            raise PermissionError('revoked')
        with self.assertRaises(PermissionError):
            await binding.async_bind_original_runtime(self.f.hass, receipt, evidence, guard=revoked)
