"""Internal synchronous same-entity Template binding transport.

This is a data-plane operation, never an authorization API. A root caller must
validate original/desired source pairs, its complete affected population and live
operation intent. It keeps its existing event/registry fence through this call
and the immediately following registry mutation, without yielding the HA loop.

Actual old _last_result and rate timestamps are migration DATA, not generated
native evidence. Every new RenderInfo is created by native setup. Setup uses the
explicit product policy full_slot_dependency_setup: availability is first, native
setup initially renders every slot with has_super_template=False, then the real
super flag is installed before dispatch. This prevents missing RenderInfo when
the new source ID is absent. No refresh, handler, output write or reload occurs.
"""
from __future__ import annotations

import asyncio
from contextvars import Context
import math
from dataclasses import dataclass
from importlib import import_module
import inspect
import sys
from pathlib import Path
from types import FunctionType, MethodType

from .native_template_consumers import _Native, _bound, _need
from .native_unload_code_proof import prove_methods, unchanged

# Full installed PUBLIC source readback, never supplied by a client.
_EXTRA_PINS = {
    '2025.6.0': {
        'helpers/ratelimit.py': '1a858c6d25b1a2311823b22f6c0aa9f63538ad00d7c4383a9be87395b932b651',
        'helpers/script_variables.py': '14267717ecef25bda034e0b66ec2e2b9d1e540d11578a0917ca6f65c30dfe061'},
    '2026.10.4': {
        'helpers/ratelimit.py': '9a1a1dbbc416b0b2156e1dc021e205f0e8f3bfcb46e87e33d4543fb4ccf3a005',
        'helpers/script_variables.py': 'c70c73cdd5979f05fd16997762c4cddb9a0dd1bfb4b7f23d7a11c4ec325bf9ea',
        'helpers/template/states.py': '482640b98dc1aa8b168e2bd5d3bd5adf7e1c151c3511d1fe6022258c28205803',
        'helpers/template/__init__.py': 'cb26b93a52a25160072ac7a987f79cc8c26534abf5522461850e8ca30dfe4bd4'},
}

# Binding-bearing fields only. Consumer outputs, state, delay owner/token and
# self-reference limiter are deliberately not replaced or restored.
_FIELDS = ('_config', '_template', '_availability_template', '_icon_template',
    '_entity_picture_template', '_friendly_name_template', '_attribute_templates',
    '_attributes_template', '_attr_last_reset_template', '_run_variables',
    '_delay_on_raw', '_delay_off_raw')


@dataclass(frozen=True)
class BindingSourcePair:
    """Internal source-checker output; by itself confers no mutation authority."""
    template: object
    before: str
    after: str
    source_identity: tuple  # caller's actual source location/token, not a client ID


@dataclass(frozen=True)
class BindingVariablePair:
    """Exact root-approved scalar producer location; never authority itself."""
    container: dict
    name: str
    before: str
    after: str
    source_identity: tuple


@dataclass(frozen=True)
class _BindingCallerContext:
    """Root-owned live scope. Never deserialize, return to API, or persist this.

    fence(phase, sources, entities) must synchronously verify the root's accepted
    source identities/full closure, current task, lease, physical inputs and
    actual intent. A false/awaitable result fails before the first detach. This
    module does not issue receipts or grant authority by inspecting this object.
    """
    task: object
    fence: object

    def check(self, phase, sources, entities):
        _need(self.task is asyncio.current_task() and callable(self.fence),
              'template_binding_live_caller_required')
        result = self.fence(phase, sources, entities)
        if inspect.iscoroutine(result):
            result.close()
        _need(result is True, 'template_binding_root_fence_required')


def _callback(native, value, entity, slot=None):
    """Authenticate native code and instance closure, without executing it."""
    if value is None:
        return
    bound_owner = None
    if type(value) is MethodType:
        bound_owner = value.__self__
        owner = value.__self__
        _need(owner is entity or owner is slot, 'template_binding_callback_owner')
        value = value.__func__
    _need(type(value) is FunctionType, 'template_binding_callback_type')
    module = import_module(value.__module__)
    _need(value.__globals__ is vars(module), 'template_binding_callback_globals')
    from .native_utility_setup_audit import _code_objects
    path = Path(value.__code__.co_filename)
    relative = path.relative_to(native.root).as_posix()
    _need(relative in native.pins and any(value.__code__ == code
        and value.__qualname__ == code.co_qualname for code in _code_objects(compile(
            path.read_bytes(), str(path), 'exec', dont_inherit=True,
            optimize=sys.flags.optimize))), 'template_binding_callback_code')
    cells = dict(zip(value.__code__.co_freevars,
        (cell.cell_contents for cell in value.__closure__ or ()), strict=True))
    if '__class__' in cells:
        # Zero-argument super() captures the DEFINING class, not arbitrary
        # per-instance data. These exact pinned native state callbacks are the
        # only admitted class-cell profiles. Keep both their original function
        # identity and the cell value: a source/code-equal clone is insufficient.
        owners = (native.binary,) + ((native.sensor,) if native.version == '2025.6.0' else ())
        defining = next((cls for cls in owners if vars(cls).get('_update_state') is value), None)
        _need(defining is not None and type(entity) is defining
              and bound_owner is entity and set(cells) == {'__class__'}
              and cells['__class__'] is defining,
              'template_binding_callback_class_closure_changed')
        return
    for key, item in cells.items():
        if key == 'self':
            _need(item is entity or item is slot, 'template_binding_callback_closure')
        elif key in ('on_update', 'validator'):
            _callback(native, item, entity, slot)
        elif key in ('attribute', 'attribute_key'):
            _need(slot is not None and item == slot._attribute,
                  'template_binding_callback_attribute')
        else:
            _need(False, 'template_binding_callback_closure_unqualified')


class _Runtime:
    def __init__(self, hass):
        from homeassistant.helpers import event, ratelimit, script_variables, template
        from homeassistant.components.template import template_entity as te
        from homeassistant.core import HassJob, HomeAssistant
        self.native = native = _Native(hass)
        _need(native.version in _EXTRA_PINS, 'template_binding_core_unqualified')
        native.pins.update(_EXTRA_PINS[native.version])
        self.refs = [(event, 'async_track_template_result'),
            (event, 'async_track_state_change_filtered'),
            (event._TrackStateChangeFiltered, 'async_setup'),
            (event._TrackStateChangeFiltered, 'async_update_listeners'),
            (event, '_render_infos_to_track_states'),
            (event, '_suppress_domain_all_in_render_info'),
            (event._TrackStateChangeFiltered, '_setup_all_listener'),
            (event._TrackStateChangeFiltered, '_setup_domains_listener'),
            (event, 'async_track_utc_time_change'),
            (event, 'async_track_point_in_utc_time'),
            (event, 'async_call_later'), (event, '_run_async_call_action'),
            (event._TrackUTCTimeChange, 'async_attach'),
            (event._TrackUTCTimeChange, 'async_cancel'),
            (event._TrackUTCTimeChange, '_calculate_next'),
            (event._TrackUTCTimeChange, '_pattern_time_change_listener'),
            (event._TrackPointUTCTime, 'async_attach'),
            (event._TrackPointUTCTime, 'async_cancel'),
            (event._TrackPointUTCTime, '__call__'),
            (HomeAssistant, 'async_run_hass_job'),
            (event.TrackTemplateResultInfo, '_setup_time_listener'),
            (event.TrackTemplateResultInfo, '_update_time_listeners'),
            (event, '_event_triggers_rerender'), (event, '_rate_limit_for_event'),
            (ratelimit.KeyedRateLimit, '__init__'),
            (ratelimit.KeyedRateLimit, 'async_remove'),
            (ratelimit.KeyedRateLimit, 'async_schedule_action'),
            (ratelimit.KeyedRateLimit, 'async_has_timer'),
            (ratelimit.KeyedRateLimit, 'async_triggered'),
            (ratelimit.KeyedRateLimit, 'async_cancel_timer'),
            (script_variables.ScriptVariables, '__init__'),
            (script_variables.ScriptVariables, 'async_render'),
            (te.TemplateEntity, '_render_script_variables'),
            (template.Template, '__init__'),
            (template.Template, 'async_render_to_info'),
            (template.TemplateStateFromEntityId, '_state')]
        if native.version != '2025.6.0':
            self.refs += [(te.TemplateEntity, '_get_this_variable')]
        self.methods, self.codes = prove_methods(native.root, native.pins, self.refs)
        self.event, self.te, self.template = event, te, template
        self.ScriptVariables = script_variables.ScriptVariables
        self.HassJob = HassJob
        self.RateLimit = ratelimit.KeyedRateLimit
        self.sources()

    def sources(self):
        self.native.sources()
        self.bindings()

    def bindings(self):
        self.native.bindings()
        unchanged(self.refs, self.methods, self.codes)
        event, te = self.event, self.te
        _need(te.TrackTemplate is event.TrackTemplate
              and te.async_track_template_result is event.async_track_template_result
              and event.TrackTemplateResultInfo.__init__.__globals__ is vars(event)
              and event.KeyedRateLimit is self.RateLimit
              and event.HassJob is self.HassJob,
              'template_binding_native_alias_changed')

    def rate_timers(self, tracker):
        """Authenticate the actual scheduled native replay, including its Event.

        Preserve the physical TimerHandle/context/deadline. Only its native
        tracker/TrackTemplate binding is transported at the synchronous seam.
        """
        from homeassistant.core import Event
        timers = tracker._rate_limit._rate_limit_timers
        _need(type(timers) is dict and len({id(h) for h in timers.values()}) == len(timers),
              'template_binding_rate_timer_inventory_changed')
        loop = self.native.hass.loop
        for template, handle in timers.items():
            _need(type(handle) is asyncio.TimerHandle and handle._loop is loop
                  and not handle.cancelled() and math.isfinite(handle.when())
                  and type(handle._context) is Context
                  and sum(item is handle for item in loop._scheduled)
                      + sum(item is handle for item in loop._ready) == 1
                  and _bound(handle._callback, tracker, self.event.TrackTemplateResultInfo._refresh),
                  'template_binding_rate_timer_owner_changed')
            args = handle._args
            _need(type(args) is tuple and len(args) == 3 and type(args[0]) is Event
                  and args[0].event_type == 'state_changed'
                  and type(args[0].data.get('entity_id')) is str
                  and type(args[1]) is tuple and len(args[1]) == 1 and args[2] is True,
                  'template_binding_rate_timer_arguments_changed')
            track = args[1][0]
            _need(type(track) is self.event.TrackTemplate and track.template is template
                  and sum(item is track for item in tracker._track_templates) == 1
                  and template in tracker._info and template in tracker._rate_limit._last_triggered,
                  'template_binding_rate_timer_template_changed')
            # Read-only graph sampling authenticates both kinds. Mutation of
            # explicit pending work is gated separately on a live internal
            # rename witness and completed producer-delivery fence.
        return timers

    def update_rate_listeners(self, tracker):
        """Use native suppression while the original pending replay still owns work."""
        event = self.event
        tracker._track_state_changes.async_update_listeners(event._render_infos_to_track_states([
            event._suppress_domain_all_in_render_info(info)
            if tracker._rate_limit.async_has_timer(template) else info
            for template, info in tracker._info.items()]))

    def time_owner(self, tracker, template, remove):
        from homeassistant.core import HassJobType
        event = self.event
        owner = getattr(remove, '__self__', None)
        _need(type(owner) is event._TrackUTCTimeChange
              and _bound(remove, owner, event._TrackUTCTimeChange.async_cancel)
              and owner.hass is self.native.hass and not owner.local
              and type(owner.job) is self.HassJob and owner.job.job_type is HassJobType.Callback,
              'template_binding_time_owner_unqualified')
        target = owner.job.target
        parent = event.TrackTemplateResultInfo._setup_time_listener
        from .native_template_consumers import _codes
        _need(type(target) is FunctionType and target.__globals__ is vars(event)
              and target.__qualname__ == 'TrackTemplateResultInfo._setup_time_listener.<locals>._refresh_from_time'
              and any(target.__code__ is code for code in _codes(parent.__code__)),
              'template_binding_time_callback_changed')
        cells = dict(zip(target.__code__.co_freevars,
            (cell.cell_contents for cell in target.__closure__ or ()), strict=True))
        tracks = cells.get('track_templates')
        _need(set(cells) == {'self', 'track_templates'} and cells['self'] is tracker
              and type(tracks) is list and len(tracks) == 1
              and tracks[0].template is template,
              'template_binding_time_closure_changed')
        point_remove = owner._cancel_callback
        point = getattr(point_remove, '__self__', None)
        _need(type(point) is event._TrackPointUTCTime
              and _bound(point_remove, point, event._TrackPointUTCTime.async_cancel)
              and point.hass is self.native.hass
              and type(point.job) is self.HassJob and point.job.job_type is HassJobType.Callback
              and _bound(point.job.target, owner, event._TrackUTCTimeChange._pattern_time_change_listener)
              and type(point._cancel_callback) is asyncio.TimerHandle
              and point._cancel_callback._loop is self.native.hass.loop
              and point._cancel_callback._callback is point
              and not point._cancel_callback.cancelled(),
              'template_binding_time_dispatch_unqualified')
        return owner

    def delay_owner(self, entity):
        from homeassistant.core import HassJobType
        cancel = entity._delay_cancel
        if cancel is None:
            return
        handle = getattr(cancel, '__self__', None)
        _need(type(handle) is asyncio.TimerHandle and handle._loop is self.native.hass.loop
              and _bound(cancel, handle, asyncio.TimerHandle.cancel)
              and handle._callback is self.event._run_async_call_action
              and len(handle._args) == 2 and handle._args[0] is self.native.hass,
              'template_binding_delay_owner_unqualified')
        job = handle._args[1]
        _need(type(job) is self.HassJob and job.job_type is HassJobType.Callback,
              'template_binding_delay_job_unqualified')
        target = job.target
        from .native_template_consumers import _codes
        parent = self.native.binary._update_state
        _need(type(target) is FunctionType and target.__globals__ is parent.__globals__
              and target.__qualname__ == parent.__qualname__ + '.<locals>._set_state'
              and any(target.__code__ is code for code in _codes(parent.__code__)),
              'template_binding_delay_callback_unqualified')
        cells = dict(zip(target.__code__.co_freevars,
            (cell.cell_contents for cell in target.__closure__ or ()), strict=True))
        _need(set(cells) == {'self', 'state'} and cells['self'] is entity
              and type(cells['state']) is bool, 'template_binding_delay_closure_unqualified')

    def graph(self, entity):
        native, event, te = self.native, self.event, self.te
        _need(type(entity) in (native.sensor, native.binary)
              and entity.hass is native.hass and entity._preview_callback is None
              and not entity._action_scripts and entity._blueprint_inputs is None,
              'template_binding_state_entity_required')
        if type(entity) is native.binary:
            self.delay_owner(entity)
        _need(_bound(entity._handle_results, entity, te.TemplateEntity._handle_results),
              'template_binding_handler_changed')
        _need(native.hass.states.get(entity.entity_id) is not None,
              'template_binding_physical_state_required')
        attrs, tracker = entity._template_attrs, entity._template_result_info
        _need(type(attrs) is dict and bool(attrs)
              and type(tracker) is event.TrackTemplateResultInfo
              and tracker.hass is native.hass and type(tracker._job) is self.HassJob
              and _bound(tracker._job.target, entity, te.TemplateEntity._handle_results)
              and tracker._track_state_changes is not None,
              'template_binding_tracker_required')
        filtered = tracker._track_state_changes
        _need(type(filtered) is event._TrackStateChangeFiltered and filtered.hass is native.hass
              and _bound(filtered._action, tracker, event.TrackTemplateResultInfo._refresh)
              and type(filtered._action_as_hassjob) is self.HassJob
              and _bound(filtered._action_as_hassjob.target, tracker, event.TrackTemplateResultInfo._refresh),
              'template_binding_filtered_owner_changed')
        _need(type(tracker._rate_limit) is self.RateLimit
              and tracker._rate_limit.hass is native.hass,
              'template_binding_rate_owner_changed')
        self.rate_timers(tracker)
        _need(set(tracker._last_result) <= set(attrs)
              and set(tracker._rate_limit._last_triggered) <= set(attrs)
              and {id(t.template) for t in tracker._track_templates} == {id(t) for t in attrs}
              and tracker._has_super_template is (entity._availability_template is not None),
              'template_binding_incomplete_native_group')
        _need(sum(cb == tracker.async_remove for cb in entity._on_remove) == 1,
              'template_binding_cleanup_owner_changed')
        declarations = getattr(entity, '_templates', {})
        _need(type(declarations) is dict, 'template_binding_declarations_changed')
        if declarations:
            from homeassistant.components.template.entity import EntityTemplate
            _need(all(type(declaration) is EntityTemplate and declaration.template in attrs
                      for declaration in declarations.values()),
                  'template_binding_declarations_changed')
        for template, slots in attrs.items():
            _need(type(template) is self.template.Template and template.hass is native.hass
                  and type(slots) is list and bool(slots), 'template_binding_slot_group')
            for slot in slots:
                _need(type(slot) is te._TemplateAttribute and slot._entity is entity
                      and type(slot.template) is self.template.Template
                      and slot.template.hass is native.hass and slot.template == template
                      and slot.async_update is None
                      and slot.on_update is not None, 'template_binding_slot_changed')
                # Native setdefault groups equal Template bodies but retains
                # each slot's original object. Authenticate that distinct object
                # against its own configuration/declaration, not dict-key `is`.
                configured = []
                attributes = entity._attribute_templates
                if isinstance(attributes, dict) and slot._attribute in attributes:
                    configured.append(attributes[slot._attribute])
                configured.extend(declaration.template for declaration in declarations.values()
                                  if declaration.attribute == slot._attribute)
                fields = {'_attr_native_value': '_template', '_state': '_template',
                    '_attr_available': '_availability_template', '_attr_icon': '_icon_template',
                    '_attr_entity_picture': '_entity_picture_template', '_attr_name': '_friendly_name_template',
                    '_attr_last_reset': '_attr_last_reset_template',
                    '_attr_extra_state_attributes': '_attributes_template',
                    '_delay_on': '_delay_on_raw', '_delay_off': '_delay_off_raw'}
                if slot._attribute in fields:
                    configured.append(getattr(entity, fields[slot._attribute], None))
                _need(any(value is slot.template for value in configured),
                      'template_binding_slot_config_identity_changed')
                _callback(native, slot.on_update, entity, slot)
                if slot._attribute == '_attr_icon':
                    native.icon_proof.validator(slot.validator)
                elif slot.validator is not None:
                    # cv validators are source-pinned, global functions.
                    _callback(native, slot.validator, entity, slot)
        for template, remove in tracker._time_listeners.items():
            self.time_owner(tracker, template, remove)
        variables = entity._run_variables
        _need(type(variables) in (dict, self.ScriptVariables),
              'template_binding_variables_unqualified')
        for track in tracker._track_templates:
            _need(type(track) is event.TrackTemplate and type(track.variables) is dict
                  and type(track.variables.get('this')) is self.template.TemplateStateFromEntityId
                  and track.variables['this']._hass is native.hass
                  and track.variables['this']._entity_id == entity.entity_id,
                  'template_binding_live_this_required')
        return tracker


class NativeTemplateBindingTransport:
    """One root/task-bound, one-use group; no awaits between install and ER API.

    capture leaves old trackers ACTIVE across all root YAML IO/intent work.
    install_before_registry does native setup before the first detach, so setup
    failure leaves old listeners and exact entity bindings intact. mark_attempt
    MUST precede the actual registry API call. After it, uncertain effects belong
    to the root journal/recovery; rollback_before_registry is deliberately barred.
    """
    @classmethod
    def capture(cls, hass, entities, *, source_pairs, caller_context,
                configured_variable_pairs=(), rendered_variable_pairs=(), rename_witness=None):
        _need(type(caller_context) is _BindingCallerContext,
              'template_binding_internal_caller_required')
        entities, source_pairs = tuple(entities), tuple(source_pairs)
        configured_variable_pairs = tuple(configured_variable_pairs)
        rendered_variable_pairs = tuple(rendered_variable_pairs)
        _need(bool(entities) and len({id(e) for e in entities}) == len(entities)
              and bool(source_pairs or configured_variable_pairs or rendered_variable_pairs)
              and all(type(p) is BindingSourcePair for p in source_pairs),
              'template_binding_qualified_sources_required')
        caller_context.check('capture', source_pairs, entities)
        obj = cls()
        obj.hass, obj.entities, obj.sources = hass, entities, source_pairs
        obj.context, obj.runtime = caller_context, _Runtime(hass)
        obj.phase, obj.rows = 'captured', []
        obj.rename_witness = rename_witness
        obj.configured_variables = tuple(configured_variable_pairs)
        obj.rendered_variables = tuple(rendered_variable_pairs)
        from homeassistant.util.yaml.objects import NodeDictClass
        obj.dictionary_types = (dict, NodeDictClass)
        obj.configured_edits, obj.rendered_edits = {}, {}
        obj.rendered_copies = {}
        for pairs, edits in ((obj.configured_variables, obj.configured_edits),
                             (obj.rendered_variables, obj.rendered_edits)):
            for pair in pairs:
                _need(type(pair) is BindingVariablePair and type(pair.container) in obj.dictionary_types
                      and type(pair.name) is str and type(pair.before) is str
                      and type(pair.after) is str and pair.name != 'this'
                      and pair.before != pair.after
                      and type(pair.source_identity) is tuple and bool(pair.source_identity)
                      and pair.name in pair.container
                      and isinstance(pair.container[pair.name], str)
                      and pair.container[pair.name] == pair.before,
                      'template_binding_variable_source_changed')
                group = edits.setdefault(id(pair.container), {})
                prior = group.get(pair.name)
                _need(prior is None or (prior.before == pair.before and prior.after == pair.after
                      and prior.source_identity == pair.source_identity),
                      'template_binding_variable_source_duplicate')
                group[pair.name] = pair

        Template = obj.runtime.template.Template
        by_id = {}
        for pair in source_pairs:
            _need(type(pair.template) is Template and pair.template.hass is hass
                  and type(pair.before) is str and type(pair.after) is str
                  and pair.before == pair.template.template
                  and type(pair.source_identity) is tuple and bool(pair.source_identity)
                  and id(pair.template) not in by_id, 'template_binding_source_identity_changed')
            by_id[id(pair.template)] = pair
        for entity in entities:
            tracker = obj.runtime.graph(entity)
            # Include unmodified templates too: a whole group is a single native
            # observation unit; no old/new equality/hash alias or body mutation.
            bindings = {name: getattr(entity, name) for name in _FIELDS if hasattr(entity, name)}
            slots = [(slot, slot.template) for group in entity._template_attrs.values() for slot in group]
            declarations = [(slot, slot.template) for slot in getattr(entity, '_templates', {}).values()]
            originals, containers = {}, {}
            def scan(value):
                if type(value) is Template:
                    originals[id(value)] = value
                elif type(value) is obj.runtime.ScriptVariables:
                    scan(value.variables)
                elif isinstance(value, dict):
                    containers[id(value)] = value
                    for key, item in value.items():
                        scan(key); scan(item)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        scan(item)
            for value in bindings.values():
                scan(value)
            for _, template in slots + declarations:
                scan(template)
            replacements = {key: Template(by_id[key].after if key in by_id else value.template, hass)
                            for key, value in originals.items()}
            # Preserve native pre-existing equality groups. A root-approved
            # rewrite of one representative body applies to all authenticated
            # equal slot objects; conflicting desired bodies cannot share one
            # old native result/history and are declined before any effect.
            for key, group in entity._template_attrs.items():
                members = [key] + [slot.template for slot in group]
                desired = {by_id[id(t)].after for t in members if id(t) in by_id}
                _need(len(desired) <= 1, 'template_binding_merged_source_disagrees')
                body = next(iter(desired), key.template)
                for template in members:
                    replacements[id(template)] = Template(body, hass)
            obj.rows.append(dict(entity=entity, tracker=tracker, bindings=bindings,
                attrs=entity._template_attrs, slots=slots, declarations=declarations,
                replacements=replacements, originals=originals, containers=containers,
                bodies={key: value.template for key, value in originals.items()}, memo={},
                cleanup=tuple(entity._on_remove), index=entity._on_remove.index(tracker.async_remove),
                new=None, detached=False, time_moves=[], rate_moves=[]))
        _need(set(by_id) <= {key for row in obj.rows for key in row['originals']},
              'template_binding_source_not_in_group')
        configured = {key: value for row in obj.rows for key, value in row['containers'].items()}
        rendered = {id(track.variables): track.variables for row in obj.rows
                    for track in row['tracker']._track_templates}
        _need(all(configured.get(id(pair.container)) is pair.container for pair in obj.configured_variables)
              and all(rendered.get(id(pair.container)) is pair.container for pair in obj.rendered_variables),
              'template_binding_variable_not_in_group')
        obj._pending_explicit_witness()
        return obj

    def _pending_explicit_witness(self):
        explicit = any(row['tracker']._info[template].entities for row in self.rows
                       for template in self.runtime.rate_timers(row['tracker']))
        if not explicit:
            return
        from .native_rename_emission_classifier import NativeRenameEmissions
        from .native_template_rename_ingress import NativeTemplateProducerDelivery
        witness = self.rename_witness
        _need(type(witness) is NativeRenameEmissions and witness.hass is self.hass
              and not witness.attempted and not witness.closed
              and type(witness.preflight) is NativeTemplateProducerDelivery
              and not witness.preflight.live and not witness.preflight.pending(),
              'template_binding_pending_explicit_rate_event_unqualified')
        witness._owner_guard()
        witness._check()

    def _variables_current(self):
        if not self.configured_variables and not self.rendered_variables:
            return
        live = {}
        def visit(value):
            if type(value) is self.runtime.ScriptVariables:
                visit(value.variables)
            elif isinstance(value, dict):
                live[id(value)] = value
                for child in value.values():
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)
        for row in self.rows:
            for name in row['bindings']:
                visit(getattr(row['entity'], name))
        rendered = {id(t.variables): t.variables for row in self.rows
                    for t in row['tracker']._track_templates}
        _need(all(live.get(id(p.container)) is p.container for p in self.configured_variables)
              and all(rendered.get(id(p.container)) is p.container for p in self.rendered_variables),
              'template_binding_variable_not_in_group')
        for pair in self.configured_variables + self.rendered_variables:
            _need(pair.name in pair.container and isinstance(pair.container[pair.name], str)
                  and pair.container[pair.name] == pair.before,
                  'template_binding_variable_source_changed')

    def _rendered(self, variables):
        edits = self.rendered_edits.get(id(variables))
        if not edits:
            return variables
        if id(variables) not in self.rendered_copies:
            # Preserve every unrelated live this/now/frozen snapshot by identity.
            copied = dict(variables)
            for name, pair in edits.items():
                copied[name] = pair.after
            self.rendered_copies[id(variables)] = copied
        return self.rendered_copies[id(variables)]

    def _rewrite(self, row, value):
        if type(value) is self.runtime.template.Template:
            return row['replacements'][id(value)]
        memo = row['memo']
        if id(value) in memo:
            return memo[id(value)]
        if type(value) is self.runtime.ScriptVariables:
            result = self.runtime.ScriptVariables(self._rewrite(row, value.variables))
        elif isinstance(value, dict):
            edits = self.configured_edits.get(id(value), {})
            result = {self._rewrite(row, k): (edits[k].after if k in edits
                      else self._rewrite(row, v)) for k, v in value.items()}
        elif isinstance(value, list):
            result = [self._rewrite(row, v) for v in value]
        elif isinstance(value, tuple):
            result = tuple(self._rewrite(row, v) for v in value)
        else:
            return value
        memo[id(value)] = result
        return result

    def _check(self, phase, context):
        _need(context is self.context, 'template_binding_caller_context_changed')
        context.check(phase, self.sources, self.entities)
        self.runtime.bindings()

    def install_before_registry(self, caller_context):
        self._check('install', caller_context)
        _need(self.phase == 'captured', 'template_binding_one_use_required')
        # Last source IO and live graph validation are BEFORE any detach.
        self.runtime.sources()
        self._variables_current()
        self._pending_explicit_witness()
        for row in self.rows:
            entity = row['entity']
            _need(self.runtime.graph(entity) is row['tracker']
                  and entity._template_attrs is row['attrs']
                  and tuple(entity._on_remove) == row['cleanup']
                  and all(getattr(entity, k) is v for k, v in row['bindings'].items())
                  and all(slot.template is template for slot, template in row['slots'] + row['declarations'])
                  and all(t.template == row['bodies'][key] for key, t in row['originals'].items()), 'template_binding_live_graph_changed')
        try:
            for row in self.rows:
                entity, replacements = row['entity'], row['replacements']
                # Preserve the actual already-rendered native variables DATA.
                # Re-rendering configured now()/this variables here would add
                # a semantic observation without a source/time event.
                tracks = [self.runtime.event.TrackTemplate(replacements[id(track.template)],
                    self._rendered(track.variables), rate_limit=track.rate_limit)
                    for track in row['tracker']._track_templates]
                _need(len({track.template for track in tracks}) == len(tracks),
                      'template_binding_template_key_collision')
                availability = entity._availability_template
                if availability is not None:
                    tracks.sort(key=lambda track: track.template is not replacements[id(availability)])
                # Native constructor+setup are the exact factory's two operations,
                # retaining ownership early enough to clean up partial setup FAIL.
                new = row['new'] = self.runtime.event.TrackTemplateResultInfo(
                    self.hass, tracks, entity._handle_results, has_super_template=False)
                new.async_setup()
                _need(set(new._info) == {track.template for track in tracks},
                      'template_binding_full_slot_setup_incomplete')
                new._has_super_template = availability is not None
                old = row['tracker']
                new._last_result = {replacements[id(t)]: value for t, value in old._last_result.items()}
                new._rate_limit._last_triggered = {replacements[id(t)]: value
                    for t, value in old._rate_limit._last_triggered.items()}
            for row in self.rows:
                old, new = row['tracker'], row['new']
                _need(not new._rate_limit._rate_limit_timers,
                      'template_binding_new_rate_timer_unexpected')
                for template, handle in tuple(self.runtime.rate_timers(old).items()):
                    replacement = row['replacements'][id(template)]
                    tracks = [track for track in new._track_templates if track.template is replacement]
                    _need(len(tracks) == 1, 'template_binding_rate_replacement_missing')
                    row['rate_moves'].append((template, replacement, handle, handle._callback, handle._args))
                    del old._rate_limit._rate_limit_timers[template]
                    new._rate_limit._rate_limit_timers[replacement] = handle
                    handle._callback = new._refresh
                    handle._args = (handle._args[0], (tracks[0],), True)
                if row['rate_moves']:
                    self.runtime.update_rate_listeners(new)
            # Transport genuine native recurring time owners, including an
            # already-due physical timer. Recreating a scheduler would randomize
            # its microsecond and could skip the pending tick at a boundary.
            for row in self.rows:
                old, new = row['tracker'], row['new']
                expected = {row['replacements'][id(t)] for t in old._time_listeners}
                for template in tuple(new._time_listeners):
                    if template not in expected:
                        new._time_listeners.pop(template)()
                for template, remove in tuple(old._time_listeners.items()):
                    replacement = row['replacements'][id(template)]
                    new._setup_time_listener(replacement, True)
                    old_owner = self.runtime.time_owner(old, template, remove)
                    temporary = new._time_listeners[replacement]
                    new_owner = self.runtime.time_owner(new, replacement, temporary)
                    row['time_moves'].append((template, replacement, remove, old_owner, old_owner.job))
                    temporary()
                    old_owner.job = new_owner.job
                    new._time_listeners[replacement] = remove
                    del old._time_listeners[template]
            # All authentic setups succeeded. No await, refresh, native handler,
            # scheduler run or entity output write occurs in this cutover.
            for row in self.rows:
                entity = row['entity']
                row['tracker'].async_remove()
                row['detached'] = True
                for name, value in row['bindings'].items():
                    setattr(entity, name, self._rewrite(row, value))
                for slot, template in row['slots'] + row['declarations']:
                    slot.template = row['replacements'][id(template)]
                entity._template_attrs = {row['replacements'][id(t)]: slots for t, slots in row['attrs'].items()}
                entity._template_result_info = row['new']
                entity._on_remove[row['index']] = row['new'].async_remove
            self.phase = 'installed'
        except BaseException as error:
            # A native factory may fail after constructing its filtered tracker
            # but before returning it. Its actual traceback owns that object;
            # remove it by its authenticated action owner, not guessed bus keys.
            frame = error.__traceback__
            try:
                while frame is not None:
                    for value in tuple(frame.tb_frame.f_locals.values()):
                        if type(value) is self.runtime.event._TrackStateChangeFiltered:
                            if any(row['new'] is not None and _bound(value._action,
                                row['new'], self.runtime.event.TrackTemplateResultInfo._refresh)
                                for row in self.rows):
                                value.async_remove()
                    frame = frame.tb_next
            finally:
                del frame
            self._restore_before_effect()
            self.phase = 'failed'
            raise

    @staticmethod
    def _stop(tracker):
        if tracker is None:
            return
        if tracker._track_state_changes is not None:
            tracker.async_remove()
        else:
            tracker._rate_limit.async_remove()
            for remove in tuple(tracker._time_listeners.values()):
                remove()
            tracker._time_listeners.clear()

    def _restore_before_effect(self):
        # Called synchronously only BEFORE registry attempt. Restore the exact
        # old objects and history; native setup recreates its authentic listeners.
        for row in reversed(self.rows):
            for template, replacement, handle, callback, args in reversed(row['rate_moves']):
                row['new']._rate_limit._rate_limit_timers.pop(replacement, None)
                handle._callback, handle._args = callback, args
                row['tracker']._rate_limit._rate_limit_timers[template] = handle
            row['rate_moves'].clear()
            for template, replacement, remove, owner, job in reversed(row['time_moves']):
                row['new']._time_listeners.pop(replacement, None)
                owner.job = job
                row['tracker']._time_listeners[template] = remove
            row['time_moves'].clear()
            self._stop(row['new'])
            if not row['detached']:
                continue
            entity = row['entity']
            for name, value in row['bindings'].items():
                setattr(entity, name, value)
            for slot, template in row['slots'] + row['declarations']:
                slot.template = template
            entity._template_attrs = row['attrs']
            entity._template_result_info = row['tracker']
            entity._on_remove[:] = row['cleanup']
            row['tracker'].async_setup()
            if row['tracker']._rate_limit._rate_limit_timers:
                self.runtime.update_rate_listeners(row['tracker'])
            row['detached'] = False

    def rollback_before_registry(self, caller_context):
        self._check('rollback_before_registry', caller_context)
        _need(self.phase == 'installed', 'template_binding_rollback_after_attempt_forbidden')
        self._restore_before_effect()
        self.phase = 'rolled_back'

    def mark_registry_attempt(self, caller_context):
        self._check('registry_attempt', caller_context)
        _need(self.phase == 'installed', 'template_binding_install_required')
        self.phase = 'registry_attempted'
