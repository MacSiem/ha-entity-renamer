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
# Source-pinned label programs whose references flow through owned variables or
# foreign static IDs. Exact hashes do not admit new JavaScript variants.
_OBSERVED_UNCHANGED = frozenset('''
04b8a6ac82e318c9526ab8d384132bba7f35d2025bfd89e4c45a3bcb3dbfa319
0c09afa4248eb26935d786750936eb0a415b1d06c2cf21751ffded197d473f8c
2083838734dead6755642e14713c4faa0cb786470203bbef7e8830e6b1cb5129
2a8524dd83ba240f49f1d44c89fe7204ede787ce7ef456e2fcc29d8291d9ae1d
4cf0068eff006b41c21f883b343fd50af181fa7aba40b7f9b483fac53453bc85
5891bd1295891ae5bce59a422f9bb86494b5333a4c3747b0a30852ecb7cea05f
595a90b96bdb6a903f37f84fcefd1e85140577e70e8275d17c8ba178d092cd1b
5c5f20476c9ad1741db384bbd3f1191a5bb3a11b448b489477721e99d5c43492
9fd183e879cbb9c4ccdd013a55b9e4bde2b9db15b842ae8607ba2d49afe35ac5
a5c8e0842f74f06ae5fd54071b9674a6530dfb26e7870764bc3e22bcc21895ff
bef7fbba47d74a394b4cd47b35abf9a9f4c2e829aa0e4ded4ebb6b1b5d95a8a1
d7667314fca7e48ecf92fbdcd859152caed497b8ffaac8f94a0a71c1ffdb1471
d91e249225ab1d718b76816e4bc200ef5d3e81f4370a7a2e20bb6321c7febd6d
e53ebf99071bcf85fb4069408ea5842d5e8870ccaef8bac11e5a2e00ae278d52
'''.split())
_PREFIX = re.compile(r"(?:hass\.)?states\[\(?\s*['\"]([a-z_][a-z0-9_]*\.[a-z0-9_]+)['\"]\s*\+", re.ASCII)



def plan_button_expression(text, mapping):
    match = _LOOKUP.fullmatch(text)
    if match:
        old = match.group(3)
        return text[:match.start(3)] + mapping.get(old, old) + text[match.end(3):], None
    if hashlib.sha256(text.encode()).hexdigest() in _OBSERVED_UNCHANGED:
        prefixes = _PREFIX.findall(text)
        if any(old in text or any(old.startswith(prefix) for prefix in prefixes) for old in mapping):
            return text, 'dynamic_button_state_reference'
        return text, None
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

# Two source-pinned target expressions select from a finite user suffix map.
# They are left byte-identical; a selected ID under their prefix refuses.
_COMPUTED_TARGETS = frozenset({
    '61ca540636192377a0124c8b8da06b5229cd62187f80ea3143b3be777e2e5248',
    '5a20aad0441149a9b221e0d050d7d4be4035249a3fd1829d5908caba171752a2',
})
_COMPUTED_PREFIX = re.compile(r"['\"]([a-z_][a-z0-9_]*\.[a-z0-9_]+)['\"]\s*\+", re.ASCII)


def unchanged_computed_target(text, mapping):
    if type(text) is not str or hashlib.sha256(text.encode()).hexdigest() not in _COMPUTED_TARGETS:
        return False
    prefixes = _COMPUTED_PREFIX.findall(text)
    return len(prefixes) == 1 and not any(old.startswith(prefixes[0]) for old in mapping)


def unchanged_finite_jinja(text, mapping):
    """Prove dynamic state arguments range only over foreign literal tuple slots."""
    try:
        from jinja2 import Environment, nodes
        tree = Environment().parse(text)
        state_names = {'states', 'is_state', 'state_attr', 'is_state_attr', 'expand'}
        for item in tree.find_all((nodes.Getitem, nodes.Getattr)):
            root = item
            while isinstance(root, (nodes.Getitem, nodes.Getattr)):
                root = root.node
            if isinstance(root, nodes.Name) and root.name == 'states':
                return False
        dynamic = []
        for call in tree.find_all(nodes.Call):
            if not isinstance(call.node, nodes.Name) or call.node.name not in state_names:
                continue
            if not call.args:
                return False
            args = call.args if call.node.name == 'expand' else call.args[:1]
            for arg in args:
                if isinstance(arg, nodes.Const) and type(arg.value) is str:
                    continue
                if not isinstance(arg, nodes.Name):
                    return False
                dynamic.append((call, arg.name))
        remaining = {(id(call), name) for call, name in dynamic}
        if not remaining:
            return False
        for loop in tree.find_all(nodes.For):
            targets = loop.target.items if isinstance(loop.target, nodes.Tuple) else [loop.target]
            if not all(isinstance(target, nodes.Name) for target in targets):
                continue
            names = [target.name for target in targets]
            if not isinstance(loop.iter, (nodes.List, nodes.Tuple)):
                continue
            records = []
            for item in loop.iter.items:
                values = item.items if isinstance(item, nodes.Tuple) else [item]
                if len(values) != len(names) or not all(isinstance(value, nodes.Const) for value in values):
                    return False
                records.append([value.value for value in values])
            for call in loop.find_all(nodes.Call):
                for arg in call.args[:1]:
                    if not isinstance(arg, nodes.Name) or arg.name not in names:
                        continue
                    key = (id(call), arg.name)
                    if key not in remaining:
                        continue
                    if any(isinstance(assign.target, nodes.Name) and assign.target.name == arg.name
                           for assign in loop.find_all(nodes.Assign)):
                        return False
                    vals = [row[names.index(arg.name)] for row in records]
                    if any(type(value) is not str or value in mapping for value in vals):
                        return False
                    remaining.remove(key)
        return not remaining
    except Exception:
        return False
