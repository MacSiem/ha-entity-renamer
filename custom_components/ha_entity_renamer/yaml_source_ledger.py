"""Pure ORIGINAL source inventory; no native load, filesystem access or authority.

Pass the exact Plan and ORIGINAL byte bundle captured for native staging. Plan
has no AST, so composing these verified bytes is necessary; include discovery is
NEVER repeated. This ledger does not establish native validation/runtime binding.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import PurePosixPath
from collections.abc import Mapping

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from .yaml_plan import Plan, Limits

_STR = 'tag:yaml.org,2002:str'
_MAP = 'tag:yaml.org,2002:map'
_SEQ = 'tag:yaml.org,2002:seq'
_INCLUDES = frozenset({'!include', '!include_dir_list', '!include_dir_named',
                       '!include_dir_merge_list', '!include_dir_merge_named'})


class SourceLedgerError(ValueError):
    """Sanitized invalid graph/bundle, never parser text or config values."""


@dataclass(frozen=True)
class IncludeHop:
    edge: int
    # Physical occurrence of the include (aliases can reuse line numbers).
    route: tuple[str, ...]
    file: str


@dataclass(frozen=True)
class Origin:
    path: str
    # Mapping pair ordinals preserve duplicate keys: k:N / v:N; lists use i:N.
    route: tuple[str, ...]
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    includes: tuple[IncludeHop, ...]


@dataclass(frozen=True)
class Finding:
    code: str
    origin: Origin


@dataclass(frozen=True)
class Definition:
    domain: str
    native_key: str | None
    section: str
    package: str | None
    origin: Origin
    key_origin: Origin
    # Hash of physical AST, before constructors/secret expansion; NOT raw_config.
    source_ast_sha256: str
    disposition: str  # candidate | duplicate | unsupported


@dataclass(frozen=True)
class SourceLedger:
    schema: int
    original_graph_sha256: str
    definitions: tuple[Definition, ...]
    findings: tuple[Finding, ...]
    sha256: str

    def keys_for_native_projection(self, domain: str) -> tuple[str, ...]:
        """Require complete unambiguous inventory before native row matching.

        A candidate means only a physically inventoried source definition. Native
        schema acceptance and exact projection hashes remain the checker's job.
        """
        if domain not in ('automation', 'script') or self.findings:
            raise SourceLedgerError('source_inventory_unknown')
        rows = [row for row in self.definitions if row.domain == domain]
        if any(row.disposition != 'candidate' or row.native_key is None for row in rows):
            raise SourceLedgerError('source_inventory_unknown')
        return tuple(sorted(row.native_key for row in rows))

    def match_native_keys(self, domain: str, keys: tuple[str, ...]) -> None:
        """Call on ORIGINAL native projection rows BEFORE hashing/exporting them."""
        expected = self.keys_for_native_projection(domain)
        if (type(keys) is not tuple or any(type(k) is not str for k in keys)
                or len(set(keys)) != len(keys) or tuple(sorted(keys)) != expected):
            raise SourceLedgerError('native_source_inventory_mismatch')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def source_graph_digest(plan: Plan) -> str:
    """Same immutable graph binding for extraction and its native consumer."""
    return _hash({'root': 'configuration.yaml', 'files': [asdict(x) for x in plan.files],
                  'directories': [asdict(x) for x in plan.directories],
                  'edges': [asdict(x) for x in plan.edges]})


@dataclass(frozen=True)
class _Ref:
    path: str
    node: object
    route: tuple[str, ...] = ()
    includes: tuple[IncludeHop, ...] = ()

    def origin(self):
        return Origin(self.path, self.route, self.node.start_mark.line + 1,
                      self.node.start_mark.column + 1, self.node.end_mark.line + 1,
                      self.node.end_mark.column + 1, self.includes)

    def child(self, node, step):
        return _Ref(self.path, node, self.route + (step,), self.includes)


def extract_source_ledger(plan: Plan, original: Mapping[str, bytes], *,
                          limits: Limits = Limits()) -> SourceLedger:
    """Inventory configuration.yaml reachability using the existing frozen graph.

    Extra Plan roots (e.g. dashboards) remain in graph digest but are not treated
    as independent native configuration roots. All Plan files must be supplied
    with exact ORIGINAL hashes/sizes. No proposed bytes are accepted implicitly.
    Include dir order is never interpreted as native precedence: retain all rows
    and reject collisions. Unsupported shapes yield findings and prevent binding.
    """
    if not isinstance(plan, Plan) or not isinstance(original, Mapping):
        raise SourceLedgerError('source_bundle_invalid')
    snapshots = {row.path: row for row in plan.files}
    if len(snapshots) != len(plan.files) or set(original) != set(snapshots):
        raise SourceLedgerError('source_bundle_membership')
    if 'configuration.yaml' not in snapshots:
        raise SourceLedgerError('source_root_missing')
    trees = {}
    total = 0
    try:
        for path, snap in snapshots.items():
            raw = original[path]
            if type(raw) is not bytes or len(raw) != snap.size or hashlib.sha256(raw).hexdigest() != snap.sha256:
                raise SourceLedgerError('source_bundle_changed')
            total += len(raw)
            if len(raw) > limits.max_file_bytes or total > limits.max_total_bytes:
                raise SourceLedgerError('source_inventory_limit')
            trees[path] = yaml.compose(raw.decode('utf-8'), Loader=yaml.SafeLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError):
        raise SourceLedgerError('source_bundle_parse') from None
    if len(trees) > limits.max_files:
        raise SourceLedgerError('source_inventory_limit')
    graph_hash = source_graph_digest(plan)
    findings, definitions = [], []
    work = 0

    def charge(depth):
        nonlocal work
        work += 1
        if work > limits.max_nodes or depth > limits.max_depth:
            raise SourceLedgerError('source_inventory_limit')

    def flag(ref, code):
        findings.append(Finding(code, ref.origin()))

    def targets(ref):
        node = ref.node
        target = str(PurePosixPath(ref.path).parent / node.value)
        matching = [(i, edge) for i, edge in enumerate(plan.edges)
                    if (edge.source, edge.line, edge.tag, edge.target) ==
                    (ref.path, node.start_mark.line + 1, node.tag, target)]
        if len(matching) != 1:
            raise SourceLedgerError('source_include_edge_mismatch')
        index, edge = matching[0]
        if node.tag == '!include' and edge.files != (target,):
            raise SourceLedgerError('source_include_edge_mismatch')
        result = []
        for path in edge.files:
            if path not in trees or trees[path] is None:
                raise SourceLedgerError('source_include_membership')
            result.append(_Ref(path, trees[path], (), ref.includes +
                               (IncludeHop(index, ref.route, path),)))
        return result

    def expand(ref, kind, depth=0, active=()):
        """Yield virtual container children without dict conversion/shadowing."""
        charge(depth)
        token = (ref.path, id(ref.node))
        if token in active:
            flag(ref, 'source_alias_or_include_cycle')
            return
        active += (token,)
        node = ref.node
        if isinstance(node, ScalarNode) and node.tag in _INCLUDES:
            children = targets(ref)
            if node.tag == '!include':
                yield from expand(children[0], kind, depth + 1, active)
            elif kind == 'map' and node.tag == '!include_dir_named':
                for child in children:
                    yield PurePosixPath(child.path).stem, child, child
            elif kind == 'map' and node.tag == '!include_dir_merge_named':
                for child in children:
                    yield from expand(child, kind, depth + 1, active)
            elif kind == 'seq' and node.tag == '!include_dir_list':
                yield from children
            elif kind == 'seq' and node.tag == '!include_dir_merge_list':
                for child in children:
                    yield from expand(child, kind, depth + 1, active)
            else:
                flag(ref, 'source_include_layout_unsupported')
        elif kind == 'map' and isinstance(node, MappingNode) and node.tag == _MAP:
            for i, (key, child) in enumerate(node.value):
                keyref = ref.child(key, f'k:{i}')
                if not isinstance(key, ScalarNode) or key.tag != _STR:
                    flag(keyref, 'source_mapping_key_or_merge_unsupported')
                    continue
                yield key.value, ref.child(child, f'v:{i}'), keyref
        elif kind == 'seq' and isinstance(node, SequenceNode) and node.tag == _SEQ:
            for i, child in enumerate(node.value):
                yield ref.child(child, f'i:{i}')
        else:
            flag(ref, 'source_container_layout_unsupported')

    def pairs(ref):
        rows = list(expand(ref, 'map'))
        seen = {}
        for key, child, keyref in rows:
            if key in seen:
                flag(keyref, 'source_duplicate_mapping_key')
                flag(seen[key], 'source_duplicate_mapping_key')
            seen[key] = keyref
        return rows

    def body(ref, active=(), depth=0):
        """Fingerprint typed physical AST; preserve pairs and unresolved tags."""
        charge(depth)
        node = ref.node
        token = (ref.path, id(node))
        if token in active:
            flag(ref, 'source_alias_cycle_unsupported')
            return ['cycle']
        active += (token,)
        if isinstance(node, ScalarNode):
            if node.tag in _INCLUDES:
                if node.tag != '!include':
                    # Container semantics inside action/variables bodies are not
                    # reconstructed by this inventory. Never silently bless
                    # collisions/dropped fragments hidden in these includes.
                    flag(ref, 'source_body_directory_include_unsupported')
                return [node.tag, [body(child, active, depth + 1) for child in targets(ref)]]
            if node.tag == '!env_var' or (not node.tag.startswith('tag:yaml.org,2002:') and node.tag != '!secret'):
                flag(ref, 'source_body_tag_unsupported')
            return [node.tag, node.value]
        if isinstance(node, MappingNode):
            pairs(ref)  # Detect duplicate/merge keys in every nested body map.
            return [node.tag, [[body(ref.child(key, f'k:{i}'), active, depth + 1),
                                body(ref.child(child, f'v:{i}'), active, depth + 1)]
                               for i, (key, child) in enumerate(node.value)]]
        if isinstance(node, SequenceNode):
            return [node.tag, [body(ref.child(child, f'i:{i}'), active, depth + 1)
                               for i, child in enumerate(node.value)]]
        flag(ref, 'source_body_layout_unsupported')
        return ['unknown']

    def mapping_ref(ref):
        """Resolve only single !include wrappers for one definition body."""
        for _ in range(limits.max_depth + 1):
            if isinstance(ref.node, ScalarNode) and ref.node.tag == '!include':
                ref = targets(ref)[0]
            else:
                return ref
        raise SourceLedgerError('source_inventory_limit')

    def definition(domain, key, ref, keyref, section, package):
        ref = mapping_ref(ref)
        start = len(findings)
        node = ref.node
        if not isinstance(node, MappingNode) or node.tag != _MAP:
            flag(ref, 'source_definition_layout_unsupported')
        else:
            members = pairs(ref)
            if any(name == 'use_blueprint' for name, _, _ in members):
                flag(ref, 'source_blueprint_unsupported')
            if domain == 'automation':
                ids = [(child, origin) for name, child, origin in members if name == 'id']
                if len(ids) == 1 and isinstance(ids[0][0].node, ScalarNode) and ids[0][0].node.tag == _STR and ids[0][0].node.value:
                    key, keyref = ids[0][0].node.value, ids[0][1]
                else:
                    flag(ref, 'source_automation_stable_id_missing_or_ambiguous')
            if not isinstance(key, str) or not key:
                flag(ref, 'source_stable_key_missing')
        digest = _hash(body(ref))
        definitions.append(Definition(domain, key, section, package, ref.origin(),
                                      keyref.origin(), digest,
                                      'unsupported' if len(findings) != start else 'candidate'))

    def sections(ref, package=None):
        for section, value, keyref in pairs(ref):
            domain = section.split(' ', 1)[0]
            if domain == 'automation':
                for item in expand(value, 'seq'):
                    definition(domain, None, item, keyref, section, package)
            elif domain == 'script':
                for key, item, origin in pairs(value):
                    definition(domain, key, item, origin, section, package)
            elif section == 'homeassistant' and package is None:
                for name, value, _ in pairs(value):
                    if name == 'packages':
                        for name, value, _ in pairs(value):
                            sections(value, name)
            elif section == 'homeassistant' and package is not None:
                flag(value, 'source_nested_package_homeassistant_unsupported')

    root = trees['configuration.yaml']
    if root is None:
        raise SourceLedgerError('source_root_empty')
    sections(_Ref('configuration.yaml', root))
    groups = {}
    for i, row in enumerate(definitions):
        if row.native_key is not None:
            groups.setdefault((row.domain, row.native_key), []).append(i)
    for indexes in groups.values():
        if len(indexes) > 1:
            for i in indexes:
                row = definitions[i]
                definitions[i] = replace(row, disposition='duplicate')
                findings.append(Finding('source_duplicate_native_key', row.key_origin))
    # Stable encounter order is physical source order / frozen edge order. It
    # conveys no guessed native include-dir precedence.
    payload = {'schema': 1, 'original_graph_sha256': graph_hash,
               'definitions': [asdict(row) for row in definitions],
               'findings': [asdict(row) for row in findings]}
    return SourceLedger(1, graph_hash, tuple(definitions), tuple(findings), _hash(payload))
