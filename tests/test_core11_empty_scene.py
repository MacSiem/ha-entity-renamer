"""Actual Core 11 empty scene admission and foreign activity refusal."""
import unittest
import importlib

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual Home Assistant')
class Core11EmptySceneTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start

    async def test_empty_loaded_scene_then_foreign_state_invalidates_fence(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.0':
            self.skipTest('Core 11 only')
        from homeassistant.setup import async_setup_component
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'scene', {'scene': []}))
        await hass.async_block_till_done()
        scene = importlib.import_module(fixture.fixtures.PACKAGE + '.native_empty_scene')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        fence = scene.NativeEmptySceneFence(hass)
        try:
            fence.check()
            hass.states.async_set('scene.foreign', 'scening')
            await hass.async_block_till_done()
            with self.assertRaises(tx.Refused):
                fence.check()
        finally:
            fence.close()
