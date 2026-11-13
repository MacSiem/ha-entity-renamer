"""Narrow native HomeKit ConfigEntry observer for receipted helper lifecycle.

The bridge itself is not a RestoreEntity. Its paired-client identity, driver
MAC, options/filter and selected registry identities are checked separately.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy

from .helper_native import read_entry
from .helper_plan import plan_helper, snapshot_equal
from .native_homekit_filter import _bridge_identity
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('homekit_helper_' + reason)


class NativeHomeKitHelperObservers:
    @classmethod
    async def capture(cls, hass, snapshots, mapping_proof, *, activation_lease=None):
        from homeassistant.const import __version__
        from homeassistant.components.homekit import FILTER_SCHEMA
        from homeassistant.helpers import entity_registry as er
        from .helper_observers import RegistryMappingProof
        _need(__version__ == '2026.11.1' and type(snapshots) in (tuple, list) and bool(snapshots), 'scope')
        _need(all(type(p) is RegistryMappingProof for p in mapping_proof), 'proof_type')
        mapping = {p.old:p.new for p in mapping_proof}
        _need(len(mapping) == len(mapping_proof) and len(set(mapping.values())) == len(mapping), 'mapping')
        registry = er.async_get(hass)
        _need(all((row := registry.async_get(p.old)) is not None and row.id == p.registry_id
                  for p in mapping_proof), 'registry_identity')
        obj = cls()
        obj.hass, obj.mapping, obj.proofs = hass, mapping, tuple(mapping_proof)
        obj.before, obj.after, obj.identities = {}, {}, {}
        obj.entry_ids = frozenset(entry.entry_id for entry in hass.config_entries.async_entries())
        for snapshot in snapshots:
            _need(snapshot['domain'] == 'homekit' and snapshot['identity']['source'] == 'import'
                  and 'index' not in snapshot['data'] and not snapshot['subentries'], 'entry_shape')
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
        obj.lease = activation_lease
        return obj

    def bind_activation_lease(self, lease):
        self.lease = lease

    async def settle(self):
        await asyncio.sleep(0)
        if self.lease is not None: self.lease.check()

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
              and snapshot_equal(read_entry(self.hass, key), snapshot)
              and _bridge_identity(entry) == self.identities[key], 'bridge_identity')
        if entry.state.value == 'not_loaded':
            from homeassistant.components.homekit.const import STATUS_STOPPED
            _need(entry.runtime_data.homekit.status == STATUS_STOPPED, 'bridge_not_stopped')
            return True
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
