"""Core-owned Template switch binding and action root integration."""
import unittest

import test_native_template_state_root_coverage as root


@unittest.skipUnless(root.server.activation.fixture.fixtures.HAS_HA, 'requires pinned HA')
class TemplateSwitchRootTests(root.NativeTemplateStateRootCoverageTests):
    async def test_live_switch_graph(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.prepare('yaml', variant='sensor', validate=False)
        old = self.source_id
        (self.f.root / 'template.yaml').write_text('''- unique_id: scope
  switch:
  - unique_id: action
    name: Action
    state: "{{ states(\'''' + old + '''\') | int(0) > 0 }}"
    turn_on:
    - action: homeassistant.turn_on
      target:
        entity_id: ''' + old + '''
    turn_off:
    - action: homeassistant.turn_off
      target:
        entity_id: ''' + old + '''
''')
        await self.f.hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        switch = next(entity for platform in async_get_platforms(self.f.hass, 'template')
                      for entity in platform.entities.values() if entity.unique_id == 'scope-action')
        binding = self.module('native_template_state_binding')
        native = binding._StateRuntime(self.f.hass)
        self.assertEqual(binding._graph(native, switch)[0], id(switch._template_result_info))

    async def test_forward_inverse_switch_and_pending_binary(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.prepare('yaml', variant='sensor', validate=False)
        hass, old = self.f.hass, self.source_id
        new = 'sensor.accepted_next'
        uuid = self.f.entity.async_get(old).id
        path = self.f.root / 'template.yaml'
        path.write_text('''- unique_id: scope
  sensor:
  - unique_id: value
    name: Total
    state: "{{ states(\'''' + old + '''\') | int(0) }}"
    attributes:
      copy: "{{ states(\'''' + old + '''\') }}"
  binary_sensor:
  - unique_id: delay
    name: Delay
    availability: "{{ states(\'''' + old + '''\') != \'unavailable\' }}"
    state: "{{ states(\'''' + old + '''\') | int(0) > 0 }}"
    delay_on: "00:05:00"
  switch:
  - unique_id: action
    name: Action
    state: "{{ states(\'''' + old + '''\') | int(0) > 0 }}"
    turn_on:
    - action: test.capture
      target:
        entity_id: ''' + old + '''
    turn_off:
    - action: test.capture
      target:
        entity_id: ''' + old + '''
''')
        original = path.read_bytes()
        await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        entities = {entity.unique_id: entity for platform in async_get_platforms(hass, 'template')
                    for entity in platform.entities.values()}
        sensor, binary, switch = (entities[k] for k in ('scope-value', 'scope-delay', 'scope-action'))
        await self.source(5)
        token = binary._delay_cancel
        self.assertIsNotNone(token)
        deadline = token.__self__.when()
        physical = [hass.states.get(e.entity_id) for e in (sensor, binary, switch)]
        events = []
        async def capture(call):
            events.append(call.data['entity_id'])
        hass.services.async_register('test', 'capture', capture)
        self.addCleanup(hass.services.async_remove, 'test', 'capture')
        await switch.async_turn_on()
        from homeassistant.config import async_hass_config_yaml, async_process_component_and_handle_errors
        from homeassistant.loader import async_get_integration
        cfg = await async_hass_config_yaml(hass)
        checked = await async_process_component_and_handle_errors(hass, cfg, await async_get_integration(hass, 'template'))
        worker = self.module('yaml_validation_worker')
        for domain, entity in (('sensor', sensor), ('binary_sensor', binary), ('switch', switch)):
            expected = worker._normal(checked['template'][0][domain][0])
            actual = worker._normal({k: v for k, v in entity._config.items() if k not in ('raw_configs', 'raw_blueprint_inputs')})
            self.assertEqual(dict(actual['$map']), dict(expected['$map']), domain)
        runtime = await self._runtime(self.module)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        import sys
        caught = []
        def trace(frame, event, arg):
            if event == 'exception' and 'ha_entity_renamer/' in frame.f_code.co_filename:
                typ, value, _ = arg
                caught.append((frame.f_code.co_name, typ.__name__, str(value)))
            return trace
        sys.settrace(trace)
        try:
            result = await runtime.native_forward(hass, {old: new}, guard=lambda: None, refresh=refresh)
        except Exception as error:
            self.fail(f'{error}: {caught[-20:]}')
        finally:
            sys.settrace(None)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual(self.f.entity.async_get(new).id, uuid)
        self.assertIs(binary._delay_cancel, token)
        self.assertEqual(token.__self__.when(), deadline)
        for entity, state in zip((sensor, binary, switch), physical, strict=True):
            self.assertIs(hass.states.get(entity.entity_id), state)
        await switch.async_turn_on()
        await switch.async_turn_off()
        self.assertEqual(events, [[old], [new], [new]])
        reverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(reverse['state'], 'rolled_back')
        self.assertEqual(self.f.entity.async_get(old).id, uuid)
        self.assertEqual(path.read_bytes(), original)
        self.assertIs(binary._delay_cancel, token)
        self.assertEqual(token.__self__.when(), deadline)
        await switch.async_turn_off()
        self.assertEqual(events[-1], [old])
