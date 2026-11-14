"""Bounded button-card JS, without executing JavaScript or rewriting prose."""
from __future__ import annotations

import hashlib
import json
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



# A complete, deliberately small JavaScript expression.  It reads only the
# current card entity and compares/returns literal strings; no dynamic lookup.
# Reject escapes, comments, operators, function calls and trailing statements.
_JS_LITERAL = r"(?:'[^'\\\\]*'|\"[^\"\\\\]*\")"
_CURRENT_ENTITY_TERNARY = re.compile(
    r"\A\[\[\[\s*return\s+entity\s*&&\s*entity\.state\s*===\s*"
    + _JS_LITERAL + r"\s*\?\s*" + _JS_LITERAL + r"\s*:\s*" + _JS_LITERAL
    + r"\s*;?\s*\]\]\]\Z", re.ASCII)


_JS_QUOTED = re.compile(r"'[^'\\\\]*'|\"[^\"\\\\]*\"")
_JS_ENTITY_TOKEN = re.compile(r'(?<![A-Za-z0-9_.])([a-z_][a-z0-9_]*\.[a-z0-9_]+)(?![A-Za-z0-9_.])', re.ASCII)
_JS_PREFIX_LOOKUP = (r"states\[\(S\+\(\{S:S,S:S\}\[user\.id\]\|\|S\)\)\]\?\.state")
_JS_FINITE_PREFIX = tuple(re.compile(x, re.ASCII) for x in (
    r"\[\[\[return\(" + _JS_PREFIX_LOOKUP + r"===S\)\?S:S;?\]\]\]",
    r"\[\[\[constselected=" + _JS_PREFIX_LOOKUP + r";returnS\+\(selected\|\|S\);?\]\]\]",
    r"\[\[\[constselected=" + _JS_PREFIX_LOOKUP + r";constcount=variables\.[A-Za-z_$][\w$]*\[selected\];returncount===undefined\?S:T;?\]\]\]",
))
_JS_LIST_LOOKUP = re.compile(
    r"\[\[\[returnvariables\.source_entities\.every\(id=>Number\.isFinite\(Number\.parseFloat\(states\[id\]\?\.state\)\)\)\?S:S;?\]\]\]", re.ASCII)


_TABLE_START = re.compile(r"\A\[\[\[\s*const\s+C\s*=\s*")
_TABLE_COMMON = (";constW=states[S];constn=e=>{consts=states[e];"
    "constv=s?parseFloat(s.state):NaN;returnisNaN(v)?null:v;};"
    "constf1=x=>(x===null||x===undefined||isNaN(x))?S:"
    "Number(x).toLocaleString(S,{minimumFractionDigits:1,maximumFractionDigits:1});")
_TABLE_ENDS = frozenset({
    "return(C[W?.state]||[null,S])[1];]]]",
    "lett=n(S);if(t===null)t=W?.attributes?.temperature??null;"
    "leth=n(S);if(h===null)h=W?.attributes?.humidity??null;returnT;]]]",
})


def _unchanged_literal_table(text, mapping):
    """Recognize a finite literal-table program; all state arguments are literals."""
    selected = set(mapping) | set(mapping.values())
    if any(m.group(1) in selected for m in _JS_ENTITY_TOKEN.finditer(text)):
        return False
    start = _TABLE_START.match(text)
    if not start:
        return False
    try:
        table, end = json.JSONDecoder().raw_decode(text[start.end():])
    except (ValueError, RecursionError):
        return False
    if (type(table) is not dict or any(type(k) is not str or type(v) is not list
            or len(v) != 2 or any(type(item) is not str for item in v)
            for k, v in table.items())):
        return False
    if any(match.group(1) in selected for key, values in table.items()
           for value in (key, *values) for match in _JS_ENTITY_TOKEN.finditer(value)):
        return False
    tail = text[start.end() + end:]
    def output_template(match):
        body = match.group(1)
        if '\\' in body:
            return match.group(0)
        expressions = re.findall(r'\$\{([^}]*)\}', body)
        normalized = [re.sub(r'\s+', '', _JS_QUOTED.sub('S', part)) for part in expressions]
        if normalized != ['f1(t)', 'h===null?S:Math.round(h)']:
            return match.group(0)
        if '${' in re.sub(r'\$\{[^}]*\}', '', body):
            return match.group(0)
        return 'T'
    tail = re.sub(r'`([^`]*)`', output_template, tail)
    tail = re.sub(r'\s+', '', _JS_QUOTED.sub('S', tail))
    return any(tail == _TABLE_COMMON + ending for ending in _TABLE_ENDS)


def _unchanged_finite_expression(text, variables, mapping):
    """Prove two small expression grammars without executing JavaScript."""
    selected = set(mapping) | set(mapping.values())
    if any(m.group(1) in selected for m in _JS_ENTITY_TOKEN.finditer(text)):
        return False
    # Template literal output may interpolate only the previously read scalar
    # and a local count. Any other expression could perform another lookup.
    def template(match):
        body = match.group(1)
        body = body.replace('${selected}', '').replace('${count}', '')
        return 'T' if '${' not in body and '\\' not in body else match.group(0)
    normalized = re.sub(r'`([^`]*)`', template, text)
    literals = []
    def quote(match):
        literals.append(match.group(0)[1:-1])
        return 'S'
    normalized = _JS_QUOTED.sub(quote, normalized)
    normalized = re.sub(r'\s+', '', normalized)
    if _JS_LIST_LOOKUP.fullmatch(normalized):
        values = variables.get('source_entities') if type(variables) is dict else None
        return (type(values) is list and all(type(item) is str for item in values)
                and not any(item in mapping.values() and item not in mapping for item in values))
    if any(pattern.fullmatch(normalized) for pattern in _JS_FINITE_PREFIX):
        if not literals or not re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z0-9_]*', literals[0], re.ASCII):
            return False
        return not any(identity.startswith(literals[0]) for identity in selected)
    return False


def plan_button_expression(text, mapping, variables=None):
    if _unchanged_literal_table(text, mapping):
        return text, None
    if _unchanged_finite_expression(text, variables, mapping):
        return text, None
    if _CURRENT_ENTITY_TERNARY.fullmatch(text):
        if any(m['id'] in mapping or m['id'] in mapping.values() for m in _ENTITY_LITERAL.finditer(text)):
            return text, 'unsupported_button_javascript'
        return text, None
    match = _LOOKUP.fullmatch(text)
    if match:
        old = match.group(3)
        if old in mapping.values() and old not in mapping:
            return text, 'target_reference_preexisting'
        return text[:match.start(3)] + mapping.get(old, old) + text[match.end(3):], None
    if hashlib.sha256(text.encode()).hexdigest() in _OBSERVED_UNCHANGED:
        prefixes = _PREFIX.findall(text)
        if any(identity in text or any(identity.startswith(prefix) for prefix in prefixes)
               for identity in (*mapping.keys(), *mapping.values())):
            return text, 'dynamic_button_state_reference'
        return text, None
    matches = list(_ENTITY_LITERAL.finditer(text))
    normalized = _ENTITY_LITERAL.sub(lambda m: m['q'] + '<entity>' + m['q'], text)
    if (len(matches) != 24 or hashlib.sha256(normalized.encode()).hexdigest() != _OBSERVED_LABEL):
        return text, 'unsupported_button_javascript'
    # A pre-existing target literal would change a previously foreign lookup.
    if any(match['id'] in mapping.values() for match in matches[:22]):
        return text, 'target_reference_preexisting'
    # The observed last two state keys concatenate one of three literal suffixes.
    # If any chosen ID can be one of them the static adapter refuses the row.
    for match in matches[22:]:
        if any(match['id'] + suffix in (*mapping.keys(), *mapping.values()) for suffix in ('cry', 'motion', 'wake')):
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
    return len(prefixes) == 1 and not any(identity.startswith(prefixes[0]) for identity in (*mapping.keys(), *mapping.values()))


def unchanged_finite_jinja(text, mapping):
    """Prove dynamic state arguments range only over foreign literal tuple slots."""
    try:
        from jinja2 import Environment, nodes
        tree = Environment().parse(text)
        # We support literal lexical loops only; nested loops that shadow names,
        # macros and with-scopes require a full lexical provenance proof.
        if any(tree.find_all((nodes.Macro, nodes.With, nodes.AssignBlock))): return False
        for outer in tree.find_all(nodes.For):
            outer_targets = outer.target.items if isinstance(outer.target, nodes.Tuple) else [outer.target]
            outer_names = {n.name for n in outer_targets if isinstance(n, nodes.Name)}
            for inner in outer.find_all(nodes.For):
                if inner is outer: continue
                inner_targets = inner.target.items if isinstance(inner.target, nodes.Tuple) else [inner.target]
                if any(isinstance(n, nodes.Name) and n.name in outer_names for n in inner_targets):
                    return False
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
                    if any(type(value) is not str or value in mapping or value in mapping.values() for value in vals):
                        return False
                    remaining.remove(key)
        return not remaining
    except Exception:
        return False

_STYLE_PREFIX = frozenset({
    '4fd4fc7edb37f7985e7197bf66569b22c12317f0223f700d6178461ec8738741',
    'fae3daaa9c1e5b44bf10af2f311982c2cc25dc7898e4e67b6cfe0f60bce54c72',
})

_STYLE_VARIABLE = frozenset({
    '605993ec7436b2728109f88f67f4e8a215d4908ee5fc76b1ed7bf0c3097be077',
    '0417fc790499d08e9622b7c08b3dd3173960144a60ad313e7ea4ceaff7e79150',
})


def unchanged_button_style(text, variables, mapping):
    if type(text) is not str: return False
    digest = hashlib.sha256(text.encode()).hexdigest()
    if digest in _STYLE_PREFIX:
        prefixes = _PREFIX.findall(text)
        return len(prefixes) == 1 and not any(identity.startswith(prefixes[0])
            for identity in (*mapping.keys(), *mapping.values()))
    if digest in _STYLE_VARIABLE:
        values = variables.get('source_entities') if type(variables) is dict else None
        return (type(values) is list and all(type(value) is str for value in values)
                and not any(value in mapping.values() for value in values))
    return False

# Eleven exact Bubble Card CSS/JS source programs use only foreign literal
# hass.states[...] IDs. Preserve their bytes; any selected source or target
# reference requires a separate semantic adapter.
_BUBBLE_STYLES = frozenset('''
d52a71ff74c3d3ae0080fd91f41bf4b9a2389df9049b3bb642cf46d225d43a0e
bf754d64833a1296e425606883fb9549033ca6cf4b91e944aa751c6a3b767050
c56657e2507363bff8d9efac500529b42faded61b296a8ece328519ffd9bb014
87d6258af2c72f077b4f8cd6c69689843989a56335df3be2e68e2b7b2b0c3c82
a5e05c0d61ed8f58e58b59efb1d814824e306e2fa63d269ed91998f4b9e474ea
637a76c53108bfb311c2411a0dff35674eaca326f0b821b9e6ad71861697362c
246b592d81c6caae62be11b8234b24946bc08446e687b455b7f38f0f9196c603
779649be991b69f5f7799bad4b9843f092b8770d0be664f4f5a01a06fc08819a
bb3308cf1bc30ec6a67d15567cbb92b57c18bdd26610de962a6f1185716cf902
ee824c267ab4b5c72025e78c5d7d7b15561b66701ab4960843e233b035ab68a3
e3a2664f7082bd802731979db5725853395724107654e4ac5c5282e185e89d92
'''.split())


def unchanged_bubble_style(text, mapping):
    return (type(text) is str and hashlib.sha256(text.encode()).hexdigest() in _BUBBLE_STYLES
            and not any(identity in text for identity in (*mapping.keys(), *mapping.values())))

_BUTTON_FIELD_LITERAL = frozenset('''
124b073b2de4f1a74e2f1919f7e368f4c4e94ed5a93fc2a1f72e2a7f3df9aae1
1bf319f42a22ff670dbfdc5db1f60382522ff0303fb7ba9c3024db25d508bf7b
21fa1dabd006599c39223d96bc4ce33f85dc7c61b12f29104db2d7c9a3a488bc
22f02386b26de20e7cee309578d9fab1cf79f4142517d9579067e8617e6af9f3
2476cbdf6bfae4b8d68d13b65cd151353852cf2c1b5980eebc882dc981c715d2
2ce3228ba83b44575a61197e8ab3eff750b447451fa43cbd5b0c5b2e0daae86c
374f519c703377480f1175fa4e6b3272e86ea1d0bda71d940975125170309741
5bb6205479c89fbffab39ba77776f2e7ac4aea5ee7a2728d2891b5846107e40b
603d6242ab7d57451864916666fe63e7204f7fba5c918f1bedbd66a1f434ddc9
6d5f46f30481be81090dc53e3384f89aa1200cacca8452f6dad20114e8bfb1ca
767629a802785aba073d6e4386bcf1d2c8cdb29de0130091b91e795290b1d1f5
981a36661efa6c65aae73c499b839c2b07a88d5a0f9aef017056d73ca1a1cf9e
9933625433e6e8c510674d4287196c8297acd31d66f65eeb6580220fb44fb7ff
aec4e7508817aa909c8b80f0348486a10e0528b99052019df837904d70ef427d
b1d23bd9043ad1e4e61ec4b6e5100f5220171d8ff9efcc32612ec438bb9b9e68
bbe8db4fcb15789bc5c793afbe8fcd65242cae5aecb4050b1198c979e1c804cc
c25a714851370fa93099fffc8f174fff7bc1d16267d816a0a6900ee0e3130371
d2d695387c13c5dbeaacde352e779c71364b7bd0f5b498ed83b20536950b89af
d735c7e482cecf0fb2ef1a864f9562bfabeeabc283871f16ddb816e943b3ad3e
da904558ae5280b56dbf665eff1c7659d963b4425c86e6b75139afd826f045ff
f285ab52bf5ddbf52ea72f384ed65dc9837ebedf1b51eedcf45b87f34965c9a6
'''.split())
_BUTTON_FIELD_VARIABLE = frozenset({
    '86d06169e0b717883a8b0f703a86d41ceaf6ccc4feced78349816679985a80d8',
    '90f1180dc15ccdf7d60b1ddd2f2ae26a96a9f2d27ddec223c2b9940c91a146dd',
})


def unchanged_button_field(text, variables, mapping):
    digest = hashlib.sha256(text.encode()).hexdigest()
    if digest in _BUTTON_FIELD_LITERAL:
        return not any(identity in text for identity in (*mapping.keys(), *mapping.values()))
    if digest in _BUTTON_FIELD_VARIABLE:
        values = variables.get('source_entities') if type(variables) is dict else None
        return (type(values) is list and all(type(value) is str for value in values)
                and not any(value in mapping.values() for value in values))
    return False
