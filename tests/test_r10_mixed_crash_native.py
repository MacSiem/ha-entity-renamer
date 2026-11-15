"""One mixed Energy cut after native write and before its durable receipt.

Each phase runs in a separate real HA interpreter on owned synthetic storage.
The uncertain operation is inspected once, never replayed or inverted.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_energy_continuity_mixed_cold_native as mixed


class MixedCrashPhase(mixed.MixedPowercalcColdPhase):
    async def test_phase(self):
        hass, root = self.f.hass, self.f.root
        runtime = self._runtime()
        evidence = Path('/qa/r10-mixed-crash')
        evidence.mkdir(exist_ok=True)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        if self.phase == 'forward':
            request, socket_witness, client_witness = await self._transport()
            await self.sum_energy._prev_state_store.persist_states()
            from homeassistant.helpers import issue_registry
            issue = issue_registry.async_get(hass)
            await issue._store.async_save(issue._data_to_save())
            reply = await request(1, 'prepare', entities=[
                {'entity_id': self.sources[0].entity_id, 'new_entity_id': 'light.public_static_target'},
                {'entity_id': self.sum_energy.entity_id, 'new_entity_id': 'sensor.synthetic_new_sum_energy'}])
            self.assertTrue(reply.get('success'), reply)
            op, fingerprint = reply['result']['operation_id'], reply['result']['fingerprint']
            (evidence / 'prepare.json').write_text(json.dumps(reply, sort_keys=True))
            mod = mixed.cold.backend_fixture.installed_module
            native = mod('energy_native').NativeEnergy
            original = native.write
            async def cut_after_native(instance, *args, **kwargs):
                result = await original(instance, *args, **kwargs)
                # Capture actual durable stores at the seam before killing the
                # interpreter; this metadata is diagnostic only, not authority.
                persisted = {}
                for name in ('.storage/powercalc_group', '.storage/core.restore_state'):
                    path = root / name
                    raw, stat = path.read_bytes(), path.stat()
                    persisted[name] = [stat.st_dev, stat.st_ino, hashlib.sha256(raw).hexdigest(),
                                       base64.b64encode(raw).decode()]
                pss = json.loads(base64.b64decode(persisted['.storage/powercalc_group'][3]))['data']
                groups = [key for key in ('sensor.synthetic_new_sum_energy', 'sensor.public_sum_energy') if key in pss]
                meta = {'pid': os.getpid(), 'operation': op, 'fingerprint': fingerprint,
                        'persisted': persisted, 'active_group': groups[0] if groups else None,
                        'backend_entry_id': self.backend_entry_id,
                        'component_sha256': self.public_source_sha256,
                        'user_id': self.user_id, 'pss_groups': groups}
                with self.meta_path.open('x') as stream:
                    json.dump(meta, stream, sort_keys=True); stream.flush(); os.fsync(stream.fileno())
                print('R10_CUT_AFTER_REAL_ENERGY_WRITE_BEFORE_RECEIPT', flush=True)
                os._exit(73)
            with patch.object(native, 'write', cut_after_native):
                reply = await request(2, 'apply', operation_id=op, fingerprint=fingerprint)
            (evidence / 'unexpected-apply.json').write_text(json.dumps(reply, sort_keys=True))
            await socket_witness.close(); await client_witness.close()
            self.fail('actual Energy write boundary was not reached')
        # Save readback before any assert, even if recovery returns an error.
        from unittest.mock import patch as observer_patch
        mod = mixed.cold.backend_fixture.installed_module
        native_io = mod('native_registry').NativeRegistryIO
        native_energy = mod('energy_native').NativeEnergy
        mutations = {'registry': 0, 'energy': 0}
        original_registry, original_energy = native_io.mutate, native_energy.write
        def count_registry(*args, **kwargs):
            mutations['registry'] += 1
            return original_registry(*args, **kwargs)
        async def count_energy(*args, **kwargs):
            mutations['energy'] += 1
            return await original_energy(*args, **kwargs)
        record = await runtime.journal.load(self.meta['operation'])
        output = {'pid': os.getpid(), 'forward_pid': self.meta['pid'],
                  'journal_status': record.status, 'journal_phases': record.phases,
                  'journal_direction': record.direction, 'operation': record.operation_id}
        try:
            with observer_patch.object(native_io, 'mutate', count_registry), observer_patch.object(native_energy, 'write', count_energy):
                result = await runtime.recovery_inspect(hass, self.meta['operation'], self.meta['fingerprint'],
                    guard=lambda: None, refresh=refresh)
                output['inspect'] = result
        except BaseException as error:
            output['inspect_exception'] = type(error).__name__ + ': ' + str(error)
        output['mutation_counts'] = mutations
        output['inverse_attempted'] = False
        (evidence / 'resume-readback.json').write_text(json.dumps(output, sort_keys=True, default=str))
        print('R10_FRESH_READBACK', json.dumps(output, sort_keys=True, default=str), flush=True)
        self.assertNotIn('inspect_exception', output)
        self.assertEqual((output['inspect']['state'], output['inspect']['eligible']), ('UNKNOWN', False))
        self.assertEqual(mutations, {'registry': 0, 'energy': 0})


@unittest.skipUnless(mixed.cold.backend_fixture.activation.fixtures.HAS_HA, 'actual supported HA required')
class MixedCrashTest(unittest.TestCase):
    def test_mixed_energy_effect_crash_fresh_inspect(self):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='r10-mixed-', dir=parent) as directory:
            root = Path(directory) / 'fixture'; root.mkdir()
            evidence = Path('/qa/r10-mixed-crash'); evidence.mkdir(exist_ok=False)
            meta = Path(directory) / 'oracle.json'
            env = dict(os.environ, RENAMER_POWER_COLD_CONFIG=str(root), RENAMER_POWER_COLD_MODE='migration',
                       RENAMER_POWER_COLD_META=str(meta), RENAMER_POWER_COLD_GROUP_ONLY='0')
            for phase, expected in (('forward', 73), ('resume', 0)):
                env['RENAMER_POWER_COLD_PHASE'] = phase
                result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                    'MixedCrashPhase.test_phase'], env=env, capture_output=True, text=True, timeout=3600)
                (evidence / (phase + '.stdout')).write_text(result.stdout)
                (evidence / (phase + '.stderr')).write_text(result.stderr)
                if meta.exists(): shutil.copyfile(meta, evidence / (phase + '-oracle.json'))
                # Retain the whole boundary before any assertion or next phase.
                shutil.copytree(root, evidence / (phase + '-config'), symlinks=True)
                print(result.stdout, flush=True)
                if result.returncode != expected:
                    print(result.stderr, flush=True)
                    self.fail(f'{phase} returned {result.returncode}, expected {expected}; boundary retained')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(MixedCrashTest)


if __name__ == '__main__':
    unittest.main()
