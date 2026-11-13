"""One ORIGINAL checker receipt retains Utility's unsupported activation."""
import importlib
import unittest
import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class UtilityValidationReceiptTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    data = server.NativeTemplateYAMLServerTests.data
    _runtime = server.NativeTemplateYAMLServerTests._runtime

    def module(self, name):
        return importlib.import_module(server.activation.fixture.fixtures.PACKAGE + '.' + name)

    async def test_utility_checker_seal_does_not_admit_activation(self):
        _, mapping, _, _ = await self.data(variant='sensor')
        old = next(iter(mapping))
        config = self.f.root / 'configuration.yaml'
        config.write_text(config.read_text() + '\nutility_meter:\n  test_meter:\n    source: '
                          + old + '\n    cycle: daily\n')
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        self.assertTrue(await async_setup_component(
            self.f.hass, 'utility_meter', await async_hass_config_yaml(self.f.hass)))
        await self.f.hass.async_block_till_done()
        planner, validation = self.module('yaml_plan'), self.module('yaml_validation')
        plan = planner.plan_yaml(self.f.root, mapping)
        self.assertTrue(plan.ready, plan.issues)
        result = await validation.async_validate_proposed_with_template_state_sources(
            self.f.hass, plan, mapping, self.f.root.parent,
            with_state_program=True, with_utility_sources=True)
        receipt = result.receipt
        self.assertIn('utility_meter', receipt.changed_domains)
        self.assertIn('utility_meter', receipt.unsupported_domains)
        self.assertFalse(receipt.ready_for_apply)
        rows = [row for row in receipt.runtime_expected if row[0] == 'utility_meter']
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0][1], rows[0][2])
        self.assertTrue(all(len(value) == 64 for value in rows[0][1:]))
        binding_module = self.module('native_utility_yaml_binding')
        binding = await binding_module.prepare_utility_binding(
            self.f.hass, mapping, receipt, guard=lambda: None)
        self.assertTrue(binding.check_prefix(frozenset()))
        self.assertFalse(receipt.ready_for_apply)
