"""Combined idle Automation/Script data plane; no lifecycle/reload adapter."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from .native_script_config_transport import (NativeScriptConfigTransport, _graph,
    _idle_census, _cache_names, _need)


@dataclass(frozen=True)
class _Actor:
    entity: object
    script: object
    census: tuple
    caches: tuple
    changed: bool


def _call_scope(hass, before, after):
    """Static transitive calls, including unchanged callers into affected Scripts.

    Reject dynamic/cyclic calls and enabled event/system callers before effects.
    This is a finite YAML program admission, never discovery by executing actions.
    """
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.helpers.template import Template
    from homeassistant.util.yaml.objects import NodeStrClass, NodeListClass, NodeDictClass
    entities = {}
    for domain in ('automation', 'script'):
        component = hass.data.get(DATA_INSTANCES, {}).get(domain)
        for entity in component.entities if component is not None else ():
            _need((domain, entity.unique_id) not in entities)
            entities[domain, entity.unique_id] = entity
    changed = {(domain, old[0]) for domain in ('automation', 'script')
        for old, new in zip(before[domain], after[domain], strict=True) if old != new}
    calls = {}
    def literal(value):
        if type(value) is Template:
            _need(value.is_static, 'automation_dynamic_script_call_unsupported')
            value = value.template
        _need(type(value) in (str, NodeStrClass), 'automation_dynamic_script_target_unsupported')
        return str(value)
    def targets(value):
        if type(value) in (list, NodeListClass):
            return tuple(literal(item) for item in value)
        return tuple(item.strip() for item in literal(value).split(','))
    def scan(value, found):
        if type(value) in (list, NodeListClass):
            for child in value:
                scan(child, found)
        elif type(value) in (dict, NodeDictClass):
            service = value.get('action', value.get('service'))
            if service is not None:
                service = literal(service)
                if service.startswith('script.') and service != 'script.turn_on':
                    _need(service not in ('script.toggle', 'script.turn_off', 'script.reload'),
                          'automation_script_system_call_unsupported')
                    found.add(('script', service.split('.', 1)[1]))
                elif service in ('script.turn_on', 'homeassistant.turn_on'):
                    target = value.get('target', {})
                    data = value.get('data', {})
                    _need(type(target) in (dict, NodeDictClass) and type(data) in (dict, NodeDictClass)
                          and not set(target) - {'entity_id'}, 'automation_opaque_script_target_unsupported')
                    ids = targets(target.get('entity_id', data.get('entity_id')))
                    if service == 'script.turn_on':
                        _need(all(item.startswith('script.') for item in ids))
                    found.update(('script', item.split('.', 1)[1]) for item in ids if item.startswith('script.'))
                else:
                    # automation.trigger/turn_on, python_script, shell/REST and
                    # unknown integration services can enter an affected Script
                    # through opaque code. No guessed call graph or narrowing.
                    _need(False, 'automation_opaque_service_caller_unsupported')
            for key, child in value.items():
                if key not in ('variables', 'event_data', 'data', 'target'):
                    scan(child, found)
    for key, entity in entities.items():
        script = entity.action_script if key[0] == 'automation' else entity.script
        found = set()
        scan(script.sequence, found)
        _need(found <= entities.keys(), 'automation_script_call_target_missing')
        calls[key] = found
    closure = {}
    def visit(key, path=()):
        _need(key not in path, 'automation_script_call_cycle_unsupported')
        if key not in closure:
            closure[key] = {key}
            for target in calls[key]:
                closure[key] |= visit(target, (*path, key))
        return closure[key]
    for key in calls:
        visit(key)
    admitted = {key for key in calls if closure[key] & changed}
    admitted |= set().union(*(closure[key] for key in admitted)) if admitted else set()
    for key in admitted:
        entity = entities[key]
        script = entity.action_script if key[0] == 'automation' else entity.script
        _idle_census(script)
        if key[0] == 'automation' and entity._is_enabled:
            from datetime import timedelta
            _need(all(cfg.get('platform') == 'state'
                      and ('for' not in cfg or type(cfg['for']) is timedelta and cfg['for'] > timedelta(0))
                      and cfg.get('enabled', True) is True and 'variables' not in cfg
                      for cfg in entity._trigger_config), 'automation_opaque_caller_unsupported')
    return entities, frozenset(admitted), frozenset(changed)


def assert_idle_automation_program_targets(hass, program, *, active=None):
    target = frozenset(program.source_order) if active is None else active
    before, after = dict(program.rows(frozenset())), dict(program.rows(target))
    _call_scope(hass, before, after)
    # Await source/graph admission before the final synchronous effect seam.



class NativeAutomationConfigTransport:
    # Reuse the exact existing native projector, cache census and write rules.
    _project = NativeScriptConfigTransport._project
    _project_value = NativeScriptConfigTransport._project_value
    _write = staticmethod(NativeScriptConfigTransport._write)

    @classmethod
    def capture(cls, observer, chain, source, *, reverse=False, guard, input_state=None):
        from .dependent_config_binding import NativeDependentConfigReadback
        _need(cls is NativeAutomationConfigTransport and type(observer) is NativeDependentConfigReadback)
        guard()
        sample = observer.sample(chain, input_state=input_state)
        active = sample.prefix.active
        _need((source in active) == reverse)
        target = active - {source} if reverse else active | {source}
        observer.program.assert_edge(active, target, source)
        before, after = dict(observer.program.rows(active)), dict(observer.program.rows(target))
        entities, admitted, changed = _call_scope(observer.hass, before, after)
        self = object.__new__(cls)
        self.observer, self.chain, self.guard, self.sample = observer, chain, guard, sample
        self.input_state, self.before, self.after = input_state, before, after
        self.task, self.phase, self.operations, self._projected = asyncio.current_task(), 'captured', [], {}
        self.actors = []
        for domain, key in sorted(admitted):
            entity = entities[domain, key]
            script = entity.action_script if domain == 'automation' else entity.script
            self.actors.append(_Actor(entity, script, _idle_census(script),
                tuple((name, getattr(script, name), tuple(getattr(script, name).items()))
                      for name in _cache_names()), (domain, key) in changed))
        for domain in ('automation', 'script'):
            _need([r[0] for r in before[domain]] == [r[0] for r in after[domain]])
            for old, new in zip(before[domain], after[domain], strict=True):
                if old == new:
                    continue
                entity = entities[domain, old[0]]
                script = entity.action_script if domain == 'automation' else entity.script
                self._project(entity.raw_config, old[1], new[1])
                if domain == 'automation':
                    _need(old[4:7] == new[4:7] and old[8:] == new[8:]
                          and script.variables is None, 'automation_metadata_change_unsupported')
                    # Matchers/topology stay native; ONLY static entity IDs change.
                    from .yaml_validation_worker import _normal
                    for cfg, old_cfg, new_cfg in zip(entity._trigger_config, old[2], new[2], strict=True):
                        _need(type(old_cfg) is dict and type(new_cfg) is dict)
                        a, b = dict(old_cfg['$map']), dict(new_cfg['$map'])
                        _need({k:v for k,v in a.items() if k != 'entity_id'}
                              == {k:v for k,v in b.items() if k != 'entity_id'},
                              'automation_trigger_topology_change_unsupported')
                    self._project(entity._trigger_config, old[2], new[2])
                    self._project(script.sequence, old[3], new[3])
                    self._project(entity._variables, old[7], new[7])
                else:
                    _need(old[3:6] == new[3:6] and old[7:] == new[7:])
                    self._project(script.sequence, old[2], new[2])
                    self._project(script.variables, old[6], new[6])
        self.operations, self.actors = tuple(self.operations), tuple(self.actors)
        self.graph = observer.owned_automation
        self.graph.transfer = self
        _need(self.graph is not None)
        mapping = dict(observer.program.mapping)
        edge = {mapping[source]: source} if reverse else {source: mapping[source]}
        self.graph.capture(tuple(actor.entity for actor in self.actors
            if actor.entity.entity_id.startswith('automation.')), edge)
        self.original = self._actor_graph()
        self.check_before()
        return self

    def _actor_graph(self, *, slots=None):
        return tuple((id(a.entity), id(a.script), _graph(a.entity.raw_config, slots=slots),
            _graph(a.script.sequence, slots=slots), _graph(a.script.variables, slots=slots),
            _graph(getattr(a.entity, '_variables', None), slots=slots),
            _graph(getattr(a.entity, '_trigger_config', None), slots=slots)) for a in self.actors)

    def check_before(self):
        _need(asyncio.current_task() is self.task and self.phase == 'captured')
        self.guard()
        _need(self._actor_graph() == self.original)
        _need(self.observer.sample(self.chain, input_state=self.input_state) == self.sample)
        for actor in self.actors:
            _need(_idle_census(actor.script) == actor.census)
        for route in self.graph.routes:
            route.binding.assert_original()

    def _rows(self, expected):
        from .yaml_runtime_observer import NativeYAMLRuntimeReadback
        from .yaml_validation_worker import _normal
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        native = NativeYAMLRuntimeReadback(self.observer.hass, owned_automation=self.graph)
        for domain, rows in expected.items():
            component = self.observer.hass.data.get(DATA_INSTANCES, {}).get(domain)
            actual, identities = native._projection(domain) if component is not None else ([], ())
            pinned = {name: (component_id, tokens) for name, component_id, tokens in self.sample.native_tokens}
            _need(id(component) == pinned[domain][0], 'automation_component_identity_changed')
            previous = pinned[domain][1]
            # Automation registration tokens change explicitly with our wrapper;
            # entity/root/raw/registry identities may NEVER be adopted anew.
            same = (tuple(row[:5] for row in identities) == tuple(row[:5] for row in previous)
                    if domain == 'automation' else identities == previous)
            _need(same, 'automation_actor_identity_changed')
            _need(_normal(actual) == rows, 'automation_full_native_row_mismatch')

    def install(self, witness):
        self.check_before()
        from .native_automation_duration_binding import _PreEffectLog
        log=_PreEffectLog()
        # Retain unmodified slots too; never restore an uncleared foreign cache.
        for kind,container,_,_ in self.operations:
            log.retain(kind,container)
        slots={id(container):before for _,container,before,_ in self.operations}
        def cache_token(value):
            # Observe the retained ORIGINAL slot references while traversing
            # their actual current children. Unlike masking a whole edited
            # container, this detects foreign changes in untouched descendants.
            return _graph(value,include_scripts=True,slots=slots)
        # Replaced Template/annotated-string leaves are retained objects too.
        # An edit inside our projected replacement must not be discarded just
        # because its containing slot still has the same object identity.
        projected=tuple((after,cache_token(after)) for _,_,_,after in self.operations)
        saved=[]
        for actor in self.actors:
            for name,cache,items in actor.caches:
                log.retain('dict',cache)
                saved.append((actor.script,name,cache,items,cache_token(items)))
        def cleanup_plan():
            _need(self._actor_graph(slots=slots)==self.original, 'automation_cleanup_root_changed')
            _need(all(cache_token(value)==token for value,token in projected),
                  'automation_cleanup_projected_value_changed')
            for actor in self.actors:
                _need(actor.entity.action_script is actor.script
                      if actor.entity.entity_id.startswith('automation.')
                      else actor.entity.script is actor.script,
                      'automation_cleanup_script_changed')
                _idle_census(actor.script)
            _need(all(getattr(script,name) is cache and cache_token(items)==token
                      for script,name,cache,items,token in saved), 'automation_cleanup_cache_changed')
            return log.plan()  # all config/cache slots validated before any cleanup write
        self._config_log,self._config_cleanup_plan=log,cleanup_plan
        try:
            for operation in self.operations:
                kind,container,before,after=operation
                log.write(kind,container,after,lambda: self._write(operation))
            for actor in self.actors:
                _idle_census(actor.script)
                if actor.changed:
                    for _, cache, _ in actor.caches:
                        log.write('dict',cache,(),cache.clear)
            self.graph.install(witness)
            self._rows(self.after)
            self.installed = self._actor_graph()
        except BaseException:
            try:
                plan=cleanup_plan()
                self.graph.retire(before_attempt=True)
                log.undo(plan)
                self.phase = 'failed_before_attempt'
            except BaseException:
                self.phase = 'failed_before_attempt_cleanup_unknown'
                raise
            raise
        self.phase = 'installed'

    def retain_after_attempt(self):
        _need(self.phase == 'installed' and asyncio.current_task() is self.task)
        self.phase = 'attempted'

    def restore_before_attempt(self):
        _need(self.phase == 'installed' and asyncio.current_task() is self.task)
        _need(self._actor_graph() == self.installed)
        self._rows(self.after)
        for actor in self.actors:
            _idle_census(actor.script)
            if actor.changed:
                _need(all(not cache for _, cache, _ in actor.caches))
        plan=self._config_cleanup_plan()
        try:
            self.graph.retire(before_attempt=True)
            self._config_log.undo(plan)
            self._rows(self.before)
            self.phase = 'restored_before_attempt'
        except BaseException:
            self.phase = 'failed_before_attempt_cleanup_unknown'
            raise
