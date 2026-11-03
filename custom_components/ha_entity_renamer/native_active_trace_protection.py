"""Preserve real native action-trace configuration during an active cutover.

Core retains the actor's live raw_config by reference in historical traces. The
active program projector must update future native action slots, so retaining
that reference would retroactively change their recorded configuration. This
helper detaches only positively identified native trace config edges, using the
existing pre-effect log, before the projector touches its first future slot.
No State, Event, Context, run, task, wait or trace contents are rewritten.
"""
from __future__ import annotations

import asyncio
from collections import ChainMap, deque
from pathlib import Path
from types import MappingProxyType

from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('active_trace_' + reason) from None


def _clone_config(root):
    """Clone only the bounded native YAML tree, retaining immutable leaves."""
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass, NodeStrClass
    memo, building, budget = {}, set(), [100_000]

    def visit(value):
        budget[0] -= 1
        _need(budget[0] >= 0, 'config_limit')
        kind = type(value)
        if value is None or kind in (str, NodeStrClass, int, float, bool):
            return value
        _need(kind in (dict, NodeDictClass, list, NodeListClass, tuple), 'config_type_unsupported')
        ident = id(value)
        _need(ident not in building, 'config_cycle_unsupported')
        if ident in memo:
            return memo[ident]
        building.add(ident)
        try:
            if kind in (dict, NodeDictClass):
                clone = {} if kind is dict else NodeDictClass()
                memo[ident] = clone
                for key, child in value.items():
                    _need(type(key) in (str, NodeStrClass), 'config_key_unsupported')
                    clone[key] = visit(child)
            elif kind in (list, NodeListClass):
                clone = [] if kind is list else NodeListClass()
                memo[ident] = clone
                clone.extend(visit(child) for child in value)
            else:
                clone = tuple(visit(child) for child in value)
                memo[ident] = clone
            if kind in (NodeDictClass, NodeListClass):
                for name in ('__config_file__', '__line__'):
                    if hasattr(value, name):
                        original = getattr(value, name)
                        _need(type(original) in (str, int), 'config_metadata_unsupported')
                        setattr(clone, name, original)
            return clone
        finally:
            building.remove(ident)

    result = visit(root)
    from .yaml_validation_worker import _normal
    _need(_normal(result) == _normal(root), 'config_copy_changed')
    return result


def _contains(value, targets, seen=None, budget=None):
    """Walk known native trace/config values, never arbitrary Python heap."""
    from homeassistant.core import Event, State
    from homeassistant.helpers.script_variables import ScriptRunVariables, ScriptVariables, _ParallelData
    from homeassistant.helpers.trace import TraceElement
    from homeassistant.util.read_only_dict import ReadOnlyDict
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass
    if seen is None:
        seen, budget = set(), [200_000]
    budget[0] -= 1
    _need(budget[0] >= 0, 'graph_limit')
    ident = id(value)
    if ident in targets:
        return True
    if ident in seen:
        return False
    seen.add(ident)
    kind = type(value)
    children = ()
    if kind in (dict, NodeDictClass, MappingProxyType, ReadOnlyDict):
        children = tuple(value.values())
    elif kind in (list, tuple, NodeListClass, deque):
        children = value
    elif kind is TraceElement:
        children = tuple(getattr(value, name, None) for name in
                         ('_variables', '_last_variables', '_result', '_template_errors'))
    elif kind is ScriptRunVariables:
        children = (value._local_data, value._previous, value._parent, value._parallel_data,
                    value._non_parallel_scope, value._full_scope)
    elif kind is _ParallelData:
        children = (value.protected, value.outer_scope_writes)
    elif kind is ChainMap:
        children = value.maps
    elif kind is ScriptVariables:
        children = (value.variables,)
    elif kind is State:
        children = (value.attributes,)
    elif kind is Event:
        children = (value.data,)
    return any(_contains(child, targets, seen, budget) for child in children)


def _mutable_containers(root):
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass
    seen, result = set(), set()
    def visit(value):
        if id(value) in seen:
            return
        seen.add(id(value))
        if type(value) in (dict, NodeDictClass):
            result.add(id(value))
            for child in value.values():
                visit(child)
        elif type(value) in (list, NodeListClass):
            result.add(id(value))
            for child in value:
                visit(child)
        elif type(value) is tuple:
            for child in value:
                visit(child)
    visit(root)
    return frozenset(result)


class NativeActiveTraceProtection:
    """Task-local native trace owners and checked pre-attempt detachment."""

    @classmethod
    def capture(cls, hass, entity, script, pin, projector, *, trace_keys=None):
        import homeassistant
        from homeassistant.components.automation import AutomationEntity
        from homeassistant.components.automation.trace import AutomationTrace
        from homeassistant.components.script import ScriptEntity
        from homeassistant.components.script.trace import ScriptTrace
        from homeassistant.components.trace import DATA_TRACE
        from homeassistant.components.trace import models, util
        from homeassistant.const import __version__
        from homeassistant.helpers.trace import TraceElement
        from homeassistant.util.limited_size_dict import LimitedSizeDict
        from .native_automation_state_binding import _PINS as automation_pins
        from .native_source_handoff import _SCRIPT_PINS
        from .native_unload_code_proof import prove_methods

        trace_pins = {
            '2025.6.0': ('e5d8879aa712c080467177dc61d17f8adfe45c4a7e967ace3cfcdf2865b7e560',
                          '3752a95b4f46a9242c336d8fb8ce3c442d8780901b02264b202a5c2ee57f4c12',
                          'abd5983c80860e461913c10edf3884923d11fe826a73e1a70a21c35edcf85f3f'),
            '2026.10.4': ('41ccc5fda5790f1ad6c2e473509903a0107bb0e6816018bd8f4449de9b5b807c',
                          '8d2775c760a3306ba599a69faa82dcc58ab39602204ad582fcaeb9e8bfb2cc46',
                          '3735ccdf467c2e5242055948803e08b6fd9e83a8d404d81562edebd1f924a6f1'),
        }
        _need(__version__ in trace_pins and asyncio.current_task() is not None
              and type(entity) in (ScriptEntity, AutomationEntity), 'scope_unsupported')
        domain = 'script' if type(entity) is ScriptEntity else 'automation'
        _need((entity.script if domain == 'script' else entity.action_script) is script
              and pin.root is script and type(entity.unique_id) is str,
              'actor_changed')
        pins = {'components/trace/models.py': trace_pins[__version__][0],
                'components/trace/util.py': trace_pins[__version__][1],
                'helpers/trace.py': trace_pins[__version__][2],
                'components/script/__init__.py': _SCRIPT_PINS[__version__],
                'components/automation/__init__.py': automation_pins[__version__]['components/automation/__init__.py']}
        refs = ((models.ActionTrace, '__init__'), (util, 'async_store_trace'),
                (ScriptEntity, '_async_run'), (AutomationEntity, 'async_trigger'),
                (TraceElement, '__init__'))
        methods, codes = prove_methods(Path(homeassistant.__file__).resolve().parent, pins, refs)
        store = hass.data.get(DATA_TRACE)
        _need(type(store) is dict and type(projector.operations) in (list, tuple), 'store_unavailable')
        obj = object.__new__(cls)
        obj.hass, obj.entity, obj.script, obj.pin, obj.projector = hass, entity, script, pin, projector
        obj.task, obj.version, obj.store = asyncio.current_task(), __version__, store
        current_key = domain + '.' + entity.unique_id
        if trace_keys is None:
            trace_keys = frozenset((current_key,))
        _need(type(trace_keys) is frozenset and current_key in trace_keys and len(trace_keys) <= 2
              and all(type(key) is str and key.startswith(domain + '.') for key in trace_keys),
              'trace_key_scope_unqualified')
        obj.keys = trace_keys
        obj.data_key, obj.refs, obj.methods, obj.codes = DATA_TRACE, refs, methods, codes
        obj.trace_types, obj.restored_type = (ScriptTrace, AutomationTrace), models.RestoredTrace
        obj.trace_element = TraceElement
        obj.bucket_type, obj.limited_type = getattr(models, 'TraceBuckets', None), LimitedSizeDict
        obj.targets = frozenset(id(container) for container, _, _, _ in projector.operations)
        _need(bool(obj.targets), 'changed_slots_missing')
        obj.owners = obj._owners()
        from homeassistant.core import Context
        contexts = tuple(run._context for run in pin.root._runs)
        _need(len(contexts) == 2 and all(type(context) is Context for context in contexts)
              and len({id(context) for context in contexts}) == 2
              and all(sum(trace.context is context for trace, _ in obj.owners) == 1 for context in contexts),
              'accepted_run_trace_missing')
        raw = entity.raw_config
        clone = _clone_config(raw)
        _need(not _contains(clone, obj.targets), 'copy_alias_changed')
        obj.raw, obj.clone = raw, clone
        from .native_script_config_transport import _graph
        obj.clone_graph = _graph(clone)
        obj.clone_targets = _mutable_containers(clone)
        obj.edges = tuple((trace, trace.context, trace.key, trace.run_id, trace.__dict__)
                          for trace, _ in obj.owners)
        obj.phase = 'captured'
        obj._foreign_aliases()
        return obj

    def _foreign_aliases(self):
        # Native actor config, retained run variables, State/Event and actual
        # registered listener closures are the existing product census. A
        # foreign edge to the trace object cannot be silently adopted merely
        # because its _config is the selected actor's raw config.
        from .native_active_execution_transport import _Projection, _entities
        projection = _Projection(self.hass, set())
        projection.targets.update(id(trace) for trace, _, _, _, _ in self.edges)
        projection.targets.update(self.clone_targets)
        projection.scan(self.hass, _entities(self.hass), ())

    def _native_aliases(self):
        from homeassistant.components.trace import DATA_TRACE
        _need(DATA_TRACE is self.data_key and self.hass.data.get(DATA_TRACE) is self.store,
              'store_changed')
        from homeassistant.components.automation import trace as automation_trace
        from homeassistant.components.script import trace as script_trace
        from homeassistant.components.trace import models, util
        from homeassistant.helpers import trace as trace_helper
        import inspect
        _need(models.ActionTrace is self.refs[0][0] and models.RestoredTrace is self.restored_type
              and trace_helper.TraceElement is self.trace_element and models.TraceElement is self.trace_element
              and util.DATA_TRACE is self.data_key
              and script_trace.ScriptTrace is self.trace_types[0]
              and automation_trace.AutomationTrace is self.trace_types[1]
              and inspect.getattr_static(self.trace_types[0], '__init__') is self.methods[0]
              and inspect.getattr_static(self.trace_types[1], '__init__') is self.methods[0]
              and (self.version == '2025.6.0' or models.TraceBuckets is self.bucket_type),
              'native_alias_changed')

    def _stored(self):
        self._native_aliases()
        rows = []
        for key, bucket in self.store.items():
            _need(type(key) is str, 'store_key_changed')
            if self.version == '2025.6.0':
                _need(type(bucket) is self.limited_type, 'bucket_changed')
                groups = (bucket,)
            else:
                _need(type(bucket) is self.bucket_type, 'bucket_changed')
                groups = (bucket.runs, bucket.not_triggered)
                _need(all(type(group) is self.limited_type for group in groups), 'bucket_changed')
            for group in groups:
                for run_id, trace in group.items():
                    _need(type(trace) in (*self.trace_types, self.restored_type)
                          and type(run_id) is str and trace.run_id == run_id and trace.key == key,
                          'store_entry_changed')
                    rows.append((trace, key))
        return tuple(rows)

    def _owners(self):
        rows = self._stored()
        owners = []
        seen = set()
        for trace, key in rows:
            _need(id(trace) not in seen, 'foreign_trace_alias')
            seen.add(id(trace))
            if type(trace) in self.trace_types:
                _need(not _contains(trace._trace, self.targets)
                      and not _contains(trace._blueprint_inputs, self.targets)
                      and not _contains(trace._short_dict, self.targets)
                      and not _contains(trace._dict, self.targets), 'trace_history_alias')
                if _contains(trace._config, self.targets):
                    _need(key in self.keys and trace._config is self.entity.raw_config
                          and trace._dict is None and type(trace).__setattr__ is object.__setattr__
                          and type(trace.__dict__) is dict and trace.__dict__.get('_config') is trace._config,
                          'foreign_trace_config_alias')
                    owners.append((trace, key))
            elif type(trace) is self.restored_type:
                _need(not _contains(trace._dict, self.targets)
                      and not _contains(trace._short_dict, self.targets), 'restored_history_alias')
            else:
                raise Refused('active_trace_foreign_trace_type') from None
        return tuple(owners)

    def check_review(self):
        """Recheck the review-owned inventory in a later apply task."""
        from .native_unload_code_proof import unchanged
        _need(self.phase == 'captured' and self.entity.raw_config is self.raw, 'owner_changed')
        unchanged(self.refs, self.methods, self.codes)
        self._foreign_aliases()
        current = self._owners()
        _need(len(current) == len(self.owners)
              and all(a is b and ka == kb for (a, ka), (b, kb) in zip(current, self.owners, strict=True)),
              'owner_inventory_changed')
        _need(all(trace.__dict__ is storage and trace.context is context
                  and trace.key == key and trace.run_id == run_id
                  and trace._config is self.raw and trace._dict is None
                  for trace, context, key, run_id, storage in self.edges), 'owner_changed')

    def check_before(self):
        _need(asyncio.current_task() is self.task, 'owner_changed')
        self.check_review()

    def retain(self, log):
        for trace, _, _, _, _ in self.edges:
            log.retain('_config', trace)

    def install(self, log):
        self.check_before()
        self.phase = 'installing'
        for trace, _, _, _, _ in self.edges:
            log.write('_config', trace, self.clone,
                      lambda trace=trace: setattr(trace, '_config', self.clone))
        self.phase = 'installed'

    def check_installed(self, *, strict):
        from .native_unload_code_proof import unchanged
        _need(asyncio.current_task() is self.task and self.phase in ('installing', 'installed'),
              'owner_changed')
        unchanged(self.refs, self.methods, self.codes)
        self._native_aliases()
        from .native_script_config_transport import _graph
        _need(_graph(self.clone) == self.clone_graph, 'copy_changed')
        _need(all(trace.__dict__ is storage and trace.context is context
                  and trace.key == key and trace.run_id == run_id
                  and (trace._config is self.clone or self.phase == 'installing' and trace._config is self.raw)
                  and (not strict or trace._dict is None)
                  for trace, context, key, run_id, storage in self.edges), 'owner_changed')
        if strict:
            self._foreign_aliases()
            rows = self._stored()
            _need(all(any(actual is trace and actual_key == key for actual, actual_key in rows)
                      for trace, _, key, _, _ in self.edges), 'owner_inventory_changed')
            # A newly introduced historical owner of a changed original slot
            # must not be silently altered while undo or installation proceeds.
            selected = {id(trace) for trace, _, _, _, _ in self.edges}
            for trace, _ in rows:
                if id(trace) in selected:
                    _need(not _contains(trace._trace, self.targets | self.clone_targets)
                          and not _contains(trace._blueprint_inputs, self.targets | self.clone_targets)
                          and not _contains(trace._short_dict, self.targets | self.clone_targets),
                          'trace_history_alias')
                elif type(trace) in self.trace_types:
                    checked = self.targets | self.clone_targets
                    _need(not _contains(trace._config, checked)
                          and not _contains(trace._trace, checked)
                          and not _contains(trace._blueprint_inputs, checked)
                          and not _contains(trace._short_dict, checked)
                          and not _contains(trace._dict, checked), 'foreign_trace_config_alias')
                elif type(trace) is self.restored_type:
                    checked = self.targets | self.clone_targets
                    _need(not _contains(trace._dict, checked)
                          and not _contains(trace._short_dict, checked), 'restored_history_alias')
                else:
                    raise Refused('active_trace_foreign_trace_type') from None

    def restored(self):
        _need(all(trace._config is self.raw for trace, _, _, _, _ in self.edges),
              'restore_changed')
        self.phase = 'restored'
