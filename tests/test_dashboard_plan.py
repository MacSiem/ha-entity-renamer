"""Run after owner integrates modules into custom_components package."""
import unittest

from custom_components.ha_entity_renamer.dashboard_plan import plan_dashboard
from custom_components.ha_entity_renamer.reference_planner import PlanError


class DashboardPlanTests(unittest.TestCase):
    mapping = {'sensor.old': 'sensor.new', 'camera.old': 'camera.new'}

    def plan(self, cards):
        return plan_dashboard({'views': [{'cards': cards}]}, self.mapping)

    def test_optimizer_bounded_profile_and_power_reference(self):
        cards = [{'type': 'custom:ha-energy-optimizer', 'power_entity': 'sensor.old',
                   'energy_price': 0.8, 'currency': 'sensor.old', 'energy_tariff_mode': 'flat',
                   'peak_rate': 0.8, 'off_peak_rate': 0.4}]
        result = self.plan(cards)
        self.assertFalse(result.markers)
        self.assertEqual(1, len(result.changes))
        self.assertEqual('sensor.new', result.value['views'][0]['cards'][0]['power_entity'])
        self.assertEqual('sensor.old', result.value['views'][0]['cards'][0]['currency'])
        for card in ({'type': 'custom:ha-network-map', 'entity': 'sensor.old'},
                     {'type': 'custom:ha-smart-reports', 'energy_entity': 'sensor.old'},
                     {'type': 'custom:ha-energy-optimizer', 'power_entity': '{{ states(x) }}'},
                     {'type': 'custom:ha-unknown'}):
            with self.subTest(card=card):
                self.assertTrue(self.plan([card]).markers)

        for mapping in ({'sun.sun': 'sun.other'}, {'sun.other': 'sun.sun'}):
            with self.subTest(mapping=mapping):
                self.assertTrue(plan_dashboard({'views': [{'cards': cards}]}, mapping).markers)
        with self.assertRaises(PlanError):
            plan_dashboard({'views': [{'cards': cards}]}, {'sensor.old': 'switch.new'})

    def test_energy_reports_profiles_and_runtime_dependencies(self):
        for kind in ('energy-insights', 'energy-email', 'smart-reports'):
            card = {'type': 'custom:ha-' + kind, 'currency': 'PLN', 'energy_price': 0.8}
            self.assertFalse(self.plan([card]).markers)
            self.assertFalse(self.plan([card]).changes)
        email = {'views': [{'cards': [{'type': 'custom:ha-energy-email'}]}]}
        for old in ('sensor.energy_report_devices', 'input_text.energy_email_price',
                    'automation.send_daily_energy_report'):
            with self.subTest(old=old):
                self.assertTrue(plan_dashboard(email, {old: old + '_renamed'}).markers)
                self.assertTrue(plan_dashboard(email, {old + '_renamed': old}).markers)

    def test_tools_defaults_preserve_unknown_profiles_and_selectors(self):
        for name in ('purge-cache yaml-checker data-exporter baby-tracker log-email '
                     'network-map trace-viewer automation-analyzer storage-monitor device-health '
                     'sentence-manager encoding-fixer frigate-privacy vacuum-water-monitor').split():
            with self.subTest(name=name):
                self.assertFalse(self.plan([{'type': 'custom:ha-' + name}]).markers)
                self.assertTrue(self.plan([{'type': 'custom:ha-' + name, 'entity': 'sensor.old'}]).markers)
        card = {'views': [{'cards': [{'type': 'custom:ha-device-health'}]}]}
        self.assertTrue(plan_dashboard(card, {'sensor.old': 'sensor.old_battery'}).markers)
        card = {'views': [{'cards': [{'type': 'custom:ha-vacuum-water-monitor'}]}]}
        self.assertTrue(plan_dashboard(card, {'sensor.roborock_q7_battery': 'sensor.other'}).markers)

    def test_nested_native_only_references(self):
        source = {'views': [{'title': 'sensor.old', 'sections': [{'type': 'grid',
                  'cards': [{'type': 'vertical-stack', 'cards': [
                      {'type': 'entities', 'title': 'sensor.old', 'entities': [
                          'sensor.old', {'entity': 'sensor.old', 'name': 'sensor.old'}]},
                      {'type': 'picture-entity', 'entity': 'sensor.old',
                       'camera_image': 'camera.old', 'name': 'sensor.old',
                       'image': '/local/sensor.old.png'}]}]}]}]}
        result = plan_dashboard(source, self.mapping)
        self.assertFalse(result.markers)
        self.assertEqual(4, len(result.changes))
        self.assertEqual('sensor.old', source['views'][0]['title'])
        nested = result.value['views'][0]['sections'][0]['cards'][0]['cards']
        self.assertEqual('sensor.new', nested[0]['entities'][0])
        self.assertEqual('sensor.old', nested[0]['entities'][1]['name'])
        self.assertEqual('/local/sensor.old.png', nested[1]['image'])

    def test_renamer_presentation_config_has_no_entity_dependencies(self):
        card = {'type': 'custom:ha-entity-renamer', 'title': 'sensor.old',
                'show_help': False, 'show_support': False, 'language': 'pl',
                'default_tab': 'automatic', 'tabs': ['devices', 'automatic', 'queue'],
                'grid_options': {'columns': 6, 'rows': 'auto'}}
        result = self.plan([card])
        self.assertFalse(result.markers)
        self.assertFalse(result.changes)
        self.assertEqual(result.value['views'][0]['cards'][0], card)

    def test_renamer_visibility_retains_native_reference_semantics(self):
        result = self.plan([{'type': 'custom:ha-entity-renamer',
            'visibility': [{'condition': 'state', 'entity': 'sensor.old', 'state': 'on'}]}])
        self.assertFalse(result.markers)
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.value['views'][0]['cards'][0]['visibility'][0]['entity'], 'sensor.new')

    def test_renamer_unknown_or_malformed_config_remains_blocked(self):
        for extra in ({'entity': 'sensor.old'}, {'template': '{{ value }}'},
                      {'tabs': ['custom']}, {'show_help': 'false'},
                      {'grid_options': {'columns': 'sensor.old'}},
                      {'grid_options': {'custom': 1}}, {'default_tab': 'custom'},
                      {'tap_action': {'action': 'toggle'}}):
            with self.subTest(extra=extra):
                result = self.plan([{'type': 'custom:ha-entity-renamer', **extra}])
                self.assertTrue(result.markers)
                self.assertFalse(result.changes)

    def test_statistics_external_ids_preserved(self):
        result = self.plan([{'type': 'statistics-graph', 'entities': [
            'sensor.old', 'provider:external', {'entity': 'sensor.old', 'name': 'sensor.old'}]}])
        self.assertFalse(result.markers)
        self.assertEqual(2, len(result.changes))
        self.assertEqual('provider:external', result.value['views'][0]['cards'][0]['entities'][1])

    def test_target_service_data_and_descriptions(self):
        result = self.plan([{'type': 'button', 'entity': 'sensor.old',
            'description': 'sensor.old', 'tap_action': {'action': 'perform-action',
            'perform_action': 'homeassistant.update_entity',
            'target': {'entity_id': ['sensor.old'], 'label_id': ['sensor.old']},
            'data': {'entity_id': 'sensor.old'}}}])
        self.assertFalse(result.markers)
        self.assertEqual(3, len(result.changes))
        target = result.value['views'][0]['cards'][0]['tap_action']['target']
        self.assertEqual(['sensor.old'], target['label_id'])

    def test_recursive_conditions_and_reference_comparisons(self):
        result = self.plan([{'type': 'conditional', 'conditions': [{'condition': 'and',
            'conditions': [{'condition': 'numeric_state', 'entity': 'sensor.old',
                            'above': 'sensor.old'},
                           {'entity': 'sensor.old', 'state': ['off', 'sensor.old']}]}],
            'card': {'type': 'entity', 'entity': 'sensor.old'}}])
        self.assertFalse(result.markers)
        self.assertEqual(5, len(result.changes))

    def test_custom_card_no_rewrite_even_entity_property(self):
        result = self.plan([{'type': 'custom:template-card', 'entity': 'sensor.old',
                             'template': "states['sensor.' + name]"}])
        self.assertFalse(result.changes)
        self.assertEqual('custom_or_unknown_dashboard_component', result.markers[0].code)

    def test_unknown_and_dynamic_block_without_literal_source(self):
        for card in [
            {'type': 'new-native-card', 'entities': []},
            {'type': 'button', 'entity': '{{ prefix ~ name }}'},
            {'type': 'entity', 'entity': 'sensor.old', 'unknown': 7},
            {'type': 'button', 'tap_action': {'action': 'fire-dom-event'}},
            {'type': 'button', 'tap_action': {'action': 'perform-action',
             'perform_action': 'script.run', 'data': {'some_dynamic_parameter': 'name'}}},
        ]:
            with self.subTest(card=card): self.assertTrue(self.plan([card]).markers)

    def test_markdown_static_jinja_only(self):
        result = self.plan([{'type': 'markdown', 'entity_ids': ['sensor.old'],
            'content': "label sensor.old {{ states('sensor.old') }}"}])
        self.assertFalse(result.markers)
        self.assertEqual(2, len(result.changes))
        self.assertIn('label sensor.old', result.value['views'][0]['cards'][0]['content'])

    def test_urls_labels_and_empty_dashboard(self):
        result = self.plan([{'type': 'button', 'name': 'sensor.old',
            'tap_action': {'action': 'url', 'url_path': 'https://example.invalid/sensor.old'}}])
        self.assertFalse(result.changes)
        self.assertFalse(result.markers)
        self.assertFalse(plan_dashboard({'views': []}, self.mapping).markers)

    def test_cycles_rejected_before_schema_walk(self):
        value = {}; value['views'] = [value]
        with self.assertRaises(PlanError): plan_dashboard(value, self.mapping)

    def test_strategy_and_features_are_coverage_blockers(self):
        self.assertTrue(plan_dashboard({'strategy': {'type': 'custom:dynamic'}}, self.mapping).markers)
        self.assertTrue(self.plan([{'type': 'tile', 'entity': 'sensor.old',
                                    'features': [{'type': 'custom:feature'}]}]).markers)


if __name__ == '__main__': unittest.main()
