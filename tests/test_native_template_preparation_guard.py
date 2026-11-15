"""Actual server preparation; negative in-memory drift has zero native effects."""
import unittest
import test_native_template_yaml_server as server

@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativePreparationGuardTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    data = server.NativeTemplateYAMLServerTests.data

    async def refuse(self, variant):
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        factory = self.module('native_template_yaml_server').NativeTemplateYAMLServer
        def files():
            return {m._store.key: __import__('pathlib').Path(m._store.path).read_bytes()
                    for m in self.f.managers}
        before = files()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        async with runtime.operation_lock:
            session = await factory.prepare(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
            try:
                session._scope()
                if variant == 'stored_payload':
                    reference = session.plan.native_inputs
                    session.preparations.plans[reference[0]]['payload']['codec'] += 'A'
                elif variant == 'composition':
                    session.composition.source_history['content']['states']['sha256'] = '0' * 64
                else:
                    item = session.chain.baseline.files[0]
                    object.__setattr__(item, 'manager_json', item.manager_json + b' ')
                with self.assertRaises((ValueError, self.module('transaction').Refused)):
                    session._scope()
                self.assertEqual(before, files())
                self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
                for old in mapping:
                    self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertEqual(await runtime.journal.all(), ())
            finally:
                session.close()
        self.assertFalse(runtime.operation_lock.locked())

    async def test_full_prepared_payload_hash_is_checked_before_effect(self):
        await self.refuse('stored_payload')

    async def test_mutable_composition_is_checked_before_effect(self):
        await self.refuse('composition')

    async def test_retained_original_manager_bytes_are_checked_before_effect(self):
        await self.refuse('baseline_bytes')
