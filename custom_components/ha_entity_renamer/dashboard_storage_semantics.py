"""Plan known storage dashboard references from detached native evidence.

Read-only qualification only. The caller must retain the inventory fence,
bind the preparations to the durable plan and supply receipted native effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .dashboard_plan import plan_dashboard
from .discovery import _plain
from .lovelace_native import PreparedDashboard, Snapshot, revision
from .native_discovery_consumers import DiscoveryConsumers


def qualify_dashboard_storage(observed, mapping):
    if (type(observed) is not DiscoveryConsumers or observed.dashboard.status != 'COMPLETE'
            or type(mapping) is not dict):
        raise ValueError('dashboard_storage_evidence_required')
    evidence = observed.dashboard.value
    if getattr(evidence, '_native_inventory_token', None) is None:
        raise ValueError('dashboard_storage_native_capture_required')
    changes = []
    identities = set()
    for row in evidence.snapshots:
        if row.dashboard.identity in identities:
            raise ValueError('dashboard_storage_identity_ambiguous')
        identities.add(row.dashboard.identity)
        if row.config is None:
            continue
        before = _plain(row.config)
        if revision(before) != row.revision:
            raise ValueError('dashboard_storage_evidence_changed')
        proposal = plan_dashboard(before, mapping)
        if proposal.markers:
            raise ValueError('dashboard_storage_semantics_unqualified')
        if proposal.changes:
            snapshot = Snapshot(row.dashboard, before, row.revision, row.file_identity)
            if snapshot.file_identity is None:
                raise ValueError('dashboard_storage_file_missing')
            changes.append(PreparedDashboard(snapshot, proposal.value, revision(proposal.value)))
    return tuple(changes)


@dataclass(frozen=True, init=False)
class NativeDashboardStorage:
    """One task-local original capture; possession does not authorize writes."""
    hass: object
    native: object
    fence: object
    changes: tuple
    files: tuple
    tracked_names: tuple
    token: tuple
    version: str
    guard: object
    mapping_items: tuple

    def __init__(self):
        raise ValueError('dashboard_storage_native_capture_required')

    def check(self):
        from .native_discovery_consumers import _inventory
        self.guard()
        self.fence.check()
        if _inventory(self.hass, self.native, self.version) != self.token:
            raise ValueError('dashboard_storage_inventory_changed')


async def async_prepare_dashboard_storage(hass, observed, mapping, *, guard):
    """Prove each detached snapshot against the settled real manager/disk."""
    from .discovery_final_fence import _ConsumerFence
    from .lovelace_native import NativeLovelace
    from .native_discovery_consumers import _inventory
    from .native_input_baseline import _drained

    changes = qualify_dashboard_storage(observed, mapping)
    native = NativeLovelace(hass)
    evidence = observed.dashboard.value
    token = getattr(evidence, '_native_inventory_token', None)
    if token is None or _inventory(hass, native, observed.version) != token:
        raise ValueError('dashboard_storage_inventory_changed')
    fence = _ConsumerFence(hass, observed, _plain, names=('dashboard',))

    def check():
        guard()
        fence.check()
        if _inventory(hass, native, observed.version) != token:
            raise ValueError('dashboard_storage_inventory_changed')

    await _drained(hass.async_add_executor_job, fence.physical)
    check()
    for row in evidence.snapshots:
        current = await native.read(row.dashboard)
        check()
        if (current.config != _plain(row.config) or current.revision != row.revision
                or current.file_identity != row.file_identity):
            raise ValueError('dashboard_storage_source_changed')
    names = {'lovelace_dashboards'}
    files = []
    for row in evidence.snapshots:
        key = row.dashboard.key
        if re.fullmatch(r'lovelace(?:\.[A-Za-z0-9_-]+)?', key) is None:
            raise ValueError('dashboard_storage_key_unqualified')
        names.add(key)
        if row.file_identity is not None:
            device, inode, digest = row.file_identity
            files.append(('.storage/' + key, digest, device, inode))
    from .lovelace_native import durable_snapshot
    _, identity = await _drained(hass.async_add_executor_job, lambda: durable_snapshot(
        Path(hass.config.path('.storage', 'lovelace_dashboards')), 'lovelace_dashboards'))
    check()
    if identity is not None:
        device, inode, digest = identity
        files.append(('.storage/lovelace_dashboards', digest, device, inode))
    await _drained(hass.async_add_executor_job, fence.physical)
    check()
    result = object.__new__(NativeDashboardStorage)
    for key, value in (('hass', hass), ('native', native), ('fence', fence), ('changes', changes),
                       ('files', tuple(sorted(files))), ('tracked_names', tuple(sorted(names))),
                       ('token', token), ('version', observed.version), ('guard', guard),
                       ('mapping_items', tuple(sorted(mapping.items())))):
        object.__setattr__(result, key, value)
    return result
