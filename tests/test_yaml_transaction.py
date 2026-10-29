"""Owner-run unit regressions; no native-runtime coverage claim.

RENAMER_COMPONENT_DIR must name the frozen integrated candidate, not canonical
live source. Run via run_build_receipt.py --unit-tests in native HA environments.
The independent observer here is an explicit fake; native consumer/continuity
proof must additionally come from the owner's real runtime observer scenarios.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

component = Path(os.environ.get('RENAMER_COMPONENT_DIR',
    str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')))
package = ModuleType('yaml_transaction_candidate')
package.__path__ = [str(component)]
sys.modules[package.__name__] = package
module = importlib.import_module(package.__name__ + '.yaml_transaction')
transaction = importlib.import_module(package.__name__ + '.transaction')
prepared = importlib.import_module(package.__name__ + '.prepared')


class Locks:
    held = False
    @asynccontextmanager
    async def hold(self, guard):
        assert not self.held
        self.held = True
        try:
            guard()
            yield
        finally:
            self.held = False


class Journal:
    def __init__(self):
        self.rows = {}
    async def load(self, key):
        return self.rows.get(key)
    async def active(self):
        return tuple(row for row in self.rows.values() if row.status not in ('applied', 'rolled_back'))
    async def create(self, row):
        assert row.operation_id not in self.rows
        self.rows[row.operation_id] = row
    async def save(self, row):
        self.rows[row.operation_id] = row


class Observer:
    """Runtime fake has state separate from files; disk alone cannot prove AFTER."""
    def __init__(self):
        self.runtime = transaction.Position.BEFORE
        self.inventory_calls = []
    async def position(self, change):
        await asyncio.sleep(0)
        return self.runtime
    async def verify_inventory(self, plan, *, rollback):
        self.inventory_calls.append(rollback)
        return True


class Regressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        scratch = os.environ.get('RENAMER_TEST_SCRATCH', tempfile.gettempdir())
        self.folder = tempfile.TemporaryDirectory(prefix='yaml-transaction-', dir=scratch)
        self.root = Path(self.folder.name)
        (self.root / '.storage').mkdir()
        self.rows = []
        sources = []
        for name in ('one.yaml', 'two.yaml'):
            path = self.root / name
            before, after = b'entity_id: sensor.old\n', b'entity_id: sensor.new\n'
            path.write_bytes(before)
            info = path.stat()
            self.rows.append(module.YAMLFile(name, before.hex(), after.hex(), info.st_dev, info.st_ino))
            sources.append((name, hashlib.sha256(before).hexdigest(), info.st_dev, info.st_ino))
        names = ('one.yaml', 'two.yaml', '.storage')
        entries = tuple(sorted((name, (s := (self.root / name).stat()).st_mode, s.st_dev, s.st_ino) for name in names))
        receipt = module.ValidationReceipt(str(self.root), tuple(sources), (('.', entries, names),),
            tuple((f.path, hashlib.sha256(f.after).hexdigest()) for f in self.rows), ('script',), (), 'unit-fixture')
        self.change = module.PreparedYAML(receipt, tuple(self.rows)).checked()
        self.observer = Observer()
        self.locks = Locks()
        self.reload_count = 0
        self.fail_reload = False
        async def reload(domain, service, data, **kwargs):
            self.reload_count += 1
            if self.fail_reload:
                return  # A native handler can swallow errors and return normally.
            if all((self.root / f.path).read_bytes() == f.after for f in self.rows):
                self.observer.runtime = transaction.Position.AFTER
            elif all((self.root / f.path).read_bytes() == f.before for f in self.rows):
                self.observer.runtime = transaction.Position.BEFORE
            else:
                self.observer.runtime = transaction.Position.UNKNOWN
        async def executor(function, *args):
            return await asyncio.to_thread(function, *args)
        self.hass = SimpleNamespace(config=SimpleNamespace(config_dir=str(self.root)),
            services=SimpleNamespace(has_service=lambda *args: True, async_call=reload),
            async_add_executor_job=executor)
        self.adapter = module.YAMLTransactionAdapter(self.hass, lambda ref: self.change,
            self.locks, self.observer, context=object(), runtime_version='unit-fixture')
        self.step = transaction.Step('yaml_graph', 'yaml', self.change.identity,
            self.change.before_revision, self.change.after_revision, 'server-id:digest')
        self.plan = transaction.Plan('unit', ('yaml',), ('yaml',), (self.step,),
            'unit-fixture-not-native-evidence', ('yaml',))
        self.journal = Journal()
        self.coordinator = transaction.Coordinator({'yaml': self.adapter}, self.journal,
            lambda: None, required_scopes=('yaml',))

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def test_typed_codec_and_rejection(self):
        encoded = prepared.encode_change(self.change)
        self.assertEqual(prepared.decode_change(encoded), self.change)
        for mutate in (
            lambda d: d['data'].update(extra=True),
            lambda d: d['data']['files'][0].update(device=True),
            lambda d: d['data']['files'][0].update(before_hex='00'),
            lambda d: d['data']['receipt'].update(changed_domains=['template']),
            lambda d: d['data']['receipt'].update(unsupported_domains=['template']),
            lambda d: d['data']['files'][0].update(path='../configuration.yaml'),
            lambda d: d['data']['receipt']['source_directories'][0][1].append(['bad/path', 1, 1, 1]),
        ):
            malformed = deepcopy(encoded)
            mutate(malformed)
            with self.assertRaises(ValueError):
                prepared.decode_change(malformed)

    async def test_mixed_template_codec_requires_complete_distinct_native_seals(self):
        domains = ('automation', 'script', 'template')
        seals = tuple((domain, hashlib.sha256((domain + ':before').encode()).hexdigest(),
                      hashlib.sha256((domain + ':after').encode()).hexdigest()) for domain in domains)
        change = replace(self.change, receipt=replace(self.change.receipt,
            changed_domains=domains, runtime_expected=seals)).checked()
        encoded = prepared.encode_change(change)
        self.assertEqual(prepared.decode_change(encoded), change)
        for seal in seals:
            self.assertNotEqual(seal[1], seal[2])
        for label, mutate in (
            ('missing', lambda r: r['runtime_expected'].pop()),
            ('duplicate', lambda r: r['runtime_expected'].append(r['runtime_expected'][0])),
            ('equal', lambda r: r['runtime_expected'][0].__setitem__(2, r['runtime_expected'][0][1])),
            ('null', lambda r: r['runtime_expected'][0].__setitem__(1, None)),
            ('extra', lambda r: r['runtime_expected'].append(['scene', 'a' * 64, 'b' * 64])),
            ('unordered', lambda r: r['runtime_expected'].reverse()),
            ('unsupported', lambda r: r['changed_domains'].append('scene')),
            ('duplicate_domain', lambda r: r['changed_domains'].append('template')),
        ):
            with self.subTest(label=label):
                malformed = deepcopy(encoded)
                mutate(malformed['data']['receipt'])
                with self.assertRaises(ValueError):
                    prepared.decode_change(malformed)
        # Existing ordinary and Template-only envelopes remain their same payloads.
        old = prepared.encode_change(self.change)
        self.assertEqual(prepared.encode_change(prepared.decode_change(old)), old)
        template = replace(self.change, receipt=replace(self.change.receipt,
            changed_domains=('template',), runtime_expected=(seals[-1],))).checked()
        old_template = prepared.encode_change(template)
        self.assertEqual(prepared.encode_change(prepared.decode_change(old_template)), old_template)

    async def test_runtime_upgrade_invalidates_old_native_validation(self):
        self.adapter.runtime_version = 'different-native-runtime'
        with self.assertRaises(transaction.Refused):
            await self.adapter.apply(self.step, lambda: None)
        self.assertEqual(self.reload_count, 0)
        for row in self.rows:
            self.assertEqual((self.root / row.path).read_bytes(), row.before)

    async def test_graph_edit_after_executor_fence_is_caught_before_first_write(self):
        fences = []
        original = module._assert_sources
        def observed(*args):
            result = original(*args)
            fences.append(True)
            return result
        injected = False
        async def executor(function, *args):
            nonlocal injected
            result = await asyncio.to_thread(function, *args)
            if len(fences) == 2 and not injected:
                injected = True
                (self.root / self.rows[1].path).write_bytes(b'foreign between executor calls\n')
            return result
        self.hass.async_add_executor_job = executor
        with patch.object(module, '_assert_sources', observed):
            with self.assertRaises(transaction.Refused):
                await self.adapter.apply(self.step, lambda: None)
        self.assertTrue(injected)
        self.assertEqual((self.root / self.rows[0].path).read_bytes(), self.rows[0].before)
        self.assertEqual((self.root / self.rows[1].path).read_bytes(), b'foreign between executor calls\n')
        self.assertEqual(self.reload_count, 0)

    async def test_apply_and_cas_rollback_with_new_inodes(self):
        original_fence = module._assert_sources
        original_effect = module.apply_file_patch
        failures, effect_failures = [], []
        def observed_effect(*args):
            try:
                return original_effect(*args)
            except Exception as error:
                path = self.root / args[1].path
                info = path.stat()
                effect_failures.append((str(error), args[1].path,
                    (args[2].device, args[2].inode), (info.st_dev, info.st_ino),
                    path.read_bytes() == args[1].proposed_bytes))
                raise
        def observed_fence(change, after_paths, restored=False):
            read_identities = {}
            original_read = module._Reader.read
            def observed_read(reader, path):
                raw, info = original_read(reader, path)
                read_identities[path] = (info.st_mode, info.st_dev, info.st_ino)
                return raw, info
            try:
                with patch.object(module._Reader, 'read', observed_read):
                    return original_fence(change, after_paths, restored)
            except Exception as error:
                files = []
                for row in self.rows:
                    path = self.root / row.path
                    info = path.stat()
                    raw = path.read_bytes()
                    files.append((row.path, (row.device, row.inode),
                                  (info.st_dev, info.st_ino), raw == row.before, raw == row.after))
                directories = []
                for path, expected, names in change.receipt.source_directories:
                    parent = self.root if path == '.' else self.root / path
                    actual = tuple(sorted((p.name, (s := p.stat()).st_mode, s.st_dev, s.st_ino)
                        for p in parent.iterdir() if names is None or p.name in names))
                    directories.append((path, expected, actual))
                failures.append((str(error), sorted(after_paths), restored, read_identities, files, directories))
                raise
        with patch.object(module, '_assert_sources', observed_fence), patch.object(module, 'apply_file_patch', observed_effect):
            result = await self.coordinator.apply(self.plan, self.plan.fingerprint, 'op')
        self.assertEqual(result.status, 'applied', (result.reason, failures, effect_failures))
        self.assertEqual(self.reload_count, 1)
        self.assertEqual(self.observer.inventory_calls, [False])
        with patch.object(module, '_assert_sources', observed_fence), patch.object(module, 'apply_file_patch', observed_effect):
            result = await self.coordinator.rollback('op')
        self.assertEqual(result.status, 'rolled_back', (result.reason, failures, effect_failures))
        self.assertEqual(self.observer.inventory_calls, [False, True])
        self.assertEqual(self.reload_count, 2)
        for row in self.rows:
            self.assertEqual((self.root / row.path).read_bytes(), row.before)

    async def test_apply_and_compensate_return_exact_actual_file_call_results(self):
        original = module.apply_file_patch
        calls = []
        def captured(*args):
            path = self.root / args[1].path
            before, source = path.read_bytes(), path.stat()
            result = original(*args)
            # Retain this fixture's replaced inode across the next call. The
            # mounted test filesystem can refresh lookup identity when the last
            # descriptor closes; a call stamp does not authorize that drift.
            result_pin = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            self.addCleanup(os.close, result_pin)
            after, final = path.read_bytes(), path.stat()
            self.assertEqual(result['before_sha256'], hashlib.sha256(before).hexdigest())
            self.assertEqual(result['after_sha256'], hashlib.sha256(after).hexdigest())
            self.assertEqual(result['before_identity'], (source.st_dev, source.st_ino, source.st_mode))
            self.assertEqual(result['after_identity'], (final.st_dev, final.st_ino, final.st_mode))
            self.assertEqual((result['before_uid'], result['before_gid']), (source.st_uid, source.st_gid))
            self.assertEqual((result['after_uid'], result['after_gid']), (final.st_uid, final.st_gid))
            self.assertEqual(result['operation_id'], args[3])
            self.assertEqual((self.root / result['backup']).read_bytes(), before)
            calls.append(result)
            return result
        with patch.object(module, 'apply_file_patch', captured):
            forward = await self.adapter.apply(self.step, lambda: None)
            self.assertEqual([r['path'] for r in forward], [f.path for f in self.rows])
            self.assertEqual(len(forward), 2)
            for actual, returned in zip(calls, forward):
                self.assertIs(actual, returned)  # No reconstructed later snapshot.
            reverse = await self.adapter.compensate(self.step, lambda: None)
            self.assertEqual([r['path'] for r in reverse], [f.path for f in reversed(self.rows)])
            self.assertEqual(len(reverse), 2)
            for actual, returned in zip(calls[2:], reverse):
                self.assertIs(actual, returned)
        by_path = {r['path']: r for r in forward}
        for returned in reverse:
            previous = by_path[returned['path']]
            self.assertEqual(returned['before_identity'], previous['after_identity'],
                [(r['path'], r['before_identity'], r['after_identity']) for r in calls])
            self.assertEqual(returned['before_sha256'], previous['after_sha256'])
            self.assertEqual(returned['after_sha256'], previous['before_sha256'])
        self.assertEqual(self.reload_count, 2)

    async def test_lost_file_call_ack_does_not_return_partial_or_fabricated_results(self):
        original = module.apply_file_patch
        calls, returned = [], []
        def lost_ack(*args):
            result = original(*args)
            calls.append(result)
            raise OSError('file call acknowledgement lost after actual replacement')
        with patch.object(module, 'apply_file_patch', lost_ack):
            with self.assertRaisesRegex(OSError, 'acknowledgement lost'):
                returned.append(await self.adapter.apply(self.step, lambda: None))
        self.assertEqual(returned, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.reload_count, 0)
        self.assertFalse(self.locks.held)
        self.assertEqual((self.root / self.rows[0].path).read_bytes(), self.rows[0].after)
        self.assertEqual((self.root / self.rows[1].path).read_bytes(), self.rows[1].before)

    async def test_full_source_preflight_and_foreign_rollback_edit(self):
        (self.root / self.rows[1].path).write_bytes(b'foreign before\n')
        with self.assertRaises(transaction.Refused):
            await self.coordinator.apply(self.plan, self.plan.fingerprint, 'stale')
        self.assertEqual((self.root / self.rows[0].path).read_bytes(), self.rows[0].before)
        self.assertEqual(self.journal.rows, {})
        (self.root / self.rows[1].path).write_bytes(self.rows[1].before)
        result = await self.coordinator.apply(self.plan, self.plan.fingerprint, 'op')
        self.assertEqual(result.status, 'applied')
        (self.root / self.rows[1].path).write_bytes(b'foreign after\n')
        result = await self.coordinator.rollback('op')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual((self.root / self.rows[1].path).read_bytes(), b'foreign after\n')
        self.assertEqual((self.root / self.rows[0].path).read_bytes(), self.rows[0].after)

    async def test_foreign_permission_change_after_apply_is_not_compensated(self):
        result = await self.coordinator.apply(self.plan, self.plan.fingerprint, 'op')
        self.assertEqual(result.status, 'applied')
        path = self.root / self.rows[0].path
        path.chmod(0o640)
        self.assertEqual(await self.adapter.inspect(self.step), transaction.Position.UNKNOWN)
        result = await self.coordinator.rollback('op')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(path.read_bytes(), self.rows[0].after)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    async def test_full_include_membership_rejects_new_foreign_file(self):
        from dataclasses import replace
        path, entries, _ = self.change.receipt.source_directories[0]
        receipt = replace(self.change.receipt, source_directories=((path, entries, None),))
        change = replace(self.change, receipt=receipt)
        (self.root / 'foreign.yaml').write_bytes(b'foreign config\n')
        with self.assertRaises(transaction.Refused):
            module._assert_sources(change, set())

    async def test_partial_write_is_unknown_no_replay(self):
        original = module.apply_file_patch
        calls = []
        def interrupted(*args):
            calls.append(args[1].path)
            result = original(*args)
            if len(calls) == 1:
                (self.root / self.rows[1].path).write_bytes(b'foreign concurrent edit\n')
            return result
        with patch.object(module, 'apply_file_patch', interrupted):
            result = await self.coordinator.apply(self.plan, self.plan.fingerprint, 'op')
            self.assertEqual(result.status, 'unknown')
            self.assertEqual(result.phases, ('intent',))
            self.assertEqual(self.reload_count, 0)
            again = await self.coordinator.apply(self.plan, self.plan.fingerprint, 'op')
            self.assertEqual(again, result)
            self.assertEqual(len(calls), 1)
            recovered = await self.coordinator.reconcile('op')
            self.assertEqual(recovered.status, 'unknown')
            with self.assertRaises(transaction.Refused):
                await self.coordinator.rollback('op')
        self.assertEqual((self.root / self.rows[1].path).read_bytes(), b'foreign concurrent edit\n')

    async def test_reload_ack_without_native_observation_is_unknown(self):
        self.fail_reload = True
        result = await self.coordinator.apply(self.plan, self.plan.fingerprint, 'op')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(result.phases, ('intent',))
        self.assertEqual(await self.adapter.inspect(self.step), transaction.Position.UNKNOWN)
        self.assertEqual(self.observer.inventory_calls, [])
        result = await self.coordinator.reconcile('op')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(self.reload_count, 1)

    async def test_cancelled_executor_drains_before_unlock(self):
        entered, release = threading.Event(), threading.Event()
        original = module.apply_file_patch
        def blocked(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError('unit release missing')
            return original(*args)
        with patch.object(module, 'apply_file_patch', blocked):
            task = asyncio.create_task(self.adapter.apply(self.step, lambda: None))
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            self.assertTrue(entered.is_set())
            task.cancel()
            await asyncio.sleep(.01)
            self.assertFalse(task.done())
            self.assertTrue(self.locks.held)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(self.locks.held)
            self.assertEqual(self.reload_count, 0)

    async def test_guard_revoked_after_executor_read_prevents_effect(self):
        authorized = True
        async def revoked(function, *args):
            nonlocal authorized
            result = await asyncio.to_thread(function, *args)
            authorized = False
            return result
        self.hass.async_add_executor_job = revoked
        def guard():
            if not authorized:
                raise transaction.Refused('revoked')
        with self.assertRaises(transaction.Refused):
            await self.adapter.apply(self.step, guard)
        self.assertFalse(self.locks.held)
        self.assertEqual(self.reload_count, 0)
        self.assertEqual(list((self.root / '.storage').iterdir()), [])
        for row in self.rows:
            self.assertEqual((self.root / row.path).read_bytes(), row.before)


if __name__ == '__main__':
    unittest.main()
