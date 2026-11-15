"""Private, pre-registry ownership of a Template tracker's ``this`` slots.

The Root-owned binding transport separately moves RenderInfo dependencies and
native subscriptions.  This helper does not authorize a registry effect and
cannot undo its slots after the native registry attempt.
"""
from __future__ import annotations

import asyncio
import re

from .native_automation_duration_binding import _PreEffectLog
from .native_template_binding_transport import _Runtime
from .transaction import Refused


def _need(condition, reason):
    if not condition:
        raise Refused(reason) from None


def _items(table, omitted=()):
    _need(type(table) is dict and all(type(key) is str for key in table), 'own_this_dictionary_unqualified')
    return tuple((key, value) for key, value in table.items() if key not in omitted)


def _same_items(left, right):
    return (len(left) == len(right)
            and all(k is old_k and value is old_value
                    for (k, value), (old_k, old_value) in zip(left, right)))


def _own_variable_names(entity):
    """Find the positively supported configured ``this.entity_id`` snapshot.

    TemplateEntity renders configured variables once at startup. Replacing the
    tracker's ``this`` object alone would leave a saved self-ID variable stale.
    Support the exact direct expression and its fixed early-render fallback;
    other configured ``this`` expressions are refused until their
    historical/rendering semantics are proven.
    """
    from homeassistant.helpers.script_variables import ScriptVariables
    from homeassistant.helpers.template import Template
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass

    configured = entity._run_variables
    if type(configured) is ScriptVariables:
        configured = configured.variables
    _need(type(configured) in (dict, NodeDictClass), 'own_this_configured_variables_unqualified')
    result, bodies, budget = [], [], [20_000]
    def visit(value, name=None, direct=False, ancestors=()):
        budget[0] -= 1
        _need(budget[0] >= 0 and len(ancestors) <= 64, 'own_this_configured_variables_limit')
        if id(value) in ancestors:
            raise Refused('own_this_configured_variables_cycle') from None
        if type(value) is Template:
            body = value.template
            bodies.append((name if direct else None, body))
            if re.search(r'\bthis\b', body):
                # A quoted word "this" is not a reference. Parse (never
                # render) the native Jinja expression before rejecting it.
                from jinja2 import Environment, TemplateSyntaxError, nodes
                try:
                    parsed = Environment().parse(body)
                except TemplateSyntaxError:
                    raise Refused('own_this_configured_expression_unsupported') from None
                if not any(item.name == 'this' for item in parsed.find_all(nodes.Name)):
                    return
                exact = re.fullmatch(r'\s*\{\{\s*this\.entity_id\s*\}\}\s*', body)
                guarded = re.fullmatch(r'''\s*\{\{\s*this\.entity_id\s+if\s+this\s+is\s+defined\s+else\s+(['"])unknown\1\s*\}\}\s*''', body)
                _need(direct and type(name) is str and name != 'this'
                      and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
                      and (exact or guarded),
                      'own_this_configured_expression_unsupported')
                result.append(name)
            return
        if type(value) in (dict, NodeDictClass):
            for key, child in value.items():
                visit(child, key, value is configured, (*ancestors, id(value)))
        elif type(value) in (list, tuple, NodeListClass):
            for child in value:
                visit(child, None, False, (*ancestors, id(value)))
        elif type(value) is str and re.search(r'\bthis\b', value) and ('{{' in value or '{%' in value):
            raise Refused('own_this_configured_expression_unsupported') from None
    visit(configured)
    _need(len(result) == len(set(result)), 'own_this_configured_variable_ambiguous')
    if result:
        # Other configured expressions are evaluated in the same ordered
        # variable namespace. Even without a literal self_ref token they can
        # have an indirect dependency, so this bounded profile refuses them.
        _need(all(owner in result for owner, _ in bodies),
              'own_this_configured_variable_alias_unsupported')
    return tuple(result)


class NativeOwnIDThisTransport:
    """One-task, exact-slot replacement with a checked pre-attempt undo only."""

    @classmethod
    def capture(cls, hass, entity, destination, *, guard, ingress=None):
        from homeassistant.helpers import entity_registry as er

        _need(asyncio.current_task() is not None and callable(guard), 'own_this_task_required')
        if ingress is None:
            runtime = _Runtime(hass)
            tracker = runtime.graph(entity)
        else:
            from .native_template_rename_ingress import NativeTemplateRenameIngress
            _need(type(ingress) is NativeTemplateRenameIngress and ingress.live
                  and ingress.transport.hass is hass
                  and any(row['entity'] is entity for row in ingress.transport.rows),
                  'own_this_ingress_unqualified')
            runtime = ingress.transport.runtime
            tracker = runtime.graph_with_ingress(entity, ingress)
        old = entity.entity_id
        _need(type(destination) is str and destination != old
              and destination.split('.', 1)[0] == old.split('.', 1)[0],
              'own_this_destination_unqualified')
        registry = er.async_get(hass)
        row = registry.async_get(old)
        _need(row is not None and row is entity.registry_entry
              and registry.async_get(destination) is None
              and hass.states.get(destination) is None, 'own_this_registry_changed')
        tracks = tuple(tracker._track_templates)
        _need(bool(tracks), 'own_this_tracks_missing')
        groups = {}
        for track in tracks:
            variables = track.variables
            this = variables['this']
            _need(type(this._collect) is bool and type(this._cache) is dict,
                  'own_this_native_cache_changed')
            groups[id(variables)] = (variables, this)
        obj = cls()
        obj.hass, obj.entity, obj.destination, obj.old = hass, entity, destination, old
        obj.runtime, obj.registry, obj.row, obj.tracker = runtime, registry, row, tracker
        obj.ingress = ingress
        obj.state, obj.task, obj.guard = hass.states.get(old), asyncio.current_task(), guard
        obj.tracks, obj.groups = tracks, tuple(groups.values())
        from .native_script_config_transport import _graph
        from .native_template_binding_transport import NativeTemplateBindingTransport
        obj.configured_owner = entity._run_variables
        obj.configured_graph = _graph(obj.configured_owner)
        obj.configured_holds = NativeTemplateBindingTransport._retain_graph(obj.configured_owner)
        obj.configured_names = _own_variable_names(entity)
        obj.variable_edits = tuple((variables, name, variables.get(name))
                                   for variables, _ in obj.groups for name in obj.configured_names)
        _need(all(type(value) is str and value == old for _, _, value in obj.variable_edits),
              'own_this_rendered_variable_changed')
        obj.track_owners = tuple((track, track.variables) for track in tracks)
        obj.group_edges = tuple((variables, this, _items(variables, ('this', *obj.configured_names)),
                                 this._collect, this._cache, _items(this._cache))
                                for variables, this in obj.groups)
        obj.log, obj.phase = _PreEffectLog(), 'captured'
        obj.installed_replacements = ()
        obj._check_aliases()
        return obj

    def _check_aliases(self):
        from homeassistant.helpers.entity_platform import async_get_platforms

        selected_vars = {id(variables) for variables, _ in self.groups}
        selected_this = {id(value) for _, value in self.groups}
        for variables, _ in self.groups:
            selected_this.add(id(variables.get('this')))
        # Census the native Template owners. The Root admission must also
        # qualify the other relevant subscriber and runtime graphs.
        for platform in async_get_platforms(self.hass, 'template'):
            for other in platform.entities.values():
                if other is self.entity:
                    continue
                info = getattr(other, '_template_result_info', None)
                if info is None:
                    continue
                for track in info._track_templates:
                    variables = track.variables
                    _need(type(variables) is dict and id(variables) not in selected_vars
                          and id(variables.get('this')) not in selected_this,
                          'own_this_foreign_template_alias')
        # Reuse the active-program census for every known Script/Automation
        # config, retained run/frame variable and native listener closure. The
        # selected variable dictionaries are owned solely by this tracker;
        # encountering one in another native root is a foreign edge.
        from .native_active_execution_transport import _Projection, _entities
        projection = _Projection(self.hass, set())
        projection.targets.update(selected_vars | selected_this)
        projection.scan(self.hass, _entities(self.hass), ())
        # Physical State attributes and Template configuration are additional
        # known owners. This bounded walk does not inspect arbitrary Python heap.
        from types import MappingProxyType
        from homeassistant.core import Event, State
        from homeassistant.helpers.script_variables import ScriptVariables
        from homeassistant.util.read_only_dict import ReadOnlyDict
        from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass
        from .native_template_binding_transport import _FIELDS
        budget = [200_000]
        def walk(value, ancestors=()):
            budget[0] -= 1
            _need(budget[0] >= 0 and len(ancestors) <= 64, 'own_this_alias_inventory_limit')
            _need(id(value) not in selected_vars and id(value) not in selected_this,
                  'own_this_foreign_native_alias')
            if id(value) in ancestors:
                return
            ancestors = (*ancestors, id(value))
            if type(value) is ScriptVariables:
                walk(value.variables, ancestors)
            elif type(value) is State:
                walk(value.attributes, ancestors)
            elif type(value) is Event:
                walk(value.data, ancestors)
            elif type(value) in (dict, NodeDictClass, MappingProxyType, ReadOnlyDict):
                for child in value.values():
                    walk(child, ancestors)
            elif type(value) in (list, tuple, NodeListClass):
                for child in value:
                    walk(child, ancestors)
        for platform in async_get_platforms(self.hass, 'template'):
            for entity in platform.entities.values():
                for name in _FIELDS:
                    if hasattr(entity, name):
                        walk(getattr(entity, name))
                tracker = getattr(entity, '_template_result_info', None)
                if tracker is not None:
                    _need(type(tracker) is self.runtime.event.TrackTemplateResultInfo,
                          'own_this_tracker_inventory_unqualified')
                    walk(tracker._last_result)
                    timers = tracker._rate_limit._rate_limit_timers
                    _need(type(timers) is dict, 'own_this_timer_inventory_unqualified')
                    for handle in timers.values():
                        _need(type(handle) is asyncio.TimerHandle and type(handle._args) is tuple,
                              'own_this_timer_inventory_unqualified')
                        walk(handle._args)
        for state in self.hass.states.async_all():
            walk(state.attributes)

    def _check(self, *, installed, require_task=True, strict_state=True):
        _need(not require_task or asyncio.current_task() is self.task, 'own_this_task_changed')
        self.runtime.sources()
        from .native_script_config_transport import _graph
        _need(self.entity._run_variables is self.configured_owner
              and _graph(self.configured_owner) == self.configured_graph,
              'own_this_configured_variables_changed')
        _need(self.entity.entity_id == self.old and self.entity.registry_entry is self.row
              and self.registry.async_get(self.old) is self.row
              and self.registry.async_get(self.destination) is None
              and (not strict_state or self.hass.states.get(self.old) is self.state)
              and self.hass.states.get(self.old) is not None
              and self.hass.states.get(self.destination) is None
              and self.entity._template_result_info is self.tracker
              and len(self.tracker._track_templates) == len(self.tracks)
              and all(a is b for a, b in zip(self.tracker._track_templates, self.tracks)),
              'own_this_native_owner_changed')
        self._check_aliases()
        for track, variables in self.track_owners:
            _need(track.variables is variables, 'own_this_track_owner_changed')
        for variables, original, items, collect, cache_owner, cache in self.group_edges:
            _need(_same_items(_items(variables, ('this', *self.configured_names)), items)
                  and original._collect is collect and original._cache is cache_owner
                  and _same_items(_items(original._cache), cache)
                  and original._entity_id == self.old and original._hass is self.hass,
                  'own_this_retained_graph_changed')
            if not installed:
                _need(variables.get('this') is original, 'own_this_slot_changed')
        if not installed:
            _need(all(variables.get(name) is value for variables, name, value in self.variable_edits),
                  'own_this_rendered_variable_changed')
            graph = (self.runtime.graph(self.entity) if self.ingress is None else
                     self.runtime.graph_with_ingress(self.entity, self.ingress))
            _need(graph is self.tracker, 'own_this_native_graph_changed')
        else:
            # Exact checked plan ensures every installed slot still has our
            # very object before any undo write occurs.
            self.log.plan()
            # Native ``entity_id`` is the only cached property on the exact
            # TemplateStateFromEntityId implementation admitted by _Runtime.
            # Its lazy first read may naturally hydrate this cache; foreign
            # entries, another cache object or altered owner fields must never
            # be silently discarded by pre-attempt undo.
            for replacement, cache, collect in self.installed_replacements:
                _need(type(replacement) is self.runtime.ThisState
                      and replacement._hass is self.hass
                      and replacement._entity_id is self.destination
                      and replacement._collect is collect
                      and replacement._cache is cache and type(cache) is dict
                      and (not cache or (len(cache) == 1
                           and cache.get('entity_id') is self.destination)),
                      'own_this_installed_cache_changed')

    def check_review(self):
        """Recheck the same owner and this graph in a later apply task."""
        _need(self.phase == 'captured', 'own_this_phase_changed')
        self._check(installed=False, require_task=False, strict_state=False)

    def install(self):
        _need(self.phase == 'captured', 'own_this_phase_changed')
        self.guard()
        self._check(installed=False)
        cls = self.runtime.ThisState
        replacements = {}
        try:
            for variables, original in self.groups:
                replacement = replacements.get(id(original))
                if replacement is None:
                    replacement = cls(self.hass, self.destination, collect=original._collect)
                    replacements[id(original)] = replacement
                    self.installed_replacements += ((replacement, replacement._cache, original._collect),)
                self.log.write('entry', (variables, 'this'), replacement,
                               lambda variables=variables, replacement=replacement:
                                   variables.__setitem__('this', replacement))
            for variables, name, _ in self.variable_edits:
                self.log.write('entry', (variables, name), self.destination,
                               lambda variables=variables, name=name:
                                   variables.__setitem__(name, self.destination))
        except BaseException:
            try:
                _PreEffectLog.undo(self.log.plan())
            except BaseException:
                self.phase = 'uncertain'
                raise
            self.phase = 'restored'
            raise
        self.phase = 'installed'

    def assert_installed_before_attempt(self):
        _need(self.phase == 'installed', 'own_this_phase_changed')
        self._check(installed=True)

    def restore_before_attempt(self):
        _need(self.phase == 'installed', 'own_this_phase_changed')
        self._check(installed=True)
        plan = self.log.plan()
        _PreEffectLog.undo(plan)
        self.phase = 'restored'


def qualify_template_own_changes(hass, changes, *, guard):
    """Preflight one Template sensor and at most one native Sensor source.

    Re-run from actual rows at review, fresh bind and the final effect. This
    grants no capability by itself; the normal accepted Root owns the effect.
    """
    from .native_registry import NativeRegistryIO
    from .native_source_handoff import NativeSourceHandoff

    changes = tuple(changes)
    owned = [change for change in changes if change.before.get('platform') == 'template']
    _need(len(owned) == 1 and len(changes) in (1, 2), 'own_this_mapping_scope_unqualified')
    io = NativeRegistryIO(hass)
    registry = io.registries['entity']
    admission = None
    for change in changes:
        _need(change.kind == 'entity' and len(change.mapping) == 1
              and {key: value for key, value in change.before.items()
                   if key not in ('entity_id', 'modified_at')}
                  == {key: value for key, value in change.after.items()
                      if key not in ('entity_id', 'modified_at')},
              'own_this_registry_change_unqualified')
        old, new = next(iter(change.mapping.items()))
        before, after = registry.async_get(old), registry.async_get(new)
        _need((before is None) != (after is None), 'own_this_registry_position_ambiguous')
        row = before if before is not None else after
        target = change.after if before is not None else change.before
        _need(row.id == change.identity and NativeSourceHandoff.supports_source(io, change, target),
              'own_this_source_handoff_unqualified')
        if change is owned[0]:
            from homeassistant.helpers.entity_platform import async_get_platforms
            entities = [entity for platform in async_get_platforms(hass, 'template')
                        for entity in platform.entities.values() if entity.registry_entry is row]
            _need(len(entities) == 1, 'own_this_entity_unqualified')
            admission = NativeOwnIDThisTransport.capture(hass, entities[0], target['entity_id'], guard=guard)
        guard()
    _need(admission is not None, 'own_this_entity_unqualified')
    return admission
