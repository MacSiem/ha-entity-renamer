"""Read-only full native inputs bound to one pre-effect ValidationReceipt.

This is preparation evidence, NOT transition authorization. Caller holds the
coordinator lock and external-editor lease BEFORE the first effect, captures
ONCE for the whole batch, and later persists encode_baseline() in an immutable
private prepared store bound to the original Plan fingerprint. No Store load,
save, migration, settle/flush or HA mutation is performed here. A missing file
is explicit absence and cannot authorize creating it. Older preparations retain
the existing conservative YAML fences; this module does not change those fences.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict, dataclass, fields
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from threading import Event, Lock

from .yaml_plan import Limits, _Reader, _relative
from .yaml_validation import ValidationReceipt, assert_validation_current

FORMAT = 1
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_ENCODED_BYTES = 2 * MAX_TOTAL_BYTES + MAX_RECEIPT_BYTES
_KEYS = ('core.entity_registry', 'core.device_registry', 'core.config_entries')
_SCHEMAS = {'2025.6.0': ((1, 17), (1, 9), (1, 5)),
            '2026.10.2': ((1, 23), (3, 4), (1, 5)), '2026.10.4': ((1, 23), (3, 4), (1, 5))}
_RECEIPT_FIELDS = frozenset(f.name for f in fields(ValidationReceipt))


class NativeInputBaselineError(ValueError):
    """Sanitized refusal; no raw document, values or underlying exception text."""


def _fail(reason='native_input_baseline_rejected'):
    raise NativeInputBaselineError(reason) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail('native_input_duplicate_key')
        result[key] = value
    return result


def _constant(_):
    _fail('native_input_nonfinite')


def _loads(raw, limit=MAX_FILE_BYTES):
    if type(raw) is not bytes or len(raw) > limit:
        _fail('native_input_limit')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
        _bounded_json(value)
        return value
    except NativeInputBaselineError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail()


def _bounded_json(value, depth=0, budget=None):
    # Bound nesting and object count, including valid JSON that expands in RAM.
    if budget is None:
        budget = [1_000_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 64:
        _fail('native_input_limit')
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _fail()
            _bounded_json(item, depth + 1, budget)
    elif type(value) in (list, tuple):
        for item in value:
            _bounded_json(item, depth + 1, budget)
    elif type(value) is float and not math.isfinite(value):
        _fail('native_input_nonfinite')
    elif value is not None and type(value) not in (str, int, float, bool):
        _fail()


def _json(value):
    try:
        _bounded_json(value)
        return _encode_validated_json(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        _fail()


def _encode_validated_json(value):
    """Encode a local tree already checked by _loads, without revalidating it.

    Only synchronous consumers of that detached decode may use this helper;
    arbitrary caller-owned objects still enter through _json.
    """
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('ascii')
    except (ValueError, TypeError, RecursionError, OverflowError):
        _fail()


def _tuple(value):
    return tuple(_tuple(v) for v in value) if type(value) is list else value


def _digest_string(value):
    return (type(value) is str and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _integer(value):
    return type(value) is int and value >= 0


def _receipt(raw):
    data = _loads(raw, MAX_RECEIPT_BYTES)
    if type(data) is not dict or set(data) != _RECEIPT_FIELDS:
        _fail('native_input_receipt_invalid')
    try:
        receipt = ValidationReceipt(**{k: _tuple(v) for k, v in data.items()})
        if (type(receipt.config_root) is not str
                or not os.path.isabs(receipt.config_root)
                or os.path.normpath(receipt.config_root) != receipt.config_root
                or receipt.version not in _SCHEMAS):
            _fail('native_input_receipt_invalid')
        if any(type(value) is not tuple for value in (receipt.source_files,
                receipt.source_directories, receipt.proposed_files, receipt.runtime_expected)):
            _fail('native_input_receipt_invalid')
        paths = set()
        for path, digest, device, inode in receipt.source_files:
            _relative(path)
            if path in paths or not _digest_string(digest) or not all(map(_integer, (device, inode))):
                _fail('native_input_receipt_invalid')
            paths.add(path)
        directories = set()
        for path, entries, names in receipt.source_directories:
            if path != '.':
                _relative(path)
            if path in directories or type(entries) is not tuple:
                _fail('native_input_receipt_invalid')
            directories.add(path)
            if names is not None and (type(names) is not tuple or len(names) != len(set(names))
                                      or any(type(n) is not str or not n or '/' in n or '\\' in n
                                             or '\x00' in n or n in ('.', '..') for n in names)):
                _fail('native_input_receipt_invalid')
            seen = set()
            for name, mode, device, inode in entries:
                if (type(name) is not str or not name or '/' in name or '\\' in name
                        or '\x00' in name or name in seen
                        or name in ('.', '..') or not all(map(_integer, (mode, device, inode)))
                        or (names is not None and name not in names)):
                    _fail('native_input_receipt_invalid')
                seen.add(name)
        if '.' not in directories:
            _fail('native_input_receipt_invalid')
        for path, digest in receipt.proposed_files:
            _relative(path)
            if not _digest_string(digest):
                _fail('native_input_receipt_invalid')
        for values in (receipt.changed_domains, receipt.unsupported_domains):
            if type(values) is not tuple or any(type(v) is not str for v in values):
                _fail('native_input_receipt_invalid')
        for entity_id, state, attributes in receipt.runtime_expected:
            if type(entity_id) is not str or any(v is not None and not _digest_string(v)
                                                for v in (state, attributes)):
                _fail('native_input_receipt_invalid')
        return receipt
    except NativeInputBaselineError:
        raise
    except (ValueError, TypeError, KeyError):
        _fail('native_input_receipt_invalid')


def _receipt_bytes(receipt):
    if type(receipt) is not ValidationReceipt:
        _fail('native_input_receipt_invalid')
    raw = _json(asdict(receipt))
    if len(raw) > MAX_RECEIPT_BYTES or _receipt(raw) != receipt:
        _fail('native_input_receipt_invalid')
    return raw


def _schema(data, key, version):
    if type(data) is not dict:
        _fail('native_input_schema_invalid')
    if key == _KEYS[0]:
        lists = ('entities', 'deleted_entities')
        expected = set(lists) | ({'settings'} if version in ('2026.10.2', '2026.10.4') else set())
        if version in ('2026.10.2', '2026.10.4') and type(data.get('settings')) is not dict:
            _fail('native_input_schema_invalid')
    elif key == _KEYS[1]:
        lists = (('devices', 'child_devices', 'deleted_devices') if version in ('2026.10.2', '2026.10.4')
                 else ('devices', 'deleted_devices'))
        expected = set(lists)
    else:
        lists, expected = ('entries',), {'entries'}
    if set(data) != expected or any(type(data.get(k)) is not list or
                                   any(type(row) is not dict for row in data[k]) for k in lists):
        _fail('native_input_schema_invalid')
    return lists


def _envelope(raw, key, version):
    result = _loads(raw)
    major, minor = _SCHEMAS[version][_KEYS.index(key)]
    if (type(result) is not dict or set(result) != {'version', 'minor_version', 'key', 'data'}
            or type(result['version']) is not int or result['version'] != major
            or type(result['minor_version']) is not int or result['minor_version'] != minor
            or type(result['key']) is not str or result['key'] != key):
        _fail('native_input_envelope_invalid')
    _schema(result['data'], key, version)
    return result


@dataclass(frozen=True)
class NativeInputFile:
    key: str
    # None/None is explicit receipt-proven absence, NEVER permission to create.
    raw: bytes | None
    identity: tuple[int, int, int] | None  # dev, inode, full st_mode
    manager_json: bytes  # full native serializer, no projections or normalization

    @property
    def sha256(self):
        return None if self.raw is None else _sha(self.raw)


@dataclass(frozen=True)
class NativeInputBaseline:
    receipt_json: bytes  # canonical complete receipt; no mutable dict/list fields
    files: tuple[NativeInputFile, ...]
    format: int = FORMAT

    @property
    def receipt(self):
        return _receipt(self.receipt_json)

    @property
    def digest(self):
        return baseline_digest(self)


def _native_receipt(receipt):
    sources = {path: (digest, device, inode) for path, digest, device, inode in receipt.source_files}
    directories = {path: (entries, names) for path, entries, names in receipt.source_directories}
    root_entries, root_names = directories['.']
    if root_names is not None and '.storage' not in root_names:
        _fail('native_input_absence_not_fenced')
    storage = [row for row in root_entries if row[0] == '.storage']
    if not storage:
        if '.storage' in directories or any('.storage/' + key in sources for key in _KEYS):
            _fail('native_input_receipt_invalid')
        return sources, None
    if len(storage) != 1 or not stat.S_ISDIR(storage[0][1]) or '.storage' not in directories:
        _fail('native_input_receipt_invalid')
    entries, names = directories['.storage']
    if names is not None and not set(_KEYS) <= set(names):
        _fail('native_input_absence_not_fenced')
    entries = {name: (mode, dev, ino) for name, mode, dev, ino in entries}
    for key in _KEYS:
        source = sources.get('.storage/' + key)
        entry = entries.get(key)
        if ((source is None) != (entry is None)
                or entry is not None and (not stat.S_ISREG(entry[0]) or source[1:] != entry[1:])):
            _fail('native_input_receipt_invalid')
    return sources, entries


def _validate_baseline(baseline):
    if (type(baseline) is not NativeInputBaseline or type(baseline.format) is not int
            or baseline.format != FORMAT or type(baseline.files) is not tuple
            or len(baseline.files) != 3):
        _fail('native_input_codec_invalid')
    receipt = _receipt(baseline.receipt_json)
    if _receipt_bytes(receipt) != baseline.receipt_json:
        _fail('native_input_codec_invalid')
    sources, entries = _native_receipt(receipt)
    total = 0
    for key, item in zip(_KEYS, baseline.files, strict=True):
        if type(item) is not NativeInputFile or item.key != key:
            _fail('native_input_codec_invalid')
        manager = _loads(item.manager_json)
        lists = _schema(manager, key, receipt.version)
        if item.manager_json != _json(manager):
            _fail('native_input_codec_invalid')
        total += len(item.manager_json)
        expected = sources.get('.storage/' + key)
        if expected is None:
            if item.raw is not None or item.identity is not None or any(manager[k] for k in lists):
                _fail('native_input_missing_disk_disagrees')
        else:
            if (type(item.raw) is not bytes or type(item.identity) is not tuple
                    or len(item.identity) != 3 or not all(map(_integer, item.identity))
                    or item.identity != (expected[1], expected[2], entries[key][0])
                    or _sha(item.raw) != expected[0]):
                _fail('native_input_receipt_mismatch')
            if _json(_envelope(item.raw, key, receipt.version)['data']) != item.manager_json:
                _fail('native_input_manager_disk_disagree')
            total += len(item.raw)
        if total > MAX_TOTAL_BYTES:
            _fail('native_input_limit')
    return receipt


def encode_baseline(baseline):
    """Bounded versioned bytes for a future immutable PRIVATE prepared store."""
    _validate_baseline(baseline)
    raw = _json({'format': baseline.format,
                 'receipt': base64.b64encode(baseline.receipt_json).decode('ascii'),
                 'files': [{'key': item.key,
                            'raw': None if item.raw is None else base64.b64encode(item.raw).decode('ascii'),
                            'identity': item.identity,
                            'manager': base64.b64encode(item.manager_json).decode('ascii')}
                           for item in baseline.files]})
    if len(raw) > MAX_ENCODED_BYTES:
        _fail('native_input_limit')
    return raw


def decode_baseline(raw):
    """Strict round trip; unknown fields, duplicate keys and noncanonical codecs fail."""
    data = _loads(raw, MAX_ENCODED_BYTES)
    try:
        if (type(data) is not dict or set(data) != {'format', 'receipt', 'files'}
                or type(data['files']) is not list or len(data['files']) != 3):
            _fail('native_input_codec_invalid')
        def unbase(value):
            if type(value) is not str:
                _fail('native_input_codec_invalid')
            return base64.b64decode(value, validate=True)
        items = []
        for item in data['files']:
            if type(item) is not dict or set(item) != {'key', 'raw', 'identity', 'manager'}:
                _fail('native_input_codec_invalid')
            identity = item['identity']
            if identity is not None and type(identity) is not list:
                _fail('native_input_codec_invalid')
            items.append(NativeInputFile(item['key'], None if item['raw'] is None else unbase(item['raw']),
                None if identity is None else tuple(identity), unbase(item['manager'])))
        result = NativeInputBaseline(unbase(data['receipt']), tuple(items), data['format'])
        if encode_baseline(result) != raw:
            _fail('native_input_codec_invalid')
        return result
    except NativeInputBaselineError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError):
        _fail('native_input_codec_invalid')


def baseline_digest(baseline):
    return _sha(b'ha-entity-renamer/native-input-baseline/v1\x00' + encode_baseline(baseline))


def assert_baseline_receipt(baseline, receipt):
    """Exact ORIGINAL receipt binding; does not accept a fresh replacement receipt."""
    _validate_baseline(baseline)
    if baseline.receipt_json != _receipt_bytes(receipt):
        _fail('native_input_receipt_mismatch')


class _Managers:
    def __init__(self, hass, receipt):
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        from homeassistant.config_entries import ConfigEntries, ConfigEntryStore
        from homeassistant.const import __version__
        from homeassistant.core import CoreState
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        if __version__ not in _SCHEMAS or receipt.version != __version__:
            _fail('native_input_runtime_unsupported')
        if hass.state is not CoreState.running or Path(hass.config.config_dir) != Path(receipt.config_root):
            _fail('native_input_lifecycle_or_root')
        self.hass, self.receipt = hass, receipt
        self.version, self.running = __version__, CoreState.running
        self.encoder = _orjson_bytes_default_encoder
        self.keys = (er.DATA_REGISTRY, dr.DATA_REGISTRY)
        # Direct lookup: async_get can CREATE a manager, so never call it here.
        self.managers = (hass.data.get(er.DATA_REGISTRY), hass.data.get(dr.DATA_REGISTRY),
                         hass.config_entries)
        classes = (er.EntityRegistry, dr.DeviceRegistry, ConfigEntries)
        store_classes = (er.EntityRegistryStore, dr.DeviceRegistryStore, ConfigEntryStore)
        if any(type(manager) is not cls for manager, cls in zip(self.managers, classes, strict=True)):
            _fail('native_input_manager_unsupported')
        self.stores = tuple(manager._store for manager in self.managers)
        if any(type(store) is not cls for store, cls in zip(self.stores, store_classes, strict=True)):
            _fail('native_input_store_unsupported')
        self.initial = self.sample()

    def sample(self):
        from homeassistant.const import __version__
        if (__version__ != self.version or self.hass.state is not self.running
                or Path(self.hass.config.config_dir) != Path(self.receipt.config_root)
                or any(actual is not original for actual, original in zip(
                    (self.hass.data.get(self.keys[0]), self.hass.data.get(self.keys[1]),
                     self.hass.config_entries), self.managers, strict=True))):
            _fail('native_input_manager_changed')
        documents = []
        for key, manager, store, schema in zip(_KEYS, self.managers, self.stores,
                                              _SCHEMAS[self.version], strict=True):
            if (manager._store is not store or store.hass is not self.hass
                    or store.key != key or (type(store.version), type(store.minor_version)) != (int, int)
                    or (store.version, store.minor_version) != schema
                    or Path(store.path) != Path(self.receipt.config_root) / '.storage' / key
                    or store._read_only or getattr(store, '_load_empty', False)
                    or store._encoder is not None or store._private
                    or store._atomic_writes is not (key != _KEYS[2])
                    or self.version in ('2026.10.2', '2026.10.4') and (
                        store._serialize_in_event_loop is not (key == _KEYS[2])
                        or store._max_readable_version != schema[0])):
                _fail('native_input_store_changed')
            if (store._data is not None or store._write_lock.locked()
                    or store._delay_handle is not None or store._unsub_final_write_listener is not None
                    or store._load_future is not None):
                _fail('native_input_native_write_pending')
            if self.version in ('2026.10.2', '2026.10.4'):
                if key == _KEYS[1] and not manager._loaded_event.is_set():
                    _fail('native_input_manager_not_loaded')
                if key == _KEYS[2] and not manager._initialized.is_set():
                    _fail('native_input_manager_not_loaded')
            # Native fragments, datetime, sets and subentries use HA's actual encoder.
            document = _loads(self.encoder(manager._data_to_save()))
            _schema(document, key, self.version)
            documents.append(_encode_validated_json(document))
        if sum(map(len, documents)) > MAX_TOTAL_BYTES:
            _fail('native_input_limit')
        return tuple(documents)

    def check(self):
        if self.sample() != self.initial:
            _fail('native_input_manager_changed')


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _open_absolute_directory(path):
    # O_NOFOLLOW on a full path protects only its final component. Walk every
    # absolute ancestor as well; a changed mount/path must not hide a symlink.
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for piece in Path(path).parts[1:]:
            next_fd = os.open(piece, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


@dataclass(frozen=True)
class _ObservedNativeFiles:
    """Internal readback; deliberately not a new preparation baseline."""
    files: tuple[NativeInputFile, ...]


def _assert_accepted_native_file(item, source, expected_document, version):
    """Compare one freshly read image, decoding its immutable bytes once.

    This pure codec check grants no freshness: _Pinned retains every physical,
    manager, named-path and accepted-head fence around this comparison.
    """
    if (item.raw is None or source is None
            or (_sha(item.raw), *item.identity[:2]) != source):
        _fail('native_input_accepted_state_mismatch')
    document = _envelope(item.raw, item.key, version)
    if (_json(document['data']) != item.manager_json
            or _json(document) != _json(expected_document)):
        _fail('native_input_accepted_state_mismatch')


class _Pinned:
    """Own descriptors until final named-path checks, including across awaits.

    Keeping the original read fd open prevents inode reuse masquerading as the
    same source on mounted filesystems. Do not replace this with close/reopen.
    """
    def __init__(self, receipt, documents, *, input_state=None):
        self.receipt, self.documents = receipt, documents
        self.input_state = input_state
        self.reader = None
        self.handles = []
        self.storage_fd = None
        self.root_signature = None
        self.files = []

    def _assert_current(self):
        if self.input_state is None:
            assert_validation_current(self.receipt)
        else:
            self.input_state.assert_current(self.receipt)

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
        # Detect replacement of config root and .storage themselves, not only
        # children reached through the now-unlinked original directory handle.
        fd = _open_absolute_directory(self.receipt.config_root)
        try:
            if _signature(os.fstat(fd))[:3] != self.root_signature[:3]:
                _fail('native_input_named_path_changed')
        finally:
            os.close(fd)
        if self.storage_fd is not None:
            fd = self.reader.open('.storage', directory=True)
            try:
                if _signature(os.fstat(fd))[:3] != _signature(os.fstat(self.storage_fd))[:3]:
                    _fail('native_input_named_path_changed')
            finally:
                os.close(fd)
        for path, fd, expected in self.handles:
            if _signature(os.fstat(fd)) != expected:
                _fail('native_input_source_changed')
            named = self.reader.open(path)
            try:
                if _signature(os.fstat(named)) != expected:
                    _fail('native_input_named_path_changed')
            finally:
                os.close(named)

    def capture(self):
        try:
            self._assert_current()  # ALL sources, not three files alone.
            sources, entries = _native_receipt(self.input_state or self.receipt)
            self.reader = _Reader(self.receipt.config_root, Limits(max_file_bytes=MAX_FILE_BYTES))
            self.root_signature = _signature(os.fstat(self.reader.root))
            if entries is not None:
                self.storage_fd = self.reader.open('.storage', directory=True)
            for key, manager in zip(_KEYS, self.documents, strict=True):
                path = '.storage/' + key
                if path not in sources:
                    self.files.append(NativeInputFile(key, None, None, manager))
                    continue
                fd = self.reader.open(path)
                initial = os.fstat(fd)
                self.handles.append((path, fd, _signature(initial)))
                if initial.st_size > MAX_FILE_BYTES:
                    _fail('native_input_limit')
                chunks, size = [], 0
                while chunk := os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size)):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        _fail('native_input_limit')
                    chunks.append(chunk)
                raw = b''.join(chunks)
                item = NativeInputFile(key, raw, (initial.st_dev, initial.st_ino, initial.st_mode), manager)
                self.files.append(item)
                # Durability barriers on the SAME read descriptor and held directory.
                os.fsync(fd)
            if self.storage_fd is not None:
                os.fsync(self.storage_fd)
            os.fsync(self.reader.root)
            if self.input_state is None:
                baseline = NativeInputBaseline(_receipt_bytes(self.receipt), tuple(self.files))
                _validate_baseline(baseline)
            else:
                baseline = _ObservedNativeFiles(tuple(self.files))
                expected_documents = self.input_state.documents()
                total = 0
                for item in self.files:
                    total += len(item.manager_json) + len(item.raw or b'')
                    if total > MAX_TOTAL_BYTES:
                        _fail('native_input_limit')
                    path = '.storage/' + item.key
                    source = dict((row[0], row[1:]) for row in self.input_state.source_files).get(path)
                    _assert_accepted_native_file(item, source,
                        expected_documents[item.key], self.receipt.version)
            self.verify()
            return baseline
        except BaseException:
            self.close()
            raise

    def verify(self):
        self._assert_current()
        self._named()
        # Re-read original descriptors while still pinned, reject same-length
        # rewrites even if an editor restored mtime. ctime is also fenced above.
        for (_, fd, _), item in zip(self.handles, (f for f in self.files if f.raw is not None), strict=True):
            os.lseek(fd, 0, os.SEEK_SET)
            parts, size = [], 0
            while chunk := os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size)):
                parts.append(chunk)
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    _fail('native_input_limit')
            if b''.join(parts) != item.raw:
                _fail('native_input_source_changed')
        self._named()
        self._assert_current()


async def _drained(executor, function):
    """Do not release native fds while a worker can still be using them.

    Shielding only the asyncio Future is insufficient: another subsystem can
    cancel the Future while its already-started executor thread keeps running.
    The gate also makes a late, not-yet-started worker inert before cleanup.
    """
    finished = Event()
    mutex = Lock()
    started = False
    abandoned = False
    cancelled = False
    task = None

    @wraps(function)
    def fenced_worker():
        nonlocal started
        with mutex:
            if abandoned or started:
                return None
            started = True
        try:
            return function()
        finally:
            finished.set()

    # Preserve the callback metadata used by the existing native test harness.
    if hasattr(function, '__self__'):
        fenced_worker.__self__ = function.__self__

    try:
        task = asyncio.ensure_future(executor(fenced_worker))
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                # Save the worker exception until descriptors are safe to close.
                break
    finally:
        with mutex:
            abandoned = True
            active = started
        while active and not finished.is_set():
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancelled = True

    if cancelled:
        # Consume a failed task's exception to avoid an unobserved-task warning.
        if task is not None and task.done() and not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def async_capture_native_input_baseline(hass, receipt, *, guard):
    """Capture ONCE per batch under caller's original lock/lease, before effects.

    guard checks that same pre-effect scope/lease both sides of every await.
    It must not be a callback that merely reports the operation's overall status.
    No global async_block_till_done (unsafe under native editor locks), no flush.
    Pending managers refuse and must be settled by owner outside these locks.
    """
    pinned = None
    try:
        guard()
        frozen = _receipt_bytes(receipt)
        _native_receipt(receipt)
        managers = _Managers(hass, receipt)
        pinned = _Pinned(receipt, managers.initial)
        baseline = await _drained(hass.async_add_executor_job, pinned.capture)
        guard()
        managers.check()
        # A second fully fenced read after loop-side manager sampling. Original
        # fds remain pinned across both executor jobs and every named-path check.
        await _drained(hass.async_add_executor_job, pinned.verify)
        guard()
        managers.check()
        if _receipt_bytes(receipt) != frozen:
            _fail('native_input_receipt_changed')
        return baseline
    except (asyncio.CancelledError, NativeInputBaselineError):
        raise
    except Exception:
        _fail()
    finally:
        if pinned is not None:
            pinned.close()
