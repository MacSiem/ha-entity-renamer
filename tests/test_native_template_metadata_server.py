"""Owner-run native metadata server flow on both pinned HA Cores.

Only synthetic YAML/producers. The actual server owns ORIGINAL/Plan/preparations.
This module does not seed consumers, states, clocks, receipts or positive proofs.
"""
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateMetadataServerTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    flow = server.NativeTemplateYAMLServerTests.flow

    async def data(self, **kwargs):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, original = await server.NativeTemplateYAMLServerTests.data(self, **kwargs)
        self.assertFalse(hasattr(self, 'plan'))
        self.assertFalse(hasattr(self, 'receipt'))
        self.assertFalse(hasattr(self, 'evidence'))
        self.mapping = mapping
        source = self.source_id
        lines = []
        for line in original.decode().splitlines(keepends=True):
            lines.append(line)
            if line.startswith('    state:'):
                lines.extend([
                    *(['    availability: "{{ not is_state(\'' + source + '\', \'-1\') }}"\n']
                      if getattr(self, 'include_availability', True) else []),
                    ('    icon: mdi:flash\n' if getattr(self, 'static_metadata', False) else
                     '    icon: "{{ \'mdi:flash\' if is_state(\'' + source + '\', \'5\') else \'mdi:power\' }}"\n'),
                    ('    picture: /local/static.png\n' if getattr(self, 'static_metadata', False) else
                     '    picture: "{{ \'/local/\' ~ states(\'' + source + '\') ~ \'.png\' }}"\n'),
                    '    attributes:\n',
                    '      sample: "{{ states(\'' + source + '\') | int(0) }}"\n',
                    '      stable: stable\n'])
        original = ''.join(lines).encode()
        (self.f.root / 'template.yaml').write_bytes(original)
        await self.f.hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        current = {(p.domain, e.unique_id): (e.entity_id,
            None if (r := self.f.entity.async_get(e.entity_id)) is None else r.id)
            for p in async_get_platforms(self.f.hass, 'template') for e in p.entities.values()}
        self.assertEqual(current, self.identities)
        return runtime, mapping, uuids, original

    async def emit(self, value):
        # Ordinary source entity event: never replace hass.states or observers.
        self.producer._attr_native_value = value
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()

    async def sample(self, value):
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.emit(value)
        for platform in async_get_platforms(self.f.hass, 'template'):
            for entity in platform.entities.values():
                state = self.f.hass.states.get(entity.entity_id)
                if value == -1 and getattr(self, 'include_availability', True):
                    self.assertEqual(state.state, 'unavailable')
                    continue
                self.assertEqual(state.state, ('on' if value == 5 else 'off')
                    if platform.domain == 'binary_sensor' else str(value * 2))
                self.assertEqual(state.attributes['icon'], 'mdi:flash'
                    if value == 5 or getattr(self, 'static_metadata', False) else 'mdi:power')
                self.assertEqual(state.attributes['entity_picture'], '/local/static.png'
                    if getattr(self, 'static_metadata', False) else '/local/' + str(value) + '.png')
                self.assertEqual(state.attributes['sample'], value)
                self.assertEqual(state.attributes['stable'], 'stable')
        self.assertEqual({(p.domain, e.unique_id): (e.entity_id,
            None if (r := self.f.entity.async_get(e.entity_id)) is None else r.id)
            for p in async_get_platforms(self.f.hass, 'template') for e in p.entities.values()}, self.identities)

    async def stimulus(self, value):
        for sample in (value, -1, value):
            await self.sample(sample)

    async def test_named_sensor_metadata_forward_fresh_reverse_history(self):
        await self.flow(variant='sensor')

    async def test_named_binary_metadata_forward_fresh_reverse_history(self):
        await self.flow(variant='binary_sensor')

    async def test_mixed_metadata_forward_fresh_reverse_history(self):
        await self.flow(variant='mixed')

    async def test_uidless_sensor_metadata_forward_fresh_reverse_history(self):
        await self.flow(variant='sensor', uidless=True)

    async def test_uidless_binary_metadata_forward_fresh_reverse_history(self):
        await self.flow(variant='binary_sensor', uidless=True)

    async def test_uidless_metadata_without_availability_forward_fresh_reverse(self):
        self.include_availability = False
        await self.flow(variant='sensor', uidless=True)

    async def test_static_icon_picture_native_slots_forward_fresh_reverse(self):
        self.include_availability = False
        self.static_metadata = True
        await self.flow(variant='sensor', uidless=True)

    def _current_consumer(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        return next(e for p in async_get_platforms(self.f.hass, 'template') for e in p.entities.values())

    async def refuse_without_effect(self, runtime, mapping, uuids, original):
        async def refresh():
            return None
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_forward(self.f.hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old in mapping:
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertFalse(await runtime.journal.active())

    async def test_detached_actual_metadata_tracker_refuses_before_effect(self):
        args = await self.data(variant='sensor')
        self._current_consumer()._template_result_info._track_state_changes.async_remove()
        await self.refuse_without_effect(*args)

    async def test_extra_native_metadata_slot_refuses_before_effect(self):
        args = await self.data(variant='sensor')
        attrs = self._current_consumer()._template_attrs
        slots = next(iter(attrs.values()))
        slots.append(slots[0])
        await self.refuse_without_effect(*args)

    async def test_foreign_metadata_callback_is_not_executed_by_proof(self):
        args = await self.data(variant='sensor')
        called = []
        def foreign(value):
            called.append(value)
            raise AssertionError('foreign metadata callback executed')
        slot = next(slot for slots in self._current_consumer()._template_attrs.values()
                    for slot in slots if slot._attribute == 'sample')
        slot.on_update = foreign
        await self.refuse_without_effect(*args)
        self.assertEqual(called, [])

    async def test_overwritten_native_metadata_output_refuses_without_effect(self):
        args = await self.data(variant='sensor')
        entity = self._current_consumer()
        entity._attr_extra_state_attributes['stable'] = 'foreign'
        entity.async_write_ha_state()
        await self.f.hass.async_block_till_done()
        await self.refuse_without_effect(*args)

    async def test_changed_source_metadata_refuses_without_effect(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        edited = original.replace(b'stable: stable', b'stable: changed')
        (self.f.root / 'template.yaml').write_bytes(edited)
        await self.refuse_without_effect(runtime, mapping, uuids, edited)

    async def test_preexisting_unavailable_held_outputs_refuse_before_effect(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        await self.sample(5)
        from copy import deepcopy
        entity = self._current_consumer()
        held = (entity._attr_native_value, entity._attr_icon, entity._attr_entity_picture,
                deepcopy(entity._attr_extra_state_attributes))
        await self.sample(-1)
        unavailable = self.f.hass.states.get(entity.entity_id)
        self.assertEqual(unavailable.state, 'unavailable')
        # Native Entity hides extra attributes while unavailable, but the
        # Template instance retains the actual prior output fields internally.
        physical = dict(unavailable.attributes)
        self.assertEqual((entity._attr_native_value, entity._attr_icon, entity._attr_entity_picture,
                          entity._attr_extra_state_attributes), held)
        history = self.module('history_guard').NativeHistoryGuard(
            self.module('recorder_guard').NativeRecorderGuard(self.f.hass))
        consumer_history = await history.capture({self._current_consumer().entity_id: self._current_consumer().entity_id})
        await self.refuse_without_effect(runtime, mapping, uuids, original)
        self.assertEqual(dict(self.f.hass.states.get(entity.entity_id).attributes), physical)
        self.assertEqual((entity._attr_native_value, entity._attr_icon, entity._attr_entity_picture,
                          entity._attr_extra_state_attributes), held)
        self.assertTrue(await history.verify(consumer_history))

    test_fresh_reverse_source_drift_refuses_without_effect = (
        server.NativeTemplateYAMLServerTests.test_drift_refuses_fresh_reverse_without_any_rollback_effect)
