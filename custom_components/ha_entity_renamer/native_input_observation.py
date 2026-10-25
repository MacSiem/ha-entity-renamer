"""Read-only observation of native documents after an effect.

This is deliberately NOT a ValidationReceipt or a new preparation baseline.
The immutable original receipt remains binding for every non-native input and
for native presence, device and file mode. Only raw bytes/inode of its three
existing native documents are observed afresh. A transition consumer must prove
those exact full documents against its ORIGINAL baseline and durable chain.
Observation alone never authorizes an effect, YAML source exception or reload.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from .native_input_baseline import (
    MAX_FILE_BYTES, NativeInputFile, _KEYS, _Managers, _Pinned, _drained,
    _fail, _native_receipt, _receipt_bytes, _sha,
)
from .yaml_plan import Limits, _Reader
from .yaml_validation import assert_validation_current


@dataclass(frozen=True)
class NativeInputObservation:
    original_receipt_json: bytes
    version: str
    files: tuple[NativeInputFile, ...]


def _observation_fence(original):
    """Internal read fence only, never returned or used as check_config proof.

    Candidate native identities are sampled first and then re-opened/pinned by
    _Pinned under this fence. Full native manager/disk comparison remains
    mandatory. Foreign YAML, other storage, mode/dev or membership cannot gain
    any exception from this operation.
    """
    sources, entries = _native_receipt(original)
    reader = _Reader(original.config_root, Limits(max_file_bytes=MAX_FILE_BYTES))
    identities = {}
    try:
        for key in _KEYS:
            path = '.storage/' + key
            if path not in sources:
                continue  # Original absence is still protected by membership.
            raw, info = reader.read(path)
            old_mode, old_dev, _ = entries[key]
            if (info.st_mode, info.st_dev) != (old_mode, old_dev):
                _fail('native_input_observation_metadata_changed')
            identities[path] = (_sha(raw), info.st_dev, info.st_ino, info.st_mode)
        files = tuple((path, *identities[path][:3]) if path in identities else row
                      for row in original.source_files for path in (row[0],))
        directories = []
        for path, members, names in original.source_directories:
            revised = []
            for row in members:
                child = row[0] if path == '.' else path + '/' + row[0]
                if child in identities:
                    _, dev, ino, mode = identities[child]
                    revised.append((row[0], mode, dev, ino))
                else:
                    revised.append(row)
            directories.append((path, tuple(revised), names))
        observed = replace(original, source_files=files, source_directories=tuple(directories))
        assert_validation_current(observed)
        return observed
    finally:
        import os
        os.close(reader.root)


async def async_observe_native_inputs(hass, original_receipt, *, guard):
    """Observe only; caller holds coordinator and external native-editor lease.

    All awaits drain safely and are bracketed by guard/manager checks. Held
    descriptors cover capture, fsync and repeated named-path/full-source fences.
    Caller must independently compare chain raw digests/identity/full expected
    documents before an effect, and retain the lock/lease through that effect.
    No native load, flush, save, settle, refresh-validation or mutation occurs.
    """
    pinned = None
    try:
        guard()
        original = _receipt_bytes(original_receipt)
        managers = _Managers(hass, original_receipt)
        fence = await _drained(hass.async_add_executor_job,
                               lambda: _observation_fence(original_receipt))
        guard()
        managers.check()
        pinned = _Pinned(fence, managers.initial)
        observation = await _drained(hass.async_add_executor_job, pinned.capture)
        guard()
        managers.check()
        await _drained(hass.async_add_executor_job, pinned.verify)
        guard()
        managers.check()
        if _receipt_bytes(original_receipt) != original:
            _fail('native_input_observation_original_changed')
        return NativeInputObservation(original, original_receipt.version, observation.files)
    except asyncio.CancelledError:
        raise
    except Exception:
        _fail('native_input_observation_rejected')
    finally:
        if pinned is not None:
            pinned.close()
