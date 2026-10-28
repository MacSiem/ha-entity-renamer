"""Two direct profile W→Energy observational clocks in the existing reload.

Actual retained update-return UTC is an operand boundary, never a W State
emission timestamp. One real native arithmetic engine and an explicitly typed
external timer per pair. Numeric-first setup/source-ID/group/reverse unsupported.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
import math
import sys

from .native_unload_code_proof import unchanged
from .native_utility_setup_audit import _code_objects
from .powercalc_source_binding import _json, _dispatch_token


def _need(ok, reason):
    if not ok:
        raise ValueError('powercalc_operand_clock_' + reason) from None


def _utc():
    return datetime.now(timezone.utc)


def _numeric(state):
    from homeassistant.core import State
    if type(state) is not State:
        return None
    try:
        value = Decimal(state.state)
    except Exception:
        return None
    return value if value.is_finite() else None


@dataclass
class _Clock:
    old: object
    power: object
    engine: object
    phase: str = 'native_old'
    value: Decimal | None = None
    endpoint: object = None
    anchor: object = None
    trigger: object = None
    deadline: float | None = None
    handle: object = None
    issued_handle: object = None
    action: object = None
    job: object = None
    first_base: bool = False
    transport: object = None
    transport_before: object = None
    cutting: object = None
    candidate: object = None
    running: bool = False
    neutralizing: bool = False
    unexpected_integral: bool = False
    native_initial: object = None
    source_attributes: tuple = ()


class _OperandTimerAction:
    _hass_callback = True
    def __init__(self, owner, name):
        self.owner, self.name = owner, name

    def __call__(self, now):
        try:
            self.owner.timer(self, now)
        except BaseException as error:
            self.owner.poison(error)


@dataclass(frozen=True)
class PowercalcOperandClockReceipt:
    evidence_sha256: str
    original_runtime_token: bytes
    chain_head: str
    source_hashes_json: bytes
    scope_bindings_json: bytes
    steps_json: bytes
    timer_proofs_json: bytes
    direct_operand_clock_verified: bool = field(default=True, init=False)
    continuity_verified: bool = field(default=False, init=False)
    counter_ownership_verified: bool = field(default=False, init=False)
    reload_ownership_verified: bool = field(default=False, init=False)
    migration_ready: bool = field(default=False, init=False)
    family_complete: bool = field(default=False, init=False)
    status: str = field(default='UNKNOWN', init=False)


@dataclass(frozen=True)
class PowercalcOperandClockReloadWitness:
    lifecycle: object
    global_rename: object
    exact_fields: object
    retained_producers: object
    operand_clock: PowercalcOperandClockReceipt
    continuity_verified: bool = field(default=False, init=False)
    counter_ownership_verified: bool = field(default=False, init=False)
    reload_ownership_verified: bool = field(default=False, init=False)
    migration_ready: bool = field(default=False, init=False)
    family_complete: bool = field(default=False, init=False)
    status: str = field(default='UNKNOWN', init=False)


class _OperandClockOwner:
    """Finite typed component; no constructor accepts a numeric counter."""
    def __init__(self, scope):
        from homeassistant.core import EventBus, StateMachine, HomeAssistant
        from homeassistant.helpers import event
        from homeassistant.helpers.entity import Entity
        from homeassistant.helpers.entity_registry import RegistryEntry
        _need(scope.producer_owner is not None and scope.exact_owner is not None, 'typed_components_required')
        self.scope, self.event = scope, event
        self.integration = scope.vendor.integration
        self.cls = scope.vendor.classes['VirtualEnergySensor']
        self.states = {name: _Clock(e, scope.producer_owner.old[e._sensor_source_id], e)
            for name, e in scope.old.items() if type(e) is self.cls
            and e._sensor_source_id in scope.producer_owner.old}
        _need(len(self.states) == 2 and all(type(c.old._method) is self.integration._Left
            and not c.old._filter_outliers for c in self.states.values()), 'two_direct_left_engines')
        self.by_power = {c.power.entity_id: name for name, c in self.states.items()}
        self.hrefs = ((Entity, '_call_on_remove_callbacks'), (Entity, '_Entity__async_remove_impl'),
            (RegistryEntry, 'write_unavailable_state'), (StateMachine, 'async_set_internal'),
            (EventBus, 'async_fire_internal'), (EventBus, '_async_dispatch'),
            (HomeAssistant, 'async_run_hass_job'), (Entity, '_async_write_ha_state')) + tuple(
            (self.integration.IntegrationSensor, method) for method in (
                '_integrate_on_state_change_with_max_sub_interval',
                '_integrate_on_state_report_with_max_sub_interval', '_update_integral',
                '_schedule_max_sub_interval_exceeded_if_state_is_numeric', 'async_added_to_hass')) + (
            (self.integration._Left, 'validate_states'),
            (self.integration._Left, 'calculate_area_with_two_states'),
            (self.integration._IntegrationMethod, 'calculate_area_with_one_state')) + tuple(
            (event, name) for name in ('async_call_at', '_run_async_call_action',
                                      '_async_dispatch_entity_id_event')) + (
            (Entity, 'add_to_platform_finish'),)
        self.bound = None
        self.steps, self.timer_proofs = [], []
        self.numeric, self.seen_results, self.kept = {}, set(), {}
        self.setup_nonnumeric = {}
        self.depth = 0
        self.timer_action_code = _OperandTimerAction.__call__.__code__
        self.error = None
        # Issuance ledger is independent of mutable per-entity runtime fields.
        # Every value is a fixed identity tuple from our own native _arm call.
        self._issued_timers = {}
        self.subtract = None

    def bind_codes(self):
        scope = self.scope
        self.hm = tuple(scope.ha_methods[scope.ha_refs.index(ref)] for ref in self.hrefs)
        self.hc = tuple(scope.ha_codes[scope.ha_refs.index(ref)] for ref in self.hrefs)
        self.codes = {ref: code for ref, code in zip(self.hrefs, self.hc, strict=True)}
        self.native_timer_codes = tuple(code for code in _code_objects(self.hc[11])
            if code.co_name == '_integrate_on_max_sub_interval_exceeded_callback')
        _need(len(self.native_timer_codes) == 1, 'native_timer_code')
        return tuple(self.hc[index] for index in (0, 4, 8, 9, 10, 11))

    def bind_original(self, bound):
        self.bound = bound
        rows = {row.entity_id: row for row in bound.rows}
        # Integral rows encode producer ownership as graph edges; scalar source
        # fields belong to the physical-input profile rows, not this relation.
        _need(all(rows[name].source_verified and rows[name].relation_kind == 'energy_integral'
                  and rows[name].edges == ((c.power.entity_id, name, 1),)
                  and rows[name].registry_id == c.old.registry_entry.id
                  and rows[c.power.entity_id].source_verified
                  and rows[c.power.entity_id].relation_kind == 'profile_lut'
                  and rows[c.power.entity_id].registry_id == c.power.registry_entry.id
                  for name, c in self.states.items()),
              'original_direct_source_graph')
        self.identities = {name: (c.old.registry_entry, c.old.unique_id,
            c.old.platform.config_entry, c.old._sensor_source_id, c.old._source_entity,
            c.old._max_sub_interval, c.old._unit_prefix, c.old._unit_time)
            for name, c in self.states.items()}
        from .powercalc_subtract_clock import _SubtractClockOwner
        self.subtract = _SubtractClockOwner(self, bound)
        self.check()

    def check(self):
        _need(self.error is None and self.scope.error is None, 'poisoned')
        self.scope.producer_owner.check()
        if self.subtract is not None:
            self.subtract.check()
        unchanged(self.hrefs, self.hm, self.hc)
        _need(asyncio.get_running_loop() is self.scope.loop, 'same_loop')
        if self.bound is None:
            return
        for name, c in self.states.items():
            row, uid, entry, sid, source, interval, prefix, unit = self.identities[name]
            e = c.engine
            from .powercalc_exact_fields import _values
            _values(e, self.integration)
            _need(type(e) is self.cls and e.hass is self.scope.hass and e.entity_id == name
                  and e.registry_entry is row and e.unique_id == uid and e.platform.config_entry is entry
                  and e._sensor_source_id == sid and e._source_entity == source
                  and e._max_sub_interval == interval and e._unit_prefix == prefix and e._unit_time == unit
                  and type(e._method) is self.integration._Left and not e._filter_outliers,
                  'engine_identity_or_semantics_changed')
            _dispatch_token(e, self.cls, ('_integrate_on_state_change',
                '_schedule_max_sub_interval_exceeded_if_state_is_numeric'))
            _dispatch_token(e, self.integration.IntegrationSensor, ('_update_integral',))
            _dispatch_token(e._method, self.integration._Left,
                            ('validate_states', 'calculate_area_with_two_states',
                             'calculate_area_with_one_state'))
            if c.handle is not None:
                self._typed_timer_proof(name)

    def poison(self, error):
        if self.error is None:
            self.error = (str(error) if type(error) is ValueError
                          and str(error).startswith(('powercalc_operand_clock_', 'powercalc_binding_',
                              'native_unload_', 'powercalc_retained_producer_', 'powercalc_exact_fields_'))
                          else type(error).__name__)
        if self.scope.error is None:
            self.scope.error = 'operand_clock_refused'

    def _ancestors(self, frame, code):
        current = frame.f_back
        try:
            while current is not None:
                if current.f_code is code:
                    yield current
                current = current.f_back
        finally:
            del current

    def _native_timer(self, entity):
        from homeassistant.core import HassJob, HassJobType, State
        callback = entity._max_sub_interval_exceeded_callback
        handle = getattr(callback, '__self__', None)
        _need(type(handle) is asyncio.TimerHandle and handle._loop is self.scope.loop
              and callback == handle.cancel and not handle.cancelled()
              and handle._callback is self.event._run_async_call_action
              and handle._args[0] is self.scope.hass and math.isfinite(handle.when()), 'native_timer_shape')
        job = handle._args[1]
        _need(type(job) is HassJob and job.job_type is HassJobType.Callback
              and job.target.__code__ is self.native_timer_codes[0], 'native_timer_action')
        action = job.target
        cells = dict(zip(action.__code__.co_freevars,
                         (cell.cell_contents for cell in action.__closure__), strict=True))
        state = cells.get('source_state')
        _need(cells.get('self') is entity and type(state) is State
              and state.entity_id == entity._sensor_source_id and _numeric(state) is not None
              and cells.get('source_state_dec') == _numeric(state), 'native_timer_actual_endpoint')
        return handle, action, state

    def _capture(self, name):
        c = self.states[name]
        if c.phase != 'native_old':
            return
        self.check()
        from .powercalc_profile_binding import _energy_listeners
        _energy_listeners(self.scope.hass, c.old, c.old._sensor_source_id, self.scope.vendor)
        handle, action, state = self._native_timer(c.old)
        c.native_initial = (handle, action, state)
        _need(type(c.old._state) is Decimal and c.old._state.is_finite()
              and type(c.old._last_valid_state) is Decimal and c.old._last_valid_state.is_finite(),
              'actual_finite_engine')
        c.value, c.endpoint = _numeric(state), state.last_reported
        c.source_attributes = tuple(state.attributes.get(k) for k in
            ("unit_of_measurement", "device_class", "state_class"))
        c.anchor, c.trigger = c.old._last_integration_time, c.old._last_integration_trigger
        c.deadline, c.phase = handle.when(), 'retained'
        self.kept[id(state)] = state
        self.steps.append({'kind': 'capture', 'entity_id': name, 'engine': id(c.engine),
            'full': str(c.engine._state), 'anchor': c.anchor.isoformat(), 'trigger': c.trigger.name,
            'endpoint': c.endpoint.isoformat(), 'power': str(c.value), 'deadline': c.deadline})

    def _typed_timer_proof(self, name):
        from homeassistant.core import HassJob, HassJobType
        c = self.states[name]
        handle, action, job = c.handle, c.action, c.job
        issued = self._issued_timers.get(name)
        _need(type(issued) is tuple and len(issued) == 3
              and issued[0] is handle and issued[1] is job and issued[2] is action,
              'typed_timer_ownership_changed')
        _need(type(handle) is asyncio.TimerHandle and c.issued_handle is handle
              and handle._loop is self.scope.loop
              and not handle.cancelled() and handle._callback is self.event._run_async_call_action
              and handle._args == (self.scope.hass, job) and handle.when() == c.deadline
              and math.isfinite(c.deadline) and type(job) is HassJob
              and job.job_type is HassJobType.Callback and job.target is action
              and type(action) is _OperandTimerAction and action.owner is self and action.name == name
              and type(action).__call__.__code__ is self.timer_action_code
              and getattr(action, "_hass_callback", None) is True
              and handle._context.get(self.scope.causal, None) is self.scope.marker,
              'typed_timer_ownership_changed')
        return {'kind': 'typed_operand_timer', 'entity_id': name, 'handle': id(handle),
                'action': id(action), 'job': id(job), 'deadline': handle.when(),
                'engine': id(c.engine), 'power': str(c.value), 'anchor': c.anchor.isoformat()}

    def _cancel(self, name):
        c = self.states[name]
        issued = self._issued_timers.get(name)
        if c.handle is None and issued is not None:
            # A mutable row can lose its handle without relinquishing the
            # independently issued callback. Quiesce that precise issuance
            # before refusing re-arm; never cancel a rebound foreign handle.
            handle, job, action = issued
            args = getattr(handle, '_args', None)
            _need(type(handle) is asyncio.TimerHandle and handle._loop is self.scope.loop
                  and handle._callback is self.event._run_async_call_action
                  and type(args) is tuple and len(args) == 2
                  and args[0] is self.scope.hass and args[1] is job
                  and type(action) is _OperandTimerAction
                  and action.owner is self and action.name == name
                  and getattr(job, 'target', None) is action,
                  'typed_timer_ownership_changed')
            if not handle.cancelled():
                handle.cancel()
            _need(handle.cancelled(), 'typed_timer_cancel_failed')
            self._issued_timers.pop(name, None)
            if c.issued_handle is handle:
                c.issued_handle = None
            if c.action is action:
                c.action = None
            if c.job is job:
                c.job = None
            _need(False, 'typed_timer_ownership_changed')
        if c.handle is None:
            _need(issued is None and c.issued_handle is None
                  and c.action is None and c.job is None,
                  'typed_timer_ownership_changed')
        else:
            _need(issued is not None and issued[0] is c.handle
                  and issued[1] is c.job and issued[2] is c.action,
                  'typed_timer_ownership_changed')
            proof = self._typed_timer_proof(name)
            c.handle.cancel()
            _need(c.handle.cancelled(), 'typed_timer_cancel_failed')
            proof['cancelled'] = True
            self.timer_proofs.append(proof)
            self._issued_timers.pop(name, None)
            c.handle = c.action = c.job = c.issued_handle = None

    def _arm(self, name, deadline):
        c = self.states[name]
        self._cancel(name)
        c.deadline = deadline
        action = _OperandTimerAction(self, name)
        token = self.scope.causal.set(self.scope.marker)
        try:
            cancel = self.event.async_call_at(self.scope.hass, action, deadline)
        finally:
            self.scope.causal.reset(token)
        c.handle, c.action = getattr(cancel, '__self__', None), action
        _need(type(c.handle) is asyncio.TimerHandle and cancel == c.handle.cancel, 'actual_native_call_at_return')
        c.job = c.handle._args[1]
        c.issued_handle = c.handle  # independently retained issuance identity
        self._issued_timers[name] = (c.handle, c.job, action)
        self.timer_proofs.append(self._typed_timer_proof(name))

    def _area(self, name, end, *, kind, next_value=None):
        c = self.states[name]
        e = c.engine
        origin = (c.anchor if kind == 'timer' or c.trigger is self.integration._IntegrationTrigger.TimeElapsed
                  else c.endpoint)
        # Refuse stale, naive or reversed boundaries BEFORE the native integral
        # writer. Normalize to UTC: same-zone fold datetimes compare wall times
        # under Python's datetime rules, silently losing DST elapsed seconds.
        _need(type(origin) is datetime and type(end) is datetime
              and origin.tzinfo is not None and end.tzinfo is not None
              and origin.utcoffset() is not None and end.utcoffset() is not None,
              'nonmonotonic_integral_interval')
        start_utc, end_utc = origin.astimezone(timezone.utc), end.astimezone(timezone.utc)
        _need(end_utc >= start_utc, 'nonmonotonic_integral_interval')
        elapsed = Decimal((end_utc - start_utc).total_seconds())
        old, power_before = e._state, c.value
        self.depth += 1
        try:
            if kind == 'timer':
                area = e._method.calculate_area_with_one_state(elapsed, c.value)
            else:
                operands = e._method.validate_states(str(c.value), str(next_value))
                _need(operands is not None, 'actual_numeric_operands')
                area = e._method.calculate_area_with_two_states(elapsed, *operands)
            e._update_integral(area)  # The only arithmetic writer; no desired total.
        finally:
            self.depth -= 1
        anchor = _utc()
        c.anchor = e._last_integration_time = anchor
        c.trigger = e._last_integration_trigger = (self.integration._IntegrationTrigger.TimeElapsed
            if kind == 'timer' else self.integration._IntegrationTrigger.StateEvent)
        if kind != 'timer':
            c.endpoint, c.value = end, next_value
        self.steps.append({'kind': kind, 'entity_id': name, 'engine': id(e), 'origin': origin.isoformat(),
            'endpoint': end.isoformat(), 'bookkeeping_anchor': anchor.isoformat(),
            'elapsed': str(elapsed), 'power_before': str(power_before), 'power_after': str(c.value),
            'full_before': str(old),
            'full_after': str(e._state), 'deadline': c.deadline})

    def timer(self, action, now):
        self.check()
        name, c = action.name, self.states[action.name]
        _need(c.action is action and c.phase == 'retained' and not c.running
              and type(now) is datetime and now.tzinfo is not None, 'actual_owned_timer_delivery')
        frames = list(self._ancestors(sys._getframe(), self.hc[17]))
        _need(any(frame.f_locals.get('hass') is self.scope.hass
                  and frame.f_locals.get('job') is c.job for frame in frames), 'native_timer_dispatch')
        c.running = True
        try:
            self._area(name, now, kind='timer')
            self.steps[-1]['fired_loop_time'] = self.scope.loop.time()
            step = self.steps[-1]
            self._arm(name, self.scope.loop.time() + c.engine._max_sub_interval.total_seconds())
            step['deadline_after'] = c.deadline
        finally:
            c.running = False

    def consume(self, result):
        from .powercalc_retained_producer import PowercalcActualCalculation
        _need(type(result) is PowercalcActualCalculation, 'typed_actual_result')
        name = self.by_power.get(result.producer.entity_id)
        _need(name is not None and result.producer is self.states[name].power
              and result is self.scope.producer_owner.results[-1] and id(result) not in self.seen_results,
              'same_live_result_stream')
        self.check()
        c = self.states[name]
        _need(c.phase == 'retained' and result.available
              and result.previous_native_power == c.value, 'operand_predecessor')
        self.seen_results.add(id(result))
        if not result.native_value_changed:
            self.steps.append({'kind': 'unchanged', 'entity_id': name,
                               'result': id(result), 'deadline': c.deadline,
                               'deadline_before': c.deadline, 'deadline_after': c.deadline})
            return
        _need(result.native_write_suppressed, 'actual_removed_update_boundary')
        self._area(name, result.update_returned_at, kind='operand', next_value=result.native_power)
        self.steps[-1]['result'] = id(result)
        step = self.steps[-1]
        self._arm(name, self.scope.loop.time() + c.engine._max_sub_interval.total_seconds())
        step['deadline_after'] = c.deadline

    def after_exact_schedule(self, entity):
        if self.subtract is not None:
            self.subtract.after_exact_schedule(entity)

    def after_exact_transfer(self, entity):
        if self.subtract is not None:
            self.subtract.after_exact_transfer(entity)
        c = self.states.get(entity.entity_id)
        if c is None:
            return
        _need(c.phase == 'retained' and c.engine is c.old and entity is not c.old
              and entity._state is c.old._state and entity._last_valid_state is c.old._last_valid_state
              and entity._last_integration_time is c.anchor and entity._last_integration_trigger is c.trigger,
              'atomic_actual_engine_pointer')
        c.engine = entity
        self.steps.append({'kind': 'engine_transfer', 'entity_id': entity.entity_id,
                           'old': id(c.old), 'new': id(entity), 'deadline': c.deadline})

    def observe(self, frame, phase):
        if self.subtract is not None:
            self.subtract.observe(frame, phase)
        code, obj = frame.f_code, frame.f_locals.get('self')
        if code is self.hc[4] and phase == 'start':
            self._emission(frame)
            return
        name = getattr(obj, 'entity_id', None)
        c = self.states.get(name)
        if c is None or type(obj) is not self.cls:
            return
        if code is self.hc[0]:
            if obj is c.old and phase == 'start':
                self._capture(name)
            elif obj is c.old and phase == 'return' and c.phase == 'retained' and c.handle is None:
                self._arm(name, c.deadline)
            return
        if code is self.hc[10]:
            if not self.depth and c.neutralizing:
                c.unexpected_integral = True
                _need(False, 'native_area_during_lifecycle_transport')
            _need(self.depth or c.phase != 'cutting', 'native_double_area_at_cutover')
            return
        if code is self.hc[11]:
            if obj is not c.old and phase == 'start':
                parent = frame.f_back
                setup = (parent is not None and parent.f_back is not None
                         and parent.f_back.f_code is self.hc[12])
                if setup:
                    _need(_numeric(frame.f_locals.get('source_state')) is None,
                          'numeric_first_setup_unsupported')
                    c.first_base = True
            if c.phase == 'cutting' and obj is c.engine and phase == 'return':
                handle, action, state = self._native_timer(obj)
                _need(state is c.cutting[1], 'native_handoff_endpoint')
                handle.cancel()
                obj._max_sub_interval_exceeded_callback = self.event.async_call_at(
                    self.scope.hass, action, c.deadline)
                actual, _, actual_state = self._native_timer(obj)
                _need(actual.when() == c.deadline and actual_state is state, 'native_deadline_handoff')
                c.candidate = actual
            return
        if code not in (self.hc[8], self.hc[9]):
            return
        event = frame.f_locals.get('event')
        setup_data = self.setup_nonnumeric.get(id(event.data))
        if ((c.transport is not None and event.data is c.transport and obj is c.old)
                or (setup_data is not None and setup_data is event.data and obj is c.engine)):
            if phase == 'start':
                c.transport_before = (obj._state, obj._last_valid_state)
                c.neutralizing, c.unexpected_integral = True, False
            else:
                _need(not c.unexpected_integral and c.neutralizing
                      and (obj._state, obj._last_valid_state) == c.transport_before,
                      'transport_native_area_unexpected')
                obj._last_integration_time, obj._last_integration_trigger = c.anchor, c.trigger
                c.transport, c.neutralizing = None, False
                self.steps.append({'kind': 'transport_neutralized', 'entity_id': name,
                                   'anchor': c.anchor.isoformat(), 'trigger': c.trigger.name})
                if c.handle is None:
                    self._arm(name, c.deadline)
            return
        if c.phase == 'native':
            return
        if obj is c.engine and obj is not c.old:
            if phase == 'start':
                self._cutover(name, frame, event)
            else:
                _need(c.phase == 'cutting' and c.cutting[0] is event.data
                      and c.candidate is not None and not c.candidate.cancelled()
                      and obj._max_sub_interval_exceeded_callback == c.candidate.cancel,
                      'native_consumer_completion')
                c.anchor, c.trigger = obj._last_integration_time, obj._last_integration_trigger
                self.steps.append({'kind': 'native_handoff', 'entity_id': name,
                    'deadline': c.candidate.when(), 'anchor': c.anchor.isoformat(),
                    'native_handle': id(c.candidate), 'event_data': id(event.data),
                    'native_source_state': id(c.cutting[1]), 'task': id(asyncio.current_task()),
                    'full': str(obj._state), 'last_valid': str(obj._last_valid_state)})
                c.phase, c.cutting = 'native', None
        elif c.phase == 'retained':
            _need(False, 'unproven_old_native_consumption')

    def _emission(self, frame):
        if frame.f_locals.get('self') is not self.scope.hass.bus:
            return
        data, kind = frame.f_locals.get('event_data'), frame.f_locals.get('event_type')
        if type(data) is not dict or data.get('entity_id') not in self.by_power or kind not in (
                'state_changed', 'state_reported'):
            return
        name, parent = self.by_power[data['entity_id']], frame.f_back
        c = self.states[name]
        self.check()
        _need(parent is not None and parent.f_code is self.hc[3]
              and parent.f_locals.get('self') is self.scope.hass.states, 'native_W_emission')
        state = data.get('new_state')
        if _numeric(state) is None:
            new = self.scope.producer_owner.new.get(c.power.entity_id)
            writes = list(self._ancestors(frame, self.hc[7]))
            adds = list(self._ancestors(frame, self.hc[19]))
            if (new is not None and state is not None and state.state in ('unknown', 'unavailable')
                    and any(f.f_locals.get('self') is new for f in writes)
                    and any(f.f_locals.get('self') is new for f in adds)):
                _need(c.phase == 'retained' and new._power is None
                      and new._platform_state.name == 'ADDED'
                      and state is self.scope.hass.states.get(c.power.entity_id),
                      'actual_initial_nonnumeric_setup')
                self.setup_nonnumeric[id(data)] = data
                self.kept[id(data)] = data
                self.steps.append({'kind': 'initial_nonnumeric_setup', 'entity_id': name})
                return
            registries = list(self._ancestors(frame, self.hc[2]))
            _need(kind == 'state_changed' and state is not None and state.state == 'unavailable'
                  and state.attributes.get('restored') is True and len(registries) == 1
                  and registries[0].f_locals.get('self') is c.power.registry_entry
                  and registries[0].f_back is not None and registries[0].f_back.f_code is self.hc[1]
                  and registries[0].f_back.f_locals.get('self') is c.power
                  and c.power._platform_state.name == 'REMOVED' and c.transport is None
                  and c.phase in ('native_old', 'retained'), 'unproven_W_unavailable_transport')
            self._capture(name)
            c.transport = data
            self.kept[id(data)] = data
            return
        if c.phase == 'native_old':
            return  # Ordinary native arithmetic remains authoritative before departure.
        new = self.scope.producer_owner.new.get(c.power.entity_id)
        _need(new is not None and new is not c.power and new.registry_entry is c.power.registry_entry
              and new._platform_state.name == 'ADDED' and new.hass is self.scope.hass
              and new.platform.entities.get(new.entity_id) is new,
              'actual_new_W_owner')
        writes = list(self._ancestors(frame, self.hc[7]))
        _need(any(f.f_locals.get('self') is new for f in writes)
              and state is self.scope.hass.states.get(c.power.entity_id), 'actual_new_W_write')
        if c.phase == 'native':
            return
        updates = list(self._ancestors(frame,
            self.scope.vendor.classes['VirtualPowerSensor']._update_power_and_write_state.__code__))
        _need(any(f.f_locals.get('self') is new for f in updates)
              and new._power == _numeric(state) and new._attr_available is True,
              'actual_new_normalized_power_update')
        _need(tuple(state.attributes.get(k) for k in
                    ('unit_of_measurement', 'device_class', 'state_class')) == c.source_attributes,
              'actual_new_W_units_or_metadata_changed')
        _need(kind == 'state_changed' and c.first_base and c.phase == 'retained',
              'numeric_before_unknown_setup_or_overlap_unsupported')
        _need(parent.f_locals.get('state') is state and parent.f_locals.get('state_changed_data') is data,
              'actual_new_W_payload')
        _need(name not in self.numeric, 'one_first_numeric_emission')
        self.numeric[name] = (data, state, state.last_updated)
        self.kept[id(data)] = data

    def _consumers(self, e):
        from homeassistant.core import HassJob, HassJobType
        from homeassistant.helpers import event
        from collections import defaultdict
        changed = None
        for key, method in ((event._TRACK_STATE_CHANGE_DATA, '_integrate_on_state_change_with_max_sub_interval'),
                            (event._TRACK_STATE_REPORT_DATA, '_integrate_on_state_report_with_max_sub_interval')):
            tracker = self.scope.hass.data.get(key)
            _need(type(tracker) is event._KeyedEventData and type(tracker.callbacks) is defaultdict
                  and tracker.callbacks.default_factory is list, 'actual_native_consumer_tracker')
            actual = getattr(e, method)
            _need(getattr(actual, '__self__', None) is e
                  and getattr(actual, '__func__', None) is getattr(self.integration.IntegrationSensor, method),
                  'native_consumer_dispatch')
            jobs = [job for members in tracker.callbacks.values() for job in members
                    if getattr(job.target, '__self__', None) is e]
            _need(len(jobs) == 1 and type(jobs[0]) is HassJob
                  and jobs[0].job_type is HassJobType.Callback and jobs[0].target == actual
                  and jobs[0] in tracker.callbacks.get(e._sensor_source_id, ()), 'one_native_consumer')
            removers = [r for r in e._on_remove if type(r) is partial
                        and r.func is event._remove_listener and not r.keywords and len(r.args) == 5
                        and r.args[0] is self.scope.hass and tuple(r.args[2]) == (e._sensor_source_id,)
                        and r.args[3] is jobs[0] and r.args[4] is tracker.callbacks]
            _need(len(removers) == 1, 'native_consumer_remover')
            if key == event._TRACK_STATE_CHANGE_DATA:
                changed = (tracker, jobs[0])
        return changed

    def _cutover(self, name, frame, event):
        c = self.states[name]
        self.check()
        numeric = self.numeric.get(name)
        _need(c.phase == 'retained' and numeric is not None and numeric[0] is event.data
              and numeric[1] is event.data.get('new_state') and c.first_base
              and c.power.entity_id not in self.scope.producer_owner.active
              and self.scope.causal.get(None) is self.scope.marker
              and type(asyncio.current_task()) is asyncio.Task,
              'first_actual_numeric_consumer')
        tracker, job = self._consumers(c.engine)
        _need(event.event_type == "state_changed" and frame.f_code is self.hc[8],
              "actual_changed_consumer")
        keyed = list(self._ancestors(frame, self.hc[18]))
        _need(any(f.f_locals.get("hass") is self.scope.hass
                  and f.f_locals.get("callbacks") is tracker.callbacks
                  and f.f_locals.get("job") is job and f.f_locals.get("event") is event
                  for f in keyed), "actual_keyed_consumer_delivery")
        bus = list(self._ancestors(frame, self.hc[5]))
        _need(any(f.f_locals.get('event') is event and f.f_locals.get('event_data') is event.data
                  for f in bus), 'native_bus_consumer_delivery')
        old = event.data.get('old_state')
        _need(old is None or c.engine._method.validate_states(old.state, numeric[1].state) is None,
              'native_body_would_double_integrate')
        if c.transport is not None:
            _need(c.old._platform_state.name == 'REMOVED' and not c.old._on_remove
                  and name in self.scope.removed, 'unresolved_transport_consumer')
            self.steps.append({'kind': 'transport_after_energy_removal', 'entity_id': name,
                               'event_data': id(c.transport)})
            c.transport = None
        previous = c.value
        self._area(name, numeric[2], kind='resume', next_value=_numeric(numeric[1]))
        deadline = (self.scope.loop.time() + c.engine._max_sub_interval.total_seconds()
                    if previous != c.value else c.deadline)
        self._cancel(name)
        c.deadline = deadline
        c.phase, c.cutting = 'cutting', numeric

    def native_departure_snapshot(self, entity, handle):
        # Exact direct-pair exception to a CURRENT-listener proof: actual native
        # remove callbacks have already popped source listeners before cancel.
        c = self.states.get(entity.entity_id)
        if (c is None or entity is not c.old or c.native_initial is None
                or entity._platform_state.name != 'REMOVED' or handle is not c.native_initial[0]):
            return False
        current = sys._getframe().f_back
        found = False
        try:
            while current is not None:
                if current.f_code is self.hc[0] and current.f_locals.get('self') is entity:
                    found = True
                    break
                current = current.f_back
        finally:
            del current
        if not found:
            return False
        actual, action, state = self._native_timer(entity)
        _need(actual is handle and action is c.native_initial[1] and state is c.native_initial[2],
              'departing_native_timer_changed')
        return True

    def snapshot(self, entity):
        c = self.states.get(entity.entity_id)
        if c is None or c.handle is None:
            return None
        return self._typed_timer_proof(entity.entity_id)

    def finish(self, current):
        from .powercalc_profile_binding import _energy_listeners
        self.check()
        _need(self.subtract.pending_native is None and self.subtract.depth == 0,
              'subtract_incomplete_native_handoff')
        _need(all(c.phase == 'native' and c.handle is None and c.transport is None
                  and not c.neutralizing and current[name] is c.engine
                  for name, c in self.states.items()), 'incomplete_direct_clock_handoff')
        for c in self.states.values():
            _energy_listeners(self.scope.hass, c.engine, c.engine._sensor_source_id, self.scope.vendor)
        policy = self.scope.exact_owner.policy
        return PowercalcOperandClockReceipt(self.scope.evidence.sha256, self.scope.original.structural_token,
            policy.state.head, policy.source_hashes_json,
            _json({'root_id': policy.chain.root_id, 'operation_id': policy.chain.operation_id,
                   'direct_pairs': [[name, c.power.entity_id] for name, c in self.states.items()],
                   'subtract_quiet_handoff': {'phase': self.subtract.phase,
                       'steps': self.subtract.steps},
                   'time_policy': 'actual_update_return_utc_v1', 'ha_codes': [id(code) for code in self.hc]}),
            _json(self.steps), _json(self.timer_proofs))

    def annotate(self, error):
        if self.subtract is not None:
            error.add_note('powercalc_subtract_clock_partial=' + _json(
                {'phase': self.subtract.phase, 'steps': self.subtract.steps}).decode()
                + ';no_numeric_rollback')
        error.add_note('powercalc_operand_clock_partial=' + _json(self.steps).decode())
        error.add_note('powercalc_operand_clock_refusal=' + str(self.error) + ';no_numeric_rollback')

    def close(self):
        # _arm records the actual returned TimerHandle, job and action as one
        # fixed issuance tuple, separate from mutable _Clock runtime fields.
        # On abort, first quiesce *that* issued handle, then refuse any drift.
        # A poisoned row may not keep a genuine callback alive after refusal.
        foreign = False
        for name, c in self.states.items():
            issued = self._issued_timers.get(name)
            if issued is None:
                foreign |= any(getattr(c, key, None) is not None
                    for key in ('handle', 'issued_handle', 'action', 'job'))
                continue
            handle, issued_job, issued_action = issued
            if type(handle) is not asyncio.TimerHandle or handle._loop is not self.scope.loop:
                foreign = True
                continue  # Neither a foreign object nor a foreign loop is ours to touch.
            # TimerHandle.cancel() erases _callback and _args. Verify the
            # issuance metadata BEFORE cancel(), while keeping the actual
            # cancellation keyed to the independent issuance ledger.
            args = getattr(handle, '_args', None)
            binding_intact = (
                handle._callback is self.event._run_async_call_action
                and type(args) is tuple and len(args) == 2
                and args[0] is self.scope.hass and args[1] is issued_job
                and type(issued_action) is _OperandTimerAction
                and issued_action.owner is self and issued_action.name == name
                and getattr(issued_job, 'target', None) is issued_action
            )
            already_cancelled = handle.cancelled()
            if not already_cancelled:
                handle.cancel()
                _need(handle.cancelled(), 'cleanup_cancel_failed')
            else:
                foreign = True
            self._issued_timers.pop(name, None)
            if not binding_intact:
                foreign = True
            if c.handle is handle:
                c.handle = None
            else:
                foreign = True
            if c.action is issued_action:
                c.action = None
            else:
                foreign = True
            if c.job is issued_job:
                c.job = None
            else:
                foreign = True
            if c.issued_handle is handle and not already_cancelled:
                c.issued_handle = None
            else:
                foreign = True
        _need(not foreign, 'cleanup_foreign_timer')


async def async_reload_with_powercalc_operand_clock(hass, chain, evidence, original, *, guard):
    from .powercalc_exact_fields import _ExactFieldsPolicy
    from .powercalc_reload_witness import async_witness_unchanged_powercalc_reload
    policy = _ExactFieldsPolicy(chain, evidence, original, guard)
    return await async_witness_unchanged_powercalc_reload(hass, policy.receipt, evidence, original,
        guard=guard, _manage_global_rename=True, _exact_fields=policy, _retain_producers=True,
        _operand_clock=True)
