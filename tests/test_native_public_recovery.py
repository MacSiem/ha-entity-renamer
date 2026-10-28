"""Registered public recovery after genuine cold source/process loss; owner FIFO.

Native AuthManager/ActiveConnection; only WS transport output is captured.
Forward kill boundary reuses the qualified native setup ACK-loss fixture9d2a.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_native_cold_setup_ack_recovery as ack
import test_native_backend_auth as auth_fixture

cold = ack.cold


class PublicRecoveryPhase(ack.RecoveryColdPhase):
    _connection = auth_fixture.NativeBackendAuthTests._connection

    def _files(self):
        keys = ('ha_entity_renamer.migrations', 'ha_entity_renamer.prepared',
                'ha_entity_renamer.registry_receipts', 'ha_entity_renamer.helper_lifecycle',
                'core.entity_registry', 'core.device_registry', 'core.config_entries')
        return {k: (self.f.root / '.storage' / k).read_bytes() for k in keys}

    async def _ws(self, action, *, admin=True, fingerprint=None, extra=None):
        connection, user, output = await self._connection(admin=admin)
        command = 'ha_entity_renamer/' + action
        self.assertIn(command, connection.handlers)
        name = {'status': 'ws_status', 'recovery/inspect': 'ws_recovery_inspect',
                'recovery/restore': 'ws_recovery_restore'}[action]
        self.assertIs(connection.handlers[command][0],
            getattr(cold.backend_fixture.installed_module('websocket_api'), name))
        message = {'id': 1, 'type': command}
        if action != 'status':
            message.update(operation_id=self.meta['operation'],
                           fingerprint=fingerprint or self.meta['fingerprint'])
        if extra:
            message.update(extra)
        connection.async_handle(message)
        response = await asyncio.wait_for(output, 120)
        self.assertEqual(response['id'], 1)
        return response

    async def _revocation_wait(self, native):
        from homeassistant.auth.const import GROUP_ID_USER
        module = cold.backend_fixture.installed_module
        backend = self._loaded_backend()
        hass = self.f.hass
        runtime = hass.data[backend.DATA_RUNTIME]
        entry = hass.config_entries.async_get_entry(self.meta['original']['identity']['entry_id'])
        lock = entry.setup_lock if native else runtime.operation_lock
        connection, user, output = await self._connection(admin=True)
        seen = hass.loop.create_future()
        lock_code = asyncio.Lock.acquire.__code__
        observed_task = None
        tool = None
        def observe(code, offset, value):
            nonlocal observed_task
            frame = sys._getframe()
            while frame is not None and frame.f_code is not code:
                frame = frame.f_back
            if frame is None or frame.f_locals.get('self') is not lock:
                return
            frame = frame.f_back
            wanted = (module('recovery_controller')._acquire_lease.__code__ if native
                      else backend.Runtime._recovery.__code__)
            while frame is not None:
                if frame.f_code is wanted:
                    observed_task = asyncio.current_task()
                    if not seen.done():
                        seen.set_result(observed_task)
                    return
                frame = frame.f_back
        for ident in range(6):
            try:
                sys.monitoring.use_tool_id(ident, 'public recovery native auth wait')
            except ValueError:
                continue
            tool = ident
            break
        self.assertIsNotNone(tool)
        before = self._files()
        await lock.acquire()
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_YIELD, observe)
            sys.monitoring.set_local_events(tool, lock_code, sys.monitoring.events.PY_YIELD)
            connection.async_handle({'id': 1, 'type': 'ha_entity_renamer/recovery/restore',
                'operation_id': self.meta['operation'], 'fingerprint': self.meta['fingerprint']})
            done, _ = await asyncio.wait((seen, output), timeout=60,
                                        return_when=asyncio.FIRST_COMPLETED)
            self.assertIn(seen, done, 'registered restore did not actually wait for the native lock')
            task = seen.result()
            self.assertIs(type(task), asyncio.Task)
            self.assertIn(task._fut_waiter, lock._waiters)
            self.assertFalse(task._fut_waiter.done())
            self.assertFalse(output.done())
            await hass.auth.async_update_user(user, group_ids=[GROUP_ID_USER])
            self.assertFalse(user.is_admin)
        finally:
            sys.monitoring.set_local_events(tool, lock_code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_YIELD, None)
            sys.monitoring.free_tool_id(tool)
            lock.release()
        response = await asyncio.wait_for(output, 120)
        self.assertFalse(response['success'])
        self.assertEqual(response['error']['code'], 'unauthorized')
        if observed_task is not None:
            try:
                await observed_task
            except PermissionError:
                self.assertTrue(native, 'only the owned restore effect task propagates auth loss')
        self.assertEqual(before, self._files())
        self.assertFalse(runtime.operation_lock.locked())
        self.assertFalse(entry.setup_lock.locked())

    async def _verify_public_reverse(self):
        module = cold.backend_fixture.installed_module
        hass, m = self.f.hass, self.meta
        runtime = hass.data[self._loaded_backend().DATA_RUNTIME]
        record = await runtime.journal.load(m['operation'])
        self.assertEqual((record.status, record.direction), ('rolled_back', 'rollback'))
        self.assertEqual(record.plan.fingerprint, m['fingerprint'])
        for value in (5, 7):
            await self.emit(value)
        current = self.f.entity.async_get(m['consumer_identity'][0])
        self.assertEqual((current.entity_id, current.id, current.unique_id,
            current.config_entry_id, current.hidden_by, current.name), tuple(m['consumer_identity']))
        self.assertEqual(module('helper_native').read_entry(hass, m['original']['identity']['entry_id']), m['original'])
        self.assertEqual(self.producer.entity_id, next(iter(m['mapping'])))
        self.assertEqual(self.f.entity.async_get(self.producer.entity_id).id, m['source_uuid'])
        p = module('prepared').native_prepared_store(hass)
        await p.initialize()
        baseline, composition = p.resolve_composition(record.plan.native_inputs)
        self.assertEqual(baseline.digest, m['baseline_digest'])
        import base64
        self.assertEqual(baseline.receipt_json, base64.b64decode(m['receipt_b64']))
        history = module('history_guard').NativeHistoryGuard(module('recorder_guard').NativeRecorderGuard(hass))
        self.assertTrue(await history.verify(composition.source_history))
        self.assertTrue(await history.verify(composition.consumer_history))
        rr = module('native_registry_receipts')
        store = rr.native_registry_receipt_store(hass, p)
        await store.initialize()
        reader = rr.RegistryReceiptChain.reopen(store, m['operation'], record.plan,
                                                baseline.receipt, dict(composition.prepared_digests))
        self.assertFalse(reader.writable)
        reader.reconstruct_inputs()
        lm = module('native_helper_lifecycle')
        life = lm.native_helper_lifecycle_store(hass, p)
        await life.initialize()
        roots = [(key, row['payload']) for key, row in life.rows.items()
                 if row['payload'].get('kind') == 'helper_lifecycle_root'
                 and row['payload']['binding']['operation_id'] == m['operation']]
        self.assertEqual(len(roots), 2)
        for key, root in roots:
            readonly = lm.validate_lifecycle_receipt_witness(life, m['operation'], record.plan,
                baseline.receipt, dict(composition.prepared_digests), root_id=key)
            self.assertFalse(readonly.writable)
            self.assertEqual(readonly.inspect()['pending'], [])
            self.assertTrue(readonly.inspect()['activation_receipts_complete'])

    async def test_phase(self):
        case = os.environ['RENAMER_PUBLIC_CASE']
        if self.phase == 'forward':
            if case == 'terminal':
                await cold.ColdPhase.test_phase(self)
            else:
                await ack.RecoveryColdPhase.test_phase(self)
            return
        if case in ('runtime_revoked', 'native_revoked'):
            await self._revocation_wait(case == 'native_revoked')
            return
        before = self._files()
        status = await self._ws('status')
        self.assertTrue(status['success'], status)
        self.assertFalse(status['result']['migration_ready'])
        self.assertIs(status['result']['recovery_required'], case != 'terminal')
        row, = [r for r in status['result']['operations'] if r['operation_id'] == self.meta['operation']]
        self.assertEqual(row['fingerprint'], self.meta['fingerprint'])
        self.assertEqual(row['reason'], 'inspection_available' if case == 'terminal' else 'setup_ack_pending')
        self.assertEqual(before, self._files())
        for command in ('recovery/inspect', 'recovery/restore'):
            denied = await self._ws(command, admin=False)
            self.assertFalse(denied['success'])
            self.assertEqual(denied['error']['code'], 'unauthorized')
        invalid = await self._ws('recovery/restore', extra={'root': 'not-server-authority'})
        self.assertFalse(invalid['success'])
        wrong = await self._ws('recovery/restore', fingerprint='0' * 64)
        self.assertTrue(wrong['success'], wrong)
        self.assertEqual(wrong['result']['state'], 'refused')
        inspected = await self._ws('recovery/inspect')
        self.assertTrue(inspected['success'], inspected)
        detail = inspected['result']
        self.assertFalse(detail['migration_ready'])
        self.assertFalse(detail['family_complete'])
        self.assertEqual(before, self._files())
        if case == 'missing_effect':
            self.assertEqual(detail['state'], 'UNKNOWN')
            self.assertFalse(detail['eligible'])
            restored = await self._ws('recovery/restore')
            self.assertTrue(restored['success'], restored)
            self.assertEqual(restored['result']['state'], 'UNKNOWN')
            self.assertEqual(before, self._files())
            return
        self.assertEqual((detail['state'], detail['reason'], detail['eligible']),
                         ('applied', 'complete' if case == 'terminal' else 'setup_ack_missing', True))
        self.assertEqual(detail['change_summary'], {
            'source': {'current_entity_id': next(iter(self.meta['mapping'].values())),
                       'original_entity_id': next(iter(self.meta['mapping']))},
            'consumers': {'count': 1, 'domains': ['sensor']}})
        self.assertNotIn('configuration.yaml', json.dumps(detail))
        self.assertNotIn('options', detail)
        restored = await self._ws('recovery/restore')
        self.assertTrue(restored['success'], restored)
        self.assertEqual(restored['result']['state'], 'rolled_back')
        await self._verify_public_reverse()
        after = self._files()
        second = await self._ws('recovery/restore')
        self.assertTrue(second['success'], second)
        self.assertEqual((second['result']['state'], second['result']['reason']),
                         ('refused', 'already_finished'))
        self.assertEqual(after, self._files())
        print(json.dumps({'public_registered_recovery': True,
                          'same_original_reverse': True, 'cold_pid': os.getpid()}))


@unittest.skipUnless(cold.backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativePublicRecoveryTests(unittest.TestCase):
    def _run(self, case):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='renamer-public-recovery-', dir=parent) as directory:
            root = Path(directory) / 'config'
            root.mkdir()
            env = dict(os.environ, RENAMER_COLD_CONFIG=str(root),
                RENAMER_COLD_META=str(Path(directory) / 'diagnostic.json'), RENAMER_COLD_UID='named',
                RENAMER_COLD_RECOVERY_CASE='missing_effect' if case == 'missing_effect' else 'recover',
                RENAMER_PUBLIC_CASE=case)
            for phase, code in (('forward', 73), ('resume', 0)):
                env['RENAMER_COLD_PHASE'] = phase
                result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                    'PublicRecoveryPhase.test_phase'], env=env, capture_output=True, text=True, timeout=240)
                print(result.stdout)
                self.assertEqual(result.returncode, code, result.stderr[-20000:])

    def test_registered_status_inspect_restore_after_cold_setup_ack_loss(self):
        self._run('recover')

    def test_registered_terminal_forward_restore_after_process_loss(self):
        self._run('terminal')

    def test_registered_missing_effect_is_unknown_and_cannot_write(self):
        self._run('missing_effect')

    def test_registered_restore_revocation_while_waiting_for_runtime_lock(self):
        self._run('runtime_revoked')

    def test_registered_restore_revocation_while_waiting_for_native_lease(self):
        self._run('native_revoked')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativePublicRecoveryTests)


if __name__ == '__main__':
    unittest.main()
