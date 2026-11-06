"""One live ORIGINAL-bound Powercalc operation through the existing Coordinator.

This private driver accepts only an already prepared server-owned chain; it is
not a websocket endpoint, a review approval, or an authority to replay UNKNOWN.
"""
from __future__ import annotations

import asyncio

from .native_registry_receipts import RegistryReceiptChain, RegistryRawObservation
from .powercalc_activation_lease import PowercalcActivationLease
from .powercalc_composition import PowercalcComposition
from .powercalc_lifecycle import PowercalcLifecycle
from .powercalc_phase_session import PowercalcPhaseSession
from .powercalc_runtime_observer import _guard
from .transaction import Coordinator, PhaseContext, Position, Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_driver_' + reason)


class _RuntimeReadback:
    def __init__(self, driver):
        self.driver = driver

    async def position(self, change):
        phase = self.driver.phase
        _need(phase is not None, 'live_phase_required')
        reverse = phase.lifecycle.direction == 'rollback'
        if phase.phase == 'sources_inactive':
            phase.verify_activation_preflight()
            return Position.AFTER if reverse else Position.BEFORE
        if phase.phase in ('activated', 'active_receipted'):
            phase.verify_activation()
            return Position.BEFORE if reverse else Position.AFTER
        return Position.UNKNOWN

    async def verify_inventory(self, plan, *, rollback=False):
        return False if rollback else await self.driver.verify(plan)


class _Adapter:
    """Preflight on active ORIGINAL, then delegate to the same live phase."""
    def __init__(self, driver, kind):
        self.driver, self.kind, self.context = driver, kind, None

    def bind_phase_context(self, context):
        _need(context is None or type(context) is PhaseContext, 'phase_context_type')
        self.context = context
        delegate = self.driver.delegates.get(self.kind)
        if delegate is not None:
            delegate.bind_phase_context(context)

    async def inspect(self, step):
        if self.driver.phase is None:
            return await self.driver.inspect_original(step)
        delegate = self.driver.delegates[self.kind]
        delegate.bind_phase_context(self.context)
        return await delegate.inspect(step)

    async def apply(self, step, guard):
        _need(type(self.context) is PhaseContext, 'durable_phase_required')
        self.context.require_effect(step, 'forward')
        await self.driver.start(self.context)
        delegate = self.driver.delegates[self.kind]
        delegate.bind_phase_context(self.context)
        return await delegate.apply(step, guard)

    async def compensate(self, step, guard):
        _need(type(self.context) is PhaseContext and self.driver.phase is not None
              and self.driver.phase.lifecycle.direction == 'rollback', 'fresh_reverse_phase_required')
        self.context.require_effect(step, 'rollback')
        delegate = self.driver.delegates[self.kind]
        delegate.bind_phase_context(self.context)
        return await delegate.compensate(step, guard)

    async def verify(self, plan):
        return await self.driver.verify(plan)

    async def verify_rollback(self, plan):
        return False


class PowercalcOrdinaryDriver:
    def __init__(self):
        raise Refused('powercalc_driver_installed_binding_required')

    @classmethod
    async def bind(cls, hass, runtime, chain, evidence, original, *, guard, context):
        from homeassistant.core import Context, HomeAssistant
        from . import Runtime
        from .const import DATA_RUNTIME
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .powercalc_runtime_observer import PowercalcRuntimeCapture
        from .recorder_guard import NativeRecorderGuard
        from .yaml_source_binding import OriginalSourceEvidence

        _need(cls is PowercalcOrdinaryDriver and type(hass) is HomeAssistant
              and type(runtime) is Runtime and hass.data.get(DATA_RUNTIME) is runtime
              and runtime.loaded and not runtime.unloading and runtime.operation_lock.locked()
              and type(chain) is RegistryReceiptChain and chain.writable and chain.active is None
              and type(evidence) is OriginalSourceEvidence and type(original) is PowercalcRuntimeCapture
              and type(context) is Context and callable(guard), 'installed_scope_required')
        _guard(guard)
        baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
        _need(type(composition) is PowercalcComposition and baseline == chain.baseline
              and composition.evidence == evidence and baseline.receipt == chain.original_receipt
              and runtime.preparations is chain.preparations and runtime.journal is not None
              and chain.reconstruct_inputs().head == chain.root_digest,
              'same_original_chain_required')
        plan = chain.plan
        plan.validate()
        _need(set(plan.required_scopes) <= {'registry', 'helpers', 'energy', 'yaml'}
              and {'registry', 'helpers', 'yaml'} <= set(plan.required_scopes)
              and all(step.adapter in ('registry', 'helpers', 'energy', 'yaml') for step in plan.steps)
              and plan.steps[-1].adapter == 'yaml', 'full_supported_queue_required')
        from .powercalc_lifecycle import _moves
        moves = _moves(chain, composition)
        _need(len(moves) == 1 and moves[0].before != moves[0].after,
              'qualified_group_rename_required')
        # Refuse unsupported owned-ID movements before the first effect, not
        # after the source/group prefix has already been committed.
        from homeassistant.helpers import entity_registry as er
        from .native_registry import PreparedRegistryChange
        registry = er.async_get(hass)
        for index, step in enumerate(plan.steps):
            if step.adapter != 'registry':
                continue
            change = chain._original_change(index)
            if type(change) is PreparedRegistryChange and change.kind == 'entity' and change.mapping:
                entry = registry.async_get(change.before['entity_id'])
                _need(entry is not None, 'original_registry_entry_required')
                if entry.platform == 'powercalc':
                    _need(change.before['entity_id'] == moves[0].before
                          and change.after['entity_id'] == moves[0].after,
                          'unsupported_owned_id_before_effect')
        from .powercalc_group_metadata import PreparedPowercalcGroupMetadata, assert_original_group
        metadata = [chain._original_change(i) for i, step in enumerate(plan.steps)
                    if step.adapter == 'helpers']
        _need(len(metadata) == 1 and type(metadata[0]) is PreparedPowercalcGroupMetadata,
              'qualified_group_metadata_required')
        assert_original_group(chain, metadata[0])
        obj = object.__new__(cls)
        obj.hass, obj.runtime, obj.chain, obj.evidence, obj.original = hass, runtime, chain, evidence, original
        obj.guard, obj.context, obj.plan = guard, context, plan
        obj.owner, obj.loop = asyncio.current_task(), asyncio.get_running_loop()
        obj.journal, obj.preparations = runtime.journal, runtime.preparations
        obj.recorder = NativeRecorderGuard(hass)
        obj.bridge = NativeRegistryBridge(NativeRegistryIO(hass), obj.recorder,
                                           native_images=chain.accepted_documents)
        obj.lease = obj.lifecycle = obj.phase = obj.periodic = None
        obj.energy = None
        obj.delegates = {}
        obj.finished = obj.used = False
        obj.result_head = None
        obj._runtime_guard()
        try:
            obj.lease = await PowercalcActivationLease.acquire(hass, chain, evidence, original,
                                                               guard=obj._runtime_guard)
            obj._scope()
        except BaseException:
            if obj.lease is not None:
                obj.lease.release()
            raise
        return obj

    def _runtime_guard(self):
        from .const import DATA_RUNTIME
        _guard(self.guard)
        if self.periodic is not None and not self.periodic.closed:
            self.periodic.check()
        _need(asyncio.get_running_loop() is self.loop and self.owner is not None and not self.owner.done()
              and self.hass.data.get(DATA_RUNTIME) is self.runtime and self.runtime.loaded
              and not self.runtime.unloading and self.runtime.operation_lock.locked()
              and self.runtime.preparations is self.preparations and self.runtime.journal is self.journal,
              'runtime_or_owner_changed')

    def _scope(self):
        self._runtime_guard()
        if self.lease is not None:
            self.lease.check()
        if self.phase is not None and not self.finished:
            _need(not self.phase.closed and self.phase.lease is self.lease, 'live_phase_changed')
            self.phase.lifecycle.check()
            self.phase.scope.check()

    def _resolve(self, reference):
        indexes = [index for index, step in enumerate(self.plan.steps) if step.prepared_ref == reference]
        _need(len(indexes) == 1, 'one_original_preparation_required')
        return self.chain._original_change(indexes[0])

    async def _observe(self):
        from .native_input_observation import async_observe_native_inputs
        self._scope()
        return await async_observe_native_inputs(self.hass, self.chain.original_receipt,
                                                guard=self._scope, input_state=self.lease.state)

    async def inspect_original(self, step):
        from .native_adapters import EnergyTransactionAdapter
        from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
        from .powercalc_prepared_yaml import PreparedPowercalcYAML, assert_powercalc_yaml_composition
        self._scope()
        _need(self.phase is None and step in self.plan.steps, 'active_original_preflight_required')
        observed = await self._observe()
        rows = tuple(RegistryRawObservation(row.key, row.raw, row.identity) for row in observed.files)
        self.chain.verify_initial(rows)
        change = self._resolve(step.prepared_ref)
        if step.adapter == 'registry':
            value = await self.bridge.inspect(change)
            self._scope()
            return {'before': Position.BEFORE, 'after': Position.AFTER}.get(value.position, Position.UNKNOWN)
        if step.adapter == 'helpers':
            from .helper_native import read_entry
            from .helper_persistence import NativeHelperPersistence
            from .helper_plan import snapshot_equal
            from .powercalc_group_metadata import assert_original_group
            assert_original_group(self.chain, change)
            current = read_entry(self.hass, change.before['identity']['entry_id'])
            durable = await NativeHelperPersistence(self.hass).inspect_current(current)
            self._scope()
            return Position.BEFORE if durable and snapshot_equal(current, change.before) else Position.UNKNOWN
        if step.adapter == 'energy':
            if self.energy is None:
                from homeassistant.components.energy.data import async_get_manager
                from .energy_native import NativeEnergy
                self.energy = NativeEnergy(self.hass, await async_get_manager(self.hass))
                self._scope()
            inner = EnergyTransactionAdapter(self.energy, self._resolve, None, None)
            return await inner.inspect(step)
        _need(step.adapter == 'yaml' and type(change) is PreparedPowercalcYAML,
              'typed_yaml_preflight_required')
        assert_powercalc_yaml_composition(self.chain, change)
        observed = await async_observe_accepted_powercalc_consumers(
            self.hass, self.chain, self.evidence, self.original, guard=self._scope)
        self._scope()
        _need(observed.source_position == 'original', 'active_original_graph_required')
        return Position.BEFORE

    async def start(self, context):
        if self.phase is not None:
            self._scope()
            return
        self._scope()
        _need(type(context) is PhaseContext and context.record.operation_id == self.chain.operation_id
              and context.record.plan == self.plan, 'same_journal_intent_required')
        context.require_effect(context.step, 'forward')
        record = await self.journal.load(self.chain.operation_id)
        self._scope()
        _need(record == context.record, 'durable_journal_changed')
        from .powercalc_periodic_lease import PowercalcPeriodicLease
        periodic_preparation = await PowercalcPeriodicLease.prepare(self.lease, guard=self._runtime_guard)
        self.lifecycle = await PowercalcLifecycle.start(self.lease)
        self.phase = await PowercalcPhaseSession.capture(self.lifecycle)
        # No await after installing the reload witness and before fencing its
        # actual native timer. A pending dump instead refuses before unload.
        self.periodic = PowercalcPeriodicLease.acquire(
            self.phase, periodic_preparation, guard=self._runtime_guard)
        self._scope()
        groups = self.lifecycle.composition.previous.groups
        _need(len(groups) == 1, 'one_qualified_group_required')
        await self.phase.unload_group(groups[0].entity_id)
        await self.phase.quiesce_sources()
        from .powercalc_pss_handoff import async_handoff_previous_states
        from .powercalc_restore_handoff import async_handoff_group_restore
        await async_handoff_previous_states(self.lifecycle)
        await async_handoff_group_restore(self.lifecycle, groups[0].entity_id)
        self._scope()
        await self._make_delegates()

    async def _make_delegates(self):
        from .powercalc_registry_adapter import PowercalcRegistryAdapter
        from .powercalc_yaml_adapter import PowercalcYAMLAdapter
        from .yaml_editor_locks import NativeYAMLEditorLocks
        self.delegates['registry'] = PowercalcRegistryAdapter(
            self.phase, self.bridge, self._resolve, self._observe, self.journal.load,
            self._runtime_guard, full_verify=self.verify, full_verify_rollback=lambda _: False)
        if 'helpers' in self.plan.required_scopes:
            from .powercalc_group_metadata_adapter import PowercalcGroupMetadataAdapter
            self.delegates['helpers'] = PowercalcGroupMetadataAdapter(
                self.phase, self._resolve, self._observe, self.journal.load,
                self._runtime_guard, full_verify=self.verify)
        if 'energy' in self.plan.required_scopes:
            if self.energy is None:
                from homeassistant.components.energy.data import async_get_manager
                from .energy_native import NativeEnergy
                self.energy = NativeEnergy(self.hass, await async_get_manager(self.hass))
                self._scope()
            from .powercalc_energy_transaction import ReceiptedPowercalcEnergyAdapter
            self.delegates['energy'] = ReceiptedPowercalcEnergyAdapter(
                self.energy, self.lifecycle, self.journal.load, self.verify, lambda _: False)
        from .native_input_observation import async_observe_native_inputs
        async def observe_state(state):
            return await async_observe_native_inputs(self.hass, self.chain.original_receipt,
                                                    guard=self._scope, input_state=state)
        self.delegates['yaml'] = PowercalcYAMLAdapter(self.phase, self.hass, self._resolve,
            NativeYAMLEditorLocks(self.hass), _RuntimeReadback(self), self.chain,
            observe_state, self.journal.load, self._scope, context=self.context,
            full_verify=self.verify, full_verify_rollback=lambda _: False)

    async def verify(self, plan):
        from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
        from .powercalc_runtime_observer import capture_powercalc_runtime
        self._scope()
        _need(plan == self.plan and self.phase is not None, 'same_plan_required')
        if self.finished:
            return self.lease.state.head == self.result_head
        _need(self.phase.phase == 'active_receipted', 'receipted_activation_required')
        record = await self.journal.load(self.chain.operation_id)
        self._scope()
        _need(record is not None and record.plan == plan and record.direction == 'forward'
              and record.phases == ('verified',) * len(plan.steps), 'all_journal_steps_required')
        self.phase.verify_activation()
        # This is a new runtime identity readback, never a new ORIGINAL: the
        # accepted candidate graph and every registry UUID stay chain-bound.
        current = capture_powercalc_runtime(self.hass, guard=self._scope)
        from .powercalc_retired_group_alias import retire_group_alias
        if retire_group_alias(self.hass, self.chain, self.phase, current):
            self._scope()
            current = capture_powercalc_runtime(self.hass, guard=self._scope)
        bound = await async_observe_accepted_powercalc_consumers(
            self.hass, self.chain, self.evidence, current, guard=self._scope)
        self._scope()
        _need(bound.source_position == 'proposed' and all(row.source_verified for row in bound.rows),
              'accepted_proposed_graph_required')
        for step in plan.steps:
            if step.adapter == 'registry':
                result = await self.bridge.inspect(self._resolve(step.prepared_ref))
                self._scope()
                _need(result.position == 'after', 'registry_history_or_uuid_changed')
            elif step.adapter == 'helpers':
                from .helper_native import read_entry
                from .helper_persistence import NativeHelperPersistence
                from .helper_plan import snapshot_equal
                change = self._resolve(step.prepared_ref)
                sample = read_entry(self.hass, change.after['identity']['entry_id'])
                _need(snapshot_equal(sample, change.after)
                      and await NativeHelperPersistence(self.hass).inspect_current(sample),
                      'group_metadata_changed')
                self._scope()
            elif step.adapter == 'energy':
                change = self._resolve(step.prepared_ref)
                sample = await self.energy.read()
                self._scope()
                _need(sample.value == change.after and sample.revision == change.after_revision,
                      'energy_reference_changed')
        self.phase.verify_activation()
        self.result_head = self.lease.state.head
        self.phase.close()
        self.finished = True
        return True

    async def apply(self):
        self._scope()
        _need(not self.used and self.phase is None, 'one_attempt_only')
        self.used = True
        adapters = {kind: _Adapter(self, kind) for kind in self.plan.required_scopes}
        coordinator = Coordinator(adapters, self.journal, self._scope,
                                  required_scopes=self.plan.required_scopes)
        result = primary = cleanup = None
        release_failed = False
        try:
            result = await coordinator.apply(self.plan, self.plan.fingerprint, self.chain.operation_id)
            return result
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                if self.phase is not None and not self.finished and not self.phase.closed:
                    self.phase.close()
            except BaseException as error:
                cleanup = error
            try:
                if self.periodic is not None and not self.periodic.closed:
                    self.periodic.release()
            except BaseException as error:
                if cleanup is None:
                    cleanup = error
                else:
                    cleanup.add_note('periodic timer release also failed: ' + type(error).__name__)
                release_failed = True
            try:
                self.lease.release()
            except BaseException as error:
                release_failed = True
                if cleanup is None:
                    cleanup = error
                else:
                    cleanup.add_note('lease release also failed: ' + type(error).__name__)
            if cleanup is not None:
                from .powercalc_phase_session import PowercalcPhaseError
                expected = (not release_failed and type(cleanup) is PowercalcPhaseError and result is not None
                    and result.status == 'unknown' and self.phase.closed
                    and str(cleanup) == 'powercalc_phase_incomplete_effect_requires_recovery')
                if primary is not None:
                    primary.add_note('phase cleanup failed: ' + type(cleanup).__name__)
                elif not expected:
                    raise cleanup
