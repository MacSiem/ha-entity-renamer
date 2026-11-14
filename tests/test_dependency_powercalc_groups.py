"""Review reveals qualified Powercalc group references, not entry secrets."""
from copy import deepcopy
import unittest

from custom_components.ha_entity_renamer.dependency_review import dependency_powercalc_groups
from custom_components.ha_entity_renamer.powercalc_group_metadata import prepare_group_metadata, PreparedPowercalcGroupMetadata
from custom_components.ha_entity_renamer.transaction import Refused


class PowercalcGroupReviewTests(unittest.TestCase):
    def test_reports_only_verified_group_reference(self):
        original = {'domain': 'powercalc', 'identity': {'entry_id': 'private-id'},
                    'data': {'_energy_entity': 'sensor.old_group', 'other': 'private'},
                    'options': {}, 'subentries': {}}
        change = prepare_group_metadata(original, 'sensor.old_group', 'sensor.new_group')
        self.assertEqual(dependency_powercalc_groups((change,)), [{'kind': 'powercalc',
            'entity_id': 'sensor.old_group', 'before': 'sensor.old_group', 'after': 'sensor.new_group'}])
        changed = deepcopy(change.after)
        changed['data']['other'] = 'foreign-edit'
        with self.assertRaises(Refused):
            dependency_powercalc_groups((PreparedPowercalcGroupMetadata(change.before, changed, change.mapping),))
        with self.assertRaises(Refused):
            dependency_powercalc_groups(())
