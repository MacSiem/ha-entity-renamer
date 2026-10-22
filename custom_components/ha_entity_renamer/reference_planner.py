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


def _syntax_template(text, mapping, max_nodes, *, initial_bindings=None,
                     binding_report=None, output_reference=False):
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
        unsupported = (nodes.Macro, nodes.Import, nodes.FromImport, nodes.CallBlock,
                       nodes.Include, nodes.Extends,
                       nodes.Block, nodes.FilterBlock)
        for node in all_nodes:
            if isinstance(node, unsupported):
                return original_text, [('unsupported_template_scope', 'Unsupported lexical template scope')]
            if isinstance(node, nodes.NSRef) and (node.name in globals_ or node.attr in globals_):
                return original_text, [('unsupported_template_scope', 'HA global namespace binding')]
            if isinstance(node, nodes.Name) and node.ctx in ('store', 'param') and node.name in globals_:
                return original_text, [('unsupported_template_scope', 'HA global shadowed by local binding')]

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

        # A value is (shape, immutable origins); origin = (start, end, entity).
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
            if (isinstance(node, nodes.Call) and isinstance(node.node, nodes.Getattr)
                    and node.node.attr in ('keys', 'items')
                    and not (node.args or node.kwargs or node.dyn_args or node.dyn_kwargs)):
                bound = value(node.node.node, bindings)
                if bound and bound[0] == 'keys':
                    return ('list' if node.node.attr == 'keys' else 'items', bound[1])
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

        def expression(node, bindings):
            charge()
            # HA creates this local object for every repeat iteration. Counters
            # and flags carry no entity provenance; item/dynamic access may.
            if isinstance(node, (nodes.Getattr, nodes.Getitem)) and isinstance(node.node, nodes.Name):
                bound = bindings.get(node.node.name)
                if bound and bound[0] == 'runtime_repeat':
                    attribute = (node.attr if isinstance(node, nodes.Getattr) else
                                 node.arg.value if isinstance(node.arg, nodes.Const)
                                 and type(node.arg.value) is str else None)
                    if attribute not in {'index', 'first', 'last'}:
                        mark('unsupported_runtime_repeat', 'Unproven runtime repeat item or access')
                    return
            if isinstance(node, nodes.Name):
                bound = bindings.get(node.name)
                if bound and bound[0] == 'runtime_repeat':
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
                if (isinstance(node, nodes.Output) and output_reference
                        and len(tree.body) == 1 and tree.body[0] is node):
                    # An entity field may return a native scalar/list. Surrounding
                    # non-whitespace output or concatenation is not a reference.
                    expressions = [child for child in node.nodes
                                   if not (isinstance(child, nodes.TemplateData)
                                           and not child.data.strip())]
                    bound = value(expressions[0], bindings) if len(expressions) == 1 else None
                    if bound and bound[0] in ('scalar', 'list'):
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
                    supported = (bound is not None and bound[0] in ('list', 'keys')
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
                        local[node.target.name] = ('scalar', bound[1])
                        # previtem/nextitem expose iterable values too. Treat all
                        # loop metadata uses conservatively as origin escapes.
                        local['loop'] = ('loop', bound[1])
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


def analyze_template_bindings(text, mapping, bindings, *, max_nodes=10000,
                              output_reference=False):
    """Parse Jinja with adapter-owned origins, without evaluating any expression.

    Bindings use the existing (shape, origins) bounded dataflow representation.
    External origins must have a negative start, so they can never be edited as
    template source. Missing parser/unsupported scope retains every origin as an
    unsafe consumer. The adapter owns source location and scope validation.
    """
    origins = {origin for bound in bindings.values() if bound for origin in bound[1]}
    if any(origin[0] >= 0 for origin in origins):
        raise PlanError('external binding origins must have negative positions')
    if set(bindings) & {'states', 'is_state', 'state_attr', 'is_state_attr', 'expand'}:
        return text, [('unsupported_template_scope', 'YAML binding shadows HA global')], set(), origins
    if _EXTERNAL.search(text) or '{#' in text or re.search(r'{%[-+]?\s*raw\b', text):
        return text, [('unsupported_template', 'External/comment/raw-bearing template')], set(), origins
    report = {}
    value, markers = _syntax_template(
        text, mapping, max_nodes, initial_bindings=bindings,
        binding_report=report, output_reference=output_reference)
    used = report.get('used', set()) & origins
    unsafe = report.get('unsafe', set()) & origins
    if not report.get('complete') or markers:
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
        if _EXTERNAL.search(text):
            markers.append(Marker(path, "external_content", "URL/script-bearing field left unchanged"))
            return text
        if "{#" in text or re.search(r"{%[-+]?\s*raw\b", text):
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
