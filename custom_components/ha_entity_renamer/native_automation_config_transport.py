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
            from .native_automation_scalar_duration import configured_duration
            for cfg in entity._trigger_config:
                if 'for' in cfg and type(cfg['for']) is not timedelta:
                    configured_duration(cfg, hass)
            _need(all(cfg.get('platform') == 'state'
                      and ('for' not in cfg or type(cfg['for']) is timedelta and cfg['for'] > timedelta(0)
                           or type(cfg['for']) in (Template, dict))
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
        self.duration_edges = ()
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
                        allowed = {'entity_id'}
                        if a.get('for') != b.get('for'):
                            from .native_automation_scalar_duration import configured_duration
                            from homeassistant.helpers.template import Template
                            from datetime import timedelta
                            _need(type(configured_duration(cfg, observer.hass)) is not timedelta,
                                  'automation_literal_duration_edit_unsupported')
                            # Validate the target's closed typed tree without
                            # rendering or copying the actual native root.
                            def target(value):
                                if type(value) is dict and set(value) == {'$template'}:
                                    _need(type(value['$template']) is str)
                                    return Template(value['$template'], observer.hass)
                                if type(value) is dict and set(value) == {'$map'}:
                                    pairs = value['$map']
                                    _need(type(pairs) is list and 0 < len(pairs) <= 5
                                          and all(type(pair) is list and len(pair) == 2
                                                  and type(pair[0]) is str for pair in pairs)
                                          and len({pair[0] for pair in pairs}) == len(pairs))
                                    return {key: target(child) for key, child in pairs}
                                _need(type(value) in (str, int, float, bool))
                                return value
                            configured_duration({'for': target(b.get('for'))}, observer.hass)
                            allowed.add('for')
                        _need({k:v for k,v in a.items() if k not in allowed}
                              == {k:v for k,v in b.items() if k not in allowed},
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
        self._capture_duration_edges(entities)
        self.original = self._actor_graph()
        self.check_before()
        return self

    def scalar_edge(self, binding):
        """Authenticate exact native config position in this sealed program edge."""
        from .yaml_validation_worker import _normal
        from .native_automation_scalar_duration import configured_duration
        configured_duration(binding.config, self.observer.hass)
        rows = {row[0]: row for row in self.before['automation']}
        row = rows[binding.entity.unique_id]
        _need(_normal(binding.entity._trigger_config) == row[2],
              'automation_duration_program_config_changed')

    def _capture_duration_edges(self, entities):
        from .native_automation_configured_duration import ConfiguredEdge, _frozen
        from .yaml_validation_worker import _normal
        edges = []
        for route in self.graph.routes:
            b = route.binding
            if not hasattr(b, '_duration_scalar_image'):
                continue
            self.scalar_edge(b)
            old = next(row for row in self.before['automation'] if row[0] == b.entity.unique_id)
            new = next(row for row in self.after['automation'] if row[0] == b.entity.unique_id)
            before = dict(old[2][b.index]['$map'])['for']
            after = dict(new[2][b.index]['$map'])['for']
            if _frozen(before) == _frozen(after):
                continue  # existing exact Template/cell and no metadata rewrite
            replacement = self._project(b.config['for'], before, after)
            # For dict, the actual root is unchanged until the authorized
            # projector operation writes its leaves. Edge.capture proves the
            # predicted normal form and graphs before that first write.
            b._duration.scalar.check_configured(replacement)
            if type(replacement) is not dict:
                _need(_normal(replacement) == after)
            pending, rows = b._duration.pending(b)
            _need(rows == route.duration_snapshot, 'automation_duration_before_changed')
            edge = ConfiguredEdge.capture(b, replacement, pending,
                before_normal=before, after_normal=after, operations=self.operations,
                projected=self._projected)
            edges.append(edge)
        # Every installed duration consumer of a shared replacement must be in
        # the authenticated call scope. Never silently alter another listener.
        self.duration_edges = tuple(edges)
        self.duration_alias_entities = tuple(entities.items())
        # Retain authentic history references before writes. An alias scan at a
        # partial journal prefix must not invoke a live AFTER pending census.
        from .native_automation_duration_binding import _cells
        history = []
        for route in self.graph.routes:
            binding = route.binding
            if not hasattr(binding, '_duration'):
                continue
            pending, _ = binding._duration.pending(binding)
            rows = []
            for item in pending:
                actual = item['own'].history() if item['own'] is not None else _cells(item['action'])
                rows.append((item['clear'], actual['from_s'], actual['to_s'], actual['event']))
            history.append((binding, tuple(rows)))
        self.duration_alias_history = tuple(history)
        self.duration_alias_inventory = self._duration_aliases(entities, edges)

    def _duration_aliases(self, entities, edges):
        """Bounded inventory of configured consumers and foreign mutable aliases.

        Shared Template leaves are transported once, but every configured
        consumer needs an authenticated route. A shared mutable root additionally
        needs every occurrence to be a known native/raw trigger-for edge. History,
        variables, action/config siblings and lazy caches cannot become aliases
        merely because their object id is already authorized somewhere else.
        """
        from homeassistant.helpers.template import Template
        from homeassistant.helpers.script_variables import ScriptVariables
        from homeassistant.helpers.script import Script
        from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass
        from .native_automation_duration_binding import _cells
        changed_leaves = {path[-4] for edge in edges for path in edge.paths
                          if path[-3] != path[-2]}
        mutable = {id(edge.before) for edge in edges if type(edge.before) is dict}
        configured = {id(edge.binding.config) for edge in edges}
        if not mutable:
            # Preserve the original scalar flow: no mutable-origin traversal
            # or new broad census is needed for immutable root replacements.
            for (domain, _), entity in entities.items():
                if domain == 'automation':
                    for cfg in entity._trigger_config:
                        root = cfg.get('for')
                        leaves = ((root,) if type(root) is Template else
                                  tuple(root.values()) if type(root) is dict else ())
                        _need(not any(id(value) in changed_leaves for value in leaves)
                              or id(cfg) in configured,
                              'automation_duration_shared_consumer_unaccounted')
            return ()
        allowed = {((id(edge.binding.entity), 'native', edge.binding.index, 'for'),
                    id(edge.binding.config), 'for', id(edge.before)) for edge in edges}
        inventories = []
        # Actual raw/native topology is inventory, not a guessed global omit.
        for edge in edges:
            b = edge.binding
            raw = b.entity.raw_config
            if type(raw) in (dict, NodeDictClass):
                for name in ('trigger', 'triggers'):
                    trigger = raw.get(name)
                    candidates = trigger if type(trigger) in (list, NodeListClass) else (trigger,)
                    if b.index < len(candidates):
                        cfg = candidates[b.index]
                        if type(cfg) in (dict, NodeDictClass) and id(cfg.get('for')) in mutable:
                            # This parent/root was visited by the same sealed
                            # program projector with a consistent target row.
                            record = self._projected.get(id(cfg))
                            _need(record is not None, 'automation_duration_raw_alias_not_projected')
                            path = ((id(b.entity), 'raw', name, b.index, 'for')
                                    if type(trigger) in (list, NodeListClass) else
                                    (id(b.entity), 'raw', name, 'for'))
                            allowed.add((path, id(cfg), 'for', id(cfg['for'])))
        budget = [100_000]
        def scan(value, parent=None, key=None, ancestry=(), *, path=(), native_dict=False):
            budget[0] -= 1
            _need(budget[0] >= 0 and len(ancestry) <= 64,
                  'automation_duration_alias_inventory_limit')
            if id(value) in mutable:
                _need((path, id(parent), key, id(value)) in allowed,
                      'automation_duration_foreign_mutable_alias')
                inventories.append((path, id(parent), key, id(value)))
            if id(value) in ancestry:
                _need(id(value) not in mutable, 'automation_duration_alias_cycle')
                return
            ancestry = (*ancestry, id(value))
            if type(value) in (dict, NodeDictClass) or native_dict:
                for name, child in dict.items(value):
                    scan(child, value, name, ancestry, path=(*path, name))
            elif type(value) in (list, tuple, NodeListClass):
                for index, child in enumerate(value):
                    scan(child, value, index, ancestry, path=(*path, index))
            elif type(value) is ScriptVariables:
                scan(value.variables, value, 'variables', ancestry, path=(*path, 'variables'))
            elif type(value) is Script:
                scan(value.sequence, value, 'sequence', ancestry, path=(*path, 'sequence'))
                scan(value.variables, value, 'variables', ancestry, path=(*path, 'variables'))
                for name in _cache_names():
                    scan(getattr(value, name), value, name, ancestry, path=(*path, name))
        for (domain, _), entity in entities.items():
            script = entity.action_script if domain == 'automation' else entity.script
            for name, value in (('raw', entity.raw_config), ('sequence', script.sequence),
                                ('script_variables', script.variables),
                                ('entity_variables', getattr(entity, '_variables', None)),
                                ('native', getattr(entity, '_trigger_config', None))):
                scan(value, path=(id(entity), name))
            for name in _cache_names():
                # include_scripts graph keeps native lazy Script cache contents
                # pinned independently; a direct foreign root alias is rejected.
                scan(getattr(script, name), path=(id(entity), 'cache', name))
            if domain == 'automation':
                for cfg in entity._trigger_config:
                    root = cfg.get('for')
                    leaves = ((root,) if type(root) is Template else
                              tuple(root.values()) if type(root) is dict else ())
                    touched = id(root) in mutable or any(id(value) in changed_leaves for value in leaves)
                    _need(not touched or id(cfg) in configured,
                          'automation_duration_shared_consumer_unaccounted')
        for b, pending in self.duration_alias_history:
            scan(b.listener_cells.get('_variables'), path=(id(b), 'attach_variables'))
            for clear, from_state, to_state, event in pending:
                for name, state in (('from_s', from_state), ('to_s', to_state)):
                    if state is not None:
                        scan(state.attributes, native_dict=True,
                             path=(id(b), 'history', id(clear), name, 'attributes'))
                scan(event.data, native_dict=True,
                     path=(id(b), 'history', id(clear), 'event_data'))
        return tuple(inventories)

    def _check_duration_aliases(self):
        actual = self._duration_aliases(dict(self.duration_alias_entities), self.duration_edges)
        _need(actual == self.duration_alias_inventory,
              'automation_duration_alias_inventory_changed')

    def _actor_graph(self, *, slots=None):
        return tuple((id(a.entity), id(a.script), _graph(a.entity.raw_config, slots=slots),
            _graph(a.script.sequence, slots=slots), _graph(a.script.variables, slots=slots),
            _graph(getattr(a.entity, '_variables', None), slots=slots),
            _graph(getattr(a.entity, '_trigger_config', None), slots=slots)) for a in self.actors)

    def check_before(self):
        _need(asyncio.current_task() is self.task and self.phase == 'captured')
        self.guard()
        self._check_duration_aliases()
        for edge in self.duration_edges:
            edge.check_pins()
            for owner, before, _ in edge.owners:
                _need(owner.image is before, "automation_duration_holder_before_changed")
                owner.check()
        _need(self._actor_graph() == self.original)
        _need(self.observer.sample(self.chain, input_state=self.input_state) == self.sample)
        for actor in self.actors:
            _need(_idle_census(actor.script) == actor.census)
        for route in self.graph.routes:
            route.binding.assert_original()

    def _check_restored_configured(self):
        _need(self._actor_graph() == self.original, 'automation_restored_config_changed')
        for edge in self.duration_edges:
            edge.check_pins()
            for owner, before, _ in edge.owners:
                _need(owner.image is before, 'automation_restored_holder_changed')
                owner.check()

    def check_configured_retained(self, *, configured_view=None):
        for edge in self.duration_edges:
            edge.check_pins()
            # Historical OWN holders/snapshot siblings must pass BEFORE undo.
            # Only a journal-validated pre-graph prefix may select a BEFORE view.
            for owner, _, _ in edge.owners:
                owner.check(configured_view)
        slots = getattr(self, '_retained_original_slots', None)
        if slots is not None:
            _need(self._actor_graph(slots=slots) == self.original,
                  'automation_retained_original_config_changed')

    def _rows(self, expected):
        self.check_configured_retained()
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
        for edge in self.duration_edges:
            edge.retain(log)
        slots={id(container):before for _,container,before,_ in self.operations}
        self._retained_original_slots = slots
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
            self._check_duration_aliases()  # global negative proof BEFORE any undo
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
            for edge in self.duration_edges:
                edge.check_pins()
            shadow = {}
            config_plan = log.plan(shadow)
            # All graph plans are read-only and precede the first undo write.
            from .native_automation_configured_duration import ConfiguredRollbackView
            view = ConfiguredRollbackView(self.duration_edges, shadow) if not self.graph.live else None
            graph_plan = self.graph.plan_retire(before_attempt=True, configured_view=view)
            return config_plan, graph_plan
        self._config_log,self._config_cleanup_plan=log,cleanup_plan
        try:
            for operation in self.operations:
                kind,container,before,after=operation
                log.write(kind,container,after,lambda: self._write(operation))
            written = set()
            for edge in self.duration_edges:
                edge.install(log, written)
            for edge in self.duration_edges:
                edge.check_after()
            # Expected stable/pending images use declared path projections;
            # every selected Template graph was fixed before the first write.
            for route in self.graph.routes:
                if hasattr(route.binding, '_duration'):
                    from .native_automation_owned_graph import _stable
                    _need(_stable(route.binding) == route.stable,
                          'automation_duration_after_stable_changed')
                    _need(route.binding._duration.pending(route.binding)[1] == route.duration_snapshot,
                          'automation_duration_after_pending_changed')
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
                plan, graph_plan=cleanup_plan()
                self.graph.apply_retire(graph_plan, before_attempt=True)
                log.undo(plan)
                self._check_restored_configured()
                self._rows(self.before)
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
        plan, graph_plan=self._config_cleanup_plan()
        try:
            self.graph.apply_retire(graph_plan, before_attempt=True)
            self._config_log.undo(plan)
            self._check_restored_configured()
            self._rows(self.before)
            self.phase = 'restored_before_attempt'
        except BaseException:
            self.phase = 'failed_before_attempt_cleanup_unknown'
            raise
