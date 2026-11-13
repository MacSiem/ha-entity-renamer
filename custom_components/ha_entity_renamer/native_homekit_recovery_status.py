"""Read-only HomeKit lifecycle classification for the ordinary status inventory.

No reopening here is writable and a terminal journal alone never proves that
bridge setup completed. None means this is not a HomeKit-only helper plan.
"""
from .transaction import Refused


def classify(preparations, lifecycle_store, record):
    from .helper_transaction import PreparedHelper
    from .native_helper_lifecycle import _id, validate_lifecycle_receipt_witness
    from .prepared import decode_change
    baseline, composition = preparations.resolve_composition(record.plan.native_inputs)
    digests = dict(composition.prepared_digests)
    steps = [step for step in record.plan.steps if step.adapter == 'helpers']
    if not steps:
        return None
    changes = [decode_change(preparations.resolve(step.prepared_ref, digests[step.prepared_ref]))
               for step in steps]
    homekit = [change for change in changes if type(change) is PreparedHelper
               and change.before.get('domain') == 'homekit']
    if not homekit:
        return None
    if len(homekit) != len(changes):
        raise Refused('homekit_mixed_recovery_scope_unqualified')
    if (record.status != 'applied' or record.direction != 'forward'
            or record.phases != ('verified',) * len(record.plan.steps)):
        raise Refused('homekit_recovery_not_terminal')
    root_id = _id(['helper_lifecycle', record.operation_id, record.plan.fingerprint])
    witness = validate_lifecycle_receipt_witness(lifecycle_store, record.operation_id,
        record.plan, baseline.receipt, digests, root_id=root_id)
    status = witness.inspect()
    complete = (status['inactive_receipts_complete'] and status['activation_receipts_complete']
                and not status['pending'])
    return bool(complete)
