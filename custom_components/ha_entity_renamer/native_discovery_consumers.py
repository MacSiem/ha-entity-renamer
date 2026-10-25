"""Pinned, server-owned consumer evidence; never an effect authorization.

Only the existing NativeLovelace.read may prime its native cache after a strict
independent read. No factory, Energy initialization, YAML loader or save is used.
All returned JSON trees are detached and recursively immutable. Keep them private.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from types import MappingProxyType, coroutine

from .energy_native import EnergySnapshot, NativeEnergy
from .lovelace_native import NativeLovelace, Snapshot, revision
from .native_input_baseline import _open_absolute_directory, _signature
from .persistence import PersistenceError


@dataclass(frozen=True)
class ConsumerOutcome:
    status: str
    reason: str
    value: object | None = None


@dataclass(frozen=True)
class YAMLRoute:
    route: str | None
    filename: str  # server-only, relative to the pinned config directory
    metadata: object
    file_identity: tuple
    sha256: str


@dataclass(frozen=True)
class DashboardEvidence:
    snapshots: tuple[Snapshot, ...]
    yaml_sources: tuple[YAMLRoute, ...]
    inventory_revision: str
    registered_metadata: tuple[tuple[str | None, object], ...]


@dataclass(frozen=True)
class DiscoveryConsumers:
    version: str
    dashboard: ConsumerOutcome
    energy: ConsumerOutcome


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise PersistenceError('consumer_json_schema')


@coroutine
def _guarded(awaitable, guard):
    """Fence every suspension, including awaits inside the reused native reader."""
    iterator = awaitable.__await__()
    try:
        guard()
        yielded = next(iterator)
        while True:
            guard()
            try:
                value = yield yielded
            except BaseException as error:
                guard()
                yielded = iterator.throw(error)
            else:
                guard()
                yielded = iterator.send(value)
    except StopIteration as result:
        guard()
        return result.value
    finally:
        iterator.close()


def _failure(error):
    # The strict disk readers preserve typed OSError as __cause__. Never inspect
    # exception strings: they may contain configuration, paths or credentials.
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PermissionError):
            return ConsumerOutcome('DENIED', 'read_access_denied')
        current = current.__cause__
    return ConsumerOutcome('UNKNOWN', 'consumer_evidence_unavailable')


def _inventory(hass, native, version):
    from homeassistant.components.lovelace.dashboard import LovelaceStorage, LovelaceYAML
    data = hass.data.get(native._data_key)
    managers = native._managers()
    definitions = data.yaml_dashboards
    if type(definitions) is not dict or None not in managers:
        raise PersistenceError('consumer_lovelace_inventory')
    rows, yaml_routes = [], set()
    for route, manager in managers.items():
        if (route is not None and type(route) is not str) or manager.hass is not hass:
            raise PersistenceError('consumer_lovelace_identity')
        if type(manager) is LovelaceStorage:
            descriptor = native._descriptor(route, manager)
            native._settled(manager)
            rows.append((route, id(manager), descriptor.identity, None))
        elif type(manager) is LovelaceYAML:
            if manager.url_path != route:
                raise PersistenceError('consumer_yaml_identity')
            if route is None:
                # 2025 legacy YAML default; 2026 always registers storage None,
                # with the legacy YAML root migrated to named route "lovelace".
                if version != '2025.6.0' or manager.config is not None:
                    raise PersistenceError('consumer_yaml_default')
                filename = 'ui-lovelace.yaml'
            else:
                metadata = definitions.get(route)
                if (type(metadata) is not dict or metadata.get('mode') != 'yaml'
                        or manager.config != {**metadata, 'url_path': route}):
                    raise PersistenceError('consumer_yaml_inventory')
                filename = metadata.get('filename')
                yaml_routes.add(route)
            filename = _filename(filename)
            if Path(manager.path) != Path(hass.config.path(filename)):
                raise PersistenceError('consumer_yaml_path_identity')
            rows.append((route, id(manager), revision(manager.config), filename))
        else:
            raise PersistenceError('consumer_lovelace_manager_type')
    if yaml_routes != set(definitions):
        raise PersistenceError('consumer_yaml_inventory_not_loaded')
    return (id(data), id(managers), revision(definitions), tuple(rows))


def _filename(value):
    if (type(value) is not str or not value or '\\' in value or '\x00' in value
            or PurePosixPath(value).is_absolute()
            or any(piece in ('', '.', '..') for piece in value.split('/'))
            or 'secrets.yaml' in value.split('/')):
        raise PersistenceError('consumer_yaml_filename')
    return value


def _open_yaml_relative(root_fd, filename):
    fd = os.dup(root_fd)
    try:
        pieces = _filename(filename).split('/')
        for index, piece in enumerate(pieces):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(pieces) - 1:
                flags |= os.O_DIRECTORY
            child = os.open(piece, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _yaml_file(root, filename):
    """No-follow each ancestor and final member; return no parsed secret values."""
    root_fd = _open_absolute_directory(root)
    fd = None
    try:
        fd = _open_yaml_relative(root_fd, filename)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 2_000_000:
            raise PersistenceError('consumer_yaml_file')
        raw = b''
        while chunk := os.read(fd, min(65536, 2_000_001 - len(raw))):
            raw += chunk
            if len(raw) > 2_000_000:
                raise PersistenceError('consumer_yaml_file')
        if _signature(before) != _signature(os.fstat(fd)):
            raise PersistenceError('consumer_yaml_file_changed')
        named = _open_yaml_relative(root_fd, filename)
        try:
            if _signature(before) != _signature(os.fstat(named)):
                raise PersistenceError('consumer_yaml_name_changed')
        finally:
            os.close(named)
        return _signature(before), hashlib.sha256(raw).hexdigest()
    finally:
        if fd is not None:
            os.close(fd)
        os.close(root_fd)


async def _dashboards(hass, native, version, baseline, guard):
    def check():
        guard()
        if _inventory(hass, native, version) != baseline:
            raise PersistenceError('consumer_lovelace_inventory_changed')
    check()
    storage, yaml = await _guarded(native.enumerate(), check)
    snapshots = []
    for descriptor in storage:
        snapshot = await _guarded(native.read(descriptor), check)
        snapshots.append(snapshot)
    sources = []
    filenames = {row[0]: row[3] for row in baseline[3] if row[3] is not None}
    if set(yaml) != set(filenames):
        raise PersistenceError('consumer_yaml_inventory_changed')
    for route in yaml:
        filename = filenames[route]
        identity, digest = await _guarded(hass.async_add_executor_job(
            _yaml_file, hass.config.config_dir, filename), check)
        sources.append(YAMLRoute(route, filename,
            _freeze(deepcopy(native._managers()[route].config)), identity, digest))
    # Whole collection metadata is independently compared again, including all
    # members which the proposal does not modify and admin-only dashboards.
    if await _guarded(native.enumerate(), check) != (storage, yaml):
        raise PersistenceError('consumer_lovelace_inventory_changed')
    check()
    return DashboardEvidence(tuple(Snapshot(row.dashboard, _freeze(row.config),
        row.revision, row.file_identity) for row in snapshots), tuple(sources),
        revision([[row[0], row[2], row[3]] for row in baseline[3]]),
        tuple((route, _freeze(deepcopy(manager.config)))
              for route, manager in native._managers().items()))


def _energy_manager(hass):
    from homeassistant.components.energy.data import EnergyManager
    manager = hass.data.get('energy_manager')
    # 2025 singleton uses Event while initializing, 2026 uses Future. Neither is
    # awaited here: doing so could adopt a newly created manager as evidence.
    if isinstance(manager, (asyncio.Event, asyncio.Future)) or type(manager) is not EnergyManager:
        raise PersistenceError('consumer_energy_manager_unavailable')
    if manager._hass is not hass:
        raise PersistenceError('consumer_energy_manager_identity')
    return manager


def _energy_schema(value, version):
    if value is None:
        return
    from homeassistant.components.energy.data import DEVICE_CONSUMPTION_SCHEMA, ENERGY_SOURCE_SCHEMA
    keys = {'energy_sources', 'device_consumption'}
    if version == '2026.10.2':
        keys.add('device_consumption_water')
    if type(value) is not dict or set(value) != keys or any(type(v) is not list for v in value.values()):
        raise PersistenceError('consumer_energy_schema')
    detached = deepcopy(value)
    if version == '2025.6.0':
        import voluptuous as vol
        ENERGY_SOURCE_SCHEMA(detached['energy_sources'])
        consumer_schema = vol.Schema(DEVICE_CONSUMPTION_SCHEMA)
    else:
        import probatio
        probatio.Schema(ENERGY_SOURCE_SCHEMA)(detached['energy_sources'])
        consumer_schema = DEVICE_CONSUMPTION_SCHEMA
    for key in keys - {'energy_sources'}:
        for consumer in detached[key]:
            consumer_schema(consumer)


async def _energy(hass, native, version, baseline, guard):
    def check():
        guard()
        if _energy_manager(hass) is not native.manager or revision(native.manager.data) != baseline:
            raise PersistenceError('consumer_energy_changed')
        native._settled()
    check()
    snapshot = await _guarded(native.read(), check)
    _energy_schema(snapshot.value, version)
    check()
    return EnergySnapshot(_freeze(snapshot.value), snapshot.revision, snapshot.file_identity)


async def async_observe_discovery_consumers(hass, *, guard):
    """Return independent family outcomes, each with full frozen private evidence.

    Missing manager is UNKNOWN; only a loaded real manager plus strict durable
    absence can prove Energy value=None. Call under the caller's operation lock.
    Caller must reobserve/compare this capture around later YAML graph awaits.
    """
    from homeassistant.const import __version__ as version
    guard()
    dashboard = energy = ConsumerOutcome('UNKNOWN', 'consumer_evidence_unavailable')
    lovelace = energy_native = None
    try:
        lovelace = NativeLovelace(hass)
        dashboard_baseline = _inventory(hass, lovelace, version)
    except Exception as error:
        lovelace = None
        dashboard = _failure(error)
    try:
        manager = _energy_manager(hass)
        energy_native = NativeEnergy(hass, manager)
        energy_baseline = revision(manager.data)
        energy_native._settled()
    except Exception as error:
        energy_native = None
        energy = _failure(error)
    first_dashboard = first_energy = None
    if lovelace is not None:
        try:
            first_dashboard = await _guarded(_dashboards(hass, lovelace, version,
                dashboard_baseline, guard), guard)
        except Exception as error:
            dashboard = _failure(error)
    if energy_native is not None:
        try:
            first_energy = await _guarded(_energy(hass, energy_native, version,
                energy_baseline, guard), guard)
        except Exception as error:
            energy = _failure(error)
    if first_dashboard is not None:
        try:
            final = await _guarded(_dashboards(hass, lovelace, version,
                dashboard_baseline, guard), guard)
            if final != first_dashboard:
                raise PersistenceError('consumer_dashboard_changed')
            dashboard = ConsumerOutcome('COMPLETE', '', first_dashboard)
        except Exception as error:
            dashboard = _failure(error)
    if first_energy is not None:
        try:
            final = await _guarded(_energy(hass, energy_native, version,
                energy_baseline, guard), guard)
            if final != first_energy:
                raise PersistenceError('consumer_energy_changed')
            energy = ConsumerOutcome('COMPLETE', '', first_energy)
        except Exception as error:
            energy = _failure(error)
    # Final synchronous fences catch changes in either family during the last
    # other-family await; do not replace a stale first snapshot with a newer one.
    if dashboard.status == 'COMPLETE':
        try:
            guard()
            if _inventory(hass, lovelace, version) != dashboard_baseline:
                raise PersistenceError('consumer_dashboard_changed')
            for snapshot in first_dashboard.snapshots:
                manager = lovelace._manager(snapshot.dashboard)
                actual = None if manager._data is None else manager._data.get('config')
                if revision(actual) != snapshot.revision:
                    raise PersistenceError('consumer_dashboard_changed')
        except Exception as error:
            dashboard = _failure(error)
    if energy.status == 'COMPLETE':
        try:
            guard()
            if _energy_manager(hass) is not energy_native.manager or revision(energy_native.manager.data) != energy_baseline:
                raise PersistenceError('consumer_energy_changed')
            energy_native._settled()
        except Exception as error:
            energy = _failure(error)
    guard()
    return DiscoveryConsumers(version, dashboard, energy)
