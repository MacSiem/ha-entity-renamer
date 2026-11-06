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


def generated_cost_moves(before, after):
    """Identify native grid-cost entities that follow a renamed meter."""
    old, new = before.get('energy_sources', []), after.get('energy_sources', [])
    if type(old) is not list or type(new) is not list or len(old) != len(new):
        raise PersistenceError('energy_generated_cost_source_shape')
    result = []
    for left, right in zip(old, new, strict=True):
        if type(left) is not dict or type(right) is not dict or left.get('type') != right.get('type'):
            raise PersistenceError('energy_generated_cost_source_shape')
        for source, cost, price_entity, price_number in (
            ('stat_energy_from', 'stat_cost', 'entity_energy_price', 'number_energy_price'),
            ('stat_energy_to', 'stat_compensation', 'entity_energy_price_export', 'number_energy_price_export'),
        ):
            previous, target = left.get(source), right.get(source)
            if previous == target or previous is None or target is None:
                continue
            generated = left.get(cost) is None and (left.get(price_entity) is not None
                                                     or left.get(price_number) is not None)
            if not generated:
                continue
            # This native lifecycle has been qualified for the current Core's
            # grid import cost. Other native generators must fail pre-effect.
            if (left.get('type') != 'grid' or source != 'stat_energy_from'
                    or type(previous) is not str or type(target) is not str):
                raise PersistenceError('energy_generated_cost_unsupported')
            result.append((previous, target))
        # Older Core represents grid flows as nested lists. Do not silently
        # skip an unqualified generated cost when a nested meter is changed.
        for key in ('flow_from', 'flow_to'):
            old_flows, new_flows = left.get(key) or [], right.get(key) or []
            if type(old_flows) is not list or type(new_flows) is not list or len(old_flows) != len(new_flows):
                raise PersistenceError('energy_generated_cost_source_shape')
            for old_flow, new_flow in zip(old_flows, new_flows, strict=True):
                if type(old_flow) is not dict or type(new_flow) is not dict:
                    raise PersistenceError('energy_generated_cost_source_shape')
                for field, cost, price_entity, price_number in (
                    ('stat_energy_from', 'stat_cost', 'entity_energy_price', 'number_energy_price'),
                    ('stat_energy_to', 'stat_compensation', 'entity_energy_price', 'number_energy_price'),
                ):
                    if (old_flow.get(field) != new_flow.get(field) and old_flow.get(field) is not None
                            and old_flow.get(cost) is None and (old_flow.get(price_entity) is not None
                                                               or old_flow.get(price_number) is not None)):
                        raise PersistenceError('energy_generated_cost_unsupported')
    if len(set(result)) != len(result) or len({old for old, _ in result}) != len(result):
        raise PersistenceError('energy_generated_cost_ambiguous')
    return tuple(result)


class NativeEnergy:
    def __init__(self, hass, manager, *, allow_generated_costs=False):
        from homeassistant.components.energy.data import EnergyManager
        from homeassistant.const import __version__
        if __version__ not in ('2025.6.0', '2026.10.2', '2026.10.4') or type(manager) is not EnergyManager:
            raise PersistenceError('unsupported_energy_runtime')
        self.hass, self.manager = hass, manager
        if type(allow_generated_costs) is not bool:
            raise PersistenceError('energy_generated_cost_authority')
        self.allow_generated_costs = allow_generated_costs
        self._legacy = __version__ == '2025.6.0'
        self._minor_version = 1 if self._legacy else 3
        self._lock = asyncio.Lock()
        self._failed = False
        self.last_generated_cost_effect = ()
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
        moves = generated_cost_moves(before.value, after)
        if moves and not self.allow_generated_costs:
            raise PersistenceError('energy_generated_cost_unsupported')
        for old, _ in moves:
            self._generated_cost(old, old)
        return PreparedEnergy(before, after, revision(after))

    def _generated_cost(self, source_id, registry_source_id):
        from homeassistant.components.energy.sensor import EnergyCostSensor
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_platform import async_get_platforms
        from .native_registry_receipts import _exact_json_equal, _freeze_json
        registry = er.async_get(self.hass)
        source = registry.async_get(registry_source_id)
        data = self.hass.data.get('energy')
        if (source is None or type(data) is not dict or type(data.get('cost_sensors')) is not dict):
            raise PersistenceError('energy_generated_cost_runtime_unqualified')
        cost_id = data['cost_sensors'].get(source_id)
        candidates = [entity for platform in async_get_platforms(self.hass, 'energy')
                      for entity in platform.entities.values()
                      if type(entity) is EnergyCostSensor and entity._adapter.source_type == 'grid'
                      and entity._adapter.stat_energy_key == 'stat_energy_from'
                      and entity._config.get('stat_energy_from') == source_id]
        if len(candidates) != 1 or candidates[0].entity_id != cost_id:
            raise PersistenceError('energy_generated_cost_runtime_unqualified')
        entity = candidates[0]
        row = registry.async_get(cost_id)
        sources = [value for value in self.manager.data.get('energy_sources', [])
                   if type(value) is dict and value.get('type') == 'grid'
                   and value.get('stat_energy_from') == source_id and value.get('stat_cost') is None
                   and (value.get('entity_energy_price') is not None or value.get('number_energy_price') is not None)]
        if (row is None or entity.registry_entry is not row or row.platform != 'energy'
                or row.unique_id != source.id + '_grid_cost'
                or len(sources) != 1 or not _exact_json_equal(entity._config, _freeze_json(sources[0]))
                or entity._adapter.source_type != 'grid'
                or entity._adapter.stat_energy_key != 'stat_energy_from'
                or entity._config.get('stat_energy_from') != source_id):
            raise PersistenceError('energy_generated_cost_identity_changed')
        return entity, row.id, row.unique_id, cost_id, source.id

    @staticmethod
    def _cost_value(entity):
        import math
        value = entity.native_value
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise PersistenceError('energy_generated_cost_value_unqualified')
        reset = entity.last_reset
        return {'value': None if value is None else repr(value),
                'last_reset': None if reset is None else reset.isoformat()}

    async def observe_generated_costs(self, before, after, guard):
        """Capture real cost identity and existing history before the Energy intent."""
        from .history_guard import NativeHistoryGuard
        from .recorder_guard import NativeRecorderGuard
        moves = generated_cost_moves(before, after)
        if moves and not self.allow_generated_costs:
            raise PersistenceError('energy_generated_cost_unsupported')
        if not moves:
            return []
        recorder = NativeRecorderGuard(self.hass)
        history = NativeHistoryGuard(recorder)
        result = []
        for old, new in moves:
            guard()
            entity, registry_id, unique_id, cost_id, source_id = self._generated_cost(old, new)
            value = self._cost_value(entity)
            mapping = {cost_id: cost_id}
            metadata = await recorder.snapshot(mapping)
            history_before = await history.capture(mapping)
            guard()
            current = self._generated_cost(old, new)
            if current != (entity, registry_id, unique_id, cost_id, source_id) or self._cost_value(entity) != value:
                raise PersistenceError('energy_generated_cost_changed_before_intent')
            result.append({'before_source': old, 'after_source': new, 'entity_id': cost_id,
                'registry_id': registry_id, 'unique_id': unique_id, 'source_registry_id': source_id,
                'native_before': value, 'metadata_before': metadata, 'history_before': history_before})
        return result

    async def write(self, prepared, guard, *, cost_preimage=None, before_cost_effect=None):
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
            moves = generated_cost_moves(current.value, prepared.after)
            if moves and (not self.allow_generated_costs or type(cost_preimage) is not list
                          or before_cost_effect is None
                          or [(row.get('before_source'), row.get('after_source')) for row in cost_preimage] != list(moves)):
                raise PersistenceError('energy_generated_cost_preimage_required')
            if not moves and cost_preimage not in (None, []):
                raise PersistenceError('energy_generated_cost_foreign_preimage')
            self.last_generated_cost_effect = ()

            async def effect():
                from .history_guard import NativeHistoryGuard
                from .recorder_guard import NativeRecorderGuard
                guard()
                self._settled()
                if self.manager.data != current.value:
                    raise PersistenceError('energy_stale_cache')
                recorder = NativeRecorderGuard(self.hass) if moves else None
                history = NativeHistoryGuard(recorder) if recorder is not None else None
                for row in cost_preimage or ():
                    entity, registry_id, unique_id, cost_id, source_id = self._generated_cost(
                        row['before_source'], row['after_source'])
                    if (registry_id, unique_id, cost_id, source_id) != (row['registry_id'], row['unique_id'],
                            row['entity_id'], row['source_registry_id']) or self._cost_value(entity) != row['native_before']:
                        raise PersistenceError('energy_generated_cost_preimage_changed')
                    if await recorder.snapshot({cost_id: cost_id}) != row['metadata_before']:
                        raise PersistenceError('energy_generated_cost_metadata_changed')
                    if not await history.verify(row['history_before']):
                        raise PersistenceError('energy_generated_cost_history_changed')
                    await before_cost_effect()
                    guard()
                    checked = self._generated_cost(row['before_source'], row['after_source'])
                    if (checked[0] is not entity or checked[1:] !=
                            (registry_id, unique_id, cost_id, source_id)
                            or self._cost_value(entity) != row['native_before']
                            or self.manager.data != current.value):
                        raise PersistenceError('energy_generated_cost_preimage_changed')
                    await entity.async_remove()
                    guard()
                    await recorder.async_synchronize()
                    guard()
                if moves:
                    await before_cost_effect()
                    guard()
                self._settled()
                if self.manager.data != current.value:
                    raise PersistenceError('energy_stale_cache')
                await self.manager.async_update(deepcopy(prepared.after))
                guard()
                if self.manager.data != prepared.after:
                    raise PersistenceError('energy_native_result_changed')
                effects = []
                for row in cost_preimage or ():
                    entity, registry_id, unique_id, cost_id, source_id = self._generated_cost(
                        row['after_source'], row['after_source'])
                    if (registry_id, unique_id, cost_id, source_id) != (row['registry_id'], row['unique_id'],
                            row['entity_id'], row['source_registry_id']):
                        raise PersistenceError('energy_generated_cost_rebind_changed')
                    if row['before_source'] in self.hass.data['energy']['cost_sensors']:
                        raise PersistenceError('energy_generated_cost_duplicate')
                    metadata = await recorder.snapshot({cost_id: cost_id})
                    for kind in ('states', 'statistics'):
                        for name, old_row in row['metadata_before'][kind].items():
                            if metadata[kind].get(name) != old_row:
                                raise PersistenceError('energy_generated_cost_metadata_changed')
                    if not await history.verify(row['history_before']):
                        raise PersistenceError('energy_generated_cost_history_changed')
                    effects.append({'before_source': row['before_source'], 'after_source': row['after_source'],
                        'entity_id': cost_id, 'registry_id': registry_id, 'unique_id': unique_id,
                        'source_registry_id': source_id, 'native_after': self._cost_value(entity),
                        'metadata_after': metadata, 'history_verified': True})
                    guard()
                # Use native Store's public save to consume its queued 60s save.
                # This preserves native serialization/listeners and never writes
                # the .storage file directly. Independent fsync/readback follows.
                await self._store.async_save(deepcopy(self.manager.data))
                guard()
                actual = await self.read()
                guard()
                if actual.value != prepared.after or actual.revision != prepared.after_revision:
                    raise PersistenceError('energy_write_not_confirmed')
                self.last_generated_cost_effect = tuple(effects)

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
