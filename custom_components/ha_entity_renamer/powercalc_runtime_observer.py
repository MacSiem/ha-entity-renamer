"""Partial native Powercalc inventory. Always UNKNOWN; never mutation authority.

Reads existing managers/platforms/indexes synchronously on HA's event loop.
No vendor import, factory, resolver, render, service, task drain, file IO or write.
Private payloads contain local config and process IDs; expose summary only.
"""
from __future__ import annotations

from types import MappingProxyType
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import inspect

from .helper_native import read_entry


BLOCKERS = ('powercalc_source_binding_unverified',
            'powercalc_vendor_schema_unverified',
            'powercalc_energy_topology_unverified',
            'powercalc_continuity_unverified')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _type(value):
    cls = type(value)
    return cls.__module__ + '.' + cls.__qualname__


def _guard(callback):
    result = callback()
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise ValueError('powercalc_synchronous_guard_required')
    if result is not None and result is not True:
        raise ValueError('powercalc_guard_rejected')


@dataclass(frozen=True)
class PowercalcRuntimeCapture:
    version: str
    payload_json: bytes
    rows: tuple[tuple[str, str, str], ...]
    structural_token: bytes
    status: str = 'UNKNOWN'
    reason: str = 'powercalc_runtime_inventory_partial'
    blockers: tuple[str, ...] = BLOCKERS


def capture_powercalc_runtime(hass, *, guard):
    """Read the full existing collection; no claim of semantic completeness.

    Call before discovery's first await, then assert_current after all awaits.
    No values from a second sample are adopted into the first capture.
    Exact native layouts only; unfamiliar runtime values stay explicit rows.
    """
    from homeassistant.const import __version__
    from homeassistant import loader
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.helpers import entity_registry as er, entity_platform as ep
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.template import Template
    _guard(guard)
    if __version__ not in ('2025.6.0', '2026.10.2'):
        raise ValueError('powercalc_core_inventory_layout_unsupported')
    manager = hass.config_entries
    registry = hass.data.get(er.DATA_REGISTRY)
    if type(manager) is not ConfigEntries or type(registry) is not er.EntityRegistry:
        raise ValueError('powercalc_native_manager_unavailable')
    platform_map = hass.data.get(ep.DATA_ENTITY_PLATFORM)
    issues, catalog, uses, active, platform_rows = [], {}, {}, {}, []
    pending = deque()

    def unsupported(path, value, reason='unsupported_value_type'):
        row = {'path': path, 'type': _type(value), 'disposition': reason}
        issues.append(row)
        return {'$unsupported': row, 'process_id': id(value)}

    def ref(entity, path):
        key = str(id(entity))
        if key not in catalog:
            catalog[key] = entity
            pending.append(key)
        uses.setdefault(key, set()).add(path)
        return {'$entity': key}

    def encode(value, path, stack=()):
        if value is None or type(value) in (str, bool, int):
            return value
        if type(value) is float:
            return value if math.isfinite(value) else unsupported(path, value, 'nonfinite')
        if isinstance(value, Entity):
            return ref(value, path)
        if isinstance(value, Enum):
            return {'$enum': _type(value), 'value': encode(value.value, path + '/value', stack)}
        if type(value) is Decimal:
            return {'$decimal': str(value)} if value.is_finite() else unsupported(path, value, 'nonfinite')
        if type(value) in (date, datetime):
            return {'$datetime': value.isoformat()}
        if type(value) is timedelta:
            return {'$timedelta': [value.days, value.seconds, value.microseconds]}
        if type(value) is Template:
            return {'$template': value.template}  # never render or compile
        if id(value) in stack:
            return unsupported(path, value, 'cyclic_container')
        nested = (*stack, id(value))
        if type(value) in (dict, MappingProxyType):
            if any(type(key) is not str for key in value):
                return unsupported(path, value, 'unsupported_mapping_key')
            return {key: encode(child, path + '/' + key, nested)
                    for key, child in sorted(value.items())}
        if type(value) in (list, tuple):
            return {'$' + type(value).__name__: [encode(child, path + '/' + str(i), nested)
                    for i, child in enumerate(value)]}
        if type(value) in (set, frozenset):
            return {'$set': sorted((encode(child, path + '/member', nested) for child in value), key=_json)}
        return unsupported(path, value)

    entries = []
    for entry in manager.async_entries():
        if entry.domain != 'powercalc':
            continue
        try:
            snapshot = read_entry(hass, entry.entry_id)
            entries.append({'snapshot': encode(snapshot, 'entry/' + entry.entry_id),
                'entry_id': entry.entry_id, 'process_id': id(entry),
                'activation': entry.state.value})
        except (ValueError, KeyError, AttributeError, TypeError):
            entries.append({'entry_id': entry.entry_id, 'process_id': id(entry),
                'disposition': 'entry_layout_unsupported'})
            issues.append({'path': 'entry/' + entry.entry_id,
                'type': _type(entry), 'disposition': 'entry_layout_unsupported'})

    if type(platform_map) is not dict:
        unsupported('native/platforms', platform_map, 'platform_inventory_unavailable')
    else:
        seen = set()
        # DATA_ENTITY_PLATFORM is keyed by INTEGRATION, not entity domain.
        # Examine every bucket so an active vendor subclass cannot be omitted.
        for bucket, platforms in platform_map.items():
            if type(platforms) is not list:
                unsupported('platform_bucket/' + str(bucket), platforms)
                continue
            for platform in platforms:
                if type(platform) is not ep.EntityPlatform:
                    unsupported('platform/' + str(bucket), platform, 'platform_layout_unsupported')
                    continue
                if id(platform) in seen:
                    continue
                seen.add(id(platform))
                for entity_id, entity in platform.entities.items():
                    relevant = (platform.platform_name == 'powercalc'
                        or type(entity).__module__.startswith('custom_components.powercalc.'))
                    if not relevant:
                        continue
                    key = str(id(entity))
                    ref(entity, 'platform/' + platform.domain + '/' + entity_id)
                    row = {'domain': platform.domain, 'integration': platform.platform_name,
                        'entity_id': entity_id, 'process_id': id(platform),
                        'entry_id': None if platform.config_entry is None else platform.config_entry.entry_id}
                    active.setdefault(key, []).append(row)
                if platform.platform_name == 'powercalc':
                    platform_rows.append({'domain': platform.domain, 'integration': platform.platform_name,
                        'process_id': id(platform),
                        'entry_id': None if platform.config_entry is None else platform.config_entry.entry_id,
                        'entity_ids': sorted(platform.entities)})

    loaded = hass.data.get(loader.DATA_INTEGRATIONS, {}).get('powercalc')
    vendor_version = loaded.manifest.get('version') if type(loaded) is loader.Integration else None
    if vendor_version is not None and type(vendor_version) is not str:
        raise ValueError('powercalc_loaded_version_layout_unsupported')
    vendor = hass.data.get('powercalc')
    indexes = {}
    if type(vendor) is not dict:
        unsupported('vendor', vendor, 'vendor_runtime_unavailable')
    else:
        for name, value in sorted(vendor.items()):
            if type(name) is not str:
                raise ValueError('powercalc_vendor_index_key_unsupported')
            path = 'vendor/' + name
            if name == 'discovery_manager' and type(value).__module__ == 'custom_components.powercalc.discovery':
                data = vars(value)
                indexes[name] = {'type': _type(value), 'process_id': id(value),
                    'fields': {key: encode(data.get(key), path + '/' + key) for key in (
                        '_status', '_configured_discovery_keys', '_pending_discovery_keys',
                        '_manually_configured_entities')},
                    'source_disposition': 'profile_library_and_flows_unverified'}
            else:
                indexes[name] = encode(value, path)

    origins = {}
    configured = None if type(vendor) is not dict else vendor.get('configured_entities')
    if type(configured) is dict:
        for source_id, members in configured.items():
            if type(members) is not list:
                unsupported('vendor/configured_entities/' + str(source_id), members, 'index_layout_unverified')
                continue
            for pair in members:
                if (type(pair) is not tuple or len(pair) != 2
                        or not isinstance(pair[0], Entity) or type(pair[1]) is not bool):
                    unsupported('vendor/configured_entities/' + str(source_id), pair, 'index_layout_unverified')
                    continue
                origins.setdefault(str(id(pair[0])), set()).add(
                    'yaml_runtime_index' if pair[1] else 'non_yaml_runtime_index')

    # Register all vendor registry rows, including disabled/inactive objects.
    registry_rows = []
    for row in registry.entities.values():
        if row.platform == 'powercalc':
            registry_rows.append({'registry_id': row.id, 'entity_id': row.entity_id,
                'unique_id': row.unique_id, 'entry_id': row.config_entry_id,
                'subentry_id': row.config_subentry_id, 'device_id': row.device_id,
                'disabled_by': encode(row.disabled_by, 'registry/' + row.id + '/disabled')})

    entity_rows = []
    while pending:
        key = pending.popleft()
        entity = catalog[key]
        data = vars(entity)
        entity_id = data.get('entity_id')
        row = registry.entities.get(entity_id)
        fields = {}
        for name in ('_sensor_config', '_entities', '_base_entity_id', '_subtract_entities',
                '_track_entities', '_source_energy_entity', '_availability_entity',
                '_powercalc_source_entity', '_group_type', '_calculation_strategy',
                '_power_profile', '_strategy_instance', '_sub_profile_selector', '_prev_state_store'):
            if name in data:
                fields[name] = encode(data[name], 'object/' + key + '/' + name)
        source = data.get('_source_entity')
        if type(source) is str or source is None:
            fields['_source_entity'] = source
        elif _type(source) == 'custom_components.powercalc.common.SourceEntity':
            source_data = vars(source)
            source_row = registry.entities.get(source_data.get('entity_id'))
            fields['_source_entity'] = {name: encode(source_data.get(name),
                'object/' + key + '/source/' + name) for name in (
                    'entity_id', 'unique_id', 'domain', 'config_entry_id')}
            fields['_source_entity']['registry_id'] = None if source_row is None else source_row.id
        else:
            fields['_source_entity'] = unsupported('object/' + key + '/source', source)
        entity_rows.append({'object_id': key, 'type': _type(entity), 'entity_id': entity_id,
            'registry_id': None if row is None else row.id,
            'registry_platform': None if row is None else row.platform,
            'unique_id': None if row is None else row.unique_id,
            'entry_id': None if row is None else row.config_entry_id,
            'subentry_id': None if row is None else row.config_subentry_id,
            'device_id': None if row is None else row.device_id,
            'active_platforms': active.get(key, []), 'references': sorted(uses[key]),
            'disposition': 'active' if key in active else 'index_only_or_external_wrapper',
            'source_origins': sorted(origins.get(key, ())),
            'source_disposition': 'runtime_only_source_binding_unverified',
            'type_disposition': 'schema_unverified', 'fields': fields})

    for row in entity_rows:
        row['references'] = sorted(uses[row['object_id']])

    body = {'version': __version__, 'vendor_version': vendor_version,
        'source_dispositions': {'yaml': 'not_bound', 'config_entries': 'manager_readback_only',
            'vendor_code': 'loaded_metadata_only_source_sha_unverified', 'profiles': 'not_bound', 'energy': 'not_bound'},
        'entries': sorted(entries, key=lambda row: row['entry_id']),
        'platforms': sorted(platform_rows, key=_json), 'indexes': indexes,
        'index_dispositions': {key: 'inventory_only_schema_unverified' for key in indexes},
        'registry': sorted(registry_rows, key=lambda row: row['registry_id']),
        'objects': sorted(entity_rows, key=lambda row: row['object_id']),
        'unsupported_rows': sorted(issues, key=_json)}
    payload = _json(body)
    # Numeric standby contributions are live samples, not structural changes.
    # Keep exact values in payload; retain its container identity in the token.
    structural = dict(body)
    structural['indexes'] = dict(indexes)
    if type(vendor) is dict and type(vendor.get('standby_power_sensors')) is dict:
        structural['indexes']['standby_power_sensors'] = {
            'live_samples_only': id(vendor['standby_power_sensors'])}
    token = _json([id(manager), id(registry), id(platform_map), id(vendor), structural])
    rows = []
    for kind, values, identity in (('entry', body['entries'], 'entry_id'),
                                 ('object', body['objects'], 'object_id'),
                                 ('registry', body['registry'], 'registry_id')):
        for row in values:
            resource = kind + ':' + row[identity]
            rows.append((resource, resource, _sha(_json(row))))
    _guard(guard)
    return PowercalcRuntimeCapture(__version__, payload, tuple(sorted(rows)), token)


def assert_powercalc_runtime_current(hass, captured, *, guard):
    """Compare only; never adopt a replacement snapshot or grant write access."""
    if type(captured) is not PowercalcRuntimeCapture:
        raise ValueError('powercalc_capture_type_invalid')
    current = capture_powercalc_runtime(hass, guard=guard)
    if current.structural_token != captured.structural_token:
        raise ValueError('powercalc_runtime_changed_during_capture')
