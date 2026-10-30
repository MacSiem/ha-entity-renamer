"""Read-only, version-pinned native YAML runtime proof.

No user action/script/template is executed, no listener is installed, no scene
is activated and no reload is initiated. Enabled automations require both the
native detach chain and independently present event/state tracker registrations.
Whole inventory, dependent-consumer and recorder continuity are a separate
trusted independent verifier's responsibility; absence of that verifier fails.
"""
from __future__ import annotations

import asyncio
from functools import partial
from importlib import import_module
from types import CodeType, FunctionType
from pathlib import Path
from typing import Protocol

from .transaction import Position
from .yaml_validation_worker import _fingerprint


class IndependentYAMLInventoryVerifier(Protocol):
    """Server-owned verifier of declared inventory AND historical continuity.

    The composition root must supply an actual independent implementation.
    A reload ACK, this observer's position, or a constant callback is not one.
    """

    async def verify_inventory(self, plan, *, rollback: bool) -> bool: ...


def _pending_native_work(hass, domains):
    """Inspect native coroutine chains, including service jobs queued to reload.

    No blocking global task drain is used (native editor locks may be held).
    Pending native component/platform additions, removals and trigger attachment
    are barriers even when a reload service has already returned normally.
    """
    from homeassistant.core import ServiceCall
    current = asyncio.current_task()
    prefixes = tuple('homeassistant.components.' + domain for domain in domains)
    for task in asyncio.all_tasks(hass.loop):
        if task is current or task.done():
            continue
        node = task.get_coro()
        seen = set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            frame = getattr(node, 'cr_frame', None) or getattr(node, 'gi_frame', None)
            if frame is not None:
                module = frame.f_globals.get('__name__', '')
                name = frame.f_code.co_name
                if module.startswith('homeassistant.'):
                    for value in frame.f_locals.values():
                        if (isinstance(value, ServiceCall) and value.domain in domains
                                and value.service in ('reload', 'reload_core_config')):
                            return True
                    if (module.startswith(prefixes)
                            and any(word in name for word in ('reload', 'process_config',
                                                              'attach_triggers', 'added_to_hass'))):
                        return True
                    if (module in ('homeassistant.helpers.entity_platform',
                                   'homeassistant.helpers.entity_component')
                            and any(word in name for word in ('reset', 'add_entit',
                                                              'remove_entit', 'setup_platform'))):
                        # Generic native platform work can affect dependent consumers.
                        return True
                    if (module == 'homeassistant.core_config'
                            and 'process' in name):
                        return True
            node = (getattr(node, 'cr_await', None)
                    or getattr(node, 'gi_yieldfrom', None)
                    or getattr(node, 'ag_await', None))
    return False


def _platforms_settled(component):
    platforms = getattr(component, '_platforms', None)
    if not isinstance(platforms, dict):
        return False
    for platform in platforms.values():
        tasks = getattr(platform, '_tasks', None)
        if not isinstance(tasks, list) or any(not task.done() for task in tasks):
            return False
    return True


def _closure(function, module, qualname):
    """Read a pinned native function's cells without invoking any callback."""
    if (type(function) is not FunctionType or function.__module__ != module
            or function.__qualname__ != qualname):
        raise ValueError('native_trigger_function_changed')
    root = getattr(import_module(module), qualname.split('.', 1)[0])
    native_codes = [value for value in root.__code__.co_consts
                    if type(value) is CodeType and value.co_qualname == qualname]
    if len(native_codes) != 1 or function.__code__ is not native_codes[0]:
        raise ValueError('native_trigger_code_changed')
    return dict(zip(function.__code__.co_freevars,
                    (cell.cell_contents for cell in (function.__closure__ or ()))))


def _registered_bus_remove(hass, remove, expected_event=None):
    """A native unsubscribe partial must name a tuple still on this exact bus."""
    from homeassistant.core import EventBus
    if (type(remove) is not partial or remove.keywords
            or getattr(remove.func, '__self__', None) is not hass.bus
            or getattr(remove.func, '__func__', None) is not EventBus._async_remove_listener
            or len(remove.args) != 2):
        raise ValueError('native_event_detach_changed')
    event_type, entry = remove.args
    if expected_event is not None and event_type != expected_event:
        raise ValueError('native_event_key_changed')
    entries = hass.bus._listeners.get(event_type, ())
    if sum(item is entry for item in entries) != 1:
        raise ValueError('native_event_registration_missing')
    return entry, (id(remove), id(entries), id(entry), id(entry[0]), id(entry[1]))


def _trigger_listener(hass, entity, listener, module, name, config, index):
    cells = _closure(listener, module, 'async_attach_trigger.<locals>.' + name)
    job = cells['job']
    action = job.target
    if (cells['hass'] is not hass
            or getattr(action, '__self__', None) is not entity
            or getattr(action, '__func__', None) is not type(entity)._async_trigger_if_enabled
            or cells['platform_type'] != config['platform']
            or cells['trigger_data'] != {'id': config.get('id', str(index)),
                                        'idx': str(index), 'alias': config.get('alias')}):
        raise ValueError('native_trigger_owner_changed')
    return cells, (id(listener), id(job), id(action.__self__), id(action.__func__))


def _automation_registrations(hass, entity):
    """Prove a narrow static native trigger graph; all other shapes fail closed.

    No callbacks, template renderers, matchers or unsubscribe functions are run.
    Tokens retain registration identity across the observer's two samples.
    """
    from homeassistant.helpers import event as event_helper
    from homeassistant.helpers import trigger as trigger_helper
    from homeassistant.helpers.template import Template
    config = entity._trigger_config
    detach = entity._async_detach_triggers
    cells = _closure(detach, trigger_helper.__name__,
                     'async_initialize_triggers.<locals>.remove_triggers')
    removes = cells['removes']
    if type(config) is not list or type(removes) is not list or not config or len(config) != len(removes):
        raise ValueError('native_trigger_count_changed')
    tokens = [id(detach), id(removes)]
    for index, (cfg, remove) in enumerate(zip(config, removes)):
        # Dynamic enabled/variables, disabled triggers, nested/new trigger types
        # and delay timers need separate proof and remain UNKNOWN.
        if cfg.get('enabled', True) is not True or 'variables' in cfg:
            raise ValueError('native_dynamic_trigger_not_covered')
        platform = cfg.get('platform')
        module = 'homeassistant.components.homeassistant.triggers.' + str(platform)
        if platform == 'event':
            if 'event_data' in cfg or 'context' in cfg:
                raise ValueError('native_event_filter_not_covered')
            types = cfg['event_type']
            if (type(types) is not list or not types
                    or any(type(value) is not Template or not value.is_static for value in types)):
                raise ValueError('native_dynamic_event_not_covered')
            event_types = [value.template for value in types]
            remove_cells = _closure(remove, module,
                                   'async_attach_trigger.<locals>.remove_listen_events')
            event_removes = remove_cells['removes']
            if type(event_removes) is not list or len(event_removes) != len(event_types):
                raise ValueError('native_event_count_changed')
            targets = []
            for event_type, event_remove in zip(event_types, event_removes):
                (job, event_filter), token = _registered_bus_remove(hass, event_remove, event_type)
                listener_cells, listener_token = _trigger_listener(
                    hass, entity, job.target, module, 'handle_event', cfg, index)
                if (event_filter is not None or listener_cells['event_context_items'] is not None
                        or listener_cells['event_context_schema'] is not None):
                    raise ValueError('native_event_filter_changed')
                targets.append(id(job.target))
                tokens.append((token, listener_token))
            if len(set(targets)) != 1:
                raise ValueError('native_event_listener_changed')
            listener = job.target
            for registered_type, members in hass.bus._listeners.items():
                count = sum(member_job.target is listener for member_job, _ in members)
                if count != event_types.count(registered_type):
                    raise ValueError('native_event_extra_registration')
        elif platform == 'state':
            if 'for' in cfg:
                raise ValueError('native_state_duration_not_covered')
            remove_cells = _closure(remove, module, 'async_attach_trigger.<locals>.async_remove')
            unsub = remove_cells['unsub']
            if (type(unsub) is not partial or unsub.func is not event_helper._remove_listener
                    or unsub.keywords or len(unsub.args) != 5 or remove_cells['unsub_track_same']):
                raise ValueError('native_state_detach_changed')
            owner_hass, tracker, keys, job, callbacks = unsub.args
            expected_keys = cfg['entity_id']
            if (owner_hass is not hass or tracker is not event_helper._KEYED_TRACK_STATE_CHANGE
                    or not expected_keys or tuple(keys) != tuple(expected_keys)):
                raise ValueError('native_state_keys_changed')
            data = hass.data.get(tracker.key)
            if data is None or data.callbacks is not callbacks:
                raise ValueError('native_state_registry_missing')
            (bus_job, event_filter), bus_token = _registered_bus_remove(hass, data.listener, tracker.event_type)
            for function, expected in ((bus_job.target, tracker.dispatcher_callable),
                                       (event_filter, tracker.filter_callable)):
                if (type(function) is not partial or function.func is not expected
                        or function.keywords or len(function.args) != 2
                        or function.args[0] is not hass or function.args[1] is not callbacks):
                    raise ValueError('native_state_dispatch_changed')
            listener_cells, listener_token = _trigger_listener(
                hass, entity, job.target, module, 'state_automation_listener', cfg, index)
            if (listener_cells['config'] is not cfg or listener_cells['time_delta'] is not None
                    or listener_cells['attribute'] != cfg.get('attribute')
                    or listener_cells['match_all'] != all(key not in cfg for key in ('from', 'not_from', 'to', 'not_to'))):
                raise ValueError('native_state_config_changed')
            for side in ('from', 'to'):
                invert = False
                parameter = cfg.get(side)
                if parameter is None:
                    parameter = cfg.get('not_' + side)
                    invert = parameter is not None
                if parameter is None:
                    parameter = '*'
                expected = event_helper.process_state_match(parameter, invert=invert)
                actual = listener_cells['match_' + side + '_state']
                if (type(actual) is not FunctionType or actual.__code__ is not expected.__code__
                        or tuple(cell.cell_contents for cell in (actual.__closure__ or ()))
                        != tuple(cell.cell_contents for cell in (expected.__closure__ or ()))):
                    raise ValueError('native_state_match_changed')
            key_tokens = []
            for key in expected_keys:
                members = callbacks.get(key, ())
                if sum(member is job for member in members) != expected_keys.count(key):
                    raise ValueError('native_state_registration_missing')
                key_tokens.append((key, id(members)))
            # The same job under an unexpected key is also a changed registration.
            if any(key not in expected_keys and any(member is job for member in members)
                   for key, members in callbacks.items()):
                raise ValueError('native_state_extra_key')
            tokens.append((id(remove), id(unsub), id(data), id(callbacks), id(job),
                           tuple(key_tokens), bus_token, listener_token))
        else:
            raise ValueError('native_trigger_platform_not_covered')
    return tuple(tokens)


class NativeYAMLRuntimeReadback:
    """Implementation of yaml_transaction.NativeYAMLRuntimeObserver.

    Use only with trusted PreparedYAML decoded by the native preparation store.
    Old receipts lacking runtime expectations, unsupported native structures,
    active scripts, unsupported triggers and mixed runtime versions fail closed.
    """

    def __init__(self, hass, *, inventory_verifier: IndependentYAMLInventoryVerifier | None = None):
        if inventory_verifier is not None and not callable(
                getattr(inventory_verifier, 'verify_inventory', None)):
            raise ValueError('independent_inventory_verifier_required')
        self.hass = hass
        self.inventory_verifier = inventory_verifier

    def _projection(self, domain):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.const import __version__
        if domain == 'homeassistant':
            from homeassistant.core_config import DATA_CUSTOMIZE
            from homeassistant.helpers.entity_values import EntityValues
            values = self.hass.data.get(DATA_CUSTOMIZE)
            if type(values) is not EntityValues:
                raise ValueError('native_customize_missing')
            return [values._exact, values._domain,
                [(pattern.pattern, attrs) for pattern, attrs in (values._glob or {}).items()]], (id(values),)
        component = self.hass.data.get(DATA_INSTANCES, {}).get(domain)
        if component is None or not _platforms_settled(component):
            raise ValueError('native_component_unsettled')
        rows, identities = [], []
        if domain == 'scene':
            from homeassistant.components.homeassistant.scene import DATA_PLATFORM, HomeAssistantScene
            platform = self.hass.data.get(DATA_PLATFORM)
            if platform is None or any(not task.done() for task in platform._tasks):
                raise ValueError('native_scene_platform_unsettled')
            entities = tuple(platform.entities.values())
        else:
            entities = tuple(component.entities)
        for entity in entities:
            if entity.hass is not self.hass or self.hass.states.get(entity.entity_id) is None:
                raise ValueError('native_entity_missing')
            identities.append((entity.entity_id, id(entity)))
            if domain == 'script':
                from homeassistant.components.script import ScriptEntity
                if type(entity) is not ScriptEntity:
                    raise ValueError('native_script_type')
                script = entity.script
                if (script.is_running or (script._unloaded if __version__ in ('2026.10.2', '2026.10.4') else False) or not script.enabled
                        or script._hass is not self.hass or script.domain != domain
                        or entity._blueprint_inputs is not None
                        or not self.hass.services.has_service('script', entity.unique_id)
                        or getattr(script.change_listener, '__self__', None) is not entity):
                    raise ValueError('native_script_not_settled')
                from homeassistant.core import Service, ServiceRegistry, HassJob
                from homeassistant.helpers.script import Script
                services = self.hass.services
                if type(services) is not ServiceRegistry or services._hass is not self.hass:
                    raise ValueError('native_script_service_registry')
                service = services._services.get('script', {}).get(entity.unique_id)
                job = getattr(service, 'job', None)
                target = getattr(job, 'target', None)
                if (type(script) is not Script or type(service) is not Service
                        or type(job) is not HassJob
                        or getattr(target, '__self__', None) is not entity
                        or getattr(target, '__func__', None) is not ScriptEntity._service_handler
                        or getattr(script.change_listener, '__func__', None)
                            is not ScriptEntity.async_change_listener):
                    raise ValueError('native_script_service_owner')
                identities[-1] = (entity.entity_id, id(entity), id(script),
                    id(entity.raw_config), id(entity.registry_entry),
                    id(services), id(service), id(job), id(target), id(service.schema))
                rows.append([entity.unique_id, entity.raw_config, script.sequence,
                    script.script_mode, script.max_runs, script._max_exceeded, script.variables,
                    entity.icon, entity.description, entity.fields, entity._trace_config])
            elif domain == 'automation':
                from homeassistant.components.automation import AutomationEntity
                if type(entity) is not AutomationEntity:
                    raise ValueError('native_automation_type')
                script = entity.action_script
                if ((entity._condition if __version__ in ('2026.10.2', '2026.10.4') else entity._cond_func) is not None or entity._trigger_variables is not None
                        or entity._blueprint_inputs is not None
                        or (script._unloaded if __version__ in ('2026.10.2', '2026.10.4') else False)
                        or script._hass is not self.hass or script.domain != domain
                        or getattr(script.change_listener, '__self__', None) is not entity
                        or getattr(script.change_listener, '__func__', None)
                            is not AutomationEntity.async_write_ha_state):
                    raise ValueError('automation_listener_proof_unavailable')
                if entity._is_enabled is True:
                    identities[-1] = (entity.entity_id, id(entity), id(script),
                        id(entity.raw_config), id(entity.registry_entry),
                        _automation_registrations(self.hass, entity))
                elif entity._is_enabled is False and entity._async_detach_triggers is None:
                    identities[-1] = (entity.entity_id, id(entity), id(script),
                        id(entity.raw_config), id(entity.registry_entry), ('disabled',))
                else:
                    raise ValueError('automation_listener_proof_unavailable')
                rows.append([entity.unique_id, entity.raw_config, entity._trigger_config,
                    script.sequence, script.script_mode, script.max_runs, script._max_exceeded,
                    entity._variables, entity._trace_config])
            elif domain == 'scene':
                if type(entity) is not HomeAssistantScene or entity.from_service:
                    raise ValueError('dynamic_scene_not_covered')
                cfg = entity.scene_config
                rows.append([cfg.id, cfg.name, cfg.icon, cfg.states])
            else:
                raise ValueError('native_domain_not_covered')
        if any(not row[0] for row in rows) or len({row[0] for row in rows}) != len(rows):
            raise ValueError('native_identity_ambiguous')
        rows.sort(key=lambda row: row[0])
        return rows, tuple(sorted(identities))

    def _sample(self, change):
        from homeassistant.const import __version__
        receipt = change.receipt
        if (__version__ not in ('2025.6.0', '2026.10.2', '2026.10.4') or __version__ != receipt.version
                or str(Path(self.hass.config.config_dir).resolve()) != receipt.config_root):
            return Position.UNKNOWN, ()
        expected = {row[0]: row[1:] for row in receipt.runtime_expected}
        domains = receipt.changed_domains
        if not domains or set(expected) != set(domains) or _pending_native_work(self.hass, domains):
            return Position.UNKNOWN, ()
        before, after, tokens = True, True, []
        for domain in domains:
            old, new = expected[domain]
            if old is None or new is None:
                return Position.UNKNOWN, ()
            if domain == 'automation':
                # Configuration/registration inventory remains readable while
                # an action runs. This generic reload observer still requires
                # ALL automation actions idle; only the separate selective
                # lifecycle can preserve an unrelated running instance.
                from homeassistant.helpers.entity_component import DATA_INSTANCES
                component = self.hass.data.get(DATA_INSTANCES, {}).get(domain)
                if component is not None and any(entity.action_script.is_running
                                                  for entity in component.entities):
                    return Position.UNKNOWN, ()
            projection, identity = self._projection(domain)
            actual = _fingerprint(projection)
            before = before and actual == old
            after = after and actual == new
            tokens.append((domain, actual, identity))
        # Identical before/after runtime projections cannot resolve position.
        if before == after:
            return Position.UNKNOWN, ()
        return (Position.BEFORE if before else Position.AFTER), tuple(tokens)

    async def position(self, change):
        try:
            first = self._sample(change)
            if first[0] is Position.UNKNOWN:
                return Position.UNKNOWN
            # Let already-queued native entity callbacks run once. This does not
            # drain the loop or execute user actions on behalf of the observer.
            await asyncio.sleep(0)
            second = self._sample(change)
            return first[0] if first == second else Position.UNKNOWN
        except Exception:
            return Position.UNKNOWN

    async def verify_inventory(self, plan, *, rollback: bool) -> bool:
        verifier = self.inventory_verifier
        if verifier is None:
            return False
        try:
            return (await verifier.verify_inventory(plan, rollback=rollback)) is True
        except Exception:
            return False
