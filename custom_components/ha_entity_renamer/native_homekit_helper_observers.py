"""Narrow native HomeKit ConfigEntry observer for receipted helper lifecycle.

The bridge itself is not a RestoreEntity. Its paired-client identity, driver
MAC, options/filter and selected registry identities are checked separately.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from .helper_native import read_entry
from .helper_plan import plan_helper, snapshot_equal
from .native_homekit_filter import _bridge_identity
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('homekit_helper_' + reason)


def _installed():
    import homeassistant
    from homeassistant.components import homekit
    from homeassistant.components.homekit import aidmanager, services
    from .native_utility_setup_audit import _code_objects
    import sys
    root = Path(homeassistant.__file__).resolve().parent
    pins = {
        'components/homekit/__init__.py':'1c6307e46934e438cbe7c2af51004a65eb5886155217c40f07613b1f900f5e4f',
        'components/homekit/services.py':'c62e2e749c0659b325fd446a36b6e47c9f87b8fa826dc1057f2a87b9a83997bf',
        'components/homekit/util.py':'0cc8f0941f846f9051d1ed6941b8dd335f03377f5f15f9a021a5fc9345f2e5bd',
        'components/homekit/aidmanager.py':'8a35b1ef450caa3bc307d3c090ae42ee4224d0813fcefbf1d44e86d79e37edc7',
    }
    compiled = {}
    for relative, digest in pins.items():
        path = root / relative; raw = path.read_bytes()
        _need(path.resolve() == path and sha256(raw).hexdigest() == digest, 'native_source')
        compiled[str(path)] = tuple(_code_objects(compile(raw, str(path), 'exec',
                                                          dont_inherit=True, optimize=sys.flags.optimize)))
    for method in (homekit.async_setup_entry, homekit.async_unload_entry,
                   homekit.HomeKit.async_start, homekit.HomeKit.async_stop,
                   aidmanager._generate_aids, aidmanager.get_system_unique_id,
                   aidmanager.AccessoryAidStorage.get_or_allocate_aid_for_entity_id,
                   aidmanager.AccessoryAidStorage.get_or_allocate_aid,
                   services._handle_homekit_reload):
        code = method.__code__
        _need(code.co_filename in compiled and any(code == item for item in compiled[code.co_filename]), 'native_code')



class NativeHomeKitHelperObservers:
    @classmethod
    async def capture(cls, hass, snapshots, mapping_proof, *, activation_lease=None):
        from homeassistant.const import __version__
        from homeassistant.components.homekit import FILTER_SCHEMA
        from homeassistant.components.homekit.const import CONFIG_OPTIONS
        from homeassistant.helpers import entity_registry as er
        from .helper_observers import RegistryMappingProof
        _need(__version__ == '2026.11.1' and type(snapshots) in (tuple, list) and bool(snapshots), 'scope')
        _installed()
        _need(all(type(p) is RegistryMappingProof for p in mapping_proof), 'proof_type')
        mapping = {p.old:p.new for p in mapping_proof}
        _need(len(mapping) == len(mapping_proof) and len(set(mapping.values())) == len(mapping), 'mapping')
        registry = er.async_get(hass)
        _need(all((row := registry.async_get(p.old)) is not None and row.id == p.registry_id
                  for p in mapping_proof), 'registry_identity')
        obj = cls()
        obj.hass, obj.mapping, obj.proofs = hass, mapping, tuple(mapping_proof)
        obj.before, obj.after, obj.identities, obj.pairing_files = {}, {}, {}, {}
        obj.entry_ids = frozenset(entry.entry_id for entry in hass.config_entries.async_entries())
        for snapshot in snapshots:
            _need(snapshot['domain'] == 'homekit' and snapshot['identity']['source'] == 'import'
                  and 'index' not in snapshot['data'] and not snapshot['subentries']
                  and not any(key in snapshot['data'] and key not in snapshot['options']
                              for key in CONFIG_OPTIONS), 'entry_shape')
            key = snapshot['identity']['entry_id']
            _need(key not in obj.before and snapshot_equal(read_entry(hass, key), snapshot), 'entry_changed')
            plan = plan_helper(snapshot, mapping)
            _need(not plan.blocked and bool(plan.changes), 'plan')
            before_filter = FILTER_SCHEMA(snapshot['options'].get('filter', {})).get_filter()
            after_filter = FILTER_SCHEMA(plan.after['options'].get('filter', {})).get_filter()
            _need(all(before_filter(old) == after_filter(new) for old,new in mapping.items()), 'membership')
            entry = hass.config_entries.async_get_entry(key)
            _need(entry is not None and entry.state.value == 'loaded', 'not_loaded')
            obj.before[key], obj.after[key] = deepcopy(snapshot), deepcopy(plan.after)
            obj.identities[key] = _bridge_identity(entry)
            from homeassistant.components.homekit.util import get_persist_fullpath_for_entry_id
            path = Path(get_persist_fullpath_for_entry_id(hass, key))
            _need(path.is_file() and not path.is_symlink(), 'pairing_store_unavailable')
            obj.pairing_files[key] = (path, sha256(path.read_bytes()).hexdigest())
        obj.lease = activation_lease
        return obj

    def bind_activation_lease(self, lease):
        self.lease = lease

    async def settle(self):
        await asyncio.sleep(0)
        if self.lease is not None: self.lease.check()

    async def verify_inactive(self, entry_id):
        _need(entry_id in self.before, 'entry_scope')
        entry = self.hass.config_entries.async_get_entry(entry_id)
        _need(entry is not None and entry.state.value == 'not_loaded', 'bridge_not_stopped')
        current = read_entry(self.hass, entry_id)
        return await self.verify_activation(current)

    async def verify_lifecycle_runtime(self, entry_id, original, authorization):
        from homeassistant.helpers import entity_registry as er
        from .native_helper_lifecycle import RestoreAuthorization
        _need(entry_id in self.before and original.get('entry_id') == entry_id
              and type(authorization) is RestoreAuthorization
              and authorization.status in ('applied', 'rolled_back'), 'entry_scope')
        current = read_entry(self.hass, entry_id)
        expected = self.after[entry_id] if authorization.status == 'applied' else self.before[entry_id]
        entry = self.hass.config_entries.async_get_entry(entry_id)
        _need(entry is not None and entry.state.value == original.get('state')
              and snapshot_equal(current, expected), 'lifecycle_position')
        registry = er.async_get(self.hass)
        for proof in self.proofs:
            source, other = ((proof.new, proof.old) if authorization.status == 'applied'
                             else (proof.old, proof.new))
            row = registry.async_get(source)
            _need(row is not None and row.id == proof.registry_id
                  and registry.async_get(other) is None, 'lifecycle_registry_position')
        return await self.verify_runtime(current)

    async def validate_snapshot(self, snapshot):
        key = snapshot['identity']['entry_id']
        _need(key in self.before and any(snapshot_equal(snapshot, candidate)
              for candidate in (self.before[key], self.after[key])), 'snapshot')
        return True

    async def verify_activation(self, snapshot):
        from homeassistant.components.homekit import FILTER_SCHEMA
        await self.validate_snapshot(snapshot)
        key = snapshot['identity']['entry_id']
        entry = self.hass.config_entries.async_get_entry(key)
        _need(entry is not None and entry.state.value in ('loaded', 'not_loaded')
              and snapshot_equal(read_entry(self.hass, key), snapshot), 'bridge_identity')
        if entry.state.value == 'not_loaded':
            path, digest = self.pairing_files[key]
            _need(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == digest,
                  'pairing_store_changed')
            return True
        _need(_bridge_identity(entry) == self.identities[key], 'bridge_identity')
        native = entry.runtime_data.homekit._filter.get_filter()
        expected = FILTER_SCHEMA(snapshot['options'].get('filter', {})).get_filter()
        _need(all(native(entity_id) == expected(entity_id)
                  for pair in self.mapping.items() for entity_id in pair), 'runtime_filter')
        return True

    async def verify_runtime(self, snapshot):
        from homeassistant.helpers import entity_registry as er
        await self.verify_activation(snapshot)
        registry = er.async_get(self.hass)
        for proof in self.proofs:
            rows = [registry.async_get(identity) for identity in (proof.old, proof.new)]
            rows = [row for row in rows if row is not None]
            _need(len(rows) == 1 and rows[0].id == proof.registry_id, 'registry_identity')
        return True

    async def _inventory(self, snapshots):
        await self.settle()
        _need(frozenset(entry.entry_id for entry in self.hass.config_entries.async_entries()) == self.entry_ids,
              'inventory_changed')
        for snapshot in snapshots.values(): await self.verify_runtime(snapshot)
        return True

    async def verify_inventory(self, plan):
        return await self._inventory(self.after)

    async def verify_original_inventory(self, plan):
        return await self._inventory(self.before)
