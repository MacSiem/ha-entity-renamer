"""Read-only ORIGINAL capture of native Powercalc previous-state inputs.

No IO effect, activation or durable receipt. This is a prerequisite for a
Powercalc composition; it must be persisted inside the existing preparation
before a plan can rely on it. Natural later samples need a typed unload witness,
not an implicit refresh of this capture.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from .native_input_baseline import MAX_FILE_BYTES, _drained, _json, _loads, _open_absolute_directory, _signature
from .powercalc_runtime_observer import _guard
from .powercalc_source_binding import PowercalcVendor, async_bind_powercalc_sources
from .yaml_plan import _Reader, Limits
from .yaml_validation import assert_validation_current


class PowercalcPreviousOriginalError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise PowercalcPreviousOriginalError('powercalc_previous_original_' + reason) from None


@dataclass(frozen=True)
class PowercalcGroupOriginal:
    entity_id: str
    registry_id: str
    members: tuple[tuple[str, str], ...]
    full_total: str


@dataclass(frozen=True)
class PowercalcPreviousOriginal:
    receipt_sha256: str
    sources_sha256: str
    core_version: str
    groups: tuple[PowercalcGroupOriginal, ...]
    memory_json: bytes
    disk_raw: bytes | None
    disk_identity: tuple[int, int, int] | None
    root_identity: tuple[int, int, int]
    storage_identity: tuple[int, int, int]


class _PinnedPSS:
    """Keep exact root/storage/file descriptors until the last async fence."""
    def __init__(self, receipt, *, key='powercalc_group', input_state=None):
        _need(key in ('powercalc_group', 'core.restore_state'), 'pinned_key_required')
        if input_state is not None:
            from .accepted_input_state import AcceptedInputState
            _need(type(input_state) is AcceptedInputState, 'accepted_state_required')
        self.receipt, self.key, self.input_state = receipt, key, input_state
        self.reader = None
        self.storage = self.file = None
        self.root_sig = self.storage_sig = self.file_sig = self.raw = None

    def _assert_sources(self):
        if self.input_state is None:
            assert_validation_current(self.receipt)
        else:
            self.input_state.assert_current(self.receipt)

    def capture(self):
        self._assert_sources()
        path = Path(self.receipt.config_root)
        _need(path.is_absolute(), 'root_required')
        safe = _open_absolute_directory(path)
        try:
            self.reader = _Reader(path, Limits(max_file_bytes=MAX_FILE_BYTES))
            self.root_sig = _signature(os.fstat(self.reader.root))
            _need(_signature(os.fstat(safe))[:3] == self.root_sig[:3], 'root_changed')
        finally:
            os.close(safe)
        self.storage = self.reader.open('.storage', directory=True)
        self.storage_sig = _signature(os.fstat(self.storage))
        try:
            self.file = self.reader.open('.storage/' + self.key)
        except FileNotFoundError:
            self.verify()
            return None, None
        self.file_sig = _signature(os.fstat(self.file))
        self.raw = self._read()
        self.verify()
        return self.raw, self.file_sig[:3]

    def _read(self):
        os.lseek(self.file, 0, os.SEEK_SET)
        parts, size = [], 0
        while chunk := os.read(self.file, min(65536, MAX_FILE_BYTES + 1 - size)):
            parts.append(chunk)
            size += len(chunk)
            _need(size <= MAX_FILE_BYTES, 'disk_limit')
        return b''.join(parts)

    def verify(self):
        self._assert_sources()
        root = _open_absolute_directory(self.receipt.config_root)
        try:
            _need(_signature(os.fstat(root))[:3] == self.root_sig[:3], 'root_changed')
        finally:
            os.close(root)
        named = self.reader.open('.storage', directory=True)
        try:
            _need(_signature(os.fstat(named))[:3] == self.storage_sig[:3]
                  and _signature(os.fstat(self.storage))[:3] == self.storage_sig[:3], 'storage_changed')
        finally:
            os.close(named)
        try:
            named = self.reader.open('.storage/' + self.key)
        except FileNotFoundError:
            _need(self.file is None, 'disk_disappeared')
            return
        try:
            _need(self.file is not None and _signature(os.fstat(named)) == self.file_sig
                  and _signature(os.fstat(self.file)) == self.file_sig
                  and self._read() == self.raw, 'disk_changed')
        finally:
            os.close(named)
        self._assert_sources()

    def close(self):
        for fd in (self.file, self.storage, None if self.reader is None else self.reader.root):
            if fd is not None:
                os.close(fd)
        self.file = self.storage = self.reader = None


def _disk_shape(raw):
    if raw is None:
        return
    value = _loads(raw)
    _need(type(value) is dict and set(value) == {'version', 'minor_version', 'key', 'data'}
          and type(value['version']) is int and value['version'] == 2
          and type(value['minor_version']) is int and value['minor_version'] == 1
          and value['key'] == 'powercalc_group' and type(value['data']) is dict,
          'disk_schema')


def _live(hass, vendor, rows):
    from homeassistant.core import State
    from homeassistant.helpers.entity_platform import async_get_platforms
    from homeassistant.helpers.json import json_bytes
    by_id = {row.entity_id: row for row in rows}
    groups = [row for row in rows if row.relation_kind == 'energy_positive_delta']
    _need(groups and all(row.source_verified for row in rows), 'complete_group_graph_required')
    inventory = [entity for platform in async_get_platforms(hass, 'powercalc')
                 for entity in platform.entities.values()]
    entities = {entity.entity_id: entity for entity in inventory}
    _need(len(entities) == len(inventory) == len(rows) and len(by_id) == len(rows)
          and set(entities) == set(by_id), 'native_graph_inventory')
    for row in rows:
        entity = entities[row.entity_id]
        _need(row.runtime_identities and id(entity) == row.runtime_identities[0]
              and entity.registry_entry is not None and entity.registry_entry.id == row.registry_id,
              'native_graph_identity')
    group_results, memory, store = [], {}, None
    for row in sorted(groups, key=lambda item: item.entity_id):
        group = entities.get(row.entity_id)
        _need(type(group) is vendor.classes['GroupedEnergySensor']
              and group.registry_entry is not None and group.registry_entry.id == row.registry_id
              and row.runtime_identities and id(group) == row.runtime_identities[0], 'group_identity')
        current = group._prev_state_store
        _need(type(current) is vendor.classes['PreviousStateStore']
              and hass.data.get('powercalc_group_storage') is current
              and (store is None or store is current), 'native_store_identity')
        store = current
        members = tuple(sorted((source, by_id[source].registry_id) for source, target, sign in row.edges
                               if target == row.entity_id and sign == 1 and source in by_id))
        _need(len(members) == len(row.edges) and members and all(uuid for _, uuid in members)
              and {name for name, _ in members} == group._entities, 'group_membership')
        states = store.states.get(row.entity_id, {})
        _need(type(states) is dict and set(states) <= {name for name, _ in members}
              and all(type(state) is State and state.entity_id == key for key, state in states.items()),
              'owned_previous_states')
        full = group._native_value_exact
        _need(type(full) is Decimal and full.is_finite(), 'native_group_total')
        group_results.append(PowercalcGroupOriginal(row.entity_id, row.registry_id, members, str(full)))
        memory[row.entity_id] = {key: state.json_fragment for key, state in sorted(states.items())}
    data = _loads(json_bytes(memory))
    return tuple(group_results), _json(data), store


async def async_capture_powercalc_previous_original(hass, receipt, evidence, original, *, guard,
                                                    allow_live_progress=False):
    """Capture once before effects; this data alone cannot authorize any write."""
    _need(type(allow_live_progress) is bool, 'live_progress_option')
    _guard(guard)
    bound = await async_bind_powercalc_sources(hass, receipt, evidence, original, guard=guard)
    vendor = PowercalcVendor(hass, with_profiles=True)
    groups, memory, store = _live(hass, vendor, bound.rows)
    pinned = _PinnedPSS(receipt)
    try:
        raw, identity = await _drained(hass.async_add_executor_job, pinned.capture)
        _guard(guard)
        _disk_shape(raw)
        def same_graph(rows):
            return tuple((group.entity_id, group.registry_id, group.members) for group in rows)
        again, memory_after, current_store = _live(hass, vendor, bound.rows)
        _need(current_store is store and same_graph(again) == same_graph(groups)
              and (allow_live_progress or (again == groups and memory_after == memory)),
              'live_capture_changed')
        await _drained(hass.async_add_executor_job, pinned.verify)
        _guard(guard)
        final, final_memory, final_store = _live(hass, vendor, bound.rows)
        _need(final_store is store and same_graph(final) == same_graph(groups)
              and (allow_live_progress or (final == groups and final_memory == memory)),
              'live_capture_changed')
        evidence.assert_receipt(receipt)
        return PowercalcPreviousOriginal(hashlib.sha256(evidence.receipt_json).hexdigest(),
            evidence.powercalc_sources.sha256, receipt.version, final, final_memory, raw, identity,
            pinned.root_sig[:3], pinned.storage_sig[:3])
    finally:
        pinned.close()
