"""Native group own-ID effect and readonly receipt regression."""
import json
from test_powercalc_previous_state_native import PowercalcPreviousStateNativeTests


class PowercalcGroupOwnIDNativeTests(PowercalcPreviousStateNativeTests):
    async def test_group_own_id_native_and_readonly(self):
        await self._group_scenario('success')

    async def test_group_own_id_rejects_placeholder_drift(self):
        await self._group_scenario('drift')

    async def test_group_own_id_rejects_missing_restore(self):
        await self._group_scenario('missing_restore')

    async def _group_scenario(self, scenario):
        import asyncio
        from homeassistant.core import State
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(1.2)
        await self.sources[0].async_turn_off()
        await self.f.hass.async_block_till_done()
        store = self.sum_energy._prev_state_store
        await store.persist_states()
        self.candidate_group_target = 'sensor.synthetic_new_sum_energy'
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
        group_ref = 'powercalc-composition-group'
        group_digest = await self.preparations.create(group_ref, self.prep.encode_change(group_change))
        energy_ref = 'powercalc-composition-energy'
        energy_digest = await self.preparations.create(energy_ref, self.prep.encode_change(self.prepared_energy))
        digests = {source_ref: source_digest, ref: digest, group_ref: group_digest, energy_ref: energy_digest}
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
        plan = self.tx.Plan('powercalc-typed-root', ('registry', 'energy', 'yaml'), ('registry', 'energy', 'yaml'),
                            (energy_step, source_step, group_step, yaml_step), self.evidence.powercalc_sources.sha256,
                            ('registry', 'energy', 'yaml'), reference)
        chain = await self.rr.RegistryReceiptChain.start(self.receipts, 'powercalc-typed-root',
                                                         plan, self.receipt, digests)
        reopened = self.rr.RegistryReceiptChain.reopen(self.receipts, 'powercalc-typed-root',
                                                       plan, self.receipt, digests)
        self.assertEqual(chain.root_id, reopened.root_id)
        self.assertFalse(reopened.writable)
        self.assertFalse(self.evidence.migration_ready)
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
            if scenario != 'missing_restore':
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
            reopened_source = self.rr.RegistryReceiptChain.reopen(
                self.receipts, 'powercalc-typed-root', plan, self.receipt, digests)
            self.assertFalse(reopened_source.writable)
            self.assertEqual(reopened_source.reconstruct_inputs().head, lease.state.head)
            # Continue on the same ORIGINAL, live lease and accepted source prefix.
            group_record = replace(record, phases=('verified', 'verified', 'intent', 'pending'))
            await journal.save(group_record)
            registry_adapter.bind_phase_context(self.tx.PhaseContext(group_record, 2))
            before_state = lifecycle._unload_states[group.entity_id]
            self.assertIs(self.f.hass.states.get(group.entity_id), before_state[0])
            if scenario == 'drift':
                # A real Core state-machine write creates a distinct State;
                # persisted unload evidence cannot turn it into live authority.
                self.f.hass.states.async_set(group.entity_id, before_state[0].state,
                    dict(before_state[0].attributes), force_update=True)
                self.assertIsNot(self.f.hass.states.get(group.entity_id), before_state[0])
            if scenario != 'success':
                with self.assertRaises(Exception) as refused:
                    await registry_adapter.apply(group_step, self.guard)
                print('GROUP_OWN_ID_REFUSAL_REASON', scenario, type(refused.exception).__name__, str(refused.exception))
                self.assertEqual(self.f.entity.async_get(group.entity_id).id, group.registry_id)
                self.assertIsNone(self.f.entity.async_get('sensor.synthetic_new_sum_energy'))
                self.assertIsNone(self.f.hass.states.get('sensor.synthetic_new_sum_energy'))
                if scenario == 'missing_restore':
                    # The durable registry intent exists, but no effect receipt
                    # can be reconstructed or replayed after the refusal.
                    reopened_pending = self.rr.RegistryReceiptChain.reopen(
                        self.receipts, 'powercalc-typed-root', plan, self.receipt, digests)
                    with self.assertRaises(self.rr.RegistryReceiptError):
                        reopened_pending.reconstruct_inputs()
                print('GROUP_OWN_ID_REFUSED', scenario)
                return
            await registry_adapter.apply(group_step, self.guard)
            renamed = self.f.entity.async_get('sensor.synthetic_new_sum_energy')
            self.assertEqual(renamed.id, group.registry_id)
            self.assertIsNone(self.f.entity.async_get(group.entity_id))
            self.assertIsNone(self.f.hass.states.get(group.entity_id))
            target_state = self.f.hass.states.get('sensor.synthetic_new_sum_energy')
            self.assertIsNotNone(target_state)
            self.assertEqual(target_state.state, 'unavailable')
            self.assertTrue(target_state.attributes['restored'])
            reopened_group = self.rr.RegistryReceiptChain.reopen(
                self.receipts, 'powercalc-typed-root', plan, self.receipt, digests)
            self.assertEqual(reopened_group.reconstruct_inputs().head, lease.state.head)
            self.assertFalse(lifecycle_type.reopen(reopened_group).writable)
            # Readonly validation must reject missing subeffect and UUID drift.
            from copy import deepcopy
            payload = deepcopy(chain._setup_history[-1][1])
            validator = self.module('powercalc_group_receipts').validate_group_receipt
            validator(reopened_group, payload, group_change)
            subeffect = self.receipts.read(payload['powercalc_group_own_id'][0])['payload']
            self.assertEqual(subeffect['registry_uuid'], group.registry_id)
            self.assertEqual(subeffect['before_state'], before_state[1])
            self.assertEqual(subeffect['before_reported'], before_state[2])
            self.assertEqual(subeffect['after_state'], target_state.as_dict_json.decode())
            self.assertEqual(subeffect['after_reported'], target_state.last_reported.isoformat())
            for key, state in (('before_state', before_state[0]), ('after_state', target_state)):
                snapshot = json.loads(subeffect[key])
                self.assertEqual(set(snapshot), {'entity_id', 'state', 'attributes', 'last_changed',
                                                  'last_updated', 'last_reported', 'context'})
                self.assertEqual(snapshot, json.loads(state.as_dict_json))
            missing = deepcopy(payload)
            del missing['powercalc_group_own_id']
            with self.assertRaises(Exception):
                validator(reopened_group, missing, group_change)
            forged = deepcopy(payload)
            forged['mutation']['identity'] = 'foreign-uuid'
            with self.assertRaises(Exception):
                validator(reopened_group, forged, group_change)
            print('GROUP_OWN_ID_NATIVE', {'uuid_preserved': True, 'exact_unload_pin': True,
                  'native_target_placeholder': True, 'readonly_reopen': True, 'refusals': 2})
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
