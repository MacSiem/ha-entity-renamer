"""Connect native dashboard/Energy effects to the durable coordinator.

Prepared references are resolved only from the backend's immutable local store.
Whole-inventory verification is mandatory and deliberately supplied by the plan
owner; checking the changed rows alone is not a full-scope proof.
"""
from __future__ import annotations

from .energy_native import PreparedEnergy
from .lovelace_native import PreparedDashboard, revision
from .transaction import Position, Refused


class LovelaceTransactionAdapter:
    def __init__(self, native, resolver, verify_inventory, verify_original_inventory):
        self.native, self.resolver = native, resolver
        self.verify_inventory = verify_inventory
        self.verify_original_inventory = verify_original_inventory

    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        if (not isinstance(change, PreparedDashboard)
                or change.before.dashboard.identity != step.identity
                or change.before.revision != step.before_revision
                or change.after_revision != step.after_revision
                or revision(change.after) != step.after_revision):
            raise Refused('dashboard_preparation_mismatch')
        return change

    async def inspect(self, step):
        change = self._change(step)
        current = await self.native.read(change.before.dashboard)
        if current.config == change.before.config and current.revision == step.before_revision:
            return Position.BEFORE
        if current.config == change.after and current.revision == step.after_revision:
            return Position.AFTER
        return Position.UNKNOWN

    async def apply(self, step, guard):
        await self.native.write(self._change(step), guard)

    async def compensate(self, step, guard):
        change = self._change(step)
        guard()
        current = await self.native.read(change.before.dashboard)
        guard()
        if current.config != change.after or current.revision != step.after_revision:
            raise Refused('dashboard_compensation_conflict')
        # The native save replaced the inode. Reverse from the current checked
        # snapshot, never reuse the pre-apply file identity or overwrite edits.
        reverse = PreparedDashboard(current, change.before.config, step.before_revision)
        await self.native.write(reverse, guard)

    async def verify(self, plan):
        return await self.verify_inventory(plan)

    async def verify_rollback(self, plan):
        return await self.verify_original_inventory(plan)


class EnergyTransactionAdapter:
    def __init__(self, native, resolver, verify_inventory, verify_original_inventory):
        self.native, self.resolver = native, resolver
        self.verify_inventory = verify_inventory
        self.verify_original_inventory = verify_original_inventory

    def _change(self, step):
        change = self.resolver(step.prepared_ref)
        if (not isinstance(change, PreparedEnergy) or step.identity != 'energy'
                or change.before.revision != step.before_revision
                or change.after_revision != step.after_revision
                or revision(change.after) != step.after_revision):
            raise Refused('energy_preparation_mismatch')
        return change

    async def inspect(self, step):
        change = self._change(step)
        current = await self.native.read()
        if current.value == change.before.value and current.revision == step.before_revision:
            return Position.BEFORE
        if current.value == change.after and current.revision == step.after_revision:
            return Position.AFTER
        return Position.UNKNOWN

    async def apply(self, step, guard):
        await self.native.write(self._change(step), guard)

    async def compensate(self, step, guard):
        change = self._change(step)
        guard()
        current = await self.native.read()
        guard()
        if current.value != change.after or current.revision != step.after_revision:
            raise Refused('energy_compensation_conflict')
        await self.native.write(PreparedEnergy(current, change.before.value, step.before_revision), guard)

    async def verify(self, plan):
        return await self.verify_inventory(plan)

    async def verify_rollback(self, plan):
        return await self.verify_original_inventory(plan)
