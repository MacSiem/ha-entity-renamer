"""Only owned filter/source edits receive external source proof."""
import copy
import unittest
from custom_components.ha_entity_renamer.yaml_validation_worker import _homekit_filter_only
from custom_components.ha_entity_renamer.native_utility_yaml_projection import source_only

class ExternalSourceProofTests(unittest.TestCase):
    def test_homekit_preserves_foreign_options_and_other_bridges(self):
        before={'homekit':[{'name':'one','filter':{'exclude_entities':['sensor.before']}},
                           {'name':'two','filter':{'include_domains':['light']}}]}
        after=copy.deepcopy(before)
        after['homekit'][0]['filter']['exclude_entities']=['sensor.after']
        self.assertTrue(_homekit_filter_only(before,after,{'sensor.before':'sensor.after'}))
        after['homekit'][1]['name']='changed'
        self.assertFalse(_homekit_filter_only(before,after,{'sensor.before':'sensor.after'}))

    def test_utility_rejects_foreign_field_change(self):
        before={'utility_meter':{'a':{'source':'sensor.before','cycle':'daily'},
                                 'b':{'source':'sensor.other','cycle':'weekly'}}}
        after=copy.deepcopy(before);after['utility_meter']['a']['source']='sensor.after'
        self.assertTrue(source_only(before,after,{'sensor.before':'sensor.after'}))
        after['utility_meter']['b']['cycle']='monthly'
        self.assertFalse(source_only(before,after,{'sensor.before':'sensor.after'}))
