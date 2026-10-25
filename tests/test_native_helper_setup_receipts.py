"""Synthetic receipt choreography only; owner executes via FIFO after integration.

The explicit synthetic row/physical proof below is NOT native audit evidence.
Live lifecycle binding and collector provenance require separate native tests.
"""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import patch

import test_native_registry_receipts as base

r = base.receipts
s = importlib.import_module(base.PACKAGE + '.native_helper_setup_receipts')
l = importlib.import_module(base.PACKAGE + '.native_helper_lifecycle')


async def fixture():
    # Build ORIGINAL once, including one helper row and untouched foreign row.
    f = await base.fixture()
    observations = list(f['observations'])
    doc = json.loads(observations[0].raw)
    helper = {'id': 'utility', 'entity_id': 'sensor.utility', 'unique_id': 'utility-unique',
              'platform': 'utility_meter', 'config_entry_id': 'entry',
              'modified_at': 'original-helper', 'options': {'sensor': {'precision': 2}}}
    doc['data']['entities'].append(helper)
    observations[0] = replace(observations[0], raw=base.raw(doc))
    observations = tuple(observations)
    receipt = replace(f['receipt'], source_files=tuple(sorted(
        ('.storage/' + o.key, base.sha(o.raw), *o.identity[:2]) for o in observations)))
    files = tuple(base.baseline.NativeInputFile(o.key, o.raw, o.identity,
        base.baseline._json(json.loads(o.raw)['data'])) for o in observations)
    original = base.baseline.NativeInputBaseline(base.baseline._receipt_bytes(receipt), files)
    native_ref = await f['preparations'].create_native_inputs('setup-original', original)
    plan = replace(f['plan'], native_inputs=native_ref)
    disk = base.MemoryDurable()
    store = r.RegistryReceiptStore(disk, f['preparations'])
    await store.initialize()
    chain = await r.RegistryReceiptChain.start(store, 'setup-operation', plan, receipt, f['digests'])
    f.update(chain=chain, plan=plan, receipt=receipt, observations=observations,
             store=store, sidecar_disk=disk)
    lifecycle_disk = base.MemoryDurable()
    lifecycle_store = r.RegistryReceiptStore(lifecycle_disk, f['preparations'])
    await lifecycle_store.initialize()
    root = {'kind': 'synthetic_lifecycle', 'original': 'never-native'}
    root_key = r._event_id(['synthetic-lifecycle'])
    root_ref = await lifecycle_store.create(root_key, root)
    intent_payload = {'root': root_key, 'kind': 'synthetic_setup_intent'}
    intent_key = r._event_id(['synthetic-lifecycle-intent'])
    intent_ref = await lifecycle_store.create(intent_key, intent_payload)
    f.update(lifecycle=SimpleNamespace(store=lifecycle_store, root_id=root_key),
             root_ref=list(root_ref), lifecycle_intent_ref=list(intent_ref))
    return f


def context(f, phases, direction='forward', terminal=False, index=0):
    status = ('applied' if direction == 'forward' else 'rolled_back') if terminal else (
        'applying' if direction == 'forward' else 'rolling_back')
    return base.tx.PhaseContext(base.tx.Record('setup-operation', f['plan'], status,
        tuple(phases), direction=direction), index)


async def native_steps(f, observations, direction='forward'):
    phases = ['pending' if direction == 'forward' else 'verified'] * 3
    for i in (range(3) if direction == 'forward' else reversed(range(3))):
        phases[i] = 'intent' if direction == 'forward' else 'undo_intent'
        ctx = context(f, phases, direction, index=i)
        intent = await f['chain'].begin(ctx, observations)
        after, mutation, audit = base.effect(observations, f['changes'][i], direction,
                                             stamp='native-' + direction + str(i))
        await f['chain'].complete(intent, ctx, observations, after, mutation=mutation, audit=audit)
        phases[i] = 'verified' if direction == 'forward' else 'undone'
        observations = after
    return context(f, phases, direction, terminal=True), observations


def activation(f, ctx, head):
    return {'entry_id': 'entry', 'root': f['root_ref'],
        'setup_intent': f['lifecycle_intent_ref'],
        'original': {'domain': 'utility_meter', 'state': 'loaded', 'entry_id': 'entry'},
        'baseline': {'synthetic': True},
        'authorization': as_authorization(f, ctx, head),
        'source_authorization': None}


def as_authorization(f, ctx, head):
    return {'operation_id': 'setup-operation', 'fingerprint': f['plan'].fingerprint,
            'head': head, 'status': ctx.record.status}


def physical_audit(source, result, source_rows, result_rows):
    return {'fixture': 'SYNTHETIC_ONLY', 'source': deepcopy(source), 'result': deepcopy(result),
            'source_rows': deepcopy(source_rows), 'result_rows': deepcopy(result_rows)}


class SetupReceiptsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Required audit adapter is deliberately explicit; it proves fixture
        # choreography only and is never installed into the production module.
        module = ModuleType(base.PACKAGE + '.native_utility_setup_audit')
        module.encode_setup_audit = deepcopy
        module.decode_setup_audit = deepcopy
        def row_validator(baseline, expected, source, audit):
            if (audit['fixture'] != 'SYNTHETIC_ONLY' or audit['source_rows'] != source):
                raise ValueError('synthetic row audit mismatch')
            return deepcopy(audit['result_rows'])
        module.validate_setup_audit = row_validator
        self.audit_patch = patch.dict(sys.modules, {module.__name__: module})
        self.audit_patch.start()
        self.addCleanup(self.audit_patch.stop)

    def offline_adapters(self, f, act):
        # The production path has exact type/live/root validation. These two
        # deliberately scoped replacements allow testing chain state machinery
        # independently of HA managers and the owner's lifecycle witness hook.
        obj = SimpleNamespace(helper_changes={'entry': SimpleNamespace(before={}, after={})})
        stack = __import__('contextlib').ExitStack()
        stack.enter_context(patch.object(s, '_activation', return_value=deepcopy(act)))
        stack.enter_context(patch.object(s, '_validate_activation', return_value=(obj, object())))
        return stack

    async def make_setup(self, f, ctx, before, act):
        chain = f['chain']
        intent = await chain.begin_helper_setup(ctx, before, lifecycle=f['lifecycle'], entry_id='entry')
        chain.verify_helper_setup_source(intent, ctx, before)
        after = list(before)
        doc = json.loads(after[0].raw)
        rows = doc['data']['entities']
        source_rows = [deepcopy(row) for row in rows if row.get('config_entry_id') == 'entry']
        next(row for row in rows if row['id'] == 'utility')['modified_at'] = ('actual-synthetic-return' if ctx.record.direction == 'forward'
                else 'actual-synthetic-reverse-return')
        after[0] = replace(after[0], raw=base.raw(doc),
                           identity=(after[0].identity[0], after[0].identity[1] + 50, after[0].identity[2]))
        after = tuple(after)
        states = chain._reconstruct()[0]
        result = {o.key: {'document': json.loads(o.raw), 'sha256': base.sha(o.raw),
                          'identity': list(o.identity)} for o in after}
        audit = physical_audit(chain._summary(states), chain._summary(result), source_rows,
                               [row for row in rows if row.get('config_entry_id') == 'entry'])
        return intent, after, audit

    async def test_original_native_setup_fold_reopen_and_reverse(self):
        f = await fixture()
        ctx, before = await native_steps(f, f['observations'])
        chain = f['chain']
        head = chain.expected(ctx, before)
        act = activation(f, ctx, head)
        with self.offline_adapters(f, act):
            intent, after, audit = await self.make_setup(f, ctx, before, act)
            ref = await chain.complete_helper_setup(intent, ctx, before, after, audit=audit)
            self.assertNotEqual(ref[1], head)
            self.assertTrue(chain.verify_setup_authorization(l.RestoreAuthorization(**act['authorization'])))
            self.assertEqual(chain.expected(ctx, after), ref[1])
            self.assertEqual(chain._reconstruct()[3], {i: 'forward' for i in range(3)})
            reopened = r.RegistryReceiptChain.reopen(f['store'], 'setup-operation', f['plan'],
                f['receipt'], f['digests'])
            self.assertEqual(reopened.accepted_documents(), chain.accepted_documents())
            self.assertFalse(reopened.writable)
            inputs = reopened.reconstruct_inputs()
            self.assertEqual(inputs.head, ref[1])
            rollback, reverse_after = await native_steps(f, after, 'rollback')
            self.assertEqual(chain.expected(rollback, reverse_after), chain._reconstruct()[1])
            with self.assertRaises(r.RegistryReceiptError):
                chain.verify_setup_authorization(l.RestoreAuthorization(**act['authorization']))
            utility = next(row for row in chain.accepted_documents()[s.ENTITY]['data']['entities']
                           if row['id'] == 'utility')
            self.assertEqual(utility['modified_at'], 'actual-synthetic-return')
            # A second setup cycle is anchored to the FINAL forward head,
            # including its accepted setup effect, then folds its own return.
            reverse_root = r._event_id(['synthetic-reverse-root'])
            reverse_root_ref = await f['lifecycle'].store.create(reverse_root,
                {'kind': 'synthetic_reverse_root'})
            reverse_intent_ref = await f['lifecycle'].store.create(
                r._event_id(['synthetic-reverse-intent']),
                {'kind': 'synthetic_reverse_intent', 'root': reverse_root})
            f['root_ref'] = list(reverse_root_ref)
            f['lifecycle_intent_ref'] = list(reverse_intent_ref)
            f['lifecycle'].root_id = reverse_root
            reverse_act = activation(f, rollback, chain.expected(rollback, reverse_after))
            reverse_act['source_authorization'] = dict(act['authorization'], head=ref[1])
            with self.offline_adapters(f, reverse_act):
                reverse_intent, final, reverse_audit = await self.make_setup(
                    f, rollback, reverse_after, reverse_act)
                await chain.complete_helper_setup(reverse_intent, rollback,
                    reverse_after, final, audit=reverse_audit)
                reopened_reverse = r.RegistryReceiptChain.reopen(f['store'],
                    'setup-operation', f['plan'], f['receipt'], f['digests'])
                reopened_reverse.expected(rollback, final)
                utility = next(row for row in reopened_reverse.accepted_documents()[s.ENTITY]
                               ['data']['entities'] if row['id'] == 'utility')
                self.assertEqual(utility['modified_at'], 'actual-synthetic-reverse-return')

    async def test_pending_setup_blocks_expected_inputs_authorization_and_reopen_write(self):
        f = await fixture()
        ctx, before = await native_steps(f, f['observations'])
        chain = f['chain']
        act = activation(f, ctx, chain.expected(ctx, before))
        with self.offline_adapters(f, act):
            intent = await chain.begin_helper_setup(ctx, before, lifecycle=f['lifecycle'], entry_id='entry')
            chain.verify_helper_setup_source(intent, ctx, before)
            for check in (lambda: chain.expected(ctx, before), chain.reconstruct_inputs,
                          lambda: chain.verify_setup_authorization(l.RestoreAuthorization(**act['authorization']))):
                with self.assertRaisesRegex(r.RegistryReceiptError, 'unresolved_intent'):
                    check()
            with self.assertRaisesRegex(r.RegistryReceiptError, 'intent_not_live'):
                chain.verify_helper_setup_source(replace(intent), ctx, before)
            reopened = r.RegistryReceiptChain.reopen(f['store'], 'setup-operation', f['plan'],
                f['receipt'], f['digests'])
            with self.assertRaisesRegex(r.RegistryReceiptError, 'read_only'):
                await reopened.begin_helper_setup(ctx, before, lifecycle=f['lifecycle'], entry_id='entry')

    async def test_setup_ack_loss_poisons_shared_preparations(self):
        f = await fixture()
        ctx, before = await native_steps(f, f['observations'])
        chain = f['chain']
        act = activation(f, ctx, chain.expected(ctx, before))
        with self.offline_adapters(f, act):
            # Copy witness successfully before injecting setup-intent ACK loss.
            await s._copy_witness(chain, f['lifecycle'])
            f['sidecar_disk'].lose_ack = True
            with self.assertRaises(OSError):
                await chain.begin_helper_setup(ctx, before, lifecycle=f['lifecycle'], entry_id='entry')
            self.assertTrue(f['store'].poisoned)
            with self.assertRaises(ValueError):
                f['preparations']._ready()
            self.assertIsNone(chain.active)

    async def test_audit_foreign_row_order_and_physical_source_refused(self):
        for fault in ('foreign', 'order', 'source', 'audit'):
            with self.subTest(fault=fault):
                f = await fixture()
                ctx, before = await native_steps(f, f['observations'])
                chain = f['chain']
                act = activation(f, ctx, chain.expected(ctx, before))
                with self.offline_adapters(f, act):
                    intent, after, audit = await self.make_setup(f, ctx, before, act)
                    if fault in ('foreign', 'order'):
                        doc = json.loads(after[0].raw)
                        if fault == 'foreign':
                            doc['data']['entities'][0]['foreign'] = True
                        else:
                            doc['data']['entities'].reverse()
                        after = (replace(after[0], raw=base.raw(doc)), *after[1:])
                    elif fault == 'source':
                        before = (replace(before[0], identity=(7, 99999, before[0].identity[2])), *before[1:])
                    else:
                        audit['result_rows'][0]['id'] = 'foreign'
                    with self.assertRaises((ValueError, r.RegistryReceiptError)):
                        await chain.complete_helper_setup(intent, ctx, before, after, audit=audit)
                    self.assertEqual(chain._reconstruct()[-1], intent.reference)

    async def test_copied_witness_exact_reuse_and_conflict(self):
        f = await fixture()
        chain = f['chain']
        first = await s._copy_witness(chain, f['lifecycle'])
        saves = f['sidecar_disk'].saves
        self.assertEqual(await s._copy_witness(chain, f['lifecycle']), first)
        self.assertEqual(f['sidecar_disk'].saves, saves)
        key = first[0][0]
        f['store'].rows[key]['payload'] = {'foreign': True}
        with self.assertRaisesRegex(r.RegistryReceiptError, 'witness_conflict'):
            await s._copy_witness(chain, f['lifecycle'])

    async def test_terminal_binding_rejects_partial_and_wrong_direction(self):
        f = await fixture()
        for ctx in (context(f, ['verified'] * 3),
                    context(f, ['verified', 'pending', 'verified'], terminal=True),
                    context(f, ['undone'] * 3, 'rollback', terminal=True)):
            with self.assertRaises(r.RegistryReceiptError):
                s.terminal_binding(f['chain'], ctx, {i: 'forward' for i in range(3)})

    async def test_same_anchor_multiple_helpers_but_not_another_cycle_or_native_step(self):
        f = await fixture()
        chain = f['chain']
        ctx, before = await native_steps(f, f['observations'])
        head = chain.expected(ctx, before)
        auth = l.RestoreAuthorization(**as_authorization(f, ctx, head))
        def payload(previous, root):
            return {'kind': 'helper_setup_receipt', 'source_chain': previous,
                    'activation': {'authorization': as_authorization(f, ctx, head), 'root': [root, 'd']}}
        history = [('b' * 64, payload(head, 'cycle')), ('c' * 64, payload('b' * 64, 'cycle'))]
        self.assertTrue(s.authorization_matches(chain, auth, completed={i: 'forward' for i in range(3)},
                                                head='c' * 64, history=history))
        for bad in ([history[0], ('c' * 64, payload('b' * 64, 'other'))],
                    [history[0], ('c' * 64, {'kind': 'registry_receipt'})]):
            with self.assertRaisesRegex(r.RegistryReceiptError, 'authorization_anchor'):
                s.authorization_matches(chain, auth, completed={i: 'forward' for i in range(3)},
                                        head='c' * 64, history=bad)

    async def test_receipt_ack_loss_reopens_accepted_but_read_only_without_replay(self):
        f = await fixture()
        ctx, before = await native_steps(f, f['observations'])
        chain = f['chain']
        act = activation(f, ctx, chain.expected(ctx, before))
        with self.offline_adapters(f, act):
            intent, after, audit = await self.make_setup(f, ctx, before, act)
            f['sidecar_disk'].lose_ack = True
            with self.assertRaises(OSError):
                await chain.complete_helper_setup(intent, ctx, before, after, audit=audit)
            self.assertTrue(f['store'].poisoned)
            fresh_preps = base.prepared.PreparedStore(f['prep_disk'])
            await fresh_preps.initialize()
            fresh_store = r.RegistryReceiptStore(f['sidecar_disk'], fresh_preps)
            await fresh_store.initialize()
            recovered = r.RegistryReceiptChain.reopen(fresh_store, 'setup-operation', f['plan'],
                f['receipt'], f['digests'])
            recovered.expected(ctx, after)
            self.assertFalse(recovered.writable)
            with self.assertRaisesRegex(r.RegistryReceiptError, 'intent_not_live'):
                await recovered.complete_helper_setup(intent, ctx, before, after, audit=audit)

    async def test_dangling_receipt_cannot_be_adopted(self):
        f = await fixture()
        ctx, before = await native_steps(f, f['observations'])
        chain = f['chain']
        await chain.store.create(r._event_id(['dangling-setup']), {
            'kind': 'helper_setup_receipt', 'chain': chain.root_id,
            'sequence': chain._reconstruct()[2]})
        with self.assertRaisesRegex(r.RegistryReceiptError, 'chain_graph_invalid'):
            chain.expected(ctx, before)

    async def test_prepared_and_chain_root_tampering_refuse_even_after_setup(self):
        for fault in ('preparation', 'root'):
            with self.subTest(fault=fault):
                f = await fixture()
                ctx, before = await native_steps(f, f['observations'])
                chain = f['chain']
                act = activation(f, ctx, chain.expected(ctx, before))
                with self.offline_adapters(f, act):
                    intent, after, audit = await self.make_setup(f, ctx, before, act)
                    await chain.complete_helper_setup(intent, ctx, before, after, audit=audit)
                    if fault == 'preparation':
                        ref = f['plan'].steps[0].prepared_ref
                        f['preparations'].plans[ref]['digest'] = '0' * 64
                    else:
                        f['store'].rows[chain.root_id]['payload']['fingerprint'] = 'foreign'
                    with self.assertRaises(ValueError):
                        chain.expected(ctx, after)

    async def test_live_binding_rejects_non_native_lifecycle(self):
        f = await fixture()
        with self.assertRaisesRegex(r.RegistryReceiptError, 'lifecycle_binding'):
            s._activation(f['chain'], f['lifecycle'], 'entry', live=True)

    async def test_witness_missing_ref_and_other_root_ref_refused(self):
        f = await fixture()
        act = activation(f, context(f, ['verified'] * 3, terminal=True), 'a' * 64)
        refs = await s._copy_witness(f['chain'], f['lifecycle'])
        act['witness'] = refs
        missing = deepcopy(act)
        missing['witness'] = [ref for ref in refs if ref != act['setup_intent']]
        with self.assertRaisesRegex(r.RegistryReceiptError, 'witness_missing'):
            s._witness_lifecycle(f['chain'], missing)
        foreign_key = r._event_id(['foreign-witness'])
        foreign_ref = await f['store'].create(foreign_key, {'root': 'other-operation'})
        foreign = deepcopy(act)
        foreign['witness'].append(list(foreign_ref))
        with self.assertRaisesRegex(r.RegistryReceiptError, 'witness_scope'):
            s._witness_lifecycle(f['chain'], foreign)
