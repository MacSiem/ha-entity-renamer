"""Whole-proposal native validation and conservative YAML reload coverage.

Owner holds native editor locks from staging through file mutation. Validation is
an isolated subprocess, never a redirection of the running HA config or loader.
The receipt is ephemeral server state, not an authorization token from a client.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import TYPE_CHECKING

from .installed_cost_trace import trace_call
from .yaml_plan import Limits, _Reader, _relative

if TYPE_CHECKING:
    from .yaml_source_binding import OriginalSourceEvidence
    from .template_source_evidence import OriginalTemplateStateSources, CandidateTemplateStateSources


class YAMLValidationError(ValueError):
    """Sanitized preflight/activation failure, without config or secret values."""


@dataclass(frozen=True)
class ValidationReceipt:
    config_root: str
    source_files: tuple[tuple[str, str, int, int], ...]
    source_directories: tuple[tuple[str, tuple], ...]
    proposed_files: tuple[tuple[str, str], ...]
    changed_domains: tuple[str, ...]
    unsupported_domains: tuple[str, ...]
    version: str
    # Only digests leave the isolated validator; never expanded secrets/config.
    runtime_expected: tuple[tuple[str, str | None, str | None], ...] = ()
    # None is a legacy/strict receipt. Otherwise this is a private, immutable
    # native-checker input, including explicitly witnessed absence.
    checker_repairs_snapshot: tuple[str, ...] | None = None

    # None preserves legacy receipt encoding.
    source_links: tuple[tuple[str, str, str, int, int], ...] | None = None

    @property
    def ready_for_apply(self):
        return not self.unsupported_domains


@dataclass(frozen=True)
class TemplateStateSourceValidation:
    """Same ORIGINAL checker/source scope, never runtime or writer authority.

    The legacy receipt retains its own reload admission/unsupported flags.
    Selective activation requires the separate actual runtime/root contract.
    """
    receipt: ValidationReceipt
    evidence: OriginalSourceEvidence
    original_sources: OriginalTemplateStateSources
    candidate_sources: CandidateTemplateStateSources
    state_config_program: object = None
    dependent_config_program: object = None
    external_source_proof: tuple = ()

    @property
    def migration_ready(self):
        return False


@dataclass(frozen=True)
class DependentConfigValidation:
    """Private read-only adjunct of one original validation, no apply authority."""
    receipt: ValidationReceipt
    evidence: OriginalSourceEvidence
    config_program: object

    @property
    def migration_ready(self):
        return False


# Ancillary runtime inputs used by the native checker (including blueprint refs).
_STORAGE = {'core.config_entries', 'core.entity_registry', 'core.device_registry',
            'core.area_registry', 'repairs.issue_registry'}
_TREES = {'custom_components', 'blueprints', 'custom_templates'}
_MAX_BYTES = 128_000_000
_MAX_FILES = 10_000
_MAX_REPAIRS_SNAPSHOT = 2 * 1024 * 1024


def _checker_repairs_snapshot(plan, staged_before, directories, dashboard_yaml, dashboard_storage):
    """Pin only the ancillary checker input, never a real planned source."""
    path = '.storage/repairs.issue_registry'
    explicit = {row.path for row in plan.files}
    explicit.update(getattr(dashboard_yaml, 'paths', ()))
    if dashboard_storage is not None:
        explicit.update(row[0] for row in dashboard_storage.files)
        if 'repairs.issue_registry' in dashboard_storage.tracked_names:
            return None
    auxiliary = plan.powercalc_inputs
    if auxiliary is not None:
        explicit.update(row.path for row in auxiliary.files)
        # The Powercalc ancillary directory list includes _STORAGE merely as
        # checker inputs. A full enumeration, unlike that named list, is a
        # genuine directory dependency and must keep the strict fence.
        if any(directory == '.storage' and names is None
               for directory, _, names in auxiliary.directories):
            return None
    if (path in explicit or any(row.path == '.storage' for row in plan.directories)
            or any(directory == '.storage' and names is None for directory, _, names in directories)):
        return None
    file = Path(staged_before) / path
    if not file.exists():
        return ('absent',)
    raw = file.read_bytes()
    if len(raw) > _MAX_REPAIRS_SNAPSHOT:
        return None  # Retain the legacy strict fence for unusually large stores.
    return ('present', base64.b64encode(raw).decode('ascii'))


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _capture_link(reader, root, path):
    """Bind a top-level alias to a directory under the held canonical root."""
    parent = reader.open('custom_components', directory=True)
    try:
        name = path.split('/')[1]
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        text = os.readlink(name, dir_fd=parent)
        if not stat.S_ISLNK(before.st_mode):
            raise YAMLValidationError('unsafe_validation_input')
        # resolve(strict=True) understands alternate names for the root, such
        # as /config -> /homeassistant. The subsequent NOFOLLOW open anchors
        # the resolved target beneath the held root, never beneath the alias.
        resolved = (root / 'custom_components' / text).resolve(strict=True)
        target = resolved.relative_to(root).as_posix()
        if not target.startswith('custom_components/') or target == path:
            raise YAMLValidationError('unsafe_validation_input')
        fd = reader.open(target, directory=True)
        os.close(fd)
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if ((before.st_mode, before.st_dev, before.st_ino) !=
                (after.st_mode, after.st_dev, after.st_ino)
                or os.readlink(name, dir_fd=parent) != text
                or (os.stat(root).st_dev, os.stat(root).st_ino) !=
                   (os.fstat(reader.root).st_dev, os.fstat(reader.root).st_ino)):
            raise YAMLValidationError('validation_source_changed')
        return (path, text, target, before.st_dev, before.st_ino)
    except (OSError, ValueError, RuntimeError):
        raise YAMLValidationError('unsafe_validation_input') from None
    finally:
        os.close(parent)


def _assert_links(reader, root, links):
    for expected in links:
        if _capture_link(reader, root, expected[0]) != expected:
            raise YAMLValidationError('validation_source_changed')


def _stage_links(roots, links):
    for root in roots:
        for path, _, target, _, _ in links:
            link = Path(root) / _relative(path)
            destination = Path(root) / _relative(target)
            if not destination.is_dir():
                raise YAMLValidationError('unsafe_validation_input')
            link.symlink_to(os.path.relpath(destination, link.parent), target_is_directory=True)


def _stage(config_root, plan, baseline, proposed, with_sources=False, native_helpers=(), registry_only=False,
           dashboard_yaml=None, dashboard_storage=None, mixed_powercalc=False):
    reader = _Reader(config_root, Limits(max_file_bytes=_MAX_BYTES))
    try:
        return _stage_owned(reader, plan, baseline, proposed, config_root=Path(config_root).resolve(), with_sources=with_sources,
                            native_helpers=native_helpers, registry_only=registry_only,
                            dashboard_yaml=dashboard_yaml, dashboard_storage=dashboard_storage,
                            mixed_powercalc=mixed_powercalc)
    except (OSError, ValueError) as error:
        if isinstance(error, YAMLValidationError):
            raise
        raise YAMLValidationError('validation_staging_failed') from None
    finally:
        os.close(reader.root)


def _stage_owned(reader, plan, baseline, proposed, *, config_root, with_sources=False, native_helpers=(), registry_only=False,
                 dashboard_yaml=None, dashboard_storage=None, mixed_powercalc=False):
    if (type(mixed_powercalc) is not bool or type(registry_only) is not bool or not plan.ready
            or (not plan.patches and not native_helpers and not registry_only)
            or registry_only and (not with_sources or plan.patches or native_helpers)):
        raise YAMLValidationError('yaml_plan_not_ready')
    reachable = {'configuration.yaml'}
    if dashboard_yaml is not None:
        from .dashboard_yaml_semantics import RegisteredDashboardYAML, qualify_dashboard_yaml
        if type(dashboard_yaml) is not RegisteredDashboardYAML or not with_sources:
            raise YAMLValidationError('dashboard_yaml_evidence_required')
        dashboard_yaml.assert_plan(plan)
        if len(dashboard_yaml.paths) != len(set(dashboard_yaml.paths)):
            raise YAMLValidationError('dashboard_yaml_evidence_required')
        for path in dashboard_yaml.paths:
            raw, _ = reader.read(path)
            qualify_dashboard_yaml(plan, path, raw, dict(dashboard_yaml.mapping_items))
            reachable.add(path)
    while True:
        expanded = reachable | {path for edge in plan.edges
                                if edge.source in reachable and edge.tag.startswith('!include')
                                for path in edge.files}
        if expanded == reachable:
            break
        reachable = expanded
    if any(patch.path not in reachable for patch in plan.patches):
        raise YAMLValidationError('yaml_activation_outside_native_config')
    paths = {item.path for item in plan.files}
    dashboard_files = {}
    if dashboard_storage is not None:
        from .dashboard_storage_semantics import NativeDashboardStorage
        if (type(dashboard_storage) is not NativeDashboardStorage or not with_sources
                or plan.powercalc_inputs is not None and not mixed_powercalc):
            raise YAMLValidationError('dashboard_storage_evidence_required')
        dashboard_files = {path: (digest, device, inode)
                           for path, digest, device, inode in dashboard_storage.files}
        if len(dashboard_files) != len(dashboard_storage.files) or set(dashboard_files) & paths:
            raise YAMLValidationError('dashboard_storage_evidence_required')
        paths.update(dashboard_files)
    # Secrets searches walk up from each included YAML to the configuration root.
    for path in tuple(paths):
        parent = Path(path).parent
        while True:
            secret = str(parent / 'secrets.yaml')
            try:
                fd = reader.open(secret)
            except FileNotFoundError:
                pass
            else:
                os.close(fd)
                paths.add(secret)
            if parent == Path('.'):
                break
            parent = parent.parent
    directories = {item.path: item.entries for item in plan.directories}
    directory_names = {item.path: None for item in plan.directories}
    auxiliary = plan.powercalc_inputs
    if auxiliary is not None:
        from .yaml_plan import PowercalcSourceInputs
        if (type(auxiliary) is not PowercalcSourceInputs
                or {r.path for r in auxiliary.files} & ({r.path for r in plan.files} | set(dashboard_files))):
            raise YAMLValidationError('powercalc_auxiliary_input_invalid')
        paths.update(row.path for row in auxiliary.files)
        for path, entries, names in auxiliary.directories:
            if path in directories:
                raise YAMLValidationError('powercalc_auxiliary_directory_overlap')
            directories[path], directory_names[path] = entries, names
    def remember_directory(relative):
        # Include graph directories are complete enumerations. Ancestors and
        # storage only guard inputs actually used by the checker, including
        # absent secrets. Unrelated HA logs/traces/backups may change normally.
        names = directory_names.get(relative)
        if relative not in directories:
            names = {'secrets.yaml'}
            prefix = '' if relative == '.' else relative + '/'
            names.update(path[len(prefix):].split('/', 1)[0]
                         for path in paths if path.startswith(prefix))
            if relative == '.':
                names.update(_TREES | {'.storage'})
            elif relative == '.storage':
                names = set(_STORAGE) | (set(dashboard_storage.tracked_names)
                                         if dashboard_storage is not None else set())
        fd = os.dup(reader.root) if relative == '.' else reader.open(relative, directory=True)
        try:
            with os.scandir(fd) as entries:
                current = tuple(sorted(
                    (entry.name, (info := entry.stat(follow_symlinks=False)).st_mode,
                     info.st_dev, info.st_ino) for entry in entries
                    if names is None or entry.name in names))
                if relative in directories and directories[relative] != current:
                    raise YAMLValidationError('validation_graph_changed')
                directories[relative] = current
                directory_names[relative] = None if names is None else tuple(sorted(names))
        finally:
            os.close(fd)
    # Auxiliary directory identities/absences came from the SAME prepared Plan.
    if auxiliary is not None:
        for path, _, _ in auxiliary.directories:
            remember_directory(path)
        # Preserve each verified Powercalc projection and include every direct
        # child used by the combined checker, including YAML files at root.
        for directory, _, _ in auxiliary.directories:
            if directory_names[directory] is None:
                continue
            old_names = set(directory_names[directory])
            names = old_names | {'secrets.yaml'}
            prefix = '' if directory == '.' else directory + '/'
            names.update(path[len(prefix):].split('/', 1)[0]
                         for path in paths if path.startswith(prefix))
            if directory == '.':
                names.update(_TREES | {'.storage'})
            elif directory == '.storage':
                names.update(_STORAGE)
                if dashboard_storage is not None:
                    names.update(dashboard_storage.tracked_names)
            fd = os.dup(reader.root) if directory == '.' else reader.open(directory, directory=True)
            try:
                with os.scandir(fd) as listing:
                    current = tuple(sorted((entry.name, (info := entry.stat(follow_symlinks=False)).st_mode,
                                            info.st_dev, info.st_ino) for entry in listing if entry.name in names))
            finally:
                os.close(fd)
            if tuple(row for row in current if row[0] in old_names) != directories[directory]:
                raise YAMLValidationError('powercalc_auxiliary_directory_changed')
            directories[directory], directory_names[directory] = current, tuple(sorted(names))
    # Track absence as well as presence (new secrets, auxiliary dirs or registry files).
    remember_directory('.')
    for path in tuple(paths):
        parent = Path(path).parent
        while parent != Path('.'):
            remember_directory(str(parent))
            parent = parent.parent
    # Copy required auxiliary trees without following symlinks. Bound all input.
    walked = 0
    links = []
    def walk(relative, depth=0):
        nonlocal walked
        if depth > 48:
            raise YAMLValidationError('validation_input_limit')
        fd = reader.open(relative, directory=True)
        try:
            entries = []
            with os.scandir(fd) as listing:
                for entry in listing:
                    walked += 1
                    if walked > _MAX_FILES:
                        raise YAMLValidationError('validation_input_limit')
                    info = entry.stat(follow_symlinks=False)
                    entries.append((entry.name, info.st_mode, info.st_dev, info.st_ino))
                    if entry.name == '__pycache__':
                        continue
                    path = relative + '/' + entry.name
                    if stat.S_ISDIR(info.st_mode):
                        walk(path, depth + 1)
                    elif stat.S_ISREG(info.st_mode):
                        paths.add(path)
                    elif relative == 'custom_components' and stat.S_ISLNK(info.st_mode):
                        links.append(_capture_link(reader, config_root, path))
                    else:
                        raise YAMLValidationError('unsafe_validation_input')
            if auxiliary is not None and relative in {r[0] for r in auxiliary.directories}:
                names = directory_names[relative]
                selected = tuple(sorted(row for row in entries if names is None or row[0] in names))
                if selected != directories[relative]:
                    raise YAMLValidationError('powercalc_auxiliary_directory_changed')
            else:
                directories[relative] = tuple(sorted(entries))
                directory_names[relative] = None
        finally:
            os.close(fd)
    try:
        for tree in sorted(_TREES):
            try:
                fd = reader.open(tree, directory=True)
            except FileNotFoundError:
                continue
            os.close(fd)
            walk(tree)
        try:
            remember_directory('.storage')
        except FileNotFoundError:
            pass
        for name in _STORAGE:
            path = '.storage/' + name
            try:
                fd = reader.open(path)
            except FileNotFoundError:
                continue
            os.close(fd)
            paths.add(path)
        if len(paths) > _MAX_FILES:
            raise YAMLValidationError('validation_input_limit')
        patches = {item.path: item for item in plan.patches}
        if len(patches) != len(plan.patches) or not set(patches) <= paths:
            raise YAMLValidationError('invalid_proposed_graph')
        for path in directories:
            if path != '.':
                (baseline / path).mkdir(parents=True, exist_ok=True, mode=0o700)
                (proposed / path).mkdir(parents=True, exist_ok=True, mode=0o700)
        snapshots = {item.path: item for item in plan.files}
        if auxiliary is not None:
            snapshots.update((item.path, item) for item in auxiliary.files)
        sources, total = [], 0
        original_bytes = {} if with_sources else None
        for path in sorted(paths):
            raw, info = reader.read(path)
            digest = _digest(raw)
            if path in dashboard_files and (digest, info.st_dev, info.st_ino) != dashboard_files[path]:
                raise YAMLValidationError('dashboard_storage_source_changed')
            total += len(raw)
            if total > _MAX_BYTES:
                raise YAMLValidationError('validation_input_limit')
            if path in snapshots:
                snap = snapshots[path]
                if (digest, info.st_dev, info.st_ino) != (snap.sha256, snap.device, snap.inode):
                    raise YAMLValidationError('validation_source_changed')
                if original_bytes is not None and path in {r.path for r in plan.files}:
                    original_bytes[path] = raw
            replacement = raw
            if patch := patches.get(path):
                if patch.expected_sha256 != digest:
                    raise YAMLValidationError('validation_source_changed')
                last = len(raw) + 1
                for edit in reversed(patch.edits):
                    if (edit.start < 0 or edit.end < edit.start or edit.end > last
                            or raw[edit.start:edit.end] != edit.before):
                        raise YAMLValidationError('invalid_patch_spans')
                    replacement = replacement[:edit.start] + edit.after + replacement[edit.end:]
                    last = edit.start
                if replacement != patch.proposed_bytes or replacement == raw:
                    raise YAMLValidationError('invalid_patch_content')
            for root, data in ((baseline, raw), (proposed, replacement)):
                destination = Path(root) / _relative(path)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                destination.write_bytes(data)
                destination.chmod(0o600)
            sources.append((path, digest, info.st_dev, info.st_ino))
        _assert_links(reader, config_root, tuple(links))
        _stage_links((baseline, proposed), links)
        staged = (tuple(sources), tuple((path, directories[path], directory_names[path])
                                       for path in sorted(directories)))
        if with_sources:
            from .yaml_source_ledger import (extract_source_ledger,
                                              extract_template_source_ledger, extract_powercalc_source_ledger)
            return (*staged, extract_source_ledger(plan, original_bytes),
                    extract_template_source_ledger(plan, original_bytes),
                    extract_powercalc_source_ledger(plan, original_bytes), tuple(links))
        return (*staged, tuple(links))
    except (OSError, ValueError) as exc:
        if isinstance(exc, YAMLValidationError):
            raise
        raise YAMLValidationError('validation_staging_failed') from None


@trace_call("validation_fence")
def assert_validation_current(receipt):
    """Recheck ALL validator inputs immediately before first mutation, under locks."""
    snapshot = getattr(receipt, 'checker_repairs_snapshot', None)
    if snapshot is not None:
        # Authenticate the private checker snapshot before excluding only this
        # ancillary input from the live fence; legacy receipts remain strict.
        from .native_input_baseline import _receipt_bytes
        _receipt_bytes(receipt)
    reader = _Reader(receipt.config_root, Limits(max_file_bytes=_MAX_BYTES))
    try:
        _assert_links(reader, Path(receipt.config_root), receipt.source_links or ())
        for index, (path, digest, device, inode) in enumerate(receipt.source_files):
            if path == '.storage/repairs.issue_registry' and snapshot is not None:
                continue
            raw, info = reader.read(path)
            if (_digest(raw), info.st_dev, info.st_ino) != (digest, device, inode):
                logging.getLogger(__name__).warning(
                    'Validation source changed: index=%d content=%s identity=%s',
                    index, _digest(raw) != digest, (info.st_dev, info.st_ino) != (device, inode))
                raise YAMLValidationError('validation_source_changed')
        for index, (path, expected, names) in enumerate(receipt.source_directories):
            if path == '.storage' and snapshot is not None:
                if names is None:
                    raise YAMLValidationError('checker_snapshot_scope_invalid')
                expected = tuple(row for row in expected if row[0] != 'repairs.issue_registry')
                names = tuple(name for name in names if name != 'repairs.issue_registry')
            fd = os.dup(reader.root) if path == '.' else reader.open(path, directory=True)
            try:
                with os.scandir(fd) as entries:
                    current = tuple(sorted((entry.name, (s := entry.stat(follow_symlinks=False)).st_mode,
                                            s.st_dev, s.st_ino) for entry in entries
                                           if names is None or entry.name in names))
                if current != expected:
                    logging.getLogger(__name__).warning(
                        'Validation directory changed: index=%d', index)
                    raise YAMLValidationError('validation_graph_changed')
            finally:
                os.close(fd)
    except OSError:
        raise YAMLValidationError('validation_source_changed') from None
    finally:
        os.close(reader.root)


async def _settled_executor(hass, function, *args):
    task = asyncio.ensure_future(hass.async_add_executor_job(function, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _async_validate_proposed(hass, plan, scratch_parent, *, timeout=120, with_sources=False,
                                   powercalc_bundle=None, powercalc_candidate_documents=None, native_helpers=(),
                                   template_reference_mapping=None, template_state_program=False,
                                   dependent_reference_mapping=None, registry_only_template=False,
                                   owned_script_services=(), dashboard_yaml=None, dashboard_storage=None,
                                   mixed_powercalc=False):
    """Return only after native validation of the entire proposed graph succeeds.

    scratch_parent must be private local storage OUTSIDE config_root. It contains
    temporary secret material with mode 0700 and is erased on every normal exit.
    A failure never invokes file_mutations or a live reload service.
    """
    if dashboard_yaml is not None:
        from .dashboard_yaml_semantics import RegisteredDashboardYAML
        if type(dashboard_yaml) is not RegisteredDashboardYAML or dashboard_yaml.hass is not hass or not with_sources:
            raise YAMLValidationError('dashboard_yaml_evidence_required')
        dashboard_yaml.assert_plan(plan)
        dashboard_yaml.check()
    if dashboard_storage is not None:
        from .dashboard_storage_semantics import NativeDashboardStorage
        if type(dashboard_storage) is not NativeDashboardStorage or dashboard_storage.hass is not hass or not with_sources:
            raise YAMLValidationError('dashboard_storage_evidence_required')
        if (dashboard_yaml is not None and
                dashboard_storage.mapping_items != dashboard_yaml.mapping_items):
            raise YAMLValidationError('dashboard_mapping_mismatch')
        dashboard_storage.check()
    if (type(registry_only_template) is not bool or registry_only_template
            and (not template_state_program or plan.patches or dependent_reference_mapping is not None)):
        raise YAMLValidationError('template_registry_only_scope_required')
    if (type(owned_script_services) is not tuple or len(owned_script_services) > 1
            or owned_script_services and (not template_state_program or dependent_reference_mapping is None
                or any(value not in dependent_reference_mapping for value in owned_script_services))):
        raise YAMLValidationError('owned_script_service_scope_required')
    if type(template_state_program) is not bool or template_state_program and template_reference_mapping is None:
        raise YAMLValidationError('template_state_source_profile_scope_required')
    if (type(mixed_powercalc) is not bool or mixed_powercalc and
            (not with_sources or not template_state_program or powercalc_bundle is None
             or powercalc_candidate_documents is None or plan.powercalc_inputs is None
             or dependent_reference_mapping is None or dependent_reference_mapping != template_reference_mapping
             or registry_only_template)):
        raise YAMLValidationError('mixed_powercalc_profile_scope_required')
    if dependent_reference_mapping is not None:
        from .yaml_plan import _mapping
        if (not with_sources or native_helpers and not (mixed_powercalc or template_state_program) or powercalc_bundle is not None and not mixed_powercalc
                or template_reference_mapping is not None
                and dependent_reference_mapping != template_reference_mapping):
            raise YAMLValidationError('dependent_config_program_scope_required')
        dependent_reference_mapping = _mapping(dependent_reference_mapping)
    if template_reference_mapping is not None:
        from .yaml_plan import _mapping
        if not with_sources or native_helpers and not (mixed_powercalc or template_state_program) or powercalc_bundle is not None and not mixed_powercalc:
            raise YAMLValidationError('template_state_source_profile_scope_required')
        # Detach mutable caller data before the first staging/executor await.
        template_reference_mapping = _mapping(template_reference_mapping)
        items = tuple(sorted(template_reference_mapping.items()))
        if ((dashboard_yaml is not None and dashboard_yaml.mapping_items != items)
                or (dashboard_storage is not None and dashboard_storage.mapping_items != items)):
            raise YAMLValidationError('dashboard_mapping_mismatch')
    if powercalc_candidate_documents is not None:
        from .powercalc_candidate_sources import PlannedPowercalcDocuments
        if (not with_sources or powercalc_bundle is None or plan.powercalc_inputs is None
                or type(powercalc_candidate_documents) is not PlannedPowercalcDocuments):
            raise YAMLValidationError('powercalc_candidate_profile_scope_required')
    if native_helpers:
        from .prepared import canonical, decode_change
        from .helper_transaction import PreparedHelper
        from .yaml_validation_worker import template_entry_seal
        if not with_sources or type(native_helpers) is not tuple:
            raise YAMLValidationError('native_helpers_private_source_scope_required')
        if any(type(row) is not tuple or len(row) != 3 for row in native_helpers):
            raise YAMLValidationError('native_helpers_preparation_changed')
        for ref, digest, payload in native_helpers:
            change = decode_change(payload)
            if type(change) is not PreparedHelper or canonical(payload)[1] != digest:
                raise YAMLValidationError('native_helpers_preparation_changed')
            change.plan()
            if template_state_program and (len(native_helpers) != 1 or len(change.mapping) != 1
                    or not set(change.mapping.items()) <= set(template_reference_mapping.items())):
                raise YAMLValidationError('controlled_entry_mapping_mismatch')
        # Freeze mutable payloads across ALL staging/worker awaits.
        native_helpers = tuple((ref,digest,canonical(payload)[0])
                               for ref,digest,payload in native_helpers)
    root = Path(hass.config.config_dir).resolve()
    scratch = Path(scratch_parent).resolve()
    if scratch == root or root in scratch.parents:
        raise YAMLValidationError('scratch_must_be_outside_config')
    try:
        with tempfile.TemporaryDirectory(prefix='renamer-validation-', dir=scratch) as stage:
            before, after = Path(stage) / 'before', Path(stage) / 'after'
            before.mkdir(mode=0o700)
            after.mkdir(mode=0o700)
            staged = await _settled_executor(hass, _stage, root, plan, before, after,
                                             with_sources, native_helpers, registry_only_template, dashboard_yaml,
                                             dashboard_storage, mixed_powercalc)
            if native_helpers and template_state_program and not mixed_powercalc:
                from homeassistant.const import __version__
                from .controlled_template_entry_candidate import candidate
                from .prepared import decode_change
                path = after / '.storage/core.config_entries'
                document = candidate((before / '.storage/core.config_entries').read_bytes(),
                                     __version__, decode_change(native_helpers[0][2]))
                path.write_text(json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False))
            if dashboard_yaml is not None:
                dashboard_yaml.check()
            if dashboard_storage is not None:
                dashboard_storage.check()
            sources, directories = staged[:2]
            if powercalc_bundle is not None:
                from .powercalc_source_binding import stage_powercalc_bundle
                await _settled_executor(hass, stage_powercalc_bundle, powercalc_bundle, before, after)
            if powercalc_candidate_documents is not None:
                from .powercalc_candidate_sources import stage_candidate_documents
                await _settled_executor(hass, stage_candidate_documents,
                    powercalc_candidate_documents, before, after)
            helper_args = ()
            if native_helpers:
                request = Path(stage)/'native-helpers.json'
                encoded = json.dumps(native_helpers,sort_keys=True,separators=(',',':'),allow_nan=False)
                if len(encoded.encode()) > 16_000_000:
                    raise YAMLValidationError('native_helpers_request_limit')
                request.write_text(encoded)
                request.chmod(0o600)
                helper_args = ('--native-helpers',str(request))
            state_source_args = ()
            dependent_args = ()
            if dependent_reference_mapping is not None:
                request = Path(stage) / 'dependent-reference-mapping.json'
                request.write_text(json.dumps(dependent_reference_mapping,
                    sort_keys=True, separators=(',', ':'), allow_nan=False))
                request.chmod(0o600)
                dependent_args = ('--dependent-config-program', '--dependent-reference-mapping', str(request))
            if template_reference_mapping is not None:
                request = Path(stage) / 'template-reference-mapping.json'
                request.write_text(json.dumps(template_reference_mapping,
                    sort_keys=True, separators=(',', ':'), allow_nan=False))
                request.chmod(0o600)
                state_source_args = ('--template-state-source-v3',
                                     '--template-reference-mapping', str(request))
                if template_state_program:
                    state_source_args += ('--template-state-config-program',)
                if registry_only_template:
                    state_source_args += ('--template-state-registry-only',)
                if owned_script_services:
                    request = Path(stage) / 'owned-script-services.json'
                    request.write_text(json.dumps(list(owned_script_services), separators=(',', ':')))
                    request.chmod(0o600)
                    state_source_args += ('--template-owned-script-services', str(request))
            worker = str(Path(__file__).with_name('yaml_validation_worker.py'))
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-I', '-B', worker, str(before), str(after),
                *helper_args,
                *state_source_args,
                *dependent_args,
                *(('--source-evidence',) if with_sources else ()),
                *(('--qualified-mixed-powercalc',) if mixed_powercalc else ()),
                *(('--powercalc-profile-inputs',) if plan.powercalc_inputs is not None else ()),
                *(('--powercalc-candidate-inputs',) if powercalc_candidate_documents is not None else ()),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True)
            try:
                async with asyncio.timeout(timeout):
                    stdout, _ = await process.communicate()
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            try:
                result = json.loads(stdout)
            except (ValueError, UnicodeError):
                raise YAMLValidationError('native_checker_failed') from None
            if process.returncode or result.get('status') != 'valid':
                from .yaml_validation_worker import checker_failure_note
                error = YAMLValidationError('native_config_rejected')
                error.add_note(checker_failure_note(result, process.returncode))
                raise error
            if native_helpers:
                expected = template_entry_seal(native_helpers,result['version'])
                received = [tuple(row) for row in result.get('runtime_expected',())
                            if row[0] == 'template.config_entries']
                if received != [expected]:
                    raise YAMLValidationError('native_helpers_validation_seal_mismatch')
            if ('template' in result['changed_domains']
                    and (not with_sources or staged[3].findings
                         or any(row.domain not in (('template.state', 'template.sensor', 'template.binary_sensor', 'template.switch') if template_state_program else ('template.state', 'template.sensor', 'template.binary_sensor'))
                                for row in staged[3].definitions))):
                result['unsupported_domains'] = sorted(
                    set(result['unsupported_domains']) | {'template'})
            receipt = ValidationReceipt(str(root), sources, directories,
                tuple(sorted((patch.path, _digest(patch.proposed_bytes)) for patch in plan.patches)),
                tuple(result['changed_domains']), tuple(result['unsupported_domains']), result['version'],
                tuple(tuple(row) for row in result.get('runtime_expected', ())),
                _checker_repairs_snapshot(plan, before, directories, dashboard_yaml, dashboard_storage),
                staged[-1] or None)
            await _settled_executor(hass, assert_validation_current, receipt)
            if dashboard_yaml is not None:
                dashboard_yaml.check()
                await _settled_executor(hass, dashboard_yaml.fence.physical)
                dashboard_yaml.check()
            if dashboard_storage is not None:
                dashboard_storage.check()
                await _settled_executor(hass, dashboard_storage.fence.physical)
                dashboard_storage.check()
            if with_sources:
                from .yaml_source_binding import seal_original_sources
                evidence = seal_original_sources(receipt, plan, staged[2], result['source_runtime'],
                    template_ledger=staged[3], template_native=result['template_sources'],
                    template_candidate_native=result['template_candidate_sources'])
                if powercalc_bundle is not None:
                    from dataclasses import replace
                    from .powercalc_source_binding import seal_powercalc_sources
                    original = seal_powercalc_sources(receipt, plan, staged[4], result['powercalc_sources'])
                    evidence = replace(evidence, powercalc_sources=original)
                    if powercalc_candidate_documents is not None:
                        from .powercalc_candidate_sources import seal_powercalc_candidate
                        candidate = seal_powercalc_candidate(receipt, plan, original,
                            powercalc_candidate_documents, result['powercalc_candidate_sources'],
                            result['powercalc_candidate_documents'], result.get('powercalc_candidate_energy'))
                        evidence = replace(evidence, powercalc_candidate_sources=candidate)
                dependent_program = None
                if dependent_reference_mapping is not None:
                    from .dependent_config_program import seal_dependent_config_program
                    dependent_program = seal_dependent_config_program(receipt, evidence,
                        result['dependent_config_program'], plan=plan,
                        mapping=dependent_reference_mapping, source_order=tuple(sorted(dependent_reference_mapping)))
                if template_reference_mapping is not None:
                    from .template_source_evidence import (seal_template_state_sources,
                        seal_template_state_candidate)
                    original = seal_template_state_sources(receipt, staged[3],
                        result['template_state_source_profile'], plan=plan,
                        mapping=template_reference_mapping)
                    candidate = seal_template_state_candidate(receipt, original,
                        result['template_candidate_state_source_profile'], plan=plan,
                        mapping=template_reference_mapping)
                    candidate.assert_original(receipt, original)
                    program = None
                    if template_state_program:
                        from .template_state_program import seal_state_config_program
                        program = seal_state_config_program(original, candidate,
                            result['template_state_config_program'],
                            result['template_candidate_state_config_program'],
                            mapping=template_reference_mapping, plan=plan)
                    external = result.get('external_source_proof', [])
                    if (type(external) is not list or any(type(row) is not list or len(row) != 2
                            or row[0] not in ('homekit', 'utility_meter') or type(row[1]) is not bool
                            for row in external)
                            or tuple(row[0] for row in external) != tuple(
                                domain for domain in ('homekit', 'utility_meter') if domain in receipt.changed_domains)):
                        raise YAMLValidationError('external_source_proof_invalid')
                    return TemplateStateSourceValidation(receipt, evidence, original, candidate, program,
                                                         dependent_program, tuple(tuple(row) for row in external))
                if dependent_program is not None:
                    return DependentConfigValidation(receipt, evidence, dependent_program)
                return receipt, evidence
            return receipt
    except (OSError, TimeoutError):
        raise YAMLValidationError('native_checker_unavailable') from None


async def async_validate_proposed(hass, plan, scratch_parent, *, timeout=120):
    """Original validation API; receipt layout and projection hashes unchanged."""
    return await _async_validate_proposed(hass, plan, scratch_parent, timeout=timeout)


async def async_validate_proposed_with_sources(hass, plan, scratch_parent, *, timeout=120, native_helpers=(),
                                               dashboard_yaml=None, dashboard_storage=None):
    """Same ORIGINAL staging/checker, plus detached source evidence for preparation.

    This server API never creates a second ValidationReceipt or gains mutation
    authority. The normal receipt/CAS remains independently required by apply.
    """
    return await _async_validate_proposed(hass, plan, scratch_parent, timeout=timeout,
                                         with_sources=True, native_helpers=native_helpers,
                                         dashboard_yaml=dashboard_yaml, dashboard_storage=dashboard_storage)


async def async_validate_proposed_with_dependent_config_program(hass, plan, mapping, scratch_parent, *, timeout=120):
    """Compile all frozen source prefixes without changing legacy receipt bytes."""
    return await _async_validate_proposed(hass, plan, scratch_parent, timeout=timeout,
        with_sources=True, dependent_reference_mapping=mapping)


async def async_validate_proposed_with_template_state_sources(
        hass, plan, mapping, scratch_parent, *, timeout=120, with_state_program=False,
        with_dependent_program=False, registry_only_template=False, owned_script_services=(),
        dashboard_yaml=None, dashboard_storage=None, native_helpers=()):
    """Explicit internal full-state source preflight; no readiness/activation.

    Same staging, isolated native checker, receipt and source/directory fences.
    This opt-in does not replace the legacy receipt or its old reload semantics.
    """
    if type(with_dependent_program) is not bool:
        raise YAMLValidationError('dependent_config_program_scope_required')
    return await _async_validate_proposed(hass, plan, scratch_parent, timeout=timeout,
        with_sources=True, template_reference_mapping=mapping, template_state_program=with_state_program,
        dependent_reference_mapping=mapping if with_dependent_program else None,
        registry_only_template=registry_only_template, owned_script_services=owned_script_services,
        dashboard_yaml=dashboard_yaml, dashboard_storage=dashboard_storage, native_helpers=native_helpers)


def assert_proposal_matches(receipt, plan):
    expected = tuple(sorted((patch.path, _digest(patch.proposed_bytes)) for patch in plan.patches))
    if not plan.ready or expected != receipt.proposed_files:
        raise YAMLValidationError('validated_proposal_mismatch')
    if (receipt.checker_repairs_snapshot is not None
            and (any(row.path == '.storage/repairs.issue_registry' for row in plan.files)
                 or any(row.path == '.storage' for row in plan.directories))):
        raise YAMLValidationError('checker_snapshot_real_source_conflict')


def assert_activation_available(hass, receipt):
    """Call BEFORE mutation; an unknown domain cannot silently become success."""
    if not receipt.ready_for_apply:
        raise YAMLValidationError('yaml_activation_unsupported')
    for domain in receipt.changed_domains:
        service = 'reload_core_config' if domain == 'homeassistant' else 'reload'
        if not hass.services.has_service(domain, service):
            raise YAMLValidationError('yaml_reload_service_unavailable')


async def async_activate_yaml(hass, receipt, *, context, guard):
    """Reload exactly the affected supported domains AFTER all file readbacks.

    Service completion is dispatch evidence only. Native reload handlers can log
    errors and return normally; owner MUST independently verify consumers before
    declaring transaction success. Partial reload failure is recovery-required.
    Never await hass.async_block_till_done() while editor locks are held.
    """
    guard()
    assert_activation_available(hass, receipt)
    completed = []
    try:
        for domain in receipt.changed_domains:
            guard()
            service = 'reload_core_config' if domain == 'homeassistant' else 'reload'
            await hass.services.async_call(domain, service, {}, blocking=True, context=context)
            completed.append((domain, service))
            guard()
    except Exception:
        raise YAMLValidationError('yaml_activation_unconfirmed') from None
    return {'status': 'reloads_completed_readback_required', 'services': tuple(completed)}


async def async_validate_proposed_with_powercalc_sources(hass, plan, scratch_parent, *, timeout=120,
                                                         candidate_documents=None):
    """Exact ORIGINAL checker plus the supported official vendor source bundle.

    Read-only prerequisite. Unsupported Powercalc apply stays unsupported.
    """
    from .powercalc_source_binding import PowercalcVendor
    vendor = PowercalcVendor(hass, with_profiles=plan.powercalc_inputs is not None)
    bundle = await _settled_executor(hass, vendor.sources)
    vendor.bindings()
    result = await _async_validate_proposed(hass, plan, scratch_parent, timeout=timeout,
                                          with_sources=True, powercalc_bundle=bundle,
                                          powercalc_candidate_documents=candidate_documents)
    def final_sources():
        vendor.sources()
        # SAME ORIGINAL receipt closes the final vendor-code executor await.
        assert_validation_current(result[0])
    await _settled_executor(hass, final_sources)
    vendor.bindings()
    return result


async def async_validate_mixed_powercalc_template_sources(
        hass, plan, mapping, scratch_parent, candidate_documents, *, timeout=120,
        owned_script_services=(), dashboard_yaml=None, dashboard_storage=None, native_helpers=()):
    """One checker invocation and receipt for both explicitly qualified profiles.

    This validates source evidence only; it grants no mutation or lifecycle capability.
    """
    from .powercalc_source_binding import PowercalcVendor
    vendor = PowercalcVendor(hass, with_profiles=True)
    bundle = await _settled_executor(hass, vendor.sources)
    vendor.bindings()
    result = await _async_validate_proposed(hass, plan, scratch_parent, timeout=timeout,
        with_sources=True, powercalc_bundle=bundle, powercalc_candidate_documents=candidate_documents,
        template_reference_mapping=mapping, template_state_program=True,
        dependent_reference_mapping=mapping, owned_script_services=owned_script_services,
        dashboard_yaml=dashboard_yaml, dashboard_storage=dashboard_storage, mixed_powercalc=True,
        native_helpers=native_helpers)
    def final_sources():
        vendor.sources()
        assert_validation_current(result.receipt)
    await _settled_executor(hass, final_sources)
    vendor.bindings()
    return result
