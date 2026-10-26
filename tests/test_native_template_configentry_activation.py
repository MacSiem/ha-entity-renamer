"""Real numeric Template ConfigEntry activation-scope RED; owner runs FIFO.

This first prerequisite has no YAML changes and needs no YAML ValidationReceipt.
Actual producer/consumer, native source binding, immutable preparations and
Recorder history precede the ordinary product lease request. No lifecycle,
Coordinator migration, accepted chain or fresh-reverse completion is claimed.
"""
import importlib
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
            unique_id='template-entry-activation', pref_disable_polling=True)
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

        # First missing real prerequisite: existing native lease refuses domain
        # template, before any unload/write. No fake receipt, expectedFailure,
        # assertRaises or invented future API turns that refusal into a PASS.
        lease_api = module('native_helper_activation_lease')
        async with await lease_api.NativeHelperActivationLease.acquire(
                hass, hass.config_entries, (entry,), guard=lambda: None) as lease:
            self.assertEqual(lease.entry_ids, (entry.entry_id,))
            self.assertIs(lease.manager, hass.config_entries)
            self.assertTrue(lease.owns_entry(entry))
        self.assertFalse(entry.setup_lock.locked())

    def _same_native_documents(self, before, after):
        self.assertEqual(set(before), set(after))
        for key in before:
            self.assertEqual(json.loads(before[key]), json.loads(after[key]), key)


if __name__ == '__main__':
    unittest.main()
