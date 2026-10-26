"""Synthetic IO/contract tests; mock checker results are never native evidence.

Owner runs through FIFO after integration. No live HA config or reload is used.
"""
import asyncio
from dataclasses import FrozenInstanceError
import importlib
import os
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

    async def test_synthetic_io_checker_sees_actual_after_and_returns_only_proof(self):
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
        async def checker(before, after, timeout, checkpoint, *, with_sources):
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
        with patch.object(subject, 'Path', wraps=Path) as paths:
            paths.side_effect = lambda value: (Path(self.temp.name).parent
                if value == '/Volumes/Samsung_SSD' else Path(value))
            with patch.object(subject, 'async_observe_native_inputs',
                              new=AsyncMock(return_value=observation)), patch.object(
                                  subject, '_checker', new=checker):
                proof = await subject.async_revalidate_accepted_candidate(
                    hass, chain, loader, self.temp.name, lambda: None)
        self.assertEqual(proof.head, self.state.head)
        self.assertEqual(proof.original_receipt_sha256,
                         fixture.digest(subject._receipt_bytes(self.original)))
        self.assertIs(chain.original_receipt, self.original)
        self.assertFalse(hasattr(proof, 'receipt'))

    def test_proof_is_frozen_and_has_no_authorization_or_refreshed_receipt(self):
        proof = subject._AcceptedCandidateProof('op', 'plan', 'head', 'forward',
                                                 'original', 'graph', 'runtime', 'version')
        for field in ('receipt', 'ready_for_apply', 'permission', 'native_inputs'):
            self.assertFalse(hasattr(proof, field))
        with self.assertRaises(FrozenInstanceError):
            proof.head = 'different'


if __name__ == '__main__':
    unittest.main()
