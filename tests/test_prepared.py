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

    async def test_paths_cycles_and_nonfinite_rejected(self):
        for key in ('../plan', '/absolute', '', 'a/b'):
            with self.assertRaises(ValueError): await self.store.create(key, {})
        cycle = []; cycle.append(cycle)
        for value in (cycle, {'x': float('nan')}):
            with self.assertRaises(ValueError): await self.store.create('plan', value)
        self.assertEqual(self.disk.calls, 0)


if __name__ == '__main__': unittest.main()
