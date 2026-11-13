"""A prefix inverse needs the exact PSS preimage, including absent keys."""
from copy import deepcopy
import unittest

from custom_components.ha_entity_renamer.controlled_powercalc_setup_receipts import _validate_prior_pss
from custom_components.ha_entity_renamer.controlled_powercalc_prefix_images import reverse_images
from custom_components.ha_entity_renamer.transaction import Refused


class PowercalcPrefixPreimageTests(unittest.TestCase):
    def test_exact_original_and_mapped_members_are_bound(self):
        before = {'sensor.group': {'sensor.leaf': {'entity_id': 'sensor.leaf', 'state': '1.25'},
                                   'sensor.foreign': {'entity_id': 'sensor.foreign', 'state': '2'}}}
        after = {'sensor.group': {'sensor.leaf_new': {'entity_id': 'sensor.leaf_new', 'state': '1.25'},
                                  'sensor.foreign': {'entity_id': 'sensor.foreign', 'state': '2'}}}
        handoff = {'pss_owned_before': before, 'pss_owned': after}
        population = {('uuid', 'sensor.group', 'sensor.group', 'group')}
        mapping = {'sensor.leaf': 'sensor.leaf_new'}
        _validate_prior_pss(handoff, mapping, population)
        for mutation in ('missing_original', 'changed_foreign', 'target_collision', 'missing_member'):
            bad = deepcopy(handoff)
            if mutation == 'missing_original':
                del bad['pss_owned_before']['sensor.group']
            elif mutation == 'changed_foreign':
                bad['pss_owned']['sensor.group']['sensor.foreign']['state'] = '3'
            elif mutation == 'target_collision':
                bad['pss_owned_before']['sensor.group']['sensor.leaf_new'] = None
            else:
                del bad['pss_owned']['sensor.group']['sensor.leaf_new']
            with self.subTest(mutation=mutation), self.assertRaises(Refused):
                _validate_prior_pss(bad, mapping, population)

    def test_moved_group_preserves_old_and_proves_absent_target(self):
        original = {'sensor.old_group': {'sensor.leaf': {'entity_id': 'sensor.leaf', 'state': '1'}}}
        result = {'sensor.old_group': deepcopy(original['sensor.old_group']),
                  'sensor.new_group': {'sensor.leaf': {'entity_id': 'sensor.leaf', 'state': '1'},
                                       'sensor.renamed': {'entity_id': 'sensor.renamed', 'state': '1'}}}
        value = {'pss_owned_before': original, 'pss_owned': result}
        population = {('uuid', 'sensor.old_group', 'sensor.new_group', 'group')}
        _validate_prior_pss(value, {'sensor.leaf': 'sensor.renamed'}, population)
        value['pss_owned_before']['sensor.new_group'] = {}
        with self.assertRaises(Refused):
            _validate_prior_pss(value, {'sensor.leaf': 'sensor.renamed'}, population)

    def test_reverse_may_replace_only_the_previously_qualified_alias(self):
        before = {'sensor.new_group': {'sensor.new_leaf': {'entity_id': 'sensor.new_leaf', 'state': '1'}},
                  'sensor.old_group': {'sensor.old_leaf': {'entity_id': 'sensor.old_leaf', 'state': '0'}}}
        after = {'sensor.new_group': deepcopy(before['sensor.new_group']),
                 'sensor.old_group': {'sensor.new_leaf': {'entity_id': 'sensor.new_leaf', 'state': '1'},
                                      'sensor.old_leaf': {'entity_id': 'sensor.old_leaf', 'state': '1'}}}
        _validate_prior_pss({'pss_owned_before': before, 'pss_owned': after},
                            {'sensor.new_leaf': 'sensor.old_leaf'},
                            {('uuid', 'sensor.new_group', 'sensor.old_group', 'group')},
                            direction='rollback')

    def test_detached_reverse_restores_owned_preimages_and_preserves_foreign(self):
        stored = {'state': {'entity_id': 'sensor.new_energy', 'state': '1.2'},
                  'entity_registry_id': 'uuid', 'extra_data': {'native_value': '1.234567'}}
        handoff = {'schema': 3, 'population': [{'uuid': 'uuid', 'before_id': 'sensor.old_energy',
                    'after_id': 'sensor.new_energy', 'payload': {'after': deepcopy(stored)}}],
                   'pss_owned_before': {'sensor.group': {'sensor.old': None}},
                   'pss_owned': {'sensor.group': {'sensor.new': None}},
                   'restore_owned': {'sensor.new_energy': deepcopy(stored)}}
        pss = {'sensor.group': {'sensor.new': None}, 'sensor.foreign': {'sensor.x': None}}
        restore = {'sensor.new_energy': deepcopy(stored), 'sensor.foreign': {'opaque': [True, 1]}}
        new_pss, new_restore, clones = reverse_images(handoff, pss, restore)
        self.assertEqual(new_pss, {'sensor.group': {'sensor.old': None}, 'sensor.foreign': {'sensor.x': None}})
        self.assertEqual(new_restore['sensor.foreign'], restore['sensor.foreign'])
        self.assertEqual(clones['uuid']['state']['entity_id'], 'sensor.old_energy')
        self.assertEqual(clones['uuid']['extra_data']['native_value'], '1.234567')
        self.assertEqual(pss['sensor.group'], {'sensor.new': None})
        changed = deepcopy(restore)
        changed['sensor.old_energy'] = {'foreign': True}
        with self.assertRaises(Refused):
            reverse_images(handoff, pss, changed)
        changed = deepcopy(pss)
        changed['sensor.group']['sensor.foreign'] = None
        with self.assertRaises(Refused):
            reverse_images(handoff, changed, restore)
