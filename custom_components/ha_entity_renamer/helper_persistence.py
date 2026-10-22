"""Native ConfigEntries durability boundary, independent of helper activation.

ConfigEntries schedules delayed saves and Store can swallow write failures.
Flush through that manager's Store, then independently compare the entire native
document. No editing .storage and no claim that durable options prove activation.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .helper_native import read_entry
from .helper_plan import snapshot_equal
from .lovelace_native import durable_snapshot
from .persistence import PersistenceError


class NativeHelperPersistence:
    def __init__(self, hass):
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.const import __version__
        if __version__ != '2026.10.2' or type(hass.config_entries) is not ConfigEntries:
            raise PersistenceError('unsupported_config_entries_runtime')
        self.hass = hass
        self.manager = hass.config_entries
        self.store = self.manager._store
        if (self.store.key != 'core.config_entries' or self.store.version != 1
                or self.store.minor_version != 5
                or Path(self.store.path) != Path(hass.config.path('.storage', self.store.key))):
            raise PersistenceError('config_entries_store_identity')
        self.lock = asyncio.Lock()
        self.failed = False

    def _identity(self):
        if self.hass.config_entries is not self.manager or self.manager._store is not self.store:
            raise PersistenceError('config_entries_manager_changed')

    def document(self):
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        self._identity()
        return json.loads(_orjson_bytes_default_encoder(self.manager._data_to_save()))

    def _settled(self):
        self._identity()
        if (self.store._write_lock.locked() or self.store._data is not None
                or self.store._delay_handle is not None
                or self.store._unsub_final_write_listener is not None):
            raise PersistenceError('config_entries_write_pending')

    async def read(self):
        self._settled()
        initial = self.document()
        stored, identity = await self.hass.async_add_executor_job(
            durable_snapshot, Path(self.store.path), self.store.key, 1, 5)
        self._settled()
        if identity is None or not snapshot_equal(stored, initial) or not snapshot_equal(self.document(), initial):
            raise PersistenceError('config_entries_cache_disk_disagree')
        return stored

    async def inspect_current(self, expected_entry):
        """Read-only proof, or explicit native delayed-save observation.

        False means only that the manager has a delayed save waiting. In-flight
        writes, poisoned runtime and disk disagreement still raise, never become
        authorization to overwrite. Caller independently verifies activation.
        """
        self._identity()
        if self.failed or self.store._write_lock.locked():
            raise PersistenceError('config_entries_write_uncertain')
        entry_id = expected_entry['identity']['entry_id']
        if not snapshot_equal(read_entry(self.hass, entry_id), expected_entry):
            raise PersistenceError('helper_snapshot_conflict')
        if self.store._delay_handle is not None:
            return False
        await self.read()
        if not snapshot_equal(read_entry(self.hass, entry_id), expected_entry):
            raise PersistenceError('helper_snapshot_conflict')
        return True

    async def flush(self, expected_entry, guard):
        """Flush after guarded native apply/listeners, not as a mutation shortcut.

        The transaction intent and runtime verification precede this call. The
        exact affected entry must still match. Unrelated entries are serialized
        from the live manager, never from a stale planning snapshot. Any change
        during the await invalidates the whole-document durability proof.
        """
        async with self.lock:
            guard()
            if self.failed:
                raise PersistenceError('config_entries_write_uncertain')
            self._identity()
            entry_id = expected_entry['identity']['entry_id']
            if not snapshot_equal(read_entry(self.hass, entry_id), expected_entry):
                raise PersistenceError('helper_snapshot_conflict')
            if self.store._write_lock.locked():
                raise PersistenceError('config_entries_write_in_flight')
            baseline = self.document()

            async def effect():
                guard()
                self._identity()
                if not snapshot_equal(self.document(), baseline) or self.store._write_lock.locked():
                    raise PersistenceError('config_entries_changed_before_save')
                await self.store.async_save(self.manager._data_to_save())
                guard()
                actual = await self.read()
                guard()
                if not snapshot_equal(actual, baseline) or not snapshot_equal(read_entry(self.hass, entry_id), expected_entry):
                    raise PersistenceError('config_entries_changed_during_save')
                return actual

            task = asyncio.create_task(effect())
            cancelled = False
            try:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                result = task.result()
                if cancelled:
                    raise asyncio.CancelledError
                return result
            except BaseException:
                self.failed = True
                raise
