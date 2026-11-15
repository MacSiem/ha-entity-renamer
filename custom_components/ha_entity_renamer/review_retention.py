"""Reclaim only leased, never-started ordinary reviews under the Runtime lock.

Unmarked historical data and all journal references survive. A receipt with any
native intent/effect evidence is never reclaimed, even without a journal record.
"""
import re

from .journal import native_journal
from .prepared import native_prepared_store
from .native_registry_receipts import native_registry_receipt_store


def review_lease(prefix, actor_id):
    if not re.fullmatch(r'[0-9a-f]{32}', prefix) or type(actor_id) is not str or not actor_id:
        raise ValueError('invalid_review_lease')
    return {'kind': 'ordinary_review_lease', 'schema': 1, 'prefix': prefix,
            'operation_id': 'template-yaml-' + prefix, 'actor_id': actor_id}


async def async_reclaim_reviews(hass, *, keep=(), guard):
    """Fresh stores; receipt roots first, then exclusively leased preparations.

    Call only at backend setup before exposure or under its operation lock.
    A crash between saves leaves a retryable lease with no native effects.
    """
    guard()
    preparations, journal = native_prepared_store(hass), native_journal(hass)
    await preparations.initialize()
    guard()
    await journal.initialize()
    guard()
    records = await journal.all()
    guard()
    protected_ops = set(keep) | {record.operation_id for record in records}
    protected_refs = {step.prepared_ref for record in records for step in record.plan.steps}
    protected_refs.update(record.plan.native_inputs[0] for record in records if record.plan.native_inputs)
    leases = [(key, row['payload']) for key, row in preparations.plans.items()
              if type(row['payload']) is dict and row['payload'].get('kind') == 'ordinary_review_lease']
    if not leases:
        return preparations, journal
    receipts = native_registry_receipt_store(hass, preparations)
    await receipts.initialize()
    guard()
    remove_prepared, remove_receipts = set(), set()
    for key, lease in leases:
        prefix = lease.get('prefix')
        if (type(prefix) is not str or not re.fullmatch(r'[0-9a-f]{32}', prefix)
                or key != 'review-' + prefix
                or lease != review_lease(prefix, lease.get('actor_id'))):
            raise ValueError('review_lease_unverified')
        operation = lease['operation_id']
        if operation in protected_ops:
            continue
        owned = {ref for ref in preparations.plans if ref.startswith(prefix + '-')}
        if (owned & protected_refs or key in protected_refs
                or any(not re.fullmatch(prefix + r'-(?:[0-9]+|original)', ref) for ref in owned)):
            continue
        roots = {ref for ref, row in receipts.rows.items()
                 if row['payload'].get('kind') == 'registry_chain'
                 and row['payload'].get('operation_id') == operation}
        # Never discard an intent, receipt, setup or rollback evidence chain.
        if len(roots) > 1 or any(row['payload'].get('chain') in roots for row in receipts.rows.values()):
            continue
        if any(row['payload'].get('operation_id') == operation and ref not in roots
               for ref, row in receipts.rows.items()):
            continue
        remove_prepared.update(owned | {key})
        remove_receipts.update(roots)
    if remove_receipts:
        updated = {key: row for key, row in receipts.rows.items() if key not in remove_receipts}
        guard()
        try:
            await receipts.store.save({'schema': 1, 'events': updated})
        except BaseException:
            receipts.rows, receipts.poisoned, preparations.plans = None, True, None
            raise
        receipts.rows = updated
        guard()
    if remove_prepared:
        updated = {key: row for key, row in preparations.plans.items() if key not in remove_prepared}
        guard()
        try:
            await preparations.store.save({'schema': 1, 'plans': updated})
        except BaseException:
            preparations.plans = None
            raise
        preparations.plans = updated
        guard()
    return preparations, journal
