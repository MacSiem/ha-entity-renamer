"""Real native full rows at the existing retained review's accepted receipt head."""
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeDependentConfigBindingTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_start = mixed.NativeTemplateMixedYAMLServerTests._before_start
    _flush_documents = mixed.NativeTemplateMixedYAMLServerTests._flush_documents
    prepare = mixed.NativeTemplateMixedYAMLServerTests.prepare
    emit = mixed.NativeTemplateMixedYAMLServerTests.emit
    _runtime = mixed.NativeTemplateMixedYAMLServerTests._runtime
    module = mixed.NativeTemplateMixedYAMLServerTests.module
    stimulus = mixed.NativeTemplateMixedYAMLServerTests.stimulus
    data = mixed.NativeTemplateMixedYAMLServerTests.data

    async def test_readonly_actual_chain_full_native_rows_and_foreign_slot_refusal(self):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.template import Template
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
            guard=lambda: None, refresh=refresh)
        plan = runtime.reviews[review['operation_id']].plan
        baseline, composition = runtime.preparations.resolve_composition(plan.native_inputs)
        bundle = await self.module('yaml_validation').async_validate_proposed_with_template_state_sources(
            hass, composition.yaml_plan, mapping, root.parent, with_state_program=True,
            with_dependent_program=True)
        self.assertEqual(bundle.receipt, baseline.receipt)
        self.assertEqual(bundle.evidence, composition.evidence)
        rr = self.module('native_registry_receipts')
        store = rr.native_registry_receipt_store(hass, runtime.preparations)
        await store.initialize()
        chain = rr.RegistryReceiptChain.reopen(store, review['operation_id'], plan, baseline.receipt,
                                               dict(composition.prepared_digests))
        self.assertFalse(chain.writable)
        binding = self.module('dependent_config_binding')
        observer = binding.NativeDependentConfigReadback(hass, bundle.dependent_config_program,
            bundle.evidence, bundle.receipt, composition.yaml_plan)
        before = observer.sample(chain)
        self.assertEqual(before.prefix.active, frozenset())
        self.assertEqual(observer.sample(chain), before)
        self.assertEqual({row.domain for row in before.registry_rows}, {'automation', 'script'})
        self.assertEqual({domain for domain, *_ in before.native_tokens}, {'automation', 'script'})
        script = next(iter(hass.data[DATA_INSTANCES]['script'].entities)).script
        event_data = script.sequence[0]['event_data']
        original = event_data['value']
        event_data['value'] = Template("{{ 'foreign native slot' }}", hass)
        try:
            with self.assertRaisesRegex(self.module('transaction').Refused, 'native_prefix_mismatch'):
                observer.sample(chain)
        finally:
            event_data['value'] = original
        self.assertEqual(observer.sample(chain), before)
        # A changed body cannot choose a later prefix; only receipt evidence can.
        self.assertEqual(binding.accepted_dependent_prefix(chain, bundle.dependent_config_program).active,
                         frozenset())
        self.assertIsNone(await runtime.journal.load(review['operation_id']))
        self.assertFalse(runtime.operation_lock.locked())
        # A complete native input read must also reject ordinary-source drift,
        # even when all Automation/Script rows still equal the expected image.
        source_id = next(iter(mapping))
        source = self.f.entity.async_get(source_id)
        original_name = source.name
        self.f.entity.async_update_entity(source_id, name='Synthetic dependent source drift')
        await self._flush_documents()
        try:
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                observer.sample(chain)
        finally:
            self.f.entity.async_update_entity(source_id, name=original_name)
            await self._flush_documents()
            self.assertEqual(self.f.entity.async_get(source_id).id, source.id)
            self.assertEqual(self.f.entity.async_get(source_id).name, original_name)
