"""M1.c Powercalc scoped planner acceptance tests, synthetic, no HA APIs.

Run against baseline and proposed isolated source copies. RED is expected on
unsupported group/dynamic selectors and risk of double-counting, GREEN after
adding explicit fail-closed markers. No product or user state is touched.
"""
from copy import deepcopy
import unittest

# Match native structural regressions: synthetic package avoids executing the
# Home Assistant integration __init__, including under native_unit_probe.
import importlib
import os
from pathlib import Path
import sys
from types import ModuleType

COMPONENT = Path(os.environ.get('RENAMER_COMPONENT_DIR',
    str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')))
PACKAGE = '_powercalc_m1c_regression'
package = ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
helper = importlib.import_module(PACKAGE + '.helper_plan')
native = importlib.import_module(PACKAGE + '.helper_native')
plan_helper, rebase_after_listeners = helper.plan_helper, helper.rebase_after_listeners
apply_entry = native.apply_entry

MAP = {'light.source_old': 'light.source_new',
       'sensor.meter_old': 'sensor.meter_new'}


def entry(data=None, options=None):
    return {
        'domain': 'powercalc',
        'identity': {'entry_id': 'synthetic_powercalc_a',
                     'unique_id': 'physical-unchanged-001',
                     'title': 'Room Meter', 'version': 1, 'minor_version': 1,
                     'source': 'user', 'disabled_by': None,
                     'pref_disable_new_entities': False,
                     'pref_disable_polling': False},
        'data': {} if data is None else data,
        'options': {} if options is None else options,
        'subentries': {},
    }


def codes(plan):
    return {marker.code for marker in plan.markers}


class PowercalcDynamicCoverage(unittest.TestCase):
    def test_area_include_without_literal_reference_is_unknown(self):
        selectors = {'include': {'area': 'upper_floor'},
                     'entities': [{'entity_id': 'light.unaffected'}],
                     'create_group': 'upstairs', 'create_domain_groups': ['light'],
                     'force_calculate_group_energy': True, 'group': 'configured_group'}
        for root in ('data', 'options'):
            for field, value in selectors.items():
                with self.subTest(root=root, field=field):
                    # Entire snapshot is literal-free relative to MAP.
                    before = entry(**{root: {field: value}})
                    plan = plan_helper(before, MAP)
                    self.assertEqual(plan.changes, ())
                    self.assertTrue(plan.blocked)
                    self.assertIn((root, field), tuple(marker.path for marker in plan.markers
                        if marker.code == 'powercalc_group_membership_unverified'))

    def test_static_nested_members_without_changed_id_are_still_unknown(self):
        before = entry({'entity_id': 'light.source_old',
                        'entities': [{'entity_id': 'light.unaffected'}]})
        self.assertIn('powercalc_group_membership_unverified', codes(
            plan_helper(before, MAP)))

    def test_create_group_without_old_literal_is_unknown(self):
        plan = plan_helper(entry({'entity_id': 'light.source_old',
                                  'create_group': 'upstairs'}), MAP)
        self.assertIn('powercalc_group_membership_unverified', codes(plan))

    def test_group_energy_aggregation_requires_proof(self):
        plan = plan_helper(entry({'entity_id': 'light.source_old',
                                  'force_calculate_group_energy': True}), MAP)
        self.assertIn('powercalc_group_membership_unverified', codes(plan))

    def test_configured_domain_groups_are_dynamic(self):
        plan = plan_helper(entry({'entity_id': 'light.source_old',
                                  'create_domain_groups': ['light']}), MAP)
        self.assertIn('powercalc_group_membership_unverified', codes(plan))

    def test_group_assignment_in_options_is_not_silently_accepted(self):
        plan = plan_helper(entry({'entity_id': 'light.source_old'},
                                 {'group': 'configured_group'}), MAP)
        self.assertIn('powercalc_group_membership_unverified', codes(plan))

    def test_forced_derived_energy_with_existing_meter_requires_proof(self):
        for root in ('data', 'options'):
            with self.subTest(root=root):
                plan = plan_helper(entry(**{root: {
                    'power_sensor_id': 'sensor.physical_power',
                    'energy_sensor_id': 'sensor.physical_energy',
                    'force_energy_sensor_creation': True}}), MAP)
                self.assertEqual(plan.changes, ())
                self.assertIn('powercalc_double_count_unverified', codes(plan))

    def test_false_forced_energy_flag_is_not_false_positive(self):
        plan = plan_helper(entry({'entity_id': 'light.source_old',
                                  'force_energy_sensor_creation': False}), MAP)
        self.assertFalse(plan.blocked, plan.markers)

    def test_static_simple_powercalc_field_remains_reference_only(self):
        before = entry({'entity_id': 'light.source_old',
                        'power_sensor_id': 'sensor.meter_old',
                        'energy_sensor_id': 'sensor.physical_energy',
                        'energy_price': 0.8, 'unique_id': 'unchanged'})
        original = deepcopy(before)
        plan = plan_helper(before, MAP)
        self.assertFalse(plan.blocked, plan.markers)
        self.assertEqual([ch.path for ch in plan.changes],
                         [('data', 'entity_id'), ('data', 'power_sensor_id')])
        self.assertEqual(plan.after['data']['entity_id'], 'light.source_new')
        self.assertEqual(plan.after['data']['power_sensor_id'], 'sensor.meter_new')
        self.assertEqual(plan.after['data']['energy_sensor_id'], 'sensor.physical_energy')
        self.assertEqual(plan.after['data']['energy_price'], 0.8)
        self.assertEqual(plan.after['identity'], before['identity'])
        self.assertEqual(plan.after['subentries'], before['subentries'])
        self.assertEqual(before, original)

    def test_all_unmapped_config_is_unchanged(self):
        before = entry({'entity_id': 'light.unaffected',
                        'power_sensor_id': 'sensor.unaffected_power',
                        'energy_sensor_id': 'sensor.unaffected_energy',
                        'mode': 'lut', 'manufacturer': 'sample'})
        plan = plan_helper(before, MAP)
        self.assertFalse(plan.blocked, plan.markers)
        self.assertEqual(plan.after, before)
        self.assertEqual(plan.changes, ())

    def test_reverse_is_exact_dry_run_on_simple_refs(self):
        before = entry({'entity_id': 'light.source_old',
                        'power_sensor_id': 'sensor.meter_old',
                        'unique_id': 'stable_source'})
        forward = plan_helper(before, MAP)
        reverse = plan_helper(forward.after, {value: key for key, value in MAP.items()})
        self.assertFalse(reverse.blocked)
        self.assertEqual(reverse.after, before)
        self.assertEqual(reverse.after['identity']['unique_id'], 'physical-unchanged-001')
        self.assertEqual(reverse.after['data']['unique_id'], 'stable_source')

    def test_repeat_preview_is_noop_not_replay(self):
        before = entry({'entity_id': 'light.source_old'})
        first = plan_helper(before, MAP)
        second = plan_helper(first.after, MAP)
        self.assertFalse(second.blocked)
        self.assertEqual(second.changes, ())
        self.assertEqual(second.after, first.after)

    def test_foreign_identity_mutation_prevents_listener_rebase(self):
        before = entry({'entity_id': 'light.source_old'})
        plan = plan_helper(before, MAP)
        native = deepcopy(plan.before)
        native['data']['entity_id'] = 'light.source_new'
        self.assertEqual(rebase_after_listeners(plan, native), plan.after)
        native['identity']['unique_id'] = 'unexpected_change'
        with self.assertRaisesRegex(ValueError, 'conflict'):
            rebase_after_listeners(plan, native)

    def test_unexpected_nested_custom_reference_remains_blocked(self):
        before = entry({'entity_id': 'light.source_old',
                        'custom_unknown': {'device': 'light.source_old'}})
        plan = plan_helper(before, MAP)
        self.assertIn('unsupported_helper_reference', codes(plan))
        self.assertTrue(plan.blocked)

    def test_non_authorizing_planner_does_not_prove_history(self):
        plan = plan_helper(entry({'entity_id': 'light.source_old'}), MAP)
        self.assertEqual(plan.coverage, 'installed_schema_fields_only')
        self.assertFalse(hasattr(plan, 'can_write'))
        self.assertFalse(hasattr(plan, 'statistics_continuity'))


class PowercalcWriteBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_plan_rejects_before_intent_reload_or_mutation(self):
        plan = plan_helper(entry({'include': {'group': 'sensors'}}), MAP)
        self.assertEqual(plan.changes, ())
        calls = []
        def guard(): calls.append('guard')
        async def settle(): calls.append('settle')
        async def intent(*args): calls.append('intent')
        async def runtime(*args): calls.append('runtime')
        async def validate(*args): calls.append('validate')
        with self.assertRaisesRegex(ValueError, 'coverage_blocked'):
            await apply_entry(object(), plan, guard=guard, settle=settle,
                              record_intent=intent, verify_runtime=runtime,
                              validate_snapshot=validate)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
