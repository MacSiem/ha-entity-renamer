from dataclasses import replace
import unittest
from custom_components.ha_entity_renamer.energy_native import EnergySnapshot, PreparedEnergy
from custom_components.ha_entity_renamer.lovelace_native import Dashboard, Snapshot, PreparedDashboard, revision
from custom_components.ha_entity_renamer.native_adapters import EnergyTransactionAdapter, LovelaceTransactionAdapter
from custom_components.ha_entity_renamer.transaction import Step, Position, Refused


class NativeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, energy):
        before, after = {'refs': ['sensor.a']}, {'refs': ['sensor.b']}
        native = type('Native', (), {})()
        if energy:
            snapshot = EnergySnapshot(before, revision(before), (1, 1, 'old'))
            prepared = PreparedEnergy(snapshot, after, revision(after))
            identity = 'energy'
        else:
            dashboard = Dashboard('route', 'lovelace.fixed', 'identity')
            snapshot = Snapshot(dashboard, before, revision(before), (1, 1, 'old'))
            prepared = PreparedDashboard(snapshot, after, revision(after))
            identity = dashboard.identity
        native.current = snapshot
        native.writes = []
        async def read(*args): return native.current
        async def write(change, guard):
            guard()
            self.assertEqual(change.before, native.current)
            native.writes.append(change)
            fields = {'value' if energy else 'config': change.after,
                      'revision': change.after_revision, 'file_identity': (1, len(native.writes) + 1, 'fresh')}
            native.current = replace(native.current, **fields)
        native.read, native.write = read, write
        async def verify(plan): return plan == 'whole_inventory'
        adapter_type = EnergyTransactionAdapter if energy else LovelaceTransactionAdapter
        adapter = adapter_type(native, lambda _: prepared, verify, verify)
        step = Step('one', 'native', identity, snapshot.revision, prepared.after_revision, 'local')
        self.assertEqual(await adapter.inspect(step), Position.BEFORE)
        await adapter.apply(step, lambda: None)
        self.assertEqual(await adapter.inspect(step), Position.AFTER)
        self.assertTrue(await adapter.verify('whole_inventory'))
        self.assertFalse(await adapter.verify('changed_rows_only'))
        await adapter.compensate(step, lambda: None)
        self.assertEqual(await adapter.inspect(step), Position.BEFORE)
        self.assertNotEqual(native.writes[1].before.file_identity, snapshot.file_identity)
        with self.assertRaises(Refused): await adapter.inspect(replace(step, identity='foreign'))
        native.current = replace(native.current, revision='foreign')
        self.assertEqual(await adapter.inspect(step), Position.UNKNOWN)
        with self.assertRaises(Refused): await adapter.compensate(step, lambda: None)

    async def test_dashboard_forward_reverse_and_foreign_edits(self): await self.exercise(False)
    async def test_energy_forward_reverse_and_foreign_edits(self): await self.exercise(True)


if __name__ == '__main__': unittest.main()
