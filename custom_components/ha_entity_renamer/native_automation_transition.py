"""Private native Automation staging; no journal or mutation authority.

The outer operation must persist intent, fence sources and independently accept
runtime observations around these effects. Staging forces only newly constructed
native instances inactive; it does not edit YAML/raw_config or live callbacks.
This is not a continuation mechanism for running actions or pending triggers.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from types import CodeType

from .transaction import Refused


def _need(value):
    if not value:
        raise Refused('native_automation_transition_unconfirmed') from None


def _codes(code):
    yield code
    for value in code.co_consts:
        if type(value) is CodeType:
            yield from _codes(value)


class NativeAutomationTransition:
    """Live native capability; callers supply no constructor/config callbacks."""
    def __init__(self, hass, *, targets=None, allow_running=False):
        import homeassistant.components.automation as native
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers.entity_component import EntityComponent
        _need(type(hass) is HomeAssistant and __version__ in ('2025.6.0', '2026.10.2', '2026.10.4', '2026.11.1'))
        self.core111 = __version__ == '2026.11.1'
        _need(not self.core111 or allow_running is True and targets is None)
        factory = native
        if self.core111:
            from homeassistant.components.automation import util as factory
        self.factory = factory
        component = hass.data.get('automation')
        _need(component is None or (type(component) is EntityComponent and component.hass is hass))
        self.hass, self.native, self.component = hass, native, component
        self.task, self.thread = asyncio.current_task(), threading.get_ident()
        self.original = self._entities()
        all_keys = {key for key, _ in self.original}
        _need(targets is None or (type(targets) is tuple
              and all(type(key) is str for key in targets) and len(set(targets)) == len(targets)
              and set(targets) <= all_keys))
        self.targets = tuple(sorted(all_keys if targets is None else targets))
        _need(type(allow_running) is bool)
        if not allow_running:
            _need(all(not entity.action_script.is_running and not entity.action_script.runs
                      for key, entity in self.original if key in self.targets))
        self.foreign = tuple(self._pin(key, entity) for key, entity in self.original if key not in self.targets)
        self.enabled = tuple((key, entity._is_enabled) for key, entity in self.original if key in self.targets)
        _need(all(type(value) is bool for _, value in self.enabled))
        path = Path(native.__file__)
        self.source = path.read_bytes()
        self.path = path
        self.code = tuple(_codes(compile(self.source, str(path), 'exec', dont_inherit=True)))
        self.functions_refs = (
            (factory, '_prepare_automation_config'), (factory, '_create_automation_entities'),
            (factory, '_automation_name'),
            (EntityComponent, 'async_prepare_reload'),
            (native.AutomationEntity, '__init__'), (native.AutomationEntity, '_async_disable'),
            (native.AutomationEntity, '_async_enable'), (native.AutomationEntity, 'async_added_to_hass'),
            (native.AutomationEntity, 'async_will_remove_from_hass'))
        self.functions = tuple((owner, name, getattr(owner, name))
                               for owner, name in self.functions_refs)
        self.other_sources = tuple((path, path.read_bytes()) for path in sorted({
            Path(function.__code__.co_filename) for _, _, function in self.functions
        }) if path != self.path)
        self.other_code = tuple(code for path, source in self.other_sources
                                for code in _codes(compile(source, str(path), 'exec', dont_inherit=True)))
        if self.core111:
            from homeassistant.helpers.entity_platform import EntityPlatform
            from homeassistant.helpers.entity import Entity
            from .native_controlled_yaml_core111 import prove
            prove(self.functions_refs + ((EntityPlatform, 'async_add_entities'),
                                          (Entity, 'async_remove')))
            self.platform_add = EntityPlatform.async_add_entities
        self.check_native()

    async def prepare_preserving_enabled(self):
        """Qualify the entire live population before the first transaction effect."""
        from homeassistant.core import CoreState
        _need(self.component is not None and self.hass.state is CoreState.running)
        self.check_native()
        config = await self.component.async_prepare_reload(skip_reset=True)
        _need(config is not None)
        configs = await self.factory._prepare_automation_config(self.hass, config, None)
        rows = dict(self._entities())
        _need(len(configs) == len(rows) and all(
            item.validation_status is self.factory.ValidationStatus.OK
            and item.config_block.get('id') in rows and item.raw_blueprint_inputs is None
            and rows[item.config_block['id']].name == self.factory._automation_name(item)
            and rows[item.config_block['id']].raw_config == item.raw_config
            for item in configs) and len({item.config_block['id'] for item in configs}) == len(configs))
        self.check_native()
        self.state_conflict = False
        self.in_preserving_stage = False
        self.registry_readd_active = False
        self.replacing_ids = set()
        self.expected_enabled = dict(self.enabled)
        self.entity_ids = {entity.entity_id for _, entity in self.original}
        from homeassistant.core import callback
        @callback
        def observed(event):
            if event.data.get('entity_id') not in self.entity_ids:
                return
            old, new = event.data.get('old_state'), event.data.get('new_state')
            if (self.in_preserving_stage and event.data.get('entity_id') in self.replacing_ids
                    and old is not None and new is not None
                    and 'unavailable' in (old.state, new.state)):
                return
            if old is not None and new is not None and old.state != new.state:
                self.state_conflict = True
        self.remove_state_listener = self.hass.bus.async_listen('state_changed', observed)
        try:
            self.check_preserving_enabled()
        except BaseException:
            self.close_preserving_enabled()
            raise

    def check_preserving_enabled(self):
        if threading.get_ident() != self.thread:
            return
        self.check_native()
        if self.state_conflict:
            raise Refused('automation_external_state_changed') from None
        if not self.in_preserving_stage:
            rows = dict(self._entities())
            if not (set(rows) == set(self.expected_enabled) and all(
                    entity._is_enabled is self.expected_enabled[key] for key, entity in rows.items())):
                raise Refused('automation_enabled_population_changed') from None

    def begin_registry_readd(self, source_id, target_id, registry_id):
        """Protect the same native automation before its one registry rename."""
        self.check_preserving_enabled()
        _need(not self.in_preserving_stage and type(source_id) is str and type(target_id) is str
              and source_id != target_id and type(registry_id) is str)
        rows = [(key, entity) for key, entity in self._entities() if entity.entity_id == source_id]
        _need(len(rows) == 1)
        key, entity = rows[0]
        registry = entity.registry_entry
        _need(registry is not None and registry.id == registry_id
              and entity._is_enabled is self.expected_enabled[key]
              and (entity._initial_state is None or type(entity._initial_state) is bool))
        if self.core111:
            self.foreign = tuple(self._pin(other_key, other) for other_key, other in self._entities()
                                 if other_key != key)
        original = entity._initial_state
        self.in_preserving_stage = True
        self.registry_readd_active = True
        self.replacing_ids = {source_id, target_id}
        self.entity_ids.add(target_id)
        entity._initial_state = self.expected_enabled[key]
        return entity, key, original, source_id, target_id, registry_id

    async def finish_registry_readd(self, token):
        """Read actual re-add and conditionally restore only our temporary field."""
        _need(type(token) is tuple and len(token) == 6 and self.in_preserving_stage)
        entity, key, original, source_id, target_id, registry_id = token
        try:
            # Drain native state_changed callbacks before leaving this window.
            await asyncio.sleep(0)
            current = dict(self._entities()).get(key)
            registry = entity.registry_entry
            _need(current is entity and entity.entity_id == target_id
                  and registry is not None and registry.id == registry_id
                  and entity._initial_state is self.expected_enabled[key])
        finally:
            # Restore only the temporary value on the captured native object;
            # never replace a different field value or a different owner.
            try:
                registry = entity.registry_entry
                if (dict(self._entities()).get(key) is entity and registry is not None
                        and registry.id == registry_id and entity.entity_id in (source_id, target_id)
                        and entity._initial_state is self.expected_enabled[key]):
                    entity._initial_state = original
            except Exception:
                # Preserve the original failure; uncertain cleanup stays unproved.
                pass
            self.in_preserving_stage = False
            self.registry_readd_active = False
            self.replacing_ids.clear()
        self.check_preserving_enabled()

    async def stage_preserving_enabled(self):
        """Use native factory/add with live on/off before trigger attachment."""
        self.check_preserving_enabled()
        self.in_preserving_stage = True
        try:
            config = await self.component.async_prepare_reload(skip_reset=True)
            self.check_preserving_enabled()
            _need(config is not None)
            configs = await self.factory._prepare_automation_config(self.hass, config, None)
            self.check_preserving_enabled()
            current = dict(self._entities())
            _need(set(current) == set(self.expected_enabled) and len(configs) == len(current)
                  and all(item.validation_status is self.factory.ValidationStatus.OK
                    and item.config_block.get('id') in current and item.raw_blueprint_inputs is None
                    for item in configs)
                  and len({item.config_block['id'] for item in configs}) == len(configs)
                  and all(entity._is_enabled is self.expected_enabled[key] for key, entity in current.items()))
            changed = [item for item in configs if
                current[item.config_block['id']].raw_config != item.raw_config
                or current[item.config_block['id']].name != self.factory._automation_name(item)]
            if self.core111:
                selected = {item.config_block['id'] for item in changed}
                self.foreign = tuple(self._pin(key, entity) for key, entity in current.items()
                                     if key not in selected)
            self.replacing_ids = {current[item.config_block['id']].entity_id for item in changed}
            for item in changed:
                key = item.config_block['id']
                _need(current[key]._is_enabled is self.expected_enabled[key])
                await current[key].async_remove()
                self.check_preserving_enabled()
            staged = [replace(item, config_block={**item.config_block,
                'initial_state': self.expected_enabled[item.config_block['id']]}) for item in changed]
            entities = await self.factory._create_automation_entities(self.hass, staged)
            self.check_preserving_enabled()
            _need(len(entities) == len(changed) and all(
                type(entity) is self.native.AutomationEntity
                and entity._initial_state is self.expected_enabled[entity.unique_id] for entity in entities))
            await self.component.async_add_entities(entities)
            # HA schedules callback listeners on the event loop. Drain events
            # raised by this add while the replacement window is still known.
            await asyncio.sleep(0)
            self.check_preserving_enabled()
        finally:
            self.in_preserving_stage = False
            self.replacing_ids.clear()
        self.check_preserving_enabled()
        return frozenset(item.config_block['id'] for item in changed)

    def close_preserving_enabled(self):
        remove = getattr(self, 'remove_state_listener', None)
        if remove is not None:
            remove()
            self.remove_state_listener = None

    @staticmethod
    def _pin(key, entity):
        return (key, id(entity), id(entity.action_script), id(entity.raw_config),
                id(entity.registry_entry), entity._is_enabled, id(entity._async_detach_triggers))

    def check_native(self):
        _need(threading.get_ident() == self.thread and self.path.read_bytes() == self.source
              and self.hass.data.get('automation') is self.component)
        if self.core111:
            from homeassistant.helpers.entity_platform import EntityPlatform
            add = self.component.async_add_entities
            platform = add.__self__
            _need(type(platform) is EntityPlatform and platform.hass is self.hass
                  and platform in self.component._platforms.values()
                  and add.__func__ is self.platform_add is EntityPlatform.async_add_entities)
        if self.component is None:
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            _need('automation' not in self.hass.config.components
                  and self.hass.data.get(DATA_INSTANCES, {}).get('automation') is None)
        live = {entity.unique_id: entity for entity in self.component.entities} if self.component is not None else {}
        _need(all(row[0] in live for row in self.foreign)
              and tuple(self._pin(row[0], live[row[0]]) for row in self.foreign) == self.foreign)
        _need(all(path.read_bytes() == source for path, source in self.other_sources))
        for owner, name, function in self.functions:
            _need(getattr(owner, name) is function and type(function.__code__) is CodeType)
            matches = [code for code in self.code + self.other_code
                       if code.co_filename == function.__code__.co_filename
                       and code.co_qualname == function.__qualname__]
            _need(len(matches) == 1 and function.__code__ == matches[0])

    def _entities(self):
        native = self.native
        if self.component is None:
            _need(self.hass.data.get('automation') is None and 'automation' not in self.hass.config.components)
            return ()
        rows = tuple(sorted((entity.unique_id, entity) for entity in self.component.entities))
        _need(all(type(key) is str and bool(key) and type(entity) is native.AutomationEntity
                  and entity.hass is self.hass for key, entity in rows)
              and len({key for key, _ in rows}) == len(rows))
        return rows

    def assert_disabled(self):
        self.check_native()
        rows = self._entities()
        _need(tuple(key for key, _ in rows) == tuple(key for key, _ in self.original)
              and all(entity._is_enabled is False and entity._async_detach_triggers is None
                      and not entity.action_script.is_running and not entity.action_script.runs
                      for key, entity in rows if key in self.targets))
        return rows

    async def quiesce(self):
        _need(asyncio.current_task() is self.task)
        self.check_native()
        _need(self._entities() == self.original)
        # No stop/cancel of running or queued actions is admitted. Native disable
        # with stop_actions=False detaches triggers without a synthetic state write.
        for key, entity in self.original:
            if key not in self.targets:
                continue
            self.check_native()
            _need(not entity.action_script.is_running and not entity.action_script.runs)
            await entity._async_disable(stop_actions=False)
        self.assert_disabled()

    async def stage(self):
        """Native config loading/validation/factory/add; new instances start off.

        Existing unchanged instances remain detached. The constructor override is
        confined to a fresh prepared config_block; raw_config remains native and
        unchanged for the independent source/projection observer.
        """
        _need(asyncio.current_task() is self.task and self.component is not None)
        self.assert_disabled()
        config = await self.component.async_prepare_reload(skip_reset=True)
        self.assert_disabled()
        configs = await self.factory._prepare_automation_config(self.hass, config, None)
        current = dict(self.assert_disabled())
        _need(len(configs) == len(current) and all(
            item.validation_status is self.factory.ValidationStatus.OK
            and item.config_block.get('id') in current
            and item.raw_blueprint_inputs is None for item in configs)
            and len({item.config_block['id'] for item in configs}) == len(configs))
        changed = [item for item in configs
                   if current[item.config_block['id']].raw_config != item.raw_config]
        _need(all(item.config_block['id'] in self.targets for item in changed))
        for item in changed:
            await current[item.config_block['id']].async_remove()
            self.check_native()
        staged = [replace(item, config_block={**item.config_block, 'initial_state': False})
                  for item in changed]
        entities = await self.factory._create_automation_entities(self.hass, staged)
        self.check_native()
        _need(len(entities) == len(changed) and all(
            type(entity) is self.native.AutomationEntity and entity._initial_state is False
            for entity in entities))
        await self.component.async_add_entities(entities)
        self.assert_disabled()

    async def restore(self):
        _need(asyncio.current_task() is self.task)
        rows = dict(self.assert_disabled())
        for key, enabled in self.enabled:
            self.check_native()
            entity = rows[key]
            _need(not entity.action_script.is_running and not entity.action_script.runs)
            if enabled:
                await entity._async_enable()
                self.check_native()
            _need(entity._is_enabled is enabled)
        # Write only after every intended native registration is restored.
        for key, enabled in self.enabled:
            entity = rows[key]
            _need(entity._is_enabled is enabled and (enabled or entity._async_detach_triggers is None))
            entity.async_write_ha_state()
        self.check_native()
