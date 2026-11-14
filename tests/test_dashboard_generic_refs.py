import unittest
from types import SimpleNamespace

from custom_components.ha_entity_renamer.dashboard_plan import plan_dashboard
from custom_components.ha_entity_renamer.dependency_review import dependency_dashboards
from custom_components.ha_entity_renamer.lovelace_native import Dashboard, Snapshot, PreparedDashboard, revision
from custom_components.ha_entity_renamer.reference_planner import PlanError


class GenericDashboardReferences(unittest.TestCase):
    mapping = {'sensor.old': 'sensor.new', 'sensor.new': 'sensor.old'}

    def test_unknown_types_and_nested_typed_fields_are_traversed_once(self):
        source = {'views': [{'cards': [{'type': 'custom:unlisted',
            'style': {'popup': {'entity': 'sensor.old'}},
            'entities': ['sensor.old', {'entity': 'sensor.new'}],
            'tap_action': {'action': 'perform-action',
                'target': {'entity_id': ['sensor.old', 'sensor.new']},
                'data': {'entity_id': 'sensor.old'}},
            'title': 'sensor.old', 'url': 'https://example.invalid/sensor.old',
            'foreign': 'untouched'}]}]}
        plan = plan_dashboard(source, self.mapping)
        self.assertFalse(plan.markers)
        self.assertEqual(len(plan.changes), 6)
        card = plan.value['views'][0]['cards'][0]
        self.assertEqual(card['style']['popup']['entity'], 'sensor.new')
        self.assertEqual(card['entities'], ['sensor.new', {'entity': 'sensor.old'}])
        self.assertEqual(card['tap_action']['target']['entity_id'], ['sensor.new', 'sensor.old'])
        self.assertEqual((card['title'], card['url'], card['foreign']),
                         ('sensor.old', 'https://example.invalid/sensor.old', 'untouched'))
        self.assertEqual(len({c.path for c in plan.changes}), len(plan.changes))
        self.assertEqual(source['views'][0]['cards'][0]['entities'][0], 'sensor.old')

    def test_ambiguity_dynamic_and_token_boundary(self):
        mapping = {'sensor.old': 'sensor.new'}
        plan = plan_dashboard({'views': [{'cards': [
            {'type': 'custom:any', 'entity': 'sensor.old', 'other': 'sensor.old',
             'text': 'sensor.old', 'detail': 'sensor.older',
             'lookup': "states['sensor.' + suffix]"}]}]}, mapping)
        self.assertEqual([m.code for m in plan.markers],
                         ['ambiguous_dashboard_reference', 'dynamic_dashboard_reference'])
        self.assertEqual(plan.markers[0].path[-1], 'other')
        self.assertTrue(all(m.detail for m in plan.markers))
        self.assertEqual(plan.value['views'][0]['cards'][0]['detail'], 'sensor.older')
        self.assertFalse(plan_dashboard({'type': 'custom:unknown'}, mapping).markers)

    def test_jinja_js_and_negative_js(self):
        mapping = {'sensor.old': 'sensor.new'}
        source = {'cards': [{'type': 'custom:any',
            'content': "{{ states('sensor.old') }}", 
            'label': "[[[ return states['sensor.old'].state ]]]"}]}
        plan = plan_dashboard(source, mapping)
        self.assertFalse(plan.markers)
        self.assertEqual(len(plan.changes), 2)
        for expression in ("[[[ return states['sensor.' + suffix] ]]]",
                           "[[[ return helper('sensor.old') ]]]"):
            p = plan_dashboard({'label': expression}, mapping)
            self.assertTrue(p.markers)
            self.assertEqual(p.value['label'], expression)

    def test_filter_regex_conditions_and_nested_foreign_target(self):
        mapping = {'sensor.old': 'sensor.new'}
        config = {'conditions': [{'entity': 'sensor.old', 'state': ['sensor.old', 'on']}],
                  'target': {'area_id': {'popup': {'entity': 'sensor.old'}}},
                  'filter': {'include': [{'name': 'entity_id', 'value': r'^sensor\.old$'}]}}
        result = plan_dashboard(config, mapping)
        self.assertEqual(result.value['conditions'][0]['state'], ['sensor.new', 'on'])
        self.assertEqual(result.value['target']['area_id']['popup']['entity'], 'sensor.new')
        self.assertEqual(result.markers[0].code, 'dynamic_dashboard_selector')
        self.assertEqual(result.markers[0].path[-1], 'value')
        for pattern in ('(a+)+$', '['):
            c = {'filter': {'include': [{'name': 'entity_id', 'value': pattern}]}}
            self.assertTrue(plan_dashboard(c, mapping).markers)

    def test_current_entity_js_rejects_selected_literal_and_extra_code(self):
        from custom_components.ha_entity_renamer.dashboard_custom import plan_button_expression
        good = "[[[ return entity && entity.state === 'on' ? 'up' : 'down'; ]]]"
        self.assertEqual(plan_button_expression(good, self.mapping), (good, None))
        for bad in (good.replace("'up'", "'sensor.old'"),
                    good.replace('; ]]]', '; states[id]; ]]]'),
                    good.replace("'up'", "call()")):
            self.assertIsNotNone(plan_button_expression(bad, self.mapping)[1])

    def test_review_real_change_no_change_and_tampering(self):
        mapping = {'sensor.old': 'sensor.new'}
        before = {'views': [{'cards': [{'type': 'custom:any', 'entity': 'sensor.old'}]}]}
        plan = plan_dashboard(before, mapping)
        snap = Snapshot(Dashboard(None, 'lovelace', 'id'), before, revision(before), (7, 100, 'a' * 64))
        change = PreparedDashboard(snap, plan.value, revision(plan.value))
        self.assertEqual(dependency_dashboards([change], mapping)[0]['references'][0]['occurrences'], 1)
        self.assertEqual(dependency_dashboards([], mapping), [])
        with self.assertRaises(Exception):
            dependency_dashboards([PreparedDashboard(snap, before, revision(before))], mapping)
        with self.assertRaises(PlanError):
            v = {}; v['child'] = v; plan_dashboard(v, mapping)


class GenericDashboardNativeIntegration(unittest.TestCase):
    def test_discovery_review_and_receipt_for_change_and_no_change(self):
        from unittest.mock import patch
        from custom_components.ha_entity_renamer.dashboard_receipts import validate_prepared
        from custom_components.ha_entity_renamer.discovery import _consumer_family
        from custom_components.ha_entity_renamer.lovelace_native import NativeLovelace
        from custom_components.ha_entity_renamer.native_discovery_consumers import ConsumerOutcome, DashboardEvidence
        from custom_components.ha_entity_renamer.native_registry import PreparedRegistryChange
        mapping = {'sensor.old': 'sensor.new'}
        before = {'views': [{'cards': [{'type': 'custom:foreign', 'entity': 'sensor.old'}]}]}
        snap = Snapshot(Dashboard(None, 'lovelace', 'id'), before, revision(before), (7, 100, 'a' * 64))
        outcome = ConsumerOutcome('COMPLETE', '', DashboardEvidence((snap,), (), 'inventory', ()))
        family = _consumer_family('dashboard', outcome, mapping)
        self.assertEqual((family.status, family.blockers), ('COMPLETE', ()))
        native = NativeLovelace(None)
        planned = plan_dashboard(before, mapping)
        change = native.prepare(snap, planned)
        self.assertEqual(len(dependency_dashboards([change], mapping)), 1)
        registry = PreparedRegistryChange('entity', 'registry-id',
            '{"entity_id":"sensor.old"}', '{"entity_id":"sensor.new"}')
        composition = SimpleNamespace(prepared_digests=(('registry-ref', 'digest'),))
        preparations = SimpleNamespace(resolve_composition=lambda _: ('baseline', composition),
                                       resolve=lambda *_: object())
        step = SimpleNamespace(adapter='registry', prepared_ref='registry-ref')
        chain = SimpleNamespace(preparations=preparations, baseline='baseline',
            plan=SimpleNamespace(native_inputs='inputs', steps=(step,)),
            original_receipt=SimpleNamespace(source_files=(('.storage/lovelace', 'a' * 64, 7, 100),)))
        with patch('custom_components.ha_entity_renamer.prepared.decode_change', return_value=registry):
            validate_prepared(chain, change)
            with self.assertRaises(Exception):
                validate_prepared(chain, PreparedDashboard(snap, before, revision(before)))
            with self.assertRaises(Exception):
                validate_prepared(chain, PreparedDashboard(snap, {}, revision({})))
        unchanged = {'views': [{'cards': [{'type': 'custom:foreign', 'title': 'A'}]}]}
        no_change = plan_dashboard(unchanged, mapping)
        self.assertFalse(no_change.markers)
        self.assertFalse(no_change.changes)
        self.assertEqual(dependency_dashboards([], mapping), [])

    def test_registered_yaml_route_qualifies_source_and_foreign_bytes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from custom_components.ha_entity_renamer.dashboard_yaml_semantics import qualify_dashboard_yaml
        from custom_components.ha_entity_renamer.yaml_plan import plan_yaml
        mapping = {'sensor.old': 'sensor.new'}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'configuration.yaml').write_text('{}\n')
            raw = ("views:\n- cards:\n  - type: custom:other\n"
                   "    entity: sensor.old\n    title: sensor.old\n").encode()
            (root / 'board.yaml').write_bytes(raw)
            plan = plan_yaml(root, mapping, yaml_dashboards=('board.yaml',))
            before, after = qualify_dashboard_yaml(plan, 'board.yaml', raw, mapping)
            self.assertEqual(before['views'][0]['cards'][0]['entity'], 'sensor.old')
            self.assertEqual(after['views'][0]['cards'][0]['entity'], 'sensor.new')
            self.assertEqual(after['views'][0]['cards'][0]['title'], 'sensor.old')
            with self.assertRaises(ValueError):
                qualify_dashboard_yaml(plan, 'board.yaml', raw + b'\n', mapping)


if __name__ == '__main__': unittest.main()
