"""Synthetic IO seams for owner FIFO execution; no native QA claim.

Uses the real immutable mixed chain and the real YAML adapter write path. Only
filesystem IO/source checker, native observer and reload service are fakes.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import importlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location('_receipted_yaml_mixed',
    Path(__file__).with_name('test_native_mixed_input_receipts.py'))
mixed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mixed)
base, tx, yaml = mixed.base, mixed.tx, mixed.yaml
adapter_module = importlib.import_module(base.PACKAGE + '.native_yaml_transaction')
observation = importlib.import_module(base.PACKAGE + '.native_input_observation')


class Locks:
    held = False
    @asynccontextmanager
    async def hold(self, guard):
        assert not self.held
        guard()
        self.held = True
        try:
            yield
        finally:
            self.held = False


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = await mixed.mixed_fixture()
        self.phases, self.native = await mixed.apply_native(self.f)
        self.phases[3] = 'intent'
        self.context = base.context(self.f, 3, self.phases)
        self.record = self.context.record
        self.locks = Locks()
        self.runtime = tx.Position.BEFORE
        self.calls = []
        self.reloads = 0
        self.changed_phase = False
        self.fail_complete_ack = False
        self.source_checks = 0
        self.images = {row.path: row.before for row in self.f['yaml'].files}
        self.identities = {row.path: [row.device, row.inode, 0o100640]
                           for row in self.f['yaml'].files}
        test = self
        class Reader:
            def __init__(self, *args):
                self.root = os.open('/', os.O_RDONLY)
            def read(self, path):
                dev, ino, mode = test.identities[path]
                return test.images[path], SimpleNamespace(st_dev=dev, st_ino=ino,
                    st_mode=mode, st_uid=501, st_gid=20)
        self.reader_patch = patch.object(yaml, '_Reader', Reader)
        self.reader_patch.start()
        self.source_patch = patch.object(mixed.accepted.AcceptedInputState,
                                        'assert_current', self.assert_sources)
        self.source_patch.start()
        def mutate(root, file_patch, snapshot, operation, guard):
            assert self.locks.held
            guard()
            state = self.f['chain'].live_yaml_inputs(self.intent, self.context, self.calls)
            # Select the next call because calls_for returns the entire group.
            reverse = self.context.record.direction == 'rollback'
            result = mixed.calls_for(self.f, state, reverse)[len(self.calls)]
            self.images[file_patch.path] = file_patch.proposed_bytes
            self.identities[file_patch.path] = list(result['after_identity'])
            self.calls.append(result)
            if self.changed_phase:
                self.record = replace(self.record, reason='foreign durable edit')
            return result
        self.mutator_patch = patch.object(yaml, 'apply_file_patch', mutate)
        self.mutator_patch.start()
        async def reload(*args, **kwargs):
            assert self.locks.held
            self.reloads += 1
            self.runtime = (tx.Position.BEFORE if self.record.direction == 'rollback'
                            else tx.Position.AFTER)
            if self.fail_complete_ack:
                self.f['sidecar_disk'].lose_ack = True
            await asyncio.sleep(0)
        self.hass = SimpleNamespace(config=SimpleNamespace(config_dir='/synthetic/never-read'),
            services=SimpleNamespace(has_service=lambda *args: True, async_call=reload),
            async_add_executor_job=self.executor)
        self.observer = SimpleNamespace(position=self.position, verify_inventory=self.inventory)
        self.adapter = self.make_adapter(self.f['chain'])
        self.adapter.bind_phase_context(self.context)
        original_begin = self.f['chain'].begin_yaml
        async def begin(*args, **kwargs):
            self.intent = await original_begin(*args, **kwargs)
            return self.intent
        self.begin_patch = patch.object(self.f['chain'], 'begin_yaml', begin)
        self.begin_patch.start()

    async def asyncTearDown(self):
        self.begin_patch.stop()
        self.mutator_patch.stop()
        self.source_patch.stop()
        self.reader_patch.stop()

    def assert_sources(self, original):
        self.source_checks += 1

    async def executor(self, function, *args):
        result = function(*args)
        await asyncio.sleep(0)
        return result

    async def position(self, change):
        await asyncio.sleep(0)
        return self.runtime

    async def inventory(self, plan, *, rollback):
        return True

    async def load(self, operation):
        await asyncio.sleep(0)
        return self.record

    async def native_observer(self, state, chain):
        assert self.locks.held or not chain.active
        state._bound(self.f['receipt'])
        await asyncio.sleep(0)
        files = tuple(base.baseline.NativeInputFile(row.key, row.raw, row.identity,
            base.baseline._json(json.loads(row.raw)['data'])) for row in self.native)
        return observation.NativeInputObservation(chain.baseline.receipt_json,
                                                  self.f['receipt'].version, files)

    async def full_verify(self, plan):
        self.full_verified = True
        return True

    def make_adapter(self, chain):
        return adapter_module.ReceiptedYAMLTransactionAdapter(self.hass,
            lambda ref: self.f['yaml'], self.locks, self.observer, chain,
            lambda state: self.native_observer(state, chain), self.load, lambda: None, context=None,
            full_verify=self.full_verify, full_verify_rollback=self.full_verify,
            runtime_version=self.f['receipt'].version)

    async def test_forward_receipt_before_journal_verified_and_readonly_reopen(self):
        result = await self.adapter.apply(self.context.step, lambda: None)
        self.assertEqual(result, self.calls)
        self.assertEqual(len(result), 2)
        self.assertEqual(self.reloads, 1)
        self.assertEqual(self.record.phases[3], 'intent')
        self.assertGreater(self.source_checks, 10)
        reopened = await base.reopened(self.f)
        adapter = self.make_adapter(reopened)
        adapter.bind_phase_context(self.context)
        self.assertEqual(await adapter.inspect(self.context.step), tx.Position.AFTER)
        self.assertEqual(len(self.calls), 2)  # inspect never replays a write.
        with self.assertRaises(Exception):
            await adapter.apply(self.context.step, lambda: None)
        self.assertEqual(len(self.calls), 2)

    async def test_phase_drift_after_first_actual_return_stops_group(self):
        self.changed_phase = True
        with self.assertRaisesRegex(tx.Refused, 'phase changed'):
            await self.adapter.apply(self.context.step, lambda: None)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.reloads, 0)
        reopened = await base.reopened(self.f)
        adapter = self.make_adapter(reopened)
        adapter.bind_phase_context(self.context)
        self.assertEqual(await adapter.inspect(self.context.step), tx.Position.UNKNOWN)

    async def test_lost_begin_ack_has_no_mutator_call(self):
        self.f['sidecar_disk'].lose_ack = True
        with self.assertRaises(Exception):
            await self.adapter.apply(self.context.step, lambda: None)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.reloads, 0)

    async def test_lost_complete_ack_only_readonly_reopen_can_recognize_receipt(self):
        self.fail_complete_ack = True
        with self.assertRaises(Exception):
            await self.adapter.apply(self.context.step, lambda: None)
        self.assertEqual(len(self.calls), 2)
        reopened = await base.reopened(self.f)
        adapter = self.make_adapter(reopened)
        adapter.bind_phase_context(self.context)
        self.assertEqual(await adapter.inspect(self.context.step), tx.Position.AFTER)
        self.assertEqual(len(self.calls), 2)

    async def test_final_full_scope_callback_and_native_runtime_are_both_required(self):
        await self.adapter.apply(self.context.step, lambda: None)
        self.record = replace(self.record, phases=('verified',) * 4)
        self.assertTrue(await self.adapter.verify(self.f['plan']))
        self.assertTrue(self.full_verified)
        self.runtime = tx.Position.BEFORE
        self.assertFalse(await self.adapter.verify(self.f['plan']))

    async def test_yaml_first_reverse_uses_exact_forward_receipt_inputs(self):
        await self.adapter.apply(self.context.step, lambda: None)
        self.phases = ['verified'] * 3 + ['undo_intent']
        self.context = base.context(self.f, 3, self.phases, 'rollback')
        self.record = self.context.record
        self.adapter.bind_phase_context(self.context)
        self.calls.clear()
        result = await self.adapter.compensate(self.context.step, lambda: None)
        self.assertEqual([row['path'] for row in result],
                         [row.path for row in reversed(self.f['yaml'].files)])
        self.assertEqual(self.runtime, tx.Position.BEFORE)
        self.assertEqual(self.reloads, 2)
        self.assertEqual(self.f['chain'].position(self.context, self.native), 'before')
        for row in self.f['yaml'].files:
            self.assertEqual(self.images[row.path], row.before)
