"""Existing source-clock actual fixture composed with shared lifecycle receipts.

Executable draft: owner runs through FIFO on the two pinned Core versions.
Own-ID+timer and saved-source-UUID-without-helper-step are not claimed here.
"""
import asyncio
import importlib
import unittest
from unittest.mock import patch

import test_native_integration_source_clock as positive


@unittest.skipUnless(positive.fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationClockLifecycleTests(positive.NativeIntegrationSourceClockTests):
    shared_lifecycle=True


class NativeIntegrationClockRightLifecycleTests(NativeIntegrationClockLifecycleTests):
    method='right'


class NativeIntegrationClockTrapezoidalLifecycleTests(NativeIntegrationClockLifecycleTests):
    method='trapezoidal'


class NativeIntegrationClockNaturalZeroLifecycleTests(NativeIntegrationClockLifecycleTests):
    initial_power='0'


@unittest.skipUnless(positive.fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationClockLifecycleRegressionTests(unittest.IsolatedAsyncioTestCase):
    # Exact real producer/Coordinator/ORIGINAL/lease fixture; no inherited
    # positive methods, synthetic cache, arithmetic writer or fake Task.
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

    async def test_post_ack_foreign_physical_yaml_still_refuses(self):
        await self.negative('post_ack_yaml')

    async def test_actual_changed_dispatcher_detach_refuses_before_next_event(self):
        await self.negative('detach_changed')

    async def test_actual_reported_dispatcher_detach_refuses_before_next_event(self):
        await self.negative('detach_reported')

    async def test_last_runtime_callback_changed_detach_refuses(self):
        await self.negative('late_changed')

    async def test_last_runtime_callback_reported_detach_refuses(self):
        await self.negative('late_reported')

    async def test_retained_owner_task_cancellation_cleans_clock(self):
        await self.negative('cancel_retained')

    async def test_two_actual_retained_clocks_cleanup_survives_native_double_unsubscribe(self):
        self.second_lifecycle_clock=True
        await self.negative('two_clock_cleanup')

    async def negative(self,mode):
        self.mode=mode
        self.asserted=False
        self.detached=[]
        self.runtime_calls=0
        self.lifecycle_clock_hook=self.hook
        await self._run_source_clock()
        self.assertTrue(self.asserted,'native lifecycle fault boundary was not reached')

    def detach_actual(self,context,kind):
        scope=context.scope
        key=(scope.event._TRACK_STATE_CHANGE_DATA if kind=='changed'
             else scope.event._TRACK_STATE_REPORT_DATA)
        tracker=context.hass.data[key]
        original_callbacks={k:tuple(v) for k,v in tracker.callbacks.items()}
        event_type,entry=tracker.listener.args
        self.assertEqual(event_type,'state_'+kind)
        self.assertEqual(sum(row is entry for row in context.hass.bus._listeners[event_type]),1)
        self.assertFalse(scope.source_binding.flights)
        self.assertFalse(scope._emitted)
        tracker.listener()  # Actual HA EventBus unsubscribe; no native patch.
        self.assertIs(context.hass.data[key],tracker)
        self.assertEqual({k:tuple(v) for k,v in tracker.callbacks.items()},original_callbacks)
        self.assertFalse(any(row is entry for row in context.hass.bus._listeners.get(event_type,())))
        self.detached.append((kind,tracker,entry))

    def capture_resources(self,context):
        context.cleanup_tools=tuple(s._tool for s in context.scopes.values())
        context.cleanup_emissions=tuple(s._emissions for s in context.scopes.values())
        return tuple(s._timer(s.old)[0] for s in context.scopes.values())

    def assert_closed(self,context,handles):
        monitor=__import__('sys').monitoring
        for tool in context.cleanup_tools:
            self.assertIsNone(monitor.get_tool(tool))
        emissions=importlib.import_module(positive.PACKAGE+'.native_utility_source_emissions')
        for witness in context.cleanup_emissions:
            self.assertFalse(witness.live)
            self.assertNotIn(witness,emissions._witnesses)
        for scope in context.scopes.values():
            self.assertFalse(scope._live)
            self.assertFalse(scope._retained)
            self.assertFalse(scope._unsubscribes)
            self.assertIsNone(scope._tool)
            self.assertIsNone(scope._emissions)
            # close() cancels the CURRENT retained timer, which real elapsed
            # callbacks may have replaced during the awaited restore.
            cancel=scope.old._max_sub_interval_exceeded_callback
            current=getattr(cancel,'__self__',None)
            self.assertIs(type(current),asyncio.TimerHandle)
            self.assertIs(current._loop,context.hass.loop)
            self.assertEqual(cancel,current.cancel)
            self.assertTrue(current.cancelled(),'current old-object timer leaked')
        loop=context.hass.loop
        for handle in handles:
            self.assertIs(type(handle),asyncio.TimerHandle)
            self.assertIs(handle._loop,loop)
            if not handle.cancelled():
                # An already-fired snapshot handle need not be marked cancelled.
                # Still reject a pending/due/ready callback, including handles
                # no longer referenced by the entity's current cancellation field.
                self.assertLessEqual(handle.when(),loop.time())
                self.assertNotIn(handle,loop._scheduled,'snapshot timer still scheduled')
                self.assertNotIn(handle,loop._ready,'snapshot timer still ready')

    async def hook(self,stage,context):
        if stage=='inactive':
            if (self.mode=='two_clock_cleanup' and not self.detached
                    and all(s._phase=='retained' for s in context.scopes.values())
                    and context.life.inspect()['inactive_receipts_complete']):
                self.assertEqual(len(context.scopes),2)
                scopes=tuple(context.scopes.values())
                self.handles=self.capture_resources(context)
                # Leave the real unsubscribe in the first scope's owned list.
                # Its later cleanup encounters native list.remove a second time.
                scopes[0]._unsubscribes[0]()
                self.detached.append('actual first bridge removed')
            return False
        if stage=='before_quiesce':
            if self.mode!='two_clock_cleanup':
                return False
            with self.assertRaises(ValueError):
                await context.life.quiesce()
            self.assertEqual(self.detached,['actual first bridge removed'])
            self.assertTrue(context.life.failed)
            self.assert_closed(context,self.handles)
            self.asserted=True
            return True
        if stage=='runtime':
            self.runtime_calls+=1
            if self.mode.startswith('late_') and self.runtime_calls==2:
                # Both real native setup and the earlier real runtime verifier
                # have completed; this is the last lifecycle runtime await.
                self.assertTrue(context.life.inspect()['activation_receipts_complete'])
                self.detach_actual(context,self.mode.removeprefix('late_'))
                await asyncio.sleep(0)
            return False
        if stage!='before_restore':
            self.fail('unknown lifecycle hook '+stage)
        life,scope=context.life,context.scope
        handles=self.capture_resources(context)
        path=context.root/'scripts/probe.yaml'
        original_bytes=path.read_bytes()
        try:
            if self.mode=='post_ack_yaml':
                actual=context.chain.complete_helper_setup
                heads=[]
                async def complete_then_foreign_edit(*args,**kwargs):
                    ref=await actual(*args,**kwargs)
                    # Actual verified receipt ACK, no adopted raw/cached head.
                    heads.append(context.chain._reconstruct()[1])
                    self.assertNotEqual(heads[0],context.authorization.head)
                    self.assertTrue(context.chain.verify_setup_authorization(context.authorization))
                    path.write_bytes(original_bytes+b'# foreign AFTER setup receipt ACK\n')
                    return ref
                with patch.object(context.chain,'complete_helper_setup',complete_then_foreign_edit):
                    with self.assertRaises(ValueError):
                        await life.restore(context.authorization)
                self.assertEqual(len(heads),1)
                self.assertEqual(scope.source_binding.accepted_input.head,heads[0])
                self.assertEqual(life.inspect()['pending'],[
                    {'stage':'setup','entry_id':context.entry.entry_id}])
                self.assertIsNotNone(scope._new)  # Partial actual setup is UNKNOWN.
            elif self.mode=='cancel_retained':
                actual=life.verify_accepted_native_chain
                owner=asyncio.current_task()
                prior=owner.cancelling()
                cancelled=[]
                async def verified_then_cancel(auth):
                    result=await actual(auth)
                    self.assertIs(result,True)
                    self.assertTrue(scope._retained)
                    cancelled.append(True)
                    owner.cancel()  # The real acquiring task, never a fake Task.
                    await asyncio.sleep(0)
                    return result
                try:
                    with patch.object(life,'verify_accepted_native_chain',verified_then_cancel):
                        with self.assertRaises(asyncio.CancelledError):
                            await life.restore(context.authorization)
                finally:
                    while owner.cancelling()>prior:
                        owner.uncancel()
                self.assertEqual(cancelled,[True])
                self.assertIsNone(scope._new)
            else:
                if self.mode.startswith('detach_'):
                    self.detach_actual(context,self.mode.removeprefix('detach_'))
                else:
                    self.assertTrue(self.mode.startswith('late_'))
                with self.assertRaises(ValueError):
                    await life.restore(context.authorization)
                self.assertEqual(len(self.detached),1)
                if self.mode.startswith('late_'):
                    self.assertEqual(self.runtime_calls,2)
                    self.assertIsNotNone(scope._new)
                else:
                    self.assertIsNone(scope._new)
            self.assertTrue(life.failed)
            self.assert_closed(context,handles)
            self.asserted=True
            return True
        finally:
            # Restoration only after the negative assertions; it supplies no
            # acceptance evidence. Real native unload owns any loaded new timer.
            path.write_bytes(original_bytes)
            if context.entry.state.value=='loaded':
                await context.lease.async_unload(context.entry.entry_id)
