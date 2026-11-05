"""Real vendor PSS serialization of natural samples; no cold or migration claim."""
import json
import unittest

import test_powercalc_numeric_continuity_native as numeric


@unittest.skipUnless(numeric.accepted.fixture.HAS_HA, 'actual supported HA required')
class PowercalcPreviousStateNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = numeric.PowercalcNumericContinuityNativeTests.namespace_source_bytes
    asyncSetUp = numeric.PowercalcNumericContinuityNativeTests.asyncSetUp
    _flush_documents = numeric.PowercalcNumericContinuityNativeTests._flush_documents
    guard = numeric.PowercalcNumericContinuityNativeTests.guard
    module = numeric.PowercalcNumericContinuityNativeTests.module
    prepare_candidate = numeric.PowercalcNumericContinuityNativeTests.prepare_candidate
    chain_root = numeric.PowercalcNumericContinuityNativeTests.chain_root
    ready = numeric.PowercalcNumericContinuityNativeTests.ready

    async def test_diagnostic_actual_source_registry_rename_events(self):
        """Observe the real source transition; this is not a migration test."""
        from homeassistant.core import callback
        await self.sources[0].async_turn_on(brightness=255)
        await self.f.hass.async_block_till_done()
        source = self.sources[0]
        before, after = source.entity_id, 'light.public_static_rename_probe'
        identity = source.registry_entry.id
        native = self.module('native_registry')
        io = native.NativeRegistryIO(self.f.hass)
        recorder = self.module('recorder_guard').NativeRecorderGuard(self.f.hass)
        change = await native.NativeRegistryBridge(io, recorder).prepare(
            'entity', identity, {'entity_id': after}, self.guard)
        witness = await self.module('native_rename_emission_classifier').NativeRenameEmissions.prepare(
            io, change, change.after, guard=self.guard)
        names = {before, after, self.static_power.entity_id, self.static_energy.entity_id}
        observed = []
        owned = []
        @callback
        def on_state(event):
            data = event.data
            if data.get('entity_id') in names:
                old, new = data.get('old_state'), data.get('new_state')
                observed.append((data['entity_id'], None if old is None else old.state,
                                 None if new is None else new.state))
                if witness.is_owned_rename(event):
                    owned.append((data['entity_id'], witness.suppresses_template_boundary(event)))
        remove = self.f.hass.bus.async_listen('state_changed', on_state)
        try:
            witness.mutate()
            proof = await witness.finish()
            await self.f.hass.async_block_till_done()
            row = self.f.entity.async_get(after)
            self.assertIsNotNone(row)
            self.assertEqual(row.id, identity)
            self.assertIsNone(self.f.entity.async_get(before))
            self.assertEqual(source.entity_id, after)
            print('DIAGNOSTIC_NATIVE_SOURCE_RENAME', json.dumps(observed))
            self.assertEqual((proof['rename_remove'], proof['rename_add']), (1, 1))
            self.assertEqual({row[0] for row in owned}, {before, after})
            print('DIAGNOSTIC_SOURCE_CLASSIFIER', json.dumps({'owned': owned, 'neutral': proof['neutral']}))
        finally:
            remove()
            witness.close()

    async def _setup_real_vendor(self):
        from homeassistant.helpers.recorder import async_initialize_recorder
        from homeassistant.setup import async_setup_component
        hass = self.f.hass
        if self._testMethodName in ('test_public_group_without_subtract_dynamic_during_unload',
                                    'test_public_single_child_group_refused_before_effect'):
            self.omit_subtract = True
        if self._testMethodName == 'test_public_single_child_group_refused_before_effect':
            self.single_child_group = True
        async_initialize_recorder(hass)
        self.assertTrue(await async_setup_component(hass, 'recorder', {'recorder': {
            'db_url': 'sqlite:///' + str(self.f.root / 'fixture.db'),
            'commit_interval': 0, 'auto_purge': False, 'auto_repack': False}}))
        await numeric.PowercalcNumericContinuityNativeTests._setup_real_vendor(self)

    async def test_detached_payload_uses_actual_vendor_persisted_states(self):
        from homeassistant.core import State
        await self.ready()
        await self.sources[0].async_turn_on(brightness=64)
        await self.f.hass.async_block_till_done()
        group = self.sum_energy
        store = group._prev_state_store
        self.assertIs(self.f.hass.data['powercalc_group_storage'], store)
        rows = store.states[group.entity_id]
        self.assertTrue(rows)
        self.assertTrue(all(type(state) is State for state in rows.values()))
        # Genuine vendor persistence in the isolated synthetic fixture.
        await store.persist_states()
        raw = await self.f.hass.async_add_executor_job(lambda: __import__('pathlib').Path(store.store.path).read_bytes())
        actual = json.loads(raw)
        self.assertEqual((actual['key'], actual['version'], actual['minor_version']),
                         ('powercalc_group', 2, 1))
        self.assertEqual(set(actual['data'][group.entity_id]), set(rows))
        for key, state in rows.items():
            self.assertEqual(State.from_dict(actual['data'][group.entity_id][key]).as_dict(), state.as_dict())
        codec = self.module('powercalc_previous_state_payload')
        chosen = sorted(rows)[0]
        members = tuple((name, 'sensor.synthetic_pss_renamed' if name == chosen else name)
                        for name in sorted(rows))
        move = codec.GroupMove(group.entity_id, 'sensor.synthetic_pss_group', members)
        payload = codec.prepare(raw, (move,))
        mapped = json.loads(payload.after)['data']['sensor.synthetic_pss_group']
        actual_state = State.from_dict(mapped['sensor.synthetic_pss_renamed'])
        self.assertEqual(actual_state.entity_id, 'sensor.synthetic_pss_renamed')
        original = rows[chosen].as_dict()
        expected = dict(original, entity_id='sensor.synthetic_pss_renamed')
        self.assertEqual(actual_state.as_dict(), expected)
        self.assertEqual(json.loads(payload.after)['data'][group.entity_id], actual['data'][group.entity_id])
        self.assertEqual(await self.f.hass.async_add_executor_job(lambda: __import__('pathlib').Path(store.store.path).read_bytes()), raw)
        print('ACTUAL_PSS_CODEC', {'group': group.entity_id, 'members': len(rows),
                                   'renamed_sample': chosen, 'native_file_unchanged': True})

    async def test_original_capture_pins_actual_pss_and_registry_graph(self):
        import asyncio
        from homeassistant.core import State
        if getattr(self, '_ordinary_driver_test', False):
            import socket
            from homeassistant import auth
            from homeassistant.setup import async_setup_component
            if getattr(self.f.hass, 'auth', None) is None:
                self.f.hass.auth = await auth.auth_manager_from_config(self.f.hass, [], [])
            if getattr(self.f.hass, 'http', None) is None:
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                self.assertTrue(await async_setup_component(self.f.hass, 'http', {'http': {
                    'server_host': '127.0.0.1', 'server_port': port}}))
            from homeassistant.components.http.config import async_get_and_load_store
            http_config = await async_get_and_load_store(self.f.hass)
            if http_config.pending is not None:
                await http_config.async_promote_pending()
            from homeassistant.components.config import automation, script, scene
            for editor in (automation, script, scene):
                self.assertTrue(editor.async_setup(self.f.hass))
        if getattr(self, '_fresh_inverse_test', False):
            # Core publishes this real synthetic entity capability; no
            # State/Event or native callback is replaced.
            self.static_energy._attr_assumed_state = True
            self.static_energy.__dict__.pop('assumed_state', None)
        revoke = [False]
        if getattr(self, '_revoke_during_source', False):
            original_guard = self.guard
            def checked_guard():
                original_guard()
                if revoke[0]:
                    raise ValueError('synthetic_authorization_revoked')
            self.guard = checked_guard
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(1.2)
        await self.sources[0].async_turn_off()
        await self.f.hass.async_block_till_done()
        if getattr(self, '_fresh_inverse_test', False):
            self.static_energy.async_write_ha_state()
            await self.f.hass.async_block_till_done()
            actual_previous = self.sum_energy._prev_state_store.get_entity_state(
                self.sum_energy.entity_id, self.static_energy.entity_id)
            self.assertIsNotNone(actual_previous)
            self.assertIs(actual_previous.attributes.get('assumed_state'), True)
        store = self.sum_energy._prev_state_store
        await store.persist_states()
        self.candidate_group_target = 'sensor.synthetic_new_sum_energy'
        if getattr(self, '_ordinary_driver_test', False):
            # Flush HA's own delayed issue-registry write before ORIGINAL.
            from homeassistant.helpers import issue_registry
            issues = issue_registry.async_get(self.f.hass)
            await issues._store.async_save(issues._data_to_save())
        await self.chain_root()
        module = self.module('powercalc_previous_state_original')
        result = await module.async_capture_powercalc_previous_original(
            self.f.hass, self.receipt, self.evidence, self.original_runtime, guard=self.guard)
        self.assertEqual(result.sources_sha256, self.evidence.powercalc_sources.sha256)
        self.assertEqual(result.core_version, self.receipt.version)
        self.assertEqual(len(result.groups), 1)
        group = result.groups[0]
        self.assertEqual(group.entity_id, self.sum_energy.entity_id)
        self.assertEqual(group.registry_id, self.sum_energy.registry_entry.id)
        self.assertEqual({name for name, _ in group.members}, self.sum_energy._entities)
        for name, uuid in group.members:
            self.assertEqual(self.f.entity.async_get(name).id, uuid)
        live = json.loads(result.memory_json)[group.entity_id]
        self.assertEqual(set(live), set(store.states[group.entity_id]))
        for name, row in live.items():
            self.assertEqual(State.from_dict(row).as_dict(), store.states[group.entity_id][name].as_dict())
        self.assertIsNotNone(result.disk_raw)
        self.assertEqual(json.loads(result.disk_raw)['key'], 'powercalc_group')
        info = __import__('os').stat(store.store.path)
        self.assertEqual(result.disk_identity, (info.st_dev, info.st_ino, info.st_mode))
        for identity, path in ((result.root_identity, self.f.root),
                               (result.storage_identity, self.f.root / '.storage')):
            info = path.stat()
            self.assertEqual(identity, (info.st_dev, info.st_ino, info.st_mode))
        print('ORIGINAL_NATIVE_PSS', {'groups': len(result.groups), 'members': len(group.members),
                                      'disk_present': result.disk_raw is not None})
        prepared_yaml = self.module('powercalc_prepared_yaml')
        executor = self.module('yaml_validation')._settled_executor
        yaml_change = await executor(self.f.hass, lambda: prepared_yaml.capture_powercalc_yaml(
            self.receipt, self.evidence, self.yaml_plan))
        ref = 'powercalc-composition-yaml'
        digest = await self.preparations.create(ref, self.prep.encode_change(yaml_change))
        registry = self.module('native_registry')
        recorder = self.module('recorder_guard').NativeRecorderGuard(self.f.hass)
        bridge = registry.NativeRegistryBridge(registry.NativeRegistryIO(self.f.hass), recorder)
        source_change = await bridge.prepare('entity', self.original_source_uuid,
                                             {'entity_id': 'light.public_static_target'}, self.guard)
        source_ref = 'powercalc-composition-source'
        source_digest = await self.preparations.create(source_ref, self.prep.encode_change(source_change))
        group_change = await bridge.prepare(
            'entity', group.registry_id, {'entity_id': 'sensor.synthetic_new_sum_energy'}, self.guard)
        if getattr(self, '_ordinary_driver_test', False):
            group_change = self.module('powercalc_group_registry_preparation').prepare_group_registry(
                group_change, group, self.receipt.version)
        group_ref = 'powercalc-composition-group'
        group_digest = await self.preparations.create(group_ref, self.prep.encode_change(group_change))
        energy_ref = 'powercalc-composition-energy'
        energy_digest = await self.preparations.create(energy_ref, self.prep.encode_change(self.prepared_energy))
        digests = {source_ref: source_digest, ref: digest, group_ref: group_digest, energy_ref: energy_digest}
        metadata_change = metadata_ref = None
        if getattr(self, '_ordinary_driver_test', False):
            entry_id = self.sum_energy.registry_entry.config_entry_id
            before_entry = self.module('helper_native').read_entry(self.f.hass, entry_id)
            metadata_change = self.module('powercalc_group_metadata').prepare_group_metadata(
                before_entry, group.entity_id, 'sensor.synthetic_new_sum_energy')
            metadata_ref = 'powercalc-composition-group-metadata'
            digests[metadata_ref] = await self.preparations.create(
                metadata_ref, self.prep.encode_change(metadata_change))
        physical_reader = self.module('powercalc_energy_receipts')._read_physical
        physical = await executor(self.f.hass, lambda: physical_reader(
            self.receipt.config_root, self.prepared_energy.before.file_identity,
            result.root_identity, result.storage_identity))
        composition = self.module('powercalc_composition').PowercalcComposition(
            self.evidence, result, tuple(sorted(digests.items())),
            json.loads(source_change.history_before_json), json.loads(group_change.history_before_json),
            self.evidence.powercalc_sources.sha256, physical)
        reference = await self.preparations.create_native_inputs(
            'powercalc-pss-composition-test', self.writer.baseline, composition=composition)
        baseline, restored = self.preparations.resolve_composition(reference)
        self.assertEqual(restored, composition)
        self.assertEqual(baseline.receipt_json, self.evidence.receipt_json)
        source_step = self.tx.Step('powercalc_source', 'registry', source_change.identity,
                                   source_change.before_revision, source_change.after_revision, source_ref)
        group_step = self.tx.Step('powercalc_group', 'registry', group_change.identity,
                                  group_change.before_revision, group_change.after_revision, group_ref)
        energy_step = self.tx.Step('powercalc_energy', 'energy', 'energy',
                                   self.prepared_energy.before.revision, self.prepared_energy.after_revision, energy_ref)
        yaml_step = self.tx.Step('powercalc_yaml', 'yaml', yaml_change.identity,
                                 yaml_change.before_revision, yaml_change.after_revision, ref)
        if metadata_change is not None:
            metadata_step = self.tx.Step('powercalc_group_metadata', 'helpers', metadata_change.identity,
                metadata_change.before_revision, metadata_change.after_revision, metadata_ref)
            scopes = ('registry', 'helpers', 'energy', 'yaml')
            steps = (energy_step, source_step, group_step, metadata_step, yaml_step)
        else:
            scopes = ('registry', 'energy', 'yaml')
            steps = (energy_step, source_step, group_step, yaml_step)
        plan = self.tx.Plan('powercalc-typed-root', scopes, scopes, steps,
                            self.evidence.powercalc_sources.sha256, scopes, reference)
        chain = await self.rr.RegistryReceiptChain.start(self.receipts, 'powercalc-typed-root',
                                                         plan, self.receipt, digests)
        reopened = self.rr.RegistryReceiptChain.reopen(self.receipts, 'powercalc-typed-root',
                                                       plan, self.receipt, digests)
        self.assertEqual(chain.root_id, reopened.root_id)
        self.assertFalse(reopened.writable)
        self.assertFalse(self.evidence.migration_ready)
        if getattr(self, '_periodic_cleanup_test', False):
            # A fast immutable proof must still reject a changed ORIGINAL.
            original_row = self.preparations.plans[plan.native_inputs[0]]
            original_codec = original_row['payload']['codec']
            energy_index = next(i for i, step in enumerate(plan.steps) if step.adapter == 'energy')
            original_row['payload']['codec'] = original_codec + 'A'
            try:
                with self.assertRaisesRegex(self.rr.RegistryReceiptError,
                                            'powercalc_original_preparation_changed'):
                    chain._original_change(energy_index)
            finally:
                original_row['payload']['codec'] = original_codec
            chain._original_change(energy_index)
            import sys
            monitor = sys.monitoring
            periodic_cls = self.module('powercalc_periodic_lease').PowercalcPeriodicLease
            lease = await self.module('powercalc_activation_lease').PowercalcActivationLease.acquire(
                self.f.hass, chain, self.evidence, self.original_runtime, guard=self.guard)
            phase = periodic = None
            try:
                prepared = await periodic_cls.prepare(lease, guard=self.guard)
                lifecycle = await self.module('powercalc_lifecycle').PowercalcLifecycle.start(lease)
                phase = await self.module('powercalc_phase_session').PowercalcPhaseSession.capture(lifecycle)
                from homeassistant.helpers.json import _orjson_bytes_default_encoder
                from collections.abc import Mapping
                import math
                native_snapshot = self.module('powercalc_native_snapshot').native_snapshot
                snapshots = {name: phase.scope.snapshot(entity)
                             for name, entity in phase.scope.old.items()}
                todo, enum_keys = [snapshots], 0
                while todo:
                    item = todo.pop()
                    if isinstance(item, Mapping):
                        enum_keys += sum(isinstance(key, str) and type(key) is not str for key in item)
                        todo.extend(item.values())
                    elif isinstance(item, (tuple, list)):
                        todo.extend(item)
                self.assertGreater(enum_keys, 0)
                encoded_snapshots = native_snapshot(snapshots)
                self.assertEqual(_orjson_bytes_default_encoder(encoded_snapshots),
                                 _orjson_bytes_default_encoder(snapshots))
                self.assertEqual(math.copysign(1, native_snapshot({'value': -0.0})['value']), -1)
                with self.assertRaisesRegex(self.tx.Refused, 'powercalc_native_snapshot_nonfinite'):
                    native_snapshot({'value': float('nan')})
                native_json = self.module('powercalc_source_binding')._json
                capture = {'entities': encoded_snapshots,
                    'producer_operations': json.loads(native_json(phase.scope.producer_owner.operations)),
                    'clock_steps': json.loads(native_json(phase.scope.clock_owner.steps)),
                    'timer_proofs': json.loads(native_json(phase.scope.clock_owner.timer_proofs))}
                self.prep.canonical(capture)
                print('POWERCalc_NATIVE_SNAPSHOT', {'enum_keys': enum_keys,
                    'clock_capture_steps': len(capture['clock_steps']),
                    'native_json_roundtrip': True, 'schema1_capture_bounded': True})
                slot = next(i for i in range(6) if monitor.get_tool(i) is None)
                def rejected_guard():
                    self.guard()
                    if monitor.get_tool(slot) == 'renamer.powercalc_periodic':
                        raise ValueError('synthetic_admission_rejected')
                with self.assertRaisesRegex(ValueError, 'synthetic_admission_rejected'):
                    periodic_cls.acquire(phase, prepared, guard=rejected_guard)
                self.assertIsNone(monitor.get_tool(slot))
                monitor.use_tool_id(slot, 'renamer.test.cleanup_readback')
                try:
                    self.assertIsNone(monitor.register_callback(slot, monitor.events.PY_START, None))
                finally:
                    monitor.free_tool_id(slot)
                periodic = periodic_cls.acquire(phase, prepared, guard=self.guard)
                self.assertTrue(periodic.handle.cancelled())
                phase.close()
                periodic.release()
                self.assertIsNone(monitor.get_tool(slot))
                monitor.use_tool_id(slot, 'renamer.test.cleanup_readback')
                try:
                    self.assertIsNone(monitor.register_callback(slot, monitor.events.PY_START, None))
                finally:
                    monitor.free_tool_id(slot)
                restored = periodic.tracker._timer_handle
                self.assertFalse(restored.cancelled())
                self.assertEqual(restored.when(), periodic.deadline)
                self.assertIs(restored._callback.__self__, periodic.tracker)
                print('POWERCalc_PERIODIC_CLEANUP', {'normal': True,
                    'admission_exception': True, 'native_deadline': True})
            finally:
                if phase is not None and not phase.closed:
                    phase.close()
                if periodic is not None and not periodic.closed:
                    periodic.release()
                lease.release()
            return
        if getattr(self, '_ordinary_driver_test', False):
            import importlib
            import sys
            from homeassistant.core import Context
            package = sys.modules[numeric.accepted.fixture.PACKAGE]
            app = self.module('__init__')
            had_runtime = hasattr(package, 'Runtime')
            old_runtime = getattr(package, 'Runtime', None)
            package.Runtime = app.Runtime
            journal = self.module('journal').native_journal(self.f.hass)
            await journal.initialize()
            runtime = app.Runtime('synthetic-powercalc', journal, self.preparations)
            key = self.module('const').DATA_RUNTIME
            self.assertNotIn(key, self.f.hass.data)
            self.f.hass.data[key] = runtime
            try:
                async with runtime.operation_lock:
                    driver = await self.module('powercalc_ordinary_driver').PowercalcOrdinaryDriver.bind(
                        self.f.hass, runtime, chain, self.evidence, self.original_runtime,
                        guard=self.guard, context=Context())
                    result = await driver.apply()
                self.assertEqual(result.status, 'applied', result.reason)
                self.assertEqual(result.phases, ('verified',) * len(plan.steps))
                periodic = driver.periodic
                self.assertTrue(periodic.closed)
                self.assertTrue(periodic.handle.cancelled())
                restored = periodic.tracker._timer_handle
                self.assertIsNot(restored, periodic.handle)
                self.assertFalse(restored.cancelled())
                self.assertEqual(restored.when(), periodic.deadline)
                self.assertIs(restored._callback.__self__, periodic.tracker)
                self.assertIs(periodic.tracker._run_job.target, periodic.action)
                due = periodic.deadline <= self.f.hass.loop.time()
                if due:
                    import sys
                    from pathlib import Path
                    monitor = sys.monitoring
                    free = [slot for slot in range(6) if monitor.get_tool(slot) is None]
                    self.assertTrue(free)
                    slot, tasks = free[0], []
                    code = type(periodic.store).persist_states.__code__
                    monitor.use_tool_id(slot, 'renamer.test.periodic_readback')
                    def started(native_code, offset):
                        frame = sys._getframe(1)
                        if frame.f_locals.get('self') is periodic.store:
                            tasks.append(asyncio.current_task())
                    try:
                        monitor.register_callback(slot, monitor.events.PY_START, started)
                        monitor.set_local_events(slot, code, monitor.events.PY_START)
                        async with asyncio.timeout(15):
                            while not tasks:
                                await asyncio.sleep(0)
                            await asyncio.gather(*tasks)
                        persisted = json.loads(await self.f.hass.async_add_executor_job(
                            lambda: Path(periodic.store.store.path).read_bytes()))
                        self.assertIn('sensor.synthetic_new_sum_energy', persisted['data'])
                    finally:
                        monitor.set_local_events(slot, code, 0)
                        monitor.free_tool_id(slot)
                print('POWERCalc_PERIODIC_RESTORED', {'same_tracker': True,
                    'same_deadline': True, 'natural_due_and_persisted': due})
                self.assertEqual(self.f.entity.async_get('light.public_static_target').id,
                                 self.original_source_uuid)
                self.assertEqual(self.f.entity.async_get('sensor.synthetic_new_sum_energy').id,
                                 group.registry_id)
                self.assertIsNone(self.f.entity.async_get(group.entity_id))
                reopened_driver = self.rr.RegistryReceiptChain.reopen(
                    self.receipts, 'powercalc-typed-root', plan, self.receipt, digests)
                self.assertFalse(reopened_driver.writable)
                self.assertEqual(reopened_driver.reconstruct_inputs().head, driver.result_head)
                self.assertNotIn(group.entity_id, self.f.hass.data['powercalc']['group_entities'])
                fresh_runtime = self.module('powercalc_runtime_observer').capture_powercalc_runtime(
                    self.f.hass, guard=self.guard)
                fresh = await self.module('native_powercalc_accepted_consumers').async_observe_accepted_powercalc_consumers(
                    self.f.hass, reopened_driver, self.evidence, fresh_runtime, guard=self.guard)
                self.assertEqual(fresh.source_position, 'proposed')
                self.assertTrue(all(row.source_verified for row in fresh.rows))
                print('POWERCalc_ORDINARY_FORWARD', {'applied': True, 'same_original': True,
                      'source_uuid': True, 'group_uuid': True, 'readonly_reopen': True,
                      'fresh_same_process_binding': True})
                if getattr(self, '_fresh_inverse_test', False):
                    from decimal import Decimal
                    # Deliver natural source samples after the completed forward;
                    # the inverse must take a fresh current total and alias CAS.
                    await self.sources[0].async_turn_on(brightness=64)
                    await asyncio.sleep(1.2)
                    await self.sources[0].async_turn_off()
                    await self.f.hass.async_block_till_done()
                    from homeassistant.helpers.entity_platform import async_get_platforms
                    active = {name: entity for platform in async_get_platforms(self.f.hass, 'powercalc')
                              for name, entity in platform.entities.items()}
                    group_before_inverse = active['sensor.synthetic_new_sum_energy']._native_value_exact
                    self.assertIs(type(group_before_inverse), Decimal)
                    self.assertTrue(group_before_inverse.is_finite())
                    async def refresh():
                        self.guard()
                    async with runtime.operation_lock:
                        # A foreign disk edit of the retained alias must refuse
                        # before admission, even where Python equality says it
                        # equals the original. Restore the exact synthetic file
                        # in place before the one real inverse attempt.
                        from pathlib import Path
                        pss_path = Path(self.sum_energy._prev_state_store.store.path)
                        original_raw = pss_path.read_bytes()
                        pss = json.loads(original_raw)
                        aliases = pss['data'][group.entity_id]
                        matches = [row for row in aliases.values() if row is not None
                                   and row['attributes'].get('assumed_state') is True]
                        self.assertEqual(len(matches), 1)
                        for mutated in (1,):
                            matches[0]['attributes']['assumed_state'] = mutated
                            changed = json.dumps(pss, sort_keys=True, separators=(',', ':')).encode()
                            try:
                                with pss_path.open('r+b') as file:
                                    file.write(changed)
                                    file.truncate()
                                with self.assertRaisesRegex(self.tx.Refused, 'retained_pss_disk_changed'):
                                    await self.module('powercalc_reverse_admission').PowercalcReverseAdmission.bind(
                                        self.f.hass, runtime, plan.plan_id, plan.fingerprint,
                                        guard=self.guard, refresh=refresh)
                                self.assertEqual(pss_path.read_bytes(), changed)
                                unchanged_record = await runtime.journal.load(plan.plan_id)
                                self.assertEqual(unchanged_record.status, 'applied')
                                self.assertEqual(unchanged_record.phases, ('verified',) * len(plan.steps))
                                self.assertIsNotNone(self.f.entity.async_get('sensor.synthetic_new_sum_energy'))
                            finally:
                                with pss_path.open('r+b') as file:
                                    file.write(original_raw)
                                    file.truncate()
                            self.assertEqual(pss_path.read_bytes(), original_raw)
                        admission = await self.module('powercalc_reverse_admission').PowercalcReverseAdmission.bind(
                            self.f.hass, runtime, plan.plan_id, plan.fingerprint,
                            guard=self.guard, refresh=refresh)
                        inverse = await self.module('powercalc_reverse_driver').PowercalcReverseDriver.bind(
                            admission, context=Context())
                        # Keep the original exception/chain if the native
                        # callback witness refuses; it otherwise records only
                        # a sanitized machine reason.
                        import traceback
                        owner_type = self.module('powercalc_retained_producer')._RetainedProducerOwner
                        original_removed = owner_type.removed
                        coordinator_type = self.module('powercalc_reverse_driver')._PowercalcInverseCoordinator
                        original_unknown = coordinator_type._unknown_after_error
                        def diagnostic_removed(owner, entity):
                            try:
                                return original_removed(owner, entity)
                            except BaseException as error:
                                traceback.print_exception(error)
                                raise
                        owner_type.removed = diagnostic_removed
                        async def diagnostic_unknown(coordinator, record, reason, error):
                            print('INVERSE_DIAGNOSTIC', reason, record.phases, flush=True)
                            traceback.print_exception(error)
                            return await original_unknown(coordinator, record, reason, error)
                        coordinator_type._unknown_after_error = diagnostic_unknown
                        try:
                            restored = await inverse.rollback()
                        finally:
                            owner_type.removed = original_removed
                            coordinator_type._unknown_after_error = original_unknown
                    self.assertEqual(restored.status, 'rolled_back', restored.reason)
                    self.assertEqual(restored.phases, ('undone',) * len(plan.steps))
                    self.assertEqual(self.f.entity.async_get(source_change.before['entity_id']).id,
                                     self.original_source_uuid)
                    self.assertEqual(self.f.entity.async_get(group.entity_id).id, group.registry_id)
                    self.assertIsNone(self.f.entity.async_get('sensor.synthetic_new_sum_energy'))
                    active = {name: entity for platform in async_get_platforms(self.f.hass, 'powercalc')
                              for name, entity in platform.entities.items()}
                    group_after_inverse = active[group.entity_id]._native_value_exact
                    self.assertIs(type(group_after_inverse), Decimal)
                    self.assertGreaterEqual(group_after_inverse, group_before_inverse)
                    print('POWERCalc_FRESH_INVERSE', {'rolled_back': True, 'same_original': True,
                        'source_uuid': True, 'group_uuid': True, 'no_total_decrease': True})
            finally:
                if self.f.hass.data.get(key) is runtime:
                    del self.f.hass.data[key]
                if had_runtime:
                    package.Runtime = old_runtime
                else:
                    del package.Runtime
            return
        lease_type = self.module('powercalc_activation_lease').PowercalcActivationLease
        lifecycle_type = self.module('powercalc_lifecycle').PowercalcLifecycle
        lease = await lease_type.acquire(self.f.hass, chain, self.evidence, self.original_runtime,
                                         guard=self.guard)
        phase = None
        try:
            lifecycle = await lifecycle_type.start(lease)
            phase_module = self.module('powercalc_phase_session')
            phase = await phase_module.PowercalcPhaseSession.capture(lifecycle)
            unload = await phase.unload_group(group.entity_id)
            self.assertIsNotNone(self.receipts.read(unload[0]))
            self.assertIsNotNone(self.receipts.read((await phase.quiesce_sources())[0]))
            # A real source report after the native unload must reach the
            # retained native producer while the same phase session is live.
            from decimal import Decimal
            await self.sources[0].async_turn_on(brightness=64)
            await phase.scope.settle_descendants()
            phase.check()
            self.assertTrue(any(row.input_state.entity_id == self.sources[0].entity_id
                                and type(row.calculated_value) is Decimal
                                for row in phase.scope.producer_owner.results))
            handoff = await self.module('powercalc_pss_handoff').async_handoff_previous_states(lifecycle)
            self.assertIsNotNone(self.receipts.read(handoff[0]))
            restore_ref = await self.module('powercalc_restore_handoff').async_handoff_group_restore(
                lifecycle, group.entity_id)
            restore_receipt = self.receipts.read(restore_ref[0])['payload']
            from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
            from homeassistant.components.sensor import SensorExtraStoredData
            restored_data = self.f.hass.data[DATA_RESTORE_STATE].last_states['sensor.synthetic_new_sum_energy']
            native_extra = SensorExtraStoredData.from_dict(restored_data.extra_data.as_dict())
            self.assertEqual(str(native_extra.native_value), restore_receipt['full_total'])
            from homeassistant.components.energy.data import async_get_manager
            manager = await async_get_manager(self.f.hass)
            native_energy = self.module('energy_native').NativeEnergy(self.f.hass, manager)
            journal = self.module('journal').native_journal(self.f.hass)
            await journal.initialize()
            record = self.tx.Record('powercalc-typed-root', plan, status='applying',
                                    phases=('intent', 'pending', 'pending', 'pending'))
            await journal.create(record)
            async def forward_check(_plan):
                return await native_energy.verify(self.prepared_energy.after_revision)
            async def reverse_check(_plan):
                return await native_energy.verify(self.prepared_energy.before.revision)
            adapter = self.module('powercalc_energy_transaction').ReceiptedPowercalcEnergyAdapter(
                native_energy, lifecycle, journal.load, forward_check, reverse_check)
            adapter.bind_phase_context(self.tx.PhaseContext(record, 0))
            await adapter.apply(energy_step, self.guard)
            energy_readback = await native_energy.read()
            self.assertEqual(energy_readback.value['device_consumption'][0]['stat_consumption'],
                             'sensor.synthetic_new_sum_energy')
            self.assertEqual(energy_readback.revision, self.prepared_energy.after_revision)
            # The task-held live unload remains valid across the accepted
            # Energy prefix; a detached persisted receipt grants no writer.
            accepted_unloads = self.module('powercalc_pss_handoff')._unloads(lifecycle)
            self.assertEqual(accepted_unloads[group.entity_id][0], unload[0])
            data = json.loads(__import__('pathlib').Path(store.store.path).read_bytes())['data']
            self.assertEqual(data[group.entity_id], data['sensor.synthetic_new_sum_energy'])
            for name, state in store.states[group.entity_id].items():
                if state is not None:
                    self.assertIsNot(state, store.states['sensor.synthetic_new_sum_energy'][name])
            self.assertEqual(self.f.entity.async_get(group.entity_id).id, group.registry_id)
            self.assertIsNone(self.f.entity.async_get('sensor.synthetic_new_sum_energy'))
            reopened_after = self.rr.RegistryReceiptChain.reopen(self.receipts, 'powercalc-typed-root',
                                                             plan, self.receipt, digests)
            self.assertFalse(lifecycle_type.reopen(reopened_after).writable)
            # Continue the same journal through the actual physical source
            # registry rename while the native producer is retained.
            from dataclasses import replace
            source_record = replace(record, phases=('verified', 'intent', 'pending', 'pending'))
            await journal.save(source_record)
            async def observe_native():
                return await self.module('native_input_observation').async_observe_native_inputs(
                    self.f.hass, self.receipt, guard=phase.check, input_state=lease.state)
            writer = registry.NativeRegistryBridge(registry.NativeRegistryIO(self.f.hass), recorder,
                                                    native_images=chain.accepted_documents)
            resolve = lambda key: self.prep.decode_change(self.preparations.resolve(key, digests[key]))
            registry_adapter = self.module('powercalc_registry_adapter').PowercalcRegistryAdapter(
                phase, writer, resolve, observe_native, journal.load, self.guard)
            registry_adapter.bind_phase_context(self.tx.PhaseContext(source_record, 1))
            if (getattr(self, '_revoke_during_source', False)
                    or getattr(self, '_cancel_during_source', False)
                    or getattr(self, '_fail_after_source_effect', False)):
                from homeassistant.core import callback
                import traceback
                cancelling = getattr(self, '_cancel_during_source', False)
                failing = getattr(self, '_fail_after_source_effect', False)
                owner_task = asyncio.current_task()
                triggered = [False]
                trigger_phases = []
                producer_owner = phase.scope.producer_owner
                original_poison = producer_owner._poison
                self.assertNotIn('_poison', vars(producer_owner))
                def capture_poison(error):
                    # Observe the real exception/chain, then run the unchanged
                    # product handler; never alter a native callback or oracle.
                    print('SOURCE_CALLBACK_TRACE_BEGIN', flush=True)
                    traceback.print_exception(error)
                    print('SOURCE_CALLBACK_TRACE_END', flush=True)
                    original_poison(error)
                producer_owner._poison = capture_poison
                @callback
                def revoke_on_actual_registry_event(event):
                    if (event.data.get('action') == 'update'
                            and event.data.get('entity_id') == 'light.public_static_target'):
                        if failing:
                            return
                        triggered[0] = True
                        transition = phase.scope.source_transition
                        trigger_phases.extend(item.phase for item in transition.witness.emissions)
                        if cancelling:
                            owner_task.cancel()
                        else:
                            revoke[0] = True
                remove_revoke = self.f.hass.bus.async_listen('entity_registry_updated', revoke_on_actual_registry_event)
                monitor = monitor_id = monitor_code = None
                if failing:
                    import sys
                    monitor = sys.monitoring
                    free = [key for key in range(6) if monitor.get_tool(key) is None]
                    self.assertTrue(free)
                    monitor_id = free[0]
                    monitor.use_tool_id(monitor_id, 'renamer.synthetic_post_effect_failure')
                    monitor_code = registry.NativeRegistryIO.mutate.__code__
                    def fail_after_native_return(code, offset, returned):
                        if (type(returned) is registry.NativeRegistryMutation
                                and returned.identity == source_change.identity):
                            transition = phase.scope.source_transition
                            triggered[0] = True
                            trigger_phases.extend(item.phase for item in transition.witness.emissions)
                            monitor.set_local_events(monitor_id, monitor_code, 0)
                            raise ValueError('synthetic_post_effect_unreturned')
                    monitor.register_callback(monitor_id, monitor.events.PY_RETURN, fail_after_native_return)
                    monitor.set_local_events(monitor_id, monitor_code, monitor.events.PY_RETURN)
                try:
                    with self.assertRaises(asyncio.CancelledError if cancelling else Exception) as caught:
                        await registry_adapter.apply(source_step, self.guard)
                    transition = phase.scope.source_transition
                    print('SOURCE_AUTH_DIAGNOSTIC', {'exception_type': type(caught.exception).__name__,
                          'exception': str(caught.exception), 'first_failure': phase.scope.first_failure,
                          'scope_error': phase.scope.error, 'producer_error': phase.scope.producer_owner.poison,
                          'witness_error': transition.witness.error,
                          'cleanup_owner': lease._cleanup_owner is transition,
                          'witness_finished': transition.witness.finished,
                          'remove_raw': len(transition.remove_raw),
                          'issued': sorted(transition.issued),
                          'producer_phases': sorted(transition.owner.phase.items()),
                          'native_phases': [(item.phase, item.dispatched)
                                            for item in transition.witness.emissions],
                          'rebound': transition.rebound, 'remove_seen': len(transition.remove_seen)})
                    self.assertTrue(triggered[0])
                    if cancelling:
                        # This fixture's earlier native listener completes the
                        # eager source re-add before this test listener runs.
                        # It does not exercise cancellation before ADD.
                        self.assertEqual(trigger_phases, ['registry', 'remove', 'add'])
                        self.assertFalse(transition.witness.finished)
                        self.assertEqual(transition.witness.error, 'finish_uncertain')
                    if failing:
                        self.assertEqual(str(caught.exception), 'synthetic_post_effect_unreturned')
                        self.assertTrue(transition.witness.failed_powercalc)
                        self.assertIsNone(transition.witness.mutation)
                        self.assertFalse(transition.live)
                        self.assertFalse(transition.issued)
                    self.assertIsNone(lease._cleanup_owner)
                    self.assertEqual(transition.remove_seen, set(transition.affected))
                    self.assertTrue(any(row.input_state.entity_id == 'light.public_static_target'
                                        for row in phase.scope.producer_owner.results))
                    self.assertIsNone(phase.scope.error)
                    self.assertIsNone(phase.scope.producer_owner.poison)
                    subtract = phase.scope.clock_owner.subtract
                    target_results = [row for row in subtract.dynamic_results
                                      if row.input_state.entity_id == 'light.public_static_target']
                    self.assertTrue(target_results)
                    self.assertTrue(all(any(op[0] == 'calculation_complete'
                                            and op[1] == row.producer.entity_id
                                            and op[2] == row.emission_sequence
                                            for op in phase.scope.producer_owner.operations)
                                        for row in target_results))
                    self.assertIsNotNone(chain.active)
                    self.assertFalse(transition._consumed)
                    print('SOURCE_POST_EFFECT_FAILURE_DELIVERY' if failing else
                          'SOURCE_CANCELLED_DELIVERY' if cancelling else 'SOURCE_AUTH_REVOKED_DELIVERY',
                          {'actual_event': True,
                          'trigger_phases': trigger_phases, 'retained_sample': True,
                          'intent_unknown': True, 'receipt': False})
                finally:
                    if monitor is not None:
                        monitor.set_local_events(monitor_id, monitor_code, 0)
                        monitor.register_callback(monitor_id, monitor.events.PY_RETURN, None)
                        monitor.free_tool_id(monitor_id)
                    remove_revoke()
                    revoke[0] = False
                    if cancelling and owner_task.cancelling():
                        owner_task.uncancel()
                    del producer_owner._poison
                return
            try:
                await registry_adapter.apply(source_step, self.guard)
            except Exception:
                transition = getattr(phase.scope, 'source_transition', None)
                if transition is not None:
                    print('SOURCE_HANDOFF_DIAGNOSTIC', {
                        'first_failure': phase.scope.first_failure,
                        'native_phases': [(item.phase, item.dispatched)
                                          for item in transition.witness.emissions],
                        'rebound': transition.rebound,
                        'remove_seen': len(transition.remove_seen),
                        'pending_calculations': len(phase.scope.producer_owner.emitted)})
                raise
            target_source = self.f.entity.async_get('light.public_static_target')
            self.assertIsNotNone(target_source)
            self.assertEqual(target_source.id, self.original_source_uuid)
            self.assertIsNone(self.f.entity.async_get(source_change.before['entity_id']))
            self.assertTrue(phase.scope.source_transition._consumed)
            self.assertTrue(any(row.input_state.entity_id == 'light.public_static_target'
                                for row in phase.scope.producer_owner.results))
            if getattr(self, '_native_stream_shape_test', False):
                native_json = self.module('powercalc_source_binding')._json
                producer_module = self.module('powercalc_retained_producer')
                streams = {
                    'producer_operations': json.loads(native_json(phase.scope.producer_owner.operations)),
                    'producer_results': json.loads(native_json([
                        producer_module._result_row(row) for row in phase.scope.producer_owner.results])),
                    'clock_steps': json.loads(native_json(phase.scope.clock_owner.steps)),
                    'timer_proofs': json.loads(native_json(phase.scope.clock_owner.timer_proofs)),
                }
                self.assertTrue(streams['producer_results'])
                self.assertTrue(streams['clock_steps'])
                self.prep.canonical(streams)
                def nodes(value):
                    if isinstance(value, dict):
                        return 1 + sum(nodes(v) for v in value.values())
                    if isinstance(value, list):
                        return 1 + sum(nodes(v) for v in value)
                    return 1
                print('POWERCalc_NATIVE_STREAM_SHAPE', {
                    'lengths': {key: len(value) for key, value in streams.items()},
                    'nodes': nodes(streams), 'canonical_schema1': True})
            reopened_source = self.rr.RegistryReceiptChain.reopen(
                self.receipts, 'powercalc-typed-root', plan, self.receipt, digests)
            self.assertFalse(reopened_source.writable)
            self.assertEqual(reopened_source.reconstruct_inputs().head, lease.state.head)
            print('NATIVE_PSS_HANDOFF_PREREQUISITE', {'same_original': True, 'old_alias_retained': True,
                  'new_alias_persisted': True, 'native_restore_exact': True,
                  'energy_reference_actual': True, 'native_source_rename': True, 'ordinary_applied': False})
        finally:
            try:
                if phase is not None:
                    # This component test intentionally stops before name/setup;
                    # its direct-effect session must report incomplete.
                    with self.assertRaises(phase_module.PowercalcPhaseError):
                        phase.close()
            finally:
                lease.release()

    async def test_source_auth_revocation_drains_actual_native_delivery(self):
        self._revoke_during_source = True
        await self.test_original_capture_pins_actual_pss_and_registry_graph()

    async def test_source_cancellation_after_actual_native_delivery(self):
        self._cancel_during_source = True
        await self.test_original_capture_pins_actual_pss_and_registry_graph()

    async def test_source_failure_after_actual_native_effect_before_stamp(self):
        self._fail_after_source_effect = True
        await self.test_original_capture_pins_actual_pss_and_registry_graph()

    async def test_native_stream_schema1_after_real_source_rename(self):
        self._native_stream_shape_test = True
        await self.test_original_capture_pins_actual_pss_and_registry_graph()

    async def test_periodic_timer_cleanup_native(self):
        self._periodic_cleanup_test = True
        await self.test_original_capture_pins_actual_pss_and_registry_graph()

    async def test_ordinary_forward_same_original_native_setup(self):
        self._ordinary_driver_test = True
        await self.test_original_capture_pins_actual_pss_and_registry_graph()

    async def test_ordinary_forward_and_fresh_inverse(self):
        self._ordinary_driver_test = True
        self._fresh_inverse_test = True
        await self.test_original_capture_pins_actual_pss_and_registry_graph()

    async def test_public_single_child_group_refused_before_effect(self):
        self._single_child_refusal = True
        await self.test_public_ordinary_review_apply_and_fresh_inverse()

    async def test_public_group_without_subtract_dynamic_during_unload(self):
        self._public_dynamic_during_unload = True
        await self.test_public_ordinary_review_apply_and_fresh_inverse()

    async def test_public_dynamic_child_during_group_unload(self):
        self._public_dynamic_during_unload = True
        await self.test_public_ordinary_review_apply_and_fresh_inverse()

    async def test_public_ordinary_review_apply_and_fresh_inverse(self):
        """Installed Runtime, genuine vendor and independent group-delta oracle."""
        import asyncio
        import socket
        import sys
        from decimal import Decimal
        from aiohttp import ClientSession
        from homeassistant import auth
        from homeassistant.auth.const import GROUP_ID_ADMIN
        from homeassistant.core import callback
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component
        from homeassistant.components.http.config import async_get_and_load_store
        from homeassistant.components.config import automation, script, scene

        hass = self.f.hass
        if getattr(hass, 'auth', None) is None:
            hass.auth = await auth.auth_manager_from_config(hass, [], [])
        if getattr(hass, 'http', None) is None:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            self.assertTrue(await async_setup_component(hass, 'http', {'http': {
                'server_host': '127.0.0.1', 'server_port': port}}))
        http_config = await async_get_and_load_store(hass)
        if http_config.pending is not None:
            await http_config.async_promote_pending()
        self.assertTrue(await async_setup_component(hass, 'websocket_api', {}))
        for editor in (automation, script, scene):
            self.assertTrue(editor.async_setup(hass))
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(1.2)
        await self.sources[0].async_turn_off()
        await hass.async_block_till_done()
        await self.sum_energy._prev_state_store.persist_states()
        from homeassistant.helpers import issue_registry
        issues = issue_registry.async_get(hass)
        await issues._store.async_save(issues._data_to_save())
        from homeassistant.components.energy.data import async_get_manager
        energy_manager = await async_get_manager(hass)
        await energy_manager._store.async_save(energy_manager.data)

        old_source, old_group = self.sources[0].entity_id, self.sum_energy.entity_id
        new_source, new_group = 'light.public_static_target', 'sensor.synthetic_new_sum_energy'
        source_uuid, group_uuid = self.sources[0].registry_entry.id, self.sum_energy.registry_entry.id
        members = tuple(sorted(self.sum_energy._entities))
        baseline = self.sum_energy._native_value_exact
        self.assertIs(type(baseline), Decimal)
        prior = {name: self.sum_energy._prev_state_store.get_entity_state(old_group, name)
                 for name in members}
        expected = baseline
        changes = []
        @callback
        def collect(event):
            state = event.data.get('new_state')
            if event.data.get('entity_id') in members and state is not None:
                changes.append(state)
        remover = hass.bus.async_listen('state_changed', collect)
        self.addCleanup(remover)
        package = sys.modules[numeric.accepted.fixture.PACKAGE]
        app = self.module('__init__')
        had_runtime, old_runtime = hasattr(package, 'Runtime'), getattr(package, 'Runtime', None)
        package.Runtime = app.Runtime
        journal = self.module('journal').native_journal(hass)
        preparations = self.module('prepared').native_prepared_store(hass)
        await journal.initialize()
        await preparations.initialize()
        runtime = app.Runtime('synthetic-public-powercalc', journal, preparations)
        key = self.module('const').DATA_RUNTIME
        self.assertNotIn(key, hass.data)
        hass.data[key] = runtime
        def restore_runtime():
            if hass.data.get(key) is runtime:
                del hass.data[key]
            if had_runtime:
                package.Runtime = old_runtime
            elif hasattr(package, 'Runtime'):
                del package.Runtime
        self.addCleanup(restore_runtime)
        self.module('websocket_api').async_register(hass)
        # This fixture configures HTTP after HA startup, so start the already
        # bound native server explicitly before opening the real WS transport.
        await hass.http.start()
        self.addAsyncCleanup(hass.http.stop)
        admin = await hass.auth.async_create_user('Synthetic Powercalc administrator', group_ids=[GROUP_ID_ADMIN])
        token = await hass.auth.async_create_refresh_token(admin, client_id='https://renamer.invalid/powercalc-qa')
        self.addCleanup(hass.auth.async_remove_refresh_token, token)
        access = hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
        client = ClientSession()
        self.addAsyncCleanup(client.close)
        ws = await client.ws_connect('http://127.0.0.1:' + str(hass.http.server_port) + '/api/websocket')
        self.addAsyncCleanup(ws.close)
        self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_required')
        await ws.send_json({'type': 'auth', 'access_token': access})
        self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_ok')
        async def request(ident, command, **payload):
            await ws.send_json({'id': ident, 'type': 'ha_entity_renamer/' + command, **payload})
            return await ws.receive_json(timeout=1200)
        async def natural_w_oracle(brightness):
            # LEFT integration from the actual previous W State and actual
            # native integration anchor. No product area/counter is used as
            # the expected delta; no timer or State is fabricated.
            active = {name: entity for platform in async_get_platforms(hass, 'powercalc')
                      for name, entity in platform.entities.items()}
            engine = active[self.static_energy.entity_id]
            source = hass.states.get(self.static_power.entity_id)
            self.assertIsNotNone(source)
            watts = Decimal(source.state)
            full = engine._state
            self.assertIs(type(full), Decimal)
            trigger = engine._last_integration_trigger.name
            anchor = engine._last_integration_time if trigger == 'TimeElapsed' else source.last_reported
            timer = getattr(engine._max_sub_interval_exceeded_callback, '__self__', None)
            self.assertIs(type(timer), asyncio.TimerHandle)
            self.assertFalse(timer.cancelled())
            deadline = timer.when()
            observed = []
            @callback
            def watch_power(event):
                if event.data.get('entity_id') == self.static_power.entity_id:
                    state = event.data.get('new_state')
                    if state is not None:
                        observed.append((state, hass.loop.time()))
            stop = hass.bus.async_listen('state_changed', watch_power)
            try:
                await self.sources[0].async_turn_on(brightness=brightness)
                async with asyncio.timeout(2):
                    while not observed:
                        await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertEqual(len(observed), 1)
                state, seen_loop = observed[0]
                self.assertLess(seen_loop, deadline, 'timer must not precede this independent W sample')
                self.assertLess(hass.loop.time(), deadline, 'timer must not contaminate W readback')
                expected_kwh = full + Decimal((state.last_updated - anchor).total_seconds()) * watts / Decimal(3600000)
                self.assertEqual(engine._state, expected_kwh)
                self.assertEqual(engine._last_valid_state, expected_kwh)
                self.assertNotEqual(Decimal(state.state), watts)
            finally:
                stop()
        import traceback
        coordinator_type = self.module('transaction').Coordinator
        original_unknown = coordinator_type._unknown
        inverse_type = self.module('powercalc_reverse_driver')._PowercalcInverseCoordinator
        original_inverse_unknown = inverse_type._unknown_after_error
        recovery_module = self.module('powercalc_public_recovery')
        original_recovery = recovery_module.inspect_or_restore
        server_type = self.module('powercalc_public_server').PowercalcPublicServer
        original_server_prepare = server_type.__dict__['prepare']
        original_server_bind = server_type.__dict__['bind_prepared']
        async def observed_prepare(cls, *args, **kwargs):
            try:
                return await original_server_prepare.__func__(cls, *args, **kwargs)
            except BaseException as error:
                traceback.print_exception(error)
                raise
        async def observed_bind(cls, *args, **kwargs):
            try:
                return await original_server_bind.__func__(cls, *args, **kwargs)
            except BaseException as error:
                traceback.print_exception(error)
                raise
        async def observed_unknown(coordinator, record, reason):
            print('PUBLIC_FORWARD_UNKNOWN', reason, record.phases, flush=True)
            error = sys.exception()
            if error is not None:
                traceback.print_exception(error)
            return await original_unknown(coordinator, record, reason)
        async def observed_inverse_unknown(coordinator, record, reason, error):
            print('PUBLIC_INVERSE_UNKNOWN', reason, record.phases, flush=True)
            traceback.print_exception(error)
            return await original_inverse_unknown(coordinator, record, reason, error)
        async def observed_recovery(*args, **kwargs):
            try:
                return await original_recovery(*args, **kwargs)
            except BaseException as error:
                traceback.print_exception(error)
                raise
        coordinator_type._unknown = observed_unknown
        inverse_type._unknown_after_error = observed_inverse_unknown
        recovery_module.inspect_or_restore = observed_recovery
        server_type.prepare = classmethod(observed_prepare)
        server_type.bind_prepared = classmethod(observed_bind)
        async def refresh():
            self.guard()
        try:
            unchanged = None
            if getattr(self, '_single_child_refusal', False):
                paths = [self.f.root / '.storage' / key for key in
                         ('core.entity_registry', 'core.device_registry', 'core.config_entries', 'energy')]
                paths.append(self.f.root / 'configuration.yaml')
                unchanged = [(path, path.read_bytes()) for path in paths]
            response = await request(1, 'prepare', entities=[
                {'entity_id': old_source, 'new_entity_id': new_source},
                {'entity_id': old_group, 'new_entity_id': new_group}])
            if unchanged is not None:
                self.assertFalse(response.get('success'), response)
                self.assertEqual(response['error']['code'], 'unsupported_powercalc_graph')
                self.assertEqual([(path, path.read_bytes()) for path, _ in unchanged], unchanged)
                self.assertFalse(await journal.all())
                print('PUBLIC_SINGLE_CHILD_REFUSED_BEFORE_EFFECT', flush=True)
                return
            self.assertTrue(response.get('success'), response)
            review = response['result']
            self.assertEqual(review['entity_count'], 2)
            self.assertIn('powercalc', review['dependencies']['domains'])
            self.assertIn('energy', review['dependencies']['domains'])
            operation, fingerprint = review['operation_id'], review['fingerprint']
            retained = runtime.reviews[operation]
            original, _ = runtime.preparations.resolve_composition(retained.plan.native_inputs)
            source_document = json.loads(original.files[0].raw)
            source_row = next(row for row in source_document['data']['entities']
                              if row['entity_id'] == old_source)
            builder = self.module('powercalc_public_candidate')
            for other_owned_platform in ('template', 'automation', 'script'):
                source_row['platform'] = other_owned_platform
                altered = ((original.files[0].key, json.dumps(source_document).encode()),) + tuple(
                    (row.key, row.raw) for row in original.files[1:])
                with self.assertRaisesRegex(self.module('transaction').Refused, 'one_owned_group_required'):
                    builder.build_candidate(altered, {old_source: new_source, old_group: new_group},
                                            original.receipt.version)
            denied = await request(2, 'apply', operation_id=operation, fingerprint='0' * 64)
            self.assertFalse(denied.get('success'), denied)
            dynamic_task = None
            dynamic_events = []
            stop_dynamic = None
            if getattr(self, '_public_dynamic_during_unload', False):
                # Trigger only from the real group's native state removal. The
                # source service and resulting producer events are unmodified.
                @callback
                def actual_group_retirement(event):
                    nonlocal dynamic_task
                    data = event.data
                    if data.get('entity_id') == self.dynamic_power.entity_id and data.get('new_state'):
                        dynamic_events.append(data['new_state'])
                    if (dynamic_task is None and data.get('entity_id') == old_group
                            and data.get('old_state') is not None and data.get('new_state') is None):
                        dynamic_task = hass.async_create_task(
                            self.sources[1].async_turn_on(brightness=255),
                            'test.public-dynamic-during-group-unload')
                stop_dynamic = hass.bus.async_listen('state_changed', actual_group_retirement)
            try:
                response = await request(3, 'apply', operation_id=operation, fingerprint=fingerprint)
            finally:
                if stop_dynamic is not None:
                    stop_dynamic()
            if stop_dynamic is not None:
                self.assertIsNotNone(dynamic_task, 'real group retirement was not observed')
                await asyncio.shield(dynamic_task)
                self.assertTrue(dynamic_events, 'real dynamic producer wrote no State')
                print('PUBLIC_DYNAMIC_DURING_UNLOAD', len(dynamic_events), flush=True)
            self.assertTrue(response.get('success'), response)
            applied = response['result']
            self.assertEqual(applied['state'], 'applied', applied)
            self.assertEqual(self.f.entity.async_get(new_source).id, source_uuid)
            self.assertEqual(self.f.entity.async_get(new_group).id, group_uuid)
            self.assertEqual(energy_manager.data['device_consumption'][0]['stat_consumption'], new_group)
            energy = self.module('energy_native').NativeEnergy(hass, energy_manager)
            self.assertEqual((await energy.read()).value['device_consumption'][0]['stat_consumption'], new_group)
            replay = await request(4, 'apply', operation_id=operation, fingerprint=fingerprint)
            self.assertFalse(replay.get('success'), replay)
            await natural_w_oracle(64)
            await asyncio.sleep(1.2)
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()
            response = await request(5, 'recovery/inspect', operation_id=operation, fingerprint=fingerprint)
            self.assertTrue(response.get('success'), response)
            inspected = response['result']
            self.assertEqual(inspected['state'], 'applied', inspected)
            self.assertEqual(inspected['frozen_review']['changes'], review['changes'])
            self.assertEqual(inspected['frozen_review']['dependencies'], review['dependencies'])
            response = await request(6, 'recovery/restore', operation_id=operation, fingerprint=fingerprint)
            self.assertTrue(response.get('success'), response)
            restored = response['result']
            self.assertEqual(restored['state'], 'rolled_back', restored)
            self.assertEqual(self.f.entity.async_get(old_source).id, source_uuid)
            self.assertEqual(self.f.entity.async_get(old_group).id, group_uuid)
            self.assertEqual(energy_manager.data['device_consumption'][0]['stat_consumption'], old_group)
            self.assertEqual((await energy.read()).value['device_consumption'][0]['stat_consumption'], old_group)
            await natural_w_oracle(128)
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()
            response = await request(7, 'recovery/inspect', operation_id=operation, fingerprint=fingerprint)
            self.assertTrue(response.get('success'), response)
            self.assertEqual(response['result']['state'], 'rolled_back', response)
            self.assertEqual(response['result']['reason'], 'restored_verified', response)
            self.assertEqual(response['result']['frozen_review']['changes'], review['changes'])
            self.assertEqual(response['result']['frozen_review']['dependencies'], review['dependencies'])
            current = {name: entity for platform in async_get_platforms(hass, 'powercalc')
                       for name, entity in platform.entities.items()}
            for state in changes:
                try:
                    value = Decimal(state.state)
                except Exception:
                    continue
                old = prior.get(state.entity_id)
                if old is not None:
                    try:
                        delta = value - Decimal(old.state)
                    except Exception:
                        delta = Decimal(0)
                    if delta > 0:
                        expected += delta
                prior[state.entity_id] = state
            self.assertTrue(changes, 'actual native energy samples required')
            self.assertEqual(current[old_group]._native_value_exact, expected)
            print('PUBLIC_POWERCalc_DECIMAL', {'review_apply_inverse': True, 'same_original': True,
                  'source_uuid': True, 'group_uuid': True, 'samples': len(changes),
                  'exact_group_delta': True, 'independent_w_kwh_forward_and_inverse': True,
                  'energy_reference_restored': True})
        finally:
            coordinator_type._unknown = original_unknown
            inverse_type._unknown_after_error = original_inverse_unknown
            recovery_module.inspect_or_restore = original_recovery
            server_type.prepare = original_server_prepare
            server_type.bind_prepared = original_server_bind
            await ws.close()
            await client.close()
            restore_runtime()


@unittest.skipUnless(numeric.accepted.fixture.HAS_HA, 'actual supported HA required')
class PowercalcGroupColdPrecisionNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = numeric.PowercalcNumericContinuityNativeTests.namespace_source_bytes
    _setup_real_vendor = numeric.PowercalcNumericContinuityNativeTests._setup_real_vendor
    _flush_documents = numeric.PowercalcNumericContinuityNativeTests._flush_documents
    guard = numeric.PowercalcNumericContinuityNativeTests.guard
    async def asyncSetUp(self):
        self.group_fixture_options = {'energy_sensor_precision': 2}
        await numeric.PowercalcNumericContinuityNativeTests.asyncSetUp(self)

    async def test_native_restore_dump_exposes_group_precision_boundary(self):
        from decimal import Decimal
        import asyncio
        from pathlib import Path
        from homeassistant.components.sensor import SensorExtraStoredData
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        # Configuration is fixed before native setup/ORIGINAL; all samples are
        # ordinary public Light and native integration/group updates.
        group = self.sum_energy
        self.assertEqual(group._rounding_digits, 2)
        self.assertEqual(self.static_energy._sensor_config['energy_sensor_precision'], 6)
        await self.sources[0].async_turn_on(brightness=255)
        async with asyncio.timeout(12):
            while not (type(group._native_value_exact) is Decimal
                       and group._native_value_exact > 0
                       and group._native_value_exact != round(group._native_value_exact, 2)):
                await asyncio.sleep(0.05)
        await self.f.hass.async_block_till_done()
        precise = group._native_value_exact
        extra = group.extra_restore_state_data
        self.assertIs(type(extra), SensorExtraStoredData)
        self.assertEqual(extra.native_value, round(precise, 2))
        self.assertNotEqual(extra.native_value, precise)
        restore = self.f.hass.data[DATA_RESTORE_STATE]
        await restore.async_dump_states()
        raw = await self.f.hass.async_add_executor_job(lambda: Path(restore.store.path).read_bytes())
        rows = [row for row in json.loads(raw)['data']
                if row['state']['entity_id'] == group.entity_id]
        self.assertEqual(len(rows), 1)
        restored = SensorExtraStoredData.from_dict(rows[0]['extra_data'])
        self.assertIsNotNone(restored)
        self.assertEqual(restored.native_value, round(precise, 2))
        self.assertNotEqual(restored.native_value, precise)
        print('NATIVE_GROUP_RESTORE_PRECISION', {'exact': str(precise),
              'stored': str(restored.native_value), 'difference': str(precise - restored.native_value)})
