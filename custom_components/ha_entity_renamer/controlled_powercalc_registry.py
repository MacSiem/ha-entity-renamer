"""One inactive group-ID effect composed with the existing Template node."""
from __future__ import annotations

from dataclasses import asdict

from .native_registry_receipts import _digest, _event_id
from .native_registry_transition import _powercalc_group_suggested_move, prepared_registry_digest
from .transaction import Refused


def _need(value):
    if not value:
        raise Refused('controlled_powercalc_group_registry_unconfirmed')


class ControlledPowercalcRegistryNode:
    @classmethod
    async def capture(cls, root, handoff, io, change, context, intent, observations, guard):
        from .native_template_state_registry_effect import NativeTemplateStateRegistryEffect
        _need(change.kind == 'entity' and change.mapping and change.before.get('platform') == 'powercalc'
              and _powercalc_group_suggested_move(change.before, change.after)
              and handoff.direction == context.record.direction and handoff.receipt is not None)
        handoff.require_inactive()
        source, target = ((change.before['entity_id'], change.after['entity_id'])
                          if handoff.direction == 'forward' else
                          (change.after['entity_id'], change.before['entity_id']))
        old = change.before['entity_id']
        from .controlled_powercalc_composition import registry_population_kind
        kind = registry_population_kind(root.composition, old, change.identity,
                                        root.chain.original_receipt.version)
        _need(kind is not None and (source, change.identity, kind) in handoff.population.descriptors
              and root.chain.active is intent)
        inner = await NativeTemplateStateRegistryEffect.capture(root, io, change, context,
            intent, observations, root.observation_state, guard)
        handoff.require_inactive()
        result = object.__new__(cls)
        result.inner, result.handoff, result.chain = inner, handoff, root.chain
        result.io, result.change, result.context, result.intent = io, change, context, intent
        result.source, result.target, result.guard = source, target, guard
        result.stage, result.mutation, result.reference = 'captured', None, None
        return result

    async def before_attempt(self):
        await self.inner.before_attempt()
        self.handoff.require_inactive()

    def abort_before_attempt(self):
        self.inner.abort_before_attempt()
        self.stage = 'aborted'

    def authorize_suggested(self, io, change, target):
        self.guard()
        self.handoff.require_inactive()
        _need(self.stage == 'attempted' and io is self.io and change is self.change
              and target == (change.after if self.context.record.direction == 'forward' else change.before)
              and self.chain.active is self.intent and _powercalc_group_suggested_move(change.before, change.after))

    def mutate(self):
        self.guard()
        self.handoff.require_inactive()
        _need(self.stage == 'captured' and not hasattr(self.io, '_powercalc_group_capability'))
        self.stage = 'attempted'
        self.io._powercalc_group_capability = self
        try:
            self.mutation = self.inner.mutate()
        finally:
            del self.io._powercalc_group_capability
        return self.mutation

    async def finish_native(self):
        await self.inner.finish_native()

    async def finish_failed_attempt(self):
        await self.inner.finish_failed_attempt()

    async def verify_effect(self, mutation, observations):
        _need(self.stage == 'attempted' and mutation is self.mutation)
        await self.inner.verify_effect(mutation, observations)
        self.handoff.require_inactive()
        key = _event_id(['controlled_pc_group', self.chain.root_id, self.context.index,
                         self.context.record.direction, 'receipt'])
        value = {'kind': 'controlled_powercalc_group_receipt', 'schema': 1,
                 'root': self.chain.root_id, 'operation_id': self.chain.operation_id,
                 'fingerprint': self.chain.plan.fingerprint, 'direction': self.context.record.direction,
                 'phase_index': self.context.index, 'intent': list(self.intent.reference),
                 'handoff': list(self.handoff.receipt), 'registry_uuid': self.change.identity,
                 'prepared_digest': prepared_registry_digest(self.change), 'mutation': asdict(mutation)}
        self.reference = await self.chain.store.create(key, value)
        self.handoff.require_inactive()
        _need(self.chain.store.read(key) == {'digest': _digest(value), 'payload': value})
        self.stage = 'recorded'

    async def accepted(self):
        await self.inner.accepted()

    def claim(self, chain, intent, context, mutation):
        _need(chain is self.chain and intent is self.intent and context == self.context
              and mutation is self.mutation and self.stage == 'recorded' and self.reference is not None)
        return self.reference

    def completed(self, reference):
        _need(self.stage == 'recorded' and type(reference) is tuple)
        self.stage = 'completed'
