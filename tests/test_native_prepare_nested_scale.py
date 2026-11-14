"""Full retained prepare on isolated Core with nested, native config entries."""
import asyncio
import json
from time import monotonic
from types import MappingProxyType
import unittest
from unittest.mock import patch

import test_native_retained_name_fields as fixture


@unittest.skipUnless(fixture.server.activation.fixture.fixtures.HAS_HA, 'native HA required')
class NativeNestedPrepareScaleTests(fixture.NativeRetainedNameFieldsTests):
    async def test_nested_native_full_prepare_heartbeat(self):
        from homeassistant import block_async_io
        from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
        from homeassistant.const import __version__
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        self.assertEqual(__version__, '2026.11.1')
        runtime, mapping, _, _, _ = await self.restored_source()
        hass = self.f.hass
        for i in range(3300):
            # Independent JSON objects, varied scalar values, and the real
            # ConfigEntry API/serializer; no production config or shared rows.
            nested = [{f'field_{k}': f'fixture-{i:04d}-{j:02d}-{k}-' + 'x' * 23
                       for k in range(6)} for j in range(25)]
            entry = ConfigEntry(domain='input_boolean', title=f'Fixture {i}',
                data={'nested': nested, 'enabled': False, 'ordinal': i},
                options={'nested_options': {'order': [i, True, None]}},
                version=1, minor_version=1, source='user', unique_id=f'nested-scale-{i}',
                disabled_by=ConfigEntryDisabler.USER,
                discovery_keys=MappingProxyType({}), subentries_data=[])
            await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        await self._flush_documents()
        raw = _orjson_bytes_default_encoder(hass.config_entries._data_to_save())
        self.assertGreater(len(raw), 24_000_000)
        self.assertLess(len(raw), 32_000_000)
        self.assertGreaterEqual(len(json.loads(raw)['entries']), 3300)
        times, running = [], True
        async def heartbeat():
            while running:
                times.append(monotonic())
                await asyncio.sleep(.05)
        ticker = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        self.assertFalse(block_async_io._BLOCKED_CALLS.calls)
        started = monotonic()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        old = next(iter(mapping))
        try:
            with patch.object(block_async_io, '_IN_TESTS', False):
                block_async_io.enable()
            review = await runtime.native_yaml_prepare(hass, mapping,
                entity_names={old: 'Reviewed fixture'}, actor_id='synthetic-admin',
                guard=lambda: None, refresh=refresh)
            self.assertIn('operation_id', review)
            await asyncio.sleep(.06)  # Include the final synchronous segment in the heartbeat.
        finally:
            running = False
            await ticker
            for row in block_async_io._BLOCKED_CALLS.calls:
                setattr(row.object, row.function, row.original_func)
            block_async_io._BLOCKED_CALLS.calls.clear()
        elapsed = monotonic() - started
        self.assertGreater(len(times), 2)
        gap = max(b - a for a, b in zip(times, times[1:]))
        print(f'nested_prepare_bytes={len(raw)} entries=3300 elapsed_seconds={elapsed:.3f} heartbeat_max_seconds={gap:.3f}')
        self.assertLess(gap, 2.0)
