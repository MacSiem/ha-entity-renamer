"""Real Core YAML UtilityMeterSensor factory and restore characterization."""
import importlib
import logging
import unittest
from datetime import timedelta
from decimal import Decimal
import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class UtilityYAMLHandoffNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_yaml_meter_preserves_counter_deadline_and_foreign(self):
        from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component
        base = fixture.NativeBaselineTests(methodName='runTest')
        await base.asyncSetUp()
        self.addAsyncCleanup(base.asyncTearDown)
        hass = base.hass
        class Source(SensorEntity):
            _attr_should_poll = False
            _attr_native_unit_of_measurement = 'kWh'
            _attr_device_class = SensorDeviceClass.ENERGY
            _attr_state_class = SensorStateClass.TOTAL_INCREASING
            def __init__(self, name):
                self._attr_name = name; self._attr_unique_id = name; self._attr_native_value = Decimal('100')
        component = EntityComponent(logging.getLogger(__name__), 'sensor', hass, timedelta(seconds=60))
        source, foreign_source = Source('P3 source'), Source('P3 foreign')
        await component.async_add_entities([source, foreign_source])
        old, new = source.entity_id, 'sensor.p3_source_renamed'
        config = {'utility_meter': {'p3_meter': {'source':old, 'cycle':'daily'},
                                    'p3_second_meter': {'source':old, 'cycle':'weekly'},
                                    'p3_foreign_meter': {'source':foreign_source.entity_id, 'cycle':'daily'}}}
        self.assertTrue(await async_setup_component(hass, 'utility_meter', config))
        await hass.async_block_till_done()
        sensors = lambda: {entity._parent_meter:entity for platform in async_get_platforms(hass, 'utility_meter')
                           for entity in platform.entities.values() if hasattr(entity, '_parent_meter')}
        self.assertEqual(set(sensors()), {'p3_meter','p3_second_meter','p3_foreign_meter'})
        async def emit(value):
            source._attr_native_value=Decimal(value); source.async_write_ha_state(); await hass.async_block_till_done()
        await emit('105'); await emit('110')
        meter, foreign = sensors()['p3_meter'], sensors()['p3_foreign_meter']
        old_value = meter.native_value
        self.assertIsNotNone(old_value)
        adapter = importlib.import_module(fixture.PACKAGE + '.native_utility_yaml_handoff')
        intent=[]
        async def record(rows): intent.append(tuple(sorted(row.meter for row in rows)))
        def guard(): pass
        forward = adapter.UtilityYAMLHandoff.capture(hass, {old:new})
        await forward.detach_group(old, guard=guard, record_intent=record)
        base.entity.async_update_entity(old, new_entity_id=new)
        await hass.async_block_till_done()
        changed_rows = await forward.attach_group(old, guard=guard)
        changed = next(e for e in changed_rows if e._parent_meter == 'p3_meter')
        await hass.async_block_till_done()
        self.assertEqual(changed.native_value, old_value)
        self.assertIs(sensors()['p3_foreign_meter'], foreign)
        await emit('115'); self.assertEqual(changed.native_value, old_value + Decimal('5'))
        reverse = adapter.UtilityYAMLHandoff.capture(hass, {new:old})
        await reverse.detach_group(new, guard=guard, record_intent=record)
        base.entity.async_update_entity(new, new_entity_id=old)
        await hass.async_block_till_done()
        restored_rows = await reverse.attach_group(new, guard=guard)
        restored = next(e for e in restored_rows if e._parent_meter == 'p3_meter')
        await hass.async_block_till_done()
        self.assertEqual(restored.native_value, old_value + Decimal('5'))
        self.assertIs(sensors()['p3_foreign_meter'], foreign)
        self.assertEqual(intent, [('p3_meter','p3_second_meter')]*2)
