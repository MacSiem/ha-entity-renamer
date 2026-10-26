"""Native G1/G2 prerequisite only; current Core 2026.10.2, fresh owner FIFO.

Reuse the exact eight-entity profile fixture/namespace/public bytes. Missing
product APIs fail AFTER genuine vendor setup, never during test collection.
No native sensor/cache/counter/Event/State/Task is substituted or seeded.
"""
from dataclasses import replace
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_powercalc_profile_energy_native as graph_fixture

fixture = graph_fixture.fixtures


@unittest.skipUnless(fixture.HAS_HA, 'actual supported HA required; never a PASS')
class NativePowercalcAcceptedCandidateTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = graph_fixture.PowercalcProfileEnergyNativeTests.namespace_source_bytes
    asyncSetUp = graph_fixture.PowercalcProfileEnergyNativeTests.asyncSetUp
    _setup_real_vendor = graph_fixture.PowercalcProfileEnergyNativeTests._setup_real_vendor
    _flush_documents = graph_fixture.PowercalcProfileEnergyNativeTests._flush_documents
    guard = graph_fixture.PowercalcProfileEnergyNativeTests.guard

    def module(self, name):
        try:
            return importlib.import_module(fixture.PACKAGE + '.' + name)
        except ModuleNotFoundError as error:
            if error.name != fixture.PACKAGE + '.' + name:
                raise
            self.fail('missing actual product API after genuine vendor setup: ' + name)

    async def prepare_candidate(self, *, dynamic=False, move_selector=True, metadata=False):
        # asyncSetUp has already used the genuine supported vendor, normal
        # Light/Select producers, GUI setup, Energy manager and native Stores.
        candidates = self.module('powercalc_candidate_sources')
        self.observer = self.module('native_powercalc_accepted_consumers')
        self.runtime = self.module('powercalc_runtime_observer')
        self.validator = self.module('yaml_validation')
        planner = self.module('yaml_plan')
        inputs = self.module('powercalc_profile_inputs')
        if metadata:
            path = self.f.root / 'configuration.yaml'
            path.write_text(path.read_text() + '\nscript:\n  accepted_candidate_probe:\n    sequence:\n'
                + '      - action: homeassistant.update_entity\n        target:\n'
                + '          entity_id: sensor.accepted_probe_old\n')
        self.original_runtime = self.runtime.capture_powercalc_runtime(self.f.hass, guard=self.guard)
        self.original_ids = {entity.entity_id: self.f.entity.async_get(entity.entity_id).id
                             for entity in self.graph}
        old, new = self.yaml_source, 'light.public_static_target'
        self.rename = old, new
        keys = ('core.entity_registry', 'core.device_registry', 'core.config_entries')
        self.original_documents = {key: (self.f.root / '.storage' / key).read_bytes() for key in keys}
        planned = {key: json.loads(raw) for key, raw in self.original_documents.items()}
        entities = planned['core.entity_registry']['data']['entities']
        source = next(row for row in entities if row['entity_id'] == old)
        self.original_source_uuid = source['id']
        if metadata:
            next(row for row in entities if row['entity_id'] == self.selector.entity_id)['name'] = (
                'Powercalc accepted selector preference')
        else:
            source['entity_id'] = new
        if dynamic:
            target = 'light.public_dynamic_target'
            next(row for row in entities if row['entity_id'] == self.gui_source)['entity_id'] = target
            gui = next(row for row in planned['core.config_entries']['data']['entries']
                       if row['entry_id'] == self.entry.entry_id)
            gui['data']['entity_id'] = target
            if move_selector:
                sid = 'select.public_dynamic_target_infrared_brightness'
                next(row for row in entities if row['entity_id'] == self.selector.entity_id)['entity_id'] = sid
        # Only immutable ORIGINAL bytes are inputs. Proposed full images are
        # derived before validation/effect, preserving all unrelated fields.
        self.planned = candidates.PlannedPowercalcDocuments(tuple(
            (key, self.original_documents[key],
             json.dumps(planned[key], sort_keys=True, separators=(',', ':')).encode())
            for key in keys))
        mapping = ({'sensor.accepted_probe_old': 'sensor.accepted_probe_new'} if metadata else {old: new})
        plan = planner.plan_yaml(self.f.root, mapping)
        self.assertTrue(plan.ready, plan.issues)
        self.yaml_plan = await inputs.async_add_powercalc_source_inputs(self.f.hass, plan, guard=self.guard)
        self.receipt, self.evidence = await self.validator.async_validate_proposed_with_powercalc_sources(
            self.f.hass, self.yaml_plan, self.f.root.parent, candidate_documents=self.planned)
        if not metadata:
            self.assertFalse(self.receipt.ready_for_apply)
            self.assertIn('powercalc', self.receipt.unsupported_domains)
        self.assertFalse(self.evidence.migration_ready)
        self.assertFalse(self.evidence.powercalc_candidate_sources.migration_ready)

    async def chain_root(self, *, metadata=False):
        await self.prepare_candidate(metadata=metadata)
        baseline = await self.module('native_input_baseline').async_capture_native_input_baseline(
            self.f.hass, self.receipt, guard=self.guard)
        self.prep = self.module('prepared')
        self.preparations = self.prep.native_prepared_store(self.f.hass)
        await self.preparations.initialize()
        inputs = await self.preparations.create_native_inputs('powercalc-accepted-inputs', baseline)
        nr = self.module('native_registry')
        io = nr.NativeRegistryIO(self.f.hass)
        change = await nr.NativeRegistryBridge(io, None).prepare(
            'entity', (self.f.entity.async_get(self.selector.entity_id).id if metadata
                       else self.original_source_uuid),
            {'name': ('Powercalc accepted selector preference' if metadata
                      else 'Powercalc accepted observer source')}, self.guard)
        ref = 'powercalc-observer-step'
        digest = await self.preparations.create(ref, self.prep.encode_change(change))
        self.tx = self.module('transaction')
        step = self.tx.Step('source', 'registry', change.identity, change.before_revision,
                            change.after_revision, ref)
        self.plan = self.tx.Plan('powercalc-observer-root', ('registry',), ('registry',),
            (step,), 'original-root-observer-prerequisite-only', ('registry',), inputs)
        self.rr = self.module('native_registry_receipts')
        self.receipts = self.rr.native_registry_receipt_store(self.f.hass, self.preparations)
        await self.receipts.initialize()
        self.digests = {ref: digest}
        self.operation = 'powercalc-observer-root'
        chain = await self.rr.RegistryReceiptChain.start(
            self.receipts, self.operation, self.plan, self.receipt, self.digests)
        self.writer = chain
        self.chain = self.rr.RegistryReceiptChain.reopen(
            self.receipts, self.operation, self.plan, self.receipt, self.digests)
        self.assertEqual(self.chain.root_id, chain.root_id)
        return self.chain

    async def observe(self, chain=None, evidence=None, guard=None):
        return await self.observer.async_observe_accepted_powercalc_consumers(
            self.f.hass, self.chain if chain is None else chain,
            self.evidence if evidence is None else evidence, self.original_runtime,
            guard=self.guard if guard is None else guard)

    def assert_original(self, observed):
        self.assertEqual(observed.source_position, 'original')
        self.assertEqual(observed.head, self.chain.reconstruct_inputs().head)
        self.assertEqual(observed.evidence_sha256, self.evidence.sha256)
        self.assertEqual({row.entity_id: row.registry_id for row in observed.rows}, self.original_ids)
        self.assertTrue(all(row.source_verified for row in observed.rows))
        self.assertTrue(observed.relationships)
        self.assertFalse(observed.family_complete)
        self.assertFalse(observed.migration_ready)
        self.assertEqual(observed.status, 'UNKNOWN')

    async def test_same_staged_candidate_has_target_and_physical_original_dependencies(self):
        await self.prepare_candidate()
        candidate = self.evidence.powercalc_candidate_sources
        candidate.assert_original(self.receipt, self.evidence.powercalc_sources)
        original = json.loads(self.evidence.powercalc_sources.closed_graph_json)
        proposed = json.loads(candidate.sources.closed_graph_json)
        old = next(row for row in original['profiles'] if row['unique_id'] == 'public-profile-static')
        new = next(row for row in proposed['profiles'] if row['unique_id'] == 'public-profile-static')
        self.assertEqual((old['source'], new['source']), self.rename)
        bindings = dict(proposed['bindings'])
        self.assertEqual(bindings[new['origin']][0][2], self.original_source_uuid)
        self.assertEqual(original['files'], proposed['files'])
        self.assertEqual(original['energy_settings_sha256'], proposed['energy_settings_sha256'])
        self.assertEqual(tuple((key, before) for key, before, _ in candidate.documents.documents),
                         tuple(self.original_documents.items()))
        for key, raw in self.original_documents.items():
            self.assertEqual((self.f.root / '.storage' / key).read_bytes(), raw)
        # A native original-bound binder remains original-only; no activation.
        bound = await self.module('powercalc_source_binding').async_bind_powercalc_sources(
            self.f.hass, self.receipt, self.evidence, self.original_runtime, guard=self.guard)
        self.assertTrue(all(row.source_verified for row in bound.rows))
        self.assertFalse(bound.migration_ready)

    async def test_reopened_chain_observes_real_events_without_rebaseline(self):
        await self.chain_root()
        evidence, capture = self.evidence.sha256, self.original_runtime.structural_token
        self.assert_original(await self.observe())
        for option in ('25%', '100%', 'Disabled'):
            await self.sources[0].async_turn_on(brightness=255)
            await self.selector.async_select_option(option)
            await self.f.hass.async_block_till_done()
            self.assert_original(await self.observe())
            self.assertEqual((self.evidence.sha256, self.original_runtime.structural_token),
                             (evidence, capture))
        fresh_preparations = self.prep.native_prepared_store(self.f.hass)
        await fresh_preparations.initialize()
        fresh_store = self.rr.native_registry_receipt_store(self.f.hass, fresh_preparations)
        await fresh_store.initialize()
        fresh = self.rr.RegistryReceiptChain.reopen(fresh_store, self.operation, self.plan,
                                                   self.receipt, self.digests)
        self.assert_original(await self.observe(chain=fresh))

    async def test_acknowledged_native_prefix_fresh_readonly_reopen_and_owned_reverse(self):
        # Actual registry-only Coordinator operation; NO Powercalc reload, YAML
        # effect, source-ID rename or lifecycle/migration success is claimed.
        await self.chain_root(metadata=True)
        nr = self.module('native_registry')
        journal = self.module('journal')
        log = journal.native_journal(self.f.hass)
        await log.initialize()
        bridge = nr.NativeRegistryBridge(nr.NativeRegistryIO(self.f.hass), None,
                                          native_images=self.writer.accepted_documents)
        async def raw_observation():
            return await self.module('native_input_observation').async_observe_native_inputs(
                self.f.hass, self.receipt, guard=self.guard)
        async def verify(position):
            actual = await self.observe(chain=self.writer)
            return actual.source_position == position and all(row.source_verified for row in actual.rows)
        def adapter(chain, store, active_log):
            return self.module('native_registry_transaction').ReceiptedRegistryTransactionAdapter(
                bridge, lambda ref: self.prep.decode_change(store.resolve(ref, self.digests[ref])),
                chain, raw_observation, active_log.load, self.guard,
                full_verify=lambda _: verify('original_yaml_proposed_native'),
                full_verify_rollback=lambda _: verify('original'))
        coordinator = self.tx.Coordinator({'registry': adapter(self.writer, self.preparations, log)},
            log, self.guard, required_scopes=('registry',))
        applied = await coordinator.apply(self.plan, self.plan.fingerprint, self.operation)
        self.assertEqual(applied.status, 'applied', applied)
        observed = await self.observe()
        self.assertEqual(observed.source_position, 'original_yaml_proposed_native')
        self.assertTrue(all(row.source_verified for row in observed.rows))
        fresh_preparations = self.prep.native_prepared_store(self.f.hass)
        await fresh_preparations.initialize()
        fresh_store = self.rr.native_registry_receipt_store(self.f.hass, fresh_preparations)
        await fresh_store.initialize()
        fresh_log = journal.native_journal(self.f.hass)
        await fresh_log.initialize()
        self.chain = self.rr.RegistryReceiptChain.reopen(
            fresh_store, self.operation, self.plan, self.receipt, self.digests)
        # A reopened chain is intentionally read-only. The SAME live writer
        # owns reverse; fresh journal/Coordinator do not mint a new writer.
        fresh = self.tx.Coordinator({'registry': adapter(self.writer, self.preparations, fresh_log)},
            fresh_log, self.guard, required_scopes=('registry',))
        duplicate = await fresh.apply(self.plan, self.plan.fingerprint, self.operation)
        self.assertEqual(duplicate, applied)
        reversed_result = await fresh.rollback(self.operation)
        self.assertEqual(reversed_result.status, 'rolled_back', reversed_result)
        restored_preparations = self.prep.native_prepared_store(self.f.hass)
        await restored_preparations.initialize()
        restored_store = self.rr.native_registry_receipt_store(self.f.hass, restored_preparations)
        await restored_store.initialize()
        self.chain = self.rr.RegistryReceiptChain.reopen(
            restored_store, self.operation, self.plan, self.receipt, self.digests)
        self.assert_original(await self.observe())

    async def test_dynamic_candidate_binds_planned_source_and_derived_selector_uuids(self):
        await self.prepare_candidate(dynamic=True)
        candidate = json.loads(self.evidence.powercalc_candidate_sources.sources.closed_graph_json)
        original = json.loads(self.evidence.powercalc_sources.closed_graph_json)
        origin = 'entry:' + self.entry.entry_id
        old, new = dict(original['bindings'])[origin], dict(candidate['bindings'])[origin]
        self.assertEqual([row[0] for row in new], ['source', 'selector'])
        self.assertEqual(new[0][1], 'light.public_dynamic_target')
        self.assertEqual(new[1][1], 'select.public_dynamic_target_infrared_brightness')
        self.assertEqual([row[2:] for row in old], [row[2:] for row in new])

    async def test_dynamic_candidate_without_planned_derived_selector_refuses(self):
        with self.assertRaises(ValueError):
            await self.prepare_candidate(dynamic=True, move_selector=False)

    async def test_dict_state_or_detached_state_is_not_chain_authority(self):
        await self.chain_root()
        for value in ({}, self.chain.reconstruct_inputs()):
            with self.assertRaises(ValueError):
                await self.observe(chain=value)

    async def test_pending_actual_durable_intent_refuses_without_effect_or_replay(self):
        await self.chain_root()
        log = self.module('journal').native_journal(self.f.hass)
        await log.initialize()
        record = self.tx.Record(self.operation, self.plan, status='applying', phases=('intent',))
        await log.create(record)
        ctx = self.tx.PhaseContext(await log.load(self.operation), 0)
        state = self.chain.reconstruct_inputs()
        observed = await self.module('native_input_observation').async_observe_native_inputs(
            self.f.hass, self.receipt, guard=self.guard, input_state=state)
        before = tuple(self.rr.RegistryRawObservation(row.key, row.raw, row.identity)
                       for row in observed.files)
        await self.writer.begin(ctx, before)
        with self.assertRaises(ValueError):
            await self.observe()
        # No mutator or compensation runs; pending state remains explicit.

    async def test_late_profile_edit_after_real_final_vendor_read_refuses(self):
        await self.chain_root()
        path = self.profile_root / 'lifx/LHB30E27UC10P/infrared_100/model.json'
        raw = path.read_bytes()
        cls = self.module('powercalc_source_binding').PowercalcVendor
        actual, calls = cls.sources, []
        def sources(owner):
            value = actual(owner)
            calls.append(True)
            if len(calls) == 2:
                path.write_bytes(raw + b'\n')
            return value
        try:
            with patch.object(cls, 'sources', sources):
                with self.assertRaises(ValueError):
                    await self.observe()
            self.assertEqual(len(calls), 2)
        finally:
            path.write_bytes(raw)

    async def test_late_real_selector_detach_after_final_io_refuses(self):
        import asyncio
        from concurrent.futures import Future
        import threading
        from homeassistant.core import callback
        from homeassistant.helpers import event
        await self.chain_root()
        cls = self.module('powercalc_source_binding').PowercalcVendor
        actual, calls = cls.sources, []
        loop, loop_thread = asyncio.get_running_loop(), threading.get_ident()
        remove = self.f.hass.data[event._TRACK_STATE_CHANGE_DATA].listener
        completion = Future()

        @callback
        def detach_on_loop():
            try:
                self.assertEqual(threading.get_ident(), loop_thread)
                remove()  # Actual shared EventBus remover, on its owning loop.
            except BaseException as error:
                completion.set_exception(error)
            else:
                completion.set_result(None)

        def sources(owner):
            value = actual(owner)
            calls.append(True)
            if len(calls) == 2:
                # Only this executor worker waits. Native removal must finish
                # before the second real source reader returns to the observer.
                self.assertNotEqual(threading.get_ident(), loop_thread)
                loop.call_soon_threadsafe(detach_on_loop)
                completion.result()  # Re-raise the original native exception.
            return value
        with patch.object(cls, 'sources', sources):
            with self.assertRaises(ValueError):
                await self.observe()
        self.assertEqual(len(calls), 2)
        self.assertTrue(completion.done())
        # Outside assertRaises: a remover ValueError cannot impersonate the
        # expected product refusal. The completed future never blocks the loop.
        completion.result()

    async def test_missing_durable_preparations_cannot_reopen_same_chain(self):
        await self.chain_root()
        path = Path(self.f.hass.config.path('.storage', 'ha_entity_renamer.prepared'))
        quarantine = path.with_name(path.name + '.quarantine')
        path.rename(quarantine)
        try:
            with self.assertRaises(ValueError):
                await self.observe()
            fresh_preparations = self.prep.native_prepared_store(self.f.hass)
            await fresh_preparations.initialize()
            fresh_store = self.rr.native_registry_receipt_store(self.f.hass, fresh_preparations)
            await fresh_store.initialize()
            with self.assertRaises(ValueError):
                self.rr.RegistryReceiptChain.reopen(fresh_store, self.operation, self.plan,
                                                    self.receipt, self.digests)
        finally:
            quarantine.rename(path)

    async def test_foreign_native_registry_edit_cannot_become_accepted_graph(self):
        await self.chain_root()
        self.f.entity.async_update_entity(self.yaml_source, name='Foreign unreceipted name')
        await self._flush_documents()
        with self.assertRaises(ValueError):
            await self.observe()

    async def test_candidate_from_different_original_is_refused(self):
        await self.chain_root()
        candidate = replace(self.evidence.powercalc_candidate_sources,
                            original_sources_sha256='0' * 64)
        with self.assertRaises(ValueError):
            candidate.assert_original(self.receipt, self.evidence.powercalc_sources)
