"""Narrow missing lifecycle ACK after a durably audited numeric Template setup.

No native effect replay, writer promotion, journal transition or new root.
A matching live runtime alone never resolves an intent. The existing complete
native setup receipt must independently authenticate the historic API effect.
"""
from dataclasses import dataclass

from .prepared import canonical, native_prepared_store
from .journal import native_journal
from .native_registry_receipts import native_registry_receipt_store
from .native_helper_lifecycle import RestoreAuthorization, native_helper_lifecycle_store
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('template_setup_recovery_' + reason) from None


@dataclass(frozen=True)
class SetupRecoveryClassification:
    # This describes native application, NOT general readiness or an API grant.
    status: str
    reason: str


def setup_ack_candidate(session, forward):
    """Return the one missing marker, only from a fully folded native receipt.

    The supplied objects are INTERNAL freshly opened readers. This pure graph
    check does not authorize a write; installed admission still proves current
    physical inputs, consumer, finite histories, authorization and exclusion.
    """
    s = session
    status = forward.inspect()
    _need(not forward.writable and status['inactive_receipts_complete']
          and forward.ids == (s.entry_id,) and forward.source_authorization is None
          and forward.root['schema'] == 1
          and forward.root['original'][0]['domain'] == 'template'
          and forward.root['original'][0]['state'] == 'loaded', 'qualified_forward_scope')
    if not status['pending'] and status['activation_receipts_complete']:
        s._forward_witness()
        return None
    _need(status['pending'] == [{'stage': 'setup', 'entry_id': s.entry_id}]
          and status['setup'] == {s.entry_id: 'unknown'}, 'single_setup_ack_required')
    intent_key = forward._slot('setup', s.entry_id, 'intent')
    intent = s.lifecycle_store.read(intent_key)
    marker_key = forward._slot('setup', s.entry_id, 'receipt')
    _need(intent is not None and s.lifecycle_store.read(marker_key) is None,
          'pending_setup_required')
    # reconstruct_inputs validates full native transitions + typed setup audit,
    # rejects every pending native intent and preserves the same ORIGINAL.
    state = s.chain.reconstruct_inputs()
    _need(state.head == s.head, 'head_changed')
    matches = [(key, row) for key, row in s.receipt_store.rows.items()
        if row['payload'].get('kind') == 'helper_setup_receipt'
        and row['payload'].get('activation', {}).get('root') ==
            [forward.root_id, canonical(forward.root)[1]]
        and row['payload']['activation'].get('entry_id') == s.entry_id]
    _need(len(matches) == 1, 'audited_native_effect_required')
    effect_key, effect = matches[0]
    payload = effect['payload']
    activation = payload['activation']
    _need(effect['digest'] == state.head
          and payload['binding'] == {'operation_id': s.operation_id,
              'fingerprint': s.fingerprint, 'direction': 'forward',
              'status': 'applied', 'phases': list(s.record.phases)}
          and activation['setup_intent'] == [intent_key, intent['digest']]
          and activation['authorization'] == intent['payload']['authorization']
          and activation['original'] == forward.root['original'][0]
          and activation['source_authorization'] is None, 'native_effect_binding')
    for key, digest in activation['witness']:
        copied = s.receipt_store.read(key)
        _need(copied is not None and copied['digest'] == digest
              and s.lifecycle_store.read(key) == copied, 'lifecycle_witness_changed')
    s.chain.verify_setup_authorization(RestoreAuthorization(**activation['authorization']))
    marker = {'kind': 'helper_lifecycle_receipt', 'root': forward.root_id,
        'intent': intent['digest'], 'stage': 'setup', 'entry_id': s.entry_id,
        'api_called': True, 'state': 'loaded', 'native_effect': [effect_key, effect['digest']]}
    return marker_key, marker


async def check_recovery_disk(s):
    """Fresh verified disk readers after admission IO, never cached authority."""
    p, j = native_prepared_store(s.hass), native_journal(s.hass)
    await p.initialize()
    s._scope()
    await j.initialize()
    s._scope()
    _need(p.plans == s.preparations.plans and j.records == s.journal.records,
          'preparations_or_journal_changed')
    for factory, current in ((native_registry_receipt_store, s.receipt_store),
                             (native_helper_lifecycle_store, s.lifecycle_store)):
        reopened = factory(s.hass, p)
        await reopened.initialize()
        s._scope()
        _need(reopened.rows == current.rows, 'receipt_graph_changed')
