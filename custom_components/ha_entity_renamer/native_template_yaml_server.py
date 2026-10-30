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


def _mixed_composition(value):
    from .native_template_state_composition import MixedTemplateStateComposition
    from .native_script_program_composition import ScriptTemplateStateComposition
    from .native_automation_program_composition import AutomationTemplateStateComposition
    return type(value) in (MixedTemplateComposition, MixedTemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition)


def _script_prefix_composition(value):
    from .native_script_program_composition import ScriptTemplateStateComposition
    from .native_automation_program_composition import AutomationTemplateStateComposition
    return type(value) in (ScriptTemplateStateComposition, AutomationTemplateStateComposition)


def _registry_mapping(changes, composition):
    """Decode only the immutable server-selected registry field scope."""
    from .native_template_state_composition import TemplateStateComposition
    names = type(composition) is TemplateStateComposition
    mapping = {}
    for change in changes:
        before, after = change.before, change.after
        delta = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
        _need(before.get('id') == after.get('id') == change.identity, 'source_uuid_required')
        if change.kind == 'entity':
            allowed = {'entity_id', 'modified_at'} | ({'name'} if names else set())
            _need(delta <= allowed, 'source_mapping_required')
            if change.mapping:
                _need(len(change.mapping) == 1 and before['platform'] not in ('template', 'automation', 'script')
                      and not set(mapping) & set(change.mapping), 'source_mapping_required')
                mapping.update(change.mapping)
            else:
                _need(names and 'name' in delta and before['platform'] not in ('automation', 'script')
                      and bool(change.runtime_before_json), 'selected_name_scope_required')
            if 'name' in delta:
                _need(names and bool(change.runtime_before_json), 'selected_name_runtime_required')
        else:
            _need(names and change.kind == 'device' and 'name_by_user' in delta
                  and delta <= {'name_by_user', 'modified_at'} and bool(change.runtime_before_json),
                  'selected_device_name_scope_required')
    _need(bool(mapping) and len(set(mapping.values())) == len(mapping)
          and not set(mapping) & set(mapping.values()), 'independent_mapping_required')
    return mapping


def _registry_history_mapping(changes):
    """Whole selected source/name population from ORIGINAL preparations only."""
    ids = set()
    for change in changes:
        if change.kind == 'entity':
            ids.update((change.before['entity_id'], change.after['entity_id']))
        else:
            # NativeNameObserver captured every associated row before effects.
            baseline = json.loads(change.runtime_before_json)
            ids.update(item['row']['entity_id'] for item in baseline['entities'].values())
    return {key: key for key in sorted(ids)}


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
    async def _open(cls, hass, *, guard, refresh, recovering_operation=None):
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
            if hasattr(obj, 'automation_lifecycle'):
                obj.automation_lifecycle.check_live()
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
        active = await obj.journal.active()
        _need(not active or (cls is NativeTemplateStateServer and type(recovering_operation) is str
              and len(active) == 1 and active[0].operation_id == recovering_operation),
              'other_operation_unresolved')
        scope()
        from .native_automation_lifecycle import async_assert_no_pending_automation_lifecycle
        await async_assert_no_pending_automation_lifecycle(hass, obj.preparations, obj.journal)
        scope()
        from .recorder_guard import NativeRecorderGuard
        from .history_guard import NativeHistoryGuard
        from .yaml_editor_locks import NativeYAMLEditorLocks
        obj.recorder = NativeRecorderGuard(hass)
        obj.history = NativeHistoryGuard(obj.recorder)
        obj.locks = NativeYAMLEditorLocks(hass)
        return obj

    @classmethod
    async def prepare(cls, hass, mapping, *, guard, refresh, entity_names=None, device_names=None,
                      review_id=None):
        from .manifest import parse_manifest
        _need(type(mapping) is dict and bool(mapping)
              and all(type(k) is str and type(v) is str for k, v in mapping.items()), 'mapping_required')
        from .native_template_state_server import NativeTemplateStateServer
        if review_id is not None:
            from .review_retention import review_lease
            _need(cls is NativeTemplateStateServer and type(review_id) is str,
                  'review_scope_required')
            review_lease(review_id, 'server')  # Validate server-owned namespace.
        names = {} if entity_names is None else entity_names
        devices = {} if device_names is None else device_names
        _need(type(names) is dict and type(devices) is dict
              and all(type(key) is str and type(value) is str for key, value in names.items())
              and all(type(key) is str and type(value) is str for key, value in devices.items())
              and (not (names or devices) or cls is NativeTemplateStateServer),
              'selected_registry_names_required')
        names, devices = dict(names), dict(devices)
        selected = set(mapping) | set(names)
        parsed = parse_manifest({'entities': [
            {'entity_id': old, 'proposed_entity_id': mapping.get(old, old),
             'proposed_name': names.get(old)} for old in sorted(selected)],
            'devices': [{'device_id': identity, 'proposed_name': name}
                        for identity, name in sorted(devices.items())]})
        _need(len(parsed.entities) == len(selected) and len(parsed.devices) == len(devices)
              and len(set(mapping.values())) == len(mapping)
              and not set(mapping) & set(mapping.values()), 'independent_mapping_required')
        obj = await cls._open(hass, guard=guard, refresh=refresh)
        obj.mapping, obj.entity_names, obj.device_names = dict(mapping), names, devices
        obj.review_id = review_id
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
        if _mixed_composition(self.composition):
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

    async def _fresh_stores(self, expected, *, allow_active=False):
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
        _need(actual == expected and (not active or (allow_active and active == (expected,))),
              'operation_changed')

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
        if _mixed_composition(self.composition):
            consumers = self.observer.history_consumers
        else:
            consumers = tuple(sorted(v[0] for v in self.observer.identities.values()))
        source_ids = tuple(_registry_history_mapping(self.registry_changes))
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
                    if not change.mapping:
                        continue
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
        if _mixed_composition(self.composition) and not _script_prefix_composition(self.composition):
            from .journal import Record
            from .native_automation_lifecycle import NativeAutomationLifecycle
            # Persist the ordinary operation BEFORE any native detach, as in
            # the already qualified ConfigEntry lifecycle. Never replay apply.
            self.plan.validate()
            # Native type/idle admission is read-only and precedes even the
            # operation record; no rejected busy source creates a pending job.
            self.automation_lifecycle = NativeAutomationLifecycle(self, 'forward')
            self.observer.automation_lifecycle = self.automation_lifecycle
            await self.journal.create(Record(self.operation_id, self.plan,
                phases=('pending',) * len(self.plan.steps)))
            self._scope()
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
        obj = await cls._open(hass, guard=guard, refresh=refresh, recovering_operation=operation_id)
        try:
            record = await obj.journal.load(operation_id)
            obj._scope()
            from .native_template_state_server import _acknowledged_registry_prefix
            obj.prefix_recovery = _acknowledged_registry_prefix(record)
            _need(record is not None and record.operation_id == operation_id
                  and record.plan.fingerprint == fingerprint and record.direction == 'forward'
                  and ((record.status == 'applied'
                        and record.phases == ('verified',) * len(record.plan.steps))
                       or obj.prefix_recovery), 'terminal_or_acknowledged_prefix_required')
            obj.operation_id, obj.plan, obj.record = operation_id, record.plan, record
            baseline, obj.composition = obj.preparations.resolve_composition(obj.plan.native_inputs)
            if obj.prefix_recovery:
                from .native_template_state_composition import TemplateStateComposition
                from .native_template_state_server import NativeTemplateStateServer
                from .native_script_program_composition import ScriptTemplateStateComposition
                from .native_automation_program_composition import AutomationTemplateStateComposition
                _need(cls is NativeTemplateStateServer
                      and type(obj.composition) in (TemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition),
                      'prefix_profile_unsupported')
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
            obj.mapping = _registry_mapping(obj.registry_changes, obj.composition)
            consumer_type = _UnchangedConsumers
            if _mixed_composition(obj.composition):
                from .mixed_yaml_runtime import MixedYAMLConsumers
                consumer_type = MixedYAMLConsumers
            obj.consumers = await consumer_type.capture(hass, obj.mapping, guard=obj._scope)
            from .native_automation_program_composition import AutomationTemplateStateComposition
            if type(obj.composition) is AutomationTemplateStateComposition:
                from .native_automation_owned_graph import OwnedAutomationGraph
                obj.consumers.owned_automation = await OwnedAutomationGraph.with_duration(hass, guard=obj._scope)
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
            async def admit():
                if obj.prefix_recovery:
                    await obj._prefix_current(record)
                else:
                    await obj._terminal(record, rollback=False)
            await admit()
            await refresh()
            obj._scope()
            await obj._fresh_stores(record, allow_active=obj.prefix_recovery)
            await admit()
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
            obj.mapping = _registry_mapping(obj.registry_changes, obj.composition)
            consumer_type = _UnchangedConsumers
            if _mixed_composition(obj.composition):
                from .mixed_yaml_runtime import MixedYAMLConsumers
                consumer_type = MixedYAMLConsumers
            obj.consumers = await consumer_type.capture(hass, obj.mapping, guard=obj._scope)
            obj._scope()
            bind_sources = (obj.consumers.bind_prefix_sources if _script_prefix_composition(obj.composition)
                            else obj.consumers.bind_sources)
            await bind_sources(obj.receipt, obj.evidence, guard=obj._scope)
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
                family, token = _runtime_family(self.hass, domain,
                    owned_automation=getattr(self.consumers, "owned_automation", None))
                _need(family.status == 'COMPLETE' and token is not None, 'source_runtime_unqualified')
        observed = await async_observe_discovery_inputs(self.hass, guard=self._scope)
        self._scope()
        sampler = _RuntimeSourceSampler.bind(self.evidence, observed)
        if _script_prefix_composition(self.composition):
            self.consumers.bind_prefix_phases(self)
            self._scope()
            return
        if _mixed_composition(self.composition):
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
        prefix = getattr(self, 'prefix_recovery', False)
        await self._fresh_stores(self.record, allow_active=prefix)
        if prefix:
            await self._prefix_current(self.record)
        else:
            await self._terminal(self.record, rollback=False)
        if _mixed_composition(self.composition) and not _script_prefix_composition(self.composition):
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
        consumers = getattr(self, 'consumers', None)
        phases = getattr(consumers, 'phases', None)
        from .native_script_prefix_phase import NativeScriptPrefixPhase
        from .native_automation_prefix_phase import NativeAutomationPrefixPhase
        if type(phases) in (NativeScriptPrefixPhase, NativeAutomationPrefixPhase):
            phases.close()
        self.bound, self.closed = False, True
