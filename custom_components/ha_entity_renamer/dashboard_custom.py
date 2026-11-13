"""A deliberately tiny, non-evaluating button-card JavaScript expression adapter."""
from __future__ import annotations

import re

# Admit only a single direct lookup, optionally followed by state/attribute.
# Arbitrary JavaScript, comments, escapes and computed keys require review.
_LOOKUP = re.compile(
    r"\A(\[\[\[\s*return\s+(?:hass\.)?states\[)(['\"])([a-z_][a-z0-9_]*\.[a-z0-9_]+)(\2\](?:\.(?:state|attributes\.[a-z_][a-z0-9_]*))?\s*;?\s*\]\]\])\Z",
    re.ASCII,
)


def plan_button_expression(text, mapping):
    match = _LOOKUP.fullmatch(text)
    if not match:
        return text, 'unsupported_button_javascript'
    old = match.group(3)
    return text[:match.start(3)] + mapping.get(old, old) + text[match.end(3):], None
