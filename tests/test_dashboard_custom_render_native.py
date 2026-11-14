"""Single synthetic native view, browser-driven controls and exact inverse."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import unittest
import test_native_input_baseline as fixtures


@unittest.skipUnless(fixtures.HAS_HA and os.environ.get('RENAMER_CUSTOM_RENDER'), 'owned browser fixture required')
class DashboardCustomRender(unittest.IsolatedAsyncioTestCase):
    async def test_active_store_render_control_and_fresh_inverse(self):
        from homeassistant import auth
        from homeassistant.auth.const import GROUP_ID_ADMIN
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        from homeassistant.components.onboarding import OnboardingStorage, STORAGE_KEY, STORAGE_VERSION
        from homeassistant.components.onboarding.const import DOMAIN as ONBOARDING, STEPS
        from homeassistant.components.onboarding.views import _BaseOnboardingStepView
        fixture = fixtures.NativeBaselineTests(methodName='runTest')
        await fixture.asyncSetUp()
        self.addAsyncCleanup(fixture.asyncTearDown)
        hass, root = fixture.hass, fixture.root
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        self.assertTrue(await async_setup_component(hass, 'http', {'http':{'server_host':'127.0.0.1', 'server_port':port}}))
        self.assertTrue(await async_setup_component(hass, 'config', {}))
        self.assertTrue(await async_setup_component(hass, ONBOARDING, {}))
        onboarding = hass.data[ONBOARDING]
        if not onboarding.onboarded:
            store = OnboardingStorage(hass, STORAGE_VERSION, STORAGE_KEY, private=True)
            step = _BaseOnboardingStepView(onboarding.steps, store)
            for name in STEPS:
                if name not in onboarding.steps['done']:
                    step.step = name; await step._async_mark_done(hass)
        self.assertTrue(await async_setup_component(hass, 'switch', {}))
        from homeassistant.components.switch import SwitchEntity
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        class SyntheticSwitch(SwitchEntity):
            _attr_name = 'Synthetic Toggle'
            _attr_unique_id = 'p3-synthetic-toggle'
            _attr_should_poll = False
            _attr_is_on = False
            async def async_turn_on(self, **kwargs):
                self._attr_is_on = True; self.async_write_ha_state()
            async def async_turn_off(self, **kwargs):
                self._attr_is_on = False; self.async_write_ha_state()
        switch = SyntheticSwitch()
        await hass.data[DATA_INSTANCES]['switch'].async_add_entities([switch])
        await hass.async_block_till_done()
        self.assertEqual(switch.entity_id, 'switch.synthetic_toggle')
        self.assertIsNotNone(hass.states.get(switch.entity_id))
        from homeassistant.helpers.storage import Store
        metadata = {'id':'p3_test', 'url_path':'p3-test', 'mode':'storage',
                    'require_admin':False, 'show_in_sidebar':True,
                    'title':'Synthetic dashboard', 'icon':'mdi:toggle-switch'}
        await Store(hass, 1, 'lovelace_dashboards').async_save({'items':[metadata]})
        self.assertTrue(await async_setup_component(hass, 'lovelace', await async_hass_config_yaml(hass)))
        www = root / 'www'; www.mkdir(exist_ok=True)
        for name in ('button-card.js','bubble-card.js'):
            shutil.copyfile(Path('/assets') / name, www / name)
        self.assertTrue(await async_setup_component(hass, 'frontend', {}))
        self.assertTrue(await async_setup_component(hass, 'api', {}))
        from homeassistant.components.http.config import DATA_STORE
        http_store = hass.data[DATA_STORE]
        if http_store._pending is not None: await http_store.async_promote_pending()
        self.assertIsNone(http_store._pending)
        from aiohttp import ClientSession
        import hashlib
        async with ClientSession() as client:
            for name in ('button-card.js','bubble-card.js'):
                async with client.get(f'http://127.0.0.1:{port}/local/{name}') as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(hashlib.sha256(await response.read()).digest(),
                                     hashlib.sha256((Path('/assets') / name).read_bytes()).digest())
        native = hass.data[LOVELACE_DATA]
        await native.resources.async_get_info()
        for name in ('button-card.js','bubble-card.js'):
            await native.resources.async_create_item({'res_type':'module','url':'/local/' + name})
        self.assertEqual({(r['url'],r['type']) for r in native.resources.async_items()},
                         {('/local/button-card.js','module'),('/local/bubble-card.js','module')})
        old, new = 'switch.synthetic_toggle', 'switch.renamed_toggle'
        config = {'views':[{'title':'Synthetic', 'cards':[
            {'type':'button','entity':old},
            {'type':'custom:button-card','entity':old,'triggers_update':[old],
             'variables':{'entity_ids':[old]}, 'label':"[[[ return states['switch.synthetic_toggle'].state; ]]]",
             'show_label':True, 'tap_action':{'action':'toggle'}},
            {'type':'custom:bubble-card','card_type':'button','entity':old,
             'sub_button':[{'entity':old,'visibility':[{'condition':'state','entity':old,'state':'on'}]}],
             'tap_action':{'action':'toggle'}},
        ]}]}
        manager = native.dashboards['p3-test']
        await manager.async_save(config)
        user = await hass.auth.async_create_user('Synthetic Admin', group_ids=[GROUP_ID_ADMIN])
        refresh = await hass.auth.async_create_refresh_token(user, client_id=f'http://127.0.0.1:{port}/')
        access = hass.auth.async_create_access_token(refresh, remote_ip='127.0.0.1')
        directory = Path(os.environ['RENAMER_CUSTOM_RENDER'])
        token = directory / 'render-token'; token.write_text(access); token.chmod(0o600)
        async def render(label, entity):
            request = {'label':label, 'source':entity, 'port':port}
            temp = directory / 'request.tmp'; temp.write_text(json.dumps(request)); temp.replace(directory / 'request.json')
            ack = directory / (label + '-done.json')
            for _ in range(1200):
                if ack.exists():
                    result = json.loads(ack.read_text())
                    self.assertEqual(result.get('error'), None, result)
                    self.assertTrue(result.get('visible'))
                    self.assertTrue(result.get('button_toggled'))
                    self.assertTrue(result.get('bubble_toggled'))
                    self.assertEqual(result.get('refs'), [entity]*3)
                    return
                await asyncio.sleep(.1)
            self.fail('browser fixture timed out')
        plan = importlib.import_module(fixtures.PACKAGE + '.dashboard_plan').plan_dashboard
        await render('before', old)
        forward = plan(config, {old:new}); self.assertFalse(forward.markers)
        fixture.entity.async_update_entity(old, new_entity_id=new)
        await hass.async_block_till_done(); await manager.async_save(forward.value)
        self.assertEqual(await manager.async_load(False), forward.value)
        await render('after', new)
        reverse = plan(forward.value, {new:old}); self.assertFalse(reverse.markers)
        fixture.entity.async_update_entity(new, new_entity_id=old)
        await hass.async_block_till_done(); await manager.async_save(reverse.value)
        self.assertEqual(await manager.async_load(False), config)
        await render('inverse', old)
