"""Whole-proposal native validation and conservative YAML reload coverage.

Owner holds native editor locks from staging through file mutation. Validation is
an isolated subprocess, never a redirection of the running HA config or loader.
The receipt is ephemeral server state, not an authorization token from a client.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from .yaml_plan import Limits, _Reader, _relative


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

    @property
    def ready_for_apply(self):
        return not self.unsupported_domains


# Ancillary runtime inputs used by the native checker (including blueprint refs).
_STORAGE = {'core.config_entries', 'core.entity_registry', 'core.device_registry',
            'core.area_registry', 'repairs.issue_registry'}
_TREES = {'custom_components', 'blueprints', 'custom_templates'}
_MAX_BYTES = 128_000_000
_MAX_FILES = 10_000


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _stage(config_root, plan, baseline, proposed, with_sources=False):
    reader = _Reader(config_root, Limits(max_file_bytes=_MAX_BYTES))
    try:
        return _stage_owned(reader, plan, baseline, proposed, with_sources=with_sources)
    except (OSError, ValueError) as error:
        if isinstance(error, YAMLValidationError):
            raise
        raise YAMLValidationError('validation_staging_failed') from None
    finally:
        os.close(reader.root)


def _stage_owned(reader, plan, baseline, proposed, *, with_sources=False):
    if not plan.ready or not plan.patches:
        raise YAMLValidationError('yaml_plan_not_ready')
    reachable = {'configuration.yaml'}
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
    def remember_directory(relative):
        # Include graph directories are complete enumerations. Ancestors and
        # storage only guard inputs actually used by the checker, including
        # absent secrets. Unrelated HA logs/traces/backups may change normally.
        names = None
        if relative not in directories:
            names = {'secrets.yaml'}
            prefix = '' if relative == '.' else relative + '/'
            names.update(path[len(prefix):].split('/', 1)[0]
                         for path in paths if path.startswith(prefix))
            if relative == '.':
                names.update(_TREES | {'.storage'})
            elif relative == '.storage':
                names = set(_STORAGE)
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
    # Track absence as well as presence (new secrets, auxiliary dirs or registry files).
    remember_directory('.')
    for path in tuple(paths):
        parent = Path(path).parent
        while parent != Path('.'):
            remember_directory(str(parent))
            parent = parent.parent
    # Copy required auxiliary trees without following symlinks. Bound all input.
    walked = 0
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
                    else:
                        raise YAMLValidationError('unsafe_validation_input')
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
        sources, total = [], 0
        original_bytes = {} if with_sources else None
        for path in sorted(paths):
            raw, info = reader.read(path)
            digest = _digest(raw)
            total += len(raw)
            if total > _MAX_BYTES:
                raise YAMLValidationError('validation_input_limit')
            if path in snapshots:
                snap = snapshots[path]
                if (digest, info.st_dev, info.st_ino) != (snap.sha256, snap.device, snap.inode):
                    raise YAMLValidationError('validation_source_changed')
                if original_bytes is not None:
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
        staged = (tuple(sources), tuple((path, directories[path], directory_names[path])
                                       for path in sorted(directories)))
        if with_sources:
            from .yaml_source_ledger import extract_source_ledger
            return (*staged, extract_source_ledger(plan, original_bytes))
        return staged
    except (OSError, ValueError) as exc:
        if isinstance(exc, YAMLValidationError):
            raise
        raise YAMLValidationError('validation_staging_failed') from None


def assert_validation_current(receipt):
    """Recheck ALL validator inputs immediately before first mutation, under locks."""
    reader = _Reader(receipt.config_root, Limits(max_file_bytes=_MAX_BYTES))
    try:
        for path, digest, device, inode in receipt.source_files:
            raw, info = reader.read(path)
            if (_digest(raw), info.st_dev, info.st_ino) != (digest, device, inode):
                raise YAMLValidationError('validation_source_changed')
        for path, expected, names in receipt.source_directories:
            fd = os.dup(reader.root) if path == '.' else reader.open(path, directory=True)
            try:
                with os.scandir(fd) as entries:
                    current = tuple(sorted((entry.name, (s := entry.stat(follow_symlinks=False)).st_mode,
                                            s.st_dev, s.st_ino) for entry in entries
                                           if names is None or entry.name in names))
                if current != expected:
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


async def _async_validate_proposed(hass, plan, scratch_parent, *, timeout=120, with_sources=False):
    """Return only after native validation of the entire proposed graph succeeds.

    scratch_parent must be private local storage OUTSIDE config_root. It contains
    temporary secret material with mode 0700 and is erased on every normal exit.
    A failure never invokes file_mutations or a live reload service.
    """
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
                                             *((True,) if with_sources else ()))
            sources, directories = staged[:2]
            worker = str(Path(__file__).with_name('yaml_validation_worker.py'))
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-I', worker, str(before), str(after),
                *(('--source-evidence',) if with_sources else ()),
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
                raise YAMLValidationError('native_config_rejected')
            receipt = ValidationReceipt(str(root), sources, directories,
                tuple(sorted((patch.path, _digest(patch.proposed_bytes)) for patch in plan.patches)),
                tuple(result['changed_domains']), tuple(result['unsupported_domains']), result['version'],
                tuple(tuple(row) for row in result.get('runtime_expected', ())))
            await _settled_executor(hass, assert_validation_current, receipt)
            if with_sources:
                from .yaml_source_binding import seal_original_sources
                evidence = seal_original_sources(receipt, plan, staged[2], result['source_runtime'])
                return receipt, evidence
            return receipt
    except (OSError, TimeoutError):
        raise YAMLValidationError('native_checker_unavailable') from None


async def async_validate_proposed(hass, plan, scratch_parent, *, timeout=120):
    """Original validation API; receipt layout and projection hashes unchanged."""
    return await _async_validate_proposed(hass, plan, scratch_parent, timeout=timeout)


async def async_validate_proposed_with_sources(hass, plan, scratch_parent, *, timeout=120):
    """Same ORIGINAL staging/checker, plus detached source evidence for preparation.

    This server API never creates a second ValidationReceipt or gains mutation
    authority. The normal receipt/CAS remains independently required by apply.
    """
    return await _async_validate_proposed(hass, plan, scratch_parent, timeout=timeout,
                                         with_sources=True)


def assert_proposal_matches(receipt, plan):
    expected = tuple(sorted((patch.path, _digest(patch.proposed_bytes)) for patch in plan.patches))
    if not plan.ready or expected != receipt.proposed_files:
        raise YAMLValidationError('validated_proposal_mismatch')


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
