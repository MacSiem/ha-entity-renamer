"""Isolated native HomeKit YAML reload, no physical client or pairing reset."""
import importlib
import unittest
from pathlib import Path
import test_native_input_baseline as fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class HomeKitNativeReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_reload_and_fresh_inverse(self):
        from homeassistant.setup import async_setup_component
        from homeassistant.config import async_hass_config_yaml
        fixture = fixtures.NativeBaselineTests(methodName='runTest')
        await fixture.asyncSetUp()
        self.addAsyncCleanup(fixture.asyncTearDown)
        hass, root = fixture.hass, fixture.root
        from homeassistant import auth
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        source = root / 'configuration.yaml'
        def config(entity):
            return ('homekit:\n  name: Synthetic Bridge\n  port: 21199\n'
                    '  ip_address: 192.0.2.1\n  advertise_ip: 192.0.2.1\n'
                    f'  filter:\n    exclude_entities:\n      - {entity}\n')
        source.write_text(config('sensor.old'))
        self.assertTrue(await async_setup_component(hass, 'homekit', await async_hass_config_yaml(hass)))
        await hass.async_block_till_done()
        # First native import adds temporary data.index; the native YAML
        # reload removes it, matching the pre-existing target bridge shape.
        await hass.services.async_call('homekit', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        adapter = importlib.import_module(fixtures.PACKAGE + '.native_homekit_filter')
        mapping = {'sensor.old':'sensor.new'}
        async def settle(): await hass.async_block_till_done()
        async def intent(_): pass
        def guard(): pass
        async def run(old, new):
            captured = adapter.capture(hass, {old:new})
            expected = config(new)
            source.write_text(expected)
            async def verify(): self.assertEqual(source.read_text(), expected)
            await adapter.reload_after_yaml(hass, captured, verify_source=verify,
                guard=guard, record_intent=intent, settle=settle)
            entry = hass.config_entries.async_entries('homekit')[0]
            native = entry.runtime_data.homekit
            self.assertFalse(native._filter.get_filter()(new))
            self.assertTrue(native._filter.get_filter()(old))
        await run('sensor.old', 'sensor.new')
        await run('sensor.new', 'sensor.old')
