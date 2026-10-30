"""Bounded provenance of one genuine native entity-id registry mutation.

This is an ephemeral, root-owned witness, not an authorization or a filtering
policy. In particular, an owned add can contain a changed physical value. The
caller must authenticate its ORIGINAL fence and intent separately. No client
input may select this module's IO, guard or prepared change.

Only exact installed, pinned CPython/HA code is supported. No producer method,
Event, State, callback, native queue or registry object is replaced or edited.
"""
from __future__ import annotations

import ast
import asyncio
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import dis
from enum import EnumType
from functools import partial
import inspect
import math
from pathlib import Path
import sys
import threading
import textwrap

from .native_registry import NativeRegistryIO, NativeRegistryMutation, PreparedRegistryChange
from .native_unload_code_proof import prove_methods, unchanged
from .native_utility_setup_audit import _SOURCES
from .native_utility_source_emissions import NativeSourceEmissions


class NativeRenameEmissionError(ValueError):
    """Sanitized refusal/uncertain effect; never retry an attempted mutation."""


def _need(value, reason):
    if not value:
        raise NativeRenameEmissionError('native_rename_emissions_' + reason) from None


_MONITOR_NAME = 'ha_entity_renamer.rename_event_occurrences'
_MAX_OCCURRENCES = 32
_MAX_DEPTH = 64
# Existing qualified helper/event.py pins shared with NativeUtilitySourceGap
# and NativeTemplateConsumers. Utility's base source table omits this file.
_EVENT_PINS = {
    '2025.6.0': 'd5e21c38afd9eafe566724b8db34ea2e87eaa8d6dc10c1719a82f5d90d43260e',
    '2026.10.2': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269',
    '2026.10.4': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269',
}


def _call_offsets(method, attribute, *, forced=False, first_argument=None):
    """Audit callsites off-loop; callbacks only compare precomputed offsets.

    CALL and its inline caches describe a synchronous call; SEND describes a
    suspended await. Positions must fall within the *particular* pinned Call
    expression, rather than anywhere in an ancestor method.
    """
    lines, first = inspect.getsourcelines(method)
    tree = ast.parse(textwrap.dedent(''.join(lines)))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != attribute:
            continue
        if first_argument is not None and not (node.args and isinstance(node.args[0], ast.Name)
                                               and node.args[0].id == first_argument):
            continue
        if forced and not any(k.arg == 'force_remove' and isinstance(k.value, ast.Constant)
                              and k.value.value is True for k in node.keywords):
            continue
        calls.append(node)
    _need(len(calls) == 1, 'callsite_unsupported')
    node = calls[0]
    lo, hi = first + node.lineno - 1, first + node.end_lineno - 1
    offsets = frozenset(i.offset for i in dis.get_instructions(method, show_caches=True)
                        if i.positions.lineno is not None
                        and lo <= i.positions.lineno <= hi
                        and i.opname in ('CALL', 'CALL_KW', 'CALL_FUNCTION_EX', 'CACHE', 'SEND'))
    _need(bool(offsets), 'callsite_unavailable')
    return offsets


def _audit(root, pins, refs, edges):
    methods, codes = prove_methods(Path(root).resolve().parent, pins, list(refs.values()))
    methods, codes = dict(zip(refs, methods, strict=True)), dict(zip(refs, codes, strict=True))
    sites = {name: _call_offsets(methods[parent], attribute, forced=forced,
                first_argument=('EVENT_STATE_CHANGED' if name in ('remove_fire', 'set_fire') else None))
             for name, (parent, attribute, forced) in edges.items()}
    # Bind every referenced native global as well as the function's globals
    # dictionary. This does not copy module state or invoke a descriptor.
    globals_ = {key: (method.__globals__, tuple((name, method.__globals__[name])
                for name in codes[key].co_names if name in method.__globals__))
                for key, method in methods.items()}
    return methods, codes, sites, globals_


@dataclass(eq=False)
class _Emission:
    phase: str
    raw: dict
    context: object
    origin: object
    fire_id: int
    binding: tuple = ()
    semantic: object = None
    event: object = None
    event_context: object = None
    dispatched: bool = False


def _semantic_state(state, *, native_attribute_key=None):
    """Bounded data snapshot, never a render or source-authority proof.

    Unsupported attribute objects make neutrality unknown/false. Do not invoke
    their serializers, descriptors, iterators or equality hooks in a callback.
    """
    budget = [4096, 1024 * 1024]
    def static_member(kind, name):
        # Even class attribute lookup can execute a descriptor. Read the
        # actual type dictionaries, without invoking metaclass/member hooks.
        for base in type.__getattribute__(kind, '__mro__'):
            namespace = type.__getattribute__(base, '__dict__')
            if name in namespace:
                return namespace[name]
        return None

    def freeze(value, depth=0, *, native_attributes=False):
        budget[0] -= 1
        if budget[0] < 0 or depth > 32:
            raise ValueError()
        kind = type(value)
        if value is None:
            return ('none',)
        native_key = (native_attribute_key is not None and kind is native_attribute_key
            and type(kind) is EnumType
            and all(static_member(kind, name) is getattr(str, name)
                    for name in ('__hash__', '__eq__', '__str__', '__getattribute__', '__len__'))
            and static_member(kind, '__bool__') is None)
        if kind is str or native_key:
            # Only native Unicode semantics: arbitrary str subclasses can have
            # different dictionary lookup or template semantics despite equal
            # underlying text. They remain unknown, without invoking hooks.
            value = str.__str__(value)
            budget[1] -= len(value)
            if budget[1] < 0:
                raise ValueError()
            return ('native_attribute_key' if native_key else 'str', value)
        if kind is bool:
            return ('bool', value)
        if kind is int:
            return ('int', value)
        if kind is float and math.isfinite(value):
            return ('float', float.hex(value))
        if kind is list or kind is tuple:
            return ('list' if kind is list else 'tuple',
                    tuple(freeze(item, depth + 1) for item in value))
        if kind is dict or native_attributes:
            if dict.__len__(value) > budget[0]:
                raise ValueError()
            rows = []
            for key, item in dict.items(value):
                if type(key) is not str and type(key) is not native_attribute_key:
                    raise ValueError()
                rows.append((freeze(key, depth + 1), freeze(item, depth + 1)))
            return ('dict', tuple(rows))
        raise ValueError()
    try:
        # State is the exact pinned native class at every callsite. Its outer
        # attributes are native ReadOnlyDict; bypass overridden dict methods.
        if dict.__len__(state.attributes) > budget[0]:
            return None
        return (freeze(state.state), freeze(state.attributes, native_attributes=True))
    except (ValueError, TypeError, RecursionError):
        return None


class _Occurrences:
    """Separate local monitoring slot; never changes the shared fire fanout."""

    def __init__(self, witness):
        self.witness, self.key, self.live = witness, None, False
        monitor = sys.monitoring
        available = [key for key in range(6) if monitor.get_tool(key) is None]
        _need(bool(available), 'occurrence_monitor_unavailable')
        self.key = available[0]
        monitor.use_tool_id(self.key, _MONITOR_NAME)
        self.masks = {}
        start = monitor.events.PY_START
        end = monitor.events.PY_RETURN
        # PY_UNWIND is not a local event (CPython sys.monitoring API). Only
        # this owned slot subscribes globally; unrelated code returns at once.
        self.global_mask = monitor.events.PY_UNWIND
        for name in ('event', 'source', 'keyed', 'registry_keyed', 'dispatch'):
            self.masks[witness.codes[name]] = start | end
        # On min, dispatch IS fire: its start is deliberately unused. The
        # fanout supplies the authoritative fire-frame identity, independently
        # of monitoring-tool callback ordering.
        if not witness.queued:
            self.masks[witness.codes['fire']] = end
        self.live = True
        try:
            monitor.register_callback(self.key, start, self._start)
            monitor.register_callback(self.key, monitor.events.PY_RETURN, self._return)
            monitor.register_callback(self.key, monitor.events.PY_UNWIND, self._unwind)
            monitor.set_events(self.key, self.global_mask)
            for code, mask in self.masks.items():
                monitor.set_local_events(self.key, code, mask)
            self.check()
        except BaseException:
            self.close()
            raise

    def _start(self, code, offset):
        frame = sys._getframe(1)
        try:
            self.witness._started(frame)
        except BaseException as error:
            self.witness._observer_error(error, 'occurrence_observer_failed')
        finally:
            del frame

    def _return(self, code, offset, value):
        frame = sys._getframe(1)
        try:
            self.witness._ended(frame, False)
        except BaseException as error:
            self.witness._observer_error(error, 'occurrence_return_failed')
        finally:
            del frame

    def _unwind(self, code, offset, exception):
        if code not in self.masks:
            return
        frame = sys._getframe(1)
        try:
            self.witness._ended(frame, True)
        except BaseException as error:
            self.witness._observer_error(error, 'occurrence_unwind_failed')
        finally:
            del frame

    def check(self):
        monitor = sys.monitoring
        _need(self.live and monitor.get_tool(self.key) == _MONITOR_NAME
              and monitor.get_events(self.key) == self.global_mask
              and all(monitor.get_local_events(self.key, code) == mask
                      for code, mask in self.masks.items()), 'occurrence_monitor_lost')

    def close(self):
        if not self.live:
            return
        self.live = False
        monitor = sys.monitoring
        if monitor.get_tool(self.key) != _MONITOR_NAME:
            self.witness._poison('occurrence_monitor_lost')
            return
        # Explicit cleanup also supports 3.13, where free does not clear masks.
        monitor.set_events(self.key, 0)
        for code in self.masks:
            monitor.set_local_events(self.key, code, 0)
        for event in (monitor.events.PY_START, monitor.events.PY_RETURN,
                      monitor.events.PY_UNWIND):
            monitor.register_callback(self.key, event, None)
        monitor.free_tool_id(self.key)


class NativeRenameEmissions:
    """One source, one attempt, one genuine IO call, original Events only."""

    @classmethod
    async def prepare(cls, io, change, target, *, guard, source_handoff=None):
        _need(type(io) is NativeRegistryIO and type(change) is PreparedRegistryChange,
              'trusted_io_required')
        _need(callable(guard), 'guard_required')
        guard()
        if source_handoff is not None:
            from .native_source_handoff import NativeSourceHandoff
            _need(type(source_handoff) is NativeSourceHandoff
                  and source_handoff.io is io and source_handoff.change is change
                  and not source_handoff.attempted and not source_handoff.closed
                  and target == source_handoff.target, 'source_handoff_binding_required')
            # The driver detached this exact target before its first await.
            target = source_handoff.target
        _need(change.kind == 'entity' and change.mapping and type(target) is dict
              and target in (change.before, change.after), 'rename_required')
        before, after = change.before, change.after
        _need({k: v for k, v in before.items() if k not in ('entity_id', 'modified_at')}
              == {k: v for k, v in after.items() if k not in ('entity_id', 'modified_at')},
              'pure_rename_required')
        import homeassistant
        from homeassistant import const
        from homeassistant.const import __version__
        from homeassistant.core import Event, EventBus, HomeAssistant, State, StateMachine
        from homeassistant.helpers import entity_platform, event as event_helper
        from homeassistant.helpers.entity import Entity
        from homeassistant.helpers.entity_registry import EntityRegistry

        _need(sys.implementation.name == 'cpython' and sys.version_info[:2] in ((3, 13), (3, 14))
              and __version__ in ('2025.6.0', '2026.10.2', '2026.10.4'), 'runtime_unavailable')
        self = cls()
        self.io, self.change, self.destination, self.guard = io, change, target, guard
        self.source_handoff = source_handoff
        self.handoff_state = self.handoff_semantic = None
        self.handoff_methods = self.handoff_codes = self.handoff_sites = None
        self.target_row = deepcopy(target)
        source = after if target == before else before
        self.source_id, self.target_id = source['entity_id'], target['entity_id']
        self.hass, self.bus, self.loop = io.hass, io.hass.bus, io.hass.loop
        _need(type(self.hass) is HomeAssistant and type(self.bus) is EventBus
              and type(self.hass.states) is StateMachine
              and type(io.registries['entity']) is EntityRegistry, 'native_runtime_required')
        self.owner, self.thread = asyncio.current_task(), threading.get_ident()
        self.Event, self.State, self.Entity, self.Platform = Event, State, Entity, entity_platform.EntityPlatform
        self.const, self.attribute_key = const, vars(const).get('EntityStateAttribute')
        self.queued = __version__ != '2025.6.0'
        self.error = None
        self.closed, self.attempted, self.in_mutate, self.finished = False, False, False, False
        self.emissions, self.pending, self.dispatches, self.constructors = [], [], {}, {}
        self.keyed, self.tasks, self.source_runs = {}, {}, 0
        self.fire_monitor = self.occurrences = None
        self.wake = None
        self.preflight = None
        self.mutation = None
        self.io_mutate, self.io_settle = NativeRegistryIO.mutate, NativeRegistryIO.settle
        self.io_mutate_code, self.io_settle_code = self.io_mutate.__code__, self.io_settle.__code__
        self.mutate_method, self.mutate_code = cls.mutate, cls.mutate.__code__
        self.refs = {
            'fire': (EventBus, 'async_fire_internal'), 'event': (Event, '__init__'),
            'remove_state': (StateMachine, 'async_remove'),
            'set_state': (StateMachine, 'async_set_internal'),
            'registry_update': (EntityRegistry, 'async_update_entity'),
            'registry_writer': (EntityRegistry, '_async_update_entity'),
            'registry_callback': (Entity, '_async_registry_updated'),
            'source': (Entity, '_async_process_registry_update_or_remove'),
            'remove': (Entity, 'async_remove'), 'remove_impl': (Entity, '_Entity__async_remove_impl'),
            'write': (Entity, '_async_write_ha_state'), 'write_public': (Entity, 'async_write_ha_state'),
            'finish_add': (Entity, 'add_to_platform_finish'),
            'platform_add': (entity_platform.EntityPlatform, 'async_add_entities'),
            'platform_add_many': (entity_platform.EntityPlatform, '_async_add_entities'),
            'platform_add_one': (entity_platform.EntityPlatform, '_async_add_entity'),
            'platforms': (entity_platform, 'async_get_platforms'),
            'keyed': (event_helper, '_async_dispatch_entity_id_event'),
            'registry_keyed': (event_helper, '_async_dispatch_old_entity_id_or_entity_id_event'),
            'create_task': (HomeAssistant, 'async_create_task_internal'),
            'run_job': (HomeAssistant, 'async_run_hass_job'),
        }
        self.refs['dispatch'] = (EventBus, '_async_dispatch' if self.queued else 'async_fire_internal')
        if self.queued:
            self.refs['platform_add_impl'] = (entity_platform.EntityPlatform, '_async_add_entity_impl')
        else:
            self.refs['keyed_soon'] = (event_helper, '_async_dispatch_entity_id_event_soon')
        edges = {
            'remove_fire': ('remove_state', 'async_fire_internal', False),
            'set_fire': ('set_state', 'async_fire_internal', False),
            'remove_state': ('remove_impl', 'async_remove', False),
            'remove_impl': ('remove', '__async_remove_impl', False),
            'source_remove': ('source', 'async_remove', True),
            'set_state': ('write', 'async_set_internal', False),
            'write': ('write_public', '_async_write_ha_state', False),
            'write_public': ('finish_add', 'async_write_ha_state', False),
            'finish_add': ('platform_add_one', 'add_to_platform_finish', False),
            'platform_add_one': ('platform_add_many', '_async_add_entity', False),
            'platform_add_many': ('platform_add', '_async_add_entities', False),
            'source_add': ('source', 'async_add_entities', False),
            'source_task': ('registry_callback', 'async_create_task_internal', False),
            'registry_fire': ('registry_writer', 'async_fire_internal', False),
            'job_target': ('run_job', 'target', False),
            'registry_job': ('registry_keyed', 'async_run_hass_job', False),
        }
        # set_state has a second fire for state_reported. Select its own exact
        # state_changed call in the audit, never resolve source in a callback.
        self.methods, self.codes, self.sites, self.globals = await self.hass.async_add_executor_job(
            _audit, homeassistant.__file__,
            {**_SOURCES[__version__], 'helpers/event.py': _EVENT_PINS[__version__]}, self.refs, edges)
        if source_handoff is not None:
            from .native_source_handoff import NativeSourceHandoff
            self.handoff_methods = (NativeSourceHandoff.mutate, NativeSourceHandoff._native_check)
            self.handoff_codes = tuple(method.__code__ for method in self.handoff_methods)
            def audit_handoff_sites():
                return (_call_offsets(self.methods['source'], 'async_write_ha_state'),
                        _call_offsets(self.handoff_methods[0], 'async_remove'))
            self.handoff_sites = await self.hass.async_add_executor_job(audit_handoff_sites)
        self._owner_guard()
        self.registry = io.registries['entity']
        self.row = self.registry.async_get(self.source_id)
        _need(self.row is not None and self.row.id == change.identity
              and not self.row.disabled and self.row.entity_id == self.source_id,
              'source_registry_binding')
        candidates = []
        for platform in entity_platform.async_get_platforms(self.hass, self.row.platform):
            for entity in platform.entities.values():
                if (getattr(entity, 'registry_entry', None) is self.row
                        and getattr(entity, 'entity_id', None) == self.source_id):
                    candidates.append((entity, platform))
        _need(len(candidates) == 1, 'source_graph_unavailable')
        self.source, self.platform = candidates[0]
        if source_handoff is not None:
            _need(source_handoff.source is self.source and source_handoff.platform is self.platform
                  and source_handoff.row is self.row and source_handoff.owner is self.owner
                  and source_handoff.old == self.source_id and source_handoff.new == self.target_id,
                  'source_handoff_actor_changed')
        _need(isinstance(self.source, Entity) and type(self.platform) is entity_platform.EntityPlatform
              and self.source.platform is self.platform and self.source.hass is self.hass
              and self.hass.states.get(self.source_id) is not None
              and self.hass.states.get(self.target_id) is None, 'source_graph_binding')
        self._native_check()
        try:
            self.occurrences = _Occurrences(self)
            self.fire_monitor = NativeSourceEmissions(self.codes['fire'], self.bus,
                                                       self._fire, self._poison)
            self._check()
        except BaseException:
            self.close()
            raise
        return self

    def _owner_guard(self, *, authorize=True):
        _need(asyncio.current_task() is self.owner and asyncio.get_running_loop() is self.loop
              and threading.get_ident() == self.thread, 'owner_task_changed')
        if authorize:
            self.guard()

    def _native_check(self):
        _need(vars(self.const).get('EntityStateAttribute') is self.attribute_key,
              'native_attribute_key_changed')
        unchanged(list(self.refs.values()), tuple(self.methods.values()), tuple(self.codes.values()))
        _need(NativeRegistryIO.mutate is self.io_mutate and NativeRegistryIO.settle is self.io_settle
              and self.io_mutate.__code__ is self.io_mutate_code
              and self.io_settle.__code__ is self.io_settle_code
              and type(self).mutate is self.mutate_method
              and self.mutate_method.__code__ is self.mutate_code
              and getattr(self.io.mutate, '__func__', None) is self.io_mutate
              and getattr(self.io.settle, '__func__', None) is self.io_settle
              and self.io.hass is self.hass and self.hass.bus is self.bus
              and self.hass.loop is self.loop and self.io.registries['entity'] is self.registry,
              'io_binding_changed')
        for key, (namespace, bindings) in self.globals.items():
            _need(self.methods[key].__globals__ is namespace
                  and all(namespace.get(name) is value for name, value in bindings),
                  'native_global_changed')
        for key in ('registry_callback', 'source', 'remove', 'remove_impl', 'write',
                    'write_public', 'finish_add'):
            _need(getattr(getattr(self.source, self.refs[key][1]), '__func__', None)
                  is self.methods[key], 'source_method_changed')
        for key in ('platform_add', 'platform_add_many', 'platform_add_one') + (
                ('platform_add_impl',) if self.queued else ()):
            _need(getattr(getattr(self.platform, self.refs[key][1]), '__func__', None)
                  is self.methods[key], 'platform_method_changed')
        for key, obj in (('fire', self.bus), ('dispatch', self.bus),
                         ('remove_state', self.hass.states), ('set_state', self.hass.states),
                         ('registry_update', self.registry), ('registry_writer', self.registry),
                         ('create_task', self.hass), ('run_job', self.hass)):
            _need(getattr(getattr(obj, self.refs[key][1]), '__func__', None)
                  is self.methods[key], 'native_instance_binding_changed')
        if self.source_handoff is not None:
            from .native_source_handoff import NativeSourceHandoff
            h = self.source_handoff
            _need(type(h) is NativeSourceHandoff and h.io is self.io and h.change is self.change
                  and h.source is self.source and h.platform is self.platform
                  and h.target is self.destination and h.target == self.target_row
                  and all(actual is expected for actual, expected in zip(
                      (NativeSourceHandoff.mutate, NativeSourceHandoff._native_check), self.handoff_methods, strict=True))
                  and all(method.__code__ is code for method, code in zip(
                      self.handoff_methods, self.handoff_codes, strict=True)), 'source_handoff_reference_changed')
            h._native_check()

    def _check(self):
        _need(not self.closed and self.error is None, self.error or 'closed')
        self._native_check()
        self.occurrences.check()
        self.fire_monitor.check()

    def _signal(self):
        if self.wake is not None and not self.wake.done():
            self.wake.set_result(None)

    def _poison(self, reason):
        if self.error is None:
            self.error = reason
        self._signal()

    def _observer_error(self, error, fallback):
        # Keep only this module's sanitized invariant code, never foreign
        # exception text, paths, State values or authentication material.
        reason = error.args[0] if type(error) is NativeRenameEmissionError and error.args else None
        prefix = 'native_rename_emissions_'
        if (type(reason) is str and reason.startswith(prefix)
                and len(reason) < 160 and reason.isascii()
                and reason.replace('_', '').isalnum()):
            self._poison(reason[len(prefix):])
        else:
            self._poison(fallback)

    def _frame(self, frame, key, obj=None):
        return (frame is not None and frame.f_code is self.codes[key]
                and frame.f_globals is self.globals[key][0]
                and (obj is None or frame.f_locals.get('self') is obj))

    def _edge(self, frame, key, site, obj):
        return self._frame(frame, key, obj) and frame.f_lasti in self.sites[site]

    def _source_frame(self, frame):
        task = asyncio.current_task()
        record = self.tasks.get(task)
        if record is None or not self._frame(frame, 'source', self.source) or id(frame) != record[1]:
            return False
        values = frame.f_locals
        old, new = values.get('old'), values.get('registry_entry')
        registry_event = self._registry_emission()
        if self.source_handoff is None:
            binding = old is self.row and old.entity_id == self.source_id
        else:
            h = self.source_handoff
            binding = (old is new is h.new_row and values.get('event') is h.registry_event
                       and h.new_row is not None and h.attempted)
        return (registry_event is not None and values.get('event') is registry_event.event
                and values.get('data') is registry_event.raw and self._registry_data(registry_event.raw)
                and binding and new is not None and new.id == self.change.identity
                and new.entity_id == self.target_id
                and self.source.registry_entry is new
                and self.registry.async_get(self.target_id) is new)

    def _registry_emission(self):
        return next((item for item in self.emissions if item.phase == 'registry'), None)

    def _registry_data(self, raw):
        return (type(raw) is dict and set(raw) == {'action', 'entity_id', 'old_entity_id', 'changes'}
                and raw['action'] == 'update' and raw['entity_id'] == self.target_id
                and raw['old_entity_id'] == self.source_id
                and type(raw['changes']) is dict
                and raw['changes'] == {'entity_id': self.source_id})

    def _data_current(self, item):
        if item.phase == 'registry':
            return self._registry_data(item.raw)
        return (type(item.raw) is dict and set(item.raw) == {'entity_id', 'old_state', 'new_state'}
                and item.raw['entity_id'] == item.binding[0]
                and item.raw['old_state'] is item.binding[1]
                and item.raw['new_state'] is item.binding[2])

    def _registry_cause(self, fire, raw):
        frame = fire.f_back
        if not self._edge(frame, 'registry_writer', 'registry_fire', self.registry):
            return False
        values = frame.f_locals
        if not (values.get('data') is raw and values.get('old') is self.row
                and getattr(values.get('new'), 'id', None) == self.change.identity
                and getattr(values.get('new'), 'entity_id', None) == self.target_id
                and self.registry.async_get(self.target_id) is values.get('new')):
            return False
        frame = frame.f_back
        if not self._frame(frame, 'registry_update', self.registry):
            return False
        frame = frame.f_back
        if not (frame is not None and frame.f_code is self.io_mutate_code
                and frame.f_globals is self.io_mutate.__globals__
                and frame.f_locals.get('self') is self.io
                and frame.f_locals.get('prepared') is self.change
                and frame.f_locals.get('target') is self.destination):
            return False
        frame = frame.f_back
        if self.source_handoff is not None:
            if not (frame is not None and frame.f_code is self.handoff_codes[0]
                    and frame.f_globals is self.handoff_methods[0].__globals__
                    and frame.f_locals.get('self') is self.source_handoff
                    and self.source_handoff.in_native and self.source_handoff.attempted):
                return False
            frame = frame.f_back
        return (frame is not None and frame.f_code is self.mutate_code
                and frame.f_locals.get('self') is self and self.in_mutate
                and asyncio.current_task() is self.owner
                and self._registry_data(raw))

    def _state_cause(self, fire, raw):
        if set(raw) != {'entity_id', 'old_state', 'new_state'}:
            return None
        frame = fire.f_back
        values = frame.f_locals if frame else {}
        if values.get('state_changed_data') is not raw:
            return None
        if self._edge(frame, 'remove_state', 'remove_fire', self.hass.states):
            if not (raw['entity_id'] == self.source_id and type(raw['old_state']) is self.State
                    and raw['old_state'] is values.get('old_state') and raw['new_state'] is None
                    and self.hass.states.get(self.source_id) is None):
                return None
            frame = frame.f_back
            if self.source_handoff is not None:
                h = self.source_handoff
                return ('remove' if frame is not None and frame.f_code is self.handoff_codes[0]
                    and frame.f_globals is self.handoff_methods[0].__globals__
                    and frame.f_lasti in self.handoff_sites[1] and frame.f_locals.get('self') is h
                    and h.in_native and h.attempted and h.old_retired
                    and raw['old_state'] is h.original_state is self.handoff_state
                    and self.source.registry_entry is h.new_row and self.registry.async_get(self.target_id) is h.new_row
                    and self.hass.states.get(self.target_id) is frame.f_locals.get('state')
                    and self.in_mutate else None)
            if not self._edge(frame, 'remove_impl', 'remove_state', self.source):
                return None
            if frame.f_locals.get('force_remove') is not True:
                return None
            frame = frame.f_back
            if not self._edge(frame, 'remove', 'remove_impl', self.source):
                return None
            if frame.f_locals.get('force_remove') is not True:
                return None
            frame = frame.f_back
            if self._edge(frame, 'source', 'source_remove', self.source) and self._source_frame(frame):
                return 'remove'
            return None
        if not self._edge(frame, 'set_state', 'set_fire', self.hass.states):
            return None
        if not (raw['entity_id'] == self.target_id and raw['old_state'] is None
                and values.get('old_state') is None and type(raw['new_state']) is self.State
                and raw['new_state'] is values.get('state')
                and raw['new_state'] is self.hass.states.get(self.target_id)
                and raw['new_state'].entity_id == self.target_id
                and raw['new_state'].context is fire.f_locals.get('context')):
            return None
        chain = (('write', 'set_state', self.source), ('write_public', 'write', self.source),
                 ('finish_add', 'write_public', self.source),
                 ('platform_add_one', 'finish_add', self.platform),
                 ('platform_add_many', 'platform_add_one', self.platform),
                 ('platform_add', 'platform_add_many', self.platform),
                 ('source', 'source_add', self.source))
        if self.source_handoff is not None:
            # New ADD is a real write in the SAME native registry handler's
            # same-id branch, without platform add/remove ancestry.
            for key, site, obj in chain[:2]:
                frame = frame.f_back
                if not self._edge(frame, key, site, obj):
                    return None
            frame = frame.f_back
            h = self.source_handoff
            return ('add' if self._frame(frame, 'source', self.source)
                and frame.f_lasti in self.handoff_sites[0] and self._source_frame(frame)
                and frame.f_locals.get('old') is h.new_row and frame.f_locals.get('registry_entry') is h.new_row
                and h.in_native and h.attempted and self.source.registry_entry is h.new_row
                and self.registry.async_get(self.target_id) is h.new_row
                and self.hass.states.get(self.source_id) is h.original_state is self.handoff_state
                and self.in_mutate else None)
        for key, site, obj in chain:
            frame = frame.f_back
            if not self._edge(frame, key, site, obj):
                return None
            local = frame.f_locals
            if key == 'platform_add_one' and not (local.get('entity') is self.source
                                                and local.get('update_before_add') is False):
                return None
            if key == 'platform_add_many' and not (type(local.get('entities')) is list
                    and len(local['entities']) == 1 and local['entities'][0] is self.source):
                return None
            if key == 'platform_add' and not (local.get('update_before_add') is False
                    and type(local.get('new_entities')) is list and len(local['new_entities']) == 1
                    and local['new_entities'][0] is self.source):
                return None
        return 'add' if self._source_frame(frame) else None

    def _fire(self, frame):
        try:
            self._observe_fire(frame)
        except BaseException as error:
            self._observer_error(error, 'emission_observer_failed')

    def _observe_fire(self, frame):
        # No guard here: native child tasks do not own the caller's capability.
        if self.closed or self.error is not None or not self.attempted:
            return
        _need(threading.get_ident() == self.thread and asyncio.get_running_loop() is self.loop,
              'emitter_runtime_changed')
        _need(self._frame(frame, 'fire', self.bus), 'emitter_binding_changed')
        self.occurrences.check()
        values = frame.f_locals
        raw, kind = values.get('event_data'), values.get('event_type')
        if type(raw) is not dict:
            return
        phase = None
        if kind == 'entity_registry_updated':
            if self._registry_cause(frame, raw):
                phase = 'registry'
        elif kind == 'state_changed' and raw.get('entity_id') in (self.source_id, self.target_id):
            phase = self._state_cause(frame, raw)
        if phase is not None:
            _need(not any(item.phase == phase for item in self.emissions), 'duplicate_native_phase')
            receipt = _Emission(phase, raw, values.get('context'), values.get('origin'), id(frame))
            if phase != 'registry':
                receipt.binding = (raw['entity_id'], raw['old_state'], raw['new_state'])
                receipt.semantic = _semantic_state(raw['old_state'] if phase == 'remove' else raw['new_state'],
                                                   native_attribute_key=self.attribute_key)
            self.emissions.append(receipt)
            if not self.queued:
                self.dispatches[id(frame)] = receipt
        else:
            receipt = None
        if self.queued and (receipt is not None or any(item.raw is raw for item in self.emissions)):
            # Record only occurrences of our <=3 raw dictionaries. A nested
            # producer re-emission has its own FIFO entry with NO capability.
            _need(len(self.pending) < _MAX_OCCURRENCES, 'occurrence_overflow')
            self.pending.append((kind, raw, receipt))

    def _started(self, frame):
        if self.closed or self.error is not None:
            return
        if self.preflight is not None:
            self.preflight.started(frame)
        code, values = frame.f_code, frame.f_locals
        if code is self.codes['source']:
            self._source_started(frame)
        elif self.queued and code is self.codes['dispatch'] and values.get('self') is self.bus:
            for index, (kind, raw, receipt) in enumerate(self.pending):
                if raw is values.get('event_data') and kind == values.get('event_type'):
                    self.pending.pop(index)
                    if receipt is not None:
                        self.dispatches[id(frame)] = receipt
                    break
        elif code is self.codes['event']:
            parent = frame.f_back
            receipt = self.dispatches.get(id(parent))
            if receipt is not None:
                _need(self._frame(parent, 'dispatch', self.bus) and receipt.event is None
                      and values.get('data') is receipt.raw, 'constructor_occurrence_changed')
                self.constructors[id(frame)] = (receipt, values.get('self'))
        elif code in (self.codes['keyed'], self.codes['registry_keyed']) and values.get('hass') is self.hass:
            if self._owned_event(values.get('event')):
                _need(len(self.keyed) < _MAX_OCCURRENCES, 'dispatch_overflow')
                self.keyed[id(frame)] = (values.get('callbacks'), values.get('event'))

    def _source_started(self, frame):
        registry = self._registry_emission()
        values = frame.f_locals
        if registry is None or registry.event is None or values.get('self') is not self.source:
            return
        if values.get('event') is not registry.event:
            return
        _need(self._registry_data(registry.raw), 'source_registry_data_changed')
        _need(self.source_runs == 0, 'duplicate_source_task')
        creator = callback = None
        parent = frame.f_back
        for _ in range(_MAX_DEPTH):
            if parent is None:
                break
            if self._frame(parent, 'create_task', self.hass):
                creator = parent
            if self._frame(parent, 'registry_callback', self.source):
                callback = parent
                break
            parent = parent.f_back
        _need(creator is not None and callback is not None
              and callback.f_lasti in self.sites['source_task']
              and callback.f_locals.get('event') is registry.event, 'source_task_provenance')
        job_frame = callback.f_back
        _need(self._edge(job_frame, 'run_job', 'job_target', self.hass)
              and getattr(job_frame.f_locals.get('hassjob').target, '__self__', None) is self.source
              and getattr(job_frame.f_locals.get('hassjob').target, '__func__', None)
                  is self.methods['registry_callback'], 'source_callback_provenance')
        dispatcher = job_frame.f_back
        _need(self._edge(dispatcher, 'registry_keyed', 'registry_job', None)
              and dispatcher.f_locals.get('hass') is self.hass
              and dispatcher.f_locals.get('event') is registry.event
              and id(dispatcher) in self.keyed, 'source_dispatch_provenance')
        outer_job = dispatcher.f_back
        _need(self._edge(outer_job, 'run_job', 'job_target', self.hass), 'source_bus_job_provenance')
        outer_target = outer_job.f_locals.get('hassjob').target
        _need(type(outer_target) is partial and outer_target.func is self.methods['registry_keyed']
              and len(outer_target.args) == 2 and outer_target.args[0] is self.hass
              and outer_target.args[1] is dispatcher.f_locals.get('callbacks')
              and not outer_target.keywords, 'source_bus_target_provenance')
        bus_dispatch = outer_job.f_back
        _need(self._frame(bus_dispatch, 'dispatch', self.bus)
              and self.dispatches.get(id(bus_dispatch)) is registry
              and bus_dispatch.f_locals.get('event') is registry.event,
              'source_original_dispatch_required')
        task, coroutine = asyncio.current_task(), creator.f_locals.get('target')
        _need(task is not None and task is not self.owner and task.get_coro() is coroutine
              and getattr(coroutine, 'cr_frame', None) is frame
              and creator.f_locals.get('eager_start') is True, 'actual_source_task_required')
        self.tasks[task] = [coroutine, id(frame), False]
        self.source_runs += 1

    def _ended(self, frame, failed):
        if self.preflight is not None:
            self.preflight.ended(frame, failed)
        code, key = frame.f_code, id(frame)
        changed = False
        if code is self.codes['event']:
            pair = self.constructors.pop(key, None)
            if pair is not None:
                receipt, event = pair
                _need(not failed and type(event) is self.Event
                      and frame.f_locals.get('self') is event
                      and event.data is receipt.raw and event.origin is receipt.origin
                      and self._data_current(receipt)
                      and event.event_type == ('entity_registry_updated' if receipt.phase == 'registry'
                                               else 'state_changed')
                      and (receipt.context is None or event.context is receipt.context),
                      'constructed_event_changed')
                receipt.event = event
                receipt.event_context = event.context
        if code is self.codes['source']:
            record = self.tasks.get(asyncio.current_task())
            if record is not None and record[1] == key:
                changed = True
                record[2] = not failed
                if failed:
                    self._poison('source_task_unwound')
        receipt = self.dispatches.pop(key, None)
        if receipt is not None:
            changed = True
            receipt.dispatched = not failed
            if failed:
                self._poison('native_dispatch_unwound')
        if key in self.keyed:
            changed = True
            self.keyed.pop(key)
            if failed:
                self._poison('keyed_dispatch_unwound')
        if changed:
            self._signal()

    def _owned_event(self, event):
        return any(item.event is event and event is not None for item in self.emissions)

    def is_owned_rename(self, event):
        """Cause only; does not establish state/attribute neutrality."""
        if self.error is not None or event is None:
            return False
        for item in self.emissions:
            if item.phase in ('remove', 'add') and item.event is event:
                if (event.data is not item.raw or not self._data_current(item)
                        or event.context is not item.event_context or event.origin is not item.origin
                        or event.event_type != 'state_changed'):
                    self._poison('owned_event_changed')
                    return False
                return True
        return False

    def suppresses_template_boundary(self, event):
        """Only a structural remove or a proven data-neutral native re-add.

        Meaningful producer data first published by native re-add must reach
        the consumer. Unknown snapshots also pass through. This is useful only
        within a root-owned rename ingress; it never authorizes a mutation.
        """
        if not self.is_owned_rename(event):
            return False
        item = next(item for item in self.emissions if item.event is event)
        if item.phase == 'remove':
            return True
        removed = next((row for row in self.emissions if row.phase == 'remove'), None)
        if self.source_handoff is not None and removed is None:
            # ADD arrives before REMOVE in this driver. The genuine current
            # physical old State was captured at the exact owning API boundary;
            # it is not installed as a new State or treated as a render result.
            return (self.handoff_semantic is not None and item.semantic is not None
                    and self.handoff_semantic == item.semantic)
        return (removed is not None and removed.semantic is not None
                and item.semantic is not None and removed.semantic == item.semantic)

    def mutate(self):
        self._owner_guard()
        _need(not self.attempted, 'attempt_already_consumed')
        self._check()
        _need(self.destination == self.target_row and self.source.registry_entry is self.row
              and self.registry.async_get(self.source_id) is self.row
              and self.registry.async_get(self.target_id) is None
              and self.source.entity_id == self.source_id
              and self.platform.entities.get(self.source_id) is self.source,
              'pre_mutation_binding_changed')
        self.attempted = self.in_mutate = True
        try:
            # This is the ONLY real mutation. The fire witness verifies this
            # exact frame and the SAME prepared/target objects inside the IO.
            if self.source_handoff is None:
                self.mutation = self.io.mutate(self.change, self.destination)
            else:
                self.handoff_state = self.hass.states.get(self.source_id)
                _need(type(self.handoff_state) is self.State, 'source_handoff_state_missing')
                self.handoff_semantic = _semantic_state(self.handoff_state, native_attribute_key=self.attribute_key)
                self.mutation = self.source_handoff.mutate()
            _need(type(self.mutation) is NativeRegistryMutation, 'native_mutation_return')
            return self.mutation
        except BaseException:
            self._poison('mutation_uncertain')
            self.close()
            raise
        finally:
            self.in_mutate = False

    def _pending_dispatches(self):
        pending = bool(self.pending or self.dispatches or self.constructors or self.keyed)
        # Only actual native keyed deliveries of our original Events count.
        # Never execute/cancel a Handle or retain the rest of the ready queue.
        count = 0
        for handle in self.loop._ready:
            if handle.cancelled() or handle._callback is not self.methods['keyed']:
                continue
            args = handle._args
            if len(args) == 3 and args[0] is self.hass and self._owned_event(args[2]):
                _need(type(args[1]) in (dict, defaultdict), 'keyed_callback_graph_changed')
                count += 1
                _need(count <= _MAX_OCCURRENCES, 'ready_dispatch_overflow')
                pending = True
        return pending

    async def finish(self):
        try:
            # Revocation cannot skip draining our already-issued native call.
            # The caller authorization fence runs after physical completion.
            self._owner_guard(authorize=False)
            _need(self.attempted and self.mutation is not None and not self.finished,
                  'mutation_required')
            self._check()
            # Existing UUID-scoped source fence, supplemented by actual eager
            # Task objects captured at PY_START even if all_tasks misses them.
            await self.io.settle(self.change)
            while True:
                self._check()
                tasks = [task for task in self.tasks if not task.done()]
                if tasks:
                    await asyncio.gather(*(asyncio.shield(task) for task in tasks))
                    continue
                self.wake = self.loop.create_future()
                if self._pending_dispatches():
                    await self.wake
                    self.wake = None
                    continue
                self.wake = None
                break
            source_complete = (self.source_runs == 1 and len(self.tasks) == 1
                and all(task.done() and not task.cancelled() and task.exception() is None
                        and record[2] for task, record in self.tasks.items()))
            dispatch_complete = (not self._pending_dispatches() and len(self.emissions) == 3
                                 and all(item.dispatched for item in self.emissions))
            counts = {phase: sum(item.phase == phase for item in self.emissions)
                      for phase in ('remove', 'add')}
            _need(source_complete and dispatch_complete and counts == {'remove': 1, 'add': 1},
                  'native_evidence_incomplete')
            row = self.registry.async_get(self.target_id)
            _need(row is not None and row.id == self.change.identity
                  and self.registry.async_get(self.source_id) is None
                  and self.source.registry_entry is row and self.source.entity_id == self.target_id
                  and self.platform.entities.get(self.target_id) is self.source
                  and self.hass.states.get(self.target_id) is not None
                  and self.hass.states.get(self.source_id) is None, 'final_source_readback')
            # No await separates the empty own-dispatch scan, caller fence,
            # binding recheck and retirement. Native callbacks never run guard.
            self._owner_guard()
            self._check()
            self.finished = True
            snapshots = {item.phase: item.semantic for item in self.emissions if item.phase in ('remove', 'add')}
            neutral = (snapshots['remove'] is not None and snapshots['add'] is not None
                       and snapshots['remove'] == snapshots['add'])
            return {'rename_remove': counts['remove'], 'rename_add': counts['add'],
                    'neutral': neutral,
                    'source_tasks_complete': source_complete,
                    'dispatches_complete': dispatch_complete}
        except BaseException:
            self._poison('finish_uncertain')
            raise
        finally:
            self.close()

    def close(self):
        """Idempotent monitoring cleanup; neither source cancellation nor proof."""
        if self.closed:
            return
        self.closed = True
        if self.source_handoff is not None:
            self.source_handoff.close()
        try:
            if self.fire_monitor is not None:
                self.fire_monitor.close()
        finally:
            if self.occurrences is not None:
                self.occurrences.close()
            self._signal()
