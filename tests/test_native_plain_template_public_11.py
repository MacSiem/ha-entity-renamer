"""Ordinary retained review with a real Template consumer and no Powercalc."""
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativePlainTemplatePublicTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    data = server.NativeTemplateYAMLServerTests.data
    stimulus = server.NativeTemplateYAMLServerTests.stimulus

    async def test_retained_ordinary_template_apply_and_fresh_inverse(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        hass = self.f.hass
        self.assertNotIn('powercalc', hass.config.components)
        old, new = next(iter(mapping.items()))

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='ordinary-template-admin',
                                                   guard=lambda: None, refresh=refresh)
        op, fingerprint = review['operation_id'], review['fingerprint']
        retained = runtime.reviews[op].plan
        self.assertEqual(review['entity_count'], 1)
        result = await runtime.native_yaml_apply(hass, op, fingerprint, actor_id='ordinary-template-admin',
                                                 guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied', result)
        self.assertEqual((await runtime.journal.load(op)).plan, retained)
        self.assertIsNone(self.f.entity.async_get(old))
        self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        await self.stimulus(7)
        inspected = await runtime.recovery_inspect(hass, op, fingerprint, guard=lambda: None, refresh=refresh)
        self.assertTrue(inspected['eligible'], inspected)
        inverse = await runtime.recovery_restore(hass, op, fingerprint, guard=lambda: None, refresh=refresh)
        self.assertEqual(inverse['state'], 'rolled_back', inverse)
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        await self.stimulus(11)
