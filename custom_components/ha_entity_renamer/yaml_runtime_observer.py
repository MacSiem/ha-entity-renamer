"""Read-only, version-pinned native YAML runtime proof.

No user action/script/template is executed, no listener is installed, no scene
is activated and no reload is initiated. Enabled automations deliberately remain
UNKNOWN: a detach callback alone cannot prove the expected trigger listeners.
Whole inventory, dependent-consumer and recorder continuity are a separate
trusted independent verifier's responsibility; absence of that verifier fails.
"""
from __future__ import annotations

import asyncio
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


class NativeYAMLRuntimeReadback:
    """Implementation of yaml_transaction.NativeYAMLRuntimeObserver.

    Use only with trusted PreparedYAML decoded by the native preparation store.
    Old receipts lacking runtime expectations, unsupported native structures,
    active scripts, enabled automations and mixed runtime versions fail closed.
    """

    def __init__(self, hass, *, inventory_verifier: IndependentYAMLInventoryVerifier | None = None):
        if inventory_verifier is not None and not callable(
                getattr(inventory_verifier, 'verify_inventory', None)):
            raise ValueError('independent_inventory_verifier_required')
        self.hass = hass
        self.inventory_verifier = inventory_verifier

    def _projection(self, domain):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
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
                if (script.is_running or script._unloaded or not script.enabled
                        or script._hass is not self.hass or script.domain != domain
                        or entity._blueprint_inputs is not None
                        or not self.hass.services.has_service('script', entity.unique_id)
                        or getattr(script.change_listener, '__self__', None) is not entity):
                    raise ValueError('native_script_not_settled')
                rows.append([entity.unique_id, entity.raw_config, script.sequence,
                    script.script_mode, script.max_runs, script._max_exceeded, script.variables,
                    entity.icon, entity.description, entity.fields, entity._trace_config])
            elif domain == 'automation':
                from homeassistant.components.automation import AutomationEntity
                if type(entity) is not AutomationEntity:
                    raise ValueError('native_automation_type')
                script = entity.action_script
                if (entity._is_enabled is not False or entity._async_detach_triggers is not None
                        or entity._condition is not None or entity._trigger_variables is not None
                        or entity._blueprint_inputs is not None
                        or script.is_running or script._unloaded
                        or script._hass is not self.hass or script.domain != domain
                        or getattr(script.change_listener, '__self__', None) is not entity):
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
        if (__version__ not in ('2025.6.0', '2026.10.2') or __version__ != receipt.version
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
