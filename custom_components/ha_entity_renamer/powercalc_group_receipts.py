"""Read-only validation of the group-own-ID subeffect in a registry receipt."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime

from .native_registry import PreparedRegistryChange
from .native_registry_receipts import _digest, _event_id
from .native_registry_transition import prepared_registry_digest
from .powercalc_source_receipts import _composition
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_group_receipt_' + reason)


def _event(chain, reference, key, kind):
    _need(type(reference) is list and len(reference) == 2 and reference[0] == key,
          'reference_invalid')
    row = chain.store.read(key)
    _need(row is not None and row['digest'] == reference[1] == _digest(row['payload'])
          and row['payload'].get('kind') == kind, 'event_missing_or_changed')
    return row['payload']


def group_lineage(chain, composition, change, source_head, refs):
    """Validate retained lineage without granting a live mutation capability."""
    from .powercalc_lifecycle import _root, _moves
    groups = [g for g in composition.previous.groups if g.registry_id == change.identity
              and g.entity_id == change.before['entity_id']]
    _need(len(groups) == 1, 'original_group_required')
    group = groups[0]
    moves = [m for m in _moves(chain, composition) if m.before == group.entity_id]
    _need(len(moves) == 1 and moves[0].after == change.after['entity_id'], 'move_changed')
    root = _root(chain, composition)
    root_id = _event_id(['powercalc_lifecycle', chain.root_id, chain.plan.fingerprint])
    root_ref = [root_id, _digest(root)]
    _need(chain.store.read(root_id) == {'digest': root_ref[1], 'payload': root}, 'root_changed')
    _need(type(refs) is dict and set(refs) == {'unload', 'pss', 'restore'}, 'refs_shape')
    unload = _event(chain, refs['unload'], _event_id([root_id, 'unload', group.registry_id, 'receipt']),
                    'powercalc_group_unload_receipt')
    ui = _event(chain, unload['intent'], _event_id([root_id, 'unload', group.registry_id, 'intent']),
                'powercalc_group_unload_intent')
    pss = _event(chain, refs['pss'], _event_id([root_id, 'pss_forward', 'receipt']),
                 'powercalc_pss_forward_receipt')
    pi = _event(chain, pss['intent'], _event_id([root_id, 'pss_forward', 'intent']),
                'powercalc_pss_forward_intent')
    restore = _event(chain, refs['restore'], _event_id([root_id, 'restore_forward', group.entity_id, 'receipt']),
                     'powercalc_group_restore_receipt')
    ri = _event(chain, restore['intent'], _event_id([root_id, 'restore_forward', group.entity_id, 'intent']),
                'powercalc_group_restore_intent')
    restore_schema = 2 if chain.original_receipt.version == '2026.11.0' else 1
    _need(all(row.get('root') == root_ref for row in (unload, ui, pss, pi, restore, ri))
          and all(row.get('schema') == 1 for row in (unload, ui, pss, pi))
          and restore.get('schema') == ri.get('schema') == restore_schema,
          'root_or_schema_changed')
    _need(unload['source_chain'] == ui['source_chain'] and unload['before'] == ui['before']
          and unload['native_state'] == 'not_loaded' and unload['entry_id'] == ui['before']['entry_id']
          and unload['after']['group_uuid'] == group.registry_id
          and unload['after']['group_id'] == group.entity_id
          and unload['entry_id'] == change.before['config_entry_id'], 'unload_binding')
    _need(pss['source_chain'] == pi['source_chain'] and refs['unload'] in pi['unloads']
          and pi['moves'] == root['moves'] and moves[0].after in pss['target']
          and pss['group_totals'].get(group.entity_id) == unload['after']['full_total'], 'pss_binding')
    from .native_restore_state_payload import _canonical, _shape
    _shape(ri['before']); _shape(ri['after'])
    _need(restore_schema == 1 or (ri['before'].get('entity_registry_id') == group.registry_id
          and ri['after'].get('entity_registry_id') == group.registry_id), 'restore_uuid_changed')
    _need(hashlib.sha256(_canonical(ri['before'])).hexdigest() == restore['before_sha256']
          and hashlib.sha256(_canonical(ri['after'])).hexdigest() == restore['after_sha256']
          and ri['before']['state']['entity_id'] == group.entity_id
          and ri['after']['state']['entity_id'] == moves[0].after
          and ri['precision'] == unload['after']['precision']
          and ri['unit'] == unload['after']['unit'], 'restore_payload_changed')
    _need(restore['source_chain'] == ri['source_chain'] and restore['unload'] == ri['unload'] == refs['unload']
          and restore['pss'] == ri['pss'] == refs['pss']
          and restore['full_total'] == ri['full_total'] == unload['after']['full_total'], 'restore_binding')
    # _setup_positions is reconstructed by the normal receipt fold; an earlier
    # prefix must be a subset of the registry intent's accepted prefix.
    positions = chain._setup_positions
    _need(source_head in positions and all(row['source_chain'] in positions
          and all(positions[source_head].get(k) == v for k, v in positions[row['source_chain']].items())
          for row in (unload, pss, restore)), 'unaccepted_prefix')
    return unload


def reverse_group_lineage(chain, change, source_head, refs, root_reference):
    """Validate the exact inverse unload and CAS handoffs on the same root."""
    from .powercalc_lifecycle import validate_reverse_root
    from .native_restore_state_payload import _canonical, _shape
    root, groups, moves = validate_reverse_root(chain, root_reference)
    groups = [group for group in groups if group.registry_id == change.identity
              and group.entity_id == change.after['entity_id']]
    _need(len(groups) == 1, 'reverse_group_required')
    group = groups[0]
    matches = [move for move in moves if move.before == group.entity_id
               and move.after == change.before['entity_id']]
    _need(len(matches) == 1 and type(refs) is dict and set(refs) == {'unload', 'pss', 'restore'},
          'reverse_move_or_refs_changed')
    root_id = root_reference[0]
    unload = _event(chain, refs['unload'], _event_id([root_id, 'unload', group.registry_id, 'receipt']),
                    'powercalc_group_unload_receipt')
    ui = _event(chain, unload['intent'], _event_id([root_id, 'unload', group.registry_id, 'intent']),
                'powercalc_group_unload_intent')
    pss = _event(chain, refs['pss'], _event_id([root_id, 'pss_reverse', 'receipt']),
                 'powercalc_pss_reverse_receipt')
    pi = _event(chain, pss['intent'], _event_id([root_id, 'pss_reverse', 'intent']),
                'powercalc_pss_reverse_intent')
    restore = _event(chain, refs['restore'], _event_id([root_id, 'restore_reverse', group.entity_id, 'receipt']),
                     'powercalc_group_restore_reverse_receipt')
    ri = _event(chain, restore['intent'], _event_id([root_id, 'restore_reverse', group.entity_id, 'intent']),
                'powercalc_group_restore_reverse_intent')
    restore_schema = 2 if chain.original_receipt.version == '2026.11.0' else 1
    _need(all(row.get('root') == list(root_reference) for row in (unload, ui, pss, pi, restore, ri))
          and all(row.get('schema') == 1 for row in (unload, ui, pss, pi))
          and restore.get('schema') == ri.get('schema') == restore_schema, 'reverse_root_changed')
    _need(unload['source_chain'] == ui['source_chain'] and unload['before'] == ui['before']
          and unload['native_state'] == 'not_loaded' and unload['entry_id'] == ui['before']['entry_id']
          and unload['after']['group_uuid'] == group.registry_id
          and unload['after']['group_id'] == group.entity_id
          and unload['entry_id'] == change.after['config_entry_id'], 'reverse_unload_changed')
    _need(pss['source_chain'] == pi['source_chain'] and refs['unload'] in pi['unloads']
          and pi['moves'] == root['moves'] and matches[0].after in pss['target']
          and pss['group_totals'].get(group.entity_id) == unload['after']['full_total'],
          'reverse_pss_changed')
    _shape(ri['before']); _shape(ri['after'])
    _need(restore_schema == 1 or (ri['before'].get('entity_registry_id') == group.registry_id
          and ri['after'].get('entity_registry_id') == group.registry_id), 'restore_uuid_changed')
    _need(hashlib.sha256(_canonical(ri['before'])).hexdigest() == restore['before_sha256']
          and hashlib.sha256(_canonical(ri['after'])).hexdigest() == restore['after_sha256']
          and ri['before']['state']['entity_id'] == group.entity_id
          and ri['after']['state']['entity_id'] == matches[0].after
          and ri['precision'] == unload['after']['precision'] and ri['unit'] == unload['after']['unit']
          and restore['source_chain'] == ri['source_chain']
          and restore['unload'] == ri['unload'] == refs['unload']
          and restore['pss'] == ri['pss'] == refs['pss']
          and restore['full_total'] == ri['full_total'] == unload['after']['full_total'],
          'reverse_restore_changed')
    positions = chain._setup_positions
    _need(source_head in positions and all(row['source_chain'] in positions
          and all(positions[source_head].get(k) == v for k, v in positions[row['source_chain']].items())
          for row in (unload, pss, restore)), 'reverse_prefix_changed')
    return unload


def validate_group_receipt(chain, payload, change):
    ref = payload.get('powercalc_group_own_id')
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    composition = _composition(chain)
    if type(composition) is ControlledPowercalcComposition:
        from .controlled_powercalc_setup_receipts import validate_handoff
        from .native_registry_transition import _powercalc_group_suggested_move
        direction = payload['binding']['direction']
        handoff = validate_handoff(chain, payload.get('controlled_powercalc_handoff'), direction)
        controlled = payload.get('controlled_powercalc_group')
        is_group = (type(change) is PreparedRegistryChange and change.kind == 'entity'
                    and change.mapping and change.before.get('platform') == 'powercalc')
        _need(ref is None, 'foreign_live_group_subeffect')
        if not is_group:
            _need(controlled is None, 'unexpected_controlled_group_subeffect')
            return
        _need(_powercalc_group_suggested_move(change.before, change.after)
              and type(controlled) is list and len(controlled) == 2, 'controlled_group_scope')
        key = _event_id(['controlled_pc_group', chain.root_id, payload['binding']['index'],
                         direction, 'receipt'])
        value = _event(chain, controlled, key, 'controlled_powercalc_group_receipt')
        ids = (change.before['entity_id'], change.after['entity_id'])
        if direction == 'rollback':
            ids = ids[::-1]
        _need(value.get('schema') == 1 and value.get('root') == chain.root_id
              and value.get('operation_id') == chain.operation_id
              and value.get('fingerprint') == chain.plan.fingerprint
              and value.get('direction') == direction
              and value.get('phase_index') == payload['binding']['index']
              and value.get('intent') == payload['intent']
              and value.get('handoff') == payload['controlled_powercalc_handoff']
              and value.get('registry_uuid') == change.identity
              and value.get('registry_preparation', value.get('prepared_digest')) == prepared_registry_digest(change)
              and value.get('mutation') == payload['mutation']
              and any((row['uuid'], row['kind'], row['before_id'], row['after_id']) ==
                      (change.identity, 'group', *ids) for row in handoff['population']),
              'controlled_group_lineage')
        return
    if (type(change) is not PreparedRegistryChange or change.kind != 'entity'
            or not change.mapping or change.before.get('platform') != 'powercalc'):
        _need(ref is None, 'unexpected_subeffect')
        return
    composition = _composition(chain)
    groups = [] if composition is None or type(change) is not PreparedRegistryChange else [
        g for g in composition.previous.groups if change.kind == 'entity' and change.mapping
        and g.registry_id == change.identity and g.entity_id == change.before['entity_id']]
    if not groups:
        _need(ref is None, 'unexpected_subeffect')
        return
    direction = payload['binding']['direction']
    _need(direction in ('forward', 'rollback') and type(ref) is list and len(ref) == 2,
          'group_direction_or_reference')
    event = chain.store.read(ref[0])
    _need(event is not None and event['digest'] == ref[1] == _digest(event['payload']),
          'group_subeffect_missing')
    root_reference = event['payload'].get('root')
    if direction == 'forward':
        from .powercalc_lifecycle import _root
        root_id = _event_id(['powercalc_lifecycle', chain.root_id, chain.plan.fingerprint])
        _need(root_reference == [root_id, _digest(_root(chain, composition))], 'forward_root_changed')
    else:
        from .powercalc_lifecycle import validate_reverse_root
        validate_reverse_root(chain, root_reference)
        root_id = root_reference[0]
    value = _event(chain, ref, _event_id([root_id, 'group_own_id', change.identity, direction, 'receipt']),
                   'powercalc_group_own_id_receipt')
    _need(set(value) == {'kind', 'schema', 'root', 'registry_root', 'intent', 'source_chain',
          'registry_preparation', 'registry_uuid', 'before_id', 'after_id', 'mutation', 'refs',
          'version', 'source_pins', 'before_state', 'before_reported', 'before_attrs',
          'after_state', 'after_reported', 'after_attrs', 'source_reserved', 'target_reserved'},
          'shape')
    before = change.after if direction == 'rollback' else change.before
    after = change.before if direction == 'rollback' else change.after
    _need(value['schema'] == 1 and value['root'] == root_reference
          and value['registry_root'] == chain.root_id and value['intent'] == payload['intent']
          and value['source_chain'] == payload['source_chain']
          and value['registry_preparation'] == prepared_registry_digest(change)
          and value['registry_uuid'] == change.identity
          and value['before_id'] == before['entity_id'] and value['after_id'] == after['entity_id']
          and value['mutation'] == payload['mutation'], 'lineage_changed')
    unload = (reverse_group_lineage(chain, change, value['source_chain'], value['refs'], root_reference)
              if direction == 'rollback' else
              group_lineage(chain, composition, change, value['source_chain'], value['refs']))
    _need(value['before_state'] == unload['placeholder_json']
          and value['before_reported'] == unload['placeholder_last_reported'], 'unload_state_changed')
    from .native_helper_placeholder_effect import _state_shape
    from .native_utility_setup_audit import _SOURCES
    version = value['version']
    _need(version in ('2026.10.2', '2026.10.4') and version == chain.original_receipt.version
          and value['source_pins'] == {key: _SOURCES[version][key] for key in
             ('core.py', 'helpers/entity_registry.py', 'helpers/entity.py')}, 'source_pins')
    for side in ('before', 'after'):
        _state_shape(value[side + '_state'], value[side + '_id'], value[side + '_attrs'])
        stamp = datetime.fromisoformat(value[side + '_reported'])
        _need(stamp.tzinfo is not None and stamp.utcoffset() is not None, 'report_time')
    _need(type(value['source_reserved']) is bool and value['target_reserved'] is False, 'reservation')
    before, after = (json.loads(value['mutation'][k]) for k in ('before_json', 'after_json'))
    _need(before['id'] == after['id'] == change.identity and before['entity_id'] == value['before_id']
          and after['entity_id'] == value['after_id'], 'mutation_rows')
