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
              and not witness.attempted and transport.phase == 'installed',
              'template_ingress_owned_transport_required')
        transport._check('registry_attempt', caller_context)
        transport.runtime.sources()
        obj = cls()
        obj.transport, obj.witness = transport, witness
        obj.owner, obj.rows, obj.live = asyncio.current_task(), [], True
        obj.suppressed = 0
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

    def _jobs(self, filtered, expected):
        """Find exact current own jobs; native domain add remains untouched."""
        from homeassistant.core import HassJob
        event = self.transport.runtime.event
        hass = self.transport.hass
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
        _need(all(type(job) is HassJob and same(job.target) for job in jobs),
              'template_ingress_job_target_changed')
        return tuple({id(job): job for job in jobs}.values())

    def retire(self):
        """No request auth needed to undo only exact still-owned wrappers."""
        if not self.live:
            return
        _need(asyncio.current_task() is self.owner, 'template_ingress_owner_changed')
        for filtered, action, wrapper, original_jobs in reversed(self.rows):
            _need(filtered._action is wrapper, 'template_ingress_action_changed')
            current_jobs = self._jobs(filtered, wrapper)
            _need(all(job.target is wrapper for job in original_jobs),
                  'template_ingress_original_job_changed')
            filtered._action = action
            for job in (*original_jobs, *current_jobs):
                job.target = action
        self.live = False
