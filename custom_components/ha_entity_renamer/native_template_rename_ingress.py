"""Temporary ingress for the transport's own newly installed native trackers.

Never replace domain bookkeeping, native queue/dispatch, Event or State. Only
the downstream render action is wrapped until the exact rename dispatch drains.
This component does not admit explicit pending timers or authorize root apply.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from functools import partial
from types import MethodType

from .native_template_binding_transport import NativeTemplateBindingTransport
from .native_rename_emission_classifier import NativeRenameEmissions
from .native_template_consumers import _bound, _need
from .yaml_runtime_observer import _registered_bus_remove


class NativeTemplateRenameIngress:
    @classmethod
    def install(cls, transport, witness, caller_context):
        from homeassistant.core import callback
        _need(type(transport) is NativeTemplateBindingTransport
              and type(witness) is NativeRenameEmissions
              and witness.hass is transport.hass and not witness.closed
              and not witness.attempted and transport.phase in ('installed', 'registry_attempted'),
              'template_ingress_owned_transport_required')
        transport._check('registry_attempt', caller_context)
        transport.runtime.sources()
        obj = cls()
        obj.transport, obj.witness = transport, witness
        obj.owner, obj.rows, obj.live = asyncio.current_task(), [], True
        obj.suppressed = 0
        obj.deferred, obj.deferred_count = [], 0
        _need(witness.ingress is None, 'template_ingress_already_installed')
        witness.ingress = obj
        try:
            for row in transport.rows:
                tracker = row['new']
                _need(row['entity']._template_result_info is tracker
                      and transport.runtime.graph(row['entity']) is tracker,
                      'template_ingress_new_tracker_required')
                filtered = tracker._track_state_changes
                action = filtered._action
                _need(_bound(action, tracker, transport.runtime.event.TrackTemplateResultInfo._refresh),
                      'template_ingress_native_action_required')

                def make_action(original):
                    @callback
                    def ingress(event):
                        if obj.live and witness.suppresses_template_boundary(event):
                            obj.suppressed += 1
                            return
                        if (obj.live and witness.source_handoff is not None
                                and witness.is_owned_rename(event) and event.data['new_state'] is not None):
                            # New ADD precedes old namespace removal. Deliver
                            # this original event exactly once after the sync
                            # handoff, so broad native queries cannot count both
                            # IDs. This is our own queued dispatch, not a native
                            # Handle execution, fabricated Event or replay.
                            token = [original, event, None]
                            obj.deferred.append(token)
                            obj.deferred_count += 1
                            token[2] = transport.hass.loop.call_soon(obj._deliver, token)
                            return
                        original(event)
                    return ingress

                wrapper = make_action(action)
                jobs = obj._jobs(filtered, action)
                owned = (filtered, action, wrapper, jobs)
                obj.rows.append(owned)
                filtered._action = wrapper
                for job in jobs:
                    job.target = wrapper
            return obj
        except BaseException:
            obj.retire()
            raise

    def _deliver(self, token):
        try:
            original, event, _ = token
            _need(self.live and any(item is token for item in self.deferred)
                  and self.witness.is_owned_rename(event), 'template_ingress_deferred_event_changed')
            h = self.witness.source_handoff
            _need(h is not None and h.attempted and h.old_retired and h.error is None
                  and self.transport.hass.states.get(h.old) is None,
                  'template_ingress_handoff_not_retired')
            matching = [(filtered, action, wrapper) for filtered, action, wrapper, _ in self.rows
                        if action is original]
            _need(len(matching) == 1, 'template_ingress_deferred_action_changed')
            filtered, action, wrapper = matching[0]
            tracker = action.__self__
            _need(filtered._action is wrapper and any(
                row['new'] is tracker and row['entity']._template_result_info is tracker
                and self.transport.runtime.graph(row['entity']) is tracker for row in self.transport.rows),
                'template_ingress_deferred_tracker_changed')
            self._jobs(filtered, wrapper)
            original(event)
        except BaseException:
            self.witness._poison('deferred_template_delivery_uncertain')
        finally:
            self.deferred[:] = [item for item in self.deferred if item is not token]
            self.witness._signal()

    def _jobs(self, filtered, expected):
        """Find exact current own jobs; native domain add remains untouched."""
        return self.native_jobs(self.transport.hass, self.transport.runtime.event, filtered, expected)

    @staticmethod
    def native_jobs(hass, event, filtered, expected):
        from homeassistant.core import HassJob, HassJobType
        jobs = [filtered._action_as_hassjob]
        _need(type(filtered._listeners) is dict and len(filtered._listeners) <= 3,
              'template_ingress_listener_inventory_changed')
        for kind, remove in filtered._listeners.items():
            if kind == event._ALL_LISTENER:
                (job, event_filter), _ = _registered_bus_remove(hass, remove, 'state_changed')
                _need(event_filter is None, 'template_ingress_foreign_all_filter')
                jobs.append(job)
            elif kind in (event._ENTITIES_LISTENER, event._DOMAINS_LISTENER):
                _need(type(remove) is partial and remove.func is event._remove_listener
                      and not remove.keywords and len(remove.args) == 5,
                      'template_ingress_keyed_remove_changed')
                owner, tracker, keys, job, callbacks = remove.args
                expected_tracker = (event._KEYED_TRACK_STATE_CHANGE if kind == event._ENTITIES_LISTENER
                                    else event._KEYED_TRACK_STATE_ADDED_DOMAIN)
                data = hass.data.get(expected_tracker.key)
                _need(owner is hass and tracker is expected_tracker and type(job) is HassJob
                      and type(data) is event._KeyedEventData and data.callbacks is callbacks
                      and type(callbacks) is defaultdict and callbacks.default_factory is list
                      and type(keys) in (tuple, list, set, frozenset) and len(keys) <= 10000
                      and all(type(key) is str for key in keys)
                      and all(sum(item is job for item in callbacks.get(key, ())) == 1 for key in keys),
                      'template_ingress_keyed_owner_changed')
                if kind == event._DOMAINS_LISTENER:
                    _need(_bound(job.target, filtered, event._TrackStateChangeFiltered._state_added),
                          'template_ingress_domain_bookkeeping_changed')
                else:
                    jobs.append(job)
            else:
                _need(False, 'template_ingress_foreign_listener')
        def same(actual):
            return (actual is expected or (type(actual) is type(expected) is MethodType
                    and actual.__self__ is expected.__self__ and actual.__func__ is expected.__func__))
        _need(all(type(job) is HassJob and job.job_type is HassJobType.Callback and same(job.target)
                  for job in jobs),
              'template_ingress_job_target_changed')
        return tuple({id(job): job for job in jobs}.values())

    def retire(self):
        """No request auth needed to undo only exact still-owned wrappers."""
        if not self.live:
            return
        _need(asyncio.current_task() is self.owner, 'template_ingress_owner_changed')
        if self.deferred:
            _need(self.witness.error is not None, 'template_ingress_deferred_delivery_pending')
            # Terminal uncertain cleanup cancels only handles created by this
            # adapter, never an integration/native producer or its timer.
            for _, _, handle in self.deferred:
                handle.cancel()
            self.deferred.clear()
        for filtered, action, wrapper, original_jobs in reversed(self.rows):
            _need(filtered._action is wrapper, 'template_ingress_action_changed')
            current_jobs = self._jobs(filtered, wrapper)
            _need(all(job.target is wrapper for job in original_jobs),
                  'template_ingress_original_job_changed')
            filtered._action = action
            for job in (*original_jobs, *current_jobs):
                job.target = action
        self.live = False
        if self.witness.ingress is self:
            self.witness.ingress = None


class NativeTemplateProducerDelivery:
    """Deliver already queued ordinary events to the OLD native jobs first.

    Only pinned native keyed returns wake this fence. The synchronous final
    clear check must precede transport and API without another await.
    """
    def __init__(self, runtime, entities, witness):
        _need(type(witness) is NativeRenameEmissions and witness.preflight is None
              and not witness.attempted and not witness.closed
              and runtime.native.hass is witness.hass, 'template_delivery_witness_required')
        self.runtime, self.witness = runtime, witness
        self.jobs, self.running, self.live = [], set(), True
        for entity in entities:
            tracker = runtime.graph(entity)
            filtered = tracker._track_state_changes
            self.jobs.extend(NativeTemplateRenameIngress.native_jobs(witness.hass, runtime.event,
                filtered, filtered._action))
        witness.preflight = self

    def _routes(self, callbacks, event):
        from homeassistant.core import Event
        if type(event) is not Event or event.event_type != 'state_changed':
            return False
        _need(type(callbacks) is defaultdict and callbacks.default_factory is list,
              'template_delivery_callback_map_changed')
        members = callbacks.get(event.data.get('entity_id'), ())
        return any(any(job is member for job in self.jobs) for member in members)

    def started(self, frame):
        values = frame.f_locals
        if (self.live and frame.f_code is self.witness.codes['keyed']
                and values.get('hass') is self.witness.hass
                and self._routes(values.get('callbacks'), values.get('event'))):
            _need(len(self.running) < 32, 'template_delivery_dispatch_overflow')
            self.running.add(id(frame))

    def ended(self, frame, failed):
        if id(frame) in self.running:
            self.running.remove(id(frame))
            if failed:
                self.witness._poison('producer_dispatch_unwound')
            self.witness._signal()

    def pending(self):
        count = 0
        for handle in self.witness.loop._ready:
            if handle.cancelled() or handle._callback is not self.witness.methods['keyed']:
                continue
            args = handle._args
            if (type(args) is tuple and len(args) == 3 and args[0] is self.witness.hass
                    and self._routes(args[1], args[2])):
                count += 1
                _need(count <= 32, 'template_delivery_ready_overflow')
        return bool(count or self.running)

    async def drain(self):
        while True:
            self.witness._owner_guard()
            self.witness._check()
            self.witness.wake = self.witness.loop.create_future()
            if not self.pending():
                return
            await self.witness.wake

    def retire_at_final_fence(self):
        self.witness._owner_guard()
        self.witness._check()
        _need(self.live and not self.pending(), 'template_delivery_new_pending_event')
        self.live = False
