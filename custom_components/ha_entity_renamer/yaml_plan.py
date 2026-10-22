"""Read-only YAML source planner. Bytes never leave the caller; no apply API.

Coverage is the declared YAML include graph, with conservative static reference
fields. HA validation, activation and external consumers are separate adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import ScalarToken


class YAMLPlanError(ValueError):
    """Graph, source or safety error: no partial plan is returned."""


@dataclass(frozen=True)
class Limits:
    max_files: int = 512
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 16_000_000
    max_depth: int = 48
    max_nodes: int = 100_000
    max_directory_entries: int = 10_000


@dataclass(frozen=True)
class Issue:
    path: str
    line: int
    code: str


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    sha256: str
    size: int
    device: int
    inode: int


@dataclass(frozen=True)
class DirectorySnapshot:
    path: str
    # All discovered entries (including ignored files) detect graph membership changes.
    entries: tuple[tuple[str, int, int, int], ...]


@dataclass(frozen=True)
class IncludeEdge:
    source: str
    line: int
    tag: str
    target: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class Edit:
    start: int  # byte offsets, not Python character positions
    end: int
    before: bytes
    after: bytes


@dataclass(frozen=True)
class FilePatch:
    path: str
    expected_sha256: str
    edits: tuple[Edit, ...]
    proposed_bytes: bytes


@dataclass(frozen=True)
class Plan:
    files: tuple[FileSnapshot, ...]
    directories: tuple[DirectorySnapshot, ...]
    edges: tuple[IncludeEdge, ...]
    patches: tuple[FilePatch, ...]
    issues: tuple[Issue, ...]
    coverage: str = "declared_yaml_graph_static_reference_fields_only"

    @property
    def ready(self) -> bool:
        return not self.issues


_ENTITY = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+\Z", re.ASCII)
_TOKEN = re.compile(r"(?<![\w.])([a-z_][a-z0-9_]*\.[a-z0-9_]+)(?![\w.])", re.ASCII)
_FIELDS = frozenset({"entity", "entity_id", "entities",
                     "statistic_id", "statistic_ids", "power_sensor_id", "energy_sensor_id"})
_INCLUDE = frozenset({"!include", "!include_dir_list", "!include_dir_named",
                      "!include_dir_merge_list", "!include_dir_merge_named"})
_STR = "tag:yaml.org,2002:str"
_STANDARD = "tag:yaml.org,2002:"


def _relative(value: str) -> str:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        raise YAMLPlanError("invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(x in ("..", ".") for x in value.split("/")):
        raise YAMLPlanError("path traversal or absolute path")
    return str(path)


def _mapping(mapping: dict[str, str]) -> dict[str, str]:
    if type(mapping) is not dict or len(mapping) > 10_000:
        raise YAMLPlanError("invalid mapping")
    targets = set()
    for old, new in mapping.items():
        if (type(old) is not str or type(new) is not str or len(old) > 255 or len(new) > 255
                or not _ENTITY.fullmatch(old) or not _ENTITY.fullmatch(new)
                or old.split('.')[0] != new.split('.')[0] or new in targets):
            raise YAMLPlanError("invalid ID, domain change or duplicate target")
        targets.add(new)
    if any(new in mapping and new != old for old, new in mapping.items()):
        raise YAMLPlanError("occupied mapping target requires transaction-level resolution")
    return dict(mapping)


class _Reader:
    """Anchor all opens at a held directory fd; reject every symlink component."""
    def __init__(self, root: str | Path, limits: Limits):
        self.root = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.limits = limits
        self.directories: dict[str, DirectorySnapshot] = {}
        self.directory_entries = 0

    def open(self, relative: str, directory: bool = False) -> int:
        pieces = _relative(relative).split('/')
        fd = os.dup(self.root)
        try:
            for i, piece in enumerate(pieces):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if i < len(pieces) - 1 or directory:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(piece, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            mode = os.fstat(fd).st_mode
            if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
                raise YAMLPlanError("not a regular file/directory")
            return fd
        except Exception:
            os.close(fd)
            raise

    def read(self, path: str) -> tuple[bytes, os.stat_result]:
        fd = self.open(path)
        try:
            before = os.fstat(fd)
            if before.st_size > self.limits.max_file_bytes:
                raise YAMLPlanError("file byte limit")
            chunks = []
            size = 0
            while chunk := os.read(fd, min(65536, self.limits.max_file_bytes + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > self.limits.max_file_bytes:
                    raise YAMLPlanError("file byte limit")
            after = os.fstat(fd)
            if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise YAMLPlanError("file changed during read")
            return b''.join(chunks), before
        finally:
            os.close(fd)

    def listing(self, directory: str, depth: int = 0) -> tuple[str, ...]:
        if depth > self.limits.max_depth:
            raise YAMLPlanError("directory depth limit")
        fd = self.open(directory, directory=True)
        try:
            records = []
            files = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    self.directory_entries += 1
                    if self.directory_entries > self.limits.max_directory_entries:
                        raise YAMLPlanError("directory entry limit")
                    info = entry.stat(follow_symlinks=False)
                    records.append((entry.name, info.st_mode, info.st_dev, info.st_ino))
                    # Reject even ignored symlinks to avoid silently incomplete discovery.
                    if stat.S_ISLNK(info.st_mode):
                        raise YAMLPlanError("symlink in include directory")
                    if entry.name.startswith('.'):
                        continue
                    child = str(PurePosixPath(directory) / entry.name)
                    if stat.S_ISDIR(info.st_mode):
                        files.extend(self.listing(child, depth + 1))
                    elif entry.name.endswith('.yaml'):
                        if not stat.S_ISREG(info.st_mode):
                            raise YAMLPlanError("nonregular YAML include")
                        files.append(child)
            self.directories[directory] = DirectorySnapshot(directory, tuple(sorted(records)))
            return tuple(sorted(files))
        finally:
            os.close(fd)


def plan_yaml(
    config_root: str | Path,
    mapping: dict[str, str],
    *,
    entrypoints: tuple[str, ...] = ("configuration.yaml",),
    yaml_dashboards: tuple[str, ...] = (),
    limits: Limits = Limits(),
) -> Plan:
    """Plan source edits without writing, secret resolution, or HA imports.

    Caller supplies runtime-known YAML dashboards not declared in configuration.
    Missing/unsafe includes fail closed. Issues make ``ready`` false; patches in
    such a plan are preview only and MUST NOT be applied independently.
    """
    if type(limits) is not Limits or any(type(x) is not int or x < 1 for x in vars(limits).values()):
        raise YAMLPlanError("positive integer limits required")
    if limits.max_depth > 100:
        raise YAMLPlanError("maximum supported depth is 100")
    if type(entrypoints) is not tuple or type(yaml_dashboards) is not tuple:
        raise YAMLPlanError("entrypoints must be tuples")
    mapping = _mapping(mapping)
    reader = None
    try:
        reader = _Reader(config_root, limits)
        return _plan(reader, mapping, entrypoints + yaml_dashboards, limits)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        # Do not leak YAML content or secret values through parser exception text.
        raise YAMLPlanError("unreadable, unsafe or invalid YAML graph") from None
    finally:
        if reader is not None:
            os.close(reader.root)


def _plan(reader: _Reader, mapping: dict[str, str], roots: tuple[str, ...], limits: Limits) -> Plan:
    files: dict[str, FileSnapshot] = {}
    patches = []
    edges = []
    issues: set[Issue] = set()
    active: set[str] = set()
    contexts_seen = {}
    total_bytes = 0
    nodes_count = 0

    def visit(path: str, graph_depth: int = 0, inherited_field=False, inherited_key="", inherited_lovelace=False) -> None:
        nonlocal total_bytes, nodes_count
        path = _relative(path)
        if PurePosixPath(path).name == "secrets.yaml":
            raise YAMLPlanError("secret stores are outside source planning scope")
        if graph_depth > limits.max_depth:
            raise YAMLPlanError("include graph depth limit")
        if path in active:
            raise YAMLPlanError("include graph cycle")
        context = (inherited_field, inherited_key, inherited_lovelace)
        if path in files:
            if contexts_seen[path] != context:
                raise YAMLPlanError("same include has different semantic contexts")
            return
        contexts_seen[path] = context
        if len(files) >= limits.max_files:
            raise YAMLPlanError("file count limit")
        data, info = reader.read(path)
        total_bytes += len(data)
        if total_bytes > limits.max_total_bytes:
            raise YAMLPlanError("graph byte limit")
        text = data.decode('utf-8')
        # Compose first: no constructors and therefore no !secret resolution.
        tree = yaml.compose(text, Loader=yaml.SafeLoader)
        tokens = {t.end_mark.index: t for t in yaml.scan(text, Loader=yaml.SafeLoader)
                  if isinstance(t, ScalarToken)}
        files[path] = FileSnapshot(path, hashlib.sha256(data).hexdigest(), len(data), info.st_dev, info.st_ino)
        active.add(path)
        candidates: dict[int, tuple[ScalarNode, list[bool]]] = {}
        visiting: set[int] = set()
        includes_seen: set[int] = set()
        dashboard_paths = set()

        def issue(node, code):
            issues.add(Issue(path, node.start_mark.line + 1, code))

        def walk(node, depth=0, field=False, parent_key="", lovelace=False):
            nonlocal nodes_count
            if node is None:
                return
            nodes_count += 1
            if nodes_count > limits.max_nodes or depth > limits.max_depth:
                raise YAMLPlanError("YAML node/depth limit")
            if id(node) in visiting:
                raise YAMLPlanError("YAML alias cycle")
            visiting.add(id(node))
            try:
                if isinstance(node, ScalarNode):
                    if node.tag in _INCLUDE:
                        if id(node) in includes_seen:
                            return
                        includes_seen.add(id(node))
                        target = str(PurePosixPath(path).parent / _relative(node.value))
                        targets = (target,) if node.tag == '!include' else reader.listing(target)
                        edges.append(IncludeEdge(path, node.start_mark.line + 1, node.tag, target, targets))
                        for child in targets:
                            visit(child, graph_depth + 1, field, parent_key, lovelace)
                    elif node.tag == '!secret':
                        issue(node, "secret_reference_unresolved")
                    elif not node.tag.startswith(_STANDARD):
                        issue(node, "unsupported_yaml_tag")
                    elif node.tag == _STR:
                        candidates.setdefault(id(node), (node, []))[1].append(field)
                    return
                if not node.tag.startswith(_STANDARD):
                    issue(node, "unsupported_yaml_tag")
                if isinstance(node, SequenceNode):
                    for child in node.value:
                        walk(child, depth + 1, field, parent_key, lovelace)
                elif isinstance(node, MappingNode):
                    # Preserve ambiguous duplicate keys, but block any apply.
                    raw_keys = [key.value for key, _ in node.value if isinstance(key, ScalarNode)]
                    if len(raw_keys) != len(set(raw_keys)):
                        issue(node, "duplicate_mapping_keys")
                    projected = [mapping.get(k, k) if parent_key == 'entities' else k for k in raw_keys]
                    if len(projected) != len(set(projected)):
                        issue(node, "entity_key_collision")
                    values = {k.value: v for k, v in node.value if isinstance(k, ScalarNode)}
                    card_type = values.get('type')
                    if isinstance(card_type, ScalarNode) and card_type.value.startswith('custom:'):
                        issue(card_type, 'custom_card_semantics_unknown')
                    mode = values.get('mode')
                    filename = values.get('filename')
                    if (lovelace and isinstance(mode, ScalarNode) and mode.value == 'yaml'
                            and isinstance(filename, ScalarNode) and filename.tag == _STR):
                        dashboard_paths.add(_relative(filename.value))
                    if (parent_key == "lovelace" and isinstance(mode, ScalarNode)
                            and mode.value == "yaml" and filename is None):
                        dashboard_paths.add("ui-lovelace.yaml")
                    for key, value in node.value:
                        if not isinstance(key, ScalarNode) or key.tag != _STR:
                            # YAML merge and complex keys need semantic provenance.
                            issue(key, "unsupported_mapping_key")
                            walk(key, depth + 1)
                            walk(value, depth + 1)
                            continue
                        walk(key, depth + 1, parent_key == 'entities')
                        walk(value, depth + 1, key.value in _FIELDS, key.value,
                             lovelace or key.value == 'lovelace')
                else:
                    issue(node, "unsupported_yaml_node")
            finally:
                visiting.remove(id(node))

        walk(tree, field=inherited_field, parent_key=inherited_key, lovelace=inherited_lovelace)
        for dashboard in sorted(dashboard_paths):
            edges.append(IncludeEdge(path, 0, "lovelace_yaml_dashboard", dashboard, (dashboard,)))
            visit(dashboard, graph_depth + 1)
        edits = []
        for node, contexts in candidates.values():
            value = node.value
            if any(x in value for x in ('{{', '{%', '{#', '[[[')):
                issue(node, "template_requires_reference_parser")
                continue
            mentioned = any(m.group(1) in mapping for m in _TOKEN.finditer(value))
            if not mentioned:
                continue
            if not all(contexts):
                # Text and identity strings are not silently rewritten.
                if any(contexts) or value in mapping:
                    issue(node, "ambiguous_reference_context")
                continue
            # Only exact IDs or comma-separated ID lists are supported here.
            parts = [p.strip() for p in value.split(',')]
            if not parts or not all(_ENTITY.fullmatch(p) for p in parts):
                issue(node, "unsupported_reference_expression")
                continue
            token = tokens.get(node.end_mark.index)
            if token is None or token.value != value:
                issue(node, "scalar_source_span_unknown")
                continue
            raw = text[token.start_mark.index:token.end_mark.index]
            content = raw[1:-1] if token.style in ("'", '"') else raw
            if token.style in ('|', '>') or content != value:
                issue(node, "escaped_or_folded_scalar_requires_span_mapper")
                continue
            replaced = _TOKEN.sub(lambda m: mapping.get(m.group(1), m.group(1)), raw)
            if replaced != raw:
                start = len(text[:token.start_mark.index].encode('utf-8'))
                end = len(text[:token.end_mark.index].encode('utf-8'))
                edits.append(Edit(start, end, raw.encode('utf-8'), replaced.encode('utf-8')))
        edits.sort(key=lambda e: e.start)
        proposed = data
        last = len(data) + 1
        for edit in reversed(edits):
            if edit.end > last or data[edit.start:edit.end] != edit.before:
                raise YAMLPlanError("overlapping or inconsistent scalar spans")
            proposed = proposed[:edit.start] + edit.after + proposed[edit.end:]
            last = edit.start
        if edits:
            # Independent parse catches quoting/syntax regressions after patching.
            yaml.compose(proposed.decode('utf-8'), Loader=yaml.SafeLoader)
            patches.append(FilePatch(path, files[path].sha256, tuple(edits), proposed))
        active.remove(path)

    for root in roots:
        visit(root)
    return Plan(tuple(files[k] for k in sorted(files)),
                tuple(reader.directories[k] for k in sorted(reader.directories)),
                tuple(edges), tuple(sorted(patches, key=lambda p: p.path)),
                tuple(sorted(issues, key=lambda i: (i.path, i.line, i.code))))
