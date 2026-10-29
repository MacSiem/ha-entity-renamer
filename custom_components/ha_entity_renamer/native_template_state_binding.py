"""Read-only SAME ORIGINAL state-v3 source-to-live binding and prefix pairs.

No render, native setup, registry effect, receipt, authority or cache adoption.
Physical file position and runtime registry prefix are independent coordinates.
"""
from __future__ import annotations
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from collections import defaultdict
from functools import partial
import hashlib
import inspect
import json

from .template_state_program import StateConfigProgram, _get, _pairs, _digest, _need
from .native_template_binding_transport import _Runtime, BindingSourcePair
from .native_template_consumers import _bound
from .yaml_validation_worker import _normal, _fingerprint


class _StateRuntime(_Runtime):
    def __init__(self, hass):
        super().__init__(hass)
        from homeassistant.helpers.template import RenderInfo
        from .native_unload_code_proof import prove_methods
        event = self.event
        self.state_refs = [(RenderInfo, '_filter_domains_and_entities'),
            (RenderInfo, '_filter_lifecycle_domains'),
            (event._TrackStateChangeFiltered, '_state_added'),
            (event, '_async_track_state_added_domain'),
            (self.ScriptVariables, 'as_dict')]
        self.state_methods, self.state_codes = prove_methods(
            self.native.root, self.native.pins, self.state_refs)

    def bindings(self):
        super().bindings()
        if hasattr(self, 'state_refs'):
            from .native_unload_code_proof import unchanged
            unchanged(self.state_refs, self.state_methods, self.state_codes)


@dataclass(frozen=True)
class StateBindingPrefix:
    program_sha256: str
    operation_id: str
    fingerprint: str
    head: str
    active: frozenset[str]
    registry_ids: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RenderedVariablePair:
    variables: object
    name: str
    before: object
    after: object
    source_identity: tuple


@dataclass(frozen=True)
class ConfiguredVariablePair:
    container: object
    name: str
    before: str
    after: str
    source_identity: tuple


@dataclass(frozen=True)
class StateBindingTransition:
    entities: tuple
    source_pairs: tuple[BindingSourcePair, ...]
    configured_variables: tuple[ConfiguredVariablePair, ...]
    rendered_variables: tuple[RenderedVariablePair, ...]
    target_semantics: tuple


@dataclass(frozen=True)
class StateEntityBinding:
    domain: str
    source_key: str
    entity: object
    registry_id: str | None
    semantic: object
    static_variables: tuple
    templates: tuple  # (actual Template, all matching source config paths)
    token: tuple
    binding_token: tuple


@dataclass(frozen=True)
class StateConsumerSnapshot:
    prefix: StateBindingPrefix
    yaml_position: str
    rows: tuple[StateEntityBinding, ...]
    inventory: tuple
    token: tuple

    def same_bindings(self, other):
        """Compare retained source ownership, allowing ordinary native renders.

        Both samples independently authenticate the complete native graph.
        RenderInfo/cache/this values may advance between IO fences. Tracker,
        configured Template, slot, variable-container and entity ownership may
        not change without the corresponding accepted registry transition.
        """
        def bindings(sample):
            return tuple((row.domain, row.source_key, row.registry_id,
                row.semantic, row.static_variables, row.binding_token,
                tuple((id(template), paths) for template, paths in row.templates))
                for row in sample.rows)
        return (type(other) is StateConsumerSnapshot and self.prefix == other.prefix
            and self.yaml_position == other.yaml_position and self.inventory == other.inventory
            and bindings(self) == bindings(other))

    @property
    def migration_ready(self):
        return False

    def transition_pairs(self, program, target):
        """Pure expected pairs for ONE accepted registry transition.

        Target is an expected prefix derived by the root from its one prepared
        step, never a receipt completion or accepted state installed by this API.
        """
        _need(type(program) is StateConfigProgram and program.sha256 == self.prefix.program_sha256
              and type(target) is frozenset and len(target ^ self.prefix.active) == 1,
              'template_state_single_registry_step_required')
        desired = {(d, k): (cfg, variables) for d, k, cfg, variables in program.rows(target)}
        result, variables, configured, entities, semantics = [], [], [], [], []
        for row in self.rows:
            semantic, static = desired[row.domain, row.source_key]
            target_templates = dict(_templates(_effective_config(semantic)))
            changed = semantic != row.semantic
            if not changed:
                continue
            entities.append(row.entity)
            semantics.append((row.domain, row.source_key, semantic))
            location = next(value for value in program.source_locations
                            if value[:2] == (row.domain, row.source_key))
            before_config = _get(_effective_config(row.semantic), 'variables')
            after_config = _get(_effective_config(semantic), 'variables')
            if before_config != after_config:
                from homeassistant.helpers.script_variables import ScriptVariables
                container = row.entity._run_variables
                if type(container) is ScriptVariables:
                    container = container.as_dict()
                _need(isinstance(container, dict), 'template_state_configured_variable_container_changed')
                before_variables = dict(_pairs(before_config.get('$variables', before_config)))
                after_variables = dict(_pairs(after_config.get('$variables', after_config)))
                _need(set(before_variables) == set(after_variables),
                      'template_state_configured_variable_inventory_changed')
                for name, before in before_variables.items():
                    after = after_variables[name]
                    if before != after:
                        _need(type(before) is str and type(after) is str,
                              'template_state_variable_transport_shape_unqualified')
                        _need(name in container and container[name] == before,
                              'template_state_configured_variable_source_changed')
                        configured.append(ConfiguredVariablePair(container, name, before, after,
                            (program.sha256, *location, 'variables', name, program.variable_locators_json)))
            for template, paths in row.templates:
                bodies = {target_templates[path] for path in paths}
                _need(len(bodies) == 1, 'template_state_equal_group_projection_collision')
                after = bodies.pop()
                if after != template.template:
                    result.append(BindingSourcePair(template, template.template, after,
                        (program.sha256, *location, paths)))
            before_values, after_values = dict(row.static_variables), dict(static)
            _need(set(before_values) == set(after_values), 'template_state_variable_inventory_changed')
            for track in row.entity._template_result_info._track_templates:
                for name, before in before_values.items():
                    _need(name in track.variables and track.variables[name] == before,
                          'template_state_static_rendered_variable_changed')
                    after = after_values[name]
                    if before != after:
                        _need(type(before) is str and type(after) is str,
                              'template_state_variable_transport_shape_unqualified')
                        variables.append(RenderedVariablePair(track.variables, name, before, after,
                            (program.sha256, *location, 'variables', name, program.variable_locators_json)))
        # Shared native variables must have one congruent projection.
        seen = {}
        for pair in variables:
            key = (id(pair.variables), pair.name)
            _need(key not in seen or seen[key] == (pair.before, pair.after),
                  'template_state_shared_variables_collision')
            seen[key] = (pair.before, pair.after)
        return StateBindingTransition(tuple(entities), tuple(result), tuple(configured),
            tuple(variables), tuple(semantics))


def _state_inputs(chain, input_state=None, *, live_intent=None, live_context=None, live_calls=()):
    """Admit acknowledged state, or EXACT current live_yaml_inputs evidence.

    Default reads still refuse every pending intent. An explicitly held accepted
    state can survive ONLY its SAME active registry intent via its original
    evidence guard. YAML partial calls require the dedicated live hook below.
    """
    from .accepted_input_state import AcceptedInputState
    if input_state is None:
        _need(live_intent is None and live_context is None and not live_calls,
              'template_state_live_input_binding_required')
        return chain.reconstruct_inputs()
    _need(type(input_state) is AcceptedInputState,
          'template_state_input_type_required')
    if input_state._live_check is None:
        _need(live_intent is None and live_context is None and not live_calls,
              'template_state_accepted_head_changed')
        states, head, _, _, pending = chain._reconstruct()
        if pending is None:
            _need(input_state.same_evidence(chain.reconstruct_inputs()),
                  'template_state_accepted_head_changed')
        else:
            # This is a held pre-intent state, never a fresh reconstruction.
            # _check_inputs_head owns the exact writable/current active-intent
            # fence; it permits registry only here, not YAML or helper setup.
            _need(input_state._evidence_check is not None and input_state.head == head
                  and chain.store.read(pending[0])['payload']['kind'] == 'registry_intent',
                  'template_state_held_registry_state_required')
            chain._check_inputs_head(input_state.head)
            input_state._bound(chain.original_receipt)
            input_state.assert_current(chain.original_receipt)
    else:
        _need(live_intent is not None and live_context is not None,
              'template_state_live_input_binding_required')
        actual = chain.live_yaml_inputs(live_intent, live_context, live_calls)
        # same_evidence deliberately refuses live objects. Match all evidence
        # only against a newly validated SAME active intent, never cached state.
        _need(type(actual) is AcceptedInputState and actual._live_check is not None
              and input_state.original_json == actual.original_json
              and input_state.head == actual.head
              and input_state.sources() == actual.sources()
              and input_state.documents() == actual.documents(),
              'template_state_live_input_changed')
        actual._bound(chain.original_receipt)
    input_state._bound(chain.original_receipt)
    _need(input_state.documents() == chain.accepted_documents(),
          'template_state_accepted_documents_changed')
    return input_state


def accepted_state_prefix(chain, program, *, input_state=None, live_intent=None,
                          live_context=None, live_calls=()):
    """Derive mask from immutable prepared registry UUIDs + SAME receipt chain."""
    from .native_registry_receipts import RegistryReceiptChain
    from .native_registry import PreparedRegistryChange
    _need(type(chain) is RegistryReceiptChain and type(program) is StateConfigProgram)
    state = _state_inputs(chain, input_state, live_intent=live_intent,
                          live_context=live_context, live_calls=live_calls)
    documents = chain.accepted_documents()
    registry = documents['core.entity_registry']['data']['entities']
    known, active, ids = {}, set(), []
    for index, step in enumerate(chain.plan.steps):
        if step.adapter != 'registry':
            continue
        change = chain._original_change(index)
        _need(type(change) is PreparedRegistryChange)
        for old, new in change.mapping.items():
            _need(old not in known, 'template_state_registry_mapping_duplicate')
            known[old] = new
            matches = [row for row in registry if row['id'] == change.identity]
            _need(len(matches) == 1 and matches[0]['entity_id'] in (old, new),
                  'template_state_accepted_registry_identity_changed')
            if matches[0]['entity_id'] == new:
                active.add(old)
            ids.append((change.identity, matches[0]['entity_id']))
    _need(known == dict(program.mapping), 'template_state_original_mapping_changed')
    return StateBindingPrefix(program.sha256, chain.operation_id, chain.plan.fingerprint,
        state.head, frozenset(active), tuple(sorted(ids)))


def _templates(value, path=()):
    if type(value) is dict and set(value) == {'$template'}:
        yield path, value['$template']
    elif type(value) is dict and set(value) == {'$map'}:
        for key, child in _pairs(value):
            yield from _templates(child, path + (key,))
    elif type(value) is dict and set(value) == {'$variables'}:
        yield from _templates(value['$variables'], path + ('$variables',))
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _templates(child, path + (index,))


def _objects(value, native, path=()):
    from collections.abc import Mapping
    if type(value) is native.template.Template:
        yield path, value
    elif type(value) is native.ScriptVariables:
        yield from _objects(value.as_dict(), native, path + ('$variables',))
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _objects(child, native, path + (key,))
    elif type(value) in (list, tuple):
        for index, child in enumerate(value):
            yield from _objects(child, native, path + (index,))


# Constructor source-field projection for the minimum core, which does not
# retain _config. This maps schema fields to native storage, never operators.
_MINIMUM_FIELDS = {'state': '_template', 'name': '_friendly_name_template',
    'availability': '_availability_template', 'icon': '_icon_template',
    'picture': '_entity_picture_template', 'attributes': '_attribute_templates',
    'variables': '_run_variables', 'last_reset': '_attr_last_reset_template',
    'unit_of_measurement': '_attr_native_unit_of_measurement',
    'device_class': '_attr_device_class', 'state_class': '_attr_state_class',
    'delay_on': '_delay_on_raw', 'delay_off': '_delay_off_raw'}


def _effective_config(semantic):
    # Minimum's pinned loader passes block variables to the state constructor;
    # current schema moves them into each normalized entity. Authenticate the
    # same effective constructor fields without altering either sealed source.
    cfg = deepcopy(semantic[3])
    block_variables = _get(semantic[2], 'variables')
    if _get(cfg, 'variables') is None and block_variables is not None:
        cfg['$map'].append(['variables', deepcopy(block_variables)])
    return cfg


def _configuration(runtime, entity, expected):
    native = runtime.native
    expected_values = dict(_pairs(expected))
    if hasattr(entity, '_config'):
        cfg = entity._config
        _need(type(cfg) is dict, 'template_state_native_config_required')
        # Source-pinned discovery adds these diagnostic source fields; they
        # are separately held in tokens and never select the runtime prefix.
        actual = {key: value for key, value in cfg.items()
                  if key not in ('raw_configs', 'raw_blueprint_inputs') or key in expected_values}
        _need(_normal(actual) == expected, 'template_state_full_native_config_changed')
    else:
        _need(native.version == '2025.6.0', 'template_state_full_native_config_missing')
        actual = {}
        for key, value in expected_values.items():
            if key in _MINIMUM_FIELDS:
                field = _MINIMUM_FIELDS[key]
                _need(hasattr(entity, field), 'template_state_native_constructor_field_missing')
                actual[key] = getattr(entity, field)
            elif key == 'unique_id':
                # Block UID prefix is verified by the full entity/source bijection.
                actual[key] = value
            elif key in ('device_id', 'object_id', 'default_entity_id') and value is None:
                actual[key] = None
            else:
                _need(False, 'template_state_minimum_config_field_unverified')
        _need(_normal(actual) == expected, 'template_state_full_native_config_changed')
    return actual


def _configured_variables_binding(runtime, entity, expected):
    """Bind actual constructor variables, separately from rendered snapshots."""
    configured = entity._run_variables
    actual = configured.as_dict() if type(configured) is runtime.ScriptVariables else configured
    _need(type(configured) in (dict, runtime.ScriptVariables) and isinstance(actual, dict),
          'template_state_configured_variables_owner_changed')
    source = _get(expected, 'variables')
    if source is None:
        source = {'$map': []}
    elif type(source) is dict and set(source) == {'$variables'}:
        source = source['$variables']
    _need(_normal(actual) == source, 'template_state_configured_variables_source_changed')
    def owners(value):
        if type(value) is runtime.ScriptVariables:
            return (type(value), id(value), owners(value.as_dict()))
        if type(value) is runtime.template.Template:
            return (type(value), id(value))
        if isinstance(value, dict):
            return (type(value), id(value), tuple((key, owners(child))
                                                 for key, child in value.items()))
        if type(value) in (list, tuple):
            return (type(value), id(value), tuple(owners(child) for child in value))
        return None  # Scalar values are compared above, never rendered here.
    return owners(configured)


def _keyed(runtime, remove, tracker, keys, callback):
    """Authenticate exact keyed callback membership and shared bus dispatcher."""
    from .yaml_runtime_observer import _registered_bus_remove
    event, hass = runtime.event, runtime.native.hass
    _need(type(remove) is partial and remove.func is event._remove_listener
          and not remove.keywords and len(remove.args) == 5,
          'template_state_native_detach_changed')
    owner, actual_tracker, actual_keys, job, callbacks = remove.args
    _need(owner is hass and actual_tracker is tracker and set(actual_keys) == set(keys)
          and len(tuple(actual_keys)) == len(set(keys)) and type(job) is runtime.HassJob
          and _bound(job.target, callback.__self__, callback.__func__),
          'template_state_keyed_owner_changed')
    data = hass.data.get(tracker.key)
    _need(type(data) is event._KeyedEventData and data.callbacks is callbacks
          and type(callbacks) is defaultdict and callbacks.default_factory is list,
          'template_state_keyed_inventory_changed')
    (bus_job, event_filter), bus_token = _registered_bus_remove(hass, data.listener, tracker.event_type)
    for value, function in ((bus_job.target, tracker.dispatcher_callable),
                            (event_filter, tracker.filter_callable)):
        _need(type(value) is partial and value.func is function and not value.keywords
              and len(value.args) == 2 and value.args[0] is hass and value.args[1] is callbacks,
              'template_state_bus_dispatch_changed')
    for key in keys:
        _need(sum(member is job for member in callbacks.get(key, ())) == 1,
              'template_state_keyed_registration_missing')
    for key, members in callbacks.items():
        for member in members:
            if _bound(member.target, callback.__self__, callback.__func__):
                _need(key in keys and member is job, 'template_state_extra_registration')
    return (id(remove), id(job), id(callbacks), bus_token)


def _graph(runtime, entity):
    """Authenticate native graph without treating RenderInfo as source proof."""
    from homeassistant.helpers.template import RenderInfo
    from .yaml_runtime_observer import _registered_bus_remove
    info = runtime.graph(entity)
    event, native = runtime.event, runtime.native
    _need(type(info._info) is dict and set(info._info) == set(entity._template_attrs),
          'template_state_complete_render_group_required')
    tokens = []
    for template, render in info._info.items():
        _need(type(render) is RenderInfo and render.template is template,
              'template_state_native_render_info_changed')
        # Native predicates have version-pinned code and native instance owner.
        for predicate in (render.filter, render.filter_lifecycle):
            _need(predicate in (native.render_module._true, native.render_module._false)
                  or _bound(predicate, render, RenderInfo._filter_entities)
                  or _bound(predicate, render, RenderInfo._filter_domains_and_entities)
                  or _bound(predicate, render, RenderInfo._filter_lifecycle_domains),
                  'template_state_render_predicate_changed')
        tokens.append((id(template), id(render), tuple(sorted(render.entities)),
            tuple(sorted(render.domains or ())), tuple(sorted(render.domains_lifecycle or ())),
            render.all_states, render.all_states_lifecycle, render.has_time))
    # This pinned native helper combines existing RenderInfo sets only. It does
    # not render, evaluate Jinja, produce source evidence or alter native state.
    expected = event._render_infos_to_track_states(info._info.values())
    filtered = info._track_state_changes
    actual = filtered._last_track_states
    _need(type(actual) is event.TrackStates and actual == expected,
          'template_state_trackstates_changed')
    listeners = filtered._listeners
    wanted = set()
    if expected.all_states:
        wanted.add(event._ALL_LISTENER)
        (job, predicate), token = _registered_bus_remove(native.hass,
            listeners.get(event._ALL_LISTENER), 'state_changed')
        _need(_bound(job.target, info, event.TrackTemplateResultInfo._refresh)
              and predicate is None, 'template_state_all_listener_changed')
        tokens.append(token)
    else:
        domains, entities = set(expected.domains), set(expected.entities)
        if domains:
            wanted.add(event._DOMAINS_LISTENER)
            tokens.append(_keyed(runtime, listeners.get(event._DOMAINS_LISTENER),
                event._KEYED_TRACK_STATE_ADDED_DOMAIN, domains, filtered._state_added))
            entities.update(native.hass.states.async_entity_ids(domains))
        if entities:
            wanted.add(event._ENTITIES_LISTENER)
            tokens.append(_keyed(runtime, listeners.get(event._ENTITIES_LISTENER),
                event._KEYED_TRACK_STATE_CHANGE, entities, info._refresh))
    _need(set(listeners) == wanted, 'template_state_listener_inventory_changed')
    return (id(info), id(filtered), tuple(tokens),
        tuple((id(track), id(track.template), id(track.variables),
               tuple((key, id(value)) for key, value in track.variables.items()))
              for track in info._track_templates),
        tuple((id(slot), id(slot.template), id(slot.on_update), id(slot.validator))
              for slots in entity._template_attrs.values() for slot in slots),
        tuple((id(t), id(remove)) for t, remove in info._time_listeners.items()),
        id(getattr(entity, '_delay_cancel', None)))


def sample_state_bindings(runtime, program, prefix, yaml_position, documents):
    """Synchronous actual full inventory and native-config bijection sample."""
    from homeassistant.helpers import entity_platform as ep, entity_registry as er
    from homeassistant.components import template as component
    from .native_utility_unload_audit import _row
    native, hass = runtime.native, runtime.native.hass
    _need(type(program) is StateConfigProgram and type(prefix) is StateBindingPrefix
          and prefix.program_sha256 == program.sha256 and yaml_position in ('original', 'proposed'))
    expected = {(d, k): (semantic, static) for d, k, semantic, static in program.rows(prefix.active)}
    original_descriptor = json.loads(program.original_json)
    unsupported = [r for r in original_descriptor['inventory']
                   if (r[0], r[1]) not in expected and r[0] != 'template.state']
    registry = hass.data.get(er.DATA_REGISTRY)
    _need(type(registry) is er.EntityRegistry, 'template_state_native_registry_missing')
    accepted_rows = documents['core.entity_registry']['data']['entities']
    registry_rows = {r['entity_id']: r for r in accepted_rows if r['platform'] == 'template'}
    rows, inventory, matched, live = [], [], set(), set()
    for platform in ep.async_get_platforms(hass, 'template'):
        _need(type(platform) is ep.EntityPlatform and platform.hass is hass
              and all(task.done() for task in platform._tasks), 'template_state_platform_unsettled')
        inventory.append(('platform', platform.domain, id(platform), id(platform.config_entry)))
        for entity_id, entity in platform.entities.items():
            _need(entity.entity_id == entity_id and entity.hass is hass and entity.platform is platform,
                  'template_state_entity_identity_changed')
            live.add(entity_id)
            domain = 'template.' + platform.domain
            if type(entity) not in (native.sensor, native.binary) or platform.config_entry is not None:
                # Unrelated trigger/other families stay present and unchanged.
                # Their full native config/source membership is checked below.
                cfg = getattr(entity, '_config', None)
                if type(cfg) is dict:
                    cfg = {k: v for k, v in cfg.items() if k not in ('raw_configs', 'raw_blueprint_inputs')}
                actual_registry = registry.async_get(entity_id)
                stored_registry = registry_rows.get(entity_id)
                _need(actual_registry is not None and stored_registry is not None
                      and _row(actual_registry) == stored_registry
                      and entity.registry_entry is actual_registry,
                      'template_state_unaffected_registry_identity_changed')
                matches = [r for r in unsupported if r[0] == domain
                    and entity.unique_id == r[1] and len(r[2]) == 3
                    and _normal(cfg) == r[2][2]]
                _need(len(matches) == 1 and (domain, entity.unique_id) not in matched,
                      'template_state_unsupported_runtime_inventory_unverified')
                matched.add((domain, entity.unique_id))
                inventory.append(('unaffected', domain, entity_id, id(entity), _fingerprint(cfg)))
                continue
            runtime.bindings()
            graph_token = _graph(runtime, entity)
            candidates = []
            for (source_domain, key), (semantic, static) in expected.items():
                if (source_domain != domain
                        or ((domain, key) in program.physical_keys) != (entity.unique_id is None)
                        or (entity.unique_id is not None and key != entity.unique_id)):
                    continue
                try:
                    cfg = _configuration(runtime, entity, _effective_config(semantic))
                except ValueError:
                    continue
                candidates.append((key, semantic, static, cfg))
            _need(len(candidates) == 1, 'template_state_full_native_bijection_unverified')
            key, semantic, static, cfg = candidates[0]
            _need((domain, key) not in matched, 'template_state_full_native_bijection_ambiguous')
            matched.add((domain, key))
            configured_owner = _configured_variables_binding(runtime, entity, _effective_config(semantic))
            entry = registry.async_get(entity_id)
            stored = registry_rows.get(entity_id)
            if entity.unique_id is None:
                _need(entry is None and stored is None and entity.registry_entry is None
                      and (domain, key) in program.physical_keys,
                      'template_state_registryless_identity_changed')
            else:
                _need(type(entry) is er.RegistryEntry and stored is not None and _row(entry) == stored
                      and entity.registry_entry is entry and entry.unique_id == key
                      and entry.disabled_by is None and entry.config_entry_id is None,
                      'template_state_registry_identity_changed')
            expected_templates = dict(_templates(_effective_config(semantic)))
            template_objects = defaultdict(lambda: [None, []])
            for path, template in _objects(cfg, runtime):
                _need(path in expected_templates and template.template == expected_templates[path],
                      'template_state_source_slot_changed')
                value = template_objects[id(template)]
                value[0] = template; value[1].append(path)
            for group, slots in entity._template_attrs.items():
                for template in (group, *(slot.template for slot in slots)):
                    if id(template) not in template_objects:
                        paths = [path for path, body in expected_templates.items() if body == template.template]
                        _need(bool(paths), 'template_state_unconfigured_slot')
                        template_objects[id(template)] = [template, paths]
            for track in entity._template_result_info._track_templates:
                for name, value in static:
                    _need(name in track.variables and track.variables[name] == value,
                          'template_state_static_rendered_variable_changed')
            rows.append(StateEntityBinding(domain, key, entity, None if entry is None else entry.id,
                semantic, static, tuple((v[0], tuple(v[1])) for _, v in sorted(template_objects.items())),
                (id(entity), id(entry), graph_token, _fingerprint(cfg)),
                (id(entity), id(entry), graph_token[:2],
                 tuple(track[:3] for track in graph_token[3]), graph_token[4],
                 _fingerprint(cfg), configured_owner)))
    _need(set(registry_rows) <= live, 'template_state_missing_registry_consumer')
    expected_unsupported = {(r[0], r[1]) for r in unsupported if r[0] != 'template.trigger'}
    _need(matched == set(expected) | expected_unsupported,
          'template_state_full_population_changed')
    coordinators = hass.data.get(component.DATA_COORDINATORS, ())
    expected_triggers = [r for r in unsupported if r[0] == 'template.trigger']
    _need(len(coordinators) == len(expected_triggers), 'template_state_coordinator_inventory_changed')
    seen = set()
    for coordinator in coordinators:
        hits = [r for r in expected_triggers if _normal(coordinator.config) == r[2]]
        _need(len(hits) == 1 and hits[0][1] not in seen,
              'template_state_coordinator_source_changed')
        seen.add(hits[0][1])
        inventory.append(('coordinator', id(coordinator), hits[0][1], _fingerprint(coordinator.config)))
    return StateConsumerSnapshot(prefix, yaml_position, tuple(rows), tuple(inventory),
        (tuple(row.token for row in rows), tuple(inventory)))


async def async_observe_accepted_state_bindings(hass, chain, program, *, guard,
        input_state=None, live_intent=None, live_context=None, live_calls=()):
    """Two samples, code/source pins and SAME accepted whole-input manager fence."""
    from .native_discovery_inputs import (async_observe_discovery_inputs,
        assert_discovery_managers_current)
    from .native_template_accepted_consumers import _documents
    from .yaml_validation import _settled_executor
    arguments = dict(live_intent=live_intent, live_context=live_context, live_calls=live_calls)
    state = _state_inputs(chain, input_state, **arguments)
    prefix = accepted_state_prefix(chain, program, input_state=state, **arguments)
    receipt = chain.original_receipt
    def check():
        result = guard()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            _need(False, 'template_state_synchronous_guard_required')
        _need(result is None or result is True, 'template_state_guard_rejected')
        _state_inputs(chain, state, **arguments)
        _need(prefix == accepted_state_prefix(chain, program, input_state=state, **arguments),
              'template_state_accepted_head_changed')
    check()
    runtime = _StateRuntime(hass)
    _need(runtime.native.version == receipt.version, 'template_state_native_version_changed')
    await _settled_executor(hass, runtime.sources)
    check()
    observed = await async_observe_discovery_inputs(hass, guard=check)
    check(); _documents(state, observed)
    original = {p: d for p, d, *_ in receipt.source_files if not p.startswith('.storage/')}
    proposed = original | dict(receipt.proposed_files)
    current = {p: r[0] for p, r in state.sources().items() if not p.startswith('.storage/')}
    _need(current in (original, proposed), 'template_state_physical_yaml_image_changed')
    position = 'original' if current == original else 'proposed'
    first = sample_state_bindings(runtime, program, prefix, position, chain.accepted_documents())
    await asyncio.sleep(0)
    check()
    await _settled_executor(hass, runtime.sources)
    check()
    await _settled_executor(hass, state.assert_current, receipt)
    check()
    assert_discovery_managers_current(hass, observed, guard=check)
    _documents(state, observed); runtime.bindings()
    second = sample_state_bindings(runtime, program, prefix, position, chain.accepted_documents())
    _need(first.same_bindings(second), 'template_state_native_observation_changed')
    check()
    return second
