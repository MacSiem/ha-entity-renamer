import copy
import unittest
from custom_components.ha_entity_renamer.prepared import PreparedStore, decode


class Store:
    def __init__(self): self.payload = None; self.calls = 0; self.fail = False
    async def load(self): return copy.deepcopy(self.payload), self.payload is not None
    async def save(self, payload):
        self.calls += 1
        self.payload = copy.deepcopy(payload)
        if self.fail: raise OSError('lost ack')


class PreparedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.disk = Store(); self.store = PreparedStore(self.disk)
        await self.store.initialize()
        self.payload = {'registry': [{'before': 'sensor.a', 'after': 'sensor.b'}]}

    async def test_immutable_idempotent_and_detached(self):
        digest = await self.store.create('plan', self.payload)
        self.assertEqual(digest, await self.store.create('plan', self.payload))
        self.assertEqual(self.disk.calls, 1)
        self.store.resolve('plan', digest)['registry'].clear()
        self.assertEqual(self.store.resolve('plan', digest), self.payload)
        self.payload['registry'].clear()
        with self.assertRaises(ValueError): await self.store.create('plan', self.payload)

    async def test_restart_resolves_exact_digest(self):
        digest = await self.store.create('plan', self.payload)
        fresh = PreparedStore(self.disk); await fresh.initialize()
        self.assertEqual(self.payload, fresh.resolve('plan', digest))
        with self.assertRaises(ValueError): fresh.resolve('plan', 'different')

    async def test_lost_ack_fences_memory_and_recovers_disk(self):
        self.disk.fail = True
        with self.assertRaises(OSError): await self.store.create('plan', self.payload)
        with self.assertRaises(ValueError): self.store.resolve('plan', 'any')
        fresh = PreparedStore(self.disk); await fresh.initialize()
        digest = self.disk.payload['plans']['plan']['digest']
        self.assertEqual(self.payload, fresh.resolve('plan', digest))

    async def test_tampered_payload_is_never_accepted(self):
        await self.store.create('plan', self.payload)
        self.disk.payload['plans']['plan']['payload']['registry'].clear()
        fresh = PreparedStore(self.disk)
        with self.assertRaises(ValueError): await fresh.initialize()

    async def test_native_change_codecs_survive_restart(self):
        from custom_components.ha_entity_renamer.prepared import encode_change, decode_change
        from custom_components.ha_entity_renamer.native_registry import PreparedRegistryChange
        from custom_components.ha_entity_renamer.lovelace_native import Dashboard, Snapshot, PreparedDashboard, revision
        from custom_components.ha_entity_renamer.energy_native import EnergySnapshot, PreparedEnergy
        before, after = {'refs': ['sensor.a']}, {'refs': ['sensor.b']}
        changes = [PreparedRegistryChange('entity', 'stable', '{"entity_id":"sensor.a"}', '{"entity_id":"sensor.b"}'),
            PreparedDashboard(Snapshot(Dashboard(None, 'lovelace', 'identity'), before, revision(before), (1, 2, 'hash')), after, revision(after)),
            PreparedEnergy(EnergySnapshot(before, revision(before), (1, 2, 'hash')), after, revision(after))]
        digest = await self.store.create('native', [encode_change(c) for c in changes])
        fresh = PreparedStore(self.disk); await fresh.initialize()
        self.assertEqual([decode_change(row) for row in fresh.resolve('native', digest)], changes)

    async def test_allowed_large_yaml_source_survives_immutable_storage_roundtrip(self):
        import hashlib
        from custom_components.ha_entity_renamer.yaml_transaction import PreparedYAML, YAMLFile
        from custom_components.ha_entity_renamer.yaml_validation import ValidationReceipt
        from custom_components.ha_entity_renamer.prepared import encode_change, decode_change
        before = b'#' + b'x' * 600000 + b'\nentity_id: sensor.a\n'
        after = before.replace(b'sensor.a', b'sensor.b')
        digest = lambda raw: hashlib.sha256(raw).hexdigest()
        receipt = ValidationReceipt('/synthetic-private-config',
            (('scripts.yaml', digest(before), 1, 2),),
            (('.', (('scripts.yaml', 0o100600, 1, 2),), ('scripts.yaml',)),),
            (('scripts.yaml', digest(after)),), ('script',), (), '2026.10.2')
        change = PreparedYAML(receipt, (YAMLFile('scripts.yaml', before.hex(), after.hex(), 1, 2),))
        prepared = encode_change(change)
        checksum = await self.store.create('large-yaml', prepared)
        fresh = PreparedStore(self.disk)
        await fresh.initialize()
        self.assertEqual(decode_change(fresh.resolve('large-yaml', checksum)), change)

    async def test_paths_cycles_and_nonfinite_rejected(self):
        for key in ('../plan', '/absolute', '', 'a/b'):
            with self.assertRaises(ValueError): await self.store.create(key, {})
        cycle = []; cycle.append(cycle)
        for value in (cycle, {'x': float('nan')}):
            with self.assertRaises(ValueError): await self.store.create('plan', value)
        self.assertEqual(self.disk.calls, 0)


if __name__ == '__main__': unittest.main()
