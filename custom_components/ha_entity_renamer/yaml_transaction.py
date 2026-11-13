"""Private, conservative YAML saga step. No mutation endpoint is installed.

One durable coordinator intent spans the whole validated graph and activation.
Mixed files/runtime are UNKNOWN, never a resumable native subset. The owner must
supply an independent native runtime observer for every affected consumer and
inventory/continuity verification. A reload ACK is never this evidence.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Protocol

from .file_mutations import apply_file_patch
from .transaction import Position, Refused
from .yaml_plan import Edit, FilePatch, FileSnapshot, Limits, _Reader, _relative
from .yaml_validation import (ValidationReceipt, _settled_executor,
    assert_activation_available, assert_proposal_matches, assert_validation_current,
    async_activate_yaml, async_validate_proposed)

_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_SUPPORTED = frozenset(('automation', 'script', 'scene', 'homeassistant', 'template'))


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return _hash(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def _path(value):
    path = _relative(value)
    if path != value:
        raise ValueError('noncanonical_prepared_yaml_path')
    return path


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError('invalid_prepared_yaml')


def _text(value):
    if type(value) is not str or not value or '\x00' in value:
        raise ValueError('invalid_prepared_yaml')
    return value


def _sha(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ValueError('invalid_prepared_yaml_digest')
    return value


def _number(value):
    if type(value) is not int or value < 0:
        raise ValueError('invalid_prepared_yaml_identity')
    return value


@dataclass(frozen=True)
class YAMLFile:
    path: str
    before_hex: str
    after_hex: str
    device: int
    inode: int

    @property
    def before(self):
        return bytes.fromhex(self.before_hex)

    @property
    def after(self):
        return bytes.fromhex(self.after_hex)


@dataclass(frozen=True)
class PreparedYAML:
    receipt: ValidationReceipt
    files: tuple[YAMLFile, ...]

    @property
    def identity(self):
        payload = asdict(self)
        if not self.receipt.runtime_expected:
            # Preserve identities of durable preparations created before this field.
            payload['receipt'].pop('runtime_expected', None)
        if self.receipt.checker_repairs_snapshot is None:
            payload['receipt'].pop('checker_repairs_snapshot', None)
        return 'yaml:' + _digest(payload)

    @property
    def before_revision(self):
        return _digest(tuple((f.path, _hash(f.before)) for f in self.files))

    @property
    def after_revision(self):
        return _digest(tuple((f.path, _hash(f.after)) for f in self.files))

    def checked(self):
        # One codec defines both trusted creation and crash-recovery validation.
        decoded = decode_yaml(asdict(self))
        if decoded != self:
            raise ValueError('noncanonical_prepared_yaml')
        return self


def decode_yaml(data):
    """Legacy codec: selective state preparations never enter this gate."""
    return _decode_yaml(data)


def _assert_mixed_state_scope(receipt, *, allow_dependent_only=False, allow_template_entry=False):
    """Private codec scope only; full Template source proof lives in composition.

    Template may retain genuine missing legacy projections. Automation/script
    still require the same exact checker seals as the legacy mixed profile.
    """
    domains, expected = receipt.changed_domains, receipt.runtime_expected
    if allow_template_entry:
        entries = tuple(row for row in expected if row[0] == 'template.config_entries')
        if len(entries) != 1 or len(entries[0]) != 3:
            raise ValueError('template_entry_seal_required')
        for digest in entries[0][1:]:
            _sha(digest)
        expected = tuple(row for row in expected if row[0] != 'template.config_entries')
    dependent_only = allow_dependent_only and domains in (('script',), ('automation',), ('automation', 'script'))
    if (type(allow_dependent_only) is not bool or type(domains) is not tuple
            or not (dependent_only or len(domains) in (2, 3) and 'template' in domains)
            or any(type(domain) is not str for domain in domains)
            or set(domains) - {'automation', 'script', 'template'}
            or domains != tuple(sorted(set(domains)))
            or type(expected) is not tuple
            or any(type(row) is not tuple or len(row) != 3
                   or type(row[0]) is not str for row in expected)
            or tuple(row[0] for row in expected) != domains
            or type(receipt.unsupported_domains) is not tuple
            or receipt.unsupported_domains not in ((), ('template',))):
        raise ValueError('template_mixed_state_scope_required')
    for domain, before, after in expected:
        if domain != 'template' and (before is None or after is None or before == after):
            raise ValueError('template_mixed_state_runtime_required')
        for value in (before, after):
            if value is not None:
                _sha(value)


def _assert_external_state_scope(receipt, *, allow_powercalc=False):
    """Private evidence codec only: external activation remains unsupported."""
    domains, expected = receipt.changed_domains, receipt.runtime_expected
    external = set(domains) & {'homekit', 'utility_meter'}
    powercalc = {'powercalc'} if allow_powercalc else set()
    if (type(allow_powercalc) is not bool or not external
            or allow_powercalc and 'powercalc' not in domains or domains != tuple(sorted(set(domains)))
            or set(domains) - ({'automation', 'script', 'template', 'homekit', 'utility_meter'} | powercalc)
            or not set(receipt.unsupported_domains) <= set(domains)
            or set(receipt.unsupported_domains) != external | powercalc | (set(receipt.unsupported_domains) & {'template'})
            or type(expected) is not tuple
            or any(type(row) is not tuple or len(row) != 3 or type(row[0]) is not str for row in expected)
            or tuple(row[0] for row in expected if row[0] != 'template.config_entries') != domains
            or sum(row[0] == 'template.config_entries' for row in expected) > 1
            or any(row[0] == 'template.config_entries' for row in expected) and 'template' not in domains):
        raise ValueError('external_state_scope_required')
    for domain, before, after in expected:
        if domain not in ('template', 'powercalc') and (before is None or after is None or before == after):
            raise ValueError('external_state_runtime_required')
        for value in (before, after):
            if value is not None:
                _sha(value)


def _decode_yaml(data, *, state_mode=False, mixed_state_mode=False, dependent_owner_mode=False,
                 powercalc_mode=False, mixed_powercalc_mode=False, external_state_mode=False):
    """Shared bounded file codec, with explicit disjoint selective gates."""
    combined_external = mixed_powercalc_mode and external_state_mode
    if (sum(bool(flag) for flag in (state_mode, mixed_state_mode, powercalc_mode, mixed_powercalc_mode, external_state_mode)) > (2 if combined_external else 1)
            or dependent_owner_mode and not (mixed_state_mode or external_state_mode)):
        raise ValueError('ambiguous_prepared_yaml_mode')
    _keys(data, ('receipt', 'files'))
    raw = data['receipt']
    receipt_keys = ('config_root', 'source_files', 'source_directories', 'proposed_files',
                'changed_domains', 'unsupported_domains', 'version')
    # Old durable preparations remain decodable, but lack runtime proof.
    _keys(raw, receipt_keys + (('runtime_expected',) if 'runtime_expected' in raw else ())
          + (('checker_repairs_snapshot',) if 'checker_repairs_snapshot' in raw else ()))
    root = _text(raw['config_root'])
    if not Path(root).is_absolute() or str(Path(root)) != root or '..' in Path(root).parts:
        raise ValueError('invalid_prepared_yaml_root')
    def sequence(value, maximum):
        if type(value) not in (tuple, list) or len(value) > maximum:
            raise ValueError('invalid_prepared_yaml_sequence')
        return value
    sources = []
    for row in sequence(raw['source_files'], 10000):
        if type(row) not in (tuple, list) or len(row) != 4:
            raise ValueError('invalid_prepared_yaml_source')
        path, sha, dev, ino = row
        sources.append((_path(path), _sha(sha), _number(dev), _number(ino)))
    if len({r[0] for r in sources}) != len(sources) or not sources:
        raise ValueError('invalid_prepared_yaml_sources')
    directories = []
    for row in sequence(raw['source_directories'], 10000):
        if type(row) not in (tuple, list) or len(row) != 3:
            raise ValueError('invalid_prepared_yaml_directory')
        path, entries, names = row
        path = '.' if path == '.' else _path(path)
        checked_entries = []
        for entry in sequence(entries, 10000):
            if type(entry) not in (tuple, list) or len(entry) != 4:
                raise ValueError('invalid_prepared_yaml_directory')
            name, mode, dev, ino = entry
            if '/' in _text(name) or name in ('.', '..') or '\\' in name:
                raise ValueError('invalid_prepared_yaml_directory')
            checked_entries.append((name, _number(mode), _number(dev), _number(ino)))
        if len({e[0] for e in checked_entries}) != len(checked_entries):
            raise ValueError('invalid_prepared_yaml_directory')
        if names is not None:
            names = tuple(sequence(names, 10000))
            if any(type(n) is not str or not n or '/' in n or '\\' in n or '\x00' in n or n in ('.', '..') for n in names):
                raise ValueError('invalid_prepared_yaml_directory')
            if len(set(names)) != len(names):
                raise ValueError('invalid_prepared_yaml_directory')
        directories.append((path, tuple(checked_entries), names))
    if len({r[0] for r in directories}) != len(directories) or not directories:
        raise ValueError('invalid_prepared_yaml_directories')
    proposed = []
    for row in sequence(raw['proposed_files'], 512):
        if type(row) not in (tuple, list) or len(row) != 2:
            raise ValueError('invalid_prepared_yaml_proposal')
        proposed.append((_path(row[0]), _sha(row[1])))
    domains = tuple(sequence(raw['changed_domains'], 6 if combined_external else 5 if external_state_mode else 4))
    allowed = (_SUPPORTED | {'homekit', 'utility_meter', 'powercalc'} if combined_external else
               _SUPPORTED | {'homekit', 'utility_meter'} if external_state_mode else
               _SUPPORTED | {'powercalc'} if powercalc_mode or mixed_powercalc_mode else _SUPPORTED)
    unsupported = sequence(raw['unsupported_domains'], 4 if combined_external else 3 if external_state_mode else 2 if mixed_powercalc_mode else
                           1 if (state_mode or mixed_state_mode or powercalc_mode) else 0)
    if (not domains or any(type(d) is not str or d not in allowed for d in domains)
            or len(set(domains)) != len(domains)
            or (powercalc_mode and (domains != ('powercalc',) or tuple(unsupported) != ('powercalc',)))
            or ((state_mode or mixed_state_mode) and tuple(unsupported) not in ((), ('template',)))
            or (mixed_powercalc_mode and ('powercalc' not in domains
                or (not combined_external and set(unsupported) not in ({'powercalc'}, {'powercalc', 'template'}))
                or domains != tuple(sorted(domains))))
            or (external_state_mode and (any(type(value) is not str for value in unsupported)
                or tuple(unsupported) != tuple(sorted(set(unsupported)))
                or not set(domains) & {'homekit', 'utility_meter'}
                or set(unsupported) != ((set(domains) & {'homekit', 'utility_meter'})
                                        | (set(unsupported) & {'template'}) | ({'powercalc'} if combined_external else set()))
                or domains != tuple(sorted(domains))))
            or (not (state_mode or mixed_state_mode or powercalc_mode or mixed_powercalc_mode or external_state_mode) and unsupported)):
        raise ValueError('unsupported_prepared_yaml_activation')
    files, total = [], 0
    for row in sequence(data['files'], 512):
        _keys(row, ('path', 'before_hex', 'after_hex', 'device', 'inode'))
        path = _path(row['path'])
        encoded = (row['before_hex'], row['after_hex'])
        if any(type(x) is not str or len(x) > 4_000_000 or not re.fullmatch(r'(?:[0-9a-f]{2})*', x) for x in encoded):
            raise ValueError('invalid_prepared_yaml_bytes')
        before, after = (bytes.fromhex(x) for x in encoded)
        total += len(before) + len(after)
        if before == after or total > 32_000_000:
            raise ValueError('invalid_prepared_yaml_bytes')
        files.append(YAMLFile(path, *encoded, _number(row['device']), _number(row['inode'])))
    if not files or len({f.path for f in files}) != len(files):
        raise ValueError('invalid_prepared_yaml_files')
    if (raw.get('checker_repairs_snapshot') is not None
            and any(f.path == '.storage/repairs.issue_registry' for f in files)):
        raise ValueError('checker_snapshot_real_source_conflict')
    source_map = {r[0]: r[1:] for r in sources}
    if any(source_map.get(f.path) != (_hash(f.before), f.device, f.inode) for f in files):
        raise ValueError('prepared_yaml_source_mismatch')
    if tuple(sorted(proposed)) != tuple(sorted((f.path, _hash(f.after)) for f in files)):
        raise ValueError('prepared_yaml_proposal_mismatch')
    runtime_expected = []
    for row in sequence(raw.get('runtime_expected', ()), 7 if combined_external else 6 if external_state_mode else 5 if mixed_powercalc_mode else 4):
        if (type(row) not in (tuple, list) or len(row) != 3
                or row[0] not in domains and not ((mixed_powercalc_mode or mixed_state_mode or external_state_mode) and row[0] == 'template.config_entries')):
            raise ValueError('invalid_prepared_yaml_runtime')
        domain, before, after = row
        runtime_expected.append((domain, None if before is None else _sha(before),
                                 None if after is None else _sha(after)))
    if len({row[0] for row in runtime_expected}) != len(runtime_expected):
        raise ValueError('invalid_prepared_yaml_runtime')
    if state_mode and domains != ('template',):
        raise ValueError('template_state_only_preparation_required')
    if not state_mode and not mixed_state_mode and not mixed_powercalc_mode and not external_state_mode and 'template' in domains:
        # Parsing one aggregate ordinary YAML change does not grant activation.
        # The receipted adapter still requires its exact typed composite observer
        # and ORIGINAL composition. Every changed domain needs checker seals;
        # missing/ambiguous runtime coverage remains rejected before any effect.
        if (set(domains) - {'automation', 'script', 'template'}
                or domains != tuple(sorted(domains))
                or tuple(row[0] for row in runtime_expected) != domains
                or any(None in row[1:] or row[1] == row[2] for row in runtime_expected)):
            raise ValueError('template_prepared_runtime_subset_required')
    snapshot = raw.get('checker_repairs_snapshot')
    if snapshot is not None:
        if type(snapshot) not in (list, tuple):
            raise ValueError('invalid_checker_repairs_snapshot')
        snapshot = tuple(snapshot)
    receipt = ValidationReceipt(root, tuple(sources), tuple(directories), tuple(proposed),
                                domains, tuple(raw['unsupported_domains']), _text(raw['version']),
                                tuple(runtime_expected), snapshot)
    if snapshot is not None:
        from .native_input_baseline import _receipt_bytes
        _receipt_bytes(receipt)
    if mixed_state_mode:
        _assert_mixed_state_scope(receipt, allow_dependent_only=dependent_owner_mode,
                                  allow_template_entry=any(row[0] == 'template.config_entries' for row in runtime_expected))
    if external_state_mode:
        _assert_external_state_scope(receipt, allow_powercalc=combined_external)
    if mixed_powercalc_mode:
        from dataclasses import replace
        scoped = replace(receipt, changed_domains=tuple(d for d in domains if d not in ('powercalc', 'homekit', 'utility_meter')),
            unsupported_domains=tuple(d for d in unsupported if d not in ('powercalc', 'homekit', 'utility_meter')),
            runtime_expected=tuple(row for row in runtime_expected
                                   if row[0] not in ('powercalc', 'homekit', 'utility_meter', 'template.config_entries')))
        if not scoped.changed_domains:
            if scoped.unsupported_domains or scoped.runtime_expected:
                raise ValueError('powercalc_only_preparation_required')
        elif scoped.changed_domains == ('template',):
            if scoped.unsupported_domains not in ((), ('template',)):
                raise ValueError('template_state_only_preparation_required')
        else:
            _assert_mixed_state_scope(scoped, allow_dependent_only=True)
    return PreparedYAML(receipt, tuple(files))


def _capture(receipt, plan):
    assert_proposal_matches(receipt, plan)
    assert_validation_current(receipt)
    reader = _Reader(receipt.config_root, Limits())
    try:
        rows = []
        for patch in plan.patches:
            before, info = reader.read(patch.path)
            if _hash(before) != patch.expected_sha256:
                raise Refused('yaml_source_changed')
            rows.append(YAMLFile(patch.path, before.hex(), patch.proposed_bytes.hex(),
                                 info.st_dev, info.st_ino))
        result = PreparedYAML(receipt, tuple(rows)).checked()
        assert_validation_current(receipt)
        return result
    finally:
        os.close(reader.root)


async def prepare_yaml(hass, plan, scratch_parent, *, guard):
    """Trusted planner only; caller persists encode_change(result) before a plan.

    No prepared payload from a request is accepted by the adapter. Preparation
    does not promise freshness; actual effects recheck all inputs under locks.
    """
    guard()
    receipt = await async_validate_proposed(hass, plan, scratch_parent)
    guard()
    assert_activation_available(hass, receipt)
    result = await _settled_executor(hass, _capture, receipt, plan)
    guard()
    return result


def _assert_sources(change, after_paths, restored=False):
    """Fence the entire validation graph, including absence and native inputs.

    Only explicitly completed owned files may have new inodes. Read and verify
    their actual bytes before using actual identity for directory comparison;
    never project expected bytes over a foreign current edit. Unchanged sources
    retain the exact original byte/device/inode CAS. Restored allows known own
    inode replacements after a completed forward operation and explicit undo.
    """
    receipt = change.receipt
    files = {f.path: f for f in change.files}
    reader = _Reader(receipt.config_root, Limits(max_file_bytes=128_000_000))
    owned = {}
    try:
        for path, digest, device, inode in receipt.source_files:
            raw, info = reader.read(path)
            replacement = files.get(path)
            if replacement is not None and (path in after_paths or restored):
                expected = replacement.after if path in after_paths else replacement.before
                if raw != expected or info.st_dev != device:
                    raise Refused('yaml_owned_source_conflict')
                owned[path] = (info.st_mode, info.st_dev, info.st_ino)
            elif (_hash(raw), info.st_dev, info.st_ino) != (digest, device, inode):
                raise Refused('yaml_validation_source_conflict')
        for path, expected, names in receipt.source_directories:
            fd = os.dup(reader.root) if path == '.' else reader.open(path, directory=True)
            try:
                with os.scandir(fd) as entries:
                    actual = tuple(sorted((entry.name, (s := entry.stat(follow_symlinks=False)).st_mode,
                                           s.st_dev, s.st_ino) for entry in entries
                                          if names is None or entry.name in names))
                actual_entries = {name: (mode, dev, ino) for name, mode, dev, ino in actual}
                adjusted = []
                for name, mode, dev, ino in expected:
                    child = name if path == '.' else path + '/' + name
                    if child in owned:
                        actual_mode, actual_dev, _ = owned[child]
                        entry = actual_entries.get(name)
                        if ((actual_mode, actual_dev) != (mode, dev)
                                or entry is None or entry[:2] != (mode, dev)):
                            raise Refused('yaml_owned_metadata_conflict')
                        # Owned bytes have already been read through anchored
                        # nofollow descriptors. Their replacement inode is not
                        # an original CAS identity. Directory membership uses
                        # the identity observed by that directory enumeration;
                        # do not compare it to a different cached descriptor
                        # identity after our replace. Unchanged sources retain
                        # the exact original device/inode/hash checks above.
                        ino = entry[2]
                    adjusted.append((name, mode, dev, ino))
                if actual != tuple(sorted(adjusted)):
                    raise Refused('yaml_validation_graph_conflict')
            finally:
                os.close(fd)
    finally:
        os.close(reader.root)


class NativeYAMLRuntimeObserver(Protocol):
    async def position(self, change: PreparedYAML) -> Position:
        """Read native consumers, including settled listener/reload state.

        BEFORE/AFTER require every covered domain's independent runtime proof;
        return UNKNOWN for absent/ambiguous evidence. Never infer from disk/ACK.
        """
    async def verify_inventory(self, plan, *, rollback: bool) -> bool:
        """Full declared inventory, dependent consumers and historical continuity."""


async def _settled(awaitable):
    """Drain native activation before releasing editor locks, even on cancel."""
    task = asyncio.ensure_future(awaitable)
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


class YAMLTransactionAdapter:
    """Register privately as 'yaml'; owner supplies native observer and leases.

    resolver resolves only decode_change(PreparedStore.resolve(id, digest)).
    Locks and external process lease must be shared with all other YAML writers.
    This adapter accepts a single YAML step per operation (one whole graph).
    """
    def __init__(self, hass, resolver, editor_locks, observer: NativeYAMLRuntimeObserver, *, context, runtime_version=None):
        if not callable(getattr(observer, 'position', None)) or not callable(getattr(observer, 'verify_inventory', None)):
            raise ValueError('native_yaml_runtime_observer_required')
        self.hass, self.resolver, self.locks = hass, resolver, editor_locks
        self.observer, self.context = observer, context
        if runtime_version is None:
            from homeassistant.const import __version__
            runtime_version = __version__
        self.runtime_version = runtime_version

    def _accepts_preparation(self, change):
        return type(change) is PreparedYAML

    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        if not self._accepts_preparation(change):
            raise Refused('yaml_preparation_type')
        change.checked()
        if (change.identity != step.identity or change.before_revision != step.before_revision
                or change.after_revision != step.after_revision
                or change.receipt.config_root != str(Path(self.hass.config.config_dir).resolve())
                or change.receipt.version != self.runtime_version):
            raise Refused('yaml_preparation_mismatch')
        self._assert_activation_available(change)
        if 'template' in change.receipt.changed_domains:
            from .native_template_yaml import NativeTemplateYAMLRuntimeReadback
            from .mixed_yaml_runtime import MixedTemplateYAMLRuntimeReadback
            if type(self.observer) not in (NativeTemplateYAMLRuntimeReadback, MixedTemplateYAMLRuntimeReadback):
                raise Refused('template_scoped_runtime_observer_required')
            self.observer.assert_change(change)
        return change

    def _assert_activation_available(self, change):
        assert_activation_available(self.hass, change.receipt)

    async def inspect(self, step):
        change = self._change(step)
        # Coordinator holds its authorization/operation lock. No effects here.
        # Observer must prove quiescence; concurrent edits are fenced at effect.
        position = await self.observer.position(change)
        if position not in (Position.BEFORE, Position.AFTER):
            return Position.UNKNOWN
        try:
            paths = {f.path for f in change.files} if position == Position.AFTER else set()
            # A successfully compensated file has a new inode. Content BEFORE
            # plus independent native BEFORE proves restoration, not write authority.
            await _settled_executor(self.hass, _assert_sources, change, paths, True)
        except (OSError, ValueError, Refused):
            return Position.UNKNOWN
        return position

    async def _write(self, change, guard, *, reverse, _trusted_hooks=None):
        # Private backend seam; never populated from a request or preparation.
        # Legacy callers keep their original source checker and activation path.
        results = []
        def sources():
            if _trusted_hooks is None:
                _assert_sources(change, after_paths, reverse)
            else:
                _trusted_hooks.assert_sources()
        async def checkpoint():
            guard()
            if _trusted_hooks is not None:
                await _trusted_hooks.checkpoint()
                guard()

        all_paths = {f.path for f in change.files}
        after_paths = set(all_paths) if reverse else set()
        # Initial forward CAS requires original identities. Compensation requires
        # exact AFTER bytes on every owned file and unchanged non-owned inputs.
        if _trusted_hooks is None:
            await _settled_executor(self.hass, _assert_sources, change, after_paths)
        else:
            await _settled_executor(self.hass, sources)
        await checkpoint()
        for row in (reversed(change.files) if reverse else change.files):
            await _settled_executor(self.hass, sources)
            await checkpoint()
            def effect(row=row):
                guard()
                sources()
                guard()
                reader = _Reader(change.receipt.config_root, Limits())
                try:
                    current, info = reader.read(row.path)
                finally:
                    os.close(reader.root)
                expected = row.after if reverse else row.before
                if current != expected or (_trusted_hooks is None and not reverse and (info.st_dev, info.st_ino) != (row.device, row.inode)):
                    raise Refused('yaml_write_cas_conflict')
                proposed = row.before if reverse else row.after
                patch = FilePatch(row.path, _hash(current), (Edit(0, len(current), current, proposed),), proposed)
                snapshot = FileSnapshot(row.path, _hash(current), len(current), info.st_dev, info.st_ino)
                # Backup ID is content-bound and distinct for the reverse image.
                operation = 'yaml_' + _digest((change.identity, row.path, reverse))[:64]
                guard()
                # The final guard runs INSIDE apply_file_patch immediately before
                # replacement. Capture its direct return before any async yield.
                def final_guard():
                    guard()
                    if _trusted_hooks is not None:
                        sources()
                result = apply_file_patch(change.receipt.config_root, patch, snapshot, operation, final_guard)
                if _trusted_hooks is not None:
                    _trusted_hooks.capture(result)
                return result
            result = await _settled_executor(self.hass, effect)
            results.append(result)
            await checkpoint()
            if reverse:
                after_paths.remove(row.path)
            else:
                after_paths.add(row.path)
        await _settled_executor(self.hass, sources)
        await checkpoint()
        if _trusted_hooks is None:
            await _settled(async_activate_yaml(self.hass, change.receipt, context=self.context, guard=guard))
        else:
            from .powercalc_yaml_adapter import _PowercalcYAMLHooks
            owner_bound = type(_trusted_hooks) is _PowercalcYAMLHooks
            async def activate_and_settle():
                try:
                    await _trusted_hooks.activate()
                finally:
                    # Service ACK may precede actual platform additions. Keep
                    # OWN started reload descendants inside these editor locks,
                    # including cancellation/error while activate was awaiting.
                    await _trusted_hooks.settle_reload()
            if owner_bound:
                # This typed phase owns ConfigEntry locks on the current task.
                # Its actual native calls are independently shielded/drained.
                await activate_and_settle()
            else:
                await _settled(activate_and_settle())
        await checkpoint()
        if _trusted_hooks is None:
            position = await self.observer.position(change)
        elif owner_bound:
            position = await _trusted_hooks.runtime_position()
        else:
            position = await _settled(_trusted_hooks.runtime_position())
        runtime_check = (self.observer.runtime_check(position)
                         if 'template' in change.receipt.changed_domains else None)
        await checkpoint()
        if position != (Position.BEFORE if reverse else Position.AFTER):
            raise Refused('yaml_native_runtime_unconfirmed')
        await _settled_executor(self.hass, sources)
        await checkpoint()
        if runtime_check is not None:
            runtime_check.check()
        return results

    async def apply(self, step, guard):
        guard()
        change = self._change(step)
        async with self.locks.hold(guard):
            guard()
            if await self.observer.position(change) != Position.BEFORE:
                raise Refused('yaml_native_before_unconfirmed')
            guard()
            return await self._write(change, guard, reverse=False)

    async def compensate(self, step, guard):
        guard()
        change = self._change(step)
        async with self.locks.hold(guard):
            guard()
            if await self.observer.position(change) != Position.AFTER:
                raise Refused('yaml_native_after_unconfirmed')
            guard()
            return await self._write(change, guard, reverse=True)

    async def verify(self, plan):
        return await self._verify(plan, rollback=False)

    async def verify_rollback(self, plan):
        return await self._verify(plan, rollback=True)

    async def _verify(self, plan, *, rollback):
        steps = [step for step in plan.steps if step.adapter == 'yaml']
        if len(steps) != 1:
            return False
        expected = Position.BEFORE if rollback else Position.AFTER
        if await self.inspect(steps[0]) != expected:
            return False
        return await self.observer.verify_inventory(plan, rollback=rollback) is True
