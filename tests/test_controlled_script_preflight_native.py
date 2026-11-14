"""Public review of controlled Script own-ID with a live native caller."""
import json
from pathlib import Path
import unittest
import asyncio

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class ControlledScriptPreflight(mixed.NativeTemplateMixedYAMLServerTests):
    async def test_controlled_script_review_with_caller_e64(self):
        from homeassistant.core import Context
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_scripts.yaml'
        path.write_text(path.read_text().replace('  sequence:\n', '  sequence:\n'
            '  - delay: "{{ 120 if long | default(false) else 0 }}"\n', 1)
            + '\nstatic_caller:\n  sequence:\n  - action: script.mixed_source\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        entity = next(e for e in hass.data['script'].entities if e.unique_id == 'mixed_source')
        await hass.services.async_call('script', 'mixed_source', {'long': True}, blocking=False)
        for _ in range(1000):
            if entity.script.is_running:
                break
            await asyncio.sleep(.001)
        evidence = {'active_before': entity.script.is_running, 'apply_attempted': False,
                    'registry_before': json.loads(_orjson_bytes_default_encoder(self.f.entity._data_to_save())),
                    'files_before': {n: (root / n).read_text() for n in
                        ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')}}
        output = Path('/qa/script-preflight.json')
        def save(): output.write_text(json.dumps(evidence, sort_keys=True, default=str))
        save()
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        try:
            with mixed.native_diagnostics(self.module, sink=evidence):
                evidence['review'] = await runtime.native_yaml_prepare(hass,
                    {**mapping, entity.entity_id: 'script.census_script_renamed'}, actor_id='native-admin',
                    guard=lambda: None, refresh=refresh)
            save()
            self.assertTrue(evidence['active_before'])
            self.assertTrue(any(row['entity_id'] == entity.entity_id for row in evidence['review']['changes']))
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            raise
        finally:
            try:
                evidence['registry_final'] = json.loads(_orjson_bytes_default_encoder(self.f.entity._data_to_save()))
                evidence['files_final'] = {name: (root / name).read_text() for name in evidence['files_before']}
            except BaseException as final_error:
                evidence['final_error'] = {'type': type(final_error).__name__, 'message': str(final_error)}
                save()
                if 'primary_error' not in evidence:
                    raise
            save()
