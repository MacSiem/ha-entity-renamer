"""Server-owned HomeKit unload, receipted steps and native setup lifecycle.

The native setup half runs only after the same journal has accepted the YAML
and ConfigEntry receipts. A crash or lost ACK is never replayed here. This
private driver has no plan-construction or public-admission capability.
"""
from __future__ import annotations

from .transaction import PhaseContext, Record, Refused


def _need(value, reason):
    if not value:
        raise Refused('homekit_lifecycle_' + reason)


class HomeKitLifecycleDriver:
    def __init__(self, root):
        from .native_homekit_yaml_binding import HomeKitYAMLBinding
        from .native_homekit_helper_adapter import prepare_entries
        binding = getattr(root, 'homekit_yaml_binding', None)
        _need(type(binding) is HomeKitYAMLBinding and binding.hass is root.hass
              and binding.receipt == root.receipt and binding.mapping == root.mapping,
              'original_binding')
        steps = root.plan.steps
        helpers = [step for step in steps if step.adapter == 'helpers']
        changes = prepare_entries(binding)
        resolver = lambda ref: root.preparations.resolve(ref, root.digests[ref])
        from .prepared import decode_change
        resolved = [decode_change(resolver(step.prepared_ref)) for step in helpers]
        adapters = tuple(step.adapter for step in steps)
        registries, dashboards, energy = adapters.count('registry'), adapters.count('dashboard'), adapters.count('energy')
        _need(bool(registries) and bool(helpers) and len(helpers) == len(changes)
              and energy in (0, 1) and adapters == (('registry',) * registries + ('helpers',) * len(helpers)
                               + ('energy',) * energy + ('dashboard',) * dashboards + ('yaml',))
              and {change.identity for change in resolved} == {change.identity for change in changes}
              and all(change in changes for change in resolved)
              and all((step.identity, step.before_revision, step.after_revision)
                      == (change.identity, change.before_revision, change.after_revision)
                      for step, change in zip(helpers, resolved))
              and all(not step.caused_by for step in steps)
              and steps[-1].adapter == 'yaml'
              and max(steps.index(step) for step in helpers) < len(steps) - 1,
              'plan_binding')
        self.root, self.binding = root, binding
        self.used = False

    async def _original(self, reference, receipt):
        from .native_input_baseline import _drained
        from .native_input_observation import async_observe_native_inputs
        from .yaml_validation import assert_validation_current
        root = self.root
        root._scope()
        _need(reference == root.plan.native_inputs and receipt == root.receipt, 'original_receipt')
        await _drained(root.hass.async_add_executor_job, lambda: assert_validation_current(receipt))
        root._scope()
        observed = await async_observe_native_inputs(root.hass, receipt, guard=root._scope,
                                                     physical_check=root.consumers.physical)
        root._scope()
        baseline = root.chain.baseline
        _need(observed.original_receipt_json == baseline.receipt_json
              and observed.files == baseline.files, 'original_changed')
        return True

    async def _accepted(self, authorization):
        from .native_registry_receipts import RegistryRawObservation
        root = self.root
        root._scope()
        record = await root.journal.load(root.operation_id)
        root._scope()
        _need(record is not None and record.plan == root.plan, 'journal_missing')
        observed = await root._observe()
        root.chain.expected(PhaseContext(record, 0), tuple(
            RegistryRawObservation(row.key, row.raw, row.identity) for row in observed.files))
        root._scope()
        return root.chain.verify_setup_authorization(authorization)

    async def apply(self):
        from .native_helper_activation_lease import NativeHelperActivationLease
        from .native_helper_lifecycle import (NativeHelperLifecycle, RestoreAuthorization,
                                             native_helper_lifecycle_store)
        root, binding = self.root, self.binding
        _need(not self.used, 'no_replay')
        self.used = True
        root._scope(); binding.phase.check()
        _need(binding.verify('before') is True, 'original_bridge_unconfirmed')
        entries = tuple(root.hass.config_entries.async_get_entry(entry_id)
                        for entry_id in sorted(binding.observer.before))
        _need(all(entry is not None for entry in entries), 'entry_missing')
        lease = await NativeHelperActivationLease.acquire(root.hass, root.hass.config_entries,
                                                           entries, guard=root._scope)
        root.homekit_lease = lease
        binding.observer.bind_activation_lease(lease)
        life = None
        try:
            store = getattr(root, 'homekit_lifecycle_store', None)
            fresh = native_helper_lifecycle_store(root.hass, root.preparations)
            await fresh.initialize()
            root._scope(); lease.check()
            from .native_registry_receipts import RegistryReceiptStore
            _need(type(store) is RegistryReceiptStore and store.preparations is root.preparations
                  and store.rows == fresh.rows, 'lifecycle_store_changed')
            root.plan.validate()
            await root.journal.create(Record(root.operation_id, root.plan,
                phases=('pending',) * len(root.plan.steps), restore_attempted=False))
            root._scope(); lease.check()
            await root.refresh()
            root._scope(); lease.check()
            life = await NativeHelperLifecycle.start(store, root.hass.config_entries,
                root.operation_id, root.plan, root.receipt, root.digests,
                activation_lease=lease, guard=root._scope,
                verify_original_native_inputs=self._original, settle=binding.observer.settle,
                verify_inactive=binding.observer.verify_inactive,
                verify_runtime=binding.observer.verify_lifecycle_runtime,
                verify_accepted_native_chain=self._accepted)
            await life.quiesce()
            coordinator = root._coordinator()
            original_guard = coordinator.guard
            def guard():
                original_guard(); lease.check(); life.require_quiescent()
                _need(binding.verify_foreign() is True, 'foreign_bridge_unconfirmed')
            coordinator.guard = guard
            ready = await coordinator.reconcile(root.operation_id)
            _need(ready.status == 'ready' and ready.direction == 'forward'
                  and ready.phases == ('pending',) * len(root.plan.steps), 'initial_not_pristine')
            result = await coordinator.resume(root.operation_id)
            root._scope(); lease.check()
            _need(result.status == 'applied', 'apply_unresolved')
            authorization = RestoreAuthorization(root.operation_id, root.plan.fingerprint,
                                                  root.chain.reconstruct_inputs().head, 'applied')
            await life.restore(authorization)
            await root.refresh()
            root._scope(); lease.check()
            await root._terminal(result, rollback=False)
            root._scope(); lease.check()
            if getattr(root, 'utility_yaml_binding', None) is not None:
                _need(await coordinator.adapters['registry'].verify(root.plan) is True,
                      'utility_terminal_readback_unconfirmed')
                root._scope(); lease.check()
            _need(await binding.observer.verify_inventory(root.plan) is True
                  and binding.verify('after') is True, 'runtime_unconfirmed')
            status = life.inspect()
            _need(status['activation_receipts_complete'] and not status['pending'], 'setup_unresolved')
            return result
        finally:
            try:
                if life is not None:
                    life.close_source_gaps()
            finally:
                binding.observer.bind_activation_lease(None)
                root.homekit_lease = None
                lease.release()
