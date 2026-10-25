"""Actual isolated HA: quiescence, mixed receipts, Recorder and restored helper.

Pinned Core schemas only. No running QA API/config, endpoint or readiness claim.
Lifecycle uses separate real private Stores in both forward and reverse cycles.
"""
from datetime import timedelta
from contextlib import ExitStack
import importlib
import json
import logging
import os
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import test_native_helper_quiescence as quiescence

fixtures = quiescence.fixtures


class NativeReceiptedHelperTests(quiescence.NativeHelperQuiescenceTests):
    with_yaml = False

    async def _before_start(self, hass, root):
        from homeassistant.const import __version__
        if __version__ not in ('2025.6.0', '2026.10.2'):
            self.skipTest('native helper callbacks require an exact pinned Core')
        from homeassistant.helpers.recorder import async_initialize_recorder
        from homeassistant.setup import async_setup_component
        async_initialize_recorder(hass)
        self.assertTrue(await async_setup_component(hass, 'recorder', {'recorder': {
            'db_url': 'sqlite:///' + str(root / 'fixture.db'), 'commit_interval': 0,
            'auto_purge': False, 'auto_repack': False}}))
        if self.with_yaml:
            import socket
            from homeassistant import auth
            hass.auth = await auth.auth_manager_from_config(hass, [], [])
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            self.assertTrue(await async_setup_component(hass, 'http', {'http': {
                'server_host': '127.0.0.1', 'server_port': port}}))
            from homeassistant.components.config import automation, script, scene
            for editor in (automation, script, scene):
                self.assertTrue(editor.async_setup(hass))

    async def test_threshold_supported_unload_own_mutations_setup_and_reverse(self):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.components.threshold.config_flow import ConfigFlowHandler
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_component import EntityComponent
        f, hass = self.f, self.f.hass
        m = {name: importlib.import_module(fixtures.PACKAGE + '.' + name) for name in (
            'native_registry', 'native_registry_receipts', 'native_registry_transaction',
            'native_helper_transaction', 'native_input_observation', 'helper_native',
            'helper_transaction', 'helper_observers', 'prepared', 'journal', 'transaction',
            'recorder_guard', 'history_guard', 'native_helper_lifecycle',
            'yaml_transaction', 'yaml_runtime_observer', 'yaml_editor_locks',
            'native_yaml_transaction')}
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
            source='user', version=ConfigFlowHandler.VERSION,
            minor_version=ConfigFlowHandler.MINOR_VERSION, subentries_data=[],
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
        # Produce the ORIGINAL receipt with the real isolated native checker.
        # The inner migration below covers registry/helpers only; YAML remains
        # unchanged and its independently projected proposal stays pinned.
        (f.root / 'configuration.yaml').write_text('script:\n  receipt_probe:\n'
            '    sequence:\n      - variables:\n'
            '          sample: "{{ states(\'' + old + '\') }}"\n')
        captured_script_values = []
        if self.with_yaml:
            from homeassistant.config import async_hass_config_yaml
            from homeassistant.setup import async_setup_component
            (f.root / 'scripts').mkdir()
            (f.root / 'configuration.yaml').write_text('script: !include_dir_merge_named scripts\n')
            (f.root / 'scripts/probe.yaml').write_text('receipt_probe:\n  sequence:\n'
                '    - action: renamer_probe.capture\n      data:\n'
                '        value: "{{ states(\'' + old + '\') }}"\n')
            async def capture_script(call):
                captured_script_values.append(str(call.data['value']))
            hass.services.async_register('renamer_probe', 'capture', capture_script)
            self.assertTrue(await async_setup_component(hass, 'script',
                await async_hass_config_yaml(hass)))
            await self._flush_documents()
        yaml_plan = importlib.import_module(fixtures.PACKAGE + '.yaml_plan')
        proposed_yaml = yaml_plan.plan_yaml(f.root, {old: new})
        self.assertTrue(proposed_yaml.ready, proposed_yaml.issues)
        self.assertEqual(len(proposed_yaml.patches), 1)
        receipt = await fixtures.validation.async_validate_proposed(
            hass, proposed_yaml, f.root.parent)
        self.assertTrue(receipt.ready_for_apply)
        self.assertEqual(receipt.changed_domains, ('script',))
        self.assertTrue(all(before is not None and after is not None
                            for _, before, after in receipt.runtime_expected))
        self.assertFalse(any('fixture.db' in row[0] for row in receipt.source_files))
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
        changes = [registry_change, helper_change]
        scopes = ['registry', 'helpers']
        if self.with_yaml:
            yaml_change = await fixtures.validation._settled_executor(
                hass, m['yaml_transaction']._capture, receipt, proposed_yaml)
            changes.append(yaml_change)
            scopes.append('yaml')
        digests, steps = {}, []
        for index, (change, adapter) in enumerate(zip(changes, scopes)):
            ref = 'mixed-helper-' + str(index)
            digests[ref] = await preparations.create(ref, prep.encode_change(change))
            steps.append(tx.Step(ref, adapter, change.identity, change.before_revision,
                                 change.after_revision, ref))
        plan = tx.Plan('mixed-helper-plan', tuple(scopes), tuple(scopes),
                       tuple(steps), 'isolated-threshold-receipts', tuple(scopes), inputs)
        log = m['journal'].native_journal(hass)
        await log.initialize()
        store = rr.native_registry_receipt_store(hass, preparations)
        await store.initialize()
        operation = 'mixed-helper-native-operation'
        chain = await rr.RegistryReceiptChain.start(store, operation, plan, receipt, digests)
        bridge = nr.NativeRegistryBridge(io, recorder, native_images=chain.accepted_documents)
        observation_state = chain.reconstruct_inputs() if self.with_yaml else None
        async def observe(input_state=None):
            nonlocal observation_state
            if self.with_yaml and input_state is None:
                try:
                    observation_state = chain.reconstruct_inputs()
                except rr.RegistryReceiptError:
                    if chain.active is None or chain.store.read(chain.active.reference[0])[
                            'payload']['kind'] != 'registry_intent':
                        raise
                    # This same live native intent may observe its direct result;
                    # the old state's head/preparations remain independently fenced.
                input_state = observation_state
            return await m['native_input_observation'].async_observe_native_inputs(
                hass, receipt, guard=lambda: None, input_state=input_state)
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
        fault = getattr(self, 'lifecycle_save_fault', None)
        if fault is not None:
            native_save = lifecycle_store.store.save
            async def lose_ack(document):
                await native_save(document)
                if any(row['payload']['kind'] == 'helper_lifecycle_' + fault
                       for row in document['events'].values()):
                    raise OSError('actual lifecycle save ACK lost after durable native save')
            with patch.object(lifecycle_store.store, 'save', lose_ack):
                with self.assertRaisesRegex(OSError, 'ACK lost'):
                    lifecycle = await lm.NativeHelperLifecycle.start(
                        *lifecycle_args, **lifecycle_callbacks)
                    await lifecycle.quiesce()
            fresh = prep.native_prepared_store(hass)
            await fresh.initialize()
            recovered_store = lm.native_helper_lifecycle_store(hass, fresh)
            await recovered_store.initialize()
            recovered = lm.NativeHelperLifecycle.reopen(recovered_store,
                *lifecycle_args[1:], **lifecycle_callbacks)
            report = await recovered.reconcile()
            self.assertTrue(report['read_only'])
            self.assertFalse(report['full_ready'])
            if fault == 'intent':
                self.assertEqual(report['pending'], [{'stage': 'unload', 'entry_id': helper.entry_id}])
            elif fault == 'receipt':
                self.assertTrue(report['inactive_receipts_complete'])
            else:
                self.assertFalse(report['inactive_receipts_complete'])
            self.assertIs(helper.state, ConfigEntryState.NOT_LOADED if fault == 'receipt'
                          else ConfigEntryState.LOADED)
            before_readonly = await observe()
            with self.assertRaisesRegex(lm.HelperLifecycleError, 'read_only'):
                await recovered.quiesce()
            self.assertEqual(await observe(), before_readonly)
            return
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
            if self.with_yaml:
                expected = tx.Position.AFTER if direction == 'after' else tx.Position.BEFORE
                if await yaml_observer.position(yaml_change) != expected:
                    return False
            return await callback(plan)
        adapter = m['native_helper_transaction'].ReceiptedHelperTransactionAdapter(
            native, chain, observe, log.load, guarded_quiescence,
            full_verify=lambda _: verify('after'), full_verify_rollback=lambda _: verify('before'))
        registry = m['native_registry_transaction'].ReceiptedRegistryTransactionAdapter(
            bridge, resolver, chain, observe, log.load, adapter.guard,
            full_verify=lambda _: verify('after'), full_verify_rollback=lambda _: verify('before'))
        adapters = {'registry': registry, 'helpers': adapter}
        if self.with_yaml:
            from homeassistant.core import Context
            yaml_observer = m['yaml_runtime_observer'].NativeYAMLRuntimeReadback(hass)
            locks = m['yaml_editor_locks'].NativeYAMLEditorLocks(hass)
            self.assertGreaterEqual(len(locks.enumerate()), 3)
            yaml_adapter = m['native_yaml_transaction'].ReceiptedYAMLTransactionAdapter(
                hass, resolver, locks, yaml_observer, chain, observe, log.load, adapter.guard,
                context=Context(), full_verify=lambda _: verify('after'),
                full_verify_rollback=lambda _: verify('before'))
            adapters['yaml'] = yaml_adapter
        coordinator = tx.Coordinator(adapters, log, adapter.guard, required_scopes=tuple(scopes))
        calls = []
        def intents(index):
            durable = m['journal'].decode(json.loads(Path(hass.config.path('.storage',
                'ha_entity_renamer.migrations')).read_bytes())['data'])[operation]
            expected = 'intent' if durable.direction == 'forward' else 'undo_intent'
            self.assertEqual(durable.phases[index], expected)
            active_adapter = (registry if index == 0 else adapter) if index < 2 else yaml_adapter
            active_context = (active_adapter.context if index < 2 else active_adapter.phase_context)
            self.assertEqual(active_context.record, durable)
            sidecar = json.loads(Path(hass.config.path('.storage',
                'ha_entity_renamer.registry_receipts')).read_bytes())['data']
            self.assertEqual(sidecar['events'][chain.active.reference[0]]['payload']['kind'],
                             'registry_intent' if index < 2 else 'yaml_intent')
            calls.append((index, durable.direction))
        original_registry, original_helper = io.mutate, m['helper_native']._mutate_entry
        def registry_call(*args, **kwargs):
            intents(0)
            return original_registry(*args, **kwargs)
        def helper_call(*args, **kwargs):
            intents(1)
            return original_helper(*args, **kwargs)
        original_file = m['yaml_transaction'].apply_file_patch
        def file_call(*args, **kwargs):
            intents(2)
            result = original_file(*args, **kwargs)
            # Keep this owned fixture's replaced inode pinned across subsequent
            # effects on mounted filesystems; never adopt a changed lookup inode.
            pin = os.open(f.root / result['path'], os.O_RDONLY | os.O_NOFOLLOW)
            self.addCleanup(os.close, pin)
            return result
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
                if self.with_yaml:
                    from homeassistant.core import Context
                    captured_script_values.clear()
                    await hass.services.async_call('script', 'receipt_probe', {},
                                                   blocking=True, context=Context())
                    self.assertEqual(captured_script_values, [str(value)])
            self.assertTrue(await history.verify(historical))
        with ExitStack() as effects:
            effects.enter_context(patch.object(io, 'mutate', registry_call))
            effects.enter_context(patch.object(m['helper_native'], '_mutate_entry', helper_call))
            if self.with_yaml:
                effects.enter_context(patch.object(m['yaml_transaction'], 'apply_file_patch', file_call))
            applied = await coordinator.apply(plan, plan.fingerprint, operation)
            self.assertEqual(applied.status, 'applied', (applied.reason, applied.phases))
            expected_forward = [(i, 'forward') for i in range(len(steps))]
            self.assertEqual(calls, expected_forward)
            self.assertEqual(await coordinator.apply(plan, plan.fingerprint, operation), applied)
            fresh_preparations = prep.native_prepared_store(hass)
            await fresh_preparations.initialize()
            fresh_store = rr.native_registry_receipt_store(hass, fresh_preparations)
            await fresh_store.initialize()
            fresh_chain = rr.RegistryReceiptChain.reopen(fresh_store, operation, plan, receipt, digests)
            observed = await observe()
            fresh_chain.expected(tx.PhaseContext(await log.load(operation), 0), tuple(
                rr.RegistryRawObservation(item.key, item.raw, item.identity) for item in observed.files))
            expected_config = helper_change.after
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
            self.assertEqual(calls, expected_forward +
                             [(i, 'rollback') for i in reversed(range(len(steps)))])
            expected_config = helper_change.before
            await restore_and_stimulate(helper_change.before)
        self.assertIs(component.get_entity(old), meter)
        self.assertEqual(f.entity.async_get(old).id, source_uuid)
        self.assertEqual(m['helper_native'].read_entry(hass, helper.entry_id), original_snapshot)


class NativeLifecycleRootAckLossTests(NativeReceiptedHelperTests):
    lifecycle_save_fault = 'root'


class NativeLifecycleIntentAckLossTests(NativeReceiptedHelperTests):
    lifecycle_save_fault = 'intent'


class NativeLifecycleReceiptAckLossTests(NativeReceiptedHelperTests):
    lifecycle_save_fault = 'receipt'
