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
        from dataclasses import replace
        import tempfile
        worker=importlib.import_module(fixture.PACKAGE+'.yaml_validation_worker')
        homekit_binding=importlib.import_module(fixture.PACKAGE+'.native_homekit_yaml_binding')
        with tempfile.TemporaryDirectory(dir=root.parent) as scratch:
            candidate=__import__('pathlib').Path(scratch)
            (candidate/'configuration.yaml').write_text(yaml(new))
            original_seal=(await worker._expected_runtime(str(root), ['homekit']))['homekit']
            candidate_seal=(await worker._expected_runtime(str(candidate), ['homekit']))['homekit']
            self.assertIsNotNone(original_seal)
            self.assertIsNotNone(candidate_seal)
            receipt=replace(fixture.receipt_for(root, '2026.11.1'), changed_domains=('homekit',),
                unsupported_domains=('homekit',), runtime_expected=(('homekit', original_seal, candidate_seal),))
            binding=await homekit_binding.HomeKitYAMLBinding.capture(hass,{old:new},receipt,
                [proofs.RegistryMappingProof(old,new,f.row.id)],guard=lambda:None)
        observer=binding.observer
        receipt_adapter=importlib.import_module(fixture.PACKAGE+'.native_homekit_helper_adapter')
        changes=receipt_adapter.prepare_entries(binding)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].before, before)
        self.assertEqual(changes[0].after, observer.after[entry.entry_id])
        from types import SimpleNamespace
        lease_module=importlib.import_module(fixture.PACKAGE+'.native_helper_activation_lease')
        lease=await lease_module.NativeHelperActivationLease.acquire(
            hass, hass.config_entries, (entry,), guard=lambda:None)
        try:
            async def unused(*args): return True
            owner=SimpleNamespace(hass=hass, receipt=receipt, mapping={old:new}, chain=object(),
                journal=SimpleNamespace(load=unused), _scope=lambda:None, _observe=unused,
                _full=unused, plan=SimpleNamespace(steps=()))
            native_adapter=receipt_adapter.create_adapter(owner, lambda ref:None, binding, lease)
            self.assertIs(native_adapter.activation_lease, lease)
            self.assertIs(native_adapter.helper.hass, hass)
        finally:
            lease.release()
            observer.bind_activation_lease(None)
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
            refused=importlib.import_module(fixture.PACKAGE+'.transaction').Refused
            with self.assertRaises(refused):
                await observer.verify_lifecycle_runtime(
                    entry.entry_id, {'entry_id':entry.entry_id, 'state':'loaded'}, opposite)
            path.write_text(yaml(target))
            await hass.services.async_call('homekit','reload',{},blocking=True);await settle()
            await observer.verify_runtime(plan.after)
        await run(forward,old,new)
        reverse=planner.plan_helper(forward.after,{new:old});self.assertFalse(reverse.markers)
        await run(reverse,new,old)
        self.assertEqual(helper.read_entry(hass,entry.entry_id),before)
