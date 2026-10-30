"""Selective Script root keeps unrelated real executions and refuses busy targets."""
import asyncio
import sys
import unittest

import test_native_template_mixed_yaml_server as mixed
from native_script_wait_witness import NativeScriptWaitWitness


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeScriptUnrelatedWaitTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_start = mixed.NativeTemplateMixedYAMLServerTests._before_start
    _flush_documents = mixed.NativeTemplateMixedYAMLServerTests._flush_documents
    prepare = mixed.NativeTemplateMixedYAMLServerTests.prepare
    emit = mixed.NativeTemplateMixedYAMLServerTests.emit
    _runtime = mixed.NativeTemplateMixedYAMLServerTests._runtime
    module = mixed.NativeTemplateMixedYAMLServerTests.module
    stimulus = mixed.NativeTemplateMixedYAMLServerTests.stimulus
    data = mixed.NativeTemplateMixedYAMLServerTests.data

    async def test_unrelated_script_actual_wait_survives_full_forward_and_fresh_inverse(self):
        await self._wait_case(affected=False)

    async def test_affected_script_actual_wait_refuses_before_operation_and_native_api(self):
        await self._wait_case(affected=True)

    async def test_legacy_reload_profile_busy_script_refuses_prepare_before_operation(self):
        await self._wait_case(affected=False, legacy=True)

    async def _wait_case(self, *, affected, legacy=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, _, _ = await self.data(with_automation=legacy)
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        path = root / 'mixed_scripts.yaml'
        key = 'mixed_source' if affected else 'foreign_wait'
        body = key + ':\n  sequence:\n  - event: script_wait_started\n'
        if affected:
            body += '    event_data:\n      value: "{{ states(' + repr(old) + ') }}"\n'
        body += ('  - wait_for_trigger:\n    - trigger: event\n' +
            '      event_type: script_wait_release\n    timeout: "00:05:00"\n' +
            '    continue_on_timeout: false\n  - event: script_wait_finished\n')
        path.write_text(body if affected else path.read_text() + body)
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self.stimulus(17)
        await self._flush_documents()
        original = {name: (root / name).read_bytes()
                    for name in ('configuration.yaml', 'template.yaml', 'mixed_scripts.yaml')}
        entity = next(entity for entity in hass.data[DATA_INSTANCES]['script'].entities if entity.unique_id == key)
        script = entity.script
        service = hass.services._services['script'][key]
        events = []
        for kind in ('script_wait_started', 'script_wait_finished'):
            self.addCleanup(hass.bus.async_listen(kind, lambda event: events.append(event.event_type)))
        witness = NativeScriptWaitWitness(hass, script)
        io = self.module('native_registry').NativeRegistryIO
        code, calls = io.mutate.__code__, []
        tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
        sys.monitoring.use_tool_id(tool, 'actual Script root native API count')
        def started(code_arg, offset):
            actor = sys._getframe(1).f_locals.get('self')
            if actor.hass is hass:
                calls.append(actor)
        sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
        sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
        task = hass.async_create_task(hass.services.async_call('script', key, {}, blocking=True,
            context=Context()), 'actual native Script wait service', eager_start=False)
        def same_wait():
            self.assertIs(entity.script, script)
            self.assertIs(hass.services._services['script'][key], service)
            witness.assert_live(self)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            await asyncio.wait_for(witness.ready, 5)
            same_wait()
            if affected or legacy:
                with self.assertRaises((ValueError, self.module('transaction').Refused)):
                    if legacy:
                        await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-admin',
                            guard=lambda: None, refresh=refresh)
                    else:
                        await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                self.assertEqual(calls, [])
                self.assertFalse(await runtime.journal.active())
            else:
                result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                self.assertEqual(result['state'], 'applied')
                same_wait()
                record = await runtime.journal.load(result['operation_id'])
                step = record.plan.steps[-1]
                change = self.module('prepared').decode_change(runtime.preparations.resolve(
                    step.prepared_ref, runtime.preparations.plans[step.prepared_ref]['digest']))
                native = self.module('yaml_runtime_observer')
                observer = native.NativeYAMLRuntimeReadback(hass)
                rows, _ = observer._projection('script')
                self.assertIn(key, [row[0] for row in rows])
                self.assertFalse(native._pending_native_work(hass, change.receipt.changed_domains))
                self.assertEqual(observer._sample(change), (self.module('transaction').Position.UNKNOWN, ()))
                inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                self.assertEqual(inverse['state'], 'rolled_back')
                self.assertEqual(len(calls), 2)
            same_wait()
            for previous, proposed in mapping.items():
                self.assertEqual(self.f.entity.async_get(previous).id, uuids[previous])
                self.assertIsNone(self.f.entity.async_get(proposed))
            for name, contents in original.items():
                self.assertEqual((root / name).read_bytes(), contents)
            self.assertEqual(events, ['script_wait_started'])
            self.assertFalse(runtime.operation_lock.locked())
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
            witness.close()
            hass.bus.async_fire('script_wait_release')
            await asyncio.wait_for(asyncio.shield(task), 5)
            await hass.async_block_till_done()
        self.assertEqual(events, ['script_wait_started', 'script_wait_finished'])
