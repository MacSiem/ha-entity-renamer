"""HA 2026.10.2 Lovelace bridge; only native manager writes configuration.

Private native fields are deliberately version-pinned. No generic Store cache
reload is evidence: Store/LovelaceStorage both have independent memory caches.
This is guarded compare/save on the HA event loop, not filesystem atomic CAS.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from .persistence import _snapshot, PersistenceError
from .dashboard_plan import plan_dashboard


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def durable_snapshot(path, key, version=1, minor_version=1):
    """Strict independent read, file+directory barrier, then inode/hash readback.

    fsync adds no config bytes. It also catches unsupported/erroring durability
    barriers rather than making a durability promise based on page-cache reads.
    Native Store may log errors without raising; expected content is checked by
    the caller, independently of the native Store object.
    """
    path = Path(path)
    data, identity = _snapshot(path, key, version, minor_version)
    if identity is None:
        return data, identity
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) != identity[:2]:
                raise PersistenceError('lovelace_file_changed')
            os.fsync(fd)
        finally:
            os.close(fd)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        final, final_identity = _snapshot(path, key, version, minor_version)
        if identity != final_identity:
            raise PersistenceError('lovelace_file_changed')
        return final, final_identity
    except OSError as err:
        raise PersistenceError('lovelace_durability_unavailable') from err


@dataclass(frozen=True)
class Dashboard:
    route: str | None
    key: str
    identity: str  # includes immutable storage ID, route and metadata


@dataclass(frozen=True)
class Snapshot:
    dashboard: Dashboard
    config: dict | None  # None is native auto-generated default/no stored config
    revision: str
    file_identity: tuple | None


@dataclass(frozen=True)
class PreparedDashboard:
    before: Snapshot
    after: dict
    after_revision: str


class NativeLovelace:
    """Small integration interface: enumerate/read/plan/write/verify.

    Caller owns one transaction lock and durable prepared backup/journal. This
    instance adds an internal lock, drains cancelled effects and refuses later
    saves after an uncertain write. Reconcile by fresh inspect, never retry.
    External processes editing .storage cannot share an atomic CAS with HA;
    caller must exclude those editors for apply. This limitation is explicit.
    """

    def __init__(self, hass, *, reader=durable_snapshot):
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        from homeassistant.components.lovelace.dashboard import LovelaceStorage
        from homeassistant.const import __version__
        if __version__ != '2026.10.2':
            raise PersistenceError('unsupported_lovelace_runtime')
        self.hass = hass
        self._data_key = LOVELACE_DATA
        self._storage_class = LovelaceStorage
        self._reader = reader
        self._lock = asyncio.Lock()
        self._failed = False

    def _managers(self):
        data = self.hass.data.get(self._data_key)
        if data is None or type(data.dashboards) is not dict:
            raise PersistenceError('lovelace_unavailable')
        return data.dashboards

    def _descriptor(self, route, manager):
        if type(manager) is not self._storage_class:
            raise PersistenceError('unsupported_lovelace_manager')
        store = manager._store
        expected_key = ('lovelace' if route is None else
                        'lovelace.' + manager.config['id'])
        if (store.key != expected_key or manager.url_path != route
                or store.version != 1 or store.minor_version != 1
                or Path(store.path) != Path(self.hass.config.path('.storage', expected_key))):
            raise PersistenceError('lovelace_identity_mismatch')
        return Dashboard(route, expected_key, revision({'route': route, 'key': expected_key,
                                                       'metadata': manager.config}))

    def _manager(self, descriptor):
        manager = self._managers().get(descriptor.route)
        if manager is None or self._descriptor(descriptor.route, manager) != descriptor:
            raise PersistenceError('lovelace_identity_changed')
        return manager

    @staticmethod
    def _settled(manager):
        store = manager._store
        if (store._write_lock.locked() or store._data is not None
                or store._delay_handle is not None
                or store._unsub_final_write_listener is not None):
            raise PersistenceError('lovelace_native_write_pending')

    async def enumerate(self):
        """Enumerate storage including None/admin-only; return YAML routes too.

        Compare registered metadata against independently read collection Store;
        a half-loaded native collection cannot produce a full coverage claim.
        YAML is handed to its source adapter, never silently counted as storage.
        """
        managers = self._managers()
        baseline = tuple((route, id(manager), revision(manager.config))
                         for route, manager in managers.items())
        payload, _ = await self.hass.async_add_executor_job(
            self._reader, Path(self.hass.config.path('.storage', 'lovelace_dashboards')),
            'lovelace_dashboards')
        if baseline != tuple((route, id(manager), revision(manager.config))
                             for route, manager in self._managers().items()):
            raise PersistenceError('lovelace_inventory_changed')
        items = [] if payload is None else payload.get('items')
        if type(items) is not list or any(type(item) is not dict for item in items):
            raise PersistenceError('invalid_lovelace_inventory')
        registered = [manager.config for manager in managers.values()
                      if type(manager) is self._storage_class and manager.config is not None]
        if sorted((revision(item) for item in registered)) != sorted(revision(item) for item in items):
            raise PersistenceError('lovelace_inventory_not_loaded')
        if None not in managers:
            raise PersistenceError('lovelace_default_missing')
        storage, yaml = [], []
        for route, manager in managers.items():
            if manager.mode == 'storage': storage.append(self._descriptor(route, manager))
            elif manager.mode == 'yaml': yaml.append(route)
            else: raise PersistenceError('unknown_lovelace_mode')
        return tuple(storage), tuple(yaml)

    async def read(self, descriptor):
        manager = self._manager(descriptor)
        self._settled(manager)
        initial = deepcopy(manager._data)
        payload, file_identity = await self.hass.async_add_executor_job(
            self._reader, Path(manager._store.path), descriptor.key)
        manager = self._manager(descriptor)
        self._settled(manager)
        if initial != manager._data:
            raise PersistenceError('lovelace_runtime_changed')
        if payload is not None and (type(payload) is not dict or set(payload) != {'config'}):
            raise PersistenceError('invalid_lovelace_payload')
        config = None if payload is None else payload['config']
        if config is not None and type(config) is not dict:
            raise PersistenceError('invalid_lovelace_config')
        # Prime native cache only after strict non-repairing independent read.
        # HA async_load(force=True) alone DOES NOT bypass LovelaceStorage cache.
        if manager._data is None and config is not None:
            await manager.async_load(False)
            self._manager(descriptor)
            self._settled(manager)
        if manager._data is not None and manager._data != {'config': config}:
            raise PersistenceError('lovelace_cache_disk_disagree')
        return Snapshot(descriptor, deepcopy(config), revision(config), file_identity)

    async def plan(self, descriptor, mapping):
        snapshot = await self.read(descriptor)
        if snapshot.config is None:
            return snapshot, None  # native auto-generation has no persisted refs
        return snapshot, plan_dashboard(snapshot.config, mapping)

    def prepare(self, snapshot, plan):
        if plan.markers:
            raise PersistenceError('lovelace_coverage_blocked')
        return PreparedDashboard(snapshot, deepcopy(plan.value), revision(plan.value))

    async def write(self, prepared, guard):
        """Compare immutable identity/revision/file identity; save through manager.

        No writes for same-content plans. Native save mutates cache synchronously
        before its first await when _data exists, checked immediately below.
        A native writer beginning during the disk await is detected by settled
        flags/runtime content/readback; it is never overwritten on a retry.
        """
        async with self._lock:
            guard()
            if self._failed: raise PersistenceError('lovelace_write_uncertain')
            current = await self.read(prepared.before.dashboard)
            guard()
            if (current != prepared.before or current.config is None
                    or revision(prepared.after) != prepared.after_revision):
                raise PersistenceError('lovelace_stale_plan')
            manager = self._manager(current.dashboard)
            self._settled(manager)
            if manager._data != {'config': current.config}:
                raise PersistenceError('lovelace_stale_cache')
            if current.revision == prepared.after_revision: return

            async def effect():
                # create_task yields control: repeat identity/revision/guard at
                # actual entry immediately before native synchronous mutation.
                latest = self._manager(current.dashboard)
                self._settled(latest)
                if latest is not manager or latest._data != {'config': current.config}:
                    raise PersistenceError('lovelace_stale_cache')
                guard()
                await latest.async_save(deepcopy(prepared.after))
                guard()
                actual = await self.read(current.dashboard)
                guard()
                if actual.revision != prepared.after_revision or actual.config != prepared.after:
                    raise PersistenceError('lovelace_write_not_confirmed')

            task = asyncio.create_task(effect())
            cancelled = False
            try:
                while not task.done():
                    try: await asyncio.shield(task)
                    except asyncio.CancelledError: cancelled = True
                task.result()
                if cancelled: raise asyncio.CancelledError
            except BaseException:
                self._failed = True
                raise

    async def verify(self, descriptor, expected_revision):
        async with self._lock:
            snapshot = await self.read(descriptor)
            return snapshot.revision == expected_revision
