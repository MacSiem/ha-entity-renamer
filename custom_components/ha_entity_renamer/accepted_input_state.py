"""Private, read-only whole-input evidence from one immutable receipt chain.

No refresh receipt, restored inode whitelist, write, reload or recovery effect.
Python objects carry trusted backend evidence, not authorization/provenance.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
import stat

from .native_input_baseline import MAX_FILE_BYTES, _receipt, _receipt_bytes
from .yaml_plan import Limits, _Reader, _relative
from .yaml_validation import assert_validation_current


def _fail(reason):
    from .native_registry_receipts import RegistryReceiptError
    raise RegistryReceiptError('registry_receipt_' + reason) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _entry_map(original):
    result = {}
    for directory, entries, _ in original.source_directories:
        for name, mode, dev, ino in entries:
            path = name if directory == '.' else directory + '/' + name
            value = (dev, ino, mode)
            if path in result and result[path] != value:
                _fail('original_graph_inconsistent')
            result[path] = value
    return result


def validate_yaml_metadata(original, change, metadata):
    """Validate original-fenced stat capture; never infer uid/gid from later IO."""
    if type(metadata) is not dict or set(metadata) != {row.path for row in change.files}:
        _fail('yaml_original_metadata_required')
    _assert_backup_membership(original, change)
    entries = _entry_map(original)
    sources = {row[0]: row[1:] for row in original.source_files}
    result = {}
    for row in change.files:
        value = metadata[row.path]
        if (type(value) not in (list, tuple) or len(value) != 5
                or any(type(n) is not int or n < 0 for n in value)
                or value[1] == 0 or not stat.S_ISREG(value[2])
                or tuple(value[:3]) != entries.get(row.path)
                or sources.get(row.path) != (_sha(row.before), row.device, row.inode)
                or tuple(value[:2]) != (row.device, row.inode)):
            _fail('yaml_original_metadata_mismatch')
        result[row.path] = list(value)
    return result


def _assert_backup_membership(original, change):
    """Reject observed new backup members BEFORE an effect, never allowlist them."""
    from .yaml_transaction import _digest
    scoped = {path: (entries, names) for path, entries, names in original.source_directories}
    targets = [('.storage', 'ha_entity_renamer_backups')]
    for reverse in (False, True):
        for row in change.files:
            operation = 'yaml_' + _digest((change.identity, row.path, reverse))[:64]
            name = operation + '-' + _sha(row.path.encode()) + '.yaml-backup'
            targets.append(('.storage/ha_entity_renamer_backups', name))
    for directory, name in targets:
        if directory in scoped:
            entries, names = scoped[directory]
            if (names is None or name in names) and name not in {entry[0] for entry in entries}:
                _fail('yaml_backup_membership_unprepared')


def capture_yaml_original_metadata(original, change):
    """Synchronous pre-effect capture; caller keeps original editor/lease locks.

    This must be called before chain start, under the ORIGINAL source fence.
    Reopen never calls it. Values are immutable root evidence once acknowledged.
    """
    if change.receipt != original:
        _fail('yaml_original_receipt_mismatch')
    assert_validation_current(original)
    reader = _Reader(original.config_root, Limits(max_file_bytes=MAX_FILE_BYTES))
    try:
        metadata = {}
        for row in change.files:
            raw, info = reader.read(row.path)
            if raw != row.before:
                _fail('yaml_original_source_mismatch')
            metadata[row.path] = [info.st_dev, info.st_ino, info.st_mode,
                                  info.st_uid, info.st_gid]
        assert_validation_current(original)
        return validate_yaml_metadata(original, change, metadata)
    finally:
        os.close(reader.root)


def initial_sources(original, metadata):
    entries = _entry_map(original)
    result = {}
    for path, digest, dev, ino in original.source_files:
        identity = entries.get(path)
        if identity is None or identity[:2] != (dev, ino) or not stat.S_ISREG(identity[2]):
            _fail('original_source_metadata_missing')
        result[path] = [digest, dev, ino, identity[2], None, None]
        if path in metadata:
            result[path][4:] = list(metadata[path][3:])
    return result


def fold_yaml_calls(change, sources, calls, direction, operation_ids, *, partial=False):
    """Strict replay of direct call stamps against ORIGINAL desired bytes.

    JSON storage normalizes identities to lists; live returns use tuples.
    No source or desired image is learned from the result or live filesystem.
    """
    if direction not in ('forward', 'rollback') or type(calls) not in (tuple, list):
        _fail('yaml_calls_invalid')
    reverse = direction == 'rollback'
    rows = tuple(reversed(change.files)) if reverse else change.files
    from .yaml_transaction import _digest
    operations = ['yaml_' + _digest((change.identity, row.path, reverse))[:64] for row in rows]
    if operation_ids != operations or (len(calls) > len(rows) if partial else len(calls) != len(rows)):
        _fail('yaml_calls_incomplete_or_unbound')
    result = deepcopy(sources)
    fields = {'operation_id', 'path', 'before_sha256', 'after_sha256', 'before_identity',
              'after_identity', 'before_uid', 'before_gid', 'after_uid', 'after_gid',
              'backup', 'sha256', 'device', 'inode'}
    for row, call, operation in zip(rows, calls, operations):
        if type(call) is not dict or set(call) != fields:
            _fail('yaml_call_invalid')
        for key in ('before_identity', 'after_identity'):
            identity = call[key]
            if (type(identity) not in (tuple, list) or len(identity) != 3
                    or any(type(n) is not int or n < 0 for n in identity)
                    or identity[1] == 0 or not stat.S_ISREG(identity[2])):
                _fail('yaml_call_identity_invalid')
        for key in ('before_uid', 'before_gid', 'after_uid', 'after_gid', 'device', 'inode'):
            if type(call[key]) is not int or call[key] < 0:
                _fail('yaml_call_metadata_invalid')
        before, after = (row.after, row.before) if reverse else (row.before, row.after)
        expected = result.get(row.path)
        if (expected is None or None in expected[4:]
                or call['operation_id'] != operation or call['path'] != row.path
                or call['before_sha256'] != _sha(before) or call['after_sha256'] != _sha(after)
                or [call['before_sha256'], *call['before_identity'], call['before_uid'],
                    call['before_gid']] != expected
                or (call['after_identity'][0], call['after_identity'][2],
                    call['after_uid'], call['after_gid']) !=
                   (expected[1], expected[3], expected[4], expected[5])
                or (call['sha256'], call['device'], call['inode']) !=
                   (call['after_sha256'], *call['after_identity'][:2])
                or call['backup'] != '.storage/ha_entity_renamer_backups/' + operation
                   + '-' + _sha(row.path.encode()) + '.yaml-backup'):
            _fail('yaml_call_binding_mismatch')
        result[row.path] = [call['after_sha256'], *call['after_identity'],
                            call['after_uid'], call['after_gid']]
    return result


class AcceptedInputState:
    """Detached internal evidence. Only chain reconstruction is a trusted source.

    Never accept this type or its fields from a client. A live partial state has
    an expiring hook guard; it cannot be used after completion or on reopen.
    """
    def __init__(self, original_json, head, sources, documents, *, live_check=None,
                 evidence_check=None):
        self.original_json = original_json
        self.head = head
        self._sources = deepcopy(sources)
        self._documents = deepcopy(documents)
        self._live_check = live_check
        self._evidence_check = evidence_check

    def sources(self):
        return deepcopy(self._sources)

    def documents(self):
        return deepcopy(self._documents)

    def same_evidence(self, other):
        return (type(other) is AcceptedInputState and self._live_check is None
                and other._live_check is None and self.original_json == other.original_json
                and self.head == other.head and self._sources == other._sources
                and self._documents == other._documents)

    def _bound(self, original):
        if _receipt_bytes(original) != self.original_json:
            _fail('input_original_receipt_mismatch')
        if self._evidence_check is not None:
            self._evidence_check()
        if self._live_check is not None:
            self._live_check()

    @property
    def config_root(self):
        return _receipt(self.original_json).config_root

    @property
    def source_files(self):
        return tuple((path, *row[:3]) for path, row in self._sources.items())

    @property
    def source_directories(self):
        original = _receipt(self.original_json)
        directories = []
        for directory, entries, names in original.source_directories:
            revised = []
            for name, mode, dev, ino in entries:
                path = name if directory == '.' else directory + '/' + name
                if path in self._sources:
                    _, dev, ino, mode, _, _ = self._sources[path]
                revised.append((name, mode, dev, ino))
            directories.append((directory, tuple(revised), names))
        return tuple(directories)

    def assert_current(self, original):
        """Pure whole graph checker through nofollow anchored descriptors."""
        self._bound(original)
        if set(self._sources) != {row[0] for row in original.source_files}:
            _fail('input_graph_mismatch')
        reader = _Reader(original.config_root, Limits(max_file_bytes=MAX_FILE_BYTES))
        try:
            for path, expected in self._sources.items():
                raw, info = reader.read(path)
                observed = [_sha(raw), info.st_dev, info.st_ino, info.st_mode,
                            info.st_uid, info.st_gid]
                if (observed[:4] != expected[:4]
                        or expected[4] is not None and observed[4:] != expected[4:]):
                    _fail('input_source_changed')
            for directory, expected, names in self.source_directories:
                fd = os.dup(reader.root) if directory == '.' else reader.open(directory, directory=True)
                try:
                    with os.scandir(fd) as entries:
                        current = tuple(sorted((entry.name, (s := entry.stat(follow_symlinks=False)).st_mode,
                                                s.st_dev, s.st_ino) for entry in entries
                                               if names is None or entry.name in names))
                    if current != expected:
                        _fail('input_graph_changed')
                finally:
                    os.close(fd)
            self._bound(original)
        finally:
            os.close(reader.root)

    def _native_observation_fence(self, original):
        """Sample native only; this private fence is never accepted chain state.

        Post-native observation may see one yet-unaccepted native effect. Its
        consumer still has to validate the transition. During YAML, native must
        remain EXACT: no fresh native sampling can release its source fence.
        """
        self._bound(original)
        if self._live_check is not None:
            self.assert_current(original)
            return self
        from .native_input_baseline import _KEYS, _envelope
        sources, documents = self.sources(), self.documents()
        reader = _Reader(original.config_root, Limits(max_file_bytes=MAX_FILE_BYTES))
        try:
            for key in _KEYS:
                path = '.storage/' + key
                if path not in sources:
                    _fail('gap_absent_native_file')
                raw, info = reader.read(path)
                old = sources[path]
                if (info.st_dev, info.st_mode) != (old[1], old[3]):
                    _fail('input_native_metadata_changed')
                sources[path][:4] = [_sha(raw), info.st_dev, info.st_ino, info.st_mode]
                documents[key] = _envelope(raw, key, original.version)
            fence = _NativeObservationFence(self.original_json, self.head, sources, documents,
                                            evidence_check=self._evidence_check)
            fence.assert_current(original)
            return fence
        finally:
            os.close(reader.root)


class _NativeObservationFence(AcceptedInputState):
    """Ephemeral read fence; never returned by reconstruct_inputs or journaled."""
