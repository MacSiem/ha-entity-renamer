"""Server-owned selective full-state Template migration, one ORIGINAL and Plan.

Private factory only. It extends the existing trusted lock/auth/store/history
lifecycle; exact source program selects the separate YAML/registry effect mode.
No profile, receipt, root, callback or preparation is supplied by a request.
"""
from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
import uuid

from .transaction import Refused, Plan, Step, Position
from .native_template_yaml_server import NativeTemplateYAMLServer, _registry_history_mapping
from .native_template_forward import _UnchangedConsumers
from .native_input_baseline import _drained
from .native_template_state_composition import TemplateStateComposition, MixedTemplateStateComposition
from .native_registry_receipts import RegistryReceiptChain, RegistryRawObservation, native_registry_receipt_store
from .prepared import encode_change, decode_change
from .native_script_program_composition import ScriptTemplateStateComposition
from .native_automation_program_composition import AutomationTemplateStateComposition


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
        _need(type(self.composition) in (TemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition)
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
        if position is not Position.BEFORE:
            # Only a single acknowledged ordinary source prefix. Original YAML
            # can reconstruct its original Template bindings after process loss;
            # prove those live twice rather than trusting a historical receipt.
            change = self.registry_changes[0]
            _need(type(self.composition) is TemplateStateComposition
                  and len(self.registry_changes) == 1
                  and record.phases == ('verified', 'pending')
                  and change.before.get('platform') not in ('template', 'automation', 'script')
                  and len(change.mapping) == 1, 'cold_original_prefix_unsupported')
            self.observer.runtime_original = True
            position = await self.observer.position_at_inputs(self.change, state)
            self._scope()
            _need(position is Position.BEFORE, 'prefix_yaml_or_runtime_changed')
            self.cold_original_prefix = True
        witness = self.observer.runtime_check(Position.BEFORE)
        actual = await async_observe_accepted_state_bindings(self.hass, self.chain,
            self.program, guard=self._scope,
            runtime_original=getattr(self, 'cold_original_prefix', False))
        self._scope()
        active = frozenset(next(iter(change.mapping)) for index, change in enumerate(self.registry_changes)
                           if change.mapping and record.phases[index] == 'verified')
        identities = self.observer.identities_at(active)
        runtime_active = frozenset() if getattr(self, 'cold_original_prefix', False) else active
        _need(actual.prefix.active == runtime_active and actual.yaml_position == 'original'
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
        # Identify the native Script-key proposal BEFORE source planning. This
        # is only the source-checker opt-in; the later Root qualifier still
        # refuses this shape until registry/service/lifecycle are implemented.
        # Retain an actual caller/service census across every staging await.
        io = NativeRegistryIO(self.hass)
        source_rows = io.document('entity')['entities']
        owned_script = []
        for old, new in self.mapping.items():
            hits = [row for row in source_rows if row.get('entity_id') == old]
            if (len(hits) == 1 and hits[0].get('platform') == 'script'
                    and type(hits[0].get('unique_id')) is str
                    and old == 'script.' + hits[0]['unique_id']
                    and new != old):
                owned_script.append(old)
        _need(len(owned_script) <= 1, 'own_script_service_scope_unsupported')
        owned_script = tuple(owned_script)
        inventory = service_audit = service_recipients = None
        if owned_script:
            from .native_own_id_inventory import NativeOwnIDInventory
            from .native_own_script_service_audit import NativeOwnScriptServiceAudit
            from .native_own_script_service_recipients import NativeOwnScriptServiceRecipients
            old = owned_script[0]
            inventory = NativeOwnIDInventory.capture(self.hass, {old: self.mapping[old]})
            service_audit = await NativeOwnScriptServiceAudit.capture(
                self.hass, inventory, guard=self._scope)
            service_recipients = await NativeOwnScriptServiceRecipients.capture(
                self.hass, inventory, guard=self._scope, scene_fence=self.scene_fence)
        root = Path(self.hass.config.config_dir).resolve()
        yaml = await _drained(self.hass.async_add_executor_job,
            lambda: plan_yaml(root, self.mapping, owned_script_services=owned_script))
        self._scope()
        if inventory is not None:
            inventory.check()
            service_audit.check()
            service_recipients.check()
        if not yaml.ready and any(issue.code == 'template_requires_reference_parser' for issue in yaml.issues):
            _need(False, 'template_requires_reference_parser')
        _need(yaml.ready, 'yaml_proposal_required')
        registry_only = not yaml.patches
        try:
            bundle = await async_validate_proposed_with_template_state_sources(
                self.hass, yaml, self.mapping, root.parent, with_state_program=True,
                registry_only_template=registry_only, with_dependent_program=bool(owned_script),
                owned_script_services=owned_script)
        except ValueError as error:
            if error.args == ('template_state_affected_unsupported_family',):
                raise Refused('template_state_server_affected_unsupported_family') from None
            raise
        if inventory is not None:
            inventory.check()
            service_audit.check()
            service_recipients.check()
        candidate_own = any(row.get('entity_id') in self.mapping
                            and row.get('platform') in ('template', 'automation', 'script')
                            for row in source_rows)
        if (not owned_script and (bundle.receipt.changed_domains in
                (('script', 'template'), ('automation', 'template'), ('automation', 'script', 'template'))
                or candidate_own and bundle.receipt.changed_domains in
                (('script',), ('automation',), ('automation', 'script')))):
            # Both programs come from one extended checked invocation, with
            # exact ORIGINAL equality to the preceding legacy admission.
            extended = await async_validate_proposed_with_template_state_sources(
                self.hass, yaml, self.mapping, root.parent, with_state_program=True,
                with_dependent_program=True)
            _need((extended.receipt, extended.evidence, extended.original_sources,
                   extended.candidate_sources, extended.state_config_program)
                  == (bundle.receipt, bundle.evidence, bundle.original_sources,
                      bundle.candidate_sources, bundle.state_config_program),
                  'script_program_original_changed')
            bundle = extended
        self.receipt, self.evidence, self.program = bundle.receipt, bundle.evidence, bundle.state_config_program
        self._scope()
        domains = self.receipt.changed_domains
        selected_owners = [row for row in source_rows if row.get('entity_id') in self.mapping
                           and row.get('platform') in ('template', 'automation', 'script')]
        dependent_only = bool(domains) and 'template' not in domains and len(selected_owners) == 1
        if domains in (('script',), ('script', 'template')):
            from .native_script_config_transport import assert_idle_script_program_targets
            assert_idle_script_program_targets(self.hass, bundle.dependent_config_program)
        automation_prefix = domains in (('automation',), ('automation', 'script'),
                                        ('automation', 'template'), ('automation', 'script', 'template'))
        if automation_prefix:
            from .native_automation_config_transport import assert_idle_automation_program_targets
            assert_idle_automation_program_targets(self.hass, bundle.dependent_config_program)
        self.mixed_state = domains not in ((), ('template',))
        _need(not ((self.entity_names or self.device_names) and self.mixed_state),
              'selected_names_mixed_lifecycle_unsupported')
        if self.mixed_state:
            from .yaml_transaction import _assert_mixed_state_scope
            from .mixed_yaml_runtime import MixedYAMLConsumers
            _assert_mixed_state_scope(self.receipt, allow_dependent_only=dependent_only)
            self.consumers = await MixedYAMLConsumers.capture(self.hass, self.mapping, guard=self._scope)
            self._scope()
        _need((domains == () and registry_only or ('template' in domains or dependent_only) and not registry_only)
              and bundle.original_sources.source_matched
              and bundle.candidate_sources.sources.source_matched, 'modern_template_state_sources_required')
        bind_sources = (self.consumers.bind_prefix_sources if domains in (('script',), ('script', 'template')) or automation_prefix
                        else self.consumers.bind_sources)
        await bind_sources(self.receipt, self.evidence, guard=self._scope)
        self._scope()
        if self.entity_names or self.device_names:
            _need(not self.consumers.runtime_sample[0], 'selected_names_dependent_lifecycle_unsupported')
        baseline = await async_capture_native_input_baseline(self.hass, self.receipt, guard=self._scope)
        self._scope()
        documents = {f.key: json.loads(f.raw) for f in baseline.files}
        entries = documents['core.config_entries']['data']['entries']
        _need(not any(row['domain'] == 'template' for row in entries), 'template_entry_unsupported')
        # Other native helper references also require their own lifecycle route.
        from .helper_native import inventory as helper_inventory
        plans = helper_inventory(self.hass, self.mapping)
        _need(not any(p.blocked or p.changes for p in plans), 'helper_dependency_unsupported')
        rows = documents['core.entity_registry']['data']['entities']
        bridge = NativeRegistryBridge(NativeRegistryIO(self.hass), self.recorder)
        registry = []
        for old, new in sorted(self.mapping.items()):
            source = [row for row in rows if row['entity_id'] == old]
            _need(len(source) == 1 and not any(row['entity_id'] == new for row in rows),
                  'source_uuid_required')
            fields = {'entity_id': new}
            if old in self.entity_names:
                fields['name'] = self.entity_names[old]
            if old in owned_script:
                _need(inventory is not None, 'own_script_inventory_required')
                inventory.check()
                service_audit.check()
                service_recipients.check()
            prepared = await bridge.prepare('entity', source[0]['id'], fields, self._scope)
            self._scope()
            if old in owned_script:
                from .native_own_script_registry_preparation import own_script_registry_preparation
                service_audit.check()
                service_recipients.check()
                prepared = own_script_registry_preparation(prepared, inventory)
            registry.append(prepared)
        own_template = [change for change in registry if change.before.get('platform') == 'template']
        own_dependent = [change for change in registry if change.before.get('platform') in ('automation', 'script')]
        if own_template or own_dependent:
            _need(not (own_template and own_dependent)
                  and domains in ((), ('template',), ('script',), ('automation',), ('automation', 'script'),
                                  ('script', 'template'), ('automation', 'template'),
                                  ('automation', 'script', 'template'))
                  and not self.entity_names and not self.device_names, 'own_entity_scope_unsupported')
            if own_template:
                from .native_own_id_this import qualify_template_own_changes
                qualify_template_own_changes(self.hass, registry, guard=self._scope)
            else:
                from .native_own_dependent_fence import qualify_dependent_own_changes
                qualify_dependent_own_changes(self.hass, registry, guard=self._scope)
        _need(not registry_only or bool(own_template or own_dependent), 'registry_only_own_entity_required')
        for entity_id, name in sorted(self.entity_names.items()):
            if entity_id in self.mapping:
                continue
            source = [row for row in rows if row['entity_id'] == entity_id]
            _need(len(source) == 1 and source[0]['platform'] not in ('automation', 'script'),
                  'selected_name_source_required')
            if source[0].get('name') != name:
                registry.append(await bridge.prepare('entity', source[0]['id'], {'name': name}, self._scope))
                self._scope()
        device_rows = documents['core.device_registry']['data']['devices']
        for identity, name in sorted(self.device_names.items()):
            source = [row for row in device_rows if row['id'] == identity]
            _need(len(source) == 1, 'selected_device_source_required')
            if source[0].get('name_by_user') != name:
                registry.append(await bridge.prepare('device', identity, {'name_by_user': name}, self._scope))
                self._scope()
        if self.mixed_state:
            from .native_template_state_yaml import capture_mixed_template_state_yaml
            capture = capture_mixed_template_state_yaml
        else:
            capture = capture_template_state_yaml
        change = (None if registry_only else
                  await _drained(self.hass.async_add_executor_job, lambda: capture(bundle, yaml, self.mapping)))
        self._scope()
        self.registry_changes = tuple(registry)
        self.change = registry[-1] if registry_only else change
        own = self.review_id or uuid.uuid4().hex
        self.operation_id = 'template-yaml-' + (self.review_id or uuid.uuid4().hex)
        self.digests, steps = {}, []
        effects = [('registry', c) for c in registry] + ([] if registry_only else [('yaml', change)])
        for index, (adapter, item) in enumerate(effects):
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
            tuple(sorted((c.identity, next(iter(c.mapping))) for c in self.registry_changes if c.mapping)))
        observed = sample_state_bindings(native, self.program, prefix, 'original', documents)
        # Registry and physical consumers retain their actual original IDs.
        physical = tuple(sorted((r.domain.removeprefix('template.'), r.source_key, r.entity.entity_id)
            for r in observed.rows if (r.domain, r.source_key) in self.program.physical_keys))
        consumer_ids = {r['entity_id']: r['entity_id']
            for r in rows if r['platform'] == 'template'}
        consumer_ids.update({entity_id: entity_id for _, _, entity_id in physical})
        if self.mixed_state:
            dependent_sample = (self.consumers.runtime_sample if domains in (('script',), ('script', 'template')) or automation_prefix
                                else self.consumers.phases.sample)
            consumer_ids.update({row.entity_id: row.entity_id for row in dependent_sample[0]})
        source_history = await self.history.capture(_registry_history_mapping(self.registry_changes))
        self._scope()
        consumer_history = await self.history.capture(consumer_ids)
        self._scope()
        script_prefix = self.receipt.changed_domains in (('script',), ('script', 'template'))
        composition_type = (AutomationTemplateStateComposition if automation_prefix else
                            ScriptTemplateStateComposition if script_prefix else
                            MixedTemplateStateComposition if self.mixed_state else TemplateStateComposition)
        self.composition = composition_type(self.evidence, bundle.original_sources,
            bundle.candidate_sources, self.program, yaml, tuple(sorted(self.mapping.items())),
            tuple(sorted(self.digests.items())), source_history, consumer_history,
            self.program.sha256, physical,
            **({'dependent_config_program': bundle.dependent_config_program} if script_prefix or automation_prefix else {}))
        inputs = await self.preparations.create_native_inputs(own + '-original', baseline,
                                                             composition=self.composition)
        self._scope()
        scopes = ('registry',) if registry_only else ('registry', 'yaml')
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
        _need(type(self.composition) in (TemplateStateComposition, MixedTemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition), 'state_composition_required')
        self.program = self.composition.state_config_program
        if any(change.before.get('platform') == 'template' for change in self.registry_changes):
            _need(type(self.composition) in (TemplateStateComposition, ScriptTemplateStateComposition,
                                               AutomationTemplateStateComposition)
                  and self.receipt.changed_domains in ((), ('template',), ('script',), ('automation',),
                      ('automation', 'script'), ('script', 'template'),
                      ('automation', 'template'), ('automation', 'script', 'template')),
                  'own_template_scope_unsupported')
            from .native_own_id_this import qualify_template_own_changes
            self.own_template_admission = qualify_template_own_changes(
                self.hass, self.registry_changes, guard=self._scope)
        own_dependent = [change for change in self.registry_changes
                         if change.before.get('platform') in ('automation', 'script')]
        if own_dependent:
            _need(type(self.composition) in (TemplateStateComposition, ScriptTemplateStateComposition,
                                             AutomationTemplateStateComposition), 'own_dependent_profile_unsupported')
            from .native_own_dependent_fence import NativeOwnDependentFence, qualify_dependent_own_changes
            change = qualify_dependent_own_changes(self.hass, self.registry_changes, guard=self._scope)
            if type(self.composition) in (ScriptTemplateStateComposition, AutomationTemplateStateComposition):
                from .dependent_config_binding import NativeDependentConfigReadback
                dependent = NativeDependentConfigReadback(self.hass, self.composition.dependent_config_program,
                    self.evidence, self.receipt, self.composition.yaml_plan,
                    owned_automation=getattr(self.consumers, 'owned_automation', None))
                sample = dependent.sample(self.chain, input_state=self.observation_state)
                initial = sample.registry_rows, (), sample.native_tokens
            else:
                from .yaml_source_binding import _runtime_rows_sample
                initial = _runtime_rows_sample(self.hass, self.evidence,
                    self.chain.accepted_documents()['core.entity_registry']['data']['entities'],
                    owned_automation=getattr(self.consumers, 'owned_automation', None))
            self.own_dependent_fence = NativeOwnDependentFence(self, change, initial)
            self.consumers.own_fence = self.own_dependent_fence
            from .native_own_dependent_fence import NativeOwnDependentAdmission
            self.own_record_admission = NativeOwnDependentAdmission(self.own_dependent_fence)
            self.own_record_head = self.observation_state.head
        if type(self.composition) in (ScriptTemplateStateComposition, AutomationTemplateStateComposition):
            self.consumers.bind_prefix_phases(self)
        _need(self.composition.mapping == tuple(sorted(self.mapping.items())), 'original_mapping_changed')
        if not self.receipt.proposed_files:
            from .native_own_id_registry_readback import NativeOwnIDRegistryReadback
            return NativeOwnIDRegistryReadback(self.hass, self.chain, self.composition, guard=self._scope)
        if type(self.composition) in (MixedTemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition):
            from .native_template_mixed_state_yaml import NativeMixedTemplateStateYAMLRuntimeReadback
            return NativeMixedTemplateStateYAMLRuntimeReadback(self.hass, self.chain,
                self.composition, self.consumers, guard=self._scope)
        return NativeTemplateStateYAMLRuntimeReadback(self.hass, self.chain,
                                                     self.composition, guard=self._scope)

    async def _common(self, rollback, *, initial=False):
        try:
            return await self._check_common(rollback, initial=initial)
        except Exception as error:
            from .yaml_validation_worker import _checker_diagnostic
            logging.getLogger(__name__).warning('Migration state verification unavailable: %s',
                                               _checker_diagnostic(error))
            raise

    async def _check_common(self, rollback, *, initial=False):
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
        if type(self.composition) in (MixedTemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition):
            self.observer.assert_dependents_position(wanted)
        actual = await async_observe_accepted_state_bindings(self.hass, self.chain,
            self.program, guard=self._scope)
        self._scope()
        expected_active = frozenset() if rollback or initial else frozenset(self.mapping)
        _need(actual.prefix.active == expected_active
              and actual.yaml_position == ('original' if rollback or initial or not self.receipt.proposed_files else 'proposed'),
              'terminal_prefix_unconfirmed')
        expected = self.observer.identities_at(actual.prefix.active)
        _need(all((r.entity.entity_id, r.registry_id) in expected for r in actual.rows),
              'consumer_identity_changed')
        await self._final_history(state, witness)
        return True

    async def _full(self, rollback):
        try:
            return await self._check_full(rollback)
        except Exception as error:
            from .yaml_validation_worker import _checker_diagnostic
            logging.getLogger(__name__).warning('Migration final verification unavailable: %s',
                                               _checker_diagnostic(error))
            raise

    async def _check_full(self, rollback):
        from .native_candidate_validation import (
            async_revalidate_accepted_template_state_candidate, private_candidate_scratch)
        await self._common(rollback)
        async with self.locks.hold(self._scope):
            with private_candidate_scratch(self.receipt.config_root) as scratch:
                proof = await async_revalidate_accepted_template_state_candidate(self.hass,
                    self.chain, self.journal.load, scratch, self._scope)
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
        own_admission = getattr(self, 'own_record_admission', None)
        template_admission = getattr(self, 'template_record_admission', None)
        trace_admission = getattr(self, 'trace_record_admission', ())
        phase_admission = (self.consumers.phases.admit_record
                           if type(self.composition) in (ScriptTemplateStateComposition, AutomationTemplateStateComposition)
                           else None)
        def admit(direction):
            if template_admission is not None:
                template_admission.check_review()
            for proof in trace_admission:
                proof.check_review()
            if phase_admission is not None:
                phase_admission(direction)
            if own_admission is not None:
                own_admission.check()
        record_admission = (admit if own_admission is not None or template_admission is not None
                            or phase_admission is not None or trace_admission else None)
        if not self.receipt.proposed_files:
            from .transaction import Coordinator
            registry.full_verify = lambda _: self._full(False)
            registry.full_verify_rollback = lambda _: self._full(True)
            return Coordinator({'registry': registry}, self.journal, self._scope,
                               required_scopes=('registry',), record_admission=record_admission)
        adapter_type, compose = ReceiptedTemplateStateYAMLTransactionAdapter, compose_template_state_yaml_coordinator
        if type(self.composition) in (MixedTemplateStateComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition):
            from .native_template_mixed_state_yaml import (ReceiptedMixedTemplateStateYAMLTransactionAdapter,
                compose_mixed_template_state_yaml_coordinator)
            adapter_type, compose = ReceiptedMixedTemplateStateYAMLTransactionAdapter, compose_mixed_template_state_yaml_coordinator
        yaml = adapter_type(self.hass, resolver, self.locks,
            self.observer, self.chain, self._observe, self.journal.load, self._scope, context=Context(),
            full_verify=lambda _: self._full(False), full_verify_rollback=lambda _: self._full(True))
        admission = {} if record_admission is None else {'record_admission': record_admission}
        return compose({'registry': registry, 'yaml': yaml},
            self.journal, self._scope, required_scopes=('registry', 'yaml'), **admission)
