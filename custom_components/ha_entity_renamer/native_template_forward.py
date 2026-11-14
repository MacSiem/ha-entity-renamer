"""Private server composer for one numeric Template ConfigEntry source rename.

This component is not a manifest importer or an all-consumer readiness gate.
The caller holds the Runtime lock throughout prepare/apply/close. No request
supplies a root, receipt, operation, preparation or native callback.
"""
from __future__ import annotations

import asyncio
import json
import logging
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


def _require_prepared_energy(config, plan, prepared, reverse):
    from .discovery import _plain
    from .energy_native import PreparedEnergy
    from .native_registry_receipts import _exact_json_equal, _freeze_json
    _need(type(prepared) is PreparedEnergy, 'energy_preparation_mismatch')
    before = prepared.after if reverse else prepared.before.value
    after = prepared.before.value if reverse else prepared.after
    _need(_exact_json_equal(_plain(config), _freeze_json(before))
          and _exact_json_equal(plan.value, _freeze_json(after)),
          'energy_preparation_mismatch')


class _UnchangedConsumers:
    """Task-local read comparisons, never an adapter or effect capability."""

    @classmethod
    async def capture(cls, hass, mapping, *, guard, _powercalc_energy=None, _dashboard_effects=False,
                      _powercalc_reverse=False):
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
        _need(type(_dashboard_effects) is bool and type(_powercalc_reverse) is bool, 'dashboard_scope_invalid')
        obj.dashboard_observed = observed if _dashboard_effects else None
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
                _need(_dashboard_effects or not result.value.yaml_sources, 'dashboard_yaml_route_unqualified')
                configs = [row.config for row in result.value.snapshots if row.config is not None]
                planner = plan_dashboard
            else:
                configs = [] if result.value.value is None else [result.value.value]
                planner = plan_energy
            for config in configs:
                selected_mapping = ({new: old for old, new in mapping.items()}
                                    if name == 'energy' and _powercalc_reverse else mapping)
                plan = planner(_plain(config), selected_mapping)
                _need(not plan.markers, name + '_dependency_unsupported')
                if plan.changes and name == 'energy' and _powercalc_energy is not None:
                    _require_prepared_energy(config, plan, _powercalc_energy, _powercalc_reverse)
                    names.remove(name)  # This exact Energy step has its own receipt/physical CAS.
                elif name != 'dashboard' or not _dashboard_effects:
                    _need(not plan.changes, name + '_dependency_unsupported')
        obj.native = _ConsumerFence(hass, observed, _plain, names=tuple(names))
        obj.runtime_sample = obj.runtime_sampler = None
        obj.evidence = obj.observed = None
        obj.check()
        return obj

    async def bind_sources(self, receipt, evidence, *, guard, owned_automation=None):
        from .yaml_source_binding import async_bind_original_runtime, _RuntimeSourceSampler
        from .native_discovery_inputs import async_observe_discovery_inputs
        from .discovery import _runtime_family
        # A loaded component must have an actual settled complete projection;
        # _runtime_sample's absent-component path cannot prove loaded emptiness.
        for domain in ('automation', 'script'):
            if domain in self.loaded or evidence.ledger.keys_for_native_projection(domain):
                family, token = _runtime_family(self.hass, domain, owned_automation=owned_automation,
                                                include_row_diagnostics=False)
                _need(family.status == 'COMPLETE' and token is not None,
                      domain + '_runtime_unqualified')
        bound = await async_bind_original_runtime(self.hass, receipt, evidence, guard=guard, owned_automation=owned_automation)
        guard()
        if not bound.verified:
            # Internal fixed codes only: never source bodies, IDs or paths.
            safe_codes = frozenset({
                'runtime_source_native_projection_unavailable', 'runtime_source_projection_mismatch',
                'runtime_source_registry_identity_unverified', 'runtime_source_inventory_mismatch',
                'runtime_source_disabled_entry_unverified', 'runtime_source_inventory_unverified',
                'runtime_source_inputs_changed', 'runtime_source_observation_changed',
                'runtime_source_runtime_unavailable'})
            logging.getLogger(__name__).warning('Native source binding refused: %s',
                tuple(code for code in bound.blockers if code in safe_codes))
        _need(bound.verified, 'source_runtime_unqualified')
        observed = await async_observe_discovery_inputs(self.hass, guard=guard)
        guard()
        sampler = _RuntimeSourceSampler.bind(evidence, observed)
        sample = sampler.sample(self.hass, evidence, observed, owned_automation=owned_automation)
        _need(not sample[1] and sample[0] == bound.rows and sample[2] == bound.tokens,
              'source_runtime_changed')
        self.evidence, self.observed, self.runtime_sample, self.runtime_sampler = (
            evidence, observed, sample, sampler)
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
            from .yaml_source_binding import _RuntimeSourceSampler
            fence = getattr(self, 'own_fence', None)
            if fence is None:
                actual = self.runtime_sampler.sample(self.hass, self.evidence, self.observed,
                    owned_automation=getattr(self, 'owned_automation', None))
            else:
                from .yaml_source_binding import _runtime_rows_sample
                actual = fence.normalize(_runtime_rows_sample(self.hass, self.evidence, fence.rows(),
                    owned_automation=getattr(self, 'owned_automation', None)))
            _need(type(self.runtime_sampler) is _RuntimeSourceSampler and actual == self.runtime_sample,
                  'source_runtime_changed')

    def physical(self):
        self.native.physical()


class NativeTemplateReviewPin:
    """Retain native identities across the review gap; equality cannot prove ABA."""
    def __init__(self, session):
        self.hass, self.entry = session.hass, session.entry
        self.options = self.entry.options
        self.setup_lock = self.entry.setup_lock
        entity, _, _, _ = session.observer.live_sample(session.change.before)
        self.entity = entity
        self.tracker = entity._template_result_info
        self.output_job = self.tracker._job
        self.filtered = self.tracker._track_state_changes
        from homeassistant.helpers import event
        self.remove = self.filtered._listeners[event._ENTITIES_LISTENER]
        self.job, self.callbacks = self.remove.args[-2:]
        self.bus_listener = self.hass.data[event._KEYED_TRACK_STATE_CHANGE.key].listener
        self.action_job = self.filtered._action_as_hassjob
        self.native = session.observer.native
        self.snapshot = session.change.before
        self.snapshot_mapping = dict(session.mapping)
        self.thread = threading.get_ident()
        self.check()

    def check_entry(self):
        if threading.get_ident() != self.thread:
            return
        _need(self.hass.config_entries.async_get_entry(self.entry.entry_id) is self.entry
              and self.entry.options is self.options and self.entry.setup_lock is self.setup_lock,
              'review_entry_changed')

    def check(self):
        if threading.get_ident() != self.thread:
            return
        self.check_entry()
        from homeassistant.helpers.entity_platform import async_get_platforms
        from .helper_native import read_entry
        from .native_template_consumers import _live_sensor, _registration
        entries = [e for p in async_get_platforms(self.hass, 'template')
                   if p.config_entry is self.entry for e in p.entities.values()]
        _need(self.hass.config_entries.async_get_entry(self.entry.entry_id) is self.entry
              and self.entry.options is self.options and len(entries) == 1
              and entries[0] is self.entity
              and self.entity._template_result_info is self.tracker
              and self.tracker._job is self.output_job
              and self.tracker._track_state_changes is self.filtered
              and read_entry(self.hass, self.entry.entry_id) == self.snapshot, 'review_entry_changed')
        _, template, _ = _live_sensor(self.native, self.entity)
        dependencies, _ = _registration(self.native, self.entity, template)
        from homeassistant.helpers import event
        remove = self.filtered._listeners[event._ENTITIES_LISTENER]
        _need(dependencies == (next(iter(self.snapshot_mapping)),)
              and remove is self.remove and remove.args[-2] is self.job
              and remove.args[-1] is self.callbacks
              and self.filtered._action_as_hassjob is self.action_job
              and self.hass.data[event._KEYED_TRACK_STATE_CHANGE.key].listener is self.bus_listener,
              'review_dependency_changed')


class NativeTemplateForward:
    """Task-local one-shot writer built only by installed native qualification."""

    def __init__(self):
        _need(False, 'installed_prepare_required')

    # Share the installed exact accepted-head, finite-history and adapter
    # implementations. These functions consume this object's own graph; no
    # reverse binding, readonly promotion or second ORIGINAL is involved.
    async def _observe(self):
        return await NativeTemplateReverse._observe(self,
            physical_check=self._unchanged_consumers.physical)

    _history_current = NativeTemplateReverse._history_current
    _final_loaded_history = NativeTemplateReverse._final_loaded_history
    _coordinator = NativeTemplateReverse._coordinator

    @classmethod
    async def prepare(cls, hass, mapping, *, guard, refresh, review_id=None):
        from . import Runtime
        from homeassistant.core import HomeAssistant
        from .manifest import parse_manifest
        _need(cls is NativeTemplateForward and type(hass) is HomeAssistant
              and type(mapping) is dict and len(mapping) == 1
              and all(type(k) is str and type(v) is str for k, v in mapping.items())
              and callable(guard) and callable(refresh), 'installed_scope_required')
        if review_id is not None:
            from .review_retention import review_lease
            review_lease(review_id, 'server')
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
            _need('powercalc' not in hass.config.components, 'powercalc_scope_unsupported')
            obj.scene_fence.check()
            _need(not ({'mqtt', 'rest', 'command_line'}
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
        from .native_empty_scene import NativeEmptySceneFence
        obj.scene_fence = NativeEmptySceneFence(hass)
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
            own = review_id or uuid.uuid4().hex
            obj.operation_id = ('template-yaml-' if review_id else 'template-forward-') + own
            obj.digests = {}
            requests = []
            for index, (suffix, change) in enumerate((('source', obj.registry_change), ('options', obj.change))):
                ref = own + '-' + (str(index) if review_id else suffix)
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
            steps = tuple(Step(own + '-' + (str(index) if review_id else suffix), adapter, change.identity,
                change.before_revision, change.after_revision, own + '-' + (str(index) if review_id else suffix))
                for index, (suffix, adapter, change) in enumerate((('source', 'registry', obj.registry_change),
                                                ('options', 'helpers', obj.change))))
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
            if review_id is not None:
                obj.review_admission = NativeTemplateReviewPin(obj)
            return obj
        except BaseException:
            obj.failed = True
            obj.close()
            raise

    @classmethod
    async def bind_prepared(cls, hass, operation_id, plan, *, guard, refresh):
        """Reopen the exact retained review, never minting a replacement ORIGINAL."""
        from . import Runtime
        from .prepared import decode_change
        from .native_template_entry_lifecycle import validate_preparations
        from .recorder_guard import NativeRecorderGuard
        from .history_guard import NativeHistoryGuard
        runtime = hass.data.get(DATA_RUNTIME)
        _need(cls is NativeTemplateForward and type(runtime) is Runtime
              and runtime.operation_lock.locked(), 'runtime_required')
        obj = object.__new__(cls)
        obj.hass, obj.runtime, obj.guard, obj.refresh = hass, runtime, guard, refresh
        obj.task, obj.lock = asyncio.current_task(), runtime.operation_lock
        obj.lease = None
        obj.bound = obj.used = obj.failed = False
        def runtime_scope():
            _authorize(guard)
            _need('powercalc' not in hass.config.components, 'powercalc_scope_unsupported')
            obj.scene_fence.check()
            _need(not ({'mqtt', 'rest', 'command_line'}
                       & set(hass.config.components)), 'other_consumer_scope_unsupported')
            _need(hass.data.get(DATA_RUNTIME) is runtime and runtime.loaded
                  and not runtime.unloading and runtime.operation_lock is obj.lock
                  and obj.lock.locked(), 'runtime_scope_lost')
            if hasattr(obj, '_unchanged_consumers'):
                obj._unchanged_consumers.check()
        def scope():
            runtime_scope()
            if obj.lease is not None:
                obj.lease.check()
            if obj.bound:
                _need(runtime.journal is obj.journal and runtime.preparations is obj.preparations,
                      'runtime_cache_replaced')
                baseline, composition = obj.preparations.resolve_composition(obj.plan.native_inputs)
                _need(composition == obj.composition and baseline.receipt == obj.receipt, 'composition_changed')
        obj._scope, obj._runtime_scope = scope, runtime_scope
        from .native_empty_scene import NativeEmptySceneFence
        obj.scene_fence = NativeEmptySceneFence(hass)
        try:
            scope()
            await refresh()
            obj.preparations, obj.journal = native_prepared_store(hass), native_journal(hass)
            await obj.preparations.initialize()
            await obj.journal.initialize()
            scope()
            _need(not await obj.journal.active() and await obj.journal.load(operation_id) is None,
                  'operation_changed')
            plan.validate()
            obj.plan, obj.operation_id, obj.fingerprint = plan, operation_id, plan.fingerprint
            baseline, obj.composition = obj.preparations.resolve_composition(plan.native_inputs)
            obj.receipt, obj.evidence = baseline.receipt, obj.composition.evidence
            obj.digests = dict(obj.composition.prepared_digests)
            _need(type(obj.composition) is TemplateComposition
                  and plan.required_scopes == plan.covered_scopes == plan.verification_adapters == ('registry', 'helpers')
                  and tuple(step.adapter for step in plan.steps) == ('registry', 'helpers')
                  and not any(step.caused_by for step in plan.steps)
                  and set(obj.digests) == {step.prepared_ref for step in plan.steps}
                  and plan.coverage_evidence == obj.composition.coverage_sha256, 'qualified_plan_required')
            changes = validate_preparations(obj.preparations, plan, obj.receipt, obj.digests)
            _need(len(changes) == 1, 'one_numeric_entry_required')
            obj.entry_id, obj.change = next(iter(changes.items()))
            obj.mapping = dict(obj.change.mapping)
            obj.registry_change = decode_change(obj.preparations.resolve(
                plan.steps[0].prepared_ref, obj.digests[plan.steps[0].prepared_ref]))
            obj.entry = hass.config_entries.async_get_entry(obj.entry_id)
            _need(obj.entry is not None, 'entry_missing')
            obj._unchanged_consumers = await _UnchangedConsumers.capture(hass, obj.mapping, guard=scope)
            await obj._unchanged_consumers.bind_sources(obj.receipt, obj.evidence, guard=scope)
            obj.lease = await NativeHelperActivationLease.acquire(hass, hass.config_entries,
                                                                  (obj.entry,), guard=runtime_scope)
            scope()
            obj.recorder = NativeRecorderGuard(hass)
            obj.history = NativeHistoryGuard(obj.recorder)
            obj.receipt_store = native_registry_receipt_store(hass, obj.preparations)
            obj.lifecycle_store = native_helper_lifecycle_store(hass, obj.preparations)
            await obj.receipt_store.initialize()
            await obj.lifecycle_store.initialize()
            readonly = RegistryReceiptChain.reopen(obj.receipt_store, operation_id, plan,
                                                   obj.receipt, obj.digests)
            state = readonly.reconstruct_inputs()
            _need(not readonly.writable and readonly.active is None
                  and state.head == readonly.root_digest, 'pristine_review_required')
            obj.chain = readonly
            obj.observer = NativeTemplateEntryObserver(hass, readonly, obj.evidence, guard=scope)
            obj.observer.lease = obj.lease
            runtime.journal, runtime.preparations = obj.journal, obj.preparations
            obj.bound = True
            await obj._check_stores()
            await obj._original_fence(plan.native_inputs, obj.receipt)
            await obj._final_loaded_history(obj.change.before)
            writer = RegistryReceiptChain(obj.receipt_store, operation_id, plan, obj.receipt, obj.digests)
            _need(writer.root == readonly.root and writer.root_digest == readonly.root_digest
                  and writer.reconstruct_inputs().same_evidence(state), 'head_changed')
            writer.writable = True
            obj.chain = writer
            obj.observer.chain = writer
            obj.observation_state = state
            return obj
        except BaseException:
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
        observed = await async_observe_native_inputs(self.hass, receipt, guard=self._scope,
            physical_check=self._unchanged_consumers.physical)
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
            admission = getattr(self, 'review_record_admission', None)
            if admission is not None:
                admission.check()
                self.lease._review_admission = admission
            await self.journal.create(Record(self.operation_id, self.plan,
                phases=('pending',) * len(self.plan.steps),
                restore_attempted=False if admission is not None else None))
            self._scope()
            if admission is not None:
                admission.check()
            await self.refresh()
            self._scope()
            if admission is not None:
                admission.check()
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
        try:
            if hasattr(self, 'lifecycle'):
                self.lifecycle.close_source_gaps()
            if self.lease is not None:
                self.lease.release()
                self.lease = None
        finally:
            self.scene_fence.close()
