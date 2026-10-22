"""Read-only YAML source planner. Bytes never leave the caller; no apply API.

Coverage is the declared YAML include graph, with conservative static reference
fields. HA validation, activation and external consumers are separate adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import ScalarToken

from .reference_planner import (Consumer, Context, Scope, Syntax, plan_references,
                                analyze_template_bindings, Plan as ReferencePlan,
                                Change as ReferenceChange)


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
class VariableBindingProof:
    path: str
    source_line: int
    entity_id: str
    producer_occurrences: int
    yaml_occurrences: int
    accounted_occurrences: int
    consumer_counts: tuple[tuple[str, int], ...]
    approved: bool


@dataclass(frozen=True)
class Plan:
    files: tuple[FileSnapshot, ...]
    directories: tuple[DirectorySnapshot, ...]
    edges: tuple[IncludeEdge, ...]
    patches: tuple[FilePatch, ...]
    issues: tuple[Issue, ...]
    coverage: str = "declared_yaml_graph_static_reference_fields_only"
    variable_bindings: tuple[VariableBindingProof, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.issues


_ENTITY = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+\Z", re.ASCII)
_TOKEN = re.compile(r"(?<![\w.])([a-z_][a-z0-9_]*\.[a-z0-9_]+)(?![\w.])", re.ASCII)
_FIELDS = frozenset({"entity", "entity_id", "entity_ids", "entities",
                     "statistic_id", "statistic_ids", "power_sensor_id", "energy_sensor_id"})
_INCLUDE = frozenset({"!include", "!include_dir_list", "!include_dir_named",
                      "!include_dir_merge_list", "!include_dir_merge_named"})
_STR = "tag:yaml.org,2002:str"
_STANDARD = "tag:yaml.org,2002:"
_ENTITY_KEY_MAPS = frozenset({'entities', 'customize'})


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


def _yaml_bindings(tree, inherited_key, mapping, issue, max_nodes):
    """File-local, schema-selected HA variable producers and all their uses.

    Origin identity is the composed scalar node, shared by every YAML alias.
    Counts distinguish a producer occurrence from an unrelated alias consumer.
    Includes/merges across lexical scope boundaries remain explicitly unsupported.
    """
    producers, used, unsafe, templates, observed = {}, set(), set(), {}, {}
    consumers = {}
    work = 0

    def record(origin, kind):
        counts = consumers.setdefault(origin, {})
        counts[kind] = counts.get(kind, 0) + 1

    def charge():
        nonlocal work
        work += 1
        if work > max_nodes:
            raise YAMLPlanError('YAML binding work limit')

    def pairs(node):
        return {key.value: child for key, child in node.value
                if isinstance(key, ScalarNode) and key.tag == _STR}

    def origins(bindings):
        return {origin for bound in bindings.values() if bound for origin in bound[1]}

    def join(environments):
        """May-values, including undefined/dynamic paths, with bounded provenance.

        A scalar may have several origins: each path still produces one scalar.
        Missing names and incompatible shapes remain unknown, never an empty
        known binding. Do not discard origins when precision is lost.
        """
        names = set()
        for environment in environments:
            charge()
            names.update(environment)
        result = {}
        for name in names:
            values = [environment.get(name) for environment in environments]
            possible, shapes = set(), set()
            for bound in values:
                charge()
                shapes.add(bound[0] if bound else 'unknown')
                if bound:
                    for origin in bound[1]:
                        charge()
                        possible.add(origin)
            shape = next(iter(shapes)) if len(shapes) == 1 else 'unknown'
            result[name] = (shape, tuple(sorted(possible))) if possible else None
        return result

    def static(node):
        charge()
        if isinstance(node, ScalarNode) and node.tag == _STR and _ENTITY.fullmatch(node.value):
            origin = (-id(node), 0, node.value)
            return ('scalar', (origin,)), [node]
        if isinstance(node, SequenceNode) and node.tag == _STANDARD + 'seq':
            parts = [static(child) for child in node.value]
            if parts and all(part and part[0][0] == 'scalar' for part in parts):
                return ('list', tuple(sorted({o for part in parts for o in part[0][1]}))), [n for part in parts for n in part[1]]
        if isinstance(node, MappingNode) and node.tag == _STANDARD + 'map':
            # Only keys are exposed by direct map iteration. Values are labels/
            # metadata, never entity producers just because their spelling fits.
            keys = [static(key) for key, _ in node.value]
            names = [key.value for key, _ in node.value if isinstance(key, ScalarNode)]
            if keys and all(key and key[0][0] == 'scalar' for key in keys):
                projected = [mapping.get(name, name) for name in names]
                if len(set(projected)) != len(projected):
                    issue(node, 'entity_key_collision')
                    return None
                return ('keys', tuple(sorted({o for key in keys for o in key[0][1]}))), [n for key in keys for n in key[1]]
        return None

    def scan(node, bindings, field=False, ancestry=(), reference_fields=True):
        charge()
        if node is None:
            return
        if id(node) in ancestry:
            raise YAMLPlanError('YAML binding alias cycle')
        ancestry += (id(node),)
        if isinstance(node, ScalarNode):
            observed[id(node)] = observed.get(id(node), 0) + 1
            if node.tag == _STR and _ENTITY.fullmatch(node.value):
                origin = (-id(node), 0, node.value)
                (used if field else unsafe).add(origin)
                record(origin, 'yaml_entity_field' if field else 'yaml_non_reference')
            if node.tag in _INCLUDE:
                # A separate include file has no caller lexical environment.
                # Do not silently authorize a producer before the include.
                if bindings:
                    unsafe.update(origins(bindings))
                    issue(node, 'yaml_variable_include_scope_unsupported')
            elif node.tag == _STR and any(t in node.value for t in ('{{', '{%', '{#')):
                result, markers, consumed, escaped = analyze_template_bindings(
                    node.value, mapping, bindings, max_nodes=max_nodes,
                    output_reference=field)
                used.update(consumed)
                unsafe.update(escaped)
                for origin in consumed:
                    record(origin, 'jinja_entity_reference')
                for origin in escaped:
                    record(origin, 'jinja_unsafe_or_unknown')
                previous = templates.get(id(node))
                if previous and previous[0:2] != (result, markers):
                    unsafe.update(origins(bindings))
                    unsafe.update(previous[3])
                    issue(node, 'yaml_template_alias_scope_conflict')
                    markers = [('unsupported_template_scope', 'Aliased template has differing lexical scopes')]
                templates[id(node)] = (result, markers, (previous[2] if previous else 0) + 1,
                                       origins(bindings) | (previous[3] if previous else set()))
            return
        if isinstance(node, SequenceNode):
            for child in node.value:
                scan(child, bindings, field, ancestry, reference_fields)
        elif isinstance(node, MappingNode):
            for key, child in node.value:
                if key.tag == _STANDARD + 'merge':
                    unsafe.update(origins(bindings))
                    issue(key, 'yaml_variable_merge_scope_unsupported')
                scan(key, bindings, False, ancestry, reference_fields)
                scan(child, bindings, reference_fields and isinstance(key, ScalarNode) and key.value in _FIELDS,
                     ancestry, reference_fields)

    def define(node, bindings):
        if not isinstance(node, MappingNode) or node.tag != _STANDARD + 'map':
            scan(node, bindings)
            issue(node, 'yaml_variable_definition_unsupported')
            return
        for key, child in node.value:
            charge()
            if not isinstance(key, ScalarNode) or key.tag != _STR or not key.value.isidentifier():
                unsafe.update(origins(bindings))
                issue(key, 'yaml_variable_definition_unsupported')
                scan(child, bindings)
                continue
            part = static(child)
            if part:
                bound, nodes = part
                for producer in nodes:
                    producers[id(producer)] = producers.get(id(producer), 0) + 1
                bindings[key.value] = bound
                if isinstance(child, MappingNode):
                    for _, metadata in child.value:
                        scan(metadata, bindings, reference_fields=False)
            else:
                scan(child, bindings)
                # Unknown reassignment must retain the old provenance: an HA
                # runtime expression may keep an earlier value on some path.
                prior = bindings.get(key.value)
                if prior:
                    unsafe.update(prior[1])
                bindings[key.value] = ('unknown', prior[1]) if prior else None

    def write_summary(node):
        """Overapproximate writes, without observing/registering a producer twice.

        HA action variables use update/_assign, including from child scripts.
        Only the runtime repeat object is local. This summary is used for loop
        invariants and interference, not as authority to edit YAML literals.
        """
        charge()
        if not isinstance(node, SequenceNode):
            return {}
        writes = {}
        for action in node.value:
            charge()
            if not isinstance(action, MappingNode):
                continue
            values = pairs(action)
            definitions = values.get('variables')
            if isinstance(definitions, MappingNode):
                for key, child in definitions.value:
                    charge()
                    if isinstance(key, ScalarNode) and key.tag == _STR and key.value.isidentifier():
                        part = static(child)
                        bound = part[0] if part else None
                        if key.value in writes:
                            writes[key.value] = join([{key.value: writes[key.value]},
                                                      {key.value: bound}])[key.value]
                        else:
                            writes[key.value] = bound
            response = values.get('response_variable')
            if isinstance(response, ScalarNode):
                prior = writes.get(response.value)
                writes[response.value] = ('unknown', prior[1]) if prior else None
            if 'wait_template' in values or 'wait_for_trigger' in values:
                prior = writes.get('wait')
                writes['wait'] = ('unknown', prior[1]) if prior else None
            children = []
            for key in ('sequence', 'then', 'else', 'default'):
                if key in values:
                    children.append(values[key])
            choices = values.get('choose')
            if isinstance(choices, SequenceNode):
                for choice in choices.value:
                    charge()
                    if isinstance(choice, MappingNode) and 'sequence' in pairs(choice):
                        children.append(pairs(choice)['sequence'])
            parallel = values.get('parallel')
            if isinstance(parallel, SequenceNode):
                # Each parallel entry is one action; explicit sequence actions
                # are handled recursively by this same summary.
                children.append(parallel)
            repeat = values.get('repeat')
            if isinstance(repeat, MappingNode) and 'sequence' in pairs(repeat):
                local_writes = write_summary(pairs(repeat)['sequence'])
                local_writes.pop('repeat', None)
                for name, bound in local_writes.items():
                    writes[name] = (join([{name: writes[name]}, {name: bound}])[name]
                                    if name in writes else bound)
            for child in children:
                for name, bound in write_summary(child).items():
                    writes[name] = (join([{name: writes[name]}, {name: bound}])[name]
                                    if name in writes else bound)
        return writes

    def envelope(bindings, writes):
        # A write may be skipped (condition, disabled action, zero iterations).
        # Include the entry value even when the syntactic branches are exhaustive.
        return join([bindings, {**bindings, **writes}])

    def control(action, values, bindings, interference):
        """Analyze child scripts and their shared outer variable writes once."""
        kinds = [name for name in ('choose', 'if', 'repeat', 'parallel', 'sequence')
                 if name in values]
        kind = kinds[0]
        allowed = {'choose': {'choose', 'default'}, 'if': {'if', 'then', 'else'},
                   'repeat': {'repeat'}, 'parallel': {'parallel'},
                   'sequence': {'sequence'}}[kind]
        if len(kinds) != 1 or 'variables' in values or 'response_variable' in values:
            unsafe.update(origins(bindings))
            issue(action, 'yaml_variable_control_scope_unsupported')
            scan(action, bindings)
            return
        # Action metadata/labels are still consumers, never variable producers.
        for key, child in action.value:
            scan(key, bindings)
            if not isinstance(key, ScalarNode) or key.value not in allowed:
                scan(child, bindings, isinstance(key, ScalarNode) and key.value in _FIELDS)
        entry = bindings.copy()
        exits = [entry]

        def branch(child, environment=None, concurrent=None):
            local = (entry if environment is None else environment).copy()
            if not isinstance(child, SequenceNode):
                issue(child, 'yaml_variable_control_scope_unsupported')
            sequence(child, local, interference if concurrent is None else concurrent)
            exits.append(local)

        if kind == 'choose':
            choices = values['choose']
            if not isinstance(choices, SequenceNode):
                unsafe.update(origins(bindings))
                issue(choices, 'yaml_variable_control_scope_unsupported')
                scan(choices, bindings)
            else:
                for choice in choices.value:
                    charge()
                    if not isinstance(choice, MappingNode) or 'sequence' not in pairs(choice):
                        unsafe.update(origins(bindings))
                        issue(choice, 'yaml_variable_control_scope_unsupported')
                        scan(choice, bindings)
                        continue
                    for key, child in choice.value:
                        scan(key, bindings)
                        if isinstance(key, ScalarNode) and key.value == 'sequence':
                            branch(child)
                        else:
                            scan(child, bindings)
            if 'default' in values:
                branch(values['default'])
        elif kind == 'if':
            scan(values['if'], bindings)
            if 'then' not in values:
                issue(action, 'yaml_variable_control_scope_unsupported')
                unsafe.update(origins(bindings))
            for name in ('then', 'else'):
                if name in values:
                    branch(values[name])
        elif kind == 'sequence':
            branch(values['sequence'])
        elif kind == 'parallel':
            children = values['parallel']
            shared = envelope(entry, write_summary(children))
            # Another branch may assign between any two actions, even between
            # this branch's own assignment and its next consumer. Keep the
            # interference envelope throughout nested child scripts too.
            concurrent = join([shared, interference]) if interference else shared
            if not isinstance(children, SequenceNode):
                branch(children, shared, concurrent)
            else:
                for action_node in children.value:
                    child = SequenceNode(_STANDARD + 'seq', [action_node],
                                         children.start_mark, children.end_mark)
                    branch(child, shared, concurrent)
        else:
            repeat = values['repeat']
            if not isinstance(repeat, MappingNode) or 'sequence' not in pairs(repeat):
                unsafe.update(origins(bindings))
                issue(repeat, 'yaml_variable_control_scope_unsupported')
                scan(repeat, bindings)
            else:
                repeat_values = pairs(repeat)
                body = repeat_values['sequence']
                writes = write_summary(body)
                writes.pop('repeat', None)
                invariant = envelope(entry, writes)
                # HA defines this runtime object locally on every iteration.
                # It must never inherit a same-named outer static binding.
                invariant['repeat'] = None
                for key, child in repeat.value:
                    scan(key, invariant)
                    if not isinstance(key, ScalarNode) or key.value != 'sequence':
                        scan(child, invariant)
                local_interference = ({name: bound for name, bound in interference.items()
                                       if name != 'repeat'} if interference else {})
                branch(body, invariant, local_interference)
                # No proof for runtime repeat.item/index/etc. Any textual use
                # gets an explicit block, independently of Jinja sink detection.
                pending = [repeat]
                while pending:
                    current = pending.pop()
                    charge()
                    if isinstance(current, ScalarNode):
                        if (current.tag == _STR and any(t in current.value for t in ('{{', '{%', '{#'))
                                and re.search(r'\brepeat\b', current.value)):
                            issue(current, 'yaml_runtime_repeat_variable_unsupported')
                    elif isinstance(current, SequenceNode):
                        pending.extend(current.value)
                    elif isinstance(current, MappingNode):
                        pending.extend(n for pair in current.value for n in pair)
                for local in exits[1:]:
                    if 'repeat' in entry:
                        local['repeat'] = entry['repeat']
                    else:
                        local.pop('repeat', None)
        bindings.clear()
        bindings.update(join(exits))

    def sequence(node, bindings, interference=None):
        if not isinstance(node, SequenceNode):
            scan(node, bindings)
            if bindings:
                issue(node, 'yaml_variable_sequence_unsupported')
            return
        for action in node.value:
            charge()
            if interference:
                bindings.update(join([bindings, interference]))
            if not isinstance(action, MappingNode):
                scan(action, bindings)
                continue
            values = pairs(action)
            if any(key.tag == _STANDARD + 'merge' for key, _ in action.value):
                unsafe.update(origins(bindings))
                issue(action, 'yaml_variable_merge_scope_unsupported')
                scan(action, bindings)
                continue
            complex_scope = any(k in values for k in ('choose', 'if', 'repeat', 'parallel', 'sequence'))
            if complex_scope:
                control(action, values, bindings, interference)
                continue
            entry = bindings.copy()
            if 'variables' in values:
                define(values['variables'], bindings)
                if interference:
                    bindings.update(join([bindings, interference]))
            for key, child in action.value:
                if not (isinstance(key, ScalarNode) and key.value == 'variables'):
                    scan(key, bindings)
                    # enabled is evaluated before this action's assignments.
                    environment = entry if isinstance(key, ScalarNode) and key.value == 'enabled' else bindings
                    scan(child, environment, isinstance(key, ScalarNode) and key.value in _FIELDS)
            response = values.get('response_variable')
            if isinstance(response, ScalarNode) and response.value in bindings:
                prior = bindings[response.value]
                if prior:
                    unsafe.update(prior[1])
                bindings[response.value] = ('unknown', prior[1]) if prior else None
                issue(response, 'yaml_response_variable_rebinding_unsupported')
            if ('wait_template' in values or 'wait_for_trigger' in values) and 'wait' in bindings:
                prior = bindings['wait']
                if prior:
                    unsafe.update(prior[1])
                bindings['wait'] = ('unknown', prior[1]) if prior else None
                issue(action, 'yaml_runtime_variable_rebinding_unsupported')
            if 'enabled' in values:
                bindings.update(join([entry, bindings]))

    def scope(node):
        if not isinstance(node, MappingNode):
            return
        values = pairs(node)
        bindings = {}
        if 'variables' in values:
            define(values['variables'], bindings)
        for key, child in node.value:
            if key.tag == _STANDARD + 'merge':
                unsafe.update(origins(bindings))
                issue(key, 'yaml_variable_merge_scope_unsupported')
                scan(child, bindings)
            elif not isinstance(key, ScalarNode):
                scan(child, bindings)
            elif key.value in ('action', 'actions', 'sequence'):
                sequence(child, bindings.copy())
            elif key.value != 'variables':
                scan(child, bindings, key.value in _FIELDS)

    def discover(node, context='', ancestry=()):
        charge()
        if node is None:
            return
        if id(node) in ancestry:
            raise YAMLPlanError('YAML binding alias cycle')
        ancestry += (id(node),)
        context = context.split(' ', 1)[0]
        if context == 'automation':
            if isinstance(node, SequenceNode):
                for child in node.value:
                    scope(child)
                return
            if isinstance(node, MappingNode):
                scope(node)
                return
        if context == 'script' and isinstance(node, MappingNode):
            if 'sequence' in pairs(node):
                scope(node)  # !include_dir_named supplies a single script body.
            else:
                for _, child in node.value:
                    scope(child)
            return
        if isinstance(node, MappingNode):
            for key, child in node.value:
                name = key.value if isinstance(key, ScalarNode) else ''
                kind = name.split(' ', 1)[0]
                if context == 'packages':
                    discover(child, '', ancestry)
                elif context == 'homeassistant' and name == 'packages':
                    discover(child, 'packages', ancestry)
                elif context == '' and kind in ('automation', 'script', 'homeassistant'):
                    discover(child, kind, ancestry)
        # Unknown integration/metadata scopes are deliberately not inferred.

    discover(tree, inherited_key)
    return producers, used, unsafe, templates, observed, consumers


def _plan(reader: _Reader, mapping: dict[str, str], roots: tuple[str, ...], limits: Limits) -> Plan:
    files: dict[str, FileSnapshot] = {}
    patches = []
    edges = []
    issues: set[Issue] = set()
    active: set[str] = set()
    contexts_seen = {}
    total_bytes = 0
    nodes_count = 0
    binding_proofs = []

    def visit(path: str, graph_depth: int = 0, inherited_field=False, inherited_key="",
              inherited_lovelace=False, inherited_variable=False) -> None:
        nonlocal total_bytes, nodes_count
        path = _relative(path)
        if PurePosixPath(path).name == "secrets.yaml":
            raise YAMLPlanError("secret stores are outside source planning scope")
        if graph_depth > limits.max_depth:
            raise YAMLPlanError("include graph depth limit")
        if path in active:
            raise YAMLPlanError("include graph cycle")
        context = (inherited_field, inherited_key, inherited_lovelace, inherited_variable)
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
        includes_seen: dict[int, tuple] = {}
        dashboard_paths = set()
        effective_keys_cache = {}

        def effective_keys(node, ancestry=()):
            """Include native YAML merge keys when checking renamed-key collisions."""
            if id(node) in ancestry or len(ancestry) > limits.max_depth:
                raise YAMLPlanError('YAML merge cycle/depth limit')
            if id(node) in effective_keys_cache:
                return effective_keys_cache[id(node)]
            if not isinstance(node, MappingNode):
                raise YAMLPlanError('invalid YAML merge source')
            keys = set()
            for key, child in node.value:
                if key.tag == _STANDARD + 'merge':
                    sources = child.value if isinstance(child, SequenceNode) else (child,)
                    for source in sources:
                        keys.update(effective_keys(source, ancestry + (id(node),)))
                elif isinstance(key, ScalarNode):
                    keys.add(key.value)
            effective_keys_cache[id(node)] = keys
            return keys

        def issue(node, code):
            issues.add(Issue(path, node.start_mark.line + 1, code))

        def walk(node, depth=0, field=False, parent_key="", lovelace=False,
                 variable_definition=False):
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
                        include_context = (field, parent_key, lovelace, variable_definition)
                        if id(node) in includes_seen:
                            if includes_seen[id(node)] != include_context:
                                raise YAMLPlanError("include alias context conflict")
                            return
                        includes_seen[id(node)] = include_context
                        target = str(PurePosixPath(path).parent / _relative(node.value))
                        targets = (target,) if node.tag == '!include' else reader.listing(target)
                        edges.append(IncludeEdge(path, node.start_mark.line + 1, node.tag, target, targets))
                        for child in targets:
                            child_key = ('__utility_meter_entry__'
                                         if parent_key == 'utility_meter' and node.tag == '!include_dir_named'
                                         else parent_key)
                            visit(child, graph_depth + 1, field, child_key, lovelace, variable_definition)
                    elif node.tag == '!secret':
                        if field:
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
                        walk(child, depth + 1, field, parent_key, lovelace, variable_definition)
                elif isinstance(node, MappingNode):
                    # Preserve ambiguous duplicate keys, but block any apply.
                    raw_keys = [key.value for key, _ in node.value if isinstance(key, ScalarNode)]
                    if len(raw_keys) != len(set(raw_keys)):
                        issue(node, "duplicate_mapping_keys")
                    merged_keys = effective_keys(node)
                    projected = [mapping.get(k, k) if parent_key in _ENTITY_KEY_MAPS else k for k in merged_keys]
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
                        if key.tag == _STANDARD + 'merge':
                            # The alias/anchor bytes stay in place. Walk the
                            # original source with the receiving map's context;
                            # conflicting alias consumers remain blocked below.
                            walk(value, depth + 1, field, parent_key, lovelace, variable_definition)
                            continue
                        if not isinstance(key, ScalarNode) or key.tag != _STR:
                            # Primitive non-string keys cannot be entity IDs.
                            # Preserve them and still inspect their descendants;
                            # a numeric/boolean key must not hide a reference.
                            if (isinstance(key, ScalarNode) and parent_key not in _ENTITY_KEY_MAPS
                                    and key.tag in {_STANDARD + kind for kind in ('bool', 'int', 'float', 'null')}):
                                walk(value, depth + 1, False, '', lovelace, variable_definition)
                                continue
                            # Complex keys need semantic provenance.
                            issue(key, "unsupported_mapping_key")
                            walk(key, depth + 1, variable_definition=variable_definition)
                            walk(value, depth + 1, variable_definition=variable_definition)
                            continue
                        if parent_key == 'customize_glob' and any(
                                fnmatch.fnmatchcase(old, key.value) != fnmatch.fnmatchcase(new, key.value)
                                for old, new in mapping.items()):
                            issue(key, 'customization_glob_membership_changed')
                        walk(key, depth + 1, parent_key in _ENTITY_KEY_MAPS and not variable_definition,
                             variable_definition=variable_definition)
                        platform = values.get('platform')
                        source_field = (key.value == 'source' and isinstance(platform, ScalarNode)
                                        and platform.value in ('integration', 'derivative'))
                        source_field = source_field or (key.value == 'source'
                                                       and parent_key == '__utility_meter_entry__')
                        child_key = '__utility_meter_entry__' if parent_key == 'utility_meter' else key.value
                        child_variable = variable_definition or key.value in ('variables', 'trigger_variables')
                        walk(value, depth + 1,
                             (key.value in _FIELDS or source_field) and not child_variable, child_key,
                             lovelace or key.value == 'lovelace', child_variable)
                else:
                    issue(node, "unsupported_yaml_node")
            finally:
                visiting.remove(id(node))

        walk(tree, field=inherited_field, parent_key=inherited_key, lovelace=inherited_lovelace,
             variable_definition=inherited_variable)
        producers, variable_used, variable_unsafe, variable_templates, observed, consumers = _yaml_bindings(
            tree, inherited_key, mapping, issue, limits.max_nodes)
        for identity, (_, _, count, origins) in variable_templates.items():
            if identity in candidates and count != len(candidates[identity][1]):
                variable_unsafe.update(origins)
                issue(candidates[identity][0], 'yaml_template_alias_scope_conflict')
        for dashboard in sorted(dashboard_paths):
            edges.append(IncludeEdge(path, 0, "lovelace_yaml_dashboard", dashboard, (dashboard,)))
            visit(dashboard, graph_depth + 1)
        edits = []
        for node, contexts in candidates.values():
            value = node.value
            if any(x in value for x in ('{{', '{%', '{#', '[[[')):
                if '[[[' in value:
                    issue(node, "template_requires_reference_parser")
                    continue
                template_context = Context(Consumer.TEMPLATE, (Scope((), Syntax.JINJA),))
                binding_template = variable_templates.get(id(node))
                if binding_template is None:
                    decoded_plan = plan_references(value, mapping, template_context)
                else:
                    replacement_value, review, _, _ = binding_template
                    # Use the parser result with the exact lexical YAML bindings;
                    # parsing it again without those bindings loses provenance.
                    changes = ((ReferenceChange((), 'value', value, replacement_value),)
                               if replacement_value != value else ())
                    decoded_plan = ReferencePlan(replacement_value, changes, tuple(review))
                token = tokens.get(node.end_mark.index)
                if token is None or token.value != value or decoded_plan.markers:
                    issue(node, "template_requires_reference_parser")
                    continue
                if not decoded_plan.changes:
                    continue  # Preserve unaffected bytes without re-serializing.
                raw = text[token.start_mark.index:token.end_mark.index]
                source_plan = plan_references(raw, mapping, template_context)
                replacement = source_plan.value
                if source_plan.markers and token.style not in ("'", '"'):
                    issue(node, "template_requires_source_span_mapper")
                    continue
                if token.style == "'":
                    # Literal newlines in single-quoted YAML are folded. JSON
                    # escaping preserves the exact decoded template when its
                    # original single-quoted scalar spans paragraphs.
                    replacement = (json.dumps(decoded_plan.value, ensure_ascii=False)
                                   if '\n' in decoded_plan.value or '\r' in decoded_plan.value
                                   else "'" + decoded_plan.value.replace("'", "''") + "'")
                elif token.style == '"':
                    replacement = json.dumps(decoded_plan.value, ensure_ascii=False)
                # Operate on the source token, preserving YAML quoting, block
                # indentation and comments. Independent decoding must match
                # the planned template exactly; escaped spellings fail closed.
                try:
                    decoded = yaml.compose(replacement, Loader=yaml.SafeLoader)
                except yaml.YAMLError:
                    decoded = None
                if not isinstance(decoded, ScalarNode) or decoded.value != decoded_plan.value:
                    issue(node, "template_requires_source_span_mapper")
                    continue
                if decoded_plan.changes and raw != replacement:
                    start = len(text[:token.start_mark.index].encode('utf-8'))
                    end = len(text[:token.end_mark.index].encode('utf-8'))
                    edits.append(Edit(start, end, raw.encode('utf-8'), replacement.encode('utf-8')))
                continue
            mentioned = any(m.group(1) in mapping for m in _TOKEN.finditer(value))
            if not mentioned:
                continue
            origin = (-id(node), 0, value)
            producer_count = producers.get(id(node), 0)
            proven_variable = (producer_count > 0
                               and len(contexts) == producer_count + observed.get(id(node), 0)
                               and origin in variable_used
                               and origin not in variable_unsafe)
            if producer_count:
                binding_proofs.append(VariableBindingProof(
                    path, node.start_mark.line + 1, value, producer_count, len(contexts),
                    producer_count + observed.get(id(node), 0),
                    tuple(sorted(consumers.get(origin, {}).items())), proven_variable))
            if (producer_count and not proven_variable) or (not all(contexts) and not proven_variable):
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
                tuple(sorted(issues, key=lambda i: (i.path, i.line, i.code))),
                variable_bindings=tuple(sorted(binding_proofs, key=lambda p: (p.path, p.source_line, p.entity_id))))
