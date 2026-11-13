"""Actual HAP bridge accessory survives a source rename without pairing reset."""
import importlib
import logging
import unittest
from datetime import timedelta
import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class HomeKitExportIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_exported_accessory_aid_is_stable_forward_and_inverse(self):
        from homeassistant import auth
        from homeassistant.components.switch import SwitchEntity
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.setup import async_setup_component
        base = fixture.NativeBaselineTests(methodName='runTest')
        await base.asyncSetUp(); self.addAsyncCleanup(base.asyncTearDown)
        hass, root = base.hass, base.root
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        class Source(SwitchEntity):
            _attr_should_poll = False
            _attr_name = 'Synthetic HAP source'
            _attr_unique_id = 'synthetic-hap-source-unique'
            _attr_is_on = False
            async def async_turn_on(self, **kwargs):
                self._attr_is_on = True; self.async_write_ha_state()
            async def async_turn_off(self, **kwargs):
                self._attr_is_on = False; self.async_write_ha_state()
        component = EntityComponent(logging.getLogger(__name__), 'switch', hass, timedelta(seconds=60))
        source = Source(); await component.async_add_entities([source]); await hass.async_block_till_done()
        old, new = source.entity_id, 'switch.synthetic_hap_source_renamed'
        def yaml(entity):
            return ('homekit:\n  name: Synthetic Bridge\n  port: 21197\n'
                    '  ip_address: 192.0.2.1\n  advertise_ip: 192.0.2.1\n'
                    f'  filter:\n    include_entities:\n      - {entity}\n')
        config = root/'configuration.yaml'; config.write_text(yaml(old))
        self.assertTrue(await async_setup_component(hass, 'homekit', await async_hass_config_yaml(hass)))
        await hass.async_block_till_done()
        await hass.services.async_call('homekit', 'reload', {}, blocking=True); await hass.async_block_till_done()
        entry = hass.config_entries.async_entries('homekit')[0]
        helper = importlib.import_module(fixture.PACKAGE + '.helper_native')
        planner = importlib.import_module(fixture.PACKAGE + '.helper_plan')
        identity = importlib.import_module(fixture.PACKAGE + '.native_homekit_filter')
        def exported(expected):
            native = entry.runtime_data.homekit
            rows = [(aid, accessory) for aid, accessory in native.bridge.accessories.items()
                    if getattr(accessory, 'entity_id', None) == expected]
            self.assertEqual(len(rows), 1)
            return rows[0][0], identity._bridge_identity(entry)
        before_aid, before_bridge = exported(old)
        original = helper.read_entry(hass, entry.entry_id)
        async def settle(): await hass.async_block_till_done()
        async def intent(*args): pass
        async def verify(*args): return True
        async def transition(source_id, target_id):
            plan = planner.plan_helper(helper.read_entry(hass, entry.entry_id), {source_id:target_id})
            self.assertFalse(plan.blocked)
            self.assertTrue(await hass.config_entries.async_unload(entry.entry_id)); await settle()
            base.entity.async_update_entity(source_id, new_entity_id=target_id); await settle()
            await helper.apply_entry(hass, plan, guard=lambda:None, settle=settle,
                record_intent=intent, verify_runtime=verify)
            self.assertTrue(await hass.config_entries.async_setup(entry.entry_id)); await settle()
            config.write_text(yaml(target_id))
            await hass.services.async_call('homekit', 'reload', {}, blocking=True); await settle()
            aid, bridge = exported(target_id)
            self.assertEqual((aid, bridge), (before_aid, before_bridge))
        await transition(old, new)
        await transition(new, old)
        self.assertEqual(helper.read_entry(hass, entry.entry_id), original)
