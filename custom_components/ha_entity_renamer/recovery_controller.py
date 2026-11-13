"""Server-owned public recovery for qualified Template entry and YAML plans.

No client Plan/roots/paths, new Store, readiness promotion or uncertain replay.
Inspect is evidence only. Restore repeats admission, optionally acknowledges a
proved setup effect, then independently uses the existing strict reverse binder.
"""
import asyncio
from dataclasses import dataclass

from .const import DATA_RUNTIME, VERSION
from .journal import native_journal
from .prepared import native_prepared_store
from .native_helper_lifecycle import native_helper_lifecycle_store, validate_lifecycle_receipt_witness, _id
from .native_template_entry_lifecycle import validate_preparations
from .native_template_reverse import NativeTemplateReverse
from .native_helper_activation_lease import NativeHelperActivationLease, NativeHelperActivationLeaseError
from .transaction import Refused


def _result(operation_id, fingerprint, state, reason, *, summary=None):
    eligible = state in ('applied', 'recoverable_prefix', 'recoverable_complete')
    result = {'operation_id': operation_id, 'fingerprint': fingerprint,
        'state': state, 'reason': reason, 'eligible': eligible,
        'migration_ready': False, 'family_complete': False}
    if summary is not None and eligible:
        result['change_summary'] = summary
    return result


@dataclass(frozen=True)
class _PreparedScope:
    entry_id: str
    summary: dict


def _prepared_scope(record, preparations):
    """Derive the only permitted entry from immutable server preparations."""
    plan = record.plan
    baseline, composition = preparations.resolve_composition(plan.native_inputs)
    digests = dict(composition.prepared_digests)
    if (tuple(s.adapter for s in plan.steps) != ('registry', 'helpers')
            or plan.required_scopes != ('registry', 'helpers')
            or plan.covered_scopes != plan.required_scopes
            or plan.verification_adapters != plan.required_scopes
            or any(s.caused_by for s in plan.steps)
            or plan.coverage_evidence != composition.coverage_sha256
            or set(digests) != {s.prepared_ref for s in plan.steps}):
        raise Refused('unsupported_recovery_plan')
    changes = validate_preparations(preparations, plan, baseline.receipt, digests)
    if len(changes) != 1:
        raise Refused('unsupported_recovery_plan')
    entry_id, change = next(iter(changes.items()))
    old, new = next(iter(change.mapping.items()))
    # validate_preparations proves exact source UUID/change and numeric sensor
    # entry schema against the ORIGINAL full-three receipt + native checker seal.
    return _PreparedScope(entry_id, {
        'source': {'current_entity_id': new, 'original_entity_id': old},
        'consumers': {'count': 1, 'domains': ['sensor']}})


def _entry_frozen_review(record, preparations):
    """Sanitized exact ordinary numeric-entry review from immutable ORIGINAL."""
    import json
    from .prepared import decode_change
    scope = _prepared_scope(record, preparations)
    baseline, composition = preparations.resolve_composition(record.plan.native_inputs)
    digests = dict(composition.prepared_digests)
    change = decode_change(preparations.resolve(record.plan.steps[0].prepared_ref,
                                                digests[record.plan.steps[0].prepared_ref]))
    before, after = change.before, change.after
    docs = {row.key: json.loads(row.manager_json) for row in baseline.files}
    rows = [row for row in docs['core.entity_registry']['entities']
            if row.get('config_entry_id') == scope.entry_id]
    if len(rows) != 1 or before['entity_id'] == after['entity_id']:
        raise Refused('unsupported_recovery_plan')
    return {'changes': [{'entity_id': before['entity_id'], 'fields': {
        'entity_id': {'before': before['entity_id'], 'after': after['entity_id']}}}],
        'identities': [[before.get(key) for key in ('id', 'platform', 'unique_id', 'device_id')]],
        'dependencies': {'domains': ['template'], 'yaml_file_count': 0, 'files': [],
            'template_consumer_count': 1, 'config_entries': [{'entity_id': rows[0]['entity_id'],
                'before': before['entity_id'], 'after': after['entity_id']}]}}


def _yaml_summary(session):
    """Sanitized exact scope after the existing strict server binder admitted it."""
    sources, registry = [], []
    named = False
    for index, change in enumerate(session.registry_changes):
        before, after = change.before, change.after
        applied = session.record.phases[index] == 'verified'
        current = after if applied else before
        for old, new in change.mapping.items():
            sources.append({'current_entity_id': new if applied else old,
                            'original_entity_id': old})
        keys = ('entity_id', 'name') if change.kind == 'entity' else ('name_by_user',)
        fields = {key: {'current': current.get(key), 'original': before.get(key)}
                  for key in keys if before.get(key) != after.get(key)}
        named = named or change.kind == 'device' or 'name' in fields
        target = ({'entity_id': current['entity_id']} if change.kind == 'entity'
                  else {'device_id': change.identity})
        registry.append({**target, 'applied': applied, 'fields': fields})
    sources.sort(key=lambda row: row['original_entity_id'])
    definitions = session.evidence.template_sources.ledger.definitions
    consumers = [(row.domain.removeprefix('template.'), row.native_key)
                 for row in definitions
                 if row.domain in ('template.sensor', 'template.binary_sensor')]
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    controlled_powercalc = type(session.composition) is ControlledPowercalcComposition
    if (not registry or len(sources) > 10000 or not (0 if controlled_powercalc else 1) <= len(consumers) <= 10000
            or len(set(consumers)) != len(consumers)):
        raise Refused('unsupported_recovery_plan')
    result = {'sources': sources,
              'consumers': {'count': len(consumers),
                            'domains': sorted({domain for domain, _ in consumers})}}
    if named:
        result['registry_changes'] = registry
    from .native_script_program_composition import ScriptTemplateStateComposition
    from .native_automation_program_composition import AutomationTemplateStateComposition
    from .controlled_script_composition import is_controlled_script
    controlled_script = is_controlled_script(session.composition)
    if (type(session.composition) in (ScriptTemplateStateComposition, AutomationTemplateStateComposition)
            or controlled_script):
        phases = session.consumers.phases
        phases.check()
        program = session.composition.dependent_config_program
        # Actual current prefix, not every proposed/future change. The existing
        # Template consumer field keeps its established meaning for old clients.
        domains = (('automation', 'script') if (type(session.composition) is AutomationTemplateStateComposition
                                                 or controlled_script)
                   else ('script',))
        result['dependent_changes'] = {}
        original, active = dict(program.rows(frozenset())), dict(program.rows(phases.mask))
        for domain in domains:
            before = {row[0]: row for row in original[domain]}
            current = {row[0]: row for row in active[domain]}
            if before.keys() != current.keys() and domain == 'script':
                # The one admitted Script definition key follows its own
                # registry identity. Align only that authenticated key; the
                # remainder of the population must still match exactly.
                from .native_registry_transition import _native_script_unique_move
                fence = getattr(phases.root, 'own_dependent_fence', None)
                if (fence is not None and fence.domain == 'script'
                        and _native_script_unique_move(fence.change.before, fence.change.after)):
                    old, new = fence.change.before['unique_id'], fence.change.after['unique_id']
                    if old in before and new in current and old not in current and new not in before:
                        before[new] = before.pop(old)
            if before.keys() != current.keys():
                raise Refused(f'unsupported_recovery_{domain}_inventory')
            changed = {key for key in before if before[key] != current[key]}
            rows = [row for row in phases.sample[0]
                    if row.domain == domain and row.native_key in changed]
            if (len(rows) != len(changed) or any(row.disposition != 'loaded' for row in rows)
                    or len({row.native_key for row in rows}) != len(rows)):
                raise Refused(f'unsupported_recovery_{domain}_inventory')
            result['dependent_changes'][domain] = sorted(row.entity_id for row in rows)
    return result


def _own_registry_plan(preparations, record):
    """Advisory routing only; the selected binder performs full admission."""
    try:
        from .native_template_yaml_server import _registry_only_state_plan
        baseline, composition = preparations.resolve_composition(record.plan.native_inputs)
        return _registry_only_state_plan(record.plan, composition, baseline.receipt)
    except Exception:
        return False


def _controlled_entry_plan(preparations, record):
    """Route only the retained typed entry profile to its strict binder."""
    try:
        from .native_template_yaml_server import _entry_reload_plan
        _, composition = preparations.resolve_composition(record.plan.native_inputs)
        return _entry_reload_plan(record.plan, composition)
    except Exception:
        return False


def _dashboard_yaml_plan(record):
    from .native_template_yaml_server import _dashboard_plan
    plan = getattr(record, 'plan', None)
    return False if plan is None else _dashboard_plan(plan)


def _controlled_powercalc_plan(preparations, record):
    try:
        from .native_template_yaml_server import _controlled_powercalc_plan as qualified
        _, composition = preparations.resolve_composition(record.plan.native_inputs)
        return qualified(record.plan, composition)
    except Exception:
        return False


def _ordinary_review_owned(preparations, operation_id):
    """Require the immutable ordinary-review lease, never infer a public review."""
    import re
    from .review_retention import review_lease
    match = re.fullmatch(r'template-yaml-([0-9a-f]{32})', operation_id)
    if match is None:
        return False
    key = 'review-' + match.group(1)
    row = preparations.plans.get(key)
    if type(row) is not dict or type(row.get('digest')) is not str:
        return False
    try:
        payload = preparations.resolve(key, row['digest'])
        return payload == review_lease(match.group(1), payload.get('actor_id'))
    except (ValueError, TypeError, AttributeError):
        return False


def _yaml_frozen_review(session, *, restored=False):
    """Project the original review only after full exact reverse admission.

    This is read-only evidence, never consent or permission to issue a write.
    Prefix recovery is excluded: its inverse scope is not the complete plan.
    """
    if any(phase != ('undone' if restored else 'verified') for phase in session.record.phases):
        raise Refused('complete_review_required')
    changes, identities = [], []
    for change in session.registry_changes:
        before, after = change.before, change.after
        keys = ('entity_id', 'name') if change.kind == 'entity' else ('name_by_user',)
        fields = {key: {'before': before.get(key), 'after': after.get(key)}
                  for key in keys if before.get(key) != after.get(key)}
        if not fields:
            raise Refused('empty_recovery_change')
        target = ({'entity_id': before['entity_id']} if change.kind == 'entity'
                  else {'device_id': change.identity})
        changes.append({**target, 'fields': fields})
        identities.append([before.get(key) for key in ('id', 'platform', 'unique_id', 'device_id')]
                          if change.kind == 'entity' else change.identity)
    from .dependency_review import dependency_files
    from .native_template_state_composition import ControlledReloadEntryComposition
    controlled_entry = type(session.composition) is ControlledReloadEntryComposition
    receipt = session.receipt
    result = {'changes': changes, 'identities': identities, 'dependencies': {
        'domains': sorted([domain for domain in receipt.changed_domains
                           if domain in ('template', 'automation', 'script')]
                          + (['dashboard'] if (_dashboard_yaml_plan(session.record) or
                             controlled_entry
                             and 'dashboard' in session.record.plan.required_scopes) else [])),
        'yaml_file_count': len(receipt.proposed_files),
        'files': dependency_files(session.composition),
        'template_consumer_count': sum(
            row.domain in ('template.sensor', 'template.binary_sensor')
            for row in session.evidence.template_sources.ledger.definitions)}}
    if controlled_entry or getattr(session, 'entry_change', None) is not None:
        import json
        change = session.entry_change
        old, new = next(iter(change.mapping.items()))
        baseline = session.chain.baseline
        rows = json.loads(next(row.raw for row in baseline.files
                               if row.key == 'core.entity_registry'))['data']['entities']
        consumers = [row for row in rows if row.get('config_entry_id') == change.before['identity']['entry_id']]
        if len(consumers) != 1:
            raise Refused('controlled_entry_review_consumer_unqualified')
        result['dependencies']['config_entries'] = [{'entity_id': consumers[0]['entity_id'],
                                                      'before': old, 'after': new}]
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    if type(session.composition) is ControlledPowercalcComposition:
        from .dependency_review import dependency_powercalc_groups
        from .prepared import decode_change
        digests = dict(session.composition.prepared_digests)
        helpers = [decode_change(session.preparations.resolve(step.prepared_ref, digests[step.prepared_ref]))
                   for step in session.plan.steps if step.adapter == 'helpers']
        result['dependencies'].setdefault('config_entries', []).extend(dependency_powercalc_groups(helpers, controlled=True))
        result['dependencies']['domains'] = sorted(set(result['dependencies']['domains']
            + ['powercalc'] + (['energy'] if 'energy' in session.plan.required_scopes else [])
            + (['dashboard'] if 'dashboard' in session.plan.required_scopes else [])))
    if _dashboard_yaml_plan(session.record) or (controlled_entry and 'dashboard' in session.record.plan.required_scopes):
        from .dependency_review import dependency_dashboards
        result['dependencies']['dashboards'] = dependency_dashboards(session.dashboard_changes, session.mapping)
    return result


async def _consume_yaml_restore_intent(session, expected, guard):
    """Consume one durable intent under the owning Runtime lock before effects."""
    from dataclasses import replace
    guard()
    if expected.restore_attempted is not False or session.record != expected:
        raise Refused('restore_attempt_unavailable')
    actual = await session.journal.load(expected.operation_id)
    guard()
    if actual != expected:
        raise Refused('restore_record_changed')
    claimed = replace(expected, restore_attempted=True)
    await session.journal.save(claimed)
    guard()
    session.record = claimed


async def _acquire_lease(hass, entry, *, guard, refresh):
    """Use native FIFO lock waiting; the actual finite lease still owns proof.

    NativeHelperActivationLease deliberately acquires without suspension. A busy
    entry is first awaited through its ordinary native Lock, then authorization
    is refreshed and the lease retries synchronously after releasing that lock.
    No callback/task tracking or native lock fields are changed.
    """
    while True:
        guard()
        if hass.config_entries.async_get_entry(entry.entry_id) is not entry:
            raise ValueError('recovery_entry_replaced')
        try:
            return await NativeHelperActivationLease.acquire(
                hass, hass.config_entries, (entry,), guard=guard)
        except NativeHelperActivationLeaseError as error:
            if str(error) != 'helper_activation_lease_busy':
                raise
        guard()
        async with entry.setup_lock:
            guard()  # Identity/auth can change while the native lock suspends.
            await refresh()
            guard()


async def _drain_restore(operation):
    """Keep Runtime's lock until the owned effect task has actually terminated.

    The lease is acquired inside this task (native leases are task-local). On
    cancellation cancel it once, shield every drain, and let existing native IO
    and VerifiedStore finish/read back before its finally releases the lease.
    """
    task = asyncio.create_task(operation)
    cancelled = False
    try:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not cancelled:
                    cancelled = True
                    task.cancel()
        if cancelled:
            try:
                task.result()
            except BaseException:
                pass
            raise asyncio.CancelledError
        return task.result()
    finally:
        # No unawaited effect task may outlive its Runtime lock owner.
        if not task.done():
            task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            try:
                task.result()
            except BaseException:
                pass


class RecoveryController:
    def __init__(self, hass, runtime, *, guard, refresh, status_only=False):
        self.hass, self.runtime = hass, runtime
        self.guard, self.refresh = guard, refresh
        self.status_only = status_only

    def check(self):
        self.guard()
        if (self.hass.data.get(DATA_RUNTIME) is not self.runtime or not self.runtime.loaded
                or self.runtime.unloading
                or (not self.status_only and not self.runtime.operation_lock.locked())):
            raise ValueError('recovery_runtime_unavailable')

    async def _read(self):
        self.check()
        journal, preparations = native_journal(self.hass), native_prepared_store(self.hass)
        await journal.initialize()
        self.check()
        await preparations.initialize()
        self.check()
        return journal, preparations

    async def status(self):
        """Advertise pending lifecycle ACKs even when the journal is applied.

        This is server-owned inventory, NOT full admission or a restore grant.
        Public inspect performs fresh full native IO before marking eligible.
        """
        # Independent readers never replace the active session's Runtime caches.
        # Each journal lock covers only the detached inventory snapshot, not the
        # long-running native operation or a restore/inspect capability.
        self.check()
        journal = native_journal(self.hass)
        await journal.initialize()
        self.check()
        records = await journal.all()
        self.check()
        preparations = store = None
        operations = []
        required = False
        for record in records:
            self.check()
            need, reason = True, 'unsupported_recovery_state'
            if record.status == 'refused' and record.reason == 'prewrite_refused':
                operations.append({'operation_id': record.operation_id,
                    'fingerprint': record.plan.fingerprint, 'status': record.status,
                    'direction': record.direction, 'recovery_required': False,
                    'reason': 'prewrite_refused', 'eligible': False})
                continue
            from .native_template_prefix_recovery import acknowledged_registry_prefix, complete_forward_unknown
            if acknowledged_registry_prefix(record) or complete_forward_unknown(record):
                # Snapshot shape only: actual source/chain/history inspection
                # must still admit this scope before eligibility is reported.
                operations.append({'operation_id': record.operation_id,
                    'fingerprint': record.plan.fingerprint, 'status': record.status,
                    'direction': record.direction, 'recovery_required': True,
                    'reason': 'inspection_available', 'eligible': False})
                required = True
                continue
            if record.status in ('applied', 'rolled_back'):
                if (record.status == 'applied' and record.direction == 'forward'
                        and (record.plan.required_scopes in (('registry', 'yaml'), ('registry', 'helpers', 'yaml'),
                                                              ('registry', 'helpers', 'dashboard', 'yaml'))
                             or _dashboard_yaml_plan(record))):
                    # Snapshot inventory is never full native/source admission.
                    # Only explicit inspect may qualify this terminal plan.
                    # A HomeKit helper plan additionally requires its durable
                    # unload AND setup receipts, not just journal=applied.
                    if 'helpers' in record.plan.required_scopes:
                        try:
                            if preparations is None:
                                preparations = native_prepared_store(self.hass)
                                await preparations.initialize()
                                self.check()
                                store = native_helper_lifecycle_store(self.hass, preparations)
                                await store.initialize()
                                self.check()
                            from .native_homekit_recovery_status import classify
                            homekit = classify(preparations, store, record)
                            if homekit is True:
                                need = False
                            elif homekit is False:
                                reason = 'homekit_setup_unresolved'
                        except Exception:
                            # Preserve recovery_required on every unproven graph.
                            pass
                    operations.append({'operation_id': record.operation_id,
                        'fingerprint': record.plan.fingerprint, 'status': record.status,
                        'direction': record.direction, 'recovery_required': need,
                        'reason': 'homekit_setup_unresolved' if reason == 'homekit_setup_unresolved' else 'inspection_available',
                        'eligible': False})
                    required = required or need
                    continue
                if preparations is None:
                    # The helper classifier requires both independently loaded
                    # stores. Never retain these readers across status calls.
                    preparations = native_prepared_store(self.hass)
                    await preparations.initialize()
                    self.check()
                    store = native_helper_lifecycle_store(self.hass, preparations)
                    await store.initialize()
                    self.check()
                try:
                    native = record.plan.native_inputs
                    stored = (preparations.plans.get(native[0]) if type(native) is tuple and len(native) == 3
                              and type(native[0]) is str else None)
                    encoded = stored['payload'].get('composition') if stored is not None else None
                    is_controlled_pc = _controlled_powercalc_plan(preparations, record)
                    if (is_controlled_pc and record.status == 'applied' and record.direction == 'forward'
                            and record.phases == ('verified',) * len(record.plan.steps)):
                        operations.append({'operation_id': record.operation_id,
                            'fingerprint': record.plan.fingerprint, 'status': record.status,
                            'direction': record.direction, 'recovery_required': True,
                            'reason': 'inspection_available', 'eligible': False})
                        required = True
                        continue
                    is_powercalc = (type(encoded) is dict
                                    and encoded.get('profile') == 'powercalc_source_own_binding_v1')
                    if is_powercalc:
                        from .powercalc_composition import PowercalcComposition
                        _, candidate = preparations.resolve_composition(native)
                        is_powercalc = type(candidate) is PowercalcComposition
                    if (is_powercalc and record.status == 'applied'
                            and record.direction == 'forward'
                            and record.phases == ('verified',) * len(record.plan.steps)):
                        operations.append({'operation_id': record.operation_id,
                            'fingerprint': record.plan.fingerprint, 'status': record.status,
                            'direction': record.direction, 'recovery_required': True,
                            'reason': 'inspection_available', 'eligible': False})
                        required = True
                        continue
                    if (record.status == 'applied' and record.direction == 'forward'
                            and _own_registry_plan(preparations, record)):
                        # Snapshot inventory is never full native/source admission.
                        # Only explicit inspect may qualify this terminal plan.
                        operations.append({'operation_id': record.operation_id,
                            'fingerprint': record.plan.fingerprint, 'status': record.status,
                            'direction': record.direction, 'recovery_required': need,
                            'reason': 'inspection_available', 'eligible': False})
                        required = required or need
                        continue
                    scope = _prepared_scope(record, preparations)
                    baseline, composition = preparations.resolve_composition(record.plan.native_inputs)
                    roots = [key for key, row in store.rows.items()
                        if row['payload'].get('kind') == 'helper_lifecycle_root'
                        and row['payload']['binding'].get('operation_id') == record.operation_id
                        and row['payload']['binding'].get('fingerprint') == record.plan.fingerprint]
                    statuses = [validate_lifecycle_receipt_witness(store, record.operation_id,
                        record.plan, baseline.receipt, dict(composition.prepared_digests), root_id=key).inspect()
                        for key in roots]
                    if record.status == 'applied' and record.direction == 'forward':
                        original = _id(['helper_lifecycle', record.operation_id, record.plan.fingerprint])
                        if len(roots) == 1 and roots[0] == original:
                            status = statuses[0]
                            if status['pending'] == [{'stage': 'setup',
                                    'entry_id': scope.entry_id}]:
                                reason = 'setup_ack_pending'
                            elif (status['inactive_receipts_complete']
                                    and status['activation_receipts_complete'] and not status['pending']):
                                need, reason = False, 'inspection_available'
                    elif (record.status == 'rolled_back' and len(statuses) == 2
                            and all(s['inactive_receipts_complete'] and s['activation_receipts_complete']
                                    and not s['pending'] for s in statuses)):
                        continue  # No success claim or restored-data claim is returned.
                except Exception:
                    reason = 'recovery_unproven'
            required = required or need
            operations.append({'operation_id': record.operation_id,
                'fingerprint': record.plan.fingerprint, 'status': record.status,
                'direction': record.direction, 'recovery_required': need,
                'reason': reason, 'eligible': False})
        # These are persisted transaction observations, not a percentage of
        # family qualification or proof that lifecycle/recovery work is complete.
        by_id = {record.operation_id: record for record in records}
        for operation in operations:
            record = by_id[operation['operation_id']]
            operation['phases'] = [
                {'adapter': step.adapter, 'phase': phase}
                for step, phase in zip(record.plan.steps, record.phases, strict=True)]
            operation['progress'] = {
                'total_steps': len(record.plan.steps),
                'verified_steps': record.phases.count('verified'),
                'undone_steps': record.phases.count('undone')}
        await self.refresh()
        self.check()
        return {'version': VERSION, 'migration_ready': False, 'family_complete': False,
            'reason': 'dependency_coverage_incomplete', 'recovery_required': required,
            'operations': operations}

    async def inspect(self, operation_id, fingerprint):
        return await self._operation(operation_id, fingerprint, restore=False)

    async def restore(self, operation_id, fingerprint):
        return await _drain_restore(self._operation(operation_id, fingerprint, restore=True))

    async def _operation(self, operation_id, fingerprint, *, restore):
        if self.status_only:
            raise ValueError('recovery_status_only')
        self.check()
        lease = session = None
        try:
            journal, preparations = await self._read()
            record = await journal.load(operation_id)
            self.check()
            if record is None or record.plan.fingerprint != fingerprint:
                return _result(operation_id, fingerprint, 'refused', 'operation_mismatch')
            if record.status == 'refused' and record.reason == 'prewrite_refused':
                return _result(operation_id, fingerprint, 'refused', 'prewrite_refused')
            native = record.plan.native_inputs
            stored = (preparations.plans.get(native[0]) if type(native) is tuple and len(native) == 3
                      and type(native[0]) is str else None)
            encoded = stored['payload'].get('composition') if stored is not None else None
            controlled_powercalc = _controlled_powercalc_plan(preparations, record)
            if type(encoded) is dict and encoded.get('profile') == 'powercalc_source_own_binding_v1':
                from .powercalc_composition import PowercalcComposition
                _, composition = preparations.resolve_composition(native)
                if type(composition) is not PowercalcComposition:
                    raise Refused('unsupported_recovery_plan')
                from .powercalc_public_recovery import inspect_or_restore
                return await inspect_or_restore(self, record, restore=restore)
            if record.status == 'rolled_back':
                if restore:
                    return _result(operation_id, fingerprint, 'refused', 'already_finished')
                # The inner journal alone is not proof of original runtime or
                # completed outer lifecycle. Reopen and independently verify.
                if (record.plan.required_scopes == ('registry', 'helpers')
                        and _ordinary_review_owned(preparations, operation_id)):
                    if record.restore_attempted is not True:
                        return _result(operation_id, fingerprint, 'UNKNOWN', 'restored_unproven')
                    scope = _prepared_scope(record, preparations)
                    entry = self.hass.config_entries.async_get_entry(scope.entry_id)
                    if entry is None:
                        return _result(operation_id, fingerprint, 'UNKNOWN', 'restored_unproven')
                    lease = await _acquire_lease(self.hass, entry, guard=self.check, refresh=self.refresh)
                    await NativeTemplateReverse.verify_restored(self.hass, operation_id, fingerprint,
                        activation_lease=lease, guard=self.check, refresh=self.refresh)
                    result = _result(operation_id, fingerprint, 'rolled_back', 'restored_verified')
                    result['frozen_review'] = _entry_frozen_review(record, preparations)
                    return result
                if (record.restore_attempted is not True
                        or (record.plan.required_scopes != ('registry', 'yaml')
                            and not _dashboard_yaml_plan(record)
                            and not _controlled_entry_plan(preparations, record) and not controlled_powercalc
                            and not _own_registry_plan(preparations, record))
                        or not _ordinary_review_owned(preparations, operation_id)):
                    return _result(operation_id, fingerprint, 'UNKNOWN', 'restored_unproven')
                from . import _yaml_factory_for_plan
                factory = _yaml_factory_for_plan(preparations, record.plan)
                session = await factory.bind_reverse(self.hass, operation_id, fingerprint,
                    guard=self.check, refresh=self.refresh, restored=True)
                self.check()
                result = _result(operation_id, fingerprint, 'rolled_back', 'restored_verified')
                # A partial inverse restores only an accepted prefix; it must
                # not be presented as the full original forward review.
                if session.record.phases == ('undone',) * len(session.record.plan.steps):
                    result['frozen_review'] = _yaml_frozen_review(session, restored=True)
                return result
            if (record.plan.required_scopes == ('registry', 'yaml') or _dashboard_yaml_plan(record)
                    or _controlled_entry_plan(preparations, record) or _own_registry_plan(preparations, record) or controlled_powercalc) and record.restore_attempted is not False:
                # A previous restore may have stopped before its first journal
                # transition, including inside outer automation lifecycle.
                return _result(operation_id, fingerprint, 'UNKNOWN',
                    'restore_attempt_unresolved' if record.restore_attempted is True else 'legacy_restore_unproven')
            # External terminal inspection shares the normal YAML binder and
            # terminal source/registry admission, but does not authorize rollback.
            from .native_template_yaml_server import _external_state_plan
            external_plan = False
            if (type(encoded) is dict and (encoded.get('profile') == 'template_external_state_binding_v1'
                    or encoded.get('profile') == 'template_controlled_powercalc_v1'
                    and encoded.get('external_source_proof'))):
                _, composition = preparations.resolve_composition(record.plan.native_inputs)
                external_plan = _external_state_plan(record.plan, composition)
            if external_plan:
                if restore:
                    return _result(operation_id, fingerprint, 'refused', 'external_rollback_lifecycle_required')
                from . import _yaml_factory_for_plan
                factory = _yaml_factory_for_plan(preparations, record.plan)
                session = await factory.bind_reverse(self.hass, operation_id, fingerprint,
                    guard=self.check, refresh=self.refresh, inspect_only=True)
                self.check()
                result = _result(operation_id, fingerprint, 'applied', 'complete', summary=_yaml_summary(session))
                result['eligible'] = False
                result['restore_attempt_available'] = False
                return result
            from .native_template_prefix_recovery import acknowledged_registry_prefix
            from .native_template_prefix_recovery import acknowledged_powercalc_registry_prefix
            prefix = (acknowledged_registry_prefix(record)
                      or controlled_powercalc and acknowledged_powercalc_registry_prefix(record))
            from .native_template_prefix_recovery import complete_forward_unknown
            uncertain_complete = complete_forward_unknown(record)
            terminal = (record.status == 'applied' and record.direction == 'forward'
                        and record.phases == ('verified',) * len(record.plan.steps))
            if _dashboard_yaml_plan(record) and not terminal and not (controlled_powercalc and prefix):
                return _result(operation_id, fingerprint, 'UNKNOWN', 'unsupported_recovery_state')
            if not terminal and not prefix and not uncertain_complete:
                return _result(operation_id, fingerprint, 'UNKNOWN', 'unsupported_recovery_state')
            if (record.plan.required_scopes == ('registry', 'yaml') or _dashboard_yaml_plan(record)
                    or _controlled_entry_plan(preparations, record) or _own_registry_plan(preparations, record) or controlled_powercalc):
                from . import _yaml_factory_for_plan
                factory = _yaml_factory_for_plan(preparations, record.plan)
                # Fresh actual source/history/auth admission, independently repeated
                # for restore; no inspect result is retained as write authority.
                if restore and record.restore_attempted is not False:
                    return _result(operation_id, fingerprint, 'UNKNOWN', 'restore_attempt_unavailable')
                session = await factory.bind_reverse(self.hass,
                    operation_id, fingerprint, guard=self.check, refresh=self.refresh)
                self.check()
                summary = _yaml_summary(session)
                if not restore:
                    result = _result(operation_id, fingerprint,
                        'recoverable_prefix' if prefix else
                            'recoverable_complete' if uncertain_complete else 'applied',
                        'acknowledged_prefix' if prefix else
                            'complete_forward_unknown' if uncertain_complete else 'complete', summary=summary)
                    result['restore_attempt_available'] = record.restore_attempted is False
                    if not prefix and _ordinary_review_owned(preparations, operation_id):
                        try:
                            result['frozen_review'] = _yaml_frozen_review(session)
                        except (Refused, ValueError, KeyError, TypeError):
                            pass  # Existing inspection remains usable without a full projection.
                    return result
                # Both Runtime entrypoints consume the same durable one-shot
                # intent under the bound session and owning operation lock.
                await _consume_yaml_restore_intent(session, record, self.check)
                result = await session.rollback()
                self.check()
                if result.status != 'rolled_back':
                    return _result(operation_id, fingerprint, 'UNKNOWN', 'restore_unproven')
                return _result(operation_id, fingerprint, 'rolled_back', 'restored')
            scope = _prepared_scope(record, preparations)
            ordinary_entry = _ordinary_review_owned(preparations, operation_id)
            if restore and ordinary_entry and record.restore_attempted is not False:
                return _result(operation_id, fingerprint, 'UNKNOWN', 'restore_attempt_unavailable')
            entry = self.hass.config_entries.async_get_entry(scope.entry_id)
            if entry is None:
                return _result(operation_id, fingerprint, 'UNKNOWN', 'recovery_unproven')
            await self.refresh()
            self.check()
            lease = await _acquire_lease(self.hass, entry, guard=self.check, refresh=self.refresh)
            self.check()
            kwargs = dict(activation_lease=lease, guard=self.check, refresh=self.refresh)
            classification = await NativeTemplateReverse.classify_setup_ack(
                self.hass, operation_id, fingerprint, **kwargs)
            self.check()
            if classification.status != 'applied':
                return _result(operation_id, fingerprint, classification.status,
                               'recovery_unproven')
            if not restore:
                result = _result(operation_id, fingerprint, 'applied', classification.reason,
                                 summary=scope.summary)
                result['restore_attempt_available'] = record.restore_attempted is False
                if ordinary_entry:
                    result['frozen_review'] = _entry_frozen_review(record, preparations)
                return result
            if classification.reason == 'setup_ack_missing':
                await NativeTemplateReverse.recover_setup_ack(
                    self.hass, operation_id, fingerprint, **kwargs)
                self.check()
            # A readonly classification never grants a writer. This is a new,
            # independent strict bind, even after an ACK marker was recovered.
            session = await NativeTemplateReverse.bind(self.hass, operation_id, fingerprint, **kwargs)
            self.check()
            if ordinary_entry:
                await _consume_yaml_restore_intent(session, record, self.check)
            result = await session.rollback()
            self.check()
            if result.status != 'rolled_back':
                return _result(operation_id, fingerprint, 'UNKNOWN', 'restore_unproven')
            # rollback() already completed native setup/full source/history and
            # final auth admission. No later auth await opens a new source gap.
            return _result(operation_id, fingerprint, 'rolled_back', 'restored')
        except PermissionError:
            raise
        except asyncio.CancelledError:
            raise
        except Refused as error:
            state = 'refused' if str(error) == 'unsupported_recovery_plan' else 'UNKNOWN'
            return _result(operation_id, fingerprint, state, 'recovery_unproven')
        except Exception:
            return _result(operation_id, fingerprint, 'UNKNOWN', 'recovery_unproven')
        finally:
            if session is not None:
                session.close()
            if lease is not None:
                lease.release()
