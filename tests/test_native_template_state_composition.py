"""Owner FIFO: actual full source validation and durable SAME ORIGINAL reopen.

This tests the private evidence codec only, never consumer/writer admission.
"""
import copy
import importlib
import json
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateStateCompositionTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    data = server.NativeTemplateYAMLServerTests.data
    _runtime = server.NativeTemplateYAMLServerTests._runtime

    def module(self, name):
        return importlib.import_module(server.activation.fixture.fixtures.PACKAGE + '.' + name)

    async def test_full_source_composition_reopens_same_original_and_rejects_tampering(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, _ = await self.data(variant='sensor')
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        path = root / 'template.yaml'
        path.write_text('- unique_id: scope\n  sensor:\n  - unique_id: value\n'
            '    name: Runtime consumer\n'
            '    state: "{% set v = states(\'' + old + '\') | int(0) %}{{ v * 2 }}"\n'
            '    attributes:\n'
            '      minute: "{{ now().minute }}"\n'
            '      memory: "{{ this.attributes.get(\'memory\', 0) + 1 }}"\n')
        await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        native_before = await self._flush_documents(initialize=True)
        original_yaml = path.read_bytes()
        planner, validation = self.module('yaml_plan'), self.module('yaml_validation')
        yaml = planner.plan_yaml(root, mapping)
        self.assertTrue(yaml.ready, yaml.issues)
        bundle = await validation.async_validate_proposed_with_template_state_sources(
            hass, yaml, mapping, root.parent, with_state_program=True)
        baseline_module = self.module('native_input_baseline')
        baseline = await baseline_module.async_capture_native_input_baseline(
            hass, bundle.receipt, guard=lambda: None)
        nr, prepared = self.module('native_registry'), self.module('prepared')
        recorder = self.module('recorder_guard').NativeRecorderGuard(hass)
        history = self.module('history_guard').NativeHistoryGuard(recorder)
        change = await nr.NativeRegistryBridge(nr.NativeRegistryIO(hass), recorder).prepare(
            'entity', uuids[old], {'entity_id': new}, lambda: None)
        store = prepared.native_prepared_store(hass)
        await store.initialize()
        digest = await store.create('state-source-registry', prepared.encode_change(change))
        consumers = {e.entity_id: e.entity_id for platform in async_get_platforms(hass, 'template')
                     for e in platform.entities.values()}
        source_history = await history.capture(mapping)
        consumer_history = await history.capture(consumers)
        # Product class missing in RED. No test-built positive evidence/Plan.
        state = self.module('native_template_state_composition')
        composition = state.TemplateStateComposition(bundle.evidence,
            bundle.original_sources, bundle.candidate_sources, bundle.state_config_program, yaml,
            tuple(sorted(mapping.items())), (('state-source-registry', digest),),
            source_history, consumer_history, bundle.state_config_program.sha256)
        reference = await store.create_native_inputs('state-source-original', baseline,
                                                     composition=composition)
        original_digest, original_receipt = baseline.digest, baseline.receipt_json
        codec = self.module('native_template_composition')
        encoded = codec.encode_composition(composition, baseline)
        del store, composition, baseline
        reopened = prepared.native_prepared_store(hass)
        await reopened.initialize()
        actual, restored = reopened.resolve_composition(reference)
        self.assertIs(type(restored), state.TemplateStateComposition)
        self.assertEqual((actual.digest, actual.receipt_json), (original_digest, original_receipt))
        self.assertEqual(codec.encode_composition(restored, actual), encoded)
        self.assertFalse(restored.migration_ready)
        self.assertFalse(actual.receipt.ready_for_apply)
        self.assertTrue(await history.verify(restored.source_history))
        self.assertTrue(await history.verify(restored.consumer_history))
        for key in ('mapping', 'original_sources', 'candidate_sources', 'yaml_plan'):
            altered = copy.deepcopy(encoded)
            if key == 'mapping':
                altered[key][0][1] = 'sensor.unapproved_target'
            elif key == 'yaml_plan':
                altered[key]['fields']['patches'][0]['fields']['proposed_bytes'] = 'dGFtcGVy'
            else:
                field = altered[key]['fields']
                if key == 'candidate_sources':
                    field = field['sources']['fields']
                raw = json.loads(__import__('base64').b64decode(field['native_json']))
                raw['reference_proof']['mapping'][0][1] = 'sensor.unapproved_target'
                field['native_json'] = __import__('base64').b64encode(json.dumps(raw).encode()).decode()
            with self.assertRaises(ValueError, msg=key):
                codec.decode_composition(altered, actual)
        self.assertEqual(path.read_bytes(), original_yaml)
        self.assertEqual({m._store.key: __import__('pathlib').Path(m._store.path).read_bytes()
                          for m in self.f.managers}, native_before)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        validation.assert_validation_current(bundle.receipt)
