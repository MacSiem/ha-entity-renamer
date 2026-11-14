"""Focused Core 11 admission through real prepare, apply and recovery."""
from datetime import timedelta
import logging
import unittest

import test_native_queue_prepare_auth as queue


@unittest.skipUnless(queue.backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativeForeignApplyTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = queue.NativeQueuePrepareAuthTests.asyncSetUp
    _before_loader = queue.NativeQueuePrepareAuthTests._before_loader
    _before_start = queue.NativeQueuePrepareAuthTests._before_start
    _loaded_backend = queue.NativeQueuePrepareAuthTests._loaded_backend
    _flush_documents = queue.NativeQueuePrepareAuthTests._flush_documents
    prepare = queue.NativeQueuePrepareAuthTests.prepare
    emit = queue.NativeQueuePrepareAuthTests.emit
    data = queue.NativeQueuePrepareAuthTests.data
    _runtime = queue.NativeQueuePrepareAuthTests._runtime
    module = queue.NativeQueuePrepareAuthTests.module

    async def test_same_file_foreign_scope_apply_and_restore(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.1':
            self.skipTest('Core 11.1 only')
        from homeassistant.components.rest.data import RestData
        from homeassistant.components.rest.sensor import RestSensor
        from homeassistant.helpers.entity_platform import EntityPlatform
        from homeassistant.helpers.trigger_template_entity import ValueTemplate
        runtime, mapping, uuids, _ = await self.data(variant='sensor')
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        path = self.f.root / 'configuration.yaml'
        template = (self.f.root / 'template.yaml').read_text()
        source = "{{ states('sensor.unrelated') }}"
        path.write_text('template:\n' + ''.join('  ' + line for line in template.splitlines(keepends=True))
            + 'rest:\n  - resource: http://example.invalid\n    sensor:\n'
              '      - name: Foreign fixture\n        value_template: "' + source + '"\n')
        await hass.services.async_call('template', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        registry = self.f.entity.async_get_or_create('sensor', 'rest', 'foreign-fixture')
        await self._flush_documents()
        platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__),
            domain='sensor', platform_name='rest', platform=None,
            scan_interval=timedelta(seconds=30), entity_namespace=None)
        platform.async_prepare()
        platform._setup_complete = True
        entity = object.__new__(RestSensor)
        entity.hass, entity.entity_id = hass, registry.entity_id
        entity._config = {}
        entity._value_template = ValueTemplate(source, hass)
        entity._resource_template = entity._coordinator = None
        entity.rest = RestData(hass, 'GET', 'http://example.invalid', 'utf-8', None,
                               None, None, None, True, 'python_default', 10)
        platform.entities[registry.entity_id] = entity
        hass.config.components.add('rest')
        original = path.read_bytes()
        async def refresh():
            pass
        try:
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='fixture',
                guard=lambda: None, refresh=refresh, entity_names={old: 'Reviewed name'})
            binding = {key: review[key] for key in ('operation_id', 'fingerprint')}
            self.assertIsNotNone(runtime.reviews[binding['operation_id']].foreign_admission)
            result = await runtime.native_yaml_apply(hass, **binding, actor_id='fixture',
                guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied', result)
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            self.assertEqual(self.f.entity.async_get(new).name, 'Reviewed name')
            self.assertIn(new.encode(), path.read_bytes())
            inspection = await runtime.recovery_inspect(hass, **binding,
                guard=lambda: None, refresh=refresh)
            self.assertTrue(inspection['eligible'], inspection)
            restored = await runtime.recovery_restore(hass, **binding,
                guard=lambda: None, refresh=refresh)
            self.assertEqual(restored['state'], 'rolled_back', restored)
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertEqual(path.read_bytes(), original)
        finally:
            platform.entities.clear()
            hass.config.components.remove('rest')
            await platform.async_destroy()
