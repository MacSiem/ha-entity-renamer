"""Private server composer for one numeric Template ConfigEntry source rename.

This component is not a manifest importer or an all-consumer readiness gate.
The caller holds the Runtime lock throughout prepare/apply/close. No request
supplies a root, receipt, operation, preparation or native callback.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import uuid

from .const import DATA_RUNTIME
from .transaction import Refused, Plan, Step, PhaseContext
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


class NativeTemplateForward:
    """Task-local one-shot writer built only by installed native qualification."""

    def __init__(self):
        _need(False, 'installed_prepare_required')

    # Share the installed exact accepted-head, finite-history and adapter
    # implementations. These functions consume this object's own graph; no
    # reverse binding, readonly promotion or second ORIGINAL is involved.
    _observe = NativeTemplateReverse._observe
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
            # This private slice supplies no dashboard/Energy/YAML runtime or
            # external consumer adapters. Refuse their active requirements even
            # if a read-only discovery view could enumerate them.
            _need(not ({'automation', 'script', 'scene', 'lovelace', 'energy', 'powercalc',
                        'mqtt', 'rest', 'command_line'} & set(hass.config.components)),
                  'other_consumer_scope_unsupported')
            _need(hass.data.get(DATA_RUNTIME) is runtime and runtime.loaded
                  and not runtime.unloading and runtime.operation_lock is obj.lock
                  and obj.lock.locked(), 'runtime_scope_lost')

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
            # No automation/script/YAML/external writer is composed by this
            # component. Broader operations must use the future controller.
            _need(not obj.evidence.ledger.definitions
                  and not obj.evidence.template_sources.ledger.definitions,
                  'other_source_scope_unsupported')
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
            self.lifecycle = await NativeHelperLifecycle.start(self.lifecycle_store,
                self.hass.config_entries, self.operation_id, self.plan, self.receipt, self.digests,
                activation_lease=self.lease, guard=self._scope,
                verify_original_native_inputs=self._original_fence, settle=self.observer.settle,
                verify_inactive=self.observer.verify_inactive, verify_runtime=loaded,
                verify_accepted_native_chain=self._accepted, setup_effect=setup)
            await self.lifecycle.quiesce()
            result = await self._coordinator().apply(self.plan, self.fingerprint, self.operation_id)
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
