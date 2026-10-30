"""Explicit private native Automation graph and OWN state ingress.

The ordinary observer remains strict. This capability observes the REAL wrapper
and both routing lists; it never temporarily unwraps a target for verification.
"""
from __future__ import annotations
import asyncio
from collections import deque
from dataclasses import dataclass
from functools import partial

from .transaction import Refused
from .native_automation_state_binding import capture_state_bindings, _value, _job
from .yaml_runtime_observer import _closure, _registered_bus_remove


def _need(value, reason='automation_owned_graph_unconfirmed'):
    if not value:
        raise Refused(reason) from None


def _shape(function):
    return (id(function), id(function.__code__), id(function.__globals__),
        tuple(id(cell) for cell in function.__closure__ or ()))


@dataclass
class _Route:
    binding: object
    before: tuple
    target: tuple
    stable: tuple
    wrapper: object = None
    queue: object = None
    handle: object = None
    seen: object = None
    wrapper_graph: tuple = ()
    added_keys: object = None
    keys_written: bool = False
    wrapper_written: bool = False
    duration_snapshot: tuple = ()
    duration_transported: bool = False
    duration_operations: object = None
    duration_events: object = None
    duration_delivered: object = None
    duration_dispatch: object = None
    duration_pending: object = None
    duration_retained: object = None


def _stable(binding):
    b = binding
    cells = _closure(b.listener, b._native.state.__name__,
                     'async_attach_trigger.<locals>.state_automation_listener')
    _need(set(cells) == set(b.listener_cells) and cells['config'] is b.config)
    return (id(b), id(b.entity), id(b.entity.action_script), id(b.entity.raw_config),
        id(b.entity.registry_entry), b.entity.unique_id, b.entity.entity_id,
        _shape(b.detach), _shape(b.remove), _shape(b.listener),
        id(b.removes), tuple(map(id, b.removes)), id(b.unsubscribe),
        id(b.config), id(b.config_ids), id(b.original_keys), id(b.keyed_job),
        (id(b.action_job), id(b.action_job.target.__self__), id(b.action_job.target.__func__),
         b.action_job.name, b.action_job._cancel_on_shutdown, id(b.action_job._cache)),
        _value(b.match_from), _value(b.match_to),
        (b._duration.stable(b) if hasattr(b, '_duration') else
         tuple((key, _value(value)) for key, value in cells.items() if key != 'config')))


class OwnedAutomationGraph:
    """Root-local typed observer authority; never serialized or client supplied."""
    def __init__(self, hass):
        self.hass, self.routes, self.witness = hass, [], None
        self.live, self.overlap = False, False
        self.prepared = ()
        self.actors = ()
        self.duration = None
        self.counts = dict(foreign_seen=0, foreign_forwarded=0, owned_remove=0,
            neutral_add=0, meaningful_seen=0, no_match=0, action_submitted=0,
            deferred_delivered=0)

    @classmethod
    async def with_duration(cls, hass, *, guard):
        from .native_automation_duration_binding import NativeAutomationDurationCapability
        from .native_automation_state_binding import _Native
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        obj = cls(hass)
        component = hass.data.get(DATA_INSTANCES, {}).get('automation')
        if component is not None and any(e._is_enabled and any('for' in c for c in e._trigger_config)
                                        for e in component.entities):
            guard()
            native = await hass.async_add_executor_job(_Native, hass)
            guard()
            obj.duration = await hass.async_add_executor_job(NativeAutomationDurationCapability, hass, native)
            guard()
        return obj

    async def prepare(self, entities, *, guard, actors):
        from .native_automation_state_binding import async_capture_state_bindings
        _need(not self.live and not self.routes)
        prepared = []
        for entity in entities:
            prepared.extend(await async_capture_state_bindings(self.hass, entity, guard=guard, _duration=self.duration))
        self.prepared = tuple(prepared)
        self.actors = tuple(actors)

    def capture(self, entities, mapping):
        _need(not self.live and not self.routes)
        for entity in entities:
            previous = tuple(binding for binding in self.prepared if binding.entity is entity)
            _need(previous or entity._is_enabled is False and entity._async_detach_triggers is None,
                  'automation_prepared_trigger_owner_changed')
            # Fresh ORIGINAL graph after producer/action drain, with the SAME
            # already-audited native source refs; no source IO at this seam.
            fresh = capture_state_bindings(self.hass, entity, _native=previous[0]._native, _duration=self.duration) if previous else ()
            for binding in fresh:
                if binding.entity is not entity:
                    continue
                binding.assert_original()
                old = tuple(binding.original_keys)
                target = tuple(mapping.get(key, key) for key in old)
                _need(len(set(target)) == len(target), 'automation_route_collision')
                self.routes.append(_Route(binding, old, target, _stable(binding),
                    queue=deque(), seen=set(), added_keys=[]))
                if hasattr(binding, '_duration'):
                    self.routes[-1].duration_events = {}
                    self.routes[-1].duration_delivered = set()
                    self.duration.lock(self.routes[-1])

    def install(self, witness):
        from homeassistant.core import callback, HassJobType
        from .native_rename_emission_classifier import NativeRenameEmissions
        _need(type(witness) is NativeRenameEmissions and witness.automation_graph is self
              and not witness.attempted and not witness.closed and not self.live)
        self.witness, self.live, self.overlap = witness, True, True
        try:
            for route in self.routes:
                b = route.binding
                _need(b.keyed_job.target is b.listener
                      and b.keyed_job.job_type is HassJobType.Callback)
                def make_wrapper(owned):
                    @callback
                    def receive(event):
                        self._receive(owned, event)
                    return receive
                route.wrapper = make_wrapper(route)
                route.wrapper_graph = _value(route.wrapper)
                # Native keys and cfg.entity_id are separate ACTUAL lists on
                # both pinned Core versions. No entity_ids freevar is assumed.
                keys = tuple(dict.fromkeys((*route.before, *route.target)))
                for key in keys:
                    members = b.callbacks[key]
                    if key not in route.before:
                        _need(not any(job is b.keyed_job for job in members))
                        members.append(b.keyed_job)
                        route.added_keys.append(key)
                b.original_keys[:] = keys
                route.keys_written = True
                if hasattr(b, '_duration'):
                    self.duration.transport(route, route.target)
                    route.duration_transported = True
                b.keyed_job.target = route.wrapper
                route.wrapper_written = True
                if hasattr(b, '_duration'):
                    self.duration.wrap(self, route)
            self.check()
        except BaseException:
            # Config transport validates its retained slots before graph undo.
            # A standalone graph still owns its complete cleanup boundary.
            if getattr(self,'transfer',None) is None:
                self.retire(before_attempt=True)
            raise

    def _receive(self, route, event):
        if hasattr(route.binding, '_duration'):
            return self._receive_duration(route, event, None)
        try:
            _need(self.live and route.binding.keyed_job.target is route.wrapper)
            change = self.witness.owned_state_change(event)
            if change is None:
                self.counts['foreign_seen'] += 1
                if route.queue:
                    route.queue.append((event, None))
                else:
                    route.binding.listener(event)  # SAME original Event
                    self.counts['foreign_forwarded'] += 1
                return
            _need(id(event) not in route.seen, 'automation_owned_event_duplicate')
            route.seen.add(id(event))
            if change.phase == 'remove':
                self.counts['owned_remove'] += 1
            elif change.disposition == 'neutral':
                self.counts['neutral_add'] += 1
            else:
                _need(change.disposition == 'meaningful', 'automation_owned_state_unknown')
                self.counts['meaningful_seen'] += 1
                route.queue.append((event, change))
                if route.handle is None:
                    route.handle = self.hass.loop.call_soon(self._deliver, route)
        except BaseException:
            self.witness._poison('automation_ingress_uncertain')

    def _receive_duration(self, route, event, cancel):
        try:
            _need(self.live and route.binding.keyed_job.target is route.wrapper)
            if route.duration_dispatch is not None:
                original_event, pending, image = route.duration_dispatch
                _need(event is original_event and cancel is not None
                      and cancel['cancel_job'] is pending['cancel_job'],
                      'automation_duration_dispatch_role_changed')
                token = (id(event), id(cancel['cancel_job']))
                _need(token not in route.seen, 'automation_duration_duplicate_role')
                _need(self._duration_role_image(route, pending) == image,
                      'automation_duration_dispatch_owner_changed')
                route.seen.add(token)
                cancel['cancel_listener'](event)
                route.duration_delivered.add(token)
                route.duration_dispatch = None
                self.duration.wrap(self, route)
                return
            self.duration.check_route(route)
            change = self.witness.owned_state_change(event)
            token = (id(event), id(None if cancel is None else cancel['cancel_job']))
            _need(token not in route.seen, 'automation_duration_duplicate_role')
            route.seen.add(token)
            route.duration_events[id(event)] = event  # Pin authentic foreign Event identity.
            if change is not None and (change.phase == 'remove' or change.disposition == 'neutral'):
                return
            _need(change is None or change.disposition == 'meaningful',
                  'automation_duration_owned_state_unknown')
            if change is None and not route.queue:
                if cancel is None:
                    pending = tuple(p for p in self.duration.pending(route.binding)[0]
                                    if p['key'] == event.data['entity_id'])
                    _need(len(pending) <= 1, 'automation_duration_dispatch_inventory')
                    if pending:
                        route.duration_dispatch = (event, pending[0], self._duration_role_image(route, pending[0]))
                    route.binding.listener(event)
                    route.duration_delivered.add(token)
                    if not pending:
                        self.duration.wrap(self, route)
                else:
                    self._forward_duration(route, event, cancel)
                return
            route.queue.append((event, change, cancel))
            if route.handle is None:
                route.handle = self.hass.loop.call_soon(self._deliver, route)
        except BaseException:
            self.witness._poison('automation_duration_ingress_uncertain')

    def _duration_role_image(self, route, pending):
        # The old native cancel still occupies its original dispatch.copy()
        # position. Main may replace U[key], but cannot alter these retained
        # edges. Validate them before allowing that cancel to touch anything.
        b = route.binding
        self.duration.check_sources()
        _need(_stable(b) == route.stable and self.hass.data.get(b.tracker.key) is b.data
              and b.data.callbacks is b.callbacks and b.data.listener is b.bus_remove,
              'automation_duration_dispatch_native_owner')
        job, handle = pending['cancel_job'], pending['handle']
        wrapper, original, shape = self.duration.wrappers[job]
        _need(job.target is wrapper and pending['cancel_listener'] is original
              and tuple(pending['keys']) == (pending['key'],)
              and sum(j is job for j in b.callbacks.get(pending['key'], ())) == 1
              and not any(j is job for key, members in b.callbacks.items()
                          if key != pending['key'] for j in members)
              and not handle.cancelled() and handle._loop is self.hass.loop
              and sum(h is handle for h in self.hass.loop._scheduled) == 1,
              'automation_duration_dispatch_retained_edges')
        return (_value(pending['clear']), _value(pending['cancel']),
                _value(pending['cancel_listener']), _value(pending['predicate']),
                _value(wrapper), shape, handle.when(), id(handle._context),
                id(handle._callback), tuple(map(id, handle._args)))

    def _forward_duration(self, route, event, cancel):
        token = (id(event), id(None if cancel is None else cancel['cancel_job']))
        if token in route.duration_delivered:
            return
        if cancel is None:
            # Capture only the cancel roles present BEFORE native main runs.
            # Main may replace U[key]; its old cancel must finish before a full
            # census can judge that transient overwrite. Keep the SAME Event.
            pending_before = tuple(p for p in self.duration.pending(route.binding)[0]
                                   if p['key'] == event.data['entity_id'])
            route.binding.listener(event)
            for pending in pending_before:
                cancel_token = (id(event), id(pending['cancel_job']))
                if cancel_token not in route.duration_delivered:
                    pending['cancel_listener'](event)
                    route.duration_delivered.add(cancel_token)
        else:
            cancel['cancel_listener'](event)
        route.duration_delivered.add(token)
        # Native dispatch.copy() may subsequently call the old wrapper: its
        # authentic role has already run once. Never send this starting Event
        # to a newly created cancel job. Census remains strict before wrapping.
        self.duration.wrap(self, route)

    def _deliver_duration(self, route):
        while route.queue:
            self.duration.check_route(route)
            event, change, cancel = route.queue.popleft()
            if change is None:
                # Includes a pending OWN timer created by an earlier FIFO
                # entry, even if absent from this Event's native dispatch.copy().
                self._forward_duration(route, event, cancel)
            else:
                _need(self.witness.owned_state_change(event) == change, 'automation_owned_pair_changed')
                if cancel is None:
                    self._action(route.binding, change)
                elif not cancel['predicate'](event.data['entity_id'], change.prior_state, change.new_state):
                    cancel['clear']()
            self.duration.lock(route)

    def _deliver(self, route):
        route.handle = None
        try:
            _need(self.live and not self.witness.in_mutate
                  and self.hass.states.get(self.witness.source_id) is None,
                  'automation_old_namespace_not_retired')
            self.witness._check()
            self.check()
            node = self.node
            _need(node.root.chain.active is node.intent and node.phase == 'registry_attempted'
                  and node.script_phase.graph is self, 'automation_live_intent_changed')
            node.root.chain.verify_intent_source(node.intent, node.context, node.before)
            if self.transfer is not None:
                _need(self.transfer._actor_graph() == self.transfer.installed,
                      'automation_live_program_changed')
                self.transfer._rows(self.transfer.after)
            if hasattr(route.binding, '_duration'):
                self._deliver_duration(route)
                return
            while route.queue:
                event, change = route.queue.popleft()
                if change is None:
                    route.binding.listener(event)
                    self.counts['foreign_forwarded'] += 1
                    continue
                _need(self.witness.owned_state_change(event) == change,
                      'automation_owned_pair_changed')
                self._action(route.binding, change)
                self.counts['deferred_delivered'] += 1
        except BaseException:
            self.witness._poison('automation_delivery_uncertain')
        finally:
            self.witness._signal()

    def _action(self, b, change):
        prior, new, event = change.prior_state, change.new_state, change.event
        attribute = b.attribute
        old_value = prior.attributes.get(attribute) if attribute is not None else prior.state
        new_value = new.attributes.get(attribute) if attribute is not None else new.state
        # Exact native no-for matcher semantics, SAME native closures/action job.
        if (attribute is not None and old_value == new_value
                or not b.match_from(old_value) or not b.match_to(new_value)
                or not b.match_all and old_value == new_value):
            self.counts['no_match'] += 1
            return
        if hasattr(b, '_duration'):
            route, = (r for r in self.routes if r.binding is b)
            for pending in self.duration.pending(b)[0]:
                if (pending['key'] == event.data['entity_id']
                        and not pending['predicate'](event.data['entity_id'], prior, new)):
                    pending['clear']()
            self.duration.lock(route)
            self.duration.start_pair(self, route, change)
            return
        payload = {'trigger': {**b.trigger_data, 'platform': b.platform_type,
            'entity_id': event.data['entity_id'], 'from_state': prior,
            'to_state': new, 'for': None, 'attribute': attribute,
            'description': f"state of {event.data['entity_id']}"}}
        self.hass.async_run_hass_job(b.action_job, payload, event.context)
        self.counts['action_submitted'] += 1

    def registrations(self, hass, entity):
        """Return explicit actual graph tokens; caller never unwraps job.target."""
        _need(hass is self.hass)
        if self.duration is not None:
            from .native_automation_duration_binding import NativeAutomationDurationCapability
            _need(type(self.duration) is NativeAutomationDurationCapability
                  and self.duration.hass is self.hass, 'typed_duration_capability_required')
        if self.duration is not None and any('for' in c for c in entity._trigger_config) and (not self.live or not any(r.binding.entity is entity for r in self.routes)):
            return self.duration.tokens(entity)
        _need(self.live)
        owned = tuple(route for route in self.routes if route.binding.entity is entity)
        _need(entity._is_enabled is True and entity.hass is hass)
        _need(len(owned) == len(entity._trigger_config) and owned,
              'automation_owned_trigger_inventory_changed')
        tokens = []
        for route in owned:
            b = route.binding
            b._native.check()
            _need(_stable(b) == route.stable, 'automation_owned_native_edges_changed')
            _need(_value(route.wrapper) == route.wrapper_graph, 'automation_owned_wrapper_graph_changed')
            _need(b.keyed_job.target is route.wrapper and b.config['entity_id'] is b.config_ids
                  and tuple(b.config_ids) == route.target
                  and entity._trigger_config[b.index] is b.config
                  and entity._async_detach_triggers is b.detach
                  and b.removes[b.index] is b.remove)
            rc = _closure(b.remove, b._native.state.__name__, 'async_attach_trigger.<locals>.async_remove')
            if hasattr(b, '_duration'):
                self.duration.check_route(route)
            _need(rc['unsub'] is b.unsubscribe and (hasattr(b, '_duration') or not rc['unsub_track_same'])
                  and b.unsubscribe.func is b._native.event._remove_listener
                  and not b.unsubscribe.keywords
                  and all(actual is expected for actual, expected in zip(b.unsubscribe.args,
                      (hass, b.tracker, b.original_keys, b.keyed_job, b.callbacks), strict=True)))
            _need(b.data is hass.data.get(b.tracker.key) and b.data.callbacks is b.callbacks
                  and b.data.listener is b.bus_remove)
            entry, bus_token = _registered_bus_remove(hass, b.bus_remove, b.tracker.event_type)
            _need(entry is b.bus_entry)
            (bus_job, event_filter) = entry
            for function, expected in ((bus_job.target, b.tracker.dispatcher_callable),
                                       (event_filter, b.tracker.filter_callable)):
                _need(type(function) is partial and function.func is expected
                      and not function.keywords and len(function.args) == 2
                      and function.args[0] is hass and function.args[1] is b.callbacks)
            keys = tuple(dict.fromkeys((*route.before, *route.target))) if self.overlap else route.target
            _need(tuple(b.original_keys) == keys)
            _need(all(key in b.callbacks and sum(job is b.keyed_job for job in b.callbacks[key]) == 1
                      for key in keys), 'automation_owned_route_missing')
            for key, members in b.callbacks.items():
                _need(type(members) is list and sum(job is b.keyed_job for job in members)
                      == (1 if key in keys else 0), 'automation_owned_route_changed')
            tokens.append((id(b.entity), id(b.keyed_job), id(route.wrapper), id(b.listener),
                id(b.action_job), id(b.unsubscribe), tuple((key, id(b.callbacks[key])) for key in keys),
                bus_token))
        return tuple(tokens)

    def owns(self, entity):
        return (self.live and any(route.binding.entity is entity for route in self.routes)
                or self.duration is not None and entity._is_enabled is True
                and any('for' in c for c in entity._trigger_config))

    def check(self):
        for entity in {route.binding.entity for route in self.routes}:
            self.registrations(self.hass, entity)

    def pending(self):
        return any(route.queue or route.handle is not None or route.duration_dispatch is not None
                   for route in self.routes)

    def retire(self, *, before_attempt=False):
        if not self.live:
            self.routes.clear()
            return
        witness = self.witness
        _need(not self.pending() or before_attempt or witness.error is not None,
              'automation_ingress_delivery_pending')
        # Validate EVERY retained edge before changing any one of them. In
        # particular, never iterate a foreign replacement/extended keys list as
        # our cleanup plan. Partial installation has its own exact write log.
        plans, shadow = [], {}
        for route in reversed(self.routes):
            b = route.binding
            b._native.check()
            _need(_stable(b) == route.stable and b.entity._is_enabled is True
                  and b.entity._async_detach_triggers is b.detach
                  and b.entity._trigger_config[b.index] is b.config
                  and b.config['entity_id'] is b.config_ids,
                  'automation_retire_native_owner_changed')
            expected = tuple(dict.fromkeys((*route.before, *route.added_keys)))
            keys = expected if route.keys_written else route.before
            _need(tuple(b.original_keys) == keys
                  and b.keyed_job.target is (route.wrapper if route.wrapper_written else b.listener),
                  'automation_retire_owned_slots_changed')
            if route.wrapper_written:
                _need(_value(route.wrapper) == route.wrapper_graph,
                      'automation_retire_wrapper_changed')
            _need(b.data is self.hass.data.get(b.tracker.key)
                  and b.data.callbacks is b.callbacks and b.data.listener is b.bus_remove,
                  'automation_retire_callback_owner_changed')
            rc = _closure(b.remove, b._native.state.__name__, 'async_attach_trigger.<locals>.async_remove')
            _need(rc['unsub'] is b.unsubscribe and (hasattr(b, '_duration') or not rc['unsub_track_same'])
                  and b.unsubscribe.func is b._native.event._remove_listener
                  and not b.unsubscribe.keywords
                  and all(actual is retained for actual, retained in zip(b.unsubscribe.args,
                      (self.hass, b.tracker, b.original_keys, b.keyed_job, b.callbacks), strict=True)),
                  'automation_retire_native_cleanup_changed')
            entry, _ = _registered_bus_remove(self.hass, b.bus_remove, b.tracker.event_type)
            _need(entry is b.bus_entry, 'automation_retire_bus_owner_changed')
            _need(all(type(members) is list and sum(job is b.keyed_job for job in members)
                      == (1 if key in expected else 0) for key, members in b.callbacks.items())
                  and all(key in b.callbacks for key in expected),
                  'automation_retire_route_membership_changed')
            duration_plan = (self.duration.validate_retire(route, before_attempt,shadow)
                             if hasattr(b, '_duration') else None)
            target=route.before if before_attempt else route.target
            plans.append((route, expected,target,duration_plan))
            if before_attempt:
                # Later routes share callbacks with earlier routes. Project
                # their main-route cleanup too before validating earlier logs.
                slot=('dict',id(b.callbacks))
                callbacks=shadow.get(slot,tuple(b.callbacks.items()))
                for key in expected:
                    if key in target:
                        continue
                    members=next(v for k,v in callbacks if k==key)
                    member_slot=('list',id(members))
                    current=shadow.get(member_slot,tuple(members))
                    projected=tuple(j for j in current if j is not b.keyed_job)
                    shadow[member_slot]=projected
                    if not projected:
                        callbacks=tuple((k,v) for k,v in callbacks if k!=key)
                shadow[slot]=callbacks
        for route, expected, target, duration_plan in plans:
            b = route.binding
            if hasattr(b, '_duration'):
                self.duration.retire(route, before_attempt, duration_plan)
            if route.handle is not None:
                route.handle.cancel()  # only this OWN handle, never native ready work
            for key in expected:
                if key in target:
                    continue
                members = b.callbacks.get(key)
                members.remove(b.keyed_job)
                if not members:
                    b.callbacks.pop(key)
            b.original_keys[:] = target
            b.keyed_job.target = b.listener
        self.live, self.overlap = False, False
        self.routes.clear()
