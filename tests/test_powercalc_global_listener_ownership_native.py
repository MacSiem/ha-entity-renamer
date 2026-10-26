"""Actual official vendor current-only seam tests; owner FIFO executes.

Missing product API is integration RED after genuine native prerequisites.
"""
import asyncio
import importlib
import json
import sys
import unittest

import test_powercalc_reload_witness_native as existing

try:
    subject=importlib.import_module(existing.profiles.fixtures.PACKAGE+'.powercalc_global_listener_ownership')
except ModuleNotFoundError as error:
    if error.name!=existing.profiles.fixtures.PACKAGE+'.powercalc_global_listener_ownership':
        raise
    subject=None


@unittest.skipUnless(existing.profiles.fixtures.HAS_HA,'requires actual current pinned HA')
class PowercalcGlobalListenerOwnershipNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes=existing.PowercalcReloadWitnessNativeTests.namespace_source_bytes
    asyncSetUp=existing.PowercalcReloadWitnessNativeTests.asyncSetUp
    _setup_real_vendor=existing.PowercalcReloadWitnessNativeTests._setup_real_vendor
    _flush_documents=existing.PowercalcReloadWitnessNativeTests._flush_documents
    guard=existing.PowercalcReloadWitnessNativeTests.guard
    prepare=existing.PowercalcReloadWitnessNativeTests.prepare
    bind=existing.PowercalcReloadWitnessNativeTests.bind
    assert_bound=existing.PowercalcReloadWitnessNativeTests.assert_bound
    ready_original=existing.PowercalcReloadWitnessNativeTests.ready_original

    def actual_pairs(self):
        from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
        from custom_components.powercalc.sensor import _register_entity_id_change_listener
        codes=existing.subject._code_objects(_register_entity_id_change_listener.__code__)
        targets=[code for code in codes if code.co_qualname.endswith('.<locals>._entity_rename_listener')]
        self.assertEqual(len(targets),1)
        result={}
        for pair in self.f.hass.bus._listeners.get(EVENT_ENTITY_REGISTRY_UPDATED,()):
            target=pair[0].target
            if getattr(target,'__code__',None) is not targets[0]:
                continue
            cells=dict(zip(target.__code__.co_freevars,
                (cell.cell_contents for cell in target.__closure__),strict=True))
            self.assertIs(cells['hass'],self.f.hass)
            self.assertIn(cells['entry'],(self.entry,self.group,self.subtract))
            result[str(id(pair[0]))]=(pair,cells['entry'])
        return result

    async def ready(self):
        await self.ready_original()
        self.assertIsNotNone(subject,'typed native global-listener ownership API is missing')

    async def reload_owned(self):
        return await subject.async_reload_with_powercalc_global_listener_ownership(self.f.hass,
            self.receipt,self.evidence,self.first,guard=self.guard)

    async def test_actual_detach_new_registration_and_native_unload_ownership(self):
        await self.ready()
        original=(self.evidence.sha256,self.first.structural_token,self.first.payload_json)
        before=self.actual_pairs()
        self.assertEqual({entry.entry_id for _,entry in before.values()},
                         {self.entry.entry_id,self.subtract.entry_id})
        result=await self.reload_owned()
        self.assertIs(type(result),subject.PowercalcOwnedReloadWitness)
        proof=result.global_rename
        self.assertIs(type(proof),subject.PowercalcGlobalRenameReceipt)
        self.assertTrue(proof.global_callback_ownership_verified)
        for record in (result,proof,result.lifecycle):
            self.assertFalse(record.continuity_verified)
            self.assertFalse(record.migration_ready)
            self.assertFalse(record.family_complete)
            self.assertEqual(record.status,'UNKNOWN')
        self.assertFalse(result.reload_ownership_verified)
        self.assertFalse(result.lifecycle.reload_ownership_verified)
        self.assertEqual((proof.original_evidence_sha256,proof.original_runtime_token),original[:2])
        self.assertEqual((self.evidence.sha256,self.first.structural_token,self.first.payload_json),original)
        after=self.actual_pairs()
        self.assertFalse(set(before)&set(after))
        self.assertEqual(set(after),set(json.loads(proof.actual_new_pairs_json)))
        self.assertEqual(len(after),len(before))
        self.assertEqual({entry.entry_id for _,entry in after.values()},
                         {self.entry.entry_id,self.subtract.entry_id})
        owners=json.loads(proof.native_unload_owners_json)
        operations=json.loads(proof.native_operations_json)
        for key,(pair,entry) in after.items():
            owner=[item for item in entry._on_unload
                   if type(item) is subject._NativeUnsubscribe and item.pair is pair]
            self.assertEqual(len(owner),1)
            self.assertEqual(id(owner[0]),owners[key]['owner_process_id'])
            self.assertEqual(id(owner[0].unsubscribe),owners[key]['unsubscribe_process_id'])
            self.assertIn(['global_add','start',key],operations)
            self.assertIn(['global_add','return',key],operations)
        for key in before:
            self.assertEqual(operations.count(['global_remove','start',key]),1)
            self.assertEqual(operations.count(['global_remove','return',key]),1)
        # Native ConfigEntry unload invokes its registered actual cleanup. The
        # test never calls callback/filter/unsubscribe/owner manually.
        self.assertTrue(await self.f.hass.config_entries.async_unload(self.entry.entry_id))
        remaining=self.actual_pairs()
        self.assertEqual({entry.entry_id for _,entry in remaining.values()},
                         {self.subtract.entry_id})
        self.assertFalse(set(before)&set(remaining))

    async def test_actual_cancellation_after_registration_restores_exact_original_pairs(self):
        await self.ready()
        from homeassistant.core import EventBus
        before=self.actual_pairs()
        caller=asyncio.current_task()
        monitor=sys.monitoring
        free=[key for key in range(6) if monitor.get_tool(key) is None]
        self.assertTrue(free)
        tool=free[0]
        code=EventBus._async_listen_filterable_job.__code__
        fired=[]
        def returned(actual_code,offset,result):
            frame=sys._getframe(1)
            pair=frame.f_locals.get('filterable_job')
            if (not fired and frame.f_locals.get('self') is self.f.hass.bus
                    and type(pair) is tuple and len(pair)==2
                    and getattr(pair[0].target,'__module__',None)=='custom_components.powercalc.sensor'
                    and getattr(pair[0].target,'__qualname__','').endswith('.<locals>._entity_rename_listener')):
                fired.append(str(id(pair[0])))
                caller.cancel()  # REAL caller Task; real service descendants drain.
        monitor.use_tool_id(tool,'test.powercalc.global_cancel')
        monitor.register_callback(tool,monitor.events.PY_RETURN,returned)
        monitor.set_local_events(tool,code,monitor.events.PY_RETURN)
        try:
            with self.assertRaises(asyncio.CancelledError):
                await self.reload_owned()
        finally:
            monitor.set_local_events(tool,code,0)
            monitor.register_callback(tool,monitor.events.PY_RETURN,None)
            monitor.free_tool_id(tool)
            if fired:
                caller.uncancel()
        self.assertEqual(len(fired),1)
        restored=self.actual_pairs()
        self.assertEqual(set(restored),set(before))
        for key,(pair,entry) in restored.items():
            self.assertIs(pair,before[key][0])
            self.assertIs(entry,before[key][1])
            self.assertEqual(len([item for item in entry._on_unload
                if type(item) is subject._NativeUnsubscribe and item.pair is pair]),1)
        # Scope rollback restores listener membership only; no assertion claims
        # restoration of old entities, numeric state, clock or ORIGINAL runtime.

    async def test_actual_profile_drift_refuses_without_detach(self):
        await self.ready()
        before=self.actual_pairs()
        path=self.profile_root/'signify/LWA001/unselected.json'
        path.write_bytes(b'{}\n')
        try:
            with self.assertRaises(ValueError):
                await self.reload_owned()
            after=self.actual_pairs()
            self.assertEqual(set(after),set(before))
            for key,(pair,entry) in after.items():
                self.assertIs(pair,before[key][0])
                self.assertIs(entry,before[key][1])
            self.runtime.assert_powercalc_runtime_current(self.f.hass,self.first,guard=self.guard)
        finally:
            path.unlink()  # Cleanup only after negative readback assertions.

    async def test_actual_source_name_takeover_cannot_reactivate_original_callbacks(self):
        # A second REAL producer exists before SAME ORIGINAL preparation. It is
        # not introduced as an unbound late input, nor assigned another UID.
        foreign=existing.profiles.ProfileLight('Foreign physical dynamic',dynamic=True)
        await self.sources[1].platform.async_add_entities([foreign])
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        await self.ready()
        from custom_components.powercalc import register_services
        from homeassistant.helpers.reload import async_integration_yaml_config
        before=self.actual_pairs()
        registry=self.f.entity
        original_id=self.gui_source
        moved_id=original_id+'_foreign_moved'
        foreign_id=foreign.entity_id
        original_row=registry.async_get(original_id)
        foreign_row=registry.async_get(foreign_id)
        self.assertIsNotNone(original_row)
        self.assertIsNotNone(foreign_row)
        self.assertNotEqual(original_row.id,foreign_row.id)
        self.assertNotEqual(original_row.unique_id,foreign_row.unique_id)
        self.assertIsNone(registry.async_get(moved_id))
        metadata=[dict(self.entry.data),dict(self.entry.options),self.entry.unique_id]
        roots=[code for code in existing.subject._code_objects(register_services.__code__)
               if code.co_qualname=='register_services.<locals>._reload_config']
        self.assertEqual(len(roots),1)
        caller=asyncio.current_task()
        monitor=sys.monitoring
        free=[key for key in range(6) if monitor.get_tool(key) is None]
        self.assertTrue(free)
        tool=free[0]
        fired=[]
        observer_errors=[]
        def started(code,offset):
            frame=sys._getframe(1)
            root=frame.f_back
            call=None if root is None else root.f_locals.get('_')
            if (fired or root is None or root.f_code is not roots[0]
                    or getattr(call,'hass',None) is not self.f.hass):
                return
            fired.append(True)
            try:
                # Enter the real YAML-loader coroutine from native reload. All
                # root PY_START observers have completed; old pairs are detached
                # and no new GUI setup has registered its callbacks yet.
                self.assertFalse(set(before)&set(self.actual_pairs()))
                registry.async_update_entity(original_id,new_entity_id=moved_id)
                registry.async_update_entity(foreign_id,new_entity_id=original_id)
                self.assertEqual(registry.async_get(original_id).id,foreign_row.id)
                self.assertEqual(registry.async_get(moved_id).id,original_row.id)
            except BaseException as error:
                observer_errors.append(type(error).__module__+'.'+type(error).__qualname__)
            finally:
                caller.cancel()  # Actual caller cancellation; native descendants drain.
        monitor.use_tool_id(tool,'test.powercalc.source_takeover')
        monitor.register_callback(tool,monitor.events.PY_START,started)
        loader_code=async_integration_yaml_config.__code__
        monitor.set_local_events(tool,loader_code,monitor.events.PY_START)
        caught=None
        try:
            try:
                await self.reload_owned()
            except BaseException as error:
                caught=error
        finally:
            monitor.set_local_events(tool,loader_code,0)
            monitor.register_callback(tool,monitor.events.PY_START,None)
            monitor.free_tool_id(tool)
            if fired:
                caller.uncancel()
        try:
            self.assertEqual(fired,[True])
            self.assertEqual(observer_errors,[])
            self.assertEqual([dict(self.entry.data),dict(self.entry.options),self.entry.unique_id],metadata)
            self.assertEqual(self.entry.state.value,'loaded')
            self.assertEqual(registry.async_get(original_id).id,foreign_row.id)
            self.assertEqual(registry.async_get(original_id).unique_id,foreign_row.unique_id)
            self.assertEqual(registry.async_get(moved_id).id,original_row.id)
            self.assertIs(foreign.registry_entry,registry.async_get(original_id))
            self.assertEqual(foreign.entity_id,original_id)
            self.assertEqual(self.sources[1].entity_id,moved_id)
            after=self.actual_pairs()
            self.assertFalse(set(before)&set(after),
                'original callbacks were reactivated against a foreign physical source')
            self.assertEqual(after,{})  # Own new pairs removed; no stale original restored.
            self.assertIs(type(caught),ValueError)
            self.assertEqual(str(caught),
                'powercalc_reload_global_rollback_source_identity_changed_original_not_reactivated')
            # No returned witness/accepted receipt. Do NOT require the stale
            # whole Powercalc runtime to equal current after a real reload.
        finally:
            # Restore only native entity names AFTER all negative assertions.
            # Neither source UID nor numeric/clock/cache fields are assigned.
            current=registry.async_get(original_id)
            if current is not None and current.id==foreign_row.id:
                registry.async_update_entity(original_id,new_entity_id=foreign_id)
            if registry.async_get(moved_id) is not None:
                registry.async_update_entity(moved_id,new_entity_id=original_id)
            await self.f.hass.async_block_till_done()
