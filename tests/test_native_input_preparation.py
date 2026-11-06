"""Immutable batch input preparation survives journal/process reopen."""
import copy
from dataclasses import asdict, replace
import hashlib
import importlib
import json
import unittest

import test_native_input_baseline as fixture_module

PACKAGE = fixture_module.PACKAGE

prepared = importlib.import_module(PACKAGE + '.prepared')
tx = importlib.import_module(PACKAGE + '.transaction')
journal = importlib.import_module(PACKAGE + '.journal')


class Disk:
    def __init__(self):
        self.payload = None
        self.fail = False

    async def load(self):
        return copy.deepcopy(self.payload), self.payload is not None

    async def save(self, payload):
        self.payload = json.loads(json.dumps(payload, allow_nan=False))
        if self.fail:
            raise OSError('synthetic lost acknowledgment')


class NativeInputPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixture_module.BaselineFileTests('test_raw_audit_order_unknowns_mode_and_codec_are_immutable')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.baseline = self.fixture.capture()
        self.disk = Disk()
        self.store = prepared.PreparedStore(self.disk)
        await self.store.initialize()
        self.step = tx.Step('one', 'registry', 'stable', 'before', 'after', 'change-ref')

    def plan(self, reference=()):
        return tx.Plan('batch', ('all',), ('all',), (self.step,), 'coverage',
                       ('registry',), native_inputs=reference)

    async def test_reopen_binds_full_baseline_and_original_receipt_to_plan(self):
        reference = await self.store.create_native_inputs('inputs', self.baseline)
        plan = self.plan(reference)
        plan.validate()
        self.assertNotEqual(plan.fingerprint, self.plan().fingerprint)
        durable = journal.DurableJournal(Disk())
        await durable.initialize()
        await durable.create(tx.Record('operation', plan, phases=('pending',)))
        fresh_journal = journal.DurableJournal(durable.store)
        await fresh_journal.initialize()
        reopened = (await fresh_journal.load('operation')).plan
        fresh = prepared.PreparedStore(self.disk)
        await fresh.initialize()
        result = fresh.resolve_native_inputs(reopened.native_inputs, self.baseline.receipt)
        self.assertEqual(result, self.baseline)
        self.assertEqual(reopened.fingerprint, plan.fingerprint)
        self.assertEqual(result.files[0].raw, self.baseline.files[0].raw)
        with self.assertRaises(ValueError):
            fresh.resolve_native_inputs(reopened.native_inputs,
                replace(self.baseline.receipt, changed_domains=('automation',)))

    async def test_reference_tamper_and_rebinding_active_plan_refuse(self):
        reference = await self.store.create_native_inputs('inputs', self.baseline)
        for index in (0, 1, 2):
            changed = list(reference)
            changed[index] = 'missing' if index == 0 else '0' * 64
            with self.assertRaises(ValueError):
                self.store.resolve_native_inputs(tuple(changed), self.baseline.receipt)
        durable = journal.DurableJournal(Disk())
        await durable.initialize()
        original = tx.Record('operation', self.plan(), phases=('pending',))
        await durable.create(original)
        with self.assertRaisesRegex(ValueError, 'journal_plan_conflict'):
            await durable.save(replace(original, plan=self.plan(reference)))

    async def test_legacy_plan_fingerprint_and_codec_remain_exact(self):
        data = asdict(self.plan())
        data.pop('native_inputs')
        expected = hashlib.sha256(json.dumps(data, sort_keys=True,
                                            separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(self.plan().fingerprint, expected)
        payload = {'schema': 1, 'operations': {'operation': asdict(
            tx.Record('operation', self.plan(), phases=('pending',)))}}
        payload['operations']['operation']['plan'].pop('native_inputs')
        restored = journal.decode(json.loads(json.dumps(payload)))['operation'].plan
        self.assertEqual(restored.native_inputs, ())
        self.assertEqual(restored.fingerprint, expected)
        with self.assertRaises(ValueError):
            self.store.resolve_native_inputs(restored.native_inputs, self.baseline.receipt)

    async def test_invalid_reference_types_and_lost_write_ack_fail_closed(self):
        for reference in ((True, '0' * 64, '0' * 64), ('../bad', '0' * 64, '0' * 64),
                          ('inputs', 'short', '0' * 64), ['inputs', '0' * 64, '0' * 64]):
            with self.subTest(reference=reference), self.assertRaises(tx.Refused):
                self.plan(reference).validate()
        self.disk.fail = True
        with self.assertRaises(OSError):
            await self.store.create_native_inputs('inputs', self.baseline)
        with self.assertRaises(ValueError):
            self.store.resolve('inputs', 'anything')
        fresh = prepared.PreparedStore(self.disk)
        await fresh.initialize()
        # Readback is possible; it does not authorize any native effect.
        row = self.disk.payload['plans']['inputs']
        ref = ('inputs', row['digest'], self.baseline.digest)
        self.assertEqual(fresh.resolve_native_inputs(ref, self.baseline.receipt), self.baseline)


if __name__ == '__main__':
    unittest.main()
