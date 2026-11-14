import asyncio, importlib, json
from copy import deepcopy
from hashlib import sha256
from decimal import Decimal
import probatio
from test_controlled_powercalc_actual_native import ControlledActualGraphTests

class EnergyCostUtilityContinuityTests(ControlledActualGraphTests):
    async def test_energy_utility_receipt(self):
        from homeassistant.components.energy.data import async_get_manager, ENERGY_SOURCE_SCHEMA
        from homeassistant.components.energy.sensor import EnergyCostSensor
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component
        hass = self.f.hass
        energy_id = self.entities['fixture-meter-10_energy'].entity_id
        energy = await async_get_manager(hass)
        prefs = energy.default_preferences()
        prefs['energy_sources'] = probatio.Schema(ENERGY_SOURCE_SCHEMA)([{'type':'grid', 'stat_energy_from':energy_id, 'stat_energy_to':None, 'stat_cost':None, 'entity_energy_price':None, 'number_energy_price':0.75, 'cost_adjustment_day':0.0}])
        prefs['device_consumption'] = [{'stat_consumption':energy_id}]
        await energy.async_update(prefs)
        await energy._store.async_save(energy.data)
        self.assertTrue(await async_setup_component(hass, 'energy', {}))
        await hass.async_block_till_done()
        for _ in range(200):
            if energy_id in hass.data['energy']['cost_sensors']: break
            await asyncio.sleep(.05)
        old = hass.data['energy']['cost_sensors'][energy_id]
        new = 'sensor.p4_energy_cost_short_renamed'
        costs = [e for p in async_get_platforms(hass, 'energy') for e in p.entities.values() if type(e) is EnergyCostSensor and e.entity_id == old]
        self.assertEqual(len(costs), 1)
        from homeassistant.config import async_hass_config_yaml
        with (self.f.root / 'configuration.yaml').open('a') as stream:
            stream.write('\nutility_meter:\n  p4_short_meter:\n    source: ' + old + '\n    cycle: weekly\n')
        config = await async_hass_config_yaml(hass)
        if 'utility_meter' in hass.config.components:
            from homeassistant.components.utility_meter import async_setup, CONFIG_SCHEMA, DATA_UTILITY
            self.assertFalse(hass.data[DATA_UTILITY])
            self.assertTrue(await async_setup(hass, CONFIG_SCHEMA(config)))
        else:
            self.assertTrue(await async_setup_component(hass, 'utility_meter', config))
        await hass.async_block_till_done()
        async def pulse():
            for light in self.sources: await light.async_turn_on(brightness=255)
            await hass.async_block_till_done()
            await asyncio.sleep(2)
            for light in self.sources: await light.async_turn_off()
            await hass.async_block_till_done()
        await pulse(); await pulse()
        sensors = lambda: {e._parent_meter:e for p in async_get_platforms(hass, 'utility_meter') for e in p.entities.values() if hasattr(e, '_parent_meter')}
        for _ in range(200):
            meter = sensors().get('p4_short_meter')
            if meter and meter.native_value is not None and Decimal(str(meter.native_value)) > 0 and meter._next_reset is not None: break
            await asyncio.sleep(.05)
        self.assertIsNotNone(meter)
        self.assertIsNotNone(meter._next_reset)
        self.assertGreater(Decimal(str(meter.native_value)), 0)
        source_before = Decimal(hass.states.get(old).state)
        meter_before = Decimal(str(meter.native_value))
        adapter = importlib.import_module('custom_components.ha_entity_renamer.native_utility_yaml_handoff')
        tx = importlib.import_module('custom_components.ha_entity_renamer.transaction')
        sidecar = importlib.import_module('custom_components.ha_entity_renamer.native_utility_yaml_receipt')
        class Prepared:
            def __init__(self): self.plans = {}
            async def create(self, key, payload):
                digest = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                self.plans[key] = {'digest':digest, 'payload':payload}
                return digest
            def resolve(self, key, digest):
                row = self.plans[key]; assert row['digest'] == digest
                return row['payload']
        class Inner:
            def bind_phase_context(self, value): pass
            async def apply(inner, step, guard):
                self.f.entity.async_update_entity(old, new_entity_id=new)
                await hass.async_block_till_done()
            async def inspect(inner, step): return tx.Position.AFTER if self.f.entity.async_get(new) else tx.Position.BEFORE
        def guard(): pass
        prepared = Prepared()
        h = adapter.UtilityYAMLHandoff.capture(hass, {old:new})
        self.assertEqual(len(h.rows), 1)
        step = tx.Step('source','registry','uuid','before','after','prepared')
        plan = tx.Plan('plan',('registry',),('registry',),(step,),'covered',('registry',))
        wrapper = sidecar.UtilityYAMLRegistryAdapter(Inner(),h,prepared,lambda ref:None,'operation',plan.fingerprint,guard)
        wrapper._mapping = lambda step, reverse: {old:new}
        wrapper.bind_phase_context(tx.PhaseContext(tx.Record('operation',plan,phases=('intent',)),0))
        new_energy = 'sensor.p4_energy_short_renamed'
        self.assertEqual(sorted([energy_id, old]), [energy_id, old])
        source_registry_id = self.f.entity.async_get(energy_id).id
        cost_registry = self.f.entity.async_get(old)
        cost_identity = (cost_registry.id, cost_registry.unique_id)
        def observe(label):
            live = h.rows[0].platform.entities[h.rows[0].entity_id]
            before, current = h.rows[0].before, adapter._static(live)
            diff = {k:(before[2].get(k),current[2].get(k)) for k in set(before[2]) | set(current[2]) if before[2].get(k) != current[2].get(k)}
            print('REBIND', label, 'BEFORE', repr(before), 'CURRENT', repr(current), 'DIFF', repr(diff), 'SOURCE', repr(hass.states.get(new)), 'AVAILABLE', live.available, 'LAST', repr(live._last_valid_state), 'FLAGS', repr((live._sensor_delta_values,live._sensor_net_consumption,live._sensor_always_available,live._sensor_periodically_resetting)), flush=True)
        self.f.entity.async_update_entity(energy_id, new_entity_id=new_energy)
        await hass.async_block_till_done()
        self.assertEqual(self.f.entity.async_get(new_energy).id, source_registry_id)
        await wrapper.apply(step,guard)
        observe('after_cost_registry')
        self.assertEqual(await wrapper.inspect(step),tx.Position.AFTER)
        from custom_components.ha_entity_renamer.energy_cost_continuity import capture, transfer
        continuity_row = {'before_source':energy_id, 'after_source':new_energy,
                          'source_registry_id':source_registry_id, 'entity_id':new,
                          'registry_id':cost_identity[0], 'unique_id':cost_identity[1]}
        captured = capture(hass, costs[0], continuity_row)
        zero_events = []
        from homeassistant.helpers.event import async_track_state_change_event
        def observe_cost_event(event):
            state = event.data.get('new_state')
            if state is not None and state.state in ('0', '0.0'):
                zero_events.append(state.state)
        cancel_observer = async_track_state_change_event(hass, new, observe_cost_event)
        self.addCleanup(cancel_observer)
        await costs[0].async_remove()
        await hass.async_block_till_done()
        observe('after_cost_remove')
        updated = deepcopy(energy.data)
        updated['energy_sources'][0]['stat_energy_from'] = new_energy
        updated['device_consumption'][0]['stat_consumption'] = new_energy
        with transfer(hass, energy, {new_energy:(continuity_row,captured)}):
            await energy.async_update(updated)
        await energy._store.async_save(energy.data)
        await hass.async_block_till_done()
        observe('after_preferences')
        self.assertEqual(hass.data['energy']['cost_sensors'][new_energy], new)
        current_cost = [e for p in async_get_platforms(hass, 'energy') for e in p.entities.values() if type(e) is EnergyCostSensor and e.entity_id == new]
        self.assertEqual(len(current_cost), 1)
        self.assertIsNot(current_cost[0], costs[0])
        row = self.f.entity.async_get(new)
        self.assertEqual((row.id, row.unique_id), cost_identity)
        self.assertEqual(current_cost[0]._config['stat_energy_from'], new_energy)
        self.assertEqual(zero_events, [])
        self.assertEqual(Decimal(str(current_cost[0].native_value)), source_before)
        self.assertEqual(Decimal(str(sensors()['p4_short_meter'].native_value)), meter_before)
        before_pulse_inspect = await wrapper.inspect(step)
        print('REBIND_INSPECT_BEFORE_PULSE', repr(before_pulse_inspect), flush=True)
        await pulse()
        for _ in range(200):
            state = hass.states.get(new)
            meter_after = Decimal(str(sensors()['p4_short_meter'].native_value))
            if state and Decimal(state.state) > source_before and meter_after > meter_before: break
            await asyncio.sleep(.05)
        observe('after_pulse_before_inspect')
        self.assertGreater(Decimal(hass.states.get(new).state), source_before)
        self.assertGreater(meter_after, meter_before)
        self.assertEqual(meter_after - meter_before, Decimal(hass.states.get(new).state) - source_before)
        self.assertEqual(before_pulse_inspect, tx.Position.AFTER)
        print('REBIND_INSPECT', repr(await wrapper.inspect(step)), flush=True)
        self.assertEqual(await wrapper.inspect(step),tx.Position.AFTER)
