"""Causal numeric observation around existing own Powercalc reload.

Performs that real reload through G4. No counter/State/cache/timer transfer,
lifecycle widening, new baseline, root or journal. Current pinned pair only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
import json
import math
import sys

from .native_unload_code_proof import prove_methods, unchanged
from .native_utility_setup_audit import _SOURCES, _code_objects
from .native_registry_receipts import RegistryReceiptChain
from .powercalc_source_binding import PowercalcVendor, _pins, _json
from .powercalc_runtime_observer import _guard
from .powercalc_reload_witness import async_witness_unchanged_powercalc_reload, PowercalcReloadWitness
from .native_powercalc_accepted_consumers import _accepted_sources
from .yaml_validation import _settled_executor


def _need(ok, reason):
    if not ok:
        raise ValueError('powercalc_numeric_probe_' + reason) from None


def _value(value, depth=0):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is Decimal:
        _need(value.is_finite(), 'finite_decimal')
        return {'decimal': str(value)}
    if type(value) is float:
        _need(math.isfinite(value), 'finite_float')
        return {'float_hex': value.hex()}
    if type(value) is datetime:
        return {'datetime': value.isoformat()}
    if isinstance(value, Enum):
        return _value(value.value, depth + 1)
    if type(value) in (tuple, list, set, frozenset):
        values = [_value(v, depth + 1) for v in value]
        return sorted(values, key=_json) if type(value) in (set, frozenset) else values
    _need(type(value) is dict, 'value_type_' + type(value).__module__ + '.' + type(value).__qualname__)
    # Native State attributes use HA StrEnum constants alongside plain strings.
    # Encode their actual string values, refusing all other keys and collisions.
    pairs = []
    for key, item in value.items():
        if type(key) is str:
            name = key
        else:
            _need(isinstance(key, str) and isinstance(key, Enum)
                  and type(key.value) is str, 'attribute_key_type')
            name = key.value
        pairs.append((name, _value(item, depth + 1)))
    _need(len({name for name, item in pairs}) == len(pairs), 'attribute_key_collision')
    return dict(pairs)


@dataclass(frozen=True)
class PowercalcNumericProbeResult:
    evidence_sha256: str
    original_runtime_token: bytes
    chain_head: str
    lifecycle: PowercalcReloadWitness
    trace_json: bytes
    continuity_verified: bool = field(default=False, init=False)
    counter_ownership_verified: bool = field(default=False, init=False)
    migration_ready: bool = field(default=False, init=False)
    family_complete: bool = field(default=False, init=False)
    status: str = field(default='UNKNOWN', init=False)


class _Probe:
    """Observer only; numeric values are never assigned to native objects."""
    def __init__(self, hass, vendor, entities):
        self.hass, self.vendor = hass, vendor
        self.loop, self.owner = asyncio.get_running_loop(), asyncio.current_task()
        self.old = dict(entities)
        self.store = next(e._prev_state_store for e in entities.values()
                          if type(e) is vendor.classes['GroupedEnergySensor'])
        self.group = next(e.entity_id for e in entities.values()
                          if type(e) is vendor.classes['GroupedEnergySensor'])
        self.names = set(entities)
        for entity in entities.values():
            self.names.update(getattr(entity, '_track_entities', set()))
        self.rows, self.keep = [], {}
        self.tool, self.error = None, None
        self.first_failure = None

    def state(self, state):
        from homeassistant.core import State
        if state is None:
            return None
        _need(type(state) is State, 'native_state_required')
        self.keep[id(state)] = state
        return {'process_id': id(state), 'entity_id': state.entity_id, 'state': state.state,
                'changed': state.last_changed.isoformat(), 'updated': state.last_updated.isoformat(),
                'reported': state.last_reported.isoformat(), 'attributes': _value(dict(state.attributes))}

    def snapshot(self, entity):
        data = {'entity_id': entity.entity_id, 'process_id': id(entity),
                'uuid': None if entity.registry_entry is None else entity.registry_entry.id}
        if type(entity) is self.vendor.classes['VirtualEnergySensor']:
            cancel = entity._max_sub_interval_exceeded_callback
            timer = getattr(cancel, '__self__', None)
            if type(timer) is asyncio.TimerHandle:
                self.keep[id(timer)] = timer
            data.update(full=_value(entity._state), last_valid=_value(entity._last_valid_state),
                        anchor=_value(entity._last_integration_time),
                        trigger=_value(entity._last_integration_trigger),
                        source=entity._sensor_source_id,
                        timer=(None if type(timer) is not asyncio.TimerHandle else
                               {'process_id': id(timer), 'deadline': timer.when(), 'cancelled': timer.cancelled()}))
        elif type(entity) is self.vendor.classes['VirtualPowerSensor']:
            data.update(power_field=_value(vars(entity).get('_power')),
                        native_field=_value(vars(entity).get('_attr_native_value')))
        else:
            self.keep[id(entity._prev_state_store.states)] = entity._prev_state_store.states
            data.update(full=_value(entity._native_value_exact),
                        store_process_id=id(entity._prev_state_store),
                        states_process_id=id(entity._prev_state_store.states),
                        previous_present=entity.entity_id in entity._prev_state_store.states,
                        previous={k: self.state(v) for k, v in
                                  entity._prev_state_store.states.get(entity.entity_id, {}).items()})
        return data

    def observe(self, frame, phase, result=None):
        try:
            tag = self.tags[frame.f_code]
            obj = frame.f_locals.get('self')
            row = {'seq': len(self.rows), 'tag': tag, 'phase': phase}
            if tag == 'fire':
                if obj is not self.hass.bus:
                    return
                data = frame.f_locals.get('event_data')
                if type(data) is not dict or data.get('entity_id') not in self.names:
                    return
                kind = frame.f_locals.get('event_type')
                if kind not in ('state_changed', 'state_reported'):
                    return
                self.keep[id(data)] = data
                row.update(event_type=kind, data_process_id=id(data), entity_id=data['entity_id'],
                           old=self.state(data.get('old_state')), new=self.state(data.get('new_state')),
                           old_last_reported=_value(data.get('old_last_reported')),
                           last_reported=_value(data.get('last_reported')))
            elif tag.startswith('store_'):
                if obj is not self.store or frame.f_locals.get('group') != self.group:
                    return
                row.update(group=self.group, store_process_id=id(obj), states_process_id=id(obj.states),
                           previous={k: self.state(v) for k, v in obj.states.get(self.group, {}).items()})
                for key in ('entity_id', 'current_entities'):
                    if key in frame.f_locals:
                        row[key] = _value(frame.f_locals[key])
                if 'state' in frame.f_locals:
                    row['state'] = self.state(frame.f_locals['state'])
            else:
                if (getattr(obj, 'hass', None) is not self.hass
                        or getattr(obj, 'entity_id', None) not in self.old
                        or type(obj) not in (self.vendor.classes['VirtualEnergySensor'],
                                            self.vendor.classes['GroupedEnergySensor'],
                                            self.vendor.classes['VirtualPowerSensor'])):
                    return
                self.keep[id(obj)] = obj
                row.update(snapshot=self.snapshot(obj))
                if tag == 'schedule':
                    # First native BASE schedule entry happens after any future
                    # Powercalc-specific transfer at the vendor override seam.
                    old = self.old[obj.entity_id]
                    row['actual_old_engine'] = self.snapshot(old)
                event = frame.f_locals.get('event')
                if event is not None:
                    self.keep[id(event)] = event
                    row.update(event_process_id=id(event), event_type=event.event_type,
                               data_process_id=id(event.data),
                               event_old=self.state(event.data.get('old_state')),
                               event_new=self.state(event.data.get('new_state')))
                for key in ('last_sensor_data', 'last_sensor_state'):
                    restored = frame.f_locals.get(key)
                    if restored is not None:
                        row[key] = {'type': type(restored).__module__ + '.' + type(restored).__qualname__,
                                    'native_value': _value(restored.native_value),
                                    'last_valid': _value(getattr(restored, 'last_valid_state', None))}
                for key in ('state', 'source_state', 'prev_state'):
                    if key in frame.f_locals:
                        row[key] = self.state(frame.f_locals[key])
                for key in ('area', 'area_scaled', 'elapsed_seconds', 'source_state_dec',
                            'cur_value', 'prev_value', 'delta', 'group_sum', 'value', 'now'):
                    if key in frame.f_locals:
                        row[key] = _value(frame.f_locals[key])
                if phase == 'return' and type(result) is Decimal:
                    row['returned_decimal'] = _value(result)
            parent = frame.f_back
            parents = []
            while parent is not None:
                parents.append(parent.f_code.co_qualname)
                parent = parent.f_back
            row['parents'] = parents
            _need(asyncio.get_running_loop() is self.loop, 'event_loop')
            self.rows.append(row)
        except BaseException as error:
            if self.error is None:
                self.error = 'native_frame_unrepresentable'
                try:
                    reason = (error.args[0] if type(error) is ValueError
                        and len(error.args) == 1 and type(error.args[0]) is str
                        and error.args[0].startswith('powercalc_numeric_probe_') else None)
                    self.first_failure = (tag, phase, frame.f_code.co_qualname,
                        frame.f_lineno, type(error).__module__ + '.' + type(error).__qualname__, reason)
                except BaseException:
                    self.first_failure = None

    def install(self):
        monitor = sys.monitoring
        available = [key for key in range(6) if monitor.get_tool(key) is None]
        _need(bool(available), 'monitor_unavailable')
        self.tool = available[0]
        monitor.use_tool_id(self.tool, 'renamer.powercalc_numeric_probe')
        def started(code, offset):
            self.observe(sys._getframe(1), 'start')
        def returned(code, offset, result):
            self.observe(sys._getframe(1), 'return', result)
        monitor.register_callback(self.tool, monitor.events.PY_START, started)
        monitor.register_callback(self.tool, monitor.events.PY_RETURN, returned)
        for code in self.tags:
            monitor.set_local_events(self.tool, code, monitor.events.PY_START | monitor.events.PY_RETURN)

    def check(self):
        if self.error is not None:
            error = ValueError('powercalc_numeric_probe_trace_refused')
            if self.first_failure is not None:
                error.add_note('first_failure=' + repr(self.first_failure))
            raise error from None
        _need(sys.monitoring.get_tool(self.tool) == 'renamer.powercalc_numeric_probe'
              and sys.monitoring.get_events(self.tool) == 0
              and all(sys.monitoring.get_local_events(self.tool, c) ==
                      sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN
                      for c in self.tags), 'monitor_lost')

    def close(self):
        first = None
        def attempt(fn):
            nonlocal first
            try:
                fn()
            except BaseException as error:
                if first is None:
                    first = error
        if self.tool is not None and sys.monitoring.get_tool(self.tool) == 'renamer.powercalc_numeric_probe':
            for code in self.tags:
                attempt(lambda code=code: sys.monitoring.set_local_events(self.tool, code, 0))
            for kind in (sys.monitoring.events.PY_START, sys.monitoring.events.PY_RETURN):
                attempt(lambda kind=kind: sys.monitoring.register_callback(self.tool, kind, None))
            attempt(lambda: sys.monitoring.free_tool_id(self.tool))
        self.keep.clear()
        if first is not None:
            raise first


async def async_capture_powercalc_numeric_reload(hass, chain, evidence, original, *, guard):
    """ONE unchanged-ORIGINAL G4 call, actual numeric boundary facts only.

    Caller owns existing operation/admin lease. Accepted source-ID effects are
    not executed here; original and current configuration must still coincide.
    """
    from homeassistant.helpers import entity_platform as ep
    from homeassistant.core import EventBus
    _need(type(chain) is RegistryReceiptChain and chain.original_receipt.version == '2026.10.2',
          'same_chain_current_pair_required')
    receipt, state = chain.original_receipt, chain.reconstruct_inputs()
    evidence.assert_receipt(receipt)
    def check():
        _guard(guard)
        _need(state.same_evidence(chain.reconstruct_inputs()), 'accepted_head_changed')
        evidence.assert_receipt(receipt)
    check()
    vendor = PowercalcVendor(hass, with_profiles=True)
    entities = {e.entity_id: e for p in ep.async_get_platforms(hass, 'powercalc') for e in p.entities.values()}
    probe = _Probe(hass, vendor, entities)
    cls, integration = vendor.classes, vendor.integration.IntegrationSensor
    vrefs = tuple((owner, name) for owner, names in (
        (cls['GroupedEnergySensor'], ('restore_last_state', 'calculate_delta', 'calculate_initial_state', 'calculate_new_state')),
        (cls['GroupedSensor'], ('_set_native_value',)),
        (cls['PreviousStateStore'], ('cleanup_entity_states', 'set_entity_state')),
        (cls['VirtualPowerSensor'], ('_handle_source_entity_state_change', 'calculate_power')),
    ) for name in names)
    hrefs = tuple((integration, name) for name in (
        'async_added_to_hass', '_update_integral', '_schedule_max_sub_interval_exceeded_if_state_is_numeric',
        '_integrate_on_state_change_with_max_sub_interval', '_integrate_on_state_report_with_max_sub_interval')) + (
        (EventBus, 'async_fire_internal'),)
    def proof():
        vendor.sources()
        vm, vc = prove_methods(vendor.root, _pins(), vrefs)
        pins = dict(_SOURCES['2026.10.2'])
        pins.update(vendor.ha_pins)
        hm, hc = prove_methods(vendor.ha_root, pins, hrefs)
        _accepted_sources(chain, state, receipt)
        return vm, vc, hm, hc
    vm, vc, hm, hc = await _settled_executor(hass, proof)
    check()
    probe.tags = dict(zip(vc + hc, (
        'group_restore', 'group_delta', 'group_initial', 'group_change', 'group_set',
        'store_cleanup', 'store_set', 'power_source', 'power_calculate',
        'integration_added', 'integral', 'schedule',
        'changed', 'reported', 'fire'), strict=True))
    timer = [c for c in _code_objects(hc[2]) if c.co_name == '_integrate_on_max_sub_interval_exceeded_callback']
    _need(len(timer) == 1, 'native_timer_code')
    probe.tags[timer[0]] = 'timer'
    try:
        probe.install()
        lifecycle = await async_witness_unchanged_powercalc_reload(
            hass, receipt, evidence, original, guard=check)
        probe.check()
        # Final vendor/HA source pins and accepted full graph in ONE last job.
        await _settled_executor(hass, proof)
        check()
        probe.check()
        vendor.bindings()
        unchanged(vrefs, vm, vc)
        unchanged(hrefs, hm, hc)
        trace = _json(probe.rows)
        return PowercalcNumericProbeResult(evidence.sha256, original.structural_token,
                                            state.head, lifecycle, trace)
    finally:
        probe.close()
