"""Synthetic durable chain cases. Owner runs via FIFO on integrated candidate.

No HA/native/private inputs are read. These tests cannot establish caller read
provenance, shared locks, native observer coverage or migration readiness.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType
import unittest

COMPONENT = Path(os.environ.get('RENAMER_COMPONENT_DIR',
                 str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')))
PACKAGE = '_registry_receipts_regression'
package = ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
receipts = importlib.import_module(PACKAGE + '.native_registry_receipts')
native = importlib.import_module(PACKAGE + '.native_registry')
transition = importlib.import_module(PACKAGE + '.native_registry_transition')
prepared = importlib.import_module(PACKAGE + '.prepared')
baseline = importlib.import_module(PACKAGE + '.native_input_baseline')
validation = importlib.import_module(PACKAGE + '.yaml_validation')
tx = importlib.import_module(PACKAGE + '.transaction')


def raw(document):
    return json.dumps(document, ensure_ascii=True, indent=1).encode() + b'\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class MemoryDurable:
    """Independent durable image; fail-after-save models lost acknowledgement."""
    def __init__(self):
        self.value = None
        self.lose_ack = False
        self.saves = 0

    async def load(self):
        return deepcopy(self.value), self.value is not None

    async def save(self, value):
        self.value = deepcopy(value)
        self.saves += 1
        if self.lose_ack:
            self.lose_ack = False
            raise OSError('synthetic lost save acknowledgement')


async def fixture(padding=0):
    data = [
        {'entities': [{'id': 'one', 'entity_id': 'sensor.one', 'name': 'One',
                       'modified_at': 'original', 'opaque': {'keep': [False, 1.0]}},
                      {'id': 'two', 'entity_id': 'sensor.two', 'name': 'Two',
                       'modified_at': 'original'},
                      {'id': 'foreign', 'entity_id': 'sensor.foreign', 'name': None,
                       'modified_at': 'never-change'}],
         'deleted_entities': [{'id': 'deleted', 'foreign': True}], 'settings': {}},
        {'devices': [{'id': 'device', 'name_by_user': 'Device', 'modified_at': 'original'},
                     {'id': 'other-device', 'name_by_user': None}],
         'deleted_devices': [], 'child_devices': []},
        {'entries': [{'entry_id': 'entry', 'options': {'foreign': True},
                      'subentries': [{'subentry_id': 'nested', 'data': {'keep': 1}}]}]},
    ]
    data[0]['entities'][2]['padding'] = 'x' * padding
    data[1]['devices'][1]['padding'] = 'x' * padding
    data[2]['entries'][0]['padding'] = 'x' * padding
    schemas = [(1, 23), (3, 4), (1, 5)]
    observations = tuple(receipts.RegistryRawObservation(key, raw({
        'key': key, 'version': schemas[i][0], 'minor_version': schemas[i][1],
        'data': data[i]}),
        (7, i + 100, stat.S_IFREG | 0o600)) for i, key in enumerate(receipts.KEYS))
    sources = tuple(sorted(('.storage/' + o.key, sha(o.raw), *o.identity[:2]) for o in observations))
    directories = (
        ('.', (('.storage', stat.S_IFDIR | 0o700, 7, 50),), None),
        ('.storage', tuple(sorted((o.key, o.identity[2], *o.identity[:2]) for o in observations)),
         tuple(sorted(receipts.KEYS))),
    )
    receipt = validation.ValidationReceipt('/synthetic/never-read', sources, directories,
                                           (), ('script',), (), '2026.10.2')
    files = tuple(baseline.NativeInputFile(o.key, o.raw, o.identity,
                  baseline._json(json.loads(o.raw)['data'])) for o in observations)
    original = baseline.NativeInputBaseline(baseline._receipt_bytes(receipt), files)
    prep_disk, sidecar_disk = MemoryDurable(), MemoryDurable()
    preparations = prepared.PreparedStore(prep_disk)
    await preparations.initialize()
    native_ref = await preparations.create_native_inputs('original-inputs', original)
    changes = []
    for kind, index, updates in (('entity', 0, {'entity_id': 'sensor.renamed'}),
                                 ('entity', 1, {'name': 'New name'}),
                                 ('device', 0, {'name_by_user': 'New device'})):
        source = deepcopy(data[0 if kind == 'entity' else 1]['entities' if kind == 'entity' else 'devices'][index])
        changes.append(native.PreparedRegistryChange(kind, source['id'],
                       json.dumps(source), json.dumps(dict(source, **updates))))
    digests, steps = {}, []
    for i, change in enumerate(changes):
        ref = 'change-' + str(i)
        digests[ref] = await preparations.create(ref, prepared.encode_change(change))
        steps.append(tx.Step('step-' + str(i), 'registry', change.identity,
                            change.before_revision, change.after_revision, ref))
    plan = tx.Plan('original-plan', ('registry',), ('registry',), tuple(steps),
                   'synthetic-coverage-only', ('registry',), native_ref)
    store = receipts.RegistryReceiptStore(sidecar_disk, preparations)
    await store.initialize()
    chain = await receipts.RegistryReceiptChain.start(store, 'provided-operation-id',
                                                     plan, receipt, digests)
    return {'chain': chain, 'store': store, 'preparations': preparations,
            'prep_disk': prep_disk, 'sidecar_disk': sidecar_disk, 'plan': plan,
            'receipt': receipt, 'digests': digests, 'observations': observations,
            'changes': changes}


def context(f, index, phases, direction='forward'):
    record = tx.Record('provided-operation-id', f['plan'],
                       'applying' if direction == 'forward' else 'rolling_back',
                       tuple(phases), direction=direction)
    return tx.PhaseContext(record, index)


def effect(observations, change, direction='forward', stamp='native-returned-stamp'):
    result = list(observations)
    key = 'core.' + change.kind + '_registry'
    i = receipts.KEYS.index(key)
    document = json.loads(observations[i].raw)
    plural = 'entities' if change.kind == 'entity' else 'devices'
    rows = document['data'][plural]
    index = next(n for n, row in enumerate(rows) if row['id'] == change.identity)
    before = deepcopy(rows[index])
    origin = change.before if direction == 'forward' else change.after
    destination = change.after if direction == 'forward' else change.before
    for field in origin:
        if origin[field] != destination[field]:
            rows[index][field] = destination[field]
    rows[index]['modified_at'] = stamp
    after = deepcopy(rows[index])
    if change.kind == 'entity' and before['entity_id'] != after['entity_id']:
        rows.append(rows.pop(index))
    previous = observations[i]
    result[i] = receipts.RegistryRawObservation(key, raw(document),
                 (previous.identity[0], previous.identity[1] + 10, previous.identity[2]))
    mutation = native.NativeRegistryMutation(change.kind, change.identity,
                                             json.dumps(before), json.dumps(after))
    audit = transition.NativeRegistryEffectAudit(change.kind, change.identity,
        'forward' if direction == 'forward' else 'reverse', sha(previous.raw), sha(result[i].raw),
        transition.prepared_registry_digest(change), before['modified_at'], after['modified_at'])
    return tuple(result), mutation, audit


async def reopened(f):
    preparations = prepared.PreparedStore(f['prep_disk'])
    await preparations.initialize()
    store = receipts.RegistryReceiptStore(f['sidecar_disk'], preparations)
    await store.initialize()
    return receipts.RegistryReceiptChain.reopen(store, 'provided-operation-id',
              f['plan'], f['receipt'], f['digests'])


class RegistryReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_registry_forward_reopen_reverse(self):
        f = await fixture()
        phases, observations = ['pending'] * 3, f['observations']
        for direction, indices in (('forward', range(3)), ('rollback', reversed(range(3)))):
            for i in indices:
                phases[i] = 'intent' if direction == 'forward' else 'undo_intent'
                ctx = context(f, i, phases, direction)
                intent = await f['chain'].begin(ctx, observations)
                result, mutation, audit = effect(observations, f['changes'][i], direction,
                                                  direction + str(i))
                await f['chain'].complete(intent, ctx, observations, result,
                                          mutation=mutation, audit=audit)
                phases[i] = 'verified' if direction == 'forward' else 'undone'
                stable = context(f, i, phases, direction)
                current = await reopened(f)
                self.assertEqual(current.expected(stable, result),
                                 f['chain'].expected(stable, result))
                self.assertEqual(current.plan.fingerprint, f['plan'].fingerprint)
                observations = result
        final = json.loads(observations[0].raw)
        self.assertEqual([r['id'] for r in final['data']['entities']], ['two', 'foreign', 'one'])
        self.assertEqual(next(r for r in final['data']['entities'] if r['id'] == 'one')['entity_id'], 'sensor.one')
        self.assertNotEqual(sha(observations[0].raw), sha(f['observations'][0].raw))

    async def test_before_requires_exact_raw_and_entire_expected_image_identity(self):
        for variation in ('format', 'foreign', 'inode', 'mode', 'dev', 'bool', 'order'):
            with self.subTest(variation=variation):
                f = await fixture()
                observations = list(f['observations'])
                original = observations[0]
                if variation == 'format':
                    observations[0] = replace(original, raw=json.dumps(json.loads(original.raw)).encode())
                elif variation == 'foreign':
                    doc = json.loads(original.raw)
                    doc['data']['entities'][2]['name'] = 'foreign edit'
                    observations[0] = replace(original, raw=raw(doc))
                elif variation == 'order':
                    observations.reverse()
                else:
                    identity = list(original.identity)
                    at = {'inode': 1, 'mode': 2, 'dev': 0, 'bool': 0}[variation]
                    identity[at] = True if variation == 'bool' else identity[at] + 1
                    observations[0] = replace(original, identity=tuple(identity))
                with self.assertRaises(ValueError):
                    await f['chain'].begin(context(f, 0, ['intent', 'pending', 'pending']), tuple(observations))
                self.assertEqual(len(f['store'].rows), 1)

    async def test_operation_plan_phase_and_preparation_bindings(self):
        f = await fixture()
        ctx = context(f, 0, ['intent', 'pending', 'pending'])
        bad_contexts = [replace(ctx, record=replace(ctx.record, operation_id='other')),
                        replace(ctx, record=replace(ctx.record, plan=replace(f['plan'], plan_id='other'))),
                        replace(ctx, record=replace(ctx.record, phases=('pending',) * 3)),
                        replace(ctx, record=replace(ctx.record, direction='rollback')),
                        replace(ctx, record=replace(ctx.record, status='unknown')),
                        context(f, 1, ['pending', 'intent', 'pending'])]
        for bad in bad_contexts:
            with self.subTest(context=bad):
                with self.assertRaises(Exception):
                    await f['chain'].begin(bad, f['observations'])
        with self.assertRaises(ValueError):
            receipts.RegistryReceiptChain.reopen(f['store'], 'provided-operation-id', f['plan'],
                f['receipt'], {**f['digests'], 'change-0': '0' * 64})

    async def test_result_delta_stamp_and_audit_all_required(self):
        for variation in ('foreign', 'audit', 'mutation', 'identity', 'other-file', 'intent', 'context'):
            with self.subTest(variation=variation):
                f = await fixture()
                ctx = context(f, 0, ['intent', 'pending', 'pending'])
                intent = await f['chain'].begin(ctx, f['observations'])
                result, mutation, audit = effect(f['observations'], f['changes'][0])
                if variation == 'foreign':
                    result = list(result)
                    doc = json.loads(result[0].raw)
                    doc['data']['entities'][0]['name'] = 'foreign beside real rename'
                    result[0] = replace(result[0], raw=raw(doc))
                    audit = replace(audit, result_sha256=sha(result[0].raw))
                    result = tuple(result)
                elif variation == 'audit':
                    audit = replace(audit, modified_at_after='fabricated result-only audit')
                elif variation == 'mutation':
                    mutation = replace(mutation, before_json=mutation.after_json)
                elif variation == 'identity':
                    result = (replace(result[0], identity=(8, 900, stat.S_IFREG | 0o600)), *result[1:])
                elif variation == 'other-file':
                    doc = json.loads(result[2].raw)
                    doc['data']['entries'][0]['options']['foreign'] = False
                    result = (*result[:2], replace(result[2], raw=raw(doc)))
                elif variation == 'intent':
                    intent = receipts.RegistryIntent(intent.reference)
                else:
                    ctx = replace(ctx, index=1)
                with self.assertRaises(Exception):
                    await f['chain'].complete(intent, ctx, f['observations'], result,
                                              mutation=mutation, audit=audit)
                self.assertEqual(len(f['store'].rows), 2)

    async def test_unresolved_intent_stays_unknown_no_replay_or_result_only_recovery(self):
        f = await fixture()
        ctx = context(f, 0, ['intent', 'pending', 'pending'])
        intent = await f['chain'].begin(ctx, f['observations'])
        with self.assertRaisesRegex(ValueError, 'unresolved_intent'):
            await f['chain'].begin(ctx, f['observations'])
        current = await reopened(f)
        with self.assertRaisesRegex(ValueError, 'unresolved_intent'):
            current.expected(ctx, f['observations'])
        with self.assertRaisesRegex(ValueError, 'read_only'):
            await current.begin(ctx, f['observations'])
        result, mutation, audit = effect(f['observations'], f['changes'][0])
        with self.assertRaisesRegex(ValueError, 'intent_not_live'):
            await current.complete(intent, ctx, f['observations'], result, mutation=mutation, audit=audit)
        with self.assertRaisesRegex(ValueError, 'mutation_invalid'):
            await f['chain'].complete(intent, ctx, f['observations'], result, mutation=None, audit=audit)
        with self.assertRaisesRegex(ValueError, 'event_exists_no_replay'):
            await receipts.RegistryReceiptChain.start(f['store'], 'provided-operation-id',
                       f['plan'], f['receipt'], f['digests'])

    async def test_lost_save_ack_poisons_existing_prepared_store_fresh_reads_only(self):
        for lost_at in ('intent', 'receipt'):
            with self.subTest(lost_at=lost_at):
                f = await fixture()
                ctx = context(f, 0, ['intent', 'pending', 'pending'])
                if lost_at == 'intent':
                    f['sidecar_disk'].lose_ack = True
                    with self.assertRaises(OSError):
                        await f['chain'].begin(ctx, f['observations'])
                else:
                    intent = await f['chain'].begin(ctx, f['observations'])
                    result, mutation, audit = effect(f['observations'], f['changes'][0])
                    f['sidecar_disk'].lose_ack = True
                    with self.assertRaises(OSError):
                        await f['chain'].complete(intent, ctx, f['observations'], result,
                                                  mutation=mutation, audit=audit)
                self.assertIsNone(f['preparations'].plans)
                self.assertTrue(f['store'].poisoned)
                with self.assertRaises(ValueError):
                    f['preparations'].resolve('change-0', f['digests']['change-0'])
                current = await reopened(f)
                self.assertFalse(current.writable)
                if lost_at == 'receipt':
                    stable = context(f, 0, ['verified', 'pending', 'pending'])
                    current.expected(stable, result)
                with self.assertRaises(ValueError):
                    await current.begin(ctx, f['observations'])

    async def test_receipt_storage_does_not_duplicate_full_document(self):
        sizes = []
        for padding in (0, 2 * 1024 * 1024):
            f = await fixture(padding)
            ctx = context(f, 0, ['intent', 'pending', 'pending'])
            intent = await f['chain'].begin(ctx, f['observations'])
            result, mutation, audit = effect(f['observations'], f['changes'][0])
            await f['chain'].complete(intent, ctx, f['observations'], result,
                                      mutation=mutation, audit=audit)
            sizes.append(len(json.dumps(f['sidecar_disk'].value)))
            self.assertLess(sizes[-1], 16000)
        self.assertEqual(sizes[0], sizes[1])
        self.assertGreater(receipts.MAX_EVENTS, 1 + 4 * 584)

    async def test_gap_refusals_do_not_create_events(self):
        f = await fixture()
        for adapter, causes in (('helper', ()), ('yaml', ()), ('unknown', ()),
                                 ('registry', ('step-0',))):
            steps = list(f['plan'].steps)
            steps[1] = replace(steps[1], adapter=adapter, caused_by=causes)
            plan = replace(f['plan'], steps=tuple(steps), verification_adapters=('registry', adapter))
            with self.assertRaisesRegex(ValueError, 'gap_'):
                await receipts.RegistryReceiptChain.start(f['store'], 'new-operation',
                           plan, f['receipt'], f['digests'])
        self.assertEqual(len(f['store'].rows), 1)

    async def test_reopen_rejects_stored_audit_intent_and_preparation_mismatches(self):
        for variation in ('audit', 'intent', 'preparation', 'source_chain', 'digest'):
            with self.subTest(variation=variation):
                f = await fixture()
                ctx = context(f, 0, ['intent', 'pending', 'pending'])
                intent = await f['chain'].begin(ctx, f['observations'])
                result, mutation, audit = effect(f['observations'], f['changes'][0])
                ref = await f['chain'].complete(intent, ctx, f['observations'], result,
                                                 mutation=mutation, audit=audit)
                row = f['sidecar_disk'].value['events'][ref[0]]
                if variation == 'audit':
                    row['payload']['audit']['modified_at_after'] = 'foreign'
                elif variation == 'intent':
                    row['payload']['intent'][1] = '0' * 64
                elif variation == 'preparation':
                    row['payload']['binding']['prepared_digest'] = '0' * 64
                elif variation == 'source_chain':
                    row['payload']['source_chain'] = '0' * 64
                if variation != 'digest':
                    row['digest'] = prepared.canonical(row['payload'])[1]
                else:
                    row['digest'] = '0' * 64
                with self.assertRaises(ValueError):
                    await reopened(f)


if __name__ == '__main__':
    unittest.main()
