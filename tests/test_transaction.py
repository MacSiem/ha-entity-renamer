"""Pure coordinator contract tests; run via owner's FIFO unit-test receipt."""
import asyncio
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import unittest

# Avoid importing HA integration __init__ in these pure Python tests.
SOURCE = Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer/transaction.py'
SPEC = importlib.util.spec_from_file_location('renamer_transaction_test_subject', SOURCE)
tx = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tx
SPEC.loader.exec_module(tx)


class MemoryJournal:
    def __init__(self):
        self.records = {}
        self.saves = 0
        self.fail_at = None
        self.after_save = None
        self.fail_create = False

    async def load(self, operation_id):
        return self.records.get(operation_id)

    async def active(self):
        return tuple(r for r in self.records.values()
                     if r.status not in ('applied', 'rolled_back'))

    async def create(self, record):
        if self.fail_create:
            raise OSError('synthetic journal failure')
        if record.operation_id in self.records:
            raise ValueError('duplicate')
        self.records[record.operation_id] = record

    async def save(self, record):
        self.saves += 1
        if self.saves == self.fail_at:
            raise OSError('synthetic journal failure')
        self.records[record.operation_id] = record
        if self.after_save:
            self.after_save(record)


class FakeAdapter:
    def __init__(self, journal):
        self.journal = journal
        self.positions = {}
        self.effects = []
        self.fail_key = None
        self.lost_ack = False
        self.cancel = False
        self.pause = None
        self.entered = asyncio.Event()
        self.full_verified = True
        self.rollback_verified = True
        self.undo_lost_ack = False
        self.before_inspect = None

    async def inspect(self, step):
        if self.before_inspect:
            self.before_inspect(step)
        return self.positions.get(step.key, tx.Position.BEFORE)

    async def apply(self, step, guard):
        # Contract witness: durable intent exists BEFORE adapter effect.
        assert any(r.phases[r.plan.steps.index(step)] == 'intent'
                   for r in self.journal.records.values() if step in r.plan.steps)
        assert self.positions.get(step.key, tx.Position.BEFORE) == tx.Position.BEFORE
        self.entered.set()
        if self.pause:
            await self.pause.wait()
        guard()
        if step.key == self.fail_key and not self.lost_ack:
            raise OSError('write outcome uncertain')
        self.positions[step.key] = tx.Position.AFTER
        self.effects.append(step.key)
        if self.cancel:
            raise asyncio.CancelledError()
        if step.key == self.fail_key:
            raise OSError('ACK lost after effect')

    async def compensate(self, step, guard):
        guard()
        assert self.positions[step.key] == tx.Position.AFTER
        self.positions[step.key] = tx.Position.BEFORE
        self.effects.append('undo:' + step.key)
        if self.undo_lost_ack:
            raise OSError('compensation ACK lost')

    async def verify(self, plan):
        return self.full_verified

    async def verify_rollback(self, plan):
        return self.rollback_verified


def plan(*keys):
    return tx.Plan('plan-' + '-'.join(keys), ('all',), ('all',), tuple(
        tx.Step(key, 'fake', 'immutable-' + key, 'r1', 'r2', 'local-patch-' + key)
        for key in keys), 'enumeration-and-validation-proof', ('fake',))


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.journal = MemoryJournal()
        self.adapter = FakeAdapter(self.journal)
        self.authorized = True
        self.coordinator = tx.Coordinator({'fake': self.adapter}, self.journal, self.guard, required_scopes=('all',))
        self.plan = plan('one', 'two')

    def guard(self):
        if not self.authorized:
            raise tx.Refused('admin revoked or integration unloaded')

    async def apply(self, plan_=None, operation='op'):
        selected = plan_ or self.plan
        return await self.coordinator.apply(selected, selected.fingerprint, operation)

    def cascading(self):
        self.plan = replace(self.plan, steps=(self.plan.steps[0],
            replace(self.plan.steps[1], caused_by=('one',))))
        original = self.adapter.apply
        async def effect(step, guard):
            try:
                await original(step, guard)
            finally:
                if step.key == 'one' and self.adapter.positions.get('one') == tx.Position.AFTER:
                    self.adapter.positions['two'] = tx.Position.AFTER
        self.adapter.apply = effect

    async def test_declared_native_listener_result_is_not_replayed(self):
        self.cascading()
        self.assertEqual('applied', (await self.apply()).status)
        self.assertEqual(['one'], self.adapter.effects)
        self.assertEqual(('verified', 'verified'), self.journal.records['op'].phases)

    async def test_lost_ack_recovers_declared_listener_without_replay(self):
        self.cascading()
        self.adapter.fail_key, self.adapter.lost_ack = 'one', True
        self.assertEqual('unknown', (await self.apply()).status)
        self.assertEqual('ready', (await self.coordinator.reconcile('op')).status)
        self.assertEqual('applied', (await self.coordinator.resume('op')).status)
        self.assertEqual(['one'], self.adapter.effects)

    async def test_preexisting_after_is_not_a_native_listener_proof(self):
        self.cascading()
        self.adapter.positions['two'] = tx.Position.AFTER
        with self.assertRaises(tx.Refused):
            await self.apply()
        self.assertFalse(self.adapter.effects)

    async def test_undeclared_cascade_remains_unknown(self):
        self.cascading()
        self.plan = replace(self.plan, steps=(self.plan.steps[0],
            replace(self.plan.steps[1], caused_by=())))
        self.assertEqual('unknown', (await self.apply()).status)
        self.assertEqual(['one'], self.adapter.effects)

    def test_cascade_dependencies_must_precede_consumer(self):
        for causes in [('two',), ('missing',), ('one', 'one')]:
            bad = replace(self.plan, steps=(self.plan.steps[0],
                replace(self.plan.steps[1], caused_by=causes)))
            with self.assertRaises(tx.Refused):
                bad.validate()

    async def test_success_and_idempotent_retry_after_restart(self):
        result = await self.apply()
        self.assertEqual('applied', result.status)
        self.coordinator = tx.Coordinator({'fake': self.adapter}, self.journal, self.guard, required_scopes=('all',))
        self.assertEqual(result, await self.apply())
        self.assertEqual(['one', 'two'], self.adapter.effects)
        with self.assertRaises(tx.Refused):
            await self.apply(plan('other'))

    async def test_unresolved_operation_fences_new_operation(self):
        self.adapter.fail_key = 'one'
        await self.apply()
        with self.assertRaises(tx.Refused):
            await self.apply(plan('other'), 'another-operation')
        self.assertEqual([], self.adapter.effects)

    async def test_revocation_inside_adapter_before_actual_effect(self):
        self.adapter.pause = asyncio.Event()
        running = asyncio.create_task(self.apply())
        await self.adapter.entered.wait()
        self.authorized = False
        self.adapter.pause.set()
        with self.assertRaises(tx.Refused):
            await running
        self.assertEqual([], self.adapter.effects)
        self.assertEqual(('intent', 'pending'), self.journal.records['op'].phases)

    async def test_missing_coverage_or_stale_last_step_has_no_effects(self):
        with self.assertRaises(tx.Refused):
            await self.apply(replace(self.plan, covered_scopes=()))
        with self.assertRaises(tx.Refused):
            await self.apply(replace(self.plan, required_scopes=('subset',), covered_scopes=('subset',)))
        self.adapter.positions['two'] = tx.Position.UNKNOWN
        with self.assertRaises(tx.Refused):
            await self.apply()
        self.assertEqual([], self.adapter.effects)
        self.assertEqual({}, self.journal.records)

    async def test_journal_create_and_intent_failure_prevent_effects(self):
        self.journal.fail_create = True
        with self.assertRaises(tx.JournalFailure):
            await self.apply()
        self.journal.fail_create = False
        self.journal.fail_at = 1
        with self.assertRaises(tx.JournalFailure):
            await self.apply()
        self.assertEqual([], self.adapter.effects)

    async def test_lost_ack_stops_and_reconciles_without_repeating_effect(self):
        self.adapter.fail_key, self.adapter.lost_ack = 'one', True
        result = await self.apply()
        self.assertEqual('unknown', result.status)
        self.assertEqual(['one'], self.adapter.effects)
        self.assertEqual(result, await self.apply())
        self.assertEqual('ready', (await self.coordinator.reconcile('op')).status)
        result = await self.coordinator.resume('op')
        self.assertEqual('applied', result.status)
        self.assertEqual(['one', 'two'], self.adapter.effects)

    async def test_failed_write_only_resumes_after_settled_readback(self):
        self.adapter.fail_key = 'one'
        self.assertEqual('unknown', (await self.apply()).status)
        self.adapter.positions['one'] = tx.Position.UNKNOWN
        self.assertEqual('unknown', (await self.coordinator.reconcile('op')).status)
        with self.assertRaises(tx.Refused):
            await self.coordinator.resume('op')
        self.adapter.positions['one'] = tx.Position.BEFORE
        self.adapter.fail_key = None
        self.assertEqual('ready', (await self.coordinator.reconcile('op')).status)
        self.assertEqual('applied', (await self.coordinator.resume('op')).status)

    async def test_journal_failure_after_effect_preserves_intent(self):
        self.journal.fail_at = 2
        with self.assertRaises(tx.JournalFailure):
            await self.apply()
        self.assertEqual(('intent', 'pending'), (await self.coordinator.status('op')).phases)
        self.assertEqual(['one'], self.adapter.effects)
        self.journal.fail_at = None
        await self.coordinator.reconcile('op')
        await self.coordinator.resume('op')
        self.assertEqual(['one', 'two'], self.adapter.effects)

    async def test_cancellation_preserves_unresolved_intent(self):
        self.adapter.cancel = True
        with self.assertRaises(asyncio.CancelledError):
            await self.apply()
        self.assertEqual(('intent', 'pending'), (await self.coordinator.status('op')).phases)
        with self.assertRaises(tx.Refused):
            await self.coordinator.rollback('op')
        self.adapter.cancel = False
        await self.coordinator.reconcile('op')
        self.assertEqual('applied', (await self.coordinator.resume('op')).status)
        self.assertEqual(['one', 'two'], self.adapter.effects)

    async def test_serialized_execution(self):
        self.adapter.pause = asyncio.Event()
        first = asyncio.create_task(self.apply(plan('one'), 'op1'))
        await self.adapter.entered.wait()
        second = asyncio.create_task(self.apply(plan('two'), 'op2'))
        await asyncio.sleep(0)
        self.assertNotIn('op2', self.journal.records)
        self.adapter.pause.set()
        await asyncio.gather(first, second)
        self.assertEqual(['one', 'two'], self.adapter.effects)

    async def test_revocation_after_intent_await_prevents_mutation(self):
        def revoke(record):
            if 'intent' in record.phases:
                self.authorized = False
        self.journal.after_save = revoke
        with self.assertRaises(tx.Refused):
            await self.apply()
        self.assertEqual([], self.adapter.effects)
        self.assertEqual(('intent', 'pending'), self.journal.records['op'].phases)

    async def test_independent_verification_required(self):
        self.adapter.full_verified = False
        self.assertEqual('unknown', (await self.apply()).status)
        self.assertEqual(('verified', 'verified'), self.journal.records['op'].phases)

    async def test_rollback_preserves_concurrent_change(self):
        await self.apply()
        self.adapter.positions['two'] = tx.Position.UNKNOWN
        self.assertEqual('unknown', (await self.coordinator.rollback('op')).status)
        self.assertEqual(['one', 'two'], self.adapter.effects)

    async def test_lost_compensation_ack_recovery_preserves_direction(self):
        await self.apply()
        self.adapter.undo_lost_ack = True
        self.assertEqual('unknown', (await self.coordinator.rollback('op')).status)
        self.assertEqual('rollback', self.journal.records['op'].direction)
        self.adapter.undo_lost_ack = False
        self.assertEqual('rollback_ready', (await self.coordinator.reconcile('op')).status)
        with self.assertRaises(tx.Refused):
            await self.coordinator.resume('op')
        self.assertEqual('rolled_back', (await self.coordinator.rollback('op')).status)
        self.assertEqual(['one', 'two', 'undo:two', 'undo:one'], self.adapter.effects)

    async def test_verified_compensation_and_idempotent_retry(self):
        await self.apply()
        result = await self.coordinator.rollback('op')
        self.assertEqual('rolled_back', result.status)
        self.assertEqual(['one', 'two', 'undo:two', 'undo:one'], self.adapter.effects)
        self.assertEqual(result, await self.coordinator.rollback('op'))

    async def test_rollback_full_verification_required(self):
        await self.apply()
        self.adapter.rollback_verified = False
        self.assertEqual('unknown', (await self.coordinator.rollback('op')).status)

    async def test_resume_prevalidates_every_remaining_revision(self):
        self.adapter.fail_key = 'one'
        await self.apply()
        self.adapter.fail_key = None
        await self.coordinator.reconcile('op')
        self.adapter.positions['two'] = tx.Position.UNKNOWN
        self.assertEqual('unknown', (await self.coordinator.resume('op')).status)
        self.assertEqual([], self.adapter.effects)


if __name__ == '__main__':
    unittest.main()
