"""Bounded ConfigEntry reference plans for HA 2026.10.2 / Powercalc 1.26.1.

No IO. A plan is not a completeness guarantee or a mutation authorization.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

from .reference_planner import Consumer, Context, Marker, Scope, Syntax, plan_references

# Config-flow schemas and runtime consumption were inspected in installed HA.
NATIVE_FIELDS = {
    'group': ('entities', 'many'), 'integration': ('source', 'one'),
    'utility_meter': ('source', 'one'), 'threshold': ('entity_id', 'one'),
    'min_max': ('entity_ids', 'many'), 'filter': ('entity_id', 'one'),
    'statistics': ('entity_id', 'one'), 'derivative': ('source', 'one'),
}
# These helper_integration listeners update options and schedule reload themselves.
NATIVE_LISTENERS = frozenset({'integration', 'utility_meter', 'threshold', 'statistics', 'derivative'})
POWERCALC_ONE = frozenset({'entity_id', 'availability_entity', 'power_sensor_id',
    'energy_sensor_id', 'energy_price_sensor', 'main_power_sensor'})
POWERCALC_MANY = frozenset({'group_power_entities', 'group_energy_entities',
    'exclude_entities', 'subtract_entities'})
TEMPLATE_FIELDS = frozenset({'state', 'value_template', 'availability', 'hvac_modes',
    'hvac_mode', 'hvac_action', 'current_temperature', 'target_temperature', 'position',
    'in_zones', 'latitude', 'longitude', 'location_accuracy', 'event_type', 'event_types',
    'percentage', 'url', 'level', 'hs', 'temperature', 'code_format', 'options',
    'installed_version', 'latest_version', 'in_progress', 'release_summary',
    'release_url', 'title', 'update_percentage', 'fan_speed', 'condition', 'humidity',
    'forecast_daily', 'forecast_hourly'})
# Identifiers and display/config metadata, never reference rewrite scopes.
PASSIVE = frozenset({'name', 'unique_id', 'device_id', 'template_type', 'type',
    'unit_of_measurement', 'icon', 'device_class', 'state_class'})
_TOKEN = re.compile(r'(?<![a-zA-Z0-9_])([a-z_][a-z0-9_]*\.[a-z0-9_]+)(?![a-zA-Z0-9_])')
_DYNAMIC = re.compile(r'{{|{%|\bstates\s*[.\[(]|\b(?:expand|is_state|state_attr)\s*\(')


@dataclass(frozen=True)
class HelperPlan:
    before: dict[str, Any]
    after: dict[str, Any]
    changes: tuple
    markers: tuple[Marker, ...]
    # Exactly these changes may already have been performed by a registry listener.
    listener_paths: tuple[tuple, ...]
    coverage: str = 'installed_schema_fields_only'

    @property
    def blocked(self):
        return bool(self.markers)


def plan_helper(snapshot: dict, mapping: dict[str, str]) -> HelperPlan:
    """Snapshot: identity, domain, data, options, subentries (see helper_native).

    Native data layouts older than the installed schema remain unsupported.
    Subentries are inventoried; no inspected domain uses them, so affected or
    dynamic subentry data blocks coverage instead of guessing their semantics.
    """
    domain = snapshot['domain']
    # Planner validates JSON shape, cycles, map syntax and size before traversal.
    checked = plan_references(snapshot, mapping, Context(Consumer.TEMPLATE, ()))
    before = checked.value
    scopes, markers = [], []
    listeners = []

    def add(path, value, kind):
        if kind == 'one' and not isinstance(value, str):
            markers.append(Marker(path, 'invalid_helper_reference', 'Expected entity ID or registry UUID'))
            return
        if kind == 'many' and (not isinstance(value, list) or any(not isinstance(x, str) for x in value)):
            markers.append(Marker(path, 'invalid_helper_reference', 'Expected entity ID list'))
            return
        values = value if isinstance(value, list) else [value]
        if any(_DYNAMIC.search(item) for item in values):
            markers.append(Marker(path, 'dynamic_helper_reference', 'Dynamic source cannot be resolved by scalar schema'))
            return
        scopes.append(Scope(path, Syntax.ENTITY_TREE))

    if domain in NATIVE_FIELDS:
        field, kind = NATIVE_FIELDS[domain]
        if field in before['options']:
            path = ('options', field)
            add(path, before['options'][field], kind)
            if domain in NATIVE_LISTENERS:
                listeners.append(path)
    elif domain == 'powercalc':
        for field in POWERCALC_ONE | POWERCALC_MANY:
            if field in before['data']:
                add(('data', field), before['data'][field], 'one' if field in POWERCALC_ONE else 'many')
        if 'entity_id' in before['data']:
            listeners.append(('data', 'entity_id'))
        cost = before['data'].get('cost')
        if isinstance(cost, dict) and 'energy_sensor_id' in cost:
            add(('data', 'cost', 'energy_sensor_id'), cost['energy_sensor_id'], 'one')
    elif domain == 'template':
        for field in TEMPLATE_FIELDS:
            if field in before['options']:
                path = ('options', field)
                if not isinstance(before['options'][field], str):
                    markers.append(Marker(path, 'invalid_template_field', 'Expected template string'))
                else:
                    scopes.append(Scope(path, Syntax.JINJA))
        additional = before['options'].get('additional_options', {})
        if isinstance(additional, dict) and 'availability' in additional:
            path = ('options', 'additional_options', 'availability')
            if not isinstance(additional['availability'], str):
                markers.append(Marker(path, 'invalid_template_field', 'Expected template string'))
            else:
                scopes.append(Scope(path, Syntax.JINJA))

    scope_paths = {s.path for s in scopes}

    def unknown(value, path):
        if path in scope_paths:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                # Top-level passive metadata only; never suppress nested unknown refs.
                if len(path) == 1 and key in PASSIVE:
                    continue
                if isinstance(key, str) and any(m.group(1) in mapping for m in _TOKEN.finditer(key)):
                    markers.append(Marker(path, 'unsupported_reference_key', 'Reference-bearing key outside schema'))
                unknown(child, path + (key,))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                unknown(child, path + (index,))
        elif isinstance(value, str) and (_DYNAMIC.search(value) or any(m.group(1) in mapping for m in _TOKEN.finditer(value))):
            markers.append(Marker(path, 'unsupported_helper_reference', 'Affected or dynamic reference outside installed schema'))

    for root in ('data', 'options', 'subentries'):
        unknown(before[root], (root,))
    result = plan_references(before, mapping, Context(Consumer.TEMPLATE, tuple(scopes)))
    retained = tuple(m for m in result.markers if m.code != 'no_supported_scopes')
    # A known listener may only produce one exact old→new scalar, never arbitrary data.
    listener_paths = tuple(c.path for c in result.changes if c.path in listeners and c.location == 'value')
    return HelperPlan(before, result.value, result.changes, retained + tuple(markers), listener_paths)


def rebase_after_listeners(plan: HelperPlan, current: dict) -> dict:
    """Accept baseline or exact native forms, preserve all listener changes.

    No broad whitelist: changing any unrelated value, identity, subentry, or
    adapter-owned reference is a conflict. Multiple listener fields independently
    accept only their before/after value. Caller must hold transaction lock.
    """
    if plan.blocked:
        raise ValueError('helper_coverage_blocked')
    expected = deepcopy(plan.before)
    for path in plan.listener_paths:
        old_parent, new_parent, now_parent, expected_parent = plan.before, plan.after, current, expected
        for part in path[:-1]:
            old_parent, new_parent = old_parent[part], new_parent[part]
            now_parent, expected_parent = now_parent[part], expected_parent[part]
        key = path[-1]
        if now_parent[key] not in (old_parent[key], new_parent[key]):
            raise ValueError('helper_listener_conflict')
        expected_parent[key] = now_parent[key]
    if expected != current:
        raise ValueError('helper_snapshot_conflict')
    return deepcopy(plan.after)
