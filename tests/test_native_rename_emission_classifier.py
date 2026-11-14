"""Actual native rename emission identity, not root authorization or ingress QA.

No filtering, constructed Event/State, handler call, fake clock or source result.
This component is a prerequisite for explicit pending/A/S; it grants no readiness.
"""
import asyncio
from types import SimpleNamespace
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'installed dependencies required')
class SemanticSnapshotTests(unittest.TestCase):
    def snapshot(self, attributes):
        import importlib
        module = importlib.import_module(server.activation.fixture.fixtures.PACKAGE
                                        + '.native_rename_emission_classifier')
        return module._semantic_state(SimpleNamespace(state='1', attributes=attributes))

    def test_source_foreign_metaclass_hooks_are_never_executed(self):
        calls = []
        class ForeignMeta(type):
            def __eq__(cls, other):
                calls.append('metaclass equality')
                raise AssertionError('snapshot compared a foreign type')
            def __getattribute__(cls, name):
                calls.append('metaclass attribute')
                raise AssertionError('snapshot inspected a foreign type')
        class Foreign(metaclass=ForeignMeta):
            pass
        self.assertIsNone(self.snapshot({'fixture': Foreign()}))
        self.assertEqual(calls, [])

    def test_source_pretend_primitive_never_retains_live_foreign_reference(self):
        class PretendMeta(type):
            def __eq__(cls, other):
                return True
        class MutableValue(metaclass=PretendMeta):
            value = 1
        value = MutableValue()
        before = self.snapshot({'fixture': value})
        value.value = 7
        after = self.snapshot({'fixture': value})
        self.assertIsNone(before)
        self.assertIsNone(after)

    def test_source_unknown_key_class_hook_is_never_executed(self):
        calls = []
        class UnknownKey:
            @property
            def __class__(self):
                calls.append('class hook')
                raise AssertionError('snapshot accessed foreign key class hook')
        self.assertIsNone(self.snapshot({UnknownKey(): 'fixture'}))
        self.assertEqual(calls, [])

    def test_source_signed_float_zero_is_a_meaningful_attribute_change(self):
        self.assertNotEqual(self.snapshot({'fixture': 0.0}), self.snapshot({'fixture': -0.0}))

    def test_source_template_visible_dictionary_order_and_native_key_type_are_preserved(self):
        with self.subTest('dictionary iteration order'):
            self.assertNotEqual(self.snapshot({'a': 1, 'b': 2}), self.snapshot({'b': 2, 'a': 1}))
        from homeassistant import const
        key_type = vars(const).get('EntityStateAttribute')
        if key_type is not None:
            with self.subTest('native enum key type'):
                import importlib
                module = importlib.import_module(server.activation.fixture.fixtures.PACKAGE
                                                + '.native_rename_emission_classifier')
                native = module._semantic_state(SimpleNamespace(state='1', attributes={
                    key_type.FRIENDLY_NAME: 'fixture'}), native_attribute_key=key_type)
                plain = self.snapshot({'friendly_name': 'fixture'})
                self.assertIsNotNone(native)
                self.assertNotEqual(native, plain)

    def test_foreign_string_key_lookup_semantics_cannot_be_promoted_to_neutral(self):
        import importlib
        module = importlib.import_module(server.activation.fixture.fixtures.PACKAGE
                                        + '.native_rename_emission_classifier')
        class ForeignKey(str):
            def __hash__(self):
                return str.__hash__(self) + 1
        attributes = {ForeignKey('friendly_name'): 'fixture'}
        self.assertIsNone(attributes.get('friendly_name'))
        self.assertIsNone(module._semantic_state(SimpleNamespace(state='1', attributes=attributes)))

    def test_source_snapshot_never_rehashes_or_compares_foreign_attribute_keys(self):
        import importlib
        module = importlib.import_module(server.activation.fixture.fixtures.PACKAGE
                                        + '.native_rename_emission_classifier')
        calls = []
        armed = False
        class StringKey(str):
            def __hash__(self):
                if armed:
                    calls.append('hash')
                    raise AssertionError('snapshot invoked key hash')
                return str.__hash__(self)
            def __eq__(self, other):
                if armed:
                    calls.append('eq')
                    raise AssertionError('snapshot invoked key comparison')
                return str.__eq__(self, other)
            def __str__(self):
                raise AssertionError('snapshot invoked key conversion')
        key = StringKey('friendly_name')
        attributes = {key: 'fixture'}
        armed = True
        self.assertIsNone(module._semantic_state(SimpleNamespace(state='1', attributes=attributes)))
        self.assertEqual(calls, [])
        armed = False
        class UnsupportedKey:
            def __hash__(self):
                if armed:
                    calls.append('unsupported hash')
                    raise AssertionError('snapshot invoked unsupported key hash')
                return 1
        attributes = {UnsupportedKey(): 'fixture'}
        armed = True
        self.assertIsNone(module._semantic_state(SimpleNamespace(state='1', attributes=attributes)))
        self.assertEqual(calls, [])


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

    async def test_handoff_mode_original_native_events_and_fresh_inverse(self):
        await self.round_trip(reemit=False, source_handoff=True)

    async def test_handoff_mode_foreign_same_dictionary_stays_foreign(self):
        await self.round_trip(reemit=True, source_handoff=True)

    async def test_handoff_mode_real_producer_before_api_is_not_rename(self):
        await self.round_trip(reemit=False, source_handoff=True, producer_gap=True)

    async def test_handoff_mode_unemitted_physical_value_is_meaningful(self):
        await self.round_trip(reemit=False, source_handoff=True, unemitted=29)

    async def round_trip(self, *, reemit, producer_gap=False, producer_hook=False,
                         source_handoff=False, unemitted=None):
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
        early = []
        witness = None
        reemitted = set()
        @callback
        def collect(event):
            if event.data.get('entity_id') in (old, new):
                received.append(event)
                if witness is not None and witness.is_owned_rename(event):
                    early.append((event, witness.suppresses_template_boundary(event)))
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
            handoff = None
            if source_handoff:
                handoff = await self.module('native_source_handoff').NativeSourceHandoff.prepare(
                    io, change, change.after, guard=guard)
                self.addCleanup(handoff.close)
            witness = await classifier.NativeRenameEmissions.prepare(io, change, change.after, guard=guard,
                source_handoff=handoff)
            self.addCleanup(witness.close)
            ordinary = []
            if producer_gap:
                before_events = len(received)
                await self.emit(7)
                ordinary = received[before_events:]
                self.assertTrue(ordinary)
                self.assertEqual(hass.states.get(old).state, '7')
            if unemitted is not None:
                self.producer._attr_native_value = unemitted
            mutation = witness.mutate()
            renamed = True
            self.assertIs(type(mutation), native.NativeRegistryMutation)
            try:
                proof = await witness.finish()
            except BaseException:
                if source_handoff:
                    print('HANDOFF_CLASSIFIER_DIAGNOSTIC', {
                        'phases': [(item.phase, item.event is not None, item.dispatched) for item in witness.emissions],
                        'source_runs': witness.source_runs,
                        'tasks': [(task.done(), record[2]) for task, record in witness.tasks.items()],
                        'pending': len(witness.pending), 'dispatches': len(witness.dispatches),
                        'constructors': len(witness.constructors), 'keyed': len(witness.keyed)})
                raise
            self.assertEqual((proof['rename_remove'], proof['rename_add']), (1, 1))
            self.assertTrue(proof['source_tasks_complete'])
            self.assertTrue(proof['dispatches_complete'])
            if not producer_hook and unemitted is None:
                self.assertTrue(proof['neutral'])
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
            if source_handoff:
                self.assertEqual(next(value for event, value in early if event is added), unemitted is None)
                self.assertIs(own[0], added)  # genuine ADD precedes structural REMOVE
            if unemitted is not None:
                self.assertFalse(proof['neutral'])
                self.assertEqual(added.data['new_state'].state, str(unemitted))
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
                reverse_handoff = None
                if source_handoff:
                    reverse_handoff = await self.module('native_source_handoff').NativeSourceHandoff.prepare(
                        io, change, change.before, guard=guard)
                    self.addCleanup(reverse_handoff.close)
                inverse = await classifier.NativeRenameEmissions.prepare(io, change, change.before, guard=guard,
                    source_handoff=reverse_handoff)
                self.addCleanup(inverse.close)
                inverse.mutate()
                inverse_proof = await inverse.finish()
                self.assertEqual((inverse_proof['rename_remove'], inverse_proof['rename_add']), (1, 1))
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        await self.stimulus(11)
