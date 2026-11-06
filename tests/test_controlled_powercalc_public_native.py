"""One real Runtime review/apply/fresh inverse on a private native fixture."""
import asyncio
import base64
from decimal import Decimal
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_powercalc_public_cold_native as cold


@unittest.skipUnless(cold.backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class ControlledPowercalcPublicNativeTests(cold.PowercalcColdPhase):
    async def _startup_services(self, hass, root):
        # Native fixture binds HTTP to loopback; the dummy interface remains
        # reserved for independently checked Zeroconf sockets only.
        self.with_yaml = True
        await cold.receipted.NativeReceiptedHelperTests._before_start(self, hass, root)

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='controlled-pc-public-',
            dir=os.environ.get('RENAMER_TEST_SCRATCH', tempfile.gettempdir()))
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        def preserve_fixture():
            # Runs before TemporaryDirectory cleanup, including setup failures.
            # These are exclusively synthetic fixture bytes, retained locally.
            import shutil
            destination = Path('/qa/fixture-teardown')
            try:
                shutil.copytree(root, destination, symlinks=True)
                (Path('/qa') / 'fixture-teardown-status.json').write_text(
                    json.dumps({'captured': True, 'path': str(destination)}))
            except BaseException as error:
                (Path('/qa') / 'fixture-teardown-status.json').write_text(
                    json.dumps({'captured': False, 'error': type(error).__name__ + ':' + str(error)}))
                raise
        self.addCleanup(preserve_fixture)
        environment = patch.dict(os.environ, {'RENAMER_POWER_COLD_PHASE': 'forward',
            'RENAMER_POWER_COLD_MODE': 'migration', 'RENAMER_POWER_COLD_CONFIG': str(root),
            'RENAMER_POWER_COLD_META': str(root / 'unused-oracle.json')})
        environment.start()
        self.addCleanup(environment.stop)
        self.group_fixture_options = {'energy_sensor_precision': 2}
        await super().asyncSetUp()
        # A dependency may already have initialized Template before its YAML
        # was added. Use HA's own reload after startup to load the final file.
        await self.f.hass.services.async_call('template', 'reload', {}, blocking=True)
        await self.f.hass.async_block_till_done()
        await self._flush_documents()

    async def _setup_vendor_forward(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        await cold.profiles.PowercalcProfileEnergyNativeTests._setup_real_vendor(self)
        root, old = self.f.root, self.yaml_source
        (root / 'controlled_automations.yaml').write_text(
            '- id: controlled_pc_source\n  alias: Controlled PC source\n  initial_state: true\n'
            '  triggers:\n  - trigger: state\n    entity_id: ' + old + '\n'
            '  actions:\n  - event: controlled_pc_automation\n')
        (root / 'controlled_scripts.yaml').write_text(
            'controlled_pc_source:\n  sequence:\n  - event: controlled_pc_script\n'
            '    event_data:\n      value: "{{ states(\'' + old + '\') }}"\n')
        (root / 'controlled_template.yaml').write_text(
            '- unique_id: controlled-pc\n  sensor:\n  - name: Controlled PC source\n    unique_id: source\n'
            '    state: "{{ states(\'' + old + '\') }}"\n')
        with (root / 'configuration.yaml').open('a') as stream:
            stream.write('\nautomation: !include controlled_automations.yaml\n'
                         'script: !include controlled_scripts.yaml\n'
                         'template: !include controlled_template.yaml\n')
        config = await async_hass_config_yaml(self.f.hass)
        for domain in ('automation', 'script', 'template'):
            self.assertTrue(await async_setup_component(self.f.hass, domain, config))
        await self.f.hass.async_block_till_done()

    async def _fixture_preflight(self):
        from homeassistant.config_entries import ConfigEntryState
        from homeassistant.helpers.entity_platform import async_get_platforms
        from ordinary_network_scope import native_dummy_mdns

        hass = self.f.hass
        self.assertEqual(hass.config_entries.async_entries('template'), [])
        for domain in ('powercalc', 'template', 'automation', 'script'):
            self.assertTrue(hass.services.has_service(domain, 'reload'), domain)
        for entry in hass.config_entries.async_entries('powercalc'):
            self.assertIs(entry.state, ConfigEntryState.LOADED)
        actual = {(platform.domain, entity.unique_id): entity
                  for platform in async_get_platforms(hass, 'template')
                  for entity in platform.entities.values()}
        self.assertEqual(set(actual), {('sensor', 'controlled-pc-source')})
        consumer = actual[('sensor', 'controlled-pc-source')]
        self.assertEqual(self.f.entity.async_get(consumer.entity_id).platform, 'template')
        for platform, unique_id in (('automation', 'controlled_pc_source'),
                                    ('script', 'controlled_pc_source')):
            rows = [row for row in self.f.entity.entities.values()
                    if row.platform == platform and row.unique_id == unique_id]
            self.assertEqual(len(rows), 1, (platform, unique_id))
            self.assertIsNotNone(hass.states.get(rows[0].entity_id))
        self.assertEqual(len([entity for platform in async_get_platforms(hass, 'powercalc')
                              for entity in platform.entities.values()]), len(self.graph))
        for entity in self.graph:
            self.assertIsNotNone(entity.registry_entry)
            self.assertIsNotNone(hass.states.get(entity.entity_id))
        self.assertIsNotNone(hass.states.get(self.yaml_source))
        self.assertIsNotNone(hass.states.get(self.gui_source))
        self.assertTrue(self._runtime())
        native_dummy_mdns(hass)
        before = await self._flush_documents()
        after = await self._flush_documents()
        self.assertEqual(before, after)

    async def test_native_fixture_preflight(self):
        await self._fixture_preflight()

    async def test_native_vendor_exact_restore_snapshot(self):
        from homeassistant.helpers.restore_state import StoredState
        from homeassistant.util import dt as dt_util
        from custom_components.powercalc.sensors.energy import VirtualEnergyExtraStoredData
        from custom_components.ha_entity_renamer.native_restore_state_payload import (
            PayloadRejected, prepare_integration_snapshot_payload, materialize_integration_payload)
        hass = self.f.hass
        await self._fixture_preflight()
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(2)
        await self.sources[0].async_turn_off()
        await hass.async_block_till_done()
        entity = self.static_energy
        extra = entity.extra_restore_state_data
        self.assertIs(type(extra), VirtualEnergyExtraStoredData)
        stored = StoredState(hass.states.get(entity.entity_id), extra, dt_util.utcnow())
        payload = prepare_integration_snapshot_payload(stored, entity.entity_id, entity=entity)
        actual = materialize_integration_payload(payload).extra_data.as_dict()
        self.assertEqual(Decimal(actual['native_value']).as_tuple(), entity._state.as_tuple())
        self.assertEqual(Decimal(actual['last_valid_state']).as_tuple(), entity._last_valid_state.as_tuple())
        self.assertEqual(actual['source_entity'], entity._source_entity)
        class ForeignExtra(VirtualEnergyExtraStoredData):
            pass
        foreign = ForeignExtra(extra.native_value, extra.native_unit_of_measurement,
                               extra.source_entity, extra.last_valid_state)
        with self.assertRaises(PayloadRejected):
            prepare_integration_snapshot_payload(
                StoredState(stored.state, foreign, stored.last_seen), entity.entity_id, entity=entity)

    async def test_native_store_fence_pss(self):
        await self._native_store_fence('powercalc_group')

    async def test_native_store_fence_restore(self):
        await self._native_store_fence('core.restore_state')

    async def test_native_store_competing_pss(self):
        await self._native_store_fence('powercalc_group', competing=True)

    async def test_native_store_competing_restore(self):
        await self._native_store_fence('core.restore_state', competing=True)

    async def test_native_pss_progress_origin_and_foreign(self):
        import homeassistant
        from homeassistant.helpers.storage import Store
        from custom_components.ha_entity_renamer.controlled_powercalc_handoff import _NativePSSProgress, _read_pinned
        from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
        from custom_components.ha_entity_renamer.native_unload_code_proof import prove_methods
        from custom_components.ha_entity_renamer.native_utility_setup_audit import _SOURCES
        from custom_components.ha_entity_renamer.powercalc_source_binding import PowercalcVendor, _pins
        from custom_components.ha_entity_renamer.powercalc_pss_handoff import _snapshot
        hass, runtime, pss = self.f.hass, self._runtime(), self.sum_energy._prev_state_store
        await self._fixture_preflight()
        await pss.persist_states()
        await self._flush_documents()
        async def refresh(): return None
        review = await runtime.native_yaml_prepare(hass,
            {self.yaml_source: 'light.progress_target', self.sum_energy.entity_id: 'sensor.progress_target'},
            actor_id='native-progress', guard=lambda: True, refresh=refresh)
        op = review['operation_id']
        async with runtime.operation_lock:
            bound = await NativeTemplateStateServer.bind_prepared(hass, op, runtime.reviews[op].plan,
                guard=lambda: True, refresh=refresh)
            try:
                receipt, state = bound.receipt, bound.chain.reconstruct_inputs()
            finally:
                bound.close()
        refs = ((type(pss), 'persist_states'),)
        core_refs = ((Store, 'async_save'), (Store, '_async_handle_write_data'),
                     (Store, '_async_write_data'), (Store, '_write_data'), (Store, '_write_prepared_data'))
        vendor = PowercalcVendor(hass, with_profiles=True)
        proof = await hass.async_add_executor_job(prove_methods, vendor.root, _pins(), refs)
        core_proof = await hass.async_add_executor_job(prove_methods, Path(homeassistant.__file__).resolve().parent,
                                                       dict(_SOURCES[receipt.version]), core_refs)
        watcher = _NativePSSProgress(pss, proof, refs, core_proof, core_refs)
        try:
            before, identity, _, _ = await hass.async_add_executor_job(_read_pinned, receipt, 'powercalc_group', state)
            group = self.sum_energy.entity_id
            previous = _snapshot(pss)[group]
            await self.sources[0].async_turn_on(brightness=255)
            async with asyncio.timeout(15):
                while _snapshot(pss)[group] == previous:
                    await asyncio.sleep(0.05)
            await pss.persist_states()
            current, current_id, _, _ = await hass.async_add_executor_job(_read_pinned, receipt, 'powercalc_group', state)
            self.assertNotEqual(before, current)
            self.assertTrue(watcher.accepts(before, identity, current, current_id, {group}))
            pss.states['sensor.foreign_fixture'] = {}
            await pss.persist_states()
            foreign, foreign_id, _, _ = await hass.async_add_executor_job(_read_pinned, receipt, 'powercalc_group', state)
            self.assertFalse(watcher.accepts(current, current_id, foreign, foreign_id, {group}))
        finally:
            watcher.close()

    async def _native_store_fence(self, key, competing=False):
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from homeassistant.helpers.storage import Store
        from custom_components.ha_entity_renamer import controlled_powercalc_handoff as handoff_module
        from custom_components.ha_entity_renamer.controlled_powercalc_handoff import _native_store_write, _read_pinned
        from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
        from ordinary_network_scope import native_dummy_mdns
        hass, root, runtime = self.f.hass, self.f.root, self._runtime()
        await self._fixture_preflight()
        pss, restore = self.sum_energy._prev_state_store, hass.data[DATA_RESTORE_STATE]
        await pss.persist_states()
        await restore.async_dump_states()
        await self._flush_documents()
        async def refresh(): return None
        review = await runtime.native_yaml_prepare(hass,
            {self.yaml_source: 'light.fence_target', self.sum_energy.entity_id: 'sensor.fence_group_target'},
            actor_id='native-fence', guard=lambda: True, refresh=refresh,
            entity_names={self.sum_energy.entity_id: 'Fence group'})
        op = review['operation_id']
        async with runtime.operation_lock:
            bound = await NativeTemplateStateServer.bind_prepared(hass, op, runtime.reviews[op].plan,
                guard=lambda: True, refresh=refresh)
            try:
                receipt, state = bound.receipt, bound.chain.reconstruct_inputs()
            finally:
                bound.close()
        owner, method = (pss, 'persist_states') if key == 'powercalc_group' else (restore, 'async_dump_states')
        evidence = {'key': key, 'network_before': native_dummy_mdns(hass)}
        out = Path('/qa/native-store-fence-' + key.replace('.', '-') + '.json')
        path = root / '.storage' / key
        try:
            before, identity, root_sig, storage_sig = await hass.async_add_executor_job(_read_pinned, receipt, key, state)
            await _native_store_write(hass, owner, method, receipt, state, key, before, identity,
                                      root_sig, storage_sig, lambda: None)
            evidence['own_native_call'] = True
            before, identity, root_sig, storage_sig = await hass.async_add_executor_job(_read_pinned, receipt, key, state)
            if competing:
                import threading
                timeline = evidence['competition_timeline'] = []
                evidence['competition_before_raw'] = base64.b64encode(path.read_bytes()).decode()
                # The product picks the lowest free ID; the timeline below
                # verifies whether this test tool actually observed it first.
                slot = next(i for i in reversed(range(6)) if sys.monitoring.get_tool(i) is None)
                sys.monitoring.use_tool_id(slot, 'fixture competing native call')
                competitors, writer_returns = [], []
                competing_started = threading.Event()
                writer_started = asyncio.Event()
                method_code = getattr(type(owner), method).__code__
                async def competing_native():
                    await asyncio.wait_for(writer_started.wait(), timeout=20)
                    if key == 'powercalc_group':
                        pss.states['sensor.foreign_fixture'] = {}
                    else:
                        from homeassistant.core import State
                        from homeassistant.helpers.restore_state import StoredState
                        from homeassistant.util import dt as dt_util
                        restore.last_states['sensor.foreign_fixture'] = StoredState(
                            State('sensor.foreign_fixture', '123'), None, dt_util.utcnow())
                    await getattr(owner, method)()
                def compete(code, offset):
                    instance = sys._getframe(1).f_locals.get('self')
                    if code is method_code and instance is owner:
                        if not competitors:
                            competitors.append(asyncio.create_task(competing_native()))
                        elif asyncio.current_task() is competitors[0]:
                            timeline.append({'event': 'competing_owner_start'})
                            # Let every monitoring tool observe this start before
                            # releasing the native writer in its executor thread.
                            hass.loop.call_soon(competing_started.set)
                    elif code is Store._write_prepared_data.__code__ and instance is owner.store:
                        timeline.append({'event': 'native_write_data_start'})
                        hass.loop.call_soon_threadsafe(writer_started.set)
                        if not competing_started.wait(timeout=15):
                            raise RuntimeError('competing native writer did not start')
                def returned(code, offset, result):
                    if sys._getframe(1).f_locals.get('self') is owner.store:
                        writer_returns.append(True)
                        timeline.append({'event': 'native_writer_return'})
                original_need = handoff_module._need
                def observe_need(value, reason):
                    if reason == 'native_write_competition_before_writer':
                        frame = sys._getframe(1)
                        candidate = frame.f_back.f_locals.get('json_data')
                        if type(candidate) is str:
                            candidate = candidate.encode()
                        if type(candidate) is bytes:
                            evidence['own_writer_candidate_raw'] = base64.b64encode(candidate).decode()
                        timeline.append({'event': 'own_writer_fence', 'accepted': bool(value),
                                         'contamination': list(frame.f_locals['contamination'])})
                    return original_need(value, reason)
                try:
                    sys.monitoring.register_callback(slot, sys.monitoring.events.PY_START, compete)
                    sys.monitoring.register_callback(slot, sys.monitoring.events.PY_RETURN, returned)
                    sys.monitoring.set_local_events(slot, method_code, sys.monitoring.events.PY_START)
                    sys.monitoring.set_local_events(slot, Store._write_prepared_data.__code__,
                        sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
                    with patch.object(handoff_module, '_need', observe_need):
                        with self.assertRaisesRegex(Exception, 'native_write_competition_before_writer'):
                            await _native_store_write(hass, owner, method, receipt, state, key, before, identity,
                                                      root_sig, storage_sig, lambda: None)
                    if competitors:
                        await asyncio.gather(*competitors, return_exceptions=True)
                finally:
                    sys.monitoring.set_local_events(slot, method_code, 0)
                    sys.monitoring.set_local_events(slot, Store._write_prepared_data.__code__, 0)
                    sys.monitoring.register_callback(slot, sys.monitoring.events.PY_START, None)
                    sys.monitoring.register_callback(slot, sys.monitoring.events.PY_RETURN, None)
                    sys.monitoring.free_tool_id(slot)
                    if competitors:
                        await asyncio.gather(*competitors, return_exceptions=True)
                    evidence['competition_final_raw'] = base64.b64encode(path.read_bytes()).decode()
                self.assertEqual(len(competitors), 1)
                fences = [row for row in timeline if row['event'] == 'own_writer_fence']
                self.assertEqual(len(fences), 1)
                self.assertFalse(fences[0]['accepted'])
                self.assertTrue(fences[0]['contamination'])
                self.assertLess(next(i for i, row in enumerate(timeline) if row['event'] == 'competing_owner_start'),
                                next(i for i, row in enumerate(timeline) if row['event'] == 'own_writer_fence'))
                self.assertEqual(writer_returns, [True])  # only the competing native writer completed
                document = json.loads(path.read_bytes())['data']
                if key == 'powercalc_group':
                    self.assertIn('sensor.foreign_fixture', document)
                else:
                    self.assertTrue(any(row['state']['entity_id'] == 'sensor.foreign_fixture'
                                        for row in document))
                evidence['competing_native_detected'] = True
                evidence['own_writer_rejected_before_replace'] = True
                return
            slot = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
            sys.monitoring.use_tool_id(slot, 'fixture foreign physical edit')
            injected = []
            def inject(code, offset):
                frame = sys._getframe(1)
                if frame.f_locals.get('self') is owner.store and not injected:
                    document = json.loads(path.read_bytes())
                    if key == 'powercalc_group':
                        document['data']['sensor.foreign_fixture'] = {}
                    else:
                        sample = json.loads(json.dumps(document['data'][0]))
                        sample['state']['entity_id'] = 'sensor.foreign_fixture'
                        document['data'].append(sample)
                    foreign = json.dumps(document, sort_keys=True, separators=(',', ':')).encode()
                    path.write_bytes(foreign)
                    injected.append(foreign)
            try:
                sys.monitoring.register_callback(slot, sys.monitoring.events.PY_START, inject)
                sys.monitoring.set_local_events(slot, Store._async_write_data.__code__, sys.monitoring.events.PY_START)
                with self.assertRaises(Exception):
                    await _native_store_write(hass, owner, method, receipt, state, key, before, identity,
                                              root_sig, storage_sig, lambda: None)
            finally:
                sys.monitoring.set_local_events(slot, Store._async_write_data.__code__, 0)
                sys.monitoring.register_callback(slot, sys.monitoring.events.PY_START, None)
                sys.monitoring.free_tool_id(slot)
            self.assertEqual(len(injected), 1)
            self.assertEqual(path.read_bytes(), injected[0])
            evidence['foreign_preimage_preserved'] = True
        finally:
            evidence['final_sha256'] = __import__('hashlib').sha256(path.read_bytes()).hexdigest()
            evidence['network_after'] = native_dummy_mdns(hass)
            record = await runtime.journal.load(op)
            evidence['journal'] = None if record is None else record.status
            out.write_text(json.dumps(evidence, sort_keys=True))

    async def test_native_review_with_live_pss_progress(self):
        from custom_components.ha_entity_renamer.powercalc_previous_state_original import _PinnedPSS
        from custom_components.ha_entity_renamer.powercalc_pss_handoff import _snapshot
        hass, runtime = self.f.hass, self._runtime()
        await self._fixture_preflight()
        await self.sources[0].async_turn_off()
        await hass.async_block_till_done()
        await self.sum_energy._prev_state_store.persist_states()
        await self._flush_documents()
        group = self.sum_energy.entity_id
        progress = []
        original_capture = _PinnedPSS.capture
        async def stimulate():
            before = _snapshot(self.sum_energy._prev_state_store).get(group)
            await self.sources[0].async_turn_on(brightness=255)
            async with asyncio.timeout(15):
                while _snapshot(self.sum_energy._prev_state_store).get(group) == before:
                    await asyncio.sleep(0.05)
            progress.append(True)
        def capture_with_native_stimulus(pin):
            result = original_capture(pin)
            if not progress:
                asyncio.run_coroutine_threadsafe(stimulate(), hass.loop).result(timeout=20)
            return result
        async def refresh(): return None
        with patch.object(_PinnedPSS, 'capture', capture_with_native_stimulus):
            review = await runtime.native_yaml_prepare(hass,
                {self.yaml_source: 'light.review_target', group: 'sensor.review_group_target'},
                actor_id='live-review', guard=lambda: True, refresh=refresh,
                entity_names={group: 'Review group'})
        self.assertEqual(progress, [True])
        plan = runtime.reviews[review['operation_id']].plan
        _, original = runtime.preparations.resolve_composition(plan.native_inputs)
        self.assertEqual(original.previous.groups[0].entity_id, group)
        self.assertEqual(original.previous.groups[0].registry_id, self.sum_energy.registry_entry.id)
        self.assertIsNone(await runtime.journal.load(review['operation_id']))

    async def test_native_live_pss_progress_during_intent(self):
        self._inject_live_pss = True
        await self.test_native_yaml_staging_boundary()

    async def test_native_live_pss_persist_during_intent(self):
        self._inject_live_pss = True
        self._persist_live_pss = True
        await self.test_native_yaml_staging_boundary()

    async def test_native_live_pss_persist_during_unload(self):
        self._persist_during_unload = True
        await self.test_native_yaml_staging_boundary()

    async def test_native_yaml_staging_boundary(self):
        from custom_components.ha_entity_renamer import yaml_transaction
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from ordinary_network_scope import native_dummy_mdns
        hass, root, runtime = self.f.hass, self.f.root, self._runtime()
        await self._fixture_preflight()
        old, group = self.yaml_source, self.sum_energy.entity_id
        mapping = {old: 'light.boundary_target', group: 'sensor.boundary_group_target'}
        async def refresh(): return None
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(2)
        await self.sources[0].async_turn_off()
        await hass.async_block_till_done()
        await self.sum_energy._prev_state_store.persist_states()
        await hass.data[DATA_RESTORE_STATE].async_dump_states()
        await self._flush_documents()
        # Drain already queued Recorder events before freezing history IDs.
        # This prefix test does not manufacture a future statistics point.
        from homeassistant.components.recorder import get_instance
        await get_instance(hass).async_block_till_done()
        evidence = {'network_before': native_dummy_mdns(hass), 'boundary': False, 'graph_diffs': [], 'native_failures': []}
        result_file = Path('/qa/staging-boundary-evidence.json')
        paths = ('configuration.yaml', 'controlled_template.yaml',
                 'controlled_automations.yaml', 'controlled_scripts.yaml')
        before_yaml = {name: (root / name).read_bytes() for name in paths}
        def raw():
            return {name: base64.b64encode((root / '.storage' / name).read_bytes()).decode()
                    if (root / '.storage' / name).is_file() else None
                    for name in ('auth', 'core.entity_registry', 'core.device_registry',
                                 'core.config_entries', 'core.restore_state', 'powercalc_group', 'energy')}
        evidence['before'] = await hass.async_add_executor_job(raw)
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='staging-boundary',
            guard=lambda: True, refresh=refresh, entity_names={group: 'Boundary group'})
        operation, fingerprint = review['operation_id'], review['fingerprint']
        from custom_components.ha_entity_renamer import file_mutations
        from custom_components.ha_entity_renamer.native_registry_receipts import RegistryReceiptStore
        from custom_components.ha_entity_renamer.powercalc_pss_handoff import _snapshot
        original_create = RegistryReceiptStore.create
        original_unload = type(hass.config_entries).async_unload
        async def unload_with_live_persist(manager, entry_id):
            if getattr(self, '_persist_during_unload', False) and not evidence.get('unload_pss_persisted'):
                previous = _snapshot(self.sum_energy._prev_state_store).get(group)
                await self.sources[0].async_turn_on(brightness=255)
                async with asyncio.timeout(15):
                    while _snapshot(self.sum_energy._prev_state_store).get(group) == previous:
                        await asyncio.sleep(0.05)
                await self.sum_energy._prev_state_store.persist_states()
                evidence['unload_pss_persisted'] = True
            return await original_unload(manager, entry_id)
        async def create_with_live_stimulus(store, key, payload):
            result = await original_create(store, key, payload)
            if getattr(self, '_inject_live_pss', False) and payload.get('kind') == 'controlled_powercalc_population_intent':
                previous = _snapshot(self.sum_energy._prev_state_store).get(group)
                await self.sources[0].async_turn_on(brightness=255)
                async with asyncio.timeout(15):
                    while _snapshot(self.sum_energy._prev_state_store).get(group) == previous:
                        await asyncio.sleep(0.05)
                if getattr(self, '_persist_live_pss', False):
                    await self.sum_energy._prev_state_store.persist_states()
                    evidence['live_pss_persisted'] = True
                evidence['live_pss_progress'] = True
            return result
        original_apply = yaml_transaction.apply_file_patch
        self.assertIs(original_apply, file_mutations.apply_file_patch)
        evidence['entered_mutator'] = False
        def stop_before_replace(config_root, patch, snapshot, operation_id, guard=None):
            evidence['entered_mutator'] = True
            def bounded_guard():
                if guard is not None:
                    guard()
                storage = root / '.storage'
                if ((storage / 'ha_entity_renamer_pending').is_dir()
                        and (storage / 'ha_entity_renamer_backups').is_dir()):
                    evidence['boundary'] = True
                    raise RuntimeError('fixture_stop_before_yaml_replace')
            return original_apply(config_root, patch, snapshot, operation_id, bounded_guard)
        slot = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(slot, 'controlled yaml staging boundary')
        def raised(code, offset, error):
            if ('/custom_components/ha_entity_renamer/' in code.co_filename
                    and not isinstance(error, (StopIteration, StopAsyncIteration, GeneratorExit))):
                text = str(error)
                reason = (text if re.fullmatch(r'(?:template|powercalc|controlled|registry|yaml|review|native)_[a-z0-9_]{1,100}', text) else None)
                line = next((number for start, end, number in code.co_lines() if start <= offset < end), None)
                row = {'file': Path(code.co_filename).name, 'function': code.co_name,
                       'line': line, 'type': type(error).__name__, 'code': reason}
                if row not in evidence['native_failures']:
                    evidence['native_failures'].append(row)
                    del evidence['native_failures'][:-120]
            if code.co_name == '_need' and str(error) == 'controlled_powercalc_handoff_source_changed_after_intent':
                try:
                    frame = sys._getframe(1).f_back
                    v, g = frame.f_locals, frame.f_globals
                    equal, freeze, snap, idle, read = (g['_exact_json_equal'], g['_freeze_json'],
                        g['_snapshot'], g['_idle'], v['read_entry'])
                    current_pss = snap(v['pss'])
                    before_pss = v['before_unload']
                    owned = {c.entity_id for c in v['self'].population.counters if c.kind == 'group'}
                    evidence['handoff_source_diff'] = {
                        'disk_equal': (v['pss_check'], v['pss_check_id'], v['restore_check'], v['restore_check_id']) ==
                                      (v['pss_before'], v['pss_identity'], v['restore_before'], v['restore_identity']),
                        'pss_bytes_equal': v['pss_check'] == v['pss_before'],
                        'pss_identity_equal': v['pss_check_id'] == v['pss_identity'],
                        'restore_bytes_equal': v['restore_check'] == v['restore_before'],
                        'restore_identity_equal': v['restore_check_id'] == v['restore_identity'],
                        'root_equal': v['root_check'] == v['root_check2'] == v['root_sig'],
                        'storage_equal': v['storage_check'] == v['storage_check2'] == v['storage_sig'],
                        'pss_memory_equal': equal(current_pss, freeze(before_pss)),
                        'pss_changed_groups': [(key, key in owned) for key in sorted(set(current_pss) | set(before_pss))
                            if not equal(current_pss.get(key), freeze(before_pss.get(key)))],
                        'entry_population_equal': tuple(v['self'].hass.config_entries.async_entries('powercalc')) == v['entries'],
                        'entry_changed': [entry.entry_id for entry in v['entries']
                            if not equal(read(v['self'].hass, entry.entry_id), freeze(v['entry_images'][entry.entry_id]))],
                        'restore_idle': idle(v['data']), 'pss_idle': idle(v['pss'])}
                except BaseException as problem:
                    evidence['handoff_diagnostic_error'] = type(problem).__name__
            if code.co_name == '_fail' and str(error) == 'registry_receipt_input_graph_changed':
                caller = sys._getframe(1).f_back
                if caller is not None and caller.f_code.co_name == 'assert_current':
                    values = caller.f_locals
                    before = {row[0]: row[1:] for row in values.get('expected', ())}
                    after = {row[0]: row[1:] for row in values.get('current', ())}
                    evidence['graph_diffs'].append({'directory': values.get('directory'), 'entries': [
                        (name, before.get(name), after.get(name))
                        for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]})
        sys.monitoring.register_callback(slot, sys.monitoring.events.RAISE, raised)
        sys.monitoring.set_events(slot, sys.monitoring.events.RAISE)
        from custom_components.ha_entity_renamer.yaml_plan import _mapping, YAMLPlanError
        with self.assertRaises(YAMLPlanError):
            _mapping({'invalid': 'invalid'})
        self.assertTrue(any(row['file'] == 'yaml_plan.py' and row['type'] == 'YAMLPlanError'
                            and type(row['line']) is int for row in evidence['native_failures']))
        evidence['native_failures'].clear()
        try:
            try:
                with patch.object(yaml_transaction, 'apply_file_patch', stop_before_replace), \
                     patch.object(RegistryReceiptStore, 'create', create_with_live_stimulus), \
                     patch.object(type(hass.config_entries), 'async_unload', unload_with_live_persist):
                    await runtime.native_yaml_apply(hass, operation, fingerprint, actor_id='staging-boundary',
                                                    guard=lambda: True, refresh=refresh)
            except Exception as error:
                evidence['exception_type'] = type(error).__name__
            record = await runtime.journal.load(operation)
            evidence['journal'] = None if record is None else {
                'status': record.status, 'reason': record.reason, 'direction': record.direction,
                'phases': list(record.phases)}
            evidence['inspect'] = await runtime.recovery_inspect(hass, operation, fingerprint,
                                                                 guard=lambda: True, refresh=refresh)
        finally:
            sys.monitoring.set_events(slot, 0)
            sys.monitoring.register_callback(slot, sys.monitoring.events.RAISE, None)
            sys.monitoring.free_tool_id(slot)
            evidence['final'] = await hass.async_add_executor_job(raw)
            evidence['yaml_unchanged'] = all((root / name).read_bytes() == before_yaml[name] for name in paths)
            evidence['final_entities'] = sorted(entity.entity_id for platform in async_get_platforms(hass, 'powercalc')
                                                 for entity in platform.entities.values())
            evidence['network_after'] = native_dummy_mdns(hass)
            result_file.write_text(json.dumps(evidence, sort_keys=True))
        self.assertTrue(evidence['boundary'] or evidence['graph_diffs'])
        if getattr(self, '_inject_live_pss', False):
            self.assertTrue(evidence.get('live_pss_progress'))
        if getattr(self, '_persist_live_pss', False):
            self.assertTrue(evidence.get('live_pss_persisted'))
            self.assertTrue(evidence['boundary'])
            self.assertFalse(evidence['graph_diffs'])
        if getattr(self, '_persist_during_unload', False):
            self.assertTrue(evidence.get('unload_pss_persisted'))
            self.assertTrue(evidence['boundary'])
            self.assertFalse(evidence['graph_diffs'])
        self.assertTrue(evidence['yaml_unchanged'])

    async def test_native_own_id_graph_transition(self):
        from dataclasses import replace
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from homeassistant.components.energy.data import async_get_manager
        from custom_components.ha_entity_renamer.native_discovery_inputs import async_observe_discovery_inputs
        from custom_components.ha_entity_renamer.native_powercalc_accepted_consumers import _prepare_profiles
        from custom_components.ha_entity_renamer.powercalc_runtime_observer import capture_powercalc_runtime
        from custom_components.ha_entity_renamer.powercalc_source_binding import PowercalcVendor, _sample
        from custom_components.ha_entity_renamer.energy_native import NativeEnergy
        from custom_components.ha_entity_renamer.controlled_powercalc_population import removed_group_indexes
        from custom_components.ha_entity_renamer.controlled_powercalc_handoff import _watch_restored_counters
        from custom_components.ha_entity_renamer.transaction import Refused
        from custom_components.ha_entity_renamer.native_registry import NativeRegistryIO
        from custom_components.ha_entity_renamer.native_name_observers import NativeNameObserver
        from custom_components.ha_entity_renamer.helper_observers import ObservationUnavailable
        from types import SimpleNamespace
        from copy import deepcopy
        from custom_components.ha_entity_renamer.powercalc_group_restore import prepare, materialize
        from custom_components.ha_entity_renamer.powercalc_pss_handoff import _clone
        from ordinary_network_scope import native_dummy_mdns
        hass, root, runtime = self.f.hass, self.f.root, self._runtime()
        await self._fixture_preflight()
        group = self.sum_energy
        old, new, uid = group.entity_id, 'sensor.graph_own_target', group.registry_entry.id
        async def refresh(): return None
        review = await runtime.native_yaml_prepare(hass, {old: new, self.yaml_source: 'light.graph_target'}, actor_id='ownid-graph-fixture',
            guard=lambda: True, refresh=refresh, entity_names={old: 'Own ID target'})
        baseline, composition = runtime.preparations.resolve_composition(runtime.reviews[review['operation_id']].plan.native_inputs)
        candidate = composition.evidence.powercalc_candidate_sources
        self.assertIsNotNone(candidate)
        energy = NativeEnergy(hass, await async_get_manager(hass))
        energy_before, energy_plan = await energy.plan({old: new})
        energy_change = energy.prepare(energy_before, energy_plan)
        pss, restore = group._prev_state_store, hass.data[DATA_RESTORE_STATE]
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(2)
        await self.sources[0].async_turn_off()
        await hass.async_block_till_done()
        await pss.persist_states()
        await restore.async_dump_states()
        await self._flush_documents()
        evidence = {'network_before': native_dummy_mdns(hass), 'phase': 'before'}
        result_file = Path('/qa/ownid-graph-evidence.json')
        def raw():
            return {name: base64.b64encode((root / '.storage' / name).read_bytes()).decode()
                    if (root / '.storage' / name).is_file() else None
                    for name in ('auth', 'core.entity_registry', 'core.device_registry',
                                 'core.config_entries', 'core.restore_state', 'powercalc_group', 'energy')}
        evidence['before'] = await hass.async_add_executor_job(raw)
        io = NativeRegistryIO(hass)
        observer = NativeNameObserver(io)
        old_registry = next(row for row in io.document('entity')['entities'] if row['id'] == uid)
        change = SimpleNamespace(kind='entity', identity=uid, before=old_registry,
                                 after=dict(old_registry, entity_id=new, name='Own ID target',
                                            suggested_object_id=new.removeprefix('sensor.')))
        change.runtime_before_json = observer.capture(change)
        original_docs = {key: json.loads(base64.b64decode(evidence['before'][key]))
                         for key in ('core.entity_registry', 'core.device_registry', 'core.config_entries')}
        try:
            await energy.write(energy_change, lambda: None)
            self.assertTrue(await hass.config_entries.async_unload(self.group.entry_id))
            await hass.async_block_till_done()
            evidence['phase'] = 'unloaded'
            total = group._native_value_exact
            self.assertIs(type(total), Decimal)
            original = restore.last_states[old]
            payload = prepare(original, new, full_total=str(total), precision=group._rounding_digits,
                              unit=str(group.native_unit_of_measurement))
            pss.states[new] = {key: _clone(state, key) for key, state in pss.states[old].items()}
            await pss.persist_states()
            restore.last_states[new] = materialize(payload)
            await restore.async_dump_states()
            self.assertEqual(self.f.entity.async_update_entity(old, new_entity_id=new, name='Own ID target').id, uid)
            data = dict(self.group.data)
            self.assertEqual(data['_energy_entity'], old)
            data['_energy_entity'] = new
            hass.config_entries.async_update_entry(self.group, data=data)
            await hass.async_block_till_done()
            restored, stop_watch = _watch_restored_counters(hass,
                [{'after_id': new, 'uuid': uid, 'kind': 'group'}])
            try:
                self.assertTrue(await hass.config_entries.async_setup(self.group.entry_id))
                await hass.async_block_till_done()
            finally:
                stop_watch()
            self.assertEqual(Decimal(restored[new]['value']).as_tuple(), total.as_tuple())
            evidence['restored_observed'] = True
            evidence['phase'] = 'setup'
            await self._flush_documents()
            actual = [entity for platform in async_get_platforms(hass, 'powercalc')
                      for entity in platform.entities.values() if entity.entity_id == new]
            self.assertEqual(len(actual), 1)
            self.assertEqual(actual[0].registry_entry.id, uid)
            self.assertEqual(actual[0]._native_value_exact.as_tuple(), total.as_tuple())
            accepted_docs = deepcopy(original_docs)
            accepted_row = next(row for row in accepted_docs['core.entity_registry']['data']['entities'] if row['id'] == uid)
            accepted_row.update(entity_id=new, name='Own ID target',
                                suggested_object_id=new.removeprefix('sensor.'))
            accepted_entry = next(row for row in accepted_docs['core.config_entries']['data']['entries']
                                  if row['entry_id'] == self.group.entry_id)
            accepted_entry['data']['_energy_entity'] = new
            self.assertTrue(observer.verify(change, change.after, expected_documents=accepted_docs))
            with self.assertRaises(ObservationUnavailable):
                observer.verify(change, change.after, expected_documents=original_docs)
            foreign = deepcopy(accepted_docs)
            foreign_entry = next(row for row in foreign['core.config_entries']['data']['entries']
                                 if row['entry_id'] == self.group.entry_id)
            foreign_entry['data']['name'] = 'Foreign edit'
            with self.assertRaises(ObservationUnavailable):
                observer.verify(change, change.after, expected_documents=foreign)
            evidence['accepted_name_and_helper'] = True
            evidence['old_object_uuid'] = group.registry_entry.id if group.registry_entry else None
            evidence['old_object_platform_state'] = str(group._platform_state)
            evidence['active_uuid'] = actual[0].registry_entry.id
            await self.sources[0].async_turn_on(brightness=255)
            async with asyncio.timeout(12):
                while actual[0]._native_value_exact <= total:
                    await asyncio.sleep(0.05)
            evidence['future_stimulus'] = True
            self.assertEqual(Decimal(restored[new]['value']).as_tuple(), total.as_tuple())
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()
            captured = capture_powercalc_runtime(hass, guard=lambda: None)
            objects = json.loads(captured.payload_json)['objects']
            evidence['inventory'] = [{'entity_id': row['entity_id'], 'registry_id': row['registry_id'],
                'active': bool(row['active_platforms']), 'references': row['references'],
                'is_old_object': row['object_id'] == str(id(group))} for row in objects]
            vendor = PowercalcVendor(hass, with_profiles=True)
            await hass.async_add_executor_job(vendor.sources)
            selected = replace(composition.evidence, powercalc_sources=candidate.sources)
            prepared = await hass.async_add_executor_job(_prepare_profiles, baseline.receipt, selected)
            observed = await async_observe_discovery_inputs(hass, guard=lambda: None)
            handoff = {'population': [{'kind': 'group', 'before_id': old, 'after_id': new, 'uuid': uid}]}
            ignored = removed_group_indexes(hass, captured, handoff, vendor)
            self.assertEqual(ignored, frozenset((str(id(group)),)))
            foreign = {'population': [dict(handoff['population'][0], uuid='foreign')]}
            with self.assertRaises(Refused):
                removed_group_indexes(hass, captured, foreign, vendor)
            index = hass.data['powercalc']['group_entities']
            index[old] = actual[0]
            try:
                with self.assertRaises(Refused):
                    removed_group_indexes(hass, captured, handoff, vendor)
            finally:
                index[old] = group
            altered = json.loads(captured.payload_json)
            target = next(row for row in altered['objects'] if row['object_id'] == str(id(group)))
            target['references'].append('vendor/domain_entities/foreign')
            altered_capture = replace(captured, payload_json=json.dumps(altered))
            with self.assertRaises(Refused):
                removed_group_indexes(hass, altered_capture, handoff, vendor)
            try:
                _sample(hass, vendor, selected, observed, captured, prepared,
                        removed_group_ids=ignored)
            except ValueError as error:
                # Candidate also changes the source; this narrow test does not.
                evidence['expected_unapplied_source'] = str(error)
                self.assertEqual(str(error), 'powercalc_profile_energy_settings_external_members_unverified')
            else:
                self.fail('candidate source mismatch unexpectedly accepted')
            detached = [row for row in evidence['inventory'] if row['is_old_object']]
            self.assertEqual(len(detached), 1)
            self.assertFalse(detached[0]['active'])
            self.assertIn('vendor/group_entities/' + old, detached[0]['references'])
            self.assertEqual(actual[0].registry_entry.id, uid)
            self.assertEqual(evidence['old_object_uuid'], uid)
        finally:
            try:
                evidence['final'] = await hass.async_add_executor_job(raw)
                evidence['network_after'] = native_dummy_mdns(hass)
                evidence['final_entities'] = sorted(entity.entity_id for platform in async_get_platforms(hass, 'powercalc')
                                                     for entity in platform.entities.values())
            finally:
                result_file.write_text(json.dumps(evidence, sort_keys=True))

    async def test_native_reload_graph_readback(self):
        from custom_components.ha_entity_renamer.native_discovery_inputs import async_observe_discovery_inputs
        from custom_components.ha_entity_renamer.native_powercalc_accepted_consumers import _prepare_profiles
        from custom_components.ha_entity_renamer.powercalc_runtime_observer import capture_powercalc_runtime
        from custom_components.ha_entity_renamer.powercalc_source_binding import PowercalcVendor, _sample
        hass, runtime = self.f.hass, self._runtime()
        await self._fixture_preflight()
        await self.sum_energy._prev_state_store.persist_states()
        await self._flush_documents()
        async def refresh():
            return None
        review = await runtime.native_yaml_prepare(hass,
            {self.yaml_source: 'light.contract_other_target', self.sum_energy.entity_id: 'sensor.contract_other_target'},
            actor_id='synthetic-reload-readback', guard=lambda: True, refresh=refresh,
            entity_names={self.sum_energy.entity_id: 'Controlled PC total'})
        op = review['operation_id']
        plan = runtime.reviews[op].plan
        baseline, composition = runtime.preparations.resolve_composition(plan.native_inputs)
        self.assertIsNone(await runtime.journal.load(op))
        await hass.services.async_call('powercalc', 'reload', {}, blocking=True)
        captured = capture_powercalc_runtime(hass, guard=lambda: None)
        vendor = PowercalcVendor(hass, with_profiles=True)
        await hass.async_add_executor_job(vendor.sources)
        prepared = await hass.async_add_executor_job(_prepare_profiles, baseline.receipt, composition.evidence)
        observed = await async_observe_discovery_inputs(hass, guard=lambda: None)
        failures = []
        slot = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(slot, 'controlled native graph qualification')
        def raised(code, offset, error):
            if Path(code.co_filename).name in ('powercalc_profile_binding.py', 'powercalc_source_binding.py'):
                reason = str(error)
                if re.fullmatch(r'(?:powercalc|profile)_[a-z0-9_]{1,100}', reason):
                    row = (Path(code.co_filename).name, code.co_name, type(error).__name__, reason)
                    if row not in failures:
                        failures.append(row)
        try:
            sys.monitoring.register_callback(slot, sys.monitoring.events.RAISE, raised)
            sys.monitoring.set_events(slot, sys.monitoring.events.RAISE)
            rows = _sample(hass, vendor, composition.evidence, observed, captured, prepared)
        finally:
            sys.monitoring.set_events(slot, 0)
            sys.monitoring.register_callback(slot, sys.monitoring.events.RAISE, None)
            sys.monitoring.free_tool_id(slot)
        inventory = json.loads(captured.payload_json)['objects']
        print('CONTROLLED_NATIVE_GRAPH_INACTIVE', json.dumps([
            (row['entity_id'], row['type'].rsplit('.', 1)[-1], row['registry_id'] is not None,
             row['references']) for row in inventory if not row['active_platforms']]))
        print('CONTROLLED_NATIVE_GRAPH_ROWS', json.dumps([(row.entity_id, row.relation_kind,
              row.source_verified, row.reason) for row in rows]))
        print('CONTROLLED_NATIVE_GRAPH_FAILURES', json.dumps(failures))
        self.assertTrue(rows and all(row.source_verified for row in rows))
        self.assertEqual(len(rows), len(self.graph))
        self.assertIsNone(await runtime.journal.load(op))

    async def test_native_removed_population_snapshot(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.components.sensor import SensorExtraStoredData
        from custom_components.ha_entity_renamer.controlled_powercalc_population import snapshot_removed
        from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
        hass, runtime = self.f.hass, self._runtime()
        await self._fixture_preflight()
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(2)
        await self.sources[0].async_turn_off()
        await hass.async_block_till_done()
        await self.sum_energy._prev_state_store.persist_states()
        await self._flush_documents()
        mapping = {self.yaml_source: 'light.controlled_pc_target',
                   self.sum_energy.entity_id: 'sensor.controlled_pc_group_target'}
        async def refresh():
            return None
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='synthetic-snapshot',
            guard=lambda: True, refresh=refresh,
            entity_names={self.sum_energy.entity_id: 'Controlled PC total'})
        op = review['operation_id']
        async with runtime.operation_lock:
            bound = await NativeTemplateStateServer.bind_prepared(hass, op, runtime.reviews[op].plan,
                guard=lambda: True, refresh=refresh)
            try:
                population = bound.powercalc_population
            finally:
                bound.close()
        self.assertIsNone(await runtime.journal.load(op))
        entries = tuple(hass.config_entries.async_entries('powercalc'))
        groups = {counter.entry_id for counter in population.counters if counter.kind == 'group'}
        for entry in sorted((entry for entry in entries if entry.disabled_by is None),
                            key=lambda entry: (entry.entry_id not in groups, entry.entry_id)):
            self.assertTrue(await hass.config_entries.async_unload(entry.entry_id))
        for platform in tuple(async_get_platforms(hass, 'powercalc')):
            await platform.async_reset()
        actual = snapshot_removed(hass, population)
        self.assertEqual(len(actual), len(population.counters))
        for identity, old, new, kind, payload in actual:
            counter = next(c for c in population.counters if c.registry_id == identity)
            self.assertEqual((old, new, kind), (counter.entity_id, counter.target_id, counter.kind))
            data = json.loads(payload.after_json)['extra_data']
            exact = counter.entity._state if kind == 'integration' else counter.entity._native_value_exact
            parsed = SensorExtraStoredData.from_dict(data)
            self.assertIsNotNone(parsed)
            self.assertEqual(Decimal(parsed.native_value).as_tuple(), exact.as_tuple())
        self.assertIsNone(await runtime.journal.load(op))

    async def test_native_population_restore_publications(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from custom_components.ha_entity_renamer.controlled_powercalc_population import snapshot_removed
        from custom_components.ha_entity_renamer.controlled_powercalc_handoff import _watch_restored_counters
        from custom_components.ha_entity_renamer.native_restore_state_payload import materialize_integration_payload
        from custom_components.ha_entity_renamer.powercalc_group_restore import materialize as materialize_group
        from custom_components.ha_entity_renamer.powercalc_pss_handoff import _clone
        from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
        from ordinary_network_scope import native_dummy_mdns
        hass, root, runtime = self.f.hass, self.f.root, self._runtime()
        await self._fixture_preflight()
        old, new = self.sum_energy.entity_id, 'sensor.restore_publication_target'
        async def refresh(): return None
        review = await runtime.native_yaml_prepare(hass,
            {self.yaml_source: 'light.restore_publication_target', old: new}, actor_id='seam-population',
            guard=lambda: True, refresh=refresh, entity_names={old: 'Restore publication target'})
        op = review['operation_id']
        async with runtime.operation_lock:
            bound = await NativeTemplateStateServer.bind_prepared(hass, op, runtime.reviews[op].plan,
                guard=lambda: True, refresh=refresh)
            try:
                population = bound.powercalc_population
            finally:
                bound.close()
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(2)
        await self.sources[0].async_turn_off()
        await hass.async_block_till_done()
        pss, restore = self.sum_energy._prev_state_store, hass.data[DATA_RESTORE_STATE]
        await pss.persist_states()
        await restore.async_dump_states()
        await self._flush_documents()
        evidence = {'network_before': native_dummy_mdns(hass), 'phase': 'before'}
        out = Path('/qa/population-publication-evidence.json')
        def physical():
            return {name: base64.b64encode((root / '.storage' / name).read_bytes()).decode()
                    if (root / '.storage' / name).is_file() else None
                    for name in ('auth', 'core.entity_registry', 'core.device_registry',
                                 'core.config_entries', 'core.restore_state', 'powercalc_group', 'energy')}
        evidence['before'] = await hass.async_add_executor_job(physical)
        try:
            entries = tuple(hass.config_entries.async_entries('powercalc'))
            groups = {counter.entry_id for counter in population.counters if counter.kind == 'group'}
            for entry in sorted((entry for entry in entries if entry.disabled_by is None),
                                key=lambda entry: (entry.entry_id not in groups, entry.entry_id)):
                self.assertTrue(await hass.config_entries.async_unload(entry.entry_id))
            for platform in tuple(async_get_platforms(hass, 'powercalc')):
                await platform.async_reset()
            evidence['phase'] = 'unloaded'
            payloads = snapshot_removed(hass, population)
            watched = []
            expected = {}
            for uuid, before, after, kind, payload in payloads:
                restore.last_states[after] = (materialize_integration_payload(payload) if kind == 'integration'
                                              else materialize_group(payload))
                row = json.loads(payload.after_json)['extra_data']
                from homeassistant.components.sensor import SensorExtraStoredData
                parsed = SensorExtraStoredData.from_dict(row)
                expected[after] = Decimal(parsed.native_value)
                watched.append({'uuid': uuid, 'after_id': after, 'kind': kind})
            pss.states[new] = {key: _clone(state, key) for key, state in pss.states[old].items()}
            await pss.persist_states()
            await restore.async_dump_states()
            self.assertEqual(self.f.entity.async_update_entity(old, new_entity_id=new).id,
                             self.sum_energy.registry_entry.id)
            config = dict(self.group.data)
            self.assertEqual(config['_energy_entity'], old)
            config['_energy_entity'] = new
            hass.config_entries.async_update_entry(self.group, data=config)
            await self._flush_documents()
            restored, stop_watch = _watch_restored_counters(hass, watched)
            try:
                await hass.services.async_call('powercalc', 'reload', {}, blocking=True)
            finally:
                stop_watch()
            evidence['phase'] = 'setup'
            self.assertEqual(set(restored), set(expected))
            for name, value in expected.items():
                self.assertEqual(Decimal(restored[name]['value']).as_tuple(), value.as_tuple())
            evidence['exact_publications'] = len(restored)
            active = {entity.entity_id: entity for platform in async_get_platforms(hass, 'powercalc')
                      for entity in platform.entities.values()}
            current = active[new]
            before_stimulus = current._native_value_exact
            await self.sources[0].async_turn_on(brightness=255)
            async with asyncio.timeout(12):
                while current._native_value_exact <= before_stimulus:
                    await asyncio.sleep(0.05)
            evidence['future_stimulus'] = True
        finally:
            evidence['final'] = await hass.async_add_executor_job(physical)
            evidence['network_after'] = native_dummy_mdns(hass)
            evidence['final_entities'] = sorted(entity.entity_id for platform in async_get_platforms(hass, 'powercalc')
                                                 for entity in platform.entities.values())
            record = await runtime.journal.load(op)
            evidence['journal'] = None if record is None else record.status
            out.write_text(json.dumps(evidence, sort_keys=True))

    async def test_native_prepared_original_roundtrip_and_binding(self):
        from dataclasses import replace
        from custom_components.ha_entity_renamer import _yaml_factory_for_plan
        from custom_components.ha_entity_renamer.native_active_execution_pins import check_census
        from custom_components.ha_entity_renamer.websocket_api import _recovery_authority
        from homeassistant.auth.const import GROUP_ID_ADMIN
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_powercalc_composition import (
            ControlledPowercalcComposition, encode_controlled_powercalc, decode_controlled_powercalc)
        from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
        from custom_components.ha_entity_renamer.native_template_yaml_server import _controlled_powercalc_plan

        hass, runtime = self.f.hass, self._runtime()
        await self._fixture_preflight()
        mapping = {self.yaml_source: 'light.controlled_pc_target',
                   self.sum_energy.entity_id: 'sensor.controlled_pc_group_target'}
        user = await hass.auth.async_create_user('Synthetic contract administrator', group_ids=[GROUP_ID_ADMIN])
        token = await hass.auth.async_create_refresh_token(user, client_id='https://renamer.invalid/contract')
        hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
        guard, refresh = _recovery_authority(hass, SimpleNamespace(user=user), runtime)
        await refresh()
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(2)
        await self.sources[0].async_turn_off()
        await self.sources[1].async_turn_on(brightness=255)
        await asyncio.sleep(2)
        await self.sources[1].async_turn_off()
        await hass.async_block_till_done()
        await self.sum_energy._prev_state_store.persist_states()
        await hass.data[DATA_RESTORE_STATE].async_dump_states()
        await self._flush_documents()
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id=user.id,
            guard=guard, refresh=refresh,
            entity_names={self.sum_energy.entity_id: 'Controlled PC total'})
        op = review['operation_id']
        retained = runtime.reviews[op]
        plan = retained.plan
        self.assertEqual(plan.fingerprint, review['fingerprint'])
        baseline, original = runtime.preparations.resolve_composition(plan.native_inputs)
        self.assertIs(type(original), ControlledPowercalcComposition)
        encoded = encode_controlled_powercalc(original, baseline)
        self.assertEqual(decode_controlled_powercalc(encoded, baseline), original)
        self.assertTrue(_controlled_powercalc_plan(plan, original))
        self.assertTrue(review['dependencies']['config_entries'])
        async def bind_as_apply():
            for script, census in retained.execution_admission:
                check_census(script, census)
            for proof in retained.trace_admission:
                proof.check_review()
            for proof in (retained.template_admission, retained.own_admission, retained.entry_admission):
                if proof is not None:
                    proof.check_review() if proof is retained.template_admission else proof.check()
            factory = _yaml_factory_for_plan(runtime.preparations, plan)
            self.assertIs(factory, NativeTemplateStateServer)
            bound = await factory.bind_prepared(hass, op, plan, guard=guard, refresh=refresh)
            try:
                for script, census in retained.execution_admission:
                    check_census(script, census)
                phases = getattr(bound.consumers, 'phases', None)
                actual = tuple(getattr(phases, 'admission', ()))
                self.assertEqual(tuple(script for script, _ in actual),
                                 tuple(script for script, _ in retained.execution_admission))
                self.assertEqual(bound.plan, plan)
                self.assertEqual(bound.composition, original)
                self.assertEqual(bound.mapping, mapping)
                state = bound.chain.reconstruct_inputs()
                self.assertEqual(state.head, bound.chain.root_digest)
                # Reproduce the native YAML writer's private staging/backup
                # directory creation with no target replacement or activation.
                def check_staging_boundary():
                    from custom_components.ha_entity_renamer.file_mutations import _pending_folder, _backup
                    from custom_components.ha_entity_renamer.yaml_plan import _Reader, Limits
                    state.assert_current(bound.receipt)
                    reader = _Reader(str(self.f.root), Limits())
                    try:
                        row = bound.change.files[0]
                        raw, info = reader.read(row.path)
                        folder = _pending_folder(reader, info.st_dev)
                        os.close(folder)
                        state.assert_current(bound.receipt)
                        _backup(reader, row.path, raw, 'fixture_staging_check')
                        state.assert_current(bound.receipt)
                    finally:
                        os.close(reader.root)
                await hass.async_add_executor_job(check_staging_boundary)
                self.assertIsNotNone(bound._coordinator())
                self.assertIsNone(await bound.journal.load(op))
            finally:
                bound.close()
        async with runtime.operation_lock:
            await asyncio.create_task(bind_as_apply())
            with self.assertRaises(Exception):
                await NativeTemplateStateServer.bind_prepared(hass, op,
                    replace(plan, coverage_evidence='0' * 64), guard=guard, refresh=refresh)
        self.assertIsNone(await runtime.journal.load(op))

    async def test_public_mixed_forward_and_fresh_inverse(self):
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.db_schema import Statistics, StatisticsMeta, StatisticsShortTerm
        from homeassistant.components.recorder.util import session_scope
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from homeassistant.auth.const import GROUP_ID_ADMIN
        from sqlalchemy import select
        from ordinary_network_scope import native_dummy_mdns

        hass, root, runtime = self.f.hass, self.f.root, self._runtime()
        user = await hass.auth.async_create_user('Synthetic controlled administrator', group_ids=[GROUP_ID_ADMIN])
        refresh_token = await hass.auth.async_create_refresh_token(user, client_id='https://renamer.invalid/controlled-pc')
        access_token = hass.auth.async_create_access_token(refresh_token, remote_ip='127.0.0.1')
        ws = None
        old_source, new_source = self.yaml_source, 'light.controlled_pc_target'
        old_group, new_group = self.sum_energy.entity_id, 'sensor.controlled_pc_group_target'
        combined = getattr(self, 'combined_fixture', False)
        preflight_only = getattr(self, 'preflight_only', False)
        old_script, new_script = 'script.controlled_pc_source', 'script.controlled_pc_target'
        source_uuid, group_uuid = self.sources[0].registry_entry.id, self.sum_energy.registry_entry.id
        recorder = get_instance(hass)
        evidence = {'network_before': native_dummy_mdns(hass)}
        evidence_file = Path(os.environ.get('RENAMER_CONTROLLED_PC_EVIDENCE', '/qa/controlled-pc-public.json'))
        op = fingerprint = None
        next_request = 2
        monitor = sys.monitoring
        monitor_slot = next(index for index in range(6) if monitor.get_tool(index) is None)
        monitor.use_tool_id(monitor_slot, 'controlled Powercalc native diagnostic')
        def observe_exception(code, offset, error):
            if ('/custom_components/ha_entity_renamer/' in code.co_filename
                    and not isinstance(error, (StopIteration, StopAsyncIteration, GeneratorExit))):
                value = str(error)
                reason = (value if re.fullmatch(r'(?:template|powercalc|controlled|registry|yaml|review|native)_[a-z0-9_]{1,100}', value) else None)
                line = next((number for start, end, number in code.co_lines()
                             if start <= offset < end), None)
                row = {'file': Path(code.co_filename).name, 'function': code.co_name,
                       'line': line, 'type': type(error).__name__, 'code': reason}
                if code.co_name == '_need' and str(error) == 'controlled_powercalc_handoff_source_changed_after_intent':
                    try:
                        frame = sys._getframe(1).f_back
                        v, g = frame.f_locals, frame.f_globals
                        equal, freeze, snap, idle, read = (g['_exact_json_equal'], g['_freeze_json'],
                            g['_snapshot'], g['_idle'], v['read_entry'])
                        current_pss = snap(v['pss'])
                        before_pss = v['before_unload']
                        owned = {c.entity_id for c in v['self'].population.counters if c.kind == 'group'}
                        evidence['handoff_source_diff'] = {
                            'disk_equal': (v['pss_check'], v['pss_check_id'], v['restore_check'], v['restore_check_id']) ==
                                          (v['pss_before'], v['pss_identity'], v['restore_before'], v['restore_identity']),
                            'pss_bytes_equal': v['pss_check'] == v['pss_before'],
                            'pss_identity_equal': v['pss_check_id'] == v['pss_identity'],
                            'restore_bytes_equal': v['restore_check'] == v['restore_before'],
                            'restore_identity_equal': v['restore_check_id'] == v['restore_identity'],
                            'root_equal': v['root_check'] == v['root_check2'] == v['root_sig'],
                            'storage_equal': v['storage_check'] == v['storage_check2'] == v['storage_sig'],
                            'pss_memory_equal': equal(current_pss, freeze(before_pss)),
                            'pss_changed_groups': [(key, key in owned) for key in sorted(set(current_pss) | set(before_pss))
                                if not equal(current_pss.get(key), freeze(before_pss.get(key)))],
                            'entry_population_equal': tuple(v['self'].hass.config_entries.async_entries('powercalc')) == v['entries'],
                            'entry_changed': [entry.entry_id for entry in v['entries']
                                if not equal(read(v['self'].hass, entry.entry_id), freeze(v['entry_images'][entry.entry_id]))],
                            'restore_idle': idle(v['data']), 'pss_idle': idle(v['pss'])}
                    except BaseException as problem:
                        evidence['handoff_diagnostic_error'] = type(problem).__name__
                if code.co_name == 'need' and str(error) == 'powercalc_profile_profile_producer_changed':
                    try:
                        caller = sys._getframe(1).f_back
                        v = caller.f_locals
                        source, producer, row, profile = (v['source'], v['producer'], v['source_row'], v['profile'])
                        evidence['profile_producer_fence'] = {
                            'source_type_matches': type(source) is v['cls']['SourceEntity'],
                            'entity_id_matches': source.entity_id == profile['source'],
                            'registry_object_matches': source.entity_entry is producer.registry_entry,
                            'unique_id_matches': source.unique_id == row['unique_id'],
                            'object_id_matches': source.object_id == profile['source'].split('.')[1],
                            'domain_matches': source.domain == 'light'}
                    except BaseException as problem:
                        evidence['profile_producer_diagnostic_error'] = type(problem).__name__
                if code.co_name == '_fail' and str(error) == 'registry_receipt_input_graph_changed':
                    caller = sys._getframe(1).f_back
                    if caller is not None and caller.f_code.co_name == 'assert_current':
                        values = caller.f_locals
                        before = {row[0]: row[1:] for row in values.get('expected', ())}
                        after = {row[0]: row[1:] for row in values.get('current', ())}
                        evidence.setdefault('input_graph_diffs', []).append({
                            'directory': values.get('directory'), 'entries': [
                                (name, before.get(name), after.get(name))
                                for name in sorted(before.keys() | after.keys())
                                if before.get(name) != after.get(name)]})
                if code.co_name == '_need' and str(error) == 'controlled_powercalc_handoff_setup_graph_unverified':
                    caller = sys._getframe(1).f_back
                    rows = () if caller is None else caller.f_locals.get('first', ())
                    evidence['setup_graph_rows'] = [
                        {'kind': row.relation_kind, 'verified': row.source_verified, 'reason': row.reason}
                        for row in rows]
                failures = evidence.setdefault('native_failures', [])
                if row not in failures:
                    failures.append(row)
                    del failures[:-120]
            if (Path(code.co_filename).name == 'yaml_validation.py'
                    and type(error).__name__ == 'YAMLValidationError'
                    and str(error) == 'native_config_rejected'):
                for note in getattr(error, '__notes__', ()):
                    prefix = 'native_checker_diagnostic='
                    if type(note) is str and note.startswith(prefix):
                        evidence['checker_diagnostic'] = json.loads(note[len(prefix):])
        monitor.register_callback(monitor_slot, monitor.events.RAISE, observe_exception)
        monitor.set_events(monitor_slot, monitor.events.RAISE)
        def stop_monitor():
            monitor.set_events(monitor_slot, 0)
            monitor.register_callback(monitor_slot, monitor.events.RAISE, None)
            monitor.free_tool_id(monitor_slot)
        self.addCleanup(stop_monitor)
        # Verify this exact monitor observes a real native raise before effects.
        from custom_components.ha_entity_renamer.yaml_plan import _mapping, YAMLPlanError
        with self.assertRaises(YAMLPlanError):
            _mapping({'invalid': 'invalid'})
        self.assertTrue(any(row['file'] == 'yaml_plan.py' and row['type'] == 'YAMLPlanError'
                            and type(row['line']) is int for row in evidence['native_failures']))
        evidence['native_failures'].clear()

        def statistics(entity_id):
            def value(item):
                return {'datetime': item.isoformat()} if hasattr(item, 'isoformat') else item
            with session_scope(session=recorder.get_session(), read_only=True) as session:
                meta = session.execute(select(StatisticsMeta).where(
                    StatisticsMeta.statistic_id == entity_id)).scalar_one_or_none()
                if meta is None:
                    return {'metadata': None, 'short': [], 'full': []}
                result = {'metadata': {col.name: value(getattr(meta, col.name))
                                       for col in StatisticsMeta.__table__.columns}}
                for name, table in (('short', StatisticsShortTerm), ('full', Statistics)):
                    rows = session.execute(select(table).where(table.metadata_id == meta.id).order_by(table.id)).scalars()
                    result[name] = [{col.name: value(getattr(row, col.name))
                                     for col in table.__table__.columns} for row in rows]
                return result

        async def snapshot(label):
            def physical():
                if combined:
                    import sqlite3
                    source = sqlite3.connect((root / 'fixture.db').as_uri() + '?mode=ro', uri=True)
                    try:
                        destination = sqlite3.connect(evidence_file.parent / (label + '-recorder.sqlite'))
                        try:
                            source.backup(destination)
                        finally:
                            destination.close()
                    finally:
                        source.close()
                    # Preserve every regular fixture configuration and store;
                    # Recorder is captured separately via SQLite's backup API.
                    inventory = {}
                    for path in sorted(root.rglob('*')):
                        if path.is_symlink():
                            raise RuntimeError('fixture_inventory_symlink')
                        if (not path.is_file() or path.name.startswith('fixture.db')
                                or '__pycache__' in path.parts):
                            continue
                        relative = path.relative_to(root).as_posix()
                        inventory[relative] = base64.b64encode(path.read_bytes()).decode()
                    (evidence_file.parent / (label + '-files.json')).write_text(
                        json.dumps(inventory, sort_keys=True))
                keys = ('auth', 'core.entity_registry', 'core.device_registry', 'core.config_entries',
                        'core.restore_state', 'powercalc_group', 'energy', 'core.area_registry',
                        'core.floor_registry', 'core.label_registry')
                result = {}
                for key in keys:
                    path = root / '.storage' / key
                    result[key] = base64.b64encode(path.read_bytes()).decode() if path.is_file() else None
                for key in ('configuration.yaml', 'controlled_template.yaml',
                            'controlled_automations.yaml', 'controlled_scripts.yaml'):
                    result[key] = base64.b64encode((root / key).read_bytes()).decode()
                return result
            if combined:
                await recorder.async_block_till_done()
            payload = {'raw': await hass.async_add_executor_job(physical), 'entities': {},
                       'statistics_old': await recorder.async_add_executor_job(statistics, old_group),
                       'statistics_new': await recorder.async_add_executor_job(statistics, new_group)}
            if combined:
                states = {state.entity_id: {'state': state.state, 'attributes': dict(state.attributes)}
                          for state in hass.states.async_all()}
                payload['runtime_states'] = json.loads(json.dumps(states, sort_keys=True, default=str))
                payload['script_services'] = sorted(hass.services.async_services().get('script', {}))
            for platform in async_get_platforms(hass, 'powercalc'):
                for name, entity in platform.entities.items():
                    payload['entities'][name] = {'uuid': entity.registry_entry.id,
                        'state': str(getattr(entity, '_state', None)),
                        'last_valid': str(getattr(entity, '_last_valid_state', None)),
                        'group_total': str(getattr(entity, '_native_value_exact', None))}
            evidence[label] = payload
            await hass.async_add_executor_job(evidence_file.write_text, json.dumps(evidence, sort_keys=True, default=str))
            return payload

        async def stimulate():
            await self.sources[0].async_turn_on(brightness=255)
            await asyncio.sleep(2)
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()
            await asyncio.sleep(2)
            await hass.async_block_till_done()

        async def read_operation():
            nonlocal next_request
            if op is None:
                return None
            next_request += 1
            reply = await request(next_request, 'recovery/inspect', operation_id=op, fingerprint=fingerprint)
            if not reply.get('success'):
                return reply
            return reply['result']

        async def request(index, command, **payload):
            phases = evidence.setdefault('phase_times', [])
            phase = {'command': command, 'started_monotonic': time.monotonic()}
            phases.append(phase)
            evidence_file.write_text(json.dumps(evidence, sort_keys=True))
            try:
                await ws.send_json({'id': index, 'type': 'ha_entity_renamer/' + command, **payload})
                return await ws.receive_json(timeout=2400)
            finally:
                phase['finished_monotonic'] = time.monotonic()
                evidence_file.write_text(json.dumps(evidence, sort_keys=True))

        def counters_by_uuid():
            return {entity.registry_entry.id: entity for platform in async_get_platforms(hass, 'powercalc')
                    for entity in platform.entities.values()}

        # Capture the actual removed objects at the unload seam and the first
        # native publications at setup. Later readings may legitimately advance.
        from custom_components.ha_entity_renamer import controlled_powercalc_handoff as handoff_module
        removed_snapshots, restored_publications, pss_snapshots, pss_handoffs = [], [], [], []
        original_snapshot = handoff_module.snapshot_removed
        original_watch = handoff_module._watch_restored_counters
        def capture_removed(hass_arg, population, **kwargs):
            result = original_snapshot(hass_arg, population, **kwargs)
            values = {}
            pss_values = {}
            for counter in population.counters:
                entity = counter.entity
                value = entity._native_value_exact if counter.kind == 'group' else entity._state
                last = None if counter.kind == 'group' else entity._last_valid_state
                values[counter.registry_id] = (value.as_tuple(), None if last is None else last.as_tuple())
                if counter.kind == 'group':
                    pss_values[counter.registry_id] = {
                        key: None if state is None else state.as_dict()
                        for key, state in entity._prev_state_store.states[counter.entity_id].items()}
            removed_snapshots.append(values)
            pss_snapshots.append(pss_values)
            return result
        def capture_publications(hass_arg, population):
            values, stop = original_watch(hass_arg, population)
            restored_publications.append(values)
            store = hass_arg.data['powercalc_group_storage']
            pss_handoffs.append({row['uuid']: {
                key: None if state is None else state.as_dict()
                for key, state in store.states[row['after_id']].items()}
                for row in population if row['kind'] == 'group'})
            return values, stop
        self.enterContext(patch.object(handoff_module, 'snapshot_removed', capture_removed))
        self.enterContext(patch.object(handoff_module, '_watch_restored_counters', capture_publications))
        def compare_counters(index):
            self.assertGreater(len(removed_snapshots), index)
            self.assertGreater(len(restored_publications), index)
            actual = {row['uuid']: row for row in restored_publications[index].values()}
            seam = {'removed': {uid: {'value': str(Decimal(value)),
                        'last_valid': None if last is None else str(Decimal(last))}
                        for uid, (value, last) in removed_snapshots[index].items()},
                    'published': {uid: {'value': row['value'], 'last_valid': row['last_valid']}
                                  for uid, row in actual.items()},
                    'pss_removed': pss_snapshots[index], 'pss_published': pss_handoffs[index]}
            evidence.setdefault('independent_seams', []).append(
                json.loads(json.dumps(seam, sort_keys=True, default=str)))
            evidence_file.write_text(json.dumps(evidence, sort_keys=True))
            self.assertEqual(set(actual), set(removed_snapshots[index]))
            self.assertEqual(pss_snapshots[index], pss_handoffs[index])
            for uid, (value, last) in removed_snapshots[index].items():
                row = actual[uid]
                self.assertEqual(Decimal(row['value']).as_tuple(), value)
                self.assertEqual(None if row['last_valid'] is None else Decimal(row['last_valid']).as_tuple(), last)
            evidence.setdefault('actual_unload_and_setup', []).append({
                uid: {'value': row['value'], 'last_valid': row['last_valid']} for uid, row in actual.items()})

        def check_stats(before, after, target):
            self.assertIsNotNone(before['metadata'])
            self.assertTrue(before['short'])
            old_meta, new_meta = dict(before['metadata']), dict(after['metadata'])
            self.assertEqual(old_meta.pop('statistic_id'), old_group)
            self.assertEqual(new_meta.pop('statistic_id'), target)
            self.assertEqual(old_meta, new_meta)
            for key in ('short', 'full'):
                indexed = {row['id']: row for row in after[key]}
                self.assertTrue(all(indexed.get(row['id']) == row for row in before[key]))

        try:
            await self._fixture_preflight()
            if combined:
                from types import MappingProxyType
                from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
                from homeassistant.config_entries import ConfigEntry, ConfigEntryState
                from homeassistant.components.sensor import SensorEntity
                from homeassistant.helpers import device_registry as dr, area_registry, floor_registry, label_registry
                from homeassistant.helpers.entity_component import DATA_INSTANCES
                scripts = root / 'controlled_scripts.yaml'
                with scripts.open('a') as stream:
                    stream.write('controlled_pc_caller:\n  sequence:\n  - action: script.controlled_pc_source\n')
                await hass.services.async_call('script', 'reload', {}, blocking=True)
                script_uuid = self.f.entity.async_get(old_script).id
                device_entry = ConfigEntry(domain='threshold', title='Combined device owner', data={},
                    options={'name': 'Combined threshold', 'entity_id': self.gui_source,
                             'lower': 20, 'upper': None, 'hysteresis': 0},
                    discovery_keys=MappingProxyType({}), source='user', version=1, minor_version=1,
                    subentries_data=[], unique_id='combined-device-owner')
                await hass.config_entries.async_add(device_entry)
                await hass.async_block_till_done()
                self.assertIs(device_entry.state, ConfigEntryState.LOADED)
                class DeviceSensor(SensorEntity):
                    _attr_unique_id = 'combined-observable-device-sensor'
                    _attr_name = 'Combined device sensor'
                    _attr_native_value = 3
                extra = DeviceSensor()
                await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([extra])
                extra_id, extra_uuid = extra.entity_id, extra.registry_entry.id
                device = dr.async_get(hass).async_get_or_create(config_entry_id=device_entry.entry_id,
                    identifiers={('threshold', 'controlled-combined-device')}, name='Combined device')
                self.f.entity.async_update_entity(extra_id, device_id=device.id)
                await hass.async_block_till_done()
                platform = extra.platform
                await platform.async_remove_entity(extra_id)
                extra = DeviceSensor()
                extra._attr_has_entity_name = True
                extra._attr_device_info = {'identifiers': {('threshold', 'controlled-combined-device')},
                                           'name': 'Combined device'}
                platform.config_entry = device_entry
                await platform.async_add_entities([extra])
                await hass.async_block_till_done()
                self.assertEqual(extra.registry_entry.id, extra_uuid)
                self.assertEqual(extra.device_entry.id, device.id)
                self.assertIs(extra.platform.config_entry, device_entry)
                extra_new = 'sensor.combined_device_target'
                entry = ConfigEntry(domain='template', title='Controlled entry', data={},
                    options={'template_type': 'sensor', 'name': 'Controlled entry',
                             'state': "{{ states('" + extra_id + "') | int(0) * 2 }}"},
                    version=TemplateConfigFlowHandler.VERSION, minor_version=TemplateConfigFlowHandler.MINOR_VERSION,
                    source='user', discovery_keys=MappingProxyType({}), subentries_data=[], unique_id=None,
                    pref_disable_polling=True)
                await hass.config_entries.async_add(entry)
                await hass.async_block_till_done()
                self.assertIs(entry.state, ConfigEntryState.LOADED)
                entry_entities = [entity for platform in async_get_platforms(hass, 'template')
                                  if platform.config_entry is entry for entity in platform.entities.values()]
                self.assertEqual(len(entry_entities), 1)
                entry_entity_id = entry_entities[0].entity_id
                entry_uuid = self.f.entity.async_get(entry_entity_id).id
                managers = []
                for module, member in ((floor_registry, 'floors'), (label_registry, 'labels'), (area_registry, 'areas')):
                    manager = hass.data.get(module.DATA_REGISTRY)
                    if manager is None:
                        await module.async_load(hass)
                        manager = hass.data[module.DATA_REGISTRY]
                    elif not hasattr(manager, member):
                        await manager.async_load()
                    managers.append(manager)
                floors, labels, areas = managers
                floor = floors.async_create('Combined floor', level=2)
                label = labels.async_create('Combined label', color='blue')
                area = areas.async_create('Combined area', floor_id=floor.floor_id, labels={label.label_id})
                for manager in managers:
                    await manager._store.async_save(manager._data_to_save())
                events, automation_events = [], []
                self.addCleanup(hass.bus.async_listen('controlled_pc_script', lambda event: events.append(event.data)))
                self.addCleanup(hass.bus.async_listen('controlled_pc_automation',
                    lambda event: automation_events.append(event.data)))
                async def call_caller():
                    count, automation_count = len(events), len(automation_events)
                    await hass.services.async_call('script', 'controlled_pc_caller', {}, blocking=True)
                    await hass.services.async_call('automation', 'trigger',
                        {'entity_id': 'automation.controlled_pc_source'}, blocking=True)
                    await hass.async_block_till_done()
                    evidence.setdefault('caller_samples', []).append({'before': count, 'after': len(events),
                        'automation_before': automation_count, 'automation_after': len(automation_events)})
                    self.assertGreater(len(events), count)
                    self.assertGreater(len(automation_events), automation_count)
                async def sample_entry(value):
                    extra._attr_native_value = value
                    extra.async_write_ha_state()
                    await hass.async_block_till_done()
                    actual = hass.states.get(entry_entity_id).state
                    evidence.setdefault('entry_samples', []).append({'source': value, 'entry': actual})
                    evidence_file.write_text(json.dumps(evidence, sort_keys=True))
                    self.assertEqual(actual, str(value * 2))
                await call_caller()
                await sample_entry(5)
                evidence['combined_identities'] = {'script_uuid': script_uuid,
                    'entry_uuid': entry_uuid, 'entry_id': entry.entry_id,
                    'entry_entity_id': entry_entity_id, 'device_id': device.id,
                    'extra_id': extra_id, 'extra_new': extra_new, 'extra_uuid': extra_uuid,
                    'old_script': old_script, 'new_script': new_script,
                    'old_source': old_source, 'new_source': new_source,
                    'old_group': old_group, 'new_group': new_group}
                producers = []
                for entity in self.graph:
                    source = getattr(entity, '_source_entity', None)
                    if getattr(source, 'entity_id', None) == old_source:
                        producers.append({'entity_id': entity.entity_id,
                            'registry_object_matches': source.entity_entry is self.sources[0].registry_entry,
                            'unique_id_matches': source.unique_id == self.sources[0].registry_entry.unique_id,
                            'domain_matches': source.domain == 'light'})
                evidence['preflight_producers'] = producers
                evidence_file.write_text(json.dumps(evidence, sort_keys=True))
                self.assertTrue(producers)
                self.assertTrue(all(all(value for key, value in row.items() if key != 'entity_id')
                                    for row in producers))
                await self._flush_documents()
                from custom_components.ha_entity_renamer.native_registry import NativeRegistryBridge, NativeRegistryIO
                from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
                await NativeRegistryBridge(NativeRegistryIO(hass), NativeRecorderGuard(hass)).prepare(
                    'device', device.id, {'name_by_user': 'Combined renamed'}, lambda: None)
                evidence['preflight_device_qualified'] = True
                from homeassistant.components.energy.data import async_get_manager
                from custom_components.ha_entity_renamer.energy_native import NativeEnergy
                _, energy_proposal = await NativeEnergy(hass, await async_get_manager(hass)).plan(
                    {old_source: new_source, old_group: new_group, old_script: new_script, extra_id: extra_new})
                evidence['preflight_energy'] = {'has_changes': bool(energy_proposal and energy_proposal.changes),
                    'markers': [] if energy_proposal is None else list(energy_proposal.markers)}
                evidence_file.write_text(json.dumps(evidence, sort_keys=True))
                self.assertTrue(evidence['preflight_energy']['has_changes'])
                self.assertFalse(evidence['preflight_energy']['markers'])
                if preflight_only:
                    await self._flush_documents()
                    evidence['preflight_families_passed'] = True
                    await snapshot('preflight')
            await stimulate()
            await self.sources[1].async_turn_on(brightness=255)
            await asyncio.sleep(2)
            await self.sources[1].async_turn_off()
            await hass.async_block_till_done()
            if preflight_only:
                initial = await recorder.async_add_executor_job(statistics, old_group)
            else:
                async with asyncio.timeout(420):
                    while True:
                        initial = await recorder.async_add_executor_job(statistics, old_group)
                        if initial['metadata'] is not None and initial['short']:
                            break
                        await asyncio.sleep(2)
            from aiohttp import ClientSession
            client = ClientSession()
            self.addAsyncCleanup(client.close)
            ws = await client.ws_connect('http://127.0.0.1:' + str(hass.http.server_port) + '/api/websocket')
            self.addAsyncCleanup(ws.close)
            self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_required')
            await ws.send_json({'type': 'auth', 'access_token': access_token})
            self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_ok')
            pss = self.sum_energy._prev_state_store
            await pss.persist_states()
            await hass.data[DATA_RESTORE_STATE].async_dump_states()
            await self._flush_documents()
            if combined and not preflight_only:
                history_ids = {old_source: new_source, old_group: new_group, old_script: new_script,
                    extra_id: extra_new, entry_entity_id: entry_entity_id,
                    'automation.controlled_pc_source': 'automation.controlled_pc_source',
                    'script.controlled_pc_caller': 'script.controlled_pc_caller',
                    'sensor.controlled_pc_source': 'sensor.controlled_pc_source'}
                from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
                history_guard = NativeRecorderGuard(hass)
                history_a = await history_guard.snapshot(history_ids)
                history_b = await history_guard.snapshot(history_ids)
                evidence['preflight_recorder_metadata'] = json.loads(json.dumps(
                    {'first': history_a, 'second': history_b}, sort_keys=True, default=str))
                evidence_file.write_text(json.dumps(evidence, sort_keys=True, default=str))
                self.assertEqual(history_a, history_b)
                self.assertIn(old_group, history_a['statistics'])
            baseline = await snapshot('before')
            self.assertIsNotNone(baseline['raw']['auth'])
            from custom_components.ha_entity_renamer.history_guard import NativeHistoryGuard
            from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
            history = NativeHistoryGuard(NativeRecorderGuard(hass))
            history_mapping = {old_source: new_source, old_group: new_group}
            if combined:
                history_mapping[old_script] = new_script
                history_mapping[extra_id] = extra_new
            history_before = await history.capture(history_mapping)
            self.assertGreater(history_before['content']['states']['count'], 0)
            evidence['history_before'] = history_before
            from copy import deepcopy
            energy_before = json.loads(base64.b64decode(baseline['raw']['energy']))['data']
            energy_after = deepcopy(energy_before)
            consumption = energy_after['device_consumption']
            self.assertEqual(consumption, [{'stat_consumption': old_group}])
            consumption[0]['stat_consumption'] = new_group
            self.assertIs(type(self.sum_energy._native_value_exact), Decimal)
            self.assertGreater(self.sum_energy._native_value_exact, 0)
            if not preflight_only:
                self.assertNotEqual(self.sum_energy._native_value_exact,
                                    round(self.sum_energy._native_value_exact, self.sum_energy._rounding_digits))
            entities = [
                {'entity_id': old_source, 'new_entity_id': new_source},
                {'entity_id': old_group, 'new_entity_id': new_group, 'name': 'Controlled PC total'}]
            if combined:
                entities.append({'entity_id': old_script, 'new_entity_id': new_script})
                entities.append({'entity_id': extra_id, 'new_entity_id': extra_new, 'name': 'Combined sensor renamed'})
            reply = await request(1, 'prepare', entities=entities,
                **({'devices': [{'device_id': device.id, 'name_by_user': 'Combined renamed'}]} if combined else {}))
            evidence['review_reply'] = reply
            if reply.get('success'):
                review = reply['result']
                op, fingerprint = review['operation_id'], review['fingerprint']
            await snapshot('review')
            self.assertTrue(reply.get('success'), reply)
            self.assertTrue(review['dependencies']['config_entries'])
            if combined:
                entries = review['dependencies']['config_entries']
                self.assertTrue(any(row.get('kind') == 'powercalc' and row.get('before') == old_group
                                    and row.get('after') == new_group for row in entries))
                self.assertTrue(any(row.get('entity_id') == entry_entity_id and row.get('before') == extra_id
                                    and row.get('after') == extra_new for row in entries))
            if preflight_only:
                evidence['preflight_passed'] = True
                evidence_file.write_text(json.dumps(evidence, sort_keys=True))
                return
            reply = await request(2, 'apply', operation_id=op, fingerprint=fingerprint)
            evidence['apply'] = reply
            forward = await snapshot('after')
            self.assertTrue(reply.get('success'), reply)
            self.assertEqual(reply['result']['state'], 'applied')
            self.assertEqual(self.f.entity.async_get(new_source).id, source_uuid)
            self.assertEqual(self.f.entity.async_get(new_group).id, group_uuid)
            if combined:
                self.assertEqual(self.f.entity.async_get(new_script).id, script_uuid)
                self.assertEqual(self.f.entity.async_get(entry_entity_id).id, entry_uuid)
                self.assertEqual(self.f.entity.async_get(extra_new).id, extra_uuid)
                self.assertIn(extra_new, entry.options['state'])
                self.assertEqual(dr.async_get(hass).async_get(device.id).name_by_user, 'Combined renamed')
                await call_caller()
                await sample_entry(13)
            compare_counters(0)
            self.assertTrue(await history.verify(history_before))
            self.assertEqual(json.loads(base64.b64decode(forward['raw']['energy']))['data'], energy_after)
            check_stats(initial, forward['statistics_new'], new_group)
            self.assertEqual(forward['raw']['auth'], baseline['raw']['auth'])
            if not combined:
                self.assertEqual(forward['raw']['core.device_registry'], baseline['raw']['core.device_registry'])
            current = counters_by_uuid()
            prior_total = current[group_uuid]._native_value_exact
            await stimulate()
            await snapshot('after_stimulus')
            self.assertGreater(current[group_uuid]._native_value_exact, prior_total)
            evidence['inspect_forward'] = await read_operation()
            await snapshot('inspected')
            self.assertTrue(evidence['inspect_forward']['eligible'])
            next_request += 1
            restore_reply = await request(next_request, 'recovery/restore', operation_id=op, fingerprint=fingerprint)
            evidence['restore_reply'] = restore_reply
            result = restore_reply.get('result', {})
            evidence['restore'] = result
            restored = await snapshot('restored')
            self.assertEqual(result['state'], 'rolled_back')
            self.assertEqual(self.f.entity.async_get(old_source).id, source_uuid)
            self.assertEqual(self.f.entity.async_get(old_group).id, group_uuid)
            if combined:
                self.assertEqual(self.f.entity.async_get(old_script).id, script_uuid)
                self.assertEqual(self.f.entity.async_get(entry_entity_id).id, entry_uuid)
                self.assertEqual(self.f.entity.async_get(extra_id).id, extra_uuid)
                self.assertIn(extra_id, entry.options['state'])
                await call_caller()
                await sample_entry(19)
            compare_counters(1)
            self.assertTrue(await history.verify(history_before))
            self.assertEqual(json.loads(base64.b64decode(restored['raw']['energy']))['data'], energy_before)
            check_stats(initial, restored['statistics_old'], old_group)
            for name in ('configuration.yaml', 'controlled_template.yaml',
                         'controlled_automations.yaml', 'controlled_scripts.yaml', 'auth'):
                self.assertEqual(restored['raw'][name], baseline['raw'][name])
            if not combined:
                self.assertEqual(restored['raw']['core.device_registry'], baseline['raw']['core.device_registry'])
            current = counters_by_uuid()
            prior_total = current[group_uuid]._native_value_exact
            await stimulate()
            await snapshot('restored_stimulus')
            self.assertGreater(current[group_uuid]._native_value_exact, prior_total)
        finally:
            primary = sys.exception()
            try:
                await snapshot('final_before_inspect')
            except BaseException as error:
                evidence['final_before_inspect_error'] = type(error).__name__ + ':' + str(error)
                evidence_file.write_text(json.dumps(evidence, sort_keys=True))
                if primary is None:
                    primary = error
                else:
                    primary.add_note('final before inspect: ' + type(error).__name__)
            try:
                evidence['final_inspect'] = await read_operation()
                if combined and evidence.get('restore', {}).get('state') == 'rolled_back':
                    self.assertEqual((evidence['final_inspect']['state'], evidence['final_inspect']['reason']),
                                     ('rolled_back', 'restored_verified'))
            except BaseException as error:
                evidence['final_inspect_error'] = type(error).__name__ + ':' + str(error)
                if primary is None:
                    primary = error
                else:
                    primary.add_note('final inspect: ' + type(error).__name__)
            try:
                evidence['network_after'] = native_dummy_mdns(hass)
            except BaseException as error:
                evidence['network_after_error'] = type(error).__name__ + ':' + str(error)
                if primary is None:
                    primary = error
                else:
                    primary.add_note('network final readback: ' + type(error).__name__)
            try:
                await snapshot('final')
            except BaseException as error:
                evidence['final_capture_error'] = type(error).__name__ + ':' + str(error)
                evidence_file.write_text(json.dumps(evidence, sort_keys=True))
                if primary is None:
                    raise
                primary.add_note('final capture: ' + type(error).__name__)
            if primary is not None and sys.exception() is None:
                raise primary


class CombinedControlledPowercalcNativeTests(ControlledPowercalcPublicNativeTests):
    async def test_combined_fixture_preflight(self):
        self.combined_fixture = True
        self.preflight_only = True
        await self.test_public_mixed_forward_and_fresh_inverse()

    async def test_combined_public_round_trip(self):
        self.combined_fixture = True
        await self.test_public_mixed_forward_and_fresh_inverse()


class MappedControlledPowercalcNativeTests(ControlledPowercalcPublicNativeTests):
    """Fresh fixture born with the destination IDs; no migration or replay."""
    async def _setup_vendor_forward(self):
        base = cold.profiles.ProfileLight
        class DestinationLight(base):
            def __init__(self, name, *, dynamic=False):
                super().__init__(name, dynamic=dynamic)
                if name == 'Public static':
                    self.entity_id = 'light.controlled_pc_target'
        self.f.entity.async_get_or_create('sensor', 'powercalc', 'public-profile-sum_energy',
                                          suggested_object_id='controlled_pc_group_target')
        for unique_id, object_id in (('public-profile-static', 'public_static_power'),
                                     ('public-profile-static_energy', 'public_static_energy')):
            self.f.entity.async_get_or_create('sensor', 'powercalc', unique_id,
                                              suggested_object_id=object_id)
        with patch.object(cold.profiles, 'ProfileLight', DestinationLight):
            await super()._setup_vendor_forward()
        self.assertEqual(self.yaml_source, 'light.controlled_pc_target')
        self.assertEqual(self.sum_energy.entity_id, 'sensor.controlled_pc_group_target')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite((loader.loadTestsFromTestCase(ControlledPowercalcPublicNativeTests),
                               CombinedControlledPowercalcNativeTests('test_combined_fixture_preflight'),
                               CombinedControlledPowercalcNativeTests('test_combined_public_round_trip'),
                               MappedControlledPowercalcNativeTests('test_native_reload_graph_readback')))
