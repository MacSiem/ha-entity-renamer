"""Only one literal numeric entry depending on the common source is selected."""
from copy import deepcopy
import unittest
from types import SimpleNamespace

from custom_components.ha_entity_renamer.helper_plan import plan_helper
from custom_components.ha_entity_renamer.controlled_template_entry_preparation import prepare_controlled_template_entry
from custom_components.ha_entity_renamer.transaction import Refused


class PreparationTests(unittest.TestCase):
    def test_scope_and_refusals(self):
        row = dict(domain='template', identity=dict(entry_id='entry', unique_id=None, title='numeric',
                   version=1, minor_version=1, source='user', disabled_by=None,
                   pref_disable_new_entities=False, pref_disable_polling=True), data={},
                   options={'template_type': 'sensor', 'name': 'numeric',
                            'state': "{{ states('sensor.source') | int(0) * 2 }}"}, subentries={})
        mapping = {'sensor.source': 'sensor.next', 'sensor.group': 'sensor.next_group'}
        plan = plan_helper(row, mapping)
        selected = prepare_controlled_template_entry((plan,), mapping, 'sensor.source')
        self.assertEqual(selected.mapping, {'sensor.source': 'sensor.next'})
        for plans, source in (((), 'sensor.source'), ((plan, plan), 'sensor.source'),
                              ((plan,), 'sensor.group')):
            with self.assertRaises((Refused, ValueError)):
                prepare_controlled_template_entry(plans, mapping, source)
        for field, value in (('template_type', 'binary_sensor'), ('state', "{{ states(variable) }}")):
            changed = deepcopy(row)
            changed['options'][field] = value
            with self.assertRaises((Refused, ValueError)):
                prepare_controlled_template_entry((plan_helper(changed, mapping),), mapping, 'sensor.source')
