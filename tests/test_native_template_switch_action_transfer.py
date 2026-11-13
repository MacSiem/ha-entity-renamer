"""Native Template switch source and idle action cutover regression."""
import copy
import importlib
import unittest

import test_native_template_state_source_profile as profile


@unittest.skipUnless(profile.fixture.fixtures.HAS_HA, 'requires pinned native HA')
class TemplateSwitchActionTransferTests(profile.NativeTemplateStateSourceProfileTests):
    async def test_switch_action_targets_forward_and_restore(self):
        from homeassistant.components.template.switch import StateSwitchEntity
        from homeassistant.core import Context
        from homeassistant.helpers.script import DATA_SCRIPTS

        def source(target):
            return '''- unique_id: scope
  switch:
  - unique_id: action
    name: Action
    state: "{{ is_state('sensor.source', 'on') }}"
    turn_on:
    - action: test.capture
      target:
        entity_id: ''' + target + '''
    turn_off:
    - action: test.capture
      target:
        entity_id: ''' + target + '''
'''
        before = await self.checked(source('switch.original'))
        after = await self.checked(source('switch.renamed'))
        worker = self.module('yaml_validation_worker')
        before_cfg = before['template'][0]['switch'][0]
        after_cfg = after['template'][0]['switch'][0]
        source_profile = worker.template_state_source_projection(before, str(self.f.root))
        self.assertIn('template.switch', {row[0] for row in source_profile['states']})
        entity = StateSwitchEntity(self.f.hass, before_cfg, 'scope-action')
        entity._context = Context()
        events = []
        async def capture(call):
            events.append(call.data['entity_id'])
        self.f.hass.services.async_register('test', 'capture', capture)
        self.addCleanup(self.f.hass.services.async_remove, 'test', 'capture')
        await entity.async_turn_on()
        original_scripts = dict(entity._action_scripts)
        original_count = len(self.f.hass.data[DATA_SCRIPTS])
        actions = self.module('native_template_switch_actions')
        images = tuple((name, worker._normal(before_cfg[name]), worker._normal(after_cfg[name]))
                       for name in ('turn_on', 'turn_off'))
        transfer = actions.SwitchActionTransfer.capture(self.f.hass, ((entity, images),), (entity,))
        entity._config = dict(before_cfg)
        for name in ('turn_on', 'turn_off'):
            entity._config[name] = copy.deepcopy(before_cfg[name])
        transfer.install()
        await entity.async_turn_on()
        await entity.async_turn_off()
        self.assertEqual(events, [['switch.original'], ['switch.renamed'], ['switch.renamed']])
        transfer.restore()
        self.assertEqual(entity._action_scripts, original_scripts)
        self.assertEqual(len(self.f.hass.data[DATA_SCRIPTS]), original_count)
        await entity.async_turn_off()
        self.assertEqual(events[-1], ['switch.original'])
        for script in original_scripts.values():
            script._async_unload()
