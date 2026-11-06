"""Server-owned ordinary Powercalc review and fresh apply admission.

Only a fully accounted source plus group move is admitted.  The client supplies
IDs, never native documents, preparations, receipts, or a writer capability.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from .const import DATA_RUNTIME
from .native_input_baseline import _drained, _KEYS, async_capture_native_input_baseline
from .native_registry_receipts import RegistryReceiptChain, native_registry_receipt_store
from .native_template_forward import _UnchangedConsumers
from .native_template_reverse import _authorize
from .powercalc_composition import PowercalcComposition
from .powercalc_public_candidate import build_candidate
from .prepared import native_prepared_store, encode_change
from .journal import native_journal
from .transaction import Plan, Step, Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_public_' + reason)


def _read_original(root, include_energy):
    from .yaml_plan import _Reader, Limits
    from .native_input_baseline import MAX_FILE_BYTES, MAX_TOTAL_BYTES
    reader = _Reader(root, Limits(max_file_bytes=MAX_FILE_BYTES))
    try:
        rows = tuple((key, reader.read('.storage/' + key)[0]) for key in _KEYS)
        energy = reader.read('.storage/energy')[0] if include_energy else None
        _need(sum(len(raw) for _, raw in rows) <= MAX_TOTAL_BYTES, 'native_input_size')
        return rows, energy
    finally:
        os.close(reader.root)


class PowercalcPublicServer:
    def __init__(self):
        raise Refused('powercalc_public_installed_scope_required')

    @classmethod
    async def _open(cls, hass, *, guard, refresh):
        from . import Runtime
        from .native_empty_scene import NativeEmptySceneFence
        from .recorder_guard import NativeRecorderGuard
        from .history_guard import NativeHistoryGuard
        from .yaml_editor_locks import NativeYAMLEditorLocks
        from homeassistant.core import HomeAssistant
        runtime = hass.data.get(DATA_RUNTIME)
        _need(cls is PowercalcPublicServer and type(hass) is HomeAssistant and type(runtime) is Runtime
              and callable(guard) and callable(refresh) and runtime.operation_lock.locked(),
              'installed_scope_required')
        obj = object.__new__(cls)
        obj.hass, obj.runtime, obj.guard, obj.refresh = hass, runtime, guard, refresh
        obj.owner, obj.lock = asyncio.current_task(), runtime.operation_lock
        obj.closed, obj.driver, obj.consumers, obj.energy = False, None, None, None
        obj.scene = NativeEmptySceneFence(hass)
        try:
            obj._scope()
            await refresh()
            obj._scope()
            obj.preparations, obj.journal = native_prepared_store(hass), native_journal(hass)
            await obj.preparations.initialize()
            obj._scope()
            await obj.journal.initialize()
            obj._scope()
            _need(not await obj.journal.active(), 'other_operation_unresolved')
            obj._scope()
            from .native_automation_lifecycle import async_assert_no_pending_automation_lifecycle
            await async_assert_no_pending_automation_lifecycle(hass, obj.preparations, obj.journal)
            obj._scope()
            obj.recorder = NativeRecorderGuard(hass)
            obj.history = NativeHistoryGuard(obj.recorder)
            obj.locks = NativeYAMLEditorLocks(hass)
            return obj
        except BaseException:
            obj.scene.close()
            raise

    def _scope(self):
        _authorize(self.guard)
        _need(not self.closed and self.owner is not None and not self.owner.done()
              and self.hass.data.get(DATA_RUNTIME) is self.runtime and self.runtime.loaded
              and not self.runtime.unloading and self.runtime.operation_lock is self.lock
              and self.lock.locked() and 'powercalc' in self.hass.config.components
              and not ({'mqtt', 'rest', 'command_line'} & set(self.hass.config.components)),
              'runtime_scope_lost')
        self.scene.check()
        if self.consumers is not None:
            self.consumers.check()
        if self.energy is not None:
            from .native_discovery_consumers import _energy_manager
            _need(_energy_manager(self.hass) is self.energy.manager
                  and self.energy.manager._store is self.energy._store, 'energy_identity_changed')

    @classmethod
    async def prepare(cls, hass, mapping, *, guard, refresh, entity_names=None, device_names=None,
                      review_id=None):
        from .manifest import parse_manifest
        from .review_retention import review_lease
        _need(type(mapping) is dict and len(mapping) == 2 and not entity_names and not device_names
              and type(review_id) is str, 'source_group_selection_required')
        review_lease(review_id, 'server')
        parsed = parse_manifest({'entities': [{'entity_id': old, 'proposed_entity_id': new}
                                              for old, new in sorted(mapping.items())]})
        _need(len(parsed.entities) == 2, 'source_group_selection_required')
        obj = await cls._open(hass, guard=guard, refresh=refresh)
        obj.mapping, obj.review_id = dict(mapping), review_id
        try:
            async with obj.locks.hold(obj._scope):
                await obj._prepare()
            obj._scope()
            return obj
        except BaseException:
            obj.close()
            raise

    async def _prepare(self):
        from homeassistant.const import __version__
        from .native_discovery_consumers import _energy_manager
        from .energy_native import NativeEnergy
        from .powercalc_runtime_observer import capture_powercalc_runtime
        from .yaml_plan import plan_yaml
        from .powercalc_profile_inputs import async_add_powercalc_source_inputs
        from .yaml_validation import async_validate_proposed_with_powercalc_sources
        from .powercalc_previous_state_original import async_capture_powercalc_previous_original
        from .native_registry import NativeRegistryBridge, NativeRegistryIO
        from .powercalc_group_registry_preparation import prepare_group_registry
        from .powercalc_group_metadata import prepare_group_metadata
        from .helper_native import read_entry
        from .powercalc_prepared_yaml import capture_powercalc_yaml
        from .powercalc_energy_receipts import _read_physical

        self._scope()
        root = Path(self.hass.config.config_dir)
        _need(root.is_absolute() and root == root.resolve(), 'native_root_required')
        # Energy may have durable consumers even before its lazy manager has
        # been requested since startup. Load the native store before deciding
        # whether this mapping has an Energy dependency.
        from homeassistant.components.energy.data import async_get_manager
        await async_get_manager(self.hass)
        self._scope()
        energy_prepared = None
        self.energy = NativeEnergy(self.hass, _energy_manager(self.hass))
        before, proposed = await self.energy.plan(self.mapping)
        self._scope()
        if proposed is not None:
            _need(not proposed.markers, 'energy_coverage_unverified')
            if proposed.changes:
                energy_prepared = self.energy.prepare(before, proposed)
        original, energy_raw = await _drained(self.hass.async_add_executor_job,
            lambda: _read_original(root, energy_prepared is not None))
        self._scope()
        candidate = build_candidate(original, self.mapping, __version__,
                                    None if energy_prepared is None else (energy_raw, energy_prepared))
        self.consumers = await _UnchangedConsumers.capture(self.hass, self.mapping, guard=self._scope,
                                                           _powercalc_energy=energy_prepared)
        self._scope()
        self.capture = capture_powercalc_runtime(self.hass, guard=self._scope)
        yaml = await _drained(self.hass.async_add_executor_job, lambda: plan_yaml(root, self.mapping))
        self._scope()
        _need(yaml.ready and bool(yaml.patches), 'yaml_proposal_required')
        yaml = await async_add_powercalc_source_inputs(self.hass, yaml, guard=self._scope)
        self.receipt, self.evidence = await async_validate_proposed_with_powercalc_sources(
            self.hass, yaml, root.parent, candidate_documents=candidate)
        self._scope()
        _need(self.receipt.changed_domains == ('powercalc',)
              and self.receipt.unsupported_domains == ('powercalc',), 'mixed_scope_unsupported')
        await self.consumers.bind_sources(self.receipt, self.evidence, guard=self._scope)
        self._scope()
        # The actual reload handoff currently qualifies exactly this native
        # energy graph. Reject other concrete graphs in review rather than
        # issuing an ordinary plan that can only fail after an Apply intent.
        from .powercalc_source_binding import async_bind_powercalc_sources
        bound = await async_bind_powercalc_sources(
            self.hass, self.receipt, self.evidence, self.capture, guard=self._scope)
        self._scope()
        kinds = [row.relation_kind for row in bound.rows]
        _need(all(row.source_verified for row in bound.rows)
              and kinds.count('profile_lut') == 2 and kinds.count('power_sum') == 1
              and kinds.count('energy_positive_delta') == 1
              and (kinds.count('energy_integral'), kinds.count('power_subtract')) in ((2, 0), (3, 1)),
              'graph_unsupported')
        baseline = await async_capture_native_input_baseline(self.hass, self.receipt, guard=self._scope)
        _need(tuple((row.key, row.raw) for row in baseline.files) == original, 'original_documents_changed')
        previous = await async_capture_powercalc_previous_original(
            self.hass, self.receipt, self.evidence, self.capture, guard=self._scope)
        self._scope()
        _need(len(previous.groups) == 1, 'one_group_scope_required')
        group = previous.groups[0]
        _need(group.entity_id in self.mapping, 'group_selection_required')
        source_ids = [name for name in self.mapping if name != group.entity_id]
        _need(len(source_ids) == 1, 'source_selection_required')
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(self.hass)
        source_row = registry.async_get(source_ids[0])
        _need(source_row is not None and source_row.platform not in
              ('powercalc', 'template', 'automation', 'script'), 'source_identity_required')
        bridge = NativeRegistryBridge(NativeRegistryIO(self.hass), self.recorder)
        source = await bridge.prepare('entity', source_row.id,
                                      {'entity_id': self.mapping[source_ids[0]]}, self._scope)
        group_change = await bridge.prepare('entity', group.registry_id,
            {'entity_id': self.mapping[group.entity_id]}, self._scope)
        group_change = prepare_group_registry(group_change, group, self.receipt.version)
        entry_id = group_change.before['config_entry_id']
        metadata = prepare_group_metadata(read_entry(self.hass, entry_id), group.entity_id,
                                          self.mapping[group.entity_id])
        yaml_change = await _drained(self.hass.async_add_executor_job,
            lambda: capture_powercalc_yaml(self.receipt, self.evidence, yaml))
        self._scope()
        self.registry_changes, self.yaml_plan = (source, group_change), yaml
        ordered = ([] if energy_prepared is None else [('energy', energy_prepared)])
        ordered += [('registry', source), ('registry', group_change), ('helpers', metadata), ('yaml', yaml_change)]
        steps, digests = [], {}
        for index, (adapter, change) in enumerate(ordered):
            ref = self.review_id + '-' + str(index)
            digests[ref] = await self.preparations.create(ref, encode_change(change))
            self._scope()
            steps.append(Step(ref, adapter, change.identity if adapter != 'energy' else 'energy',
                              change.before_revision if adapter != 'energy' else change.before.revision,
                              change.after_revision, ref))
        physical = None
        if energy_prepared is not None:
            physical = await _drained(self.hass.async_add_executor_job, lambda: _read_physical(
                self.receipt.config_root, energy_prepared.before.file_identity,
                previous.root_identity, previous.storage_identity))
        source_history = json.loads(source.history_before_json)
        group_history = json.loads(group_change.history_before_json)
        composition = PowercalcComposition(self.evidence, previous, tuple(sorted(digests.items())),
            source_history, group_history, self.evidence.powercalc_sources.sha256, physical)
        inputs = await self.preparations.create_native_inputs(self.review_id + '-original', baseline,
                                                               composition=composition)
        scopes = tuple(name for name in ('registry', 'helpers', 'energy', 'yaml')
                       if any(adapter == name for adapter, _ in ordered))
        self.operation_id = 'template-yaml-' + self.review_id
        self.plan = Plan(self.review_id, scopes, scopes, tuple(steps), composition.coverage_sha256,
                         scopes, inputs)
        self.plan.validate()
        self.receipts = native_registry_receipt_store(self.hass, self.preparations)
        await self.receipts.initialize()
        self.chain = await RegistryReceiptChain.start(self.receipts, self.operation_id, self.plan,
                                                       self.receipt, digests)
        self._scope()
        from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
        bound = await async_observe_accepted_powercalc_consumers(
            self.hass, self.chain, self.evidence, self.capture, guard=self._scope)
        _need(bound.source_position == 'original' and all(row.source_verified for row in bound.rows),
              'original_graph_unverified')
        from .dependency_review import dependency_files
        self.dependency_files = dependency_files(SimpleNamespace(mapping=self.mapping, yaml_plan=yaml))
        self.runtime.preparations, self.runtime.journal = self.preparations, self.journal
        self._scope()

    @classmethod
    async def bind_prepared(cls, hass, operation_id, plan, *, guard, refresh):
        from .powercalc_runtime_observer import capture_powercalc_runtime
        from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
        from .prepared import decode_change
        from .energy_native import NativeEnergy, PreparedEnergy
        from .native_discovery_consumers import _energy_manager
        obj = await cls._open(hass, guard=guard, refresh=refresh)
        try:
            obj.operation_id, obj.plan = operation_id, plan
            plan.validate()
            baseline, composition = obj.preparations.resolve_composition(plan.native_inputs)
            _need(type(composition) is PowercalcComposition, 'composition_required')
            obj.receipt, obj.evidence = baseline.receipt, composition.evidence
            digests = dict(composition.prepared_digests)
            changes = [decode_change(obj.preparations.resolve(step.prepared_ref, digests[step.prepared_ref]))
                       for step in plan.steps]
            registry = [change for step, change in zip(plan.steps, changes, strict=True)
                        if step.adapter == 'registry']
            obj.mapping = {old: new for change in registry for old, new in change.mapping.items()}
            _need(len(obj.mapping) == 2, 'source_group_selection_required')
            energy = [change for change in changes if type(change) is PreparedEnergy]
            _need(len(energy) <= 1, 'energy_preparation_required')
            obj.consumers = await _UnchangedConsumers.capture(hass, obj.mapping, guard=obj._scope,
                                                              _powercalc_energy=energy[0] if energy else None)
            if energy:
                obj.energy = NativeEnergy(hass, _energy_manager(hass))
                _need(await obj.energy.read() == energy[0].before, 'energy_original_changed')
            await obj.consumers.bind_sources(obj.receipt, obj.evidence, guard=obj._scope)
            obj.receipts = native_registry_receipt_store(hass, obj.preparations)
            await obj.receipts.initialize()
            readonly = RegistryReceiptChain.reopen(obj.receipts, operation_id, plan, obj.receipt, digests)
            state = readonly.reconstruct_inputs()
            _need(not readonly.writable and readonly.active is None and state.head == readonly.root_digest,
                  'pristine_review_required')
            _need(await obj.journal.load(operation_id) is None, 'operation_already_started')
            obj.capture = capture_powercalc_runtime(hass, guard=obj._scope)
            bound = await async_observe_accepted_powercalc_consumers(
                hass, readonly, obj.evidence, obj.capture, guard=obj._scope)
            _need(bound.source_position == 'original' and all(row.source_verified for row in bound.rows),
                  'original_graph_unverified')
            for history in (composition.source_history, composition.consumer_history):
                _need(await obj.history.verify(history), 'original_history_changed')
            obj._scope()
            writer = RegistryReceiptChain(obj.receipts, operation_id, plan, obj.receipt, digests)
            _need(writer.root == readonly.root and writer.root_digest == readonly.root_digest
                  and writer.reconstruct_inputs().same_evidence(state), 'review_head_changed')
            writer.writable = True
            obj.runtime.preparations, obj.runtime.journal = obj.preparations, obj.journal
            from homeassistant.core import Context
            from .powercalc_ordinary_driver import PowercalcOrdinaryDriver
            obj.driver = await PowercalcOrdinaryDriver.bind(hass, obj.runtime, writer, obj.evidence,
                                                            obj.capture, guard=obj._scope, context=Context())
            return obj
        except BaseException:
            obj.close()
            raise

    async def apply(self):
        _need(asyncio.current_task() is self.owner and self.driver is not None, 'owner_apply_required')
        return await self.driver.apply()

    def close(self):
        _need(asyncio.current_task() is self.owner and not self.closed, 'owner_close_required')
        self.closed = True
        self.scene.close()
