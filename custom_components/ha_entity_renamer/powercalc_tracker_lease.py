"""Own one native tracker sentinel during a Powercalc unload.

The sentinel keeps the issued native dispatcher nonempty while native removers
run. If hass.data is rebound, cleanup touches only our issued job and the exact
original dispatcher; it never calls the old native remover on the new manager.
"""
from __future__ import annotations

from functools import partial

from .yaml_runtime_observer import _registered_bus_remove


class PowercalcTrackerLeaseError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise PowercalcTrackerLeaseError('powercalc_tracker_lease_' + reason) from None


class PowercalcTrackerLease:
    def __init__(self, hass, entity_id):
        from homeassistant.core import callback, HassJob, HassJobType
        from homeassistant.helpers import event
        self.hass, self.event, self.entity_id = hass, event, entity_id
        self.issued = None
        self.closed = False
        data = hass.data.get(event._TRACK_STATE_CHANGE_DATA)
        _need(type(data) is event._KeyedEventData, 'native_tracker_required')
        (bus_job, event_filter), _ = _registered_bus_remove(hass, data.listener, 'state_changed')
        for actual, expected in ((bus_job.target, event._async_dispatch_entity_id_event),
                                 (event_filter, event._async_state_filter)):
            _need(type(actual) is partial and actual.func is expected and not actual.keywords
                  and actual.args == (hass, data.callbacks), 'native_dispatcher_required')
        @callback
        def sentinel(_event):
            return None
        remove = event.async_track_state_change_event(hass, entity_id, sentinel)
        _need(type(remove) is partial and remove.func is event._remove_listener and not remove.keywords
              and len(remove.args) == 5 and remove.args[0] is hass
              and remove.args[1] is event._KEYED_TRACK_STATE_CHANGE
              and set(remove.args[2]) == {entity_id}, 'native_remover_required')
        job, callbacks = remove.args[3:]
        # Once issuance is known, every later failure must retain cleanup data.
        self.issued = (remove, job, callbacks, data, data.listener, bus_job, event_filter, sentinel)
        try:
            _need(type(job) is HassJob and job.job_type is HassJobType.Callback
                  and job.target is sentinel and callbacks is data.callbacks
                  and sum(item is job for item in callbacks.get(entity_id, ())) == 1
                  and hass.data.get(event._TRACK_STATE_CHANGE_DATA) is data,
                  'native_job_required')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        _need(self.issued is not None, 'issuance_required')
        remove, job, callbacks, original, listener, bus_job, event_filter, sentinel = self.issued
        members = callbacks.get(self.entity_id, ())
        _need(sum(item is job for item in members) == 1 and job.target is sentinel,
              'issued_job_changed')
        intact = (self.hass.data.get(self.event._TRACK_STATE_CHANGE_DATA) is original
                  and original.callbacks is callbacks and original.listener is listener)
        if intact:
            try:
                (actual_job, actual_filter), _ = _registered_bus_remove(self.hass, listener, 'state_changed')
                intact = actual_job is bus_job and actual_filter is event_filter
            except (ValueError, TypeError, KeyError, AttributeError):
                intact = False
        if intact:
            remove()
        else:
            members.remove(job)
            if not members:
                del callbacks[self.entity_id]
            if not callbacks:
                (actual_job, actual_filter), _ = _registered_bus_remove(self.hass, listener, 'state_changed')
                _need(actual_job is bus_job and actual_filter is event_filter, 'original_dispatcher_changed')
                listener()
        self.closed = True
        _need(not any(item is job for rows in callbacks.values() for item in rows), 'issued_job_left')
        _need(intact, 'tracker_rebound_during_unload')
