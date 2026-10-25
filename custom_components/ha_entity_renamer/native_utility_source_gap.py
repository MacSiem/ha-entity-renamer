"""Ephemeral BASIC Utility source handoff under the original live lifecycle.

A returned stamp describes live observations, never authenticates itself or
grants READY. Lifecycle's independent runtime verifier still owns acceptance.
No synthetic Event, native field/cache/count write, monkeypatch, or recovery.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import localcontext
import hashlib
from importlib import import_module
from pathlib import Path
import sys
import threading

from .helper_native import read_entry
from .helper_plan import snapshot_equal
from .native_helper_activation_lease import NativeHelperActivationLease
from .native_utility_setup_audit import (
    NativeUtilitySetupAudit, _installed, _code_objects, _SOURCES,
)
from .utility_meter_observers import (
    UtilityMeterRuntimeBaseline, _payload, _decimal, _number, _plain, _STATIC,
)

_EXTRA = {
    '2026.10.2': {
        'helpers/event.py': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269',
        'helpers/start.py': '75110df1b5adb7912417cd23e77cfaae5c949c1e89eb188bb5a6b2b1e5eb0bcc',
    },
    '2025.6.0': {
        'helpers/event.py': 'd5e21c38afd9eafe566724b8db34ea2e87eaa8d6dc10c1719a82f5d90d43260e',
        'helpers/start.py': '241ba81686e031ec4424f8002430530f1640544b5e80c53c2f6579c2785f3e81',
    },
}


class UtilitySourceGapError(ValueError):
    """Sanitized refusal; no automatic retry after an attempted handoff."""


def _need(condition, reason):
    if not condition:
        raise UtilitySourceGapError('utility_source_gap_' + reason) from None


def _qualified():
    version, classes, codes = _installed()  # Reuse existing source/native pins.
    core = import_module('homeassistant.core')
    root = Path(import_module('homeassistant').__file__).resolve().parent
    for relative, digest in _EXTRA[version].items():
        path = root / relative
        _need(path.resolve() == path and hashlib.sha256(path.read_bytes()).hexdigest()
              == digest, 'extra_source_pin')
    # Match every used additional method to the already pinned core source.
    path = root / 'core.py'
    raw = path.read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == _SOURCES[version]['core.py'], 'core_pin')
    compiled = tuple(_code_objects(compile(raw, str(path), 'exec',
                                         dont_inherit=True, optimize=sys.flags.optimize)))
    for key, cls, name in (
        ('source_set', core.StateMachine, 'async_set_internal'),
        ('source_remove', core.StateMachine, 'async_remove'),
        ('run_job', core.HomeAssistant, 'async_run_hass_job'),
    ):
        method = getattr(cls, name)
        code = method.__code__
        _need(method.__module__ == 'homeassistant.core'
              and any(code == item for item in compiled), 'extra_native_code')
        codes[key] = code
    return version, classes, codes


@dataclass(frozen=True)
class NativeUtilitySourceGapData:
    """Detached, non-authoritative description; process IDs are diagnostic only."""
    entry_id: str
    original_baseline_sha256: str
    physical_source: tuple[str | None, ...]
    source_entity_id: str
    source_value: str
    source_state_process_id: int
    source_last_updated: str
    capture_source: str
    restore_prefix: str
    counter_before: str
    counter_after: str
    native_object_process_id: int
    job_process_id: int
    event_process_id: int | None
    event_entity_id: str | None
    event_time_fired: str | None
    native_call: bool
    no_replay: bool
    original_a0: str
    original_s0: str
    restore_provenance: str = 'UNKNOWN: existing collector lacks restore-call witness'


class NativeUtilitySourceGap:
    """One live task/loop scope spanning unload, setup, and final fences.

    Source callbacks use the same loop/thread but need not run in the owner task.
    capture/settle/check/close are owner-task-only. Retains only the latest Event
    and State; stream validity is a constant-space monotonic fold with no cap.
    """

    @classmethod
    def capture(cls, hass, baseline: UtilityMeterRuntimeBaseline, snapshot,
                activation_lease, *, guard):
        _need(cls is NativeUtilitySourceGap and callable(guard), 'capture_binding')
        self = object.__new__(cls)
        self._hass, self._baseline = hass, baseline
        self._doc = _payload(baseline)  # ORIGINAL A0/S0 and prepared names.
        self._core, self._classes, self._codes = _qualified()
        self._guard, self._lease = guard, activation_lease
        self._task, self._loop = asyncio.current_task(), asyncio.get_running_loop()
        self._thread = threading.get_ident()
        self._live, self._error, self._settling = True, None, False
        self._attempted, self._latest_event, self._unsubscribe = False, None, None
        self._event_shape = None
        self._er = import_module('homeassistant.helpers.entity_registry')
        self._registry = self._er.async_get(hass)
        self._states, self._bus = hass.states, hass.bus
        self._entry = hass.config_entries.async_get_entry(baseline.entry_id)
        core = import_module('homeassistant.core')
        _need(type(hass) is core.HomeAssistant and type(self._states) is core.StateMachine
              and type(self._registry) is self._classes['EntityRegistry']
              and self._entry is not None and self._entry.state.value == 'loaded'
              and self._core == self._doc['core'] and self._task is not None
              and type(activation_lease) is NativeHelperActivationLease
              and type(hass.bus) is self._classes['EventBus'], 'installed_binding')
        self._check(snapshot)
        source_name = self._source_name()
        source = self._states.get(source_name)
        value = self._value(source)
        _need(value >= _decimal(self._doc['s0']), 'capture_before_original')
        self._attrs = self._attributes(source)
        self._initial, self._latest_value = value, value
        self._latest_state = source
        candidates = [entity for platform in import_module(
            'homeassistant.helpers.entity_platform').async_get_platforms(hass, 'utility_meter')
            if platform.config_entry is self._entry for entity in platform.entities.values()]
        _need(len(candidates) == 1, 'original_entity_count')
        self._old_entity = candidates[0]
        self._entity(self._old_entity, snapshot, source_name)
        self._output(self._old_entity, value)  # A0+(current-S0), never recapture A0.
        self._check(snapshot)
        self._outside_dispatch()
        core = import_module('homeassistant.core')
        @core.callback
        def observe(event):
            self._observe(event)
        self._callback = observe
        try:
            self._unsubscribe = self._bus.async_listen('state_changed', observe)
            self.check()
        except BaseException:
            self.close()
            raise
        return self

    def _poison(self, reason):
        if self._error is None:
            self._error = reason

    def _check(self, snapshot=None, *, owner=True):
        try:
            _need(self._live and self._error is None, self._error or 'closed')
            _need(threading.get_ident() == self._thread
                  and asyncio.get_running_loop() is self._loop
                  and (not owner or asyncio.current_task() is self._task)
                  and not self._task.done(), 'task_or_loop_lost')
            _need(self._guard() is True, 'guard_lost')
            self._lease.check()
            _need(self._lease.owns_entry(self._entry)
                  and self._hass.config_entries.async_get_entry(self._baseline.entry_id) is self._entry
                  and self._hass.states is self._states and self._hass.bus is self._bus
                  and self._hass.data.get(self._er.DATA_REGISTRY) is self._registry,
                  'native_binding_lost')
            if snapshot is not None:
                _need(any(snapshot_equal(snapshot, self._doc[k]) for k in ('before', 'after'))
                      and snapshot_equal(read_entry(self._hass, self._baseline.entry_id), snapshot),
                      'prepared_snapshot')
        except BaseException:
            self._poison('guard_or_binding_lost')
            raise

    def _source_name(self):
        rows = [row for row in self._registry.entities.values()
                if row.id == self._doc['source_identity'][0]]
        _need(len(rows) == 1, 'physical_source_count')
        row, = rows
        _need([getattr(row, key) for key in ('id', 'unique_id', 'platform',
              'config_entry_id', 'config_subentry_id')] == self._doc['source_identity']
              and row.entity_id in (self._doc['source_before'], self._doc['source_after'])
              and row.disabled_by is None, 'physical_source_changed')
        return row.entity_id

    def _attributes(self, state):
        return tuple(state.attributes.get(k) for k in
                     ('unit_of_measurement', 'device_class', 'state_class'))

    def _value(self, state):
        core = import_module('homeassistant.core')
        _need(type(state) is core.State and state.state not in ('unknown', 'unavailable')
              and self._attributes(state) == (self._doc['source_unit'],
                  self._doc['source_class'], 'total_increasing'), 'source_invalid')
        value = _decimal(state.state)
        _need(value >= 0, 'source_negative')
        return value

    def _expected(self, value):
        with localcontext() as context:
            context.prec = 384
            return _decimal(self._doc['a0']) + value - _decimal(self._doc['s0'])

    def _entity(self, entity, snapshot, source_name):
        _need(type(entity) is self._classes['UtilityMeterSensor']
              and entity.hass is self._hass and entity.unique_id == self._baseline.entry_id
              and entity._parent_meter == self._baseline.entry_id
              and entity._sensor_source_id == source_name and entity.available
              and entity.scheduler is None and callable(entity._collecting)
              and {k: _plain(getattr(entity, k)) for k in _STATIC} == self._doc['static'],
              'native_class_mode_static')
        row = entity.registry_entry
        _need(row is not None and row.id == self._doc['helper_rows'][0]['id']
              and row.config_entry_id == self._baseline.entry_id
              and row.entity_id == entity.entity_id, 'helper_physical_identity')
        _need(self._er.async_validate_entity_id(self._registry,
              snapshot['options']['source']) == source_name, 'configured_source')

    def _output(self, entity, value):
        expected = self._expected(value)
        state = self._states.get(entity.entity_id)
        _need(_decimal(entity.native_value) == expected and entity._last_valid_state == value
              and state is not None and _decimal(state.state) == expected
              and state.attributes.get('unit_of_measurement') == self._doc['source_unit']
              and state.attributes.get('status') == 'collecting'
              and _decimal(state.attributes.get('last_valid_state')) == value
              and _number(state.attributes.get('last_period')) == self._doc['static']['_last_period']
              and state.attributes.get('last_reset') == self._doc['static']['_last_reset'],
              'native_public_counter_incoherent')

    def _outside_dispatch(self):
        frame = sys._getframe(1)
        try:
            while frame is not None:
                _need(frame.f_code not in (self._codes['fire'], self._codes.get('dispatch'),
                                          self._codes['utility_reading']), 'reentrant_dispatch')
                frame = frame.f_back
        finally:
            del frame

    def _genuine_event(self, event):
        # Actual native StateMachine -> same bus dispatch -> same Event. No
        # public-bus-only attestation. A deferred/nested 2026 emission lacking
        # its originating StateMachine frame is UNKNOWN and refuses safely.
        core = import_module('homeassistant.core')
        _need(type(event) is core.Event and event.event_type == 'state_changed', 'event_type')
        source_frame = bus_frame = None
        frame = sys._getframe(1)
        try:
            while frame is not None:
                if (frame.f_code in (self._codes['source_set'], self._codes['source_remove'])
                        and frame.f_locals.get('self') is self._states
                        and frame.f_locals.get('state_changed_data') is event.data):
                    source_frame = frame
                if (frame.f_code is self._codes.get('dispatch', self._codes['fire'])
                        and frame.f_locals.get('self') is self._bus
                        and frame.f_locals.get('event') is event
                        and frame.f_locals.get('event_data') is event.data):
                    bus_frame = frame
                frame = frame.f_back
            _need(source_frame is not None and bus_frame is not None, 'UNKNOWN_source_event_origin')
        finally:
            del frame, source_frame, bus_frame

    @staticmethod
    def _shape(event):
        return (id(event.data), event.data.get('entity_id'),
                id(event.data.get('old_state')), id(event.data.get('new_state')),
                event.event_type, event.time_fired.isoformat())

    def _observe(self, event):
        # Never throw into HA, including guard cancellation or malformed events.
        try:
            name = event.data.get('entity_id')
            if name not in (self._doc['source_before'], self._doc['source_after']):
                return
            self._check(owner=False)
            _need(not self._settling, 'source_changed_during_handoff')
            self._genuine_event(event)
            physical_name = self._source_name()
            new, old = event.data.get('new_state'), event.data.get('old_state')
            if new is None:
                # Native rename may remove the old State after registry moved.
                # This is transport only; removal of the physical current name
                # or removal outside the prepared pair remains unsupported.
                _need(name == self._doc['source_before'] and name != physical_name
                      and physical_name == self._doc['source_after'] and old is not None
                      and self._value(old) <= self._latest_value, 'source_removed')
                return
            value = self._value(new)
            _need(name == physical_name and new.entity_id == name
                  and self._states.get(name) is new and value >= self._latest_value,
                  'source_reset_or_head')
            if self._latest_state.entity_id == name:
                _need(old is self._latest_state, 'source_stream_discontinuity')
            else:
                _need(self._latest_state.entity_id == self._doc['source_before']
                      and name == self._doc['source_after'] and old is None,
                      'source_move_unproven')
            self._latest_event, self._latest_state, self._latest_value = event, new, value
            self._event_shape = self._shape(event)
        except BaseException:
            self._poison('source_monitor_failed_or_unsupported')

    def check(self):
        """Recheck through later awaits, flush, result write; never accounts."""
        self._check()
        name = self._source_name()
        current = self._states.get(name)
        _need(current is self._latest_state and self._value(current) == self._latest_value,
              'unobserved_source_head')
        _need(self._latest_event is None or self._shape(self._latest_event) == self._event_shape,
              'event_changed')

    def _job(self, collector, snapshot):
        _need(type(collector) is NativeUtilitySetupAudit
              and collector._hass is self._hass and collector._baseline is self._baseline
              and snapshot_equal(collector._snapshot, snapshot)
              and collector._active and not collector._sealed, 'collector_binding')
        collector._check()
        collector._bind_source()
        entity, platform = collector._new_entity, collector._platform
        _need(entity is not None and entity is not self._old_entity
              and entity.platform is platform and platform.config_entry is self._entry
              and platform.hass is self._hass and list(platform.entities.values()) == [entity]
              and self._entry.state.value == 'loaded' and self._old_entity._collecting is None,
              'actual_new_platform_entity')
        name = self._source_name()
        self._entity(entity, snapshot, name)
        helper = import_module('homeassistant.helpers.event')
        tracker = self._hass.data.get(helper._TRACK_STATE_CHANGE_DATA)
        _need(tracker is not None, 'native_tracker_missing')
        jobs = [(source, job) for source, values in tracker.callbacks.items() for job in values
                if getattr(getattr(job.target, '__self__', None), '_parent_meter', None)
                == self._baseline.entry_id]
        _need(len(jobs) == 1 and jobs[0][0] == name, 'native_job_count_or_source')
        job = jobs[0][1]
        core = import_module('homeassistant.core')
        _need(type(job) is core.HassJob and job.job_type is core.HassJobType.Callback
              and getattr(job.target, '__self__', None) is entity
              and getattr(job.target, '__func__', None) is self._classes['UtilityMeterSensor'].async_reading
              and job.target.__func__.__code__ is self._codes['utility_reading'], 'native_job_identity')
        return entity, platform, job

    def settle(self, snapshot, collector: NativeUtilitySetupAudit) -> NativeUtilitySourceGapData:
        """Synchronous decision -> existing native job -> independent readback."""
        _need(not self._settling, 'handoff_reentered')
        self._check(snapshot)
        self._outside_dispatch()
        self.check()
        entity, platform, job = self._job(collector, snapshot)
        source, event, value = self._latest_state, self._latest_event, self._latest_value
        prefix, before = _decimal(entity._last_valid_state), _decimal(entity.native_value)
        coherent = prefix == value and before == self._expected(value)
        called = False
        self._settling = True
        try:
            if not coherent:
                _need(not self._attempted and event is not None
                      and event.data.get('new_state') is source
                      and event.data.get('entity_id') == self._source_name()
                      and self._initial <= prefix <= value and before == self._expected(prefix),
                      'stale_without_legal_prefix_and_current_event')
                self._attempted = True  # Unknown outcome is never retried.
                _need(self._hass.async_run_hass_job.__func__.__code__ is self._codes['run_job'],
                      'native_job_runner_changed')
                result = self._hass.async_run_hass_job(job, event)  # SAME authentic Event.
                called = True
                _need(result is None, 'native_callback_not_synchronous')
            self._check(snapshot)
            collector._check()
            self.check()
            _need(self._latest_state is source and self._latest_event is event
                  and self._latest_value == value
                  and self._job(collector, snapshot) == (entity, platform, job), 'handoff_head_changed')
            self._output(entity, value)
            # No value/style trust in this detached stamp; owner authenticates the
            # live witness plus native restore/reading cause in SAME setup receipt.
            return NativeUtilitySourceGapData(
                self._baseline.entry_id, hashlib.sha256(self._baseline.payload_json.encode()).hexdigest(),
                tuple(self._doc['source_identity']), source.entity_id, _number(value), id(source),
                source.last_updated.isoformat(), _number(self._initial), _number(prefix),
                _number(before), _number(entity.native_value), id(entity), id(job),
                id(event) if event is not None else None,
                event.data['entity_id'] if event is not None else None,
                event.time_fired.isoformat() if event is not None else None,
                called, not called, self._doc['a0'], self._doc['s0'])
        except BaseException:
            self._poison('handoff_failed_outcome_not_replayable')
            raise
        finally:
            self._settling = False

    def close(self):
        """Explicit unconditional cleanup, even after guard/cancellation loss."""
        _need(asyncio.current_task() is self._task
              and asyncio.get_running_loop() is self._loop, 'cleanup_owner')
        self._live = False
        try:
            if self._unsubscribe is not None:
                self._unsubscribe()
        finally:
            self._unsubscribe = self._latest_event = self._latest_state = None
            self._event_shape = None
            self._old_entity = None
