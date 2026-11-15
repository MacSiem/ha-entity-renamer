"""Two real executables: Script own key/name persistence and SAME ORIGINAL inverse.

Owner executes through the native FIFO runner. Metadata between children is an
assertion oracle only; resumed authority comes from the installed runtime's
native journal and preparation stores. The first interpreter exits abruptly.
"""
import base64
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


class OwnIDColdPhase(backend_fixture.NativeBackendLifecycleTests):
    """One explicitly selected phase in a fresh Python executable."""

    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import device_registry as dr, entity_registry as er

        self.phase = os.environ['RENAMER_OWN_COLD_PHASE']
        root = Path(os.environ['RENAMER_OWN_COLD_CONFIG']).resolve(strict=True)
        self.meta_path = Path(os.environ['RENAMER_OWN_COLD_META'])
        hass = HomeAssistant(str(root))
        self.addAsyncCleanup(hass.async_stop, force=True)
        if self.phase == 'forward':
            (root / 'configuration.yaml').write_bytes(b'script: {}\n')
            await self._before_loader(hass, root)
        else:
            self.meta = json.loads(self.meta_path.read_text())
            self.assertNotEqual(os.getpid(), self.meta['pid'])
            self.installed_component = root / 'custom_components' / 'ha_entity_renamer'
            self.public_source_sha256 = self.meta['public_source_sha256']
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await loader.async_get_custom_components(hass)
        self.assertIsNot(await async_load_base_functionality(hass), False)
        if self.phase == 'forward':
            await self._before_start(hass, root)
        else:
            # Native Stores and the committed YAML are loaded by a new HA and
            # a new interpreter. No callback, State, cache or journal is seeded.
            from homeassistant.config import async_hass_config_yaml
            from homeassistant.setup import async_setup_component
            await receipted.NativeReceiptedHelperTests._before_start(self, hass, root)
            config = await async_hass_config_yaml(hass)
            for domain in ('script', 'template', 'ha_entity_renamer'):
                self.assertTrue(await async_setup_component(hass, domain, config), domain)
            await hass.async_block_till_done()
        await hass.async_start()
        self.f = SimpleNamespace(hass=hass, root=root, version=__version__,
            entity=er.async_get(hass), device=dr.async_get(hass))
        self.f.managers = (self.f.entity, self.f.device, hass.config_entries)
        if self.phase == 'resume':
            # Resolve the live entry from Core, not the diagnostic metadata.
            entries = hass.config_entries.async_entries('ha_entity_renamer')
            self.assertEqual(len(entries), 1)
            self.backend_entry_id = entries[0].entry_id
            self.assertEqual(self.backend_entry_id, self.meta['backend_entry_id'])
            self._loaded_backend()

    def _runtime(self):
        backend = self._loaded_backend()
        entry = self.f.hass.config_entries.async_get_entry(self.backend_entry_id)
        self.assertIsNotNone(entry)
        runtime = entry.runtime_data
        self.assertIs(type(runtime), backend.Runtime)
        self.assertIs(self.f.hass.data.get(backend.DATA_RUNTIME), runtime)
        self.assertTrue(runtime.loaded)
        self.assertFalse(runtime.unloading)
        return runtime

    async def _call(self, key, events):
        from homeassistant.core import Context
        hass = self.f.hass
        before, context = len(events), Context()
        await hass.services.async_call('script', key, {}, blocking=True, context=context)
        await hass.async_block_till_done()
        self.assertEqual(len(events), before + 1)
        self.assertEqual(events[-1].context.id, context.id)
        self.assertEqual(events[-1].data['marker'], 'native_cold')

    def _script(self, key):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        entities = self.f.hass.data[DATA_INSTANCES]['script'].entities
        matches = [entity for entity in entities if entity.unique_id == key]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def _observer(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        matches = [entity for platform in async_get_platforms(self.f.hass, 'template')
                   if platform.domain == 'sensor' for entity in platform.entities.values()
                   if entity.unique_id == 'cold_observer-view']
        self.assertEqual(len(matches), 1)
        return matches[0]

    async def _metadata(self, history, mapping):
        return await history.guard.snapshot(mapping)

    async def _event_count(self, recorder):
        # Read actual committed native event history. This checks that the
        # process boot itself did not silently execute the selected program.
        from homeassistant.components.recorder.db_schema import Events, EventTypes
        from homeassistant.components.recorder.util import session_scope
        from sqlalchemy import func, select
        await recorder.async_synchronize()
        def read():
            with session_scope(session=recorder.instance.get_session(), read_only=True) as session:
                return session.scalar(select(func.count(Events.event_id))
                    .join(EventTypes, Events.event_type_id == EventTypes.event_type_id)
                    .where(EventTypes.event_type == 'own_cold_result'))
        return await recorder.instance.async_add_executor_job(read)

    async def test_phase(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        import yaml

        hass, root = self.f.hass, self.f.root
        old, new = 'script.cold_worker', 'script.cold_worker_next'
        mapping = {old: new}
        events = []
        self.addCleanup(hass.bus.async_listen('own_cold_result', events.append))
        async def refresh():
            self.assertTrue(self._runtime().operation_lock.locked())

        if self.phase == 'forward':
            scripts = ('cold_worker:\n  sequence:\n  - event: own_cold_result\n'
                       '    event_data: {marker: native_cold}\n'
                       'cold_caller:\n  sequence:\n  - action: script.cold_worker\n')
            templates = ('- unique_id: cold_observer\n  sensor:\n  - unique_id: view\n'
                         '    name: Cold observer\n    state: "{{ states(\'script.cold_worker\') }}"\n')
            (root / 'cold_scripts.yaml').write_text(scripts)
            (root / 'template.yaml').write_text(templates)
            (root / 'configuration.yaml').write_text(
                'script: !include cold_scripts.yaml\ntemplate: !include template.yaml\n')
            config = await async_hass_config_yaml(hass)
            for domain in ('script', 'template'):
                self.assertTrue(await async_setup_component(hass, domain, config), domain)
            await hass.async_block_till_done()
            original = {name: base64.b64encode((root / name).read_bytes()).decode()
                        for name in ('configuration.yaml', 'cold_scripts.yaml', 'template.yaml')}
            worker = self._script('cold_worker')
            observer = self._observer()
            observer_row = self.f.entity.async_get(observer.entity_id)
            self.assertEqual(hass.states.get(old).state, 'off')
            self.assertEqual(hass.states.get(observer.entity_id).state, 'off')
            row = self.f.entity.async_get(old)
            self.assertIs(worker.registry_entry, row)
            self.assertEqual((row.entity_id, row.unique_id, row.original_name, row.name),
                             (old, 'cold_worker', 'cold_worker', None))
            friendly = hass.states.get(old).attributes.get('friendly_name')
            await self._call('cold_worker', events)
            await self._call('cold_caller', events)
            await self._flush_documents(initialize=True)
            mod = backend_fixture.installed_module
            recorder = mod('recorder_guard').NativeRecorderGuard(hass)
            history = mod('history_guard').NativeHistoryGuard(recorder)
            old_history = await history.capture(mapping)
            before_meta = await self._metadata(history, mapping)
            self.assertIn(old, before_meta['states'])
            self.assertNotIn(new, before_meta['states'])
            history_id = before_meta['states'][old]['metadata_id']
            runtime = self._runtime()
            count = len(events)
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='cold-own-id',
                guard=lambda: None, refresh=refresh)
            selected = next(item for item in review['changes'] if item['entity_id'] == old)
            self.assertEqual(selected['fields'], {
                'entity_id': {'before': old, 'after': new},
                'name': {'before': None, 'after': 'cold_worker'}})
            key_rows = [(file['path'], ref) for file in review['dependencies']['files']
                        for ref in file['references'] if ref.get('kind') == 'script_key']
            self.assertEqual(key_rows, [('cold_scripts.yaml', {
                'kind': 'script_key', 'before': 'cold_worker',
                'after': 'cold_worker_next', 'occurrences': 1})])
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='cold-own-id', guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
            self.assertEqual(len(events), count, 'rename must not execute the script')
            self.assertIs(self._script('cold_worker_next'), worker)
            self.assertEqual(self.f.entity.async_get(new).id, row.id)
            self.assertIs(self._observer(), observer)
            self.assertEqual(hass.states.get(observer.entity_id).state, 'off')
            await self._call('cold_worker_next', events)
            await self._call('cold_caller', events)
            self.assertTrue(await history.verify(old_history))
            record = await runtime.journal.load(result['operation_id'])
            self.assertEqual((record.status, record.direction), ('applied', 'forward'))
            baseline, _ = runtime.preparations.resolve_composition(record.plan.native_inputs)
            # Only native Stores and the recorder persist across the hard exit.
            await self._flush_documents()
            await recorder.async_synchronize()
            event_count = await self._event_count(recorder)
            self.assertEqual(event_count, len(events))
            data = {'pid': os.getpid(), 'backend_entry_id': self.backend_entry_id,
                'public_source_sha256': self.public_source_sha256, 'uuid': row.id,
                'observer_id': observer.entity_id, 'observer_uuid': observer_row.id,
                'friendly_name': friendly, 'history': old_history, 'history_id': history_id,
                'event_count': event_count, 'dependencies': review['dependencies']['files'],
                'operation': record.operation_id, 'fingerprint': record.plan.fingerprint,
                'baseline_digest': baseline.digest,
                'receipt_b64': base64.b64encode(baseline.receipt_json).decode(),
                'original': original}
            with self.meta_path.open('x') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            print('OWN_COLD_FORWARD_APPLIED', os.getpid(), flush=True)
            os._exit(73)

        meta, runtime = self.meta, self._runtime()
        worker = self._script('cold_worker_next')
        observer = self._observer()
        self.assertEqual(observer.entity_id, meta['observer_id'])
        self.assertEqual(self.f.entity.async_get(observer.entity_id).id, meta['observer_uuid'])
        self.assertEqual(hass.states.get(new).state, 'off')
        self.assertEqual(hass.states.get(observer.entity_id).state, 'off')
        row = self.f.entity.async_get(new)
        self.assertIsNone(self.f.entity.async_get(old))
        self.assertEqual((row.id, row.entity_id, row.unique_id, row.original_name, row.name),
                         (meta['uuid'], new, 'cold_worker_next', 'cold_worker_next', 'cold_worker'))
        self.assertIs(worker.registry_entry, row)
        self.assertEqual(hass.states.get(new).attributes.get('friendly_name'), meta['friendly_name'])
        self.assertTrue(hass.services.has_service('script', 'cold_worker_next'))
        self.assertFalse(hass.services.has_service('script', 'cold_worker'))
        service = hass.services._services['script']['cold_worker_next']
        self.assertIs(service.job.target.__self__, worker)
        loaded_scripts = yaml.safe_load((root / 'cold_scripts.yaml').read_text())
        self.assertNotIn('cold_worker', loaded_scripts)
        self.assertNotIn('alias', loaded_scripts['cold_worker_next'])
        self.assertEqual(loaded_scripts['cold_caller']['sequence'][0]['action'],
                         'script.cold_worker_next')
        self.assertIn("states('script.cold_worker_next')", (root / 'template.yaml').read_text())
        mod = backend_fixture.installed_module
        history = mod('history_guard').NativeHistoryGuard(mod('recorder_guard').NativeRecorderGuard(hass))
        self.assertTrue(await history.verify(meta['history']))
        persisted = await self._metadata(history, mapping)
        self.assertNotIn(old, persisted['states'])
        self.assertEqual(persisted['states'][new]['metadata_id'], meta['history_id'])
        self.assertEqual(await self._event_count(history.guard), meta['event_count'],
                         'cold boot must not execute the Script')
        await self._call('cold_worker_next', events)
        await self._call('cold_caller', events)
        print('OWN_COLD_PERSISTENCE_VERIFIED', meta['pid'], os.getpid(), flush=True)

        # Discover authority from the newly opened native journal. Test meta
        # is used only to assert that it is the SAME original operation.
        records = await runtime.journal.all()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record.status, record.direction), ('applied', 'forward'))
        self.assertEqual((record.operation_id, record.plan.fingerprint),
                         (meta['operation'], meta['fingerprint']))
        baseline, _ = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self.assertEqual(baseline.digest, meta['baseline_digest'])
        self.assertEqual(baseline.receipt_json, base64.b64decode(meta['receipt_b64']))
        # Flush the actual native managers and independently read their full
        # envelopes. This does not seed or replace the retained receipt.
        await self._flush_documents()
        inspected = await runtime.recovery_inspect(hass, record.operation_id, record.plan.fingerprint,
            guard=lambda: None, refresh=refresh)
        self.assertTrue(inspected['eligible'])
        self.assertEqual(inspected['frozen_review']['dependencies']['files'], meta['dependencies'])
        before = len(events)
        try:
            inverse = await runtime.native_yaml_reverse(hass, record.operation_id, record.plan.fingerprint,
                guard=lambda: None, refresh=refresh)
        except Exception as error:
            print('OWN_COLD_INVERSE_UNRESOLVED', type(error).__module__, type(error).__qualname__, flush=True)
            raise
        self.assertEqual(inverse['state'], 'rolled_back')
        self.assertEqual(len(events), before, 'inverse must not execute the script')
        restored = self.f.entity.async_get(old)
        self.assertEqual((restored.id, restored.unique_id, restored.original_name, restored.name),
                         (meta['uuid'], 'cold_worker', 'cold_worker', None))
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertIs(self._script('cold_worker'), worker)
        self.assertIs(self._observer(), observer)
        self.assertEqual(hass.states.get(old).state, 'off')
        self.assertEqual(hass.states.get(observer.entity_id).state, 'off')
        self.assertFalse(hass.services.has_service('script', 'cold_worker_next'))
        self.assertTrue(hass.services.has_service('script', 'cold_worker'))
        for name, raw in meta['original'].items():
            self.assertEqual((root / name).read_bytes(), base64.b64decode(raw))
        self.assertTrue(await history.verify(meta['history']))
        restored_meta = await self._metadata(history, mapping)
        self.assertNotIn(new, restored_meta['states'])
        self.assertEqual(restored_meta['states'][old]['metadata_id'], meta['history_id'])
        await self._call('cold_worker', events)
        await self._call('cold_caller', events)
        print('OWN_COLD_SAME_ORIGINAL_INVERSE_VERIFIED', flush=True)


@unittest.skipUnless(backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativeOwnIDColdProcessTests(unittest.TestCase):
    def test_default_name_script_service_cold_boot_and_fresh_inverse(self):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='renamer-own-cold-', dir=parent) as directory:
            root = Path(directory) / 'config'
            root.mkdir()
            env = dict(os.environ, RENAMER_OWN_COLD_CONFIG=str(root),
                       RENAMER_OWN_COLD_META=str(Path(directory) / 'oracle.json'))
            for phase, expected in (('forward', 73), ('resume', 0)):
                env['RENAMER_OWN_COLD_PHASE'] = phase
                result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                    'OwnIDColdPhase.test_phase'], env=env, capture_output=True, text=True, timeout=300)
                print(result.stdout)
                self.assertEqual(result.returncode, expected, result.stderr[-16000:])


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(NativeOwnIDColdProcessTests)


if __name__ == '__main__':
    unittest.main()
