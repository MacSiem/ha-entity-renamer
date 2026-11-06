"""Fresh, one-shot public recovery of an admitted Powercalc ordinary plan."""
from __future__ import annotations

from dataclasses import replace

from .transaction import Refused


def _summary(admission):
    from .native_registry import PreparedRegistryChange
    mapping = {}
    for change in admission.chain.changes:
        if type(change) is PreparedRegistryChange:
            mapping.update(change.mapping)
    if not mapping:
        raise Refused('powercalc_public_recovery_mapping_required')
    return {'sources': [{'current_entity_id': new, 'original_entity_id': old}
                        for old, new in sorted(mapping.items())],
            'consumers': {'count': len(admission.observed.rows), 'domains': ['sensor']}}


async def _frozen_review(controller, chain, *, restored):
    """Reconstruct a public projection from admitted immutable file images."""
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace
    from .native_input_baseline import _drained
    from .native_registry import PreparedRegistryChange
    from .powercalc_prepared_yaml import PreparedPowercalcYAML
    from .yaml_plan import plan_yaml
    from .dependency_review import dependency_files, dependency_powercalc_groups

    registry = [change for change in chain.changes if type(change) is PreparedRegistryChange]
    yaml = [change for change in chain.changes if type(change) is PreparedPowercalcYAML]
    mapping = {old: new for change in registry for old, new in change.mapping.items()}
    if len(yaml) != 1 or len(mapping) != 2 or len(set(mapping.values())) != len(mapping):
        raise Refused('powercalc_public_frozen_review_scope')
    direction = mapping if restored else {new: old for old, new in mapping.items()}
    state = chain.reconstruct_inputs()
    plan = await _drained(controller.hass.async_add_executor_job,
        lambda: plan_yaml(Path(chain.original_receipt.config_root), direction))
    controller.check()
    expected = {row.path: row for row in yaml[0].files}
    if (not plan.ready or {row.path for row in plan.patches} != set(expected)
            or any(patch.expected_sha256 != hashlib.sha256(
                expected[patch.path].before if restored else expected[patch.path].after).hexdigest()
                or patch.proposed_bytes != (expected[patch.path].after if restored else expected[patch.path].before)
                for patch in plan.patches)):
        raise Refused('powercalc_public_frozen_review_changed')
    files = dependency_files(SimpleNamespace(mapping=direction, yaml_plan=plan))
    if not restored:
        files = [{**row, 'references': [{**item, 'before': item['after'], 'after': item['before']}
                                      for item in row['references']]} for row in files]
    changes, identities = [], []
    for change in registry:
        before, after = change.before, change.after
        changes.append({'entity_id': before['entity_id'], 'fields': {
            'entity_id': {'before': before['entity_id'], 'after': after['entity_id']}}})
        identities.append([before.get(key) for key in ('id', 'platform', 'unique_id', 'device_id')])
    await _drained(controller.hass.async_add_executor_job,
                   lambda: state.assert_current(chain.original_receipt))
    controller.check()
    if not state.same_evidence(chain.reconstruct_inputs()):
        raise Refused('powercalc_public_frozen_review_changed')
    return {'changes': changes, 'identities': identities, 'dependencies': {
        'domains': ['powercalc'] + (['energy'] if 'energy' in chain.plan.required_scopes else []),
        'yaml_file_count': len(yaml[0].files), 'files': files, 'template_consumer_count': 0,
        'config_entries': dependency_powercalc_groups(chain.changes)}}


async def _restored(controller, record):
    """Read a completed inverse from ORIGINAL and current native objects."""
    from .powercalc_composition import PowercalcComposition
    from .prepared import native_prepared_store
    from .native_registry_receipts import RegistryReceiptChain, native_registry_receipt_store
    from .native_registry import NativeRegistryBridge, NativeRegistryIO
    from .recorder_guard import NativeRecorderGuard
    from .history_guard import NativeHistoryGuard
    from .powercalc_runtime_observer import capture_powercalc_runtime
    from .native_powercalc_accepted_consumers import async_observe_accepted_powercalc_consumers
    from .powercalc_lifecycle import validate_reverse_root
    from .helper_native import read_entry
    from .helper_persistence import NativeHelperPersistence
    from .helper_plan import snapshot_equal
    from .energy_native import NativeEnergy
    from .native_discovery_consumers import _energy_manager
    from .native_registry_receipts import _exact_json_equal, _freeze_json

    def need(value):
        if not value:
            raise Refused('powercalc_public_restored_unverified')
    need(record.direction == 'rollback' and record.restore_attempted is True
         and record.phases == ('undone',) * len(record.plan.steps))
    preparations = native_prepared_store(controller.hass)
    await preparations.initialize()
    controller.check()
    baseline, composition = preparations.resolve_composition(record.plan.native_inputs)
    need(type(composition) is PowercalcComposition)
    receipts = native_registry_receipt_store(controller.hass, preparations)
    await receipts.initialize()
    controller.check()
    chain = RegistryReceiptChain.reopen(receipts, record.operation_id, record.plan,
                                        baseline.receipt, dict(composition.prepared_digests))
    state = chain.reconstruct_inputs()
    _, _, _, completed, pending = chain._reconstruct()
    need(not chain.writable and pending is None
         and completed == {index: 'rollback' for index in range(len(record.plan.steps))})
    roots = [(key, row['digest']) for key, row in receipts.rows.items()
             if row['payload'].get('kind') == 'powercalc_lifecycle_reverse_root'
             and row['payload'].get('operation_id') == record.operation_id]
    need(len(roots) == 1)
    validate_reverse_root(chain, roots[0])
    recorder = NativeRecorderGuard(controller.hass)
    history = NativeHistoryGuard(recorder)
    bridge = NativeRegistryBridge(NativeRegistryIO(controller.hass), recorder,
                                  native_images=chain.accepted_documents)
    for index, step in enumerate(record.plan.steps):
        change = chain._original_change(index)
        if step.adapter == 'registry':
            need((await bridge.inspect(change)).position == 'before')
        elif step.adapter == 'helpers':
            current = read_entry(controller.hass, change.before['identity']['entry_id'])
            need(snapshot_equal(current, change.before)
                 and await NativeHelperPersistence(controller.hass).inspect_current(current))
        elif step.adapter == 'energy':
            current = await NativeEnergy(controller.hass, _energy_manager(controller.hass)).read()
            need(_exact_json_equal(current.value, _freeze_json(change.before.value))
                 and current.revision == change.before.revision)
        controller.check()
    for snapshot in (composition.source_history, composition.consumer_history):
        need(await history.verify(snapshot))
        controller.check()
    from .journal import native_journal
    fresh_preparations = native_prepared_store(controller.hass)
    fresh_journal = native_journal(controller.hass)
    await fresh_preparations.initialize()
    await fresh_journal.initialize()
    fresh_receipts = native_registry_receipt_store(controller.hass, fresh_preparations)
    await fresh_receipts.initialize()
    need(_exact_json_equal(fresh_preparations.plans, _freeze_json(preparations.plans))
         and _exact_json_equal(fresh_receipts.rows, _freeze_json(receipts.rows))
         and await fresh_journal.load(record.operation_id) == record)
    controller.check()
    captured = capture_powercalc_runtime(controller.hass, guard=controller.check)
    observed = await async_observe_accepted_powercalc_consumers(
        controller.hass, chain, composition.evidence, captured, guard=controller.check)
    need(observed.source_position == 'original' and observed.rows
         and all(row.source_verified for row in observed.rows)
         and state.same_evidence(chain.reconstruct_inputs()))
    controller.check()
    return chain


async def inspect_or_restore(controller, record, *, restore):
    from homeassistant.core import Context
    from .powercalc_reverse_admission import PowercalcReverseAdmission
    from .powercalc_reverse_driver import PowercalcReverseDriver
    from .recovery_controller import _result

    op, fingerprint = record.operation_id, record.plan.fingerprint
    if 'energy' in record.plan.required_scopes:
        # Energy's manager is lazily initialized on a fresh HA process. Load
        # the native store before observing it; all subsequent lineage, disk,
        # manager identity and physical fences still apply to its actual data.
        from homeassistant.components.energy.data import async_get_manager
        controller.check()
        await async_get_manager(controller.hass)
        controller.check()
    if record.status == 'rolled_back':
        if restore:
            return _result(op, fingerprint, 'refused', 'already_finished')
        chain = await _restored(controller, record)
        result = _result(op, fingerprint, 'rolled_back', 'restored_verified')
        result['frozen_review'] = await _frozen_review(controller, chain, restored=True)
        return result
    if (record.status != 'applied' or record.direction != 'forward'
            or record.phases != ('verified',) * len(record.plan.steps)):
        return _result(op, fingerprint, 'UNKNOWN', 'unsupported_recovery_state')
    if record.restore_attempted is not False:
        return _result(op, fingerprint, 'UNKNOWN', 'restore_attempt_unavailable')
    admission = await PowercalcReverseAdmission.bind(controller.hass, controller.runtime, op, fingerprint,
                                                      guard=controller.check, refresh=controller.refresh)
    try:
        controller.check()
        if admission.record != record:
            raise Refused('powercalc_public_recovery_record_changed')
        summary = _summary(admission)
        if not restore:
            result = _result(op, fingerprint, 'applied', 'complete', summary=summary)
            result['restore_attempt_available'] = True
            result['frozen_review'] = await _frozen_review(controller, admission.readonly, restored=False)
            return result
        # Consume durable one-shot restore intent on the same fresh admitted
        # record. A failure after this point is UNKNOWN, never a replay grant.
        claimed = replace(record, restore_attempted=True)
        if await admission.journal.load(op) != record:
            raise Refused('powercalc_public_recovery_record_changed')
        controller.check()
        await admission.journal.save(claimed)
        controller.check()
        if await admission.journal.load(op) != claimed:
            raise Refused('powercalc_public_recovery_claim_unverified')
        controller.check()
        admission.record = claimed
        driver = await PowercalcReverseDriver.bind(admission, context=Context())
        outcome = await driver.rollback()
        controller.check()
        if outcome.status != 'rolled_back':
            return _result(op, fingerprint, 'UNKNOWN', 'restore_unproven')
        return _result(op, fingerprint, 'rolled_back', 'restored')
    finally:
        if not admission.closed:
            admission.close()
