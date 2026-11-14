"""Actual native script inventory must authenticate its registered service owner."""
import importlib
import unittest

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'native HA dependencies required')
class NativeScriptServiceOwnerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start

    async def test_foreign_service_does_not_pass_with_original_script_projection(self):
        from homeassistant.setup import async_setup_component
        discovery = importlib.import_module(fixture.fixtures.PACKAGE + '.discovery')
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'script', {'script': {
            'ownership_probe': {'sequence': [{'variables': {'sample': 'unchanged'}}]}}}))
        await hass.async_block_till_done()
        before, token = discovery._runtime_family(hass, 'script')
        self.assertEqual(before.status, 'COMPLETE', before.reason)
        self.assertIsNotNone(token)
        original = hass.services._services['script']['ownership_probe']
        called = []
        async def foreign(call):
            called.append(call)
        hass.services.async_register('script', 'ownership_probe', foreign,
            schema=original.schema, supports_response=original.supports_response)
        try:
            self.assertTrue(hass.services.has_service('script', 'ownership_probe'))
            current, current_token = discovery._runtime_family(hass, 'script')
            self.assertEqual(current.status, 'UNKNOWN')
            self.assertIsNone(current_token)
            self.assertEqual(called, [])
        finally:
            hass.services.async_register('script', 'ownership_probe', original.job.target,
                schema=original.schema, supports_response=original.supports_response)
        restored, restored_token = discovery._runtime_family(hass, 'script')
        self.assertEqual(restored, before)
        self.assertIsNotNone(restored_token)
        self.assertNotEqual(restored_token, token)
        self.assertEqual(called, [])
