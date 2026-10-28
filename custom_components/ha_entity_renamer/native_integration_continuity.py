"""Bounded native Integration timer continuation.

Actual native objects remain the only arithmetic writers. This product adapter
migrates their actual fields and timer ownership at a pinned synchronous setup
seam. Typed source-ID/own-ID policies bind the existing ORIGINAL/accepted chain.
An owner must supply the existing full-source guard, lease and immutable store.
Restart recovery remains a separate capability.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
from importlib import import_module
import math
from pathlib import Path
import sys
import threading

from .helper_native import read_entry
from .native_helper_activation_lease import NativeHelperActivationLease
from .native_integration_unload_audit import _INTEGRATION, _INIT
from .native_registry_receipts import RegistryReceiptStore
from .native_unload_code_proof import prove_methods, unchanged
from .native_utility_setup_audit import _SOURCES, _code_objects
from .native_utility_source_emissions import NativeSourceEmissions
from .prepared import canonical

_NAME = 'ha_entity_renamer.integration_continuity'
_EVENT_PINS = {
    '2025.6.0': 'd5e21c38afd9eafe566724b8db34ea2e87eaa8d6dc10c1719a82f5d90d43260e',
    '2026.10.2': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269', '2026.10.4': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269',
}


def _need(value, reason):
    if not value:
        raise ValueError('integration_continuity_' + reason) from None


def _decimal(value):
    _need(type(value) is Decimal and value.is_finite(), 'actual_finite_decimal')
    return str(value)


@dataclass(frozen=True)
class IntegrationClockSnapshot:
    full: str
    last_valid: str
    time: str
    trigger: str
    deadline: float
    endpoint: str
    endpoint_reported: str


class NativeIntegrationContinuity:
    """Single owner-task, single same-ID unload/setup, no reopening/retry.

    The departed entity is deliberately retained as an owned arithmetic engine;
    this scope must NOT be passed to the general inactive-for-all verifier.
    """
    def __init__(self):
        _need(False, 'live_capture_required')

    def __reduce_ex__(self, protocol):
        _need(False, 'ephemeral_only')

    @classmethod
    async def start(cls, hass, entry, lease, store, *, guard, source_binding=None):
        from homeassistant.components.integration.sensor import IntegrationSensor
        from homeassistant.core import HomeAssistant, EventBus, StateMachine, State, HassJob, HassJobType, callback
        from homeassistant.helpers import entity_platform as ep, entity_registry as er, event
        from homeassistant.helpers.entity import Entity
        version = import_module('homeassistant.const').__version__
        _need(cls is NativeIntegrationContinuity and version in _INTEGRATION
              and type(lease) is NativeHelperActivationLease
              and type(store) is RegistryReceiptStore and callable(guard)
              and type(hass) is HomeAssistant and type(hass.states) is StateMachine
              and type(hass.bus) is EventBus and lease.manager is hass.config_entries
              and entry.domain == 'integration' and entry.state.value == 'loaded', 'scope')
        self = object.__new__(cls)
        self.hass, self.entry, self.lease, self.store, self.guard = hass, entry, lease, store, guard
        self.loop, self.task, self.thread = asyncio.get_running_loop(), asyncio.current_task(), threading.get_ident()
        self.version, self.event, self.cls = version, event, IntegrationSensor
        self._job_cls, self._callback_kind, self._state_cls = HassJob, HassJobType.Callback, State
        self._live, self._error, self._phase = True, None, 'capture'
        self._first_failure = None
        self._retained, self._new, self._tool, self._emissions = False, None, None, None
        self._unsubscribes, self._consumed, self._emitted = [], {}, {}
        self._bridge_depth, self._native_old_calls, self._bridge_calls = 0, 0, 0
        self._timer_rearms, self._transferred, self._new_timer_bound = 0, None, False
        self._old_cancelled = None
        self.source_binding = source_binding
        self.registry = er.async_get(hass)
        rows = list(er.async_entries_for_config_entry(self.registry, entry.entry_id))
        entities = [e for p in ep.async_get_platforms(hass, 'integration')
                    if p.config_entry is entry for e in p.entities.values()]
        _need(len(rows) == 1 and len(entities) == 1, 'one_native_entity')
        self.row, self.old = rows[0], entities[0]
        _need(type(self.old) is IntegrationSensor and self.old.registry_entry is self.row
              and self.old.unique_id == entry.entry_id and self.old.hass is hass
              and self.row.platform == 'integration' and self.row.unique_id == entry.entry_id
              and self.row.disabled_by is None and self.row.config_subentry_id is None
              and self.old._max_sub_interval is not None, 'actual_timer_helper')
        self.source = self.old._sensor_source_id
        self.source_row = self.registry.async_get(self.source)
        _need(self.source_row is not None and self.source_row.disabled_by is None
              and self.old._source_entity == self.source, 'source_binding')
        self._config = canonical(read_entry(hass, entry.entry_id))[0]
        self.source_names = (self.source,)
        self._pending_move = None
        if source_binding is not None:
            from .native_integration_source_clock import IntegrationSourceClockBinding
            from .native_integration_own_clock import IntegrationOwnClockBinding
            _need(type(source_binding) in (IntegrationSourceClockBinding, IntegrationOwnClockBinding),
                  'typed_source_binding')
            source_binding.bind(self)
        self.root = Path(import_module('homeassistant').__file__).resolve().parent
        self.pins = dict(_SOURCES[version])
        self.pins.update({'components/integration/sensor.py': _INTEGRATION[version],
                          'components/integration/__init__.py': _INIT[version],
                          'helpers/event.py': _EVENT_PINS[version]})
        names = (
            ('remove_callbacks', Entity, '_call_on_remove_callbacks'),
            ('schedule', IntegrationSensor, '_schedule_max_sub_interval_exceeded_if_state_is_numeric'),
            ('added', IntegrationSensor, 'async_added_to_hass'),
            ('changed', IntegrationSensor, '_integrate_on_state_change_with_max_sub_interval'),
            ('reported', IntegrationSensor, '_integrate_on_state_report_with_max_sub_interval'),
            ('fire', EventBus, 'async_fire_internal'),
            ('source_set', StateMachine, 'async_set_internal'),
            ('source_remove', StateMachine, 'async_remove'),
            ('call_at', event, 'async_call_at'),
            ('call_later', event, 'async_call_later'),
            ('run_timer', event, '_run_async_call_action'),
            ('keyed_dispatch', event, '_async_dispatch_entity_id_event'),
            ('keyed_filter', event, '_async_state_filter'),
            ('keyed_track', event, '_async_track_event'),
            ('keyed_remove', event, '_remove_listener'),
            ('bus_remove', EventBus, '_async_remove_listener'),
        )
        if version == '2025.6.0':
            names += (('keyed_dispatch_soon', event, '_async_dispatch_entity_id_event_soon'),)
        self.refs = tuple((owner, name) for _, owner, name in names)
        self.methods, values = prove_methods(self.root, self.pins, self.refs)
        self.codes = dict(zip((name for name, _, _ in names), values, strict=True))
        raw = (self.root / 'components/integration/sensor.py').read_bytes()
        compiled = tuple(_code_objects(compile(raw, str(self.root / 'components/integration/sensor.py'),
                                              'exec', dont_inherit=True, optimize=sys.flags.optimize)))
        timer_codes = [code for code in compiled if code.co_qualname ==
            'IntegrationSensor._schedule_max_sub_interval_exceeded_if_state_is_numeric.<locals>._integrate_on_max_sub_interval_exceeded_callback']
        _need(len(timer_codes) == 1, 'timer_code_shape')
        self._timer_code = timer_codes[0]
        if self.source_binding is not None:
            self.source_binding.qualify(self)
        self._check(owner=True)
        # Require an actual already-fired timer. This prerequisite deliberately
        # has no fallback for an uninitialized, unavailable or no-timer helper.
        _need(self.old._last_integration_trigger.name == 'TimeElapsed', 'actual_tick_required')
        self.initial = self._snapshot(self.old)
        @callback
        def changed(event_object):
            self._bridge(event_object, 'changed')
        @callback
        def reported(event_object):
            self._bridge(event_object, 'reported')
        self._bridges = {'changed': changed, 'reported': reported}
        try:
            self._install_monitor()
            self._emissions = NativeSourceEmissions(self.codes['fire'], hass.bus,
                                                    self._emission, self._poison)
            self._unsubscribes.append(event.async_track_state_change_event(hass, self.source_names, changed))
            self._unsubscribes.append(event.async_track_state_report_event(hass, self.source_names, reported))
            self._require_job_order(self.old, bridges=True)
            intent = {'kind': 'integration_clock_intent', 'schema': 1,
                      'entry_id': entry.entry_id, 'helper_uuid': self.row.id,
                      'source_uuid': self.source_row.id, 'core': version,
                      'config': self._config, 'actual': asdict(self.initial)}
            if self.source_binding is not None:
                intent['source_binding'] = self.source_binding.description()
            self.intent = await self._save(intent)
            self._check(owner=True)
            return self
        except BaseException:
            self.close()
            raise

    def _poison(self, reason):
        if self._error is None:
            self._error = 'integration_continuity_' + reason
            # Preserve the FIRST caught failure without retaining exceptions,
            # frames, Event/State data or changing a native callback's outcome.
            # sys.exception() also sees the shared emission witness's except.
            try:
                error = sys.exception()
                if error is not None:
                    frames = []
                    trace = error.__traceback__
                    while trace is not None:
                        code = trace.tb_frame.f_code
                        frames.append((code.co_filename, code.co_qualname, trace.tb_lineno))
                        trace = trace.tb_next
                    # Only our fixed machine reasons, never arbitrary exception
                    # strings, user configuration, source values or frame locals.
                    detail = (error.args[0] if type(error) is ValueError
                              and len(error.args) == 1 and type(error.args[0]) is str
                              and error.args[0].startswith(('integration_continuity_',
                                                          'integration_source_clock_')) else None)
                    self._first_failure = (self._phase,
                        type(error).__module__ + '.' + type(error).__qualname__,
                        detail, tuple(frames))
            except BaseException:
                # Diagnostics must never escape into the native emitter.
                self._first_failure = None

    def _check(self, *, owner=False):
        if not self._live or self._error is not None:
            error = ValueError('integration_continuity_closed_or_poisoned')
            if self._error is not None:
                error.add_note('first_poison=' + self._error)
                if self._first_failure is not None:
                    error.add_note('first_failure=' + repr(self._first_failure))
            raise error from None
        _need(threading.get_ident() == self.thread and asyncio.get_running_loop() is self.loop
              and self.task is not None and not self.task.done()
              and (not owner or asyncio.current_task() is self.task), 'task_loop')
        _need(self.guard() is True and self.lease.owns_entry(self.entry)
              and self.hass.config_entries.async_get_entry(self.entry.entry_id) is self.entry,
              'guard_or_lease')
        from .native_integration_own_clock import IntegrationOwnClockBinding
        if type(self.source_binding) is not IntegrationOwnClockBinding:
            _need(self.registry.async_get(self.row.entity_id) is self.row, 'same_helper_id_changed')
        if self.source_binding is None:
            _need(self.registry.async_get(self.source) is self.source_row
                  and canonical(read_entry(self.hass, self.entry.entry_id))[0] == self._config,
                  'same_id_config_changed')
        else:
            self.source_binding.check(self)
        unchanged(self.refs, self.methods, tuple(self.codes.values()))
        self._require_dispatchers()
        if self._new is not None and self._new_timer_bound:
            self._require_job_order(self._new, bridges=False)
        if self._tool is not None:
            monitor = sys.monitoring
            _need(monitor.get_tool(self._tool) == _NAME and monitor.get_events(self._tool) == 0,
                  'monitor_lost')
            mask = monitor.events.PY_START | monitor.events.PY_RETURN
            _need(all(monitor.get_local_events(self._tool, code) == mask
                      for code in self._watched), 'monitor_mask_lost')
        if self._emissions is not None:
            self._emissions.check()

    def _require_dispatchers(self):
        """Read-only actual shared bus membership, routing and native filters.

        Keyed jobs alone survive tracker.listener(). Never invoke an Event,
        dispatcher or filter to manufacture a positive observation.
        """
        from collections import defaultdict
        from functools import partial
        from .yaml_runtime_observer import _registered_bus_remove
        for kind, key, event_type, tracker in (
                ('changed', self.event._TRACK_STATE_CHANGE_DATA, 'state_changed',
                 self.event._KEYED_TRACK_STATE_CHANGE),
                ('reported', self.event._TRACK_STATE_REPORT_DATA, 'state_reported',
                 self.event._KEYED_TRACK_STATE_REPORT)):
            data = self.hass.data.get(key)
            expected_dispatch = (self.event._async_dispatch_entity_id_event_soon
                if kind == 'changed' and self.version == '2025.6.0'
                else self.event._async_dispatch_entity_id_event)
            _need(type(tracker) is self.event._KeyedEventTracker
                  and tracker.key == key and tracker.event_type == event_type
                  and tracker.dispatcher_callable is expected_dispatch
                  and tracker.filter_callable is self.event._async_state_filter
                  and type(data) is self.event._KeyedEventData
                  and type(data.callbacks) is defaultdict
                  and data.callbacks.default_factory is list, 'native_tracker_routing')
            (job, event_filter), _ = _registered_bus_remove(
                self.hass, data.listener, event_type)
            _need(type(job) is self._job_cls and job.job_type is self._callback_kind,
                  'native_bus_dispatch_job')
            for actual, expected in ((job.target, expected_dispatch),
                                     (event_filter, self.event._async_state_filter)):
                _need(type(actual) is partial and actual.func is expected
                      and not actual.keywords and len(actual.args) == 2
                      and actual.args[0] is self.hass
                      and actual.args[1] is data.callbacks, 'native_bus_dispatch_routing')

    def _timer(self, entity):
        cancel = entity._max_sub_interval_exceeded_callback
        handle = getattr(cancel, '__self__', None)
        _need(type(handle) is asyncio.TimerHandle and handle._loop is self.loop
              and cancel == handle.cancel and not handle.cancelled()
              and handle._callback is self.event._run_async_call_action
              and type(handle._args) is tuple and len(handle._args) == 2
              and handle._args[0] is self.hass and math.isfinite(handle.when()), 'actual_timer_handle')
        job = handle._args[1]
        _need(type(job) is self._job_cls and job.job_type is self._callback_kind,
              'actual_timer_job')
        action = job.target
        _need(getattr(action, '__code__', None) == self._timer_code
              and type(action.__closure__) is tuple, 'actual_timer_action')
        cells = dict(zip(action.__code__.co_freevars, (c.cell_contents for c in action.__closure__), strict=True))
        _need(set(cells) == {'self', 'source_state', 'source_state_dec'}
              and cells['self'] is entity and type(cells['source_state']) is self._state_cls
              and cells['source_state'].entity_id in self.source_names
              and type(cells['source_state_dec']) is Decimal
              and cells['source_state_dec'].is_finite()
              and cells['source_state_dec'] == Decimal(cells['source_state'].state),
              'actual_timer_closure')
        return handle, action, cells['source_state']

    def _snapshot(self, entity):
        handle, _, endpoint = self._timer(entity)
        clock = entity._last_integration_time
        _need(type(clock) is datetime and clock.tzinfo is not None
              and type(entity._last_integration_trigger) is
                  import_module('homeassistant.components.integration.sensor')._IntegrationTrigger,
              'actual_clock_shape')
        return IntegrationClockSnapshot(_decimal(entity._state), _decimal(entity._last_valid_state),
            clock.isoformat(), entity._last_integration_trigger.name, handle.when(),
            endpoint.state, endpoint.last_reported.isoformat())

    def _require_job_order(self, entity, *, bridges):
        for kind, key in (('changed', self.event._TRACK_STATE_CHANGE_DATA),
                          ('reported', self.event._TRACK_STATE_REPORT_DATA)):
            tracker = self.hass.data.get(key)
            _need(tracker is not None, 'tracker_missing')
            targets = [job.target for job in tracker.callbacks.get(self.source, ())]
            native = getattr(entity, '_integrate_on_state_' + ('change' if kind == 'changed' else 'report')
                             + '_with_max_sub_interval')
            _need(targets.count(native) == 1, 'native_listener_count')
            if bridges:
                bridge = self._bridges[kind]
                _need(targets.count(bridge) == 1 and targets.index(native) < targets.index(bridge),
                      'bridge_must_follow_native')
            else:
                _need(self._bridges[kind] not in targets
                      and all(getattr(target, '__self__', None) is not self.old for target in targets),
                      'old_consumer_left')

    def _install_monitor(self):
        monitor = sys.monitoring
        available = [key for key in range(6) if monitor.get_tool(key) is None]
        _need(bool(available), 'no_monitor_tool')
        key = available[0]
        monitor.use_tool_id(key, _NAME)
        self._tool = key
        self._watched = tuple(self.codes[name] for name in
                              ('remove_callbacks', 'schedule', 'changed', 'reported', 'fire'))
        def started(code, offset):
            self._observe(sys._getframe(1), 'start')
        def returned(code, offset, result):
            self._observe(sys._getframe(1), 'return')
        self._monitor_callbacks = (started, returned)
        monitor.register_callback(key, monitor.events.PY_START, started)
        monitor.register_callback(key, monitor.events.PY_RETURN, returned)
        for code in self._watched:
            monitor.set_local_events(key, code, monitor.events.PY_START | monitor.events.PY_RETURN)

    def _observe(self, frame, phase):
        try:
            entity = frame.f_locals.get('self')
            if frame.f_code is self.codes['fire']:
                if self.source_binding is not None and phase == 'return':
                    self.source_binding.capture_native_event(self, frame)
                return
            if frame.f_code in (self.codes['changed'], self.codes['reported']):
                if entity is self.old and not self._bridge_depth:
                    event_object = frame.f_locals.get('event')
                    _need(event_object is not None, 'callback_event_shape')
                    self._validate_emission(event_object,
                        'changed' if frame.f_code is self.codes['changed'] else 'reported')
                    if phase == 'start':
                        _need(id(event_object) not in self._consumed, 'duplicate_old_consumer')
                        self._consumed[id(event_object)] = (event_object, False)
                    else:
                        _need(self._consumed.get(id(event_object)) == (event_object, False),
                              'old_consumer_return')
                        self._consumed[id(event_object)] = (event_object, True)
                        self._native_old_calls += 1
                elif entity is self._new:
                    event_object = frame.f_locals.get('event')
                    self._validate_emission(event_object,
                        'changed' if frame.f_code is self.codes['changed'] else 'reported')
                    if phase == 'return':
                        self._emitted.pop(id(event_object.data))
                return
            if frame.f_code is self.codes['remove_callbacks'] and entity is self.old:
                _need(self._phase == 'unloading', 'foreign_old_removal')
                if phase == 'start':
                    self._departing_timer = self._timer(self.old)
                else:
                    self._check()
                    handle, action, _ = self._departing_timer
                    _need(handle.cancelled() and not self.old._on_remove, 'native_cancel_required')
                    self.old._max_sub_interval_exceeded_callback = self.event.async_call_at(
                        self.hass, action, handle.when())
                    self._retained = True
                return
            if frame.f_code is self.codes['schedule'] and entity is not self.old:
                parent = frame.f_back
                if parent is None or parent.f_code is not self.codes['added']:
                    return  # Later ordinary native schedule, not a setup seam.
                _need(self._phase == 'setting_up' and type(entity) is self.cls
                      and entity.unique_id == self.entry.entry_id, 'new_setup_binding')
                if phase == 'start':
                    self._transfer(entity)
                else:
                    _need(entity is self._new and not self._new_timer_bound, 'one_new_timer')
                    handle, action, endpoint = self._timer(entity)
                    _need(endpoint is self.hass.states.get(self.source), 'new_endpoint_head')
                    handle.cancel()
                    entity._max_sub_interval_exceeded_callback = self.event.async_call_at(
                        self.hass, action, self._transferred.deadline)
                    self._new_timer_bound = True
                    self._timer_rearms += 1
        except BaseException:
            self._poison('monitor_callback_refused')

    def _emission(self, frame):
        if self.source_binding is not None:
            return self.source_binding.emission(self, frame)
        data, parent = frame.f_locals.get('event_data'), frame.f_back
        kind = frame.f_locals.get('event_type')
        if kind not in ('state_changed', 'state_reported') or type(data) is not dict or data.get('entity_id') != self.source:
            return
        self._check()
        _need(parent is not None and parent.f_code is self.codes['source_set']
              and parent.f_locals.get('self') is self.hass.states, 'actual_source_emitter')
        if kind == 'state_changed':
            _need(parent.f_locals.get('state_changed_data') is data
                  and data.get('new_state') is parent.f_locals.get('state'), 'actual_changed_data')
        else:
            _need(data.get('new_state') is parent.f_locals.get('old_state')
                  and data.get('old_last_reported') == parent.f_locals.get('old_last_reported'),
                  'actual_report_data')
        _need(id(data) not in self._emitted, 'duplicate_emission_data')
        state = data.get('new_state')
        _need(state is not None and state is self.hass.states.get(self.source)
              and Decimal(state.state).is_finite(), 'finite_source_head')
        old_state = data.get('old_state')
        self._emitted[id(data)] = (
            data, kind, old_state, state, self._state_values(old_state),
            self._state_values(state), data.get('old_last_reported'),
            data.get('last_reported'), tuple(sorted(data)))

    def _state_values(self, state):
        if state is None:
            return None
        _need(type(state) is self._state_cls, 'actual_event_state')
        return (state.entity_id, state.state, state.last_changed, state.last_updated,
                state.last_reported, state.attributes.get('unit_of_measurement'),
                state.attributes.get('device_class'))

    def _validate_emission(self, event_object, kind):
        if self.source_binding is not None:
            return self.source_binding.validate_emission(self,event_object,kind)
        data = event_object.data
        item = self._emitted.get(id(data))
        _need(item is not None and item[0] is data
              and item[1] == event_object.event_type == 'state_' + kind
              and item[2] is data.get('old_state') and item[3] is data.get('new_state')
              and item[4] == self._state_values(item[2])
              and item[5] == self._state_values(item[3])
              and item[6] == data.get('old_last_reported')
              and item[7] == data.get('last_reported')
              and item[8] == tuple(sorted(data)),
              'emission_delivery_or_mutable_input')

    def _bridge(self, event_object, kind):
        if self.source_binding is not None:
            return self.source_binding.bridge(self, event_object, kind)
        try:
            self._check()
            data = event_object.data
            self._validate_emission(event_object, kind)
            consumed = self._consumed.pop(id(event_object), None)
            if consumed is not None:
                _need(consumed == (event_object, True), 'native_consumer_incomplete')
                self._emitted.pop(id(data))
                return
            _need(self._retained and self._phase in ('unloading', 'retained', 'setting_up')
                  and not self._bridge_depth, 'one_retained_consumer')
            self._bridge_depth += 1
            try:
                callback = getattr(self.old, '_integrate_on_state_' +
                    ('change' if kind == 'changed' else 'report') + '_with_max_sub_interval')
                callback(event_object)  # SAME real Event, real native arithmetic.
                self._validate_emission(event_object, kind)
                self._emitted.pop(id(data))
                self._bridge_calls += 1
            finally:
                self._bridge_depth -= 1
            self._timer(self.old)
        except BaseException:
            self._poison('source_bridge_refused')

    def _transfer(self, entity):
        from homeassistant.helpers.entity import EntityPlatformState
        self._check()
        if self.source_binding is not None:
            self.source_binding.require_accepted(self, seam=True)
        from .native_integration_own_clock import IntegrationOwnClockBinding
        target_row = (self.source_binding.transfer_row(self)
                      if type(self.source_binding) is IntegrationOwnClockBinding else self.row)
        _need(self._new is None and self._retained and not self._bridge_depth
              and not self._emitted and not self._consumed and self._pending_move is None
              and self.old._platform_state is EntityPlatformState.REMOVED
              and entity.entity_id == target_row.entity_id and entity.registry_entry is target_row
              and entity.platform.config_entry is self.entry and entity.hass is self.hass
              and entity._sensor_source_id == self.source and entity._source_entity == self.source
              and entity._max_sub_interval == self.old._max_sub_interval, 'atomic_setup_seam')
        frame = sys._getframe(1)
        try:
            while frame is not None:
                _need(frame.f_code is not self.codes['fire'], 'source_dispatch_at_transfer')
                frame = frame.f_back
        finally:
            del frame
        handle, _, endpoint = self._timer(self.old)
        _need(endpoint is self.hass.states.get(self.source), 'retained_endpoint_head')
        snapshot = self._snapshot(self.old)
        for unsubscribe in tuple(self._unsubscribes):
            unsubscribe()
            self._unsubscribes.remove(unsubscribe)
        handle.cancel()
        self._old_cancelled = handle
        # Causal actual-data migration, never arithmetic/calibration/fixture seed.
        entity._state = self.old._state
        entity._last_valid_state = self.old._last_valid_state
        entity._last_integration_time = self.old._last_integration_time
        entity._last_integration_trigger = self.old._last_integration_trigger
        entity._unit_of_measurement = self.old._unit_of_measurement
        self._new, self._transferred, self._retained = entity, snapshot, False
        self._transfer_readback = {
            'full': _decimal(entity._state), 'last_valid': _decimal(entity._last_valid_state),
            'time': entity._last_integration_time.isoformat(),
            'trigger': entity._last_integration_trigger.name,
        }
        _need(self._transfer_readback == {k: asdict(snapshot)[k] for k in self._transfer_readback},
              'actual_transfer_readback')

    async def _save(self, payload):
        payload, digest = canonical(payload)
        key = 'rr-' + digest
        reference = await self.store.create(key, payload)
        self._check(owner=True)
        _need(self.store.read(key) == {'digest': digest, 'payload': payload}, 'receipt_readback')
        return reference

    async def unload(self, *, own_audit=None):
        self._check(owner=True)
        _need(self._phase == 'capture', 'one_unload')
        self._phase = 'unloading'
        if own_audit is None:
            result = await self.lease.async_unload(self.entry.entry_id)
        else:
            from .native_integration_unload_audit import NativeIntegrationUnloadAudit
            from .native_integration_own_clock import IntegrationOwnClockBinding
            _need(type(own_audit) is NativeIntegrationUnloadAudit
                  and own_audit.clock is self
                  and type(self.source_binding) is IntegrationOwnClockBinding, 'own_unload_audit')
            result = await own_audit.async_unload(self.lease)
            self._own_unload_witness = result
        self._check(owner=True)
        _need((result is True if own_audit is None else result is own_audit._witness)
              and self._retained and not self.old._on_remove
              and self.entry.state.value == 'not_loaded', 'actual_unload')
        self._phase = 'retained'
        return self._snapshot(self.old)

    async def setup(self):
        self._check(owner=True)
        _need(self._phase == 'retained', 'one_setup')
        if self.source_binding is not None:
            self.source_binding.require_accepted(self)
        self._phase = 'setting_up'
        result = await self.lease.async_setup(self.entry.entry_id)
        self._check(owner=True)
        _need(result is True and self._new is not None and self._new_timer_bound
              and self._old_cancelled.cancelled() and self.entry.state.value == 'loaded',
              'actual_setup_transfer')
        self._require_job_order(self._new, bridges=False)
        self._timer(self._new)
        receipt = {'kind': 'integration_clock_receipt', 'schema': 1,
                   'intent': list(self.intent), 'entry_id': self.entry.entry_id,
                   'actual_transfer': asdict(self._transferred),
                   'transfer_readback': self._transfer_readback,
                   'native_old_calls': self._native_old_calls, 'bridge_calls': self._bridge_calls,
                   'new_timer_rearms': self._timer_rearms, 'old_timer_cancelled': True}
        if self.source_binding is not None:
            receipt['source_binding'] = self.source_binding.description(accepted=True)
        self.receipt = await self._save(receipt)
        self._phase = 'loaded'
        self._check(owner=True)
        return self._new

    def close(self):
        _need(asyncio.current_task() is self.task, 'owner_cleanup')
        self._live = False
        first_error = None
        def attempt(action):
            nonlocal first_error
            try:
                action()
                return True
            except BaseException as error:
                self._poison('cleanup_failed')
                if first_error is None:
                    first_error = error
                return False
        # Each resource category is independent. A failed native unsubscribe
        # cannot leave the retained timer or either monitoring tool alive.
        while self._unsubscribes:
            unsubscribe = self._unsubscribes.pop(0)
            attempt(unsubscribe)
        if self._retained:
            if attempt(self.old._max_sub_interval_exceeded_callback):
                self._retained = False
        if self._emissions is not None:
            if attempt(self._emissions.close):
                self._emissions = None
        monitor = sys.monitoring
        if self._tool is not None and monitor.get_tool(self._tool) == _NAME:
            key = self._tool
            for code in self._watched:
                attempt(lambda code=code: monitor.set_local_events(key, code, 0))
            attempt(lambda: monitor.register_callback(key, monitor.events.PY_START, None))
            attempt(lambda: monitor.register_callback(key, monitor.events.PY_RETURN, None))
            if attempt(lambda: monitor.free_tool_id(key)):
                self._tool = None
        self._consumed.clear()
        self._emitted.clear()
        if first_error is not None:
            raise first_error
