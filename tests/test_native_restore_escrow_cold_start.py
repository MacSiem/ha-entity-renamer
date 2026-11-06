"""Real second-process HA startup from native dual-key files, before Renamer.

Only diagnostic snapshots of native Stores are copied. The new process imports
HA alone, starts the native helper and receives a real producer stimulus.
This checks the early-start window, not mixed dependency/receipt recovery.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest

import test_native_restore_state_escrow_io as fixture
import test_native_helper_quiescence as quiescence


CHILD = r'''
import asyncio, json, logging, os, sys
from datetime import timedelta
from decimal import Decimal
from homeassistant import loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntries, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, entity_platform as ep
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.setup import async_setup_component

async def main():
    root, entry_id, expected, source_id, source_uuid, helper_uuid = sys.argv[1:]
    hass = HomeAssistant(root)
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    try:
        await loader.async_get_custom_components(hass)
        assert await async_load_base_functionality(hass) is not False
        await hass.async_start()
        class Source(SensorEntity):
            _attr_name = 'Utility Observer Source'
            _attr_unique_id = 'utility-observer-source'
            _attr_native_value = Decimal('111')
            _attr_native_unit_of_measurement = 'kWh'
            _attr_device_class = SensorDeviceClass.ENERGY
            _attr_state_class = SensorStateClass.TOTAL_INCREASING
            _attr_should_poll = False
        source = Source()
        component = EntityComponent(logging.getLogger(__name__), 'sensor', hass,
                                    timedelta(seconds=60))
        await component.async_add_entities([source])
        registry = er.async_get(hass)
        assert source.entity_id == source_id
        assert registry.async_get(source_id).id == source_uuid
        # The helper's first startup in this process precedes any Renamer setup.
        assert await async_setup_component(hass, 'utility_meter', {}) is True
        await hass.async_block_till_done()
        entry = hass.config_entries.async_get_entry(entry_id)
        assert entry.state is ConfigEntryState.LOADED
        entities = [e for p in ep.async_get_platforms(hass, 'utility_meter')
                    if p.config_entry is entry for e in p.entities.values()]
        assert len(entities) == 1
        helper = entities[0]
        assert helper.entity_id == expected
        assert helper.registry_entry.id == helper_uuid
        assert helper.native_value == Decimal('11'), helper.native_value
        source._attr_native_value = Decimal('115')
        source.async_write_ha_state()
        await hass.async_block_till_done()
        assert helper.native_value == Decimal('15'), helper.native_value
        assert not any('ha_entity_renamer' in name for name in sys.modules)
        print(json.dumps({'pid': os.getpid(), 'initial': '11', 'after': '15',
                          'entity_id': helper.entity_id, 'renamer_loaded': False}))
    finally:
        await hass.async_stop(force=True)

asyncio.run(main())
'''


@unittest.skipUnless(fixture.fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class NativeEscrowColdStartTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeEscrowIOTests.asyncSetUp
    _before_start = fixture.NativeEscrowIOTests._before_start
    _fixture = fixture.NativeEscrowIOTests._fixture
    _emit = fixture.NativeEscrowIOTests._emit
    unloaded = fixture.NativeEscrowIOTests.unloaded
    guard = fixture.NativeEscrowIOTests.guard
    held_entry = fixture.NativeEscrowIOTests.held_entry
    prepare = fixture.NativeEscrowIOTests.prepare
    projection = fixture.NativeEscrowIOTests.projection
    apply = fixture.NativeEscrowIOTests.apply
    _flush_documents = quiescence.NativeHelperQuiescenceTests._flush_documents

    async def test_both_registry_choices_start_native_before_renamer_in_new_process(self):
        data = await self.prepare()
        await self.apply(data, self.old_helper, self.target)
        restore_bytes = Path(data.store.path).read_bytes()
        for index, chosen in enumerate((self.old_helper, self.target)):
            if index:
                self.f.entity.async_update_entity(self.old_helper,
                                                   new_entity_id=self.target)
                await self.f.hass.async_block_till_done()
            native_files = await self._flush_documents(initialize=True)
            directory = fixture.fixture.fixtures.scratch()
            self.addCleanup(directory.cleanup)
            root = Path(directory.name)
            storage = root / '.storage'
            storage.mkdir()
            (root / 'configuration.yaml').write_bytes(b'script: {}\n')
            for key, raw in native_files.items():
                (storage / key).write_bytes(raw)
            (storage / 'core.restore_state').write_bytes(restore_bytes)
            process = await asyncio.create_subprocess_exec(sys.executable, '-B', '-c',
                CHILD, str(root), self.entry.entry_id, chosen, self.old,
                self.source_uuid, self.uuid, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                async with asyncio.timeout(30):
                    stdout, stderr = await process.communicate()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            self.assertEqual(process.returncode, 0, stderr.decode()[-5000:])
            result = json.loads(stdout.decode().splitlines()[-1])
            self.assertNotEqual(result['pid'], os.getpid())
            self.assertEqual(result['entity_id'], chosen)
            self.assertEqual((result['initial'], result['after']), ('11', '15'))
            self.assertFalse(result['renamer_loaded'])
