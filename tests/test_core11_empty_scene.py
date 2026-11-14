"""Actual Core 11 empty scene admission and foreign activity refusal."""
import unittest
import importlib
import socket

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual Home Assistant')
class Core11EmptySceneTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    async def _before_start(self, hass, root):
        from homeassistant import auth
        from homeassistant.setup import async_setup_component
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        config = {'http': {'server_host': '127.0.0.1', 'server_port': port}}
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        self.assertTrue(await async_setup_component(hass, 'http', config))
        from homeassistant.components.config import automation, script, scene
        for editor in (automation, script, scene):
            self.assertTrue(editor.async_setup(hass))

    async def test_native_editor_locks_are_acquired_and_released(self):
        from homeassistant.const import __version__
        if __version__ not in ('2026.11.0', '2026.11.1'):
            self.skipTest('Core 11 only')
        module = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_editor_locks')
        locks = module.NativeYAMLEditorLocks(self.f.hass)
        baseline = locks.enumerate()
        self.assertGreaterEqual(len(baseline), 3)
        async with locks.hold(lambda: None) as held:
            self.assertEqual(held, baseline)
            self.assertTrue(all(lock.locked() for _, _, _, lock in held))
        self.assertTrue(all(not lock.locked() for _, _, _, lock in baseline))

    async def test_empty_loaded_scene_then_foreign_state_invalidates_fence(self):
        from homeassistant.const import __version__
        if __version__ not in ('2026.11.0', '2026.11.1'):
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

    async def test_foreign_empty_platform_is_fenced_and_unsettled_is_refused(self):
        from datetime import timedelta
        import logging
        from homeassistant.const import __version__
        from homeassistant.setup import async_setup_component
        from homeassistant.components.scene import DATA_COMPONENT
        from homeassistant.helpers.entity_platform import EntityPlatform
        if __version__ not in ('2026.11.0', '2026.11.1'):
            self.skipTest('Core 11 only')
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'scene', {'scene': []}))
        await hass.async_block_till_done()
        component = hass.data[DATA_COMPONENT]
        platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
            domain='scene', platform_name='foreign_test', platform=None,
            scan_interval=timedelta(seconds=30), entity_namespace=None)
        component._platforms['foreign_test'] = platform
        scene = importlib.import_module(fixture.fixtures.PACKAGE + '.native_empty_scene')
        tx = importlib.import_module(fixture.fixtures.PACKAGE + '.transaction')
        try:
            with self.assertRaises(tx.Refused):
                scene.NativeEmptySceneFence(hass)
            platform._setup_complete = True
            fence = scene.NativeEmptySceneFence(hass)
            try:
                fence.check()
                platform.entities['scene.foreign'] = object()
                with self.assertRaises(tx.Refused):
                    fence.check()
                platform.entities.clear()
            finally:
                fence.close()
            fence = scene.NativeEmptySceneFence(hass)
            try:
                hass.states.async_set('scene.foreign', 'scening')
                hass.states.async_remove('scene.foreign')
                await hass.async_block_till_done()
                with self.assertRaises(tx.Refused):
                    fence.check()
            finally:
                fence.close()
        finally:
            del component._platforms['foreign_test']
