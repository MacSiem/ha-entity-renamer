"""NOT RUN: actual source registry callback completion on both pinned Cores.

The sensor's ordinary integration lifecycle hooks wait at removal and re-add.
No native method/task tracking/registry callback is replaced. A different source
is also held in its real registry callback: the product must not wait for it.
Owner runs this and the existing real foreground-reload RED through FIFO.
"""
import asyncio
import json
import unittest

import test_native_helper_quiescence as fixture
from test_native_foreground_settle import _await_frames


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeRegistryScopedSettleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start

    async def test_waits_for_source_remove_and_readd_without_waiting_for_other_source(self):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity import Entity
        from homeassistant.setup import async_setup_component

        hass = self.f.hass
        native = self.modules['native_registry']
        self.assertTrue(await async_setup_component(hass, 'sensor', {'sensor': []}))

        class Source(SensorEntity):
            _attr_should_poll = False
            _attr_native_value = 5

            def __init__(self, key):
                self._attr_unique_id = key
                self._attr_name = key
                self.renaming = False
                self.remove_entered, self.remove_release = asyncio.Event(), asyncio.Event()
                self.add_entered, self.add_release = asyncio.Event(), asyncio.Event()

            async def async_will_remove_from_hass(self):
                await super().async_will_remove_from_hass()
                if self.renaming:
                    self.remove_entered.set()
                    await self.remove_release.wait()

            async def async_added_to_hass(self):
                await super().async_added_to_hass()
                if self.renaming:
                    self.add_entered.set()
                    await self.add_release.wait()

        sources = [Source('Scoped source'), Source('Unrelated source')]
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities(sources)
        io = native.NativeRegistryIO(hass)
        changes, callback_tasks = [], []
        settle = None
        try:
            for index, source in enumerate(sources):
                old = source.entity_id
                before = next(row for row in io.document('entity')['entities']
                              if row['entity_id'] == old)
                after = dict(before, entity_id=f'sensor.scoped_result_{index}')
                change = native.PreparedRegistryChange('entity', before['id'],
                    json.dumps(before), json.dumps(after))
                changes.append(change)
                source.renaming = True
                io.mutate(change, after)
                await source.remove_entered.wait()
                matches = [task for task in asyncio.all_tasks(hass.loop)
                           if task is not asyncio.current_task() and any(
                               frame.f_code is Entity._async_process_registry_update_or_remove.__code__
                               and frame.f_locals.get('self') is source
                               for frame in _await_frames(task))]
                self.assertEqual(len(matches), 1)
                callback_tasks.append(matches[0])
                self.assertIn(matches[0], hass._tasks)

            settle = asyncio.create_task(io.settle(changes[0]))
            turn = hass.loop.create_future()
            hass.loop.call_soon(turn.set_result, None)
            await turn
            self.assertFalse(settle.done(), 'settle lost the natural removal await')
            source = sources[0]
            source.remove_release.set()
            await source.add_entered.wait()
            self.assertFalse(settle.done(), 'settle lost the natural re-add await')
            self.assertFalse(callback_tasks[0].done())
            source.add_release.set()
            await settle
            self.assertTrue(callback_tasks[0].done())
            self.assertIsNone(callback_tasks[0].result())
            self.assertFalse(callback_tasks[1].done(), 'unrelated source must still be held')
            self.assertIs(source.platform.entities.get('sensor.scoped_result_0'), source)
            self.assertIs(source.registry_entry, self.f.entity.async_get('sensor.scoped_result_0'))
            self.assertEqual(source.registry_entry.id, changes[0].identity)
            self.assertIsNone(hass.states.get(changes[0].before['entity_id']))
            self.assertEqual(hass.states.get('sensor.scoped_result_0').state, '5')
            self.assertTrue(io.runtime(changes[0], changes[0].after))
            await io.flush('entity')
            disk = await io.disk('entity')
            row = next(row for row in disk['entities'] if row['id'] == changes[0].identity)
            self.assertEqual(row['entity_id'], 'sensor.scoped_result_0')
            self.assertEqual(row['unique_id'], changes[0].before['unique_id'])
        finally:
            for source in sources:
                source.remove_release.set()
                source.add_release.set()
            if settle is not None:
                await asyncio.gather(settle, return_exceptions=True)
            await asyncio.gather(*callback_tasks, return_exceptions=True)
