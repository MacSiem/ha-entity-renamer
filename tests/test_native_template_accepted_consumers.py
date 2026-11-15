"""Actual native consumers at durable ORIGINAL chain root, no apply claim.

The root/preparations/three native stores are real product objects. No fabricated
accepted-state object, output seeding, cache write or replacement callback.
Product Template lifecycle/forward/reverse is deliberately not asserted here.
"""
import importlib
import json
import unittest
import test_native_template_consumers as native_fixture

fixture = native_fixture.fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAcceptedTemplateConsumersTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native_fixture.NativeTemplateConsumerTests.asyncSetUp
    _before_start = native_fixture.NativeTemplateConsumerTests._before_start
    _flush_documents = native_fixture.NativeTemplateConsumerTests._flush_documents
    prepare = native_fixture.NativeTemplateConsumerTests.prepare
    emit = native_fixture.NativeTemplateConsumerTests.emit
    consumer_row = native_fixture.NativeTemplateConsumerTests.consumer_row

    async def chain_root(self, kind):
        await self.prepare(kind)
        package = fixture.fixtures.PACKAGE
        def module(name):
            return importlib.import_module(package + '.' + name)
        baseline = await module('native_input_baseline').async_capture_native_input_baseline(
            self.f.hass, self.receipt, guard=lambda: None)
        prep = module('prepared')
        store = prep.native_prepared_store(self.f.hass)
        await store.initialize()
        inputs = await store.create_native_inputs('template-accepted-inputs', baseline)
        # A true ORIGINAL native row is prepared but NEVER applied in this root
        # test. No synthetic native effect or receipt is accepted as evidence.
        document = json.loads(next(row.raw for row in baseline.files
                                   if row.key == 'core.entity_registry'))['data']
        before = next(row for row in document['entities'] if row['entity_id'] == self.source_id)
        change = module('native_registry').PreparedRegistryChange('entity', before['id'],
            json.dumps(before), json.dumps({**before, 'name': 'Accepted observer source'}))
        digest = await store.create('template-observer-step', prep.encode_change(change))
        tx = module('transaction')
        step = tx.Step('source', 'registry', change.identity, change.before_revision,
                       change.after_revision, 'template-observer-step')
        plan = tx.Plan('template-observer-root', ('registry',), ('registry',),
                       (step,), 'registry-only-root-no-migration', ('registry',), inputs)
        rr = module('native_registry_receipts')
        receipts = rr.native_registry_receipt_store(self.f.hass, store)
        await receipts.initialize()
        chain = await rr.RegistryReceiptChain.start(receipts, 'template-observer-root',
            plan, self.receipt, {'template-observer-step': digest})
        # Reopen checks immutable original/preparation/root binding itself.
        self.chain = rr.RegistryReceiptChain.reopen(receipts, chain.operation_id,
            plan, self.receipt, {'template-observer-step': digest})
        try:
            self.observer = module('native_template_accepted_consumers')
        except ModuleNotFoundError as error:
            if error.name != package + '.native_template_accepted_consumers':
                raise
            self.fail('accepted-chain native Template observer is not implemented')

    async def observe(self, guard=lambda: None):
        return await self.observer.async_observe_accepted_template_consumers(
            self.f.hass, self.chain, self.evidence, guard=guard)

    async def check_root_events(self, kind):
        await self.chain_root(kind)
        if kind == 'yaml':
            candidate = self.evidence.template_candidate_sources
            self.assertIsNotNone(candidate)
            candidate.assert_original(self.receipt, self.evidence.template_sources)
            worker = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_validation_worker')
            state = "{{ states('sensor.accepted_next') | int(0) * 2 }}"
            expected = worker._fingerprint(['template.sensor', 'scope-value',
                ['id:scope', ['state_sensor_v1', state, 'Runtime consumer', None, None, None]]])
            self.assertEqual(next(row[2] for row in candidate.sources.rows
                if row[:2] == ('template.sensor', 'scope-value')), expected)
        for value in (5, 7):
            await self.emit(value)
            observed = await self.observe()
            row = self.consumer_row(observed)
            self.assertTrue(row.consumer_verified, row.reason)
            self.assertTrue(row.source_verified, row.reason)
            self.assertEqual(row.dependencies, (self.source_id,))
            self.assertEqual(row.registry_id, self.consumer_uuid)
            self.assertEqual(observed.head, self.chain.reconstruct_inputs().head)
            self.assertEqual(observed.yaml_position, 'original')
            self.assertEqual(observed.evidence_sha256, self.evidence.sha256)
            self.assertFalse(observed.migration_ready)
            self.assertFalse(observed.family_complete)

    async def test_yaml_real_producer_events_at_reopened_chain_root(self):
        await self.check_root_events('yaml')

    async def test_entry_real_producer_events_at_reopened_chain_root(self):
        await self.check_root_events('entry')

    async def test_arbitrary_dict_or_detached_input_state_is_not_chain_authority(self):
        await self.chain_root('yaml')
        for value in ({}, self.chain.reconstruct_inputs()):
            with self.assertRaises(ValueError):
                await self.observer.async_observe_accepted_template_consumers(
                    self.f.hass, value, self.evidence, guard=lambda: None)

    async def test_late_physical_source_edit_refuses_even_at_accepted_head(self):
        await self.chain_root('yaml')
        path = self.f.root / 'template.yaml'
        path.write_text(path.read_text() + '# foreign source\n')
        with self.assertRaises(ValueError):
            await self.observe()

    async def test_source_edit_during_last_native_pin_io_cannot_escape_final_fence(self):
        from unittest.mock import patch
        await self.chain_root('yaml')
        native = self.observer._Native
        actual = native.sources
        calls = 0
        path = self.f.root / 'template.yaml'
        def source_io(owner):
            nonlocal calls
            actual(owner)
            calls += 1
            if calls == 2:
                path.write_text(path.read_text() + '# late foreign source\n')
        # The real pin reader runs both times. Only a negative filesystem race
        # is injected; no positive output, registration or acceptance is faked.
        with patch.object(native, 'sources', source_io):
            with self.assertRaises(ValueError):
                await self.observe()
        self.assertEqual(calls, 2)
