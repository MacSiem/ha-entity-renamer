"""YAML-last participant for one live, typed Powercalc phase session.

This adapter is private to the ordinary Powercalc composer.  It does not
construct a session from persisted evidence or fall back to a generic reload.
"""
from __future__ import annotations

from .native_yaml_transaction import ReceiptedYAMLTransactionAdapter, _YAMLHooks
from .powercalc_phase_session import PowercalcPhaseSession
from .powercalc_prepared_yaml import PreparedPowercalcYAML, assert_powercalc_yaml_composition
from .transaction import Position, Refused


class _PowercalcYAMLHooks(_YAMLHooks):
    async def activate(self):
        await self.checkpoint()
        await self.adapter.phase.activate_yaml(self)
        await self.checkpoint()

    async def settle_reload(self):
        # The phase owns and drains exactly its started native setup tasks.
        await self.adapter.phase.settle_activation()


class PowercalcYAMLAdapter(ReceiptedYAMLTransactionAdapter):
    """Bind the existing YAML journal/IO to the exact live Powercalc phase."""

    def __init__(self, phase, *args, **kwargs):
        if type(phase) is not PowercalcPhaseSession or phase.phase != 'sources_inactive':
            raise Refused('powercalc_yaml_live_inactive_phase_required')
        phase.check()
        self.phase = phase
        self._active_hooks = None
        super().__init__(*args, **kwargs)
        if self.hass is not phase.lease.hass or self.chain is not phase.lease.chain:
            raise Refused('powercalc_yaml_same_original_required')

    def _accepts_preparation(self, change):
        return type(change) is PreparedPowercalcYAML

    def _assert_activation_available(self, change):
        # Only this live typed phase owns the supported setup suffix; the
        # generic YAML service route must still refuse Powercalc.
        if (type(change) is not PreparedPowercalcYAML
                or change.receipt.changed_domains != ('powercalc',)
                or change.receipt.unsupported_domains != ('powercalc',)
                or self.phase.lease.chain is not self.chain):
            raise Refused('powercalc_yaml_typed_activation_required')
        self.phase.check()

    def _change(self, step):
        change = super()._change(step)
        assert_powercalc_yaml_composition(self.chain, change)
        if set(change.receipt.changed_domains) != {'powercalc'}:
            raise Refused('powercalc_yaml_mixed_activation_unqualified')
        if self.phase.phase == 'sources_inactive':
            self.phase.verify_activation_preflight()
        return change

    def _intent_started(self, intent):
        self.phase.lease.track_intent(intent)

    def _receipt_completed(self, reference):
        self.phase.lease.accept_receipt(reference)
        self.phase.policy.end_activation(self._active_hooks)
        self._active_hooks = None
        self.phase.ack_activation(reference)

    def _hooks(self, change, context, intent, guard):
        hooks = _PowercalcYAMLHooks(self, change, context, intent, guard)
        self.phase.policy.bind_activation(hooks)
        self._active_hooks = hooks
        return hooks

    def _completion_extra(self, hooks, context):
        if type(hooks) is not _PowercalcYAMLHooks or hooks is not self._active_hooks:
            raise Refused('powercalc_yaml_live_activation_required')
        return {'powercalc_setup': self.phase}

    async def _runtime_position(self, change, state):
        self.phase.check()
        reverse = self.phase.lifecycle.direction == 'rollback'
        if self.phase.phase == 'sources_inactive':
            return Position.AFTER if reverse else Position.BEFORE
        if self.phase.phase in ('activated', 'active_receipted'):
            self.phase.verify_activation()
            return Position.BEFORE if reverse else Position.AFTER
        raise Refused('powercalc_yaml_intermediate_runtime_unqualified')

    async def _effect(self, step, caller_guard, direction):
        if direction != self.phase.lifecycle.direction:
            raise Refused('powercalc_live_direction_required')
        self.phase.check()
        from contextvars import copy_context
        owned_context = copy_context()
        try:
            return await super()._effect(step, caller_guard, direction, _loop_context=owned_context)
        finally:
            if self._active_hooks is not None:
                self.phase.policy.end_activation(self._active_hooks)
                self._active_hooks = None
