"""Pure, bounded reference planning. No HA/storage access and no writes.

Adapters MUST select reference-only subtrees; this is deliberately not a global
string replacement engine. A plan describes only its explicitly selected scopes.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Union


class PlanError(ValueError):
    """Invalid input, collision, or resource limit; no partial plan is returned."""


class Consumer(str, Enum):
    AUTOMATION = "automation"
    TEMPLATE = "template"
    DASHBOARD = "dashboard"
    ENERGY = "energy"


class Syntax(str, Enum):
    ENTITY_TREE = "entity_tree"
    JINJA = "jinja"


Path = tuple[Union[str, int], ...]


@dataclass(frozen=True)
class Scope:
    path: Path
    syntax: Syntax


@dataclass(frozen=True)
class Context:
    consumer: Consumer
    scopes: tuple[Scope, ...]


@dataclass(frozen=True)
class Limits:
    max_depth: int = 64
    max_nodes: int = 100_000
    max_string_length: int = 1_000_000
    max_mapping_entries: int = 10_000


@dataclass(frozen=True)
class Change:
    path: Path
    location: str  # "key" or "value"; paths always refer to the original tree
    before: str
    after: str


@dataclass(frozen=True)
class Marker:
    path: Path
    code: str
    detail: str


@dataclass(frozen=True)
class Plan:
    value: Any
    changes: tuple[Change, ...]
    markers: tuple[Marker, ...]
    coverage: str = "explicit_scopes_only"


_ENTITY = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+\Z", re.ASCII)
# A bounded scheme suffix also catches long schemes without quadratic retries
# against a very long alphabetic string that contains no colon.
_EXTERNAL = re.compile(r"(?:[a-z][a-z0-9+.-]{0,31}://|javascript:|<script\b)", re.I)
_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?=([a-z_][a-z0-9_]*\.[a-z0-9_]+)(?![A-Za-z0-9_]))", re.ASCII)


def _external_output_only(text):
    """Allow literal HTTP URLs only in Jinja output text, never expressions."""
    matches = list(_EXTERNAL.finditer(text))
    if not matches or any(not re.fullmatch(r'https?://', m.group(), re.I) for m in matches):
        return False
    try:
        from jinja2 import Environment
        tokens = list(Environment().lex(text))
        if any(_EXTERNAL.search(value) and (kind != 'data' or not value[-1:].isspace())
               for _, kind, value in tokens):
            return False
        found = sum(len(list(_EXTERNAL.finditer(value))) for _, kind, value in tokens if kind == 'data')
        return found == len(matches)
    except Exception:
        return False


def _inert_template(text, mapping):
    """Recognize two complete non-reference forms without weakening other guards."""
    if re.fullmatch(r"\s*\{#(?:(?!#\}).)*#\}\s*", text, re.S):
        return not any(match.group(1) in mapping for match in _TOKEN.finditer(text))
    if not _EXTERNAL.search(text) or '{#' in text or re.search(r'{%[-+]?\s*raw\b', text):
        return False
    try:
        from jinja2 import Environment, nodes
        tree = Environment().parse(text)
        allowed = (nodes.Template, nodes.Output, nodes.Or, nodes.And, nodes.Compare,
                   nodes.Operand, nodes.Call, nodes.Name, nodes.Const)
        for node in [tree, *tree.find_all(nodes.Node)]:
            if not isinstance(node, allowed):
                return False
            if isinstance(node, nodes.Name) and node.name != 'state_attr':
                return False
            if (isinstance(node, nodes.Const) and isinstance(node.value, str)
                    and _EXTERNAL.search(node.value)
                    and not re.match(r'^https?://', node.value, re.I)):
                return False
            if (isinstance(node, nodes.Const) and isinstance(node.value, str)
                    and not re.match(r'^https?://', node.value, re.I)
                    and any(match.group(1) in mapping for match in _TOKEN.finditer(node.value))):
                return False
            if isinstance(node, nodes.Call):
                if (not isinstance(node.node, nodes.Name) or node.node.name != 'state_attr'
                        or len(node.args) != 2 or node.kwargs or node.dyn_args or node.dyn_kwargs
                        or not all(isinstance(arg, nodes.Const) and type(arg.value) is str for arg in node.args)
                        or not (re.match(r'^https?://', node.args[0].value, re.I)
                                or _ENTITY.fullmatch(node.args[0].value)
                                and node.args[0].value not in mapping)):
                    return False
        return any(isinstance(node, nodes.Call) for node in tree.find_all(nodes.Call))
    except Exception:
        return False


def _safe_comments(text, mapping):
    """Ignore only well-formed comments that do not mention a selected ID."""
    comments = list(re.finditer(r"\{#.*?#\}", text, re.S))
    if not comments:
        return '{#' not in text
    outside = re.sub(r"\{#.*?#\}", '', text, flags=re.S)
    return ('{#' not in outside and '#}' not in outside
            and not any(match.group(1) in mapping for comment in comments
                        for match in _TOKEN.finditer(comment.group())))


def _dynamic_notify_service(text, bindings):
    """One native service-name form derived from the current device name."""
    repeat = bindings.get('repeat')
    if not repeat or repeat[0] != 'runtime_repeat':
        return False
    try:
        from jinja2 import Environment, nodes
        tree = Environment().parse(text)
        if len(tree.body) != 1 or not isinstance(tree.body[0], nodes.Output):
            return False
        parts = tree.body[0].nodes
        if len(parts) != 2 or not isinstance(parts[0], nodes.TemplateData) or not re.fullmatch(r'notify\.[a-z0-9_]*', parts[0].data):
            return False
        slug = parts[1]
        if not isinstance(slug, nodes.Filter) or slug.name != 'slugify' or slug.args or slug.kwargs or slug.dyn_args or slug.dyn_kwargs:
            return False
        call = slug.node
        return (isinstance(call, nodes.Call) and isinstance(call.node, nodes.Name)
                and call.node.name == 'device_attr' and 'device_attr' not in bindings
                and len(call.args) == 2 and not (call.kwargs or call.dyn_args or call.dyn_kwargs)
                and isinstance(call.args[0], nodes.Getattr) and call.args[0].attr == 'item'
                and isinstance(call.args[0].node, nodes.Name) and call.args[0].node.name == 'repeat'
                and isinstance(call.args[1], nodes.Const) and call.args[1].value == 'name')
    except Exception:
        return False


def _syntax_template(text, mapping, max_nodes, *, initial_bindings=None,
                     binding_report=None, output_reference=False, trigger_entities=(), alias_only=False, strict_trigger_keys=False, log_payload=False):
    """Parse only: return exact replacements and review marker (code, detail) pairs.

    Jinja's parser has no columns. Feed its native lexer tokens through a parser
    that attaches original character spans to primary nodes. Never render or
    compile a template, evaluate expressions, or call a template function.
    """
    try:
        from jinja2 import Environment, nodes
        from jinja2.lexer import TokenStream
        from jinja2.parser import Parser
    except ImportError:
        return text, [('unsupported_template_parser', 'Native Jinja parser unavailable')]

    class Unsupported(ValueError):
        pass

    original_text = text
    # Parse Jinja's normalized newlines while retaining an exact original-source
    # boundary for every character. Only authorized spans are written back.
    offsets, normalized = [], []
    cursor = 0
    while cursor < len(original_text):
        offsets.append(cursor)
        character = original_text[cursor]
        normalized.append('\n' if character == '\r' else character)
        cursor += 2 if original_text[cursor:cursor + 2] == '\r\n' else 1
    offsets.append(len(original_text))
    text = ''.join(normalized)
    try:
        env = Environment(keep_trailing_newline=True,
                          extensions=('jinja2.ext.loopcontrols', 'jinja2.ext.do'))
        raw = []
        cursor = 0
        cap = min(max_nodes, 10000)
        for line, kind, value in env.lex(text):
            if len(raw) >= cap:
                raise Unsupported('Template token budget exceeded')
            start = text.find(value, cursor)
            if start < 0 or text[cursor:start].strip():
                raise Unsupported('Ambiguous native lexer source span')
            raw.append((line, kind, value, start, start + len(value)))
            cursor = start + len(value)
        if text[cursor:].strip():
            raise Unsupported('Incomplete native lexer source coverage')
        tokens, spans, indexes = [], {}, {}
        for line, kind, value, start, end in raw:
            for token in env.lexer.wrap(iter([(line, kind, value)]), None):
                indexes[id(token)] = len(tokens)
                spans[id(token)] = (start, end)
                tokens.append(token)

        class SpannedParser(Parser):
            def parse_primary(self, *args, **kwargs):
                token = self.stream.current
                node = super().parse_primary(*args, **kwargs)
                if token.type in ('name', 'string'):
                    span = spans[id(token)]
                    if token.type == 'string':
                        # Adjacent Jinja strings are folded into one Const. An
                        # escaped literal also has no one-to-one editable ID.
                        following = indexes[id(token)] + 1
                        if following < len(tokens) and tokens[following].type == 'string':
                            span = None
                        elif text[span[0] + 1:span[1] - 1] != node.value:
                            span = None
                        elif span:
                            span = (span[0] + 1, span[1] - 1)
                    node.source_span = span
                return node

            def parse_subscript(self, node):
                token = self.stream.current
                position = indexes[id(token)]
                result = super().parse_subscript(node)
                if token.type == 'dot' and isinstance(result, nodes.Getattr):
                    result.source_span = spans[id(tokens[position + 1])]
                return result

        # Only Jinja's built-in loopcontrol/do parsers, also enabled by HA.
        # No integration/user extension callbacks and no compilation/rendering.
        env._tokenize = lambda *args, **kwargs: TokenStream(tokens, None, None)
        tree = SpannedParser(env, text).parse()
        count = 0
        pending = [(tree, 0)]
        all_nodes = []
        while pending:
            node, depth = pending.pop()
            count += 1
            if depth > 64 or count > cap:
                raise Unsupported('Template AST budget exceeded')
            all_nodes.append(node)
            pending.extend((child, depth + 1) for child in node.iter_child_nodes())

        globals_ = {'states', 'is_state', 'state_attr', 'is_state_attr', 'expand'}
        registry_globals = {'integration_entities', 'device_id', 'device_entities'}
        unsupported = (nodes.Macro, nodes.Import, nodes.FromImport, nodes.CallBlock,
                       nodes.Include, nodes.Extends,
                       nodes.Block, nodes.FilterBlock)
        for node in all_nodes:
            if isinstance(node, unsupported):
                return original_text, [('unsupported_template_scope', 'Unsupported lexical template scope')]
            if isinstance(node, nodes.NSRef) and (node.name in globals_ or node.attr in globals_):
                return original_text, [('unsupported_template_scope', 'HA global namespace binding')]
            if isinstance(node, nodes.Name) and node.ctx in ('store', 'param') and node.name in globals_ | registry_globals:
                return original_text, [('unsupported_template_scope', 'HA global shadowed by local binding')]

        if trigger_entities and any(isinstance(node, nodes.Name) and node.name == 'trigger'
                and node.ctx in ('store', 'param') for node in all_nodes):
            trigger_entities = ()
        if (initial_bindings and initial_bindings.get('this') == ('native_self', ())
                and any(isinstance(node, nodes.Name) and node.name == 'this'
                        and node.ctx in ('store', 'param') for node in all_nodes)):
            return original_text, [('unsupported_template_scope', 'Native self shadowed')]
        markers = []
        used, unsafe = set(), set()
        replacements = {}
        work_left = cap * 8

        def charge(amount=1):
            nonlocal work_left
            work_left -= amount
            if work_left < 0:
                raise Unsupported('Static dataflow work budget exceeded')

        def escape(bound):
            charge(len(bound[1]))
            unsafe.update(bound[1])
            if bound[0] == 'keys' and len(bound) > 2:
                # An opaque consumer of the map also sees every value.
                for _, member in bound[2]:
                    charge(len(member[1]))
                    unsafe.update(member[1])

        # A value is (shape, immutable origins); origin = (start, end, entity).
        def join_values(values):
            values = [item for item in values if item[0] != 'missing']
            shapes = {item[0] for item in values}
            if len(shapes) != 1:
                return None
            shape, = shapes
            origins = tuple(sorted({origin for item in values for origin in item[1]}))
            charge(len(origins))
            if shape != 'record':
                return (shape, origins)
            fields = [dict(item[2]) for item in values]
            nonempty = [field for field in fields if field]
            common = set.intersection(*(set(field) for field in nonempty)) if nonempty else set()
            return ('record', origins, tuple((name, merged) for name in sorted(common)
                if (merged := join_values([field[name] for field in nonempty])) is not None))

        def property_value(bound, key, default=None):
            if not bound or bound[0] != 'record':
                return None
            fields = dict(bound[2])
            charge(len(fields))
            if key is not None:
                found = fields.get(key, default)
                return None if found and found[0] == 'unknown' else found
            # A runtime key may select any value in this finite map. Track only
            # members with a common shape. Unproven values are never discarded.
            alternatives = list(fields.values())
            if default is not None:
                alternatives.append(default)
            return join_values(alternatives) if alternatives else None

        def trigger_key_lookup(container, selector, bindings):
            # The native event supplies the renamed ID. Only a complete table
            # with collision-checked keys and no alternate selector is safe.
            if (not strict_trigger_keys or not trigger_entities or 'trigger' in bindings
                    or not container or container[0] != 'keys'
                    or not isinstance(selector, nodes.Getattr) or selector.attr != 'entity_id'
                    or not isinstance(selector.node, nodes.Name) or selector.node.name != 'trigger'):
                return None
            keys = {origin[2] for origin in container[1]}
            if not set(trigger_entities) <= keys:
                return None
            consume([('keys', tuple(origin for origin in container[1] if origin[2] in trigger_entities))])
            fields = dict(container[2]) if len(container) > 2 else {}
            selected = [fields.get(key) for key in trigger_entities]
            if selected and all(item and item[0] == 'scalar' for item in selected):
                return join_values(selected)
            return ('metadata', ())

        def value(node, bindings):
            charge()
            if isinstance(node, nodes.Const) and type(node.value) is str:
                span = getattr(node, 'source_span', None)
                if span and _ENTITY.fullmatch(node.value):
                    return ('scalar', ((span[0], span[1], node.value),))
            if isinstance(node, nodes.Name):
                bound = bindings.get(node.name)
                if bound:
                    charge(len(bound[1]))
                return bound
            if (isinstance(node, nodes.Call) and isinstance(node.node, nodes.Name)
                    and node.node.name not in bindings and len(node.args) == 1
                    and not (node.kwargs or node.dyn_args or node.dyn_kwargs)):
                name, arg = node.node.name, node.args[0]
                if (name == 'integration_entities' and isinstance(arg, nodes.Const)
                        and isinstance(arg.value, str) and not _ENTITY.fullmatch(arg.value)):
                    return ('registry_entity_list', ())
                argument = value(arg, bindings)
                if name == 'device_id' and argument and argument[0] == 'registry_entity':
                    return ('registry_device', ())
                if name == 'device_entities' and argument and argument[0] == 'registry_device':
                    return ('registry_entity_list', ())
                if name == 'expand' and argument and argument[0] == 'registry_entity_list':
                    return ('registry_state_list', ())
            if isinstance(node, (nodes.Getattr, nodes.Getitem)):
                attribute = (node.attr if isinstance(node, nodes.Getattr) else
                             node.arg.value if isinstance(node.arg, nodes.Const) else None)
                if (attribute == 'entity_id' and isinstance(node.node, nodes.Name)
                        and node.node.name == 'this'
                        and bindings.get('this') == ('native_self', ())):
                    return ('scalar', ())
                if (strict_trigger_keys and trigger_entities and attribute == 'entity_id'
                        and isinstance(node.node, nodes.Name) and node.node.name == 'trigger'
                        and 'trigger' not in bindings):
                    return ('scalar', ())
                bound = value(node.node, bindings)
                if attribute == 'item' and bound:
                    if bound[0] == 'runtime_repeat_numeric':
                        return ('metadata', ())
                    if bound[0] == 'runtime_repeat_item':
                        return ('scalar', bound[1])
                    if bound[0] == 'runtime_repeat_record':
                        return bound[2]
                if isinstance(node, nodes.Getitem) and not isinstance(node.arg, nodes.Const):
                    lookup = trigger_key_lookup(bound, node.arg, bindings)
                    if lookup is not None:
                        return lookup
                    index = value(node.arg, bindings)
                    if index:
                        escape(index)  # selection is metadata, not an entity sink
                return property_value(bound, attribute)
            if (isinstance(node, nodes.Call) and isinstance(node.node, nodes.Getattr)
                    and node.node.attr == 'get' and 1 <= len(node.args) <= 2
                    and not (node.kwargs or node.dyn_args or node.dyn_kwargs)):
                bound = value(node.node.node, bindings)
                if len(node.args) == 1:
                    lookup = trigger_key_lookup(bound, node.args[0], bindings)
                    if lookup is not None:
                        return lookup
                if bound and bound[0] == 'record':
                    selector = node.args[0]
                    if isinstance(selector, nodes.Const) and type(selector.value) is not str:
                        return None
                    key = selector.value if isinstance(selector, nodes.Const) else None
                    if key is None:
                        expression(selector, bindings)
                    fallback = node.args[1] if len(node.args) == 2 else None
                    default = value(fallback, bindings) if fallback is not None else None
                    if default is None and isinstance(fallback, nodes.Dict) and not fallback.items:
                        default = ('record', (), ())
                    elif default is None and isinstance(fallback, nodes.Const) and fallback.value in (None, ''):
                        default = ('missing', ())
                    if fallback is not None and default is None:
                        expression(fallback, bindings)
                        return None
                    return property_value(bound, key, default)
            if (isinstance(node, nodes.Filter) and node.name in ('select', 'reject')
                    and len(node.args) in (2, 3) and isinstance(node.args[0], nodes.Const)
                    and (node.args[0].value, len(node.args)) in (('is_state', 2), ('is_state_attr', 3))
                    and not (node.kwargs or node.dyn_args or node.dyn_kwargs)):
                bound = value(node.node, bindings)
                if bound and bound[0] == 'list':
                    consume([bound])
                    for argument in node.args[1:]:
                        expression(argument, bindings)
                    return bound
            if (isinstance(node, nodes.Filter) and node.name == 'list'
                    and not (node.args or node.kwargs or node.dyn_args or node.dyn_kwargs)):
                bound = value(node.node, bindings)
                if bound and bound[0] in ('keys', 'list'):
                    return ('list', bound[1])
            if (isinstance(node, nodes.Call) and isinstance(node.node, nodes.Getattr)
                    and node.node.attr in ('keys', 'items')
                    and not (node.args or node.kwargs or node.dyn_args or node.dyn_kwargs)):
                bound = value(node.node.node, bindings)
                if bound and bound[0] == 'keys':
                    return ('list' if node.node.attr == 'keys' else 'items', bound[1])
            if (isinstance(node, nodes.CondExpr) and isinstance(node.test, nodes.Test)
                    and node.test.name == 'list' and not (node.test.args or node.test.kwargs)
                    and isinstance(node.test.node, nodes.Name)
                    and isinstance(node.expr1, nodes.Name)
                    and isinstance(node.expr2, nodes.List) and len(node.expr2.items) == 1
                    and isinstance(node.expr2.items[0], nodes.Name)
                    and node.test.node.name == node.expr1.name == node.expr2.items[0].name):
                bound = value(node.expr1, bindings)
                if bound and bound[0] in ('scalar', 'list'):
                    return ('list', bound[1])
            if isinstance(node, (nodes.List, nodes.Tuple)):
                values = [value(item, bindings) for item in node.items]
                if values and all(item is not None and item[0] == 'scalar' for item in values):
                    origins = set()
                    for item in values:
                        charge(len(item[1]))
                        origins.update(item[1])
                    return ('list', tuple(sorted(origins)))
            return None

        def mark(code, detail):
            if (code, detail) not in markers:
                markers.append((code, detail))

        def consume(values):
            for item in values:
                charge(len(item[1]))
                used.update(item[1])

        def access(node):
            parts = []
            while isinstance(node, (nodes.Getattr, nodes.Getitem)):
                if isinstance(node, nodes.Getattr):
                    parts.append((node.attr, getattr(node, 'source_span', None), False))
                else:
                    arg = node.arg
                    if isinstance(arg, nodes.Const) and type(arg.value) is str:
                        parts.append((arg.value, getattr(arg, 'source_span', None), True))
                    else:
                        parts.append((None, None, True))
                node = node.node
            if not isinstance(node, nodes.Name) or node.name != 'states':
                return None
            return list(reversed(parts))

        def trailing_indices(node, count, bindings):
            for _ in range(count):
                if isinstance(node, nodes.Getitem):
                    expression(node.arg, bindings)
                node = node.node

        def state_collection(node, bindings):
            """Prove HA State collections and exact entity_id comparison sinks.

            Arbitrary records with an entity_id label are not HA references.
            Return origin values without consuming them until the whole chain
            is known; dynamic predicates cannot authorize a partial rewrite.
            """
            charge()
            if isinstance(node, nodes.Name) and node.name == 'states':
                return ()
            if isinstance(node, (nodes.Getattr, nodes.Getitem)) and isinstance(node.node, nodes.Name) and node.node.name == 'states':
                domain = (node.attr if isinstance(node, nodes.Getattr) else
                          node.arg.value if isinstance(node.arg, nodes.Const)
                          and type(node.arg.value) is str else None)
                # AllStates has private Python attributes and reserves these
                # Jinja context probes; neither path returns DomainStates.
                if (type(domain) is str and re.fullmatch(r'[a-z][a-z0-9_]*', domain)
                        and domain not in ('contextfunction', 'environmentfunction',
                                           'evalcontextfunction', 'jinja_pass_arg')):
                    return ()
                return None
            if (not isinstance(node, nodes.Filter)
                    or node.kwargs or node.dyn_args or node.dyn_kwargs):
                return None
            if node.name == 'list' and not node.args:
                return state_collection(node.node, bindings)
            if (node.name in ('selectattr', 'rejectattr') and 1 <= len(node.args) <= 3
                    and isinstance(node.args[0], nodes.Const)
                    and node.args[0].value != 'entity_id'):
                def metadata_constant(item):
                    if isinstance(item, nodes.Const):
                        return (not isinstance(item.value, str)
                                or not any(m.group(1) in mapping for m in _TOKEN.finditer(item.value)))
                    return (isinstance(item, (nodes.List, nodes.Tuple))
                            and all(metadata_constant(child) for child in item.items))
                if all(metadata_constant(item) for item in node.args):
                    return state_collection(node.node, bindings)
            if (node.name in ('selectattr', 'rejectattr') and len(node.args) == 3
                    and isinstance(node.args[0], nodes.Const) and node.args[0].value == 'entity_id'
                    and isinstance(node.args[1], nodes.Const)
                    and node.args[1].value in ('equalto', 'eq', '==')):
                bound = value(node.args[2], bindings)
                parents = state_collection(node.node, bindings)
                if parents is not None and bound is not None and bound[0] == 'scalar':
                    return parents + (bound,)
            return None

        def expression(node, bindings):
            charge()
            if (isinstance(node, nodes.Test) and node.name in ('defined', 'undefined', 'list', 'string')
                    and not (node.args or node.kwargs or node.dyn_args or node.dyn_kwargs)
                    and isinstance(node.node, nodes.Name)):
                return  # This tests presence/type, independently of the ID.
            if (isinstance(node, nodes.Filter) and node.name in ('count', 'length')
                    and not (node.args or node.kwargs or node.dyn_args or node.dyn_kwargs)):
                bound = value(node.node, bindings)
                if bound and bound[0] in ('list', 'record', 'record_list'):
                    return
            if isinstance(node, nodes.Compare) and len(node.ops) == 1:
                operand = node.ops[0]
                left = node.expr
                if (operand.op in ('eq', 'ne') and isinstance(operand.expr, nodes.Const)
                        and operand.expr.value == '' and isinstance(left, nodes.Filter)
                        and left.name in ('trim', 'lower', 'upper')
                        and not (left.args or left.kwargs or left.dyn_args or left.dyn_kwargs)):
                    bound = value(left.node, bindings)
                    if bound and bound[0] == 'scalar':
                        consume([bound])
                        return
                right = value(node.ops[0].expr, bindings)
                left_value = value(node.expr, bindings)
                if (node.ops[0].op in ('in', 'notin') and right and right[0] == 'list'
                        and left_value and left_value[0] == 'scalar'
                        and set(left_value[1]) <= set(right[1])):
                    # Both sides derive from the same finite entity collection;
                    # renaming its origins preserves membership exactly.
                    consume([left_value, right])
                    return
                if right and right[0] == 'record' and node.ops[0].op in ('in', 'notin'):
                    expression(node.expr, bindings)
                    return
                operand = node.ops[0]
                left, right = node.expr, operand.expr
                if isinstance(left, nodes.Const):
                    left, right = right, left
                if (operand.op in ('eq', 'ne') and isinstance(left, nodes.Getattr)
                        and left.attr == 'entity_id' and isinstance(left.node, nodes.Name)
                        and left.node.name == 'trigger' and 'trigger' not in bindings
                        and isinstance(right, nodes.Const) and type(right.value) is str
                        and right.value in trigger_entities):
                    bound = value(right, bindings)
                    if bound:
                        consume([bound])
                        return
            if (isinstance(node, nodes.Getitem) and isinstance(node.arg, nodes.Const)
                    and node.arg.value == 0 and isinstance(node.node, nodes.Call)
                    and isinstance(node.node.node, nodes.Getattr)
                    and node.node.node.attr == 'split' and len(node.node.args) == 1
                    and isinstance(node.node.args[0], nodes.Const)
                    and node.node.args[0].value == '.'
                    and not (node.node.kwargs or node.node.dyn_args or node.node.dyn_kwargs)):
                bound = value(node.node.node.node, bindings)
                if bound and bound[0] == 'scalar':
                    # Mapping validates same-domain IDs; their domain prefix is invariant.
                    consume([bound])
                    return
            if isinstance(node, nodes.Dict):
                fields = {pair.key.value: pair.value for pair in node.items
                          if isinstance(pair.key, nodes.Const) and type(pair.key.value) is str}
                entity, state = fields.get('entity_id'), fields.get('state')
                candidate = value(entity, bindings) if entity is not None else None
                if (len(fields) == len(node.items) and candidate and candidate[0] == 'scalar'
                        and isinstance(state, nodes.Call) and isinstance(state.node, nodes.Name)
                        and state.node.name == 'states' and len(state.args) == 1
                        and value(state.args[0], bindings) == candidate
                        and not (state.kwargs or state.dyn_args or state.dyn_kwargs)):
                    consume([candidate])
                    for key, child in fields.items():
                        if key != 'entity_id':
                            expression(child, bindings)
                    return
            if isinstance(node, nodes.Filter):
                collection = state_collection(node, bindings)
                if (collection is None and node.name in ('count', 'length')
                        and not (node.args or node.kwargs or node.dyn_args or node.dyn_kwargs)):
                    collection = state_collection(node.node, bindings)
                if collection is not None:
                    consume(collection)
                    return
            # HA creates this local object for every repeat iteration. Counters
            # and flags carry no entity provenance; item/dynamic access may.
            if isinstance(node, (nodes.Getattr, nodes.Getitem)) and isinstance(node.node, nodes.Name):
                bound = bindings.get(node.node.name)
                if bound and bound[0] in ('runtime_repeat', 'runtime_repeat_item', 'runtime_repeat_numeric'):
                    attribute = (node.attr if isinstance(node, nodes.Getattr) else
                                 node.arg.value if isinstance(node.arg, nodes.Const)
                                 and type(node.arg.value) is str else None)
                    if attribute == 'item' and bound[0] == 'runtime_repeat_numeric':
                        return
                    if attribute == 'item' and bound[0] == 'runtime_repeat_item':
                        escape(('scalar', bound[1]))
                    elif attribute not in {'index', 'first', 'last'}:
                        mark('unsupported_runtime_repeat', 'Unproven runtime repeat item or access')
                    return
            if isinstance(node, (nodes.Getattr, nodes.Getitem, nodes.Call)):
                bound = value(node, bindings)
                if bound:
                    escape(bound)
                    return
                if isinstance(node, (nodes.Getattr, nodes.Getitem)):
                    parent = value(node.node, bindings)
                    if parent and parent[0] == 'record' and (isinstance(node, nodes.Getattr)
                            or isinstance(node.arg, nodes.Const)):
                        return
                if (isinstance(node, nodes.Call) and isinstance(node.node, nodes.Getattr)
                        and node.node.attr == 'get' and node.args and isinstance(node.args[0], nodes.Const)
                        and (parent := value(node.node.node, bindings)) and parent[0] == 'record'
                        and node.args[0].value in dict(parent[2])):
                    return
            if isinstance(node, nodes.Name):
                bound = bindings.get(node.name)
                if bound and bound[0] in ('runtime_repeat', 'runtime_repeat_item', 'runtime_repeat_numeric'):
                    escape(bound)
                    mark('unsupported_runtime_repeat', 'Runtime repeat object escapes counter access')
                    return
            if (isinstance(node, nodes.Getattr) and isinstance(node.node, nodes.Name)
                    and node.node.name == 'loop' and bindings.get('loop')
                    and bindings['loop'][0] == 'loop'
                    and node.attr in {'index', 'index0', 'revindex', 'revindex0',
                                      'first', 'last', 'length', 'depth', 'depth0'}):
                return  # Loop counters/flags do not expose iterable entity IDs.
            if isinstance(node, nodes.Getitem):
                container = value(node.node, bindings)
                key = value(node.arg, bindings)
                if (container and container[0] == 'keys' and key and key[0] == 'scalar'
                        and {origin[2] for origin in key[1]}
                            <= {origin[2] for origin in container[1]}):
                    # Lookup into adapter-proven entity-key metadata. Renaming
                    # the key and index preserves the metadata value exactly;
                    # it does not make that metadata an entity reference.
                    consume([container, key])
                    return
            if isinstance(node, nodes.Call):
                name = node.node.name if isinstance(node.node, nodes.Name) else None
                if name in globals_:
                    args = node.args if name == 'expand' else node.args[:1]
                    values = [value(arg, bindings) for arg in args]
                    valid = bool(args) and all(item is not None and item[0] in ('scalar', 'list')
                                               for item in values)
                    if name != 'expand':
                        valid = valid and values[0][0] == 'scalar'
                    valid = valid and not (node.kwargs or node.dyn_args or node.dyn_kwargs)
                    if valid:
                        consume(values)
                        if name != 'expand':
                            for arg in node.args[1:]:
                                expression(arg, bindings)
                        return
                    mark('dynamic_reference', 'Unresolved HA call argument or call signature')
                    # Failed sinks do not authorize any partial literal rewrite.
                    for arg in node.iter_child_nodes():
                        expression(arg, bindings)
                    return
            parts = access(node) if isinstance(node, (nodes.Getattr, nodes.Getitem)) else None
            if parts is not None:
                if parts and parts[0][0] and _ENTITY.fullmatch(parts[0][0]):
                    entity, span, _ = parts[0]
                    if span:
                        consume([('scalar', ((span[0], span[1], entity),))])
                        trailing_indices(node, len(parts) - 1, bindings)
                        return
                elif len(parts) >= 2 and parts[0][0] and parts[1][0]:
                    entity = parts[0][0] + '.' + parts[1][0]
                    if _ENTITY.fullmatch(entity) and parts[0][1] and parts[1][1]:
                        first, second = parts[0], parts[1]
                        if not first[2] and not second[2]:
                            span = (first[1][0], second[1][1])
                            consume([('scalar', ((span[0], span[1], entity),))])
                        else:
                            # Split string indices: only object ID changes.
                            span = second[1]
                            origin = (span[0], span[1], entity)
                            used.add(origin)
                            if entity in mapping:
                                replacements[origin] = mapping[entity].split('.', 1)[1]
                        trailing_indices(node, len(parts) - 2, bindings)
                        return
                mark('dynamic_reference', 'Unresolved states object access')
                # Inspect only computed indices; static property names are labels.
                current = node
                while isinstance(current, (nodes.Getattr, nodes.Getitem)):
                    if isinstance(current, nodes.Getitem):
                        expression(current.arg, bindings)
                    current = current.node
                return
            if isinstance(node, nodes.Name):
                if node.name in globals_:
                    mark('dynamic_reference', 'HA global used outside recognized reference syntax')
                bound = bindings.get(node.name)
                if bound:
                    escape(bound)
                return
            if isinstance(node, nodes.Const):
                bound = value(node, bindings)
                if bound:
                    escape(bound)
                return
            for child in node.iter_child_nodes():
                expression(child, bindings)

        def assigned(statements):
            names = set()
            for stmt in statements:
                for child in [stmt] + list(stmt.find_all(nodes.Name)):
                    charge()
                    if isinstance(child, nodes.Name) and child.ctx == 'store':
                        names.add(child.name)
            return names

        def statements(items, bindings):
            for node in items:
                charge()
                if (isinstance(node, nodes.Output) and log_payload and len(tree.body) == 1
                        and tree.body[0] is node and any(isinstance(part, nodes.TemplateData) and part.data
                                                       for part in node.nodes)
                        and all(isinstance(part, (nodes.TemplateData, nodes.Name)) for part in node.nodes)
                        and sum(isinstance(part, nodes.Name) and part.name == 'entity_id' for part in node.nodes) == 1
                        and (bound := bindings.get('entity_id')) and bound[0] == 'scalar'):
                    consume([bound])
                    for part in node.nodes:
                        if not (isinstance(part, nodes.Name) and part.name == 'entity_id'):
                            expression(part, bindings)
                elif (isinstance(node, nodes.Output) and output_reference
                        and len(tree.body) == 1 and tree.body[0] is node):
                    # An entity field may return a native scalar/list. Surrounding
                    # non-whitespace output or concatenation is not a reference.
                    expressions = [child for child in node.nodes
                                   if not (isinstance(child, nodes.TemplateData)
                                           and not child.data.strip())]
                    bound = value(expressions[0], bindings) if len(expressions) == 1 else None
                    if bound and (bound[0] in ('scalar', 'list') or alias_only
                                  and bound[0] in ('record', 'record_list')):
                        if binding_report is not None:
                            binding_report['output_value'] = bound
                        consume([bound])
                    else:
                        expression(node, bindings)
                elif isinstance(node, nodes.Assign):
                    bound = value(node.node, bindings)
                    if bound is None:
                        expression(node.node, bindings)
                    if isinstance(node.target, nodes.Name):
                        bindings[node.target.name] = bound
                    elif isinstance(node.target, nodes.NSRef):
                        # A numeric/state-result accumulator can depend on
                        # states(e) without storing the entity ID itself. The
                        # RHS was inspected above. Storing proven entity origins
                        # in a mutable namespace still needs a richer model.
                        if bound:
                            escape(bound)
                            mark('unsupported_template_scope', 'Namespace entity provenance unsupported')
                    else:
                        # Destructuring needs element provenance; remain unknown.
                        if bound:
                            escape(bound)
                        for name in node.target.find_all(nodes.Name):
                            bindings[name.name] = None
                elif isinstance(node, nodes.AssignBlock):
                    statements(node.body, bindings.copy())
                    if isinstance(node.target, nodes.Name):
                        bindings[node.target.name] = None
                elif isinstance(node, nodes.For):
                    bound = value(node.iter, bindings)
                    local = bindings.copy()
                    supported = (bound is not None and bound[0] in ('list', 'keys', 'registry_entity_list', 'registry_state_list')
                                 and isinstance(node.target, nodes.Name)
                                 and node.test is None and not node.recursive)
                    item_pair = (bound is not None and bound[0] == 'items'
                                 and isinstance(node.target, nodes.Tuple)
                                 and len(node.target.items) == 2
                                 and all(isinstance(item, nodes.Name) for item in node.target.items)
                                 and node.test is None and not node.recursive)
                    if item_pair:
                        # Only YAML map keys have proven entity provenance.
                        # Metadata is deliberately unknown and cannot inherit it.
                        local[node.target.items[0].name] = ('scalar', bound[1])
                        local[node.target.items[1].name] = None
                        local['loop'] = ('loop', bound[1])
                        statements(node.body, local)
                        statements(node.else_, bindings.copy())
                        continue
                    if supported:
                        item_shape = {'registry_entity_list': 'registry_entity',
                                      'registry_state_list': 'registry_state'}.get(bound[0], 'scalar')
                        local[node.target.name] = (item_shape, bound[1])
                        # previtem/nextitem expose iterable values too. Treat all
                        # loop metadata uses conservatively as origin escapes.
                        local['loop'] = ('loop', bound[1])
                    else:
                        collection = state_collection(node.iter, bindings)
                        if (collection is not None and isinstance(node.target, nodes.Name)
                                and not node.recursive):
                            # Iterating native HA State objects needs no ID
                            # rewrite; exact filters on the collection still do.
                            consume(collection)
                        else:
                            expression(node.iter, bindings)
                        targets = [node.target] + list(node.target.find_all(nodes.Name))
                        for target in targets:
                            if isinstance(target, nodes.Name):
                                local[target.name] = None
                    if node.test is not None:
                        expression(node.test, local)
                    statements(node.body, local)
                    statements(node.else_, bindings.copy())
                elif isinstance(node, nodes.If):
                    expression(node.test, bindings)
                    branches = []
                    branch = bindings.copy()
                    statements(node.body, branch)
                    branches.append(branch)
                    for elif_node in node.elif_:
                        branch = bindings.copy()
                        expression(elif_node.test, branch)
                        statements(elif_node.body, branch)
                        branches.append(branch)
                    branch = bindings.copy()
                    statements(node.else_, branch)
                    branches.append(branch)
                    for name in assigned([node]):
                        # Every branch starts from the incoming environment.
                        # Join the actual possible outputs, not an extra path
                        # that skips an exhaustive if/else. Equal known shapes
                        # preserve all producer origins; undefined/dynamic or
                        # incompatible branches remain unknown.
                        origins, shapes = set(), set()
                        for branch in branches:
                            bound = branch.get(name)
                            shapes.add(bound[0] if bound else 'unknown')
                            if bound:
                                charge(len(bound[1]))
                                origins.update(bound[1])
                        shape = next(iter(shapes)) if len(shapes) == 1 else 'unknown'
                        bindings[name] = (shape, tuple(sorted(origins))) if origins else None
                elif isinstance(node, nodes.With):
                    local = bindings.copy()
                    for target, item in zip(node.targets, node.values):
                        # Jinja evaluates every RHS against the outer scope.
                        bound = value(item, bindings)
                        if bound is None:
                            expression(item, bindings)
                        if isinstance(target, nodes.Name):
                            local[target.name] = bound
                        else:
                            if bound:
                                escape(bound)
                            mark('unsupported_template_scope', 'Unsupported with target')
                    statements(node.body, local)
                elif isinstance(node, nodes.Scope):
                    statements(node.body, bindings.copy())
                else:
                    expression(node, bindings)

        statements(tree.body, dict(initial_bindings or {}))
        if binding_report is not None:
            binding_report.update(used=set(used), unsafe=set(unsafe), complete=True)
        for origin in used & unsafe:
            mark('unsupported_reference', 'Static origin also used outside a recognized HA reference')
        authorized = used - unsafe
        # Preserve old visibility of source tokens in labels, custom functions,
        # comments and raw text; these are blockers, never replacement targets.
        approved_spans = {(a, b) for a, b, _ in authorized if a >= 0}
        ordered_spans = sorted(approved_spans)
        span_starts = [span[0] for span in ordered_spans]
        data_spans = [(start, end) for _, kind, _, start, end in raw if kind == 'data']
        data_starts = [span[0] for span in data_spans]
        has_template_code = any(kind in ('variable_begin', 'block_begin') for _, kind, _, _, _ in raw)
        for match in _TOKEN.finditer(text):
            position = bisect_right(data_starts, match.start(1)) - 1
            if has_template_code and position >= 0 and match.end(1) <= data_spans[position][1]:
                continue  # Literal template output is a label, not a dependency.
            if match.group(1) in mapping and match.span(1) not in approved_spans:
                # Spaced states.sensor . object references cover several tokens.
                position = bisect_right(span_starts, match.start(1)) - 1
                if position < 0 or match.end(1) > ordered_spans[position][1]:
                    mark('unsupported_reference', 'Unrecognized entity token left unchanged')
        edits = []
        for origin in authorized:
            a, b, entity = origin
            if a >= 0 and entity in mapping:
                edits.append((a, b, replacements.get(origin, mapping[entity])))
        edits.sort()
        if any(left[1] > right[0] for left, right in zip(edits, edits[1:])):
            raise Unsupported('Overlapping source spans')
        result = original_text
        for a, b, replacement in reversed(edits):
            result = result[:offsets[a]] + replacement + result[offsets[b]:]
        return result, markers
    except (Unsupported, RecursionError, ValueError, TypeError, KeyError, IndexError) as err:
        return original_text, [('unsupported_template', str(err)[:160])]
    except Exception as err:
        # Native parser syntax/version incompatibility is explicitly unsupported;
        # never fall back to regex rewriting after a failed syntax analysis.
        return original_text, [('unsupported_template', 'Native parser rejected template: ' + type(err).__name__)]


def analyze_template_alias(text, bindings, *, max_nodes=10000, allow_literals=False, allow_records=False,
                           trigger_entities=(), strict_trigger_keys=False, lookup_dependencies=None):
    """Prove a whole native-template scalar/list alias without rendering it.

    Only adapter-owned external origins can flow into a later YAML binding.
    Creating an alias does not authorize renaming its producer: a real entity
    sink must still consume it, and label/custom consumers remain unsafe.
    """
    if (set(bindings) & {'states', 'is_state', 'state_attr', 'is_state_attr', 'expand'}
            or _EXTERNAL.search(text) or '{#' in text
            or re.search(r'{%[-+]?\s*raw\b', text)):
        return None
    report = {}
    _, markers = _syntax_template(text, {}, max_nodes, initial_bindings=bindings,
                                   binding_report=report, output_reference=True, alias_only=allow_records,
                                   trigger_entities=trigger_entities, strict_trigger_keys=strict_trigger_keys)
    bound = report.get('output_value')
    if (markers or report.get('unsafe') or not report.get('complete') or not bound
            or bound[0] not in (('scalar', 'list', 'record', 'record_list') if allow_records else ('scalar', 'list'))
            or not bound[1]
            or not allow_literals and any(origin[0] >= 0 for origin in bound[1])):
        return None
    if lookup_dependencies is not None:
        lookup_dependencies.update(report.get('used', set()) - set(bound[1]))
    return bound


def analyze_template_bindings(text, mapping, bindings, *, max_nodes=10000,
                              output_reference=False, trigger_entities=(), strict_trigger_keys=False,
                              dynamic_service=False, log_payload=False):
    """Parse Jinja with adapter-owned origins, without evaluating any expression.

    Bindings use the existing (shape, origins) bounded dataflow representation.
    External origins must have a negative start, so they can never be edited as
    template source. Missing parser/unsupported scope retains every origin as an
    unsafe consumer. The adapter owns source location and scope validation.
    """
    origins = {origin for bound in bindings.values() if bound for origin in bound[1]}
    origins.update(origin for bound in bindings.values() if bound and bound[0] == 'keys'
                   and len(bound) > 2 for _, field in bound[2] for origin in field[1])
    if any(origin[0] >= 0 for origin in origins):
        raise PlanError('external binding origins must have negative positions')
    if set(bindings) & {'states', 'is_state', 'state_attr', 'is_state_attr', 'expand'}:
        return text, [('unsupported_template_scope', 'YAML binding shadows HA global')], set(), origins
    if dynamic_service and _dynamic_notify_service(text, bindings):
        return text, [], set(), set()
    if _inert_template(text, mapping):
        return text, [], set(), set()
    if (_EXTERNAL.search(text) and not _external_output_only(text)) or not _safe_comments(text, mapping) or re.search(r'{%[-+]?\s*raw\b', text):
        return text, [('unsupported_template', 'External/comment/raw-bearing template')], set(), origins
    report = {}
    value, markers = _syntax_template(
        text, mapping, max_nodes, initial_bindings=bindings,
        binding_report=report, output_reference=output_reference, trigger_entities=trigger_entities,
        strict_trigger_keys=strict_trigger_keys, log_payload=log_payload)
    used = report.get('used', set()) & origins
    unsafe = report.get('unsafe', set()) & origins
    if not report.get('complete'):
        unsafe |= origins
    return value, markers, used, unsafe


def plan_references(
    value: Any,
    mapping: dict[str, str],
    context: Context,
    *,
    limits: Limits = Limits(),
) -> Plan:
    """Return a detached tree and deterministic ordered changes/markers.

    ENTITY_TREE rewrites only complete string values and string dictionary keys.
    JINJA rewrites recognized HA references inside {{ }} / {% %} blocks only.
    Input trees are JSON-like (plain dict/list/string/int/float/bool/None), with
    string or integer dictionary keys. Unknown/custom objects and cycles fail.
    Each adapter selects its own paths after schema validation. No built-in schema
    discovery, disk writes, registry rename, transaction or coverage guarantee.
    """
    if type(limits) is not Limits or any(
        type(n) is not int or n < 1
        for n in (limits.max_depth, limits.max_nodes, limits.max_string_length,
                  limits.max_mapping_entries)
    ):
        raise PlanError("limits must be positive integers")
    # Keep recursion bounded even if an untrusted caller supplies huge limits.
    if limits.max_depth > 128:
        raise PlanError("maximum supported depth is 128")
    if type(mapping) is not dict or len(mapping) > limits.max_mapping_entries:
        raise PlanError("invalid or oversized mapping")
    targets: set[str] = set()
    for old, new in mapping.items():
        if type(old) is not str or type(new) is not str:
            raise PlanError("entity IDs must be plain strings")
        if (len(old) > 255 or len(new) > 255 or not _ENTITY.fullmatch(old)
                or not _ENTITY.fullmatch(new)):
            raise PlanError("invalid entity ID")
        if old.split('.')[0] != new.split('.')[0]:
            raise PlanError("entity domain cannot change")
        if new in targets:
            raise PlanError("duplicate mapping target")
        targets.add(new)
    if type(context) is not Context or type(context.consumer) is not Consumer:
        raise PlanError("typed consumer context required")
    if type(context.scopes) is not tuple or len(context.scopes) > limits.max_mapping_entries:
        raise PlanError("scopes must be a tuple")
    scopes: dict[Path, Syntax] = {}
    for scope in context.scopes:
        if type(scope) is not Scope or type(scope.syntax) is not Syntax:
            raise PlanError("typed scope required")
        if type(scope.path) is not tuple or any(type(p) not in (str, int) for p in scope.path):
            raise PlanError("invalid scope path")
        if context.consumer is Consumer.ENERGY and scope.syntax is Syntax.JINJA:
            raise PlanError("energy adapter supports entity reference trees only")
        if len(scope.path) > limits.max_depth:
            raise PlanError("scope depth limit exceeded")
        for previous in scopes:
            if scope.path[:len(previous)] == previous or previous[:len(scope.path)] == scope.path:
                raise PlanError("duplicate or overlapping scopes")
        scopes[scope.path] = scope.syntax

    changes: list[Change] = []
    markers: list[Marker] = []
    visited_scopes: set[Path] = set()
    active: set[int] = set()
    nodes = 0

    def mentions_source(text: str) -> bool:
        return any(match.group(1) in mapping for match in _TOKEN.finditer(text))

    def template(text: str, path: Path) -> str:
        if _inert_template(text, mapping):
            return text
        if _EXTERNAL.search(text) and not _external_output_only(text):
            markers.append(Marker(path, "external_content", "URL/script-bearing field left unchanged"))
            return text
        if not _safe_comments(text, mapping) or re.search(r"{%[-+]?\s*raw\b", text):
            markers.append(Marker(path, "unsupported_template", "Comment/raw-bearing template left unchanged"))
            return text
        result, review = _syntax_template(text, mapping, limits.max_nodes)
        markers.extend(Marker(path, code, detail) for code, detail in review)
        return result

    def string(text: str, path: Path, syntax: Syntax | None, location: str) -> str:
        if len(text) > limits.max_string_length:
            raise PlanError("string length limit exceeded")
        if syntax is Syntax.ENTITY_TREE:
            result = mapping.get(text, text)
            if result == text and text not in mapping and mentions_source(text):
                markers.append(Marker(path, "unsupported_reference", "Embedded text in entity reference tree left unchanged"))
        elif syntax is Syntax.JINJA and location == "value":
            result = template(text, path)
        else:
            result = text
        if result != text:
            changes.append(Change(path, location, text, result))
        return result

    def walk(item: Any, path: Path, syntax: Syntax | None) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > limits.max_nodes or len(path) > limits.max_depth:
            raise PlanError("tree resource limit exceeded")
        if path in scopes:
            visited_scopes.add(path)
            syntax = scopes[path]
        kind = type(item)
        if kind is str:
            return string(item, path, syntax, "value")
        if item is None or kind in (int, float, bool):
            return item
        if kind not in (dict, list):
            raise PlanError("unsupported value type")
        identity = id(item)
        if identity in active:
            raise PlanError("cyclic input")
        active.add(identity)
        try:
            if kind is list:
                return [walk(child, path + (index,), syntax) for index, child in enumerate(item)]
            result = {}
            for key, child in item.items():
                nodes += 1
                if nodes > limits.max_nodes or type(key) not in (str, int):
                    raise PlanError("key type or resource limit exceeded")
                child_path = path + (key,)
                new_key = string(key, child_path, syntax, "key") if type(key) is str else key
                if new_key in result:
                    raise PlanError("rewritten dictionary key collision")
                result[new_key] = walk(child, child_path, syntax)
            return result
        finally:
            active.remove(identity)

    output = walk(value, (), None)
    if visited_scopes != set(scopes):
        raise PlanError("scope path does not exist")
    if not scopes:
        markers.append(Marker((), "no_supported_scopes", "No reference scopes selected"))
    return Plan(output, tuple(changes), tuple(markers))
