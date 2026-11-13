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

class ObservedLabelTests(unittest.TestCase):
    def test_bounded_label_reference_spans_and_dynamic_refusal(self):
        import hashlib
        from unittest.mock import patch
        from custom_components.ha_entity_renamer import dashboard_custom as adapter
        ids = [f'sensor.item_{i}' for i in range(24)]
        text = '[[[ const L = [' + ','.join(repr(x) for x in ids[:15]) + '];' + ''.join(
            f" states['{item}'];" for item in ids[15:22]) + ''.join(
            f" states['{item}' + k];" for item in ids[22:]) + ' return L; ]]]'
        normalized = adapter._ENTITY_LITERAL.sub(lambda m: m['q'] + '<entity>' + m['q'], text)
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        with patch.object(adapter, '_OBSERVED_LABEL', digest):
            forward, error = adapter.plan_button_expression(text, {'sensor.item_0': 'sensor.changed'})
            self.assertIsNone(error)
            self.assertIn("'sensor.changed'", forward)
            reverse, error = adapter.plan_button_expression(forward, {'sensor.changed': 'sensor.item_0'})
            self.assertIsNone(error)
            self.assertEqual(reverse, text)
            _, error = adapter.plan_button_expression(text, {'sensor.item_22cry': 'sensor.changed'})
            self.assertEqual(error, 'dynamic_button_state_reference')
            _, error = adapter.plan_button_expression(text + ' ', {'sensor.item_0': 'sensor.changed'})
            self.assertEqual(error, 'unsupported_button_javascript')

class NestedDashboardTests(unittest.TestCase):
    def test_mod_nested_fields_and_popup_content(self):
        original = {'views': [{'sections': [{'type':'grid', 'cards': [
            {'type':'custom:mod-card', 'card_mod': {'style':'color: red'}, 'card':
             {'type':'custom:button-card', 'custom_fields': {'rail': {'do_not_eval': True, 'card':
              {'type':'vertical-stack', 'cards': [{'type':'custom:button-card',
               'variables': {'source_entities':['sensor.old']},
               'tap_action': {'action':'fire-dom-event', 'browser_mod': {'service':'browser_mod.popup',
                   'data': {'title':'sensor.old', 'content': {'type':'entities',
                            'entities':[{'entity':'sensor.old'}]}}}}}]}}}}}]}]}]}
        result = plan_dashboard(original, {'sensor.old':'sensor.new'})
        self.assertFalse(result.markers)
        self.assertEqual(len(result.changes), 2)
        reverse = plan_dashboard(result.value, {'sensor.new':'sensor.old'})
        self.assertFalse(reverse.markers)
        self.assertEqual(reverse.value, original)
