"""Real persisted-source crash at setup effect receipt save; owner FIFO only.

Forward wraps the existing immutable event create solely to kill before its ACK.
No state, journal, root, native receipt or proof is constructed by this fixture.
"""
import base64
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_native_cold_process_recovery as cold


class RecoveryColdPhase(cold.ColdPhase):
    async def _diagnostic(self, store, payload):
        # Read actual immutable preparations after genuine forward native effects.
        activation = payload['activation']
        root = store.read(activation['root'][0])['payload']
        binding = root['binding']
        inputs = tuple(binding['native_inputs'])
        baseline, _ = store.preparations.resolve_composition(inputs)
        prep_api = importlib.import_module(type(store.preparations).__module__)
        ref = 'template-entry-options'
        helper = prep_api.decode_change(store.preparations.resolve(ref, binding['preparations'][ref]))
        original = helper.before
        entry_id = original['identity']['entry_id']
        doc = json.loads(next(f.manager_json for f in baseline.files if f.key == 'core.entity_registry'))
        row, = [r for r in doc['entities'] if r.get('config_entry_id') == entry_id]
        identity = tuple(row.get(k) for k in ('entity_id', 'id', 'unique_id',
                                             'config_entry_id', 'hidden_by', 'name'))
        old = next(iter(helper.mapping))
        source_uuid = next(r['id'] for r in doc['entities'] if r['entity_id'] == old)
        recorder = cold.backend_fixture.installed_module('recorder_guard').NativeRecorderGuard(self.f.hass)
        await recorder.async_synchronize()
        data = dict(pid=os.getpid(), backend_entry_id=self.backend_entry_id,
            public_source_sha256=self.public_source_sha256, operation=binding['operation_id'],
            fingerprint=binding['fingerprint'], baseline_digest=baseline.digest,
            receipt_b64=base64.b64encode(baseline.receipt_json).decode(),
            consumer_identity=identity, original=original, mapping=helper.mapping,
            source_uuid=source_uuid)
        with self.meta_path.open('x') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())

    async def test_phase(self):
        mode = os.environ['RENAMER_COLD_RECOVERY_CASE']
        if self.phase == 'forward':
            module = cold.backend_fixture.activation.fixtures.PACKAGE
            receipt_api = importlib.import_module(module + '.native_registry_receipts')
            native_create = receipt_api.RegistryReceiptStore.create
            async def crash_at_effect_ack(store, key, payload):
                if (payload.get('kind') == 'helper_setup_receipt'
                        and payload['binding']['direction'] == 'forward'):
                    # Negative stops BEFORE the same real save, leaving only its
                    # durable native intent. Positive stops AFTER save/readback.
                    if mode != 'missing_effect':
                        await native_create(store, key, payload)
                    await self._diagnostic(store, payload)
                    os._exit(73)
                return await native_create(store, key, payload)
            with patch.object(receipt_api.RegistryReceiptStore, 'create', crash_at_effect_ack):
                await self._scope(entry_unique_id='cold-recovery-named',
                                  full_flow=True, fresh_reverse=True)
            self.fail('actual setup receipt boundary was not reached')
        else:
            hass = self.f.hass
            api = cold.backend_fixture.installed_module('native_template_reverse')
            lease_api = cold.backend_fixture.installed_module('native_helper_activation_lease')
            backend = self._loaded_backend()
            runtime = hass.config_entries.async_get_entry(self.backend_entry_id).runtime_data
            m = self.meta
            entry = hass.config_entries.async_get_entry(m['original']['identity']['entry_id'])
            revoked = False
            def guard():
                if revoked:
                    raise PermissionError('test_admin_revoked')
                self.assertIs(hass.data[backend.DATA_RUNTIME], runtime)
                self.assertTrue(runtime.operation_lock.locked())
            refresh_calls = 0
            async def refresh():
                nonlocal revoked, refresh_calls
                guard()
                refresh_calls += 1
                if mode == 'revoked' and refresh_calls == 3:
                    revoked = True
            lease = None
            before = {p.name: p.read_bytes() for p in (self.f.root / '.storage').iterdir()
                if p.name in ('ha_entity_renamer.migrations', 'ha_entity_renamer.prepared',
                              'ha_entity_renamer.registry_receipts', 'ha_entity_renamer.helper_lifecycle',
                              'core.entity_registry', 'core.device_registry', 'core.config_entries')}
            self.assertEqual(len(before), 7)
            from homeassistant.helpers.entity_platform import async_get_platforms
            def consumers():
                return tuple(e for p in async_get_platforms(hass, 'template')
                             if p.config_entry is entry for e in p.entities.values())
            loaded_before = consumers()
            try:
                async with runtime.operation_lock:
                    lease = await lease_api.NativeHelperActivationLease.acquire(
                        hass, hass.config_entries, (entry,), guard=guard)
                    kwargs = dict(activation_lease=lease, guard=guard, refresh=refresh)
                    if mode == 'revoked':
                        with self.assertRaises(PermissionError):
                            await api.NativeTemplateReverse.classify_setup_ack(
                                hass, m['operation'], m['fingerprint'], **kwargs)
                        return
                    if mode == 'recover':
                        # Prove the actual existing limit before exercising the
                        # new classifier, including on a baseline RED candidate.
                        with self.assertRaisesRegex(api.Refused, 'forward_activation_incomplete'):
                            await api.NativeTemplateReverse.bind(hass, m['operation'], m['fingerprint'], **kwargs)
                        refused = await api.NativeTemplateReverse.classify_setup_ack(
                            hass, m['operation'], '0' * 64, **kwargs)
                        self.assertEqual(refused.status, 'refused')
                    classification = await api.NativeTemplateReverse.classify_setup_ack(
                        hass, m['operation'], m['fingerprint'], **kwargs)
                    if mode == 'missing_effect':
                        self.assertEqual(classification.status, 'UNKNOWN')
                        with self.assertRaises((ValueError, api.Refused)):
                            await api.NativeTemplateReverse.recover_setup_ack(
                                hass, m['operation'], m['fingerprint'], **kwargs)
                        return
                    self.assertEqual((classification.status, classification.reason),
                                     ('applied', 'setup_ack_missing'))
                    after_classify = {name: (self.f.root / '.storage' / name).read_bytes() for name in before}
                    self.assertEqual(before, after_classify)
                    result = await api.NativeTemplateReverse.recover_setup_ack(
                        hass, m['operation'], m['fingerprint'], **kwargs)
                    self.assertEqual((result.status, result.reason), ('applied', 'setup_ack_recovered'))
                    # A second recovery must read complete state, not duplicate
                    # the marker or call setup/rename/undo. Runtime still reads.
                    recovered = (self.f.root / '.storage' / 'ha_entity_renamer.helper_lifecycle').read_bytes()
                    result = await api.NativeTemplateReverse.recover_setup_ack(
                        hass, m['operation'], m['fingerprint'], **kwargs)
                    self.assertEqual((result.status, result.reason), ('applied', 'complete'))
                    self.assertEqual(recovered, (self.f.root / '.storage' / 'ha_entity_renamer.helper_lifecycle').read_bytes())
                    self.assertEqual(consumers(), loaded_before)
                    for name, raw in before.items():
                        if name != 'ha_entity_renamer.helper_lifecycle':
                            self.assertEqual(raw, (self.f.root / '.storage' / name).read_bytes())
            finally:
                if lease is not None:
                    lease.release()
                if mode in ('missing_effect', 'revoked'):
                    self.assertEqual(before, {name: (self.f.root / '.storage' / name).read_bytes() for name in before})
            # Same proven cold scenario now binds only through its original
            # strict public product binder, performs reverse and finite history checks.
            await cold.ColdPhase.test_phase(self)


@unittest.skipUnless(cold.backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativeColdSetupAckRecoveryTests(unittest.TestCase):
    def _run(self, case):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='renamer-cold-setup-ack-', dir=parent) as directory:
            root = Path(directory) / 'config'
            root.mkdir()
            env = dict(os.environ, RENAMER_COLD_CONFIG=str(root),
                RENAMER_COLD_META=str(Path(directory) / 'diagnostic.json'),
                RENAMER_COLD_UID='named', RENAMER_COLD_RECOVERY_CASE=case)
            for phase, code in (('forward', 73), ('resume', 0)):
                env['RENAMER_COLD_PHASE'] = phase
                result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                    'RecoveryColdPhase.test_phase'], env=env, capture_output=True, text=True, timeout=180)
                print(result.stdout)
                self.assertEqual(result.returncode, code, result.stderr[-16000:])

    def test_setup_effect_ack_crash_classifies_and_recovers_before_same_original_reverse(self):
        self._run('recover')

    def test_setup_intent_without_native_effect_receipt_stays_unknown_without_replay(self):
        self._run('missing_effect')

    def test_cold_setup_recovery_auth_revocation_writes_nothing(self):
        self._run('revoked')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativeColdSetupAckRecoveryTests)


if __name__ == '__main__':
    unittest.main()
