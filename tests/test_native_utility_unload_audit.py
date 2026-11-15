"""NOT RUN. Integrate as tests/test_native_utility_unload_audit.py under owner FIFO.

Reuse actual producer + native helper fixture; no fixture state seeding.
A no-op guard below proves native choreography only, never installed authority.
"""
import importlib
import sys
import unittest

import test_native_input_baseline as fixtures
import test_utility_meter_observers as utility_fixture

u = importlib.import_module(fixtures.PACKAGE + '.native_utility_unload_audit')
lease_module = importlib.import_module(fixtures.PACKAGE + '.native_helper_activation_lease')


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class ActualUtilityUnloadAuditTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = utility_fixture.ActualUtilityMeterObserverTests.asyncSetUp
    _before_start = utility_fixture.ActualUtilityMeterObserverTests._before_start
    _fixture = utility_fixture.ActualUtilityMeterObserverTests._fixture
    _emit = utility_fixture.ActualUtilityMeterObserverTests._emit
    async def _audit_fixture(self):
        await self._fixture()  # producer 100 -> 107 -> 111, helper == 11
        await self.f.hass.async_block_till_done()  # deliver native option/registry callbacks
        self.helper_id = self.entity.entity_id
        self.expected_row = self.observer.expected_registry_rows(self.entry.entry_id)[0]
        self.lease = await lease_module.NativeHelperActivationLease.acquire(
            self.f.hass, self.f.hass.config_entries, (self.entry,), guard=lambda: None)
        self.audit = u.NativeUtilityUnloadAudit(self.f.hass, self.entry,
                                               self.expected_row, guard=lambda: None)

    async def test_real_native_unload_captures_exact_object_and_scope(self):
        await self._audit_fixture()
        try:
            old_profile = sys.getprofile()
            witness = await self.audit.async_unload(self.lease)
            self.assertIs(sys.getprofile(), old_profile)
            state = self.f.hass.states.get(self.helper_id)
            self.assertIs(state, self.audit._captured)
            self.assertEqual(state.state, 'unavailable')
            self.assertIs(state.attributes['restored'], True)
            self.assertTrue(witness.check_live(hass=self.f.hass, entry=self.entry,
                lease=self.lease, actual_state=state, expected_data=witness.data))
            encoded = u.encode_unload_audit(witness.data)
            self.assertEqual(encoded['payload']['source_row'], self.expected_row)
            self.assertEqual(encoded['payload']['writer_row'], u._row(self.entity.registry_entry))
            from copy import deepcopy
            self.assertIs(encoded['payload']['reserved'],
                          self.helper_id in self.f.hass.states._reservations)
            for key, value in (('schema', True), ('core', '2026.10.3'), ('reserved', 0),
                               ('entry_id', 'foreign'), ('state_json', '{}')):
                changed = deepcopy(encoded)
                changed['payload'][key] = value
                with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                    u.decode_unload_audit(changed)
            for key in ('same_actor', 'same_hass', 'same_row', 'same_state_at_finish',
                        'native_unloader_return_true', 'same_native_set_state', 'return_none'):
                changed = deepcopy(encoded)
                changed['payload']['call'][key] = False
                with self.subTest(key=key), self.assertRaises(u.UtilityUnloadAuditError):
                    u.decode_unload_audit(changed)
            changed = deepcopy(encoded)
            changed['payload']['writer_row']['original_name'] = 'Foreign native writer'
            with self.assertRaises(u.UtilityUnloadAuditError):
                u.decode_unload_audit(changed)
            changed = deepcopy(encoded)
            changed['authority'] = True
            with self.assertRaises(u.UtilityUnloadAuditError):
                u.decode_unload_audit(changed)
            with self.assertRaises(u.UtilityUnloadAuditError):
                u.NativeUtilityUnloadWitness()
            self.assertEqual(u.decode_unload_audit(encoded), witness.data)
            self.assertEqual(encoded['payload']['state_json'], state.as_dict_json.decode())
            self.assertTrue(u.validate_unload_audit(witness.data,
                expected_core=self.audit.core, expected_entry_id=self.entry.entry_id,
                expected_row=self.expected_row, expected_attrs=self.audit._attrs))
            # Both exact Core pins required. Do NOT rename/remove here: owner runs
            # min/current placeholder seam + counter preservation in its lifecycle.
        finally:
            self.lease.release()

    async def test_existing_profiler_refused_without_native_api_or_replacement(self):
        await self._audit_fixture()
        try:
            foreign = lambda frame, event, arg: None
            self.assertIsNone(sys.getprofile())
            sys.setprofile(foreign)
            try:
                with self.assertRaisesRegex(u.UtilityUnloadAuditError, 'existing_profile'):
                    await self.audit.async_unload(self.lease)
                self.assertIs(sys.getprofile(), foreign)
                self.assertEqual(self.entry.state.value, 'loaded')
            finally:
                sys.setprofile(None)
        finally:
            self.lease.release()

    async def test_another_task_cannot_use_live_witness(self):
        import asyncio
        await self._audit_fixture()
        try:
            witness = await self.audit.async_unload(self.lease)
            state = self.f.hass.states.get(self.helper_id)
            async def foreign_task():
                with self.assertRaisesRegex(u.UtilityUnloadAuditError, 'same_task_required'):
                    witness.check_live(hass=self.f.hass, entry=self.entry,
                        lease=self.lease, actual_state=state)
            await asyncio.create_task(foreign_task())
        finally:
            self.lease.release()

    async def test_guard_loss_refuses_and_cleans_only_owned_profiler(self):
        await self._audit_fixture()
        try:
            witness = await self.audit.async_unload(self.lease)
            self.audit.guard = lambda: False
            with self.assertRaisesRegex(u.UtilityUnloadAuditError, 'guard_changed'):
                witness.check_live(hass=self.f.hass, entry=self.entry, lease=self.lease,
                    actual_state=self.f.hass.states.get(self.helper_id))
            self.assertIsNone(sys.getprofile())
        finally:
            self.lease.release()

    async def test_live_witness_cannot_be_copied_or_pickled(self):
        from copy import copy, deepcopy
        import pickle
        await self._audit_fixture()
        try:
            witness = await self.audit.async_unload(self.lease)
            for function in (copy, deepcopy, pickle.dumps):
                with self.subTest(function=function), self.assertRaises(u.UtilityUnloadAuditError):
                    function(witness)
        finally:
            self.lease.release()


    async def test_identical_foreign_native_state_is_not_unload_state(self):
        await self._audit_fixture()
        try:
            witness = await self.audit.async_unload(self.lease)
            old = self.f.hass.states.get(self.helper_id)
            self.f.hass.states.async_set(self.helper_id, old.state,
                dict(old.attributes), force_update=True, context=old.context)
            replacement = self.f.hass.states.get(self.helper_id)
            self.assertIsNot(old, replacement)
            self.assertEqual(dict(old.attributes), dict(replacement.attributes))
            with self.assertRaisesRegex(u.UtilityUnloadAuditError, 'live_binding'):
                witness.check_live(hass=self.f.hass, entry=self.entry,
                    lease=self.lease, actual_state=replacement)
            with self.assertRaises(u.UtilityUnloadAuditError):
                witness.check_live(hass=self.f.hass, entry=self.entry,
                    lease=self.lease, actual_state=old)
            self.assertIs(self.f.hass.states.get(self.helper_id), replacement)
        finally:
            self.lease.release()

    async def test_foreign_original_row_refuses_before_unload(self):
        await self._audit_fixture()
        try:
            foreign = u.NativeUtilityUnloadAudit(self.f.hass, self.entry,
                dict(self.expected_row, id='foreign'), guard=lambda: None)
            with self.assertRaisesRegex(u.UtilityUnloadAuditError, 'accepted_registry_row'):
                await foreign.async_unload(self.lease)
            self.assertEqual(self.entry.state.value, 'loaded')
            self.assertIsNone(sys.getprofile())
        finally:
            self.lease.release()
