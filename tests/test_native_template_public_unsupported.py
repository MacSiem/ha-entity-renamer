"""Public admission must refuse concrete native dynamic/trigger dependencies."""
import unittest
import test_native_template_yaml_server as server

@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class PublicUnsupportedTemplateTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    data = server.NativeTemplateYAMLServerTests.data
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    async def _refusal(self, kind):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, _ = await self.data(variant='sensor')
        old = next(iter(mapping))
        if kind == 'dynamic':
            suffix = old.split('.', 1)[1]
            body = ('- unique_id: scope\n  sensor:\n  - unique_id: value\n'
                    '    name: Runtime consumer\n    state: "{{ (states(\'' + old + '\') | int(0)) + '
                    '(states(\'sensor.\' ~ \'' + suffix + '\') | int(0)) }}"\n')
        else:
            body = ('- unique_id: scope\n  triggers:\n  - trigger: state\n'
                    '    entity_id: ' + old + '\n  sensor:\n  - unique_id: value\n'
                    '    name: Runtime consumer\n    state: "{{ trigger.to_state.state }}"\n')
        path = self.f.root / 'template.yaml'
        path.write_text(body)
        await self.f.hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        consumer = next(e for p in async_get_platforms(self.f.hass, 'template')
                        for e in p.entities.values() if e.unique_id == 'scope-value')
        row = self.f.entity.async_get(consumer.entity_id)
        self.assertIsNotNone(row)
        before = path.read_bytes()
        self.producer._attr_native_value = 7
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()
        self.assertEqual(self.f.hass.states.get(consumer.entity_id).state, '14' if kind == 'dynamic' else '7')
        proposed = self.module('yaml_plan').plan_yaml(self.f.root, mapping)
        print('PUBLIC_PLAN_ISSUES', kind, [(i.code, i.line) for i in proposed.issues], flush=True)
        async def refresh():
            return None
        with self.assertRaises(self.module('transaction').Refused) as refused:
            await runtime.native_yaml_prepare(self.f.hass, mapping, actor_id='qa',
                                              guard=lambda: None, refresh=refresh)
        self.assertEqual(str(refused.exception), 'template_state_server_' +
                         ('template_requires_reference_parser' if kind == 'dynamic' else 'affected_unsupported_family'))
        print('PUBLIC_REFUSAL', kind, str(refused.exception), flush=True)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(mapping[old]))
        self.assertEqual(self.f.entity.async_get(consumer.entity_id).id, row.id)
        self.assertFalse(await runtime.journal.active())
        self.producer._attr_native_value = 9
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()
        self.assertEqual(self.f.hass.states.get(consumer.entity_id).state, '18' if kind == 'dynamic' else '9')

    async def test_dynamic_lookup_is_refused_without_effect(self):
        await self._refusal('dynamic')

    async def test_trigger_template_is_refused_without_effect(self):
        await self._refusal('trigger')
