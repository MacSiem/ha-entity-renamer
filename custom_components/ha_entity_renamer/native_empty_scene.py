"""Read-only admission of an empty native scene component, with a session latch.

Loaded emptiness is distinct from an absent component. No scene is activated,
reloaded or suppressed. Any scene activity invalidates this session permanently.
"""
import asyncio
import threading

from .transaction import Refused


def _need(value):
    if not value:
        raise Refused('native_empty_scene_unverified')


class NativeEmptySceneFence:
    def __init__(self, hass):
        from homeassistant.core import HomeAssistant, callback
        from homeassistant.const import __version__
        _need(type(hass) is HomeAssistant and __version__ in ('2025.6.0', '2026.10.2', '2026.10.4', '2026.11.0', '2026.11.1'))
        self.hass, self.thread = hass, threading.get_ident()
        self.changed, self.closed = False, False
        self.removers = []

        @callback
        def observed(event):
            data = event.data
            if (event.event_type == 'scene_reloaded' or data.get('domain') == 'scene'
                    or any(str(data.get(key, '')).startswith('scene.')
                           for key in ('entity_id', 'old_entity_id'))):
                self.changed = True

        try:
            for name in ('call_service', 'service_registered', 'service_removed',
                         'scene_reloaded', 'state_changed', 'entity_registry_updated'):
                self.removers.append((name, hass.bus.async_listen(name, observed)))
            self.image = self._sample()
            self.check()
        except BaseException:
            self.close()
            raise

    def _pending(self):
        from homeassistant.core import ServiceCall
        from .yaml_runtime_observer import _pending_native_work
        if _pending_native_work(self.hass, ('scene',)):
            return True
        for task in asyncio.all_tasks(self.hass.loop):
            if task is asyncio.current_task() or task.done():
                continue
            node, seen = task.get_coro(), set()
            while node is not None and id(node) not in seen:
                seen.add(id(node))
                frame = getattr(node, 'cr_frame', None) or getattr(node, 'gi_frame', None)
                if frame is not None:
                    module = frame.f_globals.get('__name__', '')
                    if module in ('homeassistant.components.scene',
                                  'homeassistant.components.scene.services',
                                  'homeassistant.components.homeassistant.scene'):
                        return True
                    if module.startswith('homeassistant.'):
                        if any(isinstance(value, ServiceCall) and value.domain == 'scene'
                               for value in frame.f_locals.values()):
                            return True
                        if module == 'homeassistant.setup' and frame.f_locals.get('domain') == 'scene':
                            return True
                node = (getattr(node, 'cr_await', None) or getattr(node, 'gi_yieldfrom', None)
                        or getattr(node, 'ag_await', None))
        return False

    def _sample(self):
        from homeassistant.components.scene import DATA_COMPONENT
        from homeassistant.components.homeassistant.scene import DATA_PLATFORM
        from homeassistant.helpers.entity_component import EntityComponent, DATA_INSTANCES
        from homeassistant.helpers.entity_platform import EntityPlatform
        from .yaml_runtime_observer import _platforms_settled
        hass = self.hass
        _need(not self._pending())
        component = hass.data.get(DATA_COMPONENT)
        native = hass.data.get(DATA_PLATFORM)
        instances = hass.data.get(DATA_INSTANCES, {})
        loaded = 'scene' in hass.config.components
        _need(not hass.states.async_entity_ids('scene'))
        if not loaded:
            _need(component is None and native is None and instances.get('scene') is None
                  and not hass.services._services.get('scene'))
            return (False,)
        _need(type(component) is EntityComponent and component.hass is hass
              and component.domain == 'scene' and instances.get('scene') is component
              and _platforms_settled(component) and not tuple(component.entities)
              and type(native) is EntityPlatform)
        rows = []
        for platform in component._platforms.values():
            _need(type(platform) is EntityPlatform and platform.hass is hass
                  and platform.domain == 'scene' and type(platform.platform_name) is str
                  and bool(platform.platform_name) and not platform.entities
                  and platform._async_cancel_retry_setup is None)
            if platform.platform_name == 'homeassistant':
                _need(platform is native and platform._setup_complete)
            elif platform.platform_name == 'scene':
                _need(getattr(component.async_add_entities, '__self__', None) is platform)
            else:
                # A foreign scene platform may coexist only after setup has
                # settled with no entities. It gains no scene migration support.
                _need(platform._setup_complete)
            rows.append((id(platform), platform.platform_name, id(platform.entities),
                         id(platform.domain_entities), platform._setup_complete))
        _need(sum(platform is native for platform in component._platforms.values()) == 1)
        services = hass.services._services.get('scene', {})
        _need(all(name in services for name in ('create', 'delete', 'reload')))
        return (True, id(component), id(component._entities), id(component._platforms),
                id(instances), tuple(sorted(rows)), id(services),
                tuple(sorted((name, id(service), id(service.job), id(service.job.target),
                              id(service.schema)) for name, service in services.items())))

    def check(self):
        _need(not self.closed and not self.changed)
        if threading.get_ident() != self.thread:
            return  # Native runtime census stays on-loop; physical guards read the latch.
        from .yaml_runtime_observer import _registered_bus_remove
        for name, remove in self.removers:
            _registered_bus_remove(self.hass, remove, name)
        _need(self._sample() == self.image and not self.changed)

    def close(self):
        if not self.closed:
            self.closed = True
            for _, remove in self.removers:
                remove()
            self.removers.clear()
