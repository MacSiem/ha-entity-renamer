"""Typed registry receipt adapter for an inactive Powercalc phase session.

Powercalc-owned IDs and renamed physical inputs require their own witnessed
effect nodes. This adapter refuses those effects until such a node is bound;
it never promotes a persisted receipt into an executable capability.
"""
from __future__ import annotations

from .native_registry import NativeRegistryBridge, NativeRegistryIO
from .native_registry_transaction import ReceiptedRegistryTransactionAdapter
from .powercalc_phase_session import PowercalcPhaseSession
from .transaction import Refused


class PowercalcRegistryAdapter(ReceiptedRegistryTransactionAdapter):
    def __init__(self, phase, bridge, resolver, observer, phase_loader, guard, *,
                 full_verify=None, full_verify_rollback=None):
        if (type(phase) is not PowercalcPhaseSession or phase.phase != 'sources_inactive'
                or type(bridge) is not NativeRegistryBridge or type(bridge.io) is not NativeRegistryIO
                or bridge.io.hass is not phase.lease.hass or not callable(guard)):
            raise Refused('powercalc_registry_live_phase_required')
        phase.check()
        self.phase = phase
        self._source_preflight = None
        def guarded():
            phase.check()
            guard()
        super().__init__(bridge, resolver, phase.lease.chain, observer, phase_loader, guarded,
                         full_verify=full_verify, full_verify_rollback=full_verify_rollback)

    def _intent_started(self, intent):
        self.phase.lease.track_intent(intent)

    def _receipt_completed(self, reference):
        self.phase.lease.accept_receipt(reference)

    async def _typed_node(self, change, context, intent, observations, guard):
        if change.kind != 'entity' or not change.mapping:
            return None
        old = (change.after if context.record.direction == 'rollback' else change.before)['entity_id']
        if change.before.get('platform') == 'powercalc':
            from .powercalc_group_own_id import PowercalcGroupOwnID
            return await PowercalcGroupOwnID.capture(
                self.phase, self.bridge.io, change, context, intent, observations, guard)
        if old not in self.phase.scope.producer_owner.inputs:
            return None
        from .powercalc_source_transition import PowercalcSourceTransition
        return await PowercalcSourceTransition.capture(
            self.phase, self.bridge.io, change, context, intent, observations,
            preflight=self._source_preflight)

    async def _typed_extra(self, node, context, mutation, observations, guard):
        from .powercalc_source_transition import PowercalcSourceTransition
        from .powercalc_group_own_id import PowercalcGroupOwnID
        if type(node) is PowercalcGroupOwnID and node.context == context:
            guard()
            await node.record(mutation)
            guard()
            return {'powercalc_group_effect': node}
        if type(node) is not PowercalcSourceTransition or node.context != context:
            raise Refused('powercalc_registry_source_effect_required')
        guard()
        await node.record(mutation)
        guard()
        return {'powercalc_source_effect': node}

    async def _effect(self, step, caller_guard, direction):
        self.phase.check()
        if direction != self.phase.lifecycle.direction:
            raise Refused('powercalc_live_direction_required')
        change = self._change(step)
        if change.kind == 'device':
            raise Refused('powercalc_device_effect_unqualified')
        if change.kind == 'entity' and change.before.get('platform') == 'powercalc':
            groups = self.phase.lifecycle.groups
            source = change.after if direction == 'rollback' else change.before
            if not change.mapping or not any(g.registry_id == change.identity and
                    g.entity_id == source['entity_id'] for g in groups):
                raise Refused('powercalc_owned_id_effect_required')
        if change.kind == 'entity' and change.mapping:
            old = (change.after if direction == 'rollback' else change.before)['entity_id']
            if (old in self.phase.scope.listener_owner.sources
                    and old not in self.phase.scope.producer_owner.inputs):
                raise Refused('powercalc_source_handoff_required')
            if old in self.phase.scope.producer_owner.inputs:
                from .powercalc_source_transition import _config_image
                owner = self.phase.scope.producer_owner
                affected = tuple(sorted(name for name, dependencies in owner.dependencies.items()
                                        if old in dependencies))
                if not affected or self._source_preflight is not None:
                    raise Refused('powercalc_source_preflight_required')
                self._source_preflight = tuple((name, owner.old[name]._sensor_config,
                    _config_image(owner.old[name]._sensor_config)) for name in affected)
                self.phase.check()
        try:
            return await super()._effect(step, caller_guard, direction)
        finally:
            self._source_preflight = None
