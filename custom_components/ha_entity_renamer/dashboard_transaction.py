"""One actual native Lovelace save and its SAME-ORIGINAL typed receipt."""
from __future__ import annotations

import asyncio

from .dashboard_phase import DashboardPhase
from .dashboard_receipts import live_inputs, observe
from .lovelace_native import NativeLovelace, PreparedDashboard
from .native_adapters import LovelaceTransactionAdapter
from .native_discovery_inputs import async_observe_discovery_inputs
from .native_input_baseline import _drained
from .native_registry_receipts import RegistryRawObservation
from .transaction import PhaseContext, Refused


def _need(value, reason):
    if not value:
        raise Refused('dashboard_adapter_' + reason)


class DashboardEffect:
    def __init__(self):
        raise Refused('dashboard_effect_requires_native_call')

    def claim(self, chain, intent, context):
        issuer = self.issuer
        _need(issuer._issued is self and issuer.chain is chain and issuer.context is context
              and chain.active is intent and self.intent is intent
              and asyncio.current_task() is self.owner and not self.used, 'witness_changed')
        issuer._scope()
        return self.before, self.after

    def completed(self, reference):
        _need(not self.used and self.issuer._issued is self, 'witness_changed')
        self.used = True
        self.issuer._issued = None


class ReceiptedDashboardAdapter(LovelaceTransactionAdapter):
    def __init__(self, native, phase, chain, phase_loader, guard, verify, verify_original):
        _need(type(native) is NativeLovelace and type(phase) is DashboardPhase
              and native.hass is phase.hass and phase.chain is chain
              and callable(phase_loader) and callable(guard), 'scope_required')
        self.phase, self.chain, self.phase_loader, self.guard = phase, chain, phase_loader, guard
        self.context, self._issued = None, None
        def resolver(reference):
            matches = [i for i, step in enumerate(chain.plan.steps)
                       if step.adapter == 'dashboard' and step.prepared_ref == reference]
            _need(len(matches) == 1, 'preparation_changed')
            change = chain._original_change(matches[0])
            _need(type(change) is PreparedDashboard, 'preparation_required')
            return change
        super().__init__(native, resolver, verify, verify_original)

    def bind_phase_context(self, context):
        _need(context is None or type(context) is PhaseContext, 'phase_required')
        self.context = context

    def _scope(self):
        self.guard()
        self.phase.check()
        _need(not self.native._failed, 'native_uncertain')

    async def _durable(self, guard, expected=None):
        self._scope(); guard()
        context = self.context if expected is None else expected
        _need(type(context) is PhaseContext and self.context == context, 'phase_changed')
        actual = await self.phase_loader(self.chain.operation_id)
        self._scope(); guard()
        _need(actual == context.record, 'durable_phase_changed')
        return context

    async def _core(self, guard):
        observed = await async_observe_discovery_inputs(self.native.hass, guard=self._scope)
        self._scope(); guard()
        _need(observed.version == self.chain.version
              and observed.config_root == self.chain.original_receipt.config_root, 'core_scope_changed')
        return tuple(RegistryRawObservation(row.key, row.raw, row.identity) for row in observed.files)

    async def _effect(self, step, guard, direction):
        context = await self._durable(guard)
        context.require_effect(step, direction)
        _need(self._issued is None, 'effect_already_issued')
        change = self._change(step)
        state = self.chain.reconstruct_inputs()
        await _drained(self.native.hass.async_add_executor_job,
                       lambda: state.assert_current(self.chain.original_receipt))
        self._scope(); guard()
        before = await self._core(guard)
        original = await observe(self.native, change, self._scope)
        intent = await self.chain.begin_dashboard(context, before, original)
        self.phase.arm(change, direction)
        self._scope(); guard()
        await self._durable(guard, context)
        prior = await observe(self.native, change, self._scope)
        _need(prior == original, 'post_intent_changed')
        fence = live_inputs(self.chain, intent, context, prior)
        await _drained(self.native.hass.async_add_executor_job,
                       lambda: fence.assert_current(self.chain.original_receipt))
        self._scope(); guard()
        if direction == 'forward':
            await super().apply(step, lambda: (self._scope(), guard()) and None)
        else:
            await super().compensate(step, lambda: (self._scope(), guard()) and None)
        self._scope(); guard()
        await self._durable(guard, context)
        current = await observe(self.native, change, self._scope)
        after = await self._core(guard)
        fence = live_inputs(self.chain, intent, context, current)
        await _drained(self.native.hass.async_add_executor_job,
                       lambda: fence.assert_current(self.chain.original_receipt))
        self._scope(); guard()
        witness = object.__new__(DashboardEffect)
        witness.issuer, witness.intent, witness.owner = self, intent, asyncio.current_task()
        witness.before, witness.after, witness.used = original, current, False
        self._issued = witness
        await self.chain.complete_dashboard(intent, context, before, after, witness)
        self.phase.accept(change, current)
        self._scope(); guard()

    async def apply(self, step, guard):
        await self._effect(step, guard, 'forward')

    async def compensate(self, step, guard):
        await self._effect(step, guard, 'rollback')
