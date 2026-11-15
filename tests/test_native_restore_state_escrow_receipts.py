"""Synthetic immutable escrow graphs only; authored, NOT RUN by worker.

Real classes/ORIGINAL resolver/schema parser, with explicit synthetic lifecycle
and RestoreState observations. No native effect, authorization or AC proof.
Owner adds the real effect to test_receipted_helper_native's both-pin flow.
"""
from copy import deepcopy
from dataclasses import replace
import importlib
import unittest

import test_native_registry_receipts as base
import test_own_helper_registry_binding as own

m = importlib.import_module(base.PACKAGE + '.native_restore_state_escrow_receipts')
codec = importlib.import_module(base.PACKAGE + '.native_restore_state_payload')
lm = own.lifecycle


def payload(old, new, counter='11'):
    stamp = '2026-10-25T10:00:00.000001+00:00'
    row = {'state': {'entity_id': old, 'state': counter,
        'attributes': {'unit_of_measurement': 'kWh', 'nested': {'keep': [1, None]}},
        'context': {'id': '01SYNTHETIC', 'parent_id': None, 'user_id': None},
        'last_changed': stamp, 'last_updated': stamp, 'last_reported': stamp},
        'extra_data': {'last_valid_state': counter, 'last_period': '3'}, 'last_seen': stamp}
    after = deepcopy(row)
    after['state']['entity_id'] = new
    return codec.PreparedPayload('2026.10.2', codec._canonical(row), codec._canonical(after))


async def lifecycle_graph(test, *, source=None, original=None):
    if original is None:
        life, doc, baseline = await own.OwnHelperBindingTests().fixture()
    else:
        life = lm.NativeHelperLifecycle(original.store, None, original.operation_id,
            original.plan, original.receipt, original.binding['preparations'],
            guard=lambda: None, verify_original_native_inputs=original.verify_original_native_inputs,
            settle=original.settle, verify_inactive=original.verify_inactive,
            verify_runtime=original.verify_runtime,
            verify_accepted_native_chain=original.verify_accepted_native_chain,
            source_authorization=source)
    # Deliberately synthetic inactive original. NO native API call is claimed.
    entry = 'entry'
    activation = {'entry_id': entry, 'state': 'not_loaded', **life.expected_metadata[entry]}
    life.root = {'kind': 'helper_lifecycle_root', 'schema': 1,
                 'binding': life.binding, 'original': [activation]}
    await life.store.create(life.root_id, life.root)
    intent = {'kind': 'helper_lifecycle_intent', 'root': life.root_id,
        'stage': 'unload', 'entry_id': entry, 'previous': m._digest(life.root),
        'original': activation}
    intent_ref = await life.store.create(life._slot('unload', entry, 'intent'), intent)
    receipt = {'kind': 'helper_lifecycle_receipt', 'root': life.root_id,
        'intent': intent_ref[1], 'stage': 'unload', 'entry_id': entry,
        'api_called': False, 'state': 'not_loaded'}
    await life.store.create(life._slot('unload', entry, 'receipt'), receipt)
    test.assertTrue(life.inspect()['inactive_receipts_complete'])
    return life


async def escrow_graph(life, *, counter='11', target=None, forward=None,
                       write_intent=True, write_receipt=True):
    binding = m._lifecycle_binding(life, 'entry')
    change = life.own_registry_changes['entry']
    old, new = change.before['entity_id'], change.after['entity_id']
    direction = 'forward'
    if life.source_authorization is not None:
        old, new, direction = new, old, 'rollback'
    value = payload(old, new, counter)
    root = {'kind': 'restore_state_escrow_root', 'schema': 1, 'binding': binding,
        'direction': direction, 'payload': codec._parse(codec.serialize(value)),
        'target_preimage': codec._parse(target) if target is not None else None,
        'disk_preimage': {old: None, new: codec._parse(target) if target is not None else None},
        'forward_receipt': list(forward) if forward is not None else None}
    ref = await life.store.create(m._root_id(binding, direction), root)
    intent_ref = None
    if write_intent:
        intent = {'kind': 'restore_state_escrow_intent', 'root': list(ref),
                  'previous': binding['unload_receipt']}
        intent_ref = await life.store.create(m._slot(ref[0], 'intent'), intent)
    receipt_ref = None
    if write_receipt:
        receipt = {'kind': 'restore_state_escrow_receipt', 'root': list(ref),
            'intent': list(intent_ref) if intent_ref else ['rr-' + '0' * 64, '0' * 64],
            'previous': binding['unload_receipt'], 'payload': root['payload'],
            'readback': {'source': codec._parse(value.before_json),
                'target': codec._parse(value.after_json),
                'source_sha256': value.before_digest, 'target_sha256': value.after_digest}}
        receipt_ref = await life.store.create(m._slot(ref[0], 'receipt'), receipt)
    return ref, intent_ref, receipt_ref, value


def inspect(life, root):
    return m.inspect_escrow(life.store, life.operation_id, life.plan, life.receipt,
                            life.binding['preparations'], root)


class EscrowReceiptGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_forward_full_payload_digests_unload_and_readonly(self):
        life = await lifecycle_graph(self)
        root, intent, receipt, value = await escrow_graph(life)
        state = inspect(life, root)
        self.assertEqual(state, {'status': 'verified', 'pending': None,
                                'receipt': list(receipt), 'read_only': True})
        parsed = m._parse_root(life.store, (life.operation_id, life.plan, life.receipt,
                                life.binding['preparations']), root)
        self.assertEqual(parsed[1], value)
        self.assertFalse(parsed[2].writable)
        self.assertEqual(parsed[2].own_registry_changes, life.own_registry_changes)

    async def test_root_and_intent_cuts_are_unknown_never_replay(self):
        for cut in ('root', 'intent'):
            life = await lifecycle_graph(self)
            root, _, _, _ = await escrow_graph(life, write_intent=cut != 'root', write_receipt=False)
            state = inspect(life, root)
            self.assertEqual(state['status'], 'unknown')
            self.assertTrue(state['read_only'])
            self.assertIsNone(state['receipt'])
            with self.assertRaises(base.receipts.RegistryReceiptError):
                await life.store.create(root[0], life.store.read(root[0])['payload'])

    async def test_receipt_without_intent_refused(self):
        life = await lifecycle_graph(self)
        root, _, _, _ = await escrow_graph(life, write_intent=False)
        with self.assertRaisesRegex(m.RestoreEscrowError, 'receipt_without_intent'):
            inspect(life, root)

    async def test_lost_receipt_ack_poison_then_fresh_read_is_only_history(self):
        life = await lifecycle_graph(self)
        root, intent, _, value = await escrow_graph(life, write_receipt=False)
        disk = life.store.store
        disk.lose_ack = True
        # Synthetic receipt bytes: this checks Store ACK semantics, NOT IO truth.
        root_event = life.store.read(root[0])['payload']
        receipt = {'kind': 'restore_state_escrow_receipt', 'root': list(root),
            'intent': list(intent), 'previous': root_event['binding']['unload_receipt'],
            'payload': root_event['payload'], 'readback': {
                'source': codec._parse(value.before_json), 'target': codec._parse(value.after_json),
                'source_sha256': value.before_digest, 'target_sha256': value.after_digest}}
        with self.assertRaises(OSError):
            await life.store.create(m._slot(root[0], 'receipt'), receipt)
        self.assertTrue(life.store.poisoned)
        with self.assertRaises(base.receipts.RegistryReceiptError):
            inspect(life, root)
        preparations = base.prepared.PreparedStore(life.store.preparations.store)
        await preparations.initialize()
        reopened = base.receipts.RegistryReceiptStore(disk, preparations)
        await reopened.initialize()
        state = m.inspect_escrow(reopened, life.operation_id, life.plan, life.receipt,
                                 life.binding['preparations'], root)
        self.assertTrue(state['read_only'])
        self.assertEqual(state['status'], 'verified')

    async def test_reverse_b1_retained_owned_old_and_copied_forward_witness(self):
        forward = await lifecycle_graph(self)
        fr, _, receipt, old = await escrow_graph(forward)
        disk = base.MemoryDurable()
        destination = base.receipts.RegistryReceiptStore(disk, forward.store.preparations)
        await destination.initialize()
        await m.copy_escrow_witness(forward.store, destination, forward.operation_id,
            forward.plan, forward.receipt, forward.binding['preparations'], fr)
        self.assertEqual(m.inspect_escrow(destination, forward.operation_id, forward.plan,
            forward.receipt, forward.binding['preparations'], fr)['status'], 'verified')
        source = lm.RestoreAuthorization(forward.operation_id, forward.plan.fingerprint,
                                          'a' * 64, 'applied')
        forward.store = destination
        reverse = await lifecycle_graph(self, original=forward, source=source)
        rr, _, _, fresh = await escrow_graph(reverse, counter='19',
                                              target=old.before_json, forward=receipt)
        self.assertEqual(inspect(reverse, rr)['status'], 'verified')
        self.assertIn(b'"state":"19"', fresh.before_json)
        self.assertNotEqual(old.before_digest, fresh.after_digest)
        self.assertFalse(reverse.writable)

    async def test_reverse_absent_with_same_forward_receipt_is_supported(self):
        life = await lifecycle_graph(self)
        _, _, receipt, _ = await escrow_graph(life)
        source = lm.RestoreAuthorization(life.operation_id, life.plan.fingerprint,
                                         'a' * 64, 'applied')
        reverse = await lifecycle_graph(self, original=life, source=source)
        rr, _, _, fresh = await escrow_graph(reverse, counter='19', forward=receipt)
        self.assertEqual(inspect(reverse, rr)['status'], 'verified')
        self.assertIsNone(reverse.store.read(rr[0])['payload']['target_preimage'])
        self.assertIn(b'"state":"19"', fresh.before_json)

    async def test_readonly_lifecycle_cannot_construct_native_writer(self):
        life = await lifecycle_graph(self)
        self.assertFalse(life.writable)
        with self.assertRaisesRegex(m.RestoreEscrowError, 'live_native_scope_required'):
            m.NativeRestoreStateEscrow(life, 'entry', None, source_guard=lambda: None)

    async def test_reverse_absent_requires_complete_same_forward_receipt(self):
        for target in (None, b'foreign'):
            life = await lifecycle_graph(self)
            root, _, receipt, value = await escrow_graph(life)
            source = lm.RestoreAuthorization(life.operation_id, life.plan.fingerprint,
                                             'a' * 64, 'applied')
            reverse = await lifecycle_graph(self, original=life, source=source)
            bad = None
            if target:
                row = codec._parse(value.before_json)
                row['state']['state'] = '999'
                bad = codec._canonical(row)
            rr, _, _, _ = await escrow_graph(reverse, counter='19', target=bad,
                                              forward=receipt if target else None)
            with self.assertRaises(m.RestoreEscrowError):
                inspect(reverse, rr)

    async def test_readback_tamper_even_with_recomputed_outer_digest_refused(self):
        for field in ('source', 'target', 'source_sha256', 'target_sha256'):
            life = await lifecycle_graph(self)
            root, _, receipt, _ = await escrow_graph(life)
            row = life.store.rows[receipt[0]]
            changed = deepcopy(row['payload'])
            changed['readback'][field] = None
            life.store.rows[receipt[0]] = {'payload': changed, 'digest': m._digest(changed)}
            with self.assertRaisesRegex(m.RestoreEscrowError, 'receipt_binding'):
                inspect(life, root)

    async def test_extension_bool_schema_cross_operation_uuid_and_digest_refused(self):
        for mutate in ('schema', 'extra', 'operation', 'uuid', 'unload', 'digest'):
            life = await lifecycle_graph(self)
            root, _, _, _ = await escrow_graph(life)
            changed = deepcopy(life.store.rows[root[0]]['payload'])
            if mutate == 'schema': changed['schema'] = True
            elif mutate == 'extra': changed['extension'] = None
            elif mutate == 'operation': changed['binding']['original']['operation_id'] = 'foreign'
            elif mutate == 'uuid': changed['binding']['helper_uuid'] = 'foreign'
            elif mutate == 'unload': changed['binding']['unload_receipt'][1] = '0' * 64
            new_digest = m._digest(changed)
            if mutate == 'digest': new_digest = '0' * 64
            life.store.rows[root[0]] = {'payload': changed, 'digest': new_digest}
            with self.assertRaises((m.RestoreEscrowError, lm.HelperLifecycleError)):
                inspect(life, [root[0], new_digest])

    async def test_live_token_copy_cannot_execute_or_complete(self):
        # No fake native IO: validate identity rejection BEFORE any live effect.
        token = m.RestoreEscrowIntent(('rr-' + 'a' * 64, 'b' * 64),
                                      ('rr-' + 'c' * 64, 'd' * 64))
        obj = object.__new__(m.NativeRestoreStateEscrow)
        obj._busy = False
        obj.active, obj.completed = token, None
        for candidate in (replace(token), tuple(token.reference), None):
            with self.assertRaisesRegex(m.RestoreEscrowError, 'intent_not_live'):
                await obj.execute(candidate)
        obj.active, obj.completed = None, token
        with self.assertRaisesRegex(m.RestoreEscrowError, 'completed_live_identity'):
            obj.require_completed(replace(token))
        with self.assertRaisesRegex(m.RestoreEscrowError, 'intent_not_live'):
            await obj.execute(token)


if __name__ == '__main__':
    unittest.main()
