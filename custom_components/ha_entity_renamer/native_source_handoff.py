"""Private native Sensor source handoff during one genuine registry update.

Not a transaction adapter, event classifier or family capability. The registry
API still owns its real row/event and the native source callback still writes
its real State. Only audited live binding edges move before native dispatch.
Any failure after entering the registry API is uncertain and never retried.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from functools import partial
import inspect
from pathlib import Path
import sys
import threading
from types import CodeType, FunctionType, MethodType

from .native_registry import NativeRegistryIO, NativeRegistryMutation
from .native_rename_emission_classifier import NativeRenameEmissions, _EVENT_PINS
from .native_unload_code_proof import prove_methods, unchanged
from .native_utility_setup_audit import _SOURCES


class NativeSourceHandoffError(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise NativeSourceHandoffError('native_source_handoff_' + reason) from None


_SENSOR_PINS = {
    '2025.6.0': 'c8144af56e98f34adf6b46c78bd81284b870c52573a4ca2f6a9e4e53b7ab5dc9',
    '2026.10.4': 'c3cbda48f1cdfae2401e800ab0dd77399b12a64b654b728d0de28f2c07b5fd09',
}
_NAME = 'ha_entity_renamer.source_binding_handoff'


def _nested(code):
    for item in code.co_consts:
        if type(item) is CodeType:
            yield item
            yield from _nested(item)


class NativeSourceHandoff:
    def __init__(self):
        _need(False, 'native_capture_required')

    @classmethod
    async def prepare(cls, io, change, target, *, guard):
        import homeassistant
        from homeassistant.const import __version__
        from homeassistant.core import StateMachine
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.helpers import event as event_helper
        from homeassistant.helpers.entity import Entity, EntityPlatformState, entity_sources
        _need(cls is NativeSourceHandoff and __version__ in _SENSOR_PINS
              and type(io) is NativeRegistryIO, 'runtime_unqualified')
        # Reuse the existing genuine IO/source/native-code audit, not its old
        # remove/add classification. Retire its observers before this new mode.
        audit = await NativeRenameEmissions.prepare(io, change, target, guard=guard)
        audit.close()
        obj = object.__new__(cls)
        obj.audit, obj.io, obj.change, obj.target, obj.guard = audit, io, change, dict(target), guard
        obj.hass, obj.source, obj.platform, obj.row = audit.hass, audit.source, audit.platform, audit.row
        obj.old, obj.new, obj.registry, obj.bus = audit.source_id, audit.target_id, audit.registry, audit.bus
        obj.owner, obj.thread, obj.loop = asyncio.current_task(), threading.get_ident(), audit.loop
        obj.attempted, obj.in_native, obj.closed, obj.error = False, False, False, None
        obj.registry_calls, obj.handler_calls, obj.handler_done = 0, 0, False
        obj.new_row = obj.payload = obj.registry_event = None
        obj.tool = None
        refs = [(SensorEntity, 'async_registry_entry_updated'),
                (SensorEntity, '_async_read_entity_options'), (SensorEntity, '_update_suggested_precision'),
                (Entity, 'async_on_remove'), (Entity, 'async_internal_added_to_hass'),
                (event_helper, 'async_track_entity_registry_updated_event'), (StateMachine, 'async_available')]
        pins = dict(_SOURCES[__version__])
        pins.update({'helpers/event.py': _EVENT_PINS[__version__],
                     'components/sensor/__init__.py': _SENSOR_PINS[__version__]})
        obj.refs = refs
        obj.methods, obj.codes = await obj.hass.async_add_executor_job(
            prove_methods, Path(homeassistant.__file__).resolve().parent, pins, refs)
        guard()
        _need(isinstance(obj.source, SensorEntity) and type(obj.source).__setattr__ is object.__setattr__,
              'sensor_source_required')
        for name in ('entity_id', 'registry_entry'):
            _need(inspect.getattr_static(type(obj.source), name, None) is None, 'source_descriptor_unqualified')
        for index in range(3):
            _need(getattr(getattr(obj.source, refs[index][1]), '__func__', None) is obj.methods[index],
                  'sensor_hook_unqualified')
        _need(obj.source._platform_state is EntityPlatformState.ADDED
              and getattr(obj.source, 'group', None) is None
              and getattr(obj.source, '_Entity__group', None) is None
              and obj.row.device_id is None, 'source_lifecycle_unqualified')
        obj.maps = (obj.platform.entities, obj.platform.domain_entities, obj.platform.domain_platform_entities)
        _need(all(type(mapping) is dict for mapping in obj.maps)
              and len({id(mapping) for mapping in obj.maps}) == 3, 'source_maps_unqualified')
        obj.sources = entity_sources(obj.hass)
        _need(type(obj.sources) is dict, 'source_info_unqualified')
        obj.info = obj.sources.get(obj.old)
        _need(type(obj.info) is dict and obj.info.get('domain') == obj.platform.platform_name,
              'source_info_unqualified')
        obj.info_items = tuple(obj.info.items())
        obj.state_info, obj.remove_future = obj.source._state_info, obj.source._Entity__remove_future
        obj.callbacks = tuple(obj.source._on_remove)
        obj.remove_callbacks = obj.source._on_remove
        _need(type(obj.source._on_remove) is list and len(obj.callbacks) == 2, 'extra_cleanup_unqualified')
        candidates = {code for method in audit.methods.values() for code in _nested(method.__code__)
                      if code.co_name == 'remove_entity_cb'}
        cleanups = [item for item in obj.callbacks if type(item) is FunctionType and item.__code__ in candidates]
        _need(len(cleanups) == 1, 'platform_cleanup_unqualified')
        obj.cleanup = cleanups[0]
        _need(obj.cleanup.__globals__ is audit.methods['platform_add_one'].__globals__,
              'platform_cleanup_globals_changed')
        cells = dict(zip(obj.cleanup.__code__.co_freevars, obj.cleanup.__closure__, strict=True))
        _need(set(cells) == {'self', 'entity_id'} and cells['self'].cell_contents is obj.platform
              and cells['entity_id'].cell_contents == obj.old, 'platform_cleanup_binding_changed')
        obj.id_cell = cells['entity_id']
        _need(sum(cell is obj.id_cell for item in obj.callbacks if type(item) is FunctionType
                  for cell in (item.__closure__ or ())) == 1, 'platform_cleanup_cell_shared')
        unsubs = [item for item in obj.callbacks if type(item) is partial]
        _need(len(unsubs) == 1, 'registry_cleanup_unqualified')
        obj.unsub = unsubs[0]
        obj.unsub_index = next(index for index, item in enumerate(obj.callbacks) if item is obj.unsub)
        obj._subscription(obj.unsub, obj.old)
        obj._before()
        available = [index for index in range(6) if sys.monitoring.get_tool(index) is None]
        _need(bool(available), 'monitor_unavailable')
        obj.tool = available[0]
        try:
            sys.monitoring.use_tool_id(obj.tool, _NAME)
            sys.monitoring.register_callback(obj.tool, sys.monitoring.events.PY_START, obj._started)
            sys.monitoring.register_callback(obj.tool, sys.monitoring.events.PY_RETURN, obj._returned)
            sys.monitoring.set_local_events(obj.tool, audit.codes['fire'], sys.monitoring.events.PY_START)
            sys.monitoring.set_local_events(obj.tool, audit.codes['source'],
                sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
        except BaseException:
            obj.close()
            raise
        return obj

    def _owner(self):
        _need(asyncio.current_task() is self.owner and threading.get_ident() == self.thread
              and asyncio.get_running_loop() is self.loop, 'owner_changed')
        self.guard()
        self.audit._native_check()
        unchanged(self.refs, self.methods, self.codes)
        _need(type(self.source).__setattr__ is object.__setattr__
              and all(inspect.getattr_static(type(self.source), name, None) is None
                      for name in ('entity_id', 'registry_entry'))
              and all(inspect.getattr_static(self.source, self.refs[index][1]) is self.methods[index]
                      for index in range(3)), 'source_hook_changed')

    def _callbacks_current(self):
        return (self.source._on_remove is self.remove_callbacks
                and len(self.remove_callbacks) == len(self.callbacks)
                and all(actual is expected for actual, expected in zip(self.remove_callbacks, self.callbacks)))

    def _subscription(self, unsub, entity_id):
        from homeassistant.core import HassJob
        from homeassistant.helpers import event as event_helper
        _need(type(unsub) is partial and unsub.func is event_helper._remove_listener
              and not unsub.keywords and len(unsub.args) == 5, 'registry_subscription_unqualified')
        owner, tracker, keys, job, callbacks = unsub.args
        _need(tracker is event_helper._KEYED_TRACK_ENTITY_REGISTRY_UPDATED
              and type(keys) in (tuple, list, set, frozenset)
              and all(type(key) is str for key in keys)
              and type(callbacks) is defaultdict and callbacks.default_factory is list,
              'registry_subscription_type_changed')
        data = self.hass.data.get(tracker.key)
        _need(owner is self.hass and tuple(keys) == (entity_id,) and type(job) is HassJob
              and type(job.target) is MethodType
              and getattr(job.target, '__self__', None) is self.source
              and getattr(job.target, '__func__', None) is self.audit.methods['registry_callback']
              and type(data) is event_helper._KeyedEventData and data.callbacks is callbacks
              and sum(item is job for item in callbacks.get(entity_id, ())) == 1,
              'registry_subscription_binding_changed')
        _need(sum(item is job for entries in callbacks.values() for item in entries) == 1,
              'registry_subscription_extra')

    def _before(self):
        from homeassistant.helpers.entity import EntityPlatformState
        self._owner()
        _need(not self.attempted and not self.closed and self.error is None
              and self.registry.async_get(self.old) is self.row and self.registry.async_get(self.new) is None
              and self.source.registry_entry is self.row and self.source.entity_id == self.old
              and self.source._platform_state is EntityPlatformState.ADDED
              and self.source._state_info is self.state_info and self.source._Entity__remove_future is self.remove_future
              and self._callbacks_current()
              and self.source.platform is self.platform and self.source.hass is self.hass
              and all(actual is expected for actual, expected in zip(
                  (self.platform.entities, self.platform.domain_entities, self.platform.domain_platform_entities), self.maps))
              and self.id_cell.cell_contents == self.old
              and self.sources.get(self.old) is self.info and self.new not in self.sources
              and all(mapping.get(self.old) is self.source and self.new not in mapping for mapping in self.maps)
              and self.hass.states.get(self.old) is not None and self.hass.states.async_available(self.new)
              and not getattr(self.bus, '_dispatching', False), 'source_preflight_changed')
        self._subscription(self.unsub, self.old)

    def _started(self, code, offset):
        frame = sys._getframe(1)
        try:
            if not self.in_native:
                return
            if code is self.audit.codes['source']:
                if frame.f_locals.get('self') is self.source:
                    self.handler_calls += 1
                    event = frame.f_locals.get('event')
                    _need(self.handler_calls == 1 and type(event) is self.audit.Event
                          and event.data == self.payload, 'source_handler_unqualified')
                    self.registry_event = event
                return
            if frame.f_locals.get('self') is not self.bus:
                return
            from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
            if frame.f_locals.get('event_type') != EVENT_ENTITY_REGISTRY_UPDATED:
                return
            parent = frame.f_back
            if parent is None or parent.f_code is not self.audit.codes['registry_writer']:
                return
            payload = frame.f_locals.get('event_data')
            if type(payload) is not dict or payload.get('entity_id') != self.new:
                return
            self.registry_calls += 1
            public = parent.f_back
            caller = public.f_back if public is not None else None
            owning = caller.f_back if caller is not None else None
            _need(self.registry_calls == 1 and parent.f_lasti in self.audit.sites['registry_fire']
                  and public is not None and public.f_code is self.audit.codes['registry_update']
                  and caller is not None and caller.f_code is self.audit.io_mutate_code
                  and caller.f_locals.get('self') is self.io
                  and caller.f_locals.get('prepared') is self.change and caller.f_locals.get('target') is self.target
                  and owning is not None and owning.f_code is NativeSourceHandoff.mutate.__code__
                  and owning.f_locals.get('self') is self
                  and parent.f_locals.get('self') is self.registry and parent.f_locals.get('old') is self.row
                  and payload.get('action') == 'update' and payload.get('old_entity_id') == self.old
                  and payload.get('changes') == {'entity_id': self.old}, 'registry_callsite_unqualified')
            new = parent.f_locals.get('new')
            _need(new is self.registry.async_get(self.new) and new is not None and new.id == self.row.id
                  and self.registry.async_get(self.old) is None and self.source.registry_entry is self.row
                  and self.source.entity_id == self.old and self._callbacks_current()
                  and self.id_cell.cell_contents == self.old and self.sources.get(self.old) is self.info
                  and self.new not in self.sources
                  and all(mapping.get(self.old) is self.source and self.new not in mapping for mapping in self.maps),
                  'registry_new_row_unqualified')
            self.payload, self.new_row = payload, new
            # Genuine row is already committed by native registry. No callback,
            # State write/remove, API replay or await runs inside this monitor.
            for mapping in self.maps:
                del mapping[self.old]
                mapping[self.new] = self.source
            del self.sources[self.old]
            self.sources[self.new] = self.info
            self.id_cell.cell_contents = self.new
            self.source.entity_id = self.new
            self.source.registry_entry = new
        except BaseException as error:
            if self.error is None:
                self.error = (error.args[0] if type(error) is NativeSourceHandoffError
                              else 'native_source_handoff_native_dispatch_uncertain')
        finally:
            del frame

    def _returned(self, code, offset, value):
        frame = sys._getframe(1)
        try:
            if self.in_native and frame.f_locals.get('self') is self.source:
                self.handler_done = True
        finally:
            del frame

    def mutate(self):
        from homeassistant.core import HassJobType
        from homeassistant.helpers.event import async_track_entity_registry_updated_event
        self._before()
        self.original_state = self.hass.states.get(self.old)
        self.attempted, self.in_native = True, True
        try:
            mutation = self.io.mutate(self.change, self.target)
            if self.error is not None:
                raise NativeSourceHandoffError(self.error)
            _need(self.registry_calls == 1, 'registry_dispatch_not_observed')
            _need(self.handler_calls == 1 and self.handler_done, 'native_handler_not_completed')
            _need(type(mutation) is NativeRegistryMutation and self.error is None
                  and self.registry_calls == self.handler_calls == 1 and self.handler_done
                  and self.source.registry_entry is self.new_row
                  and self.registry.async_get(self.new) is self.new_row
                  and self.source.entity_id == self.new and self.source._state_info is self.state_info
                  and self.source._Entity__remove_future is self.remove_future
                  and self._callbacks_current()
                  and self.hass.states.get(self.old) is self.original_state,
                  'registry_attempt_uncertain')
            state = self.hass.states.get(self.new)
            _need(type(state) is self.audit.State, 'new_native_state_unconfirmed')
            # Native handler has written NEW first, without lifecycle removal.
            self.hass.states.async_remove(self.old, context=state.context)
            self.unsub()
            unsubscribe = async_track_entity_registry_updated_event(self.hass, self.new,
                self.source._async_registry_updated, job_type=HassJobType.Callback)
            self.source._on_remove[self.unsub_index] = unsubscribe
            self._subscription(unsubscribe, self.new)
            _need(self.hass.states.get(self.old) is None and self.hass.states.get(self.new) is state
                  and all(mapping.get(self.new) is self.source and self.old not in mapping for mapping in self.maps)
                  and self.sources.get(self.new) is self.info and self.old not in self.sources
                  and self.id_cell.cell_contents == self.new, 'source_handoff_readback_uncertain')
            return mutation
        finally:
            self.in_native = False
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.tool is not None and sys.monitoring.get_tool(self.tool) == _NAME:
            for code in (self.audit.codes['fire'], self.audit.codes['source']):
                sys.monitoring.set_local_events(self.tool, code, 0)
            sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.free_tool_id(self.tool)
