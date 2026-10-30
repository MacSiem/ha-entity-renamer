"""Capture/check ORIGINAL native static state triggers; no runtime writes.

Private evidence only. No wrapper admission, action submission or post-cutover
verification. The existing strict YAML observer is deliberately unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import defaultdict
from functools import partial
import hashlib
from importlib import import_module
from pathlib import Path
from types import FunctionType, MethodType

from .native_unload_code_proof import prove_methods, unchanged
from .yaml_runtime_observer import (
    _automation_registrations, _closure, _registered_bus_remove, _trigger_listener,
)

_PINS = {
    '2025.6.0': {
        'core.py': '1b4eee1e4fb701669a4ed941f32bb36a977ce446dfa2c8d1ffb0beadc5a031a2',
        'helpers/event.py': 'd5e21c38afd9eafe566724b8db34ea2e87eaa8d6dc10c1719a82f5d90d43260e',
        'helpers/trigger.py': '82da6330f2f5f7640de8a05370496a8904fa7c25b9b1af998e023ea7dd7ded88',
        'components/automation/__init__.py': 'e9dfbaaec4803a18f6b8b19c7ac5635467b2cc3013f5ad8446d6d4ebc9709dbc',
        'components/homeassistant/triggers/state.py': 'efffe1b4478fff4ca9d979405acdfe2dffc28fbc18006fffc7122977a4dcb264',
    },
    '2026.10.4': {
        'core.py': '1fe2fcfb91fbb6edfb4450dad3ae5ebc4e325664770fedbd607d103d91ce4f19',
        'helpers/event.py': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269',
        'helpers/trigger/__init__.py': '438309232290ddba8c3110cbaec79fcb2de59706ce5fea47b6368c2531418a9f',
        'components/automation/__init__.py': 'd0bd002c7ee1f7b053d076815a5fb76f81d73808a1ee286ebc1d788241f52da9',
        'components/homeassistant/triggers/state.py': 'dc72a3a3f606b486165bf72d304cf7643cf7d4592971675f1ccab51aed9fffd8',
    },
}

# The installed staging Core has the identical six audited source bodies as
# 2026.10.4 (including Script in the emission witness). Each capture still
# hashes the actual local files; version similarity alone grants no admission.
_PINS['2026.10.2'] = dict(_PINS['2026.10.4'])


def _need(condition, reason):
    if not condition:
        raise ValueError('automation_state_binding_' + reason)


def _value(value, seen=()):
    """Identity AND contents for native containers; opaque leaves retain identity."""
    if value is None or type(value) in (str, bool, int, float):
        return type(value), value
    if id(value) in seen:
        return type(value), id(value)
    seen = (*seen, id(value))
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass, NodeStrClass
    if type(value) is NodeStrClass:
        return type(value), id(value), str(value)
    if type(value) in (dict, NodeDictClass):
        return dict, id(value), tuple((_value(k, seen), _value(v, seen)) for k, v in value.items())
    if type(value) in (list, tuple, NodeListClass):
        return type(value), id(value), tuple(_value(v, seen) for v in value)
    if type(value) in (set, frozenset):
        return type(value), id(value), frozenset(_value(v, seen) for v in value)
    if type(value) is partial:
        return partial, id(value), _value(value.func, seen), _value(value.args, seen), _value(value.keywords, seen)
    if type(value) is MethodType:
        return MethodType, id(value), id(value.__self__), _value(value.__func__, seen)
    if type(value) is FunctionType:
        return (FunctionType, id(value), value.__module__, value.__qualname__,
                id(value.__code__), id(value.__globals__),
                value.__code__.co_freevars,
                tuple((id(c), _value(c.cell_contents, seen)) for c in (value.__closure__ or ())),
                tuple((name, id(value.__globals__[name])) for name in value.__code__.co_names
                      if name in value.__globals__), _value(value.__dict__, seen))
    return type(value), id(value)


def _job(job):
    from homeassistant.core import HassJob
    _need(type(job) is HassJob, 'job_type')
    return (id(job), _value(job.target), job.name, job._cancel_on_shutdown, _value(job._cache))


class _Native:
    def __init__(self, hass):
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant, EventBus, HassJob
        from homeassistant.components.automation import AutomationEntity
        from homeassistant.helpers import event, trigger
        from homeassistant.components.homeassistant.triggers import state
        _need(__version__ in _PINS and type(hass) is HomeAssistant and type(hass.bus) is EventBus,
              'runtime_unsupported')
        self.hass = hass
        self.root = Path(import_module('homeassistant').__file__).resolve().parent
        self.version = __version__
        self.pins = _PINS[__version__]
        self.event, self.trigger, self.state = event, trigger, state
        self.HassJob, self.AutomationEntity = HassJob, AutomationEntity
        self.refs = [(state, 'async_attach_trigger'), (trigger, 'async_initialize_triggers'),
            (event, 'process_state_match'), (event, '_async_string_to_lower_list'),
            (event, '_async_track_state_change_event'), (event, '_async_track_event'),
            (event, '_remove_listener'), (event, '_async_dispatch_entity_id_event'),
            (event, '_async_state_filter'), (AutomationEntity, '_async_trigger_if_enabled'),
            (HomeAssistant, 'async_run_hass_job'), (EventBus, '_async_remove_listener'),
            (HassJob, '__init__')]
        if __version__ == '2025.6.0':
            self.refs.append((event, '_async_dispatch_entity_id_event_soon'))
        self.methods, self.codes = prove_methods(self.root, self.pins, self.refs)
        self.functions = tuple(_value(f) for f in self.methods)
        self.check_sources()
        self.check()

    def check_sources(self):
        for name, digest in self.pins.items():
            path = self.root / name
            _need(path.resolve() == path and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                  'source_changed')

    def check(self):
        unchanged(self.refs, self.methods, self.codes)
        _need(tuple(_value(getattr(o, n)) for o, n in self.refs) == self.functions,
              'native_function_changed')
        for function in self.methods:
            _need(function.__globals__ is vars(import_module(function.__module__)), 'native_globals')
        _need(self.state.process_state_match is self.event.process_state_match
              and self.state.async_track_state_change_event is self.event.async_track_state_change_event
              and self.state.HassJob is self.HassJob, 'native_import_changed')
        tracker = self.event._KEYED_TRACK_STATE_CHANGE
        dispatcher = (self.event._async_dispatch_entity_id_event_soon if self.version == '2025.6.0'
                      else self.event._async_dispatch_entity_id_event)
        _need(tracker.dispatcher_callable is dispatcher and tracker.filter_callable is self.event._async_state_filter
              and tracker.key == self.event._TRACK_STATE_CHANGE_DATA
              and tracker.event_type == 'state_changed', 'native_tracker_changed')


@dataclass(frozen=True)
class NativeAutomationStateBinding:
    hass: object
    entity: object
    index: int
    config: dict
    config_ids: list
    original_keys: list
    listener: object
    keyed_job: object
    action_job: object
    match_from: object
    match_to: object
    trigger_data: dict
    platform_type: str
    attribute: object
    match_all: bool
    detach: object
    removes: list
    remove: object
    unsubscribe: partial
    tracker: object
    data: object
    callbacks: object
    bus_remove: object
    bus_entry: tuple
    listener_cells: dict
    _native: object = field(repr=False)
    _original: tuple = field(repr=False)

    @classmethod
    def capture(cls, hass, entity, index, *, _native=None):
        """Capture actual objects, after source and strict full-trigger admission."""
        native = _Native(hass) if _native is None else _native
        _need(type(native) is _Native and native.hass is hass, 'source_proof_owner_changed')
        native.check()
        _need(type(entity) is native.AutomationEntity and entity.hass is hass
              and entity._is_enabled is True, 'enabled_native_owner_required')
        _automation_registrations(hass, entity)
        configs = entity._trigger_config
        _need(type(index) is int and 0 <= index < len(configs), 'index_required')
        cfg = configs[index]
        from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass, NodeStrClass
        _need(type(cfg) in (dict, NodeDictClass) and cfg.get('platform') == 'state'
              and 'for' not in cfg, 'static_state_no_for_required')
        detach = entity._async_detach_triggers
        removes = _closure(detach, native.trigger.__name__,
            'async_initialize_triggers.<locals>.remove_triggers')['removes']
        remove = removes[index]
        rc = _closure(remove, native.state.__name__, 'async_attach_trigger.<locals>.async_remove')
        unsub = rc['unsub']
        _, tracker, keys, job, callbacks = unsub.args
        ids = cfg['entity_id']
        _need(type(keys) is list and type(ids) in (list, NodeListClass) and keys is not ids and keys == ids and keys
              and all(type(k) in (str, NodeStrClass) and k == k.lower() and '.' in k for k in keys)
              and len(set(keys)) == len(keys)
              and sum(c.get('entity_id') is ids for c in configs) == 1, 'unique_separate_keys_required')
        _need(type(job) is native.HassJob, 'keyed_job_type')
        listener = job.target
        cells, _ = _trigger_listener(hass, entity, listener, native.state.__name__,
            'state_automation_listener', cfg, index)
        _need(type(cells['job']) is native.HassJob and type(cells['period']) is dict
              and not cells['period'] and cells['unsub_track_same'] is rc['unsub_track_same']
              and not rc['unsub_track_same'], 'idle_no_for_required')
        for function in (detach, remove, listener, cells['match_from_state'], cells['match_to_state']):
            _need(function.__globals__ is vars(import_module(function.__module__)), 'closure_globals')
        data = hass.data[tracker.key]
        _need(type(data) is native.event._KeyedEventData and type(callbacks) is defaultdict
              and callbacks.default_factory is list, 'native_callback_map_required')
        entry, _ = _registered_bus_remove(hass, data.listener, tracker.event_type)
        values = (hass, entity, index, cfg, ids, keys, listener, job, cells['job'],
            cells['match_from_state'], cells['match_to_state'], cells['trigger_data'],
            cells['platform_type'], cells['attribute'], cells['match_all'], detach,
            removes, remove, unsub, tracker, data, callbacks, data.listener, entry, cells, native)
        result = cls(*values, ())
        object.__setattr__(result, '_original', result._read())  # own draft object only
        result.assert_original()
        return result

    @property
    def config_entity_id_slot(self):
        """Future transport assigns a SEPARATE owned list to this exact slot.

        original_keys remains native route/unsubscribe storage. This accessor
        does not assign, extend, alias, project or authorize either list.
        """
        return self.config, 'entity_id'

    def original_for_ingress(self):
        """Checked original references; no wrapper or changed-route admission."""
        self.assert_original()
        return self

    def _read(self):
        e, n = self.entity, self._native
        _need(type(e) is n.AutomationEntity and e.hass is self.hass and e._is_enabled is True,
              'owner_lost')
        _need(e._async_detach_triggers is self.detach and e._trigger_config[self.index] is self.config
              and self.removes[self.index] is self.remove and self.config['entity_id'] is self.config_ids
              and self.keyed_job.target is self.listener
              and self.listener_cells['job'] is self.action_job, 'original_slots_changed')
        action = self.action_job.target
        _need(getattr(action, '__self__', None) is e
              and getattr(action, '__func__', None) is n.AutomationEntity._async_trigger_if_enabled,
              'action_owner_changed')
        _need(self.hass.data.get(self.tracker.key) is self.data
              and self.data.callbacks is self.callbacks and self.data.listener is self.bus_remove,
              'tracker_owner_changed')
        _registered_bus_remove(self.hass, self.bus_remove, self.tracker.event_type)
        registrations = _automation_registrations(self.hass, e)
        inventory = []
        for key, members in self.callbacks.items():
            _need(type(key) is str and type(members) is list
                  and len({id(j) for j in members}) == len(members), 'route_collision')
            _need(all(type(j) is n.HassJob for j in members), 'foreign_job_type')
            inventory.append((key, id(members), tuple(_job(j) for j in members)))
        return (_value(e._trigger_config), _value(e.raw_config), id(e.action_script),
            id(e.registry_entry), e.unique_id, e.entity_id, _value(self.original_keys),
            _value(self.detach), _value(self.remove), _value(self.unsubscribe), _value(self.listener),
            _job(self.keyed_job), _job(self.action_job), _value(self.listener_cells), registrations, tuple(inventory),
            _value(self.bus_remove), _value(self.bus_entry),
            tuple((kind, id(entries), tuple((_job(j), _value(f)) for j, f in entries))
                  for kind, entries in self.hass.bus._listeners.items()))

    def assert_original(self):
        """Deterministic synchronous refusal on any retained ORIGINAL drift."""
        self._native.check()  # source-before-graph; never await/call callbacks
        _need(self._read() == self._original, 'original_graph_changed')


def capture_state_bindings(hass, entity, *, _native=None):
    """Caller dispatch: genuinely disabled owners yield no state bindings."""
    from homeassistant.components.automation import AutomationEntity
    _need(type(entity) is AutomationEntity and entity.hass is hass, 'native_owner_required')
    if entity._is_enabled is False and entity._async_detach_triggers is None:
        return ()
    _need(entity._is_enabled is True, 'enabled_state_unknown')
    native = _Native(hass) if _native is None else _native
    _need(type(native) is _Native and native.hass is hass, 'source_proof_owner_changed')
    native.check()
    _automation_registrations(hass, entity)
    return tuple(NativeAutomationStateBinding.capture(hass, entity, index, _native=native)
                 for index, cfg in enumerate(entity._trigger_config) if cfg.get('platform') == 'state')


async def async_capture_state_bindings(hass, entity, *, guard):
    """Source parsing/hashing off-loop, then fresh actual graph on the HA loop."""
    guard()
    native = await hass.async_add_executor_job(_Native, hass)
    guard()
    return capture_state_bindings(hass, entity, _native=native)
