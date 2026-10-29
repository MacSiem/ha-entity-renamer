"""Actual own-ID lifecycle unload; no registry/cache mutation or readiness claim."""
import importlib
import unittest

import test_utility_meter_observers as fixture
import test_native_helper_quiescence as quiescence

PACKAGE = fixture.fixtures.PACKAGE
lm = importlib.import_module(PACKAGE + '.native_helper_lifecycle')


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class OwnHelperUnloadLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ActualUtilityMeterObserverTests.asyncSetUp
    _before_start = fixture.ActualUtilityMeterObserverTests._before_start
    _fixture = fixture.ActualUtilityMeterObserverTests._fixture
    _emit = fixture.ActualUtilityMeterObserverTests._emit
    _flush_documents = quiescence.NativeHelperQuiescenceTests._flush_documents

    def guard(self):
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id), self.entry)

    async def _configure_own_probe(self, hass, root):
        (root / 'configuration.yaml').write_text('script:\n  own_id_probe:\n'
            '    sequence:\n      - variables:\n'
            '          sample: "{{ states(\'' + self.old_helper + '\') }}"\n')

    async def _validate_own_probe(self, hass, proposal, root):
        return await fixture.fixtures.validation.async_validate_proposed(
            hass, proposal, root.parent)

    async def _prepare_own_plan(self, preparations, step, digest, native_ref, receipt, proposal):
        tx = importlib.import_module(PACKAGE + '.transaction')
        plan = tx.Plan('own-helper-unload', ('registry',), ('registry',), (step,),
            'native-unload-lifecycle', ('registry',), native_ref)
        return plan, {'own-helper-change': digest}

    async def _lifecycle(self):
        await self._fixture()
        hass = self.f.hass
        self.old_helper = self.entity.entity_id
        self.target = 'sensor.lifecycle_own_helper_changed'
        # ORIGINAL actual helper/source observation, unchanged helper configuration.
        observer = await fixture.h.NativeHelperObservers.capture(hass, [self.before], ())
        self.own_observer = observer  # retain the ORIGINAL A0/S0, never recapture
        root = self.f.root
        await self._configure_own_probe(hass, root)
        await self._flush_documents(initialize=True)
        yp = importlib.import_module(PACKAGE + '.yaml_plan')
        proposal = yp.plan_yaml(root, {self.old_helper: self.target})
        self.assertTrue(proposal.ready, proposal.issues)
        receipt = await self._validate_own_probe(hass, proposal, root)
        self.assertTrue(receipt.ready_for_apply)
        original = await fixture.fixtures.baseline.async_capture_native_input_baseline(
            hass, receipt, guard=self.guard)
        prep = importlib.import_module(PACKAGE + '.prepared')
        nr = importlib.import_module(PACKAGE + '.native_registry')
        rr = importlib.import_module(PACKAGE + '.native_registry_receipts')
        tx = importlib.import_module(PACKAGE + '.transaction')
        utility = importlib.import_module(PACKAGE + '.native_utility_unload_audit')
        preparations = prep.native_prepared_store(hass)
        await preparations.initialize()
        native_ref = await preparations.create_native_inputs('own-helper-original', original)
        self.row = utility._row(self.f.entity.async_get(self.old_helper))
        import json
        change = nr.PreparedRegistryChange('entity', self.row['id'], json.dumps(self.row),
            json.dumps(dict(self.row, entity_id=self.target)))
        if hasattr(self, '_prepare_own_change'):
            change = await self._prepare_own_change()
        digest = await preparations.create('own-helper-change', prep.encode_change(change))
        step = tx.Step('own-helper', 'registry', change.identity,
            change.before_revision, change.after_revision, 'own-helper-change')
        plan, digests = await self._prepare_own_plan(
            preparations, step, digest, native_ref, receipt, proposal)
        store = lm.native_helper_lifecycle_store(hass, preparations)
        await store.initialize()
        lease_module = importlib.import_module(PACKAGE + '.native_helper_activation_lease')
        self.lease = await lease_module.NativeHelperActivationLease.acquire(
            hass, hass.config_entries, (self.entry,), guard=self.guard)
        async def original_source(reference, actual_receipt):
            self.assertEqual((reference, actual_receipt), (native_ref, receipt))
            return original == await fixture.fixtures.baseline.async_capture_native_input_baseline(
                hass, receipt, guard=self.guard)
        async def inactive(entry_id):
            from homeassistant.helpers.entity_platform import async_get_platforms
            self.assertEqual(entry_id, self.entry.entry_id)
            self.assertEqual(self.entry.state.value, 'not_loaded')
            self.assertFalse(any(p.entities for p in async_get_platforms(hass, 'utility_meter')
                                 if p.config_entry is self.entry))
            self.assertEqual(self.own_observer._rows(entry_id),
                self.own_observer.expected_registry_rows(entry_id))
            return True
        async def unused(*args):
            raise AssertionError('unused setup/reverse verifier invoked during unload')
        self.args = (store, hass.config_entries, 'own-helper-unload', plan, receipt,
                     digests)
        self.kwargs = dict(guard=self.guard, activation_lease=self.lease,
            verify_original_native_inputs=original_source, settle=hass.async_block_till_done,
            verify_inactive=inactive, verify_runtime=unused, verify_accepted_native_chain=unused,
            runtime_baselines=observer.runtime_baselines())
        return await lm.NativeHelperLifecycle.start(*self.args, **self.kwargs)

    async def test_real_own_id_unload_receipt_retains_causal_live_state(self):
        life = await self._lifecycle()
        try:
            await life.quiesce()
            slot = life._slot('unload', self.entry.entry_id, 'receipt')
            receipt = life.store.read(slot)
            self.assertIn('native_unload', receipt['payload'])
            state = self.f.hass.states.get(self.old_helper)
            self.assertTrue(life.verify_live_unload_state(self.entry.entry_id,
                (slot, receipt['digest']), state))
            reopened = lm.NativeHelperLifecycle.reopen(*self.args, **self.kwargs)
            self.assertTrue(reopened.inspect()['inactive_receipts_complete'])
            with self.assertRaises(lm.HelperLifecycleError):
                reopened.verify_live_unload_state(self.entry.entry_id,
                    (slot, receipt['digest']), state)
            self.assertFalse(reopened.writable)
        finally:
            life.close_source_gaps()
            self.lease.release()
