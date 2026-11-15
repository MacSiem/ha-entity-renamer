import unittest
from custom_components.ha_entity_renamer.helper_plan import plan_helper

class HomekitHelperPlanTests(unittest.TestCase):
    def setUp(self):
        self.before = {'domain':'homekit', 'identity':{'entry_id':'entry','source':'import'},
            'data':{'name':'Bridge','port':21199}, 'subentries':{},
            'options':{'mode':'bridge','filter':{'exclude_entities':['sensor.old','sensor.foreign']}}}
    def test_filter_forward_inverse(self):
        f=plan_helper(self.before, {'sensor.old':'sensor.new'})
        self.assertFalse(f.markers)
        self.assertEqual(f.after['options']['filter']['exclude_entities'], ['sensor.new','sensor.foreign'])
        b=plan_helper(f.after, {'sensor.new':'sensor.old'})
        self.assertFalse(b.markers)
        self.assertEqual(b.after,self.before)
    def test_ui_and_ambiguous_configs_refuse(self):
        from copy import deepcopy
        for mutator in (lambda v:v['identity'].update(source='user'),
                        lambda v:v['options']['filter']['exclude_entities'].append('sensor.new'),
                        lambda v:v['options'].update(entity_config={'sensor.old':{'name':'x'}})):
            v=deepcopy(self.before);mutator(v)
            self.assertTrue(plan_helper(v, {'sensor.old':'sensor.new'}).markers)
