"""Actual own-listener ingress; not Root admission or pre-cutover queue proof."""
import asyncio
import importlib
import unittest

import test_native_template_binding_transport as binding


@unittest.skipUnless(binding.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateRenameIngressTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = binding.NativeBindingLifecycleTests.asyncSetUp
    _before_start = binding.NativeBindingLifecycleTests._before_start
    _flush_documents = binding.NativeBindingLifecycleTests._flush_documents
    prepare = binding.NativeBindingLifecycleTests.prepare
    pair = binding.NativeBindingLifecycleTests.pair
    emit = binding.NativeBindingLifecycleTests.emit

    def module_named(self, name):
        return importlib.import_module(binding.server.activation.fixture.fixtures.PACKAGE + '.' + name)

    async def test_actual_entity_ingress_suppresses_neutral_rename_then_delivers_producer_change(self):
        await self.round_trip()

    async def round_trip(self):
        await self.pair()
        await self._flush_documents()
        native = self.module_named('native_registry')
        classifier = self.module_named('native_rename_emission_classifier')
        ingress_module = self.module_named('native_template_rename_ingress')
        io = native.NativeRegistryIO(self.f.hass)
        bridge = native.NativeRegistryBridge(io, self.module_named('recorder_guard').NativeRecorderGuard(self.f.hass))
        change = await bridge.prepare('entity', self.source_uuid, {'entity_id': self.new}, lambda: None)
        owner = asyncio.current_task()
        def guard():
            self.assertIs(asyncio.current_task(), owner)

        async def move(source_id, target_id, target, before, desired):
            witness = await classifier.NativeRenameEmissions.prepare(io, change, target, guard=guard)
            self.addCleanup(witness.close)
            pairs = tuple(self.module.BindingSourcePair(template, template.template,
                template.template.replace(source_id, target_id),
                (str(self.path), 'scope-value', tuple(slot._attribute for slot in slots)))
                for template, slots in self.consumer._template_attrs.items())
            def fence(phase, sources, entities):
                row = self.f.entity.async_get(source_id)
                return (row is not None and row.id == self.source_uuid and sources == pairs
                        and entities == (self.consumer,) and self.path.read_bytes() in (before, desired))
            context = self.module._BindingCallerContext(owner, fence)
            transport = self.module.NativeTemplateBindingTransport.capture(self.f.hass, (self.consumer,),
                source_pairs=pairs, caller_context=context)
            physical = self.f.hass.states.get(self.consumer.entity_id)
            self.path.write_bytes(desired)
            transport.install_before_registry(context)
            ingress = ingress_module.NativeTemplateRenameIngress.install(transport, witness, context)
            try:
                transport.mark_registry_attempt(context)
                witness.mutate()
                proof = await witness.finish()
            finally:
                ingress.retire()
            self.assertTrue(proof['neutral'])
            self.assertGreater(ingress.suppressed, 0)
            self.assertFalse(ingress.live)
            self.assertIs(self.module._Runtime(self.f.hass).graph(self.consumer),
                          self.consumer._template_result_info)
            self.assertIs(self.f.hass.states.get(self.consumer.entity_id), physical)
            self.assertEqual(self.producer.entity_id, target_id)
            self.assertEqual(self.f.entity.async_get(target_id).id, self.source_uuid)
            self.assertIsNone(self.f.hass.states.get(source_id))

        forward = False
        try:
            await move(self.old, self.new, change.after, self.original, self.desired)
            forward = True
            for value in (7, 11):
                await self.emit(self.producer, value)
                await self.emit(self.other, value)
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, str(value * 2))
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state,
                                 self.f.hass.states.get(self.control.entity_id).state)
        finally:
            if forward:
                await move(self.new, self.old, change.before, self.desired, self.original)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.f.entity.async_get(self.old).id, self.source_uuid)
        self.assertIsNone(self.f.entity.async_get(self.new))
