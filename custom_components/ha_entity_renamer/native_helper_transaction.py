"""Ordinary inactive-helper writes on the existing mixed native receipt chain.

Trusted caller first durably quiesces originally active helpers (or proves they
were originally inactive), holds shared native/coordinator locks and lease, and
later reconstructs original activation with independent runtime verification.
This adapter never unloads/sets up entries and does not prove runtime readiness.
Listener-caused changes, native no-ops and unavailable call stamps are refused.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from .helper_native import NativeConfigMutation
from .helper_transaction import PreparedHelper
from .native_config_transition import NativeConfigEffectAudit, prepared_helper_digest
from .native_registry_transaction import ReceiptedRegistryTransactionAdapter
from .transaction import Position, Refused


def _require_inactive(entry):
    # Installed HA contract: missing fields are unsupported, never guessed empty.
    # NOT_LOADED alone does not exclude orphan tasks/listeners after an unload.
    try:
        if (entry.state.value != 'not_loaded' or entry.update_listeners
                or entry._on_unload or entry._async_cancel_retry_setup is not None
                or entry.setup_lock.locked()
                or any(not task.done() for task in (*entry._tasks, *entry._background_tasks))):
            raise Refused('helper_receipt_requires_quiescence')
    except (AttributeError, TypeError):
        raise Refused('helper_receipt_lifecycle_unknown') from None


class _HelperReceiptBridge:
    """Private IO facade; Position is adapted to registry evidence choreography."""
    def __init__(self, owner):
        self.owner = owner

    def _step(self, change):
        matches = [step for step in self.owner.chain.plan.steps
                   if (step.identity, step.before_revision, step.after_revision)
                   == (change.identity, change.before_revision, change.after_revision)]
        if len(matches) != 1 or self.owner._change(matches[0]) != change:
            raise Refused('helper_receipt_step_mismatch')
        return matches[0]

    async def inspect(self, change):
        owner = self.owner
        owner.guard()
        position = await owner.helper.inspect(self._step(change), guard=owner.guard)
        owner.guard()
        return SimpleNamespace(position={Position.BEFORE: 'before', Position.AFTER: 'after'}
                               .get(position, 'unknown'))

    async def _write(self, change, guard, reverse, before_mutation):
        owner = self.owner
        owner.guard()
        self._step(change)
        result = await owner.helper._write(change, guard, reverse=reverse,
                                            before_mutation=before_mutation)
        guard()
        # Keep unavailable/no-call evidence unavailable. None proves no listener.
        mutation = result.get('_native_config_mutation')
        if type(mutation) is not NativeConfigMutation or mutation.changed is not True:
            raise Refused('helper_receipt_native_call_not_confirmed')
        return mutation

    async def apply(self, change, guard, *, before_mutation=None):
        return await self._write(change, guard, False, before_mutation)

    async def compensate(self, change, guard, *, before_mutation=None):
        return await self._write(change, guard, True, before_mutation)


class ReceiptedHelperTransactionAdapter(ReceiptedRegistryTransactionAdapter):
    """Compose HelperTransactionAdapter; callbacks remain trusted installed IO.

    Both full-scope verifiers remain mandatory. A quiescent helper inspection is
    evidence about config persistence only; it cannot close full runtime AC.
    The synchronous guard rechecks ALL helpers in the mixed plan after every
    await, including observations, durable intent, flush and final verification.
    Caller must also use this lifecycle guard around other mixed-plan adapters
    if they can yield between the coordinator's preflight and their native call.
    """
    def __init__(self, helper, chain, observer, phase_loader, guard, *,
                 full_verify=None, full_verify_rollback=None):
        self.helper = helper
        self.authorization_guard = guard
        super().__init__(_HelperReceiptBridge(self), helper.resolver, chain,
                         observer, phase_loader, self._guard,
                         full_verify=full_verify, full_verify_rollback=full_verify_rollback)

    def _guard(self):
        self.authorization_guard()
        for step in self.chain.plan.steps:
            candidate = self.helper.resolver(step.prepared_ref)
            if type(candidate) is PreparedHelper:
                change = self._change(step)
                entry = self.helper.hass.config_entries.async_get_entry(
                    change.before['identity']['entry_id'])
                if entry is None:
                    raise Refused('helper_receipt_entry_missing')
                _require_inactive(entry)

    def _change(self, step):
        if step.caused_by:
            raise Refused('helper_receipt_native_listeners_unsupported')
        return self.helper._change(step)

    def _mutation_type(self):
        return NativeConfigMutation

    def _native_key(self, change):
        return 'core.config_entries'

    def _effect_audit(self, change, direction, source_raw, result_raw, mutation):
        return NativeConfigEffectAudit(change.before['identity']['entry_id'],
            'forward' if direction == 'forward' else 'reverse',
            hashlib.sha256(source_raw).hexdigest(), hashlib.sha256(result_raw).hexdigest(),
            prepared_helper_digest(change), json.loads(mutation.before_json).get('modified_at'),
            json.loads(mutation.after_json).get('modified_at'))
