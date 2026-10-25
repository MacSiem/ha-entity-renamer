"""Actual HA API, independent observations, private Stores and journal phases.

Synthetic isolated HomeAssistant only. This does not qualify active consumers,
external editor exclusion, YAML transitions or production migration readiness.
"""
from dataclasses import replace
import importlib
import json
from pathlib import Path
import unittest

import test_native_input_baseline as fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class NativeRegistryReceiptsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixtures.NativeBaselineTests(
            'test_actual_native_serialization_and_store_full_baseline')
        await self.f.asyncSetUp()
        self.addAsyncCleanup(self.f.asyncTearDown)
        self.modules = {name: importlib.import_module(fixtures.PACKAGE + '.' + name)
                       for name in ('native_registry_receipts', 'native_registry',
                                    'native_registry_transition', 'native_input_observation',
                                    'prepared', 'journal', 'transaction')}

    async def test_actual_native_multi_step_chain_reopens_and_reverses(self):
        f, m = self.f, self.modules
        rr, nr, transition, observation, prep, journal, tx = (
            m[name] for name in ('native_registry_receipts', 'native_registry',
                'native_registry_transition', 'native_input_observation', 'prepared',
                'journal', 'transaction'))
        captured = await f.capture()
        preparations = prep.native_prepared_store(f.hass)
        await preparations.initialize()
        baseline_ref = await preparations.create_native_inputs('native-chain-inputs', captured)
        io = nr.NativeRegistryIO(f.hass)
        changes, steps, digests = [], [], {}
        for kind, identity, updates in (
                ('entity', f.row.id, {'name': 'Owned native entity name'}),
                ('device', f.native_device.id, {'name_by_user': 'Owned native device name'})):
            rows = io.document(kind)['entities' if kind == 'entity' else 'devices']
            before = next(row for row in rows if row['id'] == identity)
            change = nr.PreparedRegistryChange(kind, identity, json.dumps(before),
                                               json.dumps(dict(before, **updates)))
            ref = 'native-chain-' + kind
            digests[ref] = await preparations.create(ref, prep.encode_change(change))
            changes.append(change)
            steps.append(tx.Step(ref, 'registry', identity, change.before_revision,
                                 change.after_revision, ref))
        plan = tx.Plan('native-chain-plan', ('registry',), ('registry',), tuple(steps),
                       'synthetic-native-chain-only', ('registry',), baseline_ref)
        log = journal.native_journal(f.hass)
        await log.initialize()
        operation = 'native-chain-operation'
        record = tx.Record(operation, plan, phases=('pending', 'pending'))
        await log.create(record)
        store = rr.native_registry_receipt_store(f.hass, preparations)
        await store.initialize()
        chain = await rr.RegistryReceiptChain.start(store, operation, plan, f.receipt, digests)

        async def observe():
            result = await observation.async_observe_native_inputs(f.hass, f.receipt,
                                                                   guard=f.guard)
            self.assertEqual(result.original_receipt_json, captured.receipt_json)
            return tuple(rr.RegistryRawObservation(item.key, item.raw, item.identity)
                         for item in result.files)

        for direction, indices in (('forward', range(2)), ('rollback', reversed(range(2)))):
            for index in indices:
                phases = list(record.phases)
                phases[index] = 'intent' if direction == 'forward' else 'undo_intent'
                record = replace(record, phases=tuple(phases), direction=direction,
                    status='applying' if direction == 'forward' else 'rolling_back')
                await log.save(record)
                ctx = tx.PhaseContext(await log.load(operation), index)
                before = await observe()
                intent = await chain.begin(ctx, before)
                f.guard()
                change = changes[index]
                stamp = io.mutate(change, change.after if direction == 'forward' else change.before)
                await io.settle()
                await io.flush(change.kind)
                f.guard()
                after = await observe()
                key = 'core.' + change.kind + '_registry'
                source = next(item.raw for item in before if item.key == key)
                result = next(item.raw for item in after if item.key == key)
                audit = transition.NativeRegistryEffectAudit(change.kind, change.identity,
                    'forward' if direction == 'forward' else 'reverse',
                    fixtures.digest(source), fixtures.digest(result),
                    transition.prepared_registry_digest(change),
                    json.loads(stamp.before_json).get('modified_at'),
                    json.loads(stamp.after_json).get('modified_at'))
                await chain.complete(intent, ctx, before, after, mutation=stamp, audit=audit)
                f.guard()
                phases[index] = 'verified' if direction == 'forward' else 'undone'
                record = replace(record, phases=tuple(phases))
                await log.save(record)
                fresh_preparations = prep.native_prepared_store(f.hass)
                await fresh_preparations.initialize()
                fresh_store = rr.native_registry_receipt_store(f.hass, fresh_preparations)
                await fresh_store.initialize()
                fresh_log = journal.native_journal(f.hass)
                await fresh_log.initialize()
                recovered = await fresh_log.load(operation)
                reopened = rr.RegistryReceiptChain.reopen(fresh_store, operation,
                                                         recovered.plan, f.receipt, digests)
                stable = tx.PhaseContext(recovered, index)
                self.assertEqual(reopened.expected(stable, after), chain.expected(stable, after))
                self.assertFalse(reopened.writable)
                # Original receipt never becomes fresh authorization after native changes.
                with self.assertRaises(ValueError):
                    fixtures.validation.assert_validation_current(f.receipt)
        for change in changes:
            rows = io.document(change.kind)['entities' if change.kind == 'entity' else 'devices']
            restored = next(row for row in rows if row['id'] == change.identity)
            self.assertEqual({k: v for k, v in restored.items() if k != 'modified_at'},
                             {k: v for k, v in change.before.items() if k != 'modified_at'})
        self.assertLess(Path(f.hass.config.path('.storage',
                        'ha_entity_renamer.registry_receipts')).stat().st_size, 50000)


if __name__ == '__main__':
    unittest.main()
