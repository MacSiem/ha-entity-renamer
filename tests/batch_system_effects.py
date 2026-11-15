"""Strict oracles for two observed Core 2026.10 fixture timers.

These are readback tools only. They do not participate in product admission,
fences or mutation and never replace the unmodified original evidence.
"""
from datetime import datetime, timedelta
import copy
import json
import re

from batch_readback import _strict_json


def _same(a, b):
    return json.dumps(a, sort_keys=True, separators=(',', ':'), allow_nan=False) == json.dumps(
        b, sort_keys=True, separators=(',', ':'), allow_nan=False)


def http_auto_revert(original, current):
    """Accept exactly the native pending timeout, comparing every other field."""
    before, after = _strict_json(original), _strict_json(current)
    try:
        expected = copy.deepcopy(before)
        if (expected['key'] != 'http' or expected['data']['pending']['error'] is not None
                or after['data']['pending']['error'] != 'not_promoted'):
            raise ValueError('http_timeout_not_observed')
        expected['data']['pending']['error'] = 'not_promoted'
        if not _same(expected, after):
            raise ValueError('http_foreign_change')
    except (KeyError, TypeError):
        raise ValueError('http_shape_unqualified') from None
    return original


def fixture_restore_dump(original, current, *, fixture_started, snapshot_at):
    """Admit only the four known fixture entities from one periodic dump.

    Native timestamp and context identifiers are checked for shape and bounds;
    their exact values have no independent pre-dump observation.
    """
    before, after = _strict_json(original), _strict_json(current)
    if (type(before) is not dict or before.get('key') != 'core.restore_state'
            or before.get('data') != [] or type(after) is not dict
            or set(before) != {'version', 'minor_version', 'key', 'data'}
            or not _same({k: v for k, v in before.items() if k != 'data'},
                         {k: v for k, v in after.items() if k != 'data'})):
        raise ValueError('restore_store_original_unqualified')
    rows = after.get('data')
    if type(rows) is not list or len(rows) != 4:
        raise ValueError('restore_dump_membership')
    expected = {
        'sensor.runtime_consumer': ('2', {'friendly_name': 'Runtime consumer'},
                                    {'native_unit_of_measurement': None, 'native_value': 2}, False, False),
        'binary_sensor.runtime_binary': ('off', {'friendly_name': 'Runtime binary'}, None, False, False),
        'automation.mixed_source_observer': ('on', {'current': 0, 'friendly_name': 'Mixed source observer',
            'id': 'mixed_source', 'mode': 'single'}, None, True, True),
        'script.mixed_source': ('off', {'current': 0, 'friendly_name': 'mixed_source',
            'mode': 'single'}, None, True, False),
    }
    seen, dump_time = set(), None
    def stamp(value):
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError('restore_time_unqualified')
        return result
    try:
        for row in rows:
            if type(row) is not dict or set(row) != {'state', 'extra_data', 'last_seen'}:
                raise ValueError('restore_row_shape')
            state = row['state']
            if type(state) is not dict or set(state) != {'entity_id', 'state', 'attributes', 'context',
                    'last_changed', 'last_updated', 'last_reported'}:
                raise ValueError('restore_state_shape')
            entity = state['entity_id']
            if entity not in expected or entity in seen:
                raise ValueError('restore_dump_membership')
            seen.add(entity)
            expected_state, attributes, extra, triggered, parent = expected[entity]
            if type(state['attributes']) is not dict:
                raise ValueError('restore_attributes_shape')
            attrs = dict(state['attributes'])
            last_triggered = attrs.pop('last_triggered', None) if triggered else None
            if (state['state'] != expected_state or not _same(attrs, attributes)
                    or not _same(row['extra_data'], extra)):
                raise ValueError('restore_owned_fields_changed')
            now = stamp(row['last_seen'])
            if not fixture_started + timedelta(minutes=15) <= now <= fixture_started + timedelta(minutes=15, seconds=45):
                raise ValueError('restore_dump_time_unqualified')
            if now > snapshot_at or (dump_time is not None and now != dump_time):
                raise ValueError('restore_dump_time_unqualified')
            dump_time = now
            changed, updated, reported = (stamp(state[k]) for k in ('last_changed','last_updated','last_reported'))
            if not fixture_started <= changed <= updated <= reported <= now:
                raise ValueError('restore_state_time_unqualified')
            if triggered and not fixture_started <= stamp(last_triggered) <= reported:
                raise ValueError('restore_trigger_time_unqualified')
            context = state['context']
            if (type(context) is not dict or set(context) != {'id', 'parent_id', 'user_id'}
                    or context['user_id'] is not None or (context['parent_id'] is not None) != parent
                    or any(type(v) is not str or not re.fullmatch('[0-9A-HJKMNP-TV-Z]{26}', v)
                           for v in (context['id'], *([context['parent_id']] if parent else [])))):
                raise ValueError('restore_context_unqualified')
    except (KeyError, TypeError, AttributeError):
        raise ValueError('restore_dump_unqualified') from None
    if seen != set(expected):
        raise ValueError('restore_dump_membership')
    return original
