"""Actual own-listener ingress; not Root admission or pre-cutover queue proof."""
import asyncio
import importlib
import sys
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

    async def wait_native_rate_idle(self):
        """Observe real native replay returns, including finite self echoes."""
        trackers = tuple(entity._template_result_info for entity in (self.consumer, self.control))
        code = trackers[0]._refresh.__func__.__code__
        monitor = sys.monitoring
        tool = next(key for key in range(6) if monitor.get_tool(key) is None)
        monitor.use_tool_id(tool, 'native domain control idle')
        wake = None
        def returned(code_arg, offset, result):
            frame = sys._getframe(1)
            if (frame.f_locals.get('replayed') and any(frame.f_locals.get('self') is tracker for tracker in trackers)
                    and wake is not None and not wake.done()):
                wake.set_result(None)
        monitor.register_callback(tool, monitor.events.PY_RETURN, returned)
        monitor.set_local_events(tool, code, monitor.events.PY_RETURN)
        try:
            while True:
                wake = self.f.hass.loop.create_future()
                handles = [handle for tracker in trackers for handle in tracker._rate_limit._rate_limit_timers.values()]
                if not handles:
                    return
                await asyncio.wait_for(wake, max([0] + [handle.when() - self.f.hass.loop.time()
                                                        for handle in handles]) + 5)
        finally:
            monitor.set_local_events(tool, code, 0)
            monitor.register_callback(tool, monitor.events.PY_RETURN, None)
            monitor.free_tool_id(tool)

    async def test_actual_entity_ingress_suppresses_neutral_rename_then_delivers_producer_change(self):
        await self.round_trip()

    async def test_queued_producer_event_reaches_old_tracker_before_cutover(self):
        await self.round_trip(preflight_gap=True)

    async def test_meaningful_value_first_published_by_native_readd_reaches_consumer(self):
        await self.round_trip(producer_hook=True)

    async def test_native_domain_add_keeps_bookkeeping_and_subsequent_producer_delivery(self):
        await self.round_trip(domain=True)

    async def test_conditional_dependency_keeps_active_branch_until_real_condition_change(self):
        await self.round_trip(conditional=True)

    async def test_handoff_entity_native_ingress_and_fresh_inverse(self):
        await self.round_trip(source_handoff=True)

    async def test_handoff_domain_native_ingress_and_fresh_inverse(self):
        await self.round_trip(source_handoff=True, domain=True)

    async def test_handoff_meaningful_domain_sum_has_no_duplicate_source_render(self):
        await self.round_trip(source_handoff=True, domain=True, hybrid=True)

    async def round_trip(self, *, preflight_gap=False, producer_hook=False, domain=False, conditional=False,
                         source_handoff=False, hybrid=False):
        await self.pair(domain=domain)
        if domain:
            # Isolate actual domain-add bookkeeping from bootstrap timers.
            await self.wait_native_rate_idle()
        if hybrid:
            from homeassistant.core import Context
            from homeassistant.helpers.entity_platform import async_get_platforms
            self.producer._attr_extra_state_attributes = {'renamer_ingress_group': 'subject'}
            self.other._attr_extra_state_attributes = {'renamer_ingress_group': 'control'}
            self.producer.async_write_ha_state()
            self.other.async_write_ha_state()
            blocks = []
            for source_id, key, group in ((self.old, 'scope-value', 'subject'),
                                         (self.other.entity_id, 'scope-control', 'control')):
                expression = ("{{ (states('" + source_id + "') | int(0)) + (states.sensor"
                    " | selectattr('attributes.renamer_ingress_group', 'defined')"
                    " | selectattr('attributes.renamer_ingress_group', 'eq', '" + group + "')"
                    " | map(attribute='state') | map('int', 0) | sum) }}")
                blocks.append('  - name: ' + key + '\n    unique_id: ' + key + '\n    state: "' + expression + '"\n')
            self.original = ('- sensor:\n' + ''.join(blocks)).encode()
            self.desired = self.original.replace(self.old.encode(), self.new.encode())
            self.path.write_bytes(self.original)
            await self.f.hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
            await self.f.hass.async_block_till_done()
            entities = {entity.unique_id: entity for platform in async_get_platforms(self.f.hass, 'template')
                        if platform.domain == 'sensor' for entity in platform.entities.values()}
            self.consumer, self.control = entities['scope-value'], entities['scope-control']
            await self.emit(self.other, 29)
            await self.wait_native_rate_idle()
            self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, '2')
            self.assertEqual(self.f.hass.states.get(self.control.entity_id).state, '58')
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
        if conditional:
            from homeassistant.core import Context
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            from homeassistant.helpers.entity_platform import async_get_platforms
            branches = []
            for label, value in (('a', 3), ('b', 7)):
                source = type(self.producer)()
                source._attr_unique_id = 'ingress-conditional-' + label
                source._attr_name = 'Ingress conditional ' + label
                source._attr_native_value = value
                branches.append(source)
            await self.f.hass.data[DATA_INSTANCES]['sensor'].async_add_entities(branches)
            branch_a, branch_b = branches
            for source_id in (self.old, self.other.entity_id):
                before = "{{ states('" + source_id + "') | int(0) * 2 }}"
                after = ("{{ states('" + branch_a.entity_id + "') | int(0) if states('" + source_id
                         + "') | int(0) > 0 else states('" + branch_b.entity_id + "') | int(0) }}")
                self.assertIn(before.encode(), self.original)
                self.original = self.original.replace(before.encode(), after.encode())
            self.desired = self.original.replace(self.old.encode(), self.new.encode())
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
            handoff = None
            if source_handoff:
                handoff = await self.module_named('native_source_handoff').NativeSourceHandoff.prepare(
                    io, change, target, guard=guard)
                self.addCleanup(handoff.close)
            witness = await classifier.NativeRenameEmissions.prepare(io, change, target, guard=guard,
                source_handoff=handoff)
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
            outputs = []
            from homeassistant.core import callback
            @callback
            def native_output(event):
                if event.data['entity_id'] == self.consumer.entity_id and event.data['new_state'] is not None:
                    outputs.append(event.data['new_state'].state)
            remove_output = self.f.hass.bus.async_listen('state_changed', native_output)
            try:
                transport.mark_registry_attempt(context)
                if hybrid and source_id == self.old:
                    self.producer._attr_native_value = 29
                mutation = witness.mutate()
                proof = await witness.finish()
            finally:
                remove_output()
                ingress.retire()
            from homeassistant.helpers.json import _orjson_bytes_default_encoder
            row = self.f.entity.async_get(target_id)
            self.assertEqual(_orjson_bytes_default_encoder(row.as_storage_fragment).decode('utf-8'),
                             mutation.after_json, 'inverse requires the full confirmed owned row')
            forward = source_id == self.old
            changed = (producer_hook or hybrid) and source_id == self.old
            self.assertEqual(proof['neutral'], not changed)
            if not changed and not domain:
                self.assertGreater(ingress.suppressed, 0)
            self.assertFalse(ingress.live)
            self.assertIs(self.module._Runtime(self.f.hass).graph(self.consumer),
                          self.consumer._template_result_info)
            if changed:
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, '58' if hybrid else '14')
                if hybrid:
                    self.assertEqual(outputs, ['58'])
                    self.assertGreater(ingress.deferred_count, 0)
                    self.assertEqual(ingress.deferred, [])
            else:
                self.assertIs(self.f.hass.states.get(self.consumer.entity_id), physical)
            self.assertEqual(self.producer.entity_id, target_id)
            self.assertEqual(self.f.entity.async_get(target_id).id, self.source_uuid)
            self.assertIsNone(self.f.hass.states.get(source_id))

        try:
            await move(self.old, self.new, change.after, self.original, self.desired)
            if conditional:
                await self.emit(branch_a, 5)
                self.assertEqual(self.f.hass.states.get(self.control.entity_id).state, '5')
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, '5')
                await self.emit(branch_b, 9)
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, '5')
                for source in (self.producer, self.other):
                    await self.emit(source, 0)
                self.assertTrue(all(self.f.hass.states.get(entity.entity_id).state == '9'
                                    for entity in (self.consumer, self.control)))
                held = [self.f.hass.states.get(entity.entity_id) for entity in (self.consumer, self.control)]
                await self.emit(branch_a, 11)
                self.assertTrue(all(self.f.hass.states.get(entity.entity_id) is state
                                    for entity, state in zip((self.consumer, self.control), held)))
                await self.emit(branch_b, 13)
                self.assertTrue(all(self.f.hass.states.get(entity.entity_id).state == '13'
                                    for entity in (self.consumer, self.control)))
            for value in (() if conditional else (7, 11)):
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
                if domain:
                    # Broad native controls also hear their own output. Let
                    # the real finite self replay finish before the next input;
                    # coalescing into that Event exercises Core's loop limiter.
                    await self.wait_native_rate_idle()
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state, str(value * 2))
                self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state,
                                 self.f.hass.states.get(self.control.entity_id).state)
        finally:
            if forward:
                await move(self.new, self.old, change.before, self.desired, self.original)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.f.entity.async_get(self.old).id, self.source_uuid)
        self.assertIsNone(self.f.entity.async_get(self.new))
