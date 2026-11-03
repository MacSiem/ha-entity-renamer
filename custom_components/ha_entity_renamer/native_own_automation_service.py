"""Actual native Automation trigger-service owner and target-map census."""
from __future__ import annotations

from functools import partial
from pathlib import Path
from types import CodeType, FunctionType

from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused(reason) from None


def _codes(code):
    yield code
    for child in code.co_consts:
        if type(child) is CodeType:
            yield from _codes(child)


def capture(hass):
    import homeassistant
    from homeassistant.const import __version__
    from homeassistant.components import automation
    from homeassistant.core import HassJob, HassJobType, Service
    from homeassistant.helpers import service as service_helper
    from homeassistant.helpers.entity_component import DATA_INSTANCES, EntityComponent
    from .native_automation_state_binding import _PINS
    from .native_unload_code_proof import prove_methods

    _need(__version__ in _PINS, 'own_automation_service_core_unqualified')
    service_pins = {'2025.6.0': ('6cdda27dd9e3cc25605e94365789062ffdddfe538f815bf7bf1f973bc1b8b157',
                                '4b585de73f353c3d60cafb005d33501e58c1f2a694e1afa0fda47b370d9a5474'),
                    '2026.10.4': ('ccf389ff17d99fb40d43de156f7dcea77ccf121f5425961f068529a28a105340',
                                  '97524d7f2c826a682182cf94f07507244d12ca87d03e0187aff9368610df0022')}
    service_pins['2026.10.2'] = service_pins['2026.10.4']
    pins = dict(_PINS[__version__])
    pins.update({'helpers/service.py': service_pins[__version__][0],
                 'helpers/entity_component.py': service_pins[__version__][1]})
    refs = [(automation, 'async_setup'), (EntityComponent, 'async_register_entity_service'),
            (service_helper, 'async_register_entity_service'), (service_helper, 'entity_service_call')]
    methods, codes = prove_methods(Path(homeassistant.__file__).resolve().parent, pins, refs)
    component = hass.data.get(DATA_INSTANCES, {}).get('automation')
    _need(type(component) is EntityComponent and component.hass is hass and component.domain == 'automation'
          and type(component._entities) is dict, 'own_automation_component_unqualified')
    registry = hass.services._services.get('automation')
    service = None if type(registry) is not dict else registry.get('trigger')
    _need(type(service) is Service and type(service.job) is HassJob, 'own_automation_service_missing')
    route = service.job.target
    _need(type(route) is partial and route.func is service_helper.entity_service_call
          and len(route.args) == 3 and route.args[0] is hass and route.args[1] is component._entities
          and type(route.args[2]) is HassJob and service.job.job_type is HassJobType.Coroutinefunction,
          'own_automation_service_route_changed')
    expected_keywords = ({'required_features': None} if __version__ == '2025.6.0' else
                         {'admin_only': False, 'entity_device_classes': None, 'required_features': None})
    _need(route.keywords == expected_keywords, 'own_automation_service_route_changed')
    callback = route.args[2].target
    _need(type(callback) is FunctionType and callback.__globals__ is vars(automation)
          and callback.__qualname__ == 'async_setup.<locals>.trigger_service_handler'
          and any(callback.__code__ is code for code in _codes(automation.async_setup.__code__)),
          'own_automation_service_callback_changed')
    return (component, component._entities, registry, service, service.job, route,
            route.args[2], callback, service.schema, refs, methods, codes)


def check(hass, snapshot):
    from .native_unload_code_proof import unchanged
    component, entities, registry, service, job, route, action, callback, schema, refs, methods, codes = snapshot
    unchanged(refs, methods, codes)
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    _need(hass.data.get(DATA_INSTANCES, {}).get('automation') is component
          and component._entities is entities and hass.services._services.get('automation') is registry
          and registry.get('trigger') is service and service.job is job and job.target is route
          and route.args[1] is entities and route.args[2] is action and action.target is callback
          and service.schema is schema, 'own_automation_service_changed')
