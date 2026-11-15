import copy
from dataclasses import replace
import unittest

from custom_components.ha_entity_renamer.journal import DurableJournal, decode
from custom_components.ha_entity_renamer.transaction import Plan, Record, Step


class FakeVerifiedStore:
    def __init__(self): self.payload = None; self.fail = False
    async def load(self): return copy.deepcopy(self.payload), self.payload is not None
    async def save(self, payload):
        import json
        self.payload = json.loads(json.dumps(payload))
        if self.fail: raise OSError('synthetic lost acknowledgement')


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = FakeVerifiedStore(); self.journal = DurableJournal(self.store)
        await self.journal.initialize()
        self.plan = Plan('p', ('scope',), ('scope',), (Step('one', 'a', 'identity', 'before', 'after', 'patch'),), 'proof', ('a',))
        self.record = Record('op1', self.plan, phases=('pending',))

    async def test_restart_preserves_unknown_and_plan_binding(self):
        await self.journal.create(self.record)
        await self.journal.save(replace(self.record, status='unknown', phases=('intent',)))
        fresh = DurableJournal(self.store); await fresh.initialize()
        self.assertEqual((await fresh.load('op1')).phases, ('intent',))
        self.assertEqual(len(await fresh.active()), 1)
        with self.assertRaises(ValueError): await fresh.create(self.record)

    async def test_failed_ack_fences_memory_and_recovers_disk(self):
        self.store.fail = True
        with self.assertRaises(OSError): await self.journal.create(self.record)
        with self.assertRaises(ValueError): await self.journal.load('op1')
        fresh = DurableJournal(self.store); await fresh.initialize()
        self.assertEqual(await fresh.load('op1'), self.record)

    async def test_only_terminal_verified_operations_leave_active(self):
        await self.journal.create(self.record)
        with self.assertRaises(ValueError): await self.journal.save(replace(self.record, status='applied'))
        await self.journal.save(replace(self.record, status='applied', phases=('verified',)))
        self.assertEqual(await self.journal.active(), ())

    async def test_plan_cannot_change_during_save(self):
        await self.journal.create(self.record)
        with self.assertRaises(ValueError): await self.journal.save(replace(self.record, plan=replace(self.plan, plan_id='other')))

    async def test_corrupt_journal_never_becomes_empty(self):
        self.store.payload = {'schema': 1, 'operations': {'op1': {}}}
        fresh = DurableJournal(self.store)
        with self.assertRaises((ValueError, KeyError)): await fresh.initialize()
        with self.assertRaises(ValueError): await fresh.active()

    async def test_restore_intent_is_durable_one_way_and_legacy_never_backfilled(self):
        legacy = self.record
        await self.journal.create(legacy)
        with self.assertRaisesRegex(ValueError, 'journal_restore_intent_conflict'):
            await self.journal.save(replace(legacy, restore_attempted=False))
        current = replace(legacy, operation_id='op2', restore_attempted=False)
        await self.journal.create(current)
        await self.journal.save(replace(current, restore_attempted=True))
        fresh = DurableJournal(self.store); await fresh.initialize()
        self.assertIs((await fresh.load('op2')).restore_attempted, True)
        with self.assertRaisesRegex(ValueError, 'journal_restore_intent_conflict'):
            await fresh.save(current)
        with self.assertRaisesRegex(ValueError, 'journal_restore_intent_conflict'):
            await fresh.save(replace(current, restore_attempted=None))

    async def test_lost_restore_intent_ack_requires_fresh_durable_read(self):
        current = replace(self.record, restore_attempted=False)
        await self.journal.create(current)
        self.store.fail = True
        with self.assertRaises(OSError):
            await self.journal.save(replace(current, restore_attempted=True))
        with self.assertRaises(ValueError): await self.journal.load('op1')
        fresh = DurableJournal(self.store); await fresh.initialize()
        self.assertIs((await fresh.load('op1')).restore_attempted, True)

    async def test_legacy_payload_decodes_as_unknown_not_unattempted(self):
        await self.journal.create(self.record)
        self.assertNotIn('restore_attempted', self.store.payload['operations']['op1'])
        fresh = DurableJournal(self.store); await fresh.initialize()
        self.assertIsNone((await fresh.load('op1')).restore_attempted)
        await fresh.create(replace(self.record, operation_id='new', restore_attempted=False))
        self.assertNotIn('restore_attempted', self.store.payload['operations']['op1'])

    async def test_prewrite_refusal_is_terminal_and_cannot_be_promoted(self):
        refused = replace(self.record, status='refused', reason='prewrite_refused', restore_attempted=False)
        await self.journal.create(refused)
        self.assertEqual(await self.journal.active(), ())
        with self.assertRaisesRegex(ValueError, 'journal_refusal_terminal'):
            await self.journal.save(replace(refused, status='applied', phases=('verified',), reason=''))

    async def test_operation_ids_are_not_paths(self):
        for key in ['../op', 'op/child', '', 'x'*81]:
            with self.assertRaises(ValueError): await self.journal.create(replace(self.record, operation_id=key))


if __name__ == '__main__': unittest.main()
