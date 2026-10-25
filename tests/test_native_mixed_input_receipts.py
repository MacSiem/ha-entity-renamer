"""Synthetic chain semantics; independent native runtime evidence remains owner QA.

These cases intentionally mock the pure disk checker only around synthetic
nonexistent paths. Actual checker/no-write cases below use a temporary graph.
"""
from copy import deepcopy
from dataclasses import replace
import importlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

# Explicit sibling load also supports isolated unittest file execution.
_spec = importlib.util.spec_from_file_location('_mixed_receipts_base',
    Path(__file__).with_name('test_native_registry_receipts.py'))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
receipts, prepared, baseline, tx = base.receipts, base.prepared, base.baseline, base.tx
accepted = importlib.import_module(base.PACKAGE + '.accepted_input_state')
yaml = importlib.import_module(base.PACKAGE + '.yaml_transaction')


async def mixed_fixture():
    f = await base.fixture()
    row = yaml.YAMLFile('scripts.yaml', b'old script\n'.hex(), b'new script\n'.hex(), 7, 701)
    second = yaml.YAMLFile('scripts-extra.yaml', b'old extra\n'.hex(), b'new extra\n'.hex(), 7, 702)
    rows = (row, second)
    sources = tuple((r.path, base.sha(r.before), r.device, r.inode) for r in rows)
    directories = list(f['receipt'].source_directories)
    root, members, names = directories[0]
    directories[0] = (root, tuple(sorted((*members,
                         *((r.path, stat.S_IFREG | 0o640, r.device, r.inode) for r in rows)))), names)
    receipt = replace(f['receipt'], source_files=tuple(sorted((*f['receipt'].source_files, *sources))),
        source_directories=tuple(directories), proposed_files=tuple((r.path, base.sha(r.after)) for r in rows),
        runtime_expected=(('script', base.sha(b'before runtime'), base.sha(b'after runtime')),))
    original = baseline.NativeInputBaseline(baseline._receipt_bytes(receipt),
                                           f['chain'].baseline.files)
    native_ref = await f['preparations'].create_native_inputs('mixed-inputs', original)
    change = yaml.PreparedYAML(receipt, rows).checked()
    digest = await f['preparations'].create('yaml-change', prepared.encode_change(change))
    step = tx.Step('yaml-step', 'yaml', change.identity, change.before_revision,
                   change.after_revision, 'yaml-change')
    plan = replace(f['plan'], plan_id='mixed-plan', steps=(*f['plan'].steps, step),
                   required_scopes=('registry', 'yaml'), covered_scopes=('registry', 'yaml'),
                   verification_adapters=('registry', 'yaml'), native_inputs=native_ref)
    metadata = {r.path: [r.device, r.inode, stat.S_IFREG | 0o640, 501, 20] for r in rows}
    f.update(plan=plan, receipt=receipt, digests={**f['digests'], 'yaml-change': digest},
             yaml=change, metadata=metadata)
    with patch.object(accepted, 'capture_yaml_original_metadata', return_value=metadata):
        f['chain'] = await receipts.RegistryReceiptChain.start(f['store'], 'provided-operation-id',
            plan, receipt, f['digests'])
    return f


async def apply_native(f):
    phases, observations = ['pending'] * 4, f['observations']
    for i in range(3):
        phases[i] = 'intent'
        context = base.context(f, i, phases)
        intent = await f['chain'].begin(context, observations)
        after, mutation, audit = base.effect(observations, f['changes'][i])
        await f['chain'].complete(intent, context, observations, after,
                                  mutation=mutation, audit=audit)
        phases[i], observations = 'verified', after
    return phases, observations


def calls_for(f, state, reverse=False):
    change = f['yaml']
    rows = tuple(reversed(change.files)) if reverse else change.files
    result = []
    for row in rows:
        source = state.sources()[row.path]
        digest = base.sha(row.before if reverse else row.after)
        identity = [source[1], source[2] + 100, source[3]]
        operation = 'yaml_' + yaml._digest((change.identity, row.path, reverse))[:64]
        result.append({'operation_id': operation, 'path': row.path,
            'before_sha256': source[0], 'after_sha256': digest,
            'before_identity': tuple(source[1:4]), 'after_identity': tuple(identity),
            'before_uid': source[4], 'before_gid': source[5],
            'after_uid': source[4], 'after_gid': source[5],
            'backup': '.storage/ha_entity_renamer_backups/' + operation + '-'
                      + base.sha(row.path.encode()) + '.yaml-backup',
            'sha256': digest, 'device': identity[0], 'inode': identity[1]})
    return result


class MixedReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_yaml_forward_reopen_reverse_same_head_and_original_bytes(self):
        f = await mixed_fixture()
        original = baseline._receipt_bytes(f['receipt'])
        phases, observations = await apply_native(f)
        forward_state = f['chain'].reconstruct_inputs()
        phases[3] = 'intent'
        context = base.context(f, 3, phases)
        calls = calls_for(f, forward_state)
        with patch.object(accepted.AcceptedInputState, 'assert_current'):
            intent = await f['chain'].begin_yaml(context, observations, input_state=forward_state)
            active = f['chain'].live_yaml_inputs(intent, context, calls)
            await f['chain'].complete_yaml(intent, context, observations, observations, calls=calls)
        phases[3] = 'verified'
        accepted_state = f['chain'].reconstruct_inputs()
        self.assertEqual(accepted_state.sources()['scripts.yaml'][2], 801)
        self.assertEqual(accepted_state.original_json, original)
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'input_state_stale'):
            forward_state._bound(f['receipt'])
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'intent_not_live'):
            active._bound(f['receipt'])  # The partial hook expires after receipt ACK.
        saves = f['sidecar_disk'].saves
        reopened = await base.reopened(f)
        self.assertTrue(reopened.reconstruct_inputs().same_evidence(accepted_state))
        self.assertEqual(f['sidecar_disk'].saves, saves)
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'yaml_intent_not_live'):
            await reopened.begin_yaml(context, observations, input_state=accepted_state)
        phases[3] = 'undo_intent'
        context = base.context(f, 3, phases, 'rollback')
        reverse_calls = calls_for(f, accepted_state, True)
        with patch.object(accepted.AcceptedInputState, 'assert_current'):
            intent = await f['chain'].begin_yaml(context, observations, input_state=accepted_state)
            await f['chain'].complete_yaml(intent, context, observations, observations,
                                           calls=reverse_calls)
        phases[3] = 'undone'
        restored = f['chain'].reconstruct_inputs()
        self.assertEqual(restored.sources()['scripts.yaml'][0], base.sha(f['yaml'].files[0].before))
        self.assertEqual(restored.sources()['scripts.yaml'][2], 901)  # exact third inode
        for i in reversed(range(3)):
            phases[i] = 'undo_intent'
            context = base.context(f, i, phases, 'rollback')
            intent = await f['chain'].begin(context, observations)
            after, mutation, audit = base.effect(observations, f['changes'][i], 'rollback')
            await f['chain'].complete(intent, context, observations, after,
                                      mutation=mutation, audit=audit)
            phases[i], observations = 'undone', after
        self.assertEqual((await base.reopened(f)).reconstruct_inputs().sources()['scripts.yaml'],
                         f['chain'].reconstruct_inputs().sources()['scripts.yaml'])
        self.assertEqual(baseline._receipt_bytes(f['receipt']), original)

    async def test_pending_yaml_is_unknown_and_cannot_recover_partial_calls(self):
        f = await mixed_fixture()
        phases, observations = await apply_native(f)
        phases[3] = 'intent'
        context = base.context(f, 3, phases)
        state = f['chain'].reconstruct_inputs()
        with patch.object(accepted.AcceptedInputState, 'assert_current'):
            intent = await f['chain'].begin_yaml(context, observations, input_state=state)
        calls = calls_for(f, state)
        partial = f['chain'].live_yaml_inputs(intent, context, calls[:1])
        self.assertEqual(partial.sources()['scripts.yaml'][2], 801)
        self.assertEqual(partial.sources()['scripts-extra.yaml'][2], 702)
        with patch.object(accepted.AcceptedInputState, 'assert_current'):
            with self.assertRaisesRegex(receipts.RegistryReceiptError, 'yaml_calls_incomplete_or_unbound'):
                await f['chain'].complete_yaml(intent, context, observations, observations, calls=calls[:1])
        saves = f['sidecar_disk'].saves
        reopened = await base.reopened(f)
        for chain in (f['chain'], reopened):
            with self.assertRaisesRegex(receipts.RegistryReceiptError, 'unknown_unresolved_intent'):
                chain.reconstruct_inputs()
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'intent_not_live'):
            reopened.live_yaml_inputs(intent, context, calls_for(f, state))
        self.assertEqual(f['sidecar_disk'].saves, saves)

    async def test_call_binding_and_native_reload_drift_refuse_before_receipt(self):
        for altered in ({'before_identity': (7, 999, stat.S_IFREG | 0o640)},
                        {'before_uid': 0}, {'after_gid': 0}, {'path': 'foreign.yaml'},
                        {'operation_id': 'different-operation'}, {'after_sha256': '0' * 64}):
            f = await mixed_fixture()
            phases, observations = await apply_native(f)
            phases[3] = 'intent'
            context = base.context(f, 3, phases)
            state = f['chain'].reconstruct_inputs()
            with patch.object(accepted.AcceptedInputState, 'assert_current'):
                intent = await f['chain'].begin_yaml(context, observations, input_state=state)
                saves = f['sidecar_disk'].saves
                calls = calls_for(f, state)
                calls[0].update(altered)
                with self.assertRaises((receipts.RegistryReceiptError, ValueError)):
                    await f['chain'].complete_yaml(intent, context, observations, observations, calls=calls)
                self.assertEqual(f['sidecar_disk'].saves, saves)
        # A correct file result cannot own a native listener effect.
        f = await mixed_fixture()
        phases, observations = await apply_native(f)
        phases[3] = 'intent'
        context = base.context(f, 3, phases)
        state = f['chain'].reconstruct_inputs()
        with patch.object(accepted.AcceptedInputState, 'assert_current'):
            intent = await f['chain'].begin_yaml(context, observations, input_state=state)
            foreign = (replace(observations[0], identity=(7, 9999, observations[0].identity[2])),
                       *observations[1:])
            with self.assertRaisesRegex(receipts.RegistryReceiptError, 'source_observation_mismatch'):
                await f['chain'].complete_yaml(intent, context, observations, foreign,
                                               calls=calls_for(f, state))

    async def test_reopen_revalidates_yaml_call_not_only_saved_hashes(self):
        f = await mixed_fixture()
        phases, observations = await apply_native(f)
        phases[3] = 'intent'
        context = base.context(f, 3, phases)
        state = f['chain'].reconstruct_inputs()
        with patch.object(accepted.AcceptedInputState, 'assert_current'):
            intent = await f['chain'].begin_yaml(context, observations, input_state=state)
            await f['chain'].complete_yaml(intent, context, observations, observations,
                                           calls=calls_for(f, state))
        row = next(row for row in f['sidecar_disk'].value['events'].values()
                   if row['payload']['kind'] == 'yaml_receipt')
        row['payload']['calls'][0]['before_identity'][1] += 1
        row['digest'] = prepared.canonical(row['payload'])[1]
        saves = f['sidecar_disk'].saves
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'yaml_call_binding_mismatch'):
            await base.reopened(f)
        self.assertEqual(f['sidecar_disk'].saves, saves)

    async def test_yaml_receipt_lost_ack_poisoned_writer_and_no_replay(self):
        f = await mixed_fixture()
        phases, observations = await apply_native(f)
        phases[3] = 'intent'
        context = base.context(f, 3, phases)
        state = f['chain'].reconstruct_inputs()
        with patch.object(accepted.AcceptedInputState, 'assert_current'):
            intent = await f['chain'].begin_yaml(context, observations, input_state=state)
            f['sidecar_disk'].lose_ack = True
            with self.assertRaises(OSError):
                await f['chain'].complete_yaml(intent, context, observations, observations,
                                               calls=calls_for(f, state))
        self.assertTrue(f['store'].poisoned)
        self.assertIsNone(f['preparations'].plans)
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'store_unavailable'):
            f['chain'].reconstruct_inputs()
        current = await base.reopened(f)
        self.assertFalse(current.writable)
        saves = f['sidecar_disk'].saves
        current.reconstruct_inputs()  # Read-only accepted evidence, never replay.
        self.assertEqual(f['sidecar_disk'].saves, saves)

    async def test_yaml_backup_membership_is_preflight_refusal(self):
        f = await mixed_fixture()
        scoped = tuple((path, entries, None if path == '.storage' else names)
                       for path, entries, names in f['receipt'].source_directories)
        original = replace(f['receipt'], source_directories=scoped)
        change = replace(f['yaml'], receipt=original)
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'yaml_backup_membership_unprepared'):
            accepted.validate_yaml_metadata(original, change, f['metadata'])

    async def test_mutated_prepared_original_refuses_even_with_reconstruction_cache(self):
        f = await mixed_fixture()
        captured = f['chain'].reconstruct_inputs()
        f['preparations'].plans['yaml-change']['payload']['data']['receipt']['runtime_expected'][0][2] = '0' * 64
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'preparation_mismatch'):
            f['chain'].reconstruct_inputs()
        with self.assertRaisesRegex(receipts.RegistryReceiptError, 'preparation_mismatch'):
            captured._bound(f['receipt'])


class WholeInputCheckerTests(unittest.TestCase):
    def test_original_graph_checker_refuses_same_bytes_new_inode_mode_and_membership(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'scripts.yaml'
            path.write_bytes(b'original\n')
            info = path.stat()
            receipt = base.validation.ValidationReceipt(root,
                (('scripts.yaml', base.sha(path.read_bytes()), info.st_dev, info.st_ino),),
                (('.', (('scripts.yaml', info.st_mode, info.st_dev, info.st_ino),), None),),
                (), ('script',), (), '2026.10.2')
            metadata = {'scripts.yaml': [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]}
            state = accepted.AcceptedInputState(baseline._receipt_bytes(receipt), 'test-head',
                                                accepted.initial_sources(receipt, metadata), {})
            state.assert_current(receipt)
            path.chmod(0o600 if stat.S_IMODE(info.st_mode) != 0o600 else 0o640)
            with self.assertRaisesRegex(receipts.RegistryReceiptError, 'input_source_changed'):
                state.assert_current(receipt)
            path.chmod(stat.S_IMODE(info.st_mode))
            added = Path(root) / 'unexpected.yaml'
            added.write_bytes(b'foreign\n')
            with self.assertRaisesRegex(receipts.RegistryReceiptError, 'input_graph_changed'):
                state.assert_current(receipt)
            added.rename(Path(root) / 'quarantine')
            # Keep the old descriptor to rule out inode reuse on replacement.
            with path.open('rb'):
                replacement = Path(root) / 'replacement'
                replacement.write_bytes(b'original\n')
                replacement.chmod(stat.S_IMODE(info.st_mode))
                os.replace(replacement, path)
                with self.assertRaisesRegex(receipts.RegistryReceiptError, 'input_source_changed'):
                    state.assert_current(receipt)


if __name__ == '__main__':
    unittest.main()
