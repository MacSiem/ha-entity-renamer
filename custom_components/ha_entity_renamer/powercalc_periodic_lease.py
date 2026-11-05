"""Temporarily defer the exact native PSS interval during one live migration.

No writer is replaced and no periodic or shutdown listener is fabricated. The
same vendor/Core timer is restored with its original deadline and context after
all migration observers have closed. Unexpected native timer operations refuse.
"""
from __future__ import annotations

import asyncio
import math
import sys
from importlib import import_module
import types

from .native_unload_code_proof import prove_methods, unchanged
from .powercalc_runtime_observer import _guard
from .powercalc_source_binding import _pins
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_periodic_' + reason)


class _PeriodicPreparation:
    def __init__(self, hass, previous_cls, refs, vrefs, proof):
        self.hass, self.previous_cls = hass, previous_cls
        self.refs, self.vrefs, self.proof = refs, vrefs, proof


class PowercalcPeriodicLease:
    def __init__(self):
        raise Refused('powercalc_periodic_capture_required')

    @classmethod
    async def prepare(cls, lease, *, guard):
        from homeassistant.helpers import event
        from .powercalc_activation_lease import PowercalcActivationLease
        from .powercalc_source_binding import PowercalcVendor
        from .yaml_validation import _settled_executor
        _need(cls is PowercalcPeriodicLease and type(lease) is PowercalcActivationLease
              and callable(guard), 'live_lease_required')
        lease._task_check()
        lease.check()
        hass = lease.hass
        vendor = PowercalcVendor(hass, with_profiles=True)
        previous_cls = vendor.classes['PreviousStateStore']
        refs = ((event._TrackTimeInterval, '_schedule_timer'),
                (event._TrackTimeInterval, '_interval_listener'),
                (event._TrackTimeInterval, 'async_cancel'))
        vrefs = ((previous_cls, 'async_setup_dump'),)
        def prove():
            vendor.sources()
            return (prove_methods(vendor.ha_root, vendor.ha_pins, refs),
                    prove_methods(vendor.root, _pins(), vrefs))
        proof = await _settled_executor(hass, prove)
        _guard(guard)
        lease._task_check()
        lease.check()
        return _PeriodicPreparation(hass, previous_cls, refs, vrefs, proof)

    @classmethod
    def acquire(cls, phase, preparation, *, guard):
        from homeassistant.core import CoreState
        from homeassistant.helpers import event
        from .powercalc_phase_session import PowercalcPhaseSession
        _need(cls is PowercalcPeriodicLease and type(phase) is PowercalcPhaseSession
              and type(preparation) is _PeriodicPreparation and callable(guard),
              'live_phase_required')
        phase.check()
        scope, hass = phase.scope, phase.lease.hass
        previous_cls = scope.vendor.classes['PreviousStateStore']
        refs, vrefs, proof = preparation.refs, preparation.vrefs, preparation.proof
        _need(preparation.hass is hass and preparation.previous_cls is previous_cls,
              'prepared_native_binding_changed')
        unchanged(refs, *proof[0])
        unchanged(vrefs, *proof[1])
        _need(hass.state is CoreState.running and scope.previous_store.hass is hass,
              'running_store_required')
        method = previous_cls.async_setup_dump
        codes = [code for code in method.__code__.co_consts
                 if type(code) is types.CodeType and code.co_name == '_async_dump_states']
        _need(len(codes) == 1, 'native_dump_code_required')
        code = codes[0]
        trackers = []
        # A due TimerHandle may already have moved from _scheduled to _ready.
        for handle in tuple(hass.loop._scheduled) + tuple(hass.loop._ready):
            callback = getattr(handle, '_callback', None)
            tracker = getattr(callback, '__self__', None)
            if type(tracker) is not event._TrackTimeInterval or tracker.hass is not hass:
                continue
            action = tracker.action
            if getattr(action, '__code__', None) is not code:
                continue
            cells = dict(zip(action.__code__.co_freevars,
                             (cell.cell_contents for cell in action.__closure__ or ()), strict=True))
            if cells.get('self') is not scope.previous_store:
                continue
            if tracker._timer_handle is handle and not handle.cancelled():
                trackers.append((tracker, handle))
        _need(len(trackers) == 1, 'one_native_interval_required')
        tracker, handle = trackers[0]
        action = tracker.action
        group_module = import_module(previous_cls.__module__)
        interval = getattr(group_module, 'STATE_DUMP_INTERVAL', None)
        _need(type(handle) is asyncio.TimerHandle and handle._loop is hass.loop
              and type(tracker.seconds) is float and math.isfinite(tracker.seconds)
              and interval is not None and tracker.seconds == interval.total_seconds()
              and tracker.seconds > 0 and tracker.cancel_on_shutdown is True
              and tracker._run_job is not None
              and tracker._run_job.target is action and tracker._track_job is not None
              and tracker._track_job.target == tracker._interval_listener
              and getattr(handle._callback, '__self__', None) is tracker
              and getattr(handle._callback, '__func__', None) is event._TrackTimeInterval._interval_listener
              and handle._args == (tracker._track_job,) and math.isfinite(handle.when()),
              'native_interval_binding_changed')
        store = scope.previous_store.store
        _need(not store._write_lock.locked() and store._data is None and store._delay_handle is None,
              'native_store_writer_busy')
        for task in asyncio.all_tasks(hass.loop):
            coro = task.get_coro()
            if (not task.done() and getattr(coro, 'cr_code', None) is code
                    and getattr(coro, 'cr_frame', None) is not None
                    and coro.cr_frame.f_locals.get('self') is scope.previous_store):
                raise Refused('powercalc_periodic_dump_in_flight')
        obj = object.__new__(cls)
        obj.phase, obj.scope, obj.hass, obj.tracker = phase, scope, hass, tracker
        obj.handle, obj.action, obj.store = handle, action, scope.previous_store
        obj.callback, obj.args, obj.context = handle._callback, handle._args, handle._context.copy()
        obj.deadline, obj.refs, obj.vrefs, obj.proof = handle.when(), refs, vrefs, proof
        obj.owner, obj.loop, obj.poisoned, obj.closed = asyncio.current_task(), hass.loop, False, False
        obj.tool = None
        free = [key for key in range(6) if sys.monitoring.get_tool(key) is None]
        _need(bool(free), 'monitor_unavailable')
        obj.tool = free[0]
        monitor = sys.monitoring
        try:
            monitor.use_tool_id(obj.tool, 'renamer.powercalc_periodic')
            def started(frame_code, offset):
                frame = sys._getframe(1)
                if frame.f_locals.get('self') is obj.tracker:
                    obj.poisoned = True
            monitor.register_callback(obj.tool, monitor.events.PY_START, started)
            for native_code in proof[0][1]:
                monitor.set_local_events(obj.tool, native_code, monitor.events.PY_START)
            # No await between the in-flight census and this exact native timer.
            _guard(guard)
            phase.check()
            _need(tracker._timer_handle is handle and not handle.cancelled(), 'interval_changed')
            handle.cancel()
            obj.check()
            return obj
        except BaseException as primary:
            cleanup_ok = True
            if obj.tool is not None and monitor.get_tool(obj.tool) == 'renamer.powercalc_periodic':
                try:
                    obj._close_monitor()
                except BaseException as error:
                    cleanup_ok = False
                    primary.add_note('periodic monitor cleanup failed: ' + type(error).__name__)
            elif handle.cancelled():
                cleanup_ok = False
            # No foreign timer operation may be undone during cleanup.
            if cleanup_ok and tracker._timer_handle is handle and handle.cancelled() and not obj.poisoned:
                try:
                    tracker._timer_handle = hass.loop.call_at(obj.deadline, obj.callback,
                                                              *obj.args, context=obj.context)
                except BaseException as error:
                    primary.add_note('periodic timer restore failed: ' + type(error).__name__)
            raise

    def _close_monitor(self):
        monitor = sys.monitoring
        _need(monitor.get_tool(self.tool) == 'renamer.powercalc_periodic', 'monitor_owner_changed')
        first = None
        def attempt(function):
            nonlocal first
            try:
                function()
            except BaseException as error:
                if first is None:
                    first = error
        for code in self.proof[0][1]:
            attempt(lambda code=code: monitor.set_local_events(self.tool, code, 0))
        attempt(lambda: monitor.register_callback(self.tool, monitor.events.PY_START, None))
        attempt(lambda: monitor.free_tool_id(self.tool))
        if first is not None:
            raise first

    def check(self):
        from homeassistant.core import CoreState
        monitor = sys.monitoring
        _need(not self.closed and not self.poisoned and self.owner is not None and not self.owner.done()
              and asyncio.get_running_loop() is self.loop and self.hass.loop is self.loop
              and self.hass.state is CoreState.running and self.phase.scope is self.scope
              and self.hass.data.get('powercalc_group_storage') is self.store
              and self.tracker.hass is self.hass and self.tracker.action is self.action
              and self.tracker._run_job.target is self.action
              and self.tracker._timer_handle is self.handle and self.handle.cancelled()
              and monitor.get_tool(self.tool) == 'renamer.powercalc_periodic'
              and monitor.get_events(self.tool) == 0
              and all(monitor.get_local_events(self.tool, code) == monitor.events.PY_START
                      for code in self.proof[0][1]), 'live_interval_changed')
        unchanged(self.refs, *self.proof[0])
        unchanged(self.vrefs, *self.proof[1])

    def release(self):
        """Restore this exact timer only if no foreign operation touched it."""
        from homeassistant.core import CoreState
        _need(asyncio.current_task() is self.owner and asyncio.get_running_loop() is self.loop
              and not self.closed, 'owner_release_required')
        self.closed = True
        monitor = sys.monitoring
        valid = (not self.poisoned and not self.scope.live and self.hass.state is CoreState.running
                 and self.tracker._timer_handle is self.handle and self.handle.cancelled()
                 and self.tracker.hass is self.hass and self.tracker.action is self.action
                 and self.tracker._run_job.target is self.action
                 and self.hass.data.get('powercalc_group_storage') is self.store
                 and monitor.get_tool(self.tool) == 'renamer.powercalc_periodic'
                 and monitor.get_events(self.tool) == 0
                 and all(monitor.get_local_events(self.tool, code) == monitor.events.PY_START
                         for code in self.proof[0][1]))
        if monitor.get_tool(self.tool) == 'renamer.powercalc_periodic':
            self._close_monitor()
        _need(valid, 'interval_release_uncertain')
        unchanged(self.refs, *self.proof[0])
        unchanged(self.vrefs, *self.proof[1])
        # A deadline elapsed during migration is deliberately scheduled in the
        # past, so Core runs the original callback immediately on the next turn.
        self.tracker._timer_handle = self.loop.call_at(self.deadline, self.callback,
                                                       *self.args, context=self.context)
