"""Actual native Template numeric state and literal binary consumers, read-only.

No setup/render/refresh/reload/callback execution, native field/cache writes or
new authority. Runtime and original source semantics are separate row facts.
All other platforms/coordinators/source/registry/entry rows stay visible UNKNOWN.
Product apply, source-ID window/recovery and full family acceptance are outside.
"""
from __future__ import annotations
import asyncio
from collections import defaultdict
from dataclasses import dataclass, replace
from functools import partial
import hashlib
from importlib import import_module
import inspect
import json
import sys
from pathlib import Path
from types import CodeType, FunctionType, ModuleType

from .yaml_validation_worker import (_fingerprint, simple_template_sensor_semantics,
    simple_template_binary_semantics, _qualified_binary_expression)
from .yaml_runtime_observer import _pending_native_work, _registered_bus_remove
from .native_unload_code_proof import prove_methods, unchanged

_PINS = {'2025.6.0': {'components/template/__init__.py': 'ecbdfe7f1f184c80aa6957f587ec620eebc20b4dea4b5b4273ecc0086df4c266',
              'components/template/config.py': '7812160415ae318e2471ccf69547881cd0dba8e6daec82788d9dbd8d0daa8b32',
              'components/template/const.py': '10ba59c707e50bbcae280cc883ee2bdec862e3d879128bc81bc5750b296f9d58',
              'components/template/coordinator.py': 'c5d22bee2f398b30d62144b88f60f2995d9c8854ca9b5069704ee506f61432a0',
              'components/template/entity.py': 'dbbf7dfd8c9cacbf1bad6ba206912acd58badae67bbf4f92fa642ccd08041b60',
              'components/template/helpers.py': '6e4a4f4974f2b22aca91872827e9510e8b09071e0f3ff7824c4acfa7d64827a1',
              'components/template/sensor.py': '6b71e5a605291cf925a435ebcfa70827a3dc9c624839b40156f0ee71a740e0ab',
              'components/template/template_entity.py': '7863a9a3e480fbe84f76326289d6d97a5eef4caf1b92c5caf21ada23134034f6',
              'components/template/trigger_entity.py': 'f3833cae8a749b0869453d0822fd4a58968a8d6ad42655ea602cdbf012a5e3b9',
              'helpers/entity_platform.py': 'e1c976af7fb673fe3b5cf1c62be032c0d0e1740253bff65a528a9503ffca0ed6',
              'helpers/event.py': 'd5e21c38afd9eafe566724b8db34ea2e87eaa8d6dc10c1719a82f5d90d43260e',
              'helpers/trigger_template_entity.py': 'e30cd232e71b644610c0fded1618e5c23f2f373616af6a8fccb6b9822345a3b1'},
 '2026.10.2': {'components/template/__init__.py': '33e9338f4bbc17c4e3be3f2383027ed63e6669a0679deec7a2e0b2f016a649ad',
               'components/template/config.py': '82eeafd970eff28f66cb88a9a36e0f939597bea4425827b10dac75a1cdc2670d',
               'components/template/const.py': 'ca3eaf720c5cb96985945ec68f3b7e5d0e9ad7af4d86f8f3f6ecb2ab2ed3e062',
               'components/template/coordinator.py': 'e249458544f1c17e3b9c90ed246912d51795f8e7623ffdcc92abeba77f25c593',
               'components/template/entity.py': '5904e1e43ed8812ed3ab72ded61a2bf7e7aa9941a6d52b1409609cfceb8a3ea3',
               'components/template/helpers.py': 'a214dd203972f939e1460e82521758819e9482ecf0d66d84ce9792ad7f8c0bff',
               'components/template/sensor.py': '171f6df16a272d352d0afe62cc35b9590f160c3e37180ab36dfb9afcdd49fb25',
               'components/template/template_entity.py': '904c82d89b83b0cc097dbb482b50407a7d3dfa9109af1f468ca48420cf496d15',
               'components/template/trigger_entity.py': 'c967d35e72be6b0ccbda3edbc45fceb2289822456028fb7723f00de39b37d21b',
               'helpers/entity_platform.py': '776e21f2ef16715b17e34fe27da33a3d30042b27988d75aaf5461ddcf736b09e',
               'helpers/event.py': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269',
               'helpers/trigger_template_entity.py': 'f6411c6e1a65683e5427667358f7f5033cbad34ec5d32e6f84746a60e52c1b14'}, '2026.10.4': {'components/template/__init__.py': '33e9338f4bbc17c4e3be3f2383027ed63e6669a0679deec7a2e0b2f016a649ad',
               'components/template/config.py': '82eeafd970eff28f66cb88a9a36e0f939597bea4425827b10dac75a1cdc2670d',
               'components/template/const.py': 'ca3eaf720c5cb96985945ec68f3b7e5d0e9ad7af4d86f8f3f6ecb2ab2ed3e062',
               'components/template/coordinator.py': 'e249458544f1c17e3b9c90ed246912d51795f8e7623ffdcc92abeba77f25c593',
               'components/template/entity.py': '5904e1e43ed8812ed3ab72ded61a2bf7e7aa9941a6d52b1409609cfceb8a3ea3',
               'components/template/helpers.py': 'a214dd203972f939e1460e82521758819e9482ecf0d66d84ce9792ad7f8c0bff',
               'components/template/sensor.py': '171f6df16a272d352d0afe62cc35b9590f160c3e37180ab36dfb9afcdd49fb25',
               'components/template/template_entity.py': '904c82d89b83b0cc097dbb482b50407a7d3dfa9109af1f468ca48420cf496d15',
               'components/template/trigger_entity.py': 'c967d35e72be6b0ccbda3edbc45fceb2289822456028fb7723f00de39b37d21b',
               'helpers/entity_platform.py': '776e21f2ef16715b17e34fe27da33a3d30042b27988d75aaf5461ddcf736b09e',
               'helpers/event.py': 'caafbdd2437c4912e21dceca588577f24270a743e80a217e308175818de9e269',
               'helpers/trigger_template_entity.py': 'f6411c6e1a65683e5427667358f7f5033cbad34ec5d32e6f84746a60e52c1b14'}}


_BINARY_PINS = {
    '2025.6.0': {
        'components/template/binary_sensor.py': 'bbe0f0b272f8173a5a09386752778ec758ae79f2ecf0e66ecc9d2e0fa31c15a7',
        'helpers/config_validation.py': '74ae0ebe29be4d28bc9ab31c5352599e2eccdc31bd534cb40d1b07f3765ce8ea'},
    '2026.10.2': {
        'components/template/binary_sensor.py': 'b3e86b88a258760edbdcad2a79e929ee77346888d8d7ff793f1bb97003e3c857',
        'helpers/template/helpers.py': '2f4796b9d35516ab9c39498365d4fcf843ebd3334ac9c1b07000d7811d706c67',
        'helpers/config_validation.py': '78776d1584e83ec05c97dc138cbaf343233f0293597dda4eb1872106ecb97d0c'}, '2026.10.4': {
        'components/template/binary_sensor.py': 'b3e86b88a258760edbdcad2a79e929ee77346888d8d7ff793f1bb97003e3c857',
        'helpers/template/helpers.py': '2f4796b9d35516ab9c39498365d4fcf843ebd3334ac9c1b07000d7811d706c67',
        'helpers/config_validation.py': '78776d1584e83ec05c97dc138cbaf343233f0293597dda4eb1872106ecb97d0c'},
}


class TemplateConsumerError(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise TemplateConsumerError(reason) from None


def _bound(value, owner, function):
    return (getattr(value, '__self__', None) is owner
            and getattr(value, '__func__', None) is function)


def _codes(code):
    yield code
    for child in code.co_consts:
        if type(child) is CodeType:
            yield from _codes(child)


@dataclass(frozen=True)
class TemplateConsumerRow:
    kind: str
    platform: str
    key: str | None
    entry_id: str | None = None
    entity_id: str | None = None
    registry_id: str | None = None
    consumer_verified: bool = False
    source_verified: bool = False
    semantic_sha256: str | None = None
    dependencies: tuple[str, ...] = ()
    reason: str = 'runtime_variant_unverified'


@dataclass(frozen=True)
class TemplateConsumers:
    version: str
    evidence_sha256: str
    rows: tuple[TemplateConsumerRow, ...]
    tokens: tuple

    @property
    def migration_ready(self):
        return False

    @property
    def family_complete(self):
        return False  # this component supplies row proofs, no whole-family gate


class _Native:
    def __init__(self, hass):
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import event, entity_platform as ep
        from homeassistant.components.template import template_entity as te, sensor, binary_sensor
        from homeassistant.helpers import template as ht, config_validation as cv
        from .native_utility_setup_audit import _SOURCES
        _need(type(hass) is HomeAssistant and __version__ in _PINS, 'native_runtime_unverified')
        self.hass, self.version = hass, __version__
        self.root = Path(import_module('homeassistant').__file__).resolve().parent
        self.pins = {**_SOURCES[__version__], **_PINS[__version__], **_BINARY_PINS[__version__]}
        self.binary_module, self.template_module, self.cv = binary_sensor, ht, cv
        self.boolean_module = import_module('homeassistant.helpers.template' if __version__ == '2025.6.0'
                                            else 'homeassistant.helpers.template.helpers')
        from homeassistant.helpers.template import RenderInfo
        self.render_module = import_module(RenderInfo.__module__)
        self.pins.update({'helpers/template.py':
            '2e4afdb3e16a7e6339bd72966a5d9a6d1bff5e7c602797e20118a1a191944bf7'}
            if __version__ == '2025.6.0' else {'helpers/template/render_info.py':
            '259fba6cd34246e9a022dd67e9b9a5f5a041a307637b9cc15c74b7afd5249ca9'})
        self.sensor = (sensor.SensorTemplate if __version__ == '2025.6.0'
                       else sensor.StateSensorEntity)
        self.binary = (binary_sensor.BinarySensorTemplate if __version__ == '2025.6.0'
                       else binary_sensor.StateBinarySensorEntity)
        self.refs = [(self.sensor, '__init__'), (te.TemplateEntity, '__init__'),
            (te.TemplateEntity, '_async_template_startup'),
            (te.TemplateEntity, '_handle_results'),
            (te.TemplateEntity, 'add_template_attribute'),
            (te._TemplateAttribute, '__init__'),
            (event.TrackTemplateResultInfo, '__init__'),
            (event.TrackTemplateResultInfo, 'async_setup'),
            (event.TrackTemplateResultInfo, '_refresh'),
            (event.TrackTemplateResultInfo, 'async_remove'),
            (event._TrackStateChangeFiltered, '__init__'),
            (event._TrackStateChangeFiltered, '_setup_entities_listener'),
            (event._TrackStateChangeFiltered, 'async_remove'),
            (event, '_async_track_state_change_event'),
            (event, '_async_track_event'), (event, '_remove_listener'),
            (event, '_async_dispatch_entity_id_event'), (ep, 'async_get_platforms')]
        if __version__ == '2025.6.0':
            self.refs += [(self.sensor, '_async_setup_templates'),
                          (self.sensor, '_update_state')]
        else:
            self.refs += [(te.TemplateEntity, 'setup_state_template'),
                          (sensor.AbstractTemplateSensor, '_validate_state')]
        self.refs += [(self.binary, '__init__'), (self.binary, '_update_state'),
                      (te.TemplateEntity, '_update_state'),
                      (te.TemplateEntity, '_async_setup_templates'),
                      (ht, 'result_as_boolean'), (self.boolean_module, 'forgiving_boolean'),
                      (cv, 'boolean')]
        if __version__ == '2025.6.0':
            self.refs += [(self.binary, '_async_setup_templates')]
        else:
            from homeassistant.components.template.entity import AbstractTemplateEntity
            self.refs += [(binary_sensor.AbstractTemplateBinarySensor, '__init__'),
                          (AbstractTemplateEntity, '__init__'),
                          (AbstractTemplateEntity, 'add_template'),
                          (te.TemplateEntity, 'setup_template')]
        self.refs += [(RenderInfo, '_filter_entities'), (RenderInfo, '_freeze'),
                      (self.render_module, '_false')]
        self.methods, self.codes = prove_methods(self.root, self.pins, self.refs)

    def bindings(self):
        unchanged(self.refs, self.methods, self.codes)

    def sources(self):
        for name, digest in self.pins.items():
            path = self.root / name
            _need(path.resolve() == path and hashlib.sha256(path.read_bytes()).hexdigest()
                  == digest, 'native_source_pin_changed')


def _live_sensor(native, entity):
    """Exact minimum/current semantic fields; no arbitrary _config={} fallback."""
    from homeassistant.components.template import template_entity as te
    from homeassistant.helpers.template import Template
    _need(type(entity) is native.sensor and entity._preview_callback is None,
          'native_state_sensor_required')
    _need(not entity._action_scripts and type(entity._run_variables) is dict
          and not entity._run_variables and entity._blueprint_inputs is None,
          'sensor_semantics_subset_unverified')
    if native.version == '2025.6.0':
        cfg = {'state': entity._template, 'name': entity._friendly_name_template,
            'availability': entity._availability_template,
            'attributes': entity._attribute_templates, 'icon': entity._icon_template,
            'picture': entity._entity_picture_template,
            'last_reset': entity._attr_last_reset_template,
            'unit_of_measurement': entity._attr_native_unit_of_measurement,
            'device_class': entity._attr_device_class, 'state_class': entity._attr_state_class}
        state_template = entity._template
    else:
        cfg = entity._config
        _need(set(entity._templates) == {'state'} and not entity._attribute_templates
              and entity._attributes_template is None, 'sensor_semantics_subset_unverified')
        value = entity._templates['state']
        _need(value.attribute == '_attr_native_value'
              and value.template is cfg['state'], 'native_template_slot_changed')
        state_template = value.template
    semantic = simple_template_sensor_semantics(cfg)
    _need(type(state_template) is Template and state_template.hass is native.hass
          and entity._attr_name == semantic[2]
          and [entity._attr_native_unit_of_measurement, entity._attr_device_class,
               entity._attr_state_class] == semantic[3:], 'native_semantic_field_changed')
    attrs = entity._template_attrs
    _need(type(attrs) is dict and len(attrs) == 1 and state_template in attrs
          and type(attrs[state_template]) is list and len(attrs[state_template]) == 1,
          'native_template_slots_changed')
    slot = attrs[state_template][0]
    _need(type(slot) is te._TemplateAttribute and slot._entity is entity
          and slot._attribute == '_attr_native_value' and slot.template is state_template
          and slot.validator is None and slot.none_on_template_error is False,
          'native_template_slot_changed')
    callback = slot.on_update
    if native.version == '2025.6.0':
        _need(_bound(callback, entity, native.sensor._update_state), 'native_update_owner_changed')
    else:
        parent = te.TemplateEntity.setup_state_template.__code__
        _need(type(callback) is FunctionType and any(callback.__code__ is code
              for code in _codes(parent)) and callback.__qualname__ ==
              'TemplateEntity.setup_state_template.<locals>._update_state',
              'native_update_owner_changed')
        cells = dict(zip(callback.__code__.co_freevars,
                         (cell.cell_contents for cell in callback.__closure__ or ())))
        _need(set(cells) == {'self', 'attribute', 'on_update', 'validator'}
              and cells['self'] is entity and cells['attribute'] == '_attr_native_value'
              and cells['on_update'] is None
              and _bound(cells['validator'], entity, type(entity)._validate_state),
              'native_update_owner_changed')
    return semantic, state_template, slot


def _live_binary(native, entity):
    """Actual boolean source/body/slot proof; no renderer or state is executed."""
    from homeassistant.components.template import template_entity as te
    from homeassistant.helpers.template import Template
    _need(type(entity) is native.binary and entity._preview_callback is None
          and not entity._action_scripts and type(entity._run_variables) is dict
          and not entity._run_variables and entity._blueprint_inputs is None,
          'native_state_binary_required')
    _need(entity._availability_template is None and entity._attr_device_info is None
          and all(getattr(entity, name) is None for name in
                  ('_delay_on', '_delay_off', '_delay_cancel')),
          'binary_semantics_subset_unverified')
    if native.version == '2025.6.0':
        cfg = {'state': entity._template, 'name': entity._friendly_name_template,
            'device_class': entity._attr_device_class,
            'availability': entity._availability_template,
            'attributes': entity._attribute_templates, 'icon': entity._icon_template,
            'picture': entity._entity_picture_template,
            'delay_on': entity._delay_on_raw, 'delay_off': entity._delay_off_raw}
        attribute = '_state'
    else:
        cfg, attribute = entity._config, '_attr_is_on'
        _need(set(entity._templates) == {'state'} and not entity._attribute_templates
              and entity._attributes_template is None,
              'binary_semantics_subset_unverified')
        value = entity._templates['state']
        _need(value.attribute == attribute and value.template is cfg['state']
              and value.validator is None and value.none_on_template_error is False,
              'native_binary_slot_changed')
    semantic = simple_template_binary_semantics(cfg)
    template = entity._template
    _need(type(template) is Template and template is cfg['state'] and template.hass is native.hass
          and entity._attr_name == semantic[2] and entity._attr_device_class == semantic[3]
          and type(entity._attr_is_on) is bool,
          'native_binary_semantics_changed')
    state = native.hass.states.get(entity.entity_id)
    _need(state is not None and state.state == ('on' if entity._attr_is_on else 'off'),
          'native_binary_state_changed')
    attrs = entity._template_attrs
    _need(type(attrs) is dict and set(attrs) == {template}
          and type(attrs[template]) is list and len(attrs[template]) == 1,
          'native_binary_slots_changed')
    slot = attrs[template][0]
    _need(type(slot) is te._TemplateAttribute and slot._entity is entity
          and slot._attribute == attribute and slot.template is template
          and slot.validator is None and slot.none_on_template_error is False,
          'native_binary_slot_changed')
    callback = slot.on_update
    if native.version == '2025.6.0':
        _need(_bound(callback, entity, native.binary._update_state), 'native_binary_update_changed')
    else:
        _need(type(callback) is FunctionType and any(callback.__code__ is code
              for code in _codes(te.TemplateEntity.setup_state_template.__code__))
              and callback.__qualname__ == 'TemplateEntity.setup_state_template.<locals>._update_state'
              and callback.__globals__ is vars(te)
              and callback.__defaults__ is None and callback.__kwdefaults__ is None
              and entity._templates['state'].on_update is callback,
              'native_binary_update_changed')
        cells = dict(zip(callback.__code__.co_freevars,
                         (cell.cell_contents for cell in callback.__closure__ or ())))
        _need(set(cells) == {'self', 'attribute', 'on_update', 'validator'}
              and cells['self'] is entity and cells['attribute'] == '_attr_is_on'
              and cells['validator'] is None
              and _bound(cells['on_update'], entity, native.binary._update_state),
              'native_binary_update_changed')
    # Source-pinned code plus the actual conversion aliases it dispatches to.
    ht, boolean = native.template_module, native.boolean_module
    _need(native.binary_module.template is ht
          and native.binary._update_state.__globals__ is vars(native.binary_module)
          and ht.result_as_boolean is boolean.result_as_boolean
          and boolean.result_as_boolean.__globals__ is vars(boolean)
          and boolean.result_as_boolean.__globals__['forgiving_boolean'] is boolean.forgiving_boolean
          and boolean.forgiving_boolean.__globals__ is vars(boolean)
          and native.cv.boolean.__globals__ is vars(native.cv),
          'native_binary_boolean_binding_changed')
    if native.version in ('2026.10.2', '2026.10.4'):
        _need(vars(boolean).get('cv') is native.cv,
              'native_binary_boolean_binding_changed')
    else:
        # Minimum's forgiving_boolean imports cv locally on each conversion.
        # Its proven function globals do not pin that package/import alias.
        helpers = sys.modules.get('homeassistant.helpers')
        _need(type(helpers) is ModuleType
              and vars(helpers).get('config_validation') is native.cv
              and sys.modules.get('homeassistant.helpers.config_validation') is native.cv,
              'native_binary_boolean_binding_changed')
    return semantic, template, slot


def _registration(native, entity, template):
    """Actual detach -> keyed callbacks -> shared bus dispatcher/filter graph."""
    from homeassistant.core import HassJob
    from homeassistant.helpers import event
    from homeassistant.helpers.template import TemplateStateFromEntityId, RenderInfo
    from homeassistant.components.template.template_entity import TemplateEntity
    hass, info = native.hass, entity._template_result_info
    _need(type(info) is event.TrackTemplateResultInfo and info.hass is hass
          and type(info._job) is HassJob
          and _bound(info._job.target, entity, TemplateEntity._handle_results)
          and not info._has_super_template and not info._time_listeners,
          'native_tracker_owner_changed')
    tracks = info._track_templates
    _need(len(tracks) == 1 and type(tracks[0]) is event.TrackTemplate
          and tracks[0].template is template and tracks[0].rate_limit is None,
          'native_tracked_template_changed')
    variables = tracks[0].variables
    _need(type(variables) is dict and set(variables) == {'this'}
          and type(variables['this']) is TemplateStateFromEntityId
          and variables['this']._hass is hass
          and variables['this']._entity_id == entity.entity_id,
          'native_template_variables_changed')
    _need(set(info._info) == {template}, 'native_render_info_changed')
    render = info._info[template]
    _need(type(render) is RenderInfo and render.template is template
          and render.exception is None and not render.all_states
          and not render.all_states_lifecycle and not render.domains
          and not render.domains_lifecycle and not render.has_time
          and render.rate_limit is None and bool(render.entities),
          'native_dependencies_unverified')
    _need(_bound(render.filter, render, RenderInfo._filter_entities)
          and render.filter_lifecycle is native.render_module._false,
          'native_render_predicate_changed')
    filtered = info._track_state_changes
    _need(type(filtered) is event._TrackStateChangeFiltered and filtered.hass is hass
          and _bound(filtered._action, info, event.TrackTemplateResultInfo._refresh)
          and _bound(filtered._action_as_hassjob.target, info, event.TrackTemplateResultInfo._refresh),
          'native_filtered_tracker_changed')
    states = filtered._last_track_states
    dependencies = tuple(sorted(render.entities))
    _need(type(states) is event.TrackStates and not states.all_states and not states.domains
          and tuple(sorted(states.entities)) == dependencies,
          'native_dependencies_changed')
    _need(set(filtered._listeners) == {event._ENTITIES_LISTENER}, 'native_registration_missing')
    remove = filtered._listeners[event._ENTITIES_LISTENER]
    _need(type(remove) is partial and remove.func is event._remove_listener
          and not remove.keywords and len(remove.args) == 5, 'native_detach_changed')
    owner, tracker, keys, job, callbacks = remove.args
    _need(owner is hass and tracker is event._KEYED_TRACK_STATE_CHANGE
          and tuple(sorted(keys)) == dependencies and len(tuple(keys)) == len(dependencies)
          and type(job) is HassJob
          and _bound(job.target, info, event.TrackTemplateResultInfo._refresh),
          'native_registration_owner_changed')
    data = hass.data.get(tracker.key)
    _need(type(data) is event._KeyedEventData and data.callbacks is callbacks
          and type(callbacks) is defaultdict and callbacks.default_factory is list,
          'native_registration_missing')
    (bus_job, event_filter), bus_token = _registered_bus_remove(hass, data.listener, tracker.event_type)
    for actual, expected in ((bus_job.target, tracker.dispatcher_callable),
                             (event_filter, tracker.filter_callable)):
        _need(type(actual) is partial and actual.func is expected and not actual.keywords
              and len(actual.args) == 2 and actual.args[0] is hass
              and actual.args[1] is callbacks, 'native_dispatch_changed')
    for key in dependencies:
        _need(sum(value is job for value in callbacks.get(key, ())) == 1,
              'native_registration_missing')
    for key, members in callbacks.items():
        for member in members:
            if _bound(member.target, info, event.TrackTemplateResultInfo._refresh):
                _need(key in dependencies and member is job, 'native_registration_extra')
    # Native cleanup remains owned by this entity as well as present on the bus.
    _need(sum(_bound(callback, info, event.TrackTemplateResultInfo.async_remove)
              for callback in entity._on_remove) == 1, 'native_cleanup_owner_changed')
    token = (id(entity), id(template), id(info), id(info._job), id(tracks[0]),
             id(variables), id(info._info), id(render), id(render.filter),
             id(render.filter_lifecycle), id(filtered), id(states),
             id(filtered._action_as_hassjob), id(remove), id(job), id(callbacks), bus_token)
    return dependencies, token


def _under(origin, parent):
    if origin.includes == parent.includes:
        return (origin.path == parent.path
                and origin.route[:len(parent.route)] == parent.route)
    prefix = len(parent.includes)
    return (origin.includes[:prefix] == parent.includes
        and len(origin.includes) > prefix
        and origin.includes[prefix].route[:len(parent.route)] == parent.route)


def _source_semantics(native, entity, registry_row, semantic, evidence, documents, *, platform='sensor'):
    """Compare against actual ORIGINAL full-three entry or sealed YAML hash."""
    from homeassistant.components.template import sensor
    if registry_row['config_entry_id'] is not None:
        _need(platform == 'sensor', 'binary_config_entry_unverified')
        entry_id = registry_row['config_entry_id']
        source = next((row for row in documents['core.config_entries']['entries']
                       if row['entry_id'] == entry_id), None)
        _need(source is not None and source['domain'] == 'template'
              and source['options'].get('template_type') == 'sensor'
              and entity.unique_id == entry_id, 'entry_source_identity_unverified')
        options = dict(source['options'])
        options.pop('template_type')
        if native.version in ('2026.10.2', '2026.10.4'):
            additional = options.pop('additional_options', None)
            if additional:
                options.update(additional)
            schema = sensor.SENSOR_CONFIG_ENTRY_SCHEMA
        else:
            schema = sensor.SENSOR_CONFIG_SCHEMA
        expected = simple_template_sensor_semantics(schema(options))
        return expected == semantic
    source = evidence.template_sources
    _need(source is not None and source.source_matched and source.rows is not None,
          'yaml_source_inventory_unverified')
    domain = 'template.' + platform
    definitions = [row for row in source.ledger.definitions
                   if row.domain == domain and row.native_key == entity.unique_id]
    _need(len(definitions) == 1, 'yaml_source_identity_unverified')
    blocks = [row for row in source.ledger.definitions if row.domain == 'template.state'
              and _under(definitions[0].origin, row.origin)]
    _need(len(blocks) == 1 and blocks[0].native_key is not None,
          'yaml_source_block_unverified')
    expected = next((row[2] for row in source.rows
                     if row[:2] == (domain, entity.unique_id)), None)
    actual = _fingerprint([domain, entity.unique_id,
                          [blocks[0].native_key, semantic]])
    return expected == actual


def _physical_native_metadata(native, entity, semantic, platform):
    """Complete exposed metadata for this UIDless stateless schema slice."""
    attributes = {'friendly_name': semantic[2]}
    fields = (('unit_of_measurement', 3), ('device_class', 4), ('state_class', 5))
    if platform == 'binary_sensor':
        fields = (('device_class', 3),)
    for name, index in fields:
        if semantic[index] is not None:
            attributes[name] = semantic[index]
    state = native.hass.states.get(entity.entity_id)
    _need(state is not None and dict(state.attributes) == attributes
          and entity.should_poll is False and entity.icon is None
          and entity.entity_picture is None and entity._attr_device_info is None
          and not entity._attr_extra_state_attributes,
          'yaml_physical_metadata_unverified')


def _physical_source_key(semantic, evidence, platform, *, key=None):
    """One sealed ORIGINAL/current-image signature, no live source adoption."""
    from .template_physical_identity import is_physical_definition
    source = evidence.template_sources
    _need(source is not None and source.source_matched and source.rows is not None,
          'yaml_source_inventory_unverified')
    domain = 'template.' + platform
    matches = []
    for definition in source.ledger.definitions:
        if (definition.domain != domain or not is_physical_definition(definition)
                or (key is not None and definition.native_key != key)):
            continue
        blocks = [row for row in source.ledger.definitions if row.domain == 'template.state'
                  and _under(definition.origin, row.origin)]
        _need(len(blocks) == 1 and blocks[0].native_key is not None,
              'yaml_source_block_unverified')
        expected = next((row[2] for row in source.rows
                         if row[:2] == (domain, definition.native_key)), None)
        actual = _fingerprint([domain, definition.native_key, [blocks[0].native_key, semantic]])
        if expected == actual:
            matches.append(definition.native_key)
    _need(len(matches) == 1, 'yaml_physical_semantic_bijection_unverified')
    return matches[0]


def _sample(native, evidence, observed, *, physical_identities=()):
    from homeassistant.config_entries import ConfigEntry, ConfigEntries
    from homeassistant.helpers import entity_platform as ep, entity_registry as er
    from homeassistant.components import template as template_component
    from homeassistant.components.template.coordinator import TriggerUpdateCoordinator
    from .native_utility_unload_audit import _row
    hass = native.hass
    _need(type(hass.config_entries) is ConfigEntries
          and not _pending_native_work(hass, ('template',)), 'native_template_unsettled')
    documents = {row.key: json.loads(row.manager_json) for row in observed.files}
    registry = hass.data.get(er.DATA_REGISTRY)
    _need(type(registry) is er.EntityRegistry, 'native_registry_missing')
    registry_rows = [r for r in documents['core.entity_registry']['entities']
                     if r['platform'] == 'template']
    original = {row['entity_id']: row for row in registry_rows}
    rows, tokens, live = [], [], set()
    for platform in ep.async_get_platforms(hass, 'template'):
        _need(type(platform) is ep.EntityPlatform and platform.hass is hass
              and platform.platform_name == 'template'
              and all(task.done() for task in platform._tasks), 'native_platform_unsettled')
        entry = platform.config_entry
        rows.append(TemplateConsumerRow('platform', platform.domain, None,
            entry.entry_id if entry is not None else None))
        tokens.append(('platform', id(platform), id(entry), tuple(sorted(platform.entities))))
        for key, entity in platform.entities.items():
            _need(key == entity.entity_id and entity.hass is hass and entity.platform is platform,
                  'native_entity_identity_changed')
            live.add(key)
            stored = original.get(key)
            row = registry.async_get(key)
            identity = None if row is None else row.id
            entry_id = entry.entry_id if entry is not None else None
            unique = None if stored is None else stored['unique_id']
            consumer, source_verified, semantic, dependencies = False, False, None, ()
            reason = 'runtime_variant_unverified'
            token = ('entity', id(entity), id(row))
            if ((platform.domain == 'sensor' and type(entity) is native.sensor)
                    or (platform.domain == 'binary_sensor' and type(entity) is native.binary)):
                try:
                    if entity.unique_id is None:
                        _need(entry is None and stored is None and row is None
                              and entity.registry_entry is None and hass.states.get(key) is not None,
                              'native_registryless_identity_unverified')
                        fixed = [source_key for domain, source_key, entity_id in physical_identities
                                 if domain == platform.domain and entity_id == key]
                        _need(len(fixed) <= 1, 'yaml_physical_identity_ambiguous')
                        unique = fixed[0] if fixed else None
                    else:
                        _need(stored is not None and type(row) is er.RegistryEntry
                              and _row(row) == stored and row.platform == 'template'
                              and row.disabled_by is None and row.config_entry_id == entry_id
                              and row.unique_id == entity.unique_id
                              and entity.registry_entry is row and hass.states.get(key) is not None,
                              'native_registry_identity_unverified')
                    if entry is not None:
                        _need(type(entry) is ConfigEntry and entry.domain == 'template'
                              and entry.state.value == 'loaded' and entry.disabled_by is None
                              and hass.config_entries.async_get_entry(entry_id) is entry,
                              'native_entry_identity_unverified')
                    semantic, template, slot = (_live_sensor(native, entity) if platform.domain == 'sensor'
                                                else _live_binary(native, entity))
                    dependencies, token = _registration(native, entity, template)
                    if platform.domain == 'binary_sensor':
                        _need(entry is None, 'binary_config_entry_unverified')
                        _need(dependencies == _qualified_binary_expression(template.template),
                              'native_binary_dependencies_changed')
                    token += (id(slot), id(slot.on_update), id(entity._template_attrs),
                              id(entity._template_attrs[template]))
                    from .reference_planner import (plan_references, Context, Consumer, Scope, Syntax)
                    static = plan_references(template.template,
                        {key: key for key in dependencies},
                        Context(Consumer.TEMPLATE, (Scope((), Syntax.JINJA),)))
                    _need(not static.markers, 'template_dependency_scope_unverified')
                    if entity.unique_id is None:
                        _physical_native_metadata(native, entity, semantic, platform.domain)
                    consumer = True
                    if entity.unique_id is None:
                        unique = _physical_source_key(semantic, evidence, platform.domain, key=unique)
                        source_verified = True
                    else:
                        source_verified = _source_semantics(native, entity, stored,
                            semantic, evidence, documents, platform=platform.domain)
                    reason = 'verified' if source_verified else 'source_semantics_mismatch'
                except TemplateConsumerError as error:
                    reason = str(error)
                except Exception:
                    reason = 'native_consumer_unavailable'
            rows.append(TemplateConsumerRow('entity', platform.domain, unique, entry_id,
                key, identity, consumer, source_verified,
                None if semantic is None else _fingerprint(semantic), dependencies, reason))
            tokens.append(token)
    for row in registry_rows:
        if row['entity_id'] not in live:
            rows.append(TemplateConsumerRow('registry', row['entity_id'].split('.')[0],
                row['unique_id'], row['config_entry_id'], row['entity_id'], row['id'],
                reason='registry_disabled' if row['disabled_by'] is not None else 'runtime_missing'))
    for row in documents['core.config_entries']['entries']:
        if row['domain'] == 'template':
            entry = hass.config_entries.async_get_entry(row['entry_id'])
            rows.append(TemplateConsumerRow('entry', row['options'].get('template_type', ''),
                row['entry_id'], row['entry_id'], reason='entry_inventory_only'))
            tokens.append(('entry', id(entry)))
    coordinators = hass.data.get(template_component.DATA_COORDINATORS, ())
    _need(type(coordinators) in (list, tuple), 'native_coordinator_inventory_unverified')
    for coordinator in coordinators:
        rows.append(TemplateConsumerRow('coordinator', 'template', None,
            reason='trigger_runtime_unverified'))
        digest = None
        if type(coordinator) is TriggerUpdateCoordinator:
            try:
                digest = _fingerprint(coordinator.config)
            except ValueError:
                pass  # unsupported coordinator remains visible UNKNOWN
        tokens.append(('coordinator', id(coordinator), digest))
    if evidence.template_sources is not None:
        for source in evidence.template_sources.ledger.definitions:
            rows.append(TemplateConsumerRow('source', source.domain, source.native_key,
                                           reason='physical_source_inventory_only'))
    # A physical signature is authoritative only as a bijection over the WHOLE
    # actual stateless population. A partial match never promotes UNKNOWN.
    from .template_physical_identity import is_physical_definition
    physical_rows = [r for r in rows if r.kind == 'entity' and r.registry_id is None]
    if physical_rows:
        source = evidence.template_sources
        expected = {(d.domain.removeprefix('template.'), d.native_key)
                    for d in source.ledger.definitions
                    if d.domain in ('template.sensor', 'template.binary_sensor')} if source else set()
        expected_physical = {(d.domain.removeprefix('template.'), d.native_key)
                             for d in source.ledger.definitions if is_physical_definition(d)} if source else set()
        entities = [r for r in rows if r.kind == 'entity']
        complete = (bool(expected) and len(entities) == len(expected)
            and {(r.platform, r.key) for r in entities} == expected
            and all(r.consumer_verified for r in entities)
            and not any(r.kind in ('registry', 'entry', 'coordinator') for r in rows)
            and all((r.platform, r.key) in expected_physical for r in physical_rows))
        if not complete:
            rows = [replace(r, source_verified=False, reason='yaml_physical_population_unverified')
                    if r in physical_rows else r for r in rows]
    return tuple(rows), tuple(tokens)


async def async_observe_template_consumers(hass, receipt, evidence, *, guard):
    """Two native samples fenced by the SAME ORIGINAL receipt/manager sources.

    Read-only evidence, never a persistent witness/grant or apply readiness.
    Permission/fence/code loss refuses; unsupported consumer rows remain visible.
    """
    from .yaml_source_binding import OriginalSourceEvidence
    from .native_discovery_inputs import (async_observe_discovery_inputs,
                                          assert_discovery_managers_current)
    from .yaml_validation import _settled_executor, assert_validation_current
    _need(type(evidence) is OriginalSourceEvidence, 'original_source_evidence_required')
    def check():
        value = guard()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            _need(False, 'synchronous_guard_required')
        _need(value is None or value is True, 'guard_rejected')
        evidence.assert_receipt(receipt)
    check()
    native = _Native(hass)
    _need(receipt.version == native.version, 'native_version_changed')
    await _settled_executor(hass, native.sources)
    check()
    observed = await async_observe_discovery_inputs(hass, guard=check)
    files = {row[0]: row[1:] for row in receipt.source_files}
    for row in observed.files:
        _need(files.get('.storage/' + row.key) ==
              (hashlib.sha256(row.raw).hexdigest(), *row.identity[:2]),
              'original_native_sources_changed')
    await _settled_executor(hass, assert_validation_current, receipt)
    check()
    native.bindings()
    first = _sample(native, evidence, observed)
    await asyncio.sleep(0)
    check()
    def final_sources():
        native.sources()
        # ORIGINAL physical sources close every preceding executor await.
        # The event-loop suffix compares guards/managers/bindings synchronously.
        assert_validation_current(receipt)
    await _settled_executor(hass, final_sources)
    check()
    assert_discovery_managers_current(hass, observed, guard=check)
    native.bindings()
    second = _sample(native, evidence, observed)
    _need(first == second, 'native_consumer_observation_changed')
    return TemplateConsumers(native.version, evidence.sha256, *second)
