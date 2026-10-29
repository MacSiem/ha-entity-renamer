"""Read-only HA 2025.6.0 / 2026.10.2/2026.10.4 helper callbacks; capture BEFORE registry changes.

Scope: threshold, min_max, group/binary_sensor config-entry helpers only.
No import-time HA dependency. No configuration/state/service/registry writes.
Callbacks prove a coherent current sample, not a future producer event or disk
durability. Inventory methods cover exactly the captured entry set; compose
with the owner's whole-transaction inventory and persistence verification.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
import math
import statistics

from .helper_native import read_entry
from .helper_plan import plan_helper, snapshot_equal


# Exact installed-source audit only: no semver range or inferred compatibility.
_HELPER_SCHEMA_VERSIONS = {
    '2025.6.0': {'threshold': (1, 1), 'min_max': (1, 1), 'group': (1, 1)},
    '2026.10.2': {'threshold': (1, 2), 'min_max': (1, 1), 'group': (1, 1)}, '2026.10.4': {'threshold': (1, 2), 'min_max': (1, 1), 'group': (1, 1)},
}


class ObservationUnavailable(ValueError):
    """Missing evidence is a refusal, never a successful boolean proof."""


def require(condition, reason):
    if not condition:
        raise ObservationUnavailable(reason)


def _helper_schema_version(version, domain):
    require(version in _HELPER_SCHEMA_VERSIONS, 'helper_ha_version_unsupported')
    require(domain in _HELPER_SCHEMA_VERSIONS[version], 'helper_domain_unsupported')
    return _HELPER_SCHEMA_VERSIONS[version][domain]


@dataclass(frozen=True)
class RegistryMappingProof:
    """Owner derives this from verified PreparedRegistryChange, never UI JSON."""

    old: str
    new: str
    registry_id: str


def registry_identity(row):
    return (row.id, row.unique_id, row.platform, row.config_entry_id,
            row.config_subentry_id)


def helper_row(row):
    """Stable helper identity plus settings that reload must not change."""
    return {key: getattr(row, key) for key in (
        'id', 'entity_id', 'unique_id', 'platform', 'config_entry_id',
        'config_subentry_id', 'device_id', 'area_id', 'disabled_by',
        'hidden_by', 'name', 'icon', 'entity_category')}


def finite_number(value):
    require(type(value) in (float, int) and math.isfinite(value),
            'helper_nonfinite_or_nonnumeric_value')
    return float(value)


def threshold_expected(options, value):
    """Independent deterministic branch; history inside hysteresis is unknown."""
    lower, upper, h = options['lower'], options['upper'], options['hysteresis']
    if lower is not None and upper is not None:
        if value > upper + h:
            return False, 'above'
        if value < lower - h:
            return False, 'below'
        if value > lower + h and value < upper - h:
            return True, 'in_range'
    elif lower is not None:
        if value < lower - h:
            return True, 'below'
        if value > lower + h:
            return False, 'above'
    elif upper is not None:
        if value > upper + h:
            return True, 'above'
        if value < upper - h:
            return False, 'below'
    raise ObservationUnavailable('threshold_history_required')


def min_max_expected(kind, values, digits):
    """All sources must be finite/current; last requires external event evidence."""
    require(bool(values), 'min_max_empty_sources')
    require(kind != 'last', 'min_max_last_history_required')
    if kind == 'min':
        return min(values)
    if kind == 'max':
        return max(values)
    if kind == 'mean':
        return round(statistics.mean(values), digits)
    if kind == 'median':
        return round(statistics.median(values), digits)
    if kind == 'range':
        return round(max(values) - min(values), digits)
    if kind == 'sum':
        return round(sum(values), digits)
    raise ObservationUnavailable('min_max_type_unsupported')


class NativeHelperObservers:
    """One instance per transaction, under the owner's exclusive lock.

    Construct via capture(), before any registry rename. Supply every helper
    whose coverage these callbacks will assert, including unchanged helpers.
    Unknown domains/layouts, stale IDs and missing active values raise.
    Inactive helpers prove preserved absence of runtime and source identities;
    they do not claim an active value sample. Owner separately accounts for unsupported integrations and sources.
    """

    def __init__(self, hass, before, after, source_rows, helper_rows, modes,
                 mapping, *, settle_timeout):
        self.hass = hass
        self.before, self.after = deepcopy(before), deepcopy(after)
        self.source_rows = deepcopy(source_rows)
        self.helper_rows = deepcopy(helper_rows)
        self.modes = dict(modes)
        self.mapping = dict(mapping)
        require(0 < settle_timeout <= 60, 'helper_settle_timeout_invalid')
        self.settle_timeout = settle_timeout
        self._all_entry_ids = frozenset(e.entry_id for e in hass.config_entries.async_entries())

    @classmethod
    async def capture(cls, hass, snapshots, mapping_proof, *, settle_timeout=15, activation_lease=None):
        from homeassistant.const import __version__
        from homeassistant.helpers import entity_registry as er

        require(__version__ in _HELPER_SCHEMA_VERSIONS, 'helper_ha_version_unsupported')
        snapshots = tuple(deepcopy(s) for s in snapshots)
        if any(s.get('domain') == 'utility_meter' for s in snapshots):
            require(all(s.get('domain') == 'utility_meter' for s in snapshots),
                    'utility_mixed_scope_unsupported')
            from .utility_meter_observers import NativeUtilityMeterObservers
            return await NativeUtilityMeterObservers.capture(
                hass, snapshots, mapping_proof, settle_timeout=settle_timeout,
                activation_lease=activation_lease)
        require(bool(snapshots), 'helper_observer_empty_scope')
        proof = tuple(mapping_proof)
        require(all(type(p) is RegistryMappingProof for p in proof), 'helper_mapping_proof_type')
        mapping = {p.old: p.new for p in proof}
        require(len(mapping) == len(proof) and len(set(mapping.values())) == len(proof),
                'helper_mapping_proof_ambiguous')
        registry = er.async_get(hass)
        for p in proof:
            row = registry.async_get(p.old)
            require(row is not None and row.id == p.registry_id,
                    'helper_mapping_proof_not_native')
        before, after, sources, rows, modes = {}, {}, {}, {}, {}
        for snapshot in snapshots:
            entry_id = snapshot['identity']['entry_id']
            require(entry_id not in before, 'helper_duplicate_entry')
            require(snapshot_equal(read_entry(hass, entry_id), snapshot), 'helper_capture_stale')
            plan = plan_helper(snapshot, mapping)
            require(not plan.blocked, 'helper_coverage_blocked')
            before[entry_id], after[entry_id] = snapshot, plan.after
            entry = hass.config_entries.async_get_entry(entry_id)
            modes[entry_id] = entry.state.value
            require(entry.state.value in ('loaded', 'not_loaded'), 'helper_unsettled_capture')
            native_rows = er.async_entries_for_config_entry(registry, entry_id)
            require(len(native_rows) == 1 if entry.state.value == 'loaded' else len(native_rows) <= 1,
                    'helper_registry_count_unsupported')
            rows[entry_id] = tuple(helper_row(row) for row in native_rows)
            sources[entry_id] = []
            # Resolve UUIDs via installed registry, never infer presence from syntax.
            for ref in cls._references(snapshot):
                resolved = er.async_validate_entity_id(registry, ref)
                row = registry.async_get(resolved)
                require(row is not None, 'helper_source_unregistered')
                sources[entry_id].append((ref, registry_identity(row)))
        observer = cls(hass, before, after, sources, rows, modes, mapping,
                       settle_timeout=settle_timeout)
        await observer.settle()
        for snapshot in before.values():
            await observer.validate_snapshot(snapshot)
            await observer.verify_activation(snapshot)
            await observer.verify_runtime(snapshot)
        return observer

    @staticmethod
    def _references(snapshot):
        domain, options = snapshot['domain'], snapshot['options']
        if domain == 'threshold':
            return [options['entity_id']]
        if domain == 'min_max':
            return list(options['entity_ids'])
        if domain == 'group' and options.get('group_type') == 'binary_sensor':
            return list(options['entities'])
        raise ObservationUnavailable('helper_domain_or_group_type_unsupported')

    async def settle(self):
        """Drain HA foreground jobs with a deadline, then inspect entry states.

        asyncio.timeout keeps the SAME task: wait_for would create a child
        task whose HA drain can wait on its own parent. Does not cancel native
        reload jobs on timeout; timeout leaves transaction outcome UNKNOWN.
        """
        try:
            async with asyncio.timeout(self.settle_timeout):
                await self.hass.async_block_till_done()
        except TimeoutError as err:
            raise ObservationUnavailable('helper_settle_timeout') from err
        for entry_id in self.before:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            require(entry is not None, 'helper_entry_missing')
            require(entry.state.value in ('loaded', 'not_loaded'),
                    'helper_entry_not_settled')
        return True

    def _bound(self, snapshot):
        entry_id = snapshot['identity']['entry_id']
        require(entry_id in self.before, 'helper_outside_captured_scope')
        require(snapshot_equal(snapshot['identity'], self.before[entry_id]['identity']),
                'helper_entry_identity_or_preferences_changed')
        require(snapshot_equal(snapshot, read_entry(self.hass, entry_id)),
                'helper_manager_snapshot_mismatch')
        return entry_id

    async def validate_snapshot(self, snapshot):
        """Validate desired snapshots too (no manager equality prerequisite)."""
        from homeassistant.const import __version__

        require(__version__ in _HELPER_SCHEMA_VERSIONS, 'helper_ha_version_unsupported')
        entry_id = snapshot['identity']['entry_id']
        require(entry_id in self.before, 'helper_outside_captured_scope')
        baseline = self.before[entry_id]
        require(snapshot_equal(snapshot['identity'], baseline['identity']),
                'helper_entry_identity_or_preferences_changed')
        require(snapshot['domain'] == baseline['domain'], 'helper_domain_changed')
        require(not snapshot['data'] and not snapshot['subentries'], 'helper_layout_unsupported')
        domain, options = snapshot['domain'], snapshot['options']
        self._references(snapshot)  # rejects uninspected group types
        expected = _helper_schema_version(__version__, domain)
        require((snapshot['identity']['version'], snapshot['identity']['minor_version']) == expected,
                'helper_schema_version_unsupported')
        flow = import_module(f'homeassistant.components.{domain}.config_flow')
        if domain == 'threshold':
            require(set(options) <= {'name', 'entity_id', 'lower', 'upper', 'hysteresis'}
                    and {'entity_id', 'lower', 'upper', 'hysteresis'} <= set(options),
                    'threshold_options_layout_unsupported')
            require(options['lower'] is not None or options['upper'] is not None,
                    'threshold_limits_missing')
            finite_number(options['hysteresis'])
            require(options['hysteresis'] >= 0, 'threshold_negative_hysteresis_unsupported')
            for key in ('lower', 'upper'):
                if options[key] is not None:
                    finite_number(options[key])
            if options['lower'] is not None and options['upper'] is not None:
                require(options['lower'] < options['upper'], 'threshold_limits_order_unsupported')
            cleaned = {k: v for k, v in options.items() if v is not None}
            (flow.CONFIG_SCHEMA if 'name' in cleaned else flow.OPTIONS_SCHEMA)(cleaned)
        elif domain == 'min_max':
            require(set(options) <= {'name', 'entity_ids', 'type', 'round_digits'},
                    'min_max_options_layout_unsupported')
            (flow.CONFIG_SCHEMA if 'name' in options else flow.OPTIONS_SCHEMA)(options)
        else:
            require(set(options) <= {'name', 'entities', 'all', 'hide_members', 'group_type'},
                    'group_options_layout_unsupported')
            cleaned = {k: v for k, v in options.items() if k not in ('group_type', 'name')}
            schema = await flow.binary_sensor_options_schema(None)
            schema(cleaned)
        refs = self._references(snapshot)
        require(bool(refs) and all(type(ref) is str for ref in refs), 'helper_sources_empty_or_invalid')
        return True

    def _entity(self, snapshot):
        from homeassistant.helpers import entity_platform as ep, entity_registry as er

        entry_id = self._bound(snapshot)
        registry = er.async_get(self.hass)
        native_rows = er.async_entries_for_config_entry(registry, entry_id)
        require(tuple(helper_row(row) for row in native_rows) == self.helper_rows[entry_id],
                'helper_registry_identity_or_settings_changed')
        entry = self.hass.config_entries.async_get_entry(entry_id)
        candidates = []
        for platform in ep.async_get_platforms(self.hass, snapshot['domain']):
            if platform.config_entry is entry:
                candidates.extend(platform.entities.values())
        if entry.disabled_by is not None or entry.state.value == 'not_loaded':
            require(self.modes[entry_id] == 'not_loaded' and not candidates,
                    'helper_inactive_lifecycle_changed')
            return None  # No active output is claimed; preserve absence of runtime.
        require(self.modes[entry_id] == 'loaded' and entry.state.value == 'loaded',
                'helper_lifecycle_changed')
        require(native_rows[0].disabled_by is None, 'helper_entity_disabled')
        require(len(candidates) == 1, 'helper_platform_entity_missing_or_extra')
        entity = candidates[0]
        require(entity.entity_id == native_rows[0].entity_id and entity.hass is self.hass
                and entity.registry_entry is not None
                and entity.registry_entry.id == native_rows[0].id
                and entity.unique_id == native_rows[0].unique_id,
                'helper_platform_registry_binding_mismatch')
        domain = snapshot['domain']
        platform = 'sensor' if domain == 'min_max' else 'binary_sensor'
        module = import_module(f'homeassistant.components.{domain}.{platform}')
        cls = getattr(module, {'threshold': 'ThresholdSensor', 'min_max': 'MinMaxSensor',
                               'group': 'BinarySensorGroup'}[domain])
        require(type(entity) is cls, 'helper_native_class_unsupported')
        return entity

    async def verify_activation(self, snapshot):
        await self.validate_snapshot(snapshot)
        entity = self._entity(snapshot)
        if entity is None:
            return True
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        expected_refs = [er.async_validate_entity_id(registry, ref)
                         for ref in self._references(snapshot)]
        options, domain = snapshot['options'], snapshot['domain']
        actual = [entity._entity_id] if domain == 'threshold' else list(entity._entity_ids)
        require(actual == expected_refs, 'helper_native_dependencies_mismatch')
        if domain == 'threshold':
            require(entity._hysteresis == options['hysteresis']
                    and getattr(entity, '_threshold_lower', None) == options['lower']
                    and getattr(entity, '_threshold_upper', None) == options['upper'],
                    'threshold_native_settings_mismatch')
        elif domain == 'min_max':
            require(entity._sensor_type == options['type']
                    and entity._round_digits == int(options['round_digits']),
                    'min_max_native_settings_mismatch')
        else:
            require(entity.mode is (all if options['all'] else any), 'group_native_mode_mismatch')
        return True

    def _source_states(self, snapshot, *, require_values=True):
        from homeassistant.helpers import entity_registry as er

        entry_id = snapshot['identity']['entry_id']
        refs = self._references(snapshot)
        baseline = self.source_rows[entry_id]
        require(len(refs) == len(baseline), 'helper_source_count_changed')
        registry = er.async_get(self.hass)
        result = []
        for ref, (old_ref, identity) in zip(refs, baseline):
            require(ref in (old_ref, self.mapping.get(old_ref, old_ref)),
                    'helper_unproven_source_mapping')
            entity_id = er.async_validate_entity_id(registry, ref)
            row = registry.async_get(entity_id)
            require(row is not None and registry_identity(row) == identity,
                    'helper_source_registry_identity_changed')
            if not require_values:
                continue
            require(row.disabled_by is None, 'helper_source_disabled')
            state = self.hass.states.get(entity_id)
            require(state is not None and state.state not in ('unknown', 'unavailable'),
                    'helper_source_value_missing')
            result.append(state)
        return result

    async def verify_runtime(self, snapshot):
        await self.settle()
        await self.verify_activation(snapshot)
        # No await after acquiring this coherent source/object/output sample.
        entity = self._entity(snapshot)
        if entity is None:
            self._source_states(snapshot, require_values=False)
            return True
        states = self._source_states(snapshot)
        output = self.hass.states.get(entity.entity_id)
        require(output is not None and output.state not in ('unknown', 'unavailable'),
                'helper_output_missing')
        options, domain = snapshot['options'], snapshot['domain']
        if domain == 'threshold':
            value = finite_number(float(states[0].state))
            expected, position = threshold_expected(options, value)
            require(entity.sensor_value == value and entity.is_on is expected
                    and entity._state_position == position
                    and output.state == ('on' if expected else 'off')
                    and output.attributes.get('sensor_value') == value,
                    'threshold_sample_value_mismatch')
        elif domain == 'min_max':
            values = [finite_number(float(state.state)) for state in states]
            units = {state.attributes.get('unit_of_measurement') for state in states}
            require(len(units) == 1 and not entity._unit_of_measurement_mismatch,
                    'min_max_unit_mismatch')
            require(set(entity.states) == {state.entity_id for state in states}
                    and all(entity.states.get(state.entity_id) == value
                            for state, value in zip(states, values)),
                    'min_max_native_source_cache_mismatch')
            expected = min_max_expected(options['type'], values, int(options['round_digits']))
            require(entity.native_value == expected and float(output.state) == expected,
                    'min_max_sample_value_mismatch')
        else:
            require(all(state.state in ('on', 'off') for state in states),
                    'group_nonbinary_source_state')
            expected = (all if options['all'] else any)(state.state == 'on' for state in states)
            require(entity.available and entity.is_on is expected
                    and output.state == ('on' if expected else 'off'), 'group_sample_value_mismatch')
        return True

    async def _verify_inventory(self, expected):
        await self.settle()
        require(frozenset(e.entry_id for e in self.hass.config_entries.async_entries())
                == self._all_entry_ids, 'helper_inventory_membership_changed')
        for snapshot in expected.values():
            self._bound(snapshot)
            await self.verify_runtime(snapshot)
        return True

    async def verify_inventory(self, plan):
        """Captured helper set ONLY; plan ownership is checked by coordinator."""
        return await self._verify_inventory(self.after)

    async def verify_original_inventory(self, plan):
        return await self._verify_inventory(self.before)
