"""Server-owned selective full-state Template migration, one ORIGINAL and Plan.

Private factory only. It extends the existing trusted lock/auth/store/history
lifecycle; exact source program selects the separate YAML/registry effect mode.
No profile, receipt, root, callback or preparation is supplied by a request.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import uuid

from .transaction import Refused, Plan, Step, Position
from .native_template_yaml_server import NativeTemplateYAMLServer
from .native_template_forward import _UnchangedConsumers
from .native_input_baseline import _drained
from .native_template_state_composition import TemplateStateComposition, MixedTemplateStateComposition
from .native_registry_receipts import RegistryReceiptChain, RegistryRawObservation, native_registry_receipt_store
from .prepared import encode_change, decode_change


def _need(value, reason):
    if not value:
        raise Refused('template_state_server_' + reason)


from .native_template_prefix_recovery import acknowledged_registry_prefix as _acknowledged_registry_prefix


class NativeTemplateStateServer(NativeTemplateYAMLServer):
    async def _prefix_current(self, record):
        """Read-only classification of the exact acknowledged physical prefix.

        The original YAML is still installed. Each registry UUID/metadata,
        accepted full-three input, live Template binding, source, consumer and
        finite-history fence is independently checked; no journal is advanced.
        """
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .native_template_state_binding import async_observe_accepted_state_bindings
        from .native_template_state_composition import TemplateStateComposition
        from .transaction import PhaseContext
        _need(type(self.composition) is TemplateStateComposition
              and _acknowledged_registry_prefix(record) and record.plan == self.plan
              and await self.journal.load(self.operation_id) == record,
              'acknowledged_prefix_required')
        self._scope()
        bridge = NativeRegistryBridge(NativeRegistryIO(self.hass), self.recorder,
                                       native_images=self.chain.accepted_documents)
        for index, change in enumerate(self.registry_changes):
            actual = await bridge.inspect(change)
            self._scope()
            wanted = 'after' if record.phases[index] == 'verified' else 'before'
            _need(actual.position == wanted, 'prefix_source_identity_or_history_changed')
        state = self.chain.reconstruct_inputs()
        position = await self.observer.position_at_inputs(self.change, state)
        self._scope()
        _need(position is Position.BEFORE, 'prefix_yaml_or_runtime_changed')
        witness = self.observer.runtime_check(Position.BEFORE)
        actual = await async_observe_accepted_state_bindings(self.hass, self.chain,
            self.program, guard=self._scope)
        self._scope()
        active = frozenset(next(iter(change.mapping)) for index, change in enumerate(self.registry_changes)
                           if record.phases[index] == 'verified')
        identities = set(self.observer.identities.values())
        _need(actual.prefix.active == active and actual.yaml_position == 'original'
              and len(actual.rows) == len(identities)
              and {(row.entity.entity_id, row.registry_id) for row in actual.rows} == identities,
              'prefix_consumer_identity_changed')
        observed = await self._observe()
        self.chain.expected(PhaseContext(record, len(record.plan.steps) - 1), tuple(
            RegistryRawObservation(row.key, row.raw, row.identity) for row in observed.files))
        _need(await self.journal.load(self.operation_id) == record, 'prefix_phase_changed')
        self._scope()
        # Last awaited physical worker includes the finite SQL content. Only
        # synchronous input/runtime/manager suffix checks follow it.
        await self._final_history(state, witness)
        return True

    async def _prepare(self):
        from .yaml_plan import plan_yaml
        from .yaml_validation import async_validate_proposed_with_template_state_sources
        from .native_template_state_yaml import capture_template_state_yaml
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .native_input_baseline import async_capture_native_input_baseline
        self.consumers = await _UnchangedConsumers.capture(self.hass, self.mapping, guard=self._scope)
        self._scope()
        root = Path(self.hass.config.config_dir).resolve()
        yaml = await _drained(self.hass.async_add_executor_job, lambda: plan_yaml(root, self.mapping))
        self._scope()
        _need(yaml.ready and bool(yaml.patches), 'yaml_proposal_required')
        bundle = await async_validate_proposed_with_template_state_sources(
            self.hass, yaml, self.mapping, root.parent, with_state_program=True)
        self.receipt, self.evidence, self.program = bundle.receipt, bundle.evidence, bundle.state_config_program
        self._scope()
        domains = self.receipt.changed_domains
        self.mixed_state = domains != ('template',)
        if self.mixed_state:
            from .yaml_transaction import _assert_mixed_state_scope
            from .mixed_yaml_runtime import MixedYAMLConsumers
            _assert_mixed_state_scope(self.receipt)
            self.consumers = await MixedYAMLConsumers.capture(self.hass, self.mapping, guard=self._scope)
            self._scope()
        _need('template' in domains and bundle.original_sources.source_matched
              and bundle.candidate_sources.sources.source_matched, 'modern_template_state_sources_required')
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
            _need(source[0]['platform'] not in ('automation', 'script'), 'dependent_source_identity_unsupported')
            registry.append(await bridge.prepare('entity', source[0]['id'], {'entity_id': new}, self._scope))
            self._scope()
        if self.mixed_state:
            from .native_template_state_yaml import capture_mixed_template_state_yaml
            capture = capture_mixed_template_state_yaml
        else:
            capture = capture_template_state_yaml
        change = await _drained(self.hass.async_add_executor_job, lambda: capture(bundle, yaml, self.mapping))
        self._scope()
        self.registry_changes, self.change = tuple(registry), change
        self.operation_id, own = 'template-yaml-' + uuid.uuid4().hex, uuid.uuid4().hex
        self.digests, steps = {}, []
        for index, (adapter, item) in enumerate([('registry', c) for c in registry] + [('yaml', change)]):
            ref = own + '-' + str(index)
            self.digests[ref] = await self.preparations.create(ref, encode_change(item))
            self._scope()
            steps.append(Step(ref, adapter, item.identity, item.before_revision, item.after_revision, ref))
        from .native_template_state_binding import _StateRuntime, StateBindingPrefix, sample_state_bindings
        native = _StateRuntime(self.hass)
        await _drained(self.hass.async_add_executor_job, native.sources)
        self._scope()
        prefix = StateBindingPrefix(self.program.sha256, self.operation_id,
            'source-preflight', baseline.digest, frozenset(),
            tuple(sorted((c.identity, next(iter(c.mapping))) for c in self.registry_changes)))
        observed = sample_state_bindings(native, self.program, prefix, 'original', documents)
        # Registry and physical consumers retain their actual original IDs.
        physical = tuple(sorted((r.domain.removeprefix('template.'), r.source_key, r.entity.entity_id)
            for r in observed.rows if (r.domain, r.source_key) in self.program.physical_keys))
        consumer_ids = {r['entity_id']: r['entity_id']
            for r in rows if r['platform'] == 'template'}
        consumer_ids.update({entity_id: entity_id for _, _, entity_id in physical})
        if self.mixed_state:
            consumer_ids.update({row.entity_id: row.entity_id for row in self.consumers.phases.sample[0]})
        source_history = await self.history.capture(self.mapping)
        self._scope()
        consumer_history = await self.history.capture(consumer_ids)
        self._scope()
        composition_type = MixedTemplateStateComposition if self.mixed_state else TemplateStateComposition
        self.composition = composition_type(self.evidence, bundle.original_sources,
            bundle.candidate_sources, self.program, yaml, tuple(sorted(self.mapping.items())),
            tuple(sorted(self.digests.items())), source_history, consumer_history,
            self.program.sha256, physical)
        inputs = await self.preparations.create_native_inputs(own + '-original', baseline,
                                                             composition=self.composition)
        self._scope()
        scopes = ('registry', 'yaml')
        self.plan = Plan(own, scopes, scopes, tuple(steps), self.program.sha256, scopes, inputs)
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


    def _composition_current(self, payload):
        from .native_template_state_composition import retain_state_composition
        binding = getattr(self, '_retained_state_composition', None)
        if binding is None:
            wire, check = retain_state_composition(self.composition, self.chain.baseline)
            binding = (self.composition, wire, check)
            self._retained_state_composition = binding
        _need(self.composition is binding[0], 'original_composition_replaced')
        binding[2]()
        return payload == binding[1]

    def _new_observer(self):
        from .native_template_state_yaml import NativeTemplateStateYAMLRuntimeReadback
        _need(type(self.composition) in (TemplateStateComposition, MixedTemplateStateComposition), 'state_composition_required')
        self.program = self.composition.state_config_program
        _need(self.composition.mapping == tuple(sorted(self.mapping.items())), 'original_mapping_changed')
        if type(self.composition) is MixedTemplateStateComposition:
            from .native_template_mixed_state_yaml import NativeMixedTemplateStateYAMLRuntimeReadback
            return NativeMixedTemplateStateYAMLRuntimeReadback(self.hass, self.chain,
                self.composition, self.consumers, guard=self._scope)
        return NativeTemplateStateYAMLRuntimeReadback(self.hass, self.chain,
                                                     self.composition, guard=self._scope)

    async def _common(self, rollback, *, initial=False):
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .native_template_state_binding import async_observe_accepted_state_bindings
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
        if type(self.composition) is MixedTemplateStateComposition:
            self.observer.assert_dependents_position(wanted)
        actual = await async_observe_accepted_state_bindings(self.hass, self.chain,
            self.program, guard=self._scope)
        self._scope()
        expected_active = frozenset() if rollback or initial else frozenset(self.mapping)
        _need(actual.prefix.active == expected_active
              and actual.yaml_position == ('original' if rollback or initial else 'proposed'),
              'terminal_prefix_unconfirmed')
        expected = set(self.observer.identities.values())
        _need(all((r.entity.entity_id, r.registry_id) in expected for r in actual.rows),
              'consumer_identity_changed')
        await self._final_history(state, witness)
        return True

    async def _full(self, rollback):
        from .native_candidate_validation import async_revalidate_accepted_template_state_candidate
        await self._common(rollback)
        async with self.locks.hold(self._scope):
            proof = await async_revalidate_accepted_template_state_candidate(self.hass,
                self.chain, self.journal.load, Path(self.receipt.config_root).parent, self._scope)
            self._scope()
            _need(proof.fingerprint == self.plan.fingerprint
                  and proof.head == self.chain.reconstruct_inputs().head
                  and proof.direction == ('rollback' if rollback else 'forward')
                  and proof.source_program_sha256 == self.program.sha256, 'candidate_unconfirmed')
        await self._common(rollback)
        return True

    def _coordinator(self):
        from homeassistant.core import Context
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .native_registry_transaction import ReceiptedRegistryTransactionAdapter
        from .native_template_state_yaml import (ReceiptedTemplateStateYAMLTransactionAdapter,
                                                 compose_template_state_yaml_coordinator)
        resolver = lambda ref: decode_change(self.preparations.resolve(ref, self.digests[ref]))
        bridge = NativeRegistryBridge(NativeRegistryIO(self.hass), self.recorder,
                                       native_images=self.chain.accepted_documents)
        registry = ReceiptedRegistryTransactionAdapter(bridge, resolver, self.chain, self._observe,
            self.journal.load, self._scope, full_verify=lambda _: self._common(False),
            full_verify_rollback=lambda _: self._common(True))
        registry.bind_template_state_root(self)
        adapter_type, compose = ReceiptedTemplateStateYAMLTransactionAdapter, compose_template_state_yaml_coordinator
        if type(self.composition) is MixedTemplateStateComposition:
            from .native_template_mixed_state_yaml import (ReceiptedMixedTemplateStateYAMLTransactionAdapter,
                compose_mixed_template_state_yaml_coordinator)
            adapter_type, compose = ReceiptedMixedTemplateStateYAMLTransactionAdapter, compose_mixed_template_state_yaml_coordinator
        yaml = adapter_type(self.hass, resolver, self.locks,
            self.observer, self.chain, self._observe, self.journal.load, self._scope, context=Context(),
            full_verify=lambda _: self._full(False), full_verify_rollback=lambda _: self._full(True))
        return compose({'registry': registry, 'yaml': yaml},
            self.journal, self._scope, required_scopes=('registry', 'yaml'))
