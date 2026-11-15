"""Strict semantic check for a dashboard source already in a YAML plan.

This is read-only evidence, not a writer or an authorization. Only ordinary
static YAML is admitted; HA tags, duplicate keys and unknown Lovelace schemas
remain unsupported. The caller must additionally bind the registered route and
its actual native readback to this exact source and preserve its source fence.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

import yaml

from .dashboard_plan import plan_dashboard
from .lovelace_native import revision
from .yaml_plan import Plan


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if type(key) is not str or key in result:
            raise ValueError('dashboard_yaml_duplicate_or_invalid_key')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True, init=False)
class RegisteredDashboardYAML:
    """Task-local evidence bound to one live inventory and exact source plan."""

    hass: object
    native: object
    version: str
    inventory: tuple
    fence: object
    guard: object
    plan: Plan
    mapping_items: tuple
    paths: tuple

    def __init__(self):
        raise ValueError('dashboard_yaml_native_capture_required')

    def check(self):
        from .native_discovery_consumers import _inventory
        self.guard()
        self.fence.check()
        if _inventory(self.hass, self.native, self.version) != self.inventory:
            raise ValueError('dashboard_yaml_inventory_changed')

    def assert_plan(self, plan):
        if plan is not self.plan:
            raise ValueError('dashboard_yaml_plan_changed')


def qualify_dashboard_yaml(plan, path, original, mapping):
    """Prove the entire proposed document equals the native semantic plan."""
    if (type(plan) is not Plan or not plan.ready or type(path) is not str
            or type(original) is not bytes or type(mapping) is not dict):
        raise ValueError('dashboard_yaml_source_unqualified')
    rows = [row for row in plan.files if row.path == path]
    patches = [row for row in plan.patches if row.path == path]
    digest = hashlib.sha256(original).hexdigest()
    if (len(rows) != 1 or rows[0].sha256 != digest or rows[0].size != len(original)
            or len(patches) > 1 or patches and patches[0].expected_sha256 != digest):
        raise ValueError('dashboard_yaml_source_changed')
    proposed = original
    if patches:
        last = len(original) + 1
        for edit in reversed(patches[0].edits):
            if (not 0 <= edit.start <= edit.end <= last
                    or original[edit.start:edit.end] != edit.before):
                raise ValueError('dashboard_yaml_patch_invalid')
            proposed = proposed[:edit.start] + edit.after + proposed[edit.end:]
            last = edit.start
        if proposed != patches[0].proposed_bytes or proposed == original:
            raise ValueError('dashboard_yaml_patch_invalid')
    try:
        before = yaml.load(original, Loader=_UniqueLoader)
        after = yaml.load(proposed, Loader=_UniqueLoader)
        semantic = plan_dashboard(before, mapping)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise ValueError('dashboard_yaml_semantics_unqualified') from None
    if semantic.markers or revision(semantic.value) != revision(after) or bool(semantic.changes) != bool(patches):
        raise ValueError('dashboard_yaml_semantics_mismatch')
    return before, after


async def async_qualify_registered_dashboard_yaml(hass, observed, plan, mapping, *, guard):
    """Bind static YAML semantics to every actual registered native route.

    All reads are fenced against the same detached native inventory. This does
    not install an effect adapter or permit dashboard migration by itself.
    """
    from .discovery import _plain
    from .discovery_final_fence import _ConsumerFence
    from .lovelace_native import NativeLovelace
    from .native_discovery_consumers import DiscoveryConsumers, _freeze, _inventory, _yaml_file
    from .native_input_baseline import _drained, _signature
    from .yaml_plan import Limits, _Reader

    if type(observed) is not DiscoveryConsumers or observed.dashboard.status != 'COMPLETE':
        raise ValueError('dashboard_yaml_native_evidence_required')
    native = NativeLovelace(hass)
    inventory = _inventory(hass, native, observed.version)
    evidence = observed.dashboard.value
    if inventory != getattr(evidence, '_native_inventory_token', None):
        raise ValueError('dashboard_yaml_inventory_changed')
    if len({item.filename for item in evidence.yaml_sources}) != len(evidence.yaml_sources):
        raise ValueError('dashboard_yaml_route_ambiguous')
    fence = _ConsumerFence(hass, observed, _plain, names=('dashboard',))

    def check():
        guard()
        fence.check()
        if _inventory(hass, native, observed.version) != inventory:
            raise ValueError('dashboard_yaml_inventory_changed')

    def read(source):
        reader = _Reader(hass.config.config_dir, Limits())
        try:
            raw, info = reader.read(source.filename)
            if (_signature(info), hashlib.sha256(raw).hexdigest()) != (source.file_identity, source.sha256):
                raise ValueError('dashboard_yaml_source_changed')
            return raw
        finally:
            os.close(reader.root)

    await _drained(hass.async_add_executor_job, fence.physical)
    check()
    paths = []
    for source in evidence.yaml_sources:
        check()
        raw = await _drained(hass.async_add_executor_job, lambda: read(source))
        check()
        before, _ = qualify_dashboard_yaml(plan, source.filename, raw, mapping)
        manager = native._managers().get(source.route)
        actual = await manager.async_load(False)
        check()
        if _plain(_freeze(actual)) != before:
            raise ValueError('dashboard_yaml_native_source_mismatch')
        identity = await _drained(hass.async_add_executor_job,
            lambda: _yaml_file(hass.config.config_dir, source.filename))
        check()
        if identity != (source.file_identity, source.sha256):
            raise ValueError('dashboard_yaml_source_changed')
        paths.append(source.filename)
    await _drained(hass.async_add_executor_job, fence.physical)
    check()
    evidence = object.__new__(RegisteredDashboardYAML)
    for key, value in (('hass', hass), ('native', native), ('version', observed.version),
                       ('inventory', inventory), ('fence', fence), ('guard', guard),
                       ('plan', plan), ('mapping_items', tuple(sorted(mapping.items()))),
                       ('paths', tuple(paths))):
        object.__setattr__(evidence, key, value)
    evidence.check()
    return evidence
