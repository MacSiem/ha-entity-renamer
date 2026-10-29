"""One synchronous state-binding cutover + one genuine registry API attempt.

Root owns authorization, SAME ORIGINAL/intent and independently sampled source
population. No await separates installing bindings and the actual native call.
Before-attempt cleanup restores only this node's exact still-installed objects.
After an API attempt, failure belongs to UNKNOWN; this node never retries it.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json

from .transaction import Refused
from .native_template_binding_transport import (NativeTemplateBindingTransport,
    BindingVariablePair, _BindingCallerContext)
from .native_template_state_binding import (accepted_state_prefix,
    async_observe_accepted_state_bindings, sample_state_bindings, _StateRuntime)


def _need(value, reason):
    if not value:
        raise Refused('template_state_registry_' + reason)


class NativeTemplateStateRegistryEffect:
    def __init__(self):
        _need(False, 'root_capture_required')

    @classmethod
    async def capture(cls, root, io, change, context, intent, before, state, guard):
        from .native_template_state_server import NativeTemplateStateServer
        from .native_registry import NativeRegistryIO
        _need(cls is NativeTemplateStateRegistryEffect and type(root) is NativeTemplateStateServer
              and type(io) is NativeRegistryIO and io.hass is root.hass
              and root.chain.active is intent and root.chain.writable
              and context.record.plan == root.plan and context.step.adapter == 'registry'
              and change == root.chain._original_change(context.index), 'root_binding_required')
        guard()
        root.chain.verify_intent_source(intent, context, before)
        snapshot = await async_observe_accepted_state_bindings(root.hass, root.chain,
            root.program, guard=guard, input_state=state)
        guard()
        root.chain.verify_intent_source(intent, context, before)
        old, new = next(iter(change.mapping.items()))
        reverse = context.record.direction == 'rollback'
        active = set(snapshot.prefix.active)
        _need((old in active) == reverse, 'source_prefix_changed')
        if reverse:
            active.remove(old)
        else:
            active.add(old)
        target = frozenset(active)
        transition = snapshot.transition_pairs(root.program, target)
        obj = object.__new__(cls)
        obj.root, obj.io, obj.change = root, io, change
        obj.source, obj.target = (new, old) if reverse else (old, new)
        obj.context, obj.intent, obj.before = context, intent, before
        obj.state, obj.guard, obj.snapshot, obj.transition = state, guard, snapshot, transition
        obj.task, obj.phase = asyncio.current_task(), 'captured'
        obj.runtime = _StateRuntime(root.hass)
        obj.expected = replace(snapshot.prefix, active=target,
            registry_ids=tuple((identity, obj.target if identity == change.identity else value)
                               for identity, value in snapshot.prefix.registry_ids))
        obj.transport = None
        return obj

    def _capture_at_final_fence(self):
        # Runtime values may legitimately advance during intent/phase IO. The
        # SAME immutable source program remains authoritative; recapture actual
        # tracker/history data synchronously at the final registry seam.
        self.guard()
        self.state.assert_current(self.root.receipt)
        self.root.chain.verify_intent_source(self.intent, self.context, self.before)
        self.runtime.sources()
        fresh = sample_state_bindings(self.runtime, self.root.program, self.snapshot.prefix,
            self.snapshot.yaml_position, self.root.chain.accepted_documents())
        _need(self.snapshot.same_bindings(fresh), 'source_identity_changed')
        self.snapshot = fresh
        self.transition = fresh.transition_pairs(self.root.program, self.expected.active)
        if self.transition.entities:
            self.caller = _BindingCallerContext(self.task, self._fence)
            self.transport = NativeTemplateBindingTransport.capture(self.root.hass,
                self.transition.entities, source_pairs=self.transition.source_pairs,
                caller_context=self.caller,
                configured_variable_pairs=tuple(BindingVariablePair(p.container, p.name,
                    p.before, p.after, p.source_identity) for p in self.transition.configured_variables),
                rendered_variable_pairs=tuple(BindingVariablePair(p.variables, p.name,
                    p.before, p.after, p.source_identity) for p in self.transition.rendered_variables))

    def _fence(self, stage, sources, entities):
        self.guard()
        _need(asyncio.current_task() is self.task and self.root.chain.active is self.intent
              and sources == self.transition.source_pairs and entities == self.transition.entities,
              'live_node_changed')
        self.root.chain.verify_intent_source(self.intent, self.context, self.before)
        self.state.assert_current(self.root.receipt)
        prefix = accepted_state_prefix(self.root.chain, self.root.program, input_state=self.state)
        _need(prefix == self.snapshot.prefix, 'accepted_prefix_changed')
        self.runtime.sources()
        expected = self.snapshot.prefix if stage in ('capture', 'install') else self.expected
        actual = sample_state_bindings(self.runtime, self.root.program, expected,
            self.snapshot.yaml_position, self.root.chain.accepted_documents())
        if stage in ('capture', 'install'):
            _need(actual == self.snapshot, 'source_runtime_changed')
        else:
            _need(tuple((r.domain, r.source_key, id(r.entity), r.registry_id) for r in actual.rows)
                  == tuple((r.domain, r.source_key, id(r.entity), r.registry_id)
                           for r in self.snapshot.rows), 'installed_identity_changed')
        return True

    def _restore_own_pre_attempt(self):
        transfer = self.transport
        if transfer is None or transfer.phase != 'installed':
            return
        # Revoked request authorization cannot prevent restoring our own
        # unpublished synchronous cutover. No await/reentry happened since it.
        _need(asyncio.current_task() is self.task, 'cleanup_task_changed')
        for row in transfer.rows:
            _need(row['entity']._template_result_info is row['new']
                  and row['entity']._on_remove[row['index']] == row['new'].async_remove,
                  'cleanup_owned_binding_changed')
        transfer._restore_before_effect()
        transfer.phase = 'rolled_back'

    def mutate(self):
        _need(self.phase == 'captured' and asyncio.current_task() is self.task,
              'one_use_required')
        try:
            self._capture_at_final_fence()
            if self.transport is not None:
                self.transport.install_before_registry(self.caller)
                self.transport.mark_registry_attempt(self.caller)
            else:
                self.guard()
                self.root.chain.verify_intent_source(self.intent, self.context, self.before)
                self.state.assert_current(self.root.receipt)
        except BaseException:
            self._restore_own_pre_attempt()
            self.phase = 'failed_before_attempt'
            raise
        self.phase = 'registry_attempted'
        # Exact NativeRegistryIO, not a callable supplied by an import/request.
        return self.io.mutate(self.change,
            self.change.before if self.context.record.direction == 'rollback' else self.change.after)

    async def verify_effect(self, mutation, after):
        from .native_registry import NativeRegistryMutation
        from .yaml_validation import _settled_executor
        _need(self.phase == 'registry_attempted' and type(mutation) is NativeRegistryMutation
              and mutation.identity == self.change.identity and mutation.kind == 'entity',
              'actual_mutation_required')
        self.guard()
        documents = {row.key: json.loads(row.raw) for row in after}
        async def checked_sources():
            await _settled_executor(self.root.hass, self.runtime.sources)
            self.guard()
            fence = await _settled_executor(self.root.hass,
                self.state._native_observation_fence, self.root.receipt)
            self.guard()
            _need(fence.documents() == documents, 'native_readback_changed')
        await checked_sources()
        first = sample_state_bindings(self.runtime, self.root.program, self.expected,
            self.snapshot.yaml_position, documents)
        await asyncio.sleep(0)
        await checked_sources()
        second = sample_state_bindings(self.runtime, self.root.program, self.expected,
            self.snapshot.yaml_position, documents)
        _need(first.same_bindings(second), 'native_runtime_changed')
        self.phase = 'observed_effect'

    async def accepted(self):
        _need(self.phase == 'observed_effect', 'effect_observation_required')
        self.guard()
        actual = await async_observe_accepted_state_bindings(self.root.hass,
            self.root.chain, self.root.program, guard=self.guard)
        self.guard()
        _need(actual.prefix.active == self.expected.active
              and actual.prefix.registry_ids == self.expected.registry_ids,
              'accepted_effect_changed')
        self.phase = 'accepted'
