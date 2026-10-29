"""SAME ORIGINAL quiet and bounded SUM-two-direct minus-child handoffs.

No synthetic W, timer, source event or desired counter. This deliberately narrow
quiet path requires unchanged physical inputs. The bounded dynamic path consumes
actual retained producer results before exact transfer. Other interleavings
remain unqualified; the enclosing diagnostic still reports UNKNOWN.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .powercalc_operand_clock import _Clock, _need, _numeric, _utc
from .powercalc_source_binding import _dispatch_token


@dataclass(frozen=True)
class PowercalcSubtractClockInterval:
    """Actual completed interval and local provenance, never computed energy.

    Native arithmetic called inside sys.monitoring's callback is deliberately
    not monitored recursively. These are the actual immutable datetime objects
    used by that arithmetic, with its original live references for readback.
    """
    owner: object
    start: datetime
    end: datetime
    old_engine: object
    new_engine: object
    original_source_state: object
    new_source_state: object
    producer: object
    physical_inputs: tuple
    native_update: object
    native_one_state: object


@dataclass(frozen=True)
class PowercalcSubtractOperandInterval:
    """Completed actual native operand interval; no computed area or counter."""
    owner: object
    start: datetime
    end: datetime
    engine: object
    operand_results: tuple
    result: object
    source_state: object
    physical_inputs: tuple
    native_update: object
    native_one_state: object


class _SubtractClockOwner:
    def __init__(self, parent, bound):
        self.parent, self.scope = parent, parent.scope
        self.integration = parent.integration
        cls = self.scope.vendor.classes['SubtractGroupSensor']
        pairs = [(e, self.scope.old.get(e._sensor_source_id))
                 for e in self.scope.exact_owner.old.values()
                 if type(self.scope.old.get(e._sensor_source_id)) is cls]
        _need(len(pairs) == 1, 'one_original_subtract_pair')
        old, power = pairs[0]
        rows = {r.entity_id: r for r in bound.rows}
        _need(rows[old.entity_id].source_verified
              and rows[old.entity_id].relation_kind == 'energy_integral'
              and rows[old.entity_id].registry_id == old.registry_entry.id
              and rows[old.entity_id].edges == ((power.entity_id, old.entity_id, 1),)
              and rows[power.entity_id].source_verified
              and rows[power.entity_id].relation_kind == 'power_subtract'
              and rows[power.entity_id].registry_id == power.registry_entry.id,
              'original_subtract_source_graph')
        state = self.scope.hass.states.get(power.entity_id)
        value = _numeric(state)
        members = power._member_states
        _need(value is not None and set(members) == power._entities
              and all(type(v) is Decimal and v.is_finite() for v in members.values())
              and value == members[power._base_entity_id]
                  - sum((members[k] for k in power._subtract_entities), Decimal(0))
              and power._native_value_exact == value
              and type(old._method) is self.integration._Left and not old._filter_outliers,
              'actual_complete_original_signed_producer')
        self.c = _Clock(old, power, old, value=value)
        self.original_state = state
        self.energy_identity = (old.entity_id, old.registry_entry, old.unique_id,
            old.platform.config_entry, old._sensor_source_id, old._source_entity,
            old._max_sub_interval, old._unit_prefix, old._unit_time)
        self.source_fields = (power.registry_entry, power.unique_id,
            power.platform.config_entry, power._base_entity_id, tuple(power._subtract_entities))
        self.methods = ('on_state_change', 'calculate_new_state', 'get_summed_state',
                        'set_new_state', '_set_native_value')
        self.dispatch = _dispatch_token(power, cls, self.methods)
        # Only physical inputs, not the owned Powercalc members that naturally
        # disappear and republish during this same reload.
        self.inputs = {key: self.scope.hass.states.get(key)
                       for deps in self.scope.producer_owner.dependencies.values() for key in deps}
        _need(self.inputs and all(v is not None for v in self.inputs.values()),
              'actual_subtract_physical_inputs')
        self.phase, self.depth = 'original', 0
        self.steps, self.native_integrals = [], False
        self.target = None
        self.pending_native = None
        self.at_setup_seam = False
        self.interval = None
        self.cursor = None  # c.anchor remains the immutable retirement boundary.
        self.dynamic_results, self.dynamic_intervals = [], []
        self.dynamic_source_state = None
        self.live_numeric = None
        self.dynamic_graph = None
        self.dynamic_sum_identity = None
        base = self.scope.old.get(power._base_entity_id)
        direct = self.scope.producer_owner.old
        excluded = tuple(power._subtract_entities)
        if (base is not None and type(base) is self.scope.vendor.classes['GroupedPowerSensor']
                and rows[base.entity_id].source_verified and rows[base.entity_id].relation_kind == 'power_sum'
                and rows[base.entity_id].registry_id == base.registry_entry.id
                and rows[base.entity_id].edges == tuple((key, base.entity_id, 1) for key in sorted(direct))
                and len(excluded) == 1 and excluded[0] in direct and base._entities == set(direct)
                and rows[power.entity_id].edges == tuple(sorted(((base.entity_id, power.entity_id, 1),
                                                                 (excluded[0], power.entity_id, -1))))):
            values = {key: e._power for key, e in direct.items()}
            _need(all(type(v) is Decimal and v.is_finite() for v in values.values())
                  and all(rows[key].source_verified and rows[key].relation_kind == 'profile_lut'
                          and rows[key].registry_id == direct[key].registry_entry.id
                          and _numeric(self.scope.hass.states.get(key)) == values[key] for key in direct)
                  and base._native_value_exact == sum(values.values(), Decimal(0))
                  and value == sum(values.values(), Decimal(0)) - values[excluded[0]],
                  'original_two_direct_signed_operands')
            self.dynamic_sum_identity = base.registry_entry, base.unique_id, base.platform.config_entry
            self.dynamic_graph = (base, excluded[0], values,
                _dispatch_token(base, type(base), self.methods))

    def quiet(self):
        return all(self.scope.hass.states.get(k) is v for k, v in self.inputs.items())

    def check(self):
        c = self.c
        _need(_dispatch_token(c.power, type(c.power), self.methods) == self.dispatch,
              'subtract_original_dispatch_changed')
        row, uid, entry, base, children = self.source_fields
        _need(c.power.registry_entry is row and c.power.unique_id == uid
              and c.power.platform.config_entry is entry and c.power._base_entity_id == base
              and tuple(c.power._subtract_entities) == children
              and c.power._entities == {base, *children},
              'subtract_original_identity_changed')
        if self.dynamic_graph is not None:
            base, excluded, values, dispatch = self.dynamic_graph
            _need(base.entity_id == c.power._base_entity_id
                  and base.registry_entry is self.dynamic_sum_identity[0]
                  and base.unique_id == self.dynamic_sum_identity[1]
                  and base.platform.config_entry is self.dynamic_sum_identity[2]
                  and base._entities == set(self.scope.producer_owner.old)
                  and _dispatch_token(base, type(base), self.methods) == dispatch,
                  'subtract_sum_source_dispatch_changed')

    def after_exact_transfer(self, entity):
        if entity.entity_id != self.c.old.entity_id:
            return
        _need(self.target is None and entity is not self.c.old
              and entity._state is self.c.old._state
              and entity._last_valid_state is self.c.old._last_valid_state,
              'subtract_atomic_engine_pointer')
        if self.dynamic_results:
            self._numeric_lineage(self.c.old)
            _need((entity._state, entity._last_valid_state) == self.live_numeric
                  and entity._last_integration_time is self.c.old._last_integration_time
                  and entity._last_integration_trigger is self.c.old._last_integration_trigger,
                  'subtract_dynamic_exact_live_fields')
        self.c.engine, self.target = entity, entity

    def after_exact_schedule(self, entity):
        if entity is self.target:
            self.at_setup_seam = True
            try:
                self._handoff(self.scope.hass.states.get(entity._sensor_source_id))
            finally:
                self.at_setup_seam = False

    def _handoff(self, state):
        c, scope = self.c, self.scope
        if self.phase != 'retired' or self.native_integrals:
            return
        if self.dynamic_results:
            self._dynamic_handoff(state)
            return
        if not self.quiet():
            self.phase = 'unqualified_physical_change'
            return
        if _numeric(state) != c.value:
            return  # Incomplete new member publication does not own this clock.
        new = scope.added.get(c.power.entity_id)
        row, uid, entry, base, children = self.source_fields
        _need(new is not None and type(new) is type(c.power) and new is not c.power
              and new.hass is scope.hass and new.registry_entry is row
              and new.unique_id == uid and new.platform.config_entry is entry
              and new.platform.entities.get(new.entity_id) is new
              and new._base_entity_id == base and tuple(new._subtract_entities) == children
              and new._entities == {base, *children}
              and set(new._member_states) <= new._entities
              and base in new._member_states
              and all(type(v) is Decimal and v.is_finite() for v in new._member_states.values())
              and new._member_states[base]
                  - sum((new._member_states.get(k, Decimal(0)) for k in children), Decimal(0)) == c.value
              and new._native_value_exact == c.value and new._attr_available is True
              and _dispatch_token(new, type(c.power), self.methods)[1::2] == self.dispatch[1::2]
              and state is scope.hass.states.get(c.power.entity_id)
              and state.entity_id == c.power.entity_id
              and state.attributes.get('unit_of_measurement') == 'W'
              and scope.causal.get(None) is scope.marker
              and type(asyncio.current_task()) is asyncio.Task,
              'actual_replacement_signed_producer')
        _need(c.engine is self.target and c.engine is not c.old
              and c.engine._state is c.old._state
              and c.engine._last_valid_state is c.old._last_valid_state,
              'subtract_no_intervening_integral_or_overwrite')
        if self.at_setup_seam:
            handle, action, endpoint = self.parent._native_timer(c.engine)
            _need(endpoint is state, 'subtract_actual_native_clock_endpoint')
        # The retained interval starts at an observed retirement return. It does
        # not extrapolate any pre-retirement native transport interval. Arithmetic
        # is the pinned native LEFT and _update_integral, as for the direct clocks.
        end = _utc()
        _need(end >= c.anchor and state.last_updated.astimezone(timezone.utc) <= end,
              'subtract_actual_endpoint_order')
        before = c.engine._state
        elapsed = Decimal((end - c.anchor).total_seconds())
        self.depth += 1
        self.phase = 'integrating'
        try:
            area = c.engine._method.calculate_area_with_one_state(elapsed, c.value)
            c.engine._update_integral(area)
        finally:
            self.depth -= 1
        # Native future timer/source callbacks must start at the precise endpoint
        # just integrated. Using a second now() would leave a second tiny hole.
        c.endpoint = end
        c.engine._last_integration_time = end
        c.engine._last_integration_trigger = self.integration._IntegrationTrigger.TimeElapsed
        self.steps.append({'kind': 'subtract_quiet_handoff', 'entity_id': c.old.entity_id,
            'origin': c.anchor.isoformat(), 'endpoint': end.isoformat(),
            'power': str(c.value), 'full_before': str(before), 'full_after': str(c.engine._state),
            'old_engine': id(c.old), 'new_engine': id(c.engine), 'producer': id(new),
            'original_source_state': id(self.original_state), 'new_source_state': id(state)})
        self.phase = 'native'
        if self.at_setup_seam:
            self._complete_interval(state)

    def _numeric_lineage(self, entity):
        name, row, uid, entry, sid, source, interval, prefix, unit = self.energy_identity
        _need(type(entity) is self.parent.cls and entity.hass is self.scope.hass
              and entity.entity_id == name and entity.registry_entry is row
              and entity.unique_id == uid and entity.platform.config_entry is entry
              and entity._sensor_source_id == sid and entity._source_entity == source
              and entity._max_sub_interval == interval and entity._unit_prefix == prefix
              and entity._unit_time == unit, 'subtract_dynamic_energy_identity_changed')
        _need(self.live_numeric is not None
              and entity._state is self.live_numeric[0]
              and entity._last_valid_state is self.live_numeric[1],
              'subtract_dynamic_counter_lineage_changed')

    def consume(self, result):
        from .powercalc_retained_producer import PowercalcActualCalculation
        if self.dynamic_graph is None or self.phase not in ('retired', 'unqualified_physical_change'):
            return
        scope, c = self.scope, self.c
        base, excluded, values, dispatch = self.dynamic_graph
        producer = scope.producer_owner.old.get(result.producer.entity_id)
        _need(type(result) is PowercalcActualCalculation and producer is result.producer
              and result is scope.producer_owner.results[-1]
              and all(result is not previous for previous in self.dynamic_results)
              and result.ownership_phase == 'retained_removed' and result.available
              and (result.native_write_suppressed or not result.native_value_changed)
              and self.target is None and c.engine is c.old
              and not self.native_integrals and scope.causal.get(None) is scope.marker
              and producer._platform_state.name == 'REMOVED'
              and producer.registry_entry.id == next(row.registry_id for row in self.parent.bound.rows
                                                     if row.entity_id == producer.entity_id)
              and result.previous_native_power == values[producer.entity_id]
              and result.native_power is producer._power
              and type(result.native_power) is Decimal and result.native_power.is_finite()
              and result.event.data.get('new_state') is result.input_state
              and scope.hass.states.get(result.input_state.entity_id) is result.input_state
              and result.input_state.entity_id in scope.producer_owner.dependencies[producer.entity_id]
              and result.resolved_light_state is scope.hass.states.get(producer._source_entity.entity_id)
              and result.old_profile is producer._power_profile
              and result.actual_strategy is producer._strategy_instance,
              'subtract_actual_retained_result_lineage')
        # The typed retained producer has already proved the source/registry,
        # actual callback/Task and native normalized result. Every other physical
        # input must remain the last actual State accepted by this component.
        inputs = dict(self.inputs)
        _need(result.input_state.entity_id in inputs, 'subtract_result_physical_source')
        inputs[result.input_state.entity_id] = result.input_state
        _need(all(scope.hass.states.get(key) is state for key, state in inputs.items())
              and type(result.update_returned_at) is datetime
              and self.cursor is not None and result.update_returned_at >= self.cursor,
              'subtract_unaccounted_physical_change')
        self.check()
        self._numeric_lineage(c.old)
        self.phase = 'retired'
        self._dynamic_area(result.update_returned_at, result=result, source_state=None,
                           physical_inputs=tuple(inputs.items()))
        values[producer.entity_id] = result.native_power
        c.value = sum(values.values(), Decimal(0)) - values[excluded]
        self.inputs = inputs
        self.dynamic_results.append(result)

    def _dynamic_area(self, end, *, result, source_state, physical_inputs):
        c, engine, start = self.c, self.c.engine, self.cursor
        self._numeric_lineage(engine)
        _need(self.phase == 'retired' and not self.native_integrals
              and type(start) is datetime and type(end) is datetime and end >= start
              and start.tzinfo is not None and end.tzinfo is not None,
              'subtract_dynamic_actual_interval')
        _need(type(engine._method) is self.integration._Left, 'subtract_dynamic_native_left')
        _dispatch_token(engine, self.integration.IntegrationSensor, ('_update_integral',))
        _dispatch_token(engine._method, self.integration._Left, ('calculate_area_with_one_state',))
        self.depth += 1
        try:
            elapsed = Decimal((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds())
            area = engine._method.calculate_area_with_one_state(elapsed, c.value)
            engine._update_integral(area)
        finally:
            self.depth -= 1
        engine._last_integration_time = end
        engine._last_integration_trigger = self.integration._IntegrationTrigger.TimeElapsed
        self.live_numeric = engine._state, engine._last_valid_state
        self.cursor = c.endpoint = end
        self.steps.append({'kind': 'subtract_actual_operand_interval', 'origin': start.isoformat(),
            'endpoint': end.isoformat(), 'engine': id(engine),
            'result': None if result is None else id(result)})
        self.dynamic_intervals.append(PowercalcSubtractOperandInterval(self, start, end, engine,
            tuple(self.dynamic_results), result, source_state, physical_inputs,
            engine._update_integral.__func__, engine._method.calculate_area_with_one_state.__func__))

    def _dynamic_handoff(self, state):
        c, scope = self.c, self.scope
        if _numeric(state) != c.value:
            return
        self.check()
        self._numeric_lineage(c.engine)
        new = scope.added.get(c.power.entity_id)
        row, uid, entry, base, children = self.source_fields
        _need(self.quiet() and c.engine is self.target and self.target is not c.old
              and new is not None and type(new) is type(c.power) and new is not c.power
              and new.hass is scope.hass and new.registry_entry is row and new.unique_id == uid
              and new.platform.config_entry is entry and new.platform.entities.get(new.entity_id) is new
              and new._base_entity_id == base and tuple(new._subtract_entities) == children
              and new._entities == {base, *children} and set(new._member_states) <= new._entities
              and base in new._member_states and new._attr_available is True
              and all(type(value) is Decimal and value.is_finite() for value in new._member_states.values())
              and new._native_value_exact == c.value
              and new._member_states[base] - sum((new._member_states.get(k, Decimal(0))
                                                  for k in children), Decimal(0)) == c.value
              and _dispatch_token(new, type(c.power), self.methods)[1::2] == self.dispatch[1::2]
              and state is scope.hass.states.get(c.power.entity_id)
              and state.entity_id == c.power.entity_id and state.attributes.get('unit_of_measurement') == 'W'
              and scope.causal.get(None) is scope.marker and type(asyncio.current_task()) is asyncio.Task,
              'subtract_dynamic_first_native_source')
        if self.at_setup_seam:
            handle, action, endpoint = self.parent._native_timer(c.engine)
            _need(endpoint is state, 'subtract_dynamic_native_timer_endpoint')
        # End at the actual first native source clock, not a second UTC now().
        self._dynamic_area(state.last_updated, result=None, source_state=state,
                           physical_inputs=tuple(self.inputs.items()))
        self.phase = 'native'
        if self.at_setup_seam:
            self._complete_dynamic_handoff(state)

    def _complete_dynamic_handoff(self, state):
        handle, action, endpoint = self.parent._native_timer(self.c.engine)
        _need(self.dynamic_source_state is None and endpoint is state and self.phase == 'native'
              and self.quiet() and not self.depth
              and self.c.engine._last_integration_time is self.cursor
              and self.c.engine._last_integration_trigger is self.integration._IntegrationTrigger.TimeElapsed,
              'subtract_dynamic_native_handoff_readback')
        self.dynamic_source_state = state

    def _complete_interval(self, state):
        c = self.c
        handle, action, endpoint = self.parent._native_timer(c.engine)
        _need(self.interval is None and self.phase == 'native' and not self.depth
              and self.quiet() and endpoint is state
              and c.engine._last_integration_time is c.endpoint
              and c.engine._last_integration_trigger is self.integration._IntegrationTrigger.TimeElapsed,
              'subtract_completed_interval_provenance')
        # No computed area, counter, encoded receipt total or expected value is
        # exposed by this fact. It records the endpoints actually consumed by
        # the completed arithmetic, after native clock ownership readback.
        self.interval = PowercalcSubtractClockInterval(self, c.anchor, c.endpoint,
            c.old, c.engine, self.original_state, state,
            self.scope.added[c.power.entity_id], tuple(self.inputs.items()),
            c.engine._update_integral.__func__, c.engine._method.calculate_area_with_one_state.__func__)

    def observe(self, frame, phase):
        c, p = self.c, self.parent
        obj, code = frame.f_locals.get('self'), frame.f_code
        if code is p.hc[0] and obj is c.old:
            if phase == 'return':
                _need(self.phase == 'original' and not obj._on_remove
                      and obj._platform_state.name == 'REMOVED', 'subtract_actual_retirement')
                c.anchor = _utc()
                self.cursor = c.anchor
                self.live_numeric = c.old._state, c.old._last_valid_state
                self.phase = 'retired' if self.quiet() else 'unqualified_physical_change'
            return
        if ((obj is c.old or obj is self.target) and code is p.hc[10] and phase == 'start'
                and self.phase == 'retired' and not self.depth):
            self.native_integrals = True
            self.phase = 'unqualified_native_integral'
            return
        if obj is self.target and self.pending_native is not None and code in (p.hc[8], p.hc[9]):
            if phase == 'return':
                _need(obj._state is self.pending_native, 'subtract_first_numeric_double_area')
                handle, action, endpoint = p._native_timer(obj)
                _need(endpoint is frame.f_locals['event'].data.get('new_state'),
                      'subtract_actual_consumer_clock_endpoint')
                obj._last_integration_time = c.endpoint
                obj._last_integration_trigger = self.integration._IntegrationTrigger.TimeElapsed
                self.pending_native = None
                if self.dynamic_results:
                    self._complete_dynamic_handoff(endpoint)
                else:
                    self._complete_interval(endpoint)
            return
        if obj is not self.target or self.phase not in ('retired', 'integrating'):
            return
        if code in (p.hc[8], p.hc[9]) and phase == 'start' and self.phase == 'retired':
            event = frame.f_locals.get('event')
            state = event.data.get('new_state')
            if (_numeric(state) == c.value
                    and event.data.get('old_state') is not None
                    and _numeric(event.data['old_state']) is None):
                # Reuse the existing exact listener ownership proof before a
                # handoff at an actual first numeric consumer delivery.
                tracker, job = p._consumers(self.target)
                if self.dynamic_results:
                    _need(event.event_type == 'state_changed' and code is p.hc[8]
                          and any(f.f_locals.get('hass') is self.scope.hass
                                  and f.f_locals.get('callbacks') is tracker.callbacks
                                  and f.f_locals.get('job') is job and f.f_locals.get('event') is event
                                  for f in p._ancestors(frame, p.hc[18]))
                          and any(f.f_locals.get('event') is event
                                  and f.f_locals.get('event_data') is event.data
                                  for f in p._ancestors(frame, p.hc[5])),
                          'subtract_dynamic_native_consumer_delivery')
                self._handoff(state)
                if self.phase == 'native':
                    self.pending_native = self.target._state
