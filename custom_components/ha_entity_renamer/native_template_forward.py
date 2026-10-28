"""Private server composer for one numeric Template ConfigEntry source rename.

This component is not a manifest importer or an all-consumer readiness gate.
The caller holds the Runtime lock throughout prepare/apply/close. No request
supplies a root, receipt, operation, preparation or native callback.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
import uuid

from .const import DATA_RUNTIME
from .transaction import Refused, Plan, Step, PhaseContext, Record
from .native_template_reverse import NativeTemplateReverse, _authorize
from .native_input_baseline import _drained, async_capture_native_input_baseline
from .native_input_observation import async_observe_native_inputs
from .native_registry_receipts import RegistryReceiptChain, RegistryRawObservation, native_registry_receipt_store
from .native_helper_lifecycle import NativeHelperLifecycle, RestoreAuthorization, native_helper_lifecycle_store
from .native_helper_activation_lease import NativeHelperActivationLease
from .native_template_entry_lifecycle import NativeTemplateEntryObserver
from .native_template_composition import TemplateComposition
from .prepared import native_prepared_store, encode_change
from .journal import native_journal
from .helper_native import inventory, read_entry


def _need(value, reason):
    if not value:
        raise Refused('template_forward_' + reason) from None


class _UnchangedConsumers:
    """Task-local read comparisons, never an adapter or effect capability."""

    @classmethod
    async def capture(cls, hass, mapping, *, guard):
        from .native_discovery_consumers import async_observe_discovery_consumers
        from .discovery import _plain
        from .discovery_final_fence import _ConsumerFence
        from .dashboard_plan import plan_dashboard
        from .energy_plan import plan_energy
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        obj = cls()
        obj.hass, obj.thread = hass, threading.get_ident()
        obj.loaded = frozenset(hass.config.components) & {'automation', 'script', 'lovelace', 'energy'}
        # Missing readers are never evidence for a loaded/native-present family.
        obj.presence = (id(hass.data.get(LOVELACE_DATA)), id(hass.data.get('energy_manager')))
        observed = await async_observe_discovery_consumers(hass, guard=guard)
        guard()
        names = []
        for name, component, present in (
                ('dashboard', 'lovelace', hass.data.get(LOVELACE_DATA) is not None),
                ('energy', 'energy', hass.data.get('energy_manager') is not None)):
            result = getattr(observed, name)
            if component not in obj.loaded and not present:
                continue
            _need(result.status == 'COMPLETE', name + '_evidence_unavailable')
            names.append(name)
            if name == 'dashboard':
                # File/hash inventory alone cannot prove YAML dashboard semantics.
                _need(not result.value.yaml_sources, 'dashboard_yaml_route_unqualified')
                configs = [row.config for row in result.value.snapshots if row.config is not None]
                planner = plan_dashboard
            else:
                configs = [] if result.value.value is None else [result.value.value]
                planner = plan_energy
            for config in configs:
                plan = planner(_plain(config), mapping)
                _need(not plan.markers and not plan.changes, name + '_dependency_unsupported')
        obj.native = _ConsumerFence(hass, observed, _plain, names=tuple(names))
        obj.runtime_sample = None
        obj.evidence = obj.observed = None
        obj.check()
        return obj

    async def bind_sources(self, receipt, evidence, *, guard):
        from .yaml_source_binding import async_bind_original_runtime, _runtime_sample
        from .native_discovery_inputs import async_observe_discovery_inputs
        from .discovery import _runtime_family
        # A loaded component must have an actual settled complete projection;
        # _runtime_sample's absent-component path cannot prove loaded emptiness.
        for domain in ('automation', 'script'):
            if domain in self.loaded or evidence.ledger.keys_for_native_projection(domain):
                family, token = _runtime_family(self.hass, domain)
                _need(family.status == 'COMPLETE' and token is not None,
                      domain + '_runtime_unqualified')
        bound = await async_bind_original_runtime(self.hass, receipt, evidence, guard=guard)
        guard()
        _need(bound.verified, 'source_runtime_unqualified')
        observed = await async_observe_discovery_inputs(self.hass, guard=guard)
        guard()
        sample = _runtime_sample(self.hass, evidence, observed)
        _need(not sample[1] and sample[0] == bound.rows and sample[2] == bound.tokens,
              'source_runtime_changed')
        self.evidence, self.observed, self.runtime_sample = evidence, observed, sample
        self.check()

    def check(self):
        # The composer's guard is also called by physical IO workers. Native
        # managers, EntityComponent and registrations are checked only on-loop.
        if threading.get_ident() != self.thread:
            return
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        _need(frozenset(self.hass.config.components) & {'automation', 'script', 'lovelace', 'energy'}
              == self.loaded and self.presence == (id(self.hass.data.get(LOVELACE_DATA)),
                  id(self.hass.data.get('energy_manager'))), 'consumer_inventory_changed')
        self.native.check()
        if self.runtime_sample is not None:
            from .yaml_source_binding import _runtime_sample
            _need(_runtime_sample(self.hass, self.evidence, self.observed) == self.runtime_sample,
                  'source_runtime_changed')

    def physical(self):
        self.native.physical()


class NativeTemplateForward:
    """Task-local one-shot writer built only by installed native qualification."""

    def __init__(self):
        _need(False, 'installed_prepare_required')

    # Share the installed exact accepted-head, finite-history and adapter
    # implementations. These functions consume this object's own graph; no
    # reverse binding, readonly promotion or second ORIGINAL is involved.
    async def _observe(self):
        observed = await NativeTemplateReverse._observe(self)
        await _drained(self.hass.async_add_executor_job, self._unchanged_consumers.physical)
        self._scope()
        return observed

    _history_current = NativeTemplateReverse._history_current
    _final_loaded_history = NativeTemplateReverse._final_loaded_history
    _coordinator = NativeTemplateReverse._coordinator

    @classmethod
    async def prepare(cls, hass, mapping, *, guard, refresh):
        from . import Runtime
        from homeassistant.core import HomeAssistant
        from .manifest import parse_manifest
        _need(cls is NativeTemplateForward and type(hass) is HomeAssistant
              and type(mapping) is dict and len(mapping) == 1
              and all(type(k) is str and type(v) is str for k, v in mapping.items())
              and callable(guard) and callable(refresh), 'installed_scope_required')
        old, new = next(iter(mapping.items()))
        # Reuse the existing ID/domain/collision syntax policy, not a second
        # loose mapping parser. No private/public manifest import is invoked.
        manifest = parse_manifest({'entities': [{'entity_id': old, 'proposed_entity_id': new}]})
        _need(len(manifest.entities) == 1, 'rename_required')
        runtime = hass.data.get(DATA_RUNTIME)
        _need(type(runtime) is Runtime, 'runtime_required')
        obj = object.__new__(cls)
        obj.hass, obj.runtime, obj.guard, obj.refresh = hass, runtime, guard, refresh
        obj.task = asyncio.current_task()
        obj.lock = runtime.operation_lock
        obj.lease = None
        obj.bound = obj.used = obj.failed = False
        obj.mapping = dict(mapping)

        def runtime_scope():
            _authorize(guard)
            # The four qualified families remain unchanged. No external or
            # other native consumer adapter is supplied by this private slice.
            _need(not ({'scene', 'powercalc', 'mqtt', 'rest', 'command_line'}
                       & set(hass.config.components)),
                  'other_consumer_scope_unsupported')
            _need(hass.data.get(DATA_RUNTIME) is runtime and runtime.loaded
                  and not runtime.unloading and runtime.operation_lock is obj.lock
                  and obj.lock.locked(), 'runtime_scope_lost')
            if hasattr(obj, '_unchanged_consumers'):
                obj._unchanged_consumers.check()

        def scope():
            runtime_scope()
            # Guards also execute inside drained read workers/native children;
            # API entry points separately require the exact preparing task.
            if obj.lease is not None:
                obj.lease.check()
            if obj.bound:
                _need(runtime.journal is obj.journal and runtime.preparations is obj.preparations,
                      'runtime_cache_replaced')
                baseline, composition = obj.preparations.resolve_composition(obj.plan.native_inputs)
                _need(composition == obj.composition and baseline.receipt == obj.receipt,
                      'composition_changed')
        obj._scope, obj._runtime_scope = scope, runtime_scope
        try:
            scope()
            await refresh()
            scope()
            obj._unchanged_consumers = await _UnchangedConsumers.capture(hass, obj.mapping, guard=scope)
            scope()
            obj.preparations, obj.journal = native_prepared_store(hass), native_journal(hass)
            await obj.preparations.initialize()
            scope()
            await obj.journal.initialize()
            scope()
            _need(not await obj.journal.active(), 'other_operation_unresolved')
            scope()
            from .native_discovery_inputs import async_observe_discovery_inputs, assert_discovery_managers_current
            from .native_configentry_binding import bind_source_consumer
            observed = await async_observe_discovery_inputs(hass, guard=scope)
            scope()
            plans = inventory(hass, obj.mapping)
            bind_source_consumer(observed, plans)
            _need(not any(p.blocked for p in plans), 'helper_scope_unsupported')
            affected = [p for p in plans if p.changes]
            _need(len(affected) == 1 and affected[0].before['domain'] == 'template'
                  and [c.path for c in affected[0].changes] == [('options', 'state')],
                  'one_numeric_entry_required')
            from .helper_transaction import prepare_helper
            from .yaml_validation_worker import template_entry_semantic
            obj.change = prepare_helper(affected[0].before, obj.mapping)
            template_entry_semantic(obj.change.before)
            template_entry_semantic(obj.change.after)
            obj.entry_id = obj.change.before['identity']['entry_id']
            obj.entry = hass.config_entries.async_get_entry(obj.entry_id)
            docs = {r.key: json.loads(r.manager_json) for r in observed.files}
            sources = [r for r in docs['core.entity_registry']['entities'] if r['entity_id'] == old]
            _need(len(sources) == 1 and not any(r['entity_id'] == new
                  for r in docs['core.entity_registry']['entities']), 'source_uuid_required')
            source_uuid = sources[0]['id']
            # The narrow component declines any broader Template population;
            # unsupported live variants cannot hide their dependencies.
            template_rows = [r for r in docs['core.entity_registry']['entities'] if r['platform'] == 'template']
            _need(len(template_rows) == 1 and template_rows[0]['config_entry_id'] == obj.entry_id,
                  'other_consumer_unsupported')
            _need([p.before['identity']['entry_id'] for p in plans if p.before['domain'] == 'template']
                  == [obj.entry_id], 'other_template_entry_unsupported')
            from .native_template_consumers import _Native, _live_sensor, _registration, _source_semantics
            from homeassistant.helpers.entity_platform import async_get_platforms
            native = _Native(hass)
            await _drained(hass.async_add_executor_job, native.sources)
            scope()
            native.bindings()
            entities = [e for p in async_get_platforms(hass, 'template')
                        for e in p.entities.values()]
            _need(len(entities) == 1, 'other_consumer_unsupported')
            semantic, template, _ = _live_sensor(native, entities[0])
            dependencies, _ = _registration(native, entities[0], template)
            _need(dependencies == (old,) and semantic == template_entry_semantic(obj.change.before)
                  and _source_semantics(native, entities[0], template_rows[0], semantic, None, docs),
                  'native_consumer_unqualified')
            assert_discovery_managers_current(hass, observed, guard=scope)
            obj.lease = await NativeHelperActivationLease.acquire(hass, hass.config_entries,
                                                                  (obj.entry,), guard=runtime_scope)
            scope()
            from .yaml_plan import plan_yaml
            from .yaml_validation import async_validate_proposed_with_sources
            root = Path(hass.config.config_dir).resolve()
            yaml = await _drained(hass.async_add_executor_job, lambda: plan_yaml(root, obj.mapping))
            scope()
            _need(yaml.ready and not yaml.patches, 'yaml_dependency_unsupported')
            from .recorder_guard import NativeRecorderGuard
            from .history_guard import NativeHistoryGuard
            from .native_registry import NativeRegistryBridge, NativeRegistryIO
            obj.recorder = NativeRecorderGuard(hass)
            obj.history = NativeHistoryGuard(obj.recorder)
            consumer_history = await obj.history.capture({template_rows[0]['entity_id']: template_rows[0]['entity_id']})
            scope()
            obj.registry_change = await NativeRegistryBridge(NativeRegistryIO(hass), obj.recorder).prepare(
                'entity', source_uuid, {'entity_id': new}, scope)
            scope()
            own = uuid.uuid4().hex
            obj.operation_id = 'template-forward-' + own
            obj.digests = {}
            requests = []
            for suffix, change in (('source', obj.registry_change), ('options', obj.change)):
                ref = own + '-' + suffix
                payload = encode_change(change)
                obj.digests[ref] = await obj.preparations.create(ref, payload)
                scope()
                if suffix == 'options':
                    requests.append((ref, obj.digests[ref], payload))
            obj.receipt, obj.evidence = await async_validate_proposed_with_sources(
                hass, yaml, root.parent, native_helpers=tuple(requests))
            scope()
            _need(obj.receipt.ready_for_apply, 'validation_required')
            # Unchanged automation/script sources require the SAME ORIGINAL
            # ledger/checker projection and genuine settled native registrations.
            # YAML patches and Template YAML remain outside this private writer.
            _need(not obj.evidence.template_sources.ledger.definitions,
                  'other_source_scope_unsupported')
            await obj._unchanged_consumers.bind_sources(obj.receipt, obj.evidence, guard=scope)
            scope()
            baseline = await async_capture_native_input_baseline(hass, obj.receipt, guard=scope)
            scope()
            source_history = await obj.history.capture(obj.mapping)
            scope()
            obj.composition = TemplateComposition(obj.evidence, tuple(sorted(obj.digests.items())),
                source_history, consumer_history, obj.evidence.sha256)
            inputs = await obj.preparations.create_native_inputs(own + '-original', baseline,
                                                                 composition=obj.composition)
            scope()
            scopes = ('registry', 'helpers')
            steps = tuple(Step(own + '-' + suffix, adapter, change.identity,
                change.before_revision, change.after_revision, own + '-' + suffix)
                for suffix, adapter, change in (('source', 'registry', obj.registry_change),
                                                ('options', 'helpers', obj.change)))
            obj.plan = Plan(own, scopes, scopes, steps, obj.evidence.sha256, scopes, inputs)
            obj.plan.validate()
            obj.fingerprint = obj.plan.fingerprint
            obj.receipt_store = native_registry_receipt_store(hass, obj.preparations)
            await obj.receipt_store.initialize()
            scope()
            obj.lifecycle_store = native_helper_lifecycle_store(hass, obj.preparations)
            await obj.lifecycle_store.initialize()
            scope()
            obj.chain = await RegistryReceiptChain.start(obj.receipt_store, obj.operation_id,
                obj.plan, obj.receipt, obj.digests)
            scope()
            obj.observation_state = obj.chain.reconstruct_inputs()
            obj.observer = NativeTemplateEntryObserver(hass, obj.chain, obj.evidence, guard=scope)
            obj.observer.lease = obj.lease
            runtime.journal, runtime.preparations = obj.journal, obj.preparations
            obj.bound = True
            await refresh()
            scope()
            await obj._original_fence(inputs, obj.receipt)
            await obj._final_loaded_history(obj.change.before)
            obj._scope()
            return obj
        except BaseException:
            obj.failed = True
            obj.close()
            raise

    async def _check_stores(self):
        # Preparation is a live session, not permission to trust cached rows
        # after a prepare/apply gap. Fresh readers remain readers.
        preps = native_prepared_store(self.hass)
        await preps.initialize()
        self._scope()
        _need(preps.plans == self.preparations.plans, 'preparations_changed')
        receipts = native_registry_receipt_store(self.hass, preps)
        await receipts.initialize()
        self._scope()
        _need(receipts.rows == self.receipt_store.rows, 'receipts_changed')
        lifecycle = native_helper_lifecycle_store(self.hass, preps)
        await lifecycle.initialize()
        self._scope()
        _need(lifecycle.rows == self.lifecycle_store.rows, 'lifecycle_changed')
        journal = native_journal(self.hass)
        await journal.initialize()
        self._scope()
        _need(not await journal.active() and await journal.load(self.operation_id) is None,
              'operation_changed')
        self._scope()

    async def _original_fence(self, reference, receipt):
        self._scope()
        _need(reference == self.plan.native_inputs and receipt == self.receipt, 'original_binding')
        await self._history_current()
        self._scope()
        from .yaml_validation import assert_validation_current
        await _drained(self.hass.async_add_executor_job, lambda: assert_validation_current(receipt))
        self._scope()
        observed = await async_observe_native_inputs(self.hass, receipt, guard=self._scope)
        self._scope()
        await _drained(self.hass.async_add_executor_job, self._unchanged_consumers.physical)
        self._scope()
        baseline = self.chain.baseline
        _need(observed.original_receipt_json == baseline.receipt_json and observed.files == baseline.files,
              'original_changed')
        if self.entry.state.value == 'loaded':
            self.observer.live_sample(self.change.before)
        return True

    async def _accepted(self, authorization):
        self._scope()
        record = await self.journal.load(self.operation_id)
        self._scope()
        _need(record is not None, 'journal_missing')
        observed = await self._observe()
        self.chain.expected(PhaseContext(record, 0), tuple(RegistryRawObservation(
            f.key, f.raw, f.identity) for f in observed.files))
        return self.chain.verify_setup_authorization(authorization)

    async def _acquire(self, snapshot):
        self._scope()
        _need(asyncio.current_task() is self.task and read_entry(self.hass, self.entry_id) == snapshot,
              'activation_scope')
        self.lease.release()
        # acquire's guard must not check the released lease.
        self.lease = None
        self.lease = await NativeHelperActivationLease.acquire(self.hass, self.hass.config_entries,
                                                              (self.entry,), guard=self._runtime_scope)
        self.observer.lease = self.lease
        self.lifecycle.activation_lease = self.lease
        self._scope()
        _need(read_entry(self.hass, self.entry_id) == snapshot, 'activation_scope')

    async def apply(self):
        self._scope()
        _need(asyncio.current_task() is self.task and self.bound and not self.used and not self.failed,
              'no_replay')
        self.used = True
        try:
            await self.refresh()
            self._scope()
            _need(not await self.journal.active(), 'other_operation_unresolved')
            self._scope()
            await self._check_stores()
            await self._original_fence(self.plan.native_inputs, self.receipt)
            await self._final_loaded_history(self.change.before)
            self.observer.capture_loaded_witness(self.change.before)
            from .native_helper_setup_effects import NativeHelperSetupEffects

            async def loaded(entry_id, activation, authorization):
                _need(entry_id == self.entry_id and authorization.status == 'applied', 'activation_scope')
                await self.observer.verify_loaded(self.change.after)
                return True

            setup = NativeHelperSetupEffects(self.hass, self.chain, self._observe,
                                             self.journal.load, guard=self._scope)
            # Persist the existing operation record BEFORE any native helper
            # unload. A crash/cancel in lifecycle quiescence must remain visible
            # to the ordinary journal inventory, even before registry effects.
            self.plan.validate()
            await self.journal.create(Record(self.operation_id, self.plan,
                phases=('pending',) * len(self.plan.steps)))
            self._scope()
            await self.refresh()
            self._scope()
            self.lifecycle = await NativeHelperLifecycle.start(self.lifecycle_store,
                self.hass.config_entries, self.operation_id, self.plan, self.receipt, self.digests,
                activation_lease=self.lease, guard=self._scope,
                verify_original_native_inputs=self._original_fence, settle=self.observer.settle,
                verify_inactive=self.observer.verify_inactive, verify_runtime=loaded,
                verify_accepted_native_chain=self._accepted, setup_effect=setup)
            await self.lifecycle.quiesce()
            # The record already exists. Use the existing read-only effect
            # reconciliation and explicit ready-only resume; apply() correctly
            # refuses to replay an existing operation. Every revision/capability
            # is checked again by Coordinator before its first actual write.
            coordinator = self._coordinator()
            ready = await coordinator.reconcile(self.operation_id)
            self._scope()
            _need(ready.status == 'ready' and ready.direction == 'forward'
                  and ready.phases == ('pending',) * len(self.plan.steps),
                  'initial_effects_not_pristine')
            result = await coordinator.resume(self.operation_id)
            self._scope()
            _need(result.status == 'applied', 'apply_incomplete')
            await self._acquire(self.change.after)
            authorization = RestoreAuthorization(self.operation_id, self.fingerprint,
                                                  self.chain.reconstruct_inputs().head, 'applied')
            await self.lifecycle.restore(authorization)
            await self.refresh()
            self._scope()
            await self._final_loaded_history(self.change.after)
            status = self.lifecycle.inspect()
            _need(status['activation_receipts_complete'] and not status['pending'], 'activation_incomplete')
            return result
        except BaseException:
            self.failed = True
            raise

    def close(self):
        _need(asyncio.current_task() is self.task, 'owner_task_required')
        self.bound = False
        if hasattr(self, 'lifecycle'):
            self.lifecycle.close_source_gaps()
        if self.lease is not None:
            self.lease.release()
            self.lease = None
