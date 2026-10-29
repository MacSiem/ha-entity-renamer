"""Actual native Integration prerequisite; owner executes through FIFO on both pins.

Fixture only configures a helper and emits normal producer events. Product owns
all restore-state writes. No helper-field writes, cache seeds or HA monkeypatch.
This does not close own-ID rename, source-ID gap or timer acceptance.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import importlib
import json
import logging
import sys
from types import MappingProxyType
import unittest

import test_native_input_baseline as fixture
import test_native_integration_restore as native_fixture

codec = importlib.import_module(fixture.PACKAGE + '.native_restore_state_payload')
io = importlib.import_module(fixture.PACKAGE + '.native_restore_state_escrow_io')
try:
    audit = importlib.import_module(fixture.PACKAGE + '.native_integration_unload_audit')
except ModuleNotFoundError as error:
    if error.name != fixture.PACKAGE + '.native_integration_unload_audit':
        raise
    audit = None  # The initial acceptance run reports the missing product API.
activation = importlib.import_module(fixture.PACKAGE + '.native_helper_activation_lease')


@unittest.skipUnless(fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationTransferTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native_fixture.NativeIntegrationRestoreTests.asyncSetUp

    async def prepare(self, *, zero=False):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.components.integration.config_flow import ConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.helpers import entity_registry as er
        hass = self.f.hass
        class Producer(SensorEntity):
            _attr_name = 'Exact integration producer'
            _attr_unique_id = 'exact-integration-producer'
            _attr_should_poll = False
            _attr_native_unit_of_measurement = 'W'
            _attr_native_value = Decimal(0) if zero else Decimal('123456.789')
        self.producer = Producer()
        component = EntityComponent(logging.getLogger(__name__), 'sensor', hass,
                                    timedelta(seconds=60))
        await component.async_add_entities([self.producer])
        self.entry = ConfigEntry(domain='integration', title='Exact integration transfer',
            data={}, options={'name': 'Exact integration transfer',
                'source': self.producer.entity_id, 'method': 'left',
                'unit_time': 's', 'round': 2},
            version=ConfigFlowHandler.VERSION, minor_version=ConfigFlowHandler.MINOR_VERSION,
            source='user', discovery_keys=MappingProxyType({}), subentries_data=[],
            unique_id='exact-integration-transfer')
        await hass.config_entries.async_add(self.entry)
        await hass.async_block_till_done()
        self.old = self.current()
        self.registry = er.async_get(hass)
        self.helper_id = self.old.entity_id
        self.uuid = self.registry.async_get(self.helper_id).id
        # A same-state report is a real producer event and naturally integrates
        # zero. Changing the producer is never assigning helper counter fields.
        for value in (('0', '0') if zero else ('123456.790', '123456.791')):
            await self.emit(value)
        self.assertIs(type(self.old._state), Decimal)
        self.assertIs(type(self.old._last_valid_state), Decimal)
        if zero:
            self.assertEqual(self.old._state, Decimal(0))
            self.assertEqual(self.old._last_valid_state, Decimal(0))
        else:
            self.assertGreater(self.old._state, Decimal(0))
            self.assertNotEqual(self.old._state, Decimal(str(self.old.native_value)))

    def current(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        rows = [e for p in async_get_platforms(self.f.hass, 'integration')
                if p.config_entry is self.entry for e in p.entities.values()]
        self.assertEqual(len(rows), 1)
        return rows[0]

    async def emit(self, value):
        self.producer._attr_native_value = Decimal(value)
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()

    def guard(self):
        # Diagnostic caller identity fence; product plan/receipt chain is outside
        # this prerequisite. Passing it cannot synthesize the native witness.
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id), self.entry)
        self.assertEqual(self.registry.async_get(self.helper_id).id, self.uuid)
        self.assertEqual(self.registry.async_get(self.helper_id).unique_id, self.entry.entry_id)

    async def transfer(self):
        self.assertIsNotNone(audit, 'native Integration data transfer is not implemented')
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from homeassistant.components.integration.sensor import IntegrationSensorExtraStoredData
        hass = self.f.hass
        lease = await activation.NativeHelperActivationLease.acquire(hass, hass.config_entries,
            (self.entry,), guard=self.guard)
        async with lease:
            row = audit._row(self.registry.async_get(self.helper_id))
            collector = audit.NativeIntegrationUnloadAudit(hass, self.entry, row)
            witness = await collector.async_unload(lease)
            capture = witness.capture
            data = hass.data[DATA_RESTORE_STATE]
            stored = data.last_states[self.helper_id]
            self.assertIs(stored.extra_data, collector._extra)
            # Native dump serializes its own actual payload, including rounded
            # value / zero-loss. No fixture writes any restore state.
            await data.async_dump_states()
            witness.check_live(lease=lease, data=data, stored=stored)
            payload = codec.prepare_integration_payload(stored, self.helper_id, witness=witness)
            self.assertEqual(codec.deserialize_integration_payload(
                codec.serialize_integration_payload(payload)), payload)
            before, after = json.loads(payload.before_json), json.loads(payload.after_json)
            expected = json.loads(payload.before_json)
            expected['extra_data']['native_value'] = capture.full_state_text
            expected['extra_data']['last_valid_state'] = capture.last_valid_state_text
            self.assertEqual(after, expected)  # Entire body, including time/context.
            self.assertEqual(before['state']['entity_id'], after['state']['entity_id'])
            if Decimal(capture.last_valid_state_text) == 0:
                self.assertIsNone(before['extra_data']['last_valid_state'])
                self.assertIsNone(IntegrationSensorExtraStoredData.from_dict(before['extra_data']))
            writer = io.EscrowIO(lease, self.entry.entry_id, data, guard=self.guard)
            disk = io.EscrowIO._read(data.store.path, (self.helper_id,))
            result = await writer.integration_transfer(payload, witness=witness, disk_preimage=disk)
            self.assertEqual((result.before, result.after), (payload.before_json, payload.after_json))
            self.assertIsNot(data.last_states[self.helper_id], stored)
            self.assertEqual(io.EscrowIO._read(data.store.path, (self.helper_id,)),
                             {self.helper_id: payload.after_json})
            # Payload bytes alone, even valid roundtrip, confer no live authority.
            with self.assertRaises(ValueError):
                await writer.integration_transfer(payload, witness={}, disk_preimage=disk)
            with self.assertRaises(ValueError):
                await writer.integration_transfer(payload, witness=witness,
                    disk_preimage={self.helper_id: payload.after_json})
            corrupt = json.loads(payload.after_json)
            corrupt['extra_data']['native_unit_of_measurement'] = 'corrupted-unit'
            with self.assertRaises(ValueError):
                replace(payload, after_json=codec._canonical(corrupt))
            # Observe the real native setup parser call. This profiler only reads;
            # it does not replace the parser or manufacture its input/return.
            calls = []
            native_code = IntegrationSensorExtraStoredData.from_dict.__func__.__code__
            def observe(frame, event, arg):
                if frame.f_code is native_code and event == 'return':
                    calls.append((dict(frame.f_locals['restored']), arg))
            self.assertIsNone(sys.getprofile())
            try:
                sys.setprofile(observe)
                self.assertTrue(await lease.async_setup(self.entry.entry_id))
                await hass.async_block_till_done()
            finally:
                sys.setprofile(None)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], after['extra_data'])
            self.assertIs(type(calls[0][1]), IntegrationSensorExtraStoredData)
            self.assertEqual(calls[0][1].native_value, capture.full_state_text)
            self.assertIs(type(calls[0][1].native_value), str)
            current = self.current()
            self.assertIsNot(current, self.old)
            self.assertEqual(current.entity_id, self.helper_id)
            self.assertEqual(current.unique_id, self.entry.entry_id)
            self.assertEqual(self.registry.async_get(self.helper_id).id, self.uuid)
            self.assertEqual(current._state.as_tuple(), Decimal(capture.full_state_text).as_tuple())
            self.assertEqual(current._last_valid_state.as_tuple(),
                             Decimal(capture.last_valid_state_text).as_tuple())
            # An active setup ends the witness's inactive transfer window.
            with self.assertRaises(ValueError):
                witness.check_live()
        return current

    async def test_actual_full_precision_transfer_then_real_producer_progress(self):
        await self.prepare()
        current = await self.transfer()
        full = current._state
        await self.emit('123456.792')
        self.assertGreater(current._state, full)
        self.assertEqual(current._state, current._last_valid_state)

    async def test_actual_natural_zero_transfer_then_real_producer_progress(self):
        await self.prepare(zero=True)
        current = await self.transfer()
        self.assertEqual(current._state, Decimal(0))
        await self.emit('1')
        await self.emit('2')
        self.assertGreater(current._state, Decimal(0))
        self.assertEqual(current._state, current._last_valid_state)

    async def test_actual_distinct_current_and_last_valid_fields_are_preserved(self):
        await self.prepare()
        # Ordinary native reload really produces this discrepancy. We preserve
        # its current rounded state and its distinct historical last_valid;
        # neither fixture nor adapter invents the previously lost precision.
        self.assertTrue(await self.f.hass.config_entries.async_unload(self.entry.entry_id))
        self.assertTrue(await self.f.hass.config_entries.async_setup(self.entry.entry_id))
        await self.f.hass.async_block_till_done()
        self.old = self.current()
        current_full, historical_valid = self.old._state, self.old._last_valid_state
        self.assertNotEqual(current_full, historical_valid)
        current = await self.transfer()
        self.assertEqual(current._state.as_tuple(), current_full.as_tuple())
        self.assertEqual(current._last_valid_state.as_tuple(), historical_valid.as_tuple())

    async def test_no_dictionary_or_public_constructor_can_create_live_witness(self):
        self.assertIsNotNone(audit, 'native Integration unload witness is not implemented')
        with self.assertRaises(ValueError):
            audit.NativeIntegrationUnloadWitness()
        with self.assertRaises(ValueError):
            codec.prepare_integration_payload(None, 'sensor.arbitrary', witness={})
