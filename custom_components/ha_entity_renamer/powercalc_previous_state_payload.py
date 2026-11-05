"""Strict, detached Powercalc PreviousStateStore payload transformations.

This codec supplies no ownership, live IO, lifecycle authority or persistence.
Callers must bind endpoints to the same ORIGINAL and hold the native lifecycle
lease before using any resulting payload. In particular, a matching hash is
never evidence that an uncertain write occurred.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from copy import deepcopy
import hashlib
import re

from .native_input_baseline import _json, _loads

_ENTITY_ID = re.compile(r'[a-z_][a-z0-9_]*\.[a-z0-9_]+\Z')


class PreviousStatePayloadError(ValueError):
    """Sanitized refusal; never disclose stored state contents."""


def _need(ok, reason):
    if not ok:
        raise PreviousStatePayloadError(reason) from None


def _entity(value):
    return type(value) is str and _ENTITY_ID.fullmatch(value) is not None


def _owned_state(state, entity_id):
    # Require the complete native serialization: State.from_dict otherwise
    # silently invents missing timestamps/context, losing original identity.
    if (type(state) is not dict or set(state) != {'entity_id', 'state', 'attributes',
            'last_changed', 'last_updated', 'last_reported', 'context'}
            or state['entity_id'] != entity_id or type(state['state']) is not str
            or type(state['attributes']) is not dict or type(state['context']) is not dict):
        return False
    context = state['context']
    if (set(context) != {'id', 'parent_id', 'user_id'} or type(context['id']) is not str
            or not context['id'] or any(context[key] is not None and type(context[key]) is not str
                   for key in ('parent_id', 'user_id'))):
        return False
    try:
        times = [datetime.fromisoformat(state[key]) for key in
                 ('last_changed', 'last_updated', 'last_reported')]
        return all(value.tzinfo is not None and value.utcoffset() is not None
                   and value.isoformat() == state[key] for key, value in
                   zip(('last_changed', 'last_updated', 'last_reported'), times, strict=True))
    except (TypeError, ValueError, OverflowError):
        return False


@dataclass(frozen=True)
class GroupMove:
    """A complete known group and its complete member mapping, never authority."""
    before: str
    after: str
    members: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PreparedPreviousStatePayload:
    before: bytes
    after: bytes
    before_sha256: str
    after_sha256: str
    moves: tuple[GroupMove, ...]


def prepare(raw: bytes, moves: tuple[GroupMove, ...]) -> PreparedPreviousStatePayload:
    """Retain source aliases and stage mapped copies in a detached envelope.

    Existing targets, overlapping IDs, unknown group members and malformed
    owned States are refused. Foreign groups remain semantically unchanged.
    This intentionally narrow primitive does not implement inverse alias CAS.
    """
    _need(type(raw) is bytes and type(moves) is tuple and bool(moves), 'preparation_required')
    try:
        envelope = _loads(raw)
    except ValueError:
        raise PreviousStatePayloadError('invalid_store_json') from None
    _need(type(envelope) is dict and set(envelope) == {'version', 'minor_version', 'key', 'data'}
          and type(envelope['version']) is int and envelope['version'] == 2
          and type(envelope['minor_version']) is int and envelope['minor_version'] == 1
          and envelope['key'] == 'powercalc_group' and type(envelope['data']) is dict,
          'store_envelope')
    groups = envelope['data']
    _need(all(_entity(group) and type(rows) is dict for group, rows in groups.items()),
          'store_group_shape')
    _need(all(type(move) is GroupMove and _entity(move.before) and _entity(move.after)
              and type(move.members) is tuple and bool(move.members) for move in moves),
          'move_shape')
    _need(all(move.before.split('.', 1)[0] == move.after.split('.', 1)[0] for move in moves),
          'group_domain_changed')
    sources = [move.before for move in moves]
    targets = [move.after for move in moves]
    _need(len(sources) == len(set(sources)) and len(targets) == len(set(targets)),
          'duplicate_group')
    _need(not any(move.before != move.after and move.after in sources for move in moves),
          'overlapping_groups')
    mapping = {}
    inverse = {}
    for move in moves:
        _need(all(type(pair) is tuple and len(pair) == 2 and all(_entity(x) for x in pair)
                  for pair in move.members), 'member_mapping_shape')
        for before, after in move.members:
            _need(before.split('.', 1)[0] == after.split('.', 1)[0]
                  and mapping.get(before, after) == after
                  and inverse.get(after, before) == before, 'inconsistent_member_mapping')
            mapping[before], inverse[after] = after, before
    _need(not any(before != after and after in mapping for before, after in mapping.items()),
          'overlapping_member_mapping')
    post = deepcopy(envelope)
    for move in moves:
        _need(move.before in groups and (move.before == move.after or move.after not in groups),
              'group_preimage_or_target')
        rows = groups[move.before]
        _need(all(type(pair) is tuple and len(pair) == 2 and all(_entity(x) for x in pair)
                  for pair in move.members), 'member_mapping_shape')
        old = [pair[0] for pair in move.members]
        new = [pair[1] for pair in move.members]
        _need(len(old) == len(set(old)) and len(new) == len(set(new))
              and set(rows) == set(old), 'member_coverage_or_duplicate')
        _need(not any(a != b and b in old for a, b in move.members), 'overlapping_members')
        output = deepcopy(rows)
        for before, after in move.members:
            state = rows[before]
            _need(_owned_state(state, before), 'owned_state_missing_or_mismatched')
            if before != after:
                _need(after not in rows, 'member_target_exists')
                changed = deepcopy(state)
                changed['entity_id'] = after
                output[after] = changed
        post['data'][move.after] = output
    after = _json(post)
    return PreparedPreviousStatePayload(raw, after, hashlib.sha256(raw).hexdigest(),
                                        hashlib.sha256(after).hexdigest(), moves)
