"""Real numeric Template ConfigEntry activation-scope RED; owner runs FIFO.

Natural ConfigEntry-only native checker, actual Coordinator forward and fresh
reverse, lifecycle setup effects, UUID/preferences and Recorder continuity.
The original two lease prerequisites remain separate. Owner executes via FIFO;
merely discovering these tests is no native acceptance.
"""
import importlib
from dataclasses import replace
import json
from types import MappingProxyType
import unittest

import test_native_template_consumers as consumers
import test_receipted_helper_native as receipted

fixtures = consumers.fixture.fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateConfigEntryActivationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = consumers.NativeTemplateConsumerTests.asyncSetUp
    _flush_documents = consumers.NativeTemplateConsumerTests._flush_documents
    _before_start = receipted.NativeReceiptedHelperTests._before_start
    emit = consumers.NativeTemplateConsumerTests.emit
    with_yaml = False

    async def test_numeric_entry_source_rename_can_acquire_native_activation_scope(self):
        await self._scope(entry_unique_id='template-entry-activation')

    async def test_actual_gui_entry_without_unique_id_can_acquire_same_scope(self):
        await self._scope(entry_unique_id=None)

    async def test_entry_only_proposal_can_receive_original_native_validation(self):
        await self._scope(entry_unique_id=None, validate_entry_proposal=True)

    async def test_actual_numeric_entry_source_rename_unload_setup_and_fresh_reverse(self):
        await self._scope(entry_unique_id='template-entry-activation', full_flow=True)

    async def test_actual_gui_none_entry_source_rename_unload_setup_and_fresh_reverse(self):
        await self._scope(entry_unique_id=None, full_flow=True)

    async def test_tampered_prepared_request_never_mints_native_receipt(self):
        await self._scope(entry_unique_id=None, validate_entry_proposal=True, fault='request')

    async def test_missing_helper_seal_refuses_before_unload(self):
        await self._scope(entry_unique_id=None, full_flow=True, fault='seal')

    async def test_setup_metadata_delta_refuses_receipt_and_preserves_unknown(self):
        await self._scope(entry_unique_id=None, full_flow=True, fault='metadata')

    async def test_real_core_options_update_during_setup_stays_unknown_without_replay(self):
        await self._scope(entry_unique_id=None, full_flow=True, fault='update')

    async def test_setup_receipt_ack_loss_reopens_without_replay(self):
        await self._scope(entry_unique_id=None, full_flow=True, fault='ack')

    async def test_durable_numeric_entry_reverse_has_no_forward_python_evidence(self):
        await self._durable_scope(entry_unique_id='template-entry-durable')

    async def test_durable_gui_none_reverse_has_no_forward_python_evidence(self):
        await self._durable_scope(entry_unique_id=None)

    async def test_durable_reverse_wrong_phase_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='wrong_phase')

    async def test_durable_reverse_wrong_fingerprint_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='wrong_fingerprint')

    async def test_durable_reverse_wrong_head_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='wrong_head')

    async def test_durable_reverse_wrong_lease_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='wrong_lease')

    async def test_durable_reverse_foreign_source_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='foreign_source')

    async def test_durable_reverse_incomplete_activation_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='incomplete_activation')

    async def test_durable_reverse_pending_intent_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='pending_intent')

    async def test_durable_reverse_missing_composition_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='missing_composition')

    async def test_durable_reverse_tampered_composition_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='tampered_composition')

    async def test_durable_reverse_reverse_ack_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='reverse_ack')

    async def test_durable_reverse_reverse_replay_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='reverse_replay')

    async def test_durable_reverse_revoked_refuses_without_replay(self):
        await self._durable_scope(entry_unique_id=None, fault='revoked')

    async def _durable_scope(self, *, entry_unique_id, fault=None):
        # Complete the forward caller frames before invoking ANY fresh bind.
        # Weak witnesses cannot lend the reverse path live forward authority.
        detached = await self._scope(entry_unique_id=entry_unique_id, full_flow=True,
                                     fault=fault, fresh_reverse=True)
        import gc
        gc.collect()
        values, old_refs = detached
        self.assertTrue(all(ref() is None for ref in old_refs),
                        'old forward proof object remains live after its frames returned')
        from native_template_composition_scenario import fresh_reverse_scenario
        await fresh_reverse_scenario(self, *values)

    async def _scope(self, *, entry_unique_id, validate_entry_proposal=False,
                     full_flow=False, fault=None, fresh_reverse=False):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component

        def module(name):
            return importlib.import_module(fixtures.PACKAGE + '.' + name)

        hass, root = self.f.hass, self.f.root
        self.assertTrue(await async_setup_component(hass, 'sensor', {'sensor': []}))

        class Producer(SensorEntity):
            _attr_unique_id = 'template-entry-activation-producer'
            _attr_name = 'Template entry activation producer'
            _attr_should_poll = False
            _attr_native_value = 1

        self.producer = Producer()
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([self.producer])
        self.source_id = self.producer.entity_id
        target = 'sensor.template_entry_activation_next'
        mapping = {self.source_id: target}
        self.template_variant, self.consumer_domain = 'sensor', 'sensor'
        expression = "{{ states('" + self.source_id + "') | int(0) * 2 }}"
        entry = ConfigEntry(domain='template', title='Entry activation consumer', data={},
            options={'template_type': 'sensor', 'name': 'Entry activation consumer',
                     'state': expression},
            version=TemplateConfigFlowHandler.VERSION,
            minor_version=TemplateConfigFlowHandler.MINOR_VERSION,
            source='user', discovery_keys=MappingProxyType({}), subentries_data=[],
            unique_id=entry_unique_id, pref_disable_polling=True)
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        self.consumer_key = entry.entry_id
        self.consumer = next(entity for platform in async_get_platforms(hass, 'template')
            if platform.domain == 'sensor' and platform.config_entry is entry
            for entity in platform.entities.values() if entity.unique_id == entry.entry_id)
        row = self.f.entity.async_get(self.consumer.entity_id)
        row = self.f.entity.async_update_entity(row.entity_id,
            hidden_by=er.RegistryEntryHider.USER, name='Retained entry preference')
        self.consumer_uuid = row.id
        consumer_identity = (row.entity_id, row.id, row.unique_id, row.config_entry_id,
                             row.hidden_by, row.name)
        source_uuid = self.f.entity.async_get(self.source_id).id
        original = module('helper_native').read_entry(hass, entry.entry_id)
        self.assertTrue(original['identity']['pref_disable_polling'])
        for value in (5, 7):
            await self.emit(value)  # Actual producer writes, genuine consumer 10/14.
        before_documents = await self._flush_documents(initialize=True)

        # Natural YAML graph is valid and unchanged. Never add a source_probe edit
        # or require YAML patch validation for this native prerequisite.
        yaml_plan = module('yaml_plan').plan_yaml(root, mapping)
        self.assertTrue(yaml_plan.ready, yaml_plan.issues)
        self.assertEqual(yaml_plan.patches, ())
        self.assertEqual((root / 'configuration.yaml').read_bytes(), b'script: {}\n')

        native = module('helper_native')
        helper_change = module('helper_transaction').prepare_helper(original, mapping)
        self.assertFalse(helper_change.plan().blocked)
        self.assertEqual([change.path for change in helper_change.plan().changes],
                         [('options', 'state')])
        self.assertEqual(helper_change.after['options']['state'], expression.replace(self.source_id, target))
        self.assertEqual(helper_change.after['identity'], original['identity'])
        self.assertEqual(helper_change.after['subentries'], original['subentries'])
        discovery = module('native_discovery_inputs')
        observed = await discovery.async_observe_discovery_inputs(hass, guard=lambda: None)
        source_binding = module('native_configentry_binding').bind_source_consumer(
            observed, native.inventory(hass, mapping))
        self.assertEqual(source_binding.entries, len(hass.config_entries.async_entries()))
        self.assertFalse(source_binding.can_write)
        self.assertFalse(source_binding.can_promote)

        # Reuse exact native slot/dependency/source semantics proofs. This is
        # discovery source evidence, explicitly not an ORIGINAL receipt/chain.
        proof = module('native_template_consumers')
        pinned = proof._Native(hass)
        await hass.async_add_executor_job(pinned.sources)
        pinned.bindings()
        semantic, template, _slot = proof._live_sensor(pinned, self.consumer)
        dependencies, _token = proof._registration(pinned, self.consumer, template)
        self.assertEqual(dependencies, (self.source_id,))
        documents = {item.key: json.loads(item.manager_json) for item in observed.files}
        registry_row = next(item for item in documents['core.entity_registry']['entities']
                            if item['id'] == self.consumer_uuid)
        self.assertTrue(proof._source_semantics(pinned, self.consumer, registry_row,
                                               semantic, None, documents))
        discovery.assert_discovery_managers_current(hass, observed, guard=lambda: None)

        recorder = module('recorder_guard').NativeRecorderGuard(hass)
        history = module('history_guard').NativeHistoryGuard(recorder)
        consumer_history = await history.capture({row.entity_id: row.entity_id})
        self.assertTrue(consumer_history['state_ids'])
        self.assertGreater(consumer_history['content']['states']['count'], 0)
        registry = module('native_registry')
        bridge = registry.NativeRegistryBridge(registry.NativeRegistryIO(hass), recorder)
        registry_change = await bridge.prepare('entity', source_uuid,
                                               {'entity_id': target}, lambda: None)
        preparations_api = module('prepared')
        preparations = preparations_api.native_prepared_store(hass)
        await preparations.initialize()
        digests = {}
        for reference, change in (('template-entry-source', registry_change),
                                  ('template-entry-options', helper_change)):
            digests[reference] = await preparations.create(reference,
                preparations_api.encode_change(change))
        reopened = preparations_api.native_prepared_store(hass)
        await reopened.initialize()
        self.assertIsNot(reopened, preparations)
        self.assertEqual(preparations_api.decode_change(reopened.resolve(
            'template-entry-source', digests['template-entry-source'])), registry_change)
        self.assertEqual(preparations_api.decode_change(reopened.resolve(
            'template-entry-options', digests['template-entry-options'])), helper_change)
        self._same_native_documents(before_documents, await self._flush_documents())
        self.assertTrue(await history.verify(consumer_history))
        self.assertEqual(native.read_entry(hass, entry.entry_id), original)
        current = self.f.entity.async_get(row.entity_id)
        self.assertEqual((current.entity_id, current.id, current.unique_id,
                          current.config_entry_id, current.hidden_by, current.name), consumer_identity)
        self.assertEqual(self.f.entity.async_get(self.source_id).id, source_uuid)
        self.assertIs(entry.state, ConfigEntryState.LOADED)

        if fault == 'request':
            from copy import deepcopy
            payload = deepcopy(reopened.resolve('template-entry-options', digests['template-entry-options']))
            payload['data']['after']['options']['availability'] = '{{ true }}'
            with self.assertRaisesRegex(module('transaction').Refused, '^helper_preparation_mismatch$'):
                await module('yaml_validation').async_validate_proposed_with_sources(
                    hass,yaml_plan,root.parent,native_helpers=((
                        'template-entry-options',digests['template-entry-options'],payload),))
            self.assertEqual(native.read_entry(hass,entry.entry_id),original)
            self.assertIs(entry.state,ConfigEntryState.LOADED)
            return

        if validate_entry_proposal or full_flow:
            # Genuine native input preparation has no YAML edit. The existing
            # checker must qualify this natural proposal, not a source_probe.
            # No receipt is constructed or an expected exception caught here.
            receipt, evidence = await module('yaml_validation').async_validate_proposed_with_sources(
                hass, yaml_plan, root.parent, native_helpers=((
                    'template-entry-options', digests['template-entry-options'],
                    reopened.resolve('template-entry-options', digests['template-entry-options'])),))
            self.assertTrue(receipt.ready_for_apply)
            self.assertEqual(receipt.proposed_files, ())
            self.assertEqual(receipt.changed_domains, ())
            self.assertIsNotNone(evidence)

        if full_flow:
            return await self._full_flow(module, receipt, evidence, preparations, digests,
                entry, helper_change, registry_change, recorder, history, consumer_history,
                consumer_identity, original, mapping, fault, fresh_reverse=fresh_reverse)

        # This already qualified native lease prerequisite remains independent
        # of the full Coordinator/lifecycle flow above.
        lease_api = module('native_helper_activation_lease')
        async with await lease_api.NativeHelperActivationLease.acquire(
                hass, hass.config_entries, (entry,), guard=lambda: None) as lease:
            self.assertEqual(lease.entry_ids, (entry.entry_id,))
            self.assertIs(lease.manager, hass.config_entries)
            self.assertTrue(lease.owns_entry(entry))
        self.assertFalse(entry.setup_lock.locked())

    async def _full_flow(self, module, receipt, evidence, preparations, digests,
                         entry, helper_change, registry_change, recorder, history,
                         consumer_history, consumer_identity, original, mapping, fault, *, fresh_reverse=False):
        from homeassistant.config_entries import ConfigEntryState
        from homeassistant.helpers import entity_platform as ep
        from unittest.mock import patch
        hass = self.f.hass
        prep, tx, rr, lm = (module(n) for n in
            ('prepared', 'transaction', 'native_registry_receipts', 'native_helper_lifecycle'))
        template = module('native_template_entry_lifecycle')
        original_inputs = await module('native_input_baseline').async_capture_native_input_baseline(
            hass, receipt, guard=lambda: None)
        source_history = await history.capture(mapping)
        composition = None
        coverage = 'actual-template-numeric-entry'
        if fresh_reverse and fault != 'missing_composition':
            try:
                codec = module('native_template_composition')
            except ModuleNotFoundError as error:
                if error.name != fixtures.PACKAGE + '.native_template_composition':
                    raise
                self.fail('durable composition missing after genuine native ORIGINAL validation')
            coverage = evidence.sha256  # Narrow entry proof, never all-family readiness.
            composition = codec.TemplateComposition(evidence,
                tuple(sorted(digests.items())), source_history, consumer_history, coverage)
        inputs = await preparations.create_native_inputs('template-flow-original', original_inputs,
                                                          composition=composition)
        scopes = ('registry', 'helpers')
        steps = tuple(tx.Step(ref, scope, change.identity, change.before_revision,
                              change.after_revision, ref)
            for ref, scope, change in (('template-entry-source', 'registry', registry_change),
                                      ('template-entry-options', 'helpers', helper_change)))
        plan = tx.Plan('template-numeric-entry-flow', scopes, scopes, steps,
                       coverage, scopes, inputs)
        operation = 'template-numeric-entry-operation'
        journal = module('journal').native_journal(hass)
        await journal.initialize()
        store = rr.native_registry_receipt_store(hass, preparations)
        await store.initialize()
        chain = await rr.RegistryReceiptChain.start(store, operation, plan, receipt, digests)
        observation_state = chain.reconstruct_inputs()
        lease = None
        lifecycle = None
        current_config = original

        def outer_guard():
            self.assertIs(hass.config_entries.async_get_entry(entry.entry_id), entry)
            self.assertIsNone(entry.disabled_by)

        async def observe():
            nonlocal observation_state
            outer_guard()
            try:
                observation_state = chain.reconstruct_inputs()
            except rr.RegistryReceiptError:
                self.assertIsNotNone(chain.active)
                self.assertIn(store.read(chain.active.reference[0])['payload']['kind'],
                              ('registry_intent', 'helper_setup_intent'))
            return await module('native_input_observation').async_observe_native_inputs(
                hass, receipt, guard=outer_guard, input_state=observation_state)

        async def accepted(authorization):
            record = await journal.load(operation)
            observed = await observe()
            chain.expected(tx.PhaseContext(record, 0), tuple(rr.RegistryRawObservation(
                f.key, f.raw, f.identity) for f in observed.files))
            return chain.verify_setup_authorization(authorization)

        async def original_fence(reference, checked_receipt):
            self.assertEqual((reference, checked_receipt), (inputs, receipt))
            module('yaml_validation').assert_validation_current(receipt)
            observed = await module('native_input_observation').async_observe_native_inputs(
                hass, receipt, guard=outer_guard)
            return (observed.original_receipt_json == original_inputs.receipt_json
                    and observed.files == original_inputs.files)

        observer = template.NativeTemplateEntryObserver(hass, chain, evidence, guard=outer_guard)
        await observer.capture_loaded()
        lifecycle_store = lm.native_helper_lifecycle_store(hass, preparations)
        await lifecycle_store.initialize()
        base_args = lifecycle_store, hass.config_entries, operation, plan, receipt, digests
        async def inactive(key):
            return await observer.verify_inactive(key)
        async def loaded(key, activation, authorization):
            await observer.verify_loaded(helper_change.after if authorization.status == 'applied'
                                         else helper_change.before)
            return True
        setup = module('native_helper_setup_effects').NativeHelperSetupEffects(
            hass, chain, observe, journal.load, guard=outer_guard)
        callbacks = dict(guard=outer_guard, verify_original_native_inputs=original_fence,
            settle=observer.settle, verify_inactive=inactive, verify_runtime=loaded,
            verify_accepted_native_chain=accepted, setup_effect=setup)
        lease_api = module('native_helper_activation_lease').NativeHelperActivationLease
        async def acquire(snapshot):
            nonlocal lease
            outer_guard()
            self.assertEqual(module('helper_native').read_entry(hass, entry.entry_id), snapshot)
            if lease is not None:
                lease.release()
            lease = await lease_api.acquire(hass, hass.config_entries, (entry,), guard=outer_guard)
            self.assertEqual(module('helper_native').read_entry(hass, entry.entry_id), snapshot)
            observer.lease = lease
            if lifecycle is not None:
                lifecycle.activation_lease = lease
        try:
            await acquire(original)
            if fault == 'seal':
                # This receipt is deliberately corrupted as a negative input;
                # no positive path creates or substitutes a validation receipt.
                bad = replace(receipt, runtime_expected=())
                with self.assertRaises(ValueError):
                    await lm.NativeHelperLifecycle.start(lifecycle_store, hass.config_entries,
                        operation, plan, bad, digests, activation_lease=lease, **callbacks)
                self.assertIs(entry.state, ConfigEntryState.LOADED)
                self.assertEqual(lifecycle_store.rows, {})
                return
            lifecycle = await lm.NativeHelperLifecycle.start(*base_args,
                activation_lease=lease, **callbacks)
            await lifecycle.quiesce()
            await observer.verify_inactive(entry.entry_id)
            resolver = lambda ref: prep.decode_change(preparations.resolve(ref, digests[ref]))

            def compose(log):
                bridge = module('native_registry').NativeRegistryBridge(
                    module('native_registry').NativeRegistryIO(hass), recorder,
                    native_images=chain.accepted_documents)
                inner = module('helper_transaction').HelperTransactionAdapter(hass, resolver,
                    resolve_step=lambda key: next(s for s in steps if s.key == key),
                    settle=observer.settle, validate_snapshot=observer.validate_snapshot,
                    verify_activation=observer.verify_activation, verify_runtime=observer.verify_activation,
                    verify_inventory=observer.verify_inventory,
                    verify_original_inventory=observer.verify_original_inventory)
                async def full(reverse):
                    await observer.verify_inventory_snapshot(helper_change.before if reverse
                                                             else helper_change.after)
                    return await history.verify(source_history) and await history.verify(consumer_history)
                helper = module('native_helper_transaction').ReceiptedHelperTransactionAdapter(
                    inner, chain, observe, log.load, lifecycle.require_quiescent,
                    full_verify=lambda _: full(False), full_verify_rollback=lambda _: full(True),
                    activation_lease=lease)
                registry = module('native_registry_transaction').ReceiptedRegistryTransactionAdapter(
                    bridge, resolver, chain, observe, log.load, helper.guard,
                    full_verify=lambda _: full(False), full_verify_rollback=lambda _: full(True))
                return tx.Coordinator({'registry': registry, 'helpers': helper}, log,
                                      helper.guard, required_scopes=scopes)

            forward = compose(journal)
            record = await forward.apply(plan, plan.fingerprint, operation)
            self.assertEqual(record.status, 'applied', (record.reason, record.phases))
            current_config = helper_change.after
            await acquire(current_config)
            auth = lm.RestoreAuthorization(operation, plan.fingerprint,
                chain.expected(tx.PhaseContext(record, 0), tuple(rr.RegistryRawObservation(
                    f.key, f.raw, f.identity) for f in (await observe()).files)), record.status)
            if fault == 'metadata':
                self.f.entity.async_update_entity(self.consumer.entity_id, name='foreign preference')
                with self.assertRaises(ValueError):
                    await lifecycle.restore(auth)
                self.assertFalse(lifecycle.inspect()['activation_receipts_complete'])
                return
            if fault == 'update':
                from homeassistant.core import callback
                from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED
                from homeassistant.helpers.dispatcher import async_dispatcher_connect
                updates = []
                attempted_update = False
                @callback
                def foreign_update(change, changed_entry):
                    nonlocal attempted_update
                    if (changed_entry is entry and entry.state.value == 'setup_in_progress'
                            and not attempted_update):
                        attempted_update = True
                        options = dict(entry.options)
                        options['state'] = options['state'].replace('* 2', '* 3')
                        updates.append(hass.config_entries.async_update_entry(entry, options=options))
                remove = async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, foreign_update)
                try:
                    with self.assertRaises(ValueError):
                        await lifecycle.restore(auth)
                finally:
                    remove()
                self.assertEqual(updates, [True])
                self.assertEqual(lifecycle.inspect()['pending'],
                                 [{'stage':'setup','entry_id':entry.entry_id}])
                self.assertFalse(any(r['payload']['kind']=='helper_setup_receipt'
                                     for r in store.rows.values()))
                # Read actual Core effect, never a synthetic manager marker.
                changed_entity = next(e for p in ep.async_get_platforms(hass,'template')
                    if p.config_entry is entry for e in p.entities.values())
                native = module('native_template_consumers')._Native(hass)
                semantic, actual_template, _ = module('native_template_consumers')._live_sensor(
                    native, changed_entity)
                self.assertEqual(semantic[1], entry.options['state'])
                self.assertIn('* 3', semantic[1])
                self.assertIs(entry.state, ConfigEntryState.LOADED)
                # No cleanup write or retry is performed after the uncertain effect.
                return
            if fault == 'ack':
                native_save = store.store.save
                async def lost_ack(document):
                    await native_save(document)
                    if any(r['payload']['kind'] == 'helper_setup_receipt'
                           for r in document['events'].values()):
                        raise OSError('owned setup receipt ACK lost')
                with patch.object(store.store, 'save', lost_ack):
                    with self.assertRaises((ValueError, OSError)):
                        await lifecycle.restore(auth)
                fresh_preps = prep.native_prepared_store(hass)
                await fresh_preps.initialize()
                fresh_receipts = rr.native_registry_receipt_store(hass, fresh_preps)
                await fresh_receipts.initialize()
                fresh_chain = rr.RegistryReceiptChain.reopen(
                    fresh_receipts,operation,plan,receipt,digests)
                self.assertFalse(fresh_chain.writable)
                fresh_chain.reconstruct_inputs()
                fresh_life_store = lm.native_helper_lifecycle_store(hass, fresh_preps)
                await fresh_life_store.initialize()
                readonly = lm.NativeHelperLifecycle.reopen(fresh_life_store,
                    *base_args[1:], **callbacks)
                self.assertFalse(readonly.writable)
                before_state = entry.state
                with self.assertRaises(ValueError):
                    await readonly.restore(auth)
                self.assertIs(entry.state, before_state)
                return
            await lifecycle.restore(auth)
            await observer.verify_loaded(current_config)
            self.assertTrue(lifecycle.inspect()['activation_receipts_complete'])
            self.assertTrue(any(r['payload']['kind'] == 'helper_setup_receipt'
                                for r in store.rows.values()))
            for value in (5, 7):
                await self.emit(value)
            self.assertEqual(self.producer.entity_id, next(iter(mapping.values())))
            self.assertEqual(self.f.entity.async_get(self.producer.entity_id).id, registry_change.identity)
            if fresh_reverse:
                import weakref
                values = (module, operation, plan.fingerprint, plan.native_inputs[2],
                    original_inputs.receipt_json, consumer_identity, original, mapping, fault)
                # No old chain/store/observer/evidence is returned, only weak
                # references that the completed caller verifies are dead.
                old_refs = tuple(weakref.ref(value) for value in
                    (chain, observer, preparations, journal, receipt, evidence, original_inputs))
                return values, old_refs
            fresh_preps = prep.native_prepared_store(hass)
            await fresh_preps.initialize()
            fresh_store = rr.native_registry_receipt_store(hass, fresh_preps)
            await fresh_store.initialize()
            readonly = rr.RegistryReceiptChain.reopen(fresh_store, operation, plan, receipt, digests)
            self.assertFalse(readonly.writable)
            self.assertTrue(readonly.reconstruct_inputs().same_evidence(chain.reconstruct_inputs()))
            fresh_log = module('journal').native_journal(hass)
            await fresh_log.initialize()
            # DurableJournal.load is instance-cached: reverse IO must all use
            # the fresh writer's authoritative journal, including setup effects.
            journal = fresh_log
            callbacks['setup_effect'] = module('native_helper_setup_effects').NativeHelperSetupEffects(
                hass,chain,observe,journal.load,guard=outer_guard)
            forward_auth = lm.RestoreAuthorization(operation, plan.fingerprint,
                chain.reconstruct_inputs().head, 'applied')
            await observer.capture_loaded()
            lifecycle = await lm.NativeHelperLifecycle.start(*base_args,
                activation_lease=lease, source_authorization=forward_auth, **callbacks)
            await lifecycle.quiesce()
            reverse = compose(fresh_log)
            rolled = await reverse.rollback(operation)
            self.assertEqual(rolled.status, 'rolled_back', (rolled.reason, rolled.phases))
            current_config = original
            await acquire(original)
            reverse_auth = lm.RestoreAuthorization(operation, plan.fingerprint,
                chain.reconstruct_inputs().head, 'rolled_back')
            await lifecycle.restore(reverse_auth)
            await observer.verify_loaded(original)
            for value in (5, 7):
                await self.emit(value)
            current = self.f.entity.async_get(self.consumer.entity_id)
            self.assertEqual((current.entity_id, current.id, current.unique_id,
                current.config_entry_id, current.hidden_by, current.name), consumer_identity)
            self.assertEqual(module('helper_native').read_entry(hass, entry.entry_id), original)
            self.assertEqual(self.producer.entity_id, next(iter(mapping)))
            self.assertEqual(self.f.entity.async_get(self.producer.entity_id).id, registry_change.identity)
            self.assertTrue(await history.verify(source_history))
            self.assertTrue(await history.verify(consumer_history))
            self.assertTrue(lifecycle.inspect()['activation_receipts_complete'])
            final_preps = prep.native_prepared_store(hass)
            await final_preps.initialize()
            final_store = rr.native_registry_receipt_store(hass,final_preps)
            await final_store.initialize()
            final_chain = rr.RegistryReceiptChain.reopen(final_store,operation,plan,receipt,digests)
            self.assertTrue(final_chain.reconstruct_inputs().same_evidence(chain.reconstruct_inputs()))
            final_life_store = lm.native_helper_lifecycle_store(hass,final_preps)
            await final_life_store.initialize()
            final_life = lm.NativeHelperLifecycle.reopen(final_life_store,*base_args[1:],
                source_authorization=forward_auth,**callbacks)
            self.assertFalse(final_life.writable)
            self.assertEqual(final_life.inspect()['pending'],[])
            self.assertTrue(final_life.inspect()['activation_receipts_complete'])
        finally:
            if lease is not None:
                lease.release()

    def _same_native_documents(self, before, after):
        self.assertEqual(set(before), set(after))
        for key in before:
            self.assertEqual(json.loads(before[key]), json.loads(after[key]), key)


if __name__ == '__main__':
    unittest.main()
