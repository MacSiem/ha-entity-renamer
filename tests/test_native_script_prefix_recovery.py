"""Actual ACK-only Script-prefix inverse, reusing the full root fault harness."""
import sys
import unittest

import test_native_template_prefix_recovery as prefix


@unittest.skipUnless(prefix.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeScriptPrefixRecoveryTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = prefix.NativeTemplatePrefixRecoveryTests.asyncSetUp
    _before_start = prefix.NativeTemplatePrefixRecoveryTests._before_start
    _flush_documents = prefix.NativeTemplatePrefixRecoveryTests._flush_documents
    prepare = prefix.NativeTemplatePrefixRecoveryTests.prepare
    emit = prefix.NativeTemplatePrefixRecoveryTests.emit
    _runtime = prefix.NativeTemplatePrefixRecoveryTests._runtime
    module = prefix.NativeTemplatePrefixRecoveryTests.module
    _prefix_reverse = prefix.NativeTemplatePrefixRecoveryTests._prefix_reverse

    async def data(self, *, multiple):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.core import Context
        from homeassistant.setup import async_setup_component
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        self.assertTrue(multiple)
        runtime, mapping, uuids, original = await prefix.server.NativeTemplateYAMLServerTests.data(
            self, multiple=True, variant='sensor')
        hass, root = self.f.hass, self.f.root
        first, second = sorted(mapping)
        path = root / 'prefix_scripts.yaml'
        path.write_text('prefix_read:\n  sequence:\n  - event: prefix_script_result\n' +
            '    event_data:\n      value: "{{ (states(' + repr(first) + ') | int(0)) + (states(' +
            repr(second) + ') | int(0)) }}"\n')
        configuration = root / 'configuration.yaml'
        configuration.write_text(configuration.read_text() + '\nscript: !include prefix_scripts.yaml\n')
        config = await async_hass_config_yaml(hass)
        self.assertTrue(await async_setup_component(hass, 'script', config))
        await hass.async_block_till_done()
        await self._flush_documents()
        self.script_bytes = path.read_bytes()
        self.script_entity = next(iter(hass.data[DATA_INSTANCES]['script'].entities))
        self.root_script = self.script_entity.script
        self.script_service = hass.services._services['script']['prefix_read']
        self.script_events = []
        self.addCleanup(hass.bus.async_listen('prefix_script_result',
            lambda event: self.script_events.append(str(event.data['value']))))
        return runtime, mapping, uuids, original

    async def stimulus(self, value):
        from homeassistant.core import Context
        await prefix.server.NativeTemplateYAMLServerTests.stimulus(self, value)
        hass = self.f.hass
        self.assertIs(self.script_entity.script, self.root_script)
        self.assertIs(hass.services._services['script']['prefix_read'], self.script_service)
        await hass.services.async_call('script', 'prefix_read', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        self.assertEqual(self.script_events[-1], str(value * 2))

    async def test_script_one_of_two_acknowledged_prefix_fresh_inverse(self):
        await self._prefix_reverse(1)
        self.assertEqual(self.script_events, ['34', '46'])
        self.assertEqual((self.f.root / 'prefix_scripts.yaml').read_bytes(), self.script_bytes)

    async def test_script_all_registry_acknowledged_before_yaml_fresh_inverse(self):
        await self._prefix_reverse(2)
        self.assertEqual(self.script_events, ['34', '46'])
        self.assertEqual((self.f.root / 'prefix_scripts.yaml').read_bytes(), self.script_bytes)

    async def _controller_prefix_reverse(self, *, retained):
        code = self.module('recovery_controller')._yaml_summary.__code__
        summaries = []
        tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
        sys.monitoring.use_tool_id(tool, 'actual Script prefix controller summary')
        def returned(code_arg, offset, value):
            if type(value) is dict:
                summaries.append(value)
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
            await self._prefix_reverse(1, controller=True, retained=retained)
            self.assertEqual(self.script_events, ['34', '46'])
            self.assertEqual((self.f.root / 'prefix_scripts.yaml').read_bytes(), self.script_bytes)
            self.assertEqual(len(summaries), 2, 'inspect and restore must each bind freshly')
            self.assertTrue(all(summary['dependent_changes'] == {'script': [self.script_entity.entity_id]}
                                for summary in summaries))
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(tool)

    async def test_script_acknowledged_prefix_controller_inspection_and_fresh_inverse(self):
        await self._controller_prefix_reverse(retained=False)

    async def test_script_leased_review_prefix_retention_controller_and_fresh_inverse(self):
        await self._controller_prefix_reverse(retained=True)
