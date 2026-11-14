"""Synthetic IO/contract tests; mock checker results are never native evidence.

Owner runs through FIFO after integration. No live HA config or reload is used.
"""
import asyncio
import base64
from dataclasses import FrozenInstanceError, replace
import importlib
import os
import stat
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_native_input_baseline as fixture

subject = importlib.import_module(fixture.PACKAGE + '.native_candidate_validation')
accepted = importlib.import_module(fixture.PACKAGE + '.accepted_input_state')
yaml_tx = importlib.import_module(fixture.PACKAGE + '.yaml_transaction')


class CandidateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = fixture.scratch()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'config'
        self.root.mkdir(mode=0o700)
        (self.root / '.storage').mkdir(mode=0o700)
        self.old, self.new = b'script: {before: {}}\n', b'script: {after: {}}\n'
        (self.root / 'configuration.yaml').write_bytes(self.old)
        for key in subject.KEYS:
            (self.root / '.storage' / key).write_bytes(b'{"accepted": true}')
        self.original = fixture.receipt_for(self.root)
        info = (self.root / 'configuration.yaml').stat()
        self.change = SimpleNamespace(files=(yaml_tx.YAMLFile('configuration.yaml',
            self.old.hex(), self.new.hex(), info.st_dev, info.st_ino),))
        (self.root / 'configuration.yaml').write_bytes(self.new)
        self.state = self.state_for_current()
        self.before, self.after = Path(self.temp.name) / 'before', Path(self.temp.name) / 'after'
        self.before.mkdir(mode=0o700)
        self.after.mkdir(mode=0o700)

    def state_for_current(self):
        sources = {}
        for name, *_ in self.original.source_files:
            path = self.root / name
            info = path.stat()
            sources[name] = [fixture.digest(path.read_bytes()), info.st_dev,
                            info.st_ino, info.st_mode, info.st_uid, info.st_gid]
        return accepted.AcceptedInputState(subject._receipt_bytes(self.original),
                                           'accepted-head', sources, {})

    def stage(self, reverse=False):
        return subject._stage(self.state, self.original, self.change, reverse,
                              self.before, self.after)

    def test_physical_dashboard_change_refuses_accepted_source_fence(self):
        path = self.root / '.storage/lovelace.selected'
        path.write_bytes(b'{"views": []}')
        receipt = fixture.receipt_for(self.root)
        info = path.stat()
        directories = []
        for directory, entries, names in receipt.source_directories:
            if directory == '.storage':
                entries = tuple(sorted((*entries, (path.name, info.st_mode, info.st_dev, info.st_ino))))
                names = tuple(sorted((*names, path.name)))
            directories.append((directory, entries, names))
        self.original = replace(receipt, source_directories=tuple(directories))
        state = self.state_for_current()
        state.assert_current(self.original)
        path.write_bytes(b'{"views": ["foreign"]}')
        with self.assertRaisesRegex(ValueError, 'input_source_changed'):
            state.assert_current(self.original)

    def test_checker_repairs_snapshot_survives_live_change_and_reopen(self):
        from custom_components.ha_entity_renamer import yaml_validation
        from importlib import import_module
        baseline = import_module(fixture.PACKAGE + '.native_input_baseline')
        repair = self.root / '.storage/repairs.issue_registry'
        old = b'{"issues":["original"]}'
        repair.write_bytes(old)
        receipt = fixture.receipt_for(self.root)
        directories = []
        for path, entries, names in receipt.source_directories:
            if path == '.storage':
                st = repair.stat()
                entries = tuple(sorted((*entries, (repair.name, st.st_mode, st.st_dev, st.st_ino))))
                names = tuple(sorted((*names, repair.name)))
            directories.append((path, entries, names))
        receipt = replace(receipt, source_directories=tuple(directories),
                          checker_repairs_snapshot=('present', base64.b64encode(old).decode()))
        # The actual persisted codec is also used by fresh reopen.
        encoded = baseline._receipt_bytes(receipt)
        self.assertEqual(baseline._receipt(encoded), receipt)
        self.original = baseline._receipt(encoded)
        self.state = self.state_for_current()
        repair.write_bytes(b'{"issues":["changed"]}')
        fixture.validation.assert_validation_current(self.original)
        self.stage()
        self.assertEqual((self.before / '.storage/repairs.issue_registry').read_bytes(), old)
        self.assertEqual((self.after / '.storage/repairs.issue_registry').read_bytes(), old)
        (self.root / 'configuration.yaml').write_bytes(self.old)
        self.state = self.state_for_current()
        self.stage(reverse=True)
        self.assertEqual((self.before / '.storage/repairs.issue_registry').read_bytes(), old)
        self.assertEqual((self.after / '.storage/repairs.issue_registry').read_bytes(), old)
        with self.assertRaises(ValueError):
            baseline._receipt_bytes(replace(receipt, checker_repairs_snapshot=('present', 'e30=')))
        with self.assertRaises(ValueError):
            baseline._receipt_bytes(replace(receipt, proposed_files=(
                ('.storage/repairs.issue_registry', fixture.digest(old)),)))
        # An old receipt retains the strict live fence on reopen.
        legacy = replace(receipt, checker_repairs_snapshot=None)
        self.assertNotIn(b'checker_repairs_snapshot', baseline._receipt_bytes(legacy))
        legacy_sources = self.state.sources()
        _, digest, device, inode = next(row for row in legacy.source_files
                                         if row[0] == '.storage/repairs.issue_registry')
        mode = next(row[1] for path, entries, _ in legacy.source_directories
                    if path == '.storage' for row in entries if row[0] == repair.name)
        legacy_sources['.storage/repairs.issue_registry'][:4] = [digest, device, inode, mode]
        old_state = accepted.AcceptedInputState(baseline._receipt_bytes(legacy),
                                                self.state.head, legacy_sources, {})
        with self.assertRaises(ValueError):
            old_state.assert_current(baseline._receipt(baseline._receipt_bytes(legacy)))
        (self.root / 'configuration.yaml').write_bytes(self.new)
        with self.assertRaises(ValueError):
            fixture.validation.assert_validation_current(legacy)

    def test_absent_checker_repairs_and_explicit_sources(self):
        from custom_components.ha_entity_renamer import yaml_validation
        from importlib import import_module
        baseline = import_module(fixture.PACKAGE + '.native_input_baseline')
        plan = SimpleNamespace(files=(), directories=(), powercalc_inputs=None)
        self.assertEqual(yaml_validation._checker_repairs_snapshot(plan, self.before,
            (('.storage', (), ('repairs.issue_registry',)),), None, None), ('absent',))
        plan.powercalc_inputs = SimpleNamespace(files=(),
            directories=(('.storage', (), ('energy', 'repairs.issue_registry')),))
        self.assertEqual(yaml_validation._checker_repairs_snapshot(plan, self.before,
            (('.storage', (), ('energy', 'repairs.issue_registry')),), None, None), ('absent',))
        plan.powercalc_inputs = None
        plan.files = (SimpleNamespace(path='.storage/repairs.issue_registry'),)
        self.assertIsNone(yaml_validation._checker_repairs_snapshot(plan, self.before,
            (('.storage', (), ('repairs.issue_registry',)),), None, None))
        plan.ready, plan.patches = True, ()
        with self.assertRaisesRegex(ValueError, 'checker_snapshot_real_source_conflict'):
            yaml_validation.assert_proposal_matches(
                SimpleNamespace(proposed_files=(), checker_repairs_snapshot=('absent',)), plan)
        plan.files = ()
        dashboard = SimpleNamespace(files=(), tracked_names=('repairs.issue_registry',))
        self.assertIsNone(yaml_validation._checker_repairs_snapshot(plan, self.before,
            (('.storage', (), ('repairs.issue_registry',)),), None, dashboard))
        self.assertIsNone(yaml_validation._checker_repairs_snapshot(plan, self.before,
            (('.storage', (), None),), None, None))
        directories = tuple((path, entries, tuple(sorted((*names, 'repairs.issue_registry')))
                             if path == '.storage' else names)
                            for path, entries, names in self.original.source_directories)
        self.original = replace(self.original, source_directories=directories,
                                checker_repairs_snapshot=('absent',))
        self.state = self.state_for_current()
        (self.root / '.storage/repairs.issue_registry').write_bytes(b'{"issues":[]}')
        (self.root / 'configuration.yaml').write_bytes(self.old)
        fixture.validation.assert_validation_current(self.original)
        (self.root / 'configuration.yaml').write_bytes(self.new)
        self.stage()
        self.assertFalse((self.before / '.storage/repairs.issue_registry').exists())
        self.assertFalse((self.after / '.storage/repairs.issue_registry').exists())
        legacy = replace(self.original, checker_repairs_snapshot=None)
        old_state = accepted.AcceptedInputState(baseline._receipt_bytes(legacy),
                                                self.state.head, self.state.sources(), {})
        with self.assertRaises(ValueError):
            old_state.assert_current(legacy)
        (self.root / 'configuration.yaml').write_bytes(self.old)
        with self.assertRaises(ValueError):
            fixture.validation.assert_validation_current(legacy)
        inconsistent = tuple((path, entries + (('repairs.issue_registry', 33188, 1, 2),), names)
                             if path == '.storage' else (path, entries, names)
                             for path, entries, names in self.original.source_directories)
        with self.assertRaises(ValueError):
            baseline._receipt_bytes(replace(self.original, source_directories=inconsistent))

    def test_actual_accepted_native_in_both_roots_only_original_yaml_differs(self):
        self.stage()
        self.assertEqual((self.before / 'configuration.yaml').read_bytes(), self.old)
        self.assertEqual((self.after / 'configuration.yaml').read_bytes(), self.new)
        for key in subject.KEYS:
            name = '.storage/' + key
            self.assertEqual((self.before / name).read_bytes(), (self.root / name).read_bytes())
            self.assertEqual((self.after / name).read_bytes(), (self.root / name).read_bytes())

    def test_reverse_after_is_current_original_yaml(self):
        (self.root / 'configuration.yaml').write_bytes(self.old)
        self.state = self.state_for_current()
        self.stage(reverse=True)
        self.assertEqual((self.before / 'configuration.yaml').read_bytes(), self.new)
        self.assertEqual((self.after / 'configuration.yaml').read_bytes(), self.old)

    def test_accepted_hash_cannot_substitute_wrong_desired_yaml_bytes(self):
        (self.root / 'configuration.yaml').write_bytes(b'foreign\n')
        self.state = self.state_for_current()
        with self.assertRaisesRegex(ValueError, 'yaml_current_bytes'):
            self.stage()

    def test_foreign_mode_and_symlink_refuse_without_learning_identity(self):
        path = self.root / 'configuration.yaml'
        path.chmod(0o640 if path.stat().st_mode & 0o777 != 0o640 else 0o600)
        with self.assertRaises(ValueError):
            self.stage()
        self.state = self.state_for_current()
        other = self.root / 'elsewhere'
        path.rename(other)
        path.symlink_to(other)
        with self.assertRaises((ValueError, OSError)):
            self.stage()

    def test_result_must_equal_original_including_runtime_and_version(self):
        original = self.original
        result = dict(status='valid', version=original.version,
                      changed_domains=list(original.changed_domains),
                      unsupported_domains=list(original.unsupported_domains),
                      runtime_expected=[list(row) for row in original.runtime_expected])
        subject._compare(result, original, False)
        for key, value in [('version', 'foreign'), ('runtime_expected', []),
                           ('changed_domains', []), ('unsupported_domains', ['script'])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                subject._compare({**result, key: value}, original, False)
        reversed_result = {**result, 'runtime_expected':
                           [[domain, after, before] for domain, before, after
                            in original.runtime_expected]}
        subject._compare(reversed_result, original, True)
        with self.assertRaises(ValueError):
            subject._compare(result, original, True)

    async def test_pending_intent_refuses_before_checker(self):
        chain = object.__new__(subject.RegistryReceiptChain)
        chain.plan = SimpleNamespace(steps=(object(),))
        chain.yaml_index = 0
        chain.original_receipt = self.original
        def pending():
            raise ValueError('unknown_unresolved_intent')
        chain.reconstruct_inputs = pending
        with patch.object(subject, '_checker', new_callable=AsyncMock) as checker:
            with self.assertRaisesRegex(ValueError, 'unresolved_intent'):
                await subject.async_revalidate_accepted_candidate(None, chain, None,
                                                                  self.temp.name, lambda: None)
            checker.assert_not_awaited()

    async def test_head_drift_after_phase_await_refuses_before_checker(self):
        chain = object.__new__(subject.RegistryReceiptChain)
        chain.plan = SimpleNamespace(steps=(object(),))
        chain.yaml_index = 0
        chain.original_receipt = self.original
        chain.operation_id = 'synthetic-operation'
        chain.reconstruct_inputs = lambda: self.state
        drift = accepted.AcceptedInputState(self.state.original_json, 'foreign-head',
                                            self.state.sources(), self.state.documents())
        async def loader(_):
            chain.reconstruct_inputs = lambda: drift
            return None
        # Linux isolated probes do not have the macOS SSD path. Test only this
        # local path policy here; native staging/checker are never substituted.
        with patch.object(subject, 'Path', wraps=Path) as paths:
            def make_path(value):
                return Path(self.temp.name).parent if value == '/Volumes/Samsung_SSD' else Path(value)
            paths.side_effect = make_path
            Path(self.temp.name).chmod(0o700)
            with patch.object(subject, '_checker', new_callable=AsyncMock) as checker:
                with self.assertRaisesRegex(ValueError, 'input_head_changed'):
                    await subject.async_revalidate_accepted_candidate(None, chain, loader,
                                                                      self.temp.name, lambda: None)
                checker.assert_not_awaited()

    async def test_public_parent_private_scratch_checker_returns_only_proof_and_cleans_up(self):
        tx = importlib.import_module(fixture.PACKAGE + '.transaction')
        step = tx.Step('yaml', 'yaml', 'id', 'before', 'after', 'prepared')
        plan = tx.Plan('plan', ('yaml',), ('yaml',), (step,), 'evidence', ('yaml',))
        record = tx.Record('op', plan, status='applying', phases=('verified',))
        chain = object.__new__(subject.RegistryReceiptChain)
        chain.plan, chain.yaml_index = plan, 0
        chain.original_receipt, chain.operation_id = self.original, 'op'
        chain.reconstruct_inputs = lambda: self.state
        self.change.receipt = self.original
        self.change.checked = lambda: self.change
        chain._original_change = lambda _: self.change
        chain.expected = lambda context, rows: self.state.head
        observation = subject.NativeInputObservation(self.state.original_json,
                                                       self.original.version, ())
        result = dict(status='valid', version=self.original.version,
                      changed_domains=list(self.original.changed_domains),
                      unsupported_domains=list(self.original.unsupported_domains),
                      runtime_expected=[list(row) for row in self.original.runtime_expected])
        staged = []
        async def checker(before, after, timeout, checkpoint, *, with_sources):
            staged.append(before.parent)
            scratch = before.parent.parent
            self.assertEqual(scratch.parent, self.root.parent)
            self.assertEqual(stat.S_IMODE(scratch.stat().st_mode), 0o700)
            self.assertEqual(scratch.stat().st_uid, os.getuid())
            self.assertNotEqual(scratch, self.root)
            self.assertNotIn(self.root, scratch.parents)
            self.assertIs(with_sources, False)
            await checkpoint()
            self.assertEqual((after / 'configuration.yaml').read_bytes(), self.new)
            self.assertEqual((before / 'configuration.yaml').read_bytes(), self.old)
            for key in subject.KEYS:
                name = '.storage/' + key
                self.assertEqual((after / name).read_bytes(), (self.root / name).read_bytes())
            return result
        hass = SimpleNamespace(async_add_executor_job=lambda fn: asyncio.to_thread(fn))
        async def loader(_):
            return record
        parent = self.root.parent
        mode = stat.S_IMODE(parent.stat().st_mode)
        parent.chmod(0o755)
        try:
            with patch.object(subject, 'async_observe_native_inputs',
                              new=AsyncMock(return_value=observation)), patch.object(
                                  subject, '_checker', new=checker):
                # Passing the deployment parent itself must still refuse.
                with self.assertRaisesRegex(ValueError, 'scratch_not_private'):
                    await subject.async_revalidate_accepted_candidate(
                        hass, chain, loader, parent, lambda: None)
                with subject.private_candidate_scratch(self.root) as scratch:
                    proof = await subject.async_revalidate_accepted_candidate(
                        hass, chain, loader, scratch, lambda: None)
                    self.assertTrue(scratch.is_dir())
                    self.assertTrue(staged)
                    self.assertTrue(all(not path.exists() for path in staged))
                self.assertFalse(scratch.exists())
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
        finally:
            parent.chmod(mode)
        self.assertEqual(proof.head, self.state.head)
        self.assertEqual(proof.original_receipt_sha256,
                         fixture.digest(subject._receipt_bytes(self.original)))
        self.assertIs(chain.original_receipt, self.original)
        self.assertFalse(hasattr(proof, 'receipt'))

    async def test_private_scratch_cleanup_on_failure_and_task_cancellation(self):
        parent = self.root.parent
        mode = stat.S_IMODE(parent.stat().st_mode)
        parent.chmod(0o755)
        try:
            for failure in (ValueError('synthetic checker failure'), asyncio.CancelledError()):
                with self.subTest(failure=type(failure).__name__):
                    entered = asyncio.Event()
                    blocked = asyncio.Event()
                    paths = []
                    async def validating():
                        entered.set()
                        with subject.private_candidate_scratch(self.root) as scratch:
                            paths.append(scratch)
                            self.assertEqual(stat.S_IMODE(scratch.stat().st_mode), 0o700)
                            self.assertEqual(scratch.parent, parent)
                            (scratch / 'secret-input').write_bytes(b'synthetic')
                            if isinstance(failure, asyncio.CancelledError):
                                await blocked.wait()
                            raise failure
                    task = asyncio.create_task(validating())
                    await entered.wait()
                    if isinstance(failure, asyncio.CancelledError):
                        task.cancel()
                    with self.assertRaises(type(failure)):
                        await task
                    self.assertEqual(len(paths), 1)
                    self.assertFalse(paths[0].exists())
                    self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
        finally:
            parent.chmod(mode)

    def test_proof_is_frozen_and_has_no_authorization_or_refreshed_receipt(self):
        proof = subject._AcceptedCandidateProof('op', 'plan', 'head', 'forward',
                                                 'original', 'graph', 'runtime', 'version')
        for field in ('receipt', 'ready_for_apply', 'permission', 'native_inputs'):
            self.assertFalse(hasattr(proof, field))
        with self.assertRaises(FrozenInstanceError):
            proof.head = 'different'


if __name__ == '__main__':
    unittest.main()
