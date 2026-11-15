"""Isolated native Script removal boundary; no rename or inverse is attempted."""
import asyncio
import json
from pathlib import Path
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class ControlledScriptRemoveProbe(mixed.NativeTemplateMixedYAMLServerTests):
    async def test_native_running_script_remove_observation_p43(self):
        from homeassistant.core import Context
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        _, _, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_scripts.yaml'
        path.write_text(path.read_text().replace('  sequence:\n', '  sequence:\n'
            '  - delay: "{{ 120 if long | default(false) else 0 }}"\n', 1)
            + '\nstatic_caller:\n  sequence:\n  - action: script.mixed_source\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        component = hass.data['script']
        entity = next(e for e in component.entities if e.unique_id == 'mixed_source')
        await hass.services.async_call('script', 'mixed_source', {'long': True}, blocking=False)
        for _ in range(1000):
            if entity.script.is_running:
                break
            await asyncio.sleep(.001)
        output = Path('/qa/script-remove-probe.json')
        evidence = {'remove_attempted': False, 'apply_attempted': False}
        def registry():
            return json.loads(_orjson_bytes_default_encoder(self.f.entity._data_to_save()))
        def observe():
            state = hass.states.get(entity.entity_id)
            return {'selected_present': component.get_entity(entity.entity_id) is entity,
                'old_service': hass.services.has_service('script', 'mixed_source'),
                'caller_service': hass.services.has_service('script', 'static_caller'),
                'running': entity.script.is_running,
                'state': None if state is None else {'value': state.state, 'attributes': dict(state.attributes)},
                'registry': registry()}
        def save(): output.write_text(json.dumps(evidence, sort_keys=True, default=str))
        try:
            evidence['before'] = observe(); save()
            evidence['remove_attempted'] = True; save()
            await entity.async_remove()
            evidence['after'] = observe(); save()
            evidence['qualifies'] = self.module('controlled_script_cutover')._native_removed_state(
                hass.states.get(entity.entity_id)); save()
            self.assertTrue(evidence['before']['running'])
            self.assertFalse(evidence['after']['selected_present'])
            self.assertFalse(evidence['after']['old_service'])
            self.assertTrue(evidence['after']['caller_service'])
            self.assertEqual(evidence['after']['registry'], evidence['before']['registry'])
            self.assertTrue(evidence['qualifies'])
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            raise
        finally:
            try:
                evidence['final'] = observe()
            except BaseException as error:
                evidence['final_error'] = {'type': type(error).__name__, 'message': str(error)}
                if 'primary_error' not in evidence:
                    save(); raise
            save()

    async def test_native_script_cutover_states_minimum_k62(self):
        """Observe native remove, registry update and add, without product Apply."""
        import yaml
        from homeassistant.core import Context
        from homeassistant.components.script import _create_script_entities, _prepare_script_config
        from homeassistant.components.script.config import async_validate_config_item
        _, _, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_scripts.yaml'
        path.write_text(path.read_text().replace('  sequence:\n', '  sequence:\n'
            '  - delay: "{{ 120 if long | default(false) else 0 }}"\n', 1))
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        component = hass.data['script']
        entity = next(e for e in component.entities if e.unique_id == 'mixed_source')
        old, new = entity.entity_id, 'script.native_probe_renamed'
        registry = self.f.entity
        uuid = registry.async_get(old).id
        raw = yaml.safe_load(path.read_text())['mixed_source']
        item = await async_validate_config_item(hass, 'native_probe_renamed', raw)
        prepared = await _prepare_script_config(hass, {'script': {'native_probe_renamed': item}})
        created = (await _create_script_entities(hass, prepared))[0]
        output = Path('/qa/script-cutover-probe.json')
        evidence = {'registry_attempted': False, 'uuid': uuid}
        def save(): output.write_text(json.dumps(evidence, sort_keys=True, default=str))
        def state(key):
            value = hass.states.get(key)
            return None if value is None else {'state': value.state, 'attributes': dict(value.attributes)}
        def observe():
            return {'old': state(old), 'new': state(new),
                'old_service': hass.services.has_service('script', 'mixed_source'),
                'new_service': hass.services.has_service('script', 'native_probe_renamed'),
                'old_row': None if registry.async_get(old) is None else registry.async_get(old).id,
                'new_row': None if registry.async_get(new) is None else registry.async_get(new).id}
        try:
            await hass.services.async_call('script', 'mixed_source', {'long': True}, blocking=False)
            for _ in range(1000):
                if entity.script.is_running: break
                await asyncio.sleep(.001)
            evidence['before'] = observe(); save()
            evidence['remove_attempted'] = True; save()
            await entity.async_remove()
            evidence['removed'] = observe(); removed_state = hass.states.get(old); save()
            evidence['registry_attempted'] = True; save()
            registry.async_update_entity(old, new_entity_id=new, new_unique_id='native_probe_renamed',
                name='mixed_source', original_name='native_probe_renamed')
            evidence['updated'] = observe(); save()
            await component.async_add_entities([created])
            await asyncio.sleep(0)
            evidence['added'] = observe(); evidence['same_removed_state'] = hass.states.get(old) is removed_state; save()
            self.assertEqual(evidence['added']['new_row'], uuid)
            self.assertTrue(evidence['added']['new_service'])
            self.assertIsNotNone(evidence['added']['new'])
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            raise
        finally:
            evidence['final'] = observe(); save()
