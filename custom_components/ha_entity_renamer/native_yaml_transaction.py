"""Trusted YAML-last adapter on the same immutable native receipt chain.

Caller owns the process lease and shared coordinator lock throughout. The adapter
holds the supplied shared YAML editor locks. Observers and phase_loader are trusted
backend IO seams, never request values. No pending intent recovery or replay.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from .native_input_observation import NativeInputObservation
from .native_registry_receipts import RegistryRawObservation
from .transaction import PhaseContext, Position, Refused
from .yaml_transaction import YAMLTransactionAdapter
from .yaml_validation import async_activate_yaml


class _YAMLHooks:
    """Ephemeral evidence owned by a single live write, never serialized."""
    def __init__(self, adapter, change, context, intent, guard):
        self.adapter, self.change, self.context = adapter, change, context
        self.intent, self.guard = intent, guard
        self.calls = []
        self.reload_started = False

    def state(self):
        return self.adapter.chain.live_yaml_inputs(self.intent, self.context, self.calls)

    def assert_sources(self):
        self.guard()
        self.state().assert_current(self.change.receipt)
        self.guard()

    async def checkpoint(self):
        self.assert_sources()
        await self.adapter._durable_context(self.guard, self.context)
        self.assert_sources()

    def capture(self, result):
        # Only called synchronously at the direct apply_file_patch return site.
        # No reconstruction from a readback, cached result or desired image.
        if type(result) is not dict:
            raise Refused('yaml_mutator_call_unconfirmed')
        self.calls.append(result)
        self.assert_sources()

    async def runtime_position(self):
        return await self.adapter._runtime_position(self.change, self.state())

    async def settle_reload(self):
        if self.reload_started and 'template' in self.change.receipt.changed_domains:
            await self.adapter.observer.settle_reload()

    async def activate(self):
        hooks = self
        services = self.adapter.hass.services
        class CheckedServices:
            def has_service(self, domain, service):
                return services.has_service(domain, service)

            async def async_call(self, *args, **kwargs):
                await hooks.checkpoint()
                if 'template' in hooks.change.receipt.changed_domains:
                    hooks.adapter.observer.assert_reload_current()
                hooks.reload_started = True
                result = await services.async_call(*args, **kwargs)
                if 'template' in hooks.change.receipt.changed_domains:
                    hooks.adapter.observer.assert_reload_current()
                await hooks.checkpoint()
                return result
        # Reuse native activation with the ORIGINAL receipt; wrap only the IO
        # boundary so every service await gets journal and whole-source checks.
        await async_activate_yaml(SimpleNamespace(services=CheckedServices()),
            self.change.receipt, context=self.adapter.context, guard=self.assert_sources)
        await self.checkpoint()


class ReceiptedYAMLTransactionAdapter(YAMLTransactionAdapter):
    def __init__(self, hass, resolver, editor_locks, observer, chain,
                 native_observer, phase_loader, guard, *, context,
                 full_verify, full_verify_rollback, runtime_version=None):
        if not all(callable(fn) for fn in (native_observer, phase_loader, guard,
                                          full_verify, full_verify_rollback)):
            raise ValueError('yaml_trusted_full_scope_callbacks_required')
        super().__init__(hass, resolver, editor_locks, observer, context=context,
                         runtime_version=runtime_version)
        self.chain, self.native_observer = chain, native_observer
        self.phase_loader, self.guard = phase_loader, guard
        self.full_verify, self.full_verify_rollback = full_verify, full_verify_rollback
        self.phase_context = None
        self._template_completion = None

    def _change(self, step):
        change = super()._change(step)
        if (not self.chain.plan.steps or step != self.chain.plan.steps[-1]
                or step.adapter != 'yaml' or change.receipt != self.chain.original_receipt
                or {row[0] for row in change.receipt.runtime_expected}
                   != set(change.receipt.changed_domains)):
            raise Refused('YAML original preparation/runtime coverage mismatch')
        if 'template' in change.receipt.changed_domains:
            from .native_template_yaml import _TemplateCompletionGuard
            if type(self._template_completion) is not _TemplateCompletionGuard:
                raise Refused('template_scoped_completion_guard_required')
        return change

    def _runtime_check(self, change, position):
        if 'template' in change.receipt.changed_domains:
            return self.observer.runtime_check(position)
        return None

    async def _runtime_position(self, change, state):
        if 'template' in change.receipt.changed_domains:
            from .native_template_yaml import NativeTemplateYAMLRuntimeReadback
            if type(self.observer) is not NativeTemplateYAMLRuntimeReadback:
                raise Refused('template_scoped_runtime_observer_required')
            return await self.observer.position_at_inputs(change, state)
        return await self.observer.position(change)

    def bind_phase_context(self, context):
        if context is not None and type(context) is not PhaseContext:
            raise Refused('Invalid YAML phase context')
        self.phase_context = context

    async def _durable_context(self, guard, context=None):
        expected = self.phase_context if context is None else context
        if expected is None:
            raise Refused('Missing durable YAML phase')
        guard()
        actual = await self.phase_loader(self.chain.operation_id)
        guard()
        if actual != expected.record:
            raise Refused('Durable YAML phase changed')
        return expected

    def _assert(self, state, guard):
        guard()
        state.assert_current(self.chain.original_receipt)
        guard()

    async def _read(self, state, guard, checkpoint):
        self._assert(state, guard)
        observed = await self.native_observer(state)
        await checkpoint()
        self._assert(state, guard)
        if (type(observed) is not NativeInputObservation
                or observed.original_receipt_json != self.chain.baseline.receipt_json
                or observed.version != self.chain.version):
            raise Refused('Native YAML observation binding mismatch')
        return tuple(RegistryRawObservation(row.key, row.raw, row.identity)
                     for row in observed.files)

    async def inspect(self, step):
        """Read-only reconciliation of acknowledged receipts, never pending intent."""
        try:
            guard = self.guard
            guard()
            change = self._change(step)
            context = self.phase_context
            if context is not None and context.step != step:
                return Position.UNKNOWN
            state = self.chain.reconstruct_inputs()  # Reject every pending intent.
            async def checkpoint():
                self._assert(state, guard)
                if context is None:
                    actual = await self.phase_loader(self.chain.operation_id)
                    guard()
                    if actual is not None:
                        raise Refused('Unexpected durable YAML operation')
                else:
                    await self._durable_context(guard, context)
                self._assert(state, guard)
            await checkpoint()
            runtime = await self._runtime_position(change, state)
            runtime_check = self._runtime_check(change, runtime)
            await checkpoint()
            observed = await self._read(state, guard, checkpoint)
            if context is None:
                self.chain.verify_initial(observed)
                position = 'before'
            else:
                position = self.chain.position(context, observed)
            await checkpoint()
            expected = {'before': Position.BEFORE, 'after': Position.AFTER}[position]
            if runtime_check is not None:
                runtime_check.check()
            return expected if runtime == expected else Position.UNKNOWN
        except asyncio.CancelledError:
            raise
        except Exception:
            return Position.UNKNOWN

    async def _effect(self, step, caller_guard, direction):
        def guard():
            self.guard()
            caller_guard()
        guard()
        change = self._change(step)
        context = await self._durable_context(guard)
        context.require_effect(step, direction)
        if context.index != len(self.chain.plan.steps) - 1:
            raise Refused('YAML must be the last forward step')
        # No unlock between independent source/runtime proof, durable intent,
        # actual file calls, native reload and complete receipt acknowledgement.
        async with self.locks.hold(guard):
            state = self.chain.reconstruct_inputs()
            async def checkpoint():
                self._assert(state, guard)
                await self._durable_context(guard, context)
                self._assert(state, guard)
            await checkpoint()
            runtime = await self._runtime_position(change, state)
            runtime_check = self._runtime_check(change, runtime)
            await checkpoint()
            expected = Position.BEFORE if direction == 'forward' else Position.AFTER
            if runtime != expected:
                raise Refused('yaml_native_source_unconfirmed')
            before = await self._read(state, guard, checkpoint)
            if runtime_check is not None:
                runtime_check.check()
            # begin_yaml validates native source and entire original-bound graph.
            intent = await self.chain.begin_yaml(context, before, input_state=state)
            hooks = _YAMLHooks(self, change, context, intent, guard)
            await hooks.checkpoint()  # begin durable ACK yielded; re-read journal.
            # Fresh authenticated native observation immediately before effects.
            before = await self._read(hooks.state(), guard, hooks.checkpoint)
            results = await self._write(change, guard,
                reverse=direction == 'rollback', _trusted_hooks=hooks)
            await hooks.checkpoint()
            after = await self._read(hooks.state(), guard, hooks.checkpoint)
            await hooks.checkpoint()
            await self.chain.complete_yaml(intent, context, before, after, calls=hooks.calls)
            # If acknowledgement is lost, chain/store poison and no replay. A
            # fresh read-only reopen can later inspect a durable receipt.
            guard()
            accepted = self.chain.reconstruct_inputs()
            self._assert(accepted, guard)
            runtime_check = None
            if 'template' in change.receipt.changed_domains:
                runtime = await self._runtime_position(change, accepted)
                runtime_check = self._runtime_check(change, runtime)
                if runtime is not (Position.AFTER if direction == 'forward' else Position.BEFORE):
                    raise Refused('yaml_native_runtime_unconfirmed')
            await self._durable_context(guard, context)
            self._assert(accepted, guard)
            if runtime_check is not None:
                runtime_check.check()
            return results

    async def apply(self, step, guard):
        return await self._effect(step, guard, 'forward')

    async def compensate(self, step, guard):
        return await self._effect(step, guard, 'rollback')

    async def _verify(self, plan, *, rollback):
        guard = self.guard
        guard()
        if plan != self.chain.plan:
            return False
        callback = self.full_verify_rollback if rollback else self.full_verify
        if await callback(plan) is not True:
            guard()
            return False
        guard()
        state = self.chain.reconstruct_inputs()
        self._assert(state, guard)
        record = await self.phase_loader(self.chain.operation_id)
        self._assert(state, guard)
        direction = 'rollback' if rollback else 'forward'
        if (record is None or record.plan != plan or record.direction != direction
                or record.phases != (('undone' if rollback else 'verified'),) * len(plan.steps)):
            return False
        steps = [i for i, row in enumerate(plan.steps) if row.adapter == 'yaml']
        if steps != [len(plan.steps) - 1]:
            return False
        context = PhaseContext(record, steps[0])
        async def checkpoint():
            self._assert(state, guard)
            await self._durable_context(guard, context)
            self._assert(state, guard)
        change = self._change(context.step)
        runtime = await self._runtime_position(change, state)
        runtime_check = self._runtime_check(change, runtime)
        await checkpoint()
        observed = await self._read(state, guard, checkpoint)
        position = self.chain.position(context, observed)
        await checkpoint()
        expected = Position.BEFORE if rollback else Position.AFTER
        matched = runtime == expected and position == expected.value
        if runtime_check is not None:
            runtime_check.check()
            if matched:
                self._template_completion.arm(runtime_check)
        return matched
