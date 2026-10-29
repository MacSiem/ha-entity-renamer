"""Private server-owned registry -> modern Template YAML transaction.

One aggregate YAML graph, one immutable ORIGINAL and one existing journal. This
is a narrow installed component, not a family readiness or public admission API.
The Runtime owns its lock and cancellation-drained task through close().
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import uuid

from .const import DATA_RUNTIME
from .transaction import Refused, Plan, Step, PhaseContext, Position
from .native_template_reverse import _authorize
from .native_template_forward import _UnchangedConsumers
from .native_input_baseline import _drained, _Managers, NativeInputBaseline, NativeInputFile
from .native_input_observation import async_observe_native_inputs
from .native_registry_receipts import (RegistryReceiptChain, RegistryReceiptError,
    RegistryRawObservation, native_registry_receipt_store)
from .native_template_yaml import (NativeTemplateYAMLRuntimeReadback,
    compose_template_yaml_coordinator)
from .native_template_composition import TemplateComposition, MixedTemplateComposition, encode_composition
from .prepared import native_prepared_store, encode_change, decode_change, canonical
from .journal import native_journal


def _need(value, reason):
    if not value:
        raise Refused('template_yaml_server_' + reason) from None


def _baseline_stamp(value):
    """Full immutable ORIGINAL identity without repeated JSON/base64 decoding.

    Chain construction already authenticated the native codec and receipt. Hash
    every retained raw/manager byte and exact typed identity on each check; this
    never observes or substitutes current native managers/files.
    """
    _need(type(value) is NativeInputBaseline and type(value.format) is int
          and type(value.receipt_json) is bytes and type(value.files) is tuple
          and len(value.files) == 3, 'original_binding_changed')
    rows = []
    for item in value.files:
        _need(type(item) is NativeInputFile and type(item.key) is str
              and (item.raw is None or type(item.raw) is bytes)
              and type(item.manager_json) is bytes
              and (item.identity is None or (type(item.identity) is tuple
                   and len(item.identity) == 3 and all(type(v) is int for v in item.identity))),
              'original_binding_changed')
        rows.append((item.key, item.identity,
            None if item.raw is None else hashlib.sha256(item.raw).digest(),
            hashlib.sha256(item.manager_json).digest()))
    return value.format, hashlib.sha256(value.receipt_json).digest(), tuple(rows)


class NativeTemplateYAMLServer:
    """Task-local session; all effect capabilities are constructed on the server."""

    def __init__(self):
        _need(False, 'installed_factory_required')

    @classmethod
    async def _open(cls, hass, *, guard, refresh):
        from . import Runtime
        from homeassistant.core import HomeAssistant
        from .native_template_state_server import NativeTemplateStateServer
        _need(cls in (NativeTemplateYAMLServer, NativeTemplateStateServer) and type(hass) is HomeAssistant
              and callable(guard) and callable(refresh), 'installed_scope_required')
        runtime = hass.data.get(DATA_RUNTIME)
        _need(type(runtime) is Runtime, 'runtime_required')
        obj = object.__new__(cls)
        obj.hass, obj.runtime, obj.guard, obj.refresh = hass, runtime, guard, refresh
        obj.task, obj.lock = asyncio.current_task(), runtime.operation_lock
        obj.bound = obj.used = obj.closed = False

        def scope():
            _authorize(guard)
            _need(not obj.closed and hass.data.get(DATA_RUNTIME) is runtime
                  and runtime.loaded and not runtime.unloading
                  and runtime.operation_lock is obj.lock and obj.lock.locked(), 'runtime_scope_lost')
            _need(not ({'scene', 'powercalc', 'mqtt', 'rest', 'command_line'}
                       & set(hass.config.components)), 'other_consumer_scope_unsupported')
            if hasattr(obj, 'consumers'):
                obj.consumers.check()
            if obj.bound:
                _need(runtime.journal is obj.journal and runtime.preparations is obj.preparations,
                      'runtime_cache_replaced')
                obj._preparation_current()
        obj._scope = scope
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
        from .recorder_guard import NativeRecorderGuard
        from .history_guard import NativeHistoryGuard
        from .yaml_editor_locks import NativeYAMLEditorLocks
        obj.recorder = NativeRecorderGuard(hass)
        obj.history = NativeHistoryGuard(obj.recorder)
        obj.locks = NativeYAMLEditorLocks(hass)
        return obj

    @classmethod
    async def prepare(cls, hass, mapping, *, guard, refresh):
        from .manifest import parse_manifest
        _need(type(mapping) is dict and bool(mapping)
              and all(type(k) is str and type(v) is str for k, v in mapping.items()), 'mapping_required')
        parsed = parse_manifest({'entities': [
            {'entity_id': old, 'proposed_entity_id': new} for old, new in mapping.items()]})
        _need(len(parsed.entities) == len(mapping)
              and len(set(mapping.values())) == len(mapping)
              and not set(mapping) & set(mapping.values()), 'independent_mapping_required')
        obj = await cls._open(hass, guard=guard, refresh=refresh)
        obj.mapping = dict(mapping)
        try:
            async with obj.locks.hold(obj._scope):
                await obj._prepare()
            obj._scope()
            return obj
        except BaseException:
            obj.close()
            raise

    async def _prepare(self):
        from .yaml_plan import plan_yaml
        from .yaml_validation import async_validate_proposed_with_sources
        from .yaml_transaction import _capture
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .native_input_baseline import async_capture_native_input_baseline
        self.consumers = await _UnchangedConsumers.capture(self.hass, self.mapping, guard=self._scope)
        self._scope()
        root = Path(self.hass.config.config_dir).resolve()
        yaml = await _drained(self.hass.async_add_executor_job, lambda: plan_yaml(root, self.mapping))
        self._scope()
        _need(yaml.ready and bool(yaml.patches), 'yaml_proposal_required')
        self.receipt, self.evidence = await async_validate_proposed_with_sources(
            self.hass, yaml, root.parent)
        self._scope()
        domains = self.receipt.changed_domains
        mixed = domains != ('template',)
        _need(self.receipt.ready_for_apply and 'template' in domains
              and set(domains) <= {'automation', 'script', 'template'},
              'modern_template_only_required')
        if mixed:
            from .mixed_yaml_runtime import MixedYAMLConsumers
            self.consumers = await MixedYAMLConsumers.capture(self.hass, self.mapping, guard=self._scope)
            self._scope()
        await self.consumers.bind_sources(self.receipt, self.evidence, guard=self._scope)
        self._scope()
        baseline = await async_capture_native_input_baseline(self.hass, self.receipt, guard=self._scope)
        self._scope()
        documents = {f.key: json.loads(f.raw) for f in baseline.files}
        entries = documents['core.config_entries']['data']['entries']
        _need(not any(row['domain'] == 'template' for row in entries), 'template_entry_unsupported')
        # Other native helper references also require their own lifecycle route.
        from .helper_native import inventory
        plans = inventory(self.hass, self.mapping)
        _need(not any(p.blocked or p.changes for p in plans), 'helper_dependency_unsupported')
        rows = documents['core.entity_registry']['data']['entities']
        bridge = NativeRegistryBridge(NativeRegistryIO(self.hass), self.recorder)
        registry = []
        for old, new in sorted(self.mapping.items()):
            source = [row for row in rows if row['entity_id'] == old]
            _need(len(source) == 1 and source[0]['platform'] != 'template'
                  and not any(row['entity_id'] == new for row in rows), 'source_uuid_required')
            # The dependent observer pins logical automation/script identities.
            # Renaming one of those sources needs its own accepted identity
            # handoff; reject before creating any effects until qualified.
            _need(source[0]['platform'] not in ('automation', 'script'),
                  'dependent_source_identity_unsupported')
            registry.append(await bridge.prepare('entity', source[0]['id'], {'entity_id': new}, self._scope))
            self._scope()
        change = await _drained(self.hass.async_add_executor_job, lambda: _capture(self.receipt, yaml))
        self._scope()
        self.registry_changes, self.change = tuple(registry), change
        self.operation_id, own = 'template-yaml-' + uuid.uuid4().hex, uuid.uuid4().hex
        self.digests, steps = {}, []
        for index, (adapter, item) in enumerate([('registry', c) for c in registry] + [('yaml', change)]):
            ref = own + '-' + str(index)
            self.digests[ref] = await self.preparations.create(ref, encode_change(item))
            self._scope()
            steps.append(Step(ref, adapter, item.identity, item.before_revision, item.after_revision, ref))
        from .native_template_consumers import async_observe_template_consumers
        from .template_physical_identity import is_physical_definition
        observed = await async_observe_template_consumers(self.hass, self.receipt,
            self.evidence, guard=self._scope)
        self._scope()
        consumers = [row for row in observed.rows if row.kind == 'entity']
        expected_keys = {(row.domain.removeprefix('template.'), row.native_key)
                         for row in self.evidence.template_sources.ledger.definitions
                         if row.domain in ('template.sensor', 'template.binary_sensor')}
        _need(len(consumers) == len(expected_keys)
              and {(row.platform, row.key) for row in consumers} == expected_keys
              and all(row.consumer_verified and row.source_verified for row in consumers),
              'complete_original_consumer_bijection_required')
        physical_keys = {(row.domain.removeprefix('template.'), row.native_key)
                         for row in self.evidence.template_sources.ledger.definitions
                         if is_physical_definition(row)}
        physical = tuple(sorted((row.platform, row.key, row.entity_id) for row in consumers
                                 if (row.platform, row.key) in physical_keys))
        consumer_ids = {row.entity_id: row.entity_id for row in consumers}
        if mixed:
            consumer_ids.update({row.entity_id: row.entity_id
                                 for row in self.consumers.phases.sample[0]})
        source_history = await self.history.capture(self.mapping)
        self._scope()
        consumer_history = await self.history.capture(consumer_ids)
        self._scope()
        composition_type = MixedTemplateComposition if mixed else TemplateComposition
        self.composition = composition_type(self.evidence, tuple(sorted(self.digests.items())),
            source_history, consumer_history, self.evidence.sha256, physical)
        inputs = await self.preparations.create_native_inputs(own + '-original', baseline,
                                                             composition=self.composition)
        self._scope()
        scopes = ('registry', 'yaml')
        self.plan = Plan(own, scopes, scopes, tuple(steps), self.evidence.sha256, scopes, inputs)
        self.plan.validate()
        self.receipt_store = native_registry_receipt_store(self.hass, self.preparations)
        await self.receipt_store.initialize()
        self._scope()
        self.chain = await RegistryReceiptChain.start(self.receipt_store, self.operation_id,
            self.plan, self.receipt, self.digests)
        self._scope()
        self._install()
        await self._common(False, initial=True)
        self._scope()

    def _install(self):
        # The chain has just resolved and strictly decoded this exact immutable
        # preparation. Keep its existing baseline, not a second decoded cache.
        reference = self.plan.native_inputs
        original_stamp = _baseline_stamp(self.chain.baseline)
        def preparation_current():
            _need(type(self.plan.native_inputs) is tuple and self.plan.native_inputs == reference,
                  'original_binding_changed')
            payload = self.preparations.resolve(*reference[:2])
            _need(canonical(payload)[1] == reference[1]
                  and _baseline_stamp(self.chain.baseline) == original_stamp,
                  'original_binding_changed')
            _need(self.chain.baseline.receipt == self.receipt
                  and self._composition_current(payload.get('composition')),
                  'composition_changed')
        self._preparation_current = preparation_current
        preparation_current()
        self.observation_state = self.chain.reconstruct_inputs()
        self.observer = self._new_observer()
        self.observer.assert_change(self.change)
        self.runtime.journal, self.runtime.preparations = self.journal, self.preparations
        self.bound = True

    def _new_observer(self):
        if type(self.composition) is MixedTemplateComposition:
            from .mixed_yaml_runtime import MixedTemplateYAMLRuntimeReadback
            return MixedTemplateYAMLRuntimeReadback(self.hass, self.chain, self.evidence,
                                                     self.consumers, guard=self._scope)
        return NativeTemplateYAMLRuntimeReadback(self.hass, self.chain, self.evidence,
                                                guard=self._scope)

    def _composition_current(self, payload):
        return payload == encode_composition(self.composition, self.chain.baseline)

    async def _observe(self, state=None):
        self._scope()
        if state is None:
            try:
                self.observation_state = self.chain.reconstruct_inputs()
            except RegistryReceiptError:
                _need(self.chain.active is not None, 'unresolved_intent')
                pending = self.chain.store.read(self.chain.active.reference[0])
                _need(pending is not None and pending['payload'].get('kind') == 'registry_intent',
                      'unresolved_intent')
            state = self.observation_state
        value = await async_observe_native_inputs(self.hass, self.receipt, guard=self._scope,
            input_state=state, physical_check=self.consumers.physical)
        self._scope()
        return value

    async def _fresh_stores(self, expected):
        preps, journal = native_prepared_store(self.hass), native_journal(self.hass)
        await preps.initialize()
        self._scope()
        await journal.initialize()
        self._scope()
        receipts = native_registry_receipt_store(self.hass, preps)
        await receipts.initialize()
        self._scope()
        _need(preps.plans == self.preparations.plans and receipts.rows == self.receipt_store.rows,
              'durable_store_changed')
        actual = await journal.load(self.operation_id)
        self._scope()
        active = await journal.active()
        self._scope()
        _need(actual == expected and not active, 'operation_changed')

    async def _common(self, rollback, *, initial=False):
        """Independent UUID/runtime/metadata plus full physical/history continuation."""
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .native_template_accepted_consumers import async_observe_accepted_template_consumers
        wanted = Position.BEFORE if rollback or initial else Position.AFTER
        bridge = NativeRegistryBridge(NativeRegistryIO(self.hass), self.recorder,
                                       native_images=self.chain.accepted_documents)
        for change in self.registry_changes:
            actual = await bridge.inspect(change)
            self._scope()
            _need(actual.position == wanted.value, 'source_identity_or_history_changed')
        state = self.chain.reconstruct_inputs()
        position = await self.observer.position_at_inputs(self.change, state)
        self._scope()
        _need(position is wanted, 'runtime_unconfirmed')
        witness = self.observer.runtime_check(wanted)
        actual = await async_observe_accepted_template_consumers(self.hass, self.chain,
            self.evidence, guard=self._scope)
        self._scope()
        entities = [r for r in actual.rows if r.kind == 'entity']
        _need(actual.yaml_position == ('original' if rollback or initial else 'proposed')
              and len(entities) == len(self.observer.identities)
              and {(r.platform, r.key) for r in entities} == set(self.observer.identities)
              and all(r.consumer_verified and r.source_verified
                      and (r.entity_id, r.registry_id) == self.observer.identities[r.platform, r.key]
                      for r in entities) and actual.evidence_sha256 == self.evidence.sha256,
              'consumer_identity_changed')
        await self._final_history(state, witness)
        return True

    async def _final_history(self, state, witness):
        # Source + finite histories share the LAST physical worker. All native
        # manager/registration checks are the synchronous suffix on its return.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.util import session_scope
        from .history_guard import _read_session as read_history
        from .recorder_guard import _read_session as read_metadata, verify, verify_rollback
        await self.recorder.async_synchronize()
        self._scope()
        managers = _Managers(self.hass, self.receipt)
        instance = self.recorder.instance
        _need(get_instance(self.hass) is instance, 'recorder_replaced')
        if type(self.composition) is MixedTemplateComposition:
            consumers = self.observer.history_consumers
        else:
            consumers = tuple(sorted(v[0] for v in self.observer.identities.values()))
        source_ids = tuple(sorted(set(self.mapping) | set(self.mapping.values())))
        def physical():
            self.consumers.physical()
            self.observer.native.sources()
            state.assert_current(self.receipt)
        def compare():
            physical()
            with session_scope(session=instance.get_session(), read_only=True) as sql:
                for ids, snapshot in ((source_ids, self.composition.source_history),
                                      (consumers, self.composition.consumer_history)):
                    _need(read_history(sql, snapshot['state_ids'], snapshot['statistic_ids'],
                                       snapshot['content']) == snapshot['content'], 'history_changed')
                    metadata = read_metadata(sql, ids)
                    _need(sorted({r['metadata_id'] for r in metadata['states'].values()}) == snapshot['state_ids']
                          and sorted({r['id'] for r in metadata['statistics'].values()}) == snapshot['statistic_ids'],
                          'history_identity_changed')
                for change in self.registry_changes:
                    metadata = read_metadata(sql, tuple(sorted(set(change.mapping) | set(change.mapping.values()))))
                    check = verify_rollback if next(iter(change.mapping)) in metadata['states'] else verify
                    _need(check(change.mapping, json.loads(change.recorder_before_json), metadata),
                          'source_metadata_changed')
            physical()
        await _drained(self.hass.async_add_executor_job, compare)
        self._scope()
        _need(get_instance(self.hass) is instance, 'recorder_replaced')
        managers.check()
        _need(state.same_evidence(self.chain.reconstruct_inputs()), 'head_changed')
        witness.check()

    async def _full(self, rollback):
        from .native_candidate_validation import async_revalidate_accepted_candidate
        await self._common(rollback)
        async with self.locks.hold(self._scope):
            proof = await async_revalidate_accepted_candidate(self.hass, self.chain,
                self.journal.load, Path(self.receipt.config_root).parent, self._scope)
            self._scope()
            _need(proof.fingerprint == self.plan.fingerprint and proof.head == self.chain.reconstruct_inputs().head
                  and proof.direction == ('rollback' if rollback else 'forward'), 'candidate_unconfirmed')
        # Candidate subprocess/lock awaits must not be the final source fence.
        await self._common(rollback)
        return True

    def _coordinator(self):
        from homeassistant.core import Context
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .native_registry_transaction import ReceiptedRegistryTransactionAdapter
        from .native_yaml_transaction import ReceiptedYAMLTransactionAdapter
        resolver = lambda ref: decode_change(self.preparations.resolve(ref, self.digests[ref]))
        bridge = NativeRegistryBridge(NativeRegistryIO(self.hass), self.recorder,
                                       native_images=self.chain.accepted_documents)
        registry = ReceiptedRegistryTransactionAdapter(bridge, resolver, self.chain, self._observe,
            self.journal.load, self._scope, full_verify=lambda _: self._common(False),
            full_verify_rollback=lambda _: self._common(True))
        yaml = ReceiptedYAMLTransactionAdapter(self.hass, resolver, self.locks, self.observer,
            self.chain, self._observe, self.journal.load, self._scope, context=Context(),
            full_verify=lambda _: self._full(False), full_verify_rollback=lambda _: self._full(True))
        return compose_template_yaml_coordinator({'registry': registry, 'yaml': yaml},
            self.journal, self._scope, required_scopes=('registry', 'yaml'))

    async def apply(self):
        self._scope()
        _need(asyncio.current_task() is self.task and self.bound and not self.used, 'no_replay')
        self.used = True
        await self.refresh()
        self._scope()
        await self._fresh_stores(None)
        await self._common(False, initial=True)
        if type(self.composition) is MixedTemplateComposition:
            from .journal import Record
            from .native_automation_lifecycle import NativeAutomationLifecycle
            # Persist the ordinary operation BEFORE any native detach, as in
            # the already qualified ConfigEntry lifecycle. Never replay apply.
            self.plan.validate()
            await self.journal.create(Record(self.operation_id, self.plan,
                phases=('pending',) * len(self.plan.steps)))
            self._scope()
            self.automation_lifecycle = NativeAutomationLifecycle(self, 'forward')
            self.observer.automation_lifecycle = self.automation_lifecycle
            await self.automation_lifecycle.start()
            coordinator = self._coordinator()
            ready = await coordinator.reconcile(self.operation_id)
            _need(ready.status == 'ready' and ready.direction == 'forward'
                  and ready.phases == ('pending',) * len(self.plan.steps), 'initial_effects_not_pristine')
            result = await coordinator.resume(self.operation_id)
        else:
            result = await self._coordinator().apply(self.plan, self.plan.fingerprint, self.operation_id)
        self._scope()
        _need(result.status == 'applied', 'apply_unresolved')
        if hasattr(self, 'automation_lifecycle'):
            await self.automation_lifecycle.restore()
        await self._terminal(result, rollback=False)
        if hasattr(self, 'automation_lifecycle'):
            await self.automation_lifecycle.complete(result)
        return result

    @classmethod
    async def bind_reverse(cls, hass, operation_id, fingerprint, *, guard, refresh):
        """Fresh read-only reopen, actual admission, then a DIFFERENT live chain."""
        obj = await cls._open(hass, guard=guard, refresh=refresh)
        try:
            record = await obj.journal.load(operation_id)
            obj._scope()
            _need(record is not None and record.operation_id == operation_id
                  and record.plan.fingerprint == fingerprint and record.status == 'applied'
                  and record.direction == 'forward'
                  and record.phases == ('verified',) * len(record.plan.steps), 'terminal_applied_required')
            obj.operation_id, obj.plan, obj.record = operation_id, record.plan, record
            baseline, obj.composition = obj.preparations.resolve_composition(obj.plan.native_inputs)
            obj.receipt, obj.evidence = baseline.receipt, obj.composition.evidence
            obj.digests = dict(obj.composition.prepared_digests)
            _need(obj.plan.coverage_evidence == obj.composition.coverage_sha256
                  and obj.plan.required_scopes == obj.plan.covered_scopes == obj.plan.verification_adapters == ('registry', 'yaml')
                  and tuple(s.adapter for s in obj.plan.steps) == ('registry',) * (len(obj.plan.steps) - 1) + ('yaml',)
                  and len(obj.plan.steps) >= 2 and not any(s.caused_by for s in obj.plan.steps), 'qualified_plan_required')
            changes = [decode_change(obj.preparations.resolve(s.prepared_ref, obj.digests[s.prepared_ref]))
                       for s in obj.plan.steps]
            obj.registry_changes, obj.change = tuple(changes[:-1]), changes[-1]
            _need(set(obj.digests) == {s.prepared_ref for s in obj.plan.steps}, 'preparation_scope')
            obj.mapping = {}
            for change in obj.registry_changes:
                _need(change.kind == 'entity' and len(change.mapping) == 1
                      and change.before['platform'] != 'template'
                      and set(k for k in change.before if change.before.get(k) != change.after.get(k))
                          <= {'entity_id', 'modified_at'}, 'source_mapping_required')
                _need(not set(obj.mapping) & set(change.mapping), 'duplicate_source')
                obj.mapping.update(change.mapping)
            _need(len(set(obj.mapping.values())) == len(obj.mapping)
                  and not set(obj.mapping) & set(obj.mapping.values()), 'independent_mapping_required')
            consumer_type = _UnchangedConsumers
            if type(obj.composition) is MixedTemplateComposition:
                from .mixed_yaml_runtime import MixedYAMLConsumers
                consumer_type = MixedYAMLConsumers
            obj.consumers = await consumer_type.capture(hass, obj.mapping, guard=obj._scope)
            obj._scope()
            obj.receipt_store = native_registry_receipt_store(hass, obj.preparations)
            await obj.receipt_store.initialize()
            obj._scope()
            readonly = RegistryReceiptChain.reopen(obj.receipt_store, operation_id, obj.plan,
                                                   obj.receipt, obj.digests)
            _need(not readonly.writable and readonly.active is None, 'readonly_required')
            obj.chain = readonly
            obj._install()
            # Runtime automation/script sources must also match SAME ORIGINAL.
            # Only accepted physical state differs; no replacement receipt is made.
            await obj._bind_accepted_sources()
            await obj._terminal(record, rollback=False)
            await refresh()
            obj._scope()
            await obj._fresh_stores(record)
            await obj._terminal(record, rollback=False)
            state = readonly.reconstruct_inputs()
            # Same separated admission pattern as NativeTemplateReverse: the
            # reopened reader remains readonly. Never set readonly.writable.
            writer = RegistryReceiptChain(obj.receipt_store, operation_id, obj.plan, obj.receipt, obj.digests)
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

    @classmethod
    async def bind_prepared(cls, hass, operation_id, plan, *, guard, refresh):
        """Reopen a server-retained review; preserve its exact ORIGINAL/Plan.

        This is an internal capability boundary. Websocket input never contains
        a Plan, receipt, preparations, root or callback.
        """
        obj = await cls._open(hass, guard=guard, refresh=refresh)
        try:
            obj.operation_id, obj.plan = operation_id, plan
            plan.validate()
            baseline, obj.composition = obj.preparations.resolve_composition(plan.native_inputs)
            obj.receipt, obj.evidence = baseline.receipt, obj.composition.evidence
            obj.digests = dict(obj.composition.prepared_digests)
            _need(plan.coverage_evidence == obj.composition.coverage_sha256
                  and plan.required_scopes == plan.covered_scopes == plan.verification_adapters == ('registry', 'yaml')
                  and len(plan.steps) >= 2
                  and tuple(s.adapter for s in plan.steps) == ('registry',) * (len(plan.steps) - 1) + ('yaml',)
                  and not any(s.caused_by for s in plan.steps)
                  and set(obj.digests) == {s.prepared_ref for s in plan.steps}, 'qualified_plan_required')
            changes = [decode_change(obj.preparations.resolve(s.prepared_ref, obj.digests[s.prepared_ref]))
                       for s in plan.steps]
            obj.registry_changes, obj.change = tuple(changes[:-1]), changes[-1]
            obj.mapping = {}
            for change in obj.registry_changes:
                _need(change.kind == 'entity' and len(change.mapping) == 1
                      and change.before['platform'] != 'template'
                      and set(k for k in change.before if change.before.get(k) != change.after.get(k))
                          <= {'entity_id', 'modified_at'}
                      and not set(obj.mapping) & set(change.mapping), 'source_mapping_required')
                obj.mapping.update(change.mapping)
            _need(len(set(obj.mapping.values())) == len(obj.mapping)
                  and not set(obj.mapping) & set(obj.mapping.values()), 'independent_mapping_required')
            obj.consumers = await _UnchangedConsumers.capture(hass, obj.mapping, guard=obj._scope)
            obj._scope()
            await obj.consumers.bind_sources(obj.receipt, obj.evidence, guard=obj._scope)
            obj._scope()
            obj.receipt_store = native_registry_receipt_store(hass, obj.preparations)
            await obj.receipt_store.initialize()
            obj._scope()
            readonly = RegistryReceiptChain.reopen(obj.receipt_store, operation_id, plan,
                                                   obj.receipt, obj.digests)
            state = readonly.reconstruct_inputs()
            _need(not readonly.writable and readonly.active is None
                  and state.head == readonly.root_digest, 'pristine_review_required')
            obj.chain = readonly
            obj._install()
            await refresh()
            obj._scope()
            await obj._fresh_stores(None)
            await obj._common(False, initial=True)
            # Admission constructs a separate writer; the reader stays readonly.
            writer = RegistryReceiptChain(obj.receipt_store, operation_id, plan, obj.receipt, obj.digests)
            _need(writer.root == readonly.root and writer.root_digest == readonly.root_digest
                  and writer.reconstruct_inputs().same_evidence(state), 'head_changed')
            writer.writable = True
            obj.chain = writer
            obj.observer.chain = writer
            return obj
        except BaseException:
            obj.close()
            raise

    async def _bind_accepted_sources(self):
        from .native_discovery_inputs import async_observe_discovery_inputs
        from .yaml_source_binding import _RuntimeSourceSampler
        from .discovery import _runtime_family
        for domain in ('automation', 'script'):
            if domain in self.consumers.loaded or self.evidence.ledger.keys_for_native_projection(domain):
                family, token = _runtime_family(self.hass, domain)
                _need(family.status == 'COMPLETE' and token is not None, 'source_runtime_unqualified')
        observed = await async_observe_discovery_inputs(self.hass, guard=self._scope)
        self._scope()
        sampler = _RuntimeSourceSampler.bind(self.evidence, observed)
        if type(self.composition) is MixedTemplateComposition:
            self.consumers.bind_phases(self.receipt, self.evidence, observed, Position.AFTER)
            self._scope()
            return
        sample = sampler.sample(self.hass, self.evidence, observed)
        _need(not sample[1], 'source_runtime_unqualified')
        # One synchronous replacement: guards never see a corpus from a
        # different evidence/observation pair during forward/reverse binding.
        (self.consumers.evidence, self.consumers.observed,
         self.consumers.runtime_sample, self.consumers.runtime_sampler) = (
            self.evidence, observed, sample, sampler)
        self._scope()

    async def _terminal(self, expected, *, rollback):
        record = await self.journal.load(self.operation_id)
        self._scope()
        _need(record == expected, 'phase_changed')
        await self._full(rollback)
        self._scope()
        observed = await self._observe()
        self.chain.expected(PhaseContext(record, len(self.plan.steps) - 1), tuple(
            RegistryRawObservation(r.key, r.raw, r.identity) for r in observed.files))
        await self._common(rollback)
        self._scope()

    async def rollback(self):
        self._scope()
        _need(asyncio.current_task() is self.task and self.bound and not self.used
              and hasattr(self, 'record'), 'no_replay')
        self.used = True
        await self.refresh()
        self._scope()
        await self._fresh_stores(self.record)
        await self._terminal(self.record, rollback=False)
        if type(self.composition) is MixedTemplateComposition:
            from .native_automation_lifecycle import NativeAutomationLifecycle
            self.automation_lifecycle = NativeAutomationLifecycle(self, 'rollback')
            self.observer.automation_lifecycle = self.automation_lifecycle
            await self.automation_lifecycle.start()
        result = await self._coordinator().rollback(self.operation_id)
        self._scope()
        _need(result.status == 'rolled_back', 'rollback_unresolved')
        if hasattr(self, 'automation_lifecycle'):
            await self.automation_lifecycle.restore()
        await self._terminal(result, rollback=True)
        if hasattr(self, 'automation_lifecycle'):
            await self.automation_lifecycle.complete(result)
        return result

    def close(self):
        _need(asyncio.current_task() is self.task, 'owner_task_required')
        if hasattr(self, 'automation_lifecycle'):
            self.automation_lifecycle.close()
        self.bound, self.closed = False, True
