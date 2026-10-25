"""Adapters receive the actual durable phase, including after a process reopen."""
from dataclasses import FrozenInstanceError, replace
import unittest

from test_transaction import MemoryJournal, FakeAdapter, plan, tx


class ContextAdapter(FakeAdapter):
    def __init__(self, journal):
        super().__init__(journal)
        self.context = None
        self.observed = []

    def bind_phase_context(self, context):
        self.context = context
        if context is not None:
            self.observed.append(context)

    async def inspect(self, step):
        if self.context is not None:
            assert self.journal.records[self.context.record.operation_id] == self.context.record
            assert self.context.step == step
        return await super().inspect(step)

    async def apply(self, step, guard):
        self.context.require_effect(step, 'forward')
        assert self.context.record.phases[self.context.index] == 'intent'
        return await super().apply(step, guard)

    async def compensate(self, step, guard):
        self.context.require_effect(step, 'rollback')
        assert self.context.record.phases[self.context.index] == 'undo_intent'
        return await super().compensate(step, guard)


class PhaseContextTests(unittest.IsolatedAsyncioTestCase):
    def setup_context(self, disk=None):
        disk = disk or MemoryJournal()
        adapter = ContextAdapter(disk)
        owner = tx.Coordinator({'fake': adapter}, disk, lambda: None, required_scopes=('all',))
        return disk, adapter, owner

    async def test_effects_and_reverse_order_observe_exact_durable_phases(self):
        disk, adapter, owner = self.setup_context()
        selected = plan('one', 'two')
        result = await owner.apply(selected, selected.fingerprint, 'operation')
        self.assertEqual(result.status, 'applied')
        self.assertTrue(adapter.observed)
        intents = [c for c in adapter.observed if c.record.phases[c.index] == 'intent']
        self.assertEqual({c.step.key for c in intents}, {'one', 'two'})
        self.assertEqual((await owner.rollback('operation')).status, 'rolled_back')
        reverse = [c for c in adapter.observed if c.record.phases[c.index] == 'undo_intent']
        self.assertEqual([c.step.key for c in reverse[::2]], ['two', 'one'])
        self.assertEqual(adapter.effects, ['one', 'two', 'undo:two', 'undo:one'])
        with self.assertRaises(FrozenInstanceError):
            adapter.observed[-1].index = 0

    async def test_lost_ack_reopen_never_replays_and_context_is_from_journal(self):
        disk, adapter, owner = self.setup_context()
        selected = plan('one')
        adapter.fail_key, adapter.lost_ack = 'one', True
        self.assertEqual((await owner.apply(selected, selected.fingerprint, 'operation')).status, 'unknown')
        _, fresh, recovered = self.setup_context(disk)
        fresh.positions = dict(adapter.positions)
        self.assertEqual((await recovered.reconcile('operation')).status, 'ready')
        self.assertEqual((await recovered.resume('operation')).status, 'applied')
        self.assertEqual(fresh.effects, [])
        self.assertTrue(fresh.observed)
        self.assertEqual(fresh.observed[0].record.phases, ('intent',))

    async def test_phase_context_refuses_wrong_direction_step_or_missing_intent(self):
        selected = plan('one', 'two')
        record = tx.Record('operation', selected, phases=('verified', 'intent'))
        context = tx.PhaseContext(record, 1)
        context.require_effect(selected.steps[1], 'forward')
        for step, direction in ((selected.steps[0], 'forward'),
                                (selected.steps[1], 'rollback')):
            with self.assertRaises(tx.Refused):
                context.require_effect(step, direction)
        with self.assertRaises(tx.Refused):
            tx.PhaseContext(replace(record, phases=('verified', 'pending')), 1).require_effect(
                selected.steps[1], 'forward')
        with self.assertRaises(tx.Refused):
            tx.PhaseContext(record, True)


if __name__ == '__main__':
    unittest.main()
