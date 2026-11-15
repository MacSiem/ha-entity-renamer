"""Fresh, task-owned read admission for a Powercalc inverse on the same chain.

A durable forward receipt is evidence, not a live writer. This module creates a
separate writer only after the installed runtime, current sources and history
have been checked again under the existing operation lock. It performs no
inverse effect and never resumes an uncertain intent.
"""
from __future__ import annotations

import asyncio

from .const import DATA_RUNTIME
from .native_registry_receipts import (RegistryReceiptChain, native_registry_receipt_store, _event_id,
                                       _exact_json_equal, _freeze_json)
from .powercalc_composition import PowercalcComposition
from .powercalc_lifecycle import PowercalcLifecycle
from .powercalc_runtime_observer import capture_powercalc_runtime, _guard
from .transaction import Refused


def _need(condition, reason):
    if not condition:
        raise Refused('powercalc_reverse_' + reason)


class PowercalcReverseAdmission:
    """A new live admission, not a promotion of a readonly receipt view."""

    def __init__(self):
        raise Refused('powercalc_reverse_installed_bind_required')

    @classmethod
    async def bind(cls, hass, runtime, operation_id, fingerprint, *, guard, refresh):
        from homeassistant.core import HomeAssistant
        from . import Runtime
        from .history_guard import NativeHistoryGuard
        from .journal import native_journal
        from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
        from .prepared import native_prepared_store
        from .recorder_guard import NativeRecorderGuard

        _need(cls is PowercalcReverseAdmission and type(hass) is HomeAssistant
              and type(runtime) is Runtime and hass.data.get(DATA_RUNTIME) is runtime
              and runtime.loaded and not runtime.unloading and runtime.operation_lock.locked()
              and type(operation_id) is str and bool(operation_id)
              and type(fingerprint) is str and bool(fingerprint)
              and callable(guard) and callable(refresh), 'installed_scope_required')
        obj = object.__new__(cls)
        obj.hass, obj.runtime, obj.operation_id, obj.fingerprint = hass, runtime, operation_id, fingerprint
        obj.guard, obj.refresh = guard, refresh
        obj.owner, obj.loop = asyncio.current_task(), asyncio.get_running_loop()
        obj.lock = runtime.operation_lock
        obj.closed = False
        obj._scope()
        await refresh()
        obj._scope()
        obj.preparations, obj.journal = native_prepared_store(hass), native_journal(hass)
        await obj.preparations.initialize()
        obj._scope()
        await obj.journal.initialize()
        obj._scope()
        record = await obj.journal.load(operation_id)
        obj._scope()
        _need(record is not None and record.operation_id == operation_id
              and record.plan.fingerprint == fingerprint and record.status == 'applied'
              and record.direction == 'forward'
              and record.phases == ('verified',) * len(record.plan.steps), 'terminal_forward_required')
        _need(not await obj.journal.active(), 'other_operation_unresolved')
        obj._scope()
        obj.record, obj.plan = record, record.plan
        baseline, composition = obj.preparations.resolve_composition(obj.plan.native_inputs)
        _need(type(composition) is PowercalcComposition
              and obj.plan.coverage_evidence == composition.coverage_sha256,
              'original_composition_required')
        obj.baseline, obj.composition, obj.evidence = baseline, composition, composition.evidence
        obj.receipt, obj.digests = baseline.receipt, dict(composition.prepared_digests)
        _need(set(obj.digests) == {step.prepared_ref for step in obj.plan.steps}
              and not any(step.caused_by for step in obj.plan.steps), 'plan_scope_required')
        obj.receipts = native_registry_receipt_store(hass, obj.preparations)
        await obj.receipts.initialize()
        obj._scope()
        readonly = RegistryReceiptChain.reopen(obj.receipts, operation_id, obj.plan, obj.receipt, obj.digests)
        state = readonly.reconstruct_inputs()
        _need(not readonly.writable and readonly.active is None, 'readonly_forward_required')
        obj.readonly, obj.state = readonly, state
        obj.forward = PowercalcLifecycle.reopen(readonly)
        obj.reverse_root = _event_id(['powercalc_lifecycle_rollback', operation_id, fingerprint, state.head])
        _need(obj.receipts.read(obj.reverse_root) is None, 'inverse_already_started')
        history = NativeHistoryGuard(NativeRecorderGuard(hass))

        async def retained_aliases():
            # A forward receipt binds the old aliases, but does not authorize
            # a fresh mutation. Read the current managers AND pinned files.
            from decimal import Decimal
            from pathlib import Path
            from homeassistant.core import CoreState
            from homeassistant.helpers.entity_platform import async_get_platforms
            from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
            from homeassistant.helpers.storage import Store
            from .native_input_baseline import _drained, _loads
            from .native_restore_state_payload import _canonical
            from .powercalc_lifecycle import _moves
            from .powercalc_previous_state_original import _PinnedPSS, _disk_shape
            from .powercalc_pss_handoff import _idle, _snapshot
            from .powercalc_restore_handoff import _captured, _rows
            from .powercalc_source_binding import PowercalcVendor
            from .native_registry_receipts import _digest

            moves = _moves(readonly, composition)
            _need(len(moves) == 1 and moves[0].before != moves[0].after,
                  'one_forward_group_move_required')
            move = moves[0]
            root_ref = list(obj.forward.root_reference)
            root_id = root_ref[0]
            def event(key, kind):
                item = obj.receipts.read(key)
                _need(item is not None and item['digest'] == _digest(item['payload'])
                      and item['payload'].get('kind') == kind
                      and item['payload'].get('root') == root_ref, 'forward_alias_receipt_required')
                return item
            pss_intent_key = _event_id([root_id, 'pss_forward', 'intent'])
            pss_result_key = _event_id([root_id, 'pss_forward', 'receipt'])
            pss_intent = event(pss_intent_key, 'powercalc_pss_forward_intent')
            pss_result = event(pss_result_key, 'powercalc_pss_forward_receipt')
            _need(pss_result['payload']['intent'] == [pss_intent_key, pss_intent['digest']]
                  and pss_result['payload']['source_chain'] == pss_intent['payload']['source_chain'],
                  'forward_pss_lineage_changed')
            restore_intent_key = _event_id([root_id, 'restore_forward', move.before, 'intent'])
            restore_result_key = _event_id([root_id, 'restore_forward', move.before, 'receipt'])
            restore_intent = event(restore_intent_key, 'powercalc_group_restore_intent')
            restore_result = event(restore_result_key, 'powercalc_group_restore_receipt')
            _need(restore_result['payload']['intent'] == [restore_intent_key, restore_intent['digest']]
                  and restore_result['payload']['pss'] == [pss_result_key, pss_result['digest']],
                  'forward_restore_lineage_changed')
            expected_old = pss_intent['payload']['source'].get(move.before)
            expected_restore = _canonical(restore_intent['payload']['before'])
            uuid_mode = obj.receipt.version == '2026.11.0'
            _need(type(expected_old) is dict, 'forward_old_alias_required')
            vendor = PowercalcVendor(hass, with_profiles=True)
            vendor.bindings()
            store = hass.data.get('powercalc_group_storage')
            restore = hass.data.get(DATA_RESTORE_STATE)
            _need(type(store) is vendor.classes['PreviousStateStore'] and store.hass is hass
                  and type(store.store) is vendor.classes['PreviousStateStoreStore']
                  and store.store.hass is hass and store.store.key == 'powercalc_group'
                  and type(restore) is RestoreStateData and restore.hass is hass
                  and type(restore.store) is Store and restore.store.hass is hass
                  and restore.store.key == 'core.restore_state'
                  and hass.state is CoreState.running and _idle(store) and _idle(restore),
                  'native_alias_stores_not_idle')
            groups = [entity for platform in async_get_platforms(hass, 'powercalc')
                      for entity in platform.entities.values() if entity.entity_id == move.after]
            expected_group = next(group for group in composition.previous.groups if group.entity_id == move.before)
            _need(len(groups) == 1 and type(groups[0]) is vendor.classes['GroupedEnergySensor']
                  and groups[0].registry_entry.id == expected_group.registry_id
                  and groups[0]._prev_state_store is store
                  and type(groups[0]._native_value_exact) is Decimal
                  and groups[0]._native_value_exact.is_finite(), 'current_group_identity_changed')
            memory = _snapshot(store)
            _need(_exact_json_equal(memory.get(move.before), _freeze_json(expected_old)) and move.after in memory,
                  'retained_alias_changed')
            if uuid_mode:
                from .native_restore_uuid import owned_slot
                current_restore = owned_slot(restore, move.after, move.before,
                                             expected_group.registry_id, required=True)
                current_restore_bytes = _captured(current_restore)
            else:
                _need(move.before not in restore.entities and move.before in restore.last_states
                      and _captured(restore.last_states[move.before]) == expected_restore,
                      'retained_alias_changed')
            checks = (('powercalc_group', store), ('core.restore_state', restore))
            observations = []
            for key, owner in checks:
                pinned = _PinnedPSS(obj.receipt, key=key, input_state=state)
                try:
                    raw, identity = await _drained(hass.async_add_executor_job, pinned.capture)
                    obj._scope()
                    _need(raw is not None and identity is not None
                          and pinned.root_sig[:3] == composition.previous.root_identity
                          and pinned.storage_sig[:3] == composition.previous.storage_identity
                          and _idle(owner), 'retained_alias_disk_unavailable')
                    if key == 'powercalc_group':
                        _disk_shape(raw)
                        _need(_exact_json_equal(_loads(raw)['data'].get(move.before), _freeze_json(expected_old)),
                              'retained_pss_disk_changed')
                    else:
                        if uuid_mode:
                            disk = _rows(raw, (move.before, move.after), owned_uuid=expected_group.registry_id)
                            _need(disk[move.before] is None
                                  and disk[move.after] == current_restore_bytes,
                                  'fresh_uuid_restore_disk_mismatch')
                        else:
                            _need(_rows(raw, (move.before,))[move.before] == expected_restore,
                                  'retained_restore_disk_changed')
                    await _drained(hass.async_add_executor_job, pinned.verify)
                    obj._scope()
                    _need(_idle(owner), 'retained_alias_writer_changed')
                    observations.append((key, identity))
                finally:
                    pinned.close()
            _need(_exact_json_equal(_snapshot(store).get(move.before), _freeze_json(expected_old))
                  and _idle(store) and _idle(restore), 'retained_alias_changed_during_read')
            if uuid_mode:
                _need(owned_slot(restore, move.after, move.before, expected_group.registry_id, required=True)
                      is current_restore and _captured(current_restore) == current_restore_bytes,
                      'retained_uuid_changed_during_read')
            else:
                _need(restore.last_states.get(move.before) is not None
                      and _captured(restore.last_states[move.before]) == expected_restore,
                      'retained_alias_changed_during_read')
            vendor.bindings()
            return tuple(observations)

        async def fresh_check():
            obj._scope()
            _need(await obj.journal.load(operation_id) == record, 'forward_record_changed')
            obj._scope()
            _need(state.same_evidence(readonly.reconstruct_inputs())
                  and obj.receipts.read(obj.reverse_root) is None, 'accepted_head_changed')
            for snapshot in (composition.source_history, composition.consumer_history):
                _need(await history.verify(snapshot) is True, 'forward_history_changed')
                obj._scope()
            capture = capture_powercalc_runtime(hass, guard=obj._scope)
            observed = await async_observe_accepted_powercalc_consumers(
                hass, readonly, obj.evidence, capture, guard=obj._scope)
            obj._scope()
            _need(observed.source_position == 'proposed' and observed.head == state.head
                  and observed.rows and all(row.source_verified for row in observed.rows),
                  'accepted_live_graph_required')
            aliases = await retained_aliases()
            obj._scope()
            _need(PowercalcLifecycle.reopen(readonly) == obj.forward
                  and state.same_evidence(readonly.reconstruct_inputs()), 'forward_lifecycle_changed')
            return capture, observed, aliases

        await fresh_check()
        await refresh()
        obj._scope()
        obj.capture, obj.observed, obj.aliases = await fresh_check()
        obj._scope()
        _need(await obj.journal.load(operation_id) == record, 'forward_record_changed')
        obj._scope()
        _need(state.same_evidence(readonly.reconstruct_inputs())
              and obj.receipts.read(obj.reverse_root) is None, 'accepted_head_changed')
        writer = RegistryReceiptChain(obj.receipts, operation_id, obj.plan, obj.receipt, obj.digests)
        _need(writer.root == readonly.root and writer.root_digest == readonly.root_digest
              and writer.reconstruct_inputs().same_evidence(state), 'writer_original_changed')
        # This private writer is held by the current task and conveys no
        # lifecycle/effect capability. Each effect must bind fresh native proof.
        writer.writable = True
        obj.chain = writer
        runtime.preparations, runtime.journal = obj.preparations, obj.journal
        return obj

    def _live(self):
        _guard(self.guard)
        _need(not self.closed and self.owner is not None and not self.owner.done()
              and asyncio.get_running_loop() is self.loop and self.hass.loop is self.loop
              and self.hass.data.get(DATA_RUNTIME) is self.runtime
              and self.runtime.loaded and not self.runtime.unloading
              and self.runtime.operation_lock is self.lock and self.lock.locked(), 'live_owner_required')

    def _scope(self):
        self._live()
        _need(asyncio.current_task() is self.owner, 'live_owner_required')

    def close(self):
        self._scope()
        self.closed = True
