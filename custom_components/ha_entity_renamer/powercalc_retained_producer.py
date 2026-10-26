"""Finite retained native profile-W calculators during ONE unchanged reload.

The REMOVED old Entity remains REMOVED. Actual native input events reach its
original appliance callback; native calculation updates _power but HA's pinned
write guard suppresses publication. No W State/Event construction, Energy
arithmetic, retained Energy timer or group clock is introduced here.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
import sys

from .native_unload_code_proof import unchanged
from .native_utility_setup_audit import _code_objects
from .powercalc_source_binding import _json


def _need(ok, reason):
    if not ok:
        raise ValueError('powercalc_retained_producer_' + reason) from None


def _state_snapshot(state):
    # Encoding only: no State construction or reconstruction from these bytes.
    # Reuse the diagnostic's strict HA attribute/StrEnum encoder.
    from homeassistant.core import State
    from .powercalc_numeric_continuity_probe import _value
    _need(type(state) is State, 'actual_input_state')
    return _json([state.entity_id, state.state, state.last_changed.isoformat(),
                  state.last_updated.isoformat(), state.last_reported.isoformat(),
                  _value(dict(state.attributes))])


@dataclass(frozen=True)
class PowercalcActualCalculation:
    """Actual native result, retaining original references for local inspection.

    Emission and completion orders are distinct facts. This is not a W State or
    an authorization to feed future Energy/group arithmetic.
    """
    producer: object
    event: object
    input_state: object
    resolved_light_state: object
    calculated_value: Decimal
    native_power: Decimal
    available: bool
    emission_sequence: int
    completion_sequence: int
    completed_at: datetime
    input_snapshot_json: bytes
    resolved_snapshot_json: bytes
    old_profile: object
    actual_strategy: object
    selected_sub_profile: str | None
    previous_input_state: object | None
    previous_snapshot_json: bytes | None
    ownership_phase: str
    emitted_at_timestamp: float
    source_last_changed: datetime
    source_last_updated: datetime
    source_last_reported: datetime
    calculation_returned_at: datetime
    update_started_at: datetime
    update_returned_at: datetime
    previous_native_power: Decimal | None
    previous_available: bool
    update_argument: Decimal
    native_value_changed: bool
    native_write_attempted: bool
    native_write_returned_at: datetime | None


    @property
    def native_write_suppressed(self):
        return self.native_write_attempted and self.native_write_returned_at is not None


def _result_row(result):
    return {'producer': result.producer.entity_id, 'producer_process_id': id(result.producer),
            'event_process_id': id(result.event), 'data_process_id': id(result.event.data),
            'input_state_process_id': id(result.input_state),
            'resolved_light_state_process_id': id(result.resolved_light_state),
            'selected_sub_profile': result.selected_sub_profile,
            'previous_input_state_process_id': (None if result.previous_input_state is None
                                                 else id(result.previous_input_state)),
            'calculated_value': str(result.calculated_value), 'native_power': str(result.native_power),
            'available': result.available, 'emission_sequence': result.emission_sequence,
            'completion_sequence': result.completion_sequence,
            'completed_at': result.completed_at.isoformat(),
            'profile_process_id': id(result.old_profile), 'strategy_process_id': id(result.actual_strategy),
            'ownership_phase': result.ownership_phase, 'emitted_at_timestamp': result.emitted_at_timestamp,
            'source_last_changed': result.source_last_changed.isoformat(),
            'source_last_updated': result.source_last_updated.isoformat(),
            'source_last_reported': result.source_last_reported.isoformat(),
            'calculation_returned_at': result.calculation_returned_at.isoformat(),
            'update_started_at': result.update_started_at.isoformat(),
            'update_returned_at': result.update_returned_at.isoformat(),
            'previous_native_power': (None if result.previous_native_power is None
                                      else str(result.previous_native_power)),
            'previous_available': result.previous_available, 'update_argument': str(result.update_argument),
            'native_value_changed': result.native_value_changed,
            'native_write_attempted': result.native_write_attempted,
            'native_write_suppressed': result.native_write_suppressed,
            'native_write_returned_at': (None if result.native_write_returned_at is None
                                          else result.native_write_returned_at.isoformat())}


@dataclass(frozen=True)
class PowercalcRetainedProducerReceipt:
    evidence_sha256: str
    original_runtime_token: bytes
    chain_head: str
    source_hashes_json: bytes
    scope_bindings_json: bytes
    native_operations_json: bytes
    calculations: tuple[PowercalcActualCalculation, ...]
    retained_calculations_verified: bool
    retained_producer_lifecycle_verified: bool = field(default=True, init=False)
    continuity_verified: bool = field(default=False, init=False)
    counter_ownership_verified: bool = field(default=False, init=False)
    reload_ownership_verified: bool = field(default=False, init=False)
    migration_ready: bool = field(default=False, init=False)
    family_complete: bool = field(default=False, init=False)
    status: str = field(default='UNKNOWN', init=False)

    @property
    def calculations_json(self):
        return _json([_result_row(result) for result in self.calculations])


@dataclass(frozen=True)
class PowercalcRetainedProducerReloadWitness:
    lifecycle: object
    global_rename: object
    exact_fields: object
    retained_producers: PowercalcRetainedProducerReceipt
    continuity_verified: bool = field(default=False, init=False)
    counter_ownership_verified: bool = field(default=False, init=False)
    reload_ownership_verified: bool = field(default=False, init=False)
    migration_ready: bool = field(default=False, init=False)
    family_complete: bool = field(default=False, init=False)
    status: str = field(default='UNKNOWN', init=False)


@dataclass
class _Bridge:
    callback: object
    remover: object
    job: object
    callbacks: object
    dependencies: frozenset[str]
    removed: bool = False


@dataclass
class _Flight:
    event: object
    state: object
    snapshot: bytes
    sequence: int
    task: object = None
    calculated: object = None
    resolved: object = None
    resolved_snapshot: bytes | None = None
    writes: int = 0
    handles: int = 0
    emitted_at_timestamp: float | None = None
    source_times: tuple = ()
    calculation_returned_at: datetime | None = None
    update_started_at: datetime | None = None
    update_returned_at: datetime | None = None
    previous_native_power: object = None
    previous_available: object = None
    update_argument: object = None
    write_attempts: int = 0
    native_write_returned_at: datetime | None = None
    previous_state: object = None
    previous_snapshot: bytes | None = None


class _RetainedProducerOwner:
    """Component of existing _Reload; owns only its actual bridges and tasks."""
    def __init__(self, scope):
        from homeassistant.core import EventBus, HomeAssistant, StateMachine
        from homeassistant.helpers import event
        from homeassistant.helpers.entity import Entity
        _need(scope.exact_owner is not None, 'typed_exact_owner_required')
        self.scope, self.event = scope, event
        self.cls = scope.vendor.classes['VirtualPowerSensor']
        self.old = {name: entity for name, entity in scope.old.items() if type(entity) is self.cls}
        _need(len(self.old) == 2, 'two_actual_profile_producers')
        self.vrefs = tuple((self.cls, name) for name in (
            'async_added_to_hass', '_register_tracking_listeners',
            '_handle_source_entity_state_change', 'calculate_power',
            '_resolve_calculation_state', '_update_power_and_write_state'))
        self.hrefs = ((StateMachine, 'async_set_internal'), (EventBus, 'async_fire_internal'),
                      (EventBus, '_async_dispatch'), (HomeAssistant, 'async_run_hass_job'),
                      (HomeAssistant, 'async_create_task'), (Entity, '_async_write_ha_state')) + tuple(
            (event, name) for name in ('async_track_state_change_event', '_async_track_state_change_event',
                                      '_async_track_event', '_remove_listener',
                                      '_async_dispatch_entity_id_event')) + (
            (HomeAssistant, 'async_create_task_internal'), (Entity, 'async_write_ha_state'))
        self.bound = None
        self.bridges, self.active, self.tasks, self.new = {}, {}, set(), {}
        self.observed_native_tasks = set()
        self.phase = {name: 'native_old' for name in self.old}
        self.emitted, self.seen = {}, {}
        self.results, self.operations = [], []
        self.sequence = 0
        self.handoffs = {}
        self.poison = None

    def bind_codes(self):
        scope = self.scope
        self.vm = tuple(scope.vendor_methods[scope.vendor_refs.index(ref)] for ref in self.vrefs)
        self.vc = tuple(scope.vendor_codes[scope.vendor_refs.index(ref)] for ref in self.vrefs)
        self.hm = tuple(scope.ha_methods[scope.ha_refs.index(ref)] for ref in self.hrefs)
        self.hc = tuple(scope.ha_codes[scope.ha_refs.index(ref)] for ref in self.hrefs)
        callbacks = [code for code in _code_objects(self.vc[0])
                     if code.co_name == 'appliance_state_listener']
        _need(len(callbacks) == 1, 'native_appliance_callback_code')
        self.appliance_code = callbacks[0]
        return {self.vc[1]: 'producer_tracking', self.vc[2]: 'producer_handle',
                self.vc[3]: 'producer_calculate', self.vc[4]: 'producer_resolve',
                self.vc[5]: 'producer_update', self.hc[1]: 'producer_fire',
                self.hc[5]: 'producer_ha_write'}

    def bind_original(self, bound):
        from homeassistant.helpers import entity_registry as er, entity_platform as ep
        from .powercalc_global_listener_ownership import _OriginalPhysicalSource
        self.bound = bound
        rows = {row.entity_id: row for row in bound.rows}
        self.dependencies, self.callbacks, self.inputs, self.identities, self.profiles = {}, {}, {}, {}, {}
        self.profile_data, self.source_fields, self.selector_data, self.semantics = {}, {}, {}, {}
        self.profile_specs = {}
        graph = json.loads(self.scope.evidence.powercalc_sources.closed_graph_json)
        registry = self.scope.hass.data[er.DATA_REGISTRY]
        self.registry = registry
        active = {name: entity for bucket in self.scope.hass.data[ep.DATA_ENTITY_PLATFORM].values()
                  for platform in bucket for name, entity in platform.entities.items()}
        for name, old in self.old.items():
            row = rows[name]
            _need(row.source_verified and row.relation_kind == 'profile_lut', 'original_profile_binding')
            deps = frozenset(old._track_entities)
            _need(deps and deps == frozenset(edge[0] for edge in row.edges), 'original_dependencies')
            self.dependencies[name] = deps
            self.callbacks[name] = self._native_tracking(old, deps)
            self.identities[name] = (old.registry_entry, old.unique_id, old.platform.config_entry,
                                     old._source_entity, old._power_profile, old._sub_profile_selector,
                                     old.calculation_strategy_factory)
            profile = old._power_profile
            self.profiles[name] = profile
            specs = [spec for spec in graph['profiles'] if spec['unique_id'] == old.unique_id]
            _need(len(specs) == 1, 'same_original_profile_spec')
            self.profile_specs[name] = specs[0]
            self.profile_data[name] = (_json(profile._base_json_data), _json(profile._sub_profiles))
            source = old._source_entity
            self.source_fields[name] = (source.entity_id, source.unique_id, source.domain, source.object_id)
            self.semantics[name] = (old._multiply_factor, frozenset(old._off_states),
                old._rounding_digits, old._ignore_unavailable_state, old._availability_entity,
                old._calculation_enabled_condition, old._standby_sensors)
            selector = old._sub_profile_selector
            self.selector_data[name] = (None if selector is None else
                (_json({'default': selector._config.default, 'matchers': selector._config.matchers}),
                 tuple((matcher, matcher._entity_id, _json(matcher._mapping))
                       for matcher in selector._matchers)))
            _need(not old._sensor_config.get('sleep_power')
                  and not old._sensor_config.get('power_update_interval'), 'no_retained_periodic_or_sleep_path')
            for dependency in deps:
                entity = active.get(dependency)
                source = registry.async_get(dependency)
                _need(entity is not None and source is not None and source.platform != 'powercalc'
                      and entity.registry_entry is source, 'external_actual_input')
                binding = (entity, type(entity), entity.platform, _OriginalPhysicalSource.from_row(source))
                _need(dependency not in self.inputs or self.inputs[dependency] == binding,
                      'ambiguous_physical_input')
                self.inputs[dependency] = binding
        self.check()

    def check(self):
        from homeassistant.helpers import entity_registry as er
        from .powercalc_global_listener_ownership import _OriginalPhysicalSource
        _need(self.poison is None and self.scope.error is None, 'poisoned')
        self.scope.exact_owner.check()
        unchanged(self.vrefs, self.vm, self.vc)
        unchanged(self.hrefs, self.hm, self.hc)
        _need(asyncio.get_running_loop() is self.scope.loop, 'actual_event_loop')
        if self.bound is None:
            return
        _need(self.scope.hass.data[er.DATA_REGISTRY] is self.registry, 'input_registry_changed')
        for name, (entity, cls, platform, identity) in self.inputs.items():
            row = self.registry.async_get(name)
            _need(row is not None and _OriginalPhysicalSource.from_row(row) == identity
                  and type(entity) is cls and entity.entity_id == name and entity.hass is self.scope.hass
                  and entity.registry_entry is row and entity.platform is platform
                  and platform.entities.get(name) is entity
                  and entity.unique_id == identity.unique_id, 'foreign_physical_input')
        for name, old in self.old.items():
            from .powercalc_source_binding import _dispatch_token
            row, uid, entry, source, profile, selector, factory = self.identities[name]
            _need(old.registry_entry is row and old.unique_id == uid
                  and old.platform.config_entry is entry and old._source_entity is source
                  and old._power_profile is profile and old._sub_profile_selector is selector
                  and old.calculation_strategy_factory is factory, 'old_profile_owner_changed')
            for owner, method in self.vrefs:
                actual = getattr(old, method)
                _need(getattr(actual, '__self__', None) is old
                      and getattr(actual, '__func__', None) is getattr(owner, method),
                      'producer_instance_dispatch_changed')
            from homeassistant.helpers.entity import Entity
            # Reuse the existing actual-instance proof for every already-pinned
            # method used by this producer, not only the observed frame methods.
            # Read-only native properties use the existing class/getter code
            # proof; reading native_value/source_entity is not a bound method.
            _dispatch_token(old, self.cls, tuple(dict.fromkeys(method for owner, method
                in self.scope.vendor.refs if owner is self.cls
                and not isinstance(getattr(self.cls, method), property))))
            _dispatch_token(old, Entity, ('async_write_ha_state', '_async_write_ha_state'))
            _dispatch_token(factory, self.scope.vendor.classes['PowerCalculatorStrategyFactory'],
                            ('create', '_create_lut'))
            _need(factory._prepare is self.scope.vendor.classes['PowerCalculatorStrategyFactory']._prepare,
                  'native_factory_instance_dispatch_changed')
            strategy = old._strategy_instance
            _need(type(strategy) is self.scope.vendor.classes['LutStrategy']
                  and strategy._source_entity is source and strategy._profile is profile
                  and strategy._lut_registry is factory._lut_registry
                  and source.entity_id == next(row.source for row in self.bound.rows if row.entity_id == name)
                  and source.entity_entry is self.registry.async_get(source.entity_id),
                  'actual_native_strategy_graph')
            _need((old._multiply_factor, frozenset(old._off_states), old._rounding_digits,
                   old._ignore_unavailable_state, old._availability_entity,
                   old._calculation_enabled_condition, old._standby_sensors) == self.semantics[name]
                  and old._sensor_config.get('entity_id') == source.entity_id
                  and not old._sensor_config.get('sleep_power')
                  and not old._sensor_config.get('power_update_interval')
                  and old._sensor_config.get('unavailable_power') is None,
                  'original_producer_semantics_changed')
            _need((source.entity_id, source.unique_id, source.domain, source.object_id) == self.source_fields[name]
                  and (_json(profile._base_json_data), _json(profile._sub_profiles)) == self.profile_data[name],
                  'original_profile_or_source_data_changed')
            selected = dict(profile._base_json_data)
            if profile.sub_profile is not None:
                variants = [data for key, data in profile._sub_profiles if key == profile.sub_profile]
                _need(len(variants) == 1, 'known_native_profile_variant')
                selected.update(variants[0])
            _need(_json(selected) == _json(profile._json_data), 'native_active_profile_data_changed')
            spec = self.profile_specs[name]
            variants = [v for v in spec['variants'] if v['name'] == profile.sub_profile]
            _need(len(variants) == 1 and profile._hass is self.scope.hass
                  and profile.manufacturer == spec['manufacturer'] and profile.model == spec['model']
                  and Path(profile._directory) == Path(self.scope.hass.config.config_dir) / spec['directory']
                  and profile._sub_profile_dir == (None if profile.sub_profile is None else
                      str(Path(self.scope.hass.config.config_dir) / spec['directory'] / profile.sub_profile))
                  and {str(mode) for mode in strategy._supported_modes} == set(variants[0]['modes']),
                  'same_original_selected_profile_modes')
            _need(old._standby_sensors is self.semantics[name][-1]
                  and old._standby_sensors is self.scope.hass.data['powercalc']['standby_power_sensors']
                  and old._standby_power_on == 0
                  and old._standby_power == Decimal(str(profile._json_data.get('standby_power', 0))),
                  'native_standby_binding_changed')
            if selector is not None:
                frozen_config, matchers = self.selector_data[name]
                _need(_json({'default': selector._config.default, 'matchers': selector._config.matchers}) == frozen_config
                      and len(selector._matchers) == len(matchers), 'native_selector_config_changed')
                for actual, (original, dependency, mapping) in zip(selector._matchers, matchers, strict=True):
                    _need(actual is original and actual._entity_id == dependency
                          and _json(actual._mapping) == mapping, 'native_selector_matcher_changed')
                    _dispatch_token(actual, self.scope.vendor.classes['EntityStateMatcher'],
                                    ('match', 'get_tracking_entities'))
                _dispatch_token(selector, self.scope.vendor.classes['SubProfileSelector'],
                                ('select_sub_profile', 'get_tracking_entities'))
            _dispatch_token(strategy, self.scope.vendor.classes['LutStrategy'],
                            ('calculate', 'initialize', 'validate_config', 'get_selected_color_mode',
                             'lookup_power', 'create_light_setting', 'is_enabled',
                             'get_entities_to_track', 'can_calculate_standby',
                             'lookup_power_for_brightness', 'get_nearest', '_calculate_effect_power'))
            _dispatch_token(profile, self.scope.vendor.classes['PowerProfile'],
                            ('select_sub_profile', 'get_model_directory'))

    def verify_actual_lut(self, name):
        # The existing binder's source-data checks, at the two real calculation
        # boundaries. No decoding/IO or counter values enter this verification.
        from .powercalc_profile_inputs import digest, table_digest
        from .powercalc_source_binding import _dispatch_token
        old, spec = self.old[name], self.profile_specs[name]
        profile, strategy = old._power_profile, old._strategy_instance
        registry, cls = strategy._lut_registry, self.scope.vendor.classes
        variant = next(v for v in spec['variants'] if v['name'] == profile.sub_profile)
        _need(digest(profile._base_json_data) == spec['base_sha256']
              and digest(profile._sub_profiles) == spec['sub_profiles_sha256']
              and digest(profile._json_data) == variant['json_sha256'], 'actual_profile_data_changed')
        _need(registry._cache_key is cls['LutRegistry']._cache_key
              and registry.get_lut_file is cls['LutRegistry'].get_lut_file
              and registry._load_lut_entry == cls['LutRegistry']._load_lut_entry
              and registry._supported_modes.get((profile.manufacturer, profile.model,
                  profile.sub_profile, 'supported_modes')) == strategy._supported_modes,
              'actual_lut_registry_dispatch_or_modes_changed')
        _dispatch_token(registry, cls['LutRegistry'], ('get_lookup_entry', 'get_supported_modes'))
        _need(strategy._nearest_key is cls['LutStrategy']._nearest_key
              and strategy._interpolate is cls['LutStrategy']._interpolate,
              'actual_lut_instance_dispatch_changed')
        for key, entry in registry._lut_entries.items():
            if key[:2] != (profile.manufacturer, profile.model):
                continue
            variants = [v for v in spec['variants'] if v['name'] == key[3]]
            _need(len(variants) == 1, 'actual_known_lut_variant')
            mode = str(key[2])
            _need(type(entry) is cls['_LutEntry'] and mode in variants[0]['modes']
                  and type(entry.sorted_keys) is list and entry.sorted_keys == sorted(entry.table)
                  and table_digest(entry.table) == variants[0]['modes'][mode]['table_sha256'],
                  'actual_lut_cache_changed')

    def _native_tracking(self, entity, dependencies):
        from homeassistant.core import HassJob
        from .yaml_runtime_observer import _registered_bus_remove
        data = self.scope.hass.data.get(self.event._TRACK_STATE_CHANGE_DATA)
        _need(type(data) is self.event._KeyedEventData and type(data.callbacks) is defaultdict
              and data.callbacks.default_factory is list, 'native_keyed_tracker')
        (bus_job, event_filter), _ = _registered_bus_remove(self.scope.hass, data.listener, 'state_changed')
        for actual, expected in ((bus_job.target, self.event._async_dispatch_entity_id_event),
                                 (event_filter, self.event._async_state_filter)):
            _need(type(actual) is partial and actual.func is expected and not actual.keywords
                  and actual.args == (self.scope.hass, data.callbacks), 'native_keyed_dispatch_binding')
        owned = []
        for key, members in data.callbacks.items():
            for job in members:
                target = job.target
                if getattr(target, '__code__', None) is not self.appliance_code:
                    continue
                cells = dict(zip(target.__code__.co_freevars,
                                 (cell.cell_contents for cell in target.__closure__), strict=True))
                if cells.get('self') is entity:
                    _need(type(job) is HassJob, 'actual_native_job')
                    owned.append((key, job))
        _need({key for key, _ in owned} == set(dependencies) and len(owned) == len(dependencies)
              and len({id(job) for _, job in owned}) == 1, 'native_tracking_membership')
        job = owned[0][1]
        removers = [remove for remove in entity._on_remove if type(remove) is partial
                    and remove.func is self.event._remove_listener and not remove.keywords
                    and len(remove.args) == 5 and remove.args[0] is self.scope.hass
                    and remove.args[1] is self.event._KEYED_TRACK_STATE_CHANGE
                    and set(remove.args[2]) == set(dependencies)
                    and len(tuple(remove.args[2])) == len(dependencies)
                    and remove.args[3] is job and remove.args[4] is data.callbacks]
        _need(len(removers) == 1, 'native_tracking_remover')
        return job.target

    def removed(self, entity):
        from homeassistant.core import callback, HassJob, HassJobType
        from homeassistant.helpers.entity import EntityPlatformState
        name = entity.entity_id
        if self.old.get(name) is not entity:
            return
        self.check()
        _need(self.phase[name] == 'native_old' and name in self.scope.removed
              and entity._platform_state is EntityPlatformState.REMOVED and not entity._on_remove,
              'observed_native_removal')
        @callback
        def bridge(event):
            try:
                self._bridge(name, event)
            except BaseException as error:
                self._poison(error)
        # Register only after the old native remover really ran. This function
        # returns the actual HA remover; no callback or Event is fabricated.
        remover = self.event.async_track_state_change_event(
            self.scope.hass, tuple(sorted(self.dependencies[name])), bridge)
        _need(type(remover) is partial and remover.func is self.event._remove_listener
              and not remover.keywords and len(remover.args) == 5
              and remover.args[0] is self.scope.hass
              and remover.args[1] is self.event._KEYED_TRACK_STATE_CHANGE, 'actual_bridge_remover')
        job, callbacks = remover.args[3:]
        self.bridges[name] = _Bridge(bridge, remover, job, callbacks, self.dependencies[name])
        _need(type(job) is HassJob and job.job_type is HassJobType.Callback and job.target is bridge,
              'actual_bridge_native_job')
        self.phase[name] = 'retained'
        self.operations.append(['bridge_add', name, id(job), id(remover)])

    def _poison(self, error):
        if self.poison is None:
            reason = str(error) if type(error) is ValueError and str(error).startswith(
                'powercalc_retained_producer_') else type(error).__name__
            self.poison = reason
        if self.scope.error is None:
            self.scope.error = 'retained_producer_refused'

    def observe(self, tag, frame, phase, result=None):
        if tag == 'producer_fire':
            if phase == 'start':
                self._emission(frame)
            return
        entity = frame.f_locals.get('self')
        name = getattr(entity, 'entity_id', None)
        if name not in self.old:
            return
        if tag == 'producer_tracking':
            if entity is self.old[name]:
                return
            self._handoff(entity, frame, phase)
            return
        if entity is not self.old[name]:
            return
        if tag == 'producer_handle' and phase == 'start':
            task = asyncio.current_task()
            _need(type(task) is asyncio.Task and task.get_loop() is self.scope.loop,
                  'actual_native_calculation_task')
            self.observed_native_tasks.add(task)
            if self.phase[name] == 'native_old':
                # Preflight was idle. A native calculation newly arriving before
                # the retained boundary is outside this prerequisite's delivery
                # ownership. Refuse and settle this exact observed Task.
                _need(False, 'native_input_before_retained_boundary')
        if self.phase[name] == 'native_old':
            return
        self.check()
        flight = self.active.get(name)
        _need(flight is not None and flight.task is asyncio.current_task()
              and self.scope.causal.get(None) is self.scope.marker, 'foreign_retained_calculation')
        if tag == 'producer_handle':
            _need(frame.f_locals.get('state') is flight.state
                  and frame.f_locals.get('trigger_entity_id') == entity._source_entity.entity_id,
                  'actual_callback_input')
            if phase == 'start':
                _need(frame.f_back is not None and frame.f_back.f_code is self.appliance_code,
                      'native_appliance_parent')
                flight.handles += 1
        elif tag == 'producer_resolve' and phase == 'return':
            _need(result is self.scope.hass.states.get(entity._source_entity.entity_id),
                  'actual_resolved_light_head')
            flight.resolved, flight.resolved_snapshot = result, _state_snapshot(result)
        elif tag == 'producer_calculate' and phase == 'return':
            _need(type(result) is Decimal and result.is_finite() and flight.calculated is None,
                  'one_actual_finite_calculation')
            flight.calculated = result
            flight.calculation_returned_at = datetime.now(timezone.utc)
        elif tag == 'producer_update':
            from homeassistant.helpers.entity import EntityPlatformState
            _need(entity._platform_state is EntityPlatformState.REMOVED, 'removed_write_guard')
            _need(frame.f_back is not None and frame.f_back.f_code is self.vc[2],
                  'native_update_parent')
            if phase == 'start':
                _need(frame.f_locals.get('power') is flight.calculated,
                      'actual_calculated_update_argument')
                _need(flight.update_started_at is None, 'one_native_update')
                flight.update_started_at = datetime.now(timezone.utc)
                flight.previous_native_power = entity._power
                flight.previous_available = entity._attr_available
                flight.update_argument = frame.f_locals['power']
            else:
                # The pinned native body replaces its local argument with
                # round(power, precision). Keep the original entry argument
                # separately and verify the actual normalized field on return.
                normalized = frame.f_locals.get('power')
                _need(type(normalized) is Decimal and normalized.is_finite()
                      and type(entity._power) is Decimal and normalized == entity._power,
                      'actual_normalized_update_result')
                flight.update_returned_at = datetime.now(timezone.utc)
                flight.writes += 1
        elif tag == 'producer_ha_write':
            from homeassistant.helpers.entity import EntityPlatformState
            _need(entity._platform_state is EntityPlatformState.REMOVED
                  and frame.f_back is not None and frame.f_back.f_code is self.hc[12],
                  'actual_suppressed_entity_write')
            if phase == 'start':
                flight.write_attempts += 1
            else:
                flight.native_write_returned_at = datetime.now(timezone.utc)

    def _emission(self, frame):
        from homeassistant.core import State
        if frame.f_locals.get('self') is not self.scope.hass.bus:
            return
        data = frame.f_locals.get('event_data')
        if type(data) is not dict or data.get('entity_id') not in getattr(self, 'inputs', {}):
            return
        if frame.f_locals.get('event_type') != 'state_changed':
            return  # Native W listeners do not consume state_reported.
        name = data['entity_id']
        owners = [producer for producer, deps in self.dependencies.items()
                  if name in deps and self.phase[producer] == 'retained']
        if not owners:
            return
        self.check()
        _need(frame.f_locals.get('event_type') == 'state_changed', 'only_actual_changed_input')
        parent = frame.f_back
        state = data.get('new_state')
        _need(parent is not None and parent.f_code is self.hc[0]
              and parent.f_locals.get('self') is self.scope.hass.states
              and parent.f_locals.get('state_changed_data') is data
              and parent.f_locals.get('state') is state and type(state) is State
              and state is self.scope.hass.states.get(name), 'native_statemachine_emission')
        ancestor = parent.f_back
        emitter = None
        try:
            while ancestor is not None:
                if ancestor.f_code is self.hc[5]:
                    emitter = ancestor.f_locals.get('self')
                    break
                ancestor = ancestor.f_back
        finally:
            del ancestor
        _need(emitter is self.inputs[name][0], 'actual_physical_entity_write')
        _need(state.state not in ('unavailable', 'unknown')
              and id(data) not in self.seen and id(data) not in self.emitted,
              'usable_unique_input_emission')
        _need(all(producer not in self.active for producer in owners), 'overlapping_input_inconclusive')
        self.sequence += 1
        time_fired = frame.f_locals.get('time_fired')
        _need(type(time_fired) is float, 'actual_emission_timestamp')
        previous = data.get('old_state')
        _need(previous is None or type(previous) is State, 'actual_previous_input')
        self.emitted[id(data)] = (data, state, _state_snapshot(state), self.sequence, frozenset(owners),
                                 time_fired, (state.last_changed, state.last_updated, state.last_reported),
                                 previous, None if previous is None else _state_snapshot(previous),
                                 tuple(sorted(data)))

    def _dispatch(self, bridge, event):
        from homeassistant.core import Event
        _need(type(event) is Event and event.event_type == 'state_changed', 'actual_native_event')
        frame = sys._getframe(1)
        keyed = bus = job = False
        try:
            while frame is not None:
                if frame.f_code is self.hc[10]:
                    keyed = (frame.f_locals.get('hass') is self.scope.hass
                             and frame.f_locals.get('callbacks') is bridge.callbacks
                             and frame.f_locals.get('event') is event
                             and frame.f_locals.get('job') is bridge.job)
                elif frame.f_code is self.hc[2]:
                    bus = (frame.f_locals.get('self') is self.scope.hass.bus
                           and frame.f_locals.get('event') is event
                           and frame.f_locals.get('event_data') is event.data)
                elif frame.f_code is self.hc[3] and frame.f_locals.get('hassjob') is bridge.job:
                    job = frame.f_locals.get('self') is self.scope.hass
                frame = frame.f_back
        finally:
            del frame
        _need(keyed and bus and job, 'actual_keyed_bus_dispatch')

    def _bridge(self, name, event):
        self.check()
        bridge = self.bridges[name]
        self._dispatch(bridge, event)
        data = event.data
        emitted = self.emitted.get(id(data))
        _need(emitted is not None and emitted[0] is data and name in emitted[4]
              and not bridge.removed and self.phase[name] == 'retained'
              and name not in self.active, 'one_owned_delivery')
        _, state, snapshot, sequence, owners, emitted_at, source_times, previous, previous_snapshot, keys = emitted
        _need(data.get('new_state') is state and state is self.scope.hass.states.get(state.entity_id)
              and _state_snapshot(state) == snapshot
              and data.get('old_state') is previous and tuple(sorted(data)) == keys
              and (previous is None or _state_snapshot(previous) == previous_snapshot),
              'input_changed_before_delivery')
        delivered = self.seen.setdefault(id(data), (data, set()))[1]
        _need(name not in delivered, 'duplicate_actual_delivery')
        delivered.add(name)
        if delivered == set(owners):
            del self.emitted[id(data)]
        flight = _Flight(event, state, snapshot, sequence,
                         emitted_at_timestamp=emitted_at, source_times=source_times,
                         previous_state=previous, previous_snapshot=previous_snapshot)
        self.active[name] = flight
        token = self.scope.causal.set(self.scope.marker)
        try:
            # Real HA-owned eager Task. Synchronous native calculations finish
            # before dispatch returns; genuine awaits remain tracked descendants.
            task = self.scope.hass.async_create_task(
                self._consume(name, flight), 'renamer.powercalc.retained.' + name, eager_start=True)
            _need(type(task) is asyncio.Task and task.get_loop() is self.scope.loop
                  and flight.task is task, 'actual_owned_task')
            self.tasks.add(task)
            task.add_done_callback(self._task_done)
        finally:
            self.scope.causal.reset(token)

    def _task_done(self, task):
        # Retrieve completed errors even when eager execution finished before
        # _Reload.settle_descendants sampled its pending descendants.
        try:
            if task.cancelled():
                self._poison(ValueError('powercalc_retained_producer_owned_task_cancelled'))
            elif (error := task.exception()) is not None:
                self._poison(error)
        except BaseException as error:
            self._poison(error)

    async def _consume(self, name, flight):
        from homeassistant.helpers.entity import EntityPlatformState
        flight.task = asyncio.current_task()
        try:
            self.check()
            old = self.old[name]
            self.verify_actual_lut(name)
            _need(old._platform_state is EntityPlatformState.REMOVED
                  and _state_snapshot(flight.state) == flight.snapshot, 'retained_input_at_start')
            await self.callbacks[name](flight.event)  # SAME actual Event/native closure.
            self.check()
            self.verify_actual_lut(name)
            _need(self.active.get(name) is flight and flight.handles == flight.writes == 1
                  and type(flight.calculated) is Decimal and flight.calculated.is_finite()
                  and flight.resolved is not None
                  and _state_snapshot(flight.state) == flight.snapshot
                  and _state_snapshot(flight.resolved) == flight.resolved_snapshot
                  and type(old._power) is Decimal and old._power.is_finite()
                  and old._attr_available is True
                  and old._platform_state is EntityPlatformState.REMOVED
                  and flight.calculation_returned_at is not None
                  and flight.update_started_at is not None and flight.update_returned_at is not None
                  and flight.write_attempts in (0, 1)
                  and type(flight.previous_available) is bool
                  and flight.event.event_type == 'state_changed'
                  and flight.event.data.get('new_state') is flight.state
                  and flight.event.data.get('old_state') is flight.previous_state,
                  'completed_native_result_inconclusive')
            self.results.append(PowercalcActualCalculation(
                producer=old, event=flight.event, input_state=flight.state,
                resolved_light_state=flight.resolved, calculated_value=flight.calculated,
                native_power=old._power, available=old._attr_available,
                emission_sequence=flight.sequence, completion_sequence=len(self.results) + 1,
                completed_at=datetime.now(timezone.utc), input_snapshot_json=flight.snapshot,
                resolved_snapshot_json=flight.resolved_snapshot, old_profile=old._power_profile,
                actual_strategy=old._strategy_instance,
                selected_sub_profile=old._power_profile.sub_profile,
                previous_input_state=flight.previous_state, previous_snapshot_json=flight.previous_snapshot,
                ownership_phase='retained_removed',
                emitted_at_timestamp=flight.emitted_at_timestamp,
                source_last_changed=flight.source_times[0], source_last_updated=flight.source_times[1],
                source_last_reported=flight.source_times[2],
                calculation_returned_at=flight.calculation_returned_at,
                update_started_at=flight.update_started_at, update_returned_at=flight.update_returned_at,
                previous_native_power=flight.previous_native_power,
                previous_available=flight.previous_available, update_argument=flight.update_argument,
                native_value_changed=(flight.previous_native_power != old._power
                                      or flight.previous_available != old._attr_available),
                native_write_attempted=bool(flight.write_attempts),
                native_write_returned_at=flight.native_write_returned_at))
            self.operations.append(['calculation_complete', name, flight.sequence, id(flight.task)])
        except BaseException as error:
            self._poison(error)
            raise
        finally:
            if self.active.get(name) is flight:
                del self.active[name]

    def _remove_bridge(self, name):
        bridge = self.bridges.get(name)
        if bridge is None or bridge.removed:
            return
        # Cleanup uses the exact native remover/membership, even if guard or
        # physical inputs drifted. It never adopts/removes a foreign callback.
        unchanged(self.hrefs, self.hm, self.hc)
        _need(all(sum(job is bridge.job for job in bridge.callbacks.get(key, ())) == 1
                  for key in bridge.dependencies), 'bridge_membership_changed')
        bridge.remover()
        bridge.removed = True
        _need(all(all(job is not bridge.job for job in members)
                  for members in bridge.callbacks.values()), 'bridge_remove_failed')
        self.operations.append(['bridge_remove', name, id(bridge.job), id(bridge.remover)])

    def _handoff(self, entity, frame, phase):
        from homeassistant.helpers.entity import EntityPlatformState
        name = entity.entity_id
        self.check()
        old = self.old[name]
        row, uid, entry, *_ = self.identities[name]
        _need(type(entity) is self.cls and entity.hass is self.scope.hass
              and entity.registry_entry is row and entity.unique_id == uid
              and entity.platform.config_entry is entry
              and entity._platform_state is EntityPlatformState.ADDING
              and self.scope.causal.get(None) is self.scope.marker
              and type(asyncio.current_task()) is asyncio.Task
              and frame.f_back is not None and frame.f_back.f_code is self.vc[0]
              and frame.f_back.f_locals.get('self') is entity, 'actual_new_tracking_seam')
        if phase == 'start':
            _need(self.phase[name] == 'retained' and old._platform_state is EntityPlatformState.REMOVED
                  and set(frame.f_locals.get('entities_to_track', ())) == set(self.dependencies[name]),
                  'prepared_tracking_dependencies')
            self._remove_bridge(name)  # Atomic synchronous handoff to native registration.
            _need(name not in self.active
                  and all(name not in emission[4] for emission in self.emitted.values()),
                  'pending_calculation_at_native_handoff')
            self.phase[name] = 'handoff'
            self.handoffs[name] = (entity, asyncio.current_task())
            self.operations.append(['native_tracking_start', name, id(entity),
                                    datetime.now(timezone.utc).isoformat()])
        else:
            _need(self.phase[name] == 'handoff'
                  and self.handoffs[name] == (entity, asyncio.current_task()), 'one_native_handoff')
            self._native_tracking(entity, self.dependencies[name])
            self.phase[name] = 'native_new'
            self.new[name] = entity
            self.operations.append(['native_handoff', name, id(old), id(entity), id(asyncio.current_task()),
                                    datetime.now(timezone.utc).isoformat()])

    def pending_tasks(self):
        return tuple(task for task in self.tasks | self.observed_native_tasks if not task.done())

    def finish(self, current):
        self.check()
        _need(not self.active and not self.emitted
              and set(self.new) == set(self.old)
              and all(self.phase[name] == 'native_new' and current[name] is self.new[name]
                      and self.bridges[name].removed for name in self.old), 'incomplete_producer_slice')
        _need(all(task.done() and not task.cancelled() and task.exception() is None for task in self.tasks),
              'unsettled_owned_calculations')
        for name in self.old:
            self._native_tracking(current[name], self.dependencies[name])
        bindings = {'operation_id': self.scope.exact_owner.policy.chain.operation_id,
                    'root_id': self.scope.exact_owner.policy.chain.root_id,
                    'head': self.scope.exact_owner.policy.state.head,
                    'inputs': {name: vars(binding[3]) for name, binding in self.inputs.items()},
                    'vendor_code_process_ids': [id(code) for code in self.vc],
                    'ha_code_process_ids': [id(code) for code in self.hc]}
        return PowercalcRetainedProducerReceipt(self.scope.evidence.sha256,
            self.scope.original.structural_token, self.scope.exact_owner.policy.state.head,
            self.scope.exact_owner.policy.source_hashes_json, _json(bindings),
            _json(self.operations), tuple(self.results), bool(self.results))

    def annotate(self, error):
        error.add_note('powercalc_retained_producer_refusal=' + str(self.poison)
                       + ';no_energy_or_group_continuity')
        error.add_note('powercalc_retained_producer_partial_results=' + _json(
            [_result_row(result) for result in self.results]).decode())

    def close(self):
        first = None
        for name in self.bridges:
            try:
                self._remove_bridge(name)
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first


async def async_reload_with_powercalc_retained_producers(hass, chain, evidence, original, *, guard):
    """Typed retained actual-W prerequisite; no Energy/group continuity claim."""
    from .powercalc_exact_fields import _ExactFieldsPolicy
    from .powercalc_reload_witness import async_witness_unchanged_powercalc_reload
    policy = _ExactFieldsPolicy(chain, evidence, original, guard)
    return await async_witness_unchanged_powercalc_reload(hass, policy.receipt, evidence, original,
        guard=guard, _manage_global_rename=True, _exact_fields=policy, _retain_producers=True)
