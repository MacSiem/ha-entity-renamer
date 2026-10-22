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


def bind_registry(manifest: Manifest, entities: list[dict], devices: list[dict]) -> Manifest:
    """Bind a preview to fresh stable identities; later apply must revalidate it.

    Recorder destination collisions and dependent reference coverage are separate
    mandatory preflight checks; this result alone never authorizes a mutation.
    """
    by_id = {row["entity_id"]: row for row in entities}
    by_device = {row["id"]: row for row in devices}
    if len(by_id) != len(entities) or len(by_device) != len(devices):
        raise ValueError("ambiguous_registry")
    bound = []
    for change in manifest.entities:
        current = by_id.get(change.old_id)
        if current is None:
            raise ValueError("source_missing")
        if change.new_id != change.old_id and change.new_id in by_id:
            raise ValueError("destination_occupied")
        if current.get("device_id") != change.device_id:
            raise ValueError("device_changed")
        bound.append(replace(change,
            registry_id=_text(current.get("id"), "registry_id"),
            unique_id=_text(current.get("unique_id"), "unique_id"),
            platform=_text(current.get("platform"), "platform"),
            disabled_by=current.get("disabled_by")))
    if any(change.device_id not in by_device for change in manifest.devices):
        raise ValueError("device_missing")
    return Manifest(tuple(bound), manifest.devices, dict(manifest.statistics))
