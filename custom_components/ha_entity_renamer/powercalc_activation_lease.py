"""Exact Powercalc entry locks for a live accepted ORIGINAL chain.

This lease is one lifecycle prerequisite, never a migration grant: it cannot
reset YAML platforms, mutate PSS, unload or set up entries. The installed
controller must also own the coordinator/editor lease, prove YAML ownership,
write durable intent before effects, and resolve completion with receipts.
"""
from __future__ import annotations

import asyncio
import json

from .native_helper_activation_lease import _idle
from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
from .native_registry_receipts import RegistryReceiptChain
from .powercalc_runtime_observer import _guard


class PowercalcLeaseError(ValueError):
    """Sanitized refusal before effect; no authority to replay an operation."""


def _need(condition, reason):
    if not condition:
        raise PowercalcLeaseError('powercalc_lease_' + reason) from None


class PowercalcActivationLease:
    """Task-bound ownership of every ORIGINAL Powercalc ConfigEntry lock."""
    def __init__(self):
        raise PowercalcLeaseError('powercalc_lease_acquisition_required')

    @classmethod
    async def acquire(cls, hass, chain, evidence, original, *, guard, reverse_admission=None):
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant.core import HomeAssistant
        from homeassistant.const import __version__
        _need(cls is PowercalcActivationLease and type(hass) is HomeAssistant
              and type(hass.config_entries) is ConfigEntries
              and type(chain) is RegistryReceiptChain and chain.writable
              and chain.active is None and __version__ in ('2026.10.2', '2026.10.4')
              and callable(guard), 'live_original_chain_required')
        _guard(guard)
        if reverse_admission is not None:
            from .powercalc_reverse_admission import PowercalcReverseAdmission
            _need(type(reverse_admission) is PowercalcReverseAdmission
                  and reverse_admission.chain is chain and reverse_admission.hass is hass
                  and reverse_admission.evidence is evidence and reverse_admission.capture is original,
                  'accepted_reverse_admission_required')
            reverse_admission._scope()
        observed = await async_observe_accepted_powercalc_consumers(
            hass, chain, evidence, original, guard=guard)
        _need(observed.source_position == ('proposed' if reverse_admission is not None else 'original')
              and observed.rows and all(row.source_verified for row in observed.rows),
              'accepted_reverse_graph_required' if reverse_admission is not None else 'original_graph_required')
        if reverse_admission is not None:
            reverse_admission._scope()
            _need(observed.head == reverse_admission.state.head, 'reverse_head_changed')
        graph = json.loads(evidence.powercalc_sources.closed_graph_json)
        origins = {row['origin'][6:] for family in ('profiles', 'groups')
                   for row in graph[family] if row['origin'].startswith('entry:')}
        entries = tuple(sorted(hass.config_entries.async_entries('powercalc'), key=lambda e: e.entry_id))
        _need(bool(entries) and all(type(entry) is ConfigEntry and entry.domain == 'powercalc'
              and entry.state.value == 'loaded' for entry in entries)
              and {entry.entry_id for entry in entries} == origins,
              'complete_loaded_entry_scope_required')
        locks = tuple(entry.setup_lock for entry in entries)
        _need(all(type(lock) is asyncio.Lock and _idle(lock) for lock in locks)
              and len({id(lock) for lock in locks}) == len(locks), 'entry_lock_busy')
        obj = object.__new__(cls)
        obj.hass, obj.chain, obj.evidence, obj.original = hass, chain, evidence, original
        obj.manager, obj.receipt = hass.config_entries, chain.original_receipt
        obj.guard, obj.observed, obj.entries, obj.locks = guard, observed, entries, locks
        obj.reverse_admission = reverse_admission
        obj.reverse_phase = None
        obj.owner, obj.loop = asyncio.current_task(), asyncio.get_running_loop()
        obj.state = chain.reconstruct_inputs()
        obj.owned, obj.live, obj.pending = [], False, None
        obj._cleanup_owner = None
        _need(obj.owner is not None and obj.loop is hass.loop
              and obj.state.head == observed.head, 'same_task_head_required')
        try:
            # No await between the idle census and acquisition. _idle refuses
            # queued waiters too; do not jump another native setup request.
            for lock in locks:
                acquire = lock.acquire()
                try:
                    try:
                        acquire.send(None)
                    except StopIteration as done:
                        _need(done.value is True and lock.locked(), 'lock_acquisition_unknown')
                    else:
                        raise PowercalcLeaseError('powercalc_lease_acquisition_suspended')
                    obj.owned.append(lock)
                finally:
                    acquire.close()
            obj.live = True
            obj.check()
            return obj
        except BaseException:
            obj._release()
            raise

    def _binding(self):
        from homeassistant.const import __version__
        owner = self._cleanup_owner
        if owner is None or owner.witness._registry_emission() is None:
            _guard(self.guard)
        else:
            self._check_cleanup_owner(owner)
        _need(self.live and self.owner is not None and not self.owner.done()
              and asyncio.get_running_loop() is self.loop and self.hass.loop is self.loop
              and __version__ == self.observed.version and self.chain.writable
              and self.hass.config_entries is self.manager and self.manager.hass is self.hass
              and self.chain.original_receipt is self.receipt, 'owner_or_original_head_changed')
        current = tuple(sorted(self.manager.async_entries('powercalc'), key=lambda e: e.entry_id))
        _need(len(current) == len(self.entries)
              and all(a is b for a, b in zip(current, self.entries, strict=True))
              and len(self.owned) == len(self.locks)
              and all(held is lock and lock.locked() and entry.setup_lock is lock
                      for entry, lock, held in zip(self.entries, self.locks, self.owned, strict=True)),
              'entry_identity_or_lock_changed')
        self.evidence.assert_receipt(self.receipt)
        if self.reverse_admission is not None:
            if asyncio.current_task() is self.owner:
                self.reverse_admission._scope()
            else:
                # Read checks inside native descendants belong to this exact
                # live phase; effect admission remains owner-task-only.
                phase = self.reverse_phase
                self.reverse_admission._live()
                _need(phase is not None and not phase.closed and phase.lease is self
                      and phase.owner is self.owner and phase.token is not None
                      and phase.scope.causal.get(None) is phase.scope.marker,
                      'reverse_native_descendant_required')
            _need(self.reverse_admission.chain is self.chain, 'reverse_admission_changed')

    def _check_cleanup_owner(self, transition):
        """Cleanup may finish an issued native delivery, never start a write."""
        from .powercalc_source_transition import PowercalcSourceTransition
        witness = getattr(transition, 'witness', None)
        event = None if witness is None else witness._registry_emission()
        _need(type(transition) is PowercalcSourceTransition and transition.live
              and transition.phase.lease is self and transition.attempted
              and transition.intent is self.pending and self.chain.active is self.pending
              and transition.phase.scope.source_transition is transition
              and witness.attempted and witness.io is transition.io
              and witness.change is transition.change and event is not None
              and witness._data_current(event)
              and witness.registry.async_get(witness.target_id) is not None
              and witness.registry.async_get(witness.target_id).id == transition.change.identity
              and witness.registry.async_get(witness.source_id) is None,
              'issued_source_cleanup_required')

    def begin_source_cleanup(self, transition):
        from .powercalc_source_transition import PowercalcSourceTransition
        self._task_check()
        _need(self._cleanup_owner is None, 'cleanup_already_active')
        self._binding()  # Real authorization before the native attempt.
        _need(type(transition) is PowercalcSourceTransition and transition.live
              and transition.phase.lease is self and transition.attempted
              and transition.intent is self.pending and self.chain.active is self.pending
              and transition.phase.scope.source_transition is transition
              and not transition.witness.attempted and transition.witness.io is transition.io
              and transition.witness.change is transition.change,
              'source_cleanup_preflight_required')
        self._cleanup_owner = transition

    def end_source_cleanup(self, transition):
        self._task_check()
        _need(self._cleanup_owner is transition, 'cleanup_owner_changed')
        self._cleanup_owner = None

    def check(self):
        self._binding()
        if self.pending is None:
            _need(self.chain.active is None
                  and self.state.same_evidence(self.chain.reconstruct_inputs()), 'accepted_head_changed')
        else:
            _, head, _, _, pending = self.chain._reconstruct()
            _need(self.chain.active is self.pending and pending == self.pending.reference
                  and head == self.state.head, 'pending_intent_changed')

    def _task_check(self):
        _need(asyncio.current_task() is self.owner and asyncio.get_running_loop() is self.loop,
              'owner_task_required')

    def track_intent(self, intent):
        """Bind only a successfully persisted intent from this exact live chain."""
        self._task_check()
        from .native_registry_receipts import RegistryIntent
        _need(self._cleanup_owner is None, 'cleanup_cannot_start_write')
        self._binding()
        _need(self.pending is None and type(intent) is RegistryIntent
              and self.chain.active is intent, 'live_intent_required')
        _, head, _, _, pending = self.chain._reconstruct()
        row = self.chain.store.read(intent.reference[0])
        _need(pending == intent.reference and head == self.state.head and row is not None
              and row['digest'] == intent.reference[1]
              and row['payload'].get('kind') in ('registry_intent', 'yaml_intent', 'powercalc_energy_intent')
              and row['payload'].get('chain') == self.chain.root_id
              and row['payload'].get('source_chain') == head, 'intent_binding')
        self.pending = intent
        self.check()

    def accept_receipt(self, reference):
        """Advance only through the existing chain's actual typed receipt."""
        self._task_check()
        _need(self._cleanup_owner is None, 'cleanup_cannot_accept_receipt')
        self._binding()
        _need(self.pending is not None and self.chain.active is None
              and type(reference) is tuple and len(reference) == 2, 'receipt_required')
        row = self.chain.store.read(reference[0])
        _need(row is not None and row['digest'] == reference[1]
              and row['payload'].get('kind') in ('registry_receipt', 'yaml_receipt', 'powercalc_energy_receipt')
              and row['payload'].get('chain') == self.chain.root_id
              and row['payload'].get('source_chain') == self.state.head
              and row['payload'].get('intent') == list(self.pending.reference), 'receipt_binding')
        accepted = self.chain.reconstruct_inputs()
        _need(accepted.head == reference[1], 'accepted_receipt_head')
        self.state, self.pending = accepted, None
        self.check()

    def _release(self):
        self.live = False
        for lock in reversed(self.owned):
            if lock.locked():
                lock.release()
        self.owned.clear()

    def release(self):
        _need(asyncio.current_task() is self.owner
              and asyncio.get_running_loop() is self.loop, 'release_owner_required')
        try:
            self.check()
        finally:
            # A revoked guard or changed chain must not strand issued locks.
            self._release()
