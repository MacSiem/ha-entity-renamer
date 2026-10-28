"""DRAFT: same on-disk ORIGINAL through abrupt Python loss; owner runs via FIFO.

The custom source is a real persisted ConfigEntry/platform, never hass.states.set.
Only diagnostic expected values cross processes. Product bind reopens all authority.
"""
import asyncio
import base64
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import test_native_backend_lifecycle as backend_fixture
import test_receipted_helper_native as receipted

SOURCE = {
    'manifest.json': json.dumps({'domain': 'renamer_cold_source', 'name': 'Cold source fixture',
        'version': '0.0.1', 'documentation': 'https://www.home-assistant.io/',
        'requirements': [], 'codeowners': [], 'iot_class': 'local_push'}),
    '__init__.py': '''async def async_setup(hass, config):
    return True
async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ['sensor'])
    return True
async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, ['sensor'])
''',
    'sensor.py': '''from homeassistant.components.sensor import SensorEntity
async def async_setup_entry(hass, entry, async_add_entities):
    entity = ColdSource(entry)
    async_add_entities([entity])
    hass.data['renamer_cold_source_entity'] = entity
class ColdSource(SensorEntity):
    _attr_name = 'Template entry activation producer'
    _attr_should_poll = False
    def __init__(self, entry):
        self._attr_unique_id = entry.entry_id
        self._attr_native_value = entry.options['initial']
''',
}


class ColdPhase(backend_fixture.NativeBackendLifecycleTests):
    """Selected explicitly in a new Python executable, never inherited discovery."""
    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        self.phase = os.environ['RENAMER_COLD_PHASE']
        root = Path(os.environ['RENAMER_COLD_CONFIG']).resolve(strict=True)
        self.meta_path = Path(os.environ['RENAMER_COLD_META'])  # OUTSIDE config root
        hass = HomeAssistant(str(root))
        self.addAsyncCleanup(hass.async_stop, force=True)
        if self.phase == 'forward':
            (root / 'configuration.yaml').write_bytes(b'script: {}\n')
            await super()._before_loader(hass, root)
            package = root / 'custom_components' / 'renamer_cold_source'
            package.mkdir()
            for name, body in SOURCE.items():
                (package / name).write_text(body)
        else:
            self.meta = json.loads(self.meta_path.read_text())
            self.assertNotEqual(os.getpid(), self.meta['pid'])
            self.backend_entry_id = self.meta['backend_entry_id']
            self.installed_component = root / 'custom_components' / 'ha_entity_renamer'
            self.public_source_sha256 = self.meta['public_source_sha256']
            for name, body in SOURCE.items():
                self.assertEqual((root / 'custom_components' / 'renamer_cold_source' / name).read_text(), body)
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await loader.async_get_custom_components(hass)
        self.assertIsNot(await async_load_base_functionality(hass), False)
        if self.phase == 'forward':
            await super()._before_start(hass, root)
        else:
            # Actual managers load their native persisted Stores. No ConfigEntry
            # construction, manual .storage restoration, callback replay or state seed.
            await receipted.NativeReceiptedHelperTests._before_start(self, hass, root)
            from homeassistant.setup import async_setup_component
            for domain in ('renamer_cold_source', 'template', 'ha_entity_renamer'):
                self.assertTrue(await async_setup_component(hass, domain, {}), domain)
            await hass.async_block_till_done()
        await hass.async_start()
        self.f = SimpleNamespace(hass=hass, root=root, version=__version__,
            entity=er.async_get(hass), device=dr.async_get(hass))
        self.f.managers = (self.f.entity, self.f.device, hass.config_entries)
        if self.phase == 'resume':
            from homeassistant.helpers.entity_platform import async_get_platforms
            self.producer = hass.data['renamer_cold_source_entity']
            self.source_id = next(iter(self.meta['mapping']))
            self.consumer = next(e for p in async_get_platforms(hass, 'template')
                for e in p.entities.values() if e.entity_id == self.meta['consumer_identity'][0])
            self.template_variant, self.consumer_domain = 'sensor', 'sensor'
            self.assertEqual(self.producer.entity_id, next(iter(self.meta['mapping'].values())))
            self.assertEqual(hass.states.get(self.producer.entity_id).state, '7')
            self.assertEqual(hass.states.get(self.consumer.entity_id).state, '14')
            self.assertEqual(self.f.entity.async_get(self.producer.entity_id).id, self.meta['source_uuid'])
            # Check full foreign native rows before reverse; product binder then
            # independently verifies its own accepted full-three chain.
            self._loaded_backend()

    async def _prepare_cold_source(self, hass):
        from homeassistant.config_entries import ConfigEntry
        from types import MappingProxyType
        entry = ConfigEntry(domain='renamer_cold_source', title='Persistent cold source',
            data={}, options={'initial': 7}, source='user', version=1, minor_version=1,
            discovery_keys=MappingProxyType({}), subentries_data=[], unique_id='cold-source')
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        self.assertEqual(entry.state.value, 'loaded')
        return hass.data['renamer_cold_source_entity']

    async def _reload_backend_for_reverse(self, operation, fingerprint, baseline_digest, original_receipt_json):
        # Scenario's test hook: THIS process already cold-booted the backend.
        # No extra entry reload. Resolve only from actual runtime native stores.
        backend = self._loaded_backend()
        runtime = self.f.hass.config_entries.async_get_entry(self.backend_entry_id).runtime_data
        self.assertIs(type(runtime), backend.Runtime)
        self.assertIs(self.f.hass.data[backend.DATA_RUNTIME], runtime)
        record = await runtime.journal.load(operation)
        self.assertEqual((record.status, record.direction), ('applied', 'forward'))
        self.assertEqual(record.plan.fingerprint, fingerprint)
        baseline, _ = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self.assertEqual(baseline.digest, baseline_digest)
        self.assertEqual(baseline.receipt_json, original_receipt_json)
        self._assert_backend_original_row(baseline)
        return backend_fixture.installed_module

    async def test_phase(self):
        if self.phase == 'forward':
            detached = await self._scope(entry_unique_id=None if os.environ['RENAMER_COLD_UID'] == 'none'
                else 'cold-named', full_flow=True, fresh_reverse=True)
            values, refs = detached
            gc.collect()
            self.assertTrue(all(ref() is None for ref in refs))
            _, operation, fingerprint, digest, receipt, identity, original, mapping, fault = values
            self.assertIsNone(fault)
            await self._flush_documents()
            await backend_fixture.installed_module('recorder_guard').NativeRecorderGuard(
                self.f.hass).async_synchronize()
            # Diagnostic metadata is never imported into preparations/journal.
            data = dict(pid=os.getpid(), backend_entry_id=self.backend_entry_id,
                public_source_sha256=self.public_source_sha256, operation=operation,
                fingerprint=fingerprint, baseline_digest=digest,
                receipt_b64=base64.b64encode(receipt).decode(), consumer_identity=identity,
                original=original, mapping=mapping,
                source_uuid=self.f.entity.async_get(self.producer.entity_id).id)
            with self.meta_path.open('x') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            # No hass.async_stop, no final callbacks, no object serialization.
            os._exit(73)
        else:
            from native_template_composition_scenario import fresh_reverse_scenario
            m = self.meta
            await fresh_reverse_scenario(self, backend_fixture.installed_module,
                m['operation'], m['fingerprint'], m['baseline_digest'],
                base64.b64decode(m['receipt_b64']), tuple(m['consumer_identity']),
                m['original'], m['mapping'], None, native_backend_lifecycle=True)
            print(json.dumps({'cold_pid': os.getpid(), 'forward_pid': m['pid'],
                              'same_original_reverse': True}))


@unittest.skipUnless(backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativeColdProcessRecoveryTests(unittest.TestCase):
    def _run_cold(self, uid):
        # Root remains on the native runner's owned SSD mount for both children.
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='renamer-cold-', dir=parent) as directory:
            root = Path(directory) / 'config'
            root.mkdir()
            env = dict(os.environ, RENAMER_COLD_CONFIG=str(root),
                RENAMER_COLD_META=str(Path(directory) / 'diagnostic.json'),
                RENAMER_COLD_UID=uid)
            for phase, code in (('forward', 73), ('resume', 0)):
                env['RENAMER_COLD_PHASE'] = phase
                result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                    'ColdPhase.test_phase'], env=env, capture_output=True, text=True, timeout=180)
                print(result.stdout)
                self.assertEqual(result.returncode, code, result.stderr[-12000:])

    def test_named_actual_process_loss_then_same_original_reverse(self):
        self._run_cold('named')

    def test_none_actual_process_loss_then_same_original_reverse(self):
        self._run_cold('none')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativeColdProcessRecoveryTests)


if __name__ == '__main__':
    unittest.main()
