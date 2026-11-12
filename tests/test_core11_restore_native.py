"""Observe Core 11 RestoreState UUID indexing in a private native HA fixture."""
import json
import importlib
from decimal import Decimal
import unittest
from datetime import datetime, timezone

import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'requires actual Home Assistant')
class Core11RestoreNativeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeBaselineTests.asyncSetUp
    asyncTearDown = fixture.NativeBaselineTests.asyncTearDown

    async def test_uuid_owner_checks_both_namespaces_without_native_lookup(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.0':
            self.skipTest('Core 11 RestoreState only')
        from homeassistant.core import State
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData, StoredState
        module = importlib.import_module(fixture.PACKAGE + '.native_restore_uuid')
        refused = importlib.import_module(fixture.PACKAGE + '.transaction').Refused
        data = RestoreStateData(self.hass)
        self.hass.data[DATA_RESTORE_STATE] = data
        source, target = self.row.entity_id, 'sensor.renamed_owned'
        stored = StoredState(State(source, '1'), None, datetime.now(timezone.utc), self.row.id)
        data.last_states_by_entity_registry_id[self.row.id] = stored
        self.assertIs(module.owned_slot(data, source, target, self.row.id, required=True), stored)
        self.assertEqual(stored.state.entity_id, source)
        foreign = StoredState(State(target, '2'), None, datetime.now(timezone.utc), None)
        data.last_states_by_entity_id[target] = foreign
        with self.assertRaises(refused):
            module.owned_slot(data, source, target, self.row.id, required=True)
        del data.last_states_by_entity_id[target]
        self.assertIs(data.last_states_by_entity_registry_id[self.row.id], stored)
        data.last_states_by_entity_registry_id[self.other.id] = StoredState(
            State(target, '3'), None, datetime.now(timezone.utc), self.other.id)
        with self.assertRaises(refused):
            module.owned_slot(data, source, target, self.row.id, required=True)
        self.assertEqual(stored.state.entity_id, source)

    async def test_native_dump_receipt_binds_full_physical_payload(self):
        import asyncio
        from pathlib import Path
        from homeassistant.core import State
        from homeassistant.helpers.restore_state import RestoreStateData, StoredState
        receipt_module = importlib.import_module(fixture.PACKAGE + '.native_restore_dump_receipt')
        io = importlib.import_module(fixture.PACKAGE + '.native_restore_state_escrow_io')
        data = RestoreStateData(self.hass)
        now = datetime.now(timezone.utc)
        owned = StoredState(State(self.row.entity_id, '1'), None, now, self.row.id)
        foreign = StoredState(State(self.other.entity_id, 'old'), None, now, self.other.id)
        data.last_states_by_entity_registry_id[self.row.id] = owned
        data.last_states_by_entity_registry_id[self.other.id] = foreign
        await data.async_dump_states()
        old_disk = Path(data.store.path).read_bytes()
        data.last_states_by_entity_registry_id[self.other.id] = StoredState(
            State(self.other.entity_id, 'new'), None, now, self.other.id)
        with receipt_module.DumpReceipt(data.store) as receipt:
            receipt.task = asyncio.current_task()
            await data.async_dump_states()
            actual = Path(data.store.path).read_bytes()
            self.assertNotEqual(old_disk, actual)
            receipt.verify(actual)
            io.EscrowIO._read(data.store.path, (self.row.entity_id,), self.row.id, receipt)
            document = json.loads(actual)
            for row in document['data']:
                if row['state']['entity_id'] == self.other.entity_id:
                    row['state']['state'] = 'tampered'
            tampered = json.dumps(document).encode()
            with self.assertRaisesRegex(ValueError, 'physical_readback_mismatch'):
                receipt.verify(tampered)
            Path(data.store.path).write_bytes(tampered)
            with self.assertRaisesRegex(ValueError, 'physical_readback_mismatch'):
                io.EscrowIO._read(data.store.path, (self.row.entity_id,), self.row.id, receipt)
        with receipt_module.DumpReceipt(data.store) as receipt:
            receipt.task = asyncio.current_task()
            with self.assertRaisesRegex(ValueError, 'physical_readback_mismatch'):
                receipt.verify(actual)

    async def test_exact_group_payload_preserves_uuid_and_full_total(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.0':
            self.skipTest('Core 11 RestoreState only')
        from homeassistant.components.sensor import SensorExtraStoredData
        from homeassistant.core import State
        from homeassistant.helpers.restore_state import StoredState
        module = importlib.import_module(fixture.PACKAGE + '.powercalc_group_restore')
        state = State('sensor.original', '1.23', {'unit_of_measurement': 'kWh'})
        source = StoredState(state, SensorExtraStoredData(Decimal('1.23'), 'kWh'),
                             datetime.now(timezone.utc), self.row.id)
        payload = module.prepare(source, 'sensor.renamed', full_total='1.234',
                                 precision=2, unit='kWh')
        clone = module.materialize(payload)
        self.assertEqual(clone.entity_registry_id, self.row.id)
        self.assertEqual(clone.state.entity_id, 'sensor.renamed')
        self.assertEqual(clone.extra_data.as_dict()['native_value']['decimal_str'], '1.234')
        self.assertEqual(source.state.entity_id, 'sensor.original')

    async def test_real_load_distinguishes_legacy_null_and_uuid_and_native_move(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.0':
            self.skipTest('Core 11 RestoreState only')
        from homeassistant.core import State
        from homeassistant.helpers.json import json_bytes
        from homeassistant.helpers.restore_state import RestoreStateData, StoredState
        third = self.entity.async_get_or_create(
            'sensor', 'input_boolean', 'baseline-uuid',
            suggested_object_id='baseline_uuid', config_entry=self.entry)
        rows = (self.row, self.other, third)
        now = datetime.now(timezone.utc)
        def body(row, uuid):
            state = State(row.entity_id, '17.125', {'unit_of_measurement': 'kWh'})
            return json.loads(json_bytes(StoredState(state, None, now, uuid).as_dict()))
        legacy = body(rows[0], None)
        del legacy['entity_registry_id']
        explicit_null = body(rows[1], None)
        bound = body(rows[2], rows[2].id)
        data = RestoreStateData(self.hass)
        await data.store.async_save([legacy, explicit_null, bound])
        await data.async_load()
        self.assertIn(rows[0].id, data.last_states_by_entity_registry_id)
        self.assertIn(rows[1].entity_id, data.last_states_by_entity_id)
        self.assertIsNone(data.async_get_stored_state(rows[1].entity_id))
        original = data.last_states_by_entity_registry_id[rows[2].id]
        self.assertEqual(original.state.entity_id, rows[2].entity_id)
        before = json.loads(json_bytes(original.as_dict()))
        target = 'sensor.baseline_uuid_new'
        self.entity.async_update_entity(rows[2].entity_id, new_entity_id=target)
        moved = data.async_get_stored_state(target)
        self.assertIs(moved, original)
        self.assertEqual(moved.entity_registry_id, rows[2].id)
        self.assertEqual(moved.state.entity_id, target)
        after = json.loads(json_bytes(moved.as_dict()))
        before['state']['entity_id'] = target
        self.assertEqual(before, after)
        self.assertNotIn(target, data.last_states_by_entity_id)
