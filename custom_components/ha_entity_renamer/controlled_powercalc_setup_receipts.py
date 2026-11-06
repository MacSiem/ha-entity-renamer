"""Reconstruct the controlled population setup under the same YAML intent."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .native_registry_receipts import _digest, _event_id, _exact_json_equal, _freeze_json
from .transaction import Refused


def _need(value):
    if not value:
        raise Refused('controlled_powercalc_setup_receipt_invalid')


def _event(chain, reference, key, kind):
    _need(type(reference) is list and len(reference) == 2 and reference[0] == key)
    row = chain.store.read(key)
    _need(row is not None and row['digest'] == reference[1] == _digest(row['payload'])
          and row['payload'].get('kind') == kind)
    return row['payload']


def validate_handoff(chain, reference, direction):
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
    _need(baseline == chain.baseline and type(composition) is ControlledPowercalcComposition
          and direction in ('forward', 'rollback'))
    handoff = _event(chain, reference,
                     _event_id(['controlled_pc_population', chain.root_id, direction, 'receipt']),
                     'controlled_powercalc_population_receipt')
    unload = _event(chain, handoff.get('intent'),
                    _event_id(['controlled_pc_population', chain.root_id, direction, 'intent']),
                    'controlled_powercalc_population_intent')
    common = (chain.root_id, chain.operation_id, chain.plan.fingerprint, direction)
    uuid_restore = chain.original_receipt.version == '2026.11.0'
    _need(all((event.get('root'), event.get('operation_id'), event.get('fingerprint'), event.get('direction'))
              == common and type(event.get('schema')) is int
              for event in (handoff, unload))
          and unload['schema'] == 1 and handoff['schema'] == (2 if uuid_restore else 1)
          and handoff.get('source_head') == unload.get('source_head')
          and unload.get('source_head') in chain._setup_positions
          and type(unload.get('phase_index')) is int
          and 0 <= unload['phase_index'] < len(chain.plan.steps))
    expected_prefix = ({} if direction == 'forward' else
                       {index: 'forward' for index in range(len(chain.plan.steps))})
    _need(chain._setup_positions[unload['source_head']] == expected_prefix)
    mapping = dict(composition.mapping)
    before = (composition.population if direction == 'forward' else
              tuple(sorted((mapping.get(eid, eid), uid, kind) for eid, uid, kind in composition.population)))
    _need(unload.get('population') == [list(row) for row in before])
    directional = mapping if direction == 'forward' else {new: old for old, new in mapping.items()}
    expected = {(uid, eid, directional.get(eid, eid),
                 'integration' if kind == 'energy_integral' else 'group')
                for eid, uid, kind in before if kind in ('energy_integral', 'energy_positive_delta')}
    rows = handoff.get('population')
    _need(type(rows) is list and len(rows) == len(expected) and len(expected) > 0
          and all(type(row) is dict and set(row) == {'uuid', 'before_id', 'after_id', 'kind', 'payload'}
                  and type(row['payload']) is dict for row in rows)
          and {(row['uuid'], row['before_id'], row['after_id'], row['kind']) for row in rows} == expected)
    owned_restore = ({new for _, _, new, _ in expected} if uuid_restore else
                     {name for _, old, new, _ in expected for name in (old, new)})
    owned_pss = {name for _, old, new, kind in expected if kind == 'group' for name in (old, new)}
    _need(type(handoff.get('restore_owned')) is dict and set(handoff['restore_owned']) == owned_restore
          and type(handoff.get('pss_owned')) is dict and set(handoff['pss_owned']) == owned_pss)
    from .native_restore_state_payload import deserialize_integration_payload, _canonical
    from .powercalc_group_restore import PowercalcGroupRestorePayload, materialize
    for row in rows:
        value = row['payload']
        if uuid_restore:
            _need(type(value.get('before')) is dict and type(value.get('after')) is dict
                  and value['before'].get('entity_registry_id') == row['uuid']
                  and value['after'].get('entity_registry_id') == row['uuid']
                  and _exact_json_equal(handoff['restore_owned'][row['after_id']],
                                        _freeze_json(value['after'])))
        if row['kind'] == 'integration':
            decoded = deserialize_integration_payload(_canonical(value))
            _need(__import__('json').loads(decoded.capture_json)['helper_uuid'] == row['uuid']
                  and __import__('json').loads(decoded.before_json)['state']['entity_id'] == row['before_id']
                  and __import__('json').loads(decoded.after_json)['state']['entity_id'] == row['after_id'])
        else:
            _need(set(value) == {'before', 'after', 'full_total', 'precision', 'unit'})
            decoded = PowercalcGroupRestorePayload(_canonical(value['before']), _canonical(value['after']),
                                                  value['full_total'], value['precision'], value['unit'])
            _need(value['before']['state']['entity_id'] == row['before_id']
                  and value['after']['state']['entity_id'] == row['after_id'])
            materialize(decoded)
    return handoff


def validate_controlled_setup(chain, payload, change):
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    from .native_template_state_yaml import PreparedMixedPowercalcYAML, assert_state_yaml_composition
    _need(type(change) is PreparedMixedPowercalcYAML and type(payload) is dict)
    composition = assert_state_yaml_composition(chain, change)
    _need(type(composition) is ControlledPowercalcComposition and type(payload.get('binding')) is dict)
    direction = payload['binding'].get('direction')
    index = payload['binding'].get('index')
    _need(direction in ('forward', 'rollback') and index == chain.yaml_index)
    setup = _event(chain, payload.get('powercalc_setup'),
                   _event_id(['controlled_pc_setup', chain.root_id, direction, 'receipt']),
                   'controlled_powercalc_setup_receipt')
    intent = _event(chain, setup.get('intent'),
                    _event_id(['controlled_pc_setup', chain.root_id, direction, 'intent']),
                    'controlled_powercalc_setup_intent')
    handoff = validate_handoff(chain, setup.get('handoff'), direction)
    unload = _event(chain, handoff.get('intent'),
                    _event_id(['controlled_pc_population', chain.root_id, direction, 'intent']),
                    'controlled_powercalc_population_intent')
    common = (chain.root_id, chain.operation_id, chain.plan.fingerprint, direction)
    _need(all((event.get('root'), event.get('operation_id'), event.get('fingerprint'), event.get('direction'))
              == common and type(event.get('schema')) is int
              for event in (setup, intent, handoff, unload))
          and all(event['schema'] == 1 for event in (setup, intent, unload))
          and handoff['schema'] == (2 if chain.original_receipt.version == '2026.11.0' else 1)
          and setup.get('phase_index') == intent.get('phase_index') == index
          and setup.get('yaml_intent') == intent.get('yaml_intent') == payload.get('intent')
          and setup.get('handoff') == intent.get('handoff')
          and setup.get('source_head') == intent.get('source_head') == payload.get('source_chain')
          and handoff.get('source_head') == unload.get('source_head')
          and unload.get('phase_index') in range(len(chain.plan.steps)))
    mapping = dict(composition.mapping)
    expected = (tuple(sorted((mapping.get(eid, eid), uid, kind) for eid, uid, kind in composition.population))
                if direction == 'forward' else composition.population)
    _need(setup.get('population') == [list(row) for row in expected]
          and type(handoff.get('population')) is list and type(setup.get('totals')) is list
          and type(setup.get('restored')) is list)
    rows = handoff['population']
    totals, restored = setup['totals'], setup['restored']
    _need(len(rows) == len(totals) == len(restored) and len(rows) > 0)
    for row, total, seen in zip(rows, totals, restored, strict=True):
        _need(type(row) is dict and set(row) == {'uuid', 'before_id', 'after_id', 'kind', 'payload'}
              and type(total) is dict and set(total) == {'uuid', 'entity_id', 'kind', 'value', 'last_valid'}
              and (total['uuid'], total['entity_id'], total['kind']) ==
                  (row['uuid'], row['after_id'], row['kind'])
              and row['kind'] in ('integration', 'group') and type(row['payload']) is dict)
        _need(type(seen) is dict and set(seen) == {'uuid', 'entity_id', 'kind', 'value', 'last_valid'}
              and (seen['uuid'], seen['entity_id'], seen['kind']) ==
                  (row['uuid'], row['after_id'], row['kind'])
              and type(total['value']) is str and type(seen['value']) is str)
        try:
            value = Decimal(total['value'])
            restored_value = Decimal(seen['value'])
            source = (row['payload']['full_total'] if row['kind'] == 'group'
                      else row['payload']['capture']['full_state_text'])
            _need(value.is_finite() and restored_value.is_finite()
                  and str(value) == total['value'] and str(restored_value) == seen['value']
                  and restored_value.as_tuple() == Decimal(source).as_tuple())
            if row['kind'] == 'group':
                _need(total['last_valid'] is None and seen['last_valid'] is None)
            else:
                _need(type(total['last_valid']) is str and type(seen['last_valid']) is str)
                last, restored_last = Decimal(total['last_valid']), Decimal(seen['last_valid'])
                _need(last.is_finite() and restored_last.is_finite()
                      and str(last) == total['last_valid'] and str(restored_last) == seen['last_valid']
                      and restored_last.as_tuple() ==
                      Decimal(row['payload']['capture']['last_valid_state_text']).as_tuple())
        except (InvalidOperation, KeyError, TypeError, ValueError):
            raise Refused('controlled_powercalc_setup_receipt_invalid') from None
    _need(len({row['uuid'] for row in rows}) == len(rows)
          and {(row['after_id'], row['uuid']) for row in rows}
          == {(eid, uid) for eid, uid, kind in expected if kind in ('energy_integral', 'energy_positive_delta')})
