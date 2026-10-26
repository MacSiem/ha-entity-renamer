"""Installed Utility and own-ID Integration setup IO under the existing lifecycle scope.

Actual API, CPython call/return evidence, guarded native flush and independent
full-three observations. No endpoint, replacement baseline or recovery replay.
"""
from __future__ import annotations

from .native_helper_lifecycle import NativeHelperLifecycle, RestoreAuthorization
from .native_registry import NativeRegistryIO
from .native_registry_receipts import RegistryReceiptChain, RegistryRawObservation
from .native_input_observation import NativeInputObservation
from .native_utility_setup_audit import NativeUtilitySetupAudit
from .transaction import PhaseContext


class HelperSetupEffectsError(ValueError):
    """Sanitized failure; a partially executed setup remains UNKNOWN."""


def _need(condition):
    if not condition:
        raise HelperSetupEffectsError('native_helper_setup_effect_rejected') from None


class NativeHelperSetupEffects:
    """Trusted backend IO; callbacks must come from the installed coordinator.

    observe() is the existing ORIGINAL/accepted-input native observation reader.
    load_record() independently loads the actual terminal journal record. guard()
    retains admin/runtime/editor/external-lease scope, through every await. It is
    deliberately separate from the inner adapter's all-helpers-inactive guard.
    """

    def __init__(self, hass, chain, observe, load_record, *, guard):
        _need(type(chain) is RegistryReceiptChain and all(callable(f) for f in
              (observe, load_record, guard)))
        self.hass, self.chain = hass, chain
        self.observe, self.load_record, self.guard = observe, load_record, guard

    def _scope(self, lifecycle):
        _need(type(lifecycle) is NativeHelperLifecycle and lifecycle.writable
              and not lifecycle.failed and lifecycle.plan == self.chain.plan
              and lifecycle.operation_id == self.chain.operation_id
              and lifecycle.store.preparations is self.chain.preparations)
        lifecycle._authorize()
        lifecycle.store._ready()
        self.chain.store._ready()
        _need(self.guard() is not False)
        return True  # Only after the real installed guards above have succeeded.

    async def _read(self, lifecycle):
        self._scope(lifecycle)
        result = await self.observe()
        self._scope(lifecycle)
        _need(type(result) is NativeInputObservation
              and result.version == self.chain.version
              and result.original_receipt_json == self.chain.baseline.receipt_json)
        return tuple(RegistryRawObservation(row.key, row.raw, row.identity) for row in result.files)

    async def __call__(self, entry_id, lifecycle, setup_intent, authorization):
        self._scope(lifecycle)
        _need(type(authorization) is RestoreAuthorization)
        key = lifecycle._slot('setup', entry_id, 'intent')
        intent_row = lifecycle.store.read(key)
        _need(type(setup_intent) is tuple and intent_row is not None
              and setup_intent == (key, intent_row['digest'])
              and intent_row['payload']['authorization'] == {
                  'operation_id': authorization.operation_id,
                  'fingerprint': authorization.fingerprint,
                  'head': authorization.head, 'status': authorization.status})
        original = next(row for row in lifecycle.root['original'] if row['entry_id'] == entry_id)
        lifecycle._inactive(original)
        record = await self.load_record(lifecycle.operation_id)
        self._scope(lifecycle)
        _need(record is not None and record.status == authorization.status)
        context = PhaseContext(record, 0)
        before = await self._read(lifecycle)
        self.chain.expected(context, before)
        self.chain.verify_setup_authorization(authorization)
        integration = original['domain'] == 'integration'
        if not integration:
            baseline, = tuple(value for value in lifecycle.runtime_baselines() if value.entry_id == entry_id)
        before_snapshot, after_snapshot = lifecycle.helper_snapshots[entry_id]
        snapshot = after_snapshot if authorization.status == 'applied' else before_snapshot
        own_change = lifecycle.own_registry_changes.get(entry_id)
        direction = ('forward' if authorization.status == 'applied' else 'rollback') if own_change else None
        source_rows = [row for row in self.chain.accepted_documents()['core.entity_registry']
                       ['data']['entities'] if row.get('config_entry_id') == entry_id]
        clock = lifecycle._integration_clocks.get(entry_id)
        if clock is not None:
            from .native_integration_clock_lifecycle import retained
            _need(retained(lifecycle,entry_id) is clock)
            await clock.source_binding.accept_setup(clock,authorization,self.observe,self.load_record)
            self._scope(lifecycle)
            before = await self._read(lifecycle)
            self.chain.expected(context,before)
        intent = await self.chain.begin_helper_setup(context, before,
            lifecycle=lifecycle, entry_id=entry_id)
        self._scope(lifecycle)
        fresh = await self._read(lifecycle)
        self.chain.verify_helper_setup_source(intent, context, fresh)
        lifecycle._inactive(original)
        # Existing managers have just passed actual native full-three capture.
        # Guard direct lookup before constructing the established native writer.
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        _need(type(self.hass.data.get(er.DATA_REGISTRY)) is er.EntityRegistry
              and type(self.hass.data.get(dr.DATA_REGISTRY)) is dr.DeviceRegistry)
        io = NativeRegistryIO(self.hass)
        gap = lifecycle._source_gaps.get(entry_id)
        if clock is not None:
            from .native_integration_clock_lifecycle import NativeIntegrationClockSetupAudit
            if own_change is not None:
                from .native_integration_own_clock import NativeIntegrationOwnClockSetupAudit
                collector_type = NativeIntegrationOwnClockSetupAudit
            else:
                collector_type = NativeIntegrationClockSetupAudit
            collector = collector_type(self.hass,lifecycle,self.chain,entry_id,source_rows,
                guard=lambda:self._scope(lifecycle),setup_intent=intent)
        elif integration:
            from .native_integration_setup_audit import NativeIntegrationSetupAudit
            collector = NativeIntegrationSetupAudit(self.hass, lifecycle, self.chain, entry_id,
                source_rows, guard=lambda: self._scope(lifecycle))
        else:
            collector = NativeUtilitySetupAudit(self.hass, baseline, snapshot, source_rows,
                guard=lambda: self._scope(lifecycle), own_registry_change=own_change,
                direction=direction)
        with collector:
            manager = lifecycle.activation_lease or self.hass.config_entries
            if clock is not None:
                await clock.setup()
            else:
                _need(await manager.async_setup(entry_id) is True)
            self._scope(lifecycle)
            _need(await lifecycle.settle() is not False)
            self._scope(lifecycle)
            if gap is not None:
                lifecycle._source_gap_data[entry_id] = gap.settle(snapshot, collector)
            audit = collector.finish()
            # Native setup schedules the registry's native delayed write. The
            # existing trusted writer flushes that exact manager under this scope;
            # independent raw/manager/fsync reads, not a save ACK, prove durability.
            await io.flush('entity')
            self._scope(lifecycle)
            if gap is not None:
                gap.check()
            _need(collector.finish() == audit)
            after = await self._read(lifecycle)
            _need(collector.finish() == audit)
            reference = await self.chain.complete_helper_setup(intent, context,
                before, after, audit=audit)
            self._scope(lifecycle)
            if gap is not None:
                gap.check()
            _need(collector.finish() == audit)
            final = await self._read(lifecycle)
            self.chain.expected(context, final)
            self.chain.verify_setup_authorization(authorization)
            _need(collector.finish() == audit)
            if gap is not None:
                gap.check()
        return reference
