"""Actual foreign EventBus must not replace a pending native Event witness.

Reuse the source-clock fixture's optional hooks. No Event/State construction,
native patch, arithmetic writer, clock/count seed or restore-data substitution.
"""
from decimal import Decimal
import unittest

import test_native_integration_source_clock as positive


@unittest.skipUnless(positive.fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationSourceClockForeignBusTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_foreign_bus_without_listener_preserves_actual_native_delivery(self):
        await self.foreign_bus_case(with_listener=False)

    async def test_foreign_bus_with_callback_preserves_actual_native_delivery(self):
        await self.foreign_bus_case(with_listener=True)

    async def foreign_bus_case(self, *, with_listener):
        self.with_listener=with_listener
        self.armed=False
        self.seen=[]
        self.foreign_seen=[]
        self.unsubscribes=[]
        self.checked=False
        self.source_clock_negative_hook=self.hook
        try:
            await self._run_source_clock()
            self.assertTrue(self.checked, 'retained hook did not complete assertions')
        finally:
            for unsubscribe in reversed(self.unsubscribes):
                unsubscribe()

    async def hook(self, stage, context):
        from homeassistant.core import EventBus, callback
        from homeassistant.helpers.event import async_track_state_change_event
        if stage=='before_capture':
            self.other_bus=EventBus(context.hass)
            if self.with_listener:
                @callback
                def foreign_observer(event):
                    self.foreign_seen.append(event)
                self.unsubscribes.append(self.other_bus.async_listen(
                    'state_changed', foreign_observer))

            @callback
            def forward_actual_payload(event):
                if not self.armed:
                    return
                self.armed=False
                scope=context.scope
                binding=scope.source_binding
                # Read the genuine keyed Event before the product bridge. On
                # minimum Core the original EventBus has already returned and
                # captured this Event; on current dispatch is still inline.
                record={
                    'event':event,
                    'data':event.data,
                    'full':scope.old._state,
                    'anchor':scope.old._last_integration_time,
                    'trigger':scope.old._last_integration_trigger.name,
                    'before_events':dict(binding._native_events),
                }
                self.seen.append(record)
                # No await, clone or manual Event: a second real EventBus gets
                # the exact authentic dict from the real StateMachine emission.
                self.other_bus.async_fire_internal(event.event_type,event.data)
                record['after_events']=dict(binding._native_events)

            self.forward_actual_payload=forward_actual_payload
            self.unsubscribes.append(async_track_state_change_event(
                context.hass,self.names,forward_actual_payload))
            return False

        if stage=='retained':
            scope=context.scope
            binding=scope.source_binding
            targets=[job.target for job in context.hass.data[
                scope.event._TRACK_STATE_CHANGE_DATA].callbacks[self.names[0]]]
            self.assertLess(targets.index(self.forward_actual_payload),
                            targets.index(scope._bridges['changed']))
            before_calls=scope._bridge_calls
            self.armed=True
            self.producer._attr_native_value=Decimal('13')
            self.producer.async_write_ha_state()
            await context.hass.async_block_till_done()

            self.assertEqual(len(self.seen),1, 'real keyed input was not observed once')
            record=self.seen[0]
            event,data=record['event'],record['data']
            self.assertIs(event.data,data)
            self.assertEqual(event.event_type,'state_changed')
            self.assertIsNotNone(data['old_state'])
            self.assertIs(data['new_state'],context.hass.states.get(self.names[0]))
            if self.f.version=='2025.6.0':
                self.assertIs(record['before_events'].get(id(data)),event,
                              'minimum must exercise a pending captured native Event')
            self.assertEqual(set(record['before_events']),set(record['after_events']))
            self.assertTrue(all(record['after_events'][key] is value
                for key,value in record['before_events'].items()),
                'foreign bus replaced the native Event witness')
            if self.with_listener:
                self.assertEqual(len(self.foreign_seen),1)
                self.assertIsNot(self.foreign_seen[0],event)
                self.assertIs(self.foreign_seen[0].data,data)
                self.assertFalse(any(value is self.foreign_seen[0]
                    for value in record['after_events'].values()))
            else:
                self.assertEqual(self.foreign_seen,[])

            self.assertIsNone(scope._error, 'foreign bus poisoned actual native scope')
            scope._check(owner=True)
            self.assertEqual(scope._bridge_calls,before_calls+1,
                             'actual native interval must be consumed exactly once')
            old,new=data['old_state'],data['new_state']
            end=new.last_updated if self.f.version=='2026.10.2' else new.last_reported
            start=(record['anchor'] if record['trigger']=='TimeElapsed'
                   else old.last_reported)
            elapsed=Decimal((end-start).total_seconds())
            self.assertGreaterEqual(elapsed,0)
            expected=record['full']+self.area(old.state,new.state,elapsed)
            self.assertEqual(scope.old._state,expected)
            self.assertEqual(scope.old._last_integration_trigger.name,'StateEvent')
            _,_,endpoint=scope._timer(scope.old)
            self.assertIs(endpoint,new)
            self.assertFalse(binding.flights)
            self.assertFalse(scope._emitted)
            self.assertFalse(scope._consumed)
            self.assertNotIn(id(data),binding._native_events,
                             'consumed native Event witness was not retired')
            self.assertIsNone(scope._new)
            self.assertFalse(hasattr(scope,'receipt'))
            self.checked=True
            return True  # Bounded regression ends before Coordinator migration.

        self.fail('unexpected hook stage '+stage)
