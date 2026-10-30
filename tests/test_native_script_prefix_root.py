"""Actual whole Root Script prefix/config/actions/fresh inverse qualification."""
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeScriptPrefixRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_script_prefix_nested_actions_full_forward_and_fresh_inverse(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, _, original = await self.data(with_automation=False)
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        script_path = root / 'mixed_scripts.yaml'
        script_path.write_text('mixed_source:\n  variables:\n    source: ' + old +
            '\n    alias: "{{ source }}"\n  sequence:\n' +
            '    - if: "{{ states(' + repr(old) + ') | int(0) > 0 }}"\n' +
            '      then:\n        - event: mixed_yaml_script_result\n' +
            '          event_data:\n            value: "{{ states(alias) }}"\n' +
            '      else:\n        - event: mixed_yaml_script_result\n' +
            '          event_data: {value: empty}\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        original['mixed_scripts.yaml'] = script_path.read_bytes()
        await self.stimulus(17)
        events = []
        self.addCleanup(hass.bus.async_listen('mixed_yaml_script_result',
            lambda event: events.append(str(event.data['value']))))
        await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        self.assertEqual(events, ['17'])
        entity = next(iter(hass.data[DATA_INSTANCES]['script'].entities))
        script, raw, variables = entity.script, entity.raw_config, entity.script.variables
        service = hass.services._services['script']['mixed_source']
        source = self.producer
        self.assertTrue(script._if_data)
        await self._flush_documents()
        history = self.module('history_guard').NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass))
        before_history = await history.capture(mapping)
        def identities():
            self.assertIs(next(iter(hass.data[DATA_INSTANCES]['script'].entities)), entity)
            self.assertIs(entity.script, script)
            self.assertIs(entity.raw_config, raw)
            self.assertIs(script.variables, variables)
            self.assertIs(hass.services._services['script']['mixed_source'], service)
            self.assertIs(self.producer, source)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with mixed.native_diagnostics(self.module):
            result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
            identities()
            record = await runtime.journal.load(result['operation_id'])
            baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
            self.assertIs(type(composition), self.module('native_script_program_composition').ScriptTemplateStateComposition)
            original_ref, receipt_bytes = record.plan.native_inputs, baseline.receipt_json
            self.assertIs(composition.migration_ready, False)
            for previous, proposed in mapping.items():
                self.assertIsNone(self.f.entity.async_get(previous))
                self.assertEqual(self.f.entity.async_get(proposed).id, uuids[previous])
                self.assertEqual(source.entity_id, proposed)
            self.assertEqual(events, ['17'], 'rename must not execute any Script action')
            self.assertFalse(script._if_data, 'only idle native caches should be invalidated')
            await self.stimulus(13)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            self.assertEqual(events, ['17', '13'])
            self.assertTrue(script._if_data)
            reverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertEqual(reverse['state'], 'rolled_back')
            identities()
            reopened, restored = runtime.preparations.resolve_composition(record.plan.native_inputs)
            self.assertEqual((record.plan.native_inputs, reopened.receipt_json, restored),
                             (original_ref, receipt_bytes, composition))
            for path, contents in original.items():
                self.assertEqual((root / path).read_bytes(), contents)
            self.assertEqual(events, ['17', '13'])
            await self.stimulus(23)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            self.assertEqual(events, ['17', '13', '23'])
            identities()
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertEqual(source.entity_id, old)
            self.assertTrue(await history.verify(before_history))
            self.assertTrue(await history.verify(composition.consumer_history))
            self.assertFalse(runtime.operation_lock.locked())
