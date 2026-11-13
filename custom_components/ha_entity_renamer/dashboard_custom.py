"""Bounded button-card JS, without executing JavaScript or rewriting prose."""
from __future__ import annotations

import hashlib
import re

_LOOKUP = re.compile(
    r"\A(\[\[\[\s*return\s+(?:hass\.)?states\[)(['\"])([a-z_][a-z0-9_]*\.[a-z0-9_]+)(\2\](?:\.(?:state|attributes\.[a-z_][a-z0-9_]*))?\s*;?\s*\]\]\])\Z",
    re.ASCII,
)
_ENTITY_LITERAL = re.compile(r"(?P<q>['\"])(?P<id>[a-z_][a-z0-9_]*\.[a-z0-9_]+)(?P=q)", re.ASCII)
# SHA-256 of the exact observed program with each quoted entity ID replaced by
# <entity>. This is a source-specific proof, not a general JavaScript parser.
# Its first 15 literals populate L, consumed by states[e], the next seven are
# direct states lookups or arguments to the local run() state lookup; last two
# are dynamic prefixes and are never rewritten as complete IDs.
_OBSERVED_LABEL = '520be3bd4bb7cd131e7216ade78c1253d1223062edefc472431917894beab550'


def plan_button_expression(text, mapping):
    match = _LOOKUP.fullmatch(text)
    if match:
        old = match.group(3)
        return text[:match.start(3)] + mapping.get(old, old) + text[match.end(3):], None
    matches = list(_ENTITY_LITERAL.finditer(text))
    normalized = _ENTITY_LITERAL.sub(lambda m: m['q'] + '<entity>' + m['q'], text)
    if (len(matches) != 24 or hashlib.sha256(normalized.encode()).hexdigest() != _OBSERVED_LABEL):
        return text, 'unsupported_button_javascript'
    # The observed last two state keys concatenate one of three literal suffixes.
    # If any chosen ID can be one of them the static adapter refuses the row.
    for match in matches[22:]:
        if any(match['id'] + suffix in mapping for suffix in ('cry', 'motion', 'wake')):
            return text, 'dynamic_button_state_reference'
    pieces, last = [], 0
    for match in matches[:22]:
        pieces.extend((text[last:match.start('id')], mapping.get(match['id'], match['id'])))
        last = match.end('id')
    pieces.append(text[last:])
    return ''.join(pieces), None
