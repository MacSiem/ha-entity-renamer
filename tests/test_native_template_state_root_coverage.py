"""Actual server-root coverage beyond isolated Template binding primitives."""
import asyncio
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateStateRootCoverageTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    data = server.NativeTemplateYAMLServerTests.data

    async def configure(self, *, delayed=False):
        from homeassistant.core import Context
        from homeassistant.const import __version__
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, _ = await self.data(variant='binary_sensor' if delayed else 'sensor')
        old = next(iter(mapping))
        domain = 'binary_sensor' if delayed else 'sensor'
        body = '- unique_id: scope\n'
        variables = (f'variables:\n  source: {old}\n  alias: "{{{{ source }}}}"\n'
                     '  stamp: "{{ now() }}"\n  frozen: "{{ states(source) }}"\n')
        if not delayed and __version__ != '2025.6.0':
            body += ''.join('  ' + line + '\n' for line in variables.splitlines())
        body += f'  {domain}:\n  - unique_id: value\n    name: Runtime consumer\n'
        if delayed:
            body += f'    state: "{{{{ is_state(\'{old}\', \'5\') }}}}"\n    delay_on: "00:05:00"\n'
        else:
            if __version__ == '2025.6.0':
                body += ''.join('    ' + line + '\n' for line in variables.splitlines())
            body += ('    state: "{{ states(alias) | float(0) * 2 }}"\n'
                     '    availability: "{{ states(alias) | float(0) >= 3 }}"\n'
                     '    attributes:\n      frozen: "{{ frozen }}"\n'
                     '      stamp: "{{ stamp }}"\n      identity: "{{ this.entity_id }}"\n')
        path = self.f.root / 'template.yaml'
        path.write_text(body)
        await self.f.hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        consumer = next(entity for platform in async_get_platforms(self.f.hass, 'template')
                        for entity in platform.entities.values() if entity.unique_id == 'scope-value')
        return runtime, mapping, uuids, path.read_bytes(), consumer

    async def source(self, value):
        self.producer._attr_native_value = value
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()

    async def test_unavailable_variables_snapshot_and_live_this_full_root(self):
        runtime, mapping, uuids, original, consumer = await self.configure()
        hass = self.f.hass
        initial = hass.states.get(consumer.entity_id)
        self.assertEqual(initial.state, 'unavailable')
        variables = [dict(track.variables) for track in consumer._template_result_info._track_templates]
        retained = [(value['stamp'], value['frozen']) for value in variables if 'stamp' in value]
        self.assertTrue(retained)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        # A false availability group may contain only its genuine first slot.
        # An empty/corrupt group remains a refusal before registry/YAML effects.
        info = consumer._template_result_info
        original_info = dict(info._info)
        self.assertEqual(len(original_info), 1)
        try:
            info._info.clear()
            with self.assertRaises((ValueError, self.module('transaction').Refused)):
                await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        finally:
            info._info.update(original_info)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
        self.assertFalse(await runtime.journal.active())
        result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertIs(hass.states.get(consumer.entity_id), initial)
        for value in (3, 5, 1):
            await self.source(value)
            actual = hass.states.get(consumer.entity_id)
            if value < 3:
                self.assertEqual(actual.state, 'unavailable')
            else:
                self.assertEqual(float(actual.state), value * 2)
            if value >= 3:
                self.assertEqual(actual.attributes['identity'], consumer.entity_id)
                self.assertEqual(str(actual.attributes['frozen']), '1')
        unavailable = hass.states.get(consumer.entity_id)
        inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(inverse['state'], 'rolled_back')
        self.assertIs(hass.states.get(consumer.entity_id), unavailable)
        for track in consumer._template_result_info._track_templates:
            if 'stamp' in track.variables:
                self.assertTrue(any(track.variables['stamp'] is stamp and track.variables['frozen'] is frozen
                                    for stamp, frozen in retained))
        await self.source(7)
        self.assertEqual(float(hass.states.get(consumer.entity_id).state), 14)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))

    async def test_pending_binary_delay_same_handle_deadline_forward_fresh_reverse(self):
        runtime, mapping, uuids, original, consumer = await self.configure(delayed=True)
        hass = self.f.hass
        await self.source(5)
        token = consumer._delay_cancel
        self.assertIsNotNone(token)
        handle = token.__self__
        self.assertIsInstance(handle, asyncio.TimerHandle)
        deadline = handle.when()
        self.assertGreater(deadline, hass.loop.time())
        physical = hass.states.get(consumer.entity_id)
        self.assertEqual(physical.state, 'off')
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertIs(consumer._delay_cancel, token)
        self.assertFalse(handle.cancelled())
        self.assertEqual(handle.when(), deadline)
        self.assertIs(hass.states.get(consumer.entity_id), physical)
        inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(inverse['state'], 'rolled_back')
        self.assertIs(consumer._delay_cancel, token)
        self.assertFalse(handle.cancelled())
        self.assertEqual(handle.when(), deadline)
        self.assertIs(hass.states.get(consumer.entity_id), physical)
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
        # A real false producer value naturally cancels the retained native
        # timer. The existing short-delay native primitive qualifies expiry;
        # this root case specifically qualifies unchanged pending ownership.
        await self.source(1)
        self.assertTrue(handle.cancelled())
        self.assertIsNone(consumer._delay_cancel)
        self.assertEqual(hass.states.get(consumer.entity_id).state, 'off')
