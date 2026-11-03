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
from weakref import WeakKeyDictionary

from .transaction import Refused

# In-memory provenance exists only for real running traces detached here.
_ANCHOR_KEY = object()


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
    def capture(cls, hass, entity, script, pin, projector, *, trace_keys=None, detached_expected=None, trace_scope=None):
        import homeassistant
        from homeassistant.components.automation import AutomationEntity
        from homeassistant.components.automation.trace import AutomationTrace
        from homeassistant.components.script import ScriptEntity
        from homeassistant.components.script.trace import ScriptTrace
        from homeassistant.components.trace import DATA_TRACE
        from homeassistant.components.trace import models, util
        from homeassistant.const import __version__
        from homeassistant.helpers.trace import TraceElement
        from homeassistant.helpers.script import Script
        from homeassistant.util.limited_size_dict import LimitedSizeDict
        from .native_automation_state_binding import _PINS as automation_pins
        from .native_source_handoff import _SCRIPT_PINS
        from .native_unload_code_proof import prove_methods

        trace_pins = {
            '2025.6.0': ('e5d8879aa712c080467177dc61d17f8adfe45c4a7e967ace3cfcdf2865b7e560',
                          '3752a95b4f46a9242c336d8fb8ce3c442d8780901b02264b202a5c2ee57f4c12',
                          'abd5983c80860e461913c10edf3884923d11fe826a73e1a70a21c35edcf85f3f',
                          '2d9444d77b93cea4e6232c23c101045661d713817153a21ba22a35595c97ec4d'),
            '2026.10.4': ('41ccc5fda5790f1ad6c2e473509903a0107bb0e6816018bd8f4449de9b5b807c',
                          '8d2775c760a3306ba599a69faa82dcc58ab39602204ad582fcaeb9e8bfb2cc46',
                          '3735ccdf467c2e5242055948803e08b6fd9e83a8d404d81562edebd1f924a6f1',
                          'f8da595e040a7ab80f2f9c963c18209bf554ef83d6ab0f0f0f422bc5df4124cd'),
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
                'components/automation/trace.py': trace_pins[__version__][3],
                'components/script/__init__.py': _SCRIPT_PINS[__version__],
                'components/automation/__init__.py': automation_pins[__version__]['components/automation/__init__.py'],
                'helpers/script.py': {'2025.6.0': 'c78dec6e98825b8ebdc0593061ad821f34735951ef564cc2a96231d04d9477e2',
                                      '2026.10.4': '75376abc1686bcfde2f5d235863728b630b91f703ad03eb1a4b6a22eb8387ee3'}[__version__]}
        refs = ((models.ActionTrace, '__init__'), (util, 'async_store_trace'),
                (ScriptEntity, '_async_run'), (AutomationEntity, 'async_trigger'),
                (TraceElement, '__init__'), (AutomationTrace, '__init__'), (Script, 'async_run'))
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
        obj.keys, obj.trace_scope = trace_keys, trace_scope
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
              and len({id(context) for context in contexts}) == 2, 'accepted_run_trace_missing')
        rows = obj._stored()
        from .yaml_validation_worker import _normal
        from .native_script_config_transport import _graph
        passive, lineage = [], []
        raw = entity.raw_config
        for context, run_pin in zip(contexts, (pin.parent, pin.queued), strict=True):
            matches = [(trace, key) for trace, key in rows if type(trace) in obj.trace_types
                       and key in obj.keys and trace.context is context]
            _need(len(matches) == 1, 'accepted_run_trace_missing')
            trace, key = matches[0]
            lineage.append((run_pin, trace, obj._lineage(run_pin, trace)))
            anchor = hass.data.get(_ANCHOR_KEY)
            _need(anchor is None or type(anchor) is WeakKeyDictionary, 'anchor_changed')
            proof = anchor.get(trace) if anchor is not None else None
            # Check an existing anchor before owner/passive classification: an
            # externally reattached config must not become a fresh live owner.
            if proof is not None:
                _need(type(proof) is tuple and len(proof) == 11
                      and proof[0] is context and proof[1] is trace._config
                      and proof[2] == _graph(trace._config) and proof[3] is entity
                      and proof[4] is script and proof[5] is run_pin.run
                      and proof[6] is run_pin.task and proof[7] == trace_scope
                      and proof[8] == key and proof[9] == trace.run_id
                      and proof[10] is trace.__dict__, 'detached_trace_unqualified')
            if any(trace is owner for owner, _ in obj.owners):
                _need(proof is None, 'anchor_owner_already_detached')
                continue
            # Only an own previously detached trace can be passive.
            _need(proof is not None and detached_expected is not None and trace._dict is None
                  and type(trace.__dict__) is dict and trace.__dict__.get('_config') is trace._config
                  and not _contains(trace._config, _mutable_containers(raw))
                  and _normal(trace._config) in detached_expected, 'detached_trace_unqualified')
            passive.append((trace, context, key, trace.run_id, trace.__dict__, trace._config,
                            _graph(trace._config), anchor, proof))
        obj.passive, obj.lineage = tuple(passive), tuple(lineage)
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
        import builtins
        import inspect
        from types import FunctionType
        # Recover CPython's immutable, non-heap builtin type independently of
        # the mutable builtins mapping used by the native constructor.
        object_type = ().__class__.__mro__[-1]
        type_type = ().__class__.__class__
        native_supers = tuple(candidate for candidate in object_type.__subclasses__()
            if type(candidate) is type_type and candidate.__module__ == 'builtins'
            and candidate.__name__ == 'super' and not candidate.__flags__ & (1 << 9))
        constructor = self.methods[5]
        closure = constructor.__closure__ if type(constructor) is FunctionType else None
        kwdefaults = constructor.__kwdefaults__ if type(constructor) is FunctionType else None
        expected_kwdefaults = (kwdefaults is None if self.version == '2025.6.0' else
            type(kwdefaults) is dict and set(kwdefaults) == {'not_triggered'}
            and kwdefaults['not_triggered'] is False)
        _need(type(constructor) is FunctionType
              and constructor.__globals__ is vars(automation_trace)
              and constructor.__builtins__ is vars(builtins)
              and len(native_supers) == 1
              and constructor.__builtins__.get('super') is native_supers[0]
              and 'super' not in constructor.__globals__
              and constructor.__code__.co_freevars == ('__class__',)
              and type(closure) is tuple and len(closure) == 1
              and closure[0].cell_contents is self.trace_types[1]
              and constructor.__defaults__ is None and expected_kwdefaults
              and not constructor.__dict__
              and models.ActionTrace is self.refs[0][0] and models.RestoredTrace is self.restored_type
              and trace_helper.TraceElement is self.trace_element and models.TraceElement is self.trace_element
              and util.DATA_TRACE is self.data_key
              and script_trace.ScriptTrace is self.trace_types[0]
              and automation_trace.AutomationTrace is self.trace_types[1]
              and inspect.getattr_static(self.trace_types[0], '__init__') is self.methods[0]
              and inspect.getattr_static(self.trace_types[1], '__init__') is self.methods[5]
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
        anchor = self.hass.data.get(_ANCHOR_KEY)
        _need(anchor is None or type(anchor) is WeakKeyDictionary, 'anchor_changed')
        for trace, key in rows:
            _need(id(trace) not in seen, 'foreign_trace_alias')
            seen.add(id(trace))
            if type(trace) in self.trace_types:
                _need(not _contains(trace._trace, self.targets)
                      and not _contains(trace._blueprint_inputs, self.targets)
                      and not _contains(trace._short_dict, self.targets)
                      and not _contains(trace._dict, self.targets), 'trace_history_alias')
                if _contains(trace._config, self.targets):
                    _need(anchor is None or trace not in anchor, 'anchor_owner_already_detached')
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

    def _lineage(self, run_pin, trace):
        # HA shields a separate run task; the caller's native Script.async_run
        # frame retains that exact run and shares an await chain with its trace.
        from .native_active_execution_pins import _frames
        from homeassistant.components.script import ScriptEntity
        from homeassistant.components import script as script_module, automation as automation_module
        from homeassistant.helpers import script as helper_module
        is_script = type(self.entity) is ScriptEntity
        trace_code = self.codes[2 if is_script else 3]
        trace_name = 'script_trace' if is_script else 'automation_trace'
        context_name = 'context' if is_script else 'trigger_context'
        matches = []
        for task in asyncio.all_tasks():
            frames = tuple(_frames(task.get_coro()))
            outer = [frame for frame in frames if frame.f_code is self.codes[6]
                     and frame.f_globals is vars(helper_module)
                     and frame.f_locals.get('self') is self.script
                     and frame.f_locals.get('run') is run_pin.run
                     and frame.f_locals.get('context') is run_pin.context]
            traces = [frame for frame in frames if frame.f_code is trace_code
                      and frame.f_globals is vars(script_module if is_script else automation_module)
                      and frame.f_locals.get('self') is self.entity
                      and frame.f_locals.get(trace_name) is trace
                      and frame.f_locals.get(context_name) is run_pin.context]
            if len(outer) == len(traces) == 1 and not task.done():
                matches.append((task, outer[0], traces[0]))
        _need(len(matches) == 1, 'detached_trace_frame_unowned')
        return matches[0]

    def _check_lineage(self):
        _need(all(self._lineage(run_pin, trace) == saved
                  for run_pin, trace, saved in self.lineage), 'trace_lineage_changed')

    def _check_passive(self):
        from .native_script_config_transport import _graph
        rows = self._stored()
        _need(all(any(actual is trace and actual_key == key for actual, actual_key in rows)
                  and trace.__dict__ is storage and trace.context is context
                  and trace.key == key and trace.run_id == run_id
                  and trace._config is config and trace._dict is None
                  and _graph(config) == graph and self.hass.data.get(_ANCHOR_KEY) is anchor
                  and anchor.get(trace) is proof
                  for trace, context, key, run_id, storage, config, graph, anchor, proof in self.passive),
              'detached_trace_changed')

    def check_review(self):
        """Recheck the review-owned inventory in a later apply task."""
        from .native_unload_code_proof import unchanged
        _need(self.phase == 'captured' and self.entity.raw_config is self.raw, 'owner_changed')
        unchanged(self.refs, self.methods, self.codes)
        self._foreign_aliases()
        self._check_lineage()
        self._check_passive()
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
        existing = self.hass.data.get(_ANCHOR_KEY)
        _need(existing is None or type(existing) is WeakKeyDictionary, 'anchor_changed')
        _need(existing is None or all(trace not in existing for trace, _, _, _, _ in self.edges),
              'anchor_owner_already_detached')
        self.phase = 'installing'
        for trace, _, _, _, _ in self.edges:
            log.write('_config', trace, self.clone,
                      lambda trace=trace: setattr(trace, '_config', self.clone))
        # This anchor is created only from the exact trace and clone just
        # written by this cutover; fresh admission must never adopt lookalikes.
        anchor = self.hass.data.setdefault(_ANCHOR_KEY, WeakKeyDictionary())
        _need(type(anchor) is WeakKeyDictionary, 'anchor_changed')
        minted = []
        for trace, context, key, run_id, storage in self.edges:
            pins = [item for item in (self.pin.parent, self.pin.queued) if item.context is context]
            if pins:
                _need(len(pins) == 1 and type(self.trace_scope) is tuple, 'anchor_scope_changed')
                proof = (context, self.clone, self.clone_graph, self.entity, self.script,
                         pins[0].run, pins[0].task, self.trace_scope, key, run_id, storage)
                anchor[trace] = proof
                minted.append((trace, proof))
        self.anchor, self.minted = anchor, tuple(minted)
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
            if self.phase == 'installed':
                _need(self.hass.data.get(_ANCHOR_KEY) is self.anchor
                      and all(self.anchor.get(trace) is proof for trace, proof in self.minted),
                      'anchor_changed')
            self._check_lineage()
            self._foreign_aliases()
            self._check_passive()
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
        if hasattr(self, 'anchor'):
            _need(self.hass.data.get(_ANCHOR_KEY) is self.anchor
                  and all(self.anchor.get(trace) is proof for trace, proof in self.minted),
                  'anchor_changed')
            for trace, _ in self.minted:
                del self.anchor[trace]
        self.phase = 'restored'
