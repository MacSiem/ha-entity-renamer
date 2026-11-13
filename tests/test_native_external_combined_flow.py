"""Retained public review, native consumers, and fresh inverse on pinned Core."""
import importlib
import unittest
import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class ExternalCombinedFlowTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    data = server.NativeTemplateYAMLServerTests.data
    _runtime = server.NativeTemplateYAMLServerTests._runtime

    def module(self, name):
        return importlib.import_module(server.activation.fixture.fixtures.PACKAGE + '.' + name)

    async def test_retained_review_apply_and_fresh_inverse(self):
        from homeassistant import auth
        from homeassistant.components.sensor import SensorDeviceClass
        from homeassistant.const import UnitOfTemperature
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component
        runtime, mappings, uuids, template_before = await self.data(multiple=True, variant='sensor')
        hass, root = self.f.hass, self.f.root
        old = self.source_id
        new = mappings[old]
        mapping = {old: new}
        foreign_source = self.other.entity_id
        self.producer._attr_device_class = SensorDeviceClass.TEMPERATURE
        self.producer._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self.producer.async_write_ha_state()
        await hass.async_block_till_done()
        config = root / 'configuration.yaml'
        config.write_text(config.read_text() + '\nhomekit:\n- name: Synthetic Bridge\n'
            '  port: 21198\n  ip_address: 192.0.2.1\n  advertise_ip: 192.0.2.1\n'
            '  filter:\n    include_entities:\n      - ' + old + '\n'
            '- name: Foreign Bridge\n  port: 21199\n  ip_address: 192.0.2.1\n'
            '  advertise_ip: 192.0.2.1\n  filter:\n    include_entities:\n      - ' + foreign_source + '\n'
            'utility_meter:\n  p3_meter:\n    source: ' + old + '\n    cycle: daily\n'
            '  p3_second_meter:\n    source: ' + old + '\n    cycle: weekly\n'
            '  p3_foreign_meter:\n    source: ' + foreign_source + '\n    cycle: daily\n')
        original = config.read_bytes()
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        loaded = await async_hass_config_yaml(hass)
        for domain in ('utility_meter', 'homekit'):
            self.assertTrue(await async_setup_component(hass, domain, loaded))
        await hass.async_block_till_done()
        await hass.services.async_call('homekit', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        await self._flush_documents()
        entry = next(e for e in hass.config_entries.async_entries('homekit') if e.data['name'] == 'Synthetic Bridge')
        foreign_entry = next(e for e in hass.config_entries.async_entries('homekit') if e.data['name'] == 'Foreign Bridge')
        identity = self.module('native_homekit_filter')._bridge_identity(entry)
        foreign_identity = self.module('native_homekit_filter')._bridge_identity(foreign_entry)
        foreign_bridge = foreign_entry.runtime_data.homekit
        def aid(entity_id):
            matches = [aid for aid, accessory in entry.runtime_data.homekit.bridge.accessories.items()
                       if getattr(accessory, 'entity_id', None) == entity_id]
            self.assertEqual(len(matches), 1)
            return matches[0]
        original_aid = aid(old)
        def meters():
            return {e._parent_meter: e for p in async_get_platforms(hass, 'utility_meter')
                    for e in p.entities.values() if hasattr(e, '_parent_meter')}
        async def emit(value):
            self.producer._attr_native_value = value
            self.producer.async_write_ha_state()
            await hass.async_block_till_done()
        await emit(5); await emit(10)
        foreign = meters()['p3_foreign_meter']
        before_ids = {k: v.entity_id for k, v in meters().items()}
        async def refresh(): pass
        async def transition(source, target):
            before = {name: (meter.native_value, meter._next_reset)
                      for name, meter in meters().items() if name != 'p3_foreign_meter'}
            self.assertTrue(all(value is not None and deadline is not None
                                for value, deadline in before.values()))
            review = await runtime.native_yaml_prepare(hass, {source: target}, actor_id='external-qa',
                guard=lambda: None, refresh=refresh)
            try:
                result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='external-qa', guard=lambda: None, refresh=refresh)
            finally:
                # The fixture is disposable; preserve only non-private coordinator
                # diagnostics before its temporary directory is removed.
                import json, os
                from pathlib import Path
                journal = self.module('journal').native_journal(hass)
                await journal.initialize()
                record = await journal.load(review['operation_id'])
                if record is not None and os.environ.get('RENAMER_BATCH_EVIDENCE'):
                    path = Path(os.environ['RENAMER_BATCH_EVIDENCE']) / 'external-journal.json'
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, 'w') as output:
                        json.dump({'status': record.status, 'reason': record.reason,
                                   'phases': record.phases,
                                   'adapters': [step.adapter for step in record.plan.steps]}, output)
            self.assertEqual(result['state'], 'applied', result)
            await hass.async_block_till_done()
            self.assertEqual(self.f.entity.async_get(target).id, uuids[old])
            self.assertEqual(self.module('native_homekit_filter')._bridge_identity(entry), identity)
            self.assertEqual(aid(target), original_aid)
            filt = entry.runtime_data.homekit._filter.get_filter()
            self.assertTrue(filt(target)); self.assertFalse(filt(source))
            self.assertIs(foreign_entry.runtime_data.homekit, foreign_bridge)
            self.assertEqual(self.module('native_homekit_filter')._bridge_identity(foreign_entry), foreign_identity)
            foreign_filter = foreign_bridge._filter.get_filter()
            self.assertTrue(foreign_filter(foreign_source)); self.assertFalse(foreign_filter(target))
            for name, (value, deadline) in before.items():
                meter = meters()[name]
                self.assertEqual((meter.native_value, meter._next_reset), (value, deadline))
                self.assertEqual(meter._sensor_source_id, target)
                self.assertIsNotNone(meter._collecting)
            self.assertEqual({k: v.entity_id for k, v in meters().items()}, before_ids)
            self.assertIs(meters()['p3_foreign_meter'], foreign)
            self.assertIn(target, config.read_text())
        await transition(old, new)
        before_increment = {name: meters()[name].native_value for name in ('p3_meter', 'p3_second_meter')}
        await emit(15)
        for name, value in before_increment.items():
            self.assertEqual(meters()[name].native_value, value + 5)
        await transition(new, old)
        before_increment = {name: meters()[name].native_value for name in ('p3_meter', 'p3_second_meter')}
        await emit(20)
        for name, value in before_increment.items():
            self.assertEqual(meters()[name].native_value, value + 5)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual((root / 'template.yaml').read_bytes(), template_before)
