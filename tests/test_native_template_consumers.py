"""Actual native consumers and two ordinary producer events; owner runs FIFO.

No product apply/recovery/own-ID acceptance is claimed. The native setup and
native negative detach are real; no helper output/field/cache seeds or callbacks.
"""
from datetime import timedelta
import importlib
import logging
from types import MappingProxyType
import unittest
import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateConsumerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start
    _flush_documents = fixture.NativeHelperQuiescenceTests._flush_documents

    async def prepare(self, kind, *, foreign_runtime=False, sibling=False):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.setup import async_setup_component
        hass, root = self.f.hass, self.f.root
        self.assertTrue(await async_setup_component(hass, 'sensor', {'sensor': []}))
        class Producer(SensorEntity):
            _attr_unique_id = 'template-runtime-producer'
            _attr_name = 'Template runtime producer'
            _attr_should_poll = False
            _attr_native_value = 1
        self.producer = Producer()
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([self.producer])
        self.source_id = self.producer.entity_id
        state = "{{ states('" + self.source_id + "') | int(0) * 2 }}"
        if kind == 'yaml':
            text = '- unique_id: scope\n  sensor:\n  - unique_id: value\n    name: Runtime consumer\n    state: "' + state + '"\n'
            if sibling:
                text += '  binary_sensor:\n  - unique_id: flag\n    name: Runtime flag\n    state: "{{ states(\'' + self.source_id + '\') | int(0) > 0 }}"\n'
            (root / 'template.yaml').write_text(text)
            (root / 'configuration.yaml').write_text('template: !include template.yaml\n')
            cfg = await async_hass_config_yaml(hass)
            if foreign_runtime:
                cfg['template'][0]['sensor'][0]['state'] = state.replace('* 2', '* 3')
            self.assertTrue(await async_setup_component(hass, 'template', cfg))
            self.consumer_key, self.entry_id = 'scope-value', None
        else:
            from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
            # A real changed YAML source gives the existing checker an operation;
            # ConfigEntry itself remains ORIGINAL full-three snapshot evidence.
            (root / 'configuration.yaml').write_text('script:\n  source_probe:\n    sequence:\n    - variables:\n        sample: "' + state + '"\n')
            entry = ConfigEntry(domain='template', title='Runtime consumer', data={},
                options={'template_type': 'sensor', 'name': 'Runtime consumer', 'state': state},
                version=TemplateConfigFlowHandler.VERSION,
                minor_version=TemplateConfigFlowHandler.MINOR_VERSION,
                source='user', discovery_keys=MappingProxyType({}), subentries_data=[],
                unique_id='template-runtime-entry')
            await hass.config_entries.async_add(entry)
            self.entry_id = self.consumer_key = entry.entry_id
        await hass.async_block_till_done()
        from homeassistant.helpers.entity_platform import async_get_platforms
        self.consumer = next(e for p in async_get_platforms(hass, 'template')
            for e in p.entities.values() if e.unique_id == self.consumer_key)
        self.consumer_uuid = self.f.entity.async_get(self.consumer.entity_id).id
        await self._flush_documents(initialize=True)
        planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')
        validation = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_validation')
        self.plan = planner.plan_yaml(root, {self.source_id: 'sensor.accepted_next'})
        self.assertTrue(self.plan.ready, self.plan.issues)
        self.receipt, self.evidence = await validation.async_validate_proposed_with_sources(
            hass, self.plan, root.parent)

    async def observe(self):
        try:
            module = importlib.import_module(fixture.fixtures.PACKAGE + '.native_template_consumers')
        except ModuleNotFoundError as error:
            if error.name != fixture.fixtures.PACKAGE + '.native_template_consumers':
                raise
            self.fail('actual native Template consumer observer is not implemented')
        return await module.async_observe_template_consumers(
            self.f.hass, self.receipt, self.evidence, guard=lambda: None)

    def consumer_row(self, observed):
        return next(r for r in observed.rows if r.kind == 'entity'
                    and r.entity_id == self.consumer.entity_id)

    async def emit(self, value, multiplier=2):
        self.producer._attr_native_value = value
        self.producer.async_write_ha_state()
        await self.f.hass.async_block_till_done()
        self.assertEqual(self.f.hass.states.get(self.consumer.entity_id).state,
                         str(value * multiplier))

    async def check_two_events(self):
        for value in (5, 7):
            await self.emit(value)
            observed = await self.observe()
            row = self.consumer_row(observed)
            self.assertTrue(row.consumer_verified, row.reason)
            self.assertTrue(row.source_verified, row.reason)
            self.assertEqual(row.dependencies, (self.source_id,))
            self.assertEqual(row.registry_id, self.consumer_uuid)
            self.assertFalse(observed.migration_ready)
            self.assertFalse(observed.family_complete)

    async def test_config_entry_source_real_consumer_and_two_producer_events(self):
        await self.prepare('entry')
        await self.check_two_events()

    async def test_yaml_source_real_consumer_and_two_producer_events(self):
        await self.prepare('yaml')
        await self.check_two_events()

    async def test_all_sibling_platforms_and_source_blocks_remain_visible_unknown(self):
        await self.prepare('yaml', sibling=True)
        observed = await self.observe()
        self.assertTrue(self.consumer_row(observed).source_verified)
        foreign = [r for r in observed.rows if r.kind == 'entity'
                   and r.platform == 'binary_sensor']
        self.assertEqual(len(foreign), 1)
        self.assertFalse(foreign[0].consumer_verified)
        self.assertTrue(any(r.kind == 'source' and r.platform == 'template.state'
                            for r in observed.rows))
        self.assertFalse(observed.family_complete)

    async def test_same_source_key_cannot_bless_foreign_native_template_body(self):
        await self.prepare('yaml', foreign_runtime=True)
        await self.emit(5, multiplier=3)
        observed = await self.observe()
        row = self.consumer_row(observed)
        self.assertTrue(row.consumer_verified, row.reason)
        self.assertFalse(row.source_verified)
        self.assertEqual(row.reason, 'source_semantics_mismatch')

    async def test_real_detach_is_detected_even_when_listeners_projection_survives(self):
        await self.prepare('entry')
        info = self.consumer._template_result_info
        old = info.listeners
        info._track_state_changes.async_remove()  # actual native cleanup, fixture only
        self.assertEqual(info.listeners, old)
        row = self.consumer_row(await self.observe())
        self.assertFalse(row.consumer_verified)
        self.assertFalse(row.source_verified)
        self.assertEqual(row.reason, 'native_registration_missing')

    async def test_extra_native_keyed_registration_cannot_hide_behind_original_detach(self):
        from homeassistant.helpers import event
        await self.prepare('entry')
        info = self.consumer._template_result_info
        remove = event.async_track_state_change_event(self.f.hass,
            'sensor.foreign', info._refresh)  # real native method, no fake callback
        try:
            row = self.consumer_row(await self.observe())
            self.assertFalse(row.consumer_verified)
            self.assertEqual(row.reason, 'native_registration_extra')
        finally:
            remove()

    async def test_late_source_edit_refuses_original_fence(self):
        await self.prepare('yaml')
        path = self.f.root / 'template.yaml'
        path.write_text(path.read_text() + '# foreign edit\n')
        with self.assertRaises(ValueError):
            await self.observe()
