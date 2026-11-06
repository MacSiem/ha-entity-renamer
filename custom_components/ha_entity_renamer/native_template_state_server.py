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
from .native_template_state_composition import TemplateStateComposition, MixedTemplateStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition
from .controlled_script_composition import ControlledScriptComposition, is_controlled_script
from .native_registry_receipts import RegistryReceiptChain, RegistryRawObservation, native_registry_receipt_store
from .prepared import encode_change, decode_change
from .native_script_program_composition import ScriptTemplateStateComposition
from .native_automation_program_composition import AutomationTemplateStateComposition
from .controlled_powercalc_composition import ControlledPowercalcComposition


def _need(value, reason):
    if not value:
        raise Refused('template_state_server_' + reason)


def _powercalc_group_helper_coverage(plans, rows, previous, mapping):
    """Qualify only the metadata of the proven group, never unknown helpers."""
    if previous is None or len(previous.groups) != 1:
        return False
    group = previous.groups[0]
    owners = [row for row in rows if row.get('id') == group.registry_id
              and row.get('entity_id') == group.entity_id and row.get('platform') == 'powercalc']
    if len(owners) != 1 or group.entity_id not in mapping:
        return False
    entry_id, accepted = owners[0].get('config_entry_id'), 0
    for plan in plans:
        if not plan.blocked and not plan.changes:
            continue
        before = plan.before
        if (plan.changes or before.get('domain') != 'powercalc'
                or before.get('identity', {}).get('entry_id') != entry_id
                or before.get('data', {}).get('_energy_entity') != group.entity_id
                or before.get('data', {}).get('force_calculate_group_energy') is not False
                or len(plan.markers) != 2
                or {(marker.path, marker.code) for marker in plan.markers} != {
                    (('data', '_energy_entity'), 'unsupported_helper_reference'),
                    (('data', 'force_calculate_group_energy'), 'powercalc_group_membership_unverified')}):
            return False
        accepted += 1
    return accepted == 1


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
        mixed_powercalc = getattr(self, 'mixed_powercalc', False)
        pc_candidate = pc_original = pc_source = pc_energy = None
        root = Path(self.hass.config.config_dir).resolve()
        if mixed_powercalc:
            from homeassistant.const import __version__
            from homeassistant.components.energy.data import async_get_manager
            from .energy_native import NativeEnergy
            from .native_registry import NativeRegistryIO
            from .powercalc_runtime_observer import capture_powercalc_runtime
            from .powercalc_public_server import _read_original
            from .powercalc_public_candidate import build_candidate
            native_energy = NativeEnergy(self.hass, await async_get_manager(self.hass),
                                         allow_generated_costs=True)
            before_energy, proposed_energy = await native_energy.plan(self.mapping)
            self._scope()
            _need(proposed_energy is None or not proposed_energy.markers, 'powercalc_energy_coverage_unverified')
            if proposed_energy is not None and proposed_energy.changes:
                pc_energy = native_energy.prepare(before_energy, proposed_energy)
            self.powercalc_capture = capture_powercalc_runtime(self.hass, guard=self._scope)
            objects = json.loads(self.powercalc_capture.payload_json)['objects']
            rows = NativeRegistryIO(self.hass).document('entity')['entities']
            selected = {row['entity_id']: row for row in rows if row.get('entity_id') in self.mapping}
            sources = {source['entity_id'] for row in objects
                       if type(source := row.get('fields', {}).get('_source_entity')) is dict
                       and type(source.get('entity_id')) is str and source['entity_id'] in selected
                       and selected[source['entity_id']].get('platform') not in
                           ('powercalc', 'template', 'automation', 'script')}
            _need(len(sources) == 1, 'powercalc_source_unqualified')
            pc_source = next(iter(sources))
            pc_original, energy_raw = await _drained(self.hass.async_add_executor_job,
                lambda: _read_original(root, pc_energy is not None))
            self._scope()
        self.consumers = await _UnchangedConsumers.capture(self.hass, self.mapping, guard=self._scope,
            _powercalc_energy=pc_energy, _dashboard_effects=True)
        self._scope()
        dashboard_observed = self.consumers.dashboard_observed
        dashboard_storage = dashboard_yaml = None
        dashboard_routes = ()
        if dashboard_observed.dashboard.status == 'COMPLETE':
            from .dashboard_storage_semantics import qualify_dashboard_storage, async_prepare_dashboard_storage
            dashboard_routes = tuple(row.filename for row in dashboard_observed.dashboard.value.yaml_sources)
            changes = qualify_dashboard_storage(dashboard_observed, self.mapping)
            if changes or dashboard_routes:
                dashboard_storage = await async_prepare_dashboard_storage(
                    self.hass, dashboard_observed, self.mapping, guard=self._scope)
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
            inventory = NativeOwnIDInventory.capture(self.hass, {old: self.mapping[old]}, allow_running=True)
            service_audit = await NativeOwnScriptServiceAudit.capture(
                self.hass, inventory, guard=self._scope)
            service_recipients = await NativeOwnScriptServiceRecipients.capture(
                self.hass, inventory, guard=self._scope, scene_fence=self.scene_fence)
        # Qualify one numeric ConfigEntry before the shared source checker.
        # Other entries or blocked helpers retain their existing refusal.
        from .helper_native import inventory as helper_inventory
        from .controlled_template_entry_preparation import prepare_controlled_template_entry
        plans = helper_inventory(self.hass, self.mapping)
        template_plans = [item for item in plans if item.before.get('domain') == 'template']
        from .helper_native import UnsupportedEntryReferences, partition_entry_coverage
        other_plans = tuple(item for item in plans if item not in template_plans
                            and not (mixed_powercalc and item.before.get('domain') == 'powercalc'))
        blocked, self.config_entry_limitations = partition_entry_coverage(other_plans, self.mapping)
        if blocked:
            refusal = UnsupportedEntryReferences(other_plans, self.mapping)
            refusal.sources = blocked
            raise refusal
        entry_change = None
        if template_plans:
            _need(len(template_plans) == 1, 'template_entry_scope_unsupported')
            matched = []
            for source_id in self.mapping:
                try:
                    matched.append(prepare_controlled_template_entry(plans, self.mapping, source_id))
                except (ValueError, Refused):
                    pass
            _need(len(matched) == 1, 'template_entry_scope_unsupported')
            entry_change = matched[0]
            self.entry_change = entry_change
            entry_ref = (self.review_id or uuid.uuid4().hex) + '-entry'
            entry_payload = encode_change(entry_change)
            entry_digest = await self.preparations.create(entry_ref, entry_payload)
            self._scope()
            from .native_discovery_inputs import async_observe_discovery_inputs
            from .native_configentry_binding import bind_source_consumer
            discovered = await async_observe_discovery_inputs(self.hass, guard=self._scope)
            bind_source_consumer(discovered, plans)
            self._scope()
        root = Path(self.hass.config.config_dir).resolve()
        if mixed_powercalc:
            pc_candidate = build_candidate(pc_original, self.mapping, __version__,
                None if pc_energy is None else (energy_raw, pc_energy), mixed=True,
                source_id=pc_source, entity_names=self.entity_names, device_names=self.device_names,
                template_entry=entry_change,
                owned_script=None if not owned_script else (owned_script[0], inventory.default_name))
        yaml = await _drained(self.hass.async_add_executor_job,
            lambda: plan_yaml(root, self.mapping, owned_script_services=owned_script,
                              yaml_dashboards=dashboard_routes))
        self._scope()
        if mixed_powercalc:
            from .powercalc_profile_inputs import async_add_powercalc_source_inputs
            yaml = await async_add_powercalc_source_inputs(self.hass, yaml, guard=self._scope)
            self._scope()
        if dashboard_routes:
            from .dashboard_yaml_semantics import async_qualify_registered_dashboard_yaml
            dashboard_yaml = await async_qualify_registered_dashboard_yaml(
                self.hass, dashboard_observed, yaml, self.mapping, guard=self._scope)
        if inventory is not None:
            inventory.check()
            service_audit.check()
            service_recipients.check()
        if not yaml.ready and any(issue.code == 'template_requires_reference_parser' for issue in yaml.issues):
            _need(False, 'template_requires_reference_parser')
        _need(yaml.ready, 'yaml_proposal_required')
        registry_only = not yaml.patches
        try:
            if mixed_powercalc:
                _need(not registry_only, 'powercalc_yaml_effect_required')
                from .yaml_validation import async_validate_mixed_powercalc_template_sources
                bundle = await async_validate_mixed_powercalc_template_sources(
                    self.hass, yaml, self.mapping, root.parent, pc_candidate,
                    owned_script_services=owned_script, dashboard_yaml=dashboard_yaml,
                    dashboard_storage=dashboard_storage,
                    native_helpers=() if entry_change is None else ((entry_ref, entry_digest, entry_payload),))
            else:
                bundle = await async_validate_proposed_with_template_state_sources(
                    self.hass, yaml, self.mapping, root.parent, with_state_program=True,
                    registry_only_template=registry_only, with_dependent_program=bool(owned_script),
                    owned_script_services=owned_script, dashboard_yaml=dashboard_yaml,
                    dashboard_storage=dashboard_storage,
                    native_helpers=() if entry_change is None else ((entry_ref, entry_digest, entry_payload),))
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
        unsupported_own = any(row.get('entity_id') in self.mapping
                              and (row.get('platform') == 'template' or
                                   row.get('platform') == 'script' and row.get('entity_id') not in owned_script)
                              for row in source_rows)
        controlled_reload = bool(not unsupported_own and (mixed_powercalc or
            set(bundle.receipt.changed_domains) & {'automation', 'script'}))
        _need(entry_change is None or controlled_reload, 'entry_controlled_reload_required')
        if (not controlled_reload and not owned_script and (bundle.receipt.changed_domains in
                (('script', 'template'), ('automation', 'template'), ('automation', 'script', 'template'))
                or candidate_own and bundle.receipt.changed_domains in
                (('script',), ('automation',), ('automation', 'script')))):
            # Both programs come from one extended checked invocation, with
            # exact ORIGINAL equality to the preceding legacy admission.
            extended = await async_validate_proposed_with_template_state_sources(
                self.hass, yaml, self.mapping, root.parent, with_state_program=True,
                with_dependent_program=True, dashboard_yaml=dashboard_yaml,
                dashboard_storage=dashboard_storage)
            _need((extended.receipt, extended.evidence, extended.original_sources,
                   extended.candidate_sources, extended.state_config_program)
                  == (bundle.receipt, bundle.evidence, bundle.original_sources,
                      bundle.candidate_sources, bundle.state_config_program),
                  'script_program_original_changed')
            bundle = extended
        self.receipt, self.evidence, self.program = bundle.receipt, bundle.evidence, bundle.state_config_program
        self._scope()
        domains = tuple(domain for domain in self.receipt.changed_domains if domain != 'powercalc')
        selected_owners = [row for row in source_rows if row.get('entity_id') in self.mapping
                           and row.get('platform') in ('template', 'automation', 'script')]
        dependent_only = bool(domains) and 'template' not in domains and len(selected_owners) == 1
        if not controlled_reload and domains in (('script',), ('script', 'template')):
            from .native_script_config_transport import assert_idle_script_program_targets
            assert_idle_script_program_targets(self.hass, bundle.dependent_config_program)
        automation_prefix = not controlled_reload and domains in (('automation',), ('automation', 'script'),
                                        ('automation', 'template'), ('automation', 'script', 'template'))
        if automation_prefix:
            from .native_automation_config_transport import assert_idle_automation_program_targets
            assert_idle_automation_program_targets(self.hass, bundle.dependent_config_program)
        self.mixed_state = mixed_powercalc or domains not in ((), ('template',))
        mixed_source_names = (bool(self.entity_names) and not self.device_names
            and not set(self.entity_names) & set(self.mapping)
            and all(row.get('platform') not in ('automation', 'script')
                    for row in source_rows if row.get('entity_id') in self.entity_names))
        _need(not ((self.entity_names or self.device_names) and self.mixed_state)
              or controlled_reload or (dashboard_storage is not None and mixed_source_names),
              'selected_names_mixed_lifecycle_unsupported')
        if self.mixed_state:
            from .yaml_transaction import _assert_mixed_state_scope
            from .mixed_yaml_runtime import MixedYAMLConsumers
            if mixed_powercalc:
                from dataclasses import replace
                scoped = replace(self.receipt, changed_domains=domains,
                    unsupported_domains=tuple(domain for domain in self.receipt.unsupported_domains
                                              if domain != 'powercalc'),
                    runtime_expected=tuple(row for row in self.receipt.runtime_expected
                                           if row[0] not in ('powercalc', 'template.config_entries')))
                if domains not in ((), ('template',)):
                    _assert_mixed_state_scope(scoped, allow_dependent_only=True)
            else:
                _assert_mixed_state_scope(self.receipt, allow_dependent_only=dependent_only or controlled_reload,
                                          allow_template_entry=entry_change is not None)
            self.consumers = await MixedYAMLConsumers.capture(self.hass, self.mapping, guard=self._scope,
                _powercalc_energy=pc_energy, _dashboard_effects=dashboard_storage is not None)
            self.consumers.allow_controlled_reload = controlled_reload
            self._scope()
        _need((domains == () and registry_only or ('template' in domains or dependent_only or controlled_reload) and not registry_only)
              and bundle.original_sources.source_matched
              and bundle.candidate_sources.sources.source_matched, 'modern_template_state_sources_required')
        bind_sources = (self.consumers.bind_prefix_sources if not controlled_reload and (domains in (('script',), ('script', 'template')) or automation_prefix)
                        else self.consumers.bind_sources)
        await bind_sources(self.receipt, self.evidence, guard=self._scope)
        self._scope()
        if controlled_reload and ('automation' in domains or (owned_script and self.hass.data.get('automation') is not None) or any(
                row.get('entity_id') in self.mapping and row.get('platform') == 'automation' for row in source_rows)):
            from .native_automation_transition import NativeAutomationTransition
            transition = None
            try:
                transition = NativeAutomationTransition(self.hass, allow_running=True)
                await transition.prepare_preserving_enabled()
                self._scope()
            except Refused:
                raise Refused('controlled_automation_scope_unsupported') from None
            finally:
                if transition is not None:
                    transition.close_preserving_enabled()
        if self.entity_names or self.device_names:
            _need(controlled_reload or (self.mixed_state and dashboard_storage is not None and mixed_source_names)
                  or not self.consumers.runtime_sample[0], 'selected_names_dependent_lifecycle_unsupported')
        baseline = await async_capture_native_input_baseline(self.hass, self.receipt, guard=self._scope)
        self._scope()
        pc_previous = pc_metadata = pc_energy_physical = None
        if mixed_powercalc:
            _need(tuple((row.key, row.raw) for row in baseline.files) == pc_original,
                  'powercalc_original_changed')
            from .powercalc_source_binding import async_bind_powercalc_sources
            bound = await async_bind_powercalc_sources(self.hass, self.receipt, self.evidence,
                                                       self.powercalc_capture, guard=self._scope)
            kinds = [row.relation_kind for row in bound.rows]
            _need(all(row.source_verified for row in bound.rows) and kinds.count('profile_lut') == 2
                  and kinds.count('power_sum') == 1 and kinds.count('energy_positive_delta') == 1
                  and (kinds.count('energy_integral'), kinds.count('power_subtract')) in ((2, 0), (3, 1)),
                  'powercalc_graph_unsupported')
            from .powercalc_previous_state_original import async_capture_powercalc_previous_original
            pc_previous = await async_capture_powercalc_previous_original(
                self.hass, self.receipt, self.evidence, self.powercalc_capture, guard=self._scope,
                allow_live_progress=True)
            _need(len(pc_previous.groups) == 1 and pc_previous.groups[0].entity_id in self.mapping,
                  'powercalc_group_required')
            from .controlled_powercalc_population import qualify_population
            self.powercalc_population = qualify_population(self.hass, bound, self.mapping, pc_previous)
            if pc_energy is not None:
                from .powercalc_energy_receipts import _read_physical
                pc_energy_physical = await _drained(self.hass.async_add_executor_job, lambda: _read_physical(
                    self.receipt.config_root, pc_energy.before.file_identity,
                    pc_previous.root_identity, pc_previous.storage_identity))
                self._scope()
        documents = {f.key: json.loads(f.raw) for f in baseline.files}
        entries = documents['core.config_entries']['data']['entries']
        _need(entry_change is not None or not any(row['domain'] == 'template' for row in entries), 'template_entry_unsupported')
        # Other native helper references also require their own lifecycle route.
        rows = documents['core.entity_registry']['data']['entities']
        _need((_powercalc_group_helper_coverage(tuple(item for item in plans if item not in template_plans), rows, pc_previous, self.mapping) if mixed_powercalc
               else True), 'helper_dependency_unsupported')
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
            if mixed_powercalc and source[0].get('platform') == 'powercalc':
                from .powercalc_group_registry_preparation import prepare_group_registry
                from .powercalc_group_metadata import prepare_group_metadata
                from .helper_native import read_entry
                _need(old == pc_previous.groups[0].entity_id, 'powercalc_group_scope')
                prepared = prepare_group_registry(prepared, pc_previous.groups[0], self.receipt.version)
                pc_metadata = prepare_group_metadata(read_entry(self.hass, source[0]['config_entry_id']),
                                                     old, new)
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
                  and (not self.entity_names or controlled_reload and not own_template
                       and set(self.entity_names) <= set(self.mapping))
                  and (mixed_powercalc or not self.device_names), 'own_entity_scope_unsupported')
            if own_template:
                from .native_own_id_this import qualify_template_own_changes
                qualify_template_own_changes(self.hass, registry, guard=self._scope)
            else:
                from .native_own_dependent_fence import qualify_dependent_own_changes
                qualify_dependent_own_changes(self.hass, own_dependent if mixed_powercalc else registry, guard=self._scope,
                                              native_readd=controlled_reload)
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
            if mixed_powercalc:
                _need(not any(row.get('device_id') == identity and row.get('platform') == 'powercalc'
                              for row in rows), 'powercalc_device_owner_unsupported')
            if source[0].get('name_by_user') != name:
                registry.append(await bridge.prepare('device', identity, {'name_by_user': name}, self._scope))
                self._scope()
        if mixed_powercalc:
            _need(pc_metadata is not None, 'powercalc_metadata_required')
            from .native_template_state_yaml import capture_mixed_powercalc_yaml
            capture = capture_mixed_powercalc_yaml
        elif self.mixed_state:
            from .native_template_state_yaml import capture_mixed_template_state_yaml
            capture = capture_mixed_template_state_yaml
        else:
            capture = capture_template_state_yaml
        change = (None if registry_only else
                  await _drained(self.hass.async_add_executor_job, lambda: capture(bundle, yaml, self.mapping)))
        self._scope()
        _need(bool(registry), 'registry_change_required')
        self.registry_changes = tuple(registry)
        self.change = registry[-1] if registry_only else change
        own = self.review_id or uuid.uuid4().hex
        self.operation_id = 'template-yaml-' + (self.review_id or uuid.uuid4().hex)
        self.digests, steps = ({} if entry_change is None else {entry_ref: entry_digest}), []
        self.dashboard_changes = () if dashboard_storage is None else dashboard_storage.changes
        effects = ([('registry', c) for c in registry]
                   + ([] if entry_change is None else [('helpers', entry_change)])
                   + ([] if pc_metadata is None else [('helpers', pc_metadata)])
                   + ([] if pc_energy is None else [('energy', pc_energy)])
                   + [('dashboard', c) for c in self.dashboard_changes]
                   + ([] if registry_only else [('yaml', change)]))
        for index, (adapter, item) in enumerate(effects):
            ref = entry_ref if item is entry_change else own + '-' + str(index)
            if item is not entry_change:
                self.digests[ref] = await self.preparations.create(ref, encode_change(item))
            self._scope()
            if adapter == 'dashboard':
                steps.append(Step(ref, adapter, item.before.dashboard.identity,
                                  item.before.revision, item.after_revision, ref))
            elif adapter == 'energy':
                steps.append(Step(ref, adapter, 'energy', item.before.revision, item.after_revision, ref))
            else:
                steps.append(Step(ref, adapter, item.identity, item.before_revision, item.after_revision, ref))
        from .native_template_state_binding import _StateRuntime, StateBindingPrefix, sample_state_bindings
        native = _StateRuntime(self.hass)
        await _drained(self.hass.async_add_executor_job, native.sources)
        self._scope()
        prefix = StateBindingPrefix(self.program.sha256, self.operation_id,
            'source-preflight', baseline.digest, frozenset(),
            tuple(sorted((c.identity, next(iter(c.mapping))) for c in self.registry_changes if c.mapping)))
        if entry_change is not None:
            from .controlled_template_entry_witness import ControlledTemplateEntryWitness
            source = next(iter(entry_change.mapping))
            selected = [row for row in rows if row['entity_id'] == source]
            _need(len(selected) == 1, 'entry_source_uuid_required')
            self.entry_witness = ControlledTemplateEntryWitness(self.hass, entry_change,
                native.native, selected[0]['id'], guard=self._scope)
        observed = sample_state_bindings(native, self.program, prefix, 'original', documents,
                                         entry_witness=getattr(self, 'entry_witness', None))
        # Registry and physical consumers retain their actual original IDs.
        physical = tuple(sorted((r.domain.removeprefix('template.'), r.source_key, r.entity.entity_id)
            for r in observed.rows if (r.domain, r.source_key) in self.program.physical_keys))
        consumer_ids = {r['entity_id']: r['entity_id']
            for r in rows if r['platform'] == 'template'}
        consumer_ids.update({entity_id: entity_id for _, _, entity_id in physical})
        if self.mixed_state:
            dependent_sample = (self.consumers.runtime_sample if not controlled_reload and (domains in (('script',), ('script', 'template')) or automation_prefix)
                                else self.consumers.phases.sample)
            consumer_ids.update({row.entity_id: row.entity_id for row in dependent_sample[0]})
        source_history = await self.history.capture(_registry_history_mapping(self.registry_changes))
        self._scope()
        consumer_history = await self.history.capture(consumer_ids)
        self._scope()
        script_prefix = not controlled_reload and self.receipt.changed_domains in (('script',), ('script', 'template'))
        from .controlled_powercalc_composition import ControlledPowercalcComposition
        composition_type = (ControlledPowercalcComposition if mixed_powercalc else
                            ControlledScriptComposition if controlled_reload and owned_script else
                            ControlledReloadEntryComposition if entry_change is not None else
                            ControlledReloadComposition if controlled_reload else
                            AutomationTemplateStateComposition if automation_prefix else
                            ScriptTemplateStateComposition if script_prefix else
                            MixedTemplateStateComposition if self.mixed_state else TemplateStateComposition)
        self.composition = composition_type(self.evidence, bundle.original_sources,
            bundle.candidate_sources, self.program, yaml, tuple(sorted(self.mapping.items())),
            tuple(sorted(self.digests.items())), source_history, consumer_history,
            self.program.sha256, physical,
            **({'dependent_config_program': bundle.dependent_config_program} if script_prefix or automation_prefix or controlled_reload and owned_script else {}),
            **({'previous': pc_previous, 'energy_original_physical': pc_energy_physical,
                'population': self.powercalc_population.descriptors} if mixed_powercalc else {}))
        inputs = await self.preparations.create_native_inputs(own + '-original', baseline,
                                                             composition=self.composition)
        self._scope()
        scopes = ((('registry', 'helpers') + (('energy',) if pc_energy else ())
                   + (('dashboard',) if dashboard_storage is not None else ()) + ('yaml',)) if mixed_powercalc else
                  ('registry', 'dashboard') if registry_only and dashboard_storage is not None else
                  ('registry', 'dashboard', 'yaml') if dashboard_storage is not None and not registry_only else
                  ('registry',) if registry_only else ('registry', 'yaml'))
        if entry_change is not None and not mixed_powercalc:
            scopes = tuple(scope for scope in ('registry', 'helpers', 'dashboard', 'yaml')
                           if scope in scopes or scope == 'helpers')
        self.plan = Plan(own, scopes, scopes, tuple(steps), self.program.sha256, scopes, inputs)
        self.plan.validate()
        self.receipt_store = native_registry_receipt_store(self.hass, self.preparations)
        await self.receipt_store.initialize()
        self._scope()
        self.chain = await RegistryReceiptChain.start(self.receipt_store, self.operation_id,
            self.plan, self.receipt, self.digests)
        self._scope()
        if dashboard_storage is not None:
            await self._install_dashboard_fence()
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
        _need(type(self.composition) in (TemplateStateComposition, MixedTemplateStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition, ControlledScriptComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition, ControlledPowercalcComposition), 'state_composition_required')
        self.program = self.composition.state_config_program
        if (type(self.composition) in (ControlledReloadEntryComposition, ControlledPowercalcComposition)
                or type(self.composition) is ControlledScriptComposition and 'helpers' in self.plan.required_scopes):
            from .native_template_entry_lifecycle import validate_preparations
            from .controlled_template_entry_witness import ControlledTemplateEntryWitness
            from .native_template_consumers import _Native
            changes = validate_preparations(self.preparations, self.plan, self.receipt, self.digests)
            _need(len(changes) <= 1 and (changes or type(self.composition) is ControlledPowercalcComposition),
                  'entry_preparation_required')
            if changes:
                self.entry_change = next(iter(changes.values()))
                source = next(iter(self.entry_change.mapping))
                docs = {item.key: json.loads(item.raw) for item in self.chain.baseline.files}
                source_rows = [row for row in docs['core.entity_registry']['data']['entities']
                               if row['entity_id'] == source]
                _need(len(source_rows) == 1, 'entry_source_uuid_required')
                self.entry_witness = ControlledTemplateEntryWitness(self.hass, self.entry_change,
                    _Native(self.hass), source_rows[0]['id'], guard=self._scope)
        if any(change.before.get('platform') == 'template' and change.mapping
               for change in self.registry_changes):
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
            direct_readd = type(self.composition) in (ControlledReloadComposition, ControlledReloadEntryComposition, ControlledScriptComposition, ControlledPowercalcComposition)
            _need(direct_readd or type(self.composition) in (TemplateStateComposition, ScriptTemplateStateComposition,
                                             AutomationTemplateStateComposition), 'own_dependent_profile_unsupported')
            from .native_own_dependent_fence import NativeOwnDependentFence, qualify_dependent_own_changes
            change = qualify_dependent_own_changes(self.hass, own_dependent if type(self.composition) is ControlledPowercalcComposition else self.registry_changes, guard=self._scope,
                                                   native_readd=direct_readd)
            if type(self.composition) in (ScriptTemplateStateComposition, AutomationTemplateStateComposition):
                from .dependent_config_binding import NativeDependentConfigReadback
                dependent = NativeDependentConfigReadback(self.hass, self.composition.dependent_config_program,
                    self.evidence, self.receipt, self.composition.yaml_plan,
                    owned_automation=getattr(self.consumers, 'owned_automation', None))
                sample = dependent.sample(self.chain, input_state=self.observation_state)
                initial = sample.registry_rows, (), sample.native_tokens
            else:
                from .yaml_source_binding import _runtime_rows_sample
                # A fresh inverse binds the accepted AFTER program; the sealed
                # receipt, not a new snapshot, supplies its expected projection.
                record = getattr(self, 'record', None)
                after = direct_readd and record is not None and record.direction == 'forward'
                projections = (tuple((domain, candidate) for domain, _, candidate in self.receipt.runtime_expected)
                               if after else ())
                script_moves = ()
                if after and is_controlled_script(self.composition):
                    script_moves = tuple(('script', old.split('.', 1)[1], new.split('.', 1)[1])
                                         for old, new in self.composition.mapping
                                         if old.startswith('script.') and new.startswith('script.'))
                initial = _runtime_rows_sample(self.hass, self.evidence,
                    self.chain.accepted_documents()['core.entity_registry']['data']['entities'],
                    expected_projections=projections,
                    owned_automation=getattr(self.consumers, 'owned_automation', None),
                    source_key_moves=script_moves)
            self.own_dependent_fence = NativeOwnDependentFence(self, change, initial, native_readd=direct_readd)
            self.consumers.own_fence = self.own_dependent_fence
            phases = getattr(self.consumers, 'phases', None)
            if is_controlled_script(self.composition) and phases is not None:
                from .controlled_script_phases import ControlledScriptPhases
                self.consumers.phases = ControlledScriptPhases(self, self.consumers.observed,
                    position=Position.AFTER if after else Position.BEFORE)
            elif direct_readd and phases is not None:
                phases.own_fence = self.own_dependent_fence
                phases.check()
            from .native_own_dependent_fence import NativeOwnDependentAdmission
            self.own_record_admission = NativeOwnDependentAdmission(self.own_dependent_fence)
            self.own_record_head = self.observation_state.head
        if type(self.composition) in (ScriptTemplateStateComposition, AutomationTemplateStateComposition):
            self.consumers.bind_prefix_phases(self)
        _need(self.composition.mapping == tuple(sorted(self.mapping.items())), 'original_mapping_changed')
        if not self.receipt.proposed_files:
            from .native_own_id_registry_readback import NativeOwnIDRegistryReadback
            return NativeOwnIDRegistryReadback(self.hass, self.chain, self.composition, guard=self._scope)
        if type(self.composition) in (MixedTemplateStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition, ControlledScriptComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition, ControlledPowercalcComposition):
            from .native_template_mixed_state_yaml import NativeMixedTemplateStateYAMLRuntimeReadback
            return NativeMixedTemplateStateYAMLRuntimeReadback(self.hass, self.chain,
                self.composition, self.consumers, guard=self._scope,
                entry_witness=getattr(self, 'entry_witness', None))
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
        if type(self.composition) in (MixedTemplateStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition, ControlledScriptComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition, ControlledPowercalcComposition):
            self.observer.assert_dependents_position(wanted)
        actual = await async_observe_accepted_state_bindings(self.hass, self.chain,
            self.program, guard=self._scope, entry_witness=getattr(self, 'entry_witness', None))
        self._scope()
        expected_active = frozenset() if rollback or initial else frozenset(self.mapping)
        _need(actual.prefix.active == expected_active
              and actual.yaml_position == ('original' if rollback or initial or not self.receipt.proposed_files else 'proposed'),
              'terminal_prefix_unconfirmed')
        expected = self.observer.identities_at(actual.prefix.active)
        _need(all((r.entity.entity_id, r.registry_id) in expected for r in actual.rows),
              'consumer_identity_changed')
        if hasattr(self, 'dashboard_phase'):
            await self.dashboard_phase.verify_yaml(state, self.composition.yaml_plan, self.mapping,
                self.change if self.receipt.proposed_files else None,
                original=rollback or initial, guard=self._scope)
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
        if type(self.composition) is ControlledPowercalcComposition:
            from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
            from .powercalc_runtime_observer import capture_powercalc_runtime
            captured = capture_powercalc_runtime(self.hass, guard=self._scope)
            actual = await async_observe_accepted_powercalc_consumers(
                self.hass, self.chain, self.evidence, captured, guard=self._scope)
            expected = (self.composition.population if rollback else tuple(sorted(
                (self.mapping.get(name, name), uid, kind)
                for name, uid, kind in self.composition.population)))
            _need(tuple(sorted((row.entity_id, row.registry_id, row.relation_kind)
                               for row in actual.rows)) == expected, 'powercalc_runtime_graph_changed')
        return True

    def _coordinator(self):
        from homeassistant.core import Context
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .native_registry_transaction import ReceiptedRegistryTransactionAdapter
        from .native_template_state_yaml import (ReceiptedTemplateStateYAMLTransactionAdapter,
                                                 compose_template_state_yaml_coordinator)
        resolver = lambda ref: decode_change(self.preparations.resolve(ref, self.digests[ref]))
        controlled = type(self.composition) is ControlledPowercalcComposition
        handoff = None
        if controlled:
            from .controlled_powercalc_handoff import ControlledPowercalcHandoff
            handoff = ControlledPowercalcHandoff(self, self.powercalc_population)
        io = NativeRegistryIO(self.hass)
        def runtime_probe(change, target):
            if (controlled and handoff.receipt is not None and handoff.setup_reference is None
                    and change.kind == 'entity' and change.before.get('platform') == 'powercalc'):
                from homeassistant.const import STATE_UNAVAILABLE
                from .native_helper_placeholder_effect import _attrs, _state_shape
                handoff.require_inactive()
                row = io.registries['entity'].async_get(target['entity_id'])
                state = self.hass.states.get(target['entity_id'])
                if row is None or row.id != change.identity or state is None or state.state != STATE_UNAVAILABLE:
                    return False
                if change.mapping:
                    other = change.after if target == change.before else change.before
                    if self.hass.states.get(other['entity_id']) is not None:
                        return False
                try:
                    _state_shape(state.as_dict_json.decode(), target['entity_id'],
                                 _attrs(row, self.chain.version, self.hass))
                except Exception:
                    return False
                return True
            return io.runtime(change, target, expected_documents=self.chain.accepted_documents())
        bridge = NativeRegistryBridge(io, self.recorder, runtime_probe=runtime_probe,
                                      native_images=self.chain.accepted_documents,
                                      automation_transition=getattr(self, 'controlled_automation', None))
        registry = ReceiptedRegistryTransactionAdapter(bridge, resolver, self.chain, self._observe,
            self.journal.load, self._scope, full_verify=lambda _: self._common(False),
            full_verify_rollback=lambda _: self._common(True))
        registry.bind_template_state_root(self)
        if controlled:
            registry.bind_controlled_powercalc(handoff)
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
            adapters = {'registry': registry}
            if hasattr(self, 'dashboard_phase'):
                from .dashboard_transaction import ReceiptedDashboardAdapter
                adapters['dashboard'] = ReceiptedDashboardAdapter(
                    self.dashboard_phase.native, self.dashboard_phase, self.chain,
                    self.journal.load, self._scope, lambda _: self._common(False),
                    lambda _: self._common(True))
            return Coordinator(adapters, self.journal, self._scope,
                               required_scopes=self.plan.required_scopes, record_admission=record_admission)
        adapter_type, compose = ReceiptedTemplateStateYAMLTransactionAdapter, compose_template_state_yaml_coordinator
        if type(self.composition) in (MixedTemplateStateComposition, ControlledReloadComposition, ControlledReloadEntryComposition, ControlledScriptComposition, ScriptTemplateStateComposition, AutomationTemplateStateComposition, ControlledPowercalcComposition):
            from .native_template_mixed_state_yaml import (ReceiptedMixedTemplateStateYAMLTransactionAdapter,
                compose_mixed_template_state_yaml_coordinator)
            adapter_type, compose = ReceiptedMixedTemplateStateYAMLTransactionAdapter, compose_mixed_template_state_yaml_coordinator
        if controlled:
            from .native_template_mixed_state_yaml import ReceiptedControlledPowercalcYAMLAdapter
            adapter_type = ReceiptedControlledPowercalcYAMLAdapter
        yaml = adapter_type(self.hass, resolver, self.locks,
            self.observer, self.chain, self._observe, self.journal.load, self._scope, context=Context(),
            full_verify=lambda _: self._full(False), full_verify_rollback=lambda _: self._full(True),
            **({'handoff': handoff} if controlled else {}))
        admission = {} if record_admission is None else {'record_admission': record_admission}
        adapters = {'registry': registry, 'yaml': yaml}
        if (type(self.composition) is ControlledReloadEntryComposition
                or type(self.composition) is ControlledScriptComposition and 'helpers' in self.plan.required_scopes):
            from .controlled_template_entry_adapter import create_adapter
            adapters['helpers'] = create_adapter(self, resolver)
        if controlled and 'helpers' in self.plan.required_scopes:
            from .controlled_powercalc_helper import ControlledPowercalcHelperAdapter, ControlledMixedHelperAdapter
            powercalc_helper = ControlledPowercalcHelperAdapter(self, handoff, resolver)
            if getattr(self, 'entry_change', None) is not None:
                from .controlled_template_entry_adapter import create_adapter
                adapters['helpers'] = ControlledMixedHelperAdapter(resolver, powercalc_helper,
                                                                   create_adapter(self, resolver))
            else:
                adapters['helpers'] = powercalc_helper
        if controlled and 'energy' in self.plan.required_scopes:
            from .powercalc_energy_transaction import ReceiptedPowercalcEnergyAdapter
            _need(hasattr(self, 'native_energy'), 'powercalc_energy_not_bound')
            adapters['energy'] = ReceiptedPowercalcEnergyAdapter(self.native_energy, handoff,
                self.journal.load, lambda _: self._full(False), lambda _: self._full(True))
        if hasattr(self, 'dashboard_phase'):
            from .dashboard_transaction import ReceiptedDashboardAdapter
            adapters['dashboard'] = ReceiptedDashboardAdapter(
                self.dashboard_phase.native, self.dashboard_phase, self.chain,
                self.journal.load, self._scope, lambda _: self._common(False),
                lambda _: self._common(True))
        if is_controlled_script(self.composition) and not controlled:
            admission['rollback_order'] = self.chain._script_reverse_order
        coordinator = compose(adapters, self.journal, self._scope,
            required_scopes=self.plan.required_scopes, **admission)
        if controlled:
            from .controlled_powercalc_driver import attach, inverse
            coordinator = attach(coordinator, handoff)
            self.powercalc_handoff = handoff
            if hasattr(self, 'record'):
                coordinator = inverse(coordinator, self, handoff)
        return coordinator
