"""Guarded YAML file replacement for server-prepared patches.

The runtime must hold native editor mutation locks and persist its transaction
intent before calling this adapter. Revision checks detect observed external
edits; they do not claim an OS-wide CAS against arbitrary external writers.
Backups and failed temporary files remain local for journal-driven recovery.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import PurePosixPath
import re
import stat
import uuid

from .yaml_plan import Limits, _Reader, _relative


class FileConflict(ValueError):
    """A sanitized conflict; do not retry without independent reconciliation."""


@dataclass(frozen=True)
class FileMutationResult:
    """Private call evidence, never an authorization or client-supplied receipt.

    Only the synchronous mutator creates this from its pinned source and final
    readback. Keep the public return a plain dictionary for existing consumers.
    No codec accepts external evidence or recovers a lost call acknowledgement.
    """

    operation_id: str
    path: str
    before_sha256: str
    after_sha256: str
    before_identity: tuple[int, int, int]
    after_identity: tuple[int, int, int]
    before_uid: int
    before_gid: int
    after_uid: int
    after_gid: int
    backup: str

    def __post_init__(self):
        if (type(self.operation_id) is not str
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', self.operation_id)
                or type(self.path) is not str or _relative(self.path) != self.path
                or type(self.backup) is not str or _relative(self.backup) != self.backup):
            raise ValueError('invalid_file_mutation_result')
        for digest in (self.before_sha256, self.after_sha256):
            if type(digest) is not str or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('invalid_file_mutation_result')
        for identity in (self.before_identity, self.after_identity):
            if (type(identity) is not tuple or len(identity) != 3
                    or any(type(n) is not int or n < 0 for n in identity)
                    or not stat.S_ISREG(identity[2])):
                raise ValueError('invalid_file_mutation_result')
        if any(type(n) is not int or n < 0 for n in
               (self.before_uid, self.before_gid, self.after_uid, self.after_gid)):
            raise ValueError('invalid_file_mutation_result')

    def as_dict(self):
        """Add full evidence while preserving every original dictionary field."""
        result = asdict(self)
        result.update(sha256=self.after_sha256,
                      device=self.after_identity[0], inode=self.after_identity[1])
        return result


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _metadata_equal(before, after):
    """Fail closed on an in-window source or staged-file metadata change."""
    return (stat.S_IMODE(before.st_mode) == stat.S_IMODE(after.st_mode)
            and before.st_uid == after.st_uid and before.st_gid == after.st_gid)


def _write_all(fd, data):
    remaining = memoryview(data)
    while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
            raise OSError('short_write')
        remaining = remaining[count:]
    os.fsync(fd)


def _backup(reader, path, raw, operation_id):
    """Exclusive local recovery copy; existing matching copies are reusable."""
    storage = reader.open('.storage', directory=True)
    try:
        try:
            os.mkdir('ha_entity_renamer_backups', 0o700, dir_fd=storage)
        except FileExistsError:
            pass
        folder = os.open('ha_entity_renamer_backups', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=storage)
        os.fsync(storage)
    finally:
        os.close(storage)
    try:
        filename = operation_id + '-' + _hash(path.encode()) + '.yaml-backup'
        try:
            fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        except FileExistsError:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size != len(raw):
                    raise FileConflict('backup_conflict')
                chunks, remaining = [], len(raw) + 1
                while remaining:
                    chunk = os.read(fd, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if b''.join(chunks) != raw:
                    raise FileConflict('backup_conflict')
            finally:
                os.close(fd)
        else:
            try:
                _write_all(fd, raw)
            finally:
                os.close(fd)
        os.fsync(folder)
        return '.storage/ha_entity_renamer_backups/' + filename
    finally:
        os.close(folder)


def apply_file_patch(config_root, patch, expected_snapshot, operation_id, guard=None):
    """Synchronous executor operation. No reload and no registry writes.

    Call only after server-side validation of the whole proposed configuration.
    A returned receipt confirms this file only; full operation success requires
    HA activation, dependent readback and native historical continuity.
    """
    guard = guard or (lambda: None)
    guard()
    if type(operation_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', operation_id):
        raise FileConflict('invalid_operation_id')
    path = _relative(patch.path)
    if path != expected_snapshot.path or patch.expected_sha256 != expected_snapshot.sha256:
        raise FileConflict('patch_snapshot_mismatch')
    reader = _Reader(config_root, Limits())
    parent = source_pin = temporary_fd = None
    try:
        # Pin the compared source until replacement/readback completes. Some
        # mounted filesystems refresh lookup identities when the last handle
        # closes; a staged write must keep its source identity observable.
        source_pin = reader.open(path)
        pinned = os.fstat(source_pin)
        before, info = reader.read(path)
        if ((info.st_dev, info.st_ino) != (pinned.st_dev, pinned.st_ino)
                or not _metadata_equal(info, pinned)
                or _hash(before) != patch.expected_sha256 or info.st_dev != expected_snapshot.device
                or info.st_ino != expected_snapshot.inode):
            raise FileConflict('source_changed')
        projected = before
        last = len(before) + 1
        for edit in reversed(patch.edits):
            if (edit.start < 0 or edit.end < edit.start or edit.end > last
                    or before[edit.start:edit.end] != edit.before):
                raise FileConflict('invalid_patch_spans')
            projected = projected[:edit.start] + edit.after + projected[edit.end:]
            last = edit.start
        if projected != patch.proposed_bytes or projected == before:
            raise FileConflict('invalid_patch_content')
        guard()
        backup = _backup(reader, path, before, operation_id)
        parent_path = str(PurePosixPath(path).parent)
        parent = os.dup(reader.root) if parent_path == '.' else reader.open(parent_path, directory=True)
        filename = PurePosixPath(path).name
        temporary = '.renamer-' + operation_id + '-' + uuid.uuid4().hex + '.pending'
        guard()
        temporary_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     stat.S_IMODE(info.st_mode), dir_fd=parent)
        if (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid()):
            os.fchown(temporary_fd, info.st_uid, info.st_gid)
        os.fchmod(temporary_fd, stat.S_IMODE(info.st_mode))
        _write_all(temporary_fd, projected)
        new_info = os.fstat(temporary_fd)
        # Re-open through the held root: catch source or path replacement while
        # staging the backup/temp. Native editor locks must span this operation.
        current, current_info = reader.read(path)
        if (current != before or (current_info.st_dev, current_info.st_ino) != (info.st_dev, info.st_ino)
                or not _metadata_equal(current_info, info)):
            raise FileConflict('source_changed_before_replace')
        staged = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
        if (staged.st_dev, staged.st_ino) != (new_info.st_dev, new_info.st_ino):
            raise FileConflict('staged_identity_changed')
        if not _metadata_equal(staged, new_info):
            raise FileConflict('staged_metadata_changed')
        guard()
        # guard() may block or yield while an external writer changes source
        # or pending metadata. Repeat both checks after it returns, before
        # replacement. These checks narrow a race; they are not OS-wide CAS.
        late, late_info = reader.read(path)
        if (late != before or (late_info.st_dev, late_info.st_ino) != (info.st_dev, info.st_ino)
                or not _metadata_equal(late_info, info)):
            raise FileConflict('source_changed_before_replace')
        late_staged = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
        if (late_staged.st_dev, late_staged.st_ino) != (new_info.st_dev, new_info.st_ino):
            raise FileConflict('staged_identity_changed')
        if not _metadata_equal(late_staged, new_info):
            raise FileConflict('staged_metadata_changed')
        os.replace(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        after, after_info = reader.read(path)
        if (after != projected or (after_info.st_dev, after_info.st_ino) != (new_info.st_dev, new_info.st_ino)
                or not _metadata_equal(after_info, new_info)):
            raise FileConflict('file_write_unconfirmed')
        return FileMutationResult(
            operation_id=operation_id, path=path,
            before_sha256=_hash(before), after_sha256=_hash(after),
            before_identity=(pinned.st_dev, pinned.st_ino, pinned.st_mode),
            after_identity=(after_info.st_dev, after_info.st_ino, after_info.st_mode),
            before_uid=pinned.st_uid, before_gid=pinned.st_gid,
            after_uid=after_info.st_uid, after_gid=after_info.st_gid,
            backup=backup,
        ).as_dict()
    except OSError:
        raise FileConflict('file_io_unconfirmed') from None
    finally:
        if temporary_fd is not None: os.close(temporary_fd)
        if source_pin is not None: os.close(source_pin)
        if parent is not None: os.close(parent)
        os.close(reader.root)
