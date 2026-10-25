"""Detached, read-only native discovery snapshot before YAML validation.

This is neither a ValidationReceipt nor a preparation baseline. Its descriptors
close before return; it gives no continuing fence, adoption, readiness or effect
authorization. The caller holds its discovery coordinator/editor scope throughout
the call and must independently validate any later plan against current sources.
No manager factory, load, save, flush, migration or native mutation is called.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os

from .native_input_baseline import (
    MAX_FILE_BYTES, MAX_TOTAL_BYTES, _KEYS, _Managers, _SCHEMAS, _drained,
    _envelope, _json, _open_absolute_directory, _signature,
)
from .yaml_plan import Limits, _Reader


class DiscoveryNativeInputsError(ValueError):
    """Sanitized discovery refusal, without source values or underlying text."""


def _reject():
    raise DiscoveryNativeInputsError('native_discovery_inputs_rejected') from None


@dataclass(frozen=True)
class _SourceLocation:
    """Only runtime location for _Managers; deliberately no receipt or authority."""
    version: str
    config_root: str


@dataclass(frozen=True)
class DiscoveryNativeFile:
    key: str
    raw: bytes
    identity: tuple[int, int, int]  # device, inode, full st_mode
    uid: int
    gid: int
    manager_json: bytes


@dataclass(frozen=True)
class DiscoveryNativeInputs:
    version: str
    config_root: str
    files: tuple[DiscoveryNativeFile, ...]


def _metadata(info):
    return (*_signature(info), info.st_uid, info.st_gid)


def _directory_identity(info):
    # Directory content timestamps may change for unrelated storage keys.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


def _read(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    chunks, size = [], 0
    while chunk := os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size)):
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            _reject()
        chunks.append(chunk)
    return b''.join(chunks)


class _DiscoveryPinned:
    """Hold the actual root/storage/source descriptors across both executor jobs."""
    def __init__(self, location, documents):
        self.location, self.documents = location, documents
        self.reader = None
        self.storage_fd = None
        self.root_identity = None
        self.storage_identity = None
        self.handles = []
        self.files = []

    def close(self):
        for _, fd, _ in self.handles:
            os.close(fd)
        self.handles.clear()
        if self.storage_fd is not None:
            os.close(self.storage_fd)
            self.storage_fd = None
        if self.reader is not None:
            os.close(self.reader.root)
            self.reader = None

    def _named(self):
        fd = _open_absolute_directory(self.location.config_root)
        try:
            if _directory_identity(os.fstat(fd)) != self.root_identity:
                _reject()
        finally:
            os.close(fd)
        if _directory_identity(os.fstat(self.reader.root)) != self.root_identity:
            _reject()
        fd = self.reader.open('.storage', directory=True)
        try:
            if (_directory_identity(os.fstat(fd)) != self.storage_identity
                    or _directory_identity(os.fstat(self.storage_fd)) != self.storage_identity):
                _reject()
        finally:
            os.close(fd)
        for path, held, expected in self.handles:
            if _metadata(os.fstat(held)) != expected:
                _reject()
            fd = self.reader.open(path)
            try:
                if _metadata(os.fstat(fd)) != expected:
                    _reject()
            finally:
                os.close(fd)

    def capture(self):
        try:
            # _Reader's final root open does not walk ancestors. Bind it to a
            # separately opened, every-component O_NOFOLLOW root before reads.
            root_fd = _open_absolute_directory(self.location.config_root)
            try:
                self.root_identity = _directory_identity(os.fstat(root_fd))
                self.reader = _Reader(self.location.config_root,
                                      Limits(max_file_bytes=MAX_FILE_BYTES))
                if _directory_identity(os.fstat(self.reader.root)) != self.root_identity:
                    _reject()
            finally:
                os.close(root_fd)
            self.storage_fd = self.reader.open('.storage', directory=True)
            self.storage_identity = _directory_identity(os.fstat(self.storage_fd))
            self._named()
            total = sum(map(len, self.documents))
            for key, manager in zip(_KEYS, self.documents, strict=True):
                path = '.storage/' + key
                fd = self.reader.open(path)
                initial = os.fstat(fd)
                self.handles.append((path, fd, _metadata(initial)))
                if initial.st_size > MAX_FILE_BYTES:
                    _reject()
                raw, sampled = self.reader.read(path)
                if (_metadata(sampled) != _metadata(initial)
                        or _metadata(os.fstat(fd)) != _metadata(initial)
                        or _read(fd) != raw):
                    _reject()
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    _reject()
                if _json(_envelope(raw, key, self.location.version)['data']) != manager:
                    _reject()
                self.files.append(DiscoveryNativeFile(key, raw,
                    (initial.st_dev, initial.st_ino, initial.st_mode),
                    initial.st_uid, initial.st_gid, manager))
                os.fsync(fd)
            os.fsync(self.storage_fd)
            os.fsync(self.reader.root)
            self.verify()
            return DiscoveryNativeInputs(self.location.version,
                                         self.location.config_root, tuple(self.files))
        except BaseException:
            self.close()
            raise

    def verify(self):
        self._named()
        for (_, fd, _), item in zip(self.handles, self.files, strict=True):
            if _read(fd) != item.raw:
                _reject()
        self._named()


async def async_observe_discovery_inputs(hass, *, guard):
    """Observe three existing, settled native documents under caller's scope.

Exact supported versions/store classes/schema/layout/pending-state checks come
from _Managers. Missing native files refuse even if a manager serializes empty.
Cancellation drains executor work before any held descriptor can be released.
"""
    pinned = None
    try:
        from homeassistant.const import __version__
        guard()
        root = hass.config.config_dir
        if (type(root) is not str or not os.path.isabs(root)
                or os.path.normpath(root) != root or __version__ not in _SCHEMAS):
            _reject()
        location = _SourceLocation(__version__, root)
        managers = _Managers(hass, location)

        def check():
            guard()
            if type(hass.config.config_dir) is not str or hass.config.config_dir != root:
                _reject()
            managers.check()

        pinned = _DiscoveryPinned(location, managers.initial)
        check()
        observed = await _drained(hass.async_add_executor_job, pinned.capture)
        check()
        await _drained(hass.async_add_executor_job, pinned.verify)
        check()
        return observed
    except asyncio.CancelledError:
        raise
    except PermissionError:
        raise PermissionError('native_discovery_inputs_permission_denied') from None
    except Exception:
        _reject()
    finally:
        if pinned is not None:
            pinned.close()
