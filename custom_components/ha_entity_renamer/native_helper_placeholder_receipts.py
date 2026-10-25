"""Pure own-UtilityMeter placeholder evidence in the existing registry receipt.

No native mutation, live authority, new journal, recapture or result-only replay.
The caller explicitly copies the completed escrow witness before complete().
All sources below are ORIGINAL or independently accepted immutable references.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from .native_registry import NativeRegistryMutation, PreparedRegistryChange
from .native_registry_transition import _json, _loads, prepared_registry_digest
from .native_registry_receipts import _sha, _fail


def _own_entry(chain, change):
    """Classify using ORIGINAL registry/config relation, never caller flags."""
    if type(change) is not PreparedRegistryChange or change.kind != 'entity':
        return None
    if change.before.get('entity_id') == change.after.get('entity_id'):
        return None
    documents = {item.key: _loads(item.manager_json) for item in chain.baseline.files}
    rows = [row for row in documents['core.entity_registry']['entities']
            if row.get('id') == change.identity]
    if len(rows) != 1 or _json(rows[0]) != _json(change.before):
        _fail('placeholder_original_registry_binding')
    row = rows[0]
    entry_id = row.get('config_entry_id')
    configs = [entry for entry in documents['core.config_entries']['entries']
               if entry.get('entry_id') == entry_id]
    if len(configs) != 1 or configs[0].get('domain') != 'utility_meter':
        return None
    if row.get('platform') != 'utility_meter' or row.get('unique_id') != entry_id:
        return None  # An ordinary dependency entity, not this helper's own ID.
    if (type(entry_id) is not str or not entry_id or row.get('config_subentry_id') is not None
            or any(type(r.get('entity_id')) is not str or not r['entity_id'].startswith('sensor.')
                   for r in (change.before, change.after))
            or _json({k: v for k, v in change.before.items() if k not in ('entity_id', 'modified_at')})
               != _json({k: v for k, v in change.after.items() if k not in ('entity_id', 'modified_at')})):
        _fail('placeholder_entity_id_only_required')
    return entry_id


def expected_placeholder_scope(chain, payload, change, escrow_root_reference):
    """Rebuild exact scope independently; never read the supplied stamp scope.

    RegistryReceiptChain validates its ordinary intent/context and native
    registry transition in the same completion/reopen call. The copied escrow parser reopens lifecycle
    read-only and validates original receipt/preparations and all unload refs.
    """
    from .native_restore_state_escrow_receipts import _reference, _read, _parse_root, inspect_escrow
    from .native_utility_unload_audit import decode_unload_audit, _payload
    from .native_utility_setup_audit import _SOURCES

    entry_id = _own_entry(chain, change)
    if entry_id is None:
        _fail('placeholder_own_helper_required')
    # Also re-resolve the ORIGINAL here: no mutable cached change is authority.
    phase = payload['binding']
    index, direction = phase['index'], phase['direction']
    if (chain._original_change(index) != change
            or chain._context(chain._historical_context(phase)) != phase
            or direction not in ('forward', 'rollback')
            or payload['chain'] != chain.root_id):
        _fail('placeholder_phase_binding')
    root_ref = [chain.root_id, chain.root_digest]
    if _read(chain.store, root_ref) != chain.root:
        _fail('placeholder_chain_root')
    intent_ref = _reference(payload['intent'])
    intent = _read(chain.store, intent_ref)
    if (intent_ref[0] != chain._slot('intent', index, direction)
            or intent.get('kind') != 'registry_intent'
            or intent.get('chain') != chain.root_id or intent.get('binding') != phase
            or intent.get('sequence') != payload['sequence']
            or intent.get('source_chain') != payload['source_chain']):
        _fail('placeholder_registry_intent')
    digests = {step.prepared_ref: chain.root['bindings'][i]['digest']
               for i, step in enumerate(chain.plan.steps)}
    context = chain.operation_id, chain.plan, chain.original_receipt, digests
    escrow_ref = _reference(escrow_root_reference)
    escrow, _, lifecycle = _parse_root(chain.store, context, escrow_ref)
    status = inspect_escrow(chain.store, *context, escrow_ref)
    eb = escrow['binding']
    if (status['status'] != 'verified' or escrow['direction'] != direction
            or eb['entry_id'] != entry_id or eb['helper_uuid'] != change.identity
            or eb['registry_preparation_sha256'] != prepared_registry_digest(change)
            or eb['original_receipt_sha256'] != _sha(chain.baseline.receipt_json)
            or lifecycle.own_registry_changes.get(entry_id) != change):
        _fail('placeholder_escrow_binding')
    original = [row for row in lifecycle.root['original'] if row['entry_id'] == entry_id]
    if (len(original) != 1 or original[0]['domain'] != 'utility_meter'
            or original[0]['state'] != 'loaded'):
        _fail('placeholder_actual_unload_required')
    unload = _read(chain.store, eb['unload_receipt'])
    if (unload.get('root') != lifecycle.root_id or unload.get('state') != 'not_loaded'
            or unload.get('api_called') is not True):
        _fail('placeholder_unload_binding')
    data = decode_unload_audit(unload['native_unload'])
    doc = _payload(data)  # Includes BOTH manager and writer rows and exact native JSON.
    mutation = NativeRegistryMutation(**payload['mutation'])
    source = change.before if direction == 'forward' else change.after
    target = change.after if direction == 'forward' else change.before
    state = _loads(doc['state_json'].encode('utf-8'))
    if (doc['core'] != chain.version or data.entry_id != entry_id
            or _json(doc['source_row']) != _json(_loads(mutation.before_json.encode()))
            or doc['source_row']['id'] != change.identity
            or state['entity_id'] != source['entity_id']):
        _fail('placeholder_native_unload_source')
    # ID-only change leaves every writer-relevant field unchanged. Preserve
    # attributes from exact accepted native state; do not derive new names.
    before, after = (_loads(raw.encode()) for raw in (mutation.before_json, mutation.after_json))
    if _json({k: v for k, v in before.items() if k not in ('entity_id', 'modified_at')}) != _json(
            {k: v for k, v in after.items() if k not in ('entity_id', 'modified_at')}):
        _fail('placeholder_writer_attributes_changed')
    attrs = deepcopy(state['attributes'])
    return {'version': chain.version, 'source_pins': {key: _SOURCES[chain.version][key]
                for key in ('core.py', 'helpers/entity_registry.py', 'helpers/entity.py')},
        'operation_id': chain.operation_id, 'fingerprint': chain.plan.fingerprint,
        'chain_root': chain.root_id, 'registry_intent': list(intent_ref), 'phase': deepcopy(phase),
        'lifecycle_root': deepcopy(eb['lifecycle_root']),
        'unload_receipt': deepcopy(eb['unload_receipt']), 'entry_id': entry_id,
        'registry_uuid': change.identity, 'prepared_digest': prepared_registry_digest(change),
        'direction': direction, 'source': source['entity_id'], 'target': target['entity_id'],
        'source_attrs': attrs, 'source_state_json': doc['state_json'],
        'source_reserved': doc['reserved'], 'target_attrs': deepcopy(attrs)}


def validate_placeholder_receipt(chain, payload, change):
    """Validate mandatory own-ID subeffect on completion AND immutable reopen."""
    own = _own_entry(chain, change)
    subeffect = payload.get('own_helper_placeholder')
    if own is None:
        if 'own_helper_placeholder' in payload:
            _fail('placeholder_foreign_subeffect')
        return
    if (type(subeffect) is not dict
            or set(subeffect) != {'schema', 'stamp', 'escrow_root'}
            or type(subeffect['schema']) is not int or subeffect['schema'] != 1):
        _fail('placeholder_subeffect_required')
    from .native_helper_placeholder_effect import decode_effect, validate_effect
    scope = expected_placeholder_scope(chain, payload, change, subeffect['escrow_root'])
    stamp = decode_effect(_json(subeffect['stamp']))

    def validated_unload(candidate):
        # Recheck the independently rebuilt exact graph-bound scope; this callback
        # reports historical validity only and cannot be passed as live authority.
        return _json(candidate) == _json(scope)

    validate_effect(stamp, expected_scope=scope, change=change,
                    mutation=NativeRegistryMutation(**payload['mutation']),
                    validate_unload_witness=validated_unload)


def encode_placeholder_subeffect(stamp, escrow_root_reference):
    """Detach typed evidence; caller must have copied escrow closure already."""
    from .native_helper_placeholder_effect import PlaceholderEffectStamp
    from .native_restore_state_escrow_receipts import _reference
    if type(stamp) is not PlaceholderEffectStamp:
        _fail('placeholder_typed_stamp_required')
    return {'schema': 1, 'stamp': {'schema': 'own_helper_placeholder_effect_v1', **asdict(stamp)},
            'escrow_root': list(_reference(escrow_root_reference))}
