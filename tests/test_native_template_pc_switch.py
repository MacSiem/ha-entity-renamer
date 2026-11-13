"""Core 11.1 real Powercalc input with mixed state Template consumers."""
import asyncio
import unittest

import test_controlled_powercalc_actual_native as base


@unittest.skipUnless(base.actual.fixtures.HAS_HA, 'requires pinned native HA')
class TemplatePowercalcSwitchTests(base.ControlledActualGraphTests):
    async def test_switch_actions_and_pending_binary_forward_inverse(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component
        from ordinary_network_scope import native_dummy_mdns
        import yaml

        hass, runtime, root = self.f.hass, self._runtime(), self.f.root
        source = self.sources[10]
        old_source = source.entity_id
        leaf = self.entities['fixture-meter-10'].entity_id
        mapping = {old_source: 'light.p2_sample_source', leaf: 'sensor.p2_sample_power'}
        path, config = root / 'p2_template.yaml', root / 'configuration.yaml'
        config.write_text(config.read_text() + '\ntemplate: !include p2_template.yaml\n')
        templates = [{'sensor': [{'name': 'P2 total', 'unique_id': 'p2_total',
            'state': "{% set items = ['" + leaf + "'] %}{% set ns = namespace(total=0) %}"
                     "{% for item in items %}{% if states(item) in ['10', '10.0'] %}"
                     "{% set ns.total = ns.total + (states(item) | float(0)) %}"
                     "{% endif %}{% endfor %}{{ ns.total }}",
            'attributes': {'copy': "{{ states('" + leaf + "') }}"}}],
            'binary_sensor': [{'name': 'P2 delayed', 'unique_id': 'p2_delay',
                'availability': "{{ states('" + leaf + "') != 'unavailable' }}",
                'state': "{{ states('" + leaf + "') | float(0) > 0 }}", 'delay_on': '00:05:00'}],
            'switch': [{'name': 'P2 switch', 'unique_id': 'p2_switch',
                'state': "{{ states('" + leaf + "') | float(0) > 0 }}",
                'turn_on': [{'action': 'test.capture', 'target': {'entity_id': leaf}}],
                'turn_off': [{'action': 'test.capture', 'target': {'entity_id': leaf}}]},
                {'name': 'P2 foreign', 'unique_id': 'p2_foreign', 'state': '{{ true }}'}]}]
        path.write_text(yaml.safe_dump(templates, sort_keys=False))
        initial = path.read_bytes()
        checked = await async_hass_config_yaml(hass)
        self.assertTrue(await async_setup_component(hass, 'template', checked))
        await hass.async_block_till_done()
        await self._flush_documents()
        rows = {e.unique_id: e for p in async_get_platforms(hass, 'template') for e in p.entities.values()}
        sensor, binary, switch, foreign = (rows[k] for k in ('p2_total', 'p2_delay', 'p2_switch', 'p2_foreign'))
        ids = {e.unique_id: e.registry_entry.id for e in (sensor, binary, switch, foreign)}
        events = []
        async def capture(call):
            events.append(call.data['entity_id'])
        hass.services.async_register('test', 'capture', capture)
        self.addCleanup(hass.services.async_remove, 'test', 'capture')
        native_dummy_mdns(hass)
        await source.async_turn_on(brightness=255)
        for _ in range(100):
            if binary._delay_cancel is not None and hass.states.get(leaf) is not None:
                break
            await asyncio.sleep(.05)
        token = binary._delay_cancel
        self.assertIsNotNone(token)
        deadline = token.__self__.when()
        before = [hass.states.get(e.entity_id) for e in (sensor, binary, switch, foreign)]
        await switch.async_turn_on()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='p2-fixture',
            guard=lambda: True, refresh=refresh)
        op, fingerprint = review['operation_id'], review['fingerprint']
        result = await runtime.native_yaml_apply(hass, op, fingerprint, actor_id='p2-fixture',
            guard=lambda: True, refresh=refresh)
        self.assertEqual(result['state'], 'applied')
        self.assertIs(binary._delay_cancel, token)
        self.assertEqual(token.__self__.when(), deadline)
        self.assertIs(rows['p2_foreign'], foreign)
        self.assertEqual({e.unique_id: e.registry_entry.id for e in (sensor, binary, switch, foreign)}, ids)
        for e, state in zip((sensor, binary, switch, foreign), before, strict=True):
            self.assertIs(hass.states.get(e.entity_id), state)
        await switch.async_turn_on()
        await switch.async_turn_off()
        self.assertEqual(events, [[leaf], [mapping[leaf]], [mapping[leaf]]])
        result = await runtime.recovery_restore(hass, op, fingerprint, guard=lambda: True, refresh=refresh)
        self.assertEqual(result['state'], 'rolled_back')
        self.assertEqual(path.read_bytes(), initial)
        self.assertIs(binary._delay_cancel, token)
        self.assertEqual(token.__self__.when(), deadline)
        await switch.async_turn_off()
        self.assertEqual(events[-1], [leaf])
