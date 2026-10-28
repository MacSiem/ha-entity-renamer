"""Owner FIFO: each case uses a fresh native Core process and public backend.

Data-only YAML fixture, real server-produced Plan, registered ActiveConnection,
actual AuthManager and original strict bind_reverse. No test-built Plan, receipt,
prepared reference, accepted image, positive verifier or replacement IO.
"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

import test_native_backend_auth as auth_fixture
import test_native_template_yaml_server as yaml_fixture

backend_fixture = auth_fixture.backend_fixture


@unittest.skipUnless(yaml_fixture.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativePublicYAMLRecoveryTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = yaml_fixture.NativeTemplateYAMLServerTests.asyncSetUp
    _before_loader = backend_fixture.NativeBackendLifecycleTests._before_loader
    _before_start = backend_fixture.NativeBackendLifecycleTests._before_start
    _loaded_backend = backend_fixture.NativeBackendLifecycleTests._loaded_backend
    _flush_documents = yaml_fixture.NativeTemplateYAMLServerTests._flush_documents
    prepare = yaml_fixture.NativeTemplateYAMLServerTests.prepare
    emit = yaml_fixture.NativeTemplateYAMLServerTests.emit
    data = yaml_fixture.NativeTemplateYAMLServerTests.data
    stimulus = yaml_fixture.NativeTemplateYAMLServerTests.stimulus
    _connection = auth_fixture.NativeBackendAuthTests._connection

    def module(self, name):
        return backend_fixture.installed_module(name)

    async def _runtime(self, module):
        backend = self._loaded_backend()
        runtime = self.f.hass.data[backend.DATA_RUNTIME]
        entry = self.f.hass.config_entries.async_get_entry(self.backend_entry_id)
        self.assertIs(runtime, entry.runtime_data)
        self.assertIs(type(runtime), backend.Runtime)
        return runtime

    def _files(self):
        keys = ('ha_entity_renamer.migrations', 'ha_entity_renamer.prepared',
                'ha_entity_renamer.registry_receipts', 'core.entity_registry',
                'core.device_registry', 'core.config_entries')
        return {**{k: (self.f.root / '.storage' / k).read_bytes() for k in keys},
                **{k: (self.f.root / k).read_bytes()
                   for k in ('configuration.yaml', 'template.yaml')}}

    async def _ws(self, action, result=None, *, admin=True):
        connection, user, output = await self._connection(admin=admin)
        command = 'ha_entity_renamer/' + action
        handler = {'status':'ws_status', 'recovery/inspect':'ws_recovery_inspect',
                   'recovery/restore':'ws_recovery_restore'}[action]
        self.assertIs(connection.handlers[command][0], getattr(self.module('websocket_api'), handler))
        message = {'id':1, 'type':command}
        if result is not None:
            message.update(operation_id=result['operation_id'], fingerprint=result['fingerprint'])
        connection.async_handle(message)
        return await asyncio.wait_for(output, 180)

    async def _forward(self, *, multiple=False, variant='sensor', uidless=False, status_probe=False):
        runtime, mapping, uuids, original = await self.data(
            multiple=multiple, variant=variant, uidless=uidless)
        self.assertFalse(hasattr(self, 'plan'))
        self.assertFalse(hasattr(self, 'receipt'))
        for value in (5, 7):
            await self.stimulus(value)
        connection, _, _ = await self._connection(admin=True)
        guard, refresh = self.module('websocket_api')._recovery_authority(
            self.f.hass, connection, runtime)
        poll_task = None
        tool = None
        if status_probe:
            code = self.module('native_yaml_transaction').ReceiptedYAMLTransactionAdapter.apply.__code__
            async def poll():
                response = await self._ws('status')
                self.assertTrue(response['success'], response)
                self.assertTrue(runtime.operation_lock.locked(), 'status must finish during the actual owned effect')
                self.assertFalse(response['result']['migration_ready'])
                self.assertTrue(response['result']['operations'])
                self.assertTrue(all(not row['eligible'] for row in response['result']['operations']))
            def observe(code_arg, offset):
                nonlocal poll_task
                self.assertTrue(runtime.operation_lock.locked())
                self.assertIsNone(poll_task)
                poll_task = self.f.hass.async_create_background_task(poll(), 'actual public status during native YAML apply')
            tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
            sys.monitoring.use_tool_id(tool, 'public YAML status during actual forward')
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, observe)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
        try:
            try:
                result = await runtime.native_forward(self.f.hass, mapping, guard=guard, refresh=refresh)
            except self.module('yaml_validation').YAMLValidationError as exc:
                if str(exc) == 'validation_graph_changed':
                    trace = exc.__traceback__
                    while trace is not None:
                        frame = trace.tb_frame
                        if frame.f_code.co_name == 'assert_validation_current':
                            values = frame.f_locals
                            print('QA_GRAPH_DIFF', values.get('path'),
                                  'expected', values.get('expected'), 'current', values.get('current'))
                        trace = trace.tb_next
                raise
            if status_probe:
                self.assertIsNotNone(poll_task)
                await asyncio.wait_for(poll_task, 60)
        finally:
            if tool is not None:
                sys.monitoring.set_local_events(tool, code, 0)
                sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
                sys.monitoring.free_tool_id(tool)
                if poll_task is not None and not poll_task.done():
                    poll_task.cancel()
                    await asyncio.gather(poll_task, return_exceptions=True)
        self.assertEqual(result['state'], 'applied')
        record = await runtime.journal.load(result['operation_id'])
        self.assertEqual(tuple(s.adapter for s in record.plan.steps), ('registry',) * len(mapping) + ('yaml',))
        baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self._original = (record.plan, baseline.receipt_json, composition)
        self.assertEqual(len(mapping), len(uuids))
        for old, new in mapping.items():
            self.assertIsNone(self.f.entity.async_get(old))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        for value in (5, 7):
            await self.stimulus(value)
        return runtime, mapping, uuids, original, result

    async def _flow(self, *, multiple=False, variant='sensor', uidless=False):
        runtime, mapping, uuids, original, result = await self._forward(
            multiple=multiple, variant=variant, uidless=uidless)
        before = self._files()
        caches = (runtime.journal, runtime.preparations)
        status = await self._ws('status')
        self.assertIs(runtime.journal, caches[0])
        self.assertIs(runtime.preparations, caches[1])
        self.assertTrue(status['success'], status)
        row, = [r for r in status['result']['operations'] if r['operation_id'] == result['operation_id']]
        self.assertEqual((row['fingerprint'], row['eligible']), (result['fingerprint'], False))
        self.assertFalse(status['result']['migration_ready'])
        for action in ('recovery/inspect','recovery/restore'):
            denied = await self._ws(action, result, admin=False)
            self.assertFalse(denied['success'])
            self.assertEqual(denied['error']['code'], 'unauthorized')
        inspected = await self._ws('recovery/inspect', result)
        self.assertTrue(inspected['success'], inspected)
        detail = inspected['result']
        self.assertEqual((detail['state'], detail['eligible']), ('applied', True))
        self.assertEqual(row['reason'], 'inspection_available')
        plan, receipt_json, composition = self._original
        consumers = [(r.domain.removeprefix('template.'), r.native_key)
                     for r in composition.evidence.template_sources.ledger.definitions
                     if r.domain in ('template.sensor', 'template.binary_sensor')]
        self.assertEqual(detail['change_summary'], {
            'sources':[{'original_entity_id':old,'current_entity_id':new} for old,new in sorted(mapping.items())],
            'consumers':{'count':len(consumers),'domains':sorted({d for d,_ in consumers})}})
        self.assertEqual(before, self._files(), 'status/inspect must not perform any effect')
        serialized = json.dumps(detail)
        for hidden in ('configuration.yaml','template.yaml','prepared_ref','unique_id','core.entity_registry'):
            self.assertNotIn(hidden, serialized)
        self.assertFalse(detail['migration_ready'])
        self.assertFalse(detail['family_complete'])
        restored = await self._ws('recovery/restore', result)
        self.assertTrue(restored['success'], restored)
        self.assertEqual(restored['result']['state'], 'rolled_back')
        self.assertFalse(restored['result']['migration_ready'])
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        final = await runtime.journal.load(result['operation_id'])
        self.assertEqual(final.plan, plan)
        self.assertEqual(final.phases, ('undone',) * len(plan.steps))
        baseline, reopened = runtime.preparations.resolve_composition(plan.native_inputs)
        self.assertEqual(baseline.receipt_json, receipt_json)
        self.assertEqual(reopened, composition)
        for old in mapping:
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        for value in (5, 7):
            await self.stimulus(value)
        history = self.module('history_guard').NativeHistoryGuard(
            self.module('recorder_guard').NativeRecorderGuard(self.f.hass))
        self.assertTrue(await history.verify(composition.source_history))
        self.assertTrue(await history.verify(composition.consumer_history))
        after = self._files()
        second = await self._ws('recovery/restore', result)
        self.assertTrue(second['success'], second)
        self.assertEqual((second['result']['state'], second['result']['reason']), ('refused','already_finished'))
        self.assertEqual(after, self._files())
        self.assertFalse(runtime.operation_lock.locked())

    async def test_registered_numeric_yaml_status_inspect_restore(self):
        await self._flow()

    async def test_registered_binary_yaml_status_inspect_restore(self):
        await self._flow(variant='binary_sensor')

    async def test_registered_multi_source_mixed_yaml_status_inspect_restore(self):
        await self._flow(multiple=True, variant='mixed')

    async def test_registered_uidless_numeric_yaml_status_inspect_restore(self):
        await self._flow(uidless=True)

    async def test_registered_uidless_binary_yaml_status_inspect_restore(self):
        await self._flow(variant='binary_sensor', uidless=True)

    async def _negative(self, fault):
        runtime, mapping, _, _, result = await self._forward(multiple=True, variant='mixed')
        if fault == 'source_drift':
            self.f.entity.async_update_entity(next(iter(mapping.values())), name='Independent native source edit')
            await self._flush_documents()
        elif fault == 'missing_receipt':
            path = self.f.root / '.storage' / 'ha_entity_renamer.registry_receipts'
            path.rename(path.with_name('quarantined-registry-receipts'))
            # Preserve a missing-file snapshot explicitly; do not create evidence.
        elif fault == 'nonterminal':
            # Negative durable fault stimulus keeps the exact server-built Plan.
            # It is not a fixture-built positive Plan or an accepted terminal image.
            record = await runtime.journal.load(result['operation_id'])
            await runtime.journal.save(replace(record, status='unknown', reason='native_test_interruption'))
        elif fault == 'yaml_drift':
            with (self.f.root / 'template.yaml').open('a') as handle:
                handle.write('\n# independent real source edit\n')
        else:
            raise AssertionError(fault)
        files = lambda: {str(p.relative_to(self.f.root)): p.read_bytes()
            for p in (self.f.root / '.storage').iterdir()
            if p.is_file() and ('renamer' in p.name or p.name.startswith('core.'))} | {
                'template.yaml': (self.f.root / 'template.yaml').read_bytes()}
        before = files()
        status = await self._ws('status')
        self.assertTrue(status['success'], status)
        self.assertTrue(all(not r['eligible'] for r in status['result']['operations']))
        for action in ('recovery/inspect','recovery/restore'):
            response = await self._ws(action, result)
            self.assertTrue(response['success'], response)
            self.assertIn(response['result']['state'], ('UNKNOWN', 'refused'))
            self.assertFalse(response['result']['eligible'])
            self.assertNotIn('change_summary', response['result'])
            self.assertEqual(before, files(), 'negative admission must not replay any native effect')
        for new in mapping.values():
            self.assertIsNotNone(self.f.entity.async_get(new))

    async def test_missing_real_receipt_stays_unknown_without_effects(self):
        await self._negative('missing_receipt')

    async def test_actual_source_registry_edit_refuses_without_effects(self):
        await self._negative('source_drift')

    async def test_actual_yaml_source_edit_refuses_without_effects(self):
        await self._negative('yaml_drift')

    async def test_nonterminal_server_plan_stays_unknown_without_replay(self):
        await self._negative('nonterminal')

    async def _revoked_while_waiting(self, action):
        from homeassistant.auth.const import GROUP_ID_USER
        runtime, _, _, _, result = await self._forward()
        connection, user, output = await self._connection(admin=True)
        seen = self.f.hass.loop.create_future()
        code = asyncio.Lock.acquire.__code__
        wanted = self._loaded_backend().Runtime._recovery.__code__
        lock = runtime.operation_lock
        def observe(code_arg, offset, value):
            frame = sys._getframe()
            while frame is not None and frame.f_code is not code:
                frame = frame.f_back
            if frame is None or frame.f_locals.get('self') is not lock:
                return
            frame = frame.f_back
            while frame is not None:
                if frame.f_code is wanted:
                    if not seen.done():
                        seen.set_result(asyncio.current_task())
                    return
                frame = frame.f_back
        tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(tool, 'public YAML inspect actual role lock wait')
        before = self._files()
        await lock.acquire()
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_YIELD, observe)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_YIELD)
            connection.async_handle({'id':1,'type':'ha_entity_renamer/recovery/' + action,
                'operation_id':result['operation_id'],'fingerprint':result['fingerprint']})
            done, _ = await asyncio.wait((seen,output),timeout=60,return_when=asyncio.FIRST_COMPLETED)
            self.assertIn(seen, done)
            task = seen.result()
            self.assertIn(task._fut_waiter, lock._waiters)
            self.assertFalse(output.done())
            await self.f.hass.auth.async_update_user(user, group_ids=[GROUP_ID_USER])
            self.assertFalse(user.is_admin)
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_YIELD, None)
            sys.monitoring.free_tool_id(tool)
            lock.release()
        response = await asyncio.wait_for(output, 120)
        self.assertFalse(response['success'])
        self.assertEqual(response['error']['code'], 'unauthorized')
        self.assertEqual(before, self._files())
        self.assertFalse(runtime.operation_lock.locked())

    async def test_registered_inspect_rechecks_real_role_after_runtime_lock_wait(self):
        await self._revoked_while_waiting('inspect')

    async def test_registered_restore_rechecks_real_role_after_runtime_lock_wait(self):
        await self._revoked_while_waiting('restore')

    async def test_registered_status_finishes_during_actual_server_forward_effect(self):
        await self._forward(multiple=True, variant='mixed', status_probe=True)

    async def test_registered_status_rechecks_real_role_after_actual_journal_io(self):
        from homeassistant.auth.const import GROUP_ID_USER
        runtime, _, _, _, _ = await self._forward()
        connection, user, output = await self._connection(admin=True)
        code = self.module('journal').DurableJournal.initialize.__code__
        revoked = None
        actual_pending = False
        async def revoke():
            await self.f.hass.auth.async_update_user(user, group_ids=[GROUP_ID_USER])
            self.assertFalse(user.is_admin)
        def observe(code_arg, offset, value):
            nonlocal revoked, actual_pending
            if revoked is None and isinstance(value, asyncio.Future) and not value.done():
                actual_pending = True
                revoked = self.f.hass.async_create_task(revoke(), 'native status user role change', eager_start=False)
        tool = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(tool, 'public YAML status actual journal read revocation')
        before = self._files()
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_YIELD, observe)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_YIELD)
            connection.async_handle({'id':1,'type':'ha_entity_renamer/status'})
            response = await asyncio.wait_for(output, 120)
            self.assertTrue(actual_pending, 'must observe actual journal IO, not a seeded waiter')
            self.assertIsNotNone(revoked)
            await revoked
            self.assertFalse(response['success'])
            self.assertEqual(response['error']['code'], 'unauthorized')
            self.assertNotIn('result', response)
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_YIELD, None)
            sys.monitoring.free_tool_id(tool)
        self.assertEqual(before, self._files())
        self.assertFalse(runtime.operation_lock.locked())

    async def test_status_only_controller_cannot_inspect_or_restore_or_replace_cache(self):
        runtime, _, _, _, result = await self._forward()
        connection, _, _ = await self._connection(admin=True)
        guard, refresh = self.module('websocket_api')._recovery_authority(self.f.hass, connection, runtime)
        await refresh()
        controller = self.module('recovery_controller').RecoveryController(
            self.f.hass, runtime, guard=guard, refresh=refresh, status_only=True)
        caches = runtime.journal, runtime.preparations
        before = self._files()
        for method in (controller.inspect, controller.restore):
            with self.assertRaisesRegex(ValueError, '^recovery_status_only$'):
                await method(result['operation_id'],result['fingerprint'])
        self.assertIs(runtime.journal,caches[0])
        self.assertIs(runtime.preparations,caches[1])
        self.assertEqual(before,self._files())


if __name__ == '__main__':
    unittest.main()
