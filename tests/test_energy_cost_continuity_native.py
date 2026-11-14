"""Native Energy cost rebind must preserve the running cost and listener."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import logging

from test_controlled_powercalc_actual_native import ControlledActualGraphTests


class EnergyCostContinuityNativeTests(ControlledActualGraphTests):
    async def _setup_vendor_forward(self):
        from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
        from homeassistant.helpers.entity_component import EntityComponent
        class Source(SensorEntity):
            _attr_should_poll = False
            _attr_name = 'Continuity energy'
            _attr_unique_id = 'continuity-energy'
            _attr_device_class = SensorDeviceClass.ENERGY
            _attr_state_class = SensorStateClass.TOTAL_INCREASING
            _attr_native_unit_of_measurement = 'kWh'
            _attr_native_value = Decimal('100')
        self.source = Source()
        component = EntityComponent(logging.getLogger(__name__), 'sensor', self.f.hass, timedelta(seconds=60))
        await component.async_add_entities([self.source])

    async def test_native_cost_rebind_preserves_value_and_delta(self):
        import probatio
        from homeassistant.components.energy.data import async_get_manager, ENERGY_SOURCE_SCHEMA
        from homeassistant.components.energy.sensor import EnergyCostSensor
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.event import async_track_state_change_event
        from homeassistant.setup import async_setup_component
        from custom_components.ha_entity_renamer.energy_cost_continuity import capture, transfer
        hass = self.f.hass
        old = self.source.entity_id
        manager = await async_get_manager(hass)
        prefs = manager.default_preferences()
        prefs['energy_sources'] = probatio.Schema(ENERGY_SOURCE_SCHEMA)([{
            'type':'grid', 'stat_energy_from':old, 'stat_energy_to':None,
            'stat_cost':None, 'entity_energy_price':None, 'number_energy_price':0.75,
            'cost_adjustment_day':0.0}])
        await manager.async_update(prefs)
        await manager._store.async_save(manager.data)
        self.assertTrue(await async_setup_component(hass, 'energy', {}))
        await hass.async_block_till_done()
        for _ in range(200):
            if old in hass.data['energy']['cost_sensors']: break
            await asyncio.sleep(.05)
        cost_id = hass.data['energy']['cost_sensors'][old]
        def costs():
            return [e for p in async_get_platforms(hass, 'energy') for e in p.entities.values()
                    if type(e) is EnergyCostSensor and e.entity_id == cost_id]
        self.assertEqual(len(costs()), 1)
        cost = costs()[0]
        self.source._attr_native_value = Decimal('110')
        self.source.async_write_ha_state()
        await hass.async_block_till_done()
        self.assertAlmostEqual(cost.native_value, 7.5)
        before = cost.native_value
        reset = cost.last_reset
        source_uuid = self.f.entity.async_get(old).id
        cost_registry = self.f.entity.async_get(cost_id)
        row = {'before_source':old, 'after_source':'sensor.continuity_energy_new',
               'source_registry_id':source_uuid, 'entity_id':cost_id,
               'registry_id':cost_registry.id, 'unique_id':cost_registry.unique_id}
        self.f.entity.async_update_entity(old, new_entity_id=row['after_source'])
        await hass.async_block_till_done()
        # Source advances while the old listener still points at its old ID.
        self.source._attr_native_value = Decimal('112')
        self.source.async_write_ha_state()
        await hass.async_block_till_done()
        captured = capture(hass, cost, row)
        zero = []
        def observe(event):
            state = event.data.get('new_state')
            if state and state.state in ('0', '0.0'): zero.append(state.state)
        unsubscribe = async_track_state_change_event(hass, cost_id, observe)
        self.addCleanup(unsubscribe)
        await cost.async_remove()
        updated = deepcopy(manager.data)
        updated['energy_sources'][0]['stat_energy_from'] = row['after_source']
        with transfer(hass, manager, {row['after_source']:(row, captured)}):
            await manager.async_update(updated)
        await manager._store.async_save(manager.data)
        await hass.async_block_till_done()
        self.assertEqual(len(costs()), 1)
        new_cost = costs()[0]
        self.assertIsNot(new_cost, cost)
        self.assertEqual((new_cost.registry_entry.id, new_cost.registry_entry.unique_id),
                         (row['registry_id'], row['unique_id']))
        self.assertEqual(zero, [])
        self.assertEqual(new_cost.last_reset, reset)
        self.assertAlmostEqual(new_cost.native_value, before + 1.5)
        self.source._attr_native_value = Decimal('114')
        self.source.async_write_ha_state()
        await hass.async_block_till_done()
        self.assertAlmostEqual(new_cost.native_value, before + 3.0)

    async def test_transfer_restores_callback_on_exception(self):
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.components.energy.sensor import SensorManager
        from homeassistant.setup import async_setup_component
        from custom_components.ha_entity_renamer.energy_cost_continuity import transfer

        hass = self.f.hass
        manager = await async_get_manager(hass)
        self.assertTrue(await async_setup_component(hass, 'energy', {}))
        await hass.async_block_till_done()
        listeners = [fn for fn in manager._update_listeners
                     if type(getattr(fn, '__self__', None)) is SensorManager
                     and getattr(fn, '__func__', None) is SensorManager._process_manager_data]
        self.assertEqual(len(listeners), 1)
        sensor_manager = listeners[0].__self__
        original = sensor_manager.async_add_entities
        with self.assertRaisesRegex(RuntimeError, 'injected failure'):
            with transfer(hass, manager, {}):
                self.assertIsNot(sensor_manager.async_add_entities, original)
                raise RuntimeError('injected failure')
        self.assertIs(sensor_manager.async_add_entities, original)
