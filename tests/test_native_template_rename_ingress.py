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

    async def test_queued_producer_event_reaches_old_tracker_before_cutover(self):
        await self.round_trip(preflight_gap=True)

    async def test_meaningful_value_first_published_by_native_readd_reaches_consumer(self):
        await self.round_trip(producer_hook=True)

    async def test_native_domain_add_keeps_bookkeeping_and_subsequent_producer_delivery(self):
        await self.round_trip(domain=True)

    async def round_trip(self, *, preflight_gap=False, producer_hook=False, domain=False):
        await self.pair(domain=domain)
        if producer_hook:
            from homeassistant.core import Context
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            from homeassistant.helpers.entity_platform import async_get_platforms
            class HookSource(type(self.producer)):
                change_on_remove = True
                async def async_will_remove_from_hass(source):
                    await super().async_will_remove_from_hass()
                    if source.change_on_remove:
                        source.change_on_remove = False
                        source._attr_native_value = 7
                        source.async_write_ha_state()
            source = HookSource()
            source._attr_unique_id = 'ingress-hook-native-source'
            source._attr_name = 'Ingress hook native source'
            await self.f.hass.data[DATA_INSTANCES]['sensor'].async_add_entities([source])
            self.original = self.original.replace(self.old.encode(), source.entity_id.encode())
            self.old, self.producer = source.entity_id, source
            self.desired = self.original.replace(self.old.encode(), self.new.encode())
            self.source_uuid = self.f.entity.async_get(self.old).id
            self.path.write_bytes(self.original)
            await self.f.hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
            await self.f.hass.async_block_till_done()
            entities = {e.unique_id: e for p in async_get_platforms(self.f.hass, 'template')
                        if p.domain == 'sensor' for e in p.entities.values()}
            self.consumer, self.control = entities['scope-value'], entities['scope-control']
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

        forward = False
        async def move(source_id, target_id, target, before, desired):
            nonlocal forward
            witness = await classifier.NativeRenameEmissions.prepare(io, change, target, guard=guard)
            self.addCleanup(witness.close)
            delivery = ingress_module.NativeTemplateProducerDelivery(
                self.module._Runtime(self.f.hass), (self.consumer,), witness)
            if preflight_gap and source_id == self.old:
                from homeassistant.const import __version__
                self.producer._attr_native_value = 5
                self.producer.async_write_ha_state()
                if __version__ == '2025.6.0':
                    self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, '2')
                    self.assertTrue(delivery.pending())
            await delivery.drain()
            if preflight_gap and source_id == self.old:
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, '10')
            pairs = tuple(self.module.BindingSourcePair(template, template.template,
                template.template.replace(source_id, target_id),
                (str(self.path), 'scope-value', tuple(slot._attribute for slot in slots)))
                for template, slots in self.consumer._template_attrs.items())
            def fence(phase, sources, entities):
                row = self.f.entity.async_get(source_id)
                return (row is not None and row.id == self.source_uuid and sources == pairs
                        and entities == (self.consumer,) and self.path.read_bytes() in (before, desired))
            context = self.module._BindingCallerContext(owner, fence)
            delivery.retire_at_final_fence()
            transport = self.module.NativeTemplateBindingTransport.capture(self.f.hass, (self.consumer,),
                source_pairs=pairs, caller_context=context, rename_witness=witness)
            physical = self.f.hass.states.get(self.consumer.entity_id)
            self.path.write_bytes(desired)
            transport.install_before_registry(context)
            ingress = ingress_module.NativeTemplateRenameIngress.install(transport, witness, context)
            try:
                transport.mark_registry_attempt(context)
                mutation = witness.mutate()
                proof = await witness.finish()
            finally:
                ingress.retire()
            from homeassistant.helpers.json import _orjson_bytes_default_encoder
            row = self.f.entity.async_get(target_id)
            self.assertEqual(_orjson_bytes_default_encoder(row.as_storage_fragment).decode('utf-8'),
                             mutation.after_json, 'inverse requires the full confirmed owned row')
            forward = source_id == self.old
            changed = producer_hook and source_id == self.old
            self.assertEqual(proof['neutral'], not changed)
            if not changed and not domain:
                self.assertGreater(ingress.suppressed, 0)
            self.assertFalse(ingress.live)
            self.assertIs(self.module._Runtime(self.f.hass).graph(self.consumer),
                          self.consumer._template_result_info)
            if changed:
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, '14')
            else:
                self.assertIs(self.f.hass.states.get(self.consumer.entity_id), physical)
            self.assertEqual(self.producer.entity_id, target_id)
            self.assertEqual(self.f.entity.async_get(target_id).id, self.source_uuid)
            self.assertIsNone(self.f.hass.states.get(source_id))

        try:
            await move(self.old, self.new, change.after, self.original, self.desired)
            for value in (7, 11):
                from homeassistant.core import callback
                fired = self.f.hass.loop.create_future()
                @callback
                def actual_outputs(event):
                    if (not fired.done() and all(self.f.hass.states.get(entity.entity_id).state == str(value * 2)
                                                for entity in (self.consumer, self.control))):
                        fired.set_result(None)
                remove = self.f.hass.bus.async_listen('state_changed', actual_outputs)
                try:
                    await self.emit(self.producer, value)
                    await self.emit(self.other, value)
                    deadlines = [handle.when() for entity in (self.consumer, self.control)
                                 for handle in entity._template_result_info._rate_limit._rate_limit_timers.values()]
                    # Domain native controls may rate-limit for one second.
                    # Await their real output events, never invent immediate
                    # delivery or advance a clock/call a native handler.
                    await asyncio.wait_for(fired, max([0] + [deadline - self.f.hass.loop.time()
                                                           for deadline in deadlines]) + 5)
                except TimeoutError:
                    self.fail({'value': value, 'groups': [
                        {'state': self.f.hass.states.get(entity.entity_id).state,
                         'listeners': entity._template_result_info.listeners,
                         'infos': [(sorted(info.entities), sorted(info.domains), sorted(info.domains_lifecycle),
                                    info.all_states, info.all_states_lifecycle)
                                   for info in entity._template_result_info._info.values()],
                         'timers': [(handle.when() - self.f.hass.loop.time(), handle._args[0].data['entity_id'],
                                     handle._args[0].data['old_state'] is None,
                                     handle._args[0].data['new_state'] is None)
                                    for handle in entity._template_result_info._rate_limit._rate_limit_timers.values()]}
                        for entity in (self.consumer, self.control)]})
                finally:
                    remove()
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, str(value * 2))
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state,
                                 self.f.hass.states.get(self.control.entity_id).state)
        finally:
            if forward:
                await move(self.new, self.old, change.before, self.desired, self.original)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.f.entity.async_get(self.old).id, self.source_uuid)
        self.assertIsNone(self.f.entity.async_get(self.new))
