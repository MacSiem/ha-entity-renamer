"""Trusted registry-only Coordinator adapter with full-input durable receipts.

Caller owns shared coordinator/native editor locks and external lease. Observer
must be async_observe_native_inputs (or an equally authenticated IO seam), never
client data. No endpoint, YAML exception or migration-readiness claim is added.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json

from .native_input_observation import NativeInputObservation
from .native_registry import NativeRegistryMutation, RegistryTransactionAdapter
from .native_registry_receipts import RegistryRawObservation
from .native_registry_transition import NativeRegistryEffectAudit, prepared_registry_digest
from .transaction import PhaseContext, Position, Refused


class ReceiptedRegistryTransactionAdapter(RegistryTransactionAdapter):
    def __init__(self, bridge, resolver, chain, observer, phase_loader, guard, *,
                 full_verify=None, full_verify_rollback=None):
        super().__init__(bridge, resolver, full_verify, full_verify_rollback)
        self.chain, self.observer = chain, observer
        self.phase_loader, self.guard = phase_loader, guard
        self.context = None
        self._own_escrows = {}

    def bind_own_helper_escrow(self, escrow, token, *, source_fence):
        """Trusted ephemeral composition; saved escrow data cannot bind a writer.

        source_fence(context) synchronously checks the durable journal and the
        caller's original/accepted input and manager fences. It returns None.
        It is a source check, used only BEFORE mutation, never after rename.
        """
        from .native_restore_state_escrow_receipts import NativeRestoreStateEscrow, RestoreEscrowIntent
        if (type(escrow) is not NativeRestoreStateEscrow
                or type(token) is not RestoreEscrowIntent or token is not escrow.completed
                or not callable(source_fence) or self.chain.active is not None):
            raise Refused('own_helper_live_escrow_required')
        escrow.require_completed(token)
        self._own_escrows[escrow.entry_id] = (escrow, token, source_fence)

    def _own_binding(self, entry_id, change, context, expected=None):
        binding = self._own_escrows.get(entry_id)
        if binding is None:
            raise Refused('own_helper_effect_required')
        from .native_registry import NativeRegistryIO, NativeRegistryBridge
        from .native_helper_lifecycle import NativeHelperLifecycle
        escrow, token, fence = binding
        life = escrow.lifecycle
        if (expected is not None and binding is not expected
                or type(life) is not NativeHelperLifecycle
                or type(self.bridge) is not NativeRegistryBridge
                or type(self.bridge.io) is not NativeRegistryIO
                or self.bridge.io.hass is not life.manager.hass
                or life.operation_id != self.chain.operation_id
                or life.plan != self.chain.plan or life.receipt != self.chain.original_receipt
                or life.store.preparations is not self.chain.store.preparations
                or life.own_registry_changes.get(entry_id) != change
                or (life.source_authorization is not None) != (context.record.direction == 'rollback')
                or not self.chain.writable):
            raise Refused('own_helper_scope_mismatch')
        result = fence(context)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise Refused('own_helper_synchronous_source_fence_required')
        if result is not None:
            raise Refused('own_helper_source_fence_result')
        escrow.require_completed(token)
        life.check_source_gaps()
        return binding

    def _own_node(self, binding, entry_id, change, context, intent, before, guard):
        from .native_helper_placeholder_effect import OwnHelperPlaceholder
        from .native_helper_lifecycle import validate_lifecycle_receipt_witness
        from .native_registry_receipts import _digest
        escrow, _, _ = binding
        life = escrow.lifecycle
        slot = life._slot('unload', entry_id, 'receipt')
        row = life.store.read(slot)
        if row is None:
            raise Refused('own_helper_unload_receipt_required')
        unload_ref = [slot, row['digest']]

        def fresh(scope):
            guard()
            self._own_binding(entry_id, change, context, binding)
            self.chain.verify_intent_source(intent, context, before)
            return True

        def witness(scope):
            parsed = validate_lifecycle_receipt_witness(life.store, life.operation_id,
                life.plan, life.receipt, life.binding['preparations'], root_id=life.root_id)
            if (not parsed.inspect()['inactive_receipts_complete']
                    or scope['lifecycle_root'] != [life.root_id, _digest(life.root)]
                    or scope['unload_receipt'] != unload_ref
                    or scope['registry_uuid'] != change.identity):
                raise Refused('own_helper_unload_lineage_mismatch')
            return True

        return OwnHelperPlaceholder.capture(io=self.bridge.io, lifecycle=life,
            chain=self.chain, context=context, intent=intent, observations=before,
            live_guard=guard, fresh_source_fence=fresh, validate_unload_witness=witness,
            verify_live_unload_state=lambda actual, ref, state:
                actual.verify_live_unload_state(entry_id, ref, state))

    def bind_phase_context(self, context):
        if context is not None and type(context) is not PhaseContext:
            raise Refused('Invalid registry phase context')
        self.context = context

    async def _read(self, guard):
        guard()
        observed = await self.observer()
        guard()
        if (type(observed) is not NativeInputObservation
                or observed.original_receipt_json != self.chain.baseline.receipt_json
                or observed.version != self.chain.version):
            raise Refused('Native observation binding mismatch')
        return tuple(RegistryRawObservation(item.key, item.raw, item.identity)
                     for item in observed.files)

    async def _durable_context(self, guard, context=None):
        expected = self.context if context is None else context
        if expected is None:
            raise Refused('Missing durable registry phase')
        guard()
        actual = await self.phase_loader(self.chain.operation_id)
        guard()
        if actual != expected.record:
            raise Refused('Durable registry phase changed')
        return expected

    async def inspect(self, step):
        try:
            self.guard()
            context = self.context
            if context is not None:
                if context.step != step:
                    raise Refused('Registry phase step mismatch')
                await self._durable_context(self.guard, context)
            evidence = await self.bridge.inspect(self._change(step))
            self.guard()
            observations = await self._read(self.guard)
            if context is None:
                self.chain.verify_initial(observations)
                expected = 'before'
            else:
                expected = self.chain.position(context, observations)
            return ({'before': Position.BEFORE, 'after': Position.AFTER}[expected]
                    if evidence.position == expected else Position.UNKNOWN)
        except asyncio.CancelledError:
            raise
        except Exception:
            return Position.UNKNOWN

    def _mutation_type(self):
        return NativeRegistryMutation

    def _native_key(self, change):
        return 'core.' + change.kind + '_registry'

    def _effect_audit(self, change, direction, source_raw, result_raw, mutation):
        # Timestamps belong to the actual synchronous call, never eventual rows.
        return NativeRegistryEffectAudit(change.kind, change.identity,
            'forward' if direction == 'forward' else 'reverse',
            hashlib.sha256(source_raw).hexdigest(), hashlib.sha256(result_raw).hexdigest(),
            prepared_registry_digest(change), json.loads(mutation.before_json).get('modified_at'),
            json.loads(mutation.after_json).get('modified_at'))

    async def _effect(self, step, caller_guard, direction):
        def guard():
            self.guard()
            caller_guard()
        context = await self._durable_context(guard)
        context.require_effect(step, direction)
        change = self._change(step)
        from .native_helper_placeholder_receipts import _own_entry
        own = _own_entry(self.chain, change)
        binding = None if own is None else self._own_binding(own, change, context)
        intent, before, node = None, None, None

        async def before_mutation():
            nonlocal intent, before, node
            if intent is not None:
                raise Refused('Registry mutation hook repeated')
            before = await self._read(guard)
            intent = await self.chain.begin(context, before)
            guard()
            await self._durable_context(guard, context)
            # Intent persistence yielded. Reobserve ALL original inputs before
            # the synchronous native API, rather than trusting the earlier read.
            before = await self._read(guard)
            self.chain.verify_intent_source(intent, context, before)
            guard()
            if binding is not None:
                node = self._own_node(binding, own, change, context, intent, before, guard)
                return node

        method = self.bridge.apply if direction == 'forward' else self.bridge.compensate
        mutation = await method(change, guard, before_mutation=before_mutation)
        guard()
        if intent is None or type(mutation) is not self._mutation_type():
            raise Refused('Native registry call not confirmed')
        await self._durable_context(guard, context)
        extra = {}
        if binding is not None:
            if node is None:
                raise Refused('own_helper_effect_not_confirmed')
            from .native_restore_state_escrow_receipts import copy_escrow_witness
            escrow = binding[0]
            life = escrow.lifecycle
            # require_completed is a source gate: it must NOT run after rename.
            await copy_escrow_witness(life.store, self.chain.store,
                self.chain.operation_id, self.chain.plan, self.chain.original_receipt,
                life.binding['preparations'], escrow.root_reference)
            guard()
            await self._durable_context(guard, context)
            extra['escrow_root_reference'] = escrow.root_reference
        after = await self._read(guard)
        key = self._native_key(change)
        source_raw = next(item.raw for item in before if item.key == key)
        result_raw = next(item.raw for item in after if item.key == key)
        audit = self._effect_audit(change, direction, source_raw, result_raw, mutation)
        if node is not None:
            extra['placeholder_stamp'] = node.finish()
        await self.chain.complete(intent, context, before, after,
                                  mutation=mutation, audit=audit, **extra)
        guard()
        return mutation

    async def apply(self, step, guard):
        return await self._effect(step, guard, 'forward')

    async def compensate(self, step, guard):
        return await self._effect(step, guard, 'rollback')

    async def _verify(self, plan, callback, direction):
        self.guard()
        if plan != self.chain.plan or callback is None:
            return False
        if await callback(plan) is not True:
            self.guard()
            return False
        self.guard()
        record = await self.phase_loader(self.chain.operation_id)
        self.guard()
        if (record is None or record.plan != plan or record.direction != direction
                or record.phases != (('verified' if direction == 'forward' else 'undone'),)
                                    * len(plan.steps)):
            return False
        observations = await self._read(self.guard)
        self.chain.expected(PhaseContext(record, 0), observations)
        return True

    async def verify(self, plan):
        return await self._verify(plan, self.full_verify, 'forward')

    async def verify_rollback(self, plan):
        return await self._verify(plan, self.full_verify_rollback, 'rollback')
