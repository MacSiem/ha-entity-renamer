"""Actual native rename emission identity, not root authorization or ingress QA.

No filtering, constructed Event/State, handler call, fake clock or source result.
This component is a prerequisite for explicit pending/A/S; it grants no readiness.
"""
import asyncio
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeRenameEmissionTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    data = server.NativeTemplateYAMLServerTests.data
    stimulus = server.NativeTemplateYAMLServerTests.stimulus
    module = server.NativeTemplateYAMLServerTests.module
    _runtime = server.NativeTemplateYAMLServerTests._runtime

    async def test_actual_forward_inverse_binds_original_native_events_and_completed_tasks(self):
        await self.round_trip(reemit=False)

    async def test_foreign_same_dictionary_reemission_keeps_its_distinct_original_event(self):
        await self.round_trip(reemit=True)

    async def test_real_producer_change_between_prepare_and_mutate_remains_a_producer_event(self):
        await self.round_trip(reemit=False, producer_gap=True)

    async def test_unemitted_value_change_in_remove_hook_is_not_a_neutral_rename(self):
        await self.round_trip(reemit=False, producer_hook=True)

    async def round_trip(self, *, reemit, producer_gap=False, producer_hook=False):
        from homeassistant.core import callback
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        if producer_hook:
            from homeassistant.helpers.entity_component import DATA_INSTANCES
            class HookSource(type(self.producer)):
                async def async_will_remove_from_hass(source):
                    await super().async_will_remove_from_hass()
                    source._attr_native_value = 7
                    source.async_write_ha_state()
            hooked = HookSource()
            hooked._attr_unique_id = 'classifier-native-hook-source'
            hooked._attr_name = 'Classifier native hook source'
            await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([hooked])
            await hass.async_block_till_done()
            await self._flush_documents()
            old, new = hooked.entity_id, 'sensor.classifier_native_hook_next'
            uuids = {old: self.f.entity.async_get(old).id}
        # Import before any mutation. The observer must own the one genuine API
        # call, rather than infer causality from values/UUID after another call.
        classifier = self.module('native_rename_emission_classifier')
        native = self.module('native_registry')
        io = native.NativeRegistryIO(hass)
        bridge = native.NativeRegistryBridge(io, self.module('recorder_guard').NativeRecorderGuard(hass))
        change = await bridge.prepare('entity', uuids[old], {'entity_id': new}, lambda: None)
        received = []
        reemitted = set()
        @callback
        def collect(event):
            if event.data.get('entity_id') in (old, new):
                received.append(event)
                if reemit and id(event.data) not in reemitted:
                    reemitted.add(id(event.data))
                    # A real integration can emit another native bus Event
                    # with the same dictionary. This synthetic foreign input
                    # is not a replay of any private/denied operation or an
                    # invocation of a consumer handler.
                    hass.bus.async_fire_internal('state_changed', event.data, context=event.context)
        self.addCleanup(hass.bus.async_listen('state_changed', collect))
        owner = asyncio.current_task()
        def guard():
            self.assertIs(asyncio.current_task(), owner)
            self.assertIs(io.hass, hass)
        renamed = False
        mutation = None
        try:
            witness = await classifier.NativeRenameEmissions.prepare(io, change, change.after, guard=guard)
            self.addCleanup(witness.close)
            ordinary = []
            if producer_gap:
                before_events = len(received)
                await self.emit(7)
                ordinary = received[before_events:]
                self.assertTrue(ordinary)
                self.assertEqual(hass.states.get(old).state, '7')
            mutation = witness.mutate()
            renamed = True
            self.assertIs(type(mutation), native.NativeRegistryMutation)
            proof = await witness.finish()
            self.assertEqual((proof['rename_remove'], proof['rename_add']), (1, 1))
            self.assertTrue(proof['source_tasks_complete'])
            self.assertTrue(proof['dispatches_complete'])
            if not producer_hook:
                self.assertTrue(proof['neutral'], [(item.phase, item.semantic,
                    [(type(state.state).__name__, [(key, type(value).__name__, repr(value))
                      for key, value in state.attributes.items()])
                     for state in item.binding[1:] if state is not None] if item.binding else [])
                    for item in witness.emissions])
            own = [event for event in received if witness.is_owned_rename(event)]
            self.assertEqual(len(own), 2)
            self.assertTrue(all(not witness.is_owned_rename(event) for event in ordinary))
            if producer_hook:
                updates = [event for event in received
                           if event.data['old_state'] is not None and event.data['new_state'] is not None]
                # Native Entity ignores writes after entering REMOVED. The
                # physical value reaches HA only in final native re-add. It is
                # an owned rename emission, with a non-neutral value delta.
                self.assertEqual(updates, [])
                self.assertFalse(proof['neutral'])
            if reemit:
                self.assertEqual(len(received), 4)
                groups = {}
                for event in received:
                    groups.setdefault(id(event.data), []).append(event)
                self.assertEqual(len(groups), 2)
                for first, second in groups.values():
                    self.assertIsNot(first, second)
                    self.assertIs(first.data, second.data)
                    self.assertTrue(witness.is_owned_rename(first))
                    self.assertFalse(witness.is_owned_rename(second))
            removed = next(event for event in own if event.data['new_state'] is None)
            added = next(event for event in own if event.data['old_state'] is None)
            self.assertEqual((removed.data['entity_id'], added.data['entity_id']), (old, new))
            if producer_gap:
                self.assertEqual((removed.data['old_state'].state, added.data['new_state'].state), ('7', '7'))
            elif producer_hook:
                self.assertEqual((removed.data['old_state'].state, added.data['new_state'].state), ('1', '7'))
            self.assertIs(added.data['new_state'], hass.states.get(new))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        finally:
            # Restore through a separately prepared witness. Never repeat the
            # forward API call after uncertainty or construct a replacement row.
            if renamed and self.f.entity.async_get(new) is not None:
                from homeassistant.helpers.json import _orjson_bytes_default_encoder
                await io.settle(change)
                row = self.f.entity.async_get(new)
                self.assertEqual(_orjson_bytes_default_encoder(row.as_storage_fragment).decode('utf-8'),
                                 mutation.after_json, 'inverse needs exact confirmed owned effect')
                inverse = await classifier.NativeRenameEmissions.prepare(io, change, change.before, guard=guard)
                self.addCleanup(inverse.close)
                inverse.mutate()
                inverse_proof = await inverse.finish()
                self.assertEqual((inverse_proof['rename_remove'], inverse_proof['rename_add']), (1, 1))
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        await self.stimulus(11)
