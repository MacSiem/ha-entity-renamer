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
    def __init__(self, hass, *, targets=None):
        import homeassistant.components.automation as native
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers.entity_component import EntityComponent
        _need(type(hass) is HomeAssistant and __version__ in ('2025.6.0', '2026.10.2', '2026.10.4'))
        component = hass.data.get('automation')
        _need(type(component) is EntityComponent and component.hass is hass)
        self.hass, self.native, self.component = hass, native, component
        self.task, self.thread = asyncio.current_task(), threading.get_ident()
        self.original = self._entities()
        all_keys = {key for key, _ in self.original}
        _need(targets is None or (type(targets) is tuple
              and all(type(key) is str for key in targets) and len(set(targets)) == len(targets)
              and set(targets) <= all_keys))
        self.targets = tuple(sorted(all_keys if targets is None else targets))
        self.foreign = tuple(self._pin(key, entity) for key, entity in self.original if key not in self.targets)
        self.enabled = tuple((key, entity._is_enabled) for key, entity in self.original if key in self.targets)
        _need(all(type(value) is bool for _, value in self.enabled))
        path = Path(native.__file__)
        self.source = path.read_bytes()
        self.path = path
        self.code = tuple(_codes(compile(self.source, str(path), 'exec', dont_inherit=True)))
        self.functions = tuple((owner, name, getattr(owner, name)) for owner, name in (
            (native, '_prepare_automation_config'), (native, '_create_automation_entities'),
            (EntityComponent, 'async_prepare_reload'),
            (native.AutomationEntity, '__init__'), (native.AutomationEntity, '_async_disable'),
            (native.AutomationEntity, '_async_enable'), (native.AutomationEntity, 'async_added_to_hass'),
            (native.AutomationEntity, 'async_will_remove_from_hass')))
        self.other_sources = tuple((path, path.read_bytes()) for path in sorted({
            Path(function.__code__.co_filename) for _, _, function in self.functions
        }) if path != self.path)
        self.other_code = tuple(code for path, source in self.other_sources
                                for code in _codes(compile(source, str(path), 'exec', dont_inherit=True)))
        self.check_native()

    @staticmethod
    def _pin(key, entity):
        return (key, id(entity), id(entity.action_script), id(entity.raw_config),
                id(entity.registry_entry), entity._is_enabled, id(entity._async_detach_triggers))

    def check_native(self):
        _need(threading.get_ident() == self.thread and self.path.read_bytes() == self.source
              and self.hass.data.get('automation') is self.component)
        live = {entity.unique_id: entity for entity in self.component.entities}
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
        rows = tuple(sorted((entity.unique_id, entity) for entity in self.component.entities))
        _need(all(type(key) is str and bool(key) and type(entity) is native.AutomationEntity
                  and entity.hass is self.hass and not entity.action_script.is_running
                  and not entity.action_script.runs for key, entity in rows)
              and len({key for key, _ in rows}) == len(rows))
        return rows

    def assert_disabled(self):
        self.check_native()
        rows = self._entities()
        _need(tuple(key for key, _ in rows) == tuple(key for key, _ in self.original)
              and all(entity._is_enabled is False and entity._async_detach_triggers is None
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
        _need(asyncio.current_task() is self.task)
        self.assert_disabled()
        config = await self.component.async_prepare_reload(skip_reset=True)
        self.assert_disabled()
        configs = await self.native._prepare_automation_config(self.hass, config, None)
        current = dict(self.assert_disabled())
        _need(len(configs) == len(current) and all(
            item.validation_status is self.native.ValidationStatus.OK
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
        entities = await self.native._create_automation_entities(self.hass, staged)
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
