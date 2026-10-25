"""Synthetic structural mixed-chain tests; no HA calls or provenance claims.

Prepared as a patch artifact only. Owner runs the integrated frozen candidate
through FIFO. Reopened chains read evidence; the original writer owns reverse.
"""
from copy import deepcopy
from dataclasses import replace
import importlib
import importlib.util
import json
from pathlib import Path
import unittest


def load_fixture(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_fixture('_mixed_registry_fixture', 'test_native_registry_receipts.py')
config_fixture = load_fixture('_mixed_config_fixture', 'test_native_config_transition.py')
receipts, prepared, tx = base.receipts, base.prepared, base.tx
helper = importlib.import_module(base.PACKAGE + '.helper_transaction')
native = importlib.import_module(base.PACKAGE + '.helper_native')
transition = importlib.import_module(base.PACKAGE + '.native_config_transition')


async def fixture():
    f = await base.fixture()
    config_doc, _, _ = config_fixture.fixture()
    observations = list(f['observations'])
    observations[2] = replace(observations[2], raw=base.raw(config_doc))
    observations = tuple(observations)
    # Rebuild synthetic receipt/baseline BEFORE the new mixed Plan. Never enrich
    # an existing saved plan and never copy baseline into PreparedHelper.
    sources = tuple(sorted(('.storage/' + o.key, base.sha(o.raw), *o.identity[:2])
                           for o in observations))
    receipt = replace(f['receipt'], source_files=sources)
    files = tuple(base.baseline.NativeInputFile(o.key, o.raw, o.identity,
                 base.baseline._json(json.loads(o.raw)['data'])) for o in observations)
    original = base.baseline.NativeInputBaseline(base.baseline._receipt_bytes(receipt), files)
    native_ref = await f['preparations'].create_native_inputs('mixed-inputs', original)
    changes, steps = f['changes'][:1], list(f['plan'].steps[:1])
    digests = {'change-0': f['digests']['change-0']}
    for index, row in enumerate(config_doc['data']['entries']):
        change = helper.prepare_helper(config_fixture.projection(row),
                                       {'sensor.old': 'sensor.new'})
        ref = 'helper-' + str(index)
        digests[ref] = await f['preparations'].create(ref, prepared.encode_change(change))
        steps.append(tx.Step(ref, 'helpers', change.identity, change.before_revision,
                             change.after_revision, ref))
        changes.append(change)
    plan = tx.Plan('mixed-original', ('registry', 'helpers'), ('registry', 'helpers'),
                   tuple(steps), 'synthetic-only', ('registry', 'helpers'), native_ref)
    sidecar_disk = base.MemoryDurable()
    store = receipts.RegistryReceiptStore(sidecar_disk, f['preparations'])
    await store.initialize()
    chain = await receipts.RegistryReceiptChain.start(store, 'provided-operation-id',
                                                     plan, receipt, digests)
    f.update(chain=chain, store=store, sidecar_disk=sidecar_disk, plan=plan,
             receipt=receipt, digests=digests, observations=observations, changes=changes)
    return f


def effect(observations, change, direction='forward', stamp='call'):
    if type(change) is not helper.PreparedHelper:
        return base.effect(observations, change, direction, stamp)
    result = list(observations)
    doc = json.loads(observations[2].raw)
    entry_id = change.before['identity']['entry_id']
    row = next(row for row in doc['data']['entries'] if row['entry_id'] == entry_id)
    before = deepcopy(row)
    desired = change.after if direction == 'forward' else change.before
    row['data'], row['options'] = deepcopy(desired['data']), deepcopy(desired['options'])
    row['modified_at'] = stamp
    result[2] = replace(observations[2], raw=base.raw(doc),
                        identity=(7, observations[2].identity[1] + 10, observations[2].identity[2]))
    mutation = native.NativeConfigMutation(entry_id, json.dumps(before), json.dumps(row), True)
    audit = transition.NativeConfigEffectAudit(entry_id,
        'forward' if direction == 'forward' else 'reverse',
        base.sha(observations[2].raw), base.sha(result[2].raw),
        transition.prepared_helper_digest(change), before['modified_at'], stamp)
    return tuple(result), mutation, audit


async def registry_first(f):
    ctx = base.context(f, 0, ['intent', 'pending', 'pending'])
    intent = await f['chain'].begin(ctx, f['observations'])
    result, mutation, audit = effect(f['observations'], f['changes'][0])
    await f['chain'].complete(intent, ctx, f['observations'], result,
                              mutation=mutation, audit=audit)
    return result


class ConfigReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_forward_reopen_reverse_each_helper_has_own_call(self):
        f = await fixture()
        phases, observations = ['pending'] * 3, f['observations']
        for direction, indices in (('forward', range(3)), ('rollback', reversed(range(3)))):
            for index in indices:
                phases[index] = 'intent' if direction == 'forward' else 'undo_intent'
                ctx = base.context(f, index, phases, direction)
                intent = await f['chain'].begin(ctx, observations)
                f['chain'].verify_intent_source(intent, ctx, observations)
                result, mutation, audit = effect(observations, f['changes'][index], direction,
                                                 direction + str(index))
                await f['chain'].complete(intent, ctx, observations, result,
                                          mutation=mutation, audit=audit)
                self.assertEqual(f['chain'].position(ctx, result),
                                 'after' if direction == 'forward' else 'before')
                phases[index] = 'verified' if direction == 'forward' else 'undone'
                current = await base.reopened(f)
                stable = base.context(f, index, phases, direction)
                self.assertEqual(current.expected(stable, result),
                                 f['chain'].expected(stable, result))
                with self.assertRaisesRegex(ValueError, 'reopen_read_only'):
                    await current.begin(ctx, result)
                observations = result
        doc = json.loads(observations[2].raw)
        self.assertEqual([r['entry_id'] for r in doc['data']['entries']], ['stable', 'other'])
        self.assertEqual(doc['data']['entries'][0]['options']['entities'][0], 'sensor.old')
        self.assertEqual(len(f['store'].rows), 13)  # root + six intent/receipt pairs

    async def test_foreign_documents_rows_subentries_and_order_refuse(self):
        for scope in ('registry', 'device', 'other', 'subentry', 'suborder', 'entries', 'opaque'):
            with self.subTest(scope=scope):
                f = await fixture()
                before = await registry_first(f)
                ctx = base.context(f, 1, ['verified', 'intent', 'pending'])
                intent = await f['chain'].begin(ctx, before)
                after, mutation, audit = effect(before, f['changes'][1])
                after = list(after)
                at = {'registry': 0, 'device': 1}.get(scope, 2)
                doc = json.loads(after[at].raw)
                if scope in ('registry', 'device'): doc['foreign'] = True
                elif scope == 'other': doc['data']['entries'][1]['options']['alien'] = True
                elif scope == 'subentry': doc['data']['entries'][0]['subentries'][0]['future'] = []
                elif scope == 'suborder': doc['data']['entries'][0]['subentries'].reverse()
                elif scope == 'entries': doc['data']['entries'].reverse()
                else: doc['data']['entries'][0]['future']['nested'][1] = 1.0
                after[at] = replace(after[at], raw=base.raw(doc))
                if at == 2:
                    row = next(r for r in doc['data']['entries'] if r['entry_id'] == 'stable')
                    mutation = replace(mutation, after_json=json.dumps(row))
                    audit = replace(audit, result_sha256=base.sha(after[2].raw))
                with self.assertRaises(ValueError):
                    await f['chain'].complete(intent, ctx, before, tuple(after),
                                              mutation=mutation, audit=audit)
                with self.assertRaisesRegex(ValueError, 'unknown_unresolved_intent'):
                    f['chain'].expected(ctx, before)

    async def test_call_stamp_and_raw_audit_exact_bindings(self):
        for scope in ('noop', 'bool', 'row', 'entry', 'hash', 'digest', 'direction', 'timestamp', 'inode'):
            with self.subTest(scope=scope):
                f = await fixture()
                before = await registry_first(f)
                ctx = base.context(f, 1, ['verified', 'intent', 'pending'])
                intent = await f['chain'].begin(ctx, before)
                after, mutation, audit = effect(before, f['changes'][1])
                if scope == 'noop': mutation = replace(mutation, changed=False)
                elif scope == 'bool': mutation = replace(mutation, changed=1)
                elif scope == 'row': mutation = replace(mutation, before_json='{}')
                elif scope == 'entry': mutation = replace(mutation, entry_id='other')
                elif scope == 'hash': audit = replace(audit, source_sha256='0' * 64)
                elif scope == 'digest': audit = replace(audit, prepared_sha256='0' * 64)
                elif scope == 'direction': audit = replace(audit, direction='reverse')
                elif scope == 'timestamp': audit = replace(audit, modified_at_after='forged')
                else: after = (*after[:2], replace(after[2], identity=(7, True, after[2].identity[2])))
                with self.assertRaises(ValueError):
                    await f['chain'].complete(intent, ctx, before, after,
                                              mutation=mutation, audit=audit)

    async def test_original_re_resolved_after_mutable_cached_helper_tamper(self):
        f = await fixture()
        original = f['changes'][1]
        before = await registry_first(f)
        cached = f['chain'].changes[1]
        cached.before['identity']['entry_id'] = 'other'
        cached.after['identity']['entry_id'] = 'other'
        ctx = base.context(f, 1, ['verified', 'intent', 'pending'])
        intent = await f['chain'].begin(ctx, before)
        after, mutation, audit = effect(before, original)
        await f['chain'].complete(intent, ctx, before, after, mutation=mutation, audit=audit)
        current = await base.reopened(f)
        stable = base.context(f, 1, ['verified', 'verified', 'pending'])
        current.expected(stable, after)
        # Mutating PreparedStore payload without updating its immutable digest
        # must fail even when expected() would otherwise use its derived cache.
        row = current.preparations.plans['helper-0']['payload']['data']
        row['before']['identity']['entry_id'] = 'other'
        with self.assertRaises(ValueError):
            current.expected(stable, after)

    async def test_helper_final_source_fence_and_unknown_no_replay(self):
        f = await fixture()
        before = await registry_first(f)
        ctx = base.context(f, 1, ['verified', 'intent', 'pending'])
        intent = await f['chain'].begin(ctx, before)
        different = (*before[:2], replace(before[2], raw=before[2].raw + b' '))
        with self.assertRaisesRegex(ValueError, 'source_observation_mismatch'):
            f['chain'].verify_intent_source(intent, ctx, different)
        # A matching eventual result without receipt never becomes accepted.
        after, _, _ = effect(before, f['changes'][1])
        current = await base.reopened(f)
        with self.assertRaisesRegex(ValueError, 'unknown_unresolved_intent'):
            current.position(ctx, after)
        with self.assertRaisesRegex(ValueError, 'unknown_unresolved_intent'):
            await f['chain'].begin(ctx, before)

    async def test_helper_receipt_lost_ack_poison_and_read_only_recovery(self):
        f = await fixture()
        before = await registry_first(f)
        ctx = base.context(f, 1, ['verified', 'intent', 'pending'])
        intent = await f['chain'].begin(ctx, before)
        after, mutation, audit = effect(before, f['changes'][1])
        f['sidecar_disk'].lose_ack = True
        with self.assertRaises(OSError):
            await f['chain'].complete(intent, ctx, before, after,
                                      mutation=mutation, audit=audit)
        self.assertTrue(f['store'].poisoned)
        self.assertIsNone(f['preparations'].plans)
        current = await base.reopened(f)
        self.assertEqual(current.position(ctx, after), 'after')
        with self.assertRaisesRegex(ValueError, 'reopen_read_only'):
            await current.begin(ctx, after)

    async def test_helper_listener_causes_still_refused_and_original_root_preserved(self):
        f = await fixture()
        step = replace(f['plan'].steps[1], caused_by=(f['plan'].steps[0].key,))
        plan = replace(f['plan'], steps=(f['plan'].steps[0], step, f['plan'].steps[2]))
        with self.assertRaisesRegex(ValueError, 'gap_native_listeners'):
            receipts.RegistryReceiptChain(f['store'], 'other-operation', plan,
                                           f['receipt'], f['digests'])
        legacy = await base.fixture()
        self.assertTrue(all(set(b) == {'ref', 'digest', 'registry_digest'}
                            for b in legacy['chain'].root['bindings']))
        self.assertEqual(legacy['chain'].root['kind'], 'registry_chain')
        self.assertEqual(legacy['chain'].root['fingerprint'], legacy['plan'].fingerprint)


if __name__ == '__main__':
    unittest.main()
