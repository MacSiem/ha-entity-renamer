"""Actual Coordinator registry/config/YAML source-clock forward and reverse.

Four fixtures: left/right/trapezoidal and saved-registry-UUID source. The oracle
uses actual emission times; it never seeds helper fields/cache or creates Event.
"""
import asyncio
from datetime import timedelta
from decimal import Decimal
import importlib
import json
import logging
import os
import threading
from pathlib import Path
from types import MappingProxyType
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixture
import test_native_helper_quiescence as quiescence
import test_receipted_helper_native as mixed

PACKAGE = fixture.PACKAGE


@unittest.skipUnless(fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationSourceClockTests(unittest.IsolatedAsyncioTestCase):
    method = 'left'
    saved_uuid = False
    with_yaml = True
    asyncSetUp = quiescence.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = mixed.NativeReceiptedHelperTests._before_start
    _flush_documents = quiescence.NativeHelperQuiescenceTests._flush_documents

    def current(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        entities = [e for p in async_get_platforms(self.f.hass,'integration')
                    if p.config_entry is self.entry for e in p.entities.values()]
        self.assertEqual(len(entities),1)
        return entities[0]

    def guard(self):
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id),self.entry)
        self.assertEqual(self.f.entity.async_get(self.helper_id).id,self.helper_uuid)
        rows = [r for r in self.f.entity.entities.values() if r.id == self.source_uuid]
        self.assertEqual(len(rows),1)
        self.assertIn(rows[0].entity_id,self.names)
        return True  # Identity fence only; typed binding/raw chain supplies authority.

    async def wait_tick(self):
        from homeassistant.core import callback
        from homeassistant.helpers.event import async_track_state_change_event
        entity = self.current()
        future = asyncio.get_running_loop().create_future()
        @callback
        def observed(event):
            if entity._state is not None and not future.done():
                future.set_result(True)
        unsubscribe = async_track_state_change_event(self.f.hass,self.helper_id,observed)
        try:
            await asyncio.wait_for(future,10)
        finally:
            unsubscribe()
        await self.f.hass.async_block_till_done()
        self.assertEqual(entity._last_integration_trigger.name,'TimeElapsed')

    def area(self,left,right,elapsed):
        left,right = Decimal(left),Decimal(right)
        if self.method == 'left':
            return elapsed*left
        if self.method == 'right':
            return elapsed*right
        return elapsed*(left+right)/2

    async def test_real_coordinator_source_forward_reverse_with_timer_and_reports(self):
        await self._run_source_clock()

    async def _run_source_clock(self):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.components.integration.config_flow import ConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.core import Context, callback
        from homeassistant.helpers.event import (async_track_state_change_event,
            async_track_state_report_event)
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.helpers import entity_registry as er
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        m = {name:importlib.import_module(PACKAGE+'.'+name) for name in (
            'native_integration_continuity',
            'native_registry','native_registry_receipts','native_registry_transaction',
            'native_helper_activation_lease','native_helper_transaction','helper_native','helper_transaction',
            'native_input_observation','native_input_baseline','native_helper_lifecycle',
            'recorder_guard','history_guard','prepared','journal','transaction',
            'yaml_plan','yaml_validation','yaml_transaction','yaml_editor_locks','yaml_runtime_observer',
            'native_yaml_transaction','native_candidate_validation','native_utility_source_emissions')}
        hass,root = self.f.hass,self.f.root
        class Producer(SensorEntity):
            _attr_name = 'Source clock producer'
            _attr_unique_id = 'source-clock-producer'
            _attr_should_poll = False
            _attr_native_unit_of_measurement = 'W'
            _attr_native_value = Decimal('10')
        self.producer = Producer()
        component = EntityComponent(logging.getLogger(__name__),'sensor',hass,timedelta(seconds=60))
        await component.async_add_entities([self.producer])
        self.names = (self.producer.entity_id,'sensor.source_clock_changed')
        self.source_uuid = self.f.entity.async_get(self.names[0]).id
        self.entry = ConfigEntry(domain='integration',title='Source clock actual',data={},
            options={'name':'Source clock actual','source':self.source_uuid if self.saved_uuid else self.names[0],
                     'method':self.method,'unit_time':'s','round':None,'max_sub_interval':{'seconds':1}},
            version=ConfigFlowHandler.VERSION,minor_version=ConfigFlowHandler.MINOR_VERSION,
            source='user',discovery_keys=MappingProxyType({}),subentries_data=[],unique_id='source-clock-actual')
        await hass.config_entries.async_add(self.entry)
        await hass.async_block_till_done()
        self.helper_id = self.current().entity_id
        self.helper_uuid = self.f.entity.async_get(self.helper_id).id
        await self.wait_tick()
        (root/'scripts').mkdir()
        (root/'configuration.yaml').write_text('script: !include_dir_merge_named scripts\n')
        (root/'scripts/probe.yaml').write_text('source_clock_probe:\n  sequence:\n'
            '    - action: source_clock_probe.capture\n      data:\n'
            '        value: "{{ states(\''+self.names[0]+'\') }}"\n')
        script_values=[]
        async def capture(call):
            script_values.append(str(call.data['value']))
        hass.services.async_register('source_clock_probe','capture',capture)
        self.assertTrue(await async_setup_component(hass,'script',await async_hass_config_yaml(hass)))
        await self._flush_documents(initialize=True)
        mapping={self.names[0]:self.names[1]}
        proposed=m['yaml_plan'].plan_yaml(root,mapping)
        self.assertTrue(proposed.ready,proposed.issues)
        receipt,source_evidence=await m['yaml_validation'].async_validate_proposed_with_sources(
            hass,proposed,root.parent)
        self.assertTrue(receipt.ready_for_apply)
        original=await m['native_input_baseline'].async_capture_native_input_baseline(hass,receipt,guard=self.guard)
        preparations=m['prepared'].native_prepared_store(hass)
        await preparations.initialize()
        inputs=await preparations.create_native_inputs('source-clock-original',original)
        recorder=m['recorder_guard'].NativeRecorderGuard(hass)
        history=m['history_guard'].NativeHistoryGuard(recorder)
        io=m['native_registry'].NativeRegistryIO(hass)
        bridge=m['native_registry'].NativeRegistryBridge(io,recorder,history_guard=history)
        registry_change=await bridge.prepare('entity',self.source_uuid,{'entity_id':self.names[1]},self.guard)
        before=m['helper_native'].read_entry(hass,self.entry.entry_id)
        helper_change=(None if self.saved_uuid else m['helper_transaction'].prepare_helper(before,mapping))
        yaml_change=await m['yaml_validation']._settled_executor(hass,m['yaml_transaction']._capture,receipt,proposed)
        changes=[('registry',registry_change)]
        if helper_change is not None:
            changes.append(('helpers',helper_change))
        changes.append(('yaml',yaml_change))
        digests,steps={},[]
        for i,(adapter,change) in enumerate(changes):
            ref='source-clock-'+str(i)
            digests[ref]=await preparations.create(ref,m['prepared'].encode_change(change))
            steps.append(m['transaction'].Step(ref,adapter,change.identity,change.before_revision,change.after_revision,ref))
        scopes=tuple(adapter for adapter,_ in changes)
        plan=m['transaction'].Plan('source-clock',scopes,scopes,tuple(steps),'actual-clock-source',scopes,inputs)
        store=m['native_registry_receipts'].native_registry_receipt_store(hass,preparations)
        await store.initialize()
        chain=await m['native_registry_receipts'].RegistryReceiptChain.start(store,'source-clock-operation',plan,receipt,digests)
        bridge=m['native_registry'].NativeRegistryBridge(io,recorder,history_guard=history,native_images=chain.accepted_documents)
        journal=m['journal'].native_journal(hass)
        await journal.initialize()
        observation_state=chain.reconstruct_inputs()
        async def observe(input_state=None):
            nonlocal observation_state
            if input_state is None:
                try:
                    observation_state=chain.reconstruct_inputs()
                except m['native_registry_receipts'].RegistryReceiptError:
                    self.assertIsNotNone(chain.active)
                input_state=observation_state
            return await m['native_input_observation'].async_observe_native_inputs(
                hass,receipt,guard=self.guard,input_state=input_state)
        lease=await m['native_helper_activation_lease'].NativeHelperActivationLease.acquire(
            hass,hass.config_entries,(self.entry,),guard=self.guard)
        self.scope=None
        candidate_proofs=[]
        yaml_observer=m['yaml_runtime_observer'].NativeYAMLRuntimeReadback(hass)
        locks=m['yaml_editor_locks'].NativeYAMLEditorLocks(hass)
        def retained_guard():
            if self.scope is not None and threading.get_ident() != self.scope.thread:
                from homeassistant.util.async_ import run_callback_threadsafe
                return run_callback_threadsafe(hass.loop, retained_guard).result()
            self.guard()
            self.scope._check()
            self.assertTrue(self.scope._retained)
            self.assertEqual(self.scope._phase,'retained')
            m['native_helper_transaction']._require_inactive(self.entry,lease)
            self.assertEqual(self.entry.state.value,'not_loaded')
            self.scope._timer(self.scope.old)  # Explicit owned retained exception.
        async def validate_snapshot(snapshot):
            self.assertIn(snapshot,(before,helper_change.after if helper_change else before))
            self.assertEqual(snapshot['domain'],'integration')
            return True
        async def inactive_runtime(snapshot):
            retained_guard()
            await validate_snapshot(snapshot)
            self.assertEqual(m['helper_native'].read_entry(hass,self.entry.entry_id),snapshot)
            return True
        async def verify_inventory(actual_plan,reverse=False):
            retained_guard()
            self.assertEqual(actual_plan,plan)
            expected=before if reverse or helper_change is None else helper_change.after
            self.assertEqual(m['helper_native'].read_entry(hass,self.entry.entry_id),expected)
            return True
        resolver=lambda ref:m['prepared'].decode_change(preparations.resolve(ref,digests[ref]))
        adapters={}
        async def verify(position):
            retained_guard()
            self.assertEqual((await bridge.inspect(registry_change)).position,position)
            self.assertEqual(await yaml_observer.position(yaml_change),
                m['transaction'].Position.AFTER if position=='after' else m['transaction'].Position.BEFORE)
            return await verify_inventory(plan,reverse=position=='before')
        if helper_change is not None:
            helper=m['helper_transaction'].HelperTransactionAdapter(hass,resolver,
                resolve_step=lambda key:next(s for s in steps if s.key==key),settle=hass.async_block_till_done,
                validate_snapshot=validate_snapshot,verify_activation=inactive_runtime,verify_runtime=inactive_runtime,
                verify_inventory=verify_inventory,verify_original_inventory=lambda p:verify_inventory(p,True))
            adapters['helpers']=m['native_helper_transaction'].ReceiptedHelperTransactionAdapter(
                helper,chain,observe,journal.load,retained_guard,
                full_verify=lambda _:verify('after'),full_verify_rollback=lambda _:verify('before'),activation_lease=lease)
        adapters['registry']=m['native_registry_transaction'].ReceiptedRegistryTransactionAdapter(
            bridge,resolver,chain,observe,journal.load,retained_guard,
            full_verify=lambda _:verify('after'),full_verify_rollback=lambda _:verify('before'))
        async def verify_yaml(position):
            await verify(position)
            async with locks.hold(retained_guard):
                proof=await m['native_candidate_validation'].async_revalidate_accepted_candidate(
                    hass,chain,journal.load,root.parent,retained_guard)
                candidate_proofs.append(proof)
            return True
        adapters['yaml']=m['native_yaml_transaction'].ReceiptedYAMLTransactionAdapter(
            hass,resolver,locks,yaml_observer,chain,observe,journal.load,retained_guard,context=Context(),
            full_verify=lambda _:verify_yaml('after'),full_verify_rollback=lambda _:verify_yaml('before'))
        coordinator=m['transaction'].Coordinator(adapters,journal,retained_guard,required_scopes=scopes)
        scopes_live=[]
        creation_checks=[]
        burst_checks=[]
        negative_hook=getattr(self,'source_clock_negative_hook',None)
        negative_context=None
        if negative_hook is not None:
            from types import SimpleNamespace
            negative_context=SimpleNamespace(m=m,hass=hass,root=root,store=store,
                chain=chain,lease=lease,observe=observe,load_record=journal.load,
                entry=self.entry,producer=self.producer)
        try:
            try:
                m['native_integration_source_clock'] = importlib.import_module(
                    PACKAGE + '.native_integration_source_clock')
            except ModuleNotFoundError as error:
                if error.name != PACKAGE + '.native_integration_source_clock':
                    raise
                self.fail('actual Integration source-ID clock binding is not implemented')
            for reverse in (False,True):
                if negative_hook is not None:
                    self.assertFalse(reverse,'negative stops after the first real direction')
                    await negative_hook('before_capture',negative_context)
                if reverse:
                    await self.wait_tick()
                self.scope=await m['native_integration_source_clock'].async_begin_source_clock(
                    hass,self.entry,lease,chain,guard=self.guard,observe=observe,load_record=journal.load,reverse=reverse)
                scopes_live.append(self.scope)
                scope=self.scope
                await scope.unload()
                await hass.async_block_till_done()
                self.assertEqual(scope._phase,'retained')
                if negative_hook is not None:
                    negative_context.scope=scope
                    if await negative_hook('retained',negative_context):
                        return
                # Genuine changed + reported inputs before actual registry migration.
                self.producer._attr_native_value=Decimal('12' if not reverse else '14')
                self.producer.async_write_ha_state()
                self.producer.async_write_ha_state()
                await hass.async_block_till_done()
                oracle_inputs={}
                endpoint=hass.states.get(self.producer.entity_id)
                oracle_previous=[endpoint.state,endpoint.last_reported]
                burst=[]
                burst_events=[]
                burst_deliveries=set()
                burst_base=[]
                burst_done=[]
                def origin(frame):
                    data=frame.f_locals.get('event_data')
                    event_type=frame.f_locals.get('event_type')
                    if type(data) is not dict or data.get('entity_id') not in self.names or event_type not in ('state_changed','state_reported'):
                        return
                    new,old=data.get('new_state'),data.get('old_state')
                    if new is None:
                        return
                    left,ot=oracle_previous
                    if event_type=='state_reported':
                        left=new.state
                        ot=data['old_last_reported']
                    nt=(data['last_reported'] if event_type=='state_reported' and self.f.version=='2026.10.2'
                        else new.last_updated if self.f.version=='2026.10.2' and event_type=='state_changed'
                        else new.last_reported)
                    record=(left,new.state,ot,nt,scope.old._state,scope.old._last_integration_time,
                            scope.old._last_integration_trigger.name)
                    oracle_inputs[id(data)]=record
                    oracle_previous[:]=[new.state,nt]
                    if burst_base:
                        burst.append(record)
                        burst_events.append(id(data))
                origin_witness=m['native_utility_source_emissions'].NativeSourceEmissions(
                    scope.codes['fire'],hass.bus,origin,lambda reason:self.fail('oracle observer '+reason))
                def assert_burst(event):
                    if (len(burst)==3 and not burst_done
                            and set(burst_events) <= burst_deliveries):
                        expected=burst_base[0]
                        for left,right,ot,nt,*rest in burst:
                            expected=expected+self.area(left,right,Decimal((nt-ot).total_seconds()))
                        self.assertEqual(scope.old._state,expected)
                        burst_done.append(True)
                        burst_checks.append(True)
                @callback
                def source_created(event):
                    data=event.data
                    if data.get('entity_id') not in self.names or data.get('new_state') is None:
                        return
                    if id(data) in burst_events:
                        burst_deliveries.add(id(data))
                    if (event.event_type=='state_changed' and data.get('old_state') is None
                            and data['entity_id']==scope.source_binding.to_name):
                        left,right,ot,nt,full,anchor,trigger=oracle_inputs[id(data)]
                        elapsed=Decimal((nt-(anchor if trigger=='TimeElapsed' else ot)).total_seconds())
                        self.assertEqual(scope.old._state,full+self.area(left,right,elapsed))
                        creation_checks.append(True)
                        burst_base.append(scope.old._state)
                        self.producer._attr_native_value=Decimal('20' if not reverse else '24')
                        self.producer.async_write_ha_state()
                        self.producer.async_write_ha_state()
                        self.producer.async_write_ha_state()
                        assert_burst(event)
                    elif burst_base:
                        assert_burst(event)
                # Observe the actual keyed job AFTER the product bridge on both
                # Core versions. Minimum queues changed but dispatches reports
                # inline; count real deliveries before asserting the whole burst.
                unsubscribe=async_track_state_change_event(hass,self.names,source_created)
                report_unsubscribe=async_track_state_report_event(hass,self.names,source_created)
                original_write=m['yaml_transaction'].apply_file_patch
                def pinned_write(*args,**kwargs):
                    result=original_write(*args,**kwargs)
                    fd=os.open(root/result['path'],os.O_RDONLY|os.O_NOFOLLOW)
                    self.addCleanup(os.close,fd)
                    info=os.fstat(fd)
                    self.assertEqual((info.st_dev,info.st_ino,info.st_mode),tuple(result['after_identity']))
                    return result
                try:
                    with patch.object(m['yaml_transaction'],'apply_file_patch',pinned_write):
                        record=(await coordinator.rollback(chain.operation_id) if reverse else
                                await coordinator.apply(plan,plan.fingerprint,chain.operation_id))
                    self.assertEqual(record.status,'rolled_back' if reverse else 'applied',
                                     (record.status,record.reason,record.phases))
                    observed=await observe()
                    raw=tuple(m['native_registry_receipts'].RegistryRawObservation(f.key,f.raw,f.identity) for f in observed.files)
                    head=chain.expected(m['transaction'].PhaseContext(record,0),raw)
                    auth=m['native_helper_lifecycle'].RestoreAuthorization(chain.operation_id,plan.fingerprint,head,record.status)
                    if negative_hook is not None:
                        negative_context.authorization=auth
                        if await negative_hook('before_setup',negative_context):
                            return
                    restored=await m['native_integration_source_clock'].async_setup_source_clock(
                        scope,auth,observe=observe,load_record=journal.load)
                    self.assertEqual(restored._sensor_source_id,scope.source_binding.to_name)
                    self.assertEqual(self.f.entity.async_get(restored._sensor_source_id).id,self.source_uuid)
                    self.assertEqual(self.f.entity.async_get(restored.entity_id).id,self.helper_uuid)
                    self.assertEqual(scope.source_binding.transport_calls,1)
                    self.assertTrue(burst_done)
                    if self.f.version=='2026.10.2':
                        self.assertGreaterEqual(scope.source_binding.frozen_calls,1)
                    if self.saved_uuid:
                        self.assertEqual(self.entry.options['source'],self.source_uuid)
                    else:
                        self.assertEqual(self.entry.options['source'],scope.source_binding.to_name)
                    row=store.read(scope.receipt[0])['payload']
                    self.assertEqual(row['source_binding']['authorization']['head'],head)
                    self.assertEqual(row['source_binding']['source_uuid'],self.source_uuid)
                finally:
                    unsubscribe();report_unsubscribe();origin_witness.close()
                scope.close()
                # First real post-setup event: independent actual-time oracle.
                full=restored._state
                anchor=restored._last_integration_time
                trigger=restored._last_integration_trigger.name
                previous=hass.states.get(self.producer.entity_id)
                left,old_time=previous.state,previous.last_reported
                right='22' if not reverse else '26'
                self.producer._attr_native_value=Decimal(right)
                self.producer.async_write_ha_state()
                await hass.async_block_till_done()
                new=hass.states.get(self.producer.entity_id)
                nt=new.last_updated if self.f.version=='2026.10.2' else new.last_reported
                elapsed=Decimal((nt-(anchor if trigger=='TimeElapsed' else old_time)).total_seconds())
                self.assertEqual(restored._state,full+self.area(left,right,elapsed))
                script_values.clear()
                await hass.services.async_call('script','source_clock_probe',{},blocking=True,context=Context())
                self.assertEqual([Decimal(v) for v in script_values],[Decimal(right)])
                fresh_preps=m['prepared'].native_prepared_store(hass)
                await fresh_preps.initialize()
                fresh_store=m['native_registry_receipts'].native_registry_receipt_store(hass,fresh_preps)
                await fresh_store.initialize()
                reopened=m['native_registry_receipts'].RegistryReceiptChain.reopen(
                    fresh_store,chain.operation_id,plan,receipt,digests)
                self.assertFalse(reopened.writable)
                self.assertEqual(reopened.accepted_documents(),chain.accepted_documents())
                self.assertTrue(reopened.verify_setup_authorization(auth))
            self.assertEqual(len(creation_checks),2)
            self.assertEqual(len(burst_checks),2)
            self.assertEqual([p.direction for p in candidate_proofs],['forward','rollback'])
        finally:
            for scope in scopes_live:
                scope.close()
            lease.release()


class NativeIntegrationSourceRightClockTests(NativeIntegrationSourceClockTests):
    method='right'


class NativeIntegrationSourceTrapezoidalClockTests(NativeIntegrationSourceClockTests):
    method='trapezoidal'


class NativeIntegrationSourceUUIDClockTests(NativeIntegrationSourceClockTests):
    saved_uuid=True
