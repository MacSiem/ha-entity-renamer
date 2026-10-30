"""Selective idle Script configuration data plane, not a transaction adapter.

The caller owns auth, durable intent and the registry attempt. This transport
neither advances a receipt nor selects an accepted prefix. Busy source-bearing
runs remain unsupported. Native entities, root Scripts and services survive;
idle lazily constructed action caches are invalidated, never replayed.
"""
from __future__ import annotations

import asyncio
from functools import partial

from .dependent_config_binding import NativeDependentConfigReadback
from .transaction import Refused


def _need(value, reason='native_script_config_transport_unqualified'):
    if not value:
        raise Refused(reason) from None


_CACHES = ('_repeat_script', '_choose_data', '_if_data', '_parallel_scripts', '_sequence_scripts')


def _cache_names():
    from homeassistant.const import __version__
    _need(__version__ in ('2025.6.0', '2026.10.2', '2026.10.4'))
    return (('_config_cache' if __version__ == '2025.6.0' else '_condition_cache'),) + _CACHES


def _source_string(original, text):
    from homeassistant.util.yaml.objects import NodeStrClass
    _need(type(original) in (str, NodeStrClass) and type(text) is str)
    if type(original) is str:
        return text
    replacement = NodeStrClass(text)
    for name in ('__config_file__', '__line__'):
        if hasattr(original, name):
            setattr(replacement, name, getattr(original, name))
    return replacement


def _graph(value, *, include_scripts=False, slots=None):
    """Identity of reachable configured/cache edges, without evaluating leaves.

    Compiled Template artifacts and render counters may advance normally; their
    configured object/body/hass edges remain pinned. Opaque native callables are
    compared by identity, never called, stringified or introspected generically.
    """
    from homeassistant.helpers.template import Template
    from homeassistant.helpers.script_variables import ScriptVariables
    from homeassistant.helpers.script import Script
    from homeassistant.util.yaml.objects import NodeStrClass, NodeListClass, NodeDictClass
    count = 0
    def visit(item, ancestry=()):
        nonlocal count
        count += 1
        _need(count <= 100_000 and len(ancestry) <= 64, 'native_script_config_graph_limit')
        kind = type(item)
        head = (kind, id(item))
        if id(item) in ancestry:
            return head + ('ref',)
        ancestry += (id(item),)
        if kind in (NodeStrClass, NodeListClass, NodeDictClass):
            head += (tuple((name, visit(getattr(item, name), ancestry))
                for name in ('__config_file__', '__line__') if hasattr(item, name)),)
        if kind in (dict, NodeDictClass):
            entries = item.items() if slots is None or id(item) not in slots else slots[id(item)]
            return head + (tuple((visit(key, ancestry), visit(child, ancestry)) for key, child in entries),)
        if kind in (list, tuple, NodeListClass):
            values = item if slots is None or id(item) not in slots else slots[id(item)]
            return head + (tuple(visit(child, ancestry) for child in values),)
        if kind is Template:
            return head + (id(item.hass), visit(item.template, ancestry))
        if kind is ScriptVariables:
            return head + (visit(item.variables, ancestry),)
        if kind is Script and include_scripts:
            return head + (visit(item.sequence, ancestry), visit(item.variables, ancestry),
                tuple((name, visit(getattr(item, name), ancestry)) for name in _cache_names()))
        return head
    return visit(value)


def _idle_census(root):
    """Known native lazy-cache graph, including every retained nested Script."""
    from homeassistant.helpers.script import Script
    seen, result = set(), []
    def visit(script, parent=None):
        _need(type(script) is Script and script._hass is root._hass
              and script.domain == root.domain and not script.is_running
              and type(script._runs) is list and not script._runs
              and not getattr(script, '_unloaded', False), 'native_script_config_busy_or_foreign')
        if parent is not None:
            listener = script.change_listener
            _need(script.top_level is False and type(listener) is partial
                  and not listener.keywords and listener.args == (script,)
                  and getattr(listener.func, '__self__', None) is parent
                  and getattr(listener.func, '__func__', None) is Script._chain_change_listener)
        _need(id(script) not in seen, 'native_script_config_cache_cycle')
        seen.add(id(script))
        caches = []
        for name in _cache_names():
            cache = getattr(script, name)
            _need(type(cache) is dict)
            caches.append((name, _graph(cache)))
        result.append((id(script), id(script.sequence), id(script.variables), tuple(caches)))
        for name in ('_repeat_script', '_sequence_scripts'):
            for key, child in getattr(script, name).items():
                _need(type(key) is int)
                visit(child, script)
        for key, children in script._parallel_scripts.items():
            _need(type(key) is int and type(children) is list)
            for child in children:
                visit(child, script)
        for key, data in script._choose_data.items():
            _need(type(key) is int and type(data) is dict and set(data) == {'choices', 'default'}
                  and type(data['choices']) is list)
            for conditions, child in data['choices']:
                _need(type(conditions) is list)
                visit(child, script)
            if data['default'] is not None:
                visit(data['default'], script)
        for key, data in script._if_data.items():
            _need(type(key) is int and type(data) is dict
                  and set(data) == {'if_conditions', 'if_then', 'if_else'}
                  and type(data['if_conditions']) is list)
            visit(data['if_then'], script)
            if data['if_else'] is not None:
                visit(data['if_else'], script)
    visit(root)
    return tuple(result)


def assert_idle_script_program_targets(hass, program, *, active=None):
    """Read-only target admission; unrelated executions grant no write authority."""
    from homeassistant.helpers.entity_component import DATA_INSTANCES, EntityComponent
    from homeassistant.components.script import ScriptEntity
    from .dependent_config_program import DependentConfigProgram
    _need(type(program) is DependentConfigProgram
          and (active is None or type(active) is frozenset))
    mask = frozenset(program.source_order) if active is None else active
    before = {row[0]: row for row in dict(program.rows(frozenset()))['script']}
    after = {row[0]: row for row in dict(program.rows(mask))['script']}
    _need(before.keys() == after.keys(), 'native_script_config_target_inventory_changed')
    changed = {key for key in before if before[key] != after[key]}
    component = hass.data.get(DATA_INSTANCES, {}).get('script')
    _need(type(component) is EntityComponent and component.hass is hass)
    entities = tuple(component.entities)
    _need(all(type(entity) is ScriptEntity for entity in entities))
    keyed = {entity.unique_id: entity for entity in entities}
    _need(len(keyed) == len(entities) and changed <= keyed.keys())
    for key in sorted(changed):
        _idle_census(keyed[key].script)


class NativeScriptConfigTransport:
    def __init__(self):
        _need(False, 'native_script_config_capture_required')

    @classmethod
    def capture(cls, observer, chain, source, *, reverse=False, guard, input_state=None):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        _need(cls is NativeScriptConfigTransport and type(observer) is NativeDependentConfigReadback
              and type(reverse) is bool and asyncio.current_task() is not None)
        guard()
        sample = observer.sample(chain, input_state=input_state)
        active = sample.prefix.active
        _need(source in observer.program.source_order and (source in active) == reverse)
        target = active - {source} if reverse else active | {source}
        observer.program.assert_edge(active, target, source)
        before, after = dict(observer.program.rows(active)), dict(observer.program.rows(target))
        # A separate trigger/program transport is required for automation.
        _need(before['automation'] == after['automation'], 'native_script_config_automation_change_unsupported')
        obj = object.__new__(cls)
        obj.observer, obj.chain, obj.guard, obj.sample = observer, chain, guard, sample
        obj.input_state = input_state
        obj.before, obj.after, obj.active, obj.target = before, after, active, target
        obj.task, obj.phase, obj.operations, obj.actors = asyncio.current_task(), 'captured', [], []
        obj._projected = {}
        component = observer.hass.data.get(DATA_INSTANCES, {}).get('script')
        entities = {entity.unique_id: entity for entity in component.entities} if component is not None else {}
        _need([row[0] for row in before['script']] == [row[0] for row in after['script']])
        for old, new in zip(before['script'], after['script'], strict=True):
            if old == new:
                continue
            _need(old[0] in entities and old[3:6] == new[3:6] and old[7:] == new[7:],
                  'native_script_config_metadata_change_unsupported')
            entity = entities[old[0]]
            script = entity.script
            census = _idle_census(script)
            obj.actors.append((entity, script, census,
                tuple((name, getattr(script, name), tuple(getattr(script, name).items())) for name in _cache_names())))
            obj._project(entity.raw_config, old[1], new[1])
            obj._project(script.sequence, old[2], new[2])
            obj._project(script.variables, old[6], new[6])
        _need(bool(obj.actors), 'native_script_config_no_change')
        obj.operations = tuple(obj.operations)
        obj.actors = tuple(obj.actors)
        obj._before_actor_graph = obj._actor_graph()
        obj.check_before()
        return obj

    def _project(self, value, before, after):
        """Exact native path edits; untouched values/types/containers survive."""
        from homeassistant.helpers.template import Template
        from homeassistant.helpers.script_variables import ScriptVariables
        from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass
        from .yaml_validation_worker import _normal
        _need(_normal(value) == before, 'native_script_config_slot_changed')
        tracked = type(value) in (list, dict, NodeDictClass, NodeListClass, Template, ScriptVariables)
        if tracked and id(value) in self._projected:
            old, new, projected = self._projected[id(value)]
            _need(old == before and new == after, 'native_script_config_shared_slot_conflict')
            return projected
        projected = self._project_value(value, before, after)
        if tracked:
            self._projected[id(value)] = before, after, projected
        return projected

    def _project_value(self, value, before, after):
        from homeassistant.helpers.template import Template
        from homeassistant.helpers.script_variables import ScriptVariables
        from homeassistant.util.yaml.objects import NodeStrClass, NodeDictClass, NodeListClass
        from .yaml_validation_worker import _normal
        if before == after:
            return value
        if type(value) in (str, NodeStrClass):
            _need(type(after) is str)
            return _source_string(value, after)
        if type(value) is Template:
            _need(type(after) is dict and set(after) == {'$template'}
                  and type(after['$template']) is str and value.hass in (None, self.observer.hass))
            replacement = Template(after['$template'], self.observer.hass)
            replacement.ensure_valid()
            replacement.hass = value.hass
            _need(_normal(replacement) == after)
            return replacement
        if type(value) is ScriptVariables:
            _need(type(after) is dict and set(after) == {'$variables'})
            self._project(value.variables, before['$variables'], after['$variables'])
            return value
        if type(value) in (list, NodeListClass):
            _need(type(after) is list and len(value) == len(after) == len(before))
            projected = [self._project(child, old, new) for child, old, new in zip(value, before, after, strict=True)]
            self.operations.append(('list', value, tuple(value), tuple(projected)))
            return value
        if type(value) in (dict, NodeDictClass):
            _need(type(before) is dict and set(before) == {'$map'}
                  and type(after) is dict and set(after) == {'$map'}
                  and len(value) == len(before['$map']) == len(after['$map'])
                  and all(type(key) in (str, NodeStrClass) for key in value))
            projected = []
            for (key, child), (old_key, old), (new_key, new) in zip(
                    value.items(), before['$map'], after['$map'], strict=True):
                _need(key == old_key and type(new_key) is str)
                projected.append((key if key == new_key else _source_string(key, new_key),
                                  self._project(child, old, new)))
            _need(len({key for key, _ in projected}) == len(projected))
            self.operations.append(('dict', value, tuple(value.items()), tuple(projected)))
            return value
        _need(False, 'native_script_config_changed_slot_type_unsupported')

    def check_before(self):
        _need(asyncio.current_task() is self.task and self.phase == 'captured')
        self.guard()
        _need(self._actor_graph() == self._before_actor_graph, 'native_script_config_live_edges_changed')
        _need(self.observer.sample(self.chain, input_state=self.input_state) == self.sample, 'native_script_config_prefix_changed')
        _need(all(entity.script is script and _idle_census(script) == census
                  for entity, script, census, _ in self.actors), 'native_script_config_cache_changed')

    def _actor_graph(self):
        return tuple((id(entity), id(entity.script), _graph(entity.raw_config),
                      _graph(script.sequence), _graph(script.variables))
                     for entity, script, _, _ in self.actors)

    def _saved_cache_graph(self):
        return tuple((id(cache), _graph(items, include_scripts=True))
                     for _, _, _, caches in self.actors for _, cache, items in caches)

    def _check_rows(self, expected):
        from .yaml_runtime_observer import NativeYAMLRuntimeReadback
        from .yaml_validation_worker import _normal
        observer = NativeYAMLRuntimeReadback(self.observer.hass)
        pinned = {domain: (component, identities) for domain, component, identities in self.sample.native_tokens}
        for domain, rows in expected.items():
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            component = self.observer.hass.data.get(DATA_INSTANCES, {}).get(domain)
            actual, identities = observer._projection(domain) if component is not None else ([], ())
            _need(_normal(actual) == rows, 'native_script_config_projection_mismatch')
            _need((id(component), identities) == pinned[domain], 'native_script_config_actor_identity_changed')

    @staticmethod
    def _write(operation, reverse=False):
        kind, container, before, after = operation
        value = before if reverse else after
        if kind == 'list':
            container[:] = value
        else:
            container.clear()
            container.update(value)

    def install(self):
        """No await: native configs and their idle lazy caches change together."""
        self.check_before()
        completed = []
        try:
            for operation in self.operations:
                self._write(operation)
                completed.append(operation)
            for _, script, _, caches in self.actors:
                _idle_census(script)
                for _, cache, _ in caches:
                    cache.clear()
            self._check_rows(self.after)
            self._after_actor_graph = self._actor_graph()
            self._after_saved_cache_graph = self._saved_cache_graph()
        except BaseException:
            for operation in reversed(completed):
                self._write(operation, reverse=True)
            for _, _, _, caches in self.actors:
                for _, cache, items in caches:
                    cache.clear()
                    cache.update(items)
            self.phase = 'failed_before_attempt'
            raise
        self.phase = 'installed'

    def retain_after_attempt(self):
        """Caller marks its actual effect boundary; this creates no receipt."""
        _need(asyncio.current_task() is self.task and self.phase == 'installed')
        self.phase = 'attempted'

    def restore_before_attempt(self):
        """Only this exact still-owned idle data plane, never registry replay."""
        _need(asyncio.current_task() is self.task and self.phase == 'installed')
        self._check_rows(self.after)
        _need(self._actor_graph() == self._after_actor_graph
              and self._saved_cache_graph() == self._after_saved_cache_graph,
              'native_script_config_cleanup_live_edges_changed')
        for kind, container, _, after in self.operations:
            current = tuple(container) if kind == 'list' else tuple(container.items())
            _need(len(current) == len(after) and all(
                actual is desired if kind == 'list' else actual[0] is desired[0] and actual[1] is desired[1]
                for actual, desired in zip(current, after, strict=True)),
                'native_script_config_cleanup_slot_replaced')
        _need(all(entity.script is script and not script.is_running and not script._runs
                  and all(getattr(script, name) is cache and not cache for name, cache, _ in caches)
                  for entity, script, _, caches in self.actors), 'native_script_config_cleanup_ownership_changed')
        for operation in reversed(self.operations):
            self._write(operation, reverse=True)
        for _, _, _, caches in self.actors:
            for _, cache, items in caches:
                cache.update(items)
        self._check_rows(self.before)
        self.phase = 'restored_before_attempt'
