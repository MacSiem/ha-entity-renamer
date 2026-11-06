"""Actual Automation+Script ACK-prefix recovery through the existing Root harness."""
import sys
import unittest

import test_native_script_prefix_recovery as script_prefix
import test_native_template_prefix_recovery as prefix


@unittest.skipUnless(prefix.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationPrefixRecoveryTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = script_prefix.NativeScriptPrefixRecoveryTests.asyncSetUp
    _before_start = script_prefix.NativeScriptPrefixRecoveryTests._before_start
    _flush_documents = script_prefix.NativeScriptPrefixRecoveryTests._flush_documents
    prepare = script_prefix.NativeScriptPrefixRecoveryTests.prepare
    emit = script_prefix.NativeScriptPrefixRecoveryTests.emit
    _runtime = script_prefix.NativeScriptPrefixRecoveryTests._runtime
    module = script_prefix.NativeScriptPrefixRecoveryTests.module
    _prefix_reverse = prefix.NativeTemplatePrefixRecoveryTests._prefix_reverse

    async def data(self, *, multiple):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        result = await script_prefix.NativeScriptPrefixRecoveryTests.data(self, multiple=multiple)
        runtime, mapping, uuids, original = result
        hass, root = self.f.hass, self.f.root
        path = root / 'prefix_automations.yaml'
        path.write_text('- id: prefix_automation\n  alias: Synthetic prefix observer\n'
            '  initial_state: true\n  triggers:\n  - trigger: state\n'
            '    entity_id: [' + ', '.join(sorted(mapping)) + ']\n'
            '  actions:\n  - event: prefix_automation_result\n'
            '    event_data:\n      value: "{{ trigger.to_state.state }}"\n')
        config_path = root / 'configuration.yaml'
        config_path.write_text(config_path.read_text() + '\nautomation: !include prefix_automations.yaml\n')
        config = await async_hass_config_yaml(hass)
        self.assertTrue(await async_setup_component(hass, 'automation', config))
        await hass.async_block_till_done()
        await self._flush_documents()
        self.automation_bytes = path.read_bytes()
        self.automation_entity, = hass.data[DATA_INSTANCES]['automation'].entities
        self.automation_owners = (self.automation_entity.action_script, self.automation_entity.raw_config,
            self.automation_entity._trigger_config, self.automation_entity._async_detach_triggers)
        self.automation_events = []
        self.addCleanup(hass.bus.async_listen('prefix_automation_result',
            lambda event: self.automation_events.append(str(event.data['value']))))
        return result

    async def stimulus(self, value):
        await script_prefix.NativeScriptPrefixRecoveryTests.stimulus(self, value)
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        current, = self.f.hass.data[DATA_INSTANCES]['automation'].entities
        self.assertIs(current, self.automation_entity)
        for actual, retained in zip((current.action_script, current.raw_config,
                current._trigger_config, current._async_detach_triggers), self.automation_owners, strict=True):
            self.assertIs(actual, retained)
        self.assertEqual(self.automation_events[-2:], [str(value)] * 2)

    def _assert_restored(self):
        self.assertEqual(self.script_events, ['34', '46'])
        self.assertEqual(self.automation_events, ['17', '17', '23', '23'])
        self.assertEqual((self.f.root / 'prefix_scripts.yaml').read_bytes(), self.script_bytes)
        self.assertEqual((self.f.root / 'prefix_automations.yaml').read_bytes(), self.automation_bytes)

    async def test_automation_one_of_two_acknowledged_prefix_fresh_inverse(self):
        await self._prefix_reverse(1)
        self._assert_restored()

    async def test_automation_all_registry_acknowledged_before_yaml_fresh_inverse(self):
        await self._prefix_reverse(2)
        self._assert_restored()

    async def test_automation_retained_prefix_controller_summary_and_fresh_inverse(self):
        code = self.module('recovery_controller')._yaml_summary.__code__
        summaries = []
        tool = next(key for key in range(6) if sys.monitoring.get_tool(key) is None)
        sys.monitoring.use_tool_id(tool, 'actual Automation prefix controller summary')
        def returned(code_arg, offset, value):
            if type(value) is dict:
                summaries.append(value)
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
            await self._prefix_reverse(1, controller=True, retained=True)
            self._assert_restored()
            self.assertEqual(len(summaries), 2)
            self.assertTrue(all(summary['dependent_changes'] == {
                'automation': [self.automation_entity.entity_id], 'script': [self.script_entity.entity_id]}
                for summary in summaries))
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)
