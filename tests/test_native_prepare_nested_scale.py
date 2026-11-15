"""Full retained prepare on isolated Core with nested, native config entries."""
import asyncio
from collections import Counter
import sys
import threading
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
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        self.assertEqual(__version__, '2026.11.1')
        runtime, mapping, _, _, _ = await self.restored_source()
        hass = self.f.hass
        entries = []
        for i in range(104):
            entry = ConfigEntry(domain='input_boolean', title=f'Fixture {i}',
                data={'fixture': i, 'settings': {'enabled': False}},
                options={'fixture': {'order': [i, True, None]}},
                version=1, minor_version=1, source='user', unique_id=f'scale-entry-{i}',
                disabled_by=ConfigEntryDisabler.USER,
                discovery_keys=MappingProxyType({}), subentries_data=[])
            await hass.config_entries.async_add(entry)
            entries.append(entry)
        for i in range(261):
            self.f.device.async_get_or_create(config_entry_id=entries[i % len(entries)].entry_id,
                identifiers={('input_boolean', f'synthetic-device-{i}')}, name=f'Fixture device {i}')
        initial_live = len(self.f.entity.entities)
        for i in range(37_224 + max(0, 3156 - initial_live)):
            deleted = i < 37_224
            local = i if deleted else i - 37_224
            options_count = 3059 if deleted else 3000
            options = (lambda n=local: {'sensor': {'display_precision': n % 3},
                         'conversation': {'should_expose': n % 2 == 0}}) if local < options_count else None
            capabilities = ({'fixture_key': f'fixture-{local}', 'fixture_enabled': local % 2 == 0}
                            if not deleted and local < 1730 else None)
            unique = f'fixture-{i:05d}-' + 'u' * 18
            row = self.f.entity.async_get_or_create('sensor', 'input_boolean', unique,
                config_entry=entries[i % len(entries)] if i % 5 else None,
                suggested_object_id=f'scale_row_{i:05d}_' + 'x' * 22,
                disabled_by=er.RegistryEntryDisabler.USER, get_initial_options=options,
                capabilities=capabilities, original_name=None if deleted else f'Fixture registry row {i}')
            if deleted:
                self.f.entity.async_remove(row.entity_id)
        await hass.async_block_till_done()
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import SynchronizeTask
        future = hass.loop.create_future()
        get_instance(hass).queue_task(SynchronizeTask(future))
        await future
        from homeassistant.helpers import issue_registry as ir
        issue_store = ir.async_get(hass)._store
        if issue_store._data is not None or issue_store._delay_handle is not None:
            await issue_store.async_save(ir.async_get(hass)._data_to_save())
        await hass.async_block_till_done()
        await self._flush_documents(initialize=True)
        raw = _orjson_bytes_default_encoder(self.f.entity._data_to_save())
        document = json.loads(raw)
        self.assertEqual(len(document['entities']), 3156)
        self.assertEqual(len(document['deleted_entities']), 37_224)
        self.assertGreater(len(raw), 24_000_000)
        self.assertLess(len(raw), 32_000_000)
        times, running = [], True
        async def heartbeat():
            while running:
                times.append(monotonic())
                await asyncio.sleep(.05)
        ticker = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        self.assertFalse(block_async_io._BLOCKED_CALLS.calls)
        started = monotonic()
        samples = Counter()
        sample_stop = threading.Event()
        loop_thread = threading.get_ident()
        def profile():
            while not sample_stop.wait(.1):
                frame = sys._current_frames().get(loop_thread)
                stack = []
                while frame is not None:
                    name = frame.f_code.co_filename
                    if 'ha_entity_renamer/' in name:
                        stack.append(f'{name.rsplit("/", 1)[-1]}:{frame.f_code.co_name}:{frame.f_lineno}')
                    frame = frame.f_back
                if stack:
                    samples[';'.join(reversed(stack[-8:]))] += 1
        sampler = threading.Thread(target=profile, daemon=True)
        sampler.start()
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
            sample_stop.set()
            await ticker
            for row in block_async_io._BLOCKED_CALLS.calls:
                setattr(row.object, row.function, row.original_func)
            block_async_io._BLOCKED_CALLS.calls.clear()
        elapsed = monotonic() - started
        self.assertGreater(len(times), 2)
        gap = max(b - a for a, b in zip(times, times[1:]))
        for stack, count in samples.most_common(30):
            print(f'nested_profile_samples={count} stack={stack}')
        print(f'nested_prepare_bytes={len(raw)} entities=3156 deleted=37224 elapsed_seconds={elapsed:.3f} heartbeat_max_seconds={gap:.3f}')
        self.assertLess(gap, 2.0)
