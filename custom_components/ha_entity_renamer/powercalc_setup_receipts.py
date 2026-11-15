"""Read-only validation of an ORIGINAL-bound Powercalc YAML setup receipt."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .native_input_baseline import _envelope
from .native_registry import PreparedRegistryChange
from .native_registry_receipts import _digest, _event_id
from .powercalc_source_receipts import _composition
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_setup_receipt_' + reason)


def _event(chain, reference, key, kind):
    _need(type(reference) is list and len(reference) == 2 and reference[0] == key,
          'reference_invalid')
    row = chain.store.read(key)
    _need(row is not None and row['digest'] == reference[1] == _digest(row['payload'])
          and row['payload'].get('kind') == kind, 'event_missing_or_changed')
    return row['payload']


def validate_setup_receipt(chain, payload, change):
    from .powercalc_prepared_yaml import PreparedPowercalcYAML
    from .native_template_state_yaml import PreparedMixedPowercalcYAML
    if type(change) is PreparedMixedPowercalcYAML:
        from .controlled_powercalc_setup_receipts import validate_controlled_setup
        validate_controlled_setup(chain, payload, change)
        return
    reference = payload.get('powercalc_setup')
    if type(change) is not PreparedPowercalcYAML:
        _need(reference is None, 'foreign_setup_subeffect')
        return
    composition = _composition(chain)
    direction = payload['binding']['direction']
    _need(composition is not None and direction in ('forward', 'rollback')
          and type(reference) is list and len(reference) == 2, 'direction_and_original_required')
    from .powercalc_lifecycle import _moves, _root, validate_reverse_root
    if direction == 'forward':
        root = _root(chain, composition)
        root_id = _event_id(['powercalc_lifecycle', chain.root_id, chain.plan.fingerprint])
        root_ref = [root_id, _digest(root)]
        _need(chain.store.read(root_id) == {'digest': root_ref[1], 'payload': root},
              'original_lifecycle_root_changed')
        moves = _moves(chain, composition)
    else:
        row = chain.store.read(reference[0])
        _need(row is not None and row['digest'] == reference[1] == _digest(row['payload']),
              'reverse_setup_receipt_missing')
        root_ref = row['payload'].get('root')
        root, _, moves = validate_reverse_root(chain, root_ref)
        root_id = root_ref[0]
    value = _event(chain, reference, _event_id([root_id, 'source_setup', 'receipt']),
                   'powercalc_source_setup_receipt')
    intent_ref = value.get('intent')
    intent = _event(chain, intent_ref, _event_id([root_id, 'source_setup', 'intent']),
                    'powercalc_source_setup_intent')
    _need(set(intent) == {'kind', 'schema', 'root', 'yaml_intent', 'source_chain',
                          'aliases', 'entries', 'original_entities'}
          and set(value) == {'kind', 'schema', 'root', 'intent', 'yaml_intent', 'source_chain',
                             'aliases', 'target_group', 'entities', 'exact_transfers',
                             'producer_operations', 'producer_results', 'clock_steps',
                             'timer_proofs', 'store_clean_calls'}
          and intent['schema'] == value['schema'] == 1
          and intent['root'] == value['root'] == root_ref
          and intent['yaml_intent'] == value['yaml_intent'] == payload['intent']
          and intent['source_chain'] == value['source_chain'] == payload['source_chain']
          and intent['aliases'] == value['aliases'], 'setup_lineage_changed')
    original = intent['original_entities']
    entities = value['entities']
    _need(type(original) is dict and original and type(entities) is dict
          and set(original) == set(entities) and type(intent['entries']) is list
          and intent['entries'] == sorted(set(intent['entries'])), 'inventory_shape')
    original_registry = _envelope(chain.baseline.files[0].raw, 'core.entity_registry',
                                  chain.original_receipt.version)['data']['entities']
    by_id = {row['id']: row for row in original_registry}
    _need(len(by_id) == len(original_registry), 'original_registry_ambiguous')
    changes = [item for item in chain.changes if type(item) is PreparedRegistryChange
               and item.kind == 'entity' and item.mapping]
    forward_mapping = {item.before['entity_id']: item.after['entity_id'] for item in changes}
    mapping = ({new: old for old, new in forward_mapping.items()}
               if direction == 'rollback' else forward_mapping)
    expected_aliases = sorted([new, old] for old, new in mapping.items() if old in original)
    _need(intent['aliases'] == expected_aliases, 'original_aliases_changed')
    seen = set()
    for name, saved in original.items():
        current = entities[name]
        _need(type(saved) is dict and set(saved) == {'uuid', 'unique_id', 'class'}
              and type(current) is dict and saved['uuid'] not in seen,
              'entity_binding_shape')
        row = by_id.get(saved['uuid'])
        _need(row is not None and row.get('platform') == 'powercalc'
              and (forward_mapping.get(row.get('entity_id'), row.get('entity_id'))
                   if direction == 'rollback' else row.get('entity_id')) == name
              and row.get('unique_id') == saved['unique_id']
              and current.get('uuid') == saved['uuid']
              and current.get('unique_id') == saved['unique_id']
              and current.get('class') == saved['class']
              and current.get('entity_id') == mapping.get(name, name), 'entity_uuid_changed')
        seen.add(saved['uuid'])
    _need(len(moves) == 1 and value['target_group'] == moves[0].after
          and moves[0].before in original
          and original[moves[0].before]['uuid'] == composition.previous.groups[0].registry_id
          and type(value['store_clean_calls']) is list
          and value['store_clean_calls'].count(['store_clean', 'start', moves[0].after]) == 1
          and value['store_clean_calls'].count(['store_clean', 'return', moves[0].after]) == 1,
          'native_group_cleanup_unconfirmed')
    positions = chain._setup_positions
    _need(payload['source_chain'] in positions, 'source_prefix_missing')
    for index, step in enumerate(chain.plan.steps):
        if index < payload['binding']['index']:
            _need(positions[payload['source_chain']].get(index) == direction, 'prefix_incomplete')
    transfers = value['exact_transfers']
    _need(type(transfers) is list and {item.get('entity_id') for item in transfers} == {
          name for name, saved in original.items() if saved['class'] == 'VirtualEnergySensor'},
          'exact_transfer_inventory')
    for item in transfers:
        _need(type(item) is dict and item.get('uuid') == original[item['entity_id']]['uuid']
              and item.get('old_live') == item.get('new_after') == item.get('base_start') == item.get('base_return')
              and type(item.get('old_live')) is dict, 'exact_transfer_changed')
        try:
            _need(all(Decimal(item['old_live'][key]).is_finite() for key in ('full', 'last_valid')),
                  'nonfinite_total')
        except (InvalidOperation, KeyError, TypeError):
            raise Refused('powercalc_setup_receipt_nonfinite_total') from None
    _need(all(type(value[key]) is list for key in ('producer_operations', 'producer_results',
                                                   'clock_steps', 'timer_proofs')),
          'native_delivery_proof_shape')
    handoffs = [row[1] for row in value['producer_operations']
                if type(row) is list and len(row) >= 2 and row[0] == 'native_handoff']
    _need(len(handoffs) == len(set(handoffs)) and set(handoffs) == {
          name for name, saved in original.items() if saved['class'] == 'VirtualPowerSensor'},
          'native_producer_handoff_incomplete')
