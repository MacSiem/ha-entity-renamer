"""Typed live source-identity handoff for a retained Powercalc producer.

The native registry API and its original Events remain authoritative. This
private node is bound to one existing intent, actual producer and tracker; it
does not create or replay State/Event objects.
"""
from __future__ import annotations

import asyncio
from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from functools import partial
from importlib import import_module
import math

from .native_registry import NativeRegistryIO, PreparedRegistryChange
from .native_registry_receipts import RegistryIntent
from .native_rename_emission_classifier import NativeRenameEmissions
from .powercalc_phase_session import PowercalcPhaseSession
from .transaction import PhaseContext, Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_source_transition_' + reason)


async def _drain_owned(coroutine):
    task = asyncio.create_task(coroutine)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


def _config_image(value):
    """Bounded exact native YAML/config image, including annotations and signed zero."""
    from homeassistant.util.yaml.objects import NodeDictClass, NodeListClass, NodeStrClass
    from .powercalc_source_binding import _json
    budget = [100_000]
    def visit(item, depth=0, ancestry=frozenset()):
        budget[0] -= 1
        _need(budget[0] >= 0 and depth <= 64, 'config_limit')
        kind = type(item)
        if item is None or kind in (str, bool, int):
            return [kind.__name__, item]
        if kind is float:
            _need(math.isfinite(item), 'config_nonfinite')
            return ['float', item.hex()]
        if kind is Decimal:
            _need(item.is_finite(), 'config_nonfinite')
            return ['decimal', str(item)]
        if kind is timedelta:
            return ['timedelta', item.days, item.seconds, item.microseconds]
        if isinstance(item, Enum):
            return ['enum', kind.__module__, kind.__qualname__, visit(item.value, depth + 1, ancestry)]
        metadata = []
        if kind in (NodeStrClass, NodeListClass, NodeDictClass):
            for name in ('__config_file__', '__line__'):
                if hasattr(item, name):
                    native = getattr(item, name)
                    _need(type(native) in (str, int), 'config_annotation_unqualified')
                    metadata.append([name, type(native).__name__, native])
        if kind is NodeStrClass:
            return ['node_str', str(item), metadata]
        _need(id(item) not in ancestry, 'config_cycle')
        ancestry = ancestry | {id(item)}
        if kind in (list, tuple, NodeListClass):
            return [kind.__name__, metadata, [visit(child, depth + 1, ancestry) for child in item]]
        if kind in (set, frozenset):
            values = [visit(child, depth + 1, ancestry) for child in item]
            return [kind.__name__, sorted(values, key=_json)]
        _need(kind in (dict, NodeDictClass), 'config_type_' + kind.__module__ + '.' + kind.__qualname__)
        _need(all(type(key) in (str, NodeStrClass) for key in item), 'config_key_type_unqualified')
        rows = [[visit(key, depth + 1, ancestry), visit(child, depth + 1, ancestry)]
                for key, child in item.items()]
        return [kind.__name__, metadata, sorted(rows, key=lambda row: _json(row[0]))]
    return _json(visit(value))


class PowercalcSourceTransition:
    def __init__(self):
        raise Refused('powercalc_source_transition_capture_required')

    @classmethod
    async def capture(cls, phase, io, change, context, intent, observations, *, preflight):
        """Bind an existing durable registry intent and actual source graph."""
        from homeassistant.core import callback, HassJob, HassJobType
        from homeassistant.helpers import event
        from .powercalc_retained_producer import _Bridge
        from .yaml_runtime_observer import _registered_bus_remove

        _need(cls is PowercalcSourceTransition and type(phase) is PowercalcPhaseSession
              and type(io) is NativeRegistryIO and io.hass is phase.lease.hass
              and type(change) is PreparedRegistryChange and type(context) is PhaseContext
              and type(intent) is RegistryIntent and phase.phase == 'sources_inactive'
              and phase.lease.pending is intent and phase.lease.chain.active is intent
              and context.record.direction == phase.lifecycle.direction
              and context.record.direction in ('forward', 'rollback') and context.step.adapter == 'registry'
              and change == phase.lease.chain._original_change(context.index)
              and change.kind == 'entity' and len(change.mapping) == 1,
              'live_original_intent_required')
        phase.check()
        phase.lease.chain.verify_intent_source(intent, context, observations)
        old, new = next(iter(change.mapping.items()))
        if context.record.direction == 'rollback':
            old, new = new, old
        owner = phase.scope.producer_owner
        affected = tuple(sorted(name for name, deps in owner.dependencies.items() if old in deps))
        _need(affected and old in owner.inputs and new not in owner.inputs
              and all(owner.phase[name] == 'retained' and name in owner.bridges for name in affected)
              and all(name in affected for name, producer in owner.old.items()
                      if producer._source_entity.entity_id == old)
              and getattr(phase.scope, 'source_transition', None) is None,
              'retained_actual_source_required')
        _need(type(preflight) is tuple and all(type(row) is tuple and len(row) == 3 for row in preflight)
              and tuple(row[0] for row in preflight) == affected
              and all(row[1] is owner.old[row[0]]._sensor_config
                      and type(row[2]) is bytes and _config_image(row[1]) == row[2]
                      for row in preflight), 'same_preflight_config_required')
        obj = object.__new__(cls)
        obj.phase, obj.io, obj.change, obj.context, obj.intent = phase, io, change, context, intent
        obj.old, obj.new, obj.owner, obj.affected = old, new, owner, affected
        obj.source, obj.target = old, new
        obj.task, obj.loop = asyncio.current_task(), asyncio.get_running_loop()
        obj.witness, obj.live, obj.attempted, obj.rebound = None, False, False, False
        obj._issued_ref, obj._consumed = None, False
        obj.target_bridges, obj.issued = {}, {}
        obj.remove_raw, obj.remove_seen = {}, set()
        obj.event_states = {}
        obj.original_sources, obj.original_configs, obj.original_config_json, obj.rebound_sources = {}, {}, {}, {}
        SourceEntity = import_module('custom_components.powercalc.common').SourceEntity
        for name in affected:
            producer = owner.old[name]
            source = producer._source_entity
            _need(type(source) is SourceEntity and source.entity_id == old
                  and producer._strategy_instance._source_entity is source
                  and type(producer._sensor_config) is dict
                  and producer._sensor_config.get('entity_id') == old,
                  'actual_immutable_source_required')
            # An independently configured dynamic matcher requires its own
            # real binding transition; do not rewrite it by textual matching.
            selector = producer._sub_profile_selector
            _need(selector is None or all(getattr(item, '_entity_id', None) != old
                  for item in selector._matchers), 'source_selector_binding_unqualified')
            obj.original_sources[name] = source
            obj.original_configs[name] = producer._sensor_config
            obj.original_config_json[name] = _config_image(producer._sensor_config)
        destination = change.before if context.record.direction == 'rollback' else change.after
        witness = await NativeRenameEmissions.prepare(io, change, destination, guard=phase.check)
        try:
            phase.check()
            phase.lease.chain.verify_intent_source(intent, context, observations)
            _need(witness.source is owner.inputs[old][0], 'actual_physical_source_changed')
            obj.witness = witness
            data = phase.lease.hass.data.get(event._TRACK_STATE_CHANGE_DATA)
            _need(type(data) is event._KeyedEventData, 'native_tracker_required')
            (bus_job, event_filter), _ = _registered_bus_remove(phase.lease.hass, data.listener, 'state_changed')
            for name in affected:
                def make_bridge(producer_name):
                    @callback
                    def bridge(actual_event):
                        try:
                            owner._bridge(producer_name, actual_event,
                                          bridge=obj.target_bridges[producer_name])
                        except BaseException as error:
                            owner._poison(error)
                    return bridge
                target = make_bridge(name)
                remover = event.async_track_state_change_event(phase.lease.hass, (new,), target)
                _need(type(remover) is partial and remover.func is event._remove_listener
                      and len(remover.args) == 5 and remover.args[0] is phase.lease.hass
                      and remover.args[1] is event._KEYED_TRACK_STATE_CHANGE
                      and tuple(remover.args[2]) == (new,), 'target_tracker_issuance')
                job, callbacks = remover.args[3:]
                row = _Bridge(target, remover, job, callbacks, frozenset((new,)))
                obj.issued[name] = (row, remover, job, target, callbacks, data, data.listener,
                                    event._KEYED_TRACK_STATE_CHANGE, bus_job, event_filter)
                obj.target_bridges[name] = row
                _need(type(job) is HassJob and job.job_type is HassJobType.Callback
                      and job.target is target and callbacks is data.callbacks
                      and phase.lease.hass.data.get(event._TRACK_STATE_CHANGE_DATA) is data
                      and sum(item is job for item in callbacks.get(new, ())) == 1,
                      'actual_target_tracker_required')
            obj.live = True
            phase.scope.source_transition = obj
            phase.check()
            witness.bind_powercalc_failure_owner(obj)
            return obj
        except BaseException:
            # The target issuance may already exist; cleanup remains tied to
            # each independently recorded native job, never to a rebound map.
            try:
                obj._close_targets()
            finally:
                obj.live = False
                if getattr(phase.scope, 'source_transition', None) is obj:
                    phase.scope.source_transition = None
                witness.close()
            raise

    def handles_input(self, name):
        return name == self.old

    def handles_producer(self, name):
        return name in self.affected

    def retire_target(self, name):
        _need(self.live and self.witness.finished and name in self.affected
              and name not in self.owner.active
              and all(name not in emission[4] for emission in self.owner.emitted.values()),
              'target_handoff_not_quiescent')
        self._close_target(name)

    def _target_row(self):
        from .powercalc_global_listener_ownership import _OriginalPhysicalSource
        row = self.io.registries['entity'].async_get(self.new)
        original = self.owner.inputs[self.old][3]
        _need(row is not None and _OriginalPhysicalSource.from_row(row) ==
              replace(original, entity_id=self.new), 'target_registry_identity_changed')
        return row

    def check_input(self, name, entity, cls, platform, identity):
        binding = self.owner.inputs.get(name)
        _need(self.live and name == self.old and self.phase.scope.source_transition is self
              and type(binding) is tuple and len(binding) == 4
              and all(actual is expected for actual, expected in zip(
                  binding, (entity, cls, platform, identity), strict=True))
              and type(entity) is cls and entity is self.witness.source
              and entity.hass is self.phase.lease.hass and entity.platform is platform
              and entity.unique_id == identity.unique_id, 'physical_source_binding_changed')
        registry = self.io.registries['entity']
        if registry.async_get(self.old) is self.witness.row:
            _need(not self.rebound and entity.entity_id == self.old and entity.registry_entry is self.witness.row
                  and platform.entities.get(self.old) is entity, 'original_physical_source_changed')
            return
        target = self._target_row()
        _need(registry.async_get(self.old) is None and self.witness.attempted
              and self.witness._registry_emission() is not None,
              'native_registry_transition_unproven')
        if entity.entity_id == self.old and platform.entities.get(self.old) is entity:
            _need(not self.rebound, 'old_source_after_rebind')
            return
        if entity.entity_id == self.new and platform.entities.get(self.new) is entity:
            _need(entity.registry_entry is target, 'target_physical_source_changed')
            return
        from homeassistant.helpers.entity import EntityPlatformState
        _need(not self.rebound and entity._platform_state is EntityPlatformState.REMOVED
              and self.witness.source_runs > 0 and bool(self.witness.tasks),
              'unobserved_source_absence')

    def check_exact_source(self, name, original, owner):
        entity, cls, platform, identity = self.owner.inputs[name]
        _need(identity == original and owner.source_registry is self.io.registries['entity']
              and owner.source_classes[name] is cls
              and owner.source_entries[name] is platform.config_entry,
              'original_source_witness_changed')
        self.check_input(name, entity, cls, platform, identity)

    def expected_source(self, producer, original):
        return self.new if self.rebound and producer in self.affected and original == self.old else original

    def expected_source_fields(self, producer, original):
        if self.rebound and producer in self.affected:
            _need(original[0] == self.old, 'original_source_fields_changed')
            return (self.new, original[1], original[2], self.new.split('.', 1)[1])
        return original

    def bound_producer_source(self, producer, original, actual):
        if producer not in self.affected or not self.rebound:
            _need(actual is original, 'original_producer_source_changed')
            return original
        _need(self.original_sources[producer] is original
              and actual is self.rebound_sources.get(producer), 'rebound_producer_source_changed')
        return actual

    def check_pending_source(self, producer, source):
        return (producer in self.affected and self.attempted and not self.rebound
                and source is self.original_sources[producer]
                and self.io.registries['entity'].async_get(self.old) is None
                and self._target_row() is not None and self.witness._registry_emission() is not None)

    def _rebind(self):
        """Copy frozen vendor source metadata at the actual native ADD edge."""
        from inspect import getattr_static
        _need(not self.rebound and self.witness.attempted, 'duplicate_source_rebind')
        row = self._target_row()
        staged = []
        source_copies, config_copies = {}, {}
        for name in self.affected:
            producer = self.owner.old[name]
            original = self.original_sources[name]
            config = self.original_configs[name]
            _need(producer._source_entity is original and producer._strategy_instance._source_entity is original
                  and producer._sensor_config is config and config.get('entity_id') == self.old
                  and _config_image(config) == self.original_config_json[name],
                  'producer_before_rebind_changed')
            _need(getattr_static(type(producer), '__setattr__') is object.__setattr__
                  and getattr_static(type(producer._strategy_instance), '__setattr__') is object.__setattr__,
                  'producer_assignment_binding_changed')
            if id(original) in source_copies:
                saved_original, target = source_copies[id(original)]
                _need(saved_original is original, 'source_alias_changed')
            else:
                target = replace(original, entity_id=self.new, object_id=self.new.split('.', 1)[1], entity_entry=row)
                source_copies[id(original)] = (original, target)
            _need(all(getattr(target, field.name) is getattr(original, field.name)
                      for field in fields(original)
                      if field.name not in ('entity_id', 'object_id', 'entity_entry')),
                  'immutable_source_fields_changed')
            if id(config) in config_copies:
                saved_config, detached = config_copies[id(config)]
                _need(saved_config is config, 'config_alias_changed')
            else:
                detached = dict(config)
                detached['entity_id'] = self.new
                config_copies[id(config)] = (config, detached)
            _need(_config_image({k: v for k, v in detached.items() if k != 'entity_id'}) ==
                  _config_image({k: v for k, v in config.items() if k != 'entity_id'}),
                  'producer_config_changed')
            staged.append((name, producer, target, detached))
        for name, producer, target, detached in staged:
            producer._source_entity = target
            producer._strategy_instance._source_entity = target
            producer._sensor_config = detached
            self.rebound_sources[name] = target
        self.rebound = True

    def observe_producer_fire(self, owner, frame, data):
        """Observe original native source emissions, never infer an Event."""
        from homeassistant.core import State
        from .powercalc_retained_producer import _state_snapshot

        _need(self.live and owner is self.owner and self.phase.scope.source_transition is self,
              'live_owner_changed')
        if (type(data) is not dict or frame.f_locals.get('event_type') != 'state_changed'
                or data.get('entity_id') not in (self.old, self.new)):
            return False
        if data['entity_id'] == self.old and data.get('new_state') is None:
            _need(self.attempted and self.witness._state_cause(frame, data) == 'remove'
                  and id(data) not in self.remove_raw, 'structural_remove_unproven')
            self.remove_raw[id(data)] = data
            prior = data['old_state']
            _need(type(prior) is State, 'structural_prior_required')
            self.event_states[id(data)] = (data, prior, prior.as_dict_json.decode('utf-8'),
                                           prior.last_reported.isoformat())
            return True
        if data['entity_id'] != self.new:
            return False
        state, previous = data.get('new_state'), data.get('old_state')
        native_add = self.witness._state_cause(frame, data) == 'add'
        if not native_add and self.phase.lease._cleanup_owner is self:
            # A later independent source write is not part of the issued
            # registry ADD, even if it targets the same physical entity.
            from .powercalc_runtime_observer import _guard
            _guard(self.phase.lease.guard)
        if not self.rebound:
            _need(native_add and self.attempted, 'target_before_native_add')
            self._rebind()
        else:
            # Subsequent target samples must come from the same actual native
            # physical entity writer, not merely an equal State or Context.
            parent = frame.f_back
            _need(parent is not None and parent.f_code is owner.hc[0]
                  and parent.f_locals.get('self') is self.phase.lease.hass.states
                  and parent.f_locals.get('state_changed_data') is data
                  and parent.f_locals.get('state') is state, 'target_statemachine_emission')
            ancestor, emitter = parent.f_back, None
            try:
                while ancestor is not None:
                    if ancestor.f_code is owner.hc[5]:
                        emitter = ancestor.f_locals.get('self')
                        break
                    ancestor = ancestor.f_back
            finally:
                del ancestor
            _need(emitter is self.witness.source, 'target_physical_writer_required')
        _need(type(state) is State and state is self.phase.lease.hass.states.get(self.new)
              and state.state not in ('unavailable', 'unknown')
              and (previous is None or type(previous) is State)
              and id(data) not in owner.seen and id(data) not in owner.emitted,
              'target_actual_sample_required')
        if native_add:
            _need(id(data) not in self.event_states, 'duplicate_native_add')
            self.event_states[id(data)] = (data, state, state.as_dict_json.decode('utf-8'),
                                           state.last_reported.isoformat())
        self.phase.scope.check()
        active = tuple(name for name in self.affected if owner.phase[name] == 'retained'
                       and name in self.issued)
        if not active:
            return False  # All actual new producers now own native delivery.
        _need(all(name not in owner.active for name in active), 'overlapping_input_inconclusive')
        fired = frame.f_locals.get('time_fired')
        _need(type(fired) is float, 'actual_emission_timestamp')
        owner.sequence += 1
        owner.emitted[id(data)] = (data, state, _state_snapshot(state), owner.sequence,
            frozenset(active), fired,
            (state.last_changed, state.last_updated, state.last_reported), previous,
            None if previous is None else _state_snapshot(previous), tuple(sorted(data)))
        return True

    def consume_structural(self, owner, name, event, bridge):
        _need(self.live and owner is self.owner, 'live_owner_changed')
        data = getattr(event, 'data', None)
        if type(data) is not dict or data.get('entity_id') != self.old or data.get('new_state') is not None:
            return False
        _need(name in self.affected and bridge is None and id(data) in self.remove_raw
              and self.remove_raw[id(data)] is data and self.witness.is_owned_rename(event)
              and name not in self.remove_seen, 'structural_delivery_unproven')
        owner._dispatch(owner.bridges[name], event)
        self.remove_seen.add(name)
        return True

    def owns_bridge(self, owner, name, bridge):
        if not (self.live and owner is self.owner and name in self.affected
                and self.target_bridges.get(name) is bridge and not bridge.removed):
            return False
        issued = self.issued.get(name)
        if issued is None:
            return False
        row, remover, job, target, callbacks, data, listener, tracker, bus_job, event_filter = issued
        try:
            from .yaml_runtime_observer import _registered_bus_remove
            (actual_job, actual_filter), _ = _registered_bus_remove(
                self.phase.lease.hass, listener, 'state_changed')
        except (ValueError, TypeError, KeyError, AttributeError):
            return False
        return (actual_job is bus_job and actual_filter is event_filter
                and row is bridge and bridge.remover is remover and bridge.job is job
                and bridge.callback is target and bridge.callbacks is callbacks
                and self.phase.lease.hass.data.get(tracker.key) is data
                and data.callbacks is callbacks and data.listener is listener
                and sum(item is job for item in callbacks.get(self.new, ())) == 1)

    def _close_target(self, name):
        from .yaml_runtime_observer import _registered_bus_remove
        issued = self.issued.get(name)
        if issued is None:
            return
        row, remover, job, target, callbacks, original, listener, tracker, bus_job, event_filter = issued
        members = callbacks.get(self.new, ())
        _need(sum(item is job for item in members) == 1 and job.target is target
              and row.callback is target and row.job is job and row.callbacks is callbacks,
              'target_tracker_issuance_changed')
        current = self.phase.lease.hass.data.get(tracker.key)
        intact = (current is original and original.callbacks is callbacks and original.listener is listener)
        if intact:
            try:
                (actual_job, actual_filter), _ = _registered_bus_remove(
                    self.phase.lease.hass, listener, 'state_changed')
                intact = actual_job is bus_job and actual_filter is event_filter
            except (ValueError, TypeError, KeyError, AttributeError):
                intact = False
        if intact:
            remover()
        else:
            members.remove(job)
            if not members:
                del callbacks[self.new]
            if not callbacks:
                (actual_job, actual_filter), _ = _registered_bus_remove(
                    self.phase.lease.hass, listener, 'state_changed')
                _need(actual_job is bus_job and actual_filter is event_filter,
                      'original_dispatcher_changed')
                listener()
        row.removed = True
        del self.issued[name]
        _need(not any(item is job for values in callbacks.values() for item in values),
              'target_tracker_left')
        _need(intact, 'target_tracker_rebound')

    def _close_targets(self):
        first = None
        for name in tuple(self.issued):
            try:
                self._close_target(name)
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first

    def mutate(self):
        """Attempt the classifier's one actual registry call exactly once."""
        _need(self.live and not self.attempted and asyncio.current_task() is self.task
              and asyncio.get_running_loop() is self.loop
              and self.phase.scope.source_transition is self
              and self.phase.lease.pending is self.intent
              and all(self.owns_bridge(self.owner, name, self.target_bridges[name])
                      for name in self.affected), 'source_attempt_binding_changed')
        self.phase.check()
        self.attempted = True
        # Native registry callbacks can run reentrantly inside mutate(). Arm
        # the narrow delivery capability before the call; the real guard is
        # still mandatory until the classifier sees this call's own event.
        self.phase.lease.begin_source_cleanup(self)
        return self.witness.mutate()

    async def finish_native(self):
        """Drain the actual rename and all retained native calculations."""
        _need(self.live and self.attempted and not self.witness.finished
              and asyncio.current_task() is self.task and asyncio.get_running_loop() is self.loop,
              'source_attempt_required')
        # NativeRenameEmissions itself is task-bound; run it on the owner.
        # It shields observed native tasks internally. The separately owned
        # producer descendants are drained even on caller cancellation.
        # Once the authentic registry event is issued, native delivery and
        # already-started calculations must finish even if authorization is
        # revoked meanwhile. The lease blocks new intents and receipt ACKs
        # throughout this narrow interval; check authorization again below.
        _need(self.phase.lease._cleanup_owner is self, 'armed_source_delivery_required')
        try:
            try:
                proof = await self.witness.finish()
            finally:
                await _drain_owned(self.phase.scope.settle_descendants())
        finally:
            self.phase.lease.end_source_cleanup(self)
        self.phase.check()
        _need(self.rebound and self.remove_seen == set(self.affected)
              and (proof['rename_remove'], proof['rename_add']) == (1, 1)
              and not self.owner.active
              and all(not any(name in emission[4] for emission in self.owner.emitted.values())
                      for name in self.affected), 'source_event_delivery_incomplete')
        for name in self.affected:
            original = self.owner.dependencies[name]
            _need(self.old in original and self.new not in original, 'source_dependencies_changed')
            self.owner.dependencies[name] = frozenset(
                self.new if item == self.old else item for item in original)
        self.proof = proof

    async def finish_failed_attempt(self):
        """Quiesce issued target jobs; an attempted effect stays unknown."""
        first = None
        try:
            if self.witness.failed_powercalc:
                _need(self.phase.lease._cleanup_owner is self, 'armed_source_delivery_required')
                try:
                    await self.witness.finish_failed_powercalc()
                except BaseException as error:
                    first = error
                try:
                    await _drain_owned(self.phase.scope.settle_descendants())
                except BaseException as error:
                    if first is None:
                        first = error
            elif self.witness.attempted and self.witness.mutation is not None and not self.witness.finished:
                try:
                    await self.witness.finish()
                except BaseException as error:
                    first = error
        except BaseException as error:
            if first is None:
                first = error
        finally:
            if self.phase.lease._cleanup_owner is self:
                try:
                    self.phase.lease.end_source_cleanup(self)
                except BaseException as error:
                    if first is None:
                        first = error
            try:
                self._close_targets()
            except BaseException as error:
                if first is None:
                    first = error
            self.live = False
            try:
                self.witness.close()
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first

    async def record(self, mutation):
        """Persist actual source-event evidence under the existing operation."""
        from dataclasses import asdict
        from .native_registry import NativeRegistryMutation
        from .native_registry_receipts import _digest, _event_id
        from .native_registry_transition import prepared_registry_digest

        _need(self.live and self.witness.finished and self.rebound and self.attempted
              and self._issued_ref is None and not self._consumed
              and type(mutation) is NativeRegistryMutation and mutation is self.witness.mutation
              and asyncio.current_task() is self.task and set(self.issued) == set(self.affected)
              and all(self.owns_bridge(self.owner, name, self.target_bridges[name])
                      for name in self.affected),
              'completed_native_source_required')
        self.phase.check()
        emissions = {item.phase: item for item in self.witness.emissions
                     if item.phase in ('remove', 'add')}
        _need(set(emissions) == {'remove', 'add'} and all(item.event is not None
              and self.witness.is_owned_rename(item.event) for item in emissions.values()),
              'original_native_events_required')
        added = emissions['add'].event
        calculations = [row for row in self.owner.results if row.event is added]
        _need(len(calculations) == len(self.affected)
              and {row.producer.entity_id for row in calculations} == set(self.affected)
              and all(row.input_state is added.data['new_state'] for row in calculations),
              'actual_add_calculation_required')
        def state_row(item, key):
            captured = self.event_states.get(id(item.raw))
            _need(captured is not None and captured[0] is item.raw
                  and captured[1] is item.raw[key], 'emission_snapshot_changed')
            return {'native_json': captured[2], 'last_reported': captured[3]}
        payload = {'kind': 'powercalc_source_transition_receipt', 'schema': 1,
                   'root': list(self.phase.lifecycle.root_reference),
                   'registry_root': self.phase.lease.chain.root_id, 'intent': list(self.intent.reference),
                   'source_chain': self.phase.lease.state.head,
                   'registry_preparation': prepared_registry_digest(self.change),
                   'registry_uuid': self.change.identity, 'before_id': self.old, 'after_id': self.new,
                   'mutation': asdict(mutation), 'native_proof': self.proof,
                   'removed_state': state_row(emissions['remove'], 'old_state'),
                   'added_state': state_row(emissions['add'], 'new_state'),
                   'calculations': sorted(({'producer': row.producer.entity_id,
                       'value': str(row.calculated_value), 'native_power': str(row.native_power),
                       'emission_sequence': row.emission_sequence,
                       'completion_sequence': row.completion_sequence,
                       'emitted_at': row.emitted_at_timestamp} for row in calculations),
                       key=lambda row: row['producer'])}
        key = _event_id([self.phase.lifecycle.root_reference[0], 'source_transition',
                         self.change.identity, self.context.record.direction, 'receipt'])
        reference = await self.phase.lease.chain.store.create(key, payload)
        self.phase.check()
        _need(self.phase.lease.chain.store.read(key) == {'digest': _digest(payload), 'payload': payload},
              'source_receipt_readback')
        self._issued_ref = reference
        return reference

    def claim(self, chain, intent, context, mutation):
        _need(self.live and not self._consumed and self._issued_ref is not None
              and chain is self.phase.lease.chain and intent is self.intent
              and chain.active is intent and context == self.context
              and mutation is self.witness.mutation and asyncio.current_task() is self.task,
              'source_receipt_live_issuance_required')
        self.phase.check()
        return self._issued_ref

    def completed(self, reference):
        _need(not self._consumed and self._issued_ref is not None
              and type(reference) is tuple and len(reference) == 2,
              'source_receipt_already_consumed')
        row = self.phase.lease.chain.store.read(reference[0])
        _need(row is not None and row['digest'] == reference[1]
              and row['payload'].get('intent') == list(self.intent.reference)
              and row['payload'].get('powercalc_source_transition') == list(self._issued_ref),
              'source_receipt_before_listener_mapping')
        old = self.phase.scope.rename_sources
        mapped = {(entry, self.new if source == self.old else source) for entry, source in old}
        _need(len(mapped) == len(old), 'source_listener_mapping_collision')
        self.phase.scope.rename_sources = mapped
        self._consumed = True

    def close(self):
        _need(asyncio.current_task() is self.task and asyncio.get_running_loop() is self.loop,
              'owner_close_required')
        try:
            self._close_targets()
        finally:
            self.live = False
            if self.phase.scope.source_transition is self:
                self.phase.scope.source_transition = None
            self.witness.close()

    def abort_before_attempt(self):
        _need(self.live and not self.attempted and asyncio.current_task() is self.task,
              'pre_attempt_abort_required')
        try:
            self._close_targets()
        finally:
            self.live = False
            self.phase.scope.source_transition = None
            self.witness.close()
