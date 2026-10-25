"""Trusted registry-only Coordinator adapter with full-input durable receipts.

Caller owns shared coordinator/native editor locks and external lease. Observer
must be async_observe_native_inputs (or an equally authenticated IO seam), never
client data. No endpoint, YAML exception or migration-readiness claim is added.
"""
from __future__ import annotations

import asyncio
import hashlib
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
        if _own_entry(self.chain, change) is not None:
            # This generic adapter has no installed live escrow/placeholder IO
            # binding yet. Refuse BEFORE any bridge/intent/effect, not merely at
            # post-effect receipt validation. The private scoped node is composed
            # separately; historical receipts cannot make this path writable.
            raise Refused('own_helper_effect_required')
        intent, before = None, None

        async def before_mutation():
            nonlocal intent, before
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

        method = self.bridge.apply if direction == 'forward' else self.bridge.compensate
        mutation = await method(change, guard, before_mutation=before_mutation)
        guard()
        if intent is None or type(mutation) is not self._mutation_type():
            raise Refused('Native registry call not confirmed')
        await self._durable_context(guard, context)
        after = await self._read(guard)
        key = self._native_key(change)
        source_raw = next(item.raw for item in before if item.key == key)
        result_raw = next(item.raw for item in after if item.key == key)
        audit = self._effect_audit(change, direction, source_raw, result_raw, mutation)
        await self.chain.complete(intent, context, before, after,
                                  mutation=mutation, audit=audit)
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
