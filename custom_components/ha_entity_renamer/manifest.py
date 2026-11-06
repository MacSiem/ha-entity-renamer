"""Pure manifest planning. No HA writes or assumption that a missing ID migrated."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any

_ID = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+", re.ASCII)
_MAX_ROWS = 10000


def _text(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value or len(value) > 255 or any(ord(c) < 32 for c in value):
        raise ValueError(f"invalid_{field}")
    return value


def _entity_id(value: Any) -> str:
    value = _text(value, "entity_id")
    if not _ID.fullmatch(value):
        raise ValueError("invalid_entity_id")
    return value


def _rows(document: dict, key: str) -> list[dict]:
    rows = document.get(key, [])
    if type(rows) is not list or len(rows) > _MAX_ROWS or any(type(row) is not dict for row in rows):
        raise ValueError(f"invalid_{key}")
    return rows


@dataclass(frozen=True)
class EntityChange:
    old_id: str
    new_id: str
    name: str | None
    device_id: str | None
    registry_id: str | None = None
    unique_id: str | None = None
    platform: str | None = None
    disabled_by: str | None = None


@dataclass(frozen=True)
class DeviceChange:
    device_id: str
    name: str


@dataclass(frozen=True)
class Manifest:
    entities: tuple[EntityChange, ...]
    devices: tuple[DeviceChange, ...]
    statistics: dict[str, str]

    def select(self, entity_ids: list[str], *, device_ids: list[str] | None = None) -> Manifest:
        """Select explicitly; device renames are never inferred for every batch."""
        device_ids = [] if device_ids is None else device_ids
        for selected, known in [(entity_ids, {row.old_id for row in self.entities}),
                                (device_ids, {row.device_id for row in self.devices})]:
            if (type(selected) is not list or any(type(value) is not str for value in selected)
                    or len(selected) != len(set(selected)) or not set(selected) <= known):
                raise ValueError("invalid_batch_selection")
        entity_set, device_set = set(entity_ids), set(device_ids)
        return Manifest(tuple(row for row in self.entities if row.old_id in entity_set),
                        tuple(row for row in self.devices if row.device_id in device_set),
                        {old: new for old, new in self.statistics.items() if old in entity_set})


def parse_manifest(document: Any) -> Manifest:
    """Read the local proposal schema, ignoring descriptive action labels."""
    if type(document) is not dict:
        raise ValueError("invalid_manifest")
    entities, devices, statistics = [], [], {}
    sources, destinations, device_sources, statistic_sources = set(), set(), set(), set()
    for row in _rows(document, "entities"):
        old = _entity_id(row.get("entity_id"))
        new = _entity_id(row.get("proposed_entity_id", old))
        if old in sources:
            raise ValueError("duplicate_source")
        sources.add(old)
        name = _text(row.get("proposed_name"), "name", optional=True)
        if old == new and (name is None or name == row.get("current_name")):
            continue
        if new in destinations:
            raise ValueError("duplicate_destination")
        if old.split(".")[0] != new.split(".")[0]:
            raise ValueError("domain_change")
        destinations.add(new)
        device = _text(row.get("device_id") or None, "device_id", optional=True)
        entities.append(EntityChange(old, new, name, device))
    changed_ids = {row.old_id for row in entities if row.old_id != row.new_id}
    if any(row.new_id in changed_ids for row in entities if row.old_id != row.new_id):
        raise ValueError("overlapping_ids")
    for row in _rows(document, "devices"):
        device_id = _text(row.get("device_id"), "device_id")
        if device_id in device_sources:
            raise ValueError("duplicate_device")
        device_sources.add(device_id)
        name = _text(row.get("proposed_name"), "name", optional=True)
        if name is not None and name != row.get("current_name"):
            devices.append(DeviceChange(device_id, name))
    changes = {row.old_id: row.new_id for row in entities if row.old_id != row.new_id}
    for row in _rows(document, "statistics"):
        old = _text(row.get("statistic_id"), "statistic_id")
        new = _text(row.get("proposed_statistic_id", old), "statistic_id")
        if old in statistic_sources:
            raise ValueError("duplicate_statistic")
        statistic_sources.add(old)
        if old == new:
            continue  # Includes orphan and external statistics; preserve them.
        if changes.get(old) != new:
            raise ValueError("unlinked_statistic")
        statistics[old] = new
    return Manifest(tuple(entities), tuple(devices), statistics)


def _registry_indexes(entities, devices):
    by_id = {row["entity_id"]: row for row in entities}
    by_device = {row["id"]: row for row in devices}
    if len(by_id) != len(entities) or len(by_device) != len(devices):
        raise ValueError("ambiguous_registry")
    return by_id, by_device


def _bind_entity(change, by_id):
    current = by_id.get(change.old_id)
    if current is None:
        raise ValueError("source_missing")
    if change.new_id != change.old_id and change.new_id in by_id:
        raise ValueError("destination_occupied")
    if current.get("device_id") != change.device_id:
        raise ValueError("device_changed")
    return replace(change,
        registry_id=_text(current.get("id"), "registry_id"),
        unique_id=_text(current.get("unique_id"), "unique_id"),
        platform=_text(current.get("platform"), "platform"),
        disabled_by=current.get("disabled_by"))


def bind_registry(manifest: Manifest, entities: list[dict], devices: list[dict]) -> Manifest:
    """Bind fresh stable identities using the same policy as conflict reporting.

    Recorder collisions and complete dependency coverage remain separate
    mandatory checks. A missing source never means an already-applied change.
    """
    by_id, by_device = _registry_indexes(entities, devices)
    bound = tuple(_bind_entity(change, by_id) for change in manifest.entities)
    if any(change.device_id not in by_device for change in manifest.devices):
        raise ValueError("device_missing")
    return Manifest(bound, manifest.devices, dict(manifest.statistics))


@dataclass(frozen=True)
class RegistryConflict:
    kind: str
    index: int | None
    reason: str


def registry_conflicts(manifest: Manifest, entities: list[dict],
                       devices: list[dict]) -> tuple[RegistryConflict, ...]:
    """Report every rejected proposal row without exposing registry contents."""
    try:
        by_id, by_device = _registry_indexes(entities, devices)
    except ValueError:
        return (RegistryConflict('registry', None, 'ambiguous_registry'),)
    result = []
    for index, change in enumerate(manifest.entities):
        try:
            _bind_entity(change, by_id)
        except ValueError as error:
            reason = error.args[0]
            if reason not in ('source_missing', 'destination_occupied', 'device_changed'):
                reason = 'identity_unverified'
            result.append(RegistryConflict('entity', index, reason))
    for index, change in enumerate(manifest.devices):
        if change.device_id not in by_device:
            result.append(RegistryConflict('device', index, 'device_missing'))
    return tuple(result)


# The approved local proposal contains descriptive inventory alongside changes.
# These fields are read only for format validation and never become instructions.
_IMPORT_FIELDS = {
    'entities': {'entity_id', 'proposed_entity_id', 'current_name', 'proposed_name',
        'device_id', 'device_current', 'device_proposed', 'disabled', 'platform',
        'statistics', 'energy_source', 'energy_breakdown', 'references', 'role',
        'scope', 'action', 'note'},
    'devices': {'device_id', 'current_name', 'proposed_name', 'model', 'area_current',
        'area_proposed', 'entities', 'disabled_entities', 'scope', 'action', 'note'},
    'statistics': {'statistic_id', 'proposed_statistic_id', 'current_name',
        'proposed_name', 'source', 'unit', 'has_sum', 'has_mean', 'entity_exists',
        'action', 'note'},
}
_IMPORT_CHECKS = {'registry_entities', 'additional_live_entities', 'all_devices',
    'all_statistics', 'proposed_devices', 'proposed_entities', 'mapped_statistics',
    'statistics_without_current_entity', 'collisions', 'ha_writes'}


def decode_import(content, *, entity_ids=None, device_ids=None):
    """Strict JSON import, normalized through the existing manifest policy.

    Returns a detached proposal for actual server discovery. No source paths,
    receipts, steps, unchecked selection or descriptive metadata leave here.
    Neither format checks nor the user's historical counts prove current state.
    """
    import json
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError('invalid_import')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('invalid_import')
    try:
        if type(content) is not str or len(content.encode('utf-8')) > 4_000_000:
            raise ValueError('invalid_import')
        document = json.loads(content, object_pairs_hook=pairs, parse_constant=constant)
        if (type(document) is not dict
                or set(document) - {'at', 'checks', 'entities', 'devices', 'statistics'}):
            raise ValueError('invalid_import')
        if 'at' in document and (type(document['at']) is not str or len(document['at']) > 255):
            raise ValueError('invalid_import')
        if 'checks' in document:
            checks = document['checks']
            if (type(checks) is not dict or set(checks) - _IMPORT_CHECKS
                    or any(type(v) is not int or v < 0 for v in checks.values())):
                raise ValueError('invalid_import')
        for family, fields in _IMPORT_FIELDS.items():
            for row in _rows(document, family):
                if set(row) - fields:
                    raise ValueError('invalid_import')
                for field, value in row.items():
                    if family == 'devices' and field in ('entities', 'disabled_entities'):
                        valid = type(value) is int and value >= 0
                    elif family == 'statistics' and field in ('has_sum', 'has_mean'):
                        valid = type(value) is bool
                    else:
                        valid = (type(value) is str and len(value) <= 16_384
                                 or value is None and field in ('proposed_name', 'device_id'))
                    if not valid:
                        raise ValueError('invalid_import')
        manifest = parse_manifest(document)
        if entity_ids is not None or device_ids is not None:
            manifest = manifest.select([] if entity_ids is None else entity_ids,
                device_ids=[] if device_ids is None else device_ids)
        if not manifest.entities and not manifest.devices:
            raise ValueError('invalid_import')
        return {
            'entities': [{'entity_id': row.old_id, 'proposed_entity_id': row.new_id,
                          'proposed_name': row.name, 'device_id': row.device_id}
                         for row in manifest.entities],
            'devices': [{'device_id': row.device_id, 'proposed_name': row.name}
                        for row in manifest.devices],
            'statistics': [{'statistic_id': old, 'proposed_statistic_id': new}
                           for old, new in manifest.statistics.items()],
        }
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ValueError('discovery_invalid_proposal') from None
