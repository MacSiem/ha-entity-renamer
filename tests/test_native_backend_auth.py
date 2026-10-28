"""Registered native WS commands, actual AuthManager users and role revocation.

Tests-only: run each case in a fresh FIFO native probe. The existing public
backend config-flow fixture is required. No auth/journal/runtime double, handler
replacement or hand-made waiter Task. This is not network login or full UI QA.
"""
import asyncio
import json
import logging
import sys
import unittest

import test_native_backend_lifecycle as backend_fixture


@unittest.skipUnless(backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativeBackendAuthTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = backend_fixture.NativeBackendLifecycleTests.asyncSetUp
    _before_loader = backend_fixture.NativeBackendLifecycleTests._before_loader
    _before_start = backend_fixture.NativeBackendLifecycleTests._before_start
    _loaded_backend = backend_fixture.NativeBackendLifecycleTests._loaded_backend
    with_yaml = True

    async def _connection(self, *, admin):
        from homeassistant.auth import AuthManager
        from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
        from homeassistant.components.websocket_api.connection import ActiveConnection
        hass = self.f.hass
        self.assertIs(type(hass.auth), AuthManager)
        # The first native user can become owner. Revoke only a later ordinary
        # administrator; owner authority must never be fabricated or altered.
        if not any(not existing.system_generated
                   for existing in await hass.auth.async_get_users()):
            await hass.auth.async_create_user('Native QA owner', group_ids=[GROUP_ID_ADMIN])
        user = await hass.auth.async_create_user('Native QA reader',
                         group_ids=[GROUP_ID_ADMIN if admin else GROUP_ID_USER])
        self.assertFalse(user.is_owner)
        self.assertIs(user.is_admin, admin)
        self.assertIs(await hass.auth.async_get_user(user.id), user)
        token = await hass.auth.async_create_refresh_token(user,
                            client_id='https://renamer.invalid/native-qa')
        output = hass.loop.create_future()
        def collect(raw):
            self.assertFalse(output.done(), 'one request must have one response')
            output.set_result(json.loads(raw) if isinstance(raw, (bytes, str)) else raw)
        from homeassistant.const import __version__
        self.assertIn(__version__, ('2025.6.0', '2026.10.2'))
        # Current Core requires the peer address; this collector models a local
        # connection, not network authentication or a bypass of local-only rules.
        peer = {'remote': '127.0.0.1'} if __version__ == '2026.10.2' else {}
        connection = ActiveConnection(logging.getLogger('native-renamer-ws'), hass,
                                      collect, user, token, **peer)
        self.addCleanup(connection.async_handle_close)
        return connection, user, output

    async def _revocation_while_waiting(self, command):
        from homeassistant.auth.const import GROUP_ID_USER
        backend = self._loaded_backend()
        hass = self.f.hass
        runtime = hass.data[backend.DATA_RUNTIME]
        self.assertIs(type(runtime), backend.Runtime)
        connection, user, output = await self._connection(admin=True)
        request_type = 'ha_entity_renamer/' + command
        self.assertIn(request_type, connection.handlers)
        self.assertIs(connection.handlers[request_type][0],
                      getattr(backend_fixture.installed_module('websocket_api'), 'ws_' + command))
        lock = runtime.operation_lock
        seen = hass.loop.create_future()
        lock_code = asyncio.Lock.acquire.__code__
        discover_code = backend.Runtime.discover.__code__
        observed_task = None
        tool = None
        def observe(code, offset, value):
            nonlocal observed_task
            try:
                frame = sys._getframe()
                while frame is not None and frame.f_code is not code:
                    frame = frame.f_back
                if frame is None or frame.f_locals.get('self') is not lock:
                    return
                frame = frame.f_back
                while frame is not None:
                    if frame.f_code is discover_code and frame.f_locals.get('self') is runtime:
                        observed_task = asyncio.current_task()
                        if not seen.done():
                            seen.set_result(observed_task)
                        return
                    frame = frame.f_back
            except BaseException as error:
                if not seen.done():
                    seen.set_exception(error)
        for ident in range(6):
            try:
                sys.monitoring.use_tool_id(ident, 'native registered WS role facts')
            except ValueError:
                continue
            tool = ident
            break
        self.assertIsNotNone(tool, 'no native monitoring slot available')
        await lock.acquire()
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_YIELD, observe)
            sys.monitoring.set_local_events(tool, lock_code, sys.monitoring.events.PY_YIELD)
            connection.async_handle({'id': 1, 'type': request_type, 'proposal': {}})
            await asyncio.wait((seen, output), return_when=asyncio.FIRST_COMPLETED)
            self.assertTrue(seen.done(), 'registered command returned before native Runtime lock wait')
            task = seen.result()
            self.assertIs(type(task), asyncio.Task)
            self.assertIn(task, hass._background_tasks)
            self.assertNotIn(task, hass._tasks)
            self.assertIn(task._fut_waiter, lock._waiters)
            self.assertFalse(task._fut_waiter.done())
            # The real default HA drain must finish while this real WS task is
            # still waiting. Its background classification comes from Core.
            await hass.async_block_till_done()
            self.assertFalse(task.done())
            self.assertFalse(output.done())
            await hass.auth.async_update_user(user, group_ids=[GROUP_ID_USER])
            self.assertFalse(user.is_admin)
            self.assertIs(await hass.auth.async_get_user(user.id), user)
        finally:
            sys.monitoring.set_local_events(tool, lock_code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_YIELD, None)
            sys.monitoring.free_tool_id(tool)
            lock.release()
            if observed_task is not None:
                await observed_task
        response = await output
        self.assertEqual((response['id'], response['success']), (1, False))
        self.assertEqual(response['error']['code'], 'unauthorized')
        self.assertNotIn('result', response)
        self.assertFalse(runtime.operation_lock.locked())
        self.assertEqual(await runtime.journal.active(), ())

    async def test_registered_discover_background_waiter_rechecks_real_user_role(self):
        await self._revocation_while_waiting('discover')

    async def test_registered_preview_background_waiter_rechecks_real_user_role(self):
        await self._revocation_while_waiting('preview')

    async def test_registered_status_accepts_native_admin_and_denies_household(self):
        for admin in (True, False):
            connection, user, output = await self._connection(admin=admin)
            connection.async_handle({'id': 1, 'type': 'ha_entity_renamer/status'})
            response = await output
            self.assertEqual(response['id'], 1)
            self.assertIs(response['success'], admin)
            if admin:
                self.assertIs(response['result']['migration_ready'], False)
                self.assertEqual(response['result']['operations'], [])
            else:
                self.assertEqual(response['error']['code'], 'unauthorized')
                self.assertNotIn('result', response)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativeBackendAuthTests)
