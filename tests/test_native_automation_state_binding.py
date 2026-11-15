"""Actual native state trigger binding admission, without applying any change."""
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationStateBindingTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_start = mixed.NativeTemplateMixedYAMLServerTests._before_start
    _flush_documents = mixed.NativeTemplateMixedYAMLServerTests._flush_documents
    prepare = mixed.NativeTemplateMixedYAMLServerTests.prepare
    emit = mixed.NativeTemplateMixedYAMLServerTests.emit
    _runtime = mixed.NativeTemplateMixedYAMLServerTests._runtime
    module = mixed.NativeTemplateMixedYAMLServerTests.module
    stimulus = mixed.NativeTemplateMixedYAMLServerTests.stimulus
    data = mixed.NativeTemplateMixedYAMLServerTests.data

    async def test_actual_original_state_binding_uses_distinct_native_route_and_config_lists(self):
        runtime, mapping, uuids, _, original = await self.data()
        hass = self.f.hass
        await self.stimulus(17)
        entity = next(iter(hass.data['automation'].entities))
        module = self.module('native_automation_state_binding')
        bindings = await module.async_capture_state_bindings(hass, entity, guard=lambda: None)
        self.assertEqual(len(bindings), 1)
        binding = bindings[0]
        binding.assert_original()
        self.assertIs(binding.config, entity._trigger_config[0])
        self.assertIs(binding.keyed_job.target, binding.listener)
        self.assertIs(binding.action_job.target.__self__, entity)
        self.assertIsNot(binding.config_ids, binding.original_keys)
        self.assertEqual(binding.config_ids, binding.original_keys)
        self.assertEqual(binding.config_entity_id_slot, (binding.config, 'entity_id'))
        self.assertIs(binding.original_for_ingress(), binding)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
        for path, contents in original.items():
            self.assertEqual((self.f.root / path).read_bytes(), contents)
        self.assertFalse(await runtime.journal.active())
