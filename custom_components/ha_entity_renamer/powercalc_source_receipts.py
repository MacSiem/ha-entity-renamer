"""Read-only reconstruction of one witnessed Powercalc source handoff."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
import json
import math

from .native_registry import PreparedRegistryChange
from .native_registry_receipts import _digest, _event_id
from .native_registry_transition import prepared_registry_digest
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_source_receipt_' + reason)


def _composition(chain):
    reference = chain.plan.native_inputs
    if type(reference) is not tuple or len(reference) != 3:
        return None
    row = chain.preparations.plans.get(reference[0])
    payload = None if row is None else row['payload'].get('composition')
    if not (type(payload) is dict and payload.get('profile') == 'powercalc_source_own_binding_v1'):
        return None
    from .powercalc_composition import PowercalcComposition
    baseline, composition = chain.preparations.resolve_composition(reference)
    _need(baseline == chain.baseline and type(composition) is PowercalcComposition,
          'same_original_required')
    return composition


def _expected_producers(chain, composition, change):
    if (type(change) is not PreparedRegistryChange or change.kind != 'entity'
            or not change.mapping or change.before.get('platform') == 'powercalc'):
        return ()
    original = change.before['entity_id']
    graph = json.loads(composition.evidence.powercalc_sources.closed_graph_json)
    profiles = [row for row in graph['profiles'] if row['source'] == original]
    documents = {item.key: json.loads(item.manager_json) for item in chain.baseline.files}
    rows = documents['core.entity_registry']['entities']
    result = []
    for profile in profiles:
        matches = [row for row in rows if row.get('platform') == 'powercalc'
                   and row.get('unique_id') == profile['unique_id']]
        _need(len(matches) == 1 and type(matches[0].get('entity_id')) is str,
              'original_producer_ambiguous')
        result.append(matches[0]['entity_id'])
    _need(len(result) == len(set(result)), 'duplicate_original_producer')
    return tuple(sorted(result))


def validate_source_receipt(chain, payload, change):
    """Reject missing/foreign source evidence during completion and reopen."""
    composition = _composition(chain)
    reference = payload.get('powercalc_source_transition')
    if composition is None:
        _need(reference is None, 'foreign_subeffect')
        return
    expected = _expected_producers(chain, composition, change)
    if not expected:
        _need(reference is None, 'unexpected_subeffect')
        return
    _need(type(reference) is list and len(reference) == 2
          and all(type(item) is str for item in reference), 'source_subeffect_required')
    event = chain.store.read(reference[0])
    _need(event is not None and event['digest'] == reference[1]
          and event['digest'] == _digest(event['payload']), 'source_subeffect_missing')
    value = event['payload']
    _need(type(value) is dict and set(value) == {
        'kind', 'schema', 'root', 'registry_root', 'intent', 'source_chain', 'registry_preparation',
        'registry_uuid', 'before_id', 'after_id', 'mutation', 'native_proof',
        'removed_state', 'added_state', 'calculations'}, 'source_subeffect_shape')
    from .powercalc_lifecycle import _root, validate_reverse_root
    direction = payload['binding']['direction']
    _need(direction in ('forward', 'rollback'), 'source_direction_invalid')
    if direction == 'forward':
        root = _root(chain, composition)
        root_id = _event_id(['powercalc_lifecycle', chain.root_id, chain.plan.fingerprint])
        _need(value['root'] == [root_id, _digest(root)]
              and chain.store.read(root_id) == {'digest': _digest(root), 'payload': root},
              'source_forward_root_changed')
    else:
        validate_reverse_root(chain, value['root'])
        root_id = value['root'][0]
    before = change.after if direction == 'rollback' else change.before
    after = change.before if direction == 'rollback' else change.after
    _need(value['kind'] == 'powercalc_source_transition_receipt'
          and type(value['schema']) is int and value['schema'] == 1
          and value['registry_root'] == chain.root_id and value['intent'] == payload['intent']
          and value['source_chain'] == payload['source_chain']
          and value['registry_preparation'] == prepared_registry_digest(change)
          and value['registry_uuid'] == change.identity
          and value['before_id'] == before['entity_id'] and value['after_id'] == after['entity_id']
          and value['mutation'] == payload['mutation']
          and reference[0] == _event_id([root_id, 'source_transition', change.identity,
                                         direction, 'receipt']), 'source_lineage_changed')
    proof = value['native_proof']
    _need(type(proof) is dict and set(proof) == {'rename_remove', 'rename_add', 'neutral',
          'source_tasks_complete', 'dispatches_complete'} and type(proof['rename_remove']) is int
          and proof['rename_remove'] == 1 and type(proof['rename_add']) is int
          and proof['rename_add'] == 1 and type(proof['neutral']) is bool
          and proof['source_tasks_complete'] is True and proof['dispatches_complete'] is True,
          'native_source_proof_invalid')
    for key, entity_id in (('removed_state', value['before_id']), ('added_state', value['after_id'])):
        snapshot = value[key]
        _need(type(snapshot) is dict and set(snapshot) == {'native_json', 'last_reported'}
              and type(snapshot['native_json']) is str and type(snapshot['last_reported']) is str,
              'native_state_snapshot_invalid')
        from .native_input_baseline import _loads
        state = _loads(snapshot['native_json'].encode('utf-8'))
        _need(type(state) is dict and state.get('entity_id') == entity_id
              and type(state.get('state')) is str and type(state.get('attributes')) is dict,
              'native_state_identity_invalid')
        try:
            reported = datetime.fromisoformat(snapshot['last_reported'])
        except ValueError:
            raise Refused('powercalc_source_receipt_report_time_invalid') from None
        _need(reported.tzinfo is not None and reported.utcoffset() is not None, 'report_time_invalid')
    calculations = value['calculations']
    _need(type(calculations) is list and [row.get('producer') for row in calculations] == list(expected),
          'producer_delivery_incomplete')
    for row in calculations:
        _need(type(row) is dict and set(row) == {'producer', 'value', 'native_power',
              'emission_sequence', 'completion_sequence', 'emitted_at'}
              and type(row['emission_sequence']) is int and row['emission_sequence'] > 0
              and type(row['completion_sequence']) is int and row['completion_sequence'] > 0
              and type(row['emitted_at']) is float and math.isfinite(row['emitted_at']),
              'calculation_shape_invalid')
        try:
            _need(all(type(row[key]) is str and Decimal(row[key]).is_finite()
                      for key in ('value', 'native_power')), 'calculation_value_invalid')
        except InvalidOperation:
            raise Refused('powercalc_source_receipt_calculation_value_invalid') from None
