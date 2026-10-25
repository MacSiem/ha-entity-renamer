"""Actual isolated HA: quiescence, mixed receipts, Recorder and restored helper.

Current Core only: installed helper runtime observers/persistence intentionally
refuse older layouts. No running QA API/config, endpoint or readiness claim.
Lifecycle uses separate real private Stores in both forward and reverse cycles.
"""
from dataclasses import replace
from datetime import timedelta
import importlib
import json
import logging
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import test_native_helper_quiescence as quiescence

fixtures = quiescence.fixtures


class NativeReceiptedHelperTests(quiescence.NativeHelperQuiescenceTests):
    async def _before_start(self, hass, root):
        from homeassistant.const import __version__
        if __version__ != '2026.10.2':
            self.skipTest('native helper callbacks qualified on Core 2026.10.2 only')
        from homeassistant.helpers.recorder import async_initialize_recorder
        from homeassistant.setup import async_setup_component
        async_initialize_recorder(hass)
        self.assertTrue(await async_setup_component(hass, 'recorder', {'recorder': {
            'db_url': 'sqlite:///' + str(root / 'fixture.db'), 'commit_interval': 0,
            'auto_purge': False, 'auto_repack': False}}))

    async def test_threshold_supported_unload_own_mutations_setup_and_reverse(self):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_component import EntityComponent
        f, hass = self.f, self.f.hass
        m = {name: importlib.import_module(fixtures.PACKAGE + '.' + name) for name in (
            'native_registry', 'native_registry_receipts', 'native_registry_transaction',
            'native_helper_transaction', 'native_input_observation', 'helper_native',
            'helper_transaction', 'helper_observers', 'prepared', 'journal', 'transaction',
            'recorder_guard', 'history_guard', 'native_helper_lifecycle')}
        nr, rr, prep, tx = (m[k] for k in ('native_registry', 'native_registry_receipts',
                                          'prepared', 'transaction'))
        class Meter(SensorEntity):
            _attr_name = 'Receipted Helper Meter'
            _attr_unique_id = 'receipted-helper-meter'
            _attr_native_value = 41
            _attr_should_poll = False
        component = EntityComponent(logging.getLogger(__name__), 'sensor', hass,
                                    timedelta(seconds=60))
        meter = Meter()
        await component.async_add_entities([meter])
        old, new = meter.entity_id, 'sensor.receipted_helper_changed'
        helper = ConfigEntry(domain='threshold', title='Receipted Threshold', data={},
            options={'name': 'Receipted Threshold', 'entity_id': old, 'hysteresis': 0.0,
                     'lower': None, 'upper': 42.5}, discovery_keys=MappingProxyType({}),
            source='user', version=1, minor_version=2, subentries_data=[],
            unique_id='receipted-threshold', pref_disable_polling=True)
        await hass.config_entries.async_add(helper)
        await hass.async_block_till_done()
        helper_row = er.async_entries_for_config_entry(f.entity, helper.entry_id)[0]
        f.entity.async_update_entity(helper_row.entity_id,
            hidden_by=er.RegistryEntryHider.USER, name='Retained helper preference')
        await self._flush_documents(initialize=True)
        original_snapshot = m['helper_native'].read_entry(hass, helper.entry_id)
        source_uuid = f.entity.async_get(old).id
        proof = (m['helper_observers'].RegistryMappingProof(old, new, source_uuid),)
        loaded_observer = await m['helper_observers'].NativeHelperObservers.capture(
            hass, (original_snapshot,), proof)
        # Same scope as the native checker: configuration and its three native
        # inputs. Recorder DB/WAL are output, never source authorization inputs.
        receipt = fixtures.receipt_for(f.root, f.version)
        names = {'configuration.yaml', '.storage'}
        receipt = replace(receipt,
            source_files=tuple(row for row in receipt.source_files
                if row[0] == 'configuration.yaml' or row[0] in
                    {'.storage/' + key for key in fixtures.KEYS}),
            source_directories=tuple((relative,
                tuple(row for row in entries if relative != '.' or row[0] in names),
                tuple(sorted(names)) if relative == '.' else selected)
                for relative, entries, selected in receipt.source_directories))
        original = await fixtures.baseline.async_capture_native_input_baseline(
            hass, receipt, guard=lambda: None)
        preparations = prep.native_prepared_store(hass)
        await preparations.initialize()
        inputs = await preparations.create_native_inputs('helper-original-inputs', original)
        recorder = m['recorder_guard'].NativeRecorderGuard(hass)
        history = m['history_guard'].NativeHistoryGuard(recorder)
        historical = await history.capture({old: new})
        io = nr.NativeRegistryIO(hass)
        bridge = nr.NativeRegistryBridge(io, recorder)
        registry_change = await bridge.prepare('entity', source_uuid, {'entity_id': new}, lambda: None)
        helper_change = m['helper_transaction'].prepare_helper(original_snapshot, {old: new})
        changes = (registry_change, helper_change)
        digests, steps = {}, []
        for index, (change, adapter) in enumerate(zip(changes, ('registry', 'helpers'))):
            ref = 'mixed-helper-' + str(index)
            digests[ref] = await preparations.create(ref, prep.encode_change(change))
            steps.append(tx.Step(ref, adapter, change.identity, change.before_revision,
                                 change.after_revision, ref))
        plan = tx.Plan('mixed-helper-plan', ('registry', 'helpers'), ('registry', 'helpers'),
                       tuple(steps), 'isolated-threshold-receipts', ('registry', 'helpers'), inputs)
        log = m['journal'].native_journal(hass)
        await log.initialize()
        store = rr.native_registry_receipt_store(hass, preparations)
        await store.initialize()
        operation = 'mixed-helper-native-operation'
        chain = await rr.RegistryReceiptChain.start(store, operation, plan, receipt, digests)
        bridge = nr.NativeRegistryBridge(io, recorder, native_images=chain.accepted_documents)
        async def observe():
            return await m['native_input_observation'].async_observe_native_inputs(
                hass, receipt, guard=lambda: None)
        async def original_source(reference, original_receipt):
            self.assertEqual((reference, original_receipt), (inputs, receipt))
            fixtures.validation.assert_validation_current(receipt)
            return (await fixtures.baseline.async_capture_native_input_baseline(
                hass, receipt, guard=lambda: None)) == original
        expected_config = original_snapshot
        async def verify_inactive(entry_id):
            from homeassistant.helpers import entity_platform as ep
            self.assertEqual(entry_id, helper.entry_id)
            self.assertIs(helper.state, ConfigEntryState.NOT_LOADED)
            self.assertFalse(any(platform.entities for platform in
                ep.async_get_platforms(hass, 'threshold') if platform.config_entry is helper))
            self.assertFalse(helper.update_listeners)
            self.assertEqual(m['helper_native'].read_entry(hass, entry_id), expected_config)
            self.assertEqual(f.entity.async_get(helper_row.entity_id).id, helper_row.id)
            return True
        async def accepted_source(authorization):
            record = await log.load(operation)
            self.assertIsNotNone(record)
            self.assertEqual(authorization.operation_id, operation)
            self.assertEqual(authorization.fingerprint, plan.fingerprint)
            self.assertEqual(authorization.status, record.status)
            phase = 'verified' if record.status == 'applied' else 'undone'
            self.assertEqual(record.phases, (phase,) * len(plan.steps))
            observed = await observe()
            head = chain.expected(tx.PhaseContext(record, 0), tuple(
                rr.RegistryRawObservation(item.key, item.raw, item.identity) for item in observed.files))
            return head == authorization.head
        async def runtime(entry_id, activation, authorization):
            self.assertEqual(entry_id, helper.entry_id)
            self.assertEqual(activation['state'], 'loaded')
            await loaded_observer.verify_runtime(helper_change.after
                if authorization.status == 'applied' else helper_change.before)
            return True
        lm = m['native_helper_lifecycle']
        lifecycle_store = lm.native_helper_lifecycle_store(hass, preparations)
        await lifecycle_store.initialize()
        lifecycle_args = (lifecycle_store, hass.config_entries, operation, plan, receipt, digests)
        lifecycle_callbacks = dict(guard=lambda: None, verify_original_native_inputs=original_source,
            settle=hass.async_block_till_done, verify_inactive=verify_inactive,
            verify_runtime=runtime, verify_accepted_native_chain=accepted_source)
        lifecycle = await lm.NativeHelperLifecycle.start(*lifecycle_args, **lifecycle_callbacks)
        await lifecycle.quiesce()
        inactive = await m['helper_observers'].NativeHelperObservers.capture(
            hass, (original_snapshot,), proof)
        def guarded_quiescence():
            lifecycle.require_quiescent()
        resolver = lambda ref: prep.decode_change(preparations.resolve(ref, digests[ref]))
        native = m['helper_transaction'].HelperTransactionAdapter(hass, resolver,
            resolve_step=lambda key: next(step for step in steps if step.key == key),
            settle=inactive.settle, validate_snapshot=inactive.validate_snapshot,
            verify_activation=inactive.verify_activation, verify_runtime=inactive.verify_runtime,
            verify_inventory=inactive.verify_inventory,
            verify_original_inventory=inactive.verify_original_inventory)
        async def verify(direction):
            self.assertTrue(await history.verify(historical))
            if (await bridge.inspect(registry_change)).position != direction:
                return False
            callback = (inactive.verify_inventory if direction == 'after'
                        else inactive.verify_original_inventory)
            return await callback(plan)
        adapter = m['native_helper_transaction'].ReceiptedHelperTransactionAdapter(
            native, chain, observe, log.load, guarded_quiescence,
            full_verify=lambda _: verify('after'), full_verify_rollback=lambda _: verify('before'))
        registry = m['native_registry_transaction'].ReceiptedRegistryTransactionAdapter(
            bridge, resolver, chain, observe, log.load, adapter.guard,
            full_verify=lambda _: verify('after'), full_verify_rollback=lambda _: verify('before'))
        coordinator = tx.Coordinator({'registry': registry, 'helpers': adapter}, log,
                                    adapter.guard, required_scopes=('registry', 'helpers'))
        calls = []
        def intents(index):
            durable = m['journal'].decode(json.loads(Path(hass.config.path('.storage',
                'ha_entity_renamer.migrations')).read_bytes())['data'])[operation]
            expected = 'intent' if durable.direction == 'forward' else 'undo_intent'
            self.assertEqual(durable.phases[index], expected)
            self.assertEqual((registry if index == 0 else adapter).context.record, durable)
            sidecar = json.loads(Path(hass.config.path('.storage',
                'ha_entity_renamer.registry_receipts')).read_bytes())['data']
            self.assertEqual(sidecar['events'][chain.active.reference[0]]['payload']['kind'],
                             'registry_intent')
            calls.append((index, durable.direction))
        original_registry, original_helper = io.mutate, m['helper_native']._mutate_entry
        def registry_call(*args, **kwargs):
            intents(0)
            return original_registry(*args, **kwargs)
        def helper_call(*args, **kwargs):
            intents(1)
            return original_helper(*args, **kwargs)
        async def authorization():
            record = await log.load(operation)
            observed = await observe()
            head = chain.expected(tx.PhaseContext(record, 0), tuple(
                rr.RegistryRawObservation(item.key, item.raw, item.identity) for item in observed.files))
            return lm.RestoreAuthorization(operation, plan.fingerprint, head, record.status)
        async def restore_and_stimulate(snapshot):
            await lifecycle.restore(await authorization())
            await loaded_observer.verify_runtime(snapshot)
            self.assertEqual(f.entity.async_get(helper_row.entity_id).id, helper_row.id)
            for value, expected in ((43, 'on'), (41, 'off')):
                meter._attr_native_value = value
                meter.async_write_ha_state()
                await hass.async_block_till_done()
                await loaded_observer.verify_runtime(snapshot)
                self.assertEqual(hass.states.get(helper_row.entity_id).state, expected)
            self.assertTrue(await history.verify(historical))
        with patch.object(io, 'mutate', registry_call), patch.object(
                m['helper_native'], '_mutate_entry', helper_call):
            applied = await coordinator.apply(plan, plan.fingerprint, operation)
            self.assertEqual(applied.status, 'applied', (applied.reason, applied.phases))
            self.assertEqual(calls, [(0, 'forward'), (1, 'forward')])
            self.assertEqual(await coordinator.apply(plan, plan.fingerprint, operation), applied)
            fresh_preparations = prep.native_prepared_store(hass)
            await fresh_preparations.initialize()
            fresh_store = rr.native_registry_receipt_store(hass, fresh_preparations)
            await fresh_store.initialize()
            fresh_chain = rr.RegistryReceiptChain.reopen(fresh_store, operation, plan, receipt, digests)
            observed = await observe()
            fresh_chain.expected(tx.PhaseContext(await log.load(operation), 0), tuple(
                rr.RegistryRawObservation(item.key, item.raw, item.identity) for item in observed.files))
            await restore_and_stimulate(helper_change.after)
            # A separate durable cycle starts from the accepted forward head;
            # it cannot mint a new baseline from post-migration current inputs.
            expected_config = helper_change.after
            forward_authorization = await authorization()
            lifecycle = await lm.NativeHelperLifecycle.start(*lifecycle_args,
                **lifecycle_callbacks, source_authorization=forward_authorization)
            await lifecycle.quiesce()
            self.assertIs(helper.state, ConfigEntryState.NOT_LOADED)
            restored = await coordinator.rollback(operation)
            self.assertEqual(restored.status, 'rolled_back', (restored.reason, restored.phases))
            self.assertEqual(calls, [(0, 'forward'), (1, 'forward'),
                                     (1, 'rollback'), (0, 'rollback')])
            expected_config = helper_change.before
            await restore_and_stimulate(helper_change.before)
        self.assertIs(component.get_entity(old), meter)
        self.assertEqual(f.entity.async_get(old).id, source_uuid)
        self.assertEqual(m['helper_native'].read_entry(hass, helper.entry_id), original_snapshot)
