"""Source-only checks for the privately qualified stable Script service."""
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeOwnIDStableServicePlannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='own-id-plan-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'configuration.yaml').write_text(
            'script: !include scripts.yaml\nautomation: !include automations.yaml\n'
            'template: !include template.yaml\n')
        (self.root / 'scripts.yaml').write_text('worker:\n  sequence:\n  - event: own_test\n')
        (self.root / 'template.yaml').write_text(
            '- sensor:\n  - name: Observer\n    state: "{{ states(\'script.worker\') }}"\n')
        self.planner = importlib.import_module(fixture.fixtures.PACKAGE + '.yaml_plan')

    def test_actual_service_literal_stays_service_while_entity_reference_moves(self):
        (self.root / 'automations.yaml').write_text(
            '- id: test\n  triggers: []\n  actions:\n  - action: script.worker\n')
        mapping = {'script.worker': 'script.previous_alias'}
        generic = self.planner.plan_yaml(self.root, mapping)
        self.assertFalse(generic.ready)
        qualified = self.planner.plan_yaml(self.root, mapping, stable_script_services=('script.worker',))
        self.assertTrue(qualified.ready, qualified.issues)
        patches = {patch.path: patch.proposed_bytes for patch in qualified.patches}
        self.assertNotIn('automations.yaml', patches)
        self.assertIn(b"states('script.previous_alias')", patches['template.yaml'])

    def test_service_scalar_shared_with_entity_target_is_refused(self):
        (self.root / 'automations.yaml').write_text(
            '- id: test\n  triggers: []\n  actions:\n'
            '  - action: &shared script.worker\n    target:\n      entity_id: *shared\n')
        plan = self.planner.plan_yaml(self.root, {'script.worker': 'script.previous_alias'},
                                      stable_script_services=('script.worker',))
        self.assertFalse(plan.ready)

    def test_opt_in_owned_service_changes_exact_definition_and_static_call(self):
        (self.root / 'automations.yaml').write_text(
            '- id: test\n  triggers: []\n  actions:\n  - action: script.worker\n')
        plan = self.planner.plan_yaml(self.root, {'script.worker': 'script.new_worker'},
                                      owned_script_services=('script.worker',))
        self.assertTrue(plan.ready, plan.issues)
        patches = {patch.path: patch.proposed_bytes for patch in plan.patches}
        self.assertIn(b'new_worker:', patches['scripts.yaml'])
        self.assertIn(b'action: script.new_worker', patches['automations.yaml'])
        self.assertIn(b"states('script.new_worker')", patches['template.yaml'])

    def test_owned_script_does_not_change_unrelated_script_named_mapping(self):
        (self.root / 'configuration.yaml').write_text(
            'script: !include scripts.yaml\nautomation: !include automations.yaml\n'
            'template: !include template.yaml\nother:\n  script:\n    worker: unrelated\n')
        (self.root / 'automations.yaml').write_text(
            '- id: test\n  triggers: []\n  actions:\n  - action: script.worker\n')
        plan = self.planner.plan_yaml(self.root, {'script.worker': 'script.new_worker'},
                                      owned_script_services=('script.worker',))
        self.assertTrue(plan.ready, plan.issues)
        patches = {patch.path: patch.proposed_bytes for patch in plan.patches}
        self.assertNotIn('configuration.yaml', patches)
        self.assertIn(b'new_worker:', patches['scripts.yaml'])

    def test_owned_script_definition_collision_refuses_without_effect(self):
        (self.root / 'scripts.yaml').write_text(
            'worker:\n  sequence: []\nnew_worker:\n  sequence: []\n')
        (self.root / 'automations.yaml').write_text('[]\n')
        plan = self.planner.plan_yaml(self.root, {'script.worker': 'script.new_worker'},
                                      owned_script_services=('script.worker',))
        self.assertFalse(plan.ready)

    def test_script_key_move_pairs_source_rows_when_sort_order_changes(self):
        program = importlib.import_module(fixture.fixtures.PACKAGE + '.dependent_config_program')
        before = {'automation': [['caller', 'caller-before']],
                  'script': [['alpha', 'selected-before'], ['middle', 'control']]}
        after = {'automation': [['caller', 'caller-after']],
                 'script': [['middle', 'control'], ['zeta', 'selected-after']]}
        mapping = {'script.alpha': 'script.zeta'}
        paired = dict(program._pair_program_rows(before, after, 'script.alpha', mapping, reverse=False))
        self.assertEqual(paired['script'], ((before['script'][0], after['script'][1]),
                                            (before['script'][1], after['script'][0])))
        inverse = dict(program._pair_program_rows(after, before, 'script.alpha', mapping, reverse=True))
        self.assertEqual(inverse['script'], ((after['script'][0], before['script'][1]),
                                             (after['script'][1], before['script'][0])))
        foreign = {'automation': after['automation'],
                   'script': [['middle', 'control'], ['unrelated', 'foreign']]}
        with self.assertRaises(ValueError):
            program._pair_program_rows(before, foreign, 'script.alpha', mapping, reverse=False)
