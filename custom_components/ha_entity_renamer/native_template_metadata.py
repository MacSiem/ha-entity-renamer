"""Pinned native pure metadata slots and tracker graph, without rendering.

The source checker supplies versioned original bodies; this adapter proves the
actual constructors, distinct callbacks, complete slot population and listeners.
"""
from __future__ import annotations

from collections import defaultdict
from functools import partial
from types import FunctionType

from .yaml_validation_worker import (_qualified_metadata_expression,
    _qualified_binary_expression, _qualified_state_expression)


def _closures(callback, parent, qualifier, expected):
    from .native_template_consumers import _need, _codes
    _need(type(callback) is FunctionType and callback.__globals__ is parent.__globals__
          and callback.__qualname__ == qualifier
          and any(callback.__code__ is code for code in _codes(parent.__code__))
          and callback.__defaults__ is None and callback.__kwdefaults__ is None,
          'native_metadata_callback_changed')
    values = dict(zip(callback.__code__.co_freevars,
        (cell.cell_contents for cell in callback.__closure__ or ()), strict=True))
    _need(set(values) == set(expected) and all(values[key] is value
          if key == 'self' else values[key] == value for key, value in expected.items()),
          'native_metadata_callback_changed')


def metadata_slots(native, entity, cfg, state_template, state_slot):
    from homeassistant.components.template import template_entity as te
    from homeassistant.helpers.template import Template
    from .native_template_consumers import _need, _bound
    # Reload reconstructs a state-based entity. Its latched outputs while
    # unavailable have no qualified transfer yet: refuse BEFORE the first effect.
    # The pure source language remains admitted; this is a live-state boundary.
    _need(cfg.get('availability') is None or entity._attr_available is True,
          'template_unavailable_reload_outputs_unqualified')
    expected = [(state_template, state_slot._attribute, 'state', None)]
    for option, attribute in (('availability', '_attr_available'),
                              ('icon', '_attr_icon'), ('picture', '_attr_entity_picture')):
        if (template := cfg.get(option)) is not None:
            expected.append((template, attribute, option, None))
    for key, template in (cfg.get('attributes') or {}).items():
        expected.append((template, key, 'attribute', key))
    attrs = entity._template_attrs
    _need(type(attrs) is dict
          and all(type(slots) is list and all(type(slot) is te._TemplateAttribute
                  for slot in slots) for slots in attrs.values())
          and set(attrs) == {row[0] for row in expected}
          and sum(len(slots) for slots in attrs.values()) == len(expected)
          and entity._availability_template is cfg.get('availability')
          and entity._attr_device_info is None and entity.should_poll is False,
          'native_metadata_slots_changed')
    seen = set()
    for template, attribute, option, key in expected:
        _need(type(template) is Template and template.hass is native.hass,
              'native_metadata_template_changed')
        candidates = [slot for slot in attrs[template] if slot._attribute == attribute]
        _need(len(candidates) == 1, 'native_metadata_slot_changed')
        slot = candidates[0]
        _need(id(slot) not in seen and type(slot) is te._TemplateAttribute
              and slot._entity is entity and slot.template is template
              and slot.async_update is None and slot.none_on_template_error is False,
              'native_metadata_slot_changed')
        seen.add(id(slot))
        if option == 'state':
            _need(slot is state_slot, 'native_metadata_state_changed')
            continue
        _qualified_metadata_expression(template.template)
        if option == 'availability':
            boolean = native.boolean_module
            _need(te.result_as_boolean is native.template_module.result_as_boolean
                  and te.result_as_boolean is boolean.result_as_boolean
                  and boolean.result_as_boolean.__globals__ is vars(boolean)
                  and vars(boolean).get('forgiving_boolean') is boolean.forgiving_boolean
                  and boolean.forgiving_boolean.__globals__ is vars(boolean),
                  'native_availability_boolean_binding_changed')
            _need(slot.validator is None
                  and _bound(slot.on_update, entity, te.TemplateEntity._update_available),
                  'native_availability_callback_changed')
        elif option == 'attribute':
            _need(slot.validator is None, 'native_attribute_validator_changed')
            _closures(slot.on_update, te.TemplateEntity._add_attribute_template,
                'TemplateEntity._add_attribute_template.<locals>._update_attribute',
                {'self': entity, 'attribute_key': key})
        else:
            _need(_bound(slot.on_update, slot, te._TemplateAttribute._default_update),
                  'native_metadata_default_update_changed')
            if option == 'picture':
                _need(slot.validator is native.cv.string, 'native_picture_validator_changed')
            else:
                native.icon_proof.validator(slot.validator)
        # Compare real native results with the actual target fields. Reading
        # declared bodies alone would admit customized or overwritten outputs.
        # No conversion oracle or replacement validator is executed here.
        from .yaml_validation_worker import _fingerprint
        info = entity._template_result_info
        _need(info is not None and template in info._last_result,
              'native_metadata_result_missing')
        result = info._last_result[template]
        if option == 'availability':
            _need(type(result) is bool and entity._attr_available is result,
                  'native_availability_result_unverified')
        elif option == 'attribute':
            _need(key in entity._attr_extra_state_attributes
                  and _fingerprint(entity._attr_extra_state_attributes[key]) == _fingerprint(result),
                  'native_metadata_output_changed')
        else:
            _need(isinstance(result, str) and getattr(entity, attribute) == result,
                  'native_metadata_output_unverified')
        if native.version != '2025.6.0' and option != 'attribute':
            declared = entity._templates[option]
            _need(declared.attribute == attribute and declared.template is template
                  and declared.validator is slot.validator
                  and declared.none_on_template_error is False
                  and (declared.on_update is None if option in ('icon', 'picture')
                       else _bound(declared.on_update, entity, te.TemplateEntity._update_available)),
                  'native_metadata_declaration_changed')


def _dependencies(template, state, binary):
    if template is state:
        if binary:
            return _qualified_binary_expression(template.template)
        _qualified_state_expression(template.template)
    return _qualified_metadata_expression(template.template)


def registration(native, entity, state_template):
    """Whole available native group, including metadata with no dependencies.

    Availability's native super-template ordering remains mandatory. The current
    unavailable/held-output boundary is declined by metadata_slots before IO.
    """
    from homeassistant.core import HassJob
    from homeassistant.helpers import event
    from homeassistant.helpers.template import TemplateStateFromEntityId, RenderInfo
    from homeassistant.components.template.template_entity import TemplateEntity
    from .native_template_consumers import _need, _bound
    from .yaml_runtime_observer import _registered_bus_remove
    from .reference_planner import plan_references, Context, Consumer, Scope, Syntax
    hass, info = native.hass, entity._template_result_info
    availability = entity._availability_template
    _need(type(info) is event.TrackTemplateResultInfo and info.hass is hass
          and type(info._job) is HassJob
          and _bound(info._job.target, entity, TemplateEntity._handle_results)
          and info._has_super_template is (availability is not None)
          and not info._time_listeners, 'native_tracker_owner_changed')
    tracks = info._track_templates
    templates = tuple(entity._template_attrs)
    _need(len(tracks) == len(templates)
          and {id(track.template) for track in tracks} == {id(template) for template in templates}
          and (availability is None or tracks[0].template == availability),
          'native_tracked_template_changed')
    declared, rendered, tokens = set(), set(), []
    _need(type(info._info) is dict
          and {id(template) for template in info._info} == {id(template) for template in templates}
          and (availability is None or availability in info._info)
          and set(info._info) == set(templates)
          and set(info._last_result) == set(templates)
          and type(entity._attr_available) is bool,
          'native_render_info_changed')
    variables = None
    for track in tracks:
        template = track.template
        _need(type(track) is event.TrackTemplate and track.rate_limit is None,
              'native_tracked_template_changed')
        current = track.variables
        _need(type(current) is dict and set(current) == {'this'}
              and type(current['this']) is TemplateStateFromEntityId
              and current['this']._hass is hass
              and current['this']._entity_id == entity.entity_id
              and (variables is None or current is variables), 'native_template_variables_changed')
        variables = current
        allowed = _dependencies(template, state_template, type(entity) is native.binary)
        declared.update(allowed)
        static = plan_references(template.template, {key: key for key in allowed},
            Context(Consumer.TEMPLATE, (Scope((), Syntax.JINJA),)))
        _need(not static.markers, 'template_dependency_scope_unverified')
        slots = entity._template_attrs[template]
        tokens.append((id(track), id(template), id(current), id(slots), tuple((id(slot), id(slot.on_update),
            id(slot.validator)) for slot in slots)))
        if template not in info._info:
            continue
        render = info._info[template]
        _need(type(render) is RenderInfo and render.template is template
              and render.exception is None and not render.all_states
              and not render.all_states_lifecycle and not render.domains
              and not render.domains_lifecycle and not render.has_time
              and render.rate_limit is None and set(render.entities) <= set(allowed)
              and render.is_static is template.is_static,
              'native_metadata_dependencies_unverified')
        if template.is_static:
            # Native _freeze_static deliberately retains initial true predicates;
            # it has no keyed dependency, lifecycle registration or timer.
            _need(not render.entities and render.filter is native.render_module._true
                  and render.filter_lifecycle is native.render_module._true,
                  'native_static_predicate_changed')
        else:
            _need(((_bound(render.filter, render, RenderInfo._filter_entities))
                   if render.entities else render.filter is native.render_module._false)
                  and render.filter_lifecycle is native.render_module._false,
                  'native_render_predicate_changed')
        rendered.update(render.entities)
        tokens.append((id(render), id(render.filter), id(render.filter_lifecycle),
                       tuple(sorted(render.entities))))
    filtered = info._track_state_changes
    _need(type(filtered) is event._TrackStateChangeFiltered and filtered.hass is hass
          and _bound(filtered._action, info, event.TrackTemplateResultInfo._refresh)
          and type(filtered._action_as_hassjob) is HassJob
          and _bound(filtered._action_as_hassjob.target, info, event.TrackTemplateResultInfo._refresh),
          'native_filtered_tracker_changed')
    states = filtered._last_track_states
    dependencies = tuple(sorted(rendered))
    _need(type(states) is event.TrackStates and not states.all_states and not states.domains
          and tuple(sorted(states.entities)) == dependencies,
          'native_dependencies_changed')
    _need(set(filtered._listeners) == ({event._ENTITIES_LISTENER} if dependencies else set()),
          'native_registration_missing')
    bus_token = None
    if dependencies:
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
        tokens.append((id(remove), id(job), id(callbacks)))
    else:
        callbacks = getattr(hass.data.get(event._KEYED_TRACK_STATE_CHANGE.key), 'callbacks', {})
        job = None
    for key, members in callbacks.items():
        for member in members:
            if _bound(member.target, info, event.TrackTemplateResultInfo._refresh):
                _need(key in dependencies and member is job, 'native_registration_extra')
    _need(sum(_bound(callback, info, event.TrackTemplateResultInfo.async_remove)
              for callback in entity._on_remove) == 1, 'native_cleanup_owner_changed')
    return tuple(sorted(declared)), (id(entity), id(info), id(info._job), id(info._info),
        id(entity._template_attrs), id(filtered), id(states), id(filtered._action_as_hassjob),
        tuple(tokens), bus_token)


def physical_metadata(native, entity, semantic, platform):
    """All exposed state attributes and native pure metadata, including unavailable."""
    from .native_template_consumers import _need
    attrs = {'friendly_name': semantic[2]}
    fields = (('unit_of_measurement', 3), ('device_class', 4), ('state_class', 5))
    if platform == 'binary_sensor':
        fields = (('device_class', 3),)
    for name, index in fields:
        if semantic[index] is not None:
            attrs[name] = semantic[index]
    availability, icon, picture, attributes = semantic[-1]
    for body, name, value in ((icon, 'icon', entity._attr_icon),
                             (picture, 'entity_picture', entity._attr_entity_picture)):
        _need(body is not None or value is None, 'native_metadata_output_changed')
        if value is not None:
            attrs[name] = value
    extras = entity._attr_extra_state_attributes
    expected_keys = {key for key, _ in attributes}
    _need(type(extras) is dict and set(extras) <= expected_keys
          and (set(extras) == expected_keys or entity._attr_available is False),
          'native_metadata_attributes_changed')
    attrs.update(extras)
    state = native.hass.states.get(entity.entity_id)
    _need(state is not None and dict(state.attributes) == attrs
          and entity.should_poll is False and entity._attr_device_info is None
          and entity.icon == entity._attr_icon
          and entity.entity_picture == entity._attr_entity_picture
          and entity.available is entity._attr_available
          and ((state.state == 'unavailable') is (not entity._attr_available)),
          'yaml_physical_metadata_unverified')


class IconValidatorProof:
    """Authenticate the native Or dispatch and compiled callable closures.

    Lazy: old state-only consumers do not gain an external validator dependency.
    No validator, Schema, render or native callback is invoked by this proof.
    """
    def __init__(self, native):
        self.native, self.modules, self.expected, self.refs, self.paths = native, {}, {}, [], {}

    def _load(self):
        import hashlib
        from importlib import import_module
        from pathlib import Path
        import sys
        from .native_template_consumers import _need, _codes
        version = self.native.version
        _need(version in ('2025.6.0', '2026.10.4'), 'native_icon_version_unverified')
        pins = ({
            'voluptuous.validators': 'c29ddf98aafe282eec6b0f1a7945b0d422cea31c2b723f188ad79724df5e5084',
            'voluptuous.schema_builder': '403b79a3566d2dda93b4e779215cca7333413dfb4b28693bec2cf85052540f48',
        } if version == '2025.6.0' else {
            'probatio.validators.combinators': '504c2a20c01c24fc121b017e98cf138a80c914f618e1a680beadf42eaeaca124',
            'probatio._compile': 'b6952a4932183ce2b51d10729de9910d276d80f41e96c89df754849a1828297a',
        })
        for name, digest in pins.items():
            module = import_module(name)
            path = Path(module.__file__)
            raw = path.read_bytes()
            _need(path.resolve() == path and hashlib.sha256(raw).hexdigest() == digest,
                  'native_icon_validator_source_changed')
            self.modules[name] = module
            self.paths[name] = (path, digest)
            for code in _codes(compile(raw, str(path), 'exec', dont_inherit=True,
                                      optimize=sys.flags.optimize)):
                self.expected[name, code.co_qualname] = code
        if version == '2025.6.0':
            validators = self.modules['voluptuous.validators']
            schema = self.modules['voluptuous.schema_builder']
            self.cls = validators.Any
            _need(validators.Or is self.cls and validators.Schema is schema.Schema,
                  'native_icon_validator_alias_changed')
            refs = [(validators._WithSubValidators, '__call__'), (self.cls, '_exec'),
                (schema.Schema, '__init__'), (schema.Schema, '__call__'),
                (schema.Schema, '_compile'), (schema, '_compile_scalar')]
        else:
            validators = self.modules['probatio.validators.combinators']
            self.cls = validators.Any
            refs = [(self.cls, '__call__'), (validators, '_run_any')]
        for owner, name in refs:
            function = getattr(owner, name)
            self._function(function, function.__module__, function.__qualname__)
            self.refs.append((owner, name, function, function.__code__))
        self.bindings()

    def _function(self, value, module, qualifier):
        from .native_template_consumers import _need
        _need(type(value) is FunctionType and value.__globals__ is vars(self.modules[module])
              and value.__code__ == self.expected[module, qualifier]
              and value.__code__.co_filename == str(self.paths[module][0])
              and value.__code__.co_qualname == qualifier,
              'native_icon_validator_code_changed')

    def sources(self):
        import hashlib
        from .native_template_consumers import _need
        for path, digest in self.paths.values():
            _need(path.resolve() == path and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                  'native_icon_validator_source_changed')

    def bindings(self):
        from .native_template_consumers import _need
        for owner, name, function, code in self.refs:
            _need(getattr(owner, name) is function and function.__code__ is code,
                  'native_icon_validator_binding_changed')

    def validator(self, value):
        from homeassistant.components.template import template_entity as te
        from .native_template_consumers import _need
        if not self.modules:
            self._load()
        self.bindings()
        api = te.vol if self.native.version == '2025.6.0' else te.probatio
        _need(api.Or is self.cls and type(value) is self.cls
              and tuple(value.validators) == (self.native.cv.whitespace, self.native.cv.icon)
              and value.msg is None and value.required is False,
              'native_icon_validator_changed')
        if self.native.version == '2025.6.0':
            _need(set(vars(value)) == {'validators', 'msg', 'required', 'discriminant'}
                  and type(value.validators) is tuple and value.discriminant is None,
                  'native_icon_validator_changed')
            return
        _need(set(vars(value)) == {'validators', 'msg', 'required', '_extra', '_compiled',
                  '_types', '_float_superset', '_expected'}
              and type(value.validators) is list and type(value._compiled) is list
              and len(value._compiled) == 2 and type(value._extra) is int and value._extra == 0
              and value._types is None and value._float_superset is None and value._expected is None,
              'native_icon_compiled_validator_changed')
        for compiled, expected in zip(value._compiled, value.validators, strict=True):
            self._function(compiled, 'probatio._compile', '_compile_callable.<locals>.validate')
            _need(compiled.__defaults__ is None and compiled.__kwdefaults__ is None,
                  'native_icon_compiled_validator_changed')
            cells = dict(zip(compiled.__code__.co_freevars,
                (cell.cell_contents for cell in compiled.__closure__ or ()), strict=True))
            _need(set(cells) == {'schema'} and cells['schema'] is expected,
                  'native_icon_compiled_validator_changed')
