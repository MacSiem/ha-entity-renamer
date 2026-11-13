import unittest
from custom_components.ha_entity_renamer.dashboard_plan import plan_dashboard


class CustomDashboardTests(unittest.TestCase):
    def test_owned_slots_inverse_and_foreign(self):
        original = {'views': [{'cards': [
            {'type': 'custom:button-card', 'entity': 'sensor.old',
             'name': 'sensor.old', 'styles': {'card': ['color: red']},
             'triggers_update': ['sensor.old'],
             'variables': {'entity_ids': ['sensor.old'], 'floor_entity_ids': {'up': ['sensor.old']}},
             'label': "[[[ return states['sensor.old'].state; ]]]",
             'tap_action': {'action': 'perform-action', 'perform_action': 'homeassistant.update_entity',
                            'target': {'entity_id': 'sensor.old'}}},
            {'type': 'custom:bubble-card', 'entity': 'sensor.old',
             'sub_button': [{'entity': 'sensor.old', 'conditions': [{'entity': 'sensor.old', 'state': 'on'}]}]},
        ]}]}
        forward = plan_dashboard(original, {'sensor.old': 'sensor.new'})
        self.assertFalse(forward.markers)
        self.assertEqual(len(forward.changes), 9)
        self.assertEqual(forward.value['views'][0]['cards'][0]['name'], 'sensor.old')
        reverse = plan_dashboard(forward.value, {'sensor.new': 'sensor.old'})
        self.assertFalse(reverse.markers)
        self.assertEqual(reverse.value, original)

    def test_dynamic_js_and_unknown_variable_block(self):
        for extra in ({'label': "[[[ return states[prefix + 'old'].state; ]]]"},
                      {'variables': {'unknown': 'sensor.old'}},
                      {'custom_fields': {'x': 'sensor.old'}}):
            result = plan_dashboard({'views': [{'cards': [{'type': 'custom:button-card', **extra}]}]},
                                    {'sensor.old': 'sensor.new'})
            self.assertTrue(result.markers)
            self.assertFalse(result.changes)

if __name__ == '__main__': unittest.main()
