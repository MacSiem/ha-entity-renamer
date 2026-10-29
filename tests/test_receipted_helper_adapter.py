"""Synthetic coordinator + actual helper write boundary. Owner runs via FIFO.

No HA API, native storage or runtime readiness proof. Every type below comes
from the mixed fixture's one package, including both coordinator adapters.
"""
import asyncio
from copy import copy, deepcopy
from dataclasses import replace
import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_native_config_receipts as mixed
import test_registry_receipt_adapter as registry_fixture

base, tx, helper, native = mixed.base, mixed.tx, mixed.helper, mixed.native
module = importlib.import_module(base.PACKAGE + '.native_helper_transaction')
registry_module = importlib.import_module(base.PACKAGE + '.native_registry_transaction')
observation = importlib.import_module(base.PACKAGE + '.native_input_observation')


class Harness(registry_fixture.Harness):
    async def initialize(self, test):
        self.test = test
        self.f = await mixed.fixture()
        self.current = self.f['observations']
        self.log, self.bridge = registry_fixture.Journal(), registry_fixture.Bridge(self)
        self.allowed, self.final_result, self.final_calls = True, True, 0
        self.after_intent = None
        self.helper_calls, self.reload_calls = 0, 0
        self.entries = {}
        self.changed, self.serialize = True, True
        for row in json.loads(self.current[2].raw)['data']['entries']:
            value = mixed.config_fixture.projection(row)
            entry = SimpleNamespace(domain=value['domain'], **value['identity'],
                data=value['data'], options=value['options'],
                subentries={key: SimpleNamespace(**sub['identity'], data=sub['data'])
                            for key, sub in value['subentries'].items()},
                state=SimpleNamespace(value='not_loaded'), update_listeners=[],
                _on_unload=None, _tasks=set(), _background_tasks=set(),
                _async_cancel_retry_setup=None, setup_lock=asyncio.Lock(), _row=deepcopy(row))
            entry.as_dict = lambda entry=entry: deepcopy(entry._row)
            self.entries[entry.entry_id] = entry

        def update(entry, *, data, options):
            self.helper_calls += 1
            # Reaching this native boundary requires the whole-input durable intent.
            test.assertIsNotNone(self.f['chain'].active)
            entry.data, entry.options = deepcopy(data), deepcopy(options)
            entry._row.update(data=deepcopy(data), options=deepcopy(options),
                              modified_at='helper-call-' + str(self.helper_calls))
            return self.changed

        async def reload(_):
            self.reload_calls += 1
            raise AssertionError('receipted helper must remain inactive')

        self.hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_get_entry=self.entries.get, async_update_entry=update, async_reload=reload))

        async def proof(*_):
            return True

        async def flush(snapshot, guard):
            guard()
            rows = list(self.current)
            doc = json.loads(rows[2].raw)
            doc['data']['entries'] = [self.entries[row['entry_id']].as_dict()
                                      for row in doc['data']['entries']]
            rows[2] = replace(rows[2], raw=base.raw(doc),
                identity=(7, rows[2].identity[1] + 10, rows[2].identity[2]))
            self.current = tuple(rows)

        def resolver(ref):
            return mixed.prepared.decode_change(self.f['preparations'].resolve(ref, self.f['digests'][ref]))

        self.helper = helper.HelperTransactionAdapter(self.hass, resolver,
            resolve_step=lambda key: next(s for s in self.f['plan'].steps if s.key == key),
            settle=proof, validate_snapshot=proof, verify_activation=proof,
            verify_runtime=proof, verify_inventory=proof, verify_original_inventory=proof,
            persistence=SimpleNamespace(inspect_current=proof, flush=flush))
        self.adapter = self.make_helper_adapter(self.f['chain'])
        # Same lifecycle/admin guard also fences registry awaits in a mixed plan.
        self.registry_adapter = registry_module.ReceiptedRegistryTransactionAdapter(
            self.bridge, resolver, self.f['chain'], self.observe, self.log.load,
            self.adapter.guard, full_verify=self.final_verify, full_verify_rollback=self.final_verify)
        self.coordinator = self.make_coordinator(self.adapter, self.registry_adapter)
        return self

    def make_helper_adapter(self, chain):
        bound = self.helper
        if chain is not self.f['chain']:
            bound = copy(self.helper)
            bound.resolver = lambda ref: mixed.prepared.decode_change(
                chain.preparations.resolve(ref, self.f['digests'][ref]))
        return module.ReceiptedHelperTransactionAdapter(bound, chain, self.observe,
            self.log.load, self.guard, full_verify=self.final_verify,
            full_verify_rollback=self.final_verify)

    def make_coordinator(self, adapter, registry):
        return tx.Coordinator({'registry': registry, 'helpers': adapter}, self.log,
                              adapter.guard, required_scopes=('registry', 'helpers'))

    def stamp(self, entry):
        return json.dumps(entry.as_dict()) if self.serialize else None


class ReceiptedHelperAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_forward_reverse_no_replay_and_actual_helper_boundary(self):
        h = await Harness().initialize(self)
        with patch.object(native, '_config_row_json', h.stamp):
            applied = await h.apply()
            self.assertEqual(applied.status, 'applied')
            self.assertEqual((h.bridge.effects, h.helper_calls, h.reload_calls), (1, 2, 0))
            self.assertEqual(len(h.f['store'].rows), 7)
            restored = await h.coordinator.rollback('provided-operation-id')
            self.assertEqual(restored.status, 'rolled_back')
            self.assertEqual((h.bridge.effects, h.helper_calls, h.reload_calls), (2, 4, 0))
            self.assertEqual(len(h.f['store'].rows), 13)
            self.assertEqual(await h.apply(), restored)
            self.assertEqual(h.helper_calls, 4)

    async def test_loaded_listeners_tasks_retry_and_unknown_fields_refuse_before_effects(self):
        for field in ('loaded', 'listeners', 'unload', 'tasks', 'background', 'retry', 'lock', 'missing'):
            with self.subTest(field=field):
                h = await Harness().initialize(self)
                entry = h.entries['stable']
                done = SimpleNamespace(done=lambda: False)
                if field == 'loaded': entry.state.value = 'loaded'
                elif field == 'listeners': entry.update_listeners.append(lambda: None)
                elif field == 'unload': entry._on_unload = [lambda: None]
                elif field == 'tasks': entry._tasks = [done]
                elif field == 'background': entry._background_tasks = [done]
                elif field == 'retry': entry._async_cancel_retry_setup = lambda: None
                elif field == 'lock': await entry.setup_lock.acquire()
                else: del entry.update_listeners
                with patch.object(native, '_config_row_json', h.stamp):
                    try:
                        result = await h.apply()
                        self.assertNotEqual(result.status, 'applied')
                    except tx.Refused:
                        pass
                self.assertEqual((h.bridge.effects, h.helper_calls, h.reload_calls), (0, 0, 0))
                self.assertEqual(len(h.f['store'].rows), 1)

    async def test_helper_guard_loss_and_foreign_source_after_intent_await(self):
        for action in ('guard', 'foreign', 'lifecycle', 'manager'):
            with self.subTest(action=action):
                h = await Harness().initialize(self)
                async def observe():
                    active = h.f['chain'].active
                    if active is not None and h.adapter.context.index == 1:
                        if action == 'guard': h.allowed = False
                        elif action == 'lifecycle': h.entries['stable'].state.value = 'loaded'
                        elif action == 'manager': h.entries['stable'].title = 'foreign manager edit'
                        else:
                            rows = list(h.current)
                            doc = json.loads(rows[0].raw)
                            doc['data']['entities'][2]['name'] = 'foreign during helper intent'
                            rows[0] = replace(rows[0], raw=base.raw(doc))
                            h.current = tuple(rows)
                    return await Harness.observe(h)
                h.adapter.observer = observe
                with patch.object(native, '_config_row_json', h.stamp):
                    try:
                        result = await h.apply()
                        self.assertEqual(result.status, 'unknown')
                    except (ValueError, tx.Refused):
                        pass
                self.assertEqual((h.bridge.effects, h.helper_calls), (1, 0))
                self.assertEqual(len(h.f['store'].rows), 4)

    async def test_false_or_missing_stamp_leaves_intent_without_receipt(self):
        for unavailable in ('false', 'missing'):
            with self.subTest(unavailable=unavailable):
                h = await Harness().initialize(self)
                h.changed, h.serialize = unavailable != 'false', unavailable != 'missing'
                with patch.object(native, '_config_row_json', h.stamp):
                    result = await h.apply()
                    self.assertEqual(result.status, 'unknown')
                    self.assertEqual(h.helper_calls, 1)
                    self.assertEqual(len(h.f['store'].rows), 4)
                    self.assertEqual(await h.apply(), result)
                    self.assertEqual(h.helper_calls, 1)

    async def test_no_call_and_listener_causes_cannot_create_receipts(self):
        h = await Harness().initialize(self)
        step = h.f['plan'].steps[1]
        with self.assertRaisesRegex(tx.Refused, 'listeners_unsupported'):
            h.adapter._change(replace(step, caused_by=(h.f['plan'].steps[0].key,)))
        async def no_call(*args, **kwargs):
            return {'_native_config_mutation': None}
        h.helper._write = no_call
        with patch.object(native, '_config_row_json', h.stamp):
            result = await h.apply()
            self.assertEqual(result.status, 'unknown')
            self.assertEqual(h.helper_calls, 0)
            self.assertEqual(len(h.f['store'].rows), 3)  # registry pair only

    async def test_helper_receipt_lost_ack_recovers_readonly_without_replay(self):
        h = await Harness().initialize(self)
        original_complete = h.f['chain'].complete
        async def complete(intent, context, *args, **kwargs):
            if context.index == 1:
                h.f['sidecar_disk'].lose_ack = True
            return await original_complete(intent, context, *args, **kwargs)
        h.f['chain'].complete = complete
        with patch.object(native, '_config_row_json', h.stamp):
            # Poisoned PreparedStore also invalidates the shared lifecycle guard.
            # Original durable intent remains; recover with freshly reopened IO.
            with self.assertRaisesRegex(ValueError, 'preparations_unavailable'):
                await h.apply()
            self.assertEqual(h.helper_calls, 1)
            self.assertEqual((await h.log.load('provided-operation-id')).phases,
                             ('verified', 'intent', 'pending'))
            reopened = await base.reopened(h.f)
            adapter = h.make_helper_adapter(reopened)
            registry = registry_module.ReceiptedRegistryTransactionAdapter(h.bridge,
                adapter.helper.resolver, reopened, h.observe, h.log.load, adapter.guard,
                full_verify=h.final_verify, full_verify_rollback=h.final_verify)
            coordinator = h.make_coordinator(adapter, registry)
            recovered = await coordinator.reconcile('provided-operation-id')
            self.assertEqual(recovered.phases, ('verified', 'verified', 'pending'))
            stopped = await coordinator.resume('provided-operation-id')
            self.assertEqual(stopped.status, 'unknown')
            self.assertEqual(h.helper_calls, 1)

    async def test_helper_journal_ack_loss_reconciles_and_full_verify_remains_required(self):
        h = await Harness().initialize(self)
        original_save = h.log.save
        async def save(record):
            if record.phases == ('verified', 'verified', 'pending'):
                raise OSError('helper verified ACK loss before save')
            await original_save(record)
        h.log.save = save
        with patch.object(native, '_config_row_json', h.stamp):
            with self.assertRaises(tx.JournalFailure):
                await h.apply()
            h.log.save = original_save
            recovered = await h.coordinator.reconcile('provided-operation-id')
            self.assertEqual(recovered.phases, ('verified', 'verified', 'pending'))
            self.assertEqual(h.helper_calls, 1)
        h = await Harness().initialize(self)
        h.final_result = False
        with patch.object(native, '_config_row_json', h.stamp):
            self.assertEqual((await h.apply()).reason, 'final_verification_failed')
            self.assertEqual(h.helper_calls, 2)


if __name__ == '__main__':
    unittest.main()
