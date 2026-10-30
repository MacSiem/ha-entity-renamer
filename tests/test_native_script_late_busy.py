"""Actual target starts AFTER bind, during apply refresh, before operation admission."""
import asyncio
import sys
import unittest

import test_native_script_unrelated_wait as waits
from native_script_wait_witness import NativeScriptWaitWitness


@unittest.skipUnless(waits.mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeScriptLateBusyTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = waits.NativeScriptUnrelatedWaitTests.asyncSetUp
    _before_start = waits.NativeScriptUnrelatedWaitTests._before_start
    _flush_documents = waits.NativeScriptUnrelatedWaitTests._flush_documents
    prepare = waits.NativeScriptUnrelatedWaitTests.prepare
    emit = waits.NativeScriptUnrelatedWaitTests.emit
    _runtime = waits.NativeScriptUnrelatedWaitTests._runtime
    module = waits.NativeScriptUnrelatedWaitTests.module
    stimulus = waits.NativeScriptUnrelatedWaitTests.stimulus
    data = waits.NativeScriptUnrelatedWaitTests.data

    async def test_late_busy_direct_apply_has_no_operation_record_or_native_api(self):
        await self._late_busy(retained=False)

    async def test_late_busy_retained_apply_has_no_operation_record_or_native_api(self):
        await self._late_busy(retained=True)

    async def test_late_busy_inverse_preserves_applied_record_until_target_finishes(self):
        await self._late_busy(retained=False, rollback=True)

    async def _late_busy(self, *, retained, rollback=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, _, _ = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        path = root / 'mixed_scripts.yaml'
        path.write_text('mixed_source:\n  sequence:\n  - event: late_script_started\n' +
            '    event_data:\n      value: "{{ states(' + repr(old) + ') }}"\n' +
            '  - wait_for_trigger:\n    - trigger: event\n      event_type: late_script_release\n' +
            '    timeout: "00:05:00"\n    continue_on_timeout: false\n' +
            '  - event: late_script_finished\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self.stimulus(17)
        await self._flush_documents()
        original = {name: (root / name).read_bytes()
                    for name in ('configuration.yaml', 'template.yaml', 'mixed_scripts.yaml')}
        entity = next(iter(hass.data[DATA_INSTANCES]['script'].entities))
        witness = NativeScriptWaitWitness(hass, entity.script)
        events = []
        for kind in ('late_script_started', 'late_script_finished'):
            self.addCleanup(hass.bus.async_listen(kind, lambda event: events.append(event.event_type)))
        server = self.module('native_template_yaml_server').NativeTemplateYAMLServer
        io = self.module('native_registry').NativeRegistryIO
        codes, calls = ((server.rollback if rollback else server.apply).__code__, io.mutate.__code__), []
        armed, task = False, None
        safely_admitted_inverse = False
        tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
        sys.monitoring.use_tool_id(tool, 'actual target starts in apply refresh after bind')
        def started(code, offset):
            nonlocal armed
            actor = sys._getframe(1).f_locals.get('self')
            if actor.hass is not hass:
                return
            if code is codes[0]:
                armed = True
            else:
                calls.append(actor)
        async def refresh():
            nonlocal task
            self.assertTrue(runtime.operation_lock.locked())
            if armed and task is None:
                task = hass.async_create_task(hass.services.async_call('script', 'mixed_source', {},
                    blocking=True, context=Context()), 'actual late native Script wait', eager_start=False)
                await asyncio.wait_for(witness.ready, 5)
                witness.assert_live(self)
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            for code in codes:
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            if rollback:
                applied = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                self.assertEqual(applied['state'], 'applied')
                before_record = await runtime.journal.load(applied['operation_id'])
                current_files = {name: (root / name).read_bytes() for name in original}
                self.assertIsNone(task)
            elif retained:
                review = await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                    guard=lambda: None, refresh=refresh)
                self.assertIsNone(task)
            before_calls = len(calls)
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                if rollback:
                    await runtime.native_yaml_reverse(hass, applied['operation_id'], applied['fingerprint'],
                        guard=lambda: None, refresh=refresh)
                elif retained:
                    await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                        actor_id='synthetic-admin', guard=lambda: None, refresh=refresh)
                else:
                    await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            self.assertIsNotNone(task, 'the target must start only after actual bind')
            witness.assert_live(self)
            self.assertEqual(len(calls), before_calls)
            if rollback:
                self.assertEqual(await runtime.journal.load(applied['operation_id']), before_record)
            else:
                self.assertFalse(await runtime.journal.all(), 'late busy target must not create a pending operation')
            self.assertFalse(runtime.operation_lock.locked())
            for previous, proposed in mapping.items():
                present, absent = (proposed, previous) if rollback else (previous, proposed)
                self.assertEqual(self.f.entity.async_get(present).id, uuids[previous])
                self.assertIsNone(self.f.entity.async_get(absent))
            for name, contents in (current_files if rollback else original).items():
                self.assertEqual((root / name).read_bytes(), contents)
            self.assertEqual(events, ['late_script_started'])
            safely_admitted_inverse = rollback
        finally:
            for code in codes:
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
            witness.close()
            if task is not None:
                hass.bus.async_fire('late_script_release')
                await asyncio.wait_for(asyncio.shield(task), 5)
                await hass.async_block_till_done()
        self.assertEqual(events, ['late_script_started', 'late_script_finished'])
        if safely_admitted_inverse:
            # The exact applied record was independently confirmed unchanged,
            # and the real action has naturally finished. Fresh inverse now.
            restored = await runtime.native_yaml_reverse(hass, applied['operation_id'], applied['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertEqual(restored['state'], 'rolled_back')
            for previous, proposed in mapping.items():
                self.assertEqual(self.f.entity.async_get(previous).id, uuids[previous])
                self.assertIsNone(self.f.entity.async_get(proposed))
            for name, contents in original.items():
                self.assertEqual((root / name).read_bytes(), contents)
