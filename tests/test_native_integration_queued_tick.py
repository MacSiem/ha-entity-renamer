"""Actual minimum queued source -> retained tick -> frozen delivery regression.

A normal loop callback emits the real source only after the actual native timer
is ready. The monitoring callbacks are READ-ONLY; they never emit user events.
No timer/clock/queue mutation, native method replacement or counter seeding.
"""
import asyncio
from decimal import Decimal
import math
import sys
import unittest

import test_native_integration_source_clock as positive


@unittest.skipUnless(positive.fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationQueuedTickTests(unittest.IsolatedAsyncioTestCase):
    method='left'
    shared_lifecycle=True
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

    async def test_actual_queued_changed_report_then_tick_preserves_legal_full_flow(self):
        if self.f.version!='2025.6.0':
            self.skipTest('minimum queued changed dispatcher interleaving')
        self.forced=[]
        self.source_clock_retained_probe=self.queued_before_real_tick
        await self._run_source_clock()  # Full Coordinator/YAML/native setup BOTH directions.
        self.assertEqual(len(self.forced),1)

    async def queued_before_real_tick(self,scope,reverse):
        if reverse:
            return
        from homeassistant.core import callback
        from homeassistant.helpers.event import (async_track_state_change_event,
                                                 async_track_state_report_event)
        hass=self.f.hass
        loop=asyncio.get_running_loop()
        self.assertIs(loop,hass.loop)
        scope._check(owner=True)
        self.assertTrue(scope._retained)
        timer,action,endpoint=scope._timer(scope.old)
        self.assertIs(type(timer),asyncio.TimerHandle)
        self.assertIs(timer._loop,loop)
        self.assertFalse(timer.cancelled())
        code=action.__code__
        completed=loop.create_future()
        record={}
        seen=[]
        precursor=[None]
        installed=[False]
        monitor=sys.monitoring
        name='renamer_test.actual_queued_tick'
        free=[key for key in range(6) if monitor.get_tool(key) is None]
        self.assertTrue(free,'actual CPython monitoring tool unavailable')
        tool=free[0]

        def fail(error):
            if not completed.done():
                completed.set_exception(error)

        def close_monitor():
            if monitor.get_tool(tool)==name:
                monitor.set_local_events(tool,code,0)
                monitor.register_callback(tool,monitor.events.PY_START,None)
                monitor.register_callback(tool,monitor.events.PY_RETURN,None)
                monitor.free_tool_id(tool)
            installed[0]=False

        def finish_if_delivered():
            if ('tick_return' in record and len(seen)==2
                    and not completed.done()):
                completed.set_result(None)

        @callback
        def delivered(event):
            try:
                if (event.data.get('entity_id')==self.producer.entity_id
                        and event.data.get('new_state') is record.get('source_state')):
                    self.assertNotIn(event.event_type,[row.event_type for row in seen])
                    seen.append(event)
                    finish_if_delivered()
            except BaseException as error:
                fail(error)

        @callback
        def emit_when_timer_ready():
            try:
                if completed.done() or 'emitted' in record:
                    return
                self.assertFalse(timer.cancelled(),'real timer cancelled before emission seam')
                if timer not in loop._ready:
                    # The preceding deadline may be promoted one loop iteration
                    # earlier. This ordinary callback stays ahead of the native
                    # timer when _run_once appends it; it never changes the queue.
                    self.assertIn(timer,loop._scheduled,'actual timer escaped ready barrier')
                    precursor[0]=loop.call_soon(emit_when_timer_ready)
                    return
                self.assertNotIn(timer,loop._scheduled)
                record['emitted']=True
                record['ready_before_emit']=True
                old=hass.states.get(self.producer.entity_id)
                record['left']=old.state
                # Real ordinary sensor attribute change forces a changed Event
                # while power remains constant. The second write is a report.
                attributes=dict(self.producer.extra_state_attributes or {})
                attributes['queued_tick_regression']='actual_native_tick'
                self.producer._attr_extra_state_attributes=attributes
                self.producer.async_write_ha_state()
                source=hass.states.get(self.producer.entity_id)
                self.assertIsNot(source,old)
                self.assertEqual(source.state,old.state)
                record['source_state']=source
                record['changed_time']=source.last_reported  # Frozen BEFORE report.
                self.producer.async_write_ha_state()
                self.assertIs(hass.states.get(self.producer.entity_id),source)
                record['reported_time']=source.last_reported
                self.assertGreater(record['reported_time'],record['changed_time'])
                self.assertEqual([row.event_type for row in seen],['state_reported'])
                self.assertIn(timer,loop._ready,'source must precede the same real ready timer')
            except BaseException as error:
                fail(error)

        def started(observed_code,offset):
            frame=sys._getframe(1)
            try:
                if frame.f_locals.get('self') is not scope.old:
                    return
                self.assertIs(observed_code,code)
                self.assertNotIn('tick_start',record,'more than one actual tick observed')
                self.assertTrue(record.get('ready_before_emit'))
                self.assertEqual([row.event_type for row in seen],['state_reported'])
                self.assertIs(frame.f_locals['source_state'],endpoint)
                record['tick_start']=(scope.old._state,scope.old._last_integration_time,
                    frame.f_locals['now'],frame.f_locals['source_state_dec'])
                # Observations only. User code here would suspend the product's
                # monitoring witness on CPython and would invalidate the probe.
            except BaseException as error:
                fail(error)
            finally:
                del frame

        def returned(observed_code,offset,result):
            frame=sys._getframe(1)
            try:
                if frame.f_locals.get('self') is not scope.old:
                    return
                self.assertIn('tick_start',record)
                self.assertNotIn('tick_return',record)
                self.assertEqual(scope.old._last_integration_trigger.name,'TimeElapsed')
                self.assertEqual([row.event_type for row in seen],['state_reported'])
                record['tick_return']=(scope.old._state,scope.old._last_integration_time)
                close_monitor()  # This test's own free tool only; after ONE tick.
                finish_if_delivered()
            except BaseException as error:
                fail(error)
            finally:
                del frame

        remove_changed=async_track_state_change_event(hass,self.names,delivered)
        remove_reported=async_track_state_report_event(hass,self.names,delivered)
        try:
            monitor.use_tool_id(tool,name)
            installed[0]=True
            monitor.register_callback(tool,monitor.events.PY_START,started)
            monitor.register_callback(tool,monitor.events.PY_RETURN,returned)
            monitor.set_local_events(tool,code,monitor.events.PY_START|monitor.events.PY_RETURN)
            if timer in loop._ready:
                emit_when_timer_ready()  # Ordinary owner-task execution, no monitoring callback.
            else:
                self.assertIn(timer,loop._scheduled)
                # Adjacent earlier float, not a sleep budget or altered native
                # deadline. _ready membership, not wall-time luck, authorizes emission.
                precursor[0]=loop.call_at(math.nextafter(timer.when(),-math.inf),
                                          emit_when_timer_ready)
            await asyncio.wait_for(completed,10)
            self.assertFalse(installed[0])
            self.assertIsNone(monitor.get_tool(tool))
            self.assertEqual([row.event_type for row in seen],['state_reported','state_changed'])
            self.assertIsNot(seen[0],seen[1])
            self.assertIs(seen[0].data['new_state'],seen[1].data['new_state'])
            self.assertEqual(scope.old._unit_prefix*scope.old._unit_time,1)
            full,anchor,now,power=record['tick_start']
            tick_full,tick_anchor=record['tick_return']
            self.assertEqual(Decimal(record['left']),power)
            self.assertGreater(tick_anchor,record['reported_time'])
            self.assertLess(record['changed_time'],tick_anchor)
            expected_tick=full+self.area(power,power,Decimal((now-anchor).total_seconds()))
            self.assertEqual(tick_full,expected_tick,'actual native tick versus independent area')
            # Sequential Decimal additions match the native writer's precision;
            # no inverse subtraction, seed or expected count reaches HA.
            expected=expected_tick+self.area(record['left'],record['source_state'].state,
                Decimal((record['changed_time']-tick_anchor).total_seconds()))
            expected=expected+self.area(record['source_state'].state,record['source_state'].state,
                Decimal((record['reported_time']-record['changed_time']).total_seconds()))
            self.assertIsNone(scope._error,('legal queued/tick interval refused',
                record['changed_time'],record['reported_time'],tick_anchor,
                scope._error,scope._first_failure))
            scope._check(owner=True)
            self.assertFalse(scope.source_binding.flights)
            self.assertFalse(scope._emitted)
            self.assertEqual(scope.old._state,expected,'independent tick + source interval oracle')
            self.assertEqual(scope.old._last_integration_trigger.name,'StateEvent')
            scope._timer(scope.old)  # Authentic current native timer remains owned.
            self.forced.append(record)
        finally:
            if precursor[0] is not None:
                precursor[0].cancel()  # This test's precursor, never the native timer.
            close_monitor()
            remove_changed()
            remove_reported()
