"""Inventory uses real Runtime, controller, journal and stores, with in-memory IO."""
import asyncio
from dataclasses import asdict, replace
import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_native_mixed_input_receipts as mixed

base = mixed.base
backend = importlib.import_module(base.PACKAGE + '.__init__')
controller = importlib.import_module(base.PACKAGE + '.recovery_controller')
journal_module = importlib.import_module(base.PACKAGE + '.journal')


class CountingDisk(base.MemoryDurable):
    def __init__(self, value=None, on_load=None):
        super().__init__()
        self.value, self.loads, self.on_load = value, 0, on_load

    async def load(self):
        self.loads += 1
        await asyncio.sleep(0)
        if self.on_load:
            self.on_load()
        return await super().load()


def journal_payload(records):
    return json.loads(json.dumps({'schema': 1, 'operations': {
        r.operation_id: asdict(r) for r in records}}))


class StatusLazyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = await mixed.mixed_fixture()
        self.runtime = backend.Runtime('test-entry', object(), object())
        self.hass = SimpleNamespace(data={backend.DATA_RUNTIME: self.runtime})
        self.allowed = True
        self.journal_disk = CountingDisk()
        self.prep_disk, self.helper_disk = CountingDisk(), CountingDisk()
        self.enterContext(patch.object(controller, 'native_journal', side_effect=lambda _: journal_module.DurableJournal(self.journal_disk)))
        self.enterContext(patch.object(controller, 'native_prepared_store', side_effect=lambda _: base.prepared.PreparedStore(self.prep_disk)))
        self.enterContext(patch.object(controller, 'native_helper_lifecycle_store', side_effect=lambda _, prep: base.receipts.RegistryReceiptStore(self.helper_disk, prep)))

    def guard(self):
        if not self.allowed:
            raise PermissionError('unauthorized')

    async def refresh(self):
        await asyncio.sleep(0)
        self.guard()

    async def status(self):
        return await self.runtime.recovery_status(self.hass, guard=self.guard, refresh=self.refresh)

    def record(self, op, plan, status='applied', phases=None, **kwargs):
        return base.tx.Record(op, plan, status, phases or ('verified',) * len(plan.steps), **kwargs)

    async def test_yaml_terminal_reloads_journal_but_not_large_unneeded_stores(self):
        record = self.record('yaml-operation', self.f['plan'])
        self.journal_disk.value = journal_payload([record])
        first = await self.status()
        self.assertEqual(first['operations'][0]['reason'], 'inspection_available')
        self.assertFalse(first['operations'][0]['eligible'])
        self.assertEqual((self.prep_disk.loads, self.helper_disk.loads), (0, 0))
        next_record = self.record('new-operation', self.f['plan'], 'refused', ('pending',) * 4,
                                  reason='prewrite_refused', restore_attempted=False)
        self.journal_disk.value = journal_payload([next_record])
        second = await self.status()
        self.assertEqual(second['operations'][0]['operation_id'], 'new-operation')
        self.assertEqual(second['operations'][0]['reason'], 'prewrite_refused')
        self.assertEqual(self.journal_disk.loads, 2)
        self.assertEqual((self.prep_disk.loads, self.helper_disk.loads), (0, 0))

    async def test_mixed_terminal_helper_still_loads_stores_and_cannot_fake_success(self):
        # A non-YAML terminal scope takes the existing conservative classification;
        # no lifecycle witness is fabricated and unsupported scope remains unproven.
        scopes = ('registry', 'helpers')
        steps = tuple(base.tx.Step(str(i), scope, str(i), 'before', 'after', str(i))
                      for i, scope in enumerate(scopes))
        plan = base.tx.Plan('helper-plan', scopes, scopes, steps, 'fixture-coverage', scopes)
        records = [self.record('yaml', self.f['plan']), self.record('helper', plan),
                   self.record('rolled', plan, 'rolled_back', ('undone', 'undone'), direction='rollback'),
                   self.record('unknown', self.f['plan'], 'unknown', ('intent', 'pending', 'pending', 'pending'))]
        self.journal_disk.value = journal_payload(records)
        result = await self.status()
        rows = {r['operation_id']: r for r in result['operations']}
        self.assertEqual(rows['yaml']['reason'], 'inspection_available')
        self.assertEqual(rows['helper']['reason'], 'recovery_unproven')
        self.assertEqual(rows['rolled']['reason'], 'recovery_unproven')
        self.assertEqual(rows['unknown']['reason'], 'unsupported_recovery_state')
        self.assertTrue(all(not r['eligible'] for r in rows.values()))
        self.assertEqual((self.prep_disk.loads, self.helper_disk.loads), (1, 1))

    async def test_revocation_after_journal_or_lazy_store_await_never_returns_success(self):
        terminal = self.record('yaml', self.f['plan'])
        self.journal_disk.value = journal_payload([terminal])
        self.journal_disk.on_load = lambda: setattr(self, 'allowed', False)
        with self.assertRaises(PermissionError):
            await self.status()
        self.allowed, self.journal_disk.on_load = True, None
        plan = (await base.fixture())['plan']
        self.journal_disk.value = journal_payload([self.record('helper', plan)])
        self.prep_disk.on_load = lambda: setattr(self, 'allowed', False)
        with self.assertRaises(PermissionError):
            await self.status()
        self.assertEqual(self.helper_disk.loads, 0)
        self.allowed, self.prep_disk.on_load = True, None
        self.helper_disk.on_load = lambda: setattr(self, 'allowed', False)
        with self.assertRaises(PermissionError):
            await self.status()
        self.assertEqual(self.helper_disk.loads, 1)
