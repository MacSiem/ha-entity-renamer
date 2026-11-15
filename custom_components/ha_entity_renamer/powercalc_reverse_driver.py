"""Fresh Powercalc inverse on the original journal and accepted receipt chain.

The private coordinator stages this integration's native teardown, reverses the
owned registry/helper/Energy effects, and writes YAML last so the native setup
sees the restored IDs. Other integrations retain the generic rollback order.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

from .powercalc_activation_lease import PowercalcActivationLease
from .powercalc_lifecycle import PowercalcLifecycle
from .powercalc_ordinary_driver import PowercalcOrdinaryDriver, _Adapter, _need
from .powercalc_phase_session import PowercalcPhaseSession
from .powercalc_reverse_admission import PowercalcReverseAdmission
from .transaction import Coordinator, Position, Refused


class _PowercalcInverseCoordinator(Coordinator):
    """One typed inverse order; each ordinary effect retains its own intent."""

    def __init__(self, driver, adapters):
        self.driver = driver
        super().__init__(adapters, driver.journal, driver._scope,
                         required_scopes=driver.plan.required_scopes)

    async def _unknown_after_error(self, record, reason, primary):
        try:
            return await self._unknown(record, reason)
        except Exception as status_error:
            primary.add_note('inverse UNKNOWN journal write refused: ' + type(status_error).__name__)
            raise primary from status_error

    async def rollback(self, operation_id):
        async with self.lock:
            record = await self._load(operation_id)
            self.guard()
            _need(record == self.driver.admission.record and record.status == 'applied'
                  and record.direction == 'forward'
                  and record.phases == ('verified',) * len(record.plan.steps), 'applied_record_required')
            self._admit_record('rollback')
            record = await self._save(replace(record, direction='rollback', status='rolling_back'))
            self.guard()
            try:
                await self.driver.start_inverse()
                self.guard()
                order = self.driver.chain._powercalc_reverse_order()
                _need(type(order) is tuple and set(order) == set(range(len(record.plan.steps))),
                      'reverse_order_required')
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return await self._unknown_after_error(record, 'inverse_lifecycle_uncertain', error)
            for index in order:
                step = record.plan.steps[index]
                adapter = self._adapter(step, record, index)
                try:
                    position = await adapter.inspect(step)
                    self.guard()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    return await self._unknown_after_error(record, 'inverse_inspection_uncertain', error)
                if position != Position.AFTER:
                    return await self._unknown(record, 'inverse_revision_conflict')
                record = await self._phase(record, index, 'undo_intent', 'rolling_back')
                self.guard()
                try:
                    adapter = self._adapter(step, record, index)
                    await adapter.compensate(step, self.guard)
                    self.guard()
                    position = await adapter.inspect(step)
                    self.guard()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    return await self._unknown_after_error(record, 'inverse_effect_uncertain', error)
                if position != Position.BEFORE:
                    return await self._unknown(record, 'inverse_effect_unverified')
                record = await self._phase(record, index, 'undone', 'rolling_back')
            try:
                verified = await self.driver.verify_restored(record.plan)
                self.guard()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return await self._unknown_after_error(record, 'inverse_final_verification_uncertain', error)
            if verified is not True:
                return await self._unknown(record, 'inverse_final_verification_failed')
            return await self._save(replace(record, status='rolled_back', reason=''))


class PowercalcReverseDriver(PowercalcOrdinaryDriver):
    """Fresh lease and native lifecycle; a retained receipt alone cannot bind."""

    @classmethod
    async def bind(cls, admission, *, context):
        from homeassistant.core import Context
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .recorder_guard import NativeRecorderGuard
        _need(cls is PowercalcReverseDriver and type(admission) is PowercalcReverseAdmission
              and type(context) is Context, 'reverse_admission_and_context_required')
        admission._scope()
        obj = object.__new__(cls)
        obj.admission = admission
        obj.hass, obj.runtime, obj.chain = admission.hass, admission.runtime, admission.chain
        obj.evidence, obj.original = admission.evidence, admission.capture
        obj.guard, obj.context, obj.plan = admission.guard, context, admission.plan
        obj.owner, obj.loop = asyncio.current_task(), asyncio.get_running_loop()
        obj.journal, obj.preparations = admission.journal, admission.preparations
        obj.recorder = NativeRecorderGuard(obj.hass)
        obj.bridge = NativeRegistryBridge(NativeRegistryIO(obj.hass), obj.recorder,
                                           native_images=obj.chain.accepted_documents)
        obj.lease = obj.lifecycle = obj.phase = obj.periodic = None
        obj.energy, obj.delegates = None, {}
        obj.finished = obj.used = False
        obj.result_head = None
        obj._runtime_guard()
        try:
            obj.lease = await PowercalcActivationLease.acquire(
                obj.hass, obj.chain, obj.evidence, obj.original, guard=obj._runtime_guard,
                reverse_admission=admission)
            obj._scope()
        except BaseException:
            if obj.lease is not None:
                obj.lease.release()
            raise
        return obj

    async def start_inverse(self):
        self._scope()
        _need(self.phase is None, 'reverse_lifecycle_already_started')
        from .powercalc_periodic_lease import PowercalcPeriodicLease
        prepared = await PowercalcPeriodicLease.prepare(self.lease, guard=self._runtime_guard)
        self.lifecycle = await PowercalcLifecycle.start_reverse(self.lease)
        self.phase = await PowercalcPhaseSession.capture(self.lifecycle)
        self.periodic = PowercalcPeriodicLease.acquire(self.phase, prepared, guard=self._runtime_guard)
        self._scope()
        _need(len(self.lifecycle.groups) == 1, 'one_qualified_group_required')
        group = self.lifecycle.groups[0]
        await self.phase.unload_group(group.entity_id)
        await self.phase.quiesce_sources()
        from .powercalc_pss_handoff import async_handoff_previous_states
        from .powercalc_restore_handoff import async_handoff_group_restore
        await async_handoff_previous_states(self.lifecycle)
        await async_handoff_group_restore(self.lifecycle, group.entity_id)
        self._scope()
        await self._make_delegates()

    async def verify_restored(self, plan):
        from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
        from .powercalc_retired_group_alias import retire_group_alias
        from .powercalc_runtime_observer import capture_powercalc_runtime
        self._scope()
        _need(plan == self.plan and self.phase is not None
              and self.phase.phase == 'active_receipted', 'restored_phase_required')
        record = await self.journal.load(self.chain.operation_id)
        self._scope()
        _need(record is not None and record.plan == plan and record.direction == 'rollback'
              and record.phases == ('undone',) * len(plan.steps), 'all_inverse_steps_required')
        self.phase.verify_activation()
        current = capture_powercalc_runtime(self.hass, guard=self._scope)
        if retire_group_alias(self.hass, self.chain, self.phase, current):
            self._scope()
            current = capture_powercalc_runtime(self.hass, guard=self._scope)
        bound = await async_observe_accepted_powercalc_consumers(
            self.hass, self.chain, self.evidence, current, guard=self._scope)
        self._scope()
        _need(bound.source_position == 'original' and all(row.source_verified for row in bound.rows),
              'restored_original_graph_required')
        for step in plan.steps:
            if step.adapter == 'registry':
                result = await self.bridge.inspect(self._resolve(step.prepared_ref))
                self._scope()
                _need(result.position == 'before', 'restored_registry_or_history_changed')
            elif step.adapter == 'helpers':
                from .helper_native import read_entry
                from .helper_persistence import NativeHelperPersistence
                from .helper_plan import snapshot_equal
                change = self._resolve(step.prepared_ref)
                sample = read_entry(self.hass, change.before['identity']['entry_id'])
                _need(snapshot_equal(sample, change.before)
                      and await NativeHelperPersistence(self.hass).inspect_current(sample),
                      'restored_group_metadata_changed')
                self._scope()
            elif step.adapter == 'energy':
                change = self._resolve(step.prepared_ref)
                sample = await self.energy.read()
                self._scope()
                _need(sample.value == change.before.value and sample.revision == change.before.revision,
                      'restored_energy_reference_changed')
        self.phase.verify_activation()
        self.result_head = self.lease.state.head
        self.phase.close()
        self.finished = True
        return True

    async def rollback(self):
        self._scope()
        _need(not self.used and self.phase is None, 'one_inverse_attempt_only')
        self.used = True
        adapters = {kind: _Adapter(self, kind) for kind in self.plan.required_scopes}
        coordinator = _PowercalcInverseCoordinator(self, adapters)
        result = primary = cleanup = None
        release_failed = False
        try:
            result = await coordinator.rollback(self.chain.operation_id)
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
            try:
                self.admission.close()
            except BaseException as error:
                release_failed = True
                if cleanup is None:
                    cleanup = error
                else:
                    cleanup.add_note('reverse admission close failed: ' + type(error).__name__)
            if cleanup is not None:
                from .powercalc_phase_session import PowercalcPhaseError
                expected = (not release_failed and type(cleanup) is PowercalcPhaseError and result is not None
                    and result.status == 'unknown' and self.phase.closed
                    and str(cleanup) == 'powercalc_phase_incomplete_effect_requires_recovery')
                if primary is not None:
                    primary.add_note('inverse cleanup failed: ' + type(cleanup).__name__)
                elif not expected:
                    raise cleanup
