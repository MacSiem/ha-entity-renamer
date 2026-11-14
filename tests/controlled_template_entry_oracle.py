"""Independent whole-document oracle for the disposable native fixture."""
from copy import deepcopy
from datetime import datetime
import json


def _exact(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def assert_documents(before, observed, *, entry_id, source_uuid, old, new, reverse):
    expected = deepcopy(before)
    actual = deepcopy(observed)
    for doc in (expected, actual):
        if set(doc) != {'core.config_entries', 'core.entity_registry', 'core.device_registry'}:
            raise ValueError('oracle_document_scope')
    def one(rows, key, value):
        selected = [row for row in rows if row.get(key) == value]
        if len(selected) != 1:
            raise ValueError('oracle_identity')
        return selected[0]
    b = one(expected['core.entity_registry']['data']['entities'], 'id', source_uuid)
    a = one(actual['core.entity_registry']['data']['entities'], 'id', source_uuid)
    if b['entity_id'] != old or a['entity_id'] != (old if reverse else new):
        raise ValueError('oracle_entity_id')
    def stamp(first, last):
        try:
            return datetime.fromisoformat(last) >= datetime.fromisoformat(first)
        except (TypeError, ValueError):
            raise ValueError('oracle_timestamp') from None
    if not stamp(b['modified_at'], a['modified_at']):
        raise ValueError('oracle_timestamp')
    a['entity_id'], a['modified_at'] = b['entity_id'], b['modified_at']
    b = one(expected['core.config_entries']['data']['entries'], 'entry_id', entry_id)
    a = one(actual['core.config_entries']['data']['entries'], 'entry_id', entry_id)
    original = b['options']['state']
    proposed = original.replace("states('" + old + "')", "states('" + new + "')")
    if proposed == original or a['options']['state'] != (original if reverse else proposed):
        raise ValueError('oracle_entry_reference')
    if not stamp(b['modified_at'], a['modified_at']):
        raise ValueError('oracle_timestamp')
    a['options']['state'], a['modified_at'] = original, b['modified_at']
    before_order = [row['id'] for row in expected['core.entity_registry']['data']['entities']]
    after_order = [row['id'] for row in actual['core.entity_registry']['data']['entities']]
    if ([item for item in before_order if item != source_uuid] !=
            [item for item in after_order if item != source_uuid]):
        raise ValueError('oracle_foreign_entity_order')
    # The native rename reinserts its one selected row; foreign order is fixed.
    # Compare full population by UUID with every row field and envelope exact.
    for document in (actual, expected):
        rows = document['core.entity_registry']['data']['entities']
        keyed = {row['id']: row for row in rows}
        if len(keyed) != len(rows):
            raise ValueError('oracle_duplicate_entity_uuid')
        document['core.entity_registry']['data']['entities'] = keyed
    if _exact(actual) != _exact(expected):
        raise ValueError('oracle_foreign_or_omitted_change')
    return True
