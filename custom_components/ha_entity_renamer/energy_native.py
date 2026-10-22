"""Native Energy manager updates with schema and independent durable readback.

The manager retains its listeners and generated-sensor semantics. Unexpected
native generation of a different identity blocks BEFORE any update. Historical
continuity remains the separate Recorder adapter's responsibility.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from .energy_plan import plan_energy
from .lovelace_native import durable_snapshot, revision
from .persistence import PersistenceError


@dataclass(frozen=True)
class EnergySnapshot:
    value: dict | None
    revision: str
    file_identity: tuple | None


@dataclass(frozen=True)
class PreparedEnergy:
    before: EnergySnapshot
    after: dict
    after_revision: str


class NativeEnergy:
    def __init__(self, hass, manager):
        from homeassistant.components.energy.data import EnergyManager
        from homeassistant.const import __version__
        if __version__ not in ('2025.6.0', '2026.10.2') or type(manager) is not EnergyManager:
            raise PersistenceError('unsupported_energy_runtime')
        self.hass, self.manager = hass, manager
        self._legacy = __version__ == '2025.6.0'
        self._minor_version = 1 if self._legacy else 3
        self._lock = asyncio.Lock()
        self._failed = False
        store = manager._store
        if (store.key != 'energy' or store.version != 1 or store.minor_version != self._minor_version
                or Path(store.path) != Path(hass.config.path('.storage', 'energy'))):
            raise PersistenceError('energy_store_identity')
        self._store = store

    def _settled(self):
        store = self.manager._store
        if store is not self._store:
            raise PersistenceError('energy_manager_identity_changed')
        if (store._write_lock.locked() or store._data is not None
                or store._delay_handle is not None or store._unsub_final_write_listener is not None):
            raise PersistenceError('energy_native_write_pending')

    async def read(self):
        self._settled()
        initial = deepcopy(self.manager.data)
        data, identity = await self.hass.async_add_executor_job(
            durable_snapshot, Path(self._store.path), 'energy', 1, self._minor_version)
        self._settled()
        if initial != self.manager.data or data != initial:
            raise PersistenceError('energy_cache_disk_disagree')
        return EnergySnapshot(data, revision(data), identity)

    async def plan(self, mapping):
        before = await self.read()
        if before.value is None:
            return before, None
        return before, plan_energy(before.value, mapping)

    def prepare(self, before, plan):
        from homeassistant.components.energy.data import DEVICE_CONSUMPTION_SCHEMA, ENERGY_SOURCE_SCHEMA
        if plan.markers or before.value is None:
            raise PersistenceError('energy_coverage_blocked')
        after = deepcopy(plan.value)
        keys = {'energy_sources', 'device_consumption'}
        if not self._legacy:
            keys.add('device_consumption_water')
        if set(after) - keys:
            raise PersistenceError('energy_schema_changed')
        # Validate with the installed HA schemas without adding optional defaults
        # or changing the user's configured consumer set/price representation.
        if self._legacy:
            import voluptuous as vol
            ENERGY_SOURCE_SCHEMA(after.get('energy_sources', []))
            consumer_schema = vol.Schema(DEVICE_CONSUMPTION_SCHEMA)
        else:
            import probatio
            probatio.Schema(ENERGY_SOURCE_SCHEMA)(after.get('energy_sources', []))
            consumer_schema = DEVICE_CONSUMPTION_SCHEMA
        for key in keys - {'energy_sources'}:
            for consumer in after.get(key, []):
                consumer_schema(consumer)
        if not self._legacy:
            projected = self.manager._process_energy_sources(deepcopy(after.get('energy_sources', [])))
            if projected != after.get('energy_sources', []):
                raise PersistenceError('energy_generated_identity_requires_plan')
        return PreparedEnergy(before, after, revision(after))

    async def write(self, prepared, guard):
        async with self._lock:
            guard()
            if self._failed:
                raise PersistenceError('energy_write_uncertain')
            current = await self.read()
            guard()
            if current != prepared.before or revision(prepared.after) != prepared.after_revision:
                raise PersistenceError('energy_stale_plan')
            if current.revision == prepared.after_revision:
                return

            async def effect():
                guard()
                self._settled()
                if self.manager.data != current.value:
                    raise PersistenceError('energy_stale_cache')
                await self.manager.async_update(deepcopy(prepared.after))
                guard()
                if self.manager.data != prepared.after:
                    raise PersistenceError('energy_native_result_changed')
                # Use native Store's public save to consume its queued 60s save.
                # This preserves native serialization/listeners and never writes
                # the .storage file directly. Independent fsync/readback follows.
                await self._store.async_save(deepcopy(self.manager.data))
                guard()
                actual = await self.read()
                guard()
                if actual.value != prepared.after or actual.revision != prepared.after_revision:
                    raise PersistenceError('energy_write_not_confirmed')

            task = asyncio.create_task(effect())
            cancelled = False
            try:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                task.result()
                if cancelled:
                    raise asyncio.CancelledError
            except BaseException:
                self._failed = True
                raise

    async def verify(self, expected_revision):
        async with self._lock:
            return (await self.read()).revision == expected_revision
