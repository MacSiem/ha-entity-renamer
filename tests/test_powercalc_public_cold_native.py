"""Separate real HA processes: persisted Powercalc PSS and fresh inverse.

The child config is owned synthetic data on private ext4. Metadata is only an
assertion oracle; all reverse authority is reopened from native durable stores.
"""
import asyncio
import base64
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import unittest

import test_native_backend_lifecycle as backend_fixture
import test_receipted_helper_native as receipted
import test_powercalc_profile_energy_native as profiles
from test_powercalc_profile_inputs import ProfileLight, InfraredSelector


class PowercalcColdPhase(backend_fixture.NativeBackendLifecycleTests):
    with_yaml = True
    _setup_vendor_forward = profiles.PowercalcProfileEnergyNativeTests._setup_real_vendor

    async def _startup_services(self, hass, root):
        # Core 2026.10 migrates an explicit HTTP YAML override on first boot
        # and creates a new repair on the next boot. Use Core's native default
        # HTTP configuration in this isolated network instead of injecting a
        # deprecated synthetic YAML override on each process start.
        from homeassistant import auth
        from homeassistant.setup import async_setup_component
        self.with_yaml = False
        try:
            await receipted.NativeReceiptedHelperTests._before_start(self, hass, root)
        finally:
            self.with_yaml = True
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        self.assertTrue(await async_setup_component(hass, 'http', {}))
        from homeassistant.components.config import automation, script, scene
        for editor in (automation, script, scene):
            self.assertTrue(editor.async_setup(hass))

    async def _before_start(self, hass, root):
        from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
        from homeassistant.data_entry_flow import FlowResultType
        await self._startup_services(hass, root)
        result = await hass.config_entries.flow.async_init('ha_entity_renamer',
                                                          context={'source': SOURCE_USER})
        self.assertEqual(result['type'], FlowResultType.CREATE_ENTRY, result)
        entry = result['result']
        self.assertIs(hass.config_entries.async_get_entry(entry.entry_id), entry)
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.backend_entry_id = entry.entry_id
        self._loaded_backend()

    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import device_registry as dr, entity_registry as er

        self.phase = os.environ['RENAMER_POWER_COLD_PHASE']
        self.mode = os.environ['RENAMER_POWER_COLD_MODE']
        root = Path(os.environ['RENAMER_POWER_COLD_CONFIG']).resolve(strict=True)
        self.meta_path = Path(os.environ['RENAMER_POWER_COLD_META'])
        self.meta = None if self.phase == 'forward' else json.loads(self.meta_path.read_text())
        if self.meta is not None:
            self.assertNotEqual(os.getpid(), self.meta['pid'])
            for name, proof in self.meta['persisted'].items():
                path = root / name
                info, raw = path.stat(), path.read_bytes()
                self.assertEqual((info.st_dev, info.st_ino, hashlib.sha256(raw).hexdigest()),
                                 (proof[0], proof[1], proof[2]))
                self.assertEqual(raw, base64.b64decode(proof[3]))
        hass = HomeAssistant(str(root))
        self.addAsyncCleanup(hass.async_stop, force=True)
        if self.phase == 'forward':
            (root / 'configuration.yaml').write_bytes(b'')
            await self._before_loader(hass, root)
            supplied = Path(os.environ['POWERCALC_COMPONENT_DIR']).resolve(strict=True)
            shutil.copytree(supplied, root / 'custom_components/powercalc',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
        else:
            self.installed_component = root / 'custom_components/ha_entity_renamer'
            self.public_source_sha256 = self.meta['component_sha256']
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await loader.async_get_custom_components(hass)
        self.assertIsNot(await async_load_base_functionality(hass), False)
        self.f = SimpleNamespace(hass=hass, root=root, version=__version__,
            entity=er.async_get(hass), device=dr.async_get(hass))
        self.f.managers = (self.f.entity, self.f.device, hass.config_entries)
        if self.phase == 'forward':
            await self._before_start(hass, root)
            self.omit_subtract = os.environ.get('RENAMER_POWER_COLD_GROUP_ONLY') == '1'
            await self._setup_vendor_forward()
        else:
            await self._startup_services(hass, root)
            from homeassistant.helpers.entity_component import EntityComponent
            from homeassistant.setup import async_setup_component
            from homeassistant.config import async_hass_config_yaml
            from homeassistant.core import callback
            raw_pss = base64.b64decode(self.meta['persisted']['.storage/powercalc_group'][3])
            members = json.loads(raw_pss)['data'][self.meta['active_group']]
            self.initial_members = members
            self.sample_events = []
            @callback
            def collect(event):
                value = event.data.get('new_state')
                if event.data.get('entity_id') in members and value is not None:
                    self.sample_events.append(value)
            self.addCleanup(hass.bus.async_listen('state_changed', collect))
            self.assertTrue(await async_setup_component(hass, 'network', {}))
            self.sources = [ProfileLight('Public static'), ProfileLight('Public dynamic', dynamic=True)]
            lights = EntityComponent(logging.getLogger(__name__), 'light', hass, timedelta(seconds=60))
            await lights.async_add_entities(self.sources)
            self.selector = InfraredSelector(self.sources[1].entity_id.split('.', 1)[1])
            selects = EntityComponent(logging.getLogger(__name__), 'select', hass, timedelta(seconds=60))
            await selects.async_add_entities([self.selector])
            config = await async_hass_config_yaml(hass)
            self.assertTrue(await async_setup_component(hass, 'powercalc', config))
            self.assertTrue(await async_setup_component(hass, 'ha_entity_renamer', config))
            await hass.async_block_till_done()
        await hass.async_start()
        await hass.async_block_till_done()
        if self.phase == 'forward':
            await self._flush_documents(initialize=True)
        else:
            entries = hass.config_entries.async_entries('ha_entity_renamer')
            self.assertEqual(len(entries), 1)
            self.backend_entry_id = entries[0].entry_id
            self.assertEqual(self.backend_entry_id, self.meta['backend_entry_id'])
            self._loaded_backend()

    def _runtime(self):
        backend = self._loaded_backend()
        entry = self.f.hass.config_entries.async_get_entry(self.backend_entry_id)
        self.assertIsNotNone(entry)
        self.assertIs(type(entry.runtime_data), backend.Runtime)
        self.assertIs(self.f.hass.data.get(backend.DATA_RUNTIME), entry.runtime_data)
        return entry.runtime_data

    async def _transport(self):
        from aiohttp import ClientSession
        from homeassistant.auth.const import GROUP_ID_ADMIN
        hass = self.f.hass
        if self.phase == 'forward':
            user = await hass.auth.async_create_user('Synthetic cold administrator', group_ids=[GROUP_ID_ADMIN])
            self.user_id = user.id
        else:
            user = await hass.auth.async_get_user(self.meta['user_id'])
            self.assertIsNotNone(user)
        self.assertTrue(user.is_admin)
        token = await hass.auth.async_create_refresh_token(user, client_id='https://renamer.invalid/powercalc-cold')
        access = hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
        client = ClientSession()
        self.addAsyncCleanup(client.close)
        ws = await client.ws_connect('http://127.0.0.1:' + str(hass.http.server_port) + '/api/websocket')
        self.addAsyncCleanup(ws.close)
        self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_required')
        await ws.send_json({'type': 'auth', 'access_token': access})
        self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_ok')
        async def request(index, command, **payload):
            await ws.send_json({'id': index, 'type': 'ha_entity_renamer/' + command, **payload})
            return await ws.receive_json(timeout=1200)
        return request, ws, client

    def _find_group(self, name):
        from homeassistant.helpers.entity_platform import async_get_platforms
        rows = [entity for platform in async_get_platforms(self.f.hass, 'powercalc')
                for key, entity in platform.entities.items() if key == name]
        self.assertEqual(len(rows), 1)
        return rows[0]

    async def _pulse_light(self, brightness):
        await self.sources[0].async_turn_on(brightness=brightness)
        await asyncio.sleep(1.2)
        await self.sources[0].async_turn_off()
        await self.f.hass.async_block_till_done()

    def _verify_cold_sum(self, group):
        """Independent delta sum from persisted native PSS and real State events."""
        expected = Decimal(self.meta['stored_total'])
        prior = {name: None if row is None else row['state']
                 for name, row in self.initial_members.items()}
        for state in self.sample_events:
            try:
                value = Decimal(state.state)
                if not value.is_finite():
                    continue
            except Exception:
                continue
            previous = prior.get(state.entity_id)
            if previous is not None:
                try:
                    delta = value - Decimal(previous)
                    if delta > 0:
                        expected += delta
                except Exception:
                    pass
            prior[state.entity_id] = state.state
        self.assertEqual(group._native_value_exact, expected)
        return expected

    def _install_exception_collector(self):
        """Print original local exception chains without changing product outcomes."""
        import traceback
        package = 'custom_components.ha_entity_renamer.'
        coordinator = importlib.import_module(package + 'transaction').Coordinator
        inverse = importlib.import_module(package + 'powercalc_reverse_driver')._PowercalcInverseCoordinator
        recovery = importlib.import_module(package + 'powercalc_public_recovery')
        server = importlib.import_module(package + 'powercalc_public_server').PowercalcPublicServer
        observation = importlib.import_module(package + 'native_input_observation')
        original_fail = observation._fail
        def observed_fail(reason):
            if reason == 'native_input_observation_rejected':
                error = sys.exception()
                if error is not None:
                    traceback.print_exception(error)
                    tb = error.__traceback__
                    while tb is not None:
                        frame = tb.tb_frame
                        if frame.f_code.co_name == 'assert_current':
                            row = frame.f_locals
                            if all(key in row for key in ('directory', 'expected', 'current')):
                                print('COLD_SOURCE_GRAPH_DIFF', repr(row['directory']),
                                      repr(row['expected']), repr(row['current']), flush=True)
                        tb = tb.tb_next
            return original_fail(reason)
        observation._fail = observed_fail
        original_unknown, original_inverse = coordinator._unknown, inverse._unknown_after_error
        original_recovery = recovery.inspect_or_restore
        original_prepare, original_bind = server.__dict__['prepare'], server.__dict__['bind_prepared']
        async def unknown(owner, record, reason):
            print('COLD_FORWARD_UNKNOWN', reason, record.phases, flush=True)
            error = sys.exception()
            if error is not None:
                traceback.print_exception(error)
            return await original_unknown(owner, record, reason)
        async def inverse_unknown(owner, record, reason, error):
            print('COLD_INVERSE_UNKNOWN', reason, record.phases, flush=True)
            traceback.print_exception(error)
            return await original_inverse(owner, record, reason, error)
        async def observed_recovery(*args, **kwargs):
            try:
                return await original_recovery(*args, **kwargs)
            except BaseException as error:
                traceback.print_exception(error)
                raise
        async def prepare(cls, *args, **kwargs):
            try:
                return await original_prepare.__func__(cls, *args, **kwargs)
            except BaseException as error:
                traceback.print_exception(error)
                raise
        async def bind(cls, *args, **kwargs):
            try:
                return await original_bind.__func__(cls, *args, **kwargs)
            except BaseException as error:
                traceback.print_exception(error)
                raise
        coordinator._unknown, inverse._unknown_after_error = unknown, inverse_unknown
        recovery.inspect_or_restore = observed_recovery
        server.prepare, server.bind_prepared = classmethod(prepare), classmethod(bind)

    async def test_phase(self):
        self._install_exception_collector()
        hass, root = self.f.hass, self.f.root
        runtime = self._runtime()
        request, ws, client = await self._transport()
        if self.phase == 'forward':
            await self.sources[0].async_turn_on(brightness=255)
            await asyncio.sleep(1.2)
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()
            await self.sum_energy._prev_state_store.persist_states()
            from homeassistant.helpers import issue_registry
            issue = issue_registry.async_get(hass)
            await issue._store.async_save(issue._data_to_save())
            old_source, old_group = self.sources[0].entity_id, self.sum_energy.entity_id
            new_source, new_group = 'light.public_static_target', 'sensor.synthetic_new_sum_energy'
            source_uuid, group_uuid = self.sources[0].registry_entry.id, self.sum_energy.registry_entry.id
            op = fingerprint = original_digest = receipt = None
            if self.mode == 'migration':
                response = await request(1, 'prepare', entities=[{'entity_id': old_source, 'new_entity_id': new_source},
                                                                 {'entity_id': old_group, 'new_entity_id': new_group}])
                self.assertTrue(response.get('success'), response)
                review = response['result']
                op, fingerprint = review['operation_id'], review['fingerprint']
                plan = runtime.reviews[op].plan
                original, _ = runtime.preparations.resolve_composition(plan.native_inputs)
                original_digest, receipt = original.digest, original.receipt_json
                response = await request(2, 'apply', operation_id=op, fingerprint=fingerprint)
                self.assertTrue(response.get('success'), response)
                self.assertEqual(response['result']['state'], 'applied', response)
                self.assertEqual(self.f.entity.async_get(new_source).id, source_uuid)
                self.assertEqual(self.f.entity.async_get(new_group).id, group_uuid)
            await self._pulse_light(64)
            active_group = new_group if self.mode == 'migration' else old_group
            group = self._find_group(active_group)
            precision = group._rounding_digits
            await ws.close()
            await client.close()
            await hass.async_stop(force=True)
            before_shutdown = group._native_value_exact
            self.assertIs(type(before_shutdown), Decimal)
            self.assertTrue(before_shutdown.is_finite())
            persisted = {}
            for name in ('.storage/powercalc_group', '.storage/core.restore_state'):
                path = root / name
                raw, info = path.read_bytes(), path.stat()
                persisted[name] = [info.st_dev, info.st_ino, hashlib.sha256(raw).hexdigest(),
                                   base64.b64encode(raw).decode()]
            from homeassistant.components.sensor import SensorExtraStoredData
            restore_doc = json.loads(base64.b64decode(persisted['.storage/core.restore_state'][3]))
            restore_rows = [row for row in restore_doc['data'] if row['state']['entity_id'] == active_group]
            self.assertEqual(len(restore_rows), 1)
            extra = SensorExtraStoredData.from_dict(restore_rows[0]['extra_data'])
            self.assertIsNotNone(extra)
            stored_total = Decimal(str(extra.native_value))
            self.assertEqual(stored_total, round(before_shutdown, precision))
            meta = {'pid': os.getpid(), 'operation': op, 'fingerprint': fingerprint,
                    'baseline_digest': original_digest,
                    'receipt_b64': None if receipt is None else base64.b64encode(receipt).decode(),
                    'source_uuid': source_uuid, 'group_uuid': group_uuid, 'old_source': old_source,
                    'new_source': new_source, 'old_group': old_group, 'new_group': new_group,
                    'before_shutdown': str(before_shutdown), 'stored_total': str(stored_total),
                    'precision': precision, 'active_group': active_group, 'mode': self.mode,
                    'persisted': persisted, 'backend_entry_id': self.backend_entry_id,
                    'component_sha256': self.public_source_sha256, 'user_id': self.user_id}
            self.meta_path.write_text(json.dumps(meta, sort_keys=True))
            print('COLD_PUBLIC_FORWARD_SHUTDOWN', self.mode, os.getpid(), flush=True)
            os._exit(73)
        meta = self.meta
        current_source = meta['new_source'] if self.mode == 'migration' else meta['old_source']
        self.assertEqual(self.f.entity.async_get(current_source).id, meta['source_uuid'])
        self.assertEqual(self.f.entity.async_get(meta['active_group']).id, meta['group_uuid'])
        group = self._find_group(meta['active_group'])
        self.assertIs(type(group._native_value_exact), Decimal)
        self._verify_cold_sum(group)
        if self.mode == 'lazy':
            # The Energy store exists, but no consumer has yet requested the
            # lazy manager in this fresh interpreter. A normal public review
            # must still enumerate the real persisted dependency.
            self.assertNotIn('energy_manager', hass.data)
            energy_file = root / '.storage/energy'
            before_energy = energy_file.read_bytes()
            response = await request(1, 'prepare', entities=[
                {'entity_id': meta['old_source'], 'new_entity_id': meta['new_source']},
                {'entity_id': meta['old_group'], 'new_entity_id': meta['new_group']}])
            self.assertTrue(response.get('success'), response)
            review = response['result']
            self.assertIn('energy', review['dependencies']['domains'])
            plan = runtime.reviews[review['operation_id']].plan
            self.assertIn('energy', plan.required_scopes)
            self.assertEqual(sum(step.adapter == 'energy' for step in plan.steps), 1)
            self.assertEqual(energy_file.read_bytes(), before_energy)
            print('COLD_LAZY_ENERGY_PUBLIC_REVIEW', meta['pid'], os.getpid(), flush=True)
            return
        if self.mode == 'control':
            print('COLD_NO_RENAME_CONTROL', meta['pid'], os.getpid(),
                  {'before': meta['before_shutdown'], 'stored': meta['stored_total'],
                   'observed_samples': len(self.sample_events)}, flush=True)
            return
        records = await runtime.journal.all()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record.status, record.direction), ('applied', 'forward'))
        self.assertEqual((record.operation_id, record.plan.fingerprint), (meta['operation'], meta['fingerprint']))
        original, _ = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self.assertEqual(original.digest, meta['baseline_digest'])
        self.assertEqual(original.receipt_json, base64.b64decode(meta['receipt_b64']))
        response = await request(1, 'recovery/inspect', operation_id=meta['operation'], fingerprint=meta['fingerprint'])
        self.assertTrue(response.get('success'), response)
        self.assertEqual(response['result']['state'], 'applied', response)
        response = await request(2, 'recovery/restore', operation_id=meta['operation'], fingerprint=meta['fingerprint'])
        self.assertTrue(response.get('success'), response)
        self.assertEqual(response['result']['state'], 'rolled_back', response)
        self.assertEqual(self.f.entity.async_get(meta['old_source']).id, meta['source_uuid'])
        self.assertEqual(self.f.entity.async_get(meta['old_group']).id, meta['group_uuid'])
        response = await request(3, 'recovery/inspect', operation_id=meta['operation'], fingerprint=meta['fingerprint'])
        self.assertTrue(response.get('success'), response)
        self.assertEqual(response['result']['state'], 'rolled_back', response)
        await self._pulse_light(128)
        self._verify_cold_sum(self._find_group(meta['old_group']))
        print('COLD_PUBLIC_FRESH_INVERSE', meta['pid'], os.getpid(), flush=True)


@unittest.skipUnless(backend_fixture.activation.fixtures.HAS_HA, 'actual supported HA required')
class PowercalcPublicColdTests(unittest.TestCase):
    def test_public_forward_cold_and_fresh_inverse(self):
        self._run_modes(('control', 'migration'))

    def test_cold_public_review_loads_persisted_lazy_energy(self):
        self._run_modes(('lazy',))

    def test_public_group_only_cold_and_fresh_inverse(self):
        self._run_modes(('control', 'migration'), group_only=True)

    def _run_modes(self, modes, *, group_only=False):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='renamer-power-cold-', dir=parent) as directory:
            evidence = Path('/qa/cold-evidence') / Path(directory).name
            evidence.mkdir(parents=True, exist_ok=False)
            for mode in modes:
                root = Path(directory) / mode
                root.mkdir()
                env = dict(os.environ, RENAMER_POWER_COLD_CONFIG=str(root), RENAMER_POWER_COLD_MODE=mode,
                           RENAMER_POWER_COLD_META=str(Path(directory) / (mode + '-oracle.json')),
                           RENAMER_POWER_COLD_GROUP_ONLY='1' if group_only else '0')
                for phase, expected in (('forward', 73), ('resume', 0)):
                    env['RENAMER_POWER_COLD_PHASE'] = phase
                    result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                        'PowercalcColdPhase.test_phase'], env=env, capture_output=True, text=True, timeout=1200)
                    (evidence / (mode + '-' + phase + '.stdout')).write_text(result.stdout)
                    (evidence / (mode + '-' + phase + '.stderr')).write_text(result.stderr)
                    oracle = Path(directory) / (mode + '-oracle.json')
                    if oracle.exists():
                        shutil.copyfile(oracle, evidence / (mode + '-oracle.json'))
                    print(result.stdout, flush=True)
                    if result.returncode != expected:
                        print(result.stderr, flush=True)
                        # Preserve this owned synthetic post-failure image for
                        # diagnosis; never replay an uncertain operation.
                        shutil.copytree(root, evidence / (mode + '-' + phase + '-failed-config'))
                    self.assertEqual(result.returncode, expected)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(PowercalcPublicColdTests)


if __name__ == '__main__':
    unittest.main()
