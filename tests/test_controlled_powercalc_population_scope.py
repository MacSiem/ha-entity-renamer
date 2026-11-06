"""Only the proven Powercalc group may satisfy a helper coverage marker."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.helper_plan import plan_helper
from custom_components.ha_entity_renamer.native_template_state_server import _powercalc_group_helper_coverage


class PowercalcGroupCoverageTests(unittest.TestCase):
    def test_unaccounted_helper_or_foreign_group_is_refused(self):
        mapping = {'sensor.group': 'sensor.new_group'}
        original = {'domain': 'powercalc', 'identity': {'entry_id': 'entry'},
                    'data': {'_energy_entity': 'sensor.group', 'force_calculate_group_energy': False},
                    'options': {}, 'subentries': {}}
        rows = [{'id': 'uuid', 'entity_id': 'sensor.group', 'platform': 'powercalc', 'config_entry_id': 'entry'}]
        previous = SimpleNamespace(groups=(SimpleNamespace(entity_id='sensor.group', registry_id='uuid'),))
        def allowed(snapshot, registry=rows):
            return _powercalc_group_helper_coverage((plan_helper(snapshot, mapping),), registry, previous, mapping)
        self.assertTrue(allowed(original))
        for key, value in (('force_calculate_group_energy', True), ('force_calculate_group_energy', 0),
                           ('_energy_entity', 'sensor.foreign'), ('entity_id', 'sensor.group')):
            candidate = deepcopy(original)
            candidate['data'][key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(allowed(candidate))
        self.assertFalse(allowed(original, [dict(rows[0], id='foreign')]))
