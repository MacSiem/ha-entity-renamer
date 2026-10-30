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
    eligible = state in ('applied', 'recoverable_prefix')
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
    if (not 1 <= len(sources) <= 10000 or not 1 <= len(consumers) <= 10000
            or len(set(consumers)) != len(consumers)):
        raise Refused('unsupported_recovery_plan')
    result = {'sources': sources,
              'consumers': {'count': len(consumers),
                            'domains': sorted({domain for domain, _ in consumers})}}
    if named:
        result['registry_changes'] = registry
    from .native_script_program_composition import ScriptTemplateStateComposition
    if type(session.composition) is ScriptTemplateStateComposition:
        phases = session.consumers.phases
        phases.check()
        program = session.composition.dependent_config_program
        before = {row[0]: row for row in dict(program.rows(frozenset()))['script']}
        current = {row[0]: row for row in dict(program.rows(phases.mask))['script']}
        if before.keys() != current.keys():
            raise Refused('unsupported_recovery_script_inventory')
        changed = {key for key in before if before[key] != current[key]}
        rows = [row for row in phases.sample[0]
                if row.domain == 'script' and row.native_key in changed]
        if (len(rows) != len(changed) or any(row.disposition != 'loaded' for row in rows)
                or len({row.native_key for row in rows}) != len(rows)):
            raise Refused('unsupported_recovery_script_inventory')
        # Actual current prefix, not every proposed/future change. The existing
        # Template consumer field keeps its established meaning for old clients.
        result['dependent_changes'] = {'script': sorted(row.entity_id for row in rows)}
    return result


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
        journal, preparations = await self._read()
        records = await journal.all()
        self.check()
        store = native_helper_lifecycle_store(self.hass, preparations)
        await store.initialize()
        self.check()
        operations = []
        required = False
        for record in records:
            self.check()
            need, reason = True, 'unsupported_recovery_state'
            from .native_template_prefix_recovery import acknowledged_registry_prefix
            if acknowledged_registry_prefix(record):
                # Snapshot shape only: actual source/chain/history inspection
                # must still admit this scope before eligibility is reported.
                operations.append({'operation_id': record.operation_id,
                    'fingerprint': record.plan.fingerprint, 'status': record.status,
                    'direction': record.direction, 'recovery_required': True,
                    'reason': 'inspection_available', 'eligible': False})
                required = True
                continue
            if record.status in ('applied', 'rolled_back'):
                try:
                    if (record.status == 'applied' and record.direction == 'forward'
                            and record.plan.required_scopes == ('registry', 'yaml')):
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
            if record.status == 'rolled_back':
                # Do NOT infer full native/lifecycle completion from this status.
                return _result(operation_id, fingerprint, 'refused', 'already_finished')
            from .native_template_prefix_recovery import acknowledged_registry_prefix
            prefix = acknowledged_registry_prefix(record)
            terminal = (record.status == 'applied' and record.direction == 'forward'
                        and record.phases == ('verified',) * len(record.plan.steps))
            if not terminal and not prefix:
                return _result(operation_id, fingerprint, 'UNKNOWN', 'unsupported_recovery_state')
            if record.plan.required_scopes == ('registry', 'yaml'):
                from . import _yaml_factory_for_plan
                factory = _yaml_factory_for_plan(preparations, record.plan)
                # Fresh actual source/history/auth admission, independently repeated
                # for restore; no inspect result is retained as write authority.
                session = await factory.bind_reverse(self.hass,
                    operation_id, fingerprint, guard=self.check, refresh=self.refresh)
                self.check()
                summary = _yaml_summary(session)
                if not restore:
                    return _result(operation_id, fingerprint,
                        'recoverable_prefix' if prefix else 'applied',
                        'acknowledged_prefix' if prefix else 'complete', summary=summary)
                result = await session.rollback()
                self.check()
                if result.status != 'rolled_back':
                    return _result(operation_id, fingerprint, 'UNKNOWN', 'restore_unproven')
                return _result(operation_id, fingerprint, 'rolled_back', 'restored')
            scope = _prepared_scope(record, preparations)
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
                return _result(operation_id, fingerprint, 'applied', classification.reason,
                               summary=scope.summary)
            if classification.reason == 'setup_ack_missing':
                await NativeTemplateReverse.recover_setup_ack(
                    self.hass, operation_id, fingerprint, **kwargs)
                self.check()
            # A readonly classification never grants a writer. This is a new,
            # independent strict bind, even after an ACK marker was recovered.
            session = await NativeTemplateReverse.bind(self.hass, operation_id, fingerprint, **kwargs)
            self.check()
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
