"""Narrow active native Script configuration transport.

Exactly one queued root, one retained nested sequence and its native wait are
admitted. The only changing configured values are in a subsequent event action.
No cache, list, current action, task, event, state or run is replaced. The
existing pre-effect log owns exact dictionary-entry writes and planned undo.
"""
from __future__ import annotations

import asyncio
from functools import partial
from types import FunctionType, MappingProxyType, MethodType

from .transaction import Refused
from .native_script_config_transport import _graph, _need, _source_string, _cache_names
from .native_active_execution_pins import ActiveWaitPins, capture_census, check_census


def _containers(value, seen=None):
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass
    if seen is None:
        seen = set()
    if id(value) in seen:
        return set()
    seen.add(id(value))
    result = set()
    if type(value) in (dict, NodeDictClass):
        result.add(id(value))
        for child in value.values():
            result.update(_containers(child, seen))
    elif type(value) in (list, NodeListClass):
        result.add(id(value))
        for child in value:
            result.update(_containers(child, seen))
    return result


class _Projection:
    """Only replacements of values inside the two admitted future actions."""
    def __init__(self, hass, owners):
        self.hass, self.owners = hass, owners
        self.memo, self.edges, self.targets, self.operations = {}, set(), set(), []
        self.replacements, self.equivalent = [], {}

    def project(self, value, before, after, parent, key):
        from homeassistant.helpers.template import Template
        from homeassistant.util.yaml.objects import NodeStrClass, NodeDictClass, NodeListClass
        from .yaml_validation_worker import _normal
        _need(_normal(value) == before, 'active_config_source_changed')
        if before == after:
            return value
        kind = type(value)
        tracked = kind in (dict, NodeDictClass, list, NodeListClass, Template)
        if tracked:
            self.targets.add(id(value))
            self.edges.add((id(parent), key, id(value)))
            previous = self.memo.get(id(value))
            if previous is not None:
                _need(previous[0] == before and previous[1] == after,
                      'active_config_shared_projection_conflict')
                return previous[2]
        if kind in (str, NodeStrClass):
            result = _source_string(value, after)
        elif kind is Template:
            _need(type(after) is dict and set(after) == {'$template'}
                  and type(after['$template']) is str and value.hass in (None, self.hass),
                  'active_config_template_unsupported')
            result = Template(after['$template'], self.hass)
            result.ensure_valid()
            result.hass = value.hass
            _need(_normal(result) == after)
            self.targets.add(id(result))
            self.edges.add((id(parent), key, id(result)))
            self.equivalent[id(result)] = id(value)
            self.replacements.append((result, _graph(result)))
        elif kind in (list, NodeListClass):
            _need(type(before) is list and type(after) is list
                  and len(value) == len(before) == len(after), 'active_config_list_shape')
            for index, (child, old, new) in enumerate(zip(value, before, after, strict=True)):
                replacement = self.project(child, old, new, value, index)
                # A list iterator must retain its list and all its slots. The
                # first profile changes only values inside future action dicts.
                _need(replacement is child, 'active_config_list_slot_unsupported')
            result = value
        elif kind in (dict, NodeDictClass):
            _need(type(before) is dict and set(before) == {'$map'} and type(after) is dict
                  and set(after) == {'$map'} and len(value) == len(before['$map']) == len(after['$map']),
                  'active_config_mapping_shape')
            for (name, child), (old_key, old), (new_key, new) in zip(
                    value.items(), before['$map'], after['$map'], strict=True):
                _need(name == old_key == new_key, 'active_config_key_change_unsupported')
                replacement = self.project(child, old, new, value, name)
                if replacement is not child:
                    _need(id(value) in self.owners, 'active_config_nonfuture_write')
                    self.operations.append((value, name, child, replacement))
            result = value
        else:
            raise Refused('active_config_changed_type_unsupported') from None
        if tracked:
            self.memo[id(value)] = (before, after, result)
        return result

    def scan(self, hass, entities, scripts, *, run_edges=frozenset()):
        """Known native configuration, caches and active historical variables.

        Exact traversed changed-path edges are authorized; another occurrence
        of a changed mutable node or Template in any known root is foreign.
        The active child.sequence edge is explicitly authorized by the pin.
        """
        from homeassistant.helpers.template import Template
        from homeassistant.helpers.script_variables import ScriptVariables, ScriptRunVariables
        from homeassistant.helpers.script import Script
        from homeassistant.core import State, Event
        from homeassistant.util.read_only_dict import ReadOnlyDict
        from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass
        budget = [200000]
        found = []
        def walk(value, parent, key, path, ancestry):
            budget[0] -= 1
            _need(budget[0] >= 0 and len(ancestry) < 100, 'active_config_alias_graph_limit')
            ident = id(value)
            if ident in self.targets:
                edge = (id(parent), key, ident)
                _need(edge in self.edges or edge in run_edges, 'active_config_foreign_alias')
                found.append((path, (edge[0], edge[1], self.equivalent.get(ident, ident))))
            if ident in ancestry:
                _need(ident not in self.targets, 'active_config_alias_cycle')
                return
            ancestry = (*ancestry, ident)
            kind = type(value)
            if kind in (dict, NodeDictClass, MappingProxyType, ReadOnlyDict):
                for name, item in value.items():
                    walk(item, value, name, (*path, name), ancestry)
            elif kind in (list, tuple, NodeListClass):
                for index, item in enumerate(value):
                    walk(item, value, index, (*path, index), ancestry)
            elif kind is Script:
                walk(value.sequence, value, 'sequence', (*path, 'sequence'), ancestry)
                walk(value.variables, value, 'variables', (*path, 'variables'), ancestry)
                for name in _cache_names():
                    walk(getattr(value, name), value, name, (*path, name), ancestry)
                for index, run in enumerate(value._runs):
                    walk(run._variables, run, '_variables', (*path, 'runs', index, 'variables'), ancestry)
                    action = getattr(run, '_action', None)
                    # The active current action is intentionally unmodified, but
                    # its parent path is a native retained frame edge.
                    if action is not None:
                        walk(action, run, '_action', (*path, 'runs', index, 'action'), ancestry)
            elif kind is ScriptVariables:
                walk(value.variables, value, 'variables', (*path, 'variables'), ancestry)
            elif kind is ScriptRunVariables:
                walk(value._local_data, value, '_local_data', (*path, 'local'), ancestry)
                walk(value._previous, value, '_previous', (*path, 'previous'), ancestry)
            elif kind is State:
                walk(value.attributes, value, 'attributes', (*path, 'attributes'), ancestry)
            elif kind is Event:
                walk(value.data, value, 'data', (*path, 'data'), ancestry)
            elif kind is Template:
                return
            elif kind is FunctionType:
                for name, cell in zip(value.__code__.co_freevars, value.__closure__ or (), strict=True):
                    try:
                        child = cell.cell_contents
                    except ValueError:
                        continue
                    walk(child, value, name, (*path, 'closure', name), ancestry)
            elif kind is partial:
                walk(value.func, value, 'func', (*path, 'func'), ancestry)
                walk(value.args, value, 'args', (*path, 'args'), ancestry)
                walk(value.keywords, value, 'keywords', (*path, 'keywords'), ancestry)
            elif kind is MethodType:
                walk(value.__self__, value, 'self', (*path, 'self'), ancestry)
        for (domain, _), entity in entities.items():
            script = entity.action_script if domain == 'automation' else entity.script
            for name, value in (('raw_config', entity.raw_config), ('script', script),
                                ('_variables', getattr(entity, '_variables', None)),
                                ('_trigger_config', getattr(entity, '_trigger_config', None))):
                walk(value, entity, name, (id(entity), name), ())
        # Listener closures are native retained roots too. We inspect their
        # contents without invoking them; unrelated callbacks stay untouched.
        for event_type, listeners in hass.bus._listeners.items():
            for index, (job, event_filter) in enumerate(listeners):
                for name, value in (('target', job.target), ('filter', event_filter)):
                    walk(value, job, name, (id(hass.bus), event_type, index, name), ())
        return tuple(found)


def _entities(hass):
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    entities = {}
    for domain in ('automation', 'script'):
        component = hass.data.get(DATA_INSTANCES, {}).get(domain)
        for entity in component.entities if component is not None else ():
            _need((domain, entity.unique_id) not in entities)
            entities[domain, entity.unique_id] = entity
    return entities


def _future_roots(entity, script, pin, domain):
    from homeassistant.util.yaml.objects import NodeStrClass, NodeDictClass, NodeListClass
    raw = entity.raw_config
    _need(type(raw) in (dict, NodeDictClass) and len(script.sequence) == 1
          and len(pin.child_sequence) == 2, 'active_config_profile_shape')
    name = 'sequence' if domain == 'script' else ('actions' if 'actions' in raw else 'action')
    raw_sequence = raw.get(name)
    _need(type(raw_sequence) in (list, NodeListClass) and len(raw_sequence) == 1
          and type(raw_sequence[0]) in (dict, NodeDictClass), 'active_config_raw_shape')
    raw_nested = raw_sequence[0].get('sequence')
    _need(type(raw_nested) in (list, NodeListClass) and len(raw_nested) == 2,
          'active_config_raw_nested_shape')
    raw_future, native_future = raw_nested[1], pin.child_sequence[1]
    for action in (raw_future, native_future):
        _need(type(action) in (dict, NodeDictClass) and type(action.get('event')) in (str, NodeStrClass)
              and not set(action) - {'event', 'event_data', 'event_data_template', 'alias', 'enabled'},
              'active_config_future_action_unsupported')
    return raw_future, native_future, raw_nested[0], pin.child_sequence[0]


def _validate_profile(raw_future, native_future, raw_wait, native_wait, source_ids):
    """Exclude changed wait dependencies and dynamic future state lookups."""
    from jinja2 import Environment, nodes
    from homeassistant.helpers.template import Template
    from homeassistant.util.yaml.objects import NodeStrClass, NodeDictClass, NodeListClass
    def inspect(value, *, wait):
        if type(value) is Template:
            text = value.template
        elif type(value) in (str, NodeStrClass):
            text = str(value)
        elif type(value) in (dict, NodeDictClass):
            for key, child in value.items():
                inspect(key, wait=wait)
                inspect(child, wait=wait)
            return
        elif type(value) in (list, tuple, NodeListClass):
            for child in value:
                inspect(child, wait=wait)
            return
        else:
            return
        if wait:
            _need(not any(source in text for source in source_ids), 'active_wait_source_reference')
        elif '{{' in text or '{%' in text:
            tree = Environment().parse(text)
            calls = tuple(tree.find_all(nodes.Call))
            for call in calls:
                _need(type(call.node) is nodes.Name and call.node.name == 'states'
                      and len(call.args) == 1 and type(call.args[0]) is nodes.Const
                      and type(call.args[0].value) is str and not call.kwargs
                      and call.dyn_args is None and call.dyn_kwargs is None,
                      'active_config_dynamic_template_unsupported')
            state_names = tuple(name for name in tree.find_all(nodes.Name) if name.name == 'states')
            _need(len(state_names) == len(calls)
                  and all(any(name is call.node for call in calls) for name in state_names),
                  'active_config_dynamic_template_unsupported')
    for value in (raw_wait, native_wait):
        inspect(value, wait=True)
    for value in (raw_future, native_future):
        inspect(value, wait=False)


def _project_actor(hass, entity, script, pin, key, old, new, source_ids, entities):
    raw_future, native_future, raw_wait, native_wait = _future_roots(entity, script, pin, key[0])
    _validate_profile(raw_future, native_future, raw_wait, native_wait, source_ids)
    owners = _containers(raw_future) | _containers(native_future)
    projector = _Projection(hass, owners)
    if key[0] == 'automation':
        _need(old[2] == new[2] and old[4:] == new[4:] and script.variables is None,
              'active_config_automation_nonaction_change')
        seq_index = 3
    else:
        _need(old[3:] == new[3:], 'active_config_script_nonaction_change')
        seq_index = 2
    projector.project(entity.raw_config, old[1], new[1], entity, 'raw_config')
    projector.project(script.sequence, old[seq_index], new[seq_index], script, 'sequence')
    # The nested child and current parent run intentionally share these exact
    # changed-path objects with the parent. No additional owner is authorized.
    for owner, name, value in ((pin.child, 'sequence', pin.child_sequence),
                               (pin.parent.run, '_action', pin.parent.action)):
        if id(value) in projector.targets:
            projector.edges.add((id(owner), name, id(value)))
    _need(bool(projector.operations), 'active_config_no_future_changes')
    aliases = projector.scan(hass, entities, ())
    return projector, aliases


def validate_active_program(hass, program, entities, admitted, changed):
    """Read-only pre-record admission of the actual future slots and owners."""
    from .yaml_validation_worker import _normal
    busy = [key for key in admitted if
            (entities[key].action_script if key[0] == 'automation' else entities[key].script).is_running]
    if not busy:
        return
    _need(len(program.source_order) == 1 and len(changed) == 1 and busy == list(changed),
          'active_config_single_busy_actor_required')
    source, = program.source_order
    key, = changed
    entity = entities[key]
    script = entity.action_script if key[0] == 'automation' else entity.script
    pin = ActiveWaitPins.capture(script)
    before, after = dict(program.rows(frozenset())), dict(program.rows(frozenset((source,))))
    old = next(row for row in before[key[0]] if row[0] == key[1])
    new = next(row for row in after[key[0]] if row[0] == key[1])
    seq_index = 3 if key[0] == 'automation' else 2
    actual = _normal(entity.raw_config), _normal(script.sequence)
    if actual == (new[1], new[seq_index]):
        old, new = new, old
    else:
        _need(actual == (old[1], old[seq_index]), 'active_config_prefix_unrecognized')
    if key[0] == 'automation':
        _need(all('for' not in cfg for actor_key in admitted if actor_key[0] == 'automation'
                  for cfg in entities[actor_key]._trigger_config), 'active_config_duration_unsupported')
    _project_actor(hass, entity, script, pin, key, old, new,
                   (source, dict(program.mapping)[source]), entities)


class _ActiveBase:
    def _actor_graph(self, *, slots=None):
        return tuple((id(entity), id(script), _graph(entity.raw_config, slots=slots),
                      _graph(script.sequence, slots=slots), _graph(script.variables, slots=slots),
                      _graph(getattr(entity, '_variables', None), slots=slots),
                      _graph(getattr(entity, '_trigger_config', None), slots=slots))
                     for entity, script, _ in self.actors)

    def _check_pins(self):
        for entity, script, census in self.actors:
            _need((entity.action_script if entity.entity_id.startswith('automation.') else entity.script) is script,
                  'active_config_script_replaced')
            check_census(script, census, allow_elapsed=getattr(self, '_planning_cleanup', False))

    def _check_aliases(self):
        _need(self.projector.scan(self.observer.hass, _entities(self.observer.hass), ()) == self.aliases,
              'active_config_alias_inventory_changed')

    def _check_after_attempt(self):
        """Check current native owners, allowing ordinary completed/queued runs.

        The changed slots and callback graph remain owned. A live native run
        may now point at its current action, including the previously future
        action; that edge grants no authority to edit its variables or history.
        """
        from homeassistant.helpers.script import _ScriptRun, _QueuedScriptRun
        edges = set()
        for entity, script, census in self.actors:
            _need((entity.action_script if entity.entity_id.startswith('automation.') else entity.script) is script,
                  'active_config_script_replaced')
            if type(census) is not ActiveWaitPins:
                continue
            census.check_program()
            for native, run_type in ((census.root, _QueuedScriptRun), (census.child, _ScriptRun)):
                _need(type(native._runs) is list, 'active_config_run_inventory_changed')
                for run in native._runs:
                    _need(type(run) is run_type and run._script is native,
                          'active_config_run_owner_changed')
                    action = getattr(run, '_action', None)
                    if action is not None:
                        _need(type(run._step) is int and 0 <= run._step < len(native.sequence)
                              and action is native.sequence[run._step],
                              'active_config_run_action_changed')
                        edges.add((id(run), '_action', id(action)))
        # The inventory can shrink or grow as native runs finish/start. Every
        # occurrence is nevertheless checked against the original projected
        # edges or a current native run's exact configured action edge.
        self.projector.scan(self.observer.hass, _entities(self.observer.hass), (),
                            run_edges=frozenset(edges))
        _need(self._actor_graph() == self.installed, 'active_config_after_graph_changed')

    def _check_untouched_slots(self):
        changed = {(id(container), id(key)) for container, key, _, _ in self.projector.operations}
        for container, original in self.container_images:
            current = tuple(container.items())
            _need(len(current) == len(original) and all(
                actual_key is old_key and ((id(container), id(old_key)) in changed
                                           or actual_value is old_value)
                for (actual_key, actual_value), (old_key, old_value) in zip(current, original, strict=True)),
                'active_config_foreign_sibling_changed')

    def _check_before(self):
        _need(asyncio.current_task() is self.task and self.phase == 'captured')
        self.guard()
        self._check_pins()
        self._check_aliases()
        _need(self._actor_graph() == self.original, 'active_config_before_graph_changed')
        _need(self.observer.sample(self.chain, input_state=self.input_state) == self.sample,
              'active_config_before_sample_changed')

    def _rows(self, expected):
        from .yaml_runtime_observer import NativeYAMLRuntimeReadback
        from .yaml_validation_worker import _normal
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        native = NativeYAMLRuntimeReadback(self.observer.hass, owned_automation=getattr(self, 'graph', None))
        pinned = {name: (component_id, tokens) for name, component_id, tokens in self.sample.native_tokens}
        for domain, rows in expected.items():
            component = self.observer.hass.data.get(DATA_INSTANCES, {}).get(domain)
            actual, identities = native._projection(domain) if component is not None else ([], ())
            _need(id(component) == pinned[domain][0] and _normal(actual) == rows,
                  'active_config_rows_changed')
            previous = pinned[domain][1]
            _need((tuple(row[:5] for row in identities) == tuple(row[:5] for row in previous)
                   if domain == 'automation' and getattr(self, 'graph', None) is not None
                   else identities == previous), 'active_config_actor_identity_changed')

    def check_configured_retained(self, *, configured_view=None, before_attempt=False):
        node = getattr(getattr(self, 'graph', None), 'node', None)
        attempted = (self.phase == 'attempted' or node is not None and node._registry_attempted())
        if not before_attempt and attempted:
            self._check_after_attempt()
        else:
            self._check_pins()
            self._check_aliases()
        self._check_untouched_slots()
        _need(self._actor_graph(slots=self.original_slots) == self.original,
              'active_config_retained_original_changed')
        _need(all(_graph(value) == image for value, image in self.projector.replacements),
              'active_config_replacement_changed')

    def _plan(self):
        # No await exists between install and a before-attempt cleanup. If the
        # deadline elapsed while this synchronous section ran, the native task
        # and timeout future must still be suspended, but our own writes may be
        # undone rather than stranded before the event loop can resume it.
        self._planning_cleanup = True
        try:
            self.check_configured_retained(before_attempt=True)
            plan = self.log.plan()
            graph_plan = self.graph.plan_retire(before_attempt=True) if getattr(self, 'graph', None) else None
            return plan, graph_plan
        finally:
            self._planning_cleanup = False

    def _install(self, witness=None):
        from .native_automation_duration_binding import _PreEffectLog
        self._check_before()
        self.log = _PreEffectLog()
        for container, key, _, _ in self.projector.operations:
            self.log.retain('entry', (container, key))
        try:
            for container, key, before, after in self.projector.operations:
                self.log.write('entry', (container, key), after,
                               lambda container=container, key=key, after=after: container.__setitem__(key, after))
            self.installed = self._actor_graph()
            if getattr(self, 'graph', None) is not None:
                self.graph.install(witness)
            self._rows(self.after)
            self.check_configured_retained()
        except BaseException:
            try:
                plan, graph_plan = self._plan()
                if graph_plan is not None:
                    self.graph.apply_retire(graph_plan, before_attempt=True)
                self.log.undo(plan)
                _need(self._actor_graph() == self.original, 'active_config_restore_graph_changed')
                self._rows(self.before)
                self.phase = 'failed_before_attempt'
            except BaseException:
                self.phase = 'failed_before_attempt_cleanup_unknown'
                raise
            raise
        self.phase = 'installed'

    def retain_after_attempt(self):
        _need(asyncio.current_task() is self.task and self.phase == 'installed')
        self.phase = 'attempted'

    def restore_before_attempt(self):
        _need(asyncio.current_task() is self.task and self.phase == 'installed')
        self._rows(self.after)
        _need(self._actor_graph() == self.installed, 'active_config_after_graph_changed')
        plan, graph_plan = self._plan()
        try:
            if graph_plan is not None:
                self.graph.apply_retire(graph_plan, before_attempt=True)
            self.log.undo(plan)
            _need(self._actor_graph() == self.original, 'active_config_restore_graph_changed')
            self._rows(self.before)
            self.phase = 'restored_before_attempt'
        except BaseException:
            self.phase = 'failed_before_attempt_cleanup_unknown'
            raise


def _capture(observer, chain, source, *, reverse, guard, input_state, automation, entities, admitted, changed):
    guard()
    sample = observer.sample(chain, input_state=input_state)
    active = sample.prefix.active
    _need((source in active) == reverse)
    target = active - {source} if reverse else active | {source}
    observer.program.assert_edge(active, target, source)
    before, after = dict(observer.program.rows(active)), dict(observer.program.rows(target))
    _need(len(changed) == 1, 'active_config_single_actor_required')
    key, = changed
    entity = entities[key]
    script = entity.action_script if key[0] == 'automation' else entity.script
    pin = ActiveWaitPins.capture(script)
    old = next(row for row in before[key[0]] if row[0] == key[1])
    new = next(row for row in after[key[0]] if row[0] == key[1])
    projector, aliases = _project_actor(observer.hass, entity, script, pin, key, old, new,
        (source, dict(observer.program.mapping)[source]), entities)
    actor_rows = []
    for actor_key in sorted(admitted):
        actor = entities[actor_key]
        actor_script = actor.action_script if actor_key[0] == 'automation' else actor.script
        census = pin if actor_script is script else capture_census(actor_script)
        _need(actor_script is script or type(census) is tuple, 'active_config_multiple_busy_actors')
        actor_rows.append((actor, actor_script, census))
    obj = object.__new__(ActiveAutomationTransport if automation else ActiveScriptTransport)
    obj.observer, obj.chain, obj.guard, obj.sample = observer, chain, guard, sample
    obj.input_state, obj.before, obj.after = input_state, before, after
    obj.task, obj.phase, obj.actors, obj.projector = asyncio.current_task(), 'captured', tuple(actor_rows), projector
    obj.original = obj._actor_graph()
    obj.original_slots = {id(container): tuple(container.items())
                          for container, _, _, _ in projector.operations}
    obj.container_images = tuple((container, obj.original_slots[id(container)])
        for index, (container, _, _, _) in enumerate(projector.operations)
        if all(previous[0] is not container for previous in projector.operations[:index]))
    obj.aliases = aliases
    if automation:
        obj.graph = observer.owned_automation
        _need(obj.graph is not None)
        obj.graph.transfer = obj
        _need(all('for' not in cfg for actor, _, _ in obj.actors
                  if actor.entity_id.startswith('automation.') for cfg in actor._trigger_config),
              'active_config_duration_unsupported')
        mapping = dict(observer.program.mapping)
        edge = {mapping[source]: source} if reverse else {source: mapping[source]}
        obj.graph.capture(tuple(actor for actor, _, _ in obj.actors
                          if actor.entity_id.startswith('automation.')), edge)
    obj._check_before()
    return obj


class ActiveScriptTransport(_ActiveBase):
    @classmethod
    def capture(cls, observer, chain, source, *, reverse=False, guard, input_state=None):
        entities = _entities(observer.hass)
        sample = observer.sample(chain, input_state=input_state)
        mask = sample.prefix.active
        target = mask - {source} if reverse else mask | {source}
        before, after = dict(observer.program.rows(mask)), dict(observer.program.rows(target))
        _need(before['automation'] == after['automation'])
        changed = {(domain, old[0]) for domain in ('script',) for old, new in
                   zip(before[domain], after[domain], strict=True) if old != new}
        return _capture(observer, chain, source, reverse=reverse, guard=guard, input_state=input_state,
                        automation=False, entities=entities, admitted=changed, changed=changed)

    def install(self):
        self._install()


class ActiveAutomationTransport(_ActiveBase):
    @classmethod
    def capture(cls, observer, chain, source, *, reverse=False, guard, input_state=None):
        from .native_automation_config_transport import _call_scope
        sample = observer.sample(chain, input_state=input_state)
        mask = sample.prefix.active
        target = mask - {source} if reverse else mask | {source}
        before, after = dict(observer.program.rows(mask)), dict(observer.program.rows(target))
        entities, admitted, changed = _call_scope(observer.hass, before, after)
        return _capture(observer, chain, source, reverse=reverse, guard=guard, input_state=input_state,
                        automation=True, entities=entities, admitted=admitted, changed=changed)

    def install(self, witness):
        self._install(witness)
