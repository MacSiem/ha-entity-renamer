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
        self.assertIs(binding._graph(native, switch)[0], switch._template_result_info)
