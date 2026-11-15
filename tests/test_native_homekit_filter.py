import unittest
from custom_components.ha_entity_renamer.native_homekit_filter import plan_filter
from custom_components.ha_entity_renamer.transaction import Refused

class HomeKitFilterTests(unittest.TestCase):
    def test_filter_exact_forward_inverse_and_foreign(self):
        before = {'filter': {'exclude_entities': ['sensor.old', 'sensor.foreign'],
                             'exclude_domains': ['light']}, 'name': 'sensor.old'}
        after = plan_filter(before, {'sensor.old': 'sensor.new'})
        self.assertEqual(after.value['filter']['exclude_entities'], ['sensor.new', 'sensor.foreign'])
        self.assertEqual(after.value['name'], 'sensor.old')
        self.assertEqual(plan_filter(after.value, {'sensor.new': 'sensor.old'}).value, before)

    def test_collision_and_dynamic_reference_are_refused(self):
        for values in (['sensor.old', 'sensor.new'], ['sensor.*']):
            with self.subTest(values=values), self.assertRaises(Refused):
                plan_filter({'filter': {'exclude_entities': values}}, {'sensor.old': 'sensor.new'})

if __name__ == '__main__': unittest.main()

class HomeKitReceiptTests(unittest.TestCase):
    def test_worker_admits_only_selected_filter_lists(self):
        from custom_components.ha_entity_renamer.yaml_validation_worker import _homekit_filter_only
        before = {'homekit': [{'name':'Bridge', 'filter': {'exclude_entities':['sensor.old']}}]}
        after = {'homekit': [{'name':'Bridge', 'filter': {'exclude_entities':['sensor.new']}}]}
        self.assertTrue(_homekit_filter_only(before, after, {'sensor.old':'sensor.new'}))
        self.assertFalse(_homekit_filter_only(before, after, {'sensor.other':'sensor.new'}))
        changed_name = {'homekit': [{'name':'Other', 'filter': {'exclude_entities':['sensor.new']}}]}
        self.assertFalse(_homekit_filter_only(before, changed_name, {'sensor.old':'sensor.new'}))
