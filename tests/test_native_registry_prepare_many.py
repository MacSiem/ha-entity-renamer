"""Batch preparation retains independent receipts and fences the whole input."""
import asyncio
from copy import deepcopy
import unittest

from test_native_registry_bridge import Bridge, Conflict, IO, Recorder, recorder_module


class BatchRecorder(Recorder):
    async def snapshot(self, mapping):
        await asyncio.sleep(0)
        ids = set(mapping) | set(mapping.values())
        return {kind: {key: deepcopy(value) for key, value in rows.items() if key in ids}
                for kind, rows in self.baseline.items()}


class BatchHistory:
    async def capture(self, mapping):
        return {'content': list(mapping)}

    async def verify(self, snapshot):
        return True

    async def capture_many(self, mappings, fence):
        await asyncio.sleep(0)
        fence()
        return [await self.capture(mapping) for mapping in mappings]

    async def verify_many(self, snapshots, fence):
        await asyncio.sleep(0)
        fence()
        return [snapshot.get('content') != ['corrupt'] for snapshot in snapshots]


class PrepareManyTests(unittest.IsolatedAsyncioTestCase):
    def make(self, count):
        io = IO()
        io.data['entities'] = [dict(io.data['entities'][0], id=str(i),
                                    entity_id=f'sensor.old_{i}', device_id=None)
                               for i in range(count)]
        io.saved = deepcopy(io.data)
        recorder = BatchRecorder(io)
        recorder.baseline = {'states': {f'sensor.old_{i}': {'metadata_id': i + 1}
                                        for i in range(count)}, 'statistics': {}}
        return io, recorder, Bridge(io, recorder, history_guard=BatchHistory())

    async def test_per_row_receipts_match_individual(self):
        for count in (3, 10):
            io, recorder, bridge = self.make(count)
            requests = [('entity', str(i), {'entity_id': f'sensor.new_{i}'}) for i in range(count)]
            batch = await bridge.prepare_many(requests, lambda: None)
            individual = [await bridge.prepare(*request, lambda: None) for request in requests]
            self.assertEqual(batch, individual)

    async def test_middle_collision_and_drift_refuse(self):
        io, recorder, bridge = self.make(3)
        requests = [('entity', str(i), {'entity_id': f'sensor.new_{i}'}) for i in range(3)]
        recorder.baseline['states']['sensor.new_1'] = {'metadata_id': 42}
        with self.assertRaises(recorder_module.RecorderConflict):
            await bridge.prepare_many(requests, lambda: None)
        del recorder.baseline['states']['sensor.new_1']
        io.after_settle = lambda: io.data['entities'][1].update(name='drift')
        with self.assertRaises(Conflict):
            await bridge.prepare_many(requests, lambda: None)

    async def test_disk_drift_and_pending_refuse(self):
        io, _, bridge = self.make(3)
        requests = [('entity', str(i), {'entity_id': f'sensor.new_{i}'}) for i in range(3)]
        io.after_disk = lambda: io.data['entities'][1].update(name='drift')
        with self.assertRaises(Conflict):
            await bridge.prepare_many(requests, lambda: None)
        io.data = deepcopy(io.saved)
        io.is_pending = True
        with self.assertRaises(Conflict):
            await bridge.prepare_many(requests, lambda: None)

    async def test_runtime_drift_during_second_kind_disk_refuses(self):
        io, _, bridge = self.make(1)
        calls = 0
        disk = io.disk
        async def drifting_disk(kind):
            nonlocal calls
            result = await disk(kind)
            calls += 1
            if calls == 2:
                io.runtime_ok = False
            return result
        io.disk = drifting_disk
        requests = [('entity', '0', {'entity_id': 'sensor.new_0'}),
                    ('device', 'device', {'name_by_user': 'After'})]
        with self.assertRaises(Conflict):
            await bridge.prepare_many(requests, lambda: None)

    async def test_recorder_await_drift_and_cancellation(self):
        io, recorder, bridge = self.make(3)
        requests = [('entity', str(i), {'entity_id': f'sensor.new_{i}'}) for i in range(3)]
        original = recorder.snapshot
        async def drift(mapping):
            result = await original(mapping)
            io.data['entities'][1]['name'] = 'changed during Recorder await'
            return result
        recorder.snapshot = drift
        with self.assertRaises(Conflict):
            await bridge.prepare_many(requests, lambda: None)
        io.data = deepcopy(io.saved)
        started = asyncio.Event()
        async def blocked(mapping):
            started.set()
            await asyncio.Event().wait()
        recorder.snapshot = blocked
        task = asyncio.create_task(bridge.prepare_many(requests, lambda: None))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(io.updates, [])

    async def test_device_membership_drift_during_settle_refuses(self):
        io, _, bridge = self.make(1)
        members = {'device': {'0'}}
        io.device_memberships = lambda ids: {key: set(members[key]) for key in ids}
        async def settle_many(changes):
            await asyncio.sleep(0)
            members['device'].add('new')
        io.settle_many = settle_many
        with self.assertRaisesRegex(Conflict, 'device_entity_membership_changed'):
            await bridge.prepare_many([('device', 'device', {'name_by_user': 'After'})], lambda: None)

    async def test_final_before_uses_original_metadata_and_history(self):
        from dataclasses import replace
        io, recorder, bridge = self.make(3)
        requests = [('entity', str(i), {'entity_id': f'sensor.new_{i}'}) for i in range(3)]
        changes = await bridge.prepare_many(requests, lambda: None)
        self.assertTrue(await bridge.inspect_many_before(changes, lambda: None))
        altered = list(changes)
        altered[1] = replace(altered[1], history_before_json='{"content":["corrupt"]}')
        with self.assertRaises(Conflict):
            await bridge.inspect_many_before(altered, lambda: None)
        recorder.baseline['states']['sensor.old_1']['metadata_id'] = 99
        with self.assertRaises(Conflict):
            await bridge.inspect_many_before(changes, lambda: None)
