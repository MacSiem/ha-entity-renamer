"""Offline fold of native Utility and own-ID Integration setup effects into the original receipt chain.

No HA calls, saves of native registries, authorization grants, baseline capture,
replay, timestamp normalization or result-only adoption. A typed native audit is
necessary, but the caller still owns API authorization, physical write provenance,
full independent reads, exclusion and runtime verification through receipt ACK.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import re
import stat

ENTITY = 'core.entity_registry'


def _api():
    # Lazy imports avoid the RegistryReceiptChain/module dependency cycle.
    from . import native_registry_receipts as api
    return api


def _fail(reason):
    _api()._fail('helper_setup_' + reason)


@dataclass(frozen=True)
class HelperSetupIntent:
    """ACKed live object identity; a reference alone never permits completion."""
    reference: tuple[str, str]
    lifecycle: object
    entry_id: str


def terminal_binding(chain, context, completed):
    api = _api()
    if (type(context) is not api.PhaseContext or context.index != 0
            or context.record.operation_id != chain.operation_id
            or context.record.plan != chain.plan):
        _fail('terminal_binding')
    direction = context.record.direction
    status, phase = {'forward': ('applied', 'verified'),
                     'rollback': ('rolled_back', 'undone')}.get(direction, (None, None))
    if (status is None or context.record.status != status
            or context.record.phases != (phase,) * len(chain.plan.steps)
            or completed != {i: direction for i in range(len(chain.plan.steps))}):
        _fail('terminal_scope')
    return {'operation_id': chain.operation_id, 'fingerprint': chain.plan.fingerprint,
            'direction': direction, 'status': status,
            'phases': list(context.record.phases)}


def setup_slot(chain, kind, activation):
    return _api()._event_id([chain.root_id, 'helper_setup', kind,
        activation['root'][0], activation['entry_id']])


def _preparations(chain):
    return {binding['ref']: binding['digest'] for binding in chain.root['bindings']}


def _witness_lifecycle(chain, activation):
    """Owner supplies pure readonly reopen, never fake IO callbacks.

    validate_lifecycle_receipt_witness is deliberately a required integration
    hook. Absence refuses import/use; no fallback constructs an unvalidated root.
    """
    from .native_helper_lifecycle import (RestoreAuthorization,
        validate_lifecycle_receipt_witness)
    api = _api()
    refs = activation['witness']
    if (type(refs) is not list or not refs or len(refs) > api.MAX_EVENTS
            or any(type(item) is not list or len(item) != 2
                   or any(type(v) is not str for v in item) for item in refs)
            or len({item[0] for item in refs}) != len(refs)):
        _fail('witness_layout')
    for ref in refs:
        if (type(ref) is not list or len(ref) != 2
                or chain.store.read(ref[0]) is None
                or chain.store.read(ref[0])['digest'] != ref[1]):
            _fail('witness_ref')
        row = chain.store.read(ref[0])['payload']
        if ('chain' in row or ref[0] != activation['root'][0]
                and row.get('root') != activation['root'][0]):
            _fail('witness_scope')
    if activation['root'] not in refs or activation['setup_intent'] not in refs:
        _fail('witness_missing')
    source = activation['source_authorization']
    obj = validate_lifecycle_receipt_witness(chain.store, chain.operation_id,
        chain.plan, chain.original_receipt, _preparations(chain),
        root_id=activation['root'][0],
        source_authorization=RestoreAuthorization(**source) if source is not None else None)
    if (obj.writable or obj.root_id != activation['root'][0]
            or (asdict(obj.source_authorization) if obj.source_authorization else None)
                != activation['source_authorization']):
        _fail('witness_reopen')
    return obj


def _activation(chain, lifecycle, entry_id, *, live):
    from .native_helper_lifecycle import NativeHelperLifecycle
    from .utility_meter_observers import encode_runtime_baseline
    api = _api()
    if (type(lifecycle) is not NativeHelperLifecycle
            or type(lifecycle.store) is not api.RegistryReceiptStore
            or lifecycle.operation_id != chain.operation_id or lifecycle.plan != chain.plan
            or lifecycle.receipt != chain.original_receipt
            or lifecycle.binding['preparations'] != _preparations(chain)
            or lifecycle.binding['native_inputs'] != list(chain.plan.native_inputs)):
        _fail('lifecycle_binding')
    if live:
        if (not lifecycle.writable or lifecycle.failed
                or lifecycle.store is chain.store
                or lifecycle.store.preparations is not chain.preparations):
            _fail('lifecycle_not_live')
        lifecycle._authorize()
    lifecycle.store._ready()
    status = lifecycle.inspect()
    if status['pending'] != [{'stage': 'setup', 'entry_id': entry_id}]:
        _fail('lifecycle_setup_intent_required')
    root_ref = [lifecycle.root_id, api._digest(lifecycle.root)]
    root_row = lifecycle.store.read(root_ref[0])
    if root_row != {'digest': root_ref[1], 'payload': lifecycle.root}:
        _fail('lifecycle_root')
    originals = [row for row in lifecycle.root['original'] if row['entry_id'] == entry_id]
    if (len(originals) != 1 or originals[0]['domain'] not in ('utility_meter', 'integration')
            or originals[0]['state'] != 'loaded'):
        _fail('unsupported_activation')
    key = lifecycle._slot('setup', entry_id, 'intent')
    row = lifecycle.store.read(key)
    if (row is None or row['payload'].get('stage') != 'setup'
            or row['payload'].get('entry_id') != entry_id
            or row['payload'].get('original') != originals[0]):
        _fail('lifecycle_setup_binding')
    values = [value for value in lifecycle.runtime_baselines() if value.entry_id == entry_id]
    if originals[0]['domain'] == 'integration':
        from .native_integration_setup_audit import activation_baseline
        if values:
            _fail('integration_does_not_use_utility_baseline')
        encoded = activation_baseline(chain, lifecycle, entry_id)
    else:
        if len(values) != 1:
            _fail('runtime_baseline_required')
        encoded = encode_runtime_baseline(values[0])
    # Rebind the immutable typed counter and original registry preparation.
    if lifecycle._bind_runtime_baselines(lifecycle.root['original'],
            lifecycle.runtime_baselines()) != lifecycle.root.get('runtime_baselines', []):
        _fail('runtime_baseline_binding')
    return {'entry_id': entry_id, 'root': root_ref,
            'setup_intent': [key, row['digest']],
            'original': deepcopy(originals[0]), 'baseline': encoded,
            'authorization': deepcopy(row['payload']['authorization']),
            'source_authorization': (asdict(lifecycle.source_authorization)
                                     if lifecycle.source_authorization else None)}


def _validate_activation(chain, activation):
    if (type(activation) is not dict or set(activation) != {
            'entry_id', 'root', 'setup_intent', 'original', 'baseline',
            'authorization', 'source_authorization', 'witness'}):
        _fail('activation_layout')
    obj = _witness_lifecycle(chain, activation)
    # A later setup for another helper may have copied a later lifecycle graph.
    # This entry's historical pending intent is authenticated directly; graph
    # validation above still checks every event, rather than ignoring later rows.
    root = obj.store.read(activation['root'][0])
    intent = obj.store.read(activation['setup_intent'][0])
    from .utility_meter_observers import encode_runtime_baseline
    if (root is None or root['digest'] != activation['root'][1]
            or intent is None or intent['digest'] != activation['setup_intent'][1]
            or intent['payload'].get('root') != obj.root_id
            or intent['payload'].get('stage') != 'setup'
            or intent['payload'].get('entry_id') != activation['entry_id']
            or intent['payload'].get('original') != activation['original']
            or intent['payload'].get('authorization') != activation['authorization']
            or activation['original'] not in obj.root['original']
            or activation['original']['domain'] not in ('utility_meter', 'integration')
            or activation['original']['state'] != 'loaded'):
        _fail('activation_binding')
    values = [value for value in obj.runtime_baselines()
              if value.entry_id == activation['entry_id']]
    if activation['original']['domain'] == 'integration':
        from .native_integration_setup_audit import activation_baseline
        if values or activation_baseline(chain, obj, activation['entry_id']) != activation['baseline']:
            _fail('integration_restore_binding')
        return obj, activation['baseline']
    if len(values) != 1 or encode_runtime_baseline(values[0]) != activation['baseline']:
        _fail('runtime_baseline_binding')
    return obj, values[0]


def _auth_shape(chain, auth, binding):
    if (type(auth) is not dict or set(auth) != {'operation_id', 'fingerprint', 'head', 'status'}
            or auth['operation_id'] != chain.operation_id
            or auth['fingerprint'] != chain.plan.fingerprint
            or auth['status'] != binding['status']
            or type(auth['head']) is not str or not re.fullmatch(r'[0-9a-f]{64}', auth['head'])):
        _fail('authorization_binding')


def authorization_matches(chain, authorization, *, completed, head, history):
    """Anchor may advance ONLY through this cycle's already validated setups."""
    from .native_helper_lifecycle import RestoreAuthorization
    if type(authorization) is not RestoreAuthorization:
        _fail('authorization_type')
    direction = {'applied': 'forward', 'rolled_back': 'rollback'}.get(authorization.status)
    if (direction is None or completed != {i: direction for i in range(len(chain.plan.steps))}
            or authorization.operation_id != chain.operation_id
            or authorization.fingerprint != chain.plan.fingerprint):
        _fail('authorization_scope')
    if head == authorization.head:
        return True
    tail = []
    cursor = head
    for receipt_head, payload in reversed(history):
        if receipt_head != cursor:
            break
        if payload['kind'] != 'helper_setup_receipt':
            break
        if payload['activation']['authorization'] != asdict(authorization):
            break
        tail.append(payload)
        cursor = payload['source_chain']
        if cursor == authorization.head:
            roots = {row['activation']['root'][0] for row in tail}
            if len(roots) == 1:
                return True
            break
    _fail('authorization_anchor')


def validate_intent(chain, intent, key, states, head, sequence, completed, history):
    api = _api()
    if set(intent) != {'kind', 'chain', 'binding', 'activation', 'source_chain',
                       'sequence', 'source'}:
        _fail('intent_layout')
    binding, activation = intent['binding'], intent['activation']
    from .transaction import Record, PhaseContext
    context = PhaseContext(Record(chain.operation_id, chain.plan, binding['status'],
        tuple(binding['phases']), direction=binding['direction']), 0)
    if terminal_binding(chain, context, completed) != binding:
        _fail('intent_binding')
    if (intent['source_chain'] != head or intent['source'] != chain._summary(states)
            or type(intent['sequence']) is not int or intent['sequence'] != sequence
            or key != setup_slot(chain, 'intent', activation)):
        _fail('intent_source')
    _validate_activation(chain, activation)
    auth = activation['authorization']
    _auth_shape(chain, auth, binding)
    from .native_helper_lifecycle import RestoreAuthorization
    authorization_matches(chain, RestoreAuthorization(**auth), completed=completed,
                          head=head, history=history)
    if auth['head'] != head:
        # The live lifecycle root must be the same root as the accepted tail.
        prior = history[-1][1]
        if prior['activation']['root'] != activation['root']:
            _fail('setup_cycle_changed')
    # Forward setup belongs to the original cycle, reverse to its own source.
    source = activation['source_authorization']
    if (binding['direction'] == 'forward' and source is not None
            or binding['direction'] == 'rollback' and
                (type(source) is not dict or source.get('status') != 'applied')):
        _fail('setup_cycle_direction')
    if source is not None:
        _auth_shape(chain, source, dict(binding, status='applied'))
        forward_scope = {i: 'forward' for i in range(len(chain.plan.steps))}
        forward_heads = [digest for digest, _ in history
                         if chain._setup_positions.get(digest) == forward_scope]
        if not forward_heads or source['head'] != forward_heads[-1]:
            _fail('reverse_source_anchor')


def fold_receipt(chain, states, intent, receipt):
    """Reproduce every document from ORIGINAL+accepted transitions+typed audit."""
    api = _api()
    if (set(receipt) != {'kind', 'chain', 'binding', 'activation', 'source_chain',
                        'sequence', 'intent', 'audit', 'source', 'result'}
            or receipt['source'] != chain._summary(states)
            or receipt['activation'] != intent['activation']):
        _fail('receipt_layout')
    obj, baseline = _validate_activation(chain, intent['activation'])
    entry_id = intent['activation']['entry_id']
    before_snapshot, after_snapshot = obj.helper_snapshots[entry_id]
    snapshot = after_snapshot if intent['binding']['direction'] == 'forward' else before_snapshot
    own_change = obj.own_registry_changes.get(entry_id)
    from .native_utility_setup_audit import decode_setup_audit, validate_setup_audit
    rows = states[ENTITY]['document']['data']['entities']
    indices = [i for i, row in enumerate(rows) if row.get('config_entry_id') == entry_id]
    source = [deepcopy(rows[i]) for i in indices]
    if intent['activation']['original']['domain'] == 'integration':
        from .native_integration_setup_audit import (decode_setup_audit as decode_integration,
                                                  validate_setup_audit as validate_integration)
        audit = decode_integration(receipt['audit'])
        final = validate_integration(baseline, source, audit, core=chain.version, entry_id=entry_id)
    else:
        audit = decode_setup_audit(receipt['audit'])
        final = validate_setup_audit(baseline, snapshot, source, audit,
            own_registry_change=own_change,
            direction=intent['binding']['direction'] if own_change else None)
    if (type(final) is not list or len(final) != len(source)
            or any(type(row) is not dict for row in final)
            or [row.get('id') for row in final] != [row.get('id') for row in source]):
        _fail('audit_row_scope')
    result = deepcopy(states)
    for index, row in zip(indices, final):
        result[ENTITY]['document']['data']['entities'][index] = deepcopy(row)
    observed = receipt['result']
    if type(observed) is not dict or set(observed) != set(api.KEYS):
        _fail('result_layout')
    for key in api.KEYS:
        summary = observed[key]
        if type(summary) is not dict or set(summary) != {'sha256', 'identity', 'document_sha256'}:
            _fail('result_layout')
        identity = summary['identity']
        if (type(identity) is not list or len(identity) != 3
                or any(type(v) is not int or v < 0 for v in identity)
                or identity[1] == 0 or not stat.S_ISREG(identity[2])
                or identity[0] != states[key]['identity'][0]
                or identity[2] != states[key]['identity'][2]
                or type(summary['sha256']) is not str
                or not re.fullmatch(r'[0-9a-f]{64}', summary['sha256'])):
            _fail('physical_result')
        if key != ENTITY and summary != chain._summary(states)[key]:
            _fail('foreign_native_delta')
        # Inode/hash are pinned result observations, never an effect stamp. Installed
        # trusted IO must flush/read under the same exclusion, as for registry
        # receipts. These observations alone do not establish that provenance.
        result[key]['sha256'] = summary['sha256']
        result[key]['identity'] = identity
    if chain._summary(result) != observed:
        _fail('reconstruction_mismatch')
    return result


async def _copy_witness(chain, lifecycle):
    api = _api()
    refs = []
    for key, row in tuple(lifecycle.store.rows.items()):
        payload = row['payload']
        if key != lifecycle.root_id and payload.get('root') != lifecycle.root_id:
            continue
        if 'chain' in payload or row['digest'] != api._digest(payload):
            _fail('witness_scope')
        # Idempotence only for exact immutable copied events; no effect replay.
        existing = chain.store.read(key)
        if existing is None:
            await chain.store.create(key, payload)
            existing = chain.store.read(key)
        if existing != row:
            _fail('witness_conflict')
        refs.append([key, row['digest']])
    return sorted(refs)


async def begin(chain, context, observations, *, lifecycle, entry_id):
    if not chain.writable:
        _fail('reopen_read_only')
    states, head, sequence, completed, pending = chain._reconstruct()
    if pending is not None or chain.active is not None:
        _fail('unknown_unresolved_intent')
    binding = terminal_binding(chain, context, completed)
    chain._observe(observations, states)
    activation = _activation(chain, lifecycle, entry_id, live=True)
    activation['witness'] = await _copy_witness(chain, lifecycle)
    # Recheck after each awaited copy batch; immutable facts and live guard must
    # still match. Receipt-store ACK is followed by a separate source check API.
    if _activation(chain, lifecycle, entry_id, live=True) != {
            k: v for k, v in activation.items() if k != 'witness'}:
        _fail('lifecycle_changed')
    current = chain._reconstruct()
    if current != (states, head, sequence, completed, None):
        _fail('source_changed')
    payload = {'kind': 'helper_setup_intent', 'chain': chain.root_id,
        'binding': binding, 'activation': activation, 'source_chain': head,
        'sequence': sequence, 'source': chain._summary(states)}
    validate_intent(chain, payload, setup_slot(chain, 'intent', activation),
                    states, head, sequence, completed, chain._setup_history)
    ref = await chain.store.create(setup_slot(chain, 'intent', activation), payload)
    chain.active = HelperSetupIntent(ref, lifecycle, entry_id)
    chain._cache = None
    return chain.active


def verify_source(chain, intent, context, observations):
    if (not chain.writable or type(intent) is not HelperSetupIntent
            or intent is not chain.active):
        _fail('intent_not_live')
    states, _, _, completed, pending = chain._reconstruct()
    row = chain.store.read(intent.reference[0])
    if (pending != intent.reference or row['digest'] != intent.reference[1]
            or terminal_binding(chain, context, completed) != row['payload']['binding']):
        _fail('intent_binding')
    activation = _activation(chain, intent.lifecycle, intent.entry_id, live=True)
    if activation != {k: v for k, v in row['payload']['activation'].items() if k != 'witness'}:
        _fail('lifecycle_changed')
    chain._observe(observations, states)


async def complete(chain, intent, context, before, after, *, audit):
    verify_source(chain, intent, context, before)
    api = _api()
    states, head, sequence, completed, _ = chain._reconstruct()
    source = chain.store.read(intent.reference[0])['payload']
    if (type(after) is not tuple or len(after) != 3
            or any(type(obs) is not api.RegistryRawObservation for obs in after)
            or tuple(obs.key for obs in after) != api.KEYS):
        _fail('observations_invalid')
    result = {obs.key: {'document': api._loads(obs.raw), 'sha256': api._sha(obs.raw),
                       'identity': list(obs.identity)} for obs in after}
    chain._observe(after, result)
    from .native_utility_setup_audit import encode_setup_audit
    from .native_integration_setup_audit import (IntegrationSetupAuditData,
                                               encode_setup_audit as encode_integration)
    encoded_audit = (encode_integration(audit) if type(audit) is IntegrationSetupAuditData
                     else encode_setup_audit(audit))
    payload = {'kind': 'helper_setup_receipt', 'chain': chain.root_id,
        'binding': source['binding'], 'activation': source['activation'],
        'source_chain': head, 'sequence': sequence, 'intent': list(intent.reference),
        'source': chain._summary(states), 'result': chain._summary(result),
        'audit': encoded_audit}
    if fold_receipt(chain, states, source, payload) != result:
        _fail('reconstruction_mismatch')
    ref = await chain.store.create(setup_slot(chain, 'receipt', source['activation']), payload)
    chain.active = None
    chain._cache = None
    chain._reconstruct()
    return ref


def verify_authorization(chain, authorization):
    states, head, _, completed, pending = chain._reconstruct()
    if pending is not None:
        _fail('unknown_unresolved_intent')
    return authorization_matches(chain, authorization, completed=completed,
                                 head=head, history=chain._setup_history)

