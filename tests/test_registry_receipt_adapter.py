"""Coordinator integration with independently durable intents and source fences."""
from copy import deepcopy
import importlib
import json
from types import SimpleNamespace
import unittest

import test_native_registry_receipts as fixture

adapter_module = importlib.import_module(fixture.PACKAGE + '.native_registry_transaction')
observation_module = importlib.import_module(fixture.PACKAGE + '.native_input_observation')
tx = fixture.tx


class Journal:
    def __init__(self):
        self.records = {}
        self.fail_verified = False

    async def load(self, operation):
        return self.records.get(operation)

    async def active(self):
        return tuple(r for r in self.records.values() if r.status not in ('applied', 'rolled_back'))

    async def create(self, record):
        if record.operation_id in self.records:
            raise ValueError('duplicate')
        self.records[record.operation_id] = record

    async def save(self, record):
        if self.fail_verified and 'verified' in record.phases:
            self.fail_verified = False
            raise OSError('synthetic journal ACK loss before save')
        self.records[record.operation_id] = record


class Bridge:
    def __init__(self, harness):
        self.h = harness
        self.effects = 0

    async def inspect(self, change):
        key = 'core.' + change.kind + '_registry'
        raw = next(o.raw for o in self.h.current if o.key == key)
        rows = json.loads(raw)['data']['entities' if change.kind == 'entity' else 'devices']
        row = next(row for row in rows if row['id'] == change.identity)
        content = lambda value: {k: v for k, v in value.items() if k != 'modified_at'}
        position = ('before' if content(row) == content(change.before) else
                    'after' if content(row) == content(change.after) else 'conflict')
        return SimpleNamespace(position=position)

    async def _write(self, change, guard, direction, before_mutation):
        guard()
        self.h.test.assertIsNotNone(before_mutation)
        await before_mutation()
        guard()
        record = await self.h.log.load('provided-operation-id')
        self.h.test.assertIn('intent' if direction == 'forward' else 'undo_intent', record.phases)
        self.h.test.assertIsNotNone(self.h.f['chain'].active)
        self.effects += 1
        self.h.current, mutation, _ = fixture.effect(self.h.current, change, direction,
                                                    direction + str(self.effects))
        return mutation

    async def apply(self, change, guard, *, before_mutation=None):
        return await self._write(change, guard, 'forward', before_mutation)

    async def compensate(self, change, guard, *, before_mutation=None):
        return await self._write(change, guard, 'rollback', before_mutation)


class Harness:
    async def initialize(self, test):
        self.test = test
        self.f = await fixture.fixture()
        self.current = self.f['observations']
        self.log, self.bridge = Journal(), Bridge(self)
        self.allowed, self.final_result, self.final_calls = True, True, 0
        self.after_intent = None
        self.adapter = self.make_adapter(self.f['chain'])
        self.coordinator = tx.Coordinator({'registry': self.adapter}, self.log,
                                         self.guard, required_scopes=('registry',))
        return self

    def guard(self):
        if not self.allowed:
            raise ValueError('authorization revoked')

    async def observe(self):
        if self.after_intent and self.f['chain'].active is not None:
            action, self.after_intent = self.after_intent, None
            action()
        return observation_module.NativeInputObservation(
            fixture.baseline._receipt_bytes(self.f['receipt']), self.f['receipt'].version,
            tuple(fixture.baseline.NativeInputFile(o.key, o.raw, o.identity,
                  fixture.baseline._json(json.loads(o.raw)['data'])) for o in self.current))

    async def final_verify(self, plan):
        self.final_calls += 1
        return self.final_result

    def make_adapter(self, chain):
        return adapter_module.ReceiptedRegistryTransactionAdapter(self.bridge,
            lambda ref: fixture.prepared.decode_change(
                self.f['preparations'].resolve(ref, self.f['digests'][ref])),
            chain, self.observe, self.log.load, self.guard,
            full_verify=self.final_verify, full_verify_rollback=self.final_verify)

    async def apply(self):
        return await self.coordinator.apply(self.f['plan'], self.f['plan'].fingerprint,
                                            'provided-operation-id')


class RegistryReceiptAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_coordinator_forward_reverse_and_no_replay_have_full_chain(self):
        h = await Harness().initialize(self)
        result = await h.apply()
        self.assertEqual(result.status, 'applied')
        self.assertEqual(h.bridge.effects, 3)
        self.assertEqual(len(h.f['store'].rows), 7)
        self.assertEqual(h.final_calls, 1)
        restored = await h.coordinator.rollback('provided-operation-id')
        self.assertEqual(restored.status, 'rolled_back')
        self.assertEqual(h.bridge.effects, 6)
        self.assertEqual(len(h.f['store'].rows), 13)
        self.assertEqual(h.final_calls, 2)
        self.assertEqual(await h.apply(), restored)
        self.assertEqual(h.bridge.effects, 6)

    async def test_foreign_source_between_intent_and_mutation_refuses_before_effect(self):
        h = await Harness().initialize(self)
        def foreign():
            rows = list(h.current)
            doc = json.loads(rows[0].raw)
            doc['data']['entities'][2]['name'] = 'foreign edit during intent await'
            rows[0] = fixture.replace(rows[0], raw=fixture.raw(doc))
            h.current = tuple(rows)
        h.after_intent = foreign
        result = await h.apply()
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(h.bridge.effects, 0)
        self.assertEqual(len(h.f['store'].rows), 2)
        self.assertEqual(await h.apply(), result)
        self.assertEqual(h.bridge.effects, 0)

    async def test_revoked_guard_after_sidecar_intent_blocks_effect(self):
        h = await Harness().initialize(self)
        h.after_intent = lambda: setattr(h, 'allowed', False)
        with self.assertRaises(ValueError):
            await h.apply()
        self.assertEqual(h.bridge.effects, 0)
        self.assertEqual(len(h.f['store'].rows), 2)

    async def test_full_scope_verifier_remains_required(self):
        h = await Harness().initialize(self)
        h.final_result = False
        result = await h.apply()
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(result.reason, 'final_verification_failed')
        self.assertEqual(h.bridge.effects, 3)

    async def test_receipt_survives_journal_ack_loss_readonly_reconcile_never_replays(self):
        h = await Harness().initialize(self)
        h.log.fail_verified = True
        with self.assertRaises(tx.JournalFailure):
            await h.apply()
        self.assertEqual(h.bridge.effects, 1)
        self.assertEqual((await h.log.load('provided-operation-id')).phases[0], 'intent')
        readonly = await fixture.reopened(h.f)
        adapter = h.make_adapter(readonly)
        coordinator = tx.Coordinator({'registry': adapter}, h.log, h.guard,
                                     required_scopes=('registry',))
        recovered = await coordinator.reconcile('provided-operation-id')
        self.assertEqual(recovered.status, 'ready')
        self.assertEqual(recovered.phases, ('verified', 'pending', 'pending'))
        self.assertEqual(h.bridge.effects, 1)
        stopped = await coordinator.resume('provided-operation-id')
        self.assertEqual(stopped.status, 'unknown')
        self.assertEqual(h.bridge.effects, 1)


if __name__ == '__main__':
    unittest.main()
