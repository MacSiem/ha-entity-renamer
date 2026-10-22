"""Pure, bounded reference planning. No HA/storage access and no writes.

Adapters MUST select reference-only subtrees; this is deliberately not a global
string replacement engine. A plan describes only its explicitly selected scopes.
"""
from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
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
_JINJA_OPEN = re.compile(r"{{|{%")
# Support only a complete first literal argument, never evaluate templates.
_CALL = re.compile(
    r"(?<![\w.])(?:states|is_state|state_attr|is_state_attr|expand)\s*\(\s*"
    r"(?P<quote>['\"])(?P<id>[a-z_][a-z0-9_]*\.[a-z0-9_]+)(?P=quote)(?=\s*[,\)])",
    re.ASCII,
)
_STATE = re.compile(
    r"(?<![\w.])states\s*\.\s*(?P<id>[a-z_][a-z0-9_]*\s*\.\s*[a-z0-9_]+)(?![\w])",
    re.ASCII,
)
_DYNAMIC = re.compile(r"\b(?:states|is_state|state_attr|is_state_attr|expand)\s*\(|\bstates\s*\[")
# A bounded scheme suffix also catches long schemes without quadratic retries
# against a very long alphabetic string that contains no colon.
_EXTERNAL = re.compile(r"(?:[a-z][a-z0-9+.-]{0,31}://|javascript:|<script\b)", re.I)
_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?=([a-z_][a-z0-9_]*\.[a-z0-9_]+)(?![A-Za-z0-9_]))", re.ASCII)


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
        if re.search(r"{%[-+]?\s*(?:set|for|macro|import|from|call|include|extends|with)\b", text):
            markers.append(Marker(path, "unsupported_template_scope", "Template bindings require syntax-aware resolution"))
            return text
        if _EXTERNAL.search(text):
            markers.append(Marker(path, "external_content", "URL/script-bearing field left unchanged"))
            return text
        if "{#" in text or re.search(r"{%[-+]?\s*raw\b", text):
            markers.append(Marker(path, "unsupported_template", "Comment/raw-bearing template left unchanged"))
            return text
        blocks: list[tuple[int, str]] = []
        cursor = 0
        while opening := _JINJA_OPEN.search(text, cursor):
            closing = "}}" if opening.group() == "{{" else "%}"
            end = opening.end()
            quote = None
            while end < len(text):
                if quote is not None:
                    if text[end] == "\\":
                        end += 2
                        continue
                    if text[end] == quote:
                        quote = None
                elif text.startswith(closing, end):
                    break
                elif text[end] in "'\"":
                    quote = text[end]
                end += 1
            if end >= len(text):
                markers.append(Marker(path, "unsupported_template", "Unterminated Jinja block left unchanged"))
                return text
            blocks.append((opening.start(), text[opening.start():end + 2]))
            cursor = end + 2
        if not blocks:
            if mentions_source(text):
                markers.append(Marker(path, "unsupported_reference", "Non-Jinja text left unchanged"))
            return text
        replacements: list[tuple[int, int, str]] = []
        for block_start, expression in blocks:
            spans: set[tuple[int, int]] = set()
            # Ignore text inside Jinja string literals except approved HA call
            # arguments. This prevents treating a string containing source code
            # as an executable states.sensor.foo reference.
            strings: list[tuple[int, int]] = []
            index = 0
            while index < len(expression):
                if expression[index] not in "'\"":
                    index += 1
                    continue
                start = index
                quote = expression[index]
                index += 1
                while index < len(expression):
                    if expression[index] == "\\":
                        index += 2
                    elif expression[index] == quote:
                        index += 1
                        break
                    else:
                        index += 1
                strings.append((start, index))
            string_starts = [a for a, _ in strings]
            for matcher in (_CALL, _STATE):
                for match in matcher.finditer(expression):
                    string_index = bisect_right(string_starts, match.start()) - 1
                    if string_index >= 0 and match.start() < strings[string_index][1]:
                        continue
                    start, end = match.span('id')
                    spans.add((start, end))
                    old = re.sub(r'\s+', '', match.group('id'))
                    if old in mapping:
                        replacements.append((block_start + start, block_start + end, mapping[old]))
            unresolved = False
            for match in _TOKEN.finditer(expression):
                if match.group(1) in mapping and match.span(1) not in spans:
                    unresolved = True
            # Dynamic accesses can reference sources without spelling a full ID.
            if re.search(r"\bstates\s*\[", expression) or any(
                _CALL.match(expression, m.start()) is None
                for m in _DYNAMIC.finditer(expression)
            ):
                markers.append(Marker(path, "dynamic_reference", "Dynamic/indirect template references need review"))
            if unresolved:
                markers.append(Marker(path, "unsupported_reference", "Unrecognized entity token left unchanged"))
        for start, end, replacement in sorted(set(replacements), reverse=True):
            text = text[:start] + replacement + text[end:]
        return text

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
