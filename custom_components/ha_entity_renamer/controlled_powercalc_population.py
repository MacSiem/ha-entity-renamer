"""Qualified native Powercalc counter population for a controlled reload.

This module captures detached data and never unloads, persists or activates an
entity. The caller owns the existing journal, physical CAS and actual effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .transaction import Refused


POWER_KINDS = frozenset(('profile_lut', 'profile_fixed', 'profile_linear',
                         'power_sum', 'power_subtract'))
COUNTER_KINDS = frozenset(('energy_integral', 'energy_positive_delta'))


def _need(value, reason):
    if not value:
        raise Refused('controlled_powercalc_population_' + reason)


@dataclass(frozen=True)
class Counter:
    entity_id: str
    registry_id: str
    entry_id: str | None
    target_id: str
    kind: str
    entity: object


@dataclass(frozen=True)
class PowercalcPopulation:
    counters: tuple[Counter, ...]
    entity_ids: tuple[str, ...]
    descriptors: tuple[tuple[str, str, str], ...]


def qualify_population(hass, bound, mapping, previous, *, inverse_aliases=None):
    """Recognize every running vendor entity before any write or unload."""
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers.entity_platform import async_get_platforms
    from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
    from .powercalc_source_binding import PowercalcSourceBindings, PowercalcVendor
    from .powercalc_previous_state_original import PowercalcPreviousOriginal
    from .native_input_baseline import _loads
    _need(type(hass) is HomeAssistant and type(bound) is PowercalcSourceBindings
          and type(previous) is PowercalcPreviousOriginal
          and type(mapping) is dict and all(type(a) is str and type(b) is str
                                            for a, b in mapping.items()), 'inputs')
    vendor = PowercalcVendor(hass, with_profiles=True)
    vendor.bindings()
    _need(all(entry.disabled_by is not None or entry.state is ConfigEntryState.LOADED
              for entry in hass.config_entries.async_entries('powercalc')),
          'unloaded_enabled_entry_unsupported')
    restore = hass.data.get(DATA_RESTORE_STATE)
    _need(type(restore) is RestoreStateData and restore.hass is hass, 'restore_store_unavailable')
    pss_disk = {} if previous.disk_raw is None else _loads(previous.disk_raw)['data']
    rows = bound.rows
    _need(type(rows) is tuple and bool(rows) and all(row.source_verified for row in rows),
          'unverified_graph')
    by_id = {row.entity_id: row for row in rows}
    _need(len(by_id) == len(rows), 'duplicate_graph_entity')
    entities = {}
    for platform in async_get_platforms(hass, 'powercalc'):
        for identity, entity in platform.entities.items():
            _need(identity not in entities, 'duplicate_native_entity')
            entities[identity] = entity
    _need(set(entities) == set(by_id), 'native_population_unaccounted')
    counters = []
    for identity, entity in sorted(entities.items()):
        row = by_id[identity]
        _need(row.runtime_identities and row.runtime_identities[0] == id(entity)
              and entity.registry_entry is not None and entity.registry_entry.id == row.registry_id
              and entity.registry_entry.entity_id == identity and entity.registry_entry.platform == 'powercalc',
              'native_identity_changed')
        if row.relation_kind == 'energy_integral':
            _need(type(entity) is vendor.classes['VirtualEnergySensor']
                  and type(entity.extra_restore_state_data) is vendor.classes['VirtualEnergyExtraStoredData']
                  and type(entity._state) is Decimal and entity._state.is_finite()
                  and type(entity._last_valid_state) is Decimal and entity._last_valid_state.is_finite(),
                  'integration_counter_unverified')
            kind = 'integration'
        elif row.relation_kind == 'energy_positive_delta':
            _need(type(entity) is vendor.classes['GroupedEnergySensor']
                  and type(entity._native_value_exact) is Decimal and entity._native_value_exact.is_finite(),
                  'group_counter_unverified')
            kind = 'group'
        else:
            _need(row.relation_kind in POWER_KINDS,
                  'native_family_unqualified')
            continue
        target = mapping.get(identity, identity)
        _need(target.startswith('sensor.') and (target == identity or hass.states.async_available(target)),
              'counter_target_occupied')
        if target != identity:
            allowed_restore = (inverse_aliases or {}).get('restore_owned', {})
            allowed_pss = (inverse_aliases or {}).get('pss_owned', {})
            from homeassistant.const import __version__
            if __version__ in ('2026.11.0', '2026.11.1'):
                from .native_restore_uuid import owned_slot
                owned_slot(restore, identity, target, row.registry_id, required=False)
                _need(target not in restore.entities, 'restore_target_occupied')
            else:
                _need((target not in restore.last_states or target in allowed_restore)
                      and target not in restore.entities, 'restore_target_occupied')
            if kind == 'group':
                _need((target not in pss_disk or target in allowed_pss)
                      and (target not in entity._prev_state_store.states or target in allowed_pss),
                      'pss_target_occupied')
        counters.append(Counter(identity, row.registry_id, entity.registry_entry.config_entry_id,
                                target, kind, entity))
    _need(bool(counters) and len({row.registry_id for row in counters}) == len(counters)
          and len({row.target_id for row in counters}) == len(counters), 'counter_population_unqualified')
    return PowercalcPopulation(tuple(counters), tuple(sorted(entities)),
        tuple(sorted((row.entity_id, row.registry_id, row.relation_kind) for row in rows)))


def removed_group_indexes(hass, captured, handoff, vendor):
    """Qualify only a receipted old group object retained by the vendor index."""
    import json
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity import EntityPlatformState
    from homeassistant.helpers.entity_platform import async_get_platforms
    from .powercalc_runtime_observer import PowercalcRuntimeCapture
    from .powercalc_source_binding import PowercalcVendor
    _need(type(captured) is PowercalcRuntimeCapture and type(handoff) is dict
          and type(vendor) is PowercalcVendor and type(handoff.get('population')) is list,
          'removed_index_evidence_required')
    indexes = hass.data.get('powercalc', {}).get('group_entities')
    _need(type(indexes) is dict, 'removed_group_index_unavailable')
    inventory = json.loads(captured.payload_json)['objects']
    active = {entity_id: entity for platform in async_get_platforms(hass, 'powercalc')
              for entity_id, entity in platform.entities.items()}
    registry = er.async_get(hass)
    ignored = set()
    for item in handoff['population']:
        if item['kind'] != 'group' or item['before_id'] == item['after_id']:
            continue
        old, new, uuid = item['before_id'], item['after_id'], item['uuid']
        ghost = indexes.get(old)
        if ghost is None:
            continue
        current = active.get(new)
        row = registry.async_get(new)
        matches = [row for row in inventory if row['object_id'] == str(id(ghost))]
        _need(type(ghost) is vendor.classes['GroupedEnergySensor']
              and ghost._platform_state is EntityPlatformState.REMOVED
              and ghost.entity_id == old and ghost.registry_entry is not None
              and ghost.registry_entry.id == uuid and old not in active
              and type(current) is vendor.classes['GroupedEnergySensor']
              and current.registry_entry is row and row is not None and row.id == uuid
              and len(matches) == 1 and matches[0]['entity_id'] == old
              and not matches[0]['active_platforms'] and matches[0]['registry_id'] is None
              and matches[0]['references'] == ['vendor/group_entities/' + old],
              'removed_group_index_foreign_or_active')
        ignored.add(str(id(ghost)))
    return frozenset(ignored)


def snapshot_removed(hass, population, *, allowed_targets=()):
    """Build actual post-unload restore payloads; never write native state."""
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import EntityPlatformState
    from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
    from . import native_restore_state_payload
    from .powercalc_group_restore import prepare, prepare_same_id
    _need(type(hass) is HomeAssistant and type(population) is PowercalcPopulation, 'inputs')
    data = hass.data.get(DATA_RESTORE_STATE)
    _need(type(data) is RestoreStateData and data.hass is hass, 'restore_store_unavailable')
    from homeassistant.const import __version__
    result = []
    for counter in population.counters:
        entity, old, new = counter.entity, counter.entity_id, counter.target_id
        _need(entity._platform_state is EntityPlatformState.REMOVED
              and entity.entity_id == old and entity.registry_entry is not None
              and entity.registry_entry.id == counter.registry_id
              and entity.registry_entry.config_entry_id == counter.entry_id
              and old not in data.entities, 'inactive_or_target_changed')
        if __version__ in ('2026.11.0', '2026.11.1'):
            from .native_restore_uuid import owned_slot
            stored = owned_slot(data, old, new, counter.registry_id, required=True)
            _need(stored.state.entity_id == old, 'restore_source_id_changed')
        else:
            _need(old in data.last_states and (new == old or new not in data.last_states
                  or new in allowed_targets), 'inactive_or_target_changed')
            stored = data.last_states[old]
        if counter.kind == 'integration':
            payload = native_restore_state_payload.prepare_integration_snapshot_payload(stored, new, entity=entity)
        elif counter.kind == 'group':
            _need(type(entity._native_value_exact) is Decimal and entity._native_value_exact.is_finite(),
                  'group_total_unavailable')
            arguments = {'full_total': str(entity._native_value_exact), 'precision': entity._rounding_digits,
                         'unit': str(entity.native_unit_of_measurement)}
            payload = (prepare_same_id(stored, **arguments) if old == new
                       else prepare(stored, new, **arguments))
        else:
            raise Refused('controlled_powercalc_population_counter_kind')
        result.append((counter.registry_id, old, new, counter.kind, payload))
    return tuple(result)
