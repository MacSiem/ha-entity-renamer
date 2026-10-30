"""Actual synthetic source lifecycle/namespace continuity, separate from root QA."""
import json
import sys
import unittest

import test_native_rename_emission_classifier as emission


class NativeSourceHandoffTests(emission.NativeRenameEmissionTests):
    async def test_source_handoff_retains_live_source_and_cleanup(self):
        await self.handoff_flow()

    async def test_source_handoff_keeps_real_unemitted_physical_value_change(self):
        await self.handoff_flow(changed_value=29)

    async def handoff_flow(self, *, changed_value=None):
        from homeassistant.core import callback
        from homeassistant.helpers.entity import Entity, entity_sources
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        runtime, mapping, uuids, _ = await self.data(variant='sensor')
        hass = self.f.hass
        source = self.producer
        old, new = next(iter(mapping.items()))
        native = self.module('native_registry')
        io = native.NativeRegistryIO(hass)
        bridge = native.NativeRegistryBridge(io, self.module('recorder_guard').NativeRecorderGuard(hass))
        change = await bridge.prepare('entity', uuids[old], {'entity_id': new}, lambda: None)
        platform = source.platform
        info = entity_sources(hass)[old]
        state_info = source._state_info
        source_removals, gaps, events = [], [], []
        callbacks = [(type(item).__name__, getattr(item, '__qualname__', None)) for item in source._on_remove]
        print('SOURCE_CLEANUP_SHAPE', json.dumps(callbacks))
        @callback
        def state_event(event):
            if event.data['entity_id'] not in (old, new):
                return
            events.append(event)
            if hass.states.get(old) is None and hass.states.get(new) is None:
                gaps.append(event)
        remove_state = hass.bus.async_listen('state_changed', state_event)
        code = Entity.async_remove.__code__
        tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
        sys.monitoring.use_tool_id(tool, 'native source lifecycle continuity')
        def removed(actual_code, offset):
            frame = sys._getframe(1)
            if frame.f_code is code and frame.f_locals.get('self') is source:
                source_removals.append(frame.f_locals.get('force_remove', False))
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, removed)
        sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
        mutation = None
        try:
            handoff = await self.module('native_source_handoff').NativeSourceHandoff.prepare(
                io, change, change.after, guard=lambda: None)
            if changed_value is not None:
                # A real producer value changes; no State/Event is manufactured.
                source._attr_native_value = changed_value
            mutation = handoff.mutate()
            await io.settle(change)
            await self._flush_documents()
            self.assertEqual(source_removals, [], 'source was removed/re-added during entity-ID rename')
            self.assertFalse(gaps, 'native state namespace had neither source ID')
            self.assertIs(source.platform, platform)
            self.assertIs(source._state_info, state_info)
            self.assertIs(entity_sources(hass)[new], info)
            for entries in (platform.entities, platform.domain_entities, platform.domain_platform_entities):
                self.assertIs(entries[new], source)
                self.assertNotIn(old, entries)
            self.assertIsNone(hass.states.get(old))
            self.assertIsNotNone(hass.states.get(new))
            self.assertEqual(handoff.api_calls, 1)
            self.assertEqual(handoff.handler_calls, 1)
            if changed_value is not None:
                self.assertEqual(hass.states.get(new).state, str(changed_value))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            # Source-only qualification: Template consumers still use their old
            # program and are deliberately outside this data-plane acceptance.
            source._attr_native_value = 23
            source.async_write_ha_state()
            await hass.async_block_till_done()
            self.assertEqual(hass.states.get(new).state, '23')
            self.assertEqual(len([event for event in events if event.data.get('new_state') is not None
                                  and event.data['new_state'].state == '23']), 1)
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
            remove_state()
            if mutation is not None:
                current = self.f.entity.async_get(new)
                self.assertIsNotNone(current)
                self.assertEqual(json.loads(_orjson_bytes_default_encoder(current.as_storage_fragment)),
                                 json.loads(mutation.after_json))
                inverse = await self.module('native_source_handoff').NativeSourceHandoff.prepare(
                    io, change, change.before, guard=lambda: None)
                inverse.mutate()
                await io.settle(change)
                await self._flush_documents()
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
        # Normal actual unload must clean every CURRENT map and subscription.
        from functools import partial
        subscription = next(item for item in source._on_remove if type(item) is partial)
        old_job, callbacks = subscription.args[3:5]
        await source.async_remove()
        await hass.async_block_till_done()
        for entries in (platform.entities, platform.domain_entities, platform.domain_platform_entities):
            self.assertNotIn(old, entries)
            self.assertNotIn(new, entries)
        self.assertNotIn(old, entity_sources(hass))
        self.assertNotIn(new, entity_sources(hass))
        self.assertFalse(any(job is old_job for members in callbacks.values() for job in members))
