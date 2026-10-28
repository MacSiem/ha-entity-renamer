"""Pinned local LUT/selector/signed-group/Energy row proof; no lifecycle authority."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from functools import partial
import json
import math
from pathlib import Path
import sys
from types import CodeType, MethodType

from .powercalc_profile_inputs import (
    need, digest, table_digest, PreparedProfileFiles, custom_profile_locator,
    assert_profile_cache_directories,
)


def extend_vendor(vendor):
    """Only already-loaded official modules/classes; never call their factories."""
    extra = (
        ('power_profile.library', 'ProfileLibrary', ('get_profile', 'create_power_profile', '_load_model_data')),
        ('power_profile.loader.local', 'LocalLoader', ('__init__', 'load_model', '_load_custom_model', '_load_json')),
        ('power_profile.power_profile', 'PowerProfile', ('__init__', 'get_model_directory',
            'select_sub_profile', 'json_data', 'sub_profile_select', 'standby_power',
            'standby_power_on', 'has_sub_profile_select_matchers', 'manufacturer', 'model',
            'multiply_factor', 'get_sub_profiles', 'has_sub_profiles')),
        ('power_profile.sub_profile_selector', 'SubProfileSelector', ('__init__', '_build_matchers',
            '_create_matcher', 'select_sub_profile', 'get_tracking_entities')),
        ('power_profile.sub_profile_selector', 'EntityStateMatcher',
            ('__init__', 'match', 'get_tracking_entities')),
        ('strategy.lut', 'LutStrategy', ('__init__', 'initialize', 'calculate', 'validate_config',
            'get_selected_color_mode', 'create_light_setting', 'lookup_power',
            'lookup_power_for_brightness', 'get_nearest', '_nearest_key', '_interpolate',
            '_calculate_effect_power')),
        ('strategy.lut', 'LutRegistry', ('__init__', 'get_lookup_entry', 'get_supported_modes',
            '_cache_key', '_load_lut_entry', 'get_lut_file')),
        ('strategy.lut', '_LutEntry', ()),
        ('strategy.lut', 'LookupMode', ('from_color_mode',)),
        ('strategy.factory', 'PowerCalculatorStrategyFactory', ('__init__', 'create', '_prepare', '_create_lut')),
        ('sensors.group.subtract', 'SubtractGroupSensor', ('__init__', 'get_summed_state')),
        ('sensors.group.custom', 'GroupedEnergySensor', ('__init__', 'async_added_to_hass',
            'calculate_initial_state', 'calculate_new_state', 'calculate_delta', 'restore_last_state')),
        ('sensors.group.custom', 'PreviousStateStoreStore', ('_async_migrate_func',)),
        ('sensors.group.custom', 'PreviousStateStore', ('__init__', 'get_entity_state',
            'set_entity_state', 'cleanup_entity_states', 'persist_states', 'async_setup_dump')),
        ('sensors.energy', 'VirtualEnergySensor', ('__init__', '_integrate_on_state_change',
            '_schedule_max_sub_interval_exceeded_if_state_is_numeric', '_sanitize_new_state',
            '_replace_outlier_state', 'extra_restore_state_data')),
    )
    refs = []
    for suffix, name, methods in extra:
        module = sys.modules.get('custom_components.powercalc.' + suffix)
        need(module is not None, 'official_module_not_loaded')
        cls = vars(module).get(name)
        need(isinstance(cls, type) and cls.__module__ == module.__name__, 'official_class_required')
        vendor.classes[name] = cls
        refs.extend((cls, method) for method in methods)
    factory = sys.modules.get('custom_components.powercalc.power_profile.factory')
    power = sys.modules.get('custom_components.powercalc.sensors.power')
    need(factory is not None and power is not None, 'profile_factory_module_required')
    refs.extend(((factory, 'get_power_profile'), (power, 'get_power_profile')))
    refs.extend((vendor.classes['VirtualPowerSensor'], name)
                for name in ('ensure_strategy_instance', '_select_new_sub_profile'))
    refs.extend((vendor.classes['PowerCalculationStrategyInterface'], name)
                for name in ('get_entities_to_track', 'can_calculate_standby'))
    data = sys.modules.get('custom_components.powercalc.strategy.profile_data')
    lut = sys.modules.get('custom_components.powercalc.strategy.lut')
    need(data is not None and lut is not None, 'profile_parser_module_required')
    refs.extend(((data, 'open_profile_csv'), (lut, 'open_profile_csv'),
                 (sys.modules['custom_components.powercalc.sensors.power'],
                  '_get_standby_power_from_profile')))
    vendor.refs += tuple(refs)
    from homeassistant.components.integration import sensor as integration
    from homeassistant.helpers import event
    from homeassistant.util import color
    from .native_integration_unload_audit import _INTEGRATION
    vendor.integration = integration
    vendor.ha_pins['components/integration/sensor.py'] = _INTEGRATION['2026.10.2']
    vendor.ha_pins['util/color.py'] = '20bf486782dfac8cb1607beddc26188fb82610bb5fabf42a6046b4e6b4ac4bb2'
    vendor.ha_refs += tuple((owner, name) for owner in (color, lut) for name in
        ('color_temperature_kelvin_to_mired', 'color_temperature_to_hs'))
    vendor.ha_refs += tuple((integration.IntegrationSensor, name) for name in (
        '__init__', 'async_added_to_hass', '_integrate_on_state_change_with_max_sub_interval',
        '_integrate_on_state_report_with_max_sub_interval', '_integrate_on_state_change',
        '_schedule_max_sub_interval_exceeded_if_state_is_numeric', '_update_integral', 'native_value'))
    vendor.ha_refs += ((integration._Left, 'calculate_area_with_two_states'),
        (integration._Left, 'validate_states'),
        (integration._IntegrationMethod, 'calculate_area_with_one_state'))
    vendor.ha_refs += tuple((event, name) for name in (
        'async_track_state_report_event', '_run_async_call_action', 'async_call_later'))


def pending(hass):
    """Refuse unsettled official calculation/setup; never global-drain HA."""
    for task in asyncio.all_tasks(hass.loop):
        if task is asyncio.current_task() or task.done():
            continue
        node, seen = task.get_coro(), set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            frame = getattr(node, 'cr_frame', None)
            if frame is not None and frame.f_globals.get('__name__', '').startswith('custom_components.powercalc'):
                if any(word in frame.f_code.co_name for word in ('calculate', 'state_change',
                        'strategy_instance', 'sub_profile', 'setup', 'reload', 'initial_update')):
                    return True
            node = getattr(node, 'cr_await', None)
    return False


def runtime_current(hass, original, verified, *, guard):
    """Compare SAME original inventory; only proven dynamic strategy IDs vary."""
    from .powercalc_runtime_observer import capture_powercalc_runtime
    current = capture_powercalc_runtime(hass, guard=guard)
    old, new = json.loads(original.structural_token), json.loads(current.structural_token)
    eligible = {row.entity_id for row in verified if row.source_verified
                and row.reason == 'dynamic_lut_source_bound'}
    old_rows = {row['entity_id']: row for row in old[4]['objects']}
    new_rows = {row['entity_id']: row for row in new[4]['objects']}
    for eid in eligible:
        need(eid in old_rows and eid in new_rows, 'runtime_member_changed')
        a, b = old_rows[eid]['fields'], new_rows[eid]['fields']
        need(a['_strategy_instance'].get('$unsupported', {}).get('type') ==
             b['_strategy_instance'].get('$unsupported', {}).get('type') ==
             'custom_components.powercalc.strategy.lut.LutStrategy', 'strategy_type_changed')
        # Detached comparison copies only. The ORIGINAL capture is never edited.
        a['_strategy_instance']['process_id'] = b['_strategy_instance']['process_id']
    need(old == new, 'runtime_graph_changed')


def _energy_listeners(hass, entity, source, vendor):
    from collections import defaultdict
    from homeassistant.core import HassJob, HassJobType, State
    from homeassistant.helpers import event
    from .yaml_runtime_observer import _registered_bus_remove
    native = vendor.integration.IntegrationSensor
    ids = []
    for key, tracker, name in (
        (event._TRACK_STATE_CHANGE_DATA, event._KEYED_TRACK_STATE_CHANGE,
         '_integrate_on_state_change_with_max_sub_interval'),
        (event._TRACK_STATE_REPORT_DATA, event._KEYED_TRACK_STATE_REPORT,
         '_integrate_on_state_report_with_max_sub_interval')):
        data = hass.data.get(key)
        need(type(data) is event._KeyedEventData and type(data.callbacks) is defaultdict
             and data.callbacks.default_factory is list, 'energy_tracker_required')
        need(type(tracker) is event._KeyedEventTracker and tracker.key == key
             and tracker.dispatcher_callable is event._async_dispatch_entity_id_event
             and tracker.filter_callable is event._async_state_filter, 'energy_tracker_dispatch_changed')
        (bus_job, event_filter), bus_token = _registered_bus_remove(hass, data.listener, tracker.event_type)
        need(type(bus_job) is HassJob, 'energy_bus_job_changed')
        for actual, expected in ((bus_job.target, event._async_dispatch_entity_id_event),
                                 (event_filter, event._async_state_filter)):
            need(type(actual) is partial and actual.func is expected and not actual.keywords
                 and actual.args == (hass, data.callbacks), 'energy_dispatcher_changed')
        actual = getattr(entity, name)
        need(type(actual) is MethodType and actual.__self__ is entity
             and actual.__func__ is getattr(native, name), 'energy_callback_changed')
        owned = [(eid, job) for eid, members in data.callbacks.items() for job in members
                 if getattr(job.target, '__self__', None) is entity]
        need(len(owned) == 1 and owned[0][0] == source and type(owned[0][1]) is HassJob
             and owned[0][1].target == actual, 'energy_listener_membership_changed')
        job = owned[0][1]
        removes = [r for r in entity._on_remove if type(r) is partial and r.func is event._remove_listener
                   and not r.keywords and len(r.args) == 5 and r.args[0] is hass
                   and r.args[1] is tracker and tuple(r.args[2]) == (source,)
                   and r.args[3] is job and r.args[4] is data.callbacks]
        need(len(removes) == 1, 'energy_listener_owner_changed')
        ids.extend((*bus_token, id(data), id(job), id(removes[0])))
    # Read the actual native TimerHandle; no timer callback is invoked here.
    cancel = entity._max_sub_interval_exceeded_callback
    handle = getattr(cancel, '__self__', None)
    need(type(handle) is asyncio.TimerHandle and handle._loop is hass.loop
         and cancel == handle.cancel and not handle.cancelled() and math.isfinite(handle.when())
         and handle._callback is event._run_async_call_action
         and type(handle._args) is tuple and len(handle._args) == 2 and handle._args[0] is hass,
         'energy_timer_owner_changed')
    timer_job = handle._args[1]
    need(type(timer_job) is HassJob and timer_job.job_type is HassJobType.Callback, 'energy_timer_job_changed')
    action = timer_job.target
    schedule = native._schedule_max_sub_interval_exceeded_if_state_is_numeric.__code__
    need(any(action.__code__ is code for code in schedule.co_consts if type(code) is CodeType),
         'energy_timer_code_changed')
    cells = dict(zip(action.__code__.co_freevars,
                     (cell.cell_contents for cell in action.__closure__), strict=True))
    need(set(cells) == {'self', 'source_state', 'source_state_dec'} and cells['self'] is entity
         and type(cells['source_state']) is State and cells['source_state'].entity_id == source
         and type(cells['source_state_dec']) is Decimal
         and cells['source_state_dec'].is_finite()
         and cells['source_state_dec'] == Decimal(cells['source_state'].state), 'energy_timer_input_changed')
    return tuple(ids)


def assert_custom_profile_locator(entity, profile, root):
    """Live memory-only provenance check; never call a loader or read a file."""
    expected = profile.get('custom_model_directory')
    actual = entity._sensor_config.get('custom_model_directory')
    if expected is None:
        need(actual is None, 'custom_locator_changed')
    else:
        need(custom_profile_locator(actual) == custom_profile_locator(expected)
             and profile['directory'] == expected
             and Path(entity._power_profile._directory) == Path(root) / expected,
             'custom_locator_changed')


def bind_graph(hass, vendor, evidence, observed, captured, results, row_identity, listeners, global_ok, prepared):
    from .powercalc_source_binding import PowercalcBoundRow, _dispatch_token
    # No filesystem read or gzip/CSV decoding in this event-loop suffix.
    original = evidence.powercalc_sources
    need(type(prepared) is PreparedProfileFiles
         and prepared.receipt_json == evidence.receipt_json == original.receipt_json
         and prepared.closed_graph_json == original.closed_graph_json, 'prepared_original_mismatch')
    graph = json.loads(original.closed_graph_json)
    assert_profile_cache_directories(graph['profiles'])
    need(prepared.files == tuple(tuple(row) for row in graph['files']), 'prepared_profile_files_changed')
    need(global_ok and not pending(hass), 'global_or_pending_runtime')
    documents = {row.key: json.loads(row.manager_json) for row in observed.files}
    entries = {row['entry_id']: row for row in documents['core.config_entries']['entries']}
    rows = {row['entity_id']: row for row in documents['core.entity_registry']['entities']}
    from homeassistant.helpers import entity_platform as ep
    entities = {eid: entity for bucket in hass.data[ep.DATA_ENTITY_PLATFORM].values()
                for platform in bucket for eid, entity in platform.entities.items()}
    by_id = {row.entity_id: row for row in results}
    powers, energy_specs = set(), []
    cls = vendor.classes
    for profile in graph['profiles']:
        try:
            candidates = [entity for eid, entity in entities.items()
                if type(entity) is cls['VirtualPowerSensor'] and rows.get(eid, {}).get('unique_id') == profile['unique_id']]
            need(len(candidates) == 1, 'profile_entity_membership')
            entity = candidates[0]
            row, _ = row_identity(entity.entity_id)
            if profile['origin'].startswith('entry:'):
                entry_id = profile['origin'][6:]
                need(row['config_entry_id'] == entry_id and digest(entries[entry_id]['data']) == profile['config_sha256']
                     and entity._config_entry is hass.config_entries.async_get_entry(entry_id), 'profile_entry_changed')
            else:
                need(row['config_entry_id'] is None and entity._config_entry is None, 'profile_yaml_changed')
            source_row, producer = row_identity(profile['source'])
            source = entity._source_entity
            need(type(source) is cls['SourceEntity'] and source.entity_id == profile['source']
                 and source.entity_entry is producer.registry_entry and source.unique_id == source_row['unique_id']
                 and source.object_id == profile['source'].split('.')[1] and source.domain == 'light',
                 'profile_producer_changed')
            actual = entity._power_profile
            need(type(actual) is cls['PowerProfile'] and actual._hass is hass
                 and actual.manufacturer == profile['manufacturer'] and actual.model == profile['model']
                 and Path(actual._directory) == Path(hass.config.config_dir) / profile['directory'],
                 'profile_owner_changed')
            assert_custom_profile_locator(entity, profile, hass.config.config_dir)
            # Compare native-memory JSON with the isolated ORIGINAL compiler's
            # digests; parsing measurement files belongs to that worker only.
            need(digest(actual._base_json_data) == profile['base_sha256']
                 and digest(actual._sub_profiles) == profile['sub_profiles_sha256'], 'profile_data_changed')
            variant = next(v for v in profile['variants'] if v['name'] == actual.sub_profile)
            expected_dir = (None if actual.sub_profile is None else
                            str(Path(hass.config.config_dir) / profile['directory'] / actual.sub_profile))
            need(actual._sub_profile_dir == expected_dir, 'active_directory_changed')
            _dispatch_token(actual, cls['PowerProfile'], ('get_model_directory', 'select_sub_profile'))
            need(digest(actual._json_data) == variant['json_sha256'], 'active_profile_changed')
            dependencies = {profile['source']}
            selector = actual._base_json_data.get('sub_profile_select')
            if selector is not None:
                live = entity._sub_profile_selector
                need(type(live) is cls['SubProfileSelector'] and live._hass is hass
                     and live._source_entity is source
                     and digest({'default': live._config.default, 'matchers': live._config.matchers}) ==
                         profile['selector_sha256'] and len(live._matchers) == 1, 'selector_changed')
                spec = selector['matchers'][0]
                sid = spec['entity_id'].replace('{{source_object_id}}', source.object_id)
                _, selector_producer = row_identity(sid)
                matcher = live._matchers[0]
                need(type(matcher) is cls['EntityStateMatcher'] and matcher._hass is hass
                     and matcher._entity_id == sid and matcher._mapping == spec['map'], 'selector_input_changed')
                state = hass.states.get(sid)
                chosen = spec['map'].get(state.state, selector['default']) if state is not None else selector['default']
                need(chosen == actual.sub_profile, 'selector_not_settled')
                dependencies.add(sid)
                _dispatch_token(matcher, cls['EntityStateMatcher'], ('match', 'get_tracking_entities'))
                _dispatch_token(live, cls['SubProfileSelector'], ('select_sub_profile', 'get_tracking_entities'))
            else:
                need(entity._sub_profile_selector is None, 'unexpected_selector')
            strategy = entity._strategy_instance
            factory = entity.calculation_strategy_factory
            need(type(strategy) is cls['LutStrategy'] and type(factory) is cls['PowerCalculatorStrategyFactory']
                 and hass.data.get('powercalc_strategy_factory') is factory and factory._hass is hass
                 and strategy._source_entity is source and strategy._profile is actual
                 and strategy._lut_registry is factory._lut_registry
                 and type(strategy._lut_registry) is cls['LutRegistry']
                 and strategy._lut_registry._hass is hass
                 and {str(mode) for mode in strategy._supported_modes} == set(variant['modes'])
                 and entity._track_entities == dependencies and str(entity._calculation_strategy) == 'lut'
                 and entity.native_unit_of_measurement == 'W'
                 and entity._off_states == {'off', 'standby', 'unavailable'}
                 and entity._ignore_unavailable_state is False
                 and entity._rounding_digits == hass.data['powercalc']['config'].get('power_sensor_precision', 2)
                 and entity._standby_sensors is hass.data['powercalc']['standby_power_sensors']
                 and entity._multiply_factor is None and entity._calculation_enabled_condition is None
                 and entity._availability_entity is None and entity._standby_power_on == 0
                 and entity._standby_power == Decimal(str(actual._json_data.get('standby_power', 0)))
                 and not entity._sensor_config.get('sleep_power') and not entity._sensor_config.get('unavailable_power')
                 and entity._sensor_config.get('entity_id') == profile['source'], 'lut_semantics_changed')
            _dispatch_token(factory, cls['PowerCalculatorStrategyFactory'], ('create', '_create_lut'))
            need(factory._prepare is cls['PowerCalculatorStrategyFactory']._prepare,
                 'factory_instance_dispatch_changed')
            registry = strategy._lut_registry
            need(registry._cache_key is cls['LutRegistry']._cache_key
                 and registry.get_lut_file is cls['LutRegistry'].get_lut_file
                 and registry._load_lut_entry == cls['LutRegistry']._load_lut_entry,
                 'registry_instance_dispatch_changed')
            need(registry._supported_modes.get((actual.manufacturer, actual.model, actual.sub_profile, 'supported_modes')) ==
                 strategy._supported_modes, 'lut_modes_cache_changed')
            for key, entry in registry._lut_entries.items():
                if key[:2] != (actual.manufacturer, actual.model):
                    continue
                sub_variant = next(v for v in profile['variants'] if v['name'] == key[3])
                mode = str(key[2])
                need(type(entry) is cls['_LutEntry'] and mode in sub_variant['modes']
                     and type(entry.sorted_keys) is list and entry.sorted_keys == sorted(entry.table)
                     and table_digest(entry.table) == sub_variant['modes'][mode]['table_sha256'], 'lut_cache_changed')
            _dispatch_token(registry, cls['LutRegistry'], ('get_lookup_entry', 'get_supported_modes'))
            need(strategy._nearest_key is cls['LutStrategy']._nearest_key
                 and strategy._interpolate is cls['LutStrategy']._interpolate, 'lut_instance_dispatch_changed')
            strategy_ids = _dispatch_token(strategy, cls['LutStrategy'], ('calculate', 'is_enabled',
                'get_entities_to_track', 'can_calculate_standby', 'lookup_power', 'get_selected_color_mode',
                'create_light_setting', 'lookup_power_for_brightness', 'get_nearest', '_calculate_effect_power'))
            entity_ids = _dispatch_token(entity, cls['VirtualPowerSensor'], (
                '_handle_source_entity_state_change', 'calculate_power', '_resolve_calculation_state',
                '_switch_sub_profile_dynamically', '_select_new_sub_profile', 'ensure_strategy_instance',
                '_apply_power_adjustments', '_update_power_and_write_state', '_calculate_state_standby_power',
                'calculate_standby_power', '_apply_multiply_factor', '_apply_standby_multiply_factor',
                'is_calculation_enabled', '_schedule_sleep_power', '_track_standby_power', '_clear_standby_power',
                '_has_valid_state', '_update_power_sensor'))
            eid = entity.entity_id
            by_id[eid] = PowercalcBoundRow(eid, row['id'], True,
                'dynamic_lut_source_bound' if selector is not None else 'lut_source_bound',
                profile['source'], source_row['id'], profile['origin'], digest(profile),
                tuple((sid, eid, 1 if sid == profile['source'] else 0) for sid in sorted(dependencies)),
                (id(entity), id(producer), id(source), id(actual), id(strategy), id(factory),
                 *strategy_ids, *entity_ids, *listeners(entity, dependencies, virtual=True)), 'profile_lut')
            powers.add(eid)
            energy_specs.append((entity, None, 'profile:' + profile['origin']))
        except (ValueError, KeyError, TypeError, AttributeError, StopIteration):
            # Preserve the full pre-existing unknown row; no partial profile proof.
            continue
    groups = graph['groups']
    for group in sorted(groups, key=lambda r: r['config'].get('group_type') != 'custom'):
        try:
            cfg = group['config']
            entry_id = group['origin'][6:]
            need(digest(entries[entry_id]['data']) == group['config_sha256'], 'group_original_changed')
            candidates = [e for eid, e in entities.items() if rows.get(eid, {}).get('config_entry_id') == entry_id
                and type(e) is (cls['GroupedPowerSensor'] if cfg['group_type'] == 'custom' else cls['SubtractGroupSensor'])]
            need(len(candidates) == 1, 'group_power_membership')
            entity = candidates[0]
            row, _ = row_identity(entity.entity_id)
            if cfg['group_type'] == 'custom':
                members = cfg.get('group_power_entities', [])
                need(type(members) is list and bool(members) and len(members) == len(set(members)), 'sum_members')
                edges = [(eid, entity.entity_id, 1) for eid in members]
            else:
                members = [cfg['entity_id'], *cfg['subtract_entities']]
                need(entity._base_entity_id == cfg['entity_id'] and entity._subtract_entities == cfg['subtract_entities'],
                     'subtract_terms_changed')
                edges = [(cfg['entity_id'], entity.entity_id, 1),
                         *((eid, entity.entity_id, -1) for eid in cfg['subtract_entities'])]
            need(set(members) <= powers and entity._entities == set(members)
                 and str(entity._group_type) == cfg['group_type']
                 and entity.native_unit_of_measurement == 'W' and entity._update_interval == 0
                 and entity._ignore_unavailable_state is False
                 and entity._rounding_digits == hass.data['powercalc']['config'].get('power_sensor_precision', 2),
                 'group_edges_changed')
            ids = _dispatch_token(entity, type(entity), ('on_state_change', 'calculate_new_state',
                'get_summed_state', 'set_new_state', '_set_native_value', '_get_state_value_in_native_unit',
                '_should_throttle', '_cancel_update_interval_exceeded_callback'))
            by_id[entity.entity_id] = PowercalcBoundRow(entity.entity_id, row['id'], True,
                'custom_power_sources_bound' if cfg['group_type'] == 'custom' else 'subtract_sources_bound',
                origin=group['origin'], semantic_sha256=digest(group), edges=tuple(sorted(edges)),
                runtime_identities=(id(entity), *ids, *listeners(entity, set(members), virtual=False)),
                relation_kind='power_sum' if cfg['group_type'] == 'custom' else 'power_subtract')
            powers.add(entity.entity_id)
            energy_specs.append((entity, cfg if cfg['group_type'] == 'custom' else None, group['origin']))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    energies = set()
    globals_ = hass.data['powercalc']['config']
    for parent, group_cfg, origin in energy_specs:
        try:
            candidates = [entity for eid, entity in entities.items()
                if rows.get(eid, {}).get('unique_id') == str(parent.unique_id) + '_energy']
            need(len(candidates) == 1, 'energy_child_membership')
            entity = candidates[0]
            row, _ = row_identity(entity.entity_id)
            need(row['config_entry_id'] == rows[parent.entity_id]['config_entry_id'], 'energy_entry_changed')
            if group_cfg is None:
                native = vendor.integration.IntegrationSensor
                config = entity._sensor_config
                need(type(entity) is cls['VirtualEnergySensor'] and entity._source_entity == parent.entity_id
                     and entity._sensor_source_id == parent.entity_id and entity._filter_outliers is False
                     and type(entity._method) is vendor.integration._Left
                     and entity._round_digits == globals_.get('energy_sensor_precision', 4)
                     and str(entity._unit_time_str) == 'h'
                     and entity._unit_prefix_string == globals_.get('energy_sensor_unit_prefix', '')
                     and entity._max_sub_interval.total_seconds() == globals_.get('energy_update_interval', 600),
                     'energy_integral_config_changed')
                ids = _dispatch_token(entity, cls['VirtualEnergySensor'], ('_integrate_on_state_change',
                    '_schedule_max_sub_interval_exceeded_if_state_is_numeric', '_sanitize_new_state',
                    '_replace_outlier_state', '_update_integral'))
                _dispatch_token(entity._method, vendor.integration._Left,
                                ('calculate_area_with_two_states', 'calculate_area_with_one_state', 'validate_states'))
                ids += _energy_listeners(hass, entity, parent.entity_id, vendor)
                members, reason, kind = [parent.entity_id], 'integration_energy_source_bound', 'energy_integral'
            else:
                members = group_cfg.get('group_energy_entities', [])
                need(type(entity) is cls['GroupedEnergySensor'] and entity._entities == set(members)
                     and set(members) <= energies and bool(members) and entity._update_interval == 0
                     and type(entity._prev_state_store) is cls['PreviousStateStore']
                     and hass.data.get('powercalc_group_storage') is entity._prev_state_store
                     and entity._prev_state_store.hass is hass
                     and type(entity._prev_state_store.store) is cls['PreviousStateStoreStore']
                     and entity._prev_state_store.store.key == 'powercalc_group'
                     and Path(entity._prev_state_store.store.path) ==
                         Path(hass.config.path('.storage', 'powercalc_group'))
                     and entity.native_unit_of_measurement == 'kWh'
                     and entity._rounding_digits == globals_.get('energy_sensor_precision', 4)
                     and entity._sensor_config.get('group_energy_start_at_zero', True) is True,
                     'energy_delta_members_changed')
                previous = entity._prev_state_store.states.get(entity.entity_id, {})
                need(set(previous) <= set(members), 'energy_delta_store_members_changed')
                ids = _dispatch_token(entity, cls['GroupedEnergySensor'], ('calculate_delta',
                    'calculate_new_state', 'set_new_state', '_get_state_value_in_native_unit'))
                ids += listeners(entity, set(members), virtual=False)
                _dispatch_token(entity._prev_state_store, cls['PreviousStateStore'],
                    ('get_entity_state', 'set_entity_state', 'cleanup_entity_states'))
                reason, kind = 'delta_energy_sources_bound', 'energy_positive_delta'
            by_id[entity.entity_id] = PowercalcBoundRow(entity.entity_id, row['id'], True, reason,
                origin=origin, semantic_sha256=digest([kind, sorted(members)]),
                edges=tuple((eid, entity.entity_id, 1) for eid in sorted(members)),
                runtime_identities=(id(entity), *ids), relation_kind=kind)
            energies.add(entity.entity_id)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    if graph['energy_settings_sha256'] is not None:
        from .native_discovery_consumers import _energy_manager
        from .energy_native import NativeEnergy
        manager = _energy_manager(hass)
        NativeEnergy(hass, manager)._settled()
        need(digest(manager.data) == graph['energy_settings_sha256'], 'energy_settings_changed')
        need(set(graph['energy_refs']) <= energies, 'energy_settings_external_members_unverified')
    return tuple(sorted(by_id.values(), key=lambda row: (str(row.entity_id), str(row.registry_id))))
