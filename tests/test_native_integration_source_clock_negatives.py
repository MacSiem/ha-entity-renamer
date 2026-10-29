"""Actual source-clock negative inputs; owner executes through FIFO on both pins.

No positive fake Event, restore-data substitution, clock/count/cache seed or
native arithmetic patch. A negative-only getter barrier calls the real getter
and returns its unchanged result after an actual foreign writer has run.
"""
import asyncio
from datetime import timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch

import test_native_integration_source_clock as positive


@unittest.skipUnless(positive.fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationSourceClockNegativeTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the exact actual fixture without inheriting/discovering its positives.
    method='left'
    saved_uuid=False
    with_yaml=True
    asyncSetUp=positive.NativeIntegrationSourceClockTests.asyncSetUp
    _before_start=positive.NativeIntegrationSourceClockTests._before_start
    _flush_documents=positive.NativeIntegrationSourceClockTests._flush_documents
    current=positive.NativeIntegrationSourceClockTests.current
    guard=positive.NativeIntegrationSourceClockTests.guard
    wait_tick=positive.NativeIntegrationSourceClockTests.wait_tick
    area=positive.NativeIntegrationSourceClockTests.area
    _run_source_clock=positive.NativeIntegrationSourceClockTests._run_source_clock

    async def test_real_keyed_changed_old_state_substitution_refuses(self):
        await self.negative('changed_old_state')

    async def test_real_keyed_report_old_timestamp_mutation_refuses(self):
        await self.negative('report_old_timestamp')

    async def test_current_real_keyed_report_explicit_new_timestamp_mutation_refuses(self):
        if self.f.version!='2026.10.2':
            self.skipTest('minimum payload has no explicit last_reported key')
        await self.negative('report_new_timestamp')

    async def test_real_keyed_report_state_timestamp_mutation_refuses(self):
        await self.negative('report_state_timestamp')

    async def test_public_bus_clone_of_real_payload_cannot_attest_origin(self):
        await self.negative('public_clone')

    async def test_constructed_negative_event_cannot_borrow_pending_actual_origin(self):
        await self.negative('constructed_event')

    async def test_actual_unprepared_native_source_removal_refuses(self):
        await self.negative('source_removal')

    async def test_foreign_yaml_edit_during_actual_restore_await_refuses_transfer(self):
        await self.negative('restore_yaml')

    async def test_foreign_source_registry_edit_during_restore_await_refuses_transfer(self):
        await self.negative('restore_registry')

    async def test_foreign_config_edit_during_restore_await_refuses_transfer(self):
        await self.negative('restore_config')

    async def test_real_producer_unit_drift_during_restore_await_refuses_transfer(self):
        await self.negative('restore_unit')

    async def negative(self,mode):
        self.mode=mode
        self.armed=False
        self.seen=[]
        self.restore=[]
        self.unsubscribe=None
        self.source_clock_negative_hook=self.hook
        try:
            await self._run_source_clock()
            self.assertTrue(self.negative_asserted,'negative hook did not reach refusal assertions')
        finally:
            if self.unsubscribe is not None:
                self.unsubscribe()
            # Restore only after the negative assertions were attempted. Neither
            # restoration nor cleanup is part of the claimed refusal evidence.
            for action in reversed(self.restore):
                action()

    def no_transfer_or_receipt(self,context):
        scope=context.scope
        self.assertIsNone(scope._new)
        self.assertIsNone(scope._transferred)
        self.assertFalse(scope._new_timer_bound)
        self.assertFalse(hasattr(scope,'receipt'))
        for row in context.store.rows.values():
            payload=row['payload']
            self.assertNotEqual(payload.get('kind'),'integration_clock_receipt')
            self.assertNotEqual(payload.get('kind'),'helper_setup_receipt')

    async def poisoned_refusal(self,context):
        self.assertIsNotNone(context.scope._error,'invalid actual input was not poisoned')
        self.no_transfer_or_receipt(context)
        with self.assertRaises(ValueError):
            await context.scope.setup()
        self.no_transfer_or_receipt(context)
        self.negative_asserted=True

    async def hook(self,stage,context):
        from homeassistant.core import callback
        from homeassistant.helpers.event import (async_track_state_change_event,
            async_track_state_report_event)
        if stage=='before_capture':
            self.negative_asserted=False
            if self.mode.startswith('restore_') or self.mode=='source_removal':
                return False
            @callback
            def mutate(event):
                if not self.armed:
                    return
                self.armed=False  # Public clone may synchronously redispatch.
                data=event.data
                scope=context.scope
                if self.mode=='changed_old_state':
                    original=data['old_state']
                    self.restore.append(lambda:data.__setitem__('old_state',original))
                    data['old_state']=data['new_state']  # Both are genuine State refs.
                    self.seen.append(('old_state',original,data['old_state']))
                elif self.mode in ('report_old_timestamp','report_new_timestamp'):
                    key='old_last_reported' if self.mode=='report_old_timestamp' else 'last_reported'
                    original=data[key]
                    self.restore.append(lambda:data.__setitem__(key,original))
                    data[key]=original-timedelta(seconds=1)  # Negative input only.
                    self.seen.append((key,original,data[key]))
                elif self.mode=='report_state_timestamp':
                    state=data['new_state']
                    original=state.last_reported
                    self.restore.append(lambda:setattr(state,'last_reported',original))
                    state.last_reported=original-timedelta(seconds=1)
                    self.seen.append(('state.last_reported',original,state.last_reported))
                else:
                    token=scope._emitted.get(id(data))
                    native_before=dict(scope.source_binding._native_events)
                    if self.mode=='public_clone':
                        clone=dict(data)
                        context.hass.bus.async_fire(event.event_type,clone)
                        self.seen.append(('public_clone',clone,token,
                            scope._emitted.get(id(data)),scope._emitted.get(id(clone)),
                            scope.source_binding._native_events.get(id(clone))))
                    else:
                        from homeassistant.core import Event
                        counterfeit=Event(event.event_type,data)
                        # Negative-only object inside a real keyed dispatcher.
                        # The pending real origin/data must not authenticate it.
                        scope._bridge(counterfeit,'changed')
                        self.seen.append(('constructed_event',event,counterfeit,token,
                            scope._emitted.get(id(data)),native_before,
                            dict(scope.source_binding._native_events)))
            self.mutator=mutate
            track=(async_track_state_report_event if self.mode.startswith('report_')
                   else async_track_state_change_event)
            self.unsubscribe=track(context.hass,self.names,mutate)
            return False
        if stage=='retained':
            if self.mode.startswith('restore_'):
                return False  # Finish real Coordinator effects + real auth first.
            if self.mode=='source_removal':
                await self.producer.async_remove()
            else:
                key=(context.scope.event._TRACK_STATE_REPORT_DATA if self.mode.startswith('report_')
                     else context.scope.event._TRACK_STATE_CHANGE_DATA)
                targets=[job.target for job in context.hass.data[key].callbacks[self.names[0]]]
                bridge=context.scope._bridges['reported' if self.mode.startswith('report_') else 'changed']
                self.assertLess(targets.index(self.mutator),targets.index(bridge))
                before=context.scope._bridge_calls
                self.armed=True
                if not self.mode.startswith('report_'):
                    self.producer._attr_native_value=Decimal('13')
                self.producer.async_write_ha_state()
                await context.hass.async_block_till_done()
                self.assertEqual(len(self.seen),1)
                self.assertEqual(context.scope._bridge_calls,before)
                item=self.seen[0]
                if self.mode=='changed_old_state':
                    self.assertIsNot(item[1],item[2])
                elif self.mode.startswith('report_'):
                    self.assertGreater(item[1],item[2])
                elif self.mode=='public_clone':
                    self.assertIsNotNone(item[2])  # Genuine emission was pending.
                    self.assertIs(item[2],item[3])
                    self.assertIsNone(item[4])     # Clone acquired no origin token.
                    self.assertIsNone(item[5])     # No attested native Event for clone.
                else:
                    _,real,counterfeit,old_token,new_token,old_map,new_map=item
                    self.assertIsNot(real,counterfeit)
                    self.assertIsNotNone(old_token)
                    self.assertIs(old_token,new_token)
                    self.assertEqual(set(old_map),set(new_map))
                    self.assertTrue(all(old_map[k] is new_map[k] for k in old_map))
                    self.assertFalse(any(value is counterfeit for value in new_map.values()))
                    if self.f.version=='2025.6.0':
                        self.assertIs(old_map[id(real.data)],real)
            await context.hass.async_block_till_done()
            await self.poisoned_refusal(context)
            return True
        if stage=='before_setup':
            self.assertTrue(self.mode.startswith('restore_'))
            await self.restore_await_negative(context)
            return True
        self.fail('unknown negative hook stage '+stage)

    async def restore_await_negative(self,context):
        scope=context.scope
        entered=asyncio.Event()
        released=asyncio.Event()
        getters=[]
        actual=scope.cls.async_get_last_sensor_data
        async def actual_restore_with_negative_barrier(entity):
            value=await actual(entity)  # Real native history/extra-data read.
            if entity.hass is context.hass and entity.unique_id==context.entry.entry_id and entity is not scope.old:
                getters.append((entity,value))
                entered.set()
                await released.wait()
            return value  # No fabricated data, changed Decimal or cache write.
        async def foreign_writer():
            await entered.wait()
            try:
                self.assertEqual(scope._phase,'setting_up')
                self.assertIsNone(scope._transferred)
                if self.mode=='restore_yaml':
                    path=context.root/'scripts/probe.yaml'
                    original=path.read_bytes()
                    self.restore.append(lambda:path.write_bytes(original))
                    path.write_bytes(original+b'# foreign during native restore await\n')
                elif self.mode=='restore_registry':
                    row=self.f.entity.async_get(scope.source)
                    original=row.name
                    self.restore.append(lambda:self.f.entity.async_update_entity(scope.source,name=original))
                    self.f.entity.async_update_entity(scope.source,name='Foreign restore-await source')
                elif self.mode=='restore_config':
                    original=dict(context.entry.options)
                    self.restore.append(lambda:context.hass.config_entries.async_update_entry(context.entry,options=original))
                    context.hass.config_entries.async_update_entry(context.entry,
                        options={**original,'method':'right'})
                else:
                    original=self.producer._attr_native_unit_of_measurement
                    def restore_unit():
                        self.producer._attr_native_unit_of_measurement=original
                        self.producer.async_write_ha_state()
                    self.restore.append(restore_unit)
                    self.producer._attr_native_unit_of_measurement='kW'
                    self.producer.async_write_ha_state()
                self.seen.append(self.mode)
            finally:
                released.set()
        writer=asyncio.create_task(foreign_writer())
        try:
            # Negative scheduling fault only. All underlying native restore and
            # all native arithmetic/scheduling methods remain genuine.
            with patch.object(scope.cls,'async_get_last_sensor_data',actual_restore_with_negative_barrier):
                with self.assertRaises(ValueError):
                    # timeout context preserves the owner task; wait_for would
                    # move the product API to a child and create a false refusal.
                    async with asyncio.timeout(10):
                        await self._owner_setup(context)
            await writer
            self.assertEqual(len(getters),1,'native restore-await barrier was not reached exactly once')
            self.assertEqual(self.seen,[self.mode])
            self.assertTrue(released.is_set())
            self.assertIsNotNone(scope._error)
            self.no_transfer_or_receipt(context)
            self.negative_asserted=True
        finally:
            released.set()
            if not writer.done():
                writer.cancel()
            await asyncio.gather(writer,return_exceptions=True)
            # Assertions above happen before restoration and native unload.
            for action in reversed(self.restore):
                action()
            self.restore.clear()
            if context.entry.state.value=='loaded':
                await context.lease.async_unload(context.entry.entry_id)

    async def _owner_setup(self,context):
        return await context.m['native_integration_source_clock'].async_setup_source_clock(
            context.scope,context.authorization,observe=context.observe,load_record=context.load_record)
