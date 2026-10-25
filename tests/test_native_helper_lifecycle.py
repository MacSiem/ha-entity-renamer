"""Synthetic lifecycle failure boundaries; owner runs integrated via FIFO.

Callbacks here are fakes for choreography only, not native readiness evidence.
Native Threshold proof must separately exercise installed trusted IO on both HA.
"""
import importlib
from types import SimpleNamespace
import unittest

import test_receipted_helper_adapter as fixture

mixed = fixture.mixed
module = importlib.import_module(mixed.base.PACKAGE + '.native_helper_lifecycle')


class Harness:
    async def initialize(self, test):
        h = await fixture.Harness().initialize(test)
        self.h = h
        self.disk = mixed.base.MemoryDurable()
        self.store = mixed.receipts.RegistryReceiptStore(self.disk, h.f['preparations'])
        await self.store.initialize()
        self.calls = []
        self.original_valid = self.chain_valid = self.runtime_valid = True
        self.fail_api = False
        # Pick an enabled ordinary helper; other original inactive entries stay so.
        self.active = next(entry for entry in h.entries.values() if entry.disabled_by is None)
        self.active.state.value = 'loaded'

        async def unload(key):
            test.assertIsNotNone(self.obj._read(self.obj._slot('unload', key, 'intent')))
            self.calls.append(('unload', key))
            h.entries[key].state.value = 'not_loaded'
            if self.fail_api:
                raise OSError('synthetic API ACK lost')
            return True

        async def setup(key):
            test.assertIsNotNone(self.obj._read(self.obj._slot('setup', key, 'intent')))
            self.calls.append(('setup', key))
            h.entries[key].state.value = 'loaded'
            return True

        async def original(*_):
            return self.original_valid

        async def chain(*_):
            return self.chain_valid

        async def runtime(*_):
            return self.runtime_valid

        async def inactive(key):
            return h.entries[key].state.value == 'not_loaded'

        async def settle():
            return True

        self.manager = SimpleNamespace(async_get_entry=h.entries.get,
                                       async_unload=unload, async_setup=setup)
        self.args = (self.store, self.manager, 'provided-operation-id', h.f['plan'],
                     h.f['receipt'], h.f['digests'])
        self.kwargs = dict(guard=h.guard, verify_original_native_inputs=original,
            settle=settle, verify_inactive=inactive, verify_runtime=runtime,
            verify_accepted_native_chain=chain)
        self.obj = await module.NativeHelperLifecycle.start(*self.args, **self.kwargs)
        self.authorization = module.RestoreAuthorization('provided-operation-id',
            h.f['plan'].fingerprint, 'a' * 64, 'applied')
        return self

    async def reopen(self):
        # Fresh store reads durable content, including unacknowledged saves.
        preparations = mixed.prepared.PreparedStore(self.h.f['prep_disk'])
        await preparations.initialize()
        store = mixed.receipts.RegistryReceiptStore(self.disk, preparations)
        await store.initialize()
        return module.NativeHelperLifecycle.reopen(store, *self.args[1:], **self.kwargs)


class HelperLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_plan_survives_crash_before_inner_journal_creation(self):
        h = await Harness().initialize(self)
        self.assertEqual(h.h.log.rows, {})
        await h.obj.quiesce()
        reopened = await h.reopen()
        recovered_plan = module.stored_lifecycle_plan(reopened.store, reopened.root_id)
        self.assertEqual(recovered_plan, h.h.f['plan'])
        self.assertEqual(recovered_plan.fingerprint, h.h.f['plan'].fingerprint)
        self.assertEqual(h.h.log.rows, {})

    async def test_reverse_quiescence_requires_same_applied_accepted_chain(self):
        h = await Harness().initialize(self)
        await h.obj.quiesce()
        await h.obj.restore(h.authorization)
        h.original_valid = False
        forward_root = h.obj.root_id
        reverse = await module.NativeHelperLifecycle.start(*h.args, **h.kwargs,
            source_authorization=h.authorization)
        self.assertNotEqual(reverse.root_id, forward_root)
        h.obj = reverse  # fake API checks this cycle's independently durable intent
        await reverse.quiesce()
        reverse.require_quiescent()
        await reverse.restore(module.RestoreAuthorization('provided-operation-id',
            h.h.f['plan'].fingerprint, 'b' * 64, 'rolled_back'))
        self.assertEqual(len(h.calls), 4)
        h.chain_valid = False
        with self.assertRaises(module.HelperLifecycleError):
            await module.NativeHelperLifecycle.start(*h.args, **h.kwargs,
                source_authorization=h.authorization)

    async def test_cached_original_activation_cannot_override_durable_root(self):
        h = await Harness().initialize(self)
        h.obj.root['original'][0]['state'] = (
            'not_loaded' if h.obj.root['original'][0]['state'] == 'loaded' else 'loaded')
        with self.assertRaisesRegex(module.HelperLifecycleError, 'root_binding_mismatch'):
            await h.obj.quiesce()
        self.assertEqual(h.calls, [])

    async def test_only_originally_loaded_restored_and_no_original_refresh(self):
        h = await Harness().initialize(self)
        await h.obj.quiesce()
        h.obj.require_quiescent()
        self.assertTrue(h.obj.inspect()['inactive_receipts_complete'])
        # Original raw bytes intentionally change only in the INNER migration.
        h.original_valid = False
        await h.obj.restore(h.authorization)
        self.assertEqual(h.calls, [('unload', h.active.entry_id), ('setup', h.active.entry_id)])
        self.assertTrue(h.obj.inspect()['activation_receipts_complete'])
        self.assertFalse(h.obj.inspect()['full_ready'])
        await h.obj.restore(h.authorization)
        self.assertEqual(len(h.calls), 2)

    async def test_api_ack_loss_never_replayed_and_read_only_reconcile(self):
        h = await Harness().initialize(self)
        h.fail_api = True
        with self.assertRaises(OSError):
            await h.obj.quiesce()
        reopened = await h.reopen()
        report = await reopened.reconcile()
        self.assertEqual(report['pending'][0]['stage'], 'unload')
        self.assertEqual(report['unload'][h.active.entry_id], 'unknown')
        self.assertEqual(report['current_activation'][h.active.entry_id]['state'], 'not_loaded')
        with self.assertRaises(module.HelperLifecycleError):
            await reopened.quiesce()
        self.assertEqual(h.calls, [('unload', h.active.entry_id)])

    async def test_unload_intent_save_ack_loss_prevents_native_call(self):
        h = await Harness().initialize(self)
        h.disk.lose_ack = True
        with self.assertRaises(OSError):
            await h.obj.quiesce()
        self.assertEqual(h.calls, [])
        reopened = await h.reopen()
        self.assertTrue(reopened.inspect()['pending'])
        with self.assertRaises(module.HelperLifecycleError):
            await reopened.restore(h.authorization)

    async def test_receipt_save_ack_loss_reopens_verified_but_no_writer(self):
        h = await Harness().initialize(self)
        save = h.disk.save
        async def lost_receipt(value):
            if any(row['payload']['kind'] == 'helper_lifecycle_receipt'
                   for row in value['events'].values()):
                h.disk.lose_ack = True
            await save(value)
        h.disk.save = lost_receipt
        with self.assertRaises(OSError):
            await h.obj.quiesce()
        reopened = await h.reopen()
        # Some first entry's inactive receipt reached storage despite lost ACK.
        self.assertIn('verified', reopened.inspect()['unload'].values())
        with self.assertRaises(module.HelperLifecycleError):
            await reopened.quiesce()
        self.assertLessEqual(len(h.calls), 1)

    async def test_false_source_chain_runtime_and_guard_fail_closed(self):
        for boundary in ('original', 'chain', 'runtime', 'guard'):
            with self.subTest(boundary=boundary):
                h = await Harness().initialize(self)
                if boundary == 'original':
                    h.original_valid = False
                    with self.assertRaises(module.HelperLifecycleError):
                        await h.obj.quiesce()
                    self.assertEqual(h.calls, [])
                else:
                    await h.obj.quiesce()
                    if boundary == 'chain': h.chain_valid = False
                    if boundary == 'runtime': h.runtime_valid = False
                    if boundary == 'guard': h.obj.guard = lambda: False
                    with self.assertRaises(module.HelperLifecycleError):
                        await h.obj.restore(h.authorization)
                    if boundary != 'runtime':
                        self.assertEqual(len(h.calls), 1)

    async def test_task_listener_retry_or_reactivation_refuse_quiescent_guard(self):
        for field in ('task', 'listener', 'retry', 'loaded'):
            with self.subTest(field=field):
                h = await Harness().initialize(self)
                await h.obj.quiesce()
                entry = h.active
                if field == 'task': entry._tasks = [SimpleNamespace(done=lambda: False)]
                if field == 'listener': entry.update_listeners = [object()]
                if field == 'retry': entry._async_cancel_retry_setup = object()
                if field == 'loaded': entry.state.value = 'loaded'
                with self.assertRaises((module.HelperLifecycleError, mixed.tx.Refused)):
                    h.obj.require_quiescent()


if __name__ == '__main__':
    unittest.main()
