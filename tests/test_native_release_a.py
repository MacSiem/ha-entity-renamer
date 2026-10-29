"""Release A registered restore refusal using actual HA auth and backend.

Owner runs each named case in a fresh FIFO native_unit_probe process. Monitoring
observes real calls without replacing handlers, Runtime, Store or native IO.
"""
import inspect
from pathlib import Path
import sys
import unittest

import test_native_backend_auth as auth_fixture


@unittest.skipUnless(auth_fixture.backend_fixture.activation.fixtures.HAS_HA,
                     'actual pinned HA required')
class NativeReleaseATests(unittest.IsolatedAsyncioTestCase):
    # Reuse the native fixture and connection, without inheriting its test cases.
    asyncSetUp = auth_fixture.NativeBackendAuthTests.asyncSetUp
    _before_loader = auth_fixture.NativeBackendAuthTests._before_loader
    _before_start = auth_fixture.NativeBackendAuthTests._before_start
    _loaded_backend = auth_fixture.NativeBackendAuthTests._loaded_backend
    _connection = auth_fixture.NativeBackendAuthTests._connection
    with_yaml = True

    async def _restore_refusal(self, *, admin):
        import asyncio
        from homeassistant.helpers import storage

        backend = self._loaded_backend()
        hass = self.f.hass
        runtime = hass.data[backend.DATA_RUNTIME]
        self.assertIs(type(runtime), backend.Runtime)
        ws = auth_fixture.backend_fixture.installed_module('websocket_api')
        connection, user, output = await self._connection(admin=admin)
        request_type = 'ha_entity_renamer/recovery/restore'
        self.assertIn(request_type, connection.handlers)
        self.assertIs(connection.handlers[request_type][0], ws.ws_recovery_restore)
        self.assertIs(user.is_admin, admin)
        self.assertFalse(runtime.operation_lock.locked())
        original_operations = await runtime.journal.active()
        component_root = self.installed_component.as_posix() + '/'
        storage_source = str(Path(storage.__file__).resolve())
        restore_code = inspect.unwrap(ws.ws_recovery_restore).__code__
        observed_task = None
        forbidden_calls = []
        handler_calls = []
        tool = None

        def observe(code, offset):
            nonlocal observed_task
            # Capture the actual task created by HA's registered async_response.
            # Every component/native call below is attributed to that task only.
            try:
                task = asyncio.current_task()
            except RuntimeError:  # HA executor threads have no running loop.
                return
            if code is restore_code:
                observed_task = task
                handler_calls.append(code.co_name)
            if observed_task is None or task is not observed_task:
                return
            filename = code.co_filename
            if ((filename.startswith(component_root) and code is not restore_code)
                    or filename == storage_source
                    or filename.endswith(('/homeassistant/helpers/entity_registry.py',
                                           '/homeassistant/helpers/device_registry.py',
                                           '/homeassistant/helpers/area_registry.py'))):
                forbidden_calls.append((filename, code.co_name))

        for ident in range(6):
            try:
                sys.monitoring.use_tool_id(ident, 'Release A registered restore refusal')
            except ValueError:
                continue
            tool = ident
            break
        self.assertIsNotNone(tool, 'no native monitoring slot available')
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, observe)
            sys.monitoring.set_events(tool, sys.monitoring.events.PY_START)
            connection.async_handle({'id': 1, 'type': request_type,
                                     'operation_id': 'release-a-refusal',
                                     'fingerprint': '0' * 64})
            response = await asyncio.wait_for(output, 15)
        finally:
            sys.monitoring.set_events(tool, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
        self.assertEqual((response['id'], response['success']), (1, False))
        self.assertEqual(response['error']['code'],
                         'feature_unavailable' if admin else 'unauthorized')
        self.assertNotIn('result', response)
        self.assertEqual(handler_calls, ['ws_recovery_restore'] if admin else [])
        self.assertEqual(forbidden_calls, [], 'restore entered Runtime, Store or native IO')
        self.assertIs(hass.data[backend.DATA_RUNTIME], runtime)
        self.assertFalse(runtime.operation_lock.locked())
        self.assertEqual(await runtime.journal.active(), original_operations)

    async def test_registered_restore_admin_refuses_before_runtime_store_native_io(self):
        await self._restore_refusal(admin=True)

    async def test_registered_restore_household_is_unauthorized(self):
        await self._restore_refusal(admin=False)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativeReleaseATests)
