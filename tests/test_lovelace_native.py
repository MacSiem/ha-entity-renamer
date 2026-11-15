"""Isolated native boundary tests; owner must ALSO test real HA manager/Store."""
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.lovelace_native import NativeLovelace, revision
from custom_components.ha_entity_renamer.persistence import PersistenceError


class NativeLovelaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.disk = {}
        self.root = '/Volumes/Samsung_SSD/synthetic-ha-config'
        self.guard_calls = 0
        self.save_calls = 0
        self.fail_save = False
        self.before_read = None
        self.gate = None

        def reader(path, key):
            if self.before_read:
                callback, self.before_read = self.before_read, None
                callback()
            row = self.disk.get(key)
            return deepcopy(row) if row else (None, None)

        async def executor(func, *args):
            await asyncio.sleep(0)
            return func(*args)

        fixture = self

        class Manager:
            mode = 'storage'

            def __init__(self, route, identity, config):
                self.config = None if route is None else {
                    'id': identity, 'url_path': route, 'mode': 'storage',
                    'require_admin': True, 'title': 'Private'}
                self.url_path = route
                key = 'lovelace' if route is None else 'lovelace.' + identity
                self._store = SimpleNamespace(key=key, path=fixture.root + '/.storage/' + key,
                    version=1, minor_version=1, _write_lock=asyncio.Lock(),
                    _data=None, _delay_handle=None, _unsub_final_write_listener=None)
                self._data = {'config': deepcopy(config)}
                if config is not None: fixture.disk[key] = ({'config': deepcopy(config)}, (1, 1, revision(config)))

            async def async_load(self, force):
                raise AssertionError('Read should not trust native load while initialized')

            async def async_save(self, config):
                fixture.save_calls += 1
                self._data['config'] = deepcopy(config)
                if fixture.gate: await fixture.gate.wait()
                if not fixture.fail_save:
                    fixture.disk[self._store.key] = ({'config': deepcopy(config)},
                                                      (1, 2, revision(config)))

        self.config = {'views': [{'cards': [{'type': 'entity', 'entity': 'sensor.old'}]}]}
        self.default = Manager(None, '', None)
        self.named = Manager('private-board', 'immutable-id', self.config)
        self.managers = {None: self.default, 'private-board': self.named}
        self.disk['lovelace_dashboards'] = ({'items': [self.named.config]}, (1, 3, 'metadata'))
        self.adapter = object.__new__(NativeLovelace)
        self.adapter.hass = SimpleNamespace(data={'lovelace': SimpleNamespace(dashboards=self.managers)},
            config=SimpleNamespace(path=lambda *parts: str(Path(self.root).joinpath(*parts))),
            async_add_executor_job=executor)
        self.adapter._data_key = 'lovelace'
        self.adapter._storage_class = Manager
        self.adapter._reader = reader
        self.adapter._lock = asyncio.Lock()
        self.adapter._failed = False
        self.descriptor = self.adapter._descriptor('private-board', self.named)

    def guard(self): self.guard_calls += 1

    async def prepared(self):
        snapshot, plan = await self.adapter.plan(self.descriptor, {'sensor.old': 'sensor.new'})
        return self.adapter.prepare(snapshot, plan)

    async def test_enumerates_default_and_admin_only(self):
        storage, yaml = await self.adapter.enumerate()
        self.assertEqual({None, 'private-board'}, {item.route for item in storage})
        self.assertFalse(yaml)
        self.assertEqual('lovelace.immutable-id', self.descriptor.key)

    async def test_missing_registered_collection_entry_blocks(self):
        self.disk['lovelace_dashboards'] = ({'items': []}, (1, 3, 'changed'))
        with self.assertRaises(PersistenceError): await self.adapter.enumerate()

    async def test_cache_disagreement_blocks(self):
        self.named._data['config']['views'] = []
        with self.assertRaises(PersistenceError): await self.adapter.read(self.descriptor)

    async def test_guarded_native_save_independent_readback(self):
        prepared = await self.prepared()
        await self.adapter.write(prepared, self.guard)
        self.assertEqual(1, self.save_calls)
        self.assertTrue(await self.adapter.verify(self.descriptor, prepared.after_revision))
        self.assertGreaterEqual(self.guard_calls, 4)

    async def test_logged_not_raised_native_failure_is_unknown(self):
        prepared = await self.prepared()
        self.fail_save = True
        with self.assertRaises(PersistenceError): await self.adapter.write(prepared, self.guard)
        self.assertTrue(self.adapter._failed)
        self.assertEqual(1, self.save_calls)
        with self.assertRaises(PersistenceError): await self.adapter.write(prepared, self.guard)
        self.assertEqual(1, self.save_calls)

    async def test_disk_revision_conflict_no_write(self):
        prepared = await self.prepared()
        self.disk[self.descriptor.key] = ({'config': self.config}, (1, 9, 'foreign-replace'))
        with self.assertRaises(PersistenceError): await self.adapter.write(prepared, self.guard)
        self.assertEqual(0, self.save_calls)

    async def test_identity_changed_no_write(self):
        prepared = await self.prepared()
        self.named.config['id'] = 'replacement'
        with self.assertRaises(PersistenceError): await self.adapter.write(prepared, self.guard)
        self.assertEqual(0, self.save_calls)

    async def test_native_write_pending_no_read_success(self):
        self.named._store._data = {'queued': True}
        with self.assertRaises(PersistenceError): await self.adapter.read(self.descriptor)

    async def test_runtime_edit_during_disk_read_no_write(self):
        prepared = await self.prepared()
        self.before_read = lambda: self.named._data.update(config={'views': []})
        with self.assertRaises(PersistenceError): await self.adapter.write(prepared, self.guard)
        self.assertEqual(0, self.save_calls)

    async def test_revocation_after_await_no_write(self):
        prepared = await self.prepared()
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls > 1: raise PermissionError('revoked')
        with self.assertRaises(PermissionError): await self.adapter.write(prepared, guard)
        self.assertEqual(0, self.save_calls)

    async def test_cancellation_drains_native_write_before_unlock(self):
        prepared = await self.prepared()
        self.gate = asyncio.Event()
        task = asyncio.create_task(self.adapter.write(prepared, self.guard))
        for _ in range(20):
            await asyncio.sleep(0)
            if self.save_calls: break
        self.assertEqual(1, self.save_calls)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(self.adapter._lock.locked())
        self.gate.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(self.adapter._lock.locked())
        self.assertTrue(await self.adapter.verify(self.descriptor, prepared.after_revision))


if __name__ == '__main__': unittest.main()
