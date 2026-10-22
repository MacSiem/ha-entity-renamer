"""Guarded YAML file replacement for server-prepared patches.

The runtime must hold native editor mutation locks and persist its transaction
intent before calling this adapter. Revision checks detect observed external
edits; they do not claim an OS-wide CAS against arbitrary external writers.
Backups and failed temporary files remain local for journal-driven recovery.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import PurePosixPath
import re
import stat
import uuid

from .yaml_plan import Limits, _Reader, _relative


class FileConflict(ValueError):
    """A sanitized conflict; do not retry without independent reconciliation."""


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


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
    parent = None
    try:
        before, info = reader.read(path)
        if (_hash(before) != patch.expected_sha256 or info.st_dev != expected_snapshot.device
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
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     stat.S_IMODE(info.st_mode), dir_fd=parent)
        try:
            os.fchmod(fd, stat.S_IMODE(info.st_mode))
            if (info.st_uid, info.st_gid) != (os.geteuid(), os.getegid()):
                os.fchown(fd, info.st_uid, info.st_gid)
            _write_all(fd, projected)
            new_info = os.fstat(fd)
        finally:
            os.close(fd)
        # Re-open through the held root: catch source or path replacement while
        # staging the backup/temp. Native editor locks must span this operation.
        current, current_info = reader.read(path)
        if (current != before or (current_info.st_dev, current_info.st_ino) != (info.st_dev, info.st_ino)):
            raise FileConflict('source_changed_before_replace')
        guard()
        os.replace(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        after, after_info = reader.read(path)
        if after != projected or (after_info.st_dev, after_info.st_ino) != (new_info.st_dev, new_info.st_ino):
            raise FileConflict('file_write_unconfirmed')
        return {'path': path, 'sha256': _hash(after), 'backup': backup,
                'device': after_info.st_dev, 'inode': after_info.st_ino}
    except OSError:
        raise FileConflict('file_io_unconfirmed') from None
    finally:
        if parent is not None: os.close(parent)
        os.close(reader.root)
