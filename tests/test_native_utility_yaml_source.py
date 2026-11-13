"""Utility YAML source qualification is exact and never grants activation."""
import unittest
from copy import deepcopy
from custom_components.ha_entity_renamer.native_utility_yaml_projection import source_only
from custom_components.ha_entity_renamer.yaml_validation_worker import changed_domains

class UtilitySourceTests(unittest.TestCase):
    def test_exact_source_only_preserves_all_other_meters_and_fields(self):
        before = {'utility_meter': {'daily': {'source':'sensor.old', 'cycle':'daily'},
                                    'foreign': {'source':'sensor.foreign', 'offset':'00:30'}}}
        after = deepcopy(before)
        after['utility_meter']['daily']['source'] = 'sensor.new'
        self.assertTrue(source_only(before, after, {'sensor.old':'sensor.new'}))
        self.assertEqual(changed_domains(before, after), (['utility_meter'], ['utility_meter']))
        after['utility_meter']['foreign']['offset'] = '00:31'
        self.assertFalse(source_only(before, after, {'sensor.old':'sensor.new'}))

    def test_unmapped_change_missing_source_and_noop_are_rejected(self):
        before = {'utility_meter': {'daily': {'source':'sensor.old'}}}
        for after, mapping in (({'utility_meter': {'daily': {'source':'sensor.other'}}}, {'sensor.old':'sensor.new'}),
                               ({'utility_meter': {'daily': {}}}, {'sensor.old':'sensor.new'}),
                               (deepcopy(before), {'sensor.old':'sensor.new'}),
                               ({'utility_meter': {'daily': {'source':'sensor.new'}}}, {})):
            with self.subTest(after=after):
                self.assertFalse(source_only(before, after, mapping))

    def test_native_yaml_wrapped_mapping_and_strings_are_supported(self):
        class NativeMap(dict): pass
        class NativeString(str): pass
        before = NativeMap(utility_meter=NativeMap(daily=NativeMap(source=NativeString('sensor.old'))))
        after = NativeMap(utility_meter=NativeMap(daily=NativeMap(source=NativeString('sensor.new'))))
        self.assertTrue(source_only(before, after, {'sensor.old':'sensor.new'}))

    def test_boolean_and_integer_are_not_the_same_yaml_value(self):
        before = {'utility_meter': {'daily': {'source':'sensor.old', 'net_consumption':False}}}
        after = {'utility_meter': {'daily': {'source':'sensor.new', 'net_consumption':0}}}
        self.assertFalse(source_only(before, after, {'sensor.old':'sensor.new'}))
