"""Installed fresh reverse binding for one numeric Template ConfigEntry.

No new ORIGINAL, validation receipt, importer, operation root, epoch or endpoint.
A decoded composition is evidence only. Admission owns all native proof IO; the
caller supplies only its existing live runtime/admin guard and auth refresh.
"""
from __future__ import annotations

import inspect
import json

from .const import DATA_RUNTIME
from .transaction import Coordinator, PhaseContext, Refused
from .prepared import native_prepared_store, decode_change
from .journal import native_journal
from .native_registry_receipts import (RegistryReceiptChain, RegistryReceiptError, RegistryRawObservation,
                                      native_registry_receipt_store)
from .native_helper_lifecycle import (NativeHelperLifecycle, RestoreAuthorization,
    native_helper_lifecycle_store, validate_lifecycle_receipt_witness, _id)
from .native_helper_activation_lease import NativeHelperActivationLease
from .native_template_entry_lifecycle import NativeTemplateEntryObserver, validate_preparations
from .native_input_observation import async_observe_native_inputs
from .native_helper_setup_effects import NativeHelperSetupEffects
from .recorder_guard import NativeRecorderGuard
from .history_guard import NativeHistoryGuard
from .helper_native import read_entry
from .native_input_baseline import _Managers, _drained


def _need(value, reason):
    if not value:
        raise Refused('template_reverse_' + reason) from None


def _authorize(guard):
    result = guard()
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        _need(False, 'synchronous_guard_required')
    _need(result is None or result is True, 'authorization_rejected')


class NativeTemplateReverse:
    """One live task/lease, with all closures built from freshly opened stores.

    bind() is read-only. rollback() starts the already-supported reverse
    lifecycle once, on the SAME operation/plan/receipt chain. Reopen objects are
    never upgraded to writers. A second bind/effect with a reverse root refuses.
    The Runtime's shared operation lock must span bind through rollback/close.
    """
    def __init__(self):
        _need(False, 'installed_bind_required')

    @classmethod
    async def bind(cls, hass, operation_id, fingerprint, *, activation_lease,
                   guard, refresh, expected_head=None):
        from . import Runtime
        from homeassistant.core import HomeAssistant
        _need(cls is NativeTemplateReverse and type(hass) is HomeAssistant
              and type(operation_id) is str and type(fingerprint) is str
              and callable(guard) and callable(refresh), 'installed_binding_required')
        runtime = hass.data.get(DATA_RUNTIME)
        _need(type(runtime) is Runtime, 'runtime_required')
        _need(type(activation_lease) is NativeHelperActivationLease, 'lease_required')
        obj = object.__new__(cls)
        obj.hass, obj.runtime, obj.operation_id = hass, runtime, operation_id
        obj.fingerprint, obj.guard, obj.refresh = fingerprint, guard, refresh
        obj.lease = activation_lease
        obj.used, obj.failed, obj.bound = False, False, False
        obj.chain = obj.lifecycle = obj.observer = None

        def scope():
            _authorize(guard)
            _need(hass.data.get(DATA_RUNTIME) is runtime and runtime.loaded
                  and not runtime.unloading and runtime.operation_lock.locked(), 'runtime_scope_lost')
            obj.lease.check()
            if hasattr(obj, 'composition'):
                baseline, composition = obj.preparations.resolve_composition(obj.plan.native_inputs)
                _need(composition == obj.composition
                      and baseline.receipt == obj.receipt, 'composition_changed')
            if obj.bound:
                _need(runtime.journal is obj.journal and runtime.preparations is obj.preparations,
                      'runtime_cache_replaced')
                obj.preparations._ready()
                obj.receipt_store._ready()
                obj.lifecycle_store._ready()
            _need(obj.lease.manager is hass.config_entries, 'lease_manager')
        obj._scope = scope
        scope()
        await refresh()
        scope()
        # Do not trust instance-cached DurableJournal/PreparedStore from forward.
        obj.preparations, obj.journal = native_prepared_store(hass), native_journal(hass)
        await obj.preparations.initialize()
        scope()
        await obj.journal.initialize()
        scope()
        record = await obj.journal.load(operation_id)
        scope()
        _need(record is not None and record.plan.fingerprint == fingerprint
              and record.operation_id == operation_id and record.status == 'applied'
              and record.direction == 'forward'
              and record.phases == ('verified',) * len(record.plan.steps), 'terminal_applied_required')
        active = await obj.journal.active()
        scope()
        _need(not active, 'other_operation_unresolved')
        obj.record = record
        obj.plan = record.plan
        baseline, obj.composition = obj.preparations.resolve_composition(record.plan.native_inputs)
        obj.receipt, obj.evidence = baseline.receipt, obj.composition.evidence
        obj.digests = dict(obj.composition.prepared_digests)
        _need(obj.plan.coverage_evidence == obj.composition.coverage_sha256
              and set(obj.digests) == {s.prepared_ref for s in obj.plan.steps}
              and tuple(s.adapter for s in obj.plan.steps) == ('registry', 'helpers')
              and obj.plan.required_scopes == obj.plan.covered_scopes ==
                  obj.plan.verification_adapters == ('registry', 'helpers')
              and not any(s.caused_by for s in obj.plan.steps), 'qualified_plan_required')
        obj.registry_change = decode_change(obj.preparations.resolve(
            obj.plan.steps[0].prepared_ref, obj.digests[obj.plan.steps[0].prepared_ref]))
        changes = validate_preparations(obj.preparations, obj.plan, obj.receipt, obj.digests)
        _need(len(changes) == 1, 'one_numeric_entry_required')
        obj.entry_id, obj.change = next(iter(changes.items()))
        entry = hass.config_entries.async_get_entry(obj.entry_id)
        _need(entry is not None and obj.lease.entry_ids == (obj.entry_id,)
              and obj.lease.owns_entry(entry) and entry.state.value == 'loaded'
              and entry.disabled_by is None
              and read_entry(hass, obj.entry_id) == obj.change.after, 'activation_scope')
        obj.receipt_store = native_registry_receipt_store(hass, obj.preparations)
        obj.lifecycle_store = native_helper_lifecycle_store(hass, obj.preparations)
        await obj.receipt_store.initialize()
        scope()
        await obj.lifecycle_store.initialize()
        scope()
        readonly = RegistryReceiptChain.reopen(obj.receipt_store, operation_id, obj.plan,
                                               obj.receipt, obj.digests)
        _need(not readonly.writable and readonly.active is None, 'readonly_reopen_required')
        obj.chain = readonly
        state = readonly.reconstruct_inputs()  # Refuses every pending native intent.
        obj.head = state.head
        _need(expected_head is None or expected_head == obj.head, 'wrong_head')
        forward = obj._forward_witness()
        status = forward.inspect()
        _need(not forward.writable and not status['pending']
              and status['inactive_receipts_complete']
              and status['activation_receipts_complete'], 'forward_activation_incomplete')
        _need(len(forward.ids) == 1 and forward.ids == (obj.entry_id,)
              and forward.source_authorization is None
              and forward.root['original'][0]['state'] == 'loaded', 'forward_activation_scope')
        obj.authorization = RestoreAuthorization(operation_id, fingerprint, obj.head, 'applied')
        # The existing reverse lifecycle is derived from this same graph; any
        # previous attempt, acknowledged or uncertain, prohibits a new attempt.
        binding = {**forward.binding, 'source_authorization': {
            'operation_id': operation_id, 'fingerprint': fingerprint,
            'head': obj.head, 'status': 'applied'}}
        obj.reverse_root_id = _id(['helper_lifecycle_rollback', binding])
        _need(obj.lifecycle_store.read(obj.reverse_root_id) is None, 'reverse_already_started')
        obj.recorder = NativeRecorderGuard(hass)
        obj.history = NativeHistoryGuard(obj.recorder)
        obj.observer = NativeTemplateEntryObserver(hass, readonly, obj.evidence, guard=scope)
        obj.observer.lease = obj.lease
        # Admission sequence is installed, never caller-supplied proof=True:
        # actual full-three/source, metadata+history, loaded accepted consumer,
        # refreshed authorization, LAST full source/consumer continuation.
        await obj._accepted_forward()
        await obj._history_current()
        await obj.observer.verify_loaded(obj.change.after)
        scope()
        await refresh()
        scope()
        await obj._accepted_forward()
        await obj._final_loaded_history(obj.change.after)
        scope()
        obj._forward_witness()  # Recheck exact lifecycle graph after all awaits.
        _need(obj.lifecycle_store.read(obj.reverse_root_id) is None, 'reverse_already_started')
        # Construct a DIFFERENT live chain only after actual admission. The
        # readonly `readonly` object remains readonly, including its references.
        writer = RegistryReceiptChain(obj.receipt_store, operation_id, obj.plan,
                                      obj.receipt, obj.digests)
        _need(writer.root == readonly.root and writer.root_digest == readonly.root_digest
              and writer.reconstruct_inputs().same_evidence(readonly.reconstruct_inputs()), 'head_changed')
        writer.writable = True
        obj.chain = writer
        obj.observer.chain = writer
        # Make every subsequent installed callback use this authoritative cache.
        runtime.journal, runtime.preparations = obj.journal, obj.preparations
        obj.bound = True
        return obj

    def _forward_witness(self):
        self._scope()
        key = _id(['helper_lifecycle', self.operation_id, self.plan.fingerprint])
        forward = validate_lifecycle_receipt_witness(self.lifecycle_store, self.operation_id,
            self.plan, self.receipt, self.digests, root_id=key)
        status = forward.inspect()
        _need(not forward.writable and not status['pending']
              and status['inactive_receipts_complete'] and status['activation_receipts_complete'],
              'forward_activation_incomplete')
        return forward

    async def _accepted_forward(self):
        self._scope()
        record = await self.journal.load(self.operation_id)
        self._scope()
        _need(record == self.record, 'phase_changed')
        state = self.chain.reconstruct_inputs()
        _need(state.head == self.head, 'head_changed')
        observed = await async_observe_native_inputs(self.hass, self.receipt,
            guard=self._scope, input_state=state)
        self._scope()
        _need(self.chain.expected(PhaseContext(record, 0), tuple(RegistryRawObservation(
            f.key, f.raw, f.identity) for f in observed.files)) == self.head, 'head_changed')
        return observed

    async def _history_current(self):
        self._scope()
        mapping = self.change.mapping
        for scope, snapshot in ((mapping, self.composition.source_history),
                ({self.observer.rows[0]['entity_id']: self.observer.rows[0]['entity_id']},
                 self.composition.consumer_history)):
            metadata = await self.recorder.snapshot(scope)
            self._scope()
            _need(sorted({r['metadata_id'] for r in metadata['states'].values()}) == snapshot['state_ids']
                  and sorted({r['id'] for r in metadata['statistics'].values()}) == snapshot['statistic_ids'],
                  'history_identity_changed')
            if scope is mapping:
                from .recorder_guard import verify, verify_rollback
                old = next(iter(mapping))
                verifier = verify_rollback if old in metadata['states'] else verify
                _need(verifier(mapping, json.loads(self.registry_change.recorder_before_json),
                               metadata) is True, 'source_metadata_changed')
            _need(await self.history.verify(snapshot) is True, 'history_changed')
            self._scope()

    async def _final_loaded_history(self, snapshot):
        """Actual source + both finite histories at the final IO continuation.

        Same existing native SQL readers and whole accepted physical checker.
        The worker never reads manager objects. After its LAST await we compare
        manager identity/bytes and actual live consumer registrations on-loop.
        This is point-in-time evidence, not filesystem/SQL atomicity.
        """
        await self.observer.verify_loaded(snapshot)
        self._scope()
        first = self.observer.loaded_sample(snapshot)
        state = self.chain.reconstruct_inputs()
        await self.recorder.async_synchronize()
        self._scope()
        managers = _Managers(self.hass, self.receipt)
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.util import session_scope
        from .history_guard import _read_session as read_history
        from .recorder_guard import _read_session as read_metadata, verify, verify_rollback
        instance = self.recorder.instance
        _need(get_instance(self.hass) is instance, 'recorder_replaced')
        consumer = self.observer.rows[0]['entity_id']
        mapping = self.change.mapping

        def compare():
            state.assert_current(self.receipt)
            with session_scope(session=instance.get_session(), read_only=True) as sql:
                for historical in (self.composition.source_history, self.composition.consumer_history):
                    actual = read_history(sql, historical['state_ids'], historical['statistic_ids'],
                                          historical['content'])
                    _need(actual == historical['content'], 'history_changed')
                for ids, historical in ((tuple(sorted(set(mapping) | set(mapping.values()))),
                        self.composition.source_history), ((consumer,), self.composition.consumer_history)):
                    metadata = read_metadata(sql, ids)
                    _need(sorted({r['metadata_id'] for r in metadata['states'].values()}) == historical['state_ids']
                          and sorted({r['id'] for r in metadata['statistics'].values()}) == historical['statistic_ids'],
                          'history_identity_changed')
                    if historical is self.composition.source_history:
                        verifier = verify_rollback if next(iter(mapping)) in metadata['states'] else verify
                        _need(verifier(mapping, json.loads(self.registry_change.recorder_before_json), metadata),
                              'source_metadata_changed')
            state.assert_current(self.receipt)
        await _drained(self.hass.async_add_executor_job, compare)
        # Synchronous suffix: no source/auth/history gap introduced after IO.
        self._scope()
        _need(get_instance(self.hass) is instance, 'recorder_replaced')
        managers.check()
        _need(state.same_evidence(self.chain.reconstruct_inputs()), 'head_changed')
        _need(self.observer.loaded_sample(snapshot) == first, 'consumer_changed')
        self.loaded_admission_sample = first

    async def _observe(self):
        self._scope()
        # The previous accepted head may remain usable while OUR native intent
        # is live; the same chain checks live object identity, never a saved ref.
        try:
            self.observation_state = self.chain.reconstruct_inputs()
        except RegistryReceiptError:
            _need(self.chain.active is not None, 'unresolved_intent')
            pending = self.chain.store.read(self.chain.active.reference[0])
            _need(pending is not None and pending['payload'].get('kind') in
                  ('registry_intent', 'helper_setup_intent'), 'unresolved_intent')
        value = await async_observe_native_inputs(self.hass, self.receipt,
            guard=self._scope, input_state=self.observation_state)
        self._scope()
        return value

    async def _accepted(self, authorization):
        self._scope()
        record = await self.journal.load(self.operation_id)
        self._scope()
        _need(record is not None, 'journal_missing')
        observed = await self._observe()
        self.chain.expected(PhaseContext(record, 0), tuple(RegistryRawObservation(
            f.key, f.raw, f.identity) for f in observed.files))
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        _need(entry is not None, 'entry_missing')
        if entry.state.value == 'loaded':
            if authorization.status == 'applied':
                # Still before this reverse cycle's unload: the retained loaded
                # entity must be exactly the same real graph as admission.
                _need(self.observer.live_sample(self.change.after) == self.loaded_admission_sample,
                      'consumer_changed_before_unload')
            else:
                self.observer.loaded_sample(self.change.before)
        else:
            await self.observer.verify_inactive(self.entry_id)
            self._scope()
        return self.chain.verify_setup_authorization(authorization)

    async def rollback(self):
        self._scope()
        _need(self.bound and not self.used and not self.failed, 'no_replay')
        self.used = True  # Never retry this session, including cancellation/ACK loss.
        try:
            _need(self.runtime.journal is self.journal
                  and self.runtime.preparations is self.preparations, 'runtime_cache_replaced')
            # Re-read disk stores before any first effect rather than trusting
            # the state retained during a gap after bind(). No root is recreated.
            check_store = native_helper_lifecycle_store(self.hass, self.preparations)
            await check_store.initialize()
            self._scope()
            _need(check_store.rows == self.lifecycle_store.rows, 'lifecycle_changed')
            check_journal = native_journal(self.hass)
            await check_journal.initialize()
            self._scope()
            _need(await check_journal.load(self.operation_id) == self.record, 'phase_changed')
            self._scope()
            await self.refresh()
            self._scope()
            await self._history_current()
            await self._accepted_forward()
            await self._final_loaded_history(self.change.after)
            self._forward_witness()
            self.observation_state = self.chain.reconstruct_inputs()
            self.observer.capture_loaded_witness(self.change.after)

            async def original(*_):
                _need(False, 'original_fence_not_valid_in_reverse')

            async def loaded(entry_id, activation, authorization):
                _need(entry_id == self.entry_id, 'activation_scope')
                await self.observer.verify_loaded(self.change.before
                    if authorization.status == 'rolled_back' else self.change.after)
                return True

            self.lifecycle = await NativeHelperLifecycle.start(self.lifecycle_store,
                self.hass.config_entries, self.operation_id, self.plan, self.receipt, self.digests,
                guard=self._scope, verify_original_native_inputs=original,
                settle=self.observer.settle, verify_inactive=self.observer.verify_inactive,
                verify_runtime=loaded, verify_accepted_native_chain=self._accepted,
                source_authorization=self.authorization, activation_lease=self.lease,
                setup_effect=NativeHelperSetupEffects(self.hass, self.chain, self._observe,
                                                      self.journal.load, guard=self._scope))
            await self.lifecycle.quiesce()
            await self.observer.verify_inactive(self.entry_id)
            coordinator = self._coordinator()
            result = await coordinator.rollback(self.operation_id)
            self._scope()
            _need(result.status == 'rolled_back', 'rollback_unresolved')
            # A Template native API lease additionally pins the acquired options.
            # Reacquire at the verified original snapshot before setup.
            _need(read_entry(self.hass, self.entry_id) == self.change.before, 'rollback_config')
            self.lease.release()
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            _authorize(self.guard)
            self.lease = await NativeHelperActivationLease.acquire(self.hass,
                self.hass.config_entries, (entry,), guard=self.guard)
            self.observer.lease = self.lifecycle.activation_lease = self.lease
            self._scope()
            state = self.chain.reconstruct_inputs()
            authorization = RestoreAuthorization(self.operation_id, self.fingerprint,
                                                  state.head, 'rolled_back')
            await self.lifecycle.restore(authorization)
            await self.observer.verify_loaded(self.change.before)
            await self._history_current()
            self._scope()
            await self.refresh()
            self._scope()
            await self._final_loaded_history(self.change.before)
            _need(self.lifecycle.inspect()['activation_receipts_complete']
                  and not self.lifecycle.inspect()['pending'], 'activation_incomplete')
            self.chain.reconstruct_inputs()
            return result
        except BaseException:
            self.failed = True
            raise

    def _coordinator(self):
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .helper_transaction import HelperTransactionAdapter
        from .native_helper_transaction import ReceiptedHelperTransactionAdapter
        from .native_registry_transaction import ReceiptedRegistryTransactionAdapter
        resolver = lambda ref: decode_change(self.preparations.resolve(ref, self.digests[ref]))
        inner = HelperTransactionAdapter(self.hass, resolver,
            resolve_step=lambda key: next(s for s in self.plan.steps if s.key == key),
            settle=self.observer.settle, validate_snapshot=self.observer.validate_snapshot,
            verify_activation=self.observer.verify_activation, verify_runtime=self.observer.verify_activation,
            verify_inventory=self.observer.verify_inventory,
            verify_original_inventory=self.observer.verify_original_inventory)

        async def full(reverse):
            await self.observer.verify_inventory_snapshot(self.change.before if reverse else self.change.after)
            await self._history_current()
            return True

        helper = ReceiptedHelperTransactionAdapter(inner, self.chain, self._observe, self.journal.load,
            self.lifecycle.require_quiescent, full_verify=lambda _: full(False),
            full_verify_rollback=lambda _: full(True), activation_lease=self.lease)
        bridge = NativeRegistryBridge(NativeRegistryIO(self.hass), self.recorder,
                                      native_images=self.chain.accepted_documents)
        registry = ReceiptedRegistryTransactionAdapter(bridge, resolver, self.chain, self._observe,
            self.journal.load, helper.guard, full_verify=lambda _: full(False),
            full_verify_rollback=lambda _: full(True))
        return Coordinator({'registry': registry, 'helpers': helper}, self.journal,
                           helper.guard, required_scopes=('registry', 'helpers'))

    def close(self):
        self.bound = False
        self.lease.release()
