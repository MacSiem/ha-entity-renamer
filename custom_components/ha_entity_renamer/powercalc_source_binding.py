"""ORIGINAL-bound fixed/custom-group Powercalc row evidence, never apply authority.

One official vendor/Core pair; full inventory remains UNKNOWN. No factory,
resolver, template render, lifecycle call, source rebaseline or numeric writer.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from types import MethodType

from .native_input_baseline import _receipt_bytes
from .native_unload_code_proof import prove_methods, unchanged
from .powercalc_runtime_observer import assert_powercalc_runtime_current, _guard
from .yaml_source_ledger import SourceLedger, source_graph_digest
from .yaml_validation_worker import _fingerprint, powercalc_fixed_semantics

_TREE = '6f21338bcb02d2b488f8b1e9851b4d20300a888d7a493fdb753f78603f27c91c'
_PREFIX = 'custom_components.powercalc'


def _need(value, reason):
    if not value:
        raise ValueError('powercalc_binding_' + reason) from None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pins():
    data = json.loads(Path(__file__).with_name('powercalc_v1261_sources.json').read_bytes())
    _need(_sha(_json(data['files'])) == _TREE, 'official_pin_catalog_changed')
    return data['files']


def stage_powercalc_bundle(bundle, before, after):
    """Verify the SAME ORIGINAL vendor trees already copied by native staging.

    Do not recreate directories or overwrite any staged source/namespace bytes.
    """
    from .yaml_plan import _Reader, Limits
    import os
    pins = _pins()
    _need(set(bundle) == set(pins) and all(_sha(bundle[p]) == pins[p] for p in pins),
          'official_bundle_changed')
    for root in (before, after):
        target = Path(root) / 'custom_components/powercalc'
        _need(target.is_dir() and target == target.resolve(), 'staged_vendor_root_unverified')
        found = set()
        for path in target.rglob('*'):
            _need(not path.is_symlink(), 'staged_vendor_symlink')
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if path.is_file():
                found.add(path.relative_to(target).as_posix())
        _need(found == set(pins), 'staged_vendor_membership_changed')
        reader = _Reader(str(target), Limits())
        try:
            for relative, raw in bundle.items():
                actual, _ = reader.read(relative)
                _need(actual == raw, 'staged_vendor_source_changed')
        finally:
            os.close(reader.root)


class PowercalcVendor:
    """Process-local exact class/method bindings + full official source file pin."""
    def __init__(self, hass, *, with_profiles=False):
        from homeassistant.const import __version__
        from homeassistant import loader
        _need(__version__ == '2026.10.2', 'vendor_core_pair_unverified')
        loaded = hass.data.get(loader.DATA_INTEGRATIONS, {}).get('powercalc')
        _need(type(loaded) is loader.Integration and loaded.manifest.get('version') == 'v1.26.1',
              'loaded_vendor_unverified')
        self.hass, self.loaded = hass, loaded
        module = sys.modules.get(_PREFIX)
        _need(module is not None, 'vendor_not_loaded')
        self.root = Path(hass.config.config_dir) / 'custom_components/powercalc'
        _need(self.root == self.root.resolve()
              and Path(module.__file__) == self.root / '__init__.py', 'vendor_root_unverified')
        self.classes, refs = {}, []
        classes = (
            ('sensors.power', 'VirtualPowerSensor', ('__init__', 'async_added_to_hass',
                '_register_tracking_listeners', '_get_tracking_entities',
                '_handle_source_entity_state_change', 'calculate_power', '_resolve_calculation_state',
                '_calculate_state_standby_power', 'calculate_standby_power',
                '_apply_power_adjustments', '_apply_multiply_factor',
                '_apply_standby_multiply_factor', 'is_calculation_enabled', 'native_value',
                'source_entity', '_switch_sub_profile_dynamically', '_schedule_sleep_power',
                '_track_standby_power', '_clear_standby_power', '_has_valid_state',
                '_update_power_and_write_state', '_update_power_sensor')),
            ('strategy.fixed', 'FixedStrategy', ('__init__', 'calculate', 'validate_config',
                'get_entities_to_track', 'can_calculate_standby')),
            ('sensors.group.custom', 'GroupedSensor', ('__init__', 'async_added_to_hass',
                'on_start', 'initial_update', 'on_state_change', 'set_new_state',
                '_set_native_value', '_get_state_value_in_native_unit', '_should_throttle',
                '_cancel_update_interval_exceeded_callback')),
            ('sensors.group.custom', 'GroupedPowerSensor', ('calculate_initial_state',
                'calculate_new_state', 'get_summed_state')),
            ('common', 'SourceEntity', ('is_dummy',)),
            ('strategy.strategy_interface', 'PowerCalculationStrategyInterface',
                ('is_enabled', 'set_update_callback', 'on_start')),
        )
        for suffix, name, methods in classes:
            owner = sys.modules.get(_PREFIX + '.' + suffix)
            _need(owner is not None, 'required_vendor_module_not_loaded')
            cls = vars(owner).get(name)
            _need(isinstance(cls, type) and cls.__module__ == owner.__name__, 'vendor_class_changed')
            self.classes[name] = cls
            refs.extend((cls, method) for method in methods)
        for suffix, names in (
            ('strategy.fixed', ('evaluate_to_decimal',)),
            ('sensors.power', ('evaluate_to_decimal',)),
            ('sensors.group.custom', ('parse_decimal', 'convert_to_decimal')),
            ('unit', ('evaluate_to_decimal', 'parse_decimal', 'convert_to_decimal')),
        ):
            module = sys.modules.get(_PREFIX + '.' + suffix)
            _need(module is not None, 'required_vendor_module_not_loaded')
            refs.extend((module, name) for name in names)
        self.refs = tuple(refs)
        from homeassistant.helpers import event
        import homeassistant
        self.ha_root = Path(homeassistant.__file__).resolve().parent
        from homeassistant.core import EventBus
        self.ha_pins = {
            'helpers/event.py': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269',
            'core.py': '1fe2fcfb91fbb6edfb4450dad3ae5ebc4e325664770fedbd607d103d91ce4f19'}
        self.ha_refs = tuple((event, name) for name in (
            '_remove_listener', '_async_dispatch_entity_id_event', '_async_state_filter',
            '_async_track_event', '_async_track_state_change_event',
            'async_track_state_change_event')) + ((EventBus, '_async_remove_listener'),)
        if with_profiles:
            from .powercalc_profile_binding import extend_vendor
            extend_vendor(self)
        self.ha_methods = tuple(getattr(owner, name) for owner, name in self.ha_refs)
        self.ha_codes = tuple((method.fget if type(method) is property else method).__code__
                              for method in self.ha_methods)
        self.methods = tuple(getattr(owner, name) for owner, name in self.refs)
        self.codes = tuple((method.fget if type(method) is property else method).__code__
                           for method in self.methods)

    def bindings(self):
        from homeassistant import loader
        _need(self.hass.data.get(loader.DATA_INTEGRATIONS, {}).get('powercalc') is self.loaded
              and self.loaded.manifest.get('version') == 'v1.26.1', 'loaded_vendor_changed')
        unchanged(self.refs, self.methods, self.codes)
        unchanged(self.ha_refs, self.ha_methods, self.ha_codes)
        for name, cls in self.classes.items():
            _need(vars(sys.modules[cls.__module__]).get(name) is cls, 'vendor_class_changed')

    def sources(self):
        """Executor-only disk proof; returned actual bytes are staging input."""
        pins, result = _pins(), {}
        found = set()
        for path in self.root.rglob('*'):
            _need(not path.is_symlink(), 'vendor_symlink')
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if path.is_file():
                found.add(path.relative_to(self.root).as_posix())
        _need(found == set(pins), 'vendor_source_membership_changed')
        from .yaml_plan import _Reader, Limits
        reader = _Reader(str(self.root), Limits())
        try:
            for path, digest in pins.items():
                raw, _ = reader.read(path)
                _need(_sha(raw) == digest, 'vendor_source_changed')
                result[path] = raw
        finally:
            import os
            os.close(reader.root)
        methods, codes = prove_methods(self.root, pins, self.refs)
        _need(methods == self.methods and all(a is b for a, b in zip(codes, self.codes, strict=True)),
              'vendor_code_changed')
        ha_methods, ha_codes = prove_methods(self.ha_root, self.ha_pins, self.ha_refs)
        _need(ha_methods == self.ha_methods and all(a is b for a, b in
            zip(ha_codes, self.ha_codes, strict=True)), 'native_event_code_changed')
        self.bindings()
        return result


@dataclass(frozen=True)
class OriginalPowercalcSources:
    ledger: SourceLedger
    receipt_json: bytes
    native_json: bytes
    closed_graph_json: bytes | None = None

    @property
    def sha256(self):
        value = [_TREE, self.ledger.sha256, self.receipt_json.decode(), self.native_json.decode()]
        if self.closed_graph_json is not None:
            value.append(self.closed_graph_json.decode())
        return _sha(_json(value))

    @property
    def migration_ready(self):
        return False


def seal_powercalc_sources(receipt, plan, ledger, native):
    from .yaml_validation import assert_proposal_matches
    assert_proposal_matches(receipt, plan)
    _need(type(ledger) is SourceLedger and ledger.schema == 3
          and ledger.original_graph_sha256 == source_graph_digest(plan), 'original_graph_mismatch')
    sources = {row[0]: row[1:] for row in receipt.source_files}
    _need(all(sources.get(row.path) == (row.sha256, row.device, row.inode) for row in plan.files),
          'original_receipt_mismatch')
    _need(native['version'] == receipt.version and set(native) ==
          ({'version', 'rows', 'globals'} | ({'closed_graph'} if plan.powercalc_inputs is not None else set())),
          'original_projection_mismatch')
    keys = [row[0] for row in native['rows']]
    if not ledger.findings:
        expected = [row.native_key for row in ledger.definitions if row.domain == 'powercalc.sensor']
        _need(None not in keys and sorted(keys) == sorted(expected)
              and len(keys) == len(set(keys)), 'original_source_membership_mismatch')
    closed = native.get('closed_graph')
    if plan.powercalc_inputs is not None:
        _need(type(closed) is dict and closed.get('schema') == 1, 'profile_graph_required')
        _need(all(sources.get(row.path) == (row.sha256, row.device, row.inode)
                  for row in plan.powercalc_inputs.files), 'profile_receipt_mismatch')
        planned = {row.path: row.sha256 for row in plan.powercalc_inputs.files
                   if row.path.startswith('powercalc/profiles/')}
        _need(dict(closed['files']) == planned, 'profile_graph_membership_mismatch')
    plain = {k: v for k, v in native.items() if k != 'closed_graph'}
    return OriginalPowercalcSources(ledger, _receipt_bytes(receipt), _json(plain),
                                   None if closed is None else _json(closed))


@dataclass(frozen=True)
class PowercalcBoundRow:
    entity_id: str
    registry_id: str | None
    source_verified: bool
    reason: str
    source: str | None = None
    source_registry_id: str | None = None
    origin: str | None = None
    semantic_sha256: str | None = None
    # producer -> consumer; sign preserves custom group sum semantics.
    edges: tuple[tuple[str, str, int], ...] = ()
    runtime_identities: tuple[int, ...] = ()
    relation_kind: str | None = None


@dataclass(frozen=True)
class PowercalcSourceBindings:
    evidence_sha256: str
    runtime_sha256: str
    rows: tuple[PowercalcBoundRow, ...]
    status: str = 'UNKNOWN'
    vendor_tree_sha256: str = _TREE
    vendor_version: str = 'v1.26.1'
    core_version: str = '2026.10.2'
    blockers: tuple[str, ...] = ('powercalc_profiles_dynamic_unverified',
        'powercalc_energy_topology_unverified', 'powercalc_continuity_unverified')
    relationships: tuple[tuple[str, str, str, int], ...] = ()
    energy_settings_sha256: str | None = None

    @property
    def migration_ready(self):
        return False

    @property
    def family_complete(self):
        return False


def _dispatch_token(entity, expected_class, names):
    """Bind the methods actually looked up by this native runtime instance.

    Class code pins alone do not reject a method shadow in instance __dict__.
    Use owner/function identity, never transient id(bound_method) or invocation.
    """
    token = []
    for name in names:
        actual, expected = getattr(entity, name), getattr(expected_class, name)
        _need(type(actual) is MethodType and actual.__self__ is entity
              and actual.__func__ is expected, 'native_instance_dispatch_changed')
        token.extend((id(actual.__self__), id(actual.__func__)))
    return tuple(token)


def _sample(hass, vendor, evidence, observed, captured, prepared=None):
    from homeassistant.helpers import entity_platform as ep, entity_registry as er
    from homeassistant.helpers.entity import Entity
    original = evidence.powercalc_sources
    _need(type(original) is OriginalPowercalcSources
          and original.receipt_json == evidence.receipt_json, 'original_sources_required')
    documents = {row.key: json.loads(row.manager_json) for row in observed.files}
    entry_rows = {row['entry_id']: row for row in documents['core.config_entries']['entries']}
    registry_rows = {row['entity_id']: row for row in documents['core.entity_registry']['entities']}
    registry = hass.data[er.DATA_REGISTRY]
    active = {eid: entity for platforms in hass.data[ep.DATA_ENTITY_PLATFORM].values()
              for platform in platforms for eid, entity in platform.entities.items()}
    source = json.loads(original.native_json)
    runtime = json.loads(captured.payload_json)
    yaml_rows = {row[0]: row for row in source['rows']}
    vendor_config = hass.data['powercalc']['config']
    global_ok = (not original.ledger.findings
        and all(_fingerprint(vendor_config.get(key)) == digest for key, digest in source['globals'].items())
        and not any(row['domain'] == 'powercalc' and row['unique_id'] == 'powercalc_global_configuration'
                    for row in entry_rows.values()))
    results = []

    def row_identity(eid):
        row, entity = registry_rows.get(eid), active.get(eid)
        _need(row is not None and isinstance(entity, Entity), 'physical_entity_missing')
        live = registry.async_get(eid)
        _need(live is not None and live.id == row['id'] and entity.registry_entry is live
              and entity.unique_id == row['unique_id'] and live.unique_id == row['unique_id']
              and live.platform == row['platform'] and live.config_entry_id == row['config_entry_id']
              and live.config_subentry_id == row['config_subentry_id']
              and entity.platform.entities.get(eid) is entity
              and entity.platform.config_entry is (None if row['config_entry_id'] is None
                  else hass.config_entries.async_get_entry(row['config_entry_id'])),
              'physical_identity_changed')
        return row, entity

    def listeners(entity, dependencies, *, virtual):
        from functools import partial
        from homeassistant.core import HassJob
        from homeassistant.const import EVENT_STATE_CHANGED
        from homeassistant.helpers import event
        from .yaml_runtime_observer import _registered_bus_remove
        tracker = hass.data.get(event._TRACK_STATE_CHANGE_DATA)
        _need(type(tracker) is event._KeyedEventData
              and type(tracker.callbacks) is defaultdict
              and tracker.callbacks.default_factory is list, 'native_source_tracker_missing')
        callbacks = tracker.callbacks
        native_tracker = event._KEYED_TRACK_STATE_CHANGE
        _need(native_tracker.key == event._TRACK_STATE_CHANGE_DATA
              and native_tracker.event_type == EVENT_STATE_CHANGED
              and native_tracker.dispatcher_callable is event._async_dispatch_entity_id_event
              and native_tracker.filter_callable is event._async_state_filter,
              'native_source_tracker_changed')
        (bus_job, event_filter), bus_token = _registered_bus_remove(
            hass, tracker.listener, EVENT_STATE_CHANGED)
        _need(type(bus_job) is HassJob, 'native_source_dispatch_changed')
        for actual, expected in (
                (bus_job.target, event._async_dispatch_entity_id_event),
                (event_filter, event._async_state_filter)):
            _need(type(actual) is partial and actual.func is expected and not actual.keywords
                  and len(actual.args) == 2 and actual.args[0] is hass
                  and actual.args[1] is callbacks, 'native_source_dispatch_changed')
        identities = [id(tracker), id(callbacks), *bus_token]
        nested = [code for code in vendor.classes['VirtualPowerSensor'].async_added_to_hass.__code__.co_consts
                  if getattr(code, 'co_name', None) == 'appliance_state_listener']

        def owns(target):
            if not virtual:
                return (getattr(target, '__self__', None) is entity
                    and getattr(target, '__func__', None) is vendor.classes['GroupedSensor'].on_state_change)
            if len(nested) != 1 or getattr(target, '__code__', None) is not nested[0]:
                return False
            cells = dict(zip(target.__code__.co_freevars,
                (cell.cell_contents for cell in target.__closure__), strict=True))
            return cells.get('self') is entity

        for key, members in callbacks.items():
            _need(type(members) is list, 'native_source_callbacks_changed')
            for job in members:
                _need(type(job) is HassJob, 'native_source_callbacks_changed')
                if owns(job.target):
                    _need(key in dependencies, 'actual_native_source_listener_extra')
        for dependency in sorted(dependencies):
            candidates = [job for job in callbacks.get(dependency, ()) if owns(job.target)]
            _need(len(candidates) == 1, 'actual_native_source_listener_missing')
            job = candidates[0]
            removals = [remove for remove in entity._on_remove if type(remove) is partial
                        and remove.func is event._remove_listener and not remove.keywords
                        and len(remove.args) == 5 and remove.args[0] is hass
                        and remove.args[1] is event._KEYED_TRACK_STATE_CHANGE
                        and set(remove.args[2]) == dependencies
                        and len(tuple(remove.args[2])) == len(dependencies)
                        and remove.args[3] is job and remove.args[4] is callbacks]
            _need(len(removals) == 1, 'actual_native_source_listener_owner_missing')
            identities.extend((id(job), id(removals[0])))
        return tuple(identities)

    fixed_type, strategy_type = vendor.classes['VirtualPowerSensor'], vendor.classes['FixedStrategy']
    for inventory in runtime['objects']:
        eid = inventory['entity_id']
        entity = active.get(eid)
        if type(entity) is not fixed_type:
            continue
        try:
            _need(global_ok, 'global_source_unverified')
            _need(inventory['active_platforms'] and inventory['object_id'] == str(id(entity)),
                  'inventory_object_binding_changed')
            row, _ = row_identity(eid)
            entry_id = row['config_entry_id']
            if entry_id is None:
                definition = yaml_rows.get(entity.unique_id)
                _need(definition is not None and definition[2] is not None,
                      'yaml_variant_unverified')
                semantic, origin = definition[2], 'yaml:' + entity.unique_id
                _need(entity._config_entry is None, 'yaml_entry_binding_changed')
            else:
                entry = entry_rows[entry_id]
                actual_entry = hass.config_entries.async_get_entry(entry_id)
                _need(entry['domain'] == 'powercalc' and entity._config_entry is actual_entry
                      and entry['options'] == {} and not entry['subentries']
                      and entry['data'].get('_power_entity') == eid, 'entry_source_unverified')
                semantic, origin = powercalc_fixed_semantics(entry['data']), 'entry:' + entry_id
                _need(semantic is not None, 'entry_variant_unverified')
            source_id = semantic['source']
            physical, producer = row_identity(source_id)
            actual_source = entity._source_entity
            _need(type(actual_source) is vendor.classes['SourceEntity']
                  and actual_source.entity_id == source_id
                  and actual_source.entity_entry is registry.async_get(source_id)
                  and actual_source.unique_id == physical['unique_id'], 'source_identity_changed')
            strategy = entity._strategy_instance
            _need(type(strategy) is strategy_type and str(entity._calculation_strategy) == 'fixed'
                  and strategy._source_entity is actual_source
                  and type(strategy._power) in (int, float) and strategy._per_state_power is None
                  and Decimal(str(strategy._power)) == Decimal(semantic['power'])
                  and entity._track_entities == {source_id}
                  and entity.native_unit_of_measurement == 'W'
                  and entity._power_profile is None and entity._sub_profile_selector is None
                  and entity._calculation_enabled_condition is None
                  and entity._multiply_factor is None and entity._standby_power == 0
                  and entity._standby_power_on == 0 and entity._availability_entity is None
                  and not entity._sensor_config.get('sleep_power')
                  and not entity._sensor_config.get('unavailable_power'), 'fixed_semantics_unverified')
            _need(entity._sensor_config.get('entity_id') == source_id
                  and Decimal(str(entity._sensor_config['fixed']['power'])) == Decimal(semantic['power']),
                  'runtime_config_changed')
            results.append(PowercalcBoundRow(eid, row['id'], True, 'fixed_source_bound',
                source_id, physical['id'], origin, _fingerprint(semantic), ((source_id, eid, 1),),
                (id(entity), id(producer), id(actual_source), id(strategy),
                 *_dispatch_token(strategy, strategy_type,
                     ('calculate', 'can_calculate_standby', 'is_enabled')),
                 *_dispatch_token(entity, fixed_type,
                     ('_handle_source_entity_state_change', 'calculate_power',
                      '_resolve_calculation_state', '_calculate_state_standby_power',
                      'calculate_standby_power', '_apply_power_adjustments',
                      '_apply_multiply_factor', '_apply_standby_multiply_factor',
                      'is_calculation_enabled', '_switch_sub_profile_dynamically',
                      '_schedule_sleep_power', '_track_standby_power', '_clear_standby_power',
                      '_has_valid_state', '_update_power_and_write_state', '_update_power_sensor')),
                 *listeners(entity, {source_id}, virtual=True))))
        except (ValueError, KeyError, TypeError, AttributeError):
            results.append(PowercalcBoundRow(eid, inventory['registry_id'], False,
                                            'fixed_source_or_semantics_unverified'))
    verified = {row.entity_id for row in results if row.source_verified}
    for inventory in runtime['objects']:
        eid, entity = inventory['entity_id'], active.get(inventory['entity_id'])
        if type(entity) is not vendor.classes['GroupedPowerSensor']:
            if eid not in {row.entity_id for row in results}:
                results.append(PowercalcBoundRow(eid, inventory['registry_id'], False,
                                                'profile_dynamic_energy_or_runtime_variant_unverified'))
            continue
        try:
            _need(global_ok, 'global_source_unverified')
            _need(inventory['active_platforms'] and inventory['object_id'] == str(id(entity)),
                  'inventory_object_binding_changed')
            row, _ = row_identity(eid)
            entry = entry_rows[row['config_entry_id']]
            data = entry['data']
            allowed = {'name', 'sensor_type', 'group_type', 'group_member_sensors',
                       'create_energy_sensor', 'unique_id', '_power_entity', '_energy_entity', 'group_power_update_interval'}
            _need(entry['domain'] == 'powercalc' and entry['options'] == {} and not entry['subentries']
                  and set(data) <= allowed and data.get('sensor_type') == 'group'
                  and data.get('group_type') == 'custom' and data.get('create_energy_sensor') is False
                  and entity.unique_id == (data.get('unique_id') or entry['unique_id'] or entry['entry_id']),
                  'custom_group_variant_unverified')
            members = data.get('group_member_sensors')
            _need(type(members) is list and members and len(members) == len(set(members)),
                  'custom_group_members_unverified')
            expected = {entry_rows[key]['data']['_power_entity'] for key in members}
            _need(expected <= verified and entity._entities == expected
                  and entity._sensor_config.get('group_member_sensors') == members
                  and str(entity._group_type) == 'custom'
                  and entity.native_unit_of_measurement == 'W'
                  and set(entity._member_states) <= expected, 'custom_group_edges_changed')
            results.append(PowercalcBoundRow(eid, row['id'], True, 'custom_sum_sources_bound',
                origin='entry:' + entry['entry_id'], semantic_sha256=_fingerprint(sorted(expected)),
                edges=tuple((member, eid, 1) for member in sorted(expected)),
                runtime_identities=(id(entity),
                    *_dispatch_token(entity, vendor.classes['GroupedPowerSensor'],
                        ('on_state_change', 'calculate_new_state', 'get_summed_state',
                         '_get_state_value_in_native_unit', 'set_new_state', '_set_native_value',
                         '_should_throttle', '_cancel_update_interval_exceeded_callback')),
                    *listeners(entity, expected, virtual=False))))
        except (ValueError, KeyError, TypeError, AttributeError):
            results.append(PowercalcBoundRow(eid, inventory['registry_id'], False,
                                            'custom_group_sources_unverified'))
    if original.closed_graph_json is not None:
        from .powercalc_profile_binding import bind_graph
        return bind_graph(hass, vendor, evidence, observed, captured, results,
                          row_identity, listeners, global_ok, prepared)
    return tuple(sorted(results, key=lambda row: (str(row.entity_id), str(row.registry_id))))


async def async_bind_powercalc_sources(hass, receipt, evidence, captured, *, guard):
    """Two comparison samples under the same ORIGINAL full-three/source receipt.

    Returns row evidence beside the existing full runtime capture. It neither
    adopts replacement bytes nor grants completeness or mutation capability.
    """
    from .yaml_source_binding import OriginalSourceEvidence
    from .native_discovery_inputs import (async_observe_discovery_inputs,
                                          assert_discovery_managers_current)
    from .yaml_validation import _settled_executor, assert_validation_current
    _need(type(evidence) is OriginalSourceEvidence
          and type(evidence.powercalc_sources) is OriginalPowercalcSources,
          'original_sources_required')
    def check():
        _guard(guard)
        evidence.assert_receipt(receipt)
    check()
    closed = evidence.powercalc_sources.closed_graph_json is not None
    vendor = PowercalcVendor(hass, with_profiles=closed)
    def initial_sources():
        vendor.sources()
        if closed:
            from .powercalc_profile_inputs import prepare_profile_files
            return prepare_profile_files(receipt, evidence.powercalc_sources)
        return None
    prepared = await _settled_executor(hass, initial_sources)
    check()
    observed = await async_observe_discovery_inputs(hass, guard=check)
    sources = {row[0]: row[1:] for row in receipt.source_files}
    for row in observed.files:
        _need(sources.get('.storage/' + row.key) == (_sha(row.raw), *row.identity[:2]),
              'original_full_three_changed')
    await _settled_executor(hass, assert_validation_current, receipt)
    check()
    vendor.bindings()
    if not closed:
        assert_powercalc_runtime_current(hass, captured, guard=check)
    first = _sample(hass, vendor, evidence, observed, captured, prepared)
    if closed:
        from .powercalc_profile_binding import runtime_current
        runtime_current(hass, captured, first, guard=check)
    def final_sources():
        vendor.sources()
        assert_validation_current(receipt)
    await _settled_executor(hass, final_sources)
    check()
    assert_discovery_managers_current(hass, observed, guard=check)
    if not closed:
        assert_powercalc_runtime_current(hass, captured, guard=check)
    vendor.bindings()
    second = _sample(hass, vendor, evidence, observed, captured, prepared)
    if closed:
        runtime_current(hass, captured, second, guard=check)
    _need(first == second, 'source_binding_changed_during_capture')
    relationships = tuple(sorted((row.relation_kind, source, target, sign)
        for row in second if row.source_verified and row.relation_kind is not None
        for source, target, sign in row.edges))
    settings = (json.loads(evidence.powercalc_sources.closed_graph_json)['energy_settings_sha256']
                if closed else None)
    return PowercalcSourceBindings(evidence.sha256, _sha(captured.payload_json), second,
                                   relationships=relationships, energy_settings_sha256=settings)
