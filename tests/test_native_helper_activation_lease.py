"""Draft: actual HA classes/locks/APIs; synthetic guard authorization only.

Owner runs through FIFO in both pinned native runtimes AFTER integration.
No installed running HA API/config/database use; isolated SSD fixture only.
No patching native methods, native locks or native fields. This is lock-scope
proof only, not original Plan/root authorization or full-three escrow evidence.
"""
import asyncio
import importlib
from pathlib import Path
from types import MappingProxyType
import unittest

import test_native_input_baseline as fixtures

lease_module = importlib.import_module(fixtures.PACKAGE + '.native_helper_activation_lease')
Lease = lease_module.NativeHelperActivationLease
Refusal = lease_module.NativeHelperActivationLeaseError


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class NativeActivationLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.core import HomeAssistant
        directory = fixtures.scratch()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        (root / 'configuration.yaml').write_bytes(b'script: {}\n')
        self.hass = hass = HomeAssistant(str(root))
        self.addAsyncCleanup(hass.async_stop, force=True)
        loader.async_setup(hass)
        hass.config_entries = self.manager = ConfigEntries(hass, {})
        await loader.async_get_custom_components(hass)
        self.assertIsNot(await async_load_base_functionality(hass), False)
        await hass.async_start()
        self.allowed = True
        self.cancel_if_owned = False
        self.entries = []

    def guard(self):
        # Synthetic authorization ONLY. Never an installed admin/runtime proof.
        if self.cancel_if_owned and any(e.setup_lock.locked() for e in self.entries):
            raise asyncio.CancelledError
        if not self.allowed:
            raise PermissionError('synthetic authorization lost')

    async def helper(self, *, disabled=False, entry_id=None):
        from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
        from homeassistant.setup import async_setup_component
        self.assertTrue(await async_setup_component(self.hass, 'threshold', {}))
        self.hass.states.async_set('sensor.lease_source', '41')
        entry = ConfigEntry(domain='threshold', entry_id=entry_id,
            title='Activation lease synthetic fixture', data={},
            options={'name': 'Activation lease synthetic fixture',
                     'entity_id': 'sensor.lease_source', 'hysteresis': 0.0,
                     'lower': None, 'upper': 42.5},
            disabled_by=ConfigEntryDisabler.USER if disabled else None,
            discovery_keys=MappingProxyType({}), source='user', version=1,
            minor_version=2, subentries_data=[], unique_id=None)
        await self.manager.async_add(entry)
        await self.hass.async_block_till_done()
        self.entries.append(entry)
        return entry

    async def acquire(self, *entries, manager=None, guard=None):
        return await Lease.acquire(self.hass, self.manager if manager is None else manager,
            tuple(entries), guard=self.guard if guard is None else guard)

    async def test_no_native_api_until_explicit_call_and_real_returns(self):
        from homeassistant.config_entries import ConfigEntryState
        entry = await self.helper()
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        before = (entry.state, entry.disabled_by, entry.data, entry.options)
        lease = await self.acquire(entry)
        self.assertEqual(before, (entry.state, entry.disabled_by, entry.data, entry.options))
        async with lease:
            self.assertEqual(lease.entry_ids, (entry.entry_id,))
            self.assertIs(lease.manager, self.manager)
            self.assertTrue(entry.setup_lock.locked())
            # Acquires no second lock; actual API return is retained.
            async with asyncio.timeout(10):
                self.assertIs(await lease.async_unload(entry.entry_id), True)
                self.assertIs(entry.state, ConfigEntryState.NOT_LOADED)
                self.assertIs(await lease.async_setup(entry.entry_id), True)
                self.assertIs(entry.state, ConfigEntryState.LOADED)
            self.assertTrue(entry.setup_lock.locked())
        self.assertFalse(entry.setup_lock.locked())
        disabled = await self.helper(disabled=True)
        async with await self.acquire(disabled) as lease:
            self.assertIs(await lease.async_setup(disabled.entry_id), False)
        self.assertFalse(disabled.setup_lock.locked())

    async def test_only_owned_exact_lock_can_pass_inactive_proof(self):
        adapter = importlib.import_module(fixtures.PACKAGE + '.native_helper_transaction')
        entry = await self.helper()
        other = await self.helper(disabled=True)
        async with await self.acquire(entry) as lease:
            self.assertTrue(await lease.async_unload(entry.entry_id))
            await self.hass.async_block_till_done()
            with self.assertRaises(adapter.Refused):
                adapter._require_inactive(entry)
            adapter._require_inactive(entry, lease)
            with self.assertRaises(adapter.Refused):
                adapter._require_inactive(other, lease)
        with self.assertRaises(Refusal):
            adapter._require_inactive(entry, lease)

    async def test_ordinary_setup_queues_on_same_native_lock_until_release(self):
        entry = await self.helper(disabled=True)
        async with await self.acquire(entry):
            ordinary = asyncio.create_task(self.manager.async_setup(entry.entry_id))
            try:
                await asyncio.sleep(0)  # Schedule the actual native caller once.
                self.assertFalse(ordinary.done())
                self.assertTrue(any(not f.cancelled() for f in entry.setup_lock._waiters))
            except BaseException:
                ordinary.cancel()
                await asyncio.gather(ordinary, return_exceptions=True)
                raise
        self.assertIs(await ordinary, False)
        self.assertFalse(entry.setup_lock.locked())

    async def test_ordinary_remove_queues_on_same_native_lock_until_release(self):
        entry = await self.helper(disabled=True)
        async with await self.acquire(entry):
            ordinary = asyncio.create_task(self.manager.async_remove(entry.entry_id))
            try:
                await asyncio.sleep(0)
                self.assertFalse(ordinary.done())
                self.assertIs(self.manager.async_get_entry(entry.entry_id), entry)
                self.assertTrue(entry.setup_lock.locked())
            except BaseException:
                ordinary.cancel()
                await asyncio.gather(ordinary, return_exceptions=True)
                raise
        self.assertIs((await ordinary)['require_restart'], False)
        self.assertIsNone(self.manager.async_get_entry(entry.entry_id))

    async def test_acquire_entire_set_does_not_yield(self):
        first, second = await self.helper(disabled=True), await self.helper(disabled=True)
        callback_ran = []
        asyncio.get_running_loop().call_soon(callback_ran.append, True)
        lease = await self.acquire(first, second)
        try:
            self.assertEqual(callback_ran, [])
            self.assertTrue(first.setup_lock.locked())
            self.assertTrue(second.setup_lock.locked())
        finally:
            lease.release()
        await asyncio.sleep(0)
        self.assertEqual(callback_ran, [True])

    async def test_whole_set_busy_precheck_takes_no_free_lock(self):
        first, second = await self.helper(disabled=True), await self.helper(disabled=True)
        await second.setup_lock.acquire()
        try:
            with self.assertRaisesRegex(Refusal, 'busy'):
                await self.acquire(first, second)
            self.assertFalse(first.setup_lock.locked())
            self.assertTrue(second.setup_lock.locked())
        finally:
            second.setup_lock.release()

    async def test_genuine_waiter_refused_even_during_unlocked_handoff(self):
        first, second = await self.helper(disabled=True), await self.helper(disabled=True)
        await second.setup_lock.acquire()
        waiter = asyncio.create_task(second.setup_lock.acquire())
        await asyncio.sleep(0)
        self.assertTrue(second.setup_lock._waiters)
        second.setup_lock.release()
        # Release woke the waiter but it has not run: lock.locked() is False,
        # genuine noncancelled completed Future still has queue ownership.
        self.assertFalse(second.setup_lock.locked())
        try:
            with self.assertRaisesRegex(Refusal, 'busy'):
                await self.acquire(first, second)
            self.assertFalse(first.setup_lock.locked())
        finally:
            await waiter
            second.setup_lock.release()

    async def test_duplicate_entry_and_empty_scope_refused(self):
        entry = await self.helper(disabled=True)
        for entries in ((entry, entry), ()):
            with self.assertRaises(Refusal):
                await self.acquire(*entries)
            self.assertFalse(entry.setup_lock.locked())

    async def test_stale_original_entry_and_wrong_native_manager_refused(self):
        from homeassistant.config_entries import ConfigEntries
        entry = await self.helper(disabled=True)
        await self.manager.async_remove(entry.entry_id)
        replacement = await self.helper(disabled=True, entry_id=entry.entry_id)
        self.assertIsNot(replacement, entry)
        with self.assertRaisesRegex(Refusal, 'entry_changed'):
            await self.acquire(entry)
        other = ConfigEntries(self.hass, {})
        with self.assertRaisesRegex(Refusal, 'installed_binding_required'):
            await self.acquire(replacement, manager=other)
        self.assertFalse(entry.setup_lock.locked())
        self.assertFalse(replacement.setup_lock.locked())

    async def test_async_or_false_guard_refused_without_acquiring(self):
        entry = await self.helper(disabled=True)
        async def async_guard():
            return True
        for guard in (async_guard, lambda: False):
            with self.assertRaises(Refusal):
                await self.acquire(entry, guard=guard)
            self.assertFalse(entry.setup_lock.locked())

    async def test_guard_refusal_and_cancel_cleanup_before_any_api(self):
        first, second = await self.helper(disabled=True), await self.helper(disabled=True)
        self.allowed = False
        with self.assertRaises(PermissionError):
            await self.acquire(first, second)
        self.allowed = True
        self.cancel_if_owned = True
        with self.assertRaises(asyncio.CancelledError):
            await self.acquire(first, second)
        self.assertFalse(first.setup_lock.locked())
        self.assertFalse(second.setup_lock.locked())
        self.cancel_if_owned = False
        lease = await self.acquire(first, second)
        self.allowed = False
        # async-with does not call __aexit__ if __aenter__ fails: enter must clean.
        with self.assertRaises(PermissionError):
            async with lease:
                self.fail('guard lost before enter')
        self.assertFalse(first.setup_lock.locked())
        self.assertFalse(second.setup_lock.locked())

    async def test_context_cancel_and_guard_loss_release_own_locks(self):
        entry = await self.helper(disabled=True)
        with self.assertRaises(asyncio.CancelledError):
            async with await self.acquire(entry):
                raise asyncio.CancelledError
        self.assertFalse(entry.setup_lock.locked())
        with self.assertRaises(PermissionError):
            async with await self.acquire(entry):
                self.allowed = False
        self.assertFalse(entry.setup_lock.locked())

    async def test_stale_token_cannot_bypass_new_owner_or_outside_scope(self):
        entry = await self.helper(disabled=True)
        stale = await self.acquire(entry)
        stale.release()
        async with await self.acquire(entry) as live:
            with self.assertRaisesRegex(Refusal, 'not_owned'):
                await stale.async_setup(entry.entry_id)
            with self.assertRaisesRegex(Refusal, 'entry_outside_scope'):
                await live.async_unload('outside')
            self.assertTrue(entry.setup_lock.locked())
        self.assertFalse(entry.setup_lock.locked())
        with self.assertRaisesRegex(Refusal, 'not_owned'):
            stale.check()

    async def test_native_child_task_can_check_facts_but_cannot_write(self):
        entry = await self.helper()
        async with await self.acquire(entry) as lease:
            async def child():
                lease.check()
                self.assertEqual(lease.entry_ids, (entry.entry_id,))
                with self.assertRaisesRegex(Refusal, 'runtime_changed'):
                    await lease.async_unload(entry.entry_id)
                with self.assertRaisesRegex(Refusal, 'runtime_changed'):
                    lease.release()
            await asyncio.create_task(child())
            self.assertTrue(entry.setup_lock.locked())

    async def test_different_task_cannot_use_live_token(self):
        entry = await self.helper(disabled=True)
        async with await self.acquire(entry) as lease:
            thief = asyncio.create_task(lease.async_setup(entry.entry_id))
            with self.assertRaisesRegex(Refusal, 'runtime_changed'):
                await thief
            self.assertTrue(entry.setup_lock.locked())
            for attempt in (lease.__aenter__(), lease.__aexit__(None, None, None)):
                other = asyncio.create_task(attempt)
                with self.assertRaisesRegex(Refusal, 'runtime_changed'):
                    await other
                self.assertTrue(entry.setup_lock.locked())

    async def test_component_bootstrap_refused_before_native_api(self):
        # Use a real disabled entry in a domain not bootstrapped on this fixture.
        from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
        entry = ConfigEntry(domain='derivative', title='Unbootstrapped fixture',
            data={}, options={}, disabled_by=ConfigEntryDisabler.USER,
            discovery_keys=MappingProxyType({}), source='user', version=1,
            minor_version=1, subentries_data=[], unique_id=None)
        # Load through actual native Store/initialize: async_add would bootstrap
        # the entire domain even for a disabled entry. This fixture has no other
        # entries; no replacement of manager/entry fields or patching is used.
        await self.manager._store.async_save({'entries': [entry.as_dict()]})
        await self.manager.async_initialize()
        entry = self.manager.async_get_entry(entry.entry_id)
        self.assertIsNotNone(entry)
        self.assertNotIn('derivative', self.hass.config.components)
        async with await self.acquire(entry) as lease:
            with self.assertRaisesRegex(Refusal, 'component_bootstrap_required'):
                await lease.async_setup(entry.entry_id)
            self.assertTrue(entry.setup_lock.locked())


if __name__ == '__main__':
    unittest.main()
