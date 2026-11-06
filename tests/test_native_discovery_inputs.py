"""Discovery snapshots are read-only, full native observations, never receipts.

Uses existing synthetic fixtures. Owner runs native cases on current/minimum HA
from the frozen integrated candidate through the FIFO runner.
"""
import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib
import json
import os
from pathlib import Path
import stat
from threading import Event
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixture_module

subject = importlib.import_module(fixture_module.PACKAGE + '.native_discovery_inputs')


class DiscoveryFileTests(unittest.TestCase):
    def setUp(self):
        fixture = fixture_module.BaselineFileTests(
            'test_raw_audit_order_unknowns_mode_and_codec_are_immutable')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.documents, self.receipt = fixture.root, fixture.documents, fixture.receipt

    def pinned(self):
        return subject._DiscoveryPinned(
            subject._SourceLocation(self.receipt.version, str(self.root)),
            tuple(map(fixture_module.baseline._json, self.documents)))

    def test_discovery_preserves_all_foreign_audit_order_options_and_metadata(self):
        pinned = self.pinned()
        try:
            observed = pinned.capture()
            self.assertFalse(hasattr(observed, 'receipt'))
            self.assertFalse(hasattr(observed, 'receipt_json'))
            for item, document in zip(observed.files, self.documents, strict=True):
                path = self.root / '.storage' / item.key
                info = path.stat()
                self.assertEqual(item.raw, path.read_bytes())
                self.assertEqual(json.loads(item.manager_json), document)
                self.assertEqual(item.identity, (info.st_dev, info.st_ino, info.st_mode))
                self.assertEqual((item.uid, item.gid), (info.st_uid, info.st_gid))
                with self.assertRaises(FrozenInstanceError):
                    item.uid = 0
        finally:
            pinned.close()
        self.assertIsNone(pinned.reader)

    def test_discovery_full_foreign_changes_refuse_even_without_yaml_receipt(self):
        original = deepcopy(self.documents)
        for target in ('audit', 'unknown', 'order', 'deleted', 'option', 'subentry'):
            changed = deepcopy(original)
            if target == 'audit': changed[0]['entities'][1]['modified_at'] = 'foreign'
            elif target == 'unknown': changed[0]['entities'][0]['extra'] = True
            elif target == 'order': changed[0]['entities'].reverse()
            elif target == 'deleted': changed[1]['deleted_devices'].clear()
            elif target == 'option': changed[2]['entries'][0]['options'].clear()
            else: changed[2]['entries'][0]['subentries'].clear()
            with self.subTest(target=target):
                pinned = subject._DiscoveryPinned(
                    subject._SourceLocation(self.receipt.version, str(self.root)),
                    tuple(map(fixture_module.baseline._json, changed)))
                try:
                    with self.assertRaises(ValueError): pinned.capture()
                finally:
                    pinned.close()

    def test_discovery_missing_symlink_and_envelope_refuse(self):
        path = self.root / '.storage' / fixture_module.KEYS[0]
        saved = path.with_name('quarantined-entity')
        path.rename(saved)
        for kind in ('missing', 'symlink', 'envelope', 'schema'):
            with self.subTest(kind=kind):
                if kind == 'symlink': path.symlink_to(saved.name)
                elif kind in ('envelope', 'schema'):
                    data = json.loads(saved.read_bytes())
                    if kind == 'envelope': data['extra'] = 'foreign'
                    else: data['data']['extra'] = []
                    path.write_text(json.dumps(data))
                pinned = self.pinned()
                try:
                    with self.assertRaises((ValueError, OSError)): pinned.capture()
                    self.assertIsNone(pinned.reader)
                finally:
                    pinned.close()
                    if path.is_symlink() or path.exists():
                        path.rename(self.root / ('quarantined-' + kind))

    def test_discovery_file_mode_inode_and_storage_replacement_during_capture_refuse(self):
        for kind in ('mode', 'inode', 'storage'):
            with self.subTest(kind=kind):
                pinned = self.pinned()
                try:
                    pinned.capture()
                    path = self.root / '.storage' / fixture_module.KEYS[0]
                    if kind == 'mode': path.chmod(0o600)
                    elif kind == 'inode':
                        replacement = self.root / 'replacement'
                        replacement.write_bytes(path.read_bytes())
                        replacement.chmod(stat.S_IMODE(path.stat().st_mode))
                        os.replace(replacement, path)
                    else:
                        (self.root / '.storage').rename(self.root / 'quarantined-storage')
                        (self.root / '.storage').mkdir()
                    with self.assertRaises(ValueError): pinned.verify()
                finally:
                    pinned.close()
                if kind == 'mode': path.chmod(0o640)

    def test_discovery_source_swap_between_pin_and_reader_read_refuses(self):
        original = subject._Reader.read
        replaced = False
        def read(reader, path):
            nonlocal replaced
            if not replaced:
                replaced = True
                file = self.root / path
                replacement = self.root / 'replacement'
                replacement.write_bytes(file.read_bytes())
                replacement.chmod(stat.S_IMODE(file.stat().st_mode))
                os.replace(replacement, file)
            return original(reader, path)
        pinned = self.pinned()
        try:
            with patch.object(subject._Reader, 'read', read):
                with self.assertRaises(ValueError): pinned.capture()
        finally:
            pinned.close()

    def test_discovery_limits_and_root_symlink_refuse(self):
        for limit in ('MAX_FILE_BYTES', 'MAX_TOTAL_BYTES'):
            pinned = self.pinned()
            try:
                with patch.object(subject, limit, 2):
                    with self.assertRaises(ValueError): pinned.capture()
            finally:
                pinned.close()
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        pinned = subject._DiscoveryPinned(
            subject._SourceLocation(self.receipt.version, str(alias)),
            tuple(map(fixture_module.baseline._json, self.documents)))
        try:
            with self.assertRaises(OSError): pinned.capture()
        finally:
            pinned.close()


@unittest.skipUnless(fixture_module.HAS_HA, 'native HA dependencies required')
class DiscoveryNativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixture_module.NativeBaselineTests(
            'test_actual_native_serialization_and_store_full_baseline')
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)

    async def observe(self, guard=None):
        return await subject.async_observe_discovery_inputs(self.fixture.hass,
            guard=guard or self.fixture.guard)

    async def test_actual_native_without_validation_or_native_effects(self):
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        from homeassistant.helpers.storage import Store
        # A foreign YAML edit invalidates the old fixture receipt. Discovery
        # has no YAML receipt yet and must still observe coherent native inputs.
        (self.fixture.root / 'configuration.yaml').write_bytes(b'script: {changed: true}\n')
        def forbidden(*args, **kwargs):
            raise AssertionError('discovery attempted a native effect/factory/validation')
        with patch.object(Store, 'async_load', forbidden), \
             patch.object(Store, 'async_save', forbidden), \
             patch.object(er, 'async_get', forbidden), patch.object(dr, 'async_get', forbidden), \
             patch.object(fixture_module.validation, 'assert_validation_current', forbidden):
            observed = await self.observe()
        self.assertEqual(observed.version, self.fixture.version)
        self.assertEqual(observed.config_root, str(self.fixture.root))
        for item, manager in zip(observed.files, self.fixture.managers, strict=True):
            self.assertEqual(item.raw, Path(manager._store.path).read_bytes())
            self.assertEqual(json.loads(item.raw)['data'], json.loads(item.manager_json))
        self.assertFalse(hasattr(observed, 'receipt'))

    async def test_pending_store_refuses_without_settling(self):
        for manager in self.fixture.managers:
            store = manager._store
            for name in ('_data', '_delay_handle', '_unsub_final_write_listener', '_load_future'):
                old = getattr(store, name)
                value = object()
                setattr(store, name, value)
                try:
                    with self.subTest(key=store.key, name=name):
                        with self.assertRaises(subject.DiscoveryNativeInputsError):
                            await self.observe()
                        self.assertIs(getattr(store, name), value)
                finally:
                    setattr(store, name, old)
            await store._write_lock.acquire()
            try:
                with self.assertRaises(subject.DiscoveryNativeInputsError): await self.observe()
            finally:
                store._write_lock.release()

    async def test_runtime_version_root_and_native_identity_changes_refuse(self):
        import homeassistant.const as const
        with patch.object(const, '__version__', '2026.10.3'):
            with self.assertRaises(subject.DiscoveryNativeInputsError): await self.observe()
        original = self.fixture.hass.async_add_executor_job
        from homeassistant.helpers import entity_registry as er
        old = self.fixture.hass.data[er.DATA_REGISTRY]
        async def executor(function, *args):
            result = await original(function, *args)
            self.fixture.hass.data[er.DATA_REGISTRY] = object()
            return result
        try:
            with patch.object(self.fixture.hass, 'async_add_executor_job', executor):
                with self.assertRaises(subject.DiscoveryNativeInputsError): await self.observe()
        finally:
            self.fixture.hass.data[er.DATA_REGISTRY] = old
        root = self.fixture.hass.config.config_dir
        self.fixture.hass.config.config_dir = root + '/'
        try:
            with self.assertRaises(subject.DiscoveryNativeInputsError): await self.observe()
        finally:
            self.fixture.hass.config.config_dir = root

    async def test_exact_store_schema_and_pending_state_after_executor_refuse(self):
        store = self.fixture.hass.config_entries._store
        old_minor = store.minor_version
        store.minor_version += 1
        try:
            with self.assertRaises(subject.DiscoveryNativeInputsError): await self.observe()
        finally:
            store.minor_version = old_minor
        original = self.fixture.hass.async_add_executor_job
        async def executor(function, *args):
            result = await original(function, *args)
            store._data = {'pending': True}
            return result
        try:
            with patch.object(self.fixture.hass, 'async_add_executor_job', executor):
                with self.assertRaises(subject.DiscoveryNativeInputsError): await self.observe()
        finally:
            store._data = None

    async def test_permission_and_guard_failures_are_sanitized(self):
        with patch.object(subject._DiscoveryPinned, 'capture',
                          side_effect=PermissionError('private secret source path')):
            with self.assertRaises(PermissionError) as context: await self.observe()
        self.assertEqual(str(context.exception), 'native_discovery_inputs_permission_denied')
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 3: raise RuntimeError('private guard information')
        with self.assertRaises(subject.DiscoveryNativeInputsError) as context:
            await self.observe(guard)
        self.assertEqual(str(context.exception), 'native_discovery_inputs_rejected')

    async def test_active_executor_cancellation_drains_before_fd_release(self):
        entered, release = Event(), Event()
        descriptors = []
        original = self.fixture.hass.async_add_executor_job
        async def executor(function, *args):
            def run():
                result = function(*args)
                if function.__name__ == 'capture':
                    descriptors.extend(fd for _, fd, _ in function.__self__.handles)
                    entered.set()
                    if not release.wait(10): raise AssertionError('test worker release timeout')
                    for fd in descriptors: os.fstat(fd)
                return result
            return await original(run)
        task = None
        with patch.object(self.fixture.hass, 'async_add_executor_job', executor):
            try:
                task = asyncio.create_task(self.observe())
                async def started():
                    while not entered.is_set(): await asyncio.sleep(0.01)
                await asyncio.wait_for(started(), timeout=10)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertEqual(len(descriptors), 3)
                for fd in descriptors: os.fstat(fd)
                release.set()
                with self.assertRaises(asyncio.CancelledError): await task
            finally:
                release.set()
                if task is not None and not task.done():
                    task.cancel()
                    try: await task
                    except asyncio.CancelledError: pass
        for fd in descriptors:
            with self.assertRaises(OSError): os.fstat(fd)


if __name__ == '__main__':
    unittest.main()
