"""Runtime routing/lease/authorization tests, not native discovery evidence."""
import asyncio
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

backend = importlib.import_module('custom_components.ha_entity_renamer')


class DiscoveryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = backend.Runtime('entry', object(), object())
        self.hass = SimpleNamespace(data={backend.DATA_RUNTIME: self.runtime})
        self.proposal = {'entities': [], 'devices': [], 'statistics': []}
        self.refresh = AsyncMock()
        self.guard = lambda: None

    async def discover(self):
        return await self.runtime.discover(self.hass, self.proposal,
                                           guard=self.guard, refresh=self.refresh)

    async def test_read_only_backend_capture_is_under_shared_operation_lock(self):
        discovery = importlib.import_module(backend.__name__ + '.discovery')
        capture = object()
        async def source(hass, proposal, guard):
            self.assertTrue(self.runtime.operation_lock.locked())
            self.assertIs(hass, self.hass)
            self.assertEqual(proposal, self.proposal)
            guard()
            return capture
        with patch.object(discovery, 'async_discover', source):
            self.assertIs(await self.discover(), capture)
        self.assertFalse(self.runtime.operation_lock.locked())
        self.assertGreaterEqual(self.refresh.await_count, 2)

    async def test_unloaded_or_replaced_runtime_refuses_before_source(self):
        for state in ('unloaded', 'unloading', 'replaced'):
            with self.subTest(state=state):
                self.runtime.loaded = state != 'unloaded'
                self.runtime.unloading = state == 'unloading'
                self.hass.data[backend.DATA_RUNTIME] = object() if state == 'replaced' else self.runtime
                with self.assertRaisesRegex(ValueError, 'runtime_unavailable'):
                    await self.discover()

    async def test_revocation_during_capture_cannot_return_private_capture(self):
        discovery = importlib.import_module(backend.__name__ + '.discovery')
        authorized = True
        def guard():
            if not authorized:
                raise PermissionError('revoked')
        self.guard = guard
        async def source(*args):
            nonlocal authorized
            authorized = False
            return object()
        with patch.object(discovery, 'async_discover', source):
            with self.assertRaises(PermissionError):
                await self.discover()
        self.assertFalse(self.runtime.operation_lock.locked())

    async def test_pending_lease_rechecks_runtime_after_acquisition(self):
        await self.runtime.operation_lock.acquire()
        pending = asyncio.create_task(self.discover())
        await asyncio.sleep(0)
        self.runtime.unloading = True
        self.runtime.operation_lock.release()
        with self.assertRaisesRegex(ValueError, 'runtime_unavailable'):
            await pending

    async def test_preview_uses_same_runtime_discovery_and_server_composer(self):
        discovery = importlib.import_module(backend.__name__ + '.discovery')
        capture, result = object(), {'migration_ready': False}
        with patch.object(self.runtime, 'discover', new=AsyncMock(return_value=capture)) as read, \
                patch.object(discovery, 'compose_preview', return_value=result) as compose:
            actual = await self.runtime.preview(self.hass, self.proposal,
                guard=self.guard, refresh=self.refresh)
        self.assertEqual(actual, result)
        read.assert_awaited_once_with(self.hass, self.proposal,
                                     guard=self.guard, refresh=self.refresh)
        compose.assert_called_once_with(capture)

    async def test_cancelled_capture_releases_lease_without_a_result(self):
        discovery = importlib.import_module(backend.__name__ + '.discovery')
        entered = asyncio.Event()
        async def source(*args):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(discovery, 'async_discover', source):
            pending = asyncio.create_task(self.discover())
            await entered.wait()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertFalse(self.runtime.operation_lock.locked())


    async def test_final_native_read_continuation_follows_last_authorization_io(self):
        # Explicit fake read IO proves topology only, never native SQL/history.
        from custom_components.ha_entity_renamer.discovery import DiscoveryCapture
        from custom_components.ha_entity_renamer.discovery_final_fence import _FinalFence,_attach
        discovery = importlib.import_module(backend.__name__ + '.discovery')
        calls=[]
        async def refresh():
            calls.append('auth')
        async def finish(guard):
            self.assertTrue(self.runtime.operation_lock.locked())
            guard()
            calls.append('final-read')
            return capture
        capture=_attach(DiscoveryCapture('id','test',b'{}',None,(),()),_FinalFence(self.hass,finish))
        async def source(*args):
            calls.append('source')
            return capture
        with patch.object(discovery,'async_discover',source):
            result=await self.runtime.discover(self.hass,self.proposal,guard=self.guard,refresh=refresh)
        self.assertIs(result,capture)
        self.assertEqual(calls,['auth','source','auth','final-read'])

    async def test_revocation_during_final_read_refuses_the_capture(self):
        # A new await exists after the final refresh; its synchronous guard must
        # still reject a current user's revocation before output.
        from custom_components.ha_entity_renamer.discovery import DiscoveryCapture
        from custom_components.ha_entity_renamer.discovery_final_fence import _FinalFence,_attach
        discovery = importlib.import_module(backend.__name__ + '.discovery')
        authorized=True
        def guard():
            if not authorized:
                raise PermissionError('revoked')
        async def finish(current):
            nonlocal authorized
            authorized=False
            return capture
        capture=_attach(DiscoveryCapture('id','test',b'{}',None,(),()),_FinalFence(self.hass,finish))
        with patch.object(discovery,'async_discover',new=AsyncMock(return_value=capture)):
            with self.assertRaises(PermissionError):
                await self.runtime.discover(self.hass,self.proposal,guard=guard,refresh=self.refresh)
        self.assertFalse(self.runtime.operation_lock.locked())
