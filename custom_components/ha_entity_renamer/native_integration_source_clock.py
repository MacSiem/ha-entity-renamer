"""Prepared/accepted-chain source-ID policy for actual Integration clock scope.

No new root, expected counter input, Event/State construction, cache seed or
native method replacement. Reuse native retained arithmetic and actual fields.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from importlib import import_module
import json
import sys

from .helper_native import read_entry
from .helper_transaction import PreparedHelper
from .native_config_transition import _projection
from .native_registry import PreparedRegistryChange
from .native_registry_receipts import RegistryReceiptChain, RegistryRawObservation
from .native_input_observation import NativeInputObservation
from .native_unload_code_proof import prove_methods, unchanged
from .native_utility_unload_audit import _row
from .prepared import canonical, decode_change
from .transaction import PhaseContext


def _need(value, reason):
    if not value:
        raise ValueError('integration_source_clock_' + reason) from None


@dataclass(frozen=True)
class SourceInterval:
    kind: str
    name: str
    left: str | None
    right: str | None
    old_time: datetime | None
    new_time: datetime | None
    predecessor: object
    state: object
    transport: str | None


@dataclass
class _Flight:
    data: dict
    interval: SourceInterval
    event: object = None
    consumed: bool = False


class IntegrationSourceClockBinding:
    """One live direction, ORIGINAL preparation and later terminal authorization.

    A serialized description is evidence only. No constructor accepts a count.
    The same original chain is used for forward and reverse; each direction
    captures the CURRENT actual Integration clock, not the old numeric baseline.
    """
    def __init__(self, chain, entry_id, *, reverse=False):
        _need(type(chain) is RegistryReceiptChain and chain.writable
              and chain.active is None and type(reverse) is bool, 'live_chain')
        self.chain, self.entry_id, self.reverse = chain, entry_id, reverse
        self.direction = 'rollback' if reverse else 'forward'
        docs = {f.key: json.loads(f.manager_json) for f in chain.baseline.files}
        configs = [row for row in docs['core.config_entries']['entries'] if row['entry_id'] == entry_id]
        _need(len(configs) == 1 and configs[0]['domain'] == 'integration', 'original_integration')
        self.before = _projection(configs[0])
        helpers, registries = [], []
        for index, step in enumerate(chain.plan.steps):
            change = chain._original_change(index)
            if type(change) is PreparedHelper and change.before['identity']['entry_id'] == entry_id:
                helpers.append(change)
            if type(change) is PreparedRegistryChange and change.kind == 'entity':
                if self.before['options']['source'] in (change.before['id'], change.before['entity_id']):
                    registries.append(change)
        _need(len(registries) == 1 and len(helpers) <= 1, 'one_source_preparation')
        self.change = registries[0]
        a, b = self.change.before, self.change.after
        _need(a['id'] == b['id'] == self.change.identity and a['entity_id'] != b['entity_id']
              and {k:v for k,v in a.items() if k not in ('entity_id','modified_at')}
                == {k:v for k,v in b.items() if k not in ('entity_id','modified_at')}, 'id_only_physical_source')
        original = [row for row in docs['core.entity_registry']['entities'] if row['id'] == a['id']]
        _need(original == [a], 'original_registry_source')
        self.names = (a['entity_id'], b['entity_id'])
        self.from_name, self.to_name = (self.names[::-1] if reverse else self.names)
        if helpers:
            helper, = helpers
            helper.plan()
            _need(helper.before == self.before, 'original_helper_preparation')
            self.after = helper.after
        else:
            _need(self.before['options']['source'] == a['id'], 'prepared_config_source_required')
            self.after = self.before
        expected = canonical(self.before)[0]
        if expected['options']['source'] == a['entity_id']:
            expected['options']['source'] = b['entity_id']
        _need(self.after == expected, 'source_only_configuration')
        self.source_snapshot = self.after if reverse else self.before
        self.target_snapshot = self.before if reverse else self.after
        root = chain.store.read(chain.root_id)
        _need(root == {'digest': canonical(chain.root)[1], 'payload': chain.root}, 'original_root')
        self.root_ref = (chain.root_id, root['digest'])
        self.scope = self.authorization = None
        self.flights = deque()
        self._emission_endpoint = None
        self._endpoint_time = None
        self._report_updates = {}
        self._creation_emitted = False
        self.transport_calls = self.frozen_calls = 0
        self.last_consumption = None

    def description(self, *, accepted=False):
        value = {'kind':'integration_source_clock_v1', 'root':list(self.root_ref),
                 'entry_id':self.entry_id, 'source_uuid':self.change.identity,
                 'registry_preparation':canonical({'before':self.change.before,'after':self.change.after})[1],
                 'source_snapshot':canonical(self.source_snapshot)[1],
                 'target_snapshot':canonical(self.target_snapshot)[1], 'direction':self.direction}
        if accepted:
            _need(self.authorization is not None, 'terminal_authorization_required')
            value['authorization'] = self.authorization
            value['transport_calls'], value['frozen_calls'] = self.transport_calls, self.frozen_calls
        return value

    def bind(self, scope):
        _need(self.scope is None and scope.entry.entry_id == self.entry_id
              and scope.source == self.from_name and scope._config == self.source_snapshot,
              'actual_loaded_source_snapshot')
        self.scope = scope
        scope.source_names = self.names
        self._emission_endpoint = scope.hass.states.get(scope.source)
        _need(type(self._emission_endpoint) is scope._state_cls, 'actual_endpoint')
        self._endpoint_time = self._emission_endpoint.last_reported
        self._endpoint_values = self._values(scope,self._emission_endpoint)
        self.attrs = tuple(self._emission_endpoint.attributes.get(k) for k in
                           ('unit_of_measurement','device_class','state_class'))

    def qualify(self, scope):
        sensor = import_module('homeassistant.components.integration.sensor')
        cls = sensor.IntegrationSensor
        refs = [(cls,'_integrate_on_state_update_with_max_sub_interval'),
                (cls,'_update_integral'), (cls,'_derive_and_set_attributes_from_state'),
                (cls,'_cancel_max_sub_interval_exceeded_callback')]
        for method in (sensor._Left, sensor._Right, sensor._Trapezoidal):
            refs.extend(((method,'validate_states'), (method,'calculate_area_with_two_states')))
        refs.append((sensor._IntegrationMethod, 'calculate_area_with_one_state'))
        core = import_module('homeassistant.core')
        if scope.version == '2026.10.2':
            refs.append((core.EventBus,'_async_dispatch'))
        self.refs = tuple(refs)
        self.methods, self.codes = prove_methods(scope.root, scope.pins, self.refs)
        self.dispatch_code = (self.codes[-1] if scope.version == '2026.10.2' else scope.codes['fire'])

    def check(self, scope):
        _need(scope is self.scope and self.chain.writable and not self.chain.store.poisoned
              and self.chain.store.read(self.root_ref[0]) == {
                  'digest':self.root_ref[1], 'payload':self.chain.root}, 'live_original_chain')
        current = [row for row in scope.registry.entities.values() if row.id == self.change.identity]
        _need(len(current) == 1, 'physical_source_count')
        row, = current
        actual = _row(row)
        prepared = self.change.before if row.entity_id == self.names[0] else self.change.after
        _need(row.entity_id in self.names and row.disabled_by is None
              and {k:v for k,v in actual.items() if k != 'modified_at'}
                == {k:v for k,v in prepared.items() if k != 'modified_at'}, 'physical_uuid_or_source_fields')
        snapshot = read_entry(scope.hass, self.entry_id)
        _need(snapshot in (self.source_snapshot,self.target_snapshot), 'unprepared_config_mutation')
        if hasattr(self,'refs'):
            unchanged(self.refs,self.methods,self.codes)
        if self.authorization is not None:
            _need(snapshot == self.target_snapshot and row.entity_id == self.to_name,
                  'accepted_source_changed')

    def emission(self, scope, frame):
        data, parent = frame.f_locals.get('event_data'), frame.f_back
        kind = frame.f_locals.get('event_type')
        if kind not in ('state_changed','state_reported') or type(data) is not dict or data.get('entity_id') not in self.names:
            return
        scope._check()
        _need(parent is not None and parent.f_locals.get('self') is scope.hass.states
              and parent.f_code in (scope.codes['source_set'],scope.codes['source_remove']), 'native_source_origin')
        name, old, new = data['entity_id'], data.get('old_state'), data.get('new_state')
        physical = next(row for row in scope.registry.entities.values() if row.id == self.change.identity).entity_id
        move = None
        predecessor = old
        if kind == 'state_changed':
            if old is not None:
                _need(old is self._emission_endpoint
                      and self._values(scope,old) == self._endpoint_values, 'previous_endpoint_mutated')
            _need(parent.f_locals.get('state_changed_data') is data, 'actual_changed_payload')
            if new is None:
                _need(scope._retained and name == self.from_name and physical == self.to_name
                      and old is self._emission_endpoint and scope._pending_move is None
                      and scope.hass.states.get(name) is None, 'transport_remove')
                scope._pending_move = (old,self._endpoint_time,old.state,self._endpoint_values)
                move = 'remove'
                left,right,old_time,new_time = old.state,None,self._endpoint_time,None
            elif old is None:
                _need(scope._pending_move is not None and not self._creation_emitted
                      and name == self.to_name and physical == self.to_name, 'transport_creation')
                predecessor, old_time, left, predecessor_values = scope._pending_move
                _need(self._values(scope,predecessor) == predecessor_values, 'removed_endpoint_mutated')
                right = new.state
                new_time = new.last_updated if scope.version == '2026.10.2' else new.last_reported
                self._creation_emitted, move = True, 'creation'
            else:
                _need(old is self._emission_endpoint and name == physical, 'source_stream')
                left,right,old_time = old.state,new.state,self._endpoint_time
                new_time = new.last_updated if scope.version == '2026.10.2' else new.last_reported
        else:
            _need(parent.f_code is scope.codes['source_set']
                  and new is parent.f_locals.get('old_state')
                  and data.get('old_last_reported') == parent.f_locals.get('old_last_reported')
                  and new is self._emission_endpoint and name == physical, 'actual_report_payload')
            left = right = new.state
            old_time = data['old_last_reported']
            values = self._values(scope,new)
            _need(old_time == self._endpoint_time
                  and values[:4]+values[5:] == self._endpoint_values[:4]+self._endpoint_values[5:],
                  'reported_predecessor_mutated')
            new_time = data['last_reported'] if scope.version == '2026.10.2' else new.last_reported
        if new is not None:
            _need(type(new) is scope._state_cls and new is scope.hass.states.get(name)
                  and Decimal(new.state).is_finite()
                  and tuple(new.attributes.get(k) for k in ('unit_of_measurement','device_class','state_class')) == self.attrs,
                  'actual_finite_endpoint')
            self._emission_endpoint, self._endpoint_time = new, new_time
            self._endpoint_values = self._values(scope,new)
        interval = SourceInterval(kind,name,left,right,old_time,new_time,predecessor,new,move)
        _need(id(data) not in scope._emitted, 'duplicate_origin')
        if kind == 'state_reported':
            # Only the pinned StateMachine emission can authorize mutation of
            # an earlier State.last_reported. Other fields remain immutable.
            self._report_updates[id(new)] = (new,new.last_reported)
        scope._emitted[id(data)] = (
            data,kind,old,new,self._values(scope,old),self._values(scope,new),
            data.get('old_last_reported'),data.get('last_reported'),tuple(sorted(data)),
            interval,self._values(scope,predecessor))
        if scope._new is None:
            self.flights.append(_Flight(data,interval))

    def _values(self, scope, state):
        values = scope._state_values(state)
        return None if values is None else values + (state.attributes.get('state_class'),)

    def _unchanged_state(self, scope, state, snapshot, *, retained):
        current = self._values(scope,state)
        if current == snapshot:
            return True
        # Mutable report time is accepted only if its CURRENT value has a later
        # real StateMachine emission witness for this exact object. No timestamp
        # ordering alone, public-bus Event or arbitrary mutation grants authority.
        return (retained and current is not None and snapshot is not None
                and current[:4]+current[5:] == snapshot[:4]+snapshot[5:]
                and self._report_updates.get(id(state)) == (state,current[4]))

    def validate_emission(self, scope, event, kind, *, retained=False):
        data = event.data
        item = scope._emitted.get(id(data))
        _need(item is not None and item[0] is data
              and item[1] == event.event_type == 'state_'+kind
              and item[2] is data.get('old_state') and item[3] is data.get('new_state')
              and self._unchanged_state(scope,item[2],item[4],retained=retained)
              and self._unchanged_state(scope,item[3],item[5],retained=retained)
              and item[6] == data.get('old_last_reported')
              and item[7] == data.get('last_reported')
              and item[8] == tuple(sorted(data))
              and data.get('entity_id') == item[9].name
              and self._unchanged_state(scope,item[9].predecessor,item[10],retained=retained),
              'emission_delivery_or_mutable_input')

    def _prune_reports(self, scope):
        retained = {id(self._emission_endpoint)}
        for item in scope._emitted.values():
            retained.update(id(state) for state in (item[2],item[3],item[9].predecessor)
                            if state is not None)
        self._report_updates = {key:value for key,value in self._report_updates.items()
                                if key in retained}

    def _dispatch(self, scope, event):
        frame = sys._getframe(1)
        try:
            while frame is not None:
                if (frame.f_code is self.dispatch_code and frame.f_locals.get('self') is scope.hass.bus
                        and frame.f_locals.get('event') is event and frame.f_locals.get('event_data') is event.data):
                    return
                frame = frame.f_back
            _need(False,'native_bus_delivery')
        finally:
            del frame

    def bridge(self, scope, event, kind):
        try:
            scope._check()
            self._dispatch(scope,event)
            self.validate_emission(scope,event,kind,retained=scope._retained)
            flight = next((f for f in self.flights if f.data is event.data),None)
            _need(flight is not None and flight.event is None, 'one_delivery')
            consumed = scope._consumed.pop(id(event),None)
            _need(consumed is None or consumed == (event,True), 'incomplete_old_consumer')
            flight.event,flight.consumed = event,consumed is not None
            # 2025 nested dispatch may deliver a child before its parent. Retain
            # authentic Event refs, then consume chronological emission order.
            while self.flights and self.flights[0].event is not None:
                current = self.flights.popleft()
                event_kind = current.interval.kind.removeprefix('state_')
                self.validate_emission(scope,current.event,event_kind,retained=scope._retained)
                if current.consumed:
                    scope._emitted.pop(id(current.data))
                    continue
                _need(scope._retained and scope._phase in ('unloading','retained','setting_up'),
                      'owned_retained_consumer')
                interval = current.interval
                if interval.transport == 'remove':
                    scope._emitted.pop(id(current.data))
                    continue  # Transport must not cancel/reset the native timer.
                if interval.transport == 'creation':
                    scope.source = interval.name
                    self.transport_calls += 1
                self._consume(scope,current.event,interval)
                self.validate_emission(scope,current.event,event_kind,retained=scope._retained)
                scope._emitted.pop(id(current.data))
                if interval.transport == 'creation':
                    scope._pending_move = None
            self._prune_reports(scope)
        except BaseException:
            scope._poison('source_clock_bridge_refused')

    def _consume(self, scope, event, interval):
        old,new = interval.predecessor,interval.state
        before = (scope.old._state, scope.old._last_integration_time,
                  scope.old._last_integration_trigger.name)
        # Stable source data can use the actual native callback/wrapper directly.
        stable_new = new.last_reported == interval.new_time
        stable_old = old is None or old.last_reported == interval.old_time
        scope._bridge_depth += 1
        try:
            if stable_new and stable_old:
                if interval.transport == 'creation':
                    args = (None,None,old,new) if scope.version == '2026.10.2' else (None,old,new)
                    scope.old._integrate_on_state_update_with_max_sub_interval(*args)
                else:
                    callback = (scope.old._integrate_on_state_change_with_max_sub_interval
                                if interval.kind == 'state_changed' else
                                scope.old._integrate_on_state_report_with_max_sub_interval)
                    callback(event)
            else:
                # Native State.last_reported is mutable. Feed frozen ACTUAL
                # operands to pinned native area/update primitives; no fabricated
                # Event/State and no desired total/correction reaches the writer.
                self.frozen_calls += 1
                engine = scope.old
                sensor = import_module('homeassistant.components.integration.sensor')
                _need(interval.old_time is not None and interval.new_time is not None, 'actual_interval_times')
                origin = (interval.old_time if engine._last_integration_trigger is sensor._IntegrationTrigger.StateEvent
                          else engine._last_integration_time)
                elapsed = Decimal((interval.new_time-origin).total_seconds())
                states = engine._method.validate_states(interval.left,interval.right)
                _need(states is not None and elapsed >= 0, 'actual_numeric_interval')
                engine._cancel_max_sub_interval_exceeded_callback()
                area = engine._method.calculate_area_with_two_states(elapsed,*states)
                engine._update_integral(area)
                engine._derive_and_set_attributes_from_state(new)
                engine.async_write_ha_state()  # Native REMOVED entity: no public write.
                engine._last_integration_time = (sensor.dt_util.utcnow() if scope.version == '2026.10.2'
                                                else datetime.now(tz=import_module('datetime').UTC))
                engine._last_integration_trigger = sensor._IntegrationTrigger.StateEvent
                engine._schedule_max_sub_interval_exceeded_if_state_is_numeric(new)
            scope._bridge_calls += 1
            self.last_consumption = (interval, before, scope.old._state)
        finally:
            scope._bridge_depth -= 1
        scope._timer(scope.old)

    async def accept_setup(self, scope, authorization, observe, load_record):
        from .native_helper_lifecycle import RestoreAuthorization
        _need(type(authorization) is RestoreAuthorization and scope is self.scope
              and self.authorization is None and callable(observe) and callable(load_record), 'terminal_source_api')
        record = await load_record(self.chain.operation_id)
        scope._check(owner=True)
        observation = await observe()
        scope._check(owner=True)
        _need(type(observation) is NativeInputObservation
              and observation.original_receipt_json == self.chain.baseline.receipt_json
              and observation.version == scope.version and record is not None,
              'actual_original_bound_observation')
        raw = tuple(RegistryRawObservation(f.key,f.raw,f.identity) for f in observation.files)
        head = self.chain.expected(PhaseContext(record,0),raw)
        _need(authorization.operation_id == self.chain.operation_id
              and authorization.fingerprint == self.chain.plan.fingerprint
              and authorization.status == ('rolled_back' if self.reverse else 'applied')
              and authorization.head == head and record.status == authorization.status
              and self.chain.verify_setup_authorization(authorization), 'accepted_terminal_chain')
        self.accepted_input = self.chain.reconstruct_inputs()
        self.authorization = {'operation_id':authorization.operation_id,'fingerprint':authorization.fingerprint,
                              'head':authorization.head,'status':authorization.status}
        self.require_accepted(scope)

    def require_accepted(self, scope, *, seam=False):
        from .native_helper_lifecycle import RestoreAuthorization
        _need(self.authorization is not None and not self.flights and scope._pending_move is None
              and scope.source == self.to_name and not scope._emitted, 'accepted_transport_complete')
        self.check(scope)
        _need(self.chain.verify_setup_authorization(RestoreAuthorization(**self.authorization)),
              'accepted_chain_lost')
        docs = self.chain.accepted_documents()
        if seam:
            # No await between this ORIGINAL/accepted full-source fence and the
            # actual field/timer ownership transfer. Reuse existing input proof,
            # including physical native files and all non-native YAML sources.
            from .native_input_baseline import _Managers, _KEYS, _json
            self.accepted_input.assert_current(self.chain.original_receipt)
            managers = _Managers(scope.hass,self.chain.original_receipt)
            _need(managers.initial == tuple(_json(docs[key]['data']) for key in _KEYS),
                  'accepted_setup_manager_image')
        actual = _row(next(r for r in scope.registry.entities.values() if r.id == self.change.identity))
        accepted = [r for r in docs['core.entity_registry']['data']['entities'] if r['id'] == self.change.identity]
        _need(accepted == [actual] and read_entry(scope.hass,self.entry_id) == self.target_snapshot,
              'accepted_actual_source_configuration')


async def async_begin_source_clock(hass, entry, lease, chain, *, guard, observe, load_record, reverse=False):
    """Existing Coordinator chain authorizes preparation; no boolean-only mode."""
    from .native_integration_continuity import NativeIntegrationContinuity
    binding = IntegrationSourceClockBinding(chain,entry.entry_id,reverse=reverse)
    record = await load_record(chain.operation_id)
    lease.check()
    _need((record is not None and record.status == 'applied') if reverse else record is None,
          'one_original_direction')
    observation = await observe()
    lease.check()
    _need(type(observation) is NativeInputObservation
          and observation.original_receipt_json == chain.baseline.receipt_json,
          'original_bound_source_observation')
    states,head,sequence,completed,pending = chain._reconstruct()
    _need(pending is None and chain.active is None, 'no_pending_chain')
    raw = tuple(RegistryRawObservation(f.key,f.raw,f.identity) for f in observation.files)
    chain._observe(raw,states)
    return await NativeIntegrationContinuity.start(hass,entry,lease,chain.store,
                                                  guard=guard,source_binding=binding)


async def async_setup_source_clock(scope, authorization, *, observe, load_record):
    """Terminal native three-file source proof before AND after real setup."""
    binding = scope.source_binding
    _need(type(binding) is IntegrationSourceClockBinding and binding.scope is scope,
          'typed_live_source_scope')
    await binding.accept_setup(scope,authorization,observe,load_record)
    entity = await scope.setup()
    observation = await observe()
    scope._check(owner=True)
    record = await load_record(binding.chain.operation_id)
    scope._check(owner=True)
    _need(type(observation) is NativeInputObservation
          and observation.original_receipt_json == binding.chain.baseline.receipt_json,
          'post_setup_observation')
    binding.chain.expected(PhaseContext(record,0),tuple(
        RegistryRawObservation(f.key,f.raw,f.identity) for f in observation.files))
    binding.check(scope)
    return entity
