"""One task-owned native rename of an unloaded Powercalc group placeholder."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json

from .native_registry import NativeRegistryIO, NativeRegistryMutation
from .native_registry_receipts import _digest, _event_id
from .native_registry_transition import prepared_registry_digest
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_group_own_id_' + reason)


def _live_unload(lifecycle, group_id, intent):
    # Unlike the PSS pre-intent helper, this runs inside a known registry
    # intent. Reconstruct its accepted prefix without treating that very intent
    # as an unresolved recovery operation.
    lifecycle.lease._task_check()
    lifecycle.check()
    chain = lifecycle.chain
    _, head, _, _, pending = chain._reconstruct()
    issued = lifecycle._unloaded.get(group_id)
    _need(chain.active is intent and pending == intent.reference
          and head == lifecycle.lease.state.head and type(issued) is tuple and len(issued) == 3,
          'live_unload_intent_binding')
    reference, source_head, entry = issued
    row = chain.store.read(reference[0])
    _need(row is not None and row['digest'] == reference[1] == _digest(row['payload'])
          and row['payload'].get('source_chain') == source_head
          and source_head in chain._setup_positions
          and lifecycle.lease.manager.async_get_entry(row['payload']['entry_id']) is entry
          and any(entry is item for item in lifecycle.lease.entries)
          and entry.state.value == 'not_loaded', 'live_unload_issuance_changed')
    return reference


class PowercalcGroupOwnID:
    def __init__(self):
        _need(False, 'capture_required')

    def __copy__(self):
        _need(False, 'live_node_not_copyable')

    __deepcopy__ = lambda self, memo: self.__copy__()

    def __reduce__(self):
        _need(False, 'live_node_not_serializable')

    @classmethod
    async def capture(cls, phase, io, change, context, intent, observations, guard):
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant, State, StateMachine
        from homeassistant.helpers import entity_registry as er
        from .powercalc_phase_session import PowercalcPhaseSession
        from .powercalc_group_receipts import group_lineage
        from .native_helper_placeholder_effect import _attrs, _fragment, _installed_effect_methods, _state_shape
        _need(cls is PowercalcGroupOwnID and type(phase) is PowercalcPhaseSession
              and phase.phase == 'sources_inactive' and type(io) is NativeRegistryIO
              and io.hass is phase.lease.hass and phase.lease.chain.active is intent
              and context.record.direction == phase.lifecycle.direction
              and context.record.direction in ('forward', 'rollback')
              and change == phase.lease.chain._original_change(context.index)
              and change.kind == 'entity' and change.mapping and change.before['platform'] == 'powercalc',
              'live_binding_required')
        phase.check(); guard()
        chain = phase.lease.chain
        chain.verify_intent_source(intent, context, observations)
        reverse = context.record.direction == 'rollback'
        old, new = change.before['entity_id'], change.after['entity_id']
        if reverse:
            old, new = new, old
        groups = [g for g in phase.lifecycle.groups
                  if g.entity_id == old and g.registry_id == change.identity]
        _need(len(groups) == 1, 'original_group_required')
        unload = _live_unload(phase.lifecycle, old, intent)
        root_id = phase.lifecycle.root_reference[0]
        def reference(key):
            row = chain.store.read(key)
            _need(row is not None and row['digest'] == _digest(row['payload']), 'handoff_missing')
            return [key, row['digest']]
        refs = {'unload': list(unload),
                'pss': reference(_event_id([root_id, 'pss_reverse' if reverse else 'pss_forward', 'receipt'])),
                'restore': reference(_event_id([root_id, 'restore_reverse' if reverse else 'restore_forward', old, 'receipt']))}
        if reverse:
            from .powercalc_group_receipts import reverse_group_lineage
            durable = reverse_group_lineage(chain, change, phase.lease.state.head, refs,
                                            phase.lifecycle.root_reference)
        else:
            durable = group_lineage(chain, phase.lifecycle.composition, change, phase.lease.state.head, refs)
        hass, registry = io.hass, io.registries['entity']
        _need(type(hass) is HomeAssistant and type(hass.states) is StateMachine
              and type(registry) is er.EntityRegistry and hass.data.get(er.DATA_REGISTRY) is registry
              and __version__ in ('2026.10.2', '2026.10.4'), 'native_runtime_required')
        row = registry.async_get(old)
        accepted = [r for r in chain.accepted_documents()['core.entity_registry']['data']['entities']
                    if r['id'] == change.identity]
        _need(row is not None and row.id == change.identity and len(accepted) == 1
              and json.loads(_fragment(row)) == accepted[0], 'accepted_registry_row_required')
        state_pin = phase.lifecycle._unload_states.get(old)
        state = hass.states.get(old)
        _need(type(state_pin) is tuple and len(state_pin) == 3 and type(state) is State
              and state is state_pin[0] and state.as_dict_json.decode() == state_pin[1] == durable['placeholder_json']
              and state.last_reported.isoformat() == state_pin[2] == durable['placeholder_last_reported'],
              'live_unload_state_required')
        attrs = _attrs(row, __version__, hass)
        _state_shape(state_pin[1], old, attrs)
        _need(registry.async_get(new) is None and hass.states.get(new) is None
              and hass.states.async_available(new), 'target_collision')
        obj = object.__new__(cls)
        obj.phase, obj.io, obj.change, obj.context, obj.intent = phase, io, change, context, intent
        obj.chain, obj.guard, obj.observations = chain, guard, observations
        obj.source, obj.target, obj.refs = old, new, refs
        from .powercalc_source_binding import PowercalcVendor
        obj.vendor = PowercalcVendor(hass, with_profiles=True)
        obj.hass, obj.registry, obj.machine, obj.row = hass, registry, hass.states, row
        obj.before_row = _fragment(row)
        obj.state_pin, obj.before_attrs = state_pin, attrs
        obj.source_reserved = old in hass.states._reservations
        obj.version, obj.pins, obj.methods = __version__, *_installed_effect_methods(__version__)
        obj.task, obj.loop, obj.stage, obj.issued, obj.consumed = asyncio.current_task(), asyncio.get_running_loop(), 'captured', None, False
        obj.private_registry_method = er.EntityRegistry._async_update_entity
        obj.io_mutate = io.mutate
        _need(obj.io_mutate.__func__ is NativeRegistryIO.mutate and obj.io_mutate.__self__ is io, 'native_io_changed')
        obj._live()
        return obj

    def _live(self):
        from homeassistant import core
        from homeassistant.const import __version__
        from homeassistant.helpers import entity_registry as er, entity
        from .powercalc_group_receipts import group_lineage
        _need(asyncio.current_task() is self.task and asyncio.get_running_loop() is self.loop
              and self.stage in ('captured', 'attempted', 'finished', 'recorded')
              and __version__ == self.version and self.phase.phase == 'sources_inactive', 'live_owner_required')
        self.phase.check(); self.guard()
        methods = (core.StateMachine.async_remove, er.RegistryEntry.write_unavailable_state,
                   er.EntityRegistry.async_update_entity, entity.Entity._Entity__async_remove_impl,
                   er.async_get_legacy_friendly_name)
        _need(er.EntityRegistry._async_update_entity is self.private_registry_method
              and all(a is b for a, b in zip(methods, self.methods, strict=True))
              and self.io.mutate.__func__ is self.io_mutate.__func__ and self.io.mutate.__self__ is self.io
              and self.hass.states is self.machine and self.io.registries['entity'] is self.registry
              and self.chain.active is self.intent and self.phase.lease.pending is self.intent,
              'native_binding_changed')
        unload = _live_unload(self.phase.lifecycle, self.source, self.intent)
        _need(list(unload) == self.refs['unload']
              and self.phase.lifecycle._unload_states.get(self.source) is self.state_pin, 'live_unload_changed')
        if self.context.record.direction == 'rollback':
            from .powercalc_group_receipts import reverse_group_lineage
            reverse_group_lineage(self.chain, self.change, self.phase.lease.state.head,
                                  self.refs, self.phase.lifecycle.root_reference)
        else:
            group_lineage(self.chain, self.phase.lifecycle.composition, self.change, self.phase.lease.state.head, self.refs)
        self._handoff_live()

    def _handoff_live(self):
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
        from .powercalc_pss_handoff import _store, _snapshot, _idle
        from .powercalc_restore_handoff import _captured
        from .native_restore_state_payload import _canonical
        self.vendor.bindings()
        pss = self.chain.store.read(self.refs['pss'][0])['payload']
        restore = self.chain.store.read(self.refs['restore'][0])['payload']
        intent = self.chain.store.read(restore['intent'][0])['payload']
        store = _store(self.phase.lifecycle, self.vendor)
        _need(_idle(store) and _snapshot(store).get(self.target) == pss['target'][self.target],
              'live_pss_target_changed')
        data = self.hass.data.get(DATA_RESTORE_STATE)
        _need(type(data) is RestoreStateData and data.hass is self.hass and _idle(data)
              and self.source not in data.entities and self.target not in data.entities
              and self.target in data.last_states
              and _captured(data.last_states[self.target]) == _canonical(intent['after']),
              'live_restore_target_changed')

    def _before(self):
        from .native_helper_placeholder_effect import _fragment
        state, body, reported = self.state_pin
        _need(self.registry.async_get(self.source) is self.row
              and _fragment(self.row) == self.before_row
              and self.machine.get(self.source) is state and state.as_dict_json.decode() == body
              and state.last_reported.isoformat() == reported
              and (self.source in self.machine._reservations) is self.source_reserved
              and self.registry.async_get(self.target) is None and self.machine.get(self.target) is None
              and self.machine.async_available(self.target), 'preimage_changed')

    def mutate(self):
        _need(self.stage == 'captured', 'one_attempt_only')
        self._live(); self.chain.verify_intent_source(self.intent, self.context, self.observations)
        self._before()
        self.stage = 'attempted'
        _need(not hasattr(self.io, '_powercalc_group_capability'), 'capability_already_installed')
        self.io._powercalc_group_capability = self
        try:
            target = self.change.before if self.context.record.direction == 'rollback' else self.change.after
            self.mutation = self.io.mutate(self.change, target)
        finally:
            del self.io._powercalc_group_capability
        _need(type(self.mutation) is NativeRegistryMutation and self.mutation.identity == self.change.identity
              and self.mutation.kind == 'entity' and self.mutation.before_json == self.before_row,
              'native_mutation_unconfirmed')
        self.returned = self.registry.async_get(self.target)
        _need(self.returned is not None and self.returned.id == self.change.identity
              and self.registry.async_get(self.source) is None, 'returned_row_changed')
        return self.mutation

    def authorize_suggested(self, io, change, target):
        from .native_registry_transition import _powercalc_group_suggested_move
        from homeassistant.helpers import entity_registry as er
        self._live()
        _need(self.stage == 'attempted' and io is self.io and change is self.change
              and target == (change.before if self.context.record.direction == 'rollback' else change.after)
              and self.version in ('2026.10.2', '2026.10.4')
              and _powercalc_group_suggested_move(change.before, change.after)
              and self.registry is er.async_get(self.hass)
              and self.registry.async_get(self.source) is self.row,
              'suggested_effect_scope')
        self._before()

    async def finish_native(self):
        self._live()

    async def finish_failed_attempt(self):
        # No retry or compensating registry call after entering mutate.
        self.stage = 'failed'

    def abort_before_attempt(self):
        _need(self.stage == 'captured', 'attempt_already_started')
        self.stage = 'aborted'

    async def record(self, mutation):
        from homeassistant.core import State
        from .native_helper_placeholder_effect import _attrs, _fragment, _state_shape
        self._live()
        _need(self.stage == 'attempted' and mutation is self.mutation
              and self.registry.async_get(self.target) is self.returned
              and _fragment(self.returned) == mutation.after_json
              and self.registry.async_get(self.source) is None and self.machine.get(self.source) is None
              and self.machine.async_available(self.source), 'native_settle_unconfirmed')
        state = self.machine.get(self.target)
        _need(type(state) is State and self.target not in self.machine._reservations, 'target_placeholder_missing')
        body, reported = state.as_dict_json.decode(), state.last_reported.isoformat()
        attrs = _attrs(self.returned, self.version, self.hass)
        _state_shape(body, self.target, attrs)
        self.after_pin = (state, body, reported)
        payload = {'kind': 'powercalc_group_own_id_receipt', 'schema': 1,
                   'root': list(self.phase.lifecycle.root_reference), 'registry_root': self.chain.root_id,
                   'intent': list(self.intent.reference), 'source_chain': self.phase.lease.state.head,
                   'registry_preparation': prepared_registry_digest(self.change), 'registry_uuid': self.change.identity,
                   'before_id': self.source, 'after_id': self.target, 'mutation': asdict(mutation),
                   'refs': self.refs, 'version': self.version, 'source_pins': self.pins,
                   'before_state': self.state_pin[1], 'before_reported': self.state_pin[2],
                   'before_attrs': self.before_attrs, 'after_state': body, 'after_reported': reported,
                   'after_attrs': attrs, 'source_reserved': self.source_reserved, 'target_reserved': False}
        key = _event_id([self.phase.lifecycle.root_reference[0], 'group_own_id', self.change.identity,
                         self.context.record.direction, 'receipt'])
        self.issued = await self.chain.store.create(key, payload)
        self._live()
        _need(self.chain.store.read(key) == {'digest': _digest(payload), 'payload': payload}
              and self.machine.get(self.target) is state and state.as_dict_json.decode() == body
              and state.last_reported.isoformat() == reported, 'receipt_or_placeholder_changed')
        self.stage = 'recorded'

    def claim(self, chain, intent, context, mutation):
        self._live()
        _need(self.stage == 'recorded' and not self.consumed and self.issued is not None
              and chain is self.chain and intent is self.intent and context == self.context
              and mutation is self.mutation, 'live_issuance_required')
        return self.issued

    def completed(self, reference):
        _need(self.stage == 'recorded' and not self.consumed and type(reference) is tuple, 'already_consumed')
        state, body, reported = self.after_pin
        _need(asyncio.current_task() is self.task and asyncio.get_running_loop() is self.loop
              and self.registry.async_get(self.target) is self.returned
              and self.registry.async_get(self.source) is None
              and self.machine.get(self.source) is None and self.machine.get(self.target) is state
              and state.as_dict_json.decode() == body and state.last_reported.isoformat() == reported,
              'placeholder_changed_during_registry_receipt')
        self.consumed = True
        self.stage = 'complete'
