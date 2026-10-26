"""Actual Recorder/Renamer concurrency regressions; owner runs both Core via FIFO.

Real HA tracked task, Runtime, AuthManager, SQL comparison, WS and unload.
The only pause wraps Renamer physical IO after its real read; no positive
Task/result/SQL/capture/count replacement or native cache/history seeding.
"""
import asyncio
import importlib
import threading
import unittest
from unittest.mock import patch

import test_native_recorder_late_fence as native_fixture

backend=native_fixture.backend
recorder=native_fixture.recorder_module


@unittest.skipUnless(native_fixture.fixture.fixtures.HAS_HA,'actual pinned HA required')
class NativeRecorderFinalLockTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=native_fixture.NativeRecorderLateFenceTests.asyncSetUp
    _before_start=native_fixture.NativeRecorderLateFenceTests._before_start
    _flush_documents=native_fixture.NativeRecorderLateFenceTests._flush_documents
    import_hour=native_fixture.NativeRecorderLateFenceTests.import_hour

    async def administrator(self):
        from homeassistant import auth
        from homeassistant.auth.const import GROUP_ID_ADMIN
        self.f.hass.auth=await auth.auth_manager_from_config(self.f.hass,[],[])
        user=await self.f.hass.auth.async_create_user('Native final fence administrator',
                                                    group_ids=[GROUP_ID_ADMIN])
        self.assertTrue(user.is_admin)
        self.assertTrue(user.is_active)
        return user

    def assert_current_metadata(self,capture):
        import json
        row=next(row for row in capture.families if row.name=='statistics')
        self.assertEqual(row.status,'COMPLETE',row.reason)
        self.assertEqual(json.loads(row.payload_json)['metadata'],self.initial['statistics'])
        self.assertFalse(capture.summary()['migration_ready'])

    async def test_real_tracked_second_runtime_read_waits_then_finishes_without_global_drain_cycle(self):
        user=await self.administrator()
        hass=self.f.hass
        second=None
        second_entered=asyncio.Event()
        first_refreshes=[]
        second_refreshes=[]
        order=[]
        def guard():
            if not user.is_active or not user.is_admin:
                raise PermissionError('revoked')
        async def real_refresh(calls):
            actual=await hass.auth.async_get_user(user.id)
            self.assertIs(actual,user)
            guard()
            calls.append(actual)
        async def second_refresh():
            await real_refresh(second_refreshes)
        async def queued_read():
            second_entered.set()
            capture=await self.runtime.discover(hass,self.proposal,guard=guard,refresh=second_refresh)
            order.append('second')
            return capture
        async def first_refresh():
            nonlocal second
            await real_refresh(first_refreshes)
            if len(first_refreshes)==2:
                # Real tracked HA Task, not the ordinary WS background path.
                second=hass.async_create_task(queued_read(),name='recorder-final-queued-read',eager_start=True)
                await second_entered.wait()
                self.assertIn(second,hass._tasks)
                self.assertFalse(second.done())
                self.assertTrue(self.runtime.operation_lock.locked())
                self.assertTrue(self.runtime.operation_lock._waiters)
                self.assertTrue(any(not waiter.done() for waiter in self.runtime.operation_lock._waiters))
        try:
            # Deadline detects the actual lock/global-drain cycle; no product cap.
            async with asyncio.timeout(15):
                first=await self.runtime.discover(hass,self.proposal,guard=guard,refresh=first_refresh)
                order.append('first')
                self.assertIsNotNone(second)
                final=await second
            self.assertEqual(order,['first','second'])
            self.assertEqual(len(first_refreshes),2)
            self.assertEqual(len(second_refreshes),2)
            self.assert_current_metadata(first)
            self.assert_current_metadata(final)
            self.assertFalse(self.runtime.operation_lock.locked())
        finally:
            if second is not None:
                if not second.done():
                    second.cancel()
                await asyncio.gather(second,return_exceptions=True)

    async def test_real_ws_discover_final_job_holds_lease_against_actual_unload(self):
        await self.ws_final_job(preview=False,cancel=False)

    async def test_real_ws_preview_final_job_holds_lease_against_actual_unload(self):
        await self.ws_final_job(preview=True,cancel=False)

    async def test_cancelled_real_ws_discover_drains_final_job_before_releasing_lease(self):
        await self.ws_final_job(preview=False,cancel=True)

    async def test_cancelled_real_ws_preview_drains_final_job_before_releasing_lease(self):
        await self.ws_final_job(preview=True,cancel=True)

    async def ws_final_job(self,*,preview,cancel):
        from homeassistant.config_entries import ConfigEntry
        from types import MappingProxyType
        user=await self.administrator()
        hass=self.f.hass
        # Genuine typed lifecycle input for the actual Renamer unload function.
        # This test exercises that backend function, not ConfigEntries dispatch.
        entry=ConfigEntry(domain=backend.DOMAIN,title='Native final fence backend',data={},
            options={},version=1,minor_version=1,source='user',unique_id='recorder-final-fence',
            discovery_keys=MappingProxyType({}),subentries_data=[])
        self.runtime.entry_id=entry.entry_id
        ws=importlib.import_module(backend.__name__+'.websocket_api')
        output_locks=[]
        class Connection:
            def __init__(self,user):
                self.user,self.output=user,None
            def send_result(self,ident,result):
                output_locks.append(self_runtime.operation_lock.locked())
                self.output=('result',result)
            def send_error(self,ident,code,message):
                output_locks.append(self_runtime.operation_lock.locked())
                self.output=('error',code)
        self_runtime=self.runtime
        connection=Connection(user)
        auth_type=type(hass.auth)
        actual_user=auth_type.async_get_user
        actual_compare=recorder.async_final_comparison
        refreshes=[]
        started=asyncio.Event()
        release=threading.Event()
        entered_threads=[]
        physical_calls=[]
        results=[]
        order=[]
        armed=False
        async def actual_user_read(manager,user_id):
            nonlocal armed
            value=await actual_user(manager,user_id)
            refreshes.append(value)
            if len(refreshes)==4:
                armed=True  # Handler's real output refresh, for discover/preview.
            return value
        async def actual_compare_with_negative_pause(native,mapping,metadata,historical,physical):
            if not armed:
                return await actual_compare(native,mapping,metadata,historical,physical)
            calls=0
            def actual_physical_then_pause():
                nonlocal calls
                physical()  # Genuine original native/YAML/consumer graph check.
                calls+=1
                physical_calls.append(calls)
                if calls==2:
                    # Actual finite SQL/history/metadata ran between both reads.
                    # Pause only this Renamer executor job, never Recorder's queue.
                    entered_threads.append(threading.get_ident())
                    hass.loop.call_soon_threadsafe(started.set)
                    if not release.wait(15):
                        raise AssertionError('negative IO pause was not released')
            try:
                value=await actual_compare(native,mapping,metadata,historical,actual_physical_then_pause)
                results.append(value)  # Actual result, never substituted True.
                return value
            finally:
                order.append('final-drained')
        async def actual_unload():
            value=await backend.async_unload_entry(hass,entry)
            order.append('unloaded')
            return value
        reader=unload=None
        try:
            with patch.object(auth_type,'async_get_user',actual_user_read), \
                    patch.object(recorder,'async_final_comparison',actual_compare_with_negative_pause):
                reader=asyncio.create_task(ws._read_discovery(hass,connection,
                    {'id':1,'proposal':self.proposal},preview=preview))
                async with asyncio.timeout(15):
                    await started.wait()
                    self.assertEqual(len(refreshes),4)
                    self.assertEqual(physical_calls,[1,2])
                    self.assertEqual(len(entered_threads),1)
                    self.assertNotEqual(entered_threads[0],threading.get_ident())
                    self.assertFalse(reader.done())
                    self.assertTrue(self.runtime.operation_lock.locked())
                    if cancel:
                        reader.cancel()
                        await asyncio.sleep(0)
                        await asyncio.sleep(0)
                        self.assertFalse(reader.done(),'cancel escaped before real job drained')
                        self.assertTrue(self.runtime.operation_lock.locked())
                    unload=asyncio.create_task(actual_unload())
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    self.assertTrue(self.runtime.unloading)
                    self.assertFalse(unload.done(),'actual unload bypassed the final read lease')
                    self.assertTrue(self.runtime.operation_lock.locked())
                    self.assertIs(hass.data[backend.DATA_RUNTIME],self.runtime)
                    self.assertTrue(self.runtime.loaded)
                    self.assertNotIn('final-drained',order)
                    release.set()
                    if cancel:
                        with self.assertRaises(asyncio.CancelledError):
                            await reader
                    else:
                        await reader
                    self.assertIs(await unload,True)
                self.assertLess(order.index('final-drained'),order.index('unloaded'))
                self.assertFalse(self.runtime.operation_lock.locked())
                self.assertFalse(self.runtime.loaded)
                self.assertNotIn(backend.DATA_RUNTIME,hass.data)
                if cancel:
                    self.assertIsNone(connection.output)
                else:
                    # Unload declared its intent while we held the lease: the
                    # suffix must refuse output after the genuine read completes.
                    self.assertEqual(results,[True])
                    self.assertEqual(connection.output,('error','unavailable'))
                    self.assertEqual(output_locks,[True])
        finally:
            # Cleanup follows the attempted ordering/refusal assertions. Release
            # the real job before cancellation and drain every task we created.
            release.set()
            tasks=[task for task in (reader,unload) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks,return_exceptions=True)
