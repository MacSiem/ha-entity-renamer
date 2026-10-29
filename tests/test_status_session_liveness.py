"""SOURCE/mixed status contract: real HA/AuthManager, fake readonly projection IO.

Invokes native WS decorators/handler through the existing transport collector;
this is not a new registered-WS, cold recovery, controller, persistence or login
proof. Runtime.recovery_status is the explicit AsyncMock read projection seam.
A valid connection references the SAME native User object held by AuthStore.
"""
from copy import copy
import importlib
import unittest
from unittest.mock import AsyncMock

import test_native_input_baseline as fixture
import test_discovery_websocket as websocket_fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class SessionStatusTests(unittest.IsolatedAsyncioTestCase):
    call_handler = websocket_fixture.DiscoveryWebsocketTests.call

    async def asyncSetUp(self):
        # Reuse the ordinary native fixture, without public backend loader/cache
        # installation per test and without DiscoveryWebsocket's fake AuthManager.
        self.f = fixture.NativeBaselineTests('test_actual_native_serialization_and_store_full_baseline')
        await self.f.asyncSetUp()
        self.addAsyncCleanup(self.f.asyncTearDown)
        self.backend = importlib.import_module('custom_components.ha_entity_renamer')
        self.ws = importlib.import_module(self.backend.__name__ + '.websocket_api')
        self.runtime = self.backend.Runtime('fixture', object(), object())
        self.f.hass.data[self.backend.DATA_RUNTIME] = self.runtime

    async def setup_status(self):
        from homeassistant import auth
        from homeassistant.auth import AuthManager
        from homeassistant.auth.const import GROUP_ID_ADMIN
        hass = self.f.hass
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        self.assertIs(type(hass.auth), AuthManager)
        # First public user becomes owner. All role/liveness changes below target
        # a second ordinary administrator; owner authority is never fabricated.
        if not any(not existing.system_generated for existing in await hass.auth.async_get_users()):
            owner = await hass.auth.async_create_user('Status fixture owner', group_ids=[GROUP_ID_ADMIN])
            self.assertTrue(owner.is_owner)
        self.user = await hass.auth.async_create_user('Status fixture administrator', group_ids=[GROUP_ID_ADMIN])
        self.assertFalse(self.user.is_owner)
        self.assertTrue(self.user.is_admin)
        self.assertTrue(self.user.is_active)
        self.session = self.persisted = self.user
        self.assertIs(await hass.auth.async_get_user(self.user.id), self.session)
        self.projection = {
            'version': '5.0.0.dev0', 'migration_ready': False, 'family_complete': False,
            'reason': 'dependency_coverage_incomplete', 'recovery_required': True,
            'operations': [{'operation_id': 'private-opaque-operation',
                'fingerprint': 'a' * 64, 'status': 'applied', 'direction': 'forward',
                'recovery_required': True, 'reason': 'setup_ack_pending', 'eligible': False}]}
        self.runtime.recovery_status = AsyncMock(return_value=self.projection)

    async def call_status(self):
        from homeassistant.exceptions import Unauthorized
        try:
            return await self.call_handler('status')
        except Unauthorized:
            # require_admin can reject synchronously before async_response.
            # Normalize only the transport error shape used by these source tests.
            return ('error', 'unauthorized')

    async def test_inactive_session_cannot_receive_operations(self):
        await self.setup_status()
        await self.f.hass.auth.async_update_user(self.session, is_active=False)
        output = await self.call_status()
        self.assertEqual(output[:2], ('error', 'unauthorized'))
        self.assertNotIn('private-opaque-operation', str(output))
        self.runtime.recovery_status.assert_not_awaited()

    async def test_session_deactivated_during_projection_await(self):
        await self.setup_status()
        async def read(hass, **kwargs):
            await hass.auth.async_update_user(self.session, is_active=False)
            return self.projection  # Handler must own its final output decision.
        self.runtime.recovery_status.side_effect = read
        output = await self.call_status()
        self.assertEqual(output[:2], ('error', 'unauthorized'))
        self.assertNotIn('private-opaque-operation', str(output))
        self.runtime.recovery_status.assert_awaited_once()

    async def test_valid_admin_status_remains_readonly(self):
        await self.setup_status()
        output = await self.call_status()
        self.assertEqual(output, ('result', self.projection))
        self.assertIs(output[1]['migration_ready'], False)
        self.assertIs(await self.f.hass.auth.async_get_user(self.user.id), self.user)
        self.runtime.recovery_status.assert_awaited_once()
        args, kwargs = self.runtime.recovery_status.await_args
        self.assertEqual(args, (self.f.hass,))
        self.assertEqual(set(kwargs), {'guard', 'refresh'})
        self.assertTrue(all(callable(value) for value in kwargs.values()))

    async def test_persisted_admin_revoked(self):
        from homeassistant.auth.const import GROUP_ID_USER
        await self.setup_status()
        await self.f.hass.auth.async_update_user(self.persisted, group_ids=[GROUP_ID_USER])
        self.assertIs(self.session, self.persisted)
        self.assertFalse(self.session.is_admin)
        self.assertEqual((await self.call_status())[:2], ('error', 'unauthorized'))
        self.runtime.recovery_status.assert_not_awaited()

    async def test_session_admin_revoked_during_await(self):
        from homeassistant.auth.const import GROUP_ID_USER
        await self.setup_status()
        async def read(hass, **kwargs):
            await hass.auth.async_update_user(self.session, group_ids=[GROUP_ID_USER])
            return self.projection
        self.runtime.recovery_status.side_effect = read
        self.assertEqual((await self.call_status())[:2], ('error', 'unauthorized'))
        self.runtime.recovery_status.assert_awaited_once()

    async def test_identity_removed_during_await(self):
        await self.setup_status()
        async def read(hass, **kwargs):
            await hass.auth.async_remove_user(self.persisted)
            self.assertIsNone(await hass.auth.async_get_user(self.persisted.id))
            return self.projection
        self.runtime.recovery_status.side_effect = read
        self.assertEqual((await self.call_status())[:2], ('error', 'unauthorized'))
        self.runtime.recovery_status.assert_awaited_once()

    async def test_stale_native_session_object_with_same_id_is_refused(self):
        await self.setup_status()
        # A deliberate invalid/stale transport identity, NEVER a valid fixture
        # user or AuthStore replacement. Native user id/roles alone are insufficient.
        self.user = copy(self.persisted)
        self.assertEqual(self.user.id, self.persisted.id)
        self.assertIsNot(self.user, self.persisted)
        self.assertIs(await self.f.hass.auth.async_get_user(self.user.id), self.persisted)
        self.assertEqual((await self.call_status())[:2], ('error', 'unauthorized'))
        self.runtime.recovery_status.assert_not_awaited()

    async def test_runtime_unloaded_during_await(self):
        await self.setup_status()
        async def read(hass, **kwargs):
            self.runtime.unloading = True
            return self.projection
        self.runtime.recovery_status.side_effect = read
        self.assertEqual((await self.call_status())[:2], ('error', 'unavailable'))

    async def test_runtime_replaced_during_await(self):
        await self.setup_status()
        async def read(hass, **kwargs):
            hass.data[self.backend.DATA_RUNTIME] = self.backend.Runtime('replacement', object(), object())
            return self.projection
        self.runtime.recovery_status.side_effect = read
        self.assertEqual((await self.call_status())[:2], ('error', 'unavailable'))

    async def test_read_errors_are_sanitized(self):
        await self.setup_status()
        self.runtime.recovery_status.side_effect = RuntimeError('private-storage-path')
        output = await self.call_status()
        self.assertEqual(output[:2], ('error', 'recovery_unavailable'))
        self.assertNotIn('private-storage-path', str(output))
        self.assertNotIn('private-opaque-operation', str(output))
