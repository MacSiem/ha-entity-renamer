"""Pinned native dual-key IO only. No authority, journal, replay or receipt.

Caller holds NativeHelperActivationLease throughout and supplies synchronous fresh guards.
A failure after mutation is UNKNOWN: never automatically compensate or retry.
Readback proves current bytes, not fsync/power-loss durability.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from pathlib import Path

from . import native_restore_state_payload as codec

ABSENT = None  # payloads are canonical bytes; None can never be a payload


class EscrowRejected(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise EscrowRejected(reason)


def captured(stored):
    return codec._canonical(codec._capture(stored, codec._native()))


@dataclass(frozen=True)
class Readback:
    """Ephemeral evidence only; caller must bind/persist its own typed receipt."""
    source: bytes | None
    target: bytes | None


class EscrowIO:
    def __init__(self, lease, entry_id, data, *, guard):
        from .native_helper_activation_lease import NativeHelperActivationLease
        require(type(lease) is NativeHelperActivationLease and callable(guard),
                'held_entry_and_guard_required')
        lease._task_check()
        manager = lease.manager
        self.entry = manager.async_get_entry(entry_id)
        require(lease.owns_entry(self.entry), 'entry_outside_owned_scope')
        self.hass = manager.hass
        self.lease, self.data, self.store, self.guard = lease, data, data.store, guard
        self.write_lock = self.store._write_lock
        self._check()

    def _check(self):
        from homeassistant.const import __version__
        from homeassistant.core import CoreState
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData
        from homeassistant.helpers.storage import Store
        self.lease._task_check()
        self.lease.check()
        require(self.lease.owns_entry(self.entry), 'entry_outside_owned_scope')
        hass, data, store = self.hass, self.data, self.store
        require(__version__ in codec.PINS, 'ha_version_unsupported')
        require(type(data) is RestoreStateData and hass.data.get(DATA_RESTORE_STATE) is data
                and data.hass is hass and data.store is store and type(store) is Store
                and store.hass is hass, 'native_data_store_identity')
        require(type(self.write_lock) is asyncio.Lock and store._write_lock is self.write_lock,
                'store_write_lock_identity')
        require(store.key == 'core.restore_state' and type(store.version) is int
                and store.version == 1 and type(store.minor_version) is int
                and store.minor_version == 1, 'store_schema')
        require(Path(store.path) == Path(hass.config.path('.storage', 'core.restore_state')),
                'store_path')
        require(hass.state not in (CoreState.stopping, CoreState.final_write, CoreState.not_running)
                and not getattr(store, '_read_only', False)
                and not getattr(store, '_load_empty', False), 'store_not_writable')
        result = self.guard()
        if inspect.iscoroutine(result):
            result.close()
        require(result is None, 'guard_must_raise_or_return_none_synchronously')
        self.lease.check()

    def _inactive(self, ids):
        from homeassistant.config_entries import ConfigEntryState
        from homeassistant.helpers.entity_platform import async_get_platforms
        require(self.entry.state is ConfigEntryState.NOT_LOADED, 'helper_entry_active')
        from .native_helper_transaction import _require_inactive
        _require_inactive(self.entry, self.lease)
        for entity_id in ids:
            require(entity_id not in self.data.entities, 'restore_entity_active')
            state = self.hass.states.get(entity_id)
            require(state is None or state.attributes.get('restored') is True,
                    'ordinary_state_collision')
        for platform in async_get_platforms(self.hass, self.entry.domain):
            require(not (platform.config_entry is self.entry and platform.entities),
                    'helper_platform_active')

    def _memory(self, expected):
        for entity_id, raw in expected.items():
            current = self.data.last_states.get(entity_id)
            require((entity_id not in self.data.last_states and raw is ABSENT) or
                    (current is not None and raw is not ABSENT and captured(current) == raw),
                    'memory_preimage_changed:' + entity_id)

    def _boundary(self, expected, *, inactive):
        self._check()
        if inactive:
            self._inactive(expected)
        self._memory(expected)

    @staticmethod
    def _read(path, ids):
        # Never Store.async_load: pending _data/preload can conceal disk failure.
        import json
        try:
            with Path(path).open('rb') as stream:
                raw = stream.read(64 * 1024 * 1024 + 1)
        except FileNotFoundError:
            return {key: ABSENT for key in ids}
        require(len(raw) <= 64 * 1024 * 1024, 'store_file_size')
        doc = json.loads(raw, object_pairs_hook=codec._pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(EscrowRejected('nonfinite')))
        require(type(doc) is dict and set(doc) == {'version','minor_version','key','data'}
                and type(doc['version']) is int and doc['version'] == 1
                and type(doc['minor_version']) is int and doc['minor_version'] == 1
                and doc['key'] == 'core.restore_state' and type(doc['data']) is list,
                'raw_store_envelope')
        result = {key: ABSENT for key in ids}
        seen = set()
        for row in doc['data']:
            require(type(row) is dict and type(row.get('state')) is dict
                    and type(row['state'].get('entity_id')) is str, 'raw_row_schema')
            key = row['state']['entity_id']
            # Reject every duplicate: load's dict comprehension would conceal it.
            require(key not in seen, 'raw_duplicate_entity_id')
            seen.add(key)
            if key in result:
                codec._shape(row)
                result[key] = codec._canonical(row)
        return result

    async def _raw(self, memory, disk, *, inactive=True):
        self._boundary(memory, inactive=inactive)
        # Await acquiring lock and await executor both get fresh checks.
        async with self.write_lock:
            self._boundary(memory, inactive=inactive)
            actual = await self.hass.async_add_executor_job(
                self._read, self.store.path, tuple(memory))
            self._boundary(memory, inactive=inactive)
            require(actual == disk, 'raw_disk_preimage_or_postimage_mismatch')
            return actual

    async def escrow(self, payload, *, expected_source, expected_target, disk_preimage, witness=None):
        """Copy captured source to target, retaining source. Reverse uses same API.

        expected_source is the actual captured native object; expected_target is
        ABSENT or canonical exact owned preimage established by caller's chain.
        disk_preimage is explicit projection of BOTH keys, including stale source
        if a real unload has not yet dumped it. This API confers no ownership.
        """
        integration = type(payload) is codec.PreparedIntegrationPayload
        require(type(payload) in (codec.PreparedPayload, codec.PreparedIntegrationPayload),
                'prepared_payload_required')
        before, after = codec._parse(payload.before_json), codec._parse(payload.after_json)
        source, target = before['state']['entity_id'], after['state']['entity_id']
        require(captured(expected_source) == payload.before_json, 'capture_binding')
        require(expected_target is ABSENT or type(expected_target) is bytes, 'target_preimage_type')
        if expected_target is not ABSENT:
            codec._shape(codec._parse(expected_target))
            require(codec._parse(expected_target)['state']['entity_id'] == target, 'target_preimage_endpoint')
        expected = {source: payload.before_json, target: expected_target}
        require(type(disk_preimage) is dict and set(disk_preimage) == set(expected),
                'explicit_disk_preimage_required')
        require(disk_preimage[target] == expected_target, 'disk_target_preimage_conflict')
        self._boundary(expected, inactive=True)
        require(self.data.last_states[source] is expected_source, 'source_object_changed')
        if integration:
            from .native_integration_unload_audit import NativeIntegrationUnloadWitness
            require(type(witness) is NativeIntegrationUnloadWitness
                    and self.entry.domain == 'integration', 'integration_live_witness_required')
            witness._claim(self, payload)
        await self._raw(expected, disk_preimage)
        self._boundary(expected, inactive=True)
        if integration:
            witness.check_live(lease=self.lease, data=self.data, stored=expected_source)
        require(self.data.last_states[source] is expected_source, 'source_object_changed')
        clone = codec.materialize(payload)
        self.data.last_states[target] = clone  # source deliberately retained
        if integration:
            witness._written(self, clone)
        post = {source: payload.before_json, target: payload.after_json}
        self._boundary(post, inactive=True)
        await self.data.async_dump_states()  # NEVER while holding store._write_lock
        self._boundary(post, inactive=True)
        actual = await self._raw(post, post)
        if integration:
            witness.check_live(lease=self.lease, data=self.data)
        return Readback(actual[source], actual[target])

    async def cleanup(self, *, source, target, owned_source, expected_target,
                      durable_names_setup_guard):
        """Remove only exact owned retained source after caller durable receipts.

        Both helpers must currently be inactive. Caller can defer cleanup until
        the next ordinary native unload; this narrow version never reasons about
        a concurrently accumulating active target. It never removes target.
        """
        require(callable(durable_names_setup_guard), 'durable_names_setup_guard_required')
        require(source != target and type(owned_source) is bytes
                and type(expected_target) is bytes, 'cleanup_preimages_required')
        expected = {source: owned_source, target: expected_target}
        def check_receipts():
            result = durable_names_setup_guard()
            if inspect.iscoroutine(result):
                result.close()
            require(result is None, 'durable_receipt_guard_must_be_synchronous')
        original_guard = self.guard
        def combined_guard():
            result = original_guard()
            if inspect.iscoroutine(result):
                result.close()
            require(result is None, 'guard_must_raise_or_return_none_synchronously')
            check_receipts()
        self.guard = combined_guard
        try:
            check_receipts()
            await self._raw(expected, expected)
            self._boundary(expected, inactive=True)
            del self.data.last_states[source]
            post = {source: ABSENT, target: expected_target}
            self._boundary(post, inactive=True)
            await self.data.async_dump_states()
            self._boundary(post, inactive=True)
            actual = await self._raw(post, post)
            return Readback(actual[source], actual[target])
        finally:
            self.guard = original_guard

    async def integration_transfer(self, payload, *, witness, disk_preimage):
        """One-key, same-helper-ID Integration data migration prerequisite.

        Unlike dual-key escrow(), source and target name are identical. Returned
        before is historical preimage, never a claim that it remains in cache.
        The enclosing product must persist its own intent/receipt. This primitive
        supplies capture authenticity and guarded native IO, no plan authority.
        """
        from .native_integration_unload_audit import NativeIntegrationUnloadWitness
        require(type(payload) is codec.PreparedIntegrationPayload
                and type(witness) is NativeIntegrationUnloadWitness,
                'integration_typed_capture_required')
        before, after = codec._parse(payload.before_json), codec._parse(payload.after_json)
        entity_id = before['state']['entity_id']
        require(after['state']['entity_id'] == entity_id and self.entry.domain == 'integration',
                'integration_same_id_prerequisite_only')
        expected = {entity_id: payload.before_json}
        require(type(disk_preimage) is dict and set(disk_preimage) == {entity_id},
                'explicit_disk_preimage_required')
        self._boundary(expected, inactive=True)
        witness._claim(self, payload)
        await self._raw(expected, disk_preimage)
        self._boundary(expected, inactive=True)
        witness.check_live(lease=self.lease, data=self.data)
        clone = codec.materialize_integration_payload(payload)
        self._boundary(expected, inactive=True)
        witness.check_live(lease=self.lease, data=self.data)
        self.data.last_states[entity_id] = clone
        witness._written(self, clone)
        post = {entity_id: payload.after_json}
        self._boundary(post, inactive=True)
        await self.data.async_dump_states()
        self._boundary(post, inactive=True)
        witness.check_live(lease=self.lease, data=self.data)
        actual = await self._raw(post, post)
        witness.check_live(lease=self.lease, data=self.data)
        return IntegrationReadback(payload.before_json, actual[entity_id])


@dataclass(frozen=True)
class IntegrationReadback:
    """One key: immutable original preimage and actual transformed postimage."""
    before: bytes
    after: bytes
