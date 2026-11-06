"""Native Template this-variable primitive; no registry/Root acceptance."""
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeOwnIDThisTransportTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_start = mixed.NativeTemplateMixedYAMLServerTests._before_start
    _flush_documents = mixed.NativeTemplateMixedYAMLServerTests._flush_documents
    prepare = mixed.NativeTemplateMixedYAMLServerTests.prepare
    emit = mixed.NativeTemplateMixedYAMLServerTests.emit
    _runtime = mixed.NativeTemplateMixedYAMLServerTests._runtime
    module = mixed.NativeTemplateMixedYAMLServerTests.module
    stimulus = mixed.NativeTemplateMixedYAMLServerTests.stimulus
    data = mixed.NativeTemplateMixedYAMLServerTests.data

    async def test_cached_this_replacement_and_exact_pre_attempt_undo(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.template import TemplateStateFromEntityId
        await self.data(with_automation=False)
        await self.stimulus(17)
        hass = self.f.hass
        entity = next(e for p in async_get_platforms(hass, 'template') if p.domain == 'sensor'
                      for e in p.entities.values() if e.unique_id == 'scope-value')
        tracker, state = entity._template_result_info, hass.states.get(entity.entity_id)
        old, new = entity.entity_id, 'sensor.native_own_id_this_next'
        tracks = tuple(tracker._track_templates)
        originals = tuple(track.variables['this'] for track in tracks)
        # Exercise the real native cached-property edge before capture.
        self.assertTrue(all(value.entity_id == old for value in originals))
        transport = self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
            hass, entity, new, guard=lambda: None)
        transport.install()
        self.assertIs(entity._template_result_info, tracker)
        self.assertIs(hass.states.get(old), state)
        self.assertTrue(all(type(track.variables['this']) is TemplateStateFromEntityId
                            and track.variables['this'].entity_id == new for track in tracks))
        self.assertTrue(all(value.entity_id == old for value in originals))
        owned = tracks[0].variables['this']
        foreign = TemplateStateFromEntityId(hass, new)
        tracks[0].variables['this'] = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                transport.restore_before_attempt()
            self.assertIs(tracks[0].variables['this'], foreign)
        finally:
            tracks[0].variables['this'] = owned
            transport.restore_before_attempt()
        self.assertTrue(all(track.variables['this'] is original
                            for track, original in zip(tracks, originals)))
        self.assertIs(entity._template_result_info, tracker)
        self.assertIs(hass.states.get(old), state)
        await self.stimulus(23)

    async def test_foreign_installed_this_cache_refuses_undo_before_any_write(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.data(with_automation=False)
        await self.stimulus(17)
        hass = self.f.hass
        entity = next(e for p in async_get_platforms(hass, 'template') if p.domain == 'sensor'
                      for e in p.entities.values() if e.unique_id == 'scope-value')
        tracker, state, row = entity._template_result_info, hass.states.get(entity.entity_id), entity.registry_entry
        variables = tracker._track_templates[0].variables
        original = variables['this']
        transport = self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
            hass, entity, 'sensor.native_own_id_this_next', guard=lambda: None)
        transport.install()
        replacement = variables['this']
        foreign = object()
        replacement._cache['foreign'] = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                transport.restore_before_attempt()
            self.assertIs(variables['this'], replacement)
            self.assertIs(replacement._cache['foreign'], foreign)
            self.assertIs(entity._template_result_info, tracker)
            self.assertIs(entity.registry_entry, row)
            self.assertIs(hass.states.get(entity.entity_id), state)
        finally:
            del replacement._cache['foreign']
            transport.restore_before_attempt()
        self.assertIs(variables['this'], original)

    async def test_foreign_original_cache_owner_refuses_undo_before_any_write(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.data(with_automation=False)
        await self.stimulus(17)
        hass = self.f.hass
        entity = next(e for p in async_get_platforms(hass, 'template') if p.domain == 'sensor'
                      for e in p.entities.values() if e.unique_id == 'scope-value')
        tracker, state, row = entity._template_result_info, hass.states.get(entity.entity_id), entity.registry_entry
        variables = tracker._track_templates[0].variables
        original = variables['this']
        transport = self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
            hass, entity, 'sensor.native_own_id_this_next', guard=lambda: None)
        transport.install()
        replacement, original_cache = variables['this'], original._cache
        foreign_cache = dict(original_cache)
        original._cache = foreign_cache
        try:
            with self.assertRaises(self.module('transaction').Refused):
                transport.restore_before_attempt()
            self.assertIs(variables['this'], replacement)
            self.assertIs(original._cache, foreign_cache)
            self.assertIs(entity._template_result_info, tracker)
            self.assertIs(entity.registry_entry, row)
            self.assertIs(hass.states.get(entity.entity_id), state)
        finally:
            original._cache = original_cache
            transport.restore_before_attempt()
        self.assertIs(variables['this'], original)

    async def test_foreign_this_collection_flag_refuses_before_install(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.data(with_automation=False)
        await self.stimulus(17)
        hass = self.f.hass
        entity = next(e for p in async_get_platforms(hass, 'template') if p.domain == 'sensor'
                      for e in p.entities.values() if e.unique_id == 'scope-value')
        tracker, state, row = entity._template_result_info, hass.states.get(entity.entity_id), entity.registry_entry
        variables = tracker._track_templates[0].variables
        original = variables['this']
        transport = self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
            hass, entity, 'sensor.native_own_id_this_next', guard=lambda: None)
        collect = original._collect
        original._collect = not collect
        try:
            with self.assertRaises(self.module('transaction').Refused):
                transport.install()
            self.assertIs(variables['this'], original)
            self.assertIs(original._collect, not collect)
            self.assertIs(entity._template_result_info, tracker)
            self.assertIs(entity.registry_entry, row)
            self.assertIs(hass.states.get(entity.entity_id), state)
        finally:
            original._collect = collect

    async def test_foreign_this_class_alias_refuses_before_install(self):
        from homeassistant.helpers import template as template_module
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.data(with_automation=False)
        await self.stimulus(17)
        hass = self.f.hass
        entity = next(e for p in async_get_platforms(hass, 'template') if p.domain == 'sensor'
                      for e in p.entities.values() if e.unique_id == 'scope-value')
        tracker, state, row = entity._template_result_info, hass.states.get(entity.entity_id), entity.registry_entry
        variables = tuple(track.variables for track in tracker._track_templates)
        originals = tuple(values['this'] for values in variables)
        transport = self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
            hass, entity, 'sensor.native_own_id_this_next', guard=lambda: None)
        from homeassistant.components.template import template_entity
        class ForeignThis:
            pass
        for module in (template_module, template_entity):
            with self.subTest(module=module.__name__):
                native = module.TemplateStateFromEntityId
                module.TemplateStateFromEntityId = ForeignThis
                try:
                    with self.assertRaises((self.module('transaction').Refused, ValueError)):
                        transport.install()
                    self.assertIs(module.TemplateStateFromEntityId, ForeignThis)
                    self.assertTrue(all(values['this'] is original
                                        for values, original in zip(variables, originals)))
                    self.assertIs(entity._template_result_info, tracker)
                    self.assertIs(entity.registry_entry, row)
                    self.assertIs(hass.states.get(entity.entity_id), state)
                finally:
                    module.TemplateStateFromEntityId = native

    async def test_this_shared_with_other_template_refuses_without_write(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.data(with_automation=False)
        await self.stimulus(17)
        hass = self.f.hass
        entities = [e for p in async_get_platforms(hass, 'template')
                    for e in p.entities.values() if getattr(e, '_template_result_info', None) is not None]
        selected = next(e for e in entities if e.unique_id == 'scope-value' and e.entity_id.startswith('sensor.'))
        foreign = next(e for e in entities if e is not selected)
        shared = selected._template_result_info._track_templates[0].variables['this']
        variables = foreign._template_result_info._track_templates[0].variables
        original = variables['this']
        variables['this'] = shared
        try:
            with self.assertRaises(self.module('transaction').Refused):
                self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
                    hass, selected, 'sensor.native_own_id_this_next', guard=lambda: None)
            self.assertIs(variables['this'], shared)
        finally:
            variables['this'] = original

    async def test_script_config_foreign_variables_alias_refuses_without_write(self):
        await self._script_alias_refuses(wrapper_only=False)

    async def test_script_config_foreign_this_wrapper_refuses_without_write(self):
        await self._script_alias_refuses(wrapper_only=True)

    async def test_configured_self_id_alias_refuses_without_changing_native_state(self):
        from homeassistant.const import __version__
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms

        await self.data(with_automation=False)
        path = self.f.root / 'template.yaml'
        text = path.read_text()
        expression = '{{ this.entity_id if this is defined else \'unknown\' }}'
        if __version__ == '2025.6.0':
            marker = '    name: Runtime consumer\n'
            self.assertIn(marker, text)
            text = text.replace(marker, marker + '    variables:\n      self_ref: "'
                + expression + '"\n      indirect: "{{ self_ref }}"\n', 1)
        else:
            marker = '- unique_id: scope\n'
            self.assertIn(marker, text)
            text = text.replace(marker, marker + '  variables:\n    self_ref: "'
                + expression + '"\n    indirect: "{{ self_ref }}"\n', 1)
        path.write_text(text)
        hass = self.f.hass
        await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self.stimulus(17)
        entity = next(e for platform in async_get_platforms(hass, 'template')
                      if platform.domain == 'sensor' for e in platform.entities.values()
                      if e.unique_id == 'scope-value')
        tracker, state, row = entity._template_result_info, hass.states.get(entity.entity_id), entity.registry_entry
        variables = tuple((track.variables, track.variables['self_ref'], track.variables['indirect'])
                          for track in tracker._track_templates)
        with self.assertRaises(self.module('transaction').Refused):
            self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
                hass, entity, 'sensor.native_own_id_this_next', guard=lambda: None)
        self.assertIs(entity._template_result_info, tracker)
        self.assertIs(entity.registry_entry, row)
        self.assertIs(hass.states.get(entity.entity_id), state)
        self.assertTrue(all(values['self_ref'] is ref and values['indirect'] is indirect
                            for values, ref, indirect in variables))

    async def test_configured_self_id_foreign_slot_refuses_undo_before_any_write(self):
        from homeassistant.const import __version__
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms

        await self.data(with_automation=False)
        path = self.f.root / 'template.yaml'
        text = path.read_text()
        expression = '{{ this.entity_id if this is defined else \'unknown\' }}'
        if __version__ == '2025.6.0':
            marker = '    name: Runtime consumer\n'
            self.assertIn(marker, text)
            text = text.replace(marker, marker + '    variables:\n      self_ref: "'
                                + expression + '"\n', 1)
        else:
            marker = '- unique_id: scope\n'
            self.assertIn(marker, text)
            text = text.replace(marker, marker + '  variables:\n    self_ref: "'
                                + expression + '"\n', 1)
        path.write_text(text)
        hass = self.f.hass
        await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self.stimulus(17)
        entity = next(e for platform in async_get_platforms(hass, 'template')
                      if platform.domain == 'sensor' for e in platform.entities.values()
                      if e.unique_id == 'scope-value')
        tracker, state, row = entity._template_result_info, hass.states.get(entity.entity_id), entity.registry_entry
        variables = tracker._track_templates[0].variables
        old_this, old_ref = variables['this'], variables['self_ref']
        self.assertEqual(old_ref, entity.entity_id)
        transport = self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
            hass, entity, 'sensor.native_own_id_this_next', guard=lambda: None)
        transport.install()
        new_this, new_ref = variables['this'], variables['self_ref']
        self.assertEqual(new_ref, 'sensor.native_own_id_this_next')
        variables['self_ref'] = 'sensor.foreign_edit'
        try:
            with self.assertRaises(self.module('transaction').Refused):
                transport.restore_before_attempt()
            self.assertIs(variables['this'], new_this)
            self.assertEqual(variables['self_ref'], 'sensor.foreign_edit')
            self.assertIs(entity._template_result_info, tracker)
            self.assertIs(entity.registry_entry, row)
            self.assertIs(hass.states.get(entity.entity_id), state)
        finally:
            variables['self_ref'] = new_ref
            transport.restore_before_attempt()
        self.assertIs(variables['this'], old_this)
        self.assertIs(variables['self_ref'], old_ref)
        # A foreign replacement of the configured expression is a separate
        # ownership edge; identical variable names do not authorize undo.
        from homeassistant.helpers.template import Template
        configured = entity._run_variables.variables
        original_template = configured['self_ref']
        again = self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
            hass, entity, 'sensor.native_own_id_this_next', guard=lambda: None)
        again.install()
        installed_this, installed_ref = variables['this'], variables['self_ref']
        configured['self_ref'] = Template('{{ this.entity_id }}', hass)
        try:
            with self.assertRaises(self.module('transaction').Refused):
                again.restore_before_attempt()
            self.assertIs(variables['this'], installed_this)
            self.assertIs(variables['self_ref'], installed_ref)
        finally:
            configured['self_ref'] = original_template
            again.restore_before_attempt()
        self.assertIs(variables['this'], old_this)
        self.assertIs(variables['self_ref'], old_ref)

    async def _script_alias_refuses(self, *, wrapper_only):
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.data(with_automation=False)
        await self.stimulus(17)
        hass = self.f.hass
        selected = next(e for platform in async_get_platforms(hass, 'template')
                        if platform.domain == 'sensor' for e in platform.entities.values()
                        if e.unique_id == 'scope-value')
        script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities
                      if e.unique_id == 'mixed_source')
        variables = selected._template_result_info._track_templates[0].variables
        foreign = variables['this'] if wrapper_only else variables
        self.assertNotIn('foreign_this_alias', script.raw_config)
        script.raw_config['foreign_this_alias'] = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                self.module('native_own_id_this').NativeOwnIDThisTransport.capture(
                    hass, selected, 'sensor.native_own_id_this_next', guard=lambda: None)
            self.assertIs(script.raw_config['foreign_this_alias'], foreign)
            self.assertIsNotNone(self.f.entity.async_get(selected.entity_id))
        finally:
            del script.raw_config['foreign_this_alias']
