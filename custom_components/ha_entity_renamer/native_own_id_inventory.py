"""Read-only native Script own-ID and actual caller census.

This object grants no write authority. It does not rename an entity or a
service; the Root retains it as one fence for the stable-service profile.
Registry entity IDs and Script service names are separate native identities.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from collections import defaultdict, deque
from functools import partial
from types import CodeType, FunctionType, MethodType

from .transaction import Refused


def _need(value, reason='own_id_inventory_unqualified'):
    if not value:
        raise Refused(reason) from None


def _literal(value):
    from homeassistant.helpers.template import Template
    from homeassistant.util.yaml.objects import NodeStrClass
    if type(value) is Template:
        _need(value.is_static, 'own_id_dynamic_caller_unsupported')
        value = value.template
    _need(type(value) in (str, NodeStrClass), 'own_id_dynamic_caller_unsupported')
    return str(value)


def _ids(value):
    from homeassistant.util.yaml.objects import NodeListClass
    if type(value) in (list, NodeListClass):
        return tuple(_literal(item) for item in value)
    return tuple(item.strip() for item in _literal(value).split(','))


def _service_description(hass, entity):
    from homeassistant.helpers import service as service_helper
    from .native_script_config_transport import _graph
    from .native_template_binding_transport import NativeTemplateBindingTransport
    from homeassistant.util.yaml.objects import NodeStrClass

    cache = hass.data.get(service_helper.SERVICE_DESCRIPTION_CACHE)
    _need(type(cache) is dict, 'own_id_script_description_missing')
    value = cache.get(('script', entity.unique_id))
    _need(type(value) is dict and set(value) == {'name', 'description', 'fields', 'response'}
          and type(value['name']) in (str, NodeStrClass)
          and value['description'] is entity.description and value['fields'] is entity.fields
          and type(value['response']) is dict and set(value['response']) == {'optional'}
          and value['response']['optional'] is True, 'own_id_script_description_changed')
    return cache, value, _graph(value), NativeTemplateBindingTransport._retain_graph(value)


def _script_storage_owners(hass, entity, script, *, include_name):
    """Exclude foreign known-graph owners of the exact moving native slots."""
    from .native_active_execution_transport import _Projection, _entities
    projection = _Projection(hass, set())
    projection.targets.update((id(entity), id(entity.__dict__)))
    if include_name:
        projection.targets.update((id(script), id(script.__dict__)))
        projection.edges.add((id(entity), 'script', id(script)))
    projection.scan(hass, _entities(hass), ())


def _foreign_static_callers(hass, listeners, service_ids):
    """Refuse unowned registered service call sites in the bounded profile.

    The Script/Automation source census handles native configured callers.
    Listener closures and their loaded globals are separate known owners:
    a call site using the native service registry, or naming the script
    domain or either selected service, cannot be adopted as a transportable
    native program. A call may compute its domain or key dynamically. This
    is not a claim about arbitrary dynamically generated Python calls.
    """
    from homeassistant.core import ServiceRegistry
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass, NodeStrClass
    budget = [100_000]
    def walk(value, literals, names, seen):
        budget[0] -= 1
        _need(budget[0] >= 0, 'own_id_service_caller_graph_limit')
        if id(value) in seen:
            return
        seen.add(id(value))
        kind = type(value)
        if kind in (str, NodeStrClass):
            literals.add(str(value))
        elif kind is CodeType:
            names.update(value.co_names)
            for child in value.co_consts:
                walk(child, literals, names, seen)
        elif kind is FunctionType:
            walk(value.__code__, literals, names, seen)
            for name in value.__code__.co_names:
                if name not in value.__globals__:
                    continue
                item = value.__globals__[name]
                # Follow literal route constants and explicit bound/partial
                # service callables and directly referenced local-module
                # functions, never enumerate an arbitrary global object graph.
                if (type(item) in (str, NodeStrClass, partial, MethodType)
                        or type(item) is FunctionType and item.__globals__ is value.__globals__
                        or type(item) is ServiceRegistry and item is hass.services
                        or type(item) in (tuple, list) and len(item) <= 64
                        and all(type(child) in (str, NodeStrClass) for child in item)):
                    walk(item, literals, names, seen)
            for cell in value.__closure__ or ():
                try:
                    walk(cell.cell_contents, literals, names, seen)
                except ValueError:
                    raise Refused('own_id_service_caller_closure_unqualified') from None
        elif kind is MethodType:
            if (value.__self__ is hass.services
                    and value.__func__ in (ServiceRegistry.async_call, ServiceRegistry.call)):
                names.update(('hass_services', value.__func__.__name__))
            else:
                walk(value.__func__, literals, names, seen)
        elif kind is ServiceRegistry and value is hass.services:
            names.add('hass_services')
        elif kind is partial:
            walk(value.func, literals, names, seen)
            walk(value.args, literals, names, seen)
            walk(value.keywords, literals, names, seen)
        elif kind in (dict, NodeDictClass):
            for key, child in value.items():
                walk(key, literals, names, seen)
                walk(child, literals, names, seen)
        elif kind in (tuple, list, NodeListClass, set, frozenset):
            for child in value:
                walk(child, literals, names, seen)
    for members in listeners.values():
        _need(type(members) is list, 'own_id_service_listener_unqualified')
        for job, event_filter in members:
            literals, names, seen = set(), set(), set()
            walk(job.target, literals, names, seen)
            walk(event_filter, literals, names, seen)
            if 'async_call' in names or 'call' in names:
                # A real unowned registry call can compute its domain or
                # key dynamically. Refuse that known call site instead of
                # guessing that some unrelated literal is its target.
                direct = ('services' in names or 'hass_services' in names)
                _need(not direct and not (literals & service_ids)
                      and 'script' not in literals and 'script.' not in literals,
                      'own_id_foreign_service_caller')



class _ServiceEventCensus:
    """Read-only pins of the two actual service events and match-all listeners."""
    def __init__(self, hass, service, old, new):
        from homeassistant.core import EventBus, MATCH_ALL
        bus = hass.bus
        _need(type(bus) is EventBus and bus._hass is hass
              and type(bus._listeners) is defaultdict and bus._listeners.default_factory is list
              and type(bus._match_all_listeners) is list
              and bus._listeners.get(MATCH_ALL) is bus._match_all_listeners,
              'own_id_service_event_bus_unqualified')
        self.hass, self.bus, self.listeners, self.service = hass, bus, bus._listeners, service
        self.service_ids = frozenset((old, new))
        self.match_all = bus._match_all_listeners
        self.queue = getattr(bus, '_event_queue', None)
        _need(self.queue is None or type(self.queue) is deque, 'own_id_service_event_bus_unqualified')
        self.scene_projection = False
        self.scene_fence = None
        self.scene_proof = ()
        self.token, self.holds = self._sample()
        self._aliases()

    def _aliases(self):
        from .native_active_execution_transport import _Projection, _entities
        _need(all(type(members) is list and all(type(pair) is tuple and len(pair) == 2
                  and pair[0] is not self.service.job for pair in members)
                  for members in self.listeners.values()), 'own_id_service_foreign_job_alias')
        projection = _Projection(self.hass, set())
        projection.targets.update((id(self.service), id(self.service.job)))
        projection.scan(self.hass, _entities(self.hass), ())
        _foreign_static_callers(self.hass, self.listeners, self.service_ids)

    def _sample(self):
        from homeassistant.core import EventBus, HassJob, HassJobType, MATCH_ALL, get_hassjob_callable_job_type
        from .native_automation_duration_binding import _retained_value
        bus = self.bus
        _need(self.hass.bus is bus and type(bus) is EventBus and bus._hass is self.hass
              and bus._listeners is self.listeners and bus._match_all_listeners is self.match_all
              and self.listeners.get(MATCH_ALL) is self.match_all
              and getattr(bus, '_event_queue', None) is self.queue
              and (self.queue is None or type(self.queue) is deque and not self.queue)
              and getattr(bus, '_dispatching', False) is False,
              'own_id_service_event_bus_changed')
        holds, seen, budget = [], set(), [100_000]
        def shape(value):
            if type(value) is FunctionType:
                cells = []
                for name, cell in zip(value.__code__.co_freevars, value.__closure__ or (), strict=True):
                    try:
                        child = cell.cell_contents
                    except ValueError:
                        raise Refused('own_id_service_listener_graph_unqualified') from None
                    event_names = ('service_removed', 'service_registered', '*')
                    relevant = None
                    if type(child) in (set, frozenset):
                        relevant = tuple((event, event in child) for event in event_names)
                    elif type(child) is dict:
                        relevant = tuple((event, event in child,
                            _retained_value(child[event], ()) if event in child else None)
                            for event in event_names)
                    cells.append((name, id(cell), id(child), relevant))
                return (id(value), id(value.__code__), id(value.__globals__), tuple(cells),
                    tuple((name, id(value.__globals__[name])) for name in value.__code__.co_names
                          if name in value.__globals__))
            if type(value) is MethodType:
                return (id(value), id(value.__self__), shape(value.__func__))
            if type(value) is partial:
                return (id(value), shape(value.func), id(value.args), tuple(map(id, value.args)),
                        id(value.keywords), () if value.keywords is None else
                        tuple((id(k), id(v)) for k, v in value.keywords.items()))
            return type(value), id(value)
        def retain(value):
            budget[0] -= 1
            _need(budget[0] >= 0, 'own_id_service_event_graph_limit')
            if id(value) in seen:
                return
            seen.add(id(value)); holds.append(value)
            if type(value) is FunctionType:
                retain(value.__code__)
                for name in value.__code__.co_names:
                    if name in value.__globals__:
                        holds.append(value.__globals__[name])
                for cell in value.__closure__ or ():
                    retain(cell)
                    try:
                        retain(cell.cell_contents)
                    except ValueError:
                        pass
                retain(value.__dict__)
            elif type(value) is partial:
                retain(value.func); retain(value.args); retain(value.keywords)
            elif type(value) is MethodType:
                retain(value.__self__); retain(value.__func__)
            elif type(value) in (list, tuple):
                for item in value:
                    retain(item)
            elif type(value) is dict:
                for key, child in value.items():
                    retain(key); retain(child)
        excluded = set()
        if self.scene_projection:
            from .native_own_script_service_recipients import NativeOwnScriptServiceRecipients
            fence = self.scene_fence
            if fence is not None and fence.closed:
                fence = None
            proof = NativeOwnScriptServiceRecipients.scene_recipients(self.hass, fence)
            if fence is not None:
                _need(len(proof) == len(self.scene_proof) and all(
                    current is pinned for current, pinned in zip(proof, self.scene_proof, strict=True)),
                    'own_id_service_scene_projection_changed')
            excluded = {id(proof[index]) for index in (1, 6)} if proof else set()
        rows = []
        for event_type in ('service_removed', 'service_registered', MATCH_ALL):
            members = self.listeners.get(event_type)
            _need(members is None or type(members) is list, 'own_id_service_listener_unqualified')
            snapshots = []
            for pair in members or ():
                if id(pair) in excluded:
                    continue
                _need(type(pair) is tuple and len(pair) == 2 and type(pair[0]) is HassJob,
                      'own_id_service_listener_unqualified')
                job, event_filter = pair
                _need(job is not self.service.job, 'own_id_service_foreign_job_alias')
                try:
                    retain(pair); retain(job); retain(job.target); retain(job._cache); retain(event_filter)
                    _need(type(job.name) in (str, type(None))
                          and type(job._cancel_on_shutdown) in (bool, type(None))
                          and type(job._cache) is dict and set(job._cache) <= {'job_type'},
                          'own_id_service_listener_job_unqualified')
                    job_type = (job._cache['job_type'] if 'job_type' in job._cache
                                else get_hassjob_callable_job_type(job.target))
                    _need(type(job_type) is HassJobType, 'own_id_service_listener_job_unqualified')
                    target_graph, filter_graph = (shape(job.target), _retained_value(event_filter, ()))
                except (ValueError, RecursionError):
                    raise Refused('own_id_service_listener_graph_unqualified') from None
                snapshots.append((id(pair), id(job), job.name, job._cancel_on_shutdown,
                                  id(job._cache), job_type, id(event_filter),
                                  target_graph, filter_graph))
            member_id = (None if self.scene_projection and event_type != MATCH_ALL else id(members))
            rows.append((event_type, member_id, tuple(snapshots)))
        return tuple(rows), tuple(holds)

    def project_scene_fence(self, fence):
        """Retain the external census while the authenticated Root latch changes lifetime."""
        _need(not self.scene_projection, 'own_id_service_scene_projection_changed')
        self.check()
        from .native_own_script_service_recipients import NativeOwnScriptServiceRecipients
        proof = NativeOwnScriptServiceRecipients.scene_recipients(self.hass, fence)
        _need(bool(proof), 'own_id_service_scene_projection_changed')
        self.scene_fence, self.scene_proof, self.scene_projection = fence, proof, True
        self.token, self.holds = self._sample()
        self._aliases()

    def attach_scene_fence(self, fence):
        _need(self.scene_projection and self.scene_fence is not None
              and self.scene_fence.closed and fence is not self.scene_fence,
              'own_id_service_scene_projection_changed')
        from .native_own_script_service_recipients import NativeOwnScriptServiceRecipients
        proof = NativeOwnScriptServiceRecipients.scene_recipients(self.hass, fence)
        _need(bool(proof), 'own_id_service_scene_projection_changed')
        self.scene_fence, self.scene_proof = fence, proof
        self.check()

    def check(self):
        token, _ = self._sample()
        _need(token == self.token, 'own_id_service_listener_changed')
        self._aliases()


def _script_attr_descriptor(entity, name):
    """Qualify the native metaclass property and its exact backing slot.

    Source code for the setter is authenticated separately by the service
    audit before any write. No getter or setter supplied by an unknown owner is
    called to discover a slot.
    """
    import inspect
    from operator import attrgetter
    from types import FunctionType
    from homeassistant.components.script import ScriptEntity
    from homeassistant.helpers import entity as entity_module
    _need(type(entity) is ScriptEntity and name in ('unique_id', 'name'),
          'own_id_script_attr_unqualified')
    descriptor = inspect.getattr_static(ScriptEntity, '_attr_' + name, None)
    _need(type(descriptor) is property and type(descriptor.fget) is attrgetter
          and descriptor.fget.__reduce__() == (attrgetter, ('__attr_' + name,))
          and type(descriptor.fset) is FunctionType
          and descriptor.fset.__globals__ is vars(entity_module)
          and descriptor.fset.__defaults__ is None and descriptor.fset.__kwdefaults__ is None
          and descriptor.fset.__code__.co_name == '_setter',
          'own_id_script_attr_unqualified')
    setter = descriptor.fset
    _need(setter.__closure__ is not None
          and dict(zip(setter.__code__.co_freevars,
                       (cell.cell_contents for cell in setter.__closure__), strict=True))
              == {'name': name, 'private_attr_name': '__attr_' + name},
          'own_id_script_attr_unqualified')
    return descriptor


def _sample(hass, old, new, *, stable_service=False):
    from homeassistant.const import __version__
    from homeassistant.components.script import ScriptEntity, SCRIPT_SERVICE_SCHEMA
    from homeassistant.core import HomeAssistant, HassJob, Service, ServiceRegistry, SupportsResponse
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity_component import DATA_INSTANCES, EntityComponent
    from homeassistant.helpers.template import Template
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass, NodeStrClass
    from .native_script_config_transport import _graph, _cache_names
    from .yaml_runtime_observer import NativeYAMLRuntimeReadback
    from .yaml_validation_worker import _normal

    _need(type(stable_service) is bool
          and __version__ in ('2025.6.0', '2026.10.2', '2026.10.4')
          and type(hass) is HomeAssistant and type(old) is str and type(new) is str
          and old.startswith('script.') and new.startswith('script.') and old != new,
          'own_id_script_scope_unsupported')
    component = hass.data.get(DATA_INSTANCES, {}).get('script')
    _need(type(component) is EntityComponent and component.hass is hass)
    registry, services = er.async_get(hass), hass.services
    _need(type(services) is ServiceRegistry and services._hass is hass
          and type(services._services) is dict)
    row = registry.async_get(old)
    _need(row is not None and row.entity_id == old and row.platform == 'script'
          and registry.async_get(new) is None and not row.disabled,
          'own_id_registry_scope_changed')
    scripts = tuple(component.entities)
    _need(all(type(entity) is ScriptEntity for entity in scripts))
    selected = [entity for entity in scripts if entity.registry_entry is row
                and entity.entity_id == old and entity.unique_id == row.unique_id]
    _need(len(selected) == 1, 'own_id_script_owner_missing')
    entity = selected[0]
    # The native _attr_ property owns __attr_unique_id; cached unique_id
    # and the lifecycle getter must identify that exact backing value.
    _script_attr_descriptor(entity, 'unique_id')
    _need(type(entity.__dict__) is dict and 'unique_id' in entity.__dict__
          and '__attr_unique_id' in entity.__dict__ and '_attr_unique_id' not in entity.__dict__
          and entity.__dict__['unique_id'] is entity.__dict__['__attr_unique_id']
          and entity._attr_unique_id is entity.__dict__['__attr_unique_id']
          and entity.unique_id is entity.__dict__['unique_id'],
          'own_id_script_unique_cache_unqualified')
    script_services = services._services.get('script')
    _need(type(script_services) is dict and type(entity.unique_id) is str)
    service = script_services.get(entity.unique_id)
    target = getattr(getattr(service, 'job', None), 'target', None)
    _need(type(service) is Service and type(service.job) is HassJob
          and service.schema is SCRIPT_SERVICE_SCHEMA and service.schema.schema is dict
          and service.schema.required is False and service.supports_response is SupportsResponse.OPTIONAL
          and getattr(service, 'description_placeholders', None) is None
          and getattr(target, '__self__', None) is entity
          and getattr(target, '__func__', None) is ScriptEntity._service_handler,
          'own_id_script_service_owner_changed')
    new_service = new.split('.', 1)[1]
    _need(new_service == entity.unique_id or new_service not in script_services,
          'own_id_destination_service_occupied')
    if new_service != entity.unique_id and not stable_service:
        raw = entity.raw_config
        _need(type(raw) in (dict, NodeDictClass), 'own_id_script_name_unqualified')
        alias = raw.get('alias')
        if alias is None and 'alias' not in raw:
            # Startup derives this name from the Script key and updates the
            # native original_name field. The move must bind all three native
            # runtime name owners and that exact registry field together.
            from homeassistant.helpers.script import Script
            _script_attr_descriptor(entity, 'name')
            _need(type(entity.script) is Script and type(entity.script).__setattr__ is object.__setattr__
                  and type(entity.script.__dict__) is dict
                  and entity.script.__dict__.get('name') == entity.unique_id
                  and '_attr_name' not in entity.__dict__
                  and entity._attr_name is entity.__dict__.get('__attr_name')
                  and entity._attr_name == entity.unique_id
                  and entity.__dict__.get('name') == entity.unique_id
                  and row.original_name == entity.unique_id,
                  'own_id_script_default_name_unqualified')
        else:
            _need(type(alias) in (str, NodeStrClass) and bool(alias)
                  and str(alias) == entity.script.name and entity._attr_name == entity.script.name,
                  'own_id_script_name_unqualified')

    actors = {}
    for domain in ('automation', 'script'):
        owner = hass.data.get(DATA_INSTANCES, {}).get(domain)
        for actor in owner.entities if owner is not None else ():
            key = (domain, actor.unique_id)
            _need(type(key[1]) is str and key not in actors, 'own_id_actor_inventory_ambiguous')
            actors[key] = actor
    if new_service != entity.unique_id and not stable_service:
        # Native Script service relocation emits service_removed/registered.
        # Enabled automations reacting to either event would run an action due
        # to the rename, so this profile must reject them before any write.
        # Static unrelated state/event triggers remain admitted; opaque native
        # trigger forms do not get guessed as unrelated.
        for (domain, _), actor in actors.items():
            if domain != 'automation' or not actor._is_enabled:
                continue
            _need(type(actor._trigger_config) in (list, NodeListClass),
                  'own_id_service_event_trigger_unqualified')
            for trigger in actor._trigger_config:
                _need(type(trigger) in (dict, NodeDictClass), 'own_id_service_event_trigger_unqualified')
                platform = trigger.get('platform')
                _need(platform in ('state', 'event'), 'own_id_service_event_trigger_unsupported')
                if platform == 'event':
                    value = trigger.get('event_type')
                    kinds = value if type(value) in (list, NodeListClass) else (value,)
                    _need(bool(kinds) and all(type(item) in (str, NodeStrClass)
                          or type(item) is Template and item.is_static for item in kinds),
                          'own_id_service_event_trigger_unsupported')
                    names = tuple(item.template if type(item) is Template else str(item) for item in kinds)
                    _need(not set(names) & {'*', 'service_registered', 'service_removed',
                                             'entity_registry_updated'},
                          'own_id_service_event_trigger_unsupported')
    direct_by_service = {}
    for script_entity in scripts:
        script_service = script_services.get(script_entity.unique_id)
        job_target = getattr(getattr(script_service, 'job', None), 'target', None)
        _need(type(script_service) is Service and type(script_service.job) is HassJob
              and script_service.schema is SCRIPT_SERVICE_SCHEMA and script_service.schema.schema is dict
              and script_service.schema.required is False
              and script_service.supports_response is SupportsResponse.OPTIONAL
              and getattr(script_service, 'description_placeholders', None) is None
              and getattr(job_target, '__self__', None) is script_entity
              and getattr(job_target, '__func__', None) is ScriptEntity._service_handler,
              'own_id_script_service_owner_changed')
        direct_by_service['script.' + script_entity.unique_id] = script_entity

    budget, calls, automation_service = [100_000], [], []
    def scan(value, actor_key, path=(), ancestry=()):
        budget[0] -= 1
        _need(budget[0] >= 0 and len(ancestry) <= 64, 'own_id_caller_graph_limit')
        if id(value) in ancestry:
            raise Refused('own_id_caller_cycle') from None
        ancestry += (id(value),)
        if type(value) in (list, NodeListClass):
            for index, item in enumerate(value):
                scan(item, actor_key, (*path, index), ancestry)
            return
        if type(value) not in (dict, NodeDictClass):
            return
        action = value.get('action', value.get('service'))
        if action is not None:
            action = _literal(action)
            if action in direct_by_service:
                if direct_by_service[action] is entity:
                    calls.append((actor_key[0], actor_key[1], 'direct', action, path))
            elif action == 'automation.trigger':
                target, data = value.get('target', {}), value.get('data', {})
                _need(type(target) in (dict, NodeDictClass) and type(data) in (dict, NodeDictClass)
                      and not set(target) - {'entity_id'} and not (
                          'entity_id' in target and 'entity_id' in data),
                      'own_id_opaque_caller_unsupported')
                target_ids = _ids(target.get('entity_id', data.get('entity_id')))
                _need(bool(target_ids) and all(item.startswith('automation.') for item in target_ids),
                      'own_id_opaque_caller_unsupported')
                if not automation_service:
                    from .native_own_automation_service import capture
                    automation_service.append(capture(hass))
                for item in target_ids:
                    matching = [candidate for (domain, _), candidate in actors.items()
                                if domain == 'automation' and candidate.entity_id == item]
                    _need(len(matching) == 1, 'own_id_automation_target_unqualified')
                    calls.append((actor_key[0], actor_key[1], 'automation', item, path))
            elif action == 'script.turn_on':
                target, data = value.get('target', {}), value.get('data', {})
                _need(type(target) in (dict, NodeDictClass) and type(data) in (dict, NodeDictClass)
                      and not set(target) - {'entity_id'}, 'own_id_opaque_caller_unsupported')
                target_ids = _ids(target.get('entity_id', data.get('entity_id')))
                _need(bool(target_ids) and all(item.startswith('script.') for item in target_ids),
                      'own_id_opaque_caller_unsupported')
                if old in target_ids:
                    calls.append((actor_key[0], actor_key[1], 'entity', old, path))
            else:
                # System, dynamic, unowned and custom actions are opaque:
                # no inference that an external caller cannot reach Script.
                _need(False, 'own_id_opaque_caller_unsupported')
        for key, child in value.items():
            if key not in ('variables', 'event_data', 'event_data_template', 'data', 'target'):
                scan(child, actor_key, (*path, str(key)), ancestry)

    rows, caches = [], []
    native = NativeYAMLRuntimeReadback(hass)
    for domain in ('automation', 'script'):
        owner = hass.data.get(DATA_INSTANCES, {}).get(domain)
        if owner is None:
            rows.append((domain, None, None))
            continue
        try:
            projection, identities = native._projection(domain)
            rows.append((domain, _normal(projection), identities))
        except Exception as error:
            raise Refused('own_id_native_inventory_unavailable') from error
    for key, actor in sorted(actors.items()):
        script = actor.action_script if key[0] == 'automation' else actor.script
        _need(type(script._runs) is list, 'own_id_script_run_inventory_changed')
        scan(script.sequence, key)
        caches.append((key, id(actor), id(script), _graph(script.sequence),
            tuple((name, _graph(getattr(script, name), include_scripts=True)) for name in _cache_names()),
            tuple((id(run), id(run._context), id(run._variables), id(run._stop)) for run in script._runs)))
    calls.sort(key=lambda item: repr(item))
    # Sample both logical projections and exact physical object edges. The
    # target identity stays a registry UUID, not a guessed script service key.
    signature = (id(hass), id(component), id(registry), id(row), row.id, old, new,
                 id(entity), id(entity.script), entity.unique_id, id(service), id(service.job),
                 id(service.job.target), id(service.schema), service.supports_response,
                 id(script_services), _normal(rows), tuple(caches), tuple(calls),
                 tuple(id(item) for item in automation_service[0][:9]) if automation_service else ())
    return entity, service, tuple(call[:4] for call in calls), signature


@dataclass(frozen=True, eq=False)
class NativeOwnIDInventory:
    hass: object
    old: str
    new: str
    selected_entity: object
    selected_script: object
    service: object
    service_registry: object
    service_domains: object
    service_table: object
    service_job: object
    service_target: object
    service_schema: object
    service_response: object
    service_key: str
    unique_storage: object
    caller_bus: object
    caller_listeners: object
    description_cache: object
    description: object
    description_name: object
    description_graph: object
    description_holds: object
    service_event_census: object
    automation_service: object
    callers: tuple
    signature: tuple
    task: object
    stable_service: bool
    default_name: bool
    script_storage: object
    script_name: object
    entity_attr_name: object
    entity_cached_name: object
    unique_attr_descriptor: object
    name_attr_descriptor: object

    @classmethod
    def capture(cls, hass, mapping, *, stable_change=None, moving_change=None):
        _need(type(mapping) is dict and len(mapping) == 1 and asyncio.current_task() is not None)
        old, new = next(iter(mapping.items()))
        _need(stable_change is None or moving_change is None, 'own_id_service_proof_ambiguous')
        stable = False
        if moving_change is not None:
            from homeassistant.helpers import entity_registry as er
            from .native_registry import PreparedRegistryChange
            from .native_registry_transition import _native_script_unique_move
            _need(type(moving_change) is PreparedRegistryChange
                  and _native_script_unique_move(moving_change.before, moving_change.after),
                  'own_id_moving_service_preparation_unqualified')
            before, after = moving_change.before, moving_change.after
            row = er.async_get(hass).async_get(old)
            _need(row is not None and row.id == moving_change.identity
                  and (old, new) in ((before['entity_id'], after['entity_id']),
                                     (after['entity_id'], before['entity_id']))
                  and row.unique_id == (before['unique_id'] if old == before['entity_id'] else after['unique_id']),
                  'own_id_moving_service_preparation_unqualified')
        if stable_change is not None:
            # A forward alias→service and its inverse are the SAME original
            # registry change. Neither direction moves the native service key.
            # This structural proof is a census input, never write authority.
            from homeassistant.helpers import entity_registry as er
            from homeassistant.helpers.json import _orjson_bytes_default_encoder
            from .native_registry import PreparedRegistryChange, _content
            _need(type(stable_change) is PreparedRegistryChange and stable_change.kind == 'entity',
                  'own_id_stable_service_preparation_unqualified')
            before, after = stable_change.before, stable_change.after
            row = er.async_get(hass).async_get(old)
            _need(row is not None and row.id == stable_change.identity and row.platform == 'script'
                  and before.get('platform') == after.get('platform') == 'script'
                  and before.get('id') == after.get('id') == row.id
                  and type(before.get('unique_id')) is str
                  and before.get('unique_id') == after.get('unique_id') == row.unique_id
                  and after.get('entity_id') == 'script.' + row.unique_id
                  and (old, new) in ((before.get('entity_id'), after.get('entity_id')),
                                     (after.get('entity_id'), before.get('entity_id')))
                  and {k: v for k, v in _content(before).items() if k != 'entity_id'}
                      == {k: v for k, v in _content(after).items() if k != 'entity_id'}
                  and _content(json.loads(_orjson_bytes_default_encoder(row.as_storage_fragment)))
                      == _content(before if old == before['entity_id'] else after),
                  'own_id_stable_service_preparation_unqualified')
            stable = True
        entity, service, callers, signature = _sample(hass, old, new, stable_service=stable)
        description_cache, description, description_graph, description_holds = _service_description(hass, entity)
        automation_service = None
        if any(call[2] == 'automation' for call in callers):
            from .native_own_automation_service import capture
            automation_service = capture(hass)
        from homeassistant.core import EventBus
        _need(type(hass.bus) is EventBus and type(hass.bus._listeners) is defaultdict,
              'own_id_service_caller_bus_unqualified')
        caller_bus, caller_listeners = hass.bus, hass.bus._listeners
        event_census = (_ServiceEventCensus(hass, service, old, new)
                        if new.split('.', 1)[1] != entity.unique_id and not stable else None)
        if event_census is None:
            _foreign_static_callers(hass, caller_listeners, frozenset((old, new)))
        else:
            _need(('script', new.split('.', 1)[1]) not in description_cache
                  and sum(value is description for value in description_cache.values()) == 1,
                  'own_id_script_description_foreign_alias')
        default_name = (not stable and new.split('.', 1)[1] != entity.unique_id
                        and 'alias' not in entity.raw_config)
        result = cls(hass, old, new, entity, entity.script, service, hass.services,
                     hass.services._services, hass.services._services['script'], service.job,
                     service.job.target, service.schema, service.supports_response,
                     entity.unique_id, entity.__dict__, caller_bus, caller_listeners,
                     description_cache, description, description['name'], description_graph, description_holds,
                     event_census, automation_service, callers, signature, asyncio.current_task(), stable,
                     default_name, entity.script.__dict__, entity.script.name,
                     entity.__dict__.get('__attr_name'), entity.__dict__.get('name'),
                     _script_attr_descriptor(entity, 'unique_id'),
                     _script_attr_descriptor(entity, 'name'))
        result.check_service()
        return result

    def check_service(self, *, installed_row=None):
        """Recheck service ownership at the old or owning registry-event edge."""
        from homeassistant.components.script import ScriptEntity, SCRIPT_SERVICE_SCHEMA
        from homeassistant.core import HassJob, Service, ServiceRegistry, SupportsResponse
        from homeassistant.helpers import service as service_helper
        from .native_script_config_transport import _graph
        if self.automation_service is not None:
            from .native_own_automation_service import check
            check(self.hass, self.automation_service)
        services = self.hass.services
        domains = services._services if type(services) is ServiceRegistry else None
        table = domains.get('script') if type(domains) is dict else None
        service = table.get(self.service_key) if type(table) is dict else None
        _need(services is self.service_registry and domains is self.service_domains
              and table is self.service_table
              and type(service) is Service and service is self.service
              and sum(item is service for table in domains.values()
                      if type(table) is dict for item in table.values()) == 1
              and type(self.selected_entity.__dict__) is dict
              and self.selected_entity.__dict__ is self.unique_storage
              and self.selected_entity.__dict__.get('unique_id') is self.service_key
              and '_attr_unique_id' not in self.unique_storage
              and self.unique_storage.get('__attr_unique_id') is self.service_key
              and _script_attr_descriptor(self.selected_entity, 'unique_id') is self.unique_attr_descriptor
              and self.selected_entity._attr_unique_id is self.service_key
              and self.selected_entity.unique_id is self.service_key
              and type(service.job) is HassJob and service.job is self.service_job
              and service.job.target is self.service_target
              and getattr(service.job.target, '__self__', None) is self.selected_entity
              and getattr(service.job.target, '__func__', None) is ScriptEntity._service_handler
              and service.schema is self.service_schema and service.schema is SCRIPT_SERVICE_SCHEMA
              and service.schema.schema is dict and service.schema.required is False
              and service.supports_response is self.service_response
              and service.supports_response is SupportsResponse.OPTIONAL
              and getattr(service, 'description_placeholders', None) is None,
              'own_id_script_service_owner_changed')
        if self.service_event_census is not None:
            _script_storage_owners(self.hass, self.selected_entity, self.selected_script,
                                   include_name=self.default_name)
        if self.default_name:
            from homeassistant.helpers.script import Script
            _need(type(self.selected_script) is Script and type(self.selected_script).__setattr__ is object.__setattr__
                  and self.selected_script.__dict__ is self.script_storage
                  and self.script_storage.get('name') is self.script_name
                  and '_attr_name' not in self.unique_storage
                  and _script_attr_descriptor(self.selected_entity, 'name') is self.name_attr_descriptor
                  and self.unique_storage.get('__attr_name') is self.entity_attr_name
                  and self.selected_entity._attr_name is self.entity_attr_name
                  and self.selected_entity.__dict__.get('name') is self.entity_cached_name
                  and self.script_name == self.entity_attr_name == self.entity_cached_name == self.service_key
                  and (self.selected_entity.registry_entry.original_name == self.service_key
                       if installed_row is None else
                       self.selected_entity.registry_entry is installed_row
                       and installed_row.id == self.signature[4]
                       and installed_row.entity_id == self.new
                       and installed_row.original_name == self.new.split('.', 1)[1]),
                  'own_id_script_default_name_changed')
        _need(self.hass.data.get(service_helper.SERVICE_DESCRIPTION_CACHE) is self.description_cache
              and self.description_cache.get(('script', self.service_key)) is self.description
              and _graph(self.description) == self.description_graph
              and self.description.get('name') is self.description_name
              and self.description.get('description') is self.selected_entity.description
              and self.description.get('fields') is self.selected_entity.fields,
              'own_id_script_description_changed')
        _need(self.hass.bus is self.caller_bus and self.caller_bus._listeners is self.caller_listeners,
              'own_id_service_caller_bus_changed')
        if self.service_event_census is None:
            _foreign_static_callers(self.hass, self.caller_listeners, frozenset((self.old, self.new)))
        else:
            _need(('script', self.new.split('.', 1)[1]) not in self.description_cache
                  and sum(value is self.description for value in self.description_cache.values()) == 1,
                  'own_id_script_description_foreign_alias')
            self.service_event_census.check()

    def check(self):
        _need(asyncio.current_task() is self.task, 'own_id_inventory_task_changed')
        self.check_service()
        entity, service, callers, signature = _sample(
            self.hass, self.old, self.new, stable_service=self.stable_service)
        _need(entity is self.selected_entity and entity.script is self.selected_script
              and service is self.service and service.job is self.service_job
              and service.job.target is self.service_target and service.schema is self.service_schema
              and service.supports_response is self.service_response
              and callers == self.callers and signature == self.signature,
              'own_id_inventory_changed')
