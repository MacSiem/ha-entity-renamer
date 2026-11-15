"""Qualify only a generated-cost row's later metadata timestamp.

The native Store and full manager image are read independently; this module
never saves a registry, adopts unrelated edits, or grants an inverse.
The comparison establishes semantic equivalence, not restart or caller provenance.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
import stat

from .transaction import Refused


def _need(value, code):
    if not value:
        raise Refused('controlled_pc_cold_registry_' + code)


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value) if type(value) is str else None
    except ValueError:
        result = None
    _need(result is not None and result.tzinfo is not None, 'timestamp')
    return result


def _costs(history):
    """Only cost identities proven by the acknowledged forward Energy effect."""
    rows = [payload for _, payload in history
            if payload.get('kind') == 'powercalc_energy_receipt'
            and payload.get('binding', {}).get('direction') == 'forward']
    if len(rows) != 1:
        return ()
    costs = rows[0].get('generated_cost_after', [])
    _need(type(costs) is list, 'cost_evidence')
    return tuple(costs)


def apply_projection(states, head, history, projection):
    """Replay a bound timestamp-only image; does not read or write live data."""
    from .native_registry_receipts import _exact_json_equal, _freeze_json
    if projection is None:
        return states
    _need(type(projection) is dict and set(projection) == {
        'schema', 'forward_head', 'source_sha256', 'source_identity',
        'result_sha256', 'result_identity', 'updates'} and projection['schema'] == 1
        and projection['forward_head'] == head, 'binding')
    current = states['core.entity_registry']
    _need(projection['source_sha256'] == current['sha256']
        and projection['source_identity'] == current['identity'], 'source')
    identity = projection['result_identity']
    _need(type(identity) is list and len(identity) == 3
        and all(type(item) is int and item >= 0 for item in identity)
        and identity[1] > 0 and stat.S_ISREG(identity[2])
        and identity[0] == current['identity'][0] and identity[2] == current['identity'][2]
        and type(projection['result_sha256']) is str
        and re.fullmatch(r'[0-9a-f]{64}', projection['result_sha256']) is not None,
        'identity')
    costs = _costs(history)
    allowed = {row['registry_id']: row for row in costs}
    updates = projection['updates']
    _need(type(updates) is list and bool(updates) and len(updates) <= len(allowed), 'updates')
    result = deepcopy(states)
    document = result['core.entity_registry']['document']
    used = set()
    for update in updates:
        _need(type(update) is dict and set(update) == {'registry_id', 'before', 'after'}
            and update['registry_id'] in allowed and update['registry_id'] not in used, 'update')
        used.add(update['registry_id'])
        cost = allowed[update['registry_id']]
        matches = [row for row in document['data']['entities'] if row.get('id') == update['registry_id']]
        _need(len(matches) == 1 and matches[0].get('entity_id') == cost['entity_id']
            and matches[0].get('unique_id') == cost['unique_id']
            and matches[0].get('platform') == 'energy'
            and cost['unique_id'] == cost['source_registry_id'] + '_grid_cost'
            and matches[0].get('modified_at') == update['before']
            and _timestamp(update['after']) > _timestamp(update['before']), 'cost_identity')
        matches[0]['modified_at'] = update['after']
    # The entire input state remains bound to the prior chain plus those fields.
    _need(_exact_json_equal(result['core.device_registry'], _freeze_json(states['core.device_registry']))
        and _exact_json_equal(result['core.config_entries'], _freeze_json(states['core.config_entries'])), 'foreign')
    result['core.entity_registry']['sha256'] = projection['result_sha256']
    result['core.entity_registry']['identity'] = identity
    return result


def qualify_projection(chain, states, head, completed, pending, observed):
    """Qualify settled native documents against a fully completed head.

    A later timestamp alone is not evidence of its cause. Only the receipted
    generated-cost row may differ, and every other field and source stays bound.
    """
    from .native_input_baseline import _loads
    from .native_registry_receipts import _exact_json_equal, _freeze_json, _sha
    _need(pending is None and completed == {i: 'forward' for i in range(len(chain.plan.steps))}
        and observed.version == chain.version and observed.config_root == chain.original_receipt.config_root,
        'forward_required')
    files = {row.key: row for row in observed.files}
    _need(set(files) == set(states), 'inventory')
    sources = chain._input_sources(states)
    for key in ('core.device_registry', 'core.config_entries'):
        row, old = files[key], states[key]
        _need(_sha(row.raw) == old['sha256'] and list(row.identity) == old['identity']
            and _exact_json_equal(_loads(row.raw), _freeze_json(old['document'])), 'foreign')
    actual = files['core.entity_registry']
    old = states['core.entity_registry']
    expected = deepcopy(old['document'])
    document = _loads(actual.raw)
    costs = _costs(chain._setup_history)
    updates = []
    for cost in costs:
        prior = [row for row in expected['data']['entities'] if row.get('id') == cost['registry_id']]
        live = [row for row in document['data']['entities'] if row.get('id') == cost['registry_id']]
        _need(len(prior) == len(live) == 1, 'cost_identity')
        if prior[0].get('modified_at') != live[0].get('modified_at'):
            updates.append({'registry_id': cost['registry_id'], 'before': prior[0]['modified_at'],
                            'after': live[0]['modified_at']})
            prior[0]['modified_at'] = live[0]['modified_at']
    _need(_exact_json_equal(document, _freeze_json(expected)), 'foreign')
    old_physical = sources['.storage/core.entity_registry']
    _need(actual.identity[0] == old['identity'][0] and actual.identity[2] == old['identity'][2]
        and (old_physical[4] is None or [actual.uid, actual.gid] == old_physical[4:]), 'identity')
    if not updates:
        _need(_sha(actual.raw) == old['sha256'] and list(actual.identity) == old['identity'], 'unexplained_write')
        return None
    projection = {'schema': 1, 'forward_head': head, 'source_sha256': old['sha256'],
        'source_identity': old['identity'], 'result_sha256': _sha(actual.raw),
        'result_identity': list(actual.identity), 'updates': updates}
    apply_projection(states, head, chain._setup_history, projection)
    return projection
