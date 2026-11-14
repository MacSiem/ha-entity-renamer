"""Private Runtime forward: actual HA foreground caller and native effects.

Not registered WebSocket, public readiness, importer, HACS or browser proof.
Reuse accepted real source/consumer fixture; never pass a test-built Plan.
"""
import asyncio
import importlib
import sys
import unittest

import test_native_template_forward as forward


@unittest.skipUnless(forward.fixtures.HAS_HA, 'actual pinned HA required')
class NativeForwardRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = forward.NativeTemplateForwardTests.asyncSetUp
    _before_start = forward.NativeTemplateForwardTests._before_start
    _flush_documents = forward.NativeTemplateForwardTests._flush_documents
    _same_native_documents = forward.NativeTemplateForwardTests._same_native_documents
    emit = forward.NativeTemplateForwardTests.emit
    _fixture = forward.NativeTemplateForwardTests._fixture
    _runtime = forward.NativeTemplateForwardTests._runtime
    with_yaml = False

    async def test_actual_foreground_runtime_forward_owns_effect_task_and_fresh_reverse(self):
        module, entry, mapping, identity, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        execute = runtime.native_forward
        factory = module('native_template_forward').NativeTemplateForward
        code = factory.prepare.__func__.__code__
        preparing = []
        caller = None
        tool = None
        source_uuid = self.f.entity.async_get(next(iter(mapping))).id
        def observe(current_code, offset):
            frame = sys._getframe()
            while frame is not None and frame.f_code is not current_code:
                frame = frame.f_back
            if frame is None or frame.f_locals.get('hass') is not self.f.hass:
                return
            task = asyncio.current_task()
            self.assertIs(type(task), asyncio.Task)
            self.assertIsNot(task, caller, 'native lease must belong to the owned effect task')
            self.assertTrue(runtime.operation_lock.locked())
            preparing.append(task)
        async def refresh():
            self.assertIs(self.f.hass.data[module('const').DATA_RUNTIME], runtime)
            self.assertTrue(runtime.operation_lock.locked())
        for ident in range(6):
            try:
                sys.monitoring.use_tool_id(ident, 'Runtime forward actual effect owner')
            except ValueError:
                continue
            tool = ident
            break
        self.assertIsNotNone(tool)
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, observe)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            # Ordinary native foreground HA task, not a tracker edit or a fake
            # waiter. The real product operation executes inside it.
            caller = self.f.hass.async_create_task(execute(self.f.hass, mapping,
                guard=lambda: None, refresh=refresh), 'test Runtime native forward', eager_start=False)
            result = await asyncio.wait_for(asyncio.shield(caller), 120)
            self.assertEqual(result['state'], 'applied')
            self.assertIs(result['migration_ready'], False)
            self.assertIs(result['family_complete'], False)
            self.assertEqual(len(preparing), 1)
            self.assertTrue(preparing[0].done())
            self.assertTrue(caller.done())
            self.assertFalse(runtime.operation_lock.locked())
            self.assertFalse(entry.setup_lock.locked())
            record = await runtime.journal.load(result['operation_id'])
            self.assertEqual(record.plan.fingerprint, result['fingerprint'])
            self.assertEqual((record.status, record.phases), ('applied', ('verified', 'verified')))
            baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
            self.assertEqual(self.f.entity.async_get(next(iter(mapping.values()))).id, source_uuid)
            for value in (5, 7):
                await self.emit(value)
            history = module('history_guard').NativeHistoryGuard(module('recorder_guard').NativeRecorderGuard(self.f.hass))
            self.assertTrue(await history.verify(composition.source_history))
            self.assertTrue(await history.verify(composition.consumer_history))
            from native_template_composition_scenario import fresh_reverse_scenario
            await fresh_reverse_scenario(self, module, result['operation_id'], result['fingerprint'],
                baseline.digest, baseline.receipt_json, identity, original, mapping, None)
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
            if caller is not None and not caller.done():
                caller.cancel()
                try:
                    await caller
                except asyncio.CancelledError:
                    pass
