import unittest
from custom_components.ha_entity_renamer.energy_plan import plan_energy


class EnergyPlanTests(unittest.TestCase):
    def test_grid_consumers_prices_and_inclusion_without_double_counting(self):
        source = {'energy_sources': [{'type': 'grid', 'stat_energy_from': 'sensor.old',
                  'entity_energy_price': 'sensor.price', 'number_energy_price': 0.75,
                  'stat_cost': None, 'name': 'sensor.old'}],
                  'device_consumption': [{'stat_consumption': 'sensor.load', 'included_in_stat': 'sensor.old'}]}
        result = plan_energy(source, {'sensor.old': 'sensor.new', 'sensor.price': 'sensor.price_new', 'sensor.load': 'sensor.load_new'})
        self.assertFalse(result.markers)
        self.assertEqual(len(result.value['energy_sources']), 1)
        self.assertEqual(len(result.value['device_consumption']), 1)
        grid = result.value['energy_sources'][0]
        self.assertEqual(grid['stat_energy_from'], 'sensor.new')
        self.assertEqual(grid['entity_energy_price'], 'sensor.price_new')
        self.assertEqual(grid['number_energy_price'], 0.75)
        self.assertEqual(grid['name'], 'sensor.old')
        self.assertIsNone(grid['stat_cost'])
        self.assertEqual(result.value['device_consumption'][0]['included_in_stat'], 'sensor.new')
        self.assertEqual(source['energy_sources'][0]['stat_energy_from'], 'sensor.old')

    def test_battery_power_config_and_water_consumers(self):
        source = {'energy_sources': [{'type': 'battery', 'stat_soc': 'sensor.soc',
                   'power_config': {'stat_rate_from': 'sensor.power'}, 'capacity': 12}],
                  'device_consumption_water': [{'stat_consumption': 'sensor.water'}]}
        result = plan_energy(source, {'sensor.soc': 'sensor.new_soc', 'sensor.power': 'sensor.new_power', 'sensor.water': 'sensor.new_water'})
        self.assertEqual(len(result.changes), 3)
        self.assertFalse(result.markers)
        self.assertEqual(result.value['energy_sources'][0]['capacity'], 12)

    def test_external_statistics_preserved(self):
        source = {'device_consumption': [{'stat_consumption': 'external:meter'}]}
        self.assertEqual(plan_energy(source, {'sensor.old': 'sensor.new'}).value, source)

    def test_unknown_field_prevents_complete_coverage(self):
        result = plan_energy({'future_ref': 'sensor.old'}, {'sensor.old': 'sensor.new'})
        self.assertTrue(result.markers)
        self.assertEqual(result.value['future_ref'], 'sensor.old')

    def test_empty_energy_is_valid_without_invented_sources(self):
        result = plan_energy({'energy_sources': [], 'device_consumption': []}, {'sensor.old': 'sensor.new'})
        self.assertFalse(result.markers)
        self.assertFalse(result.changes)

    def test_malformed_reference_rejected(self):
        with self.assertRaises(ValueError): plan_energy({'stat_soc': {'x': 'sensor.old'}}, {'sensor.old': 'sensor.new'})


if __name__ == '__main__': unittest.main()
