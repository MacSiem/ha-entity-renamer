"""Native HomeKit ConfigEntry step before YAML source finalization."""
import importlib
import unittest
import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class HomeKitHelperNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_inactive_config_step_native_reactivation_and_inverse(self):
        from homeassistant import auth
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        f=fixture.NativeBaselineTests(methodName='runTest'); await f.asyncSetUp(); self.addAsyncCleanup(f.asyncTearDown)
        hass, root=f.hass,f.root
        hass.auth=await auth.auth_manager_from_config(hass,[],[])
        old,new=f.row.entity_id,'sensor.homekit_renamed_source'
        def yaml(entity):
            return ('homekit:\n  name: Synthetic Bridge\n  port: 21198\n'
                    '  ip_address: 192.0.2.1\n  advertise_ip: 192.0.2.1\n'
                    f'  filter:\n    exclude_entities:\n      - {entity}\n')
        path=root/'configuration.yaml';path.write_text(yaml(old))
        self.assertTrue(await async_setup_component(hass,'homekit',await async_hass_config_yaml(hass)))
        await hass.async_block_till_done()
        await hass.services.async_call('homekit','reload',{},blocking=True);await hass.async_block_till_done()
        entry=hass.config_entries.async_entries('homekit')[0]
        helper=importlib.import_module(fixture.PACKAGE+'.helper_native')
        planner=importlib.import_module(fixture.PACKAGE+'.helper_plan')
        obsmod=importlib.import_module(fixture.PACKAGE+'.native_homekit_helper_observers')
        proofs=importlib.import_module(fixture.PACKAGE+'.helper_observers')
        before=helper.read_entry(hass,entry.entry_id)
        observer=await obsmod.NativeHomeKitHelperObservers.capture(hass,[before],
            [proofs.RegistryMappingProof(old,new,f.row.id)])
        forward=planner.plan_helper(before,{old:new});self.assertFalse(forward.markers)
        async def settle():await hass.async_block_till_done()
        async def intent(a,b):pass
        def guard():pass
        async def run(plan, source, target):
            self.assertTrue(await hass.config_entries.async_unload(entry.entry_id));await settle()
            self.assertTrue(await observer.verify_inactive(entry.entry_id))
            f.entity.async_update_entity(source,new_entity_id=target);await settle()
            result=await helper.apply_entry(hass,plan,guard=guard,settle=settle,record_intent=intent,
                validate_snapshot=observer.validate_snapshot,verify_runtime=observer.verify_runtime)
            self.assertTrue(result['adapter_write']);self.assertIsNotNone(result['_native_config_mutation'])
            self.assertTrue(await hass.config_entries.async_setup(entry.entry_id));await settle()
            await observer.verify_runtime(plan.after)
            lifecycle=importlib.import_module(fixture.PACKAGE+'.native_helper_lifecycle')
            auth=lifecycle.RestoreAuthorization('operation', 'f'*64, 'a'*64,
                                                'applied' if source == old else 'rolled_back')
            self.assertTrue(await observer.verify_lifecycle_runtime(
                entry.entry_id, {'entry_id':entry.entry_id, 'state':'loaded'}, auth))
            opposite=lifecycle.RestoreAuthorization('operation', 'f'*64, 'a'*64,
                         'rolled_back' if source == old else 'applied')
            with self.assertRaises(Exception):
                await observer.verify_lifecycle_runtime(
                    entry.entry_id, {'entry_id':entry.entry_id, 'state':'loaded'}, opposite)
            path.write_text(yaml(target))
            await hass.services.async_call('homekit','reload',{},blocking=True);await settle()
            await observer.verify_runtime(plan.after)
        await run(forward,old,new)
        reverse=planner.plan_helper(forward.after,{new:old});self.assertFalse(reverse.markers)
        await run(reverse,new,old)
        self.assertEqual(helper.read_entry(hass,entry.entry_id),before)
