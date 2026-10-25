"""Authored diagnostics, NOT executed. Owner runs FIFO on both exact HA pins.

Uses actual native BASIC Utility Meter and native Store/file IO in isolated HA.
No counter/cache values are injected. Patches below only induce failed IO.
These tests do not establish authorization/typed receipts/crash acceptance.
"""
import asyncio
from decimal import Decimal
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_utility_meter_observers as fixture

codec = importlib.import_module(fixture.fixtures.PACKAGE + '.native_restore_state_payload')
io = importlib.import_module(fixture.fixtures.PACKAGE + '.native_restore_state_escrow_io')


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class NativeEscrowIOTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ActualUtilityMeterObserverTests.asyncSetUp
    _before_start = fixture.ActualUtilityMeterObserverTests._before_start
    _fixture = fixture.ActualUtilityMeterObserverTests._fixture
    _emit = fixture.ActualUtilityMeterObserverTests._emit

    def current(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        entries = [entity for platform in async_get_platforms(self.f.hass, 'utility_meter')
                   if platform.config_entry is self.entry
                   for entity in platform.entities.values()]
        self.assertEqual(len(entries), 1)
        return entries[0]

    async def unloaded(self):
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        hass = self.f.hass
        self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
        await hass.async_block_till_done()
        return hass.data[DATA_RESTORE_STATE]

    def guard(self):
        # Diagnostic identity guard only; production root/chain binds at caller.
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id), self.entry)
        rows = [row for row in self.f.entity.entities.values()
                if row.config_entry_id == self.entry.entry_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, self.uuid)
        self.assertIn(rows[0].entity_id, (self.old_helper, self.target))

    async def prepare(self):
        await self._fixture()
        self.old_helper = self.entity.entity_id
        self.target = 'sensor.escrow_renamed_utility'
        self.uuid = self.f.entity.async_get(self.old_helper).id
        data = await self.unloaded()
        # Native dump fresh unloaded payload before projection; not fake disk IO.
        await data.async_dump_states()
        return data

    def projection(self, data, source=None, target=None):
        return io.EscrowIO._read(data.store.path,
                                (source or self.old_helper, target or self.target))

    async def apply(self, data, source, target, target_preimage=None):
        stored = data.last_states[source]
        payload = codec.prepare_payload(stored, target)
        before_disk = self.projection(data, source, target)
        async with io.hold_entry(self.f.hass, self.entry) as lease:
            adapter = io.EscrowIO(lease, data, guard=self.guard)
            async with asyncio.timeout(10):
                readback = await adapter.escrow(payload,
                    expected_source=stored, expected_target=target_preimage,
                    disk_preimage=before_disk)
            self.assertTrue(self.entry.setup_lock.locked())
        self.assertFalse(self.entry.setup_lock.locked())
        self.assertEqual(readback.source, payload.before_json)
        self.assertEqual(readback.target, payload.after_json)
        self.assertIs(data.last_states[source], stored)
        self.assertIsNot(data.last_states[target], stored)
        return payload

    async def test_actual_native_forward_then_reverse_uses_newly_accumulated_B1(self):
        data = await self.prepare()
        first = await self.apply(data, self.old_helper, self.target)
        hass = self.f.hass
        self.f.entity.async_update_entity(self.old_helper, new_entity_id=self.target)
        self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
        await hass.async_block_till_done()
        self.entity = self.current()
        self.assertEqual(self.entity.entity_id, self.target)
        self.assertEqual(self.entity.native_value, Decimal('11'))
        self.assertEqual(self.f.entity.async_get(self.target).id, self.uuid)
        await self._emit('119')
        self.assertEqual(self.entity.native_value, Decimal('19'))
        data = await self.unloaded()
        await data.async_dump_states()
        second = await self.apply(data, self.target, self.old_helper, first.before_json)
        self.assertNotEqual(first.before_json, second.after_json)
        self.assertEqual(Decimal(json.loads(second.before_json)['extra_data']['last_valid_state']),
                         Decimal('119'))
        self.f.entity.async_update_entity(self.target, new_entity_id=self.old_helper)
        self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
        await hass.async_block_till_done()
        self.entity = self.current()
        self.assertEqual(self.entity.native_value, Decimal('19'))
        self.assertEqual(self.f.entity.async_get(self.old_helper).id, self.uuid)
        # Actual unload/setup consumer again; both cache entries remain escrowed.
        await self.unloaded()
        self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
        await hass.async_block_till_done()
        self.assertEqual(self.current().native_value, Decimal('19'))

    async def test_unrelated_real_helper_progress_and_periodic_native_dump_survive(self):
        data = await self.prepare()
        # A second real helper on the producer, created through native user flow.
        options = dict(fixture.u._BASIC, name='Independent Escrow Meter', source=self.old)
        result = await self.f.hass.config_entries.flow.async_init('utility_meter',
            context={'source':'user'}, data=options)
        self.assertEqual(result['type'], 'create_entry')
        other_entry = result['result']
        await self.f.hass.async_block_till_done()
        from homeassistant.helpers.entity_platform import async_get_platforms
        others = [e for p in async_get_platforms(self.f.hass, 'utility_meter')
                  if p.config_entry is other_entry for e in p.entities.values()]
        self.assertEqual(len(others), 1)
        other = others[0]
        await self._emit('120')
        before = other.native_value
        payload = codec.prepare_payload(data.last_states[self.old_helper], self.target)
        async with io.hold_entry(self.f.hass, self.entry) as lease:
            adapter = io.EscrowIO(lease, data, guard=self.guard)
            # Both use actual native store serialization; no method replacement.
            periodic = asyncio.create_task(data.async_dump_states())
            advance = asyncio.create_task(self._emit('125'))
            await adapter.escrow(payload, expected_source=data.last_states[self.old_helper],
                expected_target=io.ABSENT, disk_preimage=self.projection(data))
            await asyncio.gather(periodic, advance)
        await data.async_dump_states()
        self.assertEqual(other.native_value, before + Decimal('5'))
        rows = json.loads(Path(data.store.path).read_bytes())['data']
        matches = [r for r in rows if r['state']['entity_id'] == other.entity_id]
        self.assertEqual(len(matches), 1)
        self.assertEqual(Decimal(matches[0]['extra_data']['native_value']), other.native_value)
        self.assertEqual(self.projection(data), {
            self.old_helper: payload.before_json, self.target: payload.after_json})

    async def test_native_collision_is_rejected_without_cache_or_file_changes(self):
        data = await self.prepare()
        # Real lifecycle creates historical target under another helper's ID.
        options = dict(fixture.u._BASIC, name='Collision Escrow Meter', source=self.old)
        result = await self.f.hass.config_entries.flow.async_init('utility_meter',
            context={'source':'user'}, data=options)
        other_entry = result['result']
        await self.f.hass.async_block_till_done()
        row = next(r for r in self.f.entity.entities.values()
                   if r.config_entry_id == other_entry.entry_id)
        await self._emit('130')
        self.assertTrue(await self.f.hass.config_entries.async_unload(other_entry.entry_id))
        await self.f.hass.async_block_till_done()
        await data.async_dump_states()
        target = row.entity_id
        retained = data.last_states[target]
        original_file = Path(data.store.path).read_bytes()
        payload = codec.prepare_payload(data.last_states[self.old_helper], target)
        async with io.hold_entry(self.f.hass, self.entry) as lease:
            adapter = io.EscrowIO(lease, data, guard=self.guard)
            with self.assertRaises(io.EscrowRejected):
                await adapter.escrow(payload, expected_source=data.last_states[self.old_helper],
                    expected_target=io.ABSENT,
                    disk_preimage=self.projection(data, self.old_helper, target))
        self.assertIs(data.last_states[target], retained)
        self.assertEqual(Path(data.store.path).read_bytes(), original_file)

    async def test_native_noop_write_cannot_get_ack_from_pending_store_load(self):
        data = await self.prepare()
        payload = codec.prepare_payload(data.last_states[self.old_helper], self.target)
        before_disk = self.projection(data)
        async def no_write(store, values):
            # Explicit failure injection ONLY at Store IO; native payload untouched.
            store._data = {'version':1, 'minor_version':1,
                           'key':store.key, 'data':values}
        async with io.hold_entry(self.f.hass, self.entry) as lease:
            adapter = io.EscrowIO(lease, data, guard=self.guard)
            with patch.object(type(data.store), 'async_save', new=no_write):
                with self.assertRaisesRegex(io.EscrowRejected, 'raw_disk'):
                    async with asyncio.timeout(10):
                        await adapter.escrow(payload,
                            expected_source=data.last_states[self.old_helper],
                            expected_target=io.ABSENT, disk_preimage=before_disk)
        self.assertEqual(self.projection(data), before_disk)
        # Mutation happened: memory remains dual, outcome UNKNOWN; no compensation.
        self.assertEqual(io.captured(data.last_states[self.target]), payload.after_json)

    async def test_actual_native_setup_waits_for_actual_entry_lock(self):
        data = await self.prepare()
        payload = codec.prepare_payload(data.last_states[self.old_helper], self.target)
        async with io.hold_entry(self.f.hass, self.entry) as lease:
            adapter = io.EscrowIO(lease, data, guard=self.guard)
            setup = asyncio.create_task(self.f.hass.config_entries.async_setup(self.entry.entry_id))
            self.addAsyncCleanup(self._finish_task, setup)
            await asyncio.sleep(0)
            self.assertFalse(setup.done())
            await adapter.escrow(payload, expected_source=data.last_states[self.old_helper],
                expected_target=io.ABSENT, disk_preimage=self.projection(data))
            self.assertFalse(setup.done())
        self.assertTrue(await setup)
        await self.f.hass.async_block_till_done()
        self.assertEqual(self.current().native_value, Decimal('11'))

    async def _finish_task(self, task):
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_readonly_and_cleanup_without_durable_guard_are_refused(self):
        data = await self.prepare()
        raw = Path(data.store.path).read_bytes()
        stored = data.last_states[self.old_helper]
        payload = codec.prepare_payload(stored, self.target)
        async with io.hold_entry(self.f.hass, self.entry) as lease:
            with patch.object(data.store, '_read_only', True):
                with self.assertRaisesRegex(io.EscrowRejected, 'not_writable'):
                    io.EscrowIO(lease, data, guard=self.guard)
            adapter = io.EscrowIO(lease, data, guard=self.guard)
            with self.assertRaises(io.EscrowRejected):
                await adapter.cleanup(source=self.old_helper, target=self.target,
                    owned_source=payload.before_json, expected_target=payload.after_json,
                    durable_names_setup_guard=None)
        self.assertIs(data.last_states[self.old_helper], stored)
        self.assertNotIn(self.target, data.last_states)
        self.assertEqual(Path(data.store.path).read_bytes(), raw)

    async def test_disk_duplicates_detected_and_missing_guard_lock_rejected(self):
        data = await self.prepare()
        raw = Path(data.store.path).read_bytes()
        doc = json.loads(raw)
        # Disk corruption diagnostic, never an acceptance payload seed.
        row = next(r for r in doc['data'] if r['state']['entity_id'] == self.old_helper)
        doc['data'].append(row)
        duplicate_file = self.f.root / 'duplicate-restore-diagnostic.json'
        duplicate_file.write_text(json.dumps(doc))
        with self.assertRaisesRegex(io.EscrowRejected, 'duplicate'):
            io.EscrowIO._read(duplicate_file, (self.old_helper, self.target))
        async with io.hold_entry(self.f.hass, self.entry) as lease:
            with self.assertRaises(io.EscrowRejected):
                io.EscrowIO(lease, data, guard=None)
        with self.assertRaises(io.EscrowRejected):
            io.EscrowIO(lease, data, guard=self.guard)
        self.assertEqual(Path(data.store.path).read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
