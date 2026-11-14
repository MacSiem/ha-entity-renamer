"""Guard native housekeeping without authorizing foreign restore edits."""
import json
from datetime import datetime, timezone
import unittest

from custom_components.ha_entity_renamer.controlled_powercalc_handoff import _restore_housekeeping, _pss_foreign_unchanged
from custom_components.ha_entity_renamer.native_registry_transition import _powercalc_group_suggested_move


def store(*rows):
    return json.dumps({'version': 1, 'minor_version': 1, 'key': 'core.restore_state',
                       'data': list(rows)}).encode()


def row(name, value, seen='2026-01-01T00:00:00+00:00'):
    return {'state': {'entity_id': name, 'state': value}, 'extra_data': {'native_value': value},
            'last_seen': seen}


class ControlledPowercalcHandoffTests(unittest.TestCase):
    def test_moved_group_receipt_proves_exact_preimage_and_absent_source(self):
        from copy import deepcopy
        from custom_components.ha_entity_renamer.controlled_powercalc_setup_receipts import _validate_prior_pss
        from custom_components.ha_entity_renamer.transaction import Refused
        before = {'sensor.old': {'sensor.member': {'entity_id': 'sensor.member', 'state': '1'}}}
        after = {'sensor.new': deepcopy(before['sensor.old'])}
        receipt = {'schema': 4, 'pss_owned_before': before, 'pss_owned': after}
        population = {('uuid', 'sensor.old', 'sensor.new', 'group')}
        _validate_prior_pss(receipt, {}, population)
        for mutation in ('missing_preimage', 'changed_member', 'retained_source', 'occupied_target'):
            bad = deepcopy(receipt)
            if mutation == 'missing_preimage': bad['pss_owned_before'].clear()
            if mutation == 'changed_member': bad['pss_owned']['sensor.new']['sensor.member']['state'] = '2'
            if mutation == 'retained_source': bad['pss_owned']['sensor.old'] = deepcopy(before['sensor.old'])
            if mutation == 'occupied_target': bad['pss_owned_before']['sensor.new'] = {}
            with self.subTest(mutation=mutation), self.assertRaises(Refused):
                _validate_prior_pss(bad, {}, population)

    def test_live_owned_pss_can_advance_in_both_directions_but_foreign_cannot(self):
        for current_id, alias in (('sensor.old', 'sensor.new'), ('sensor.new', 'sensor.old')):
            before = {current_id: {'sensor.member': {'state': '1'}},
                      alias: {'sensor.member': {'state': '0'}},
                      'sensor.foreign': {'sensor.other': {'state': '7'}}}
            advanced = dict(before, **{current_id: {'sensor.member': {'state': '2'}}})
            self.assertTrue(_pss_foreign_unchanged(before, advanced, {current_id}))
            changed = dict(advanced, **{'sensor.foreign': {'sensor.other': {'state': '8'}}})
            self.assertFalse(_pss_foreign_unchanged(before, changed, {current_id}))
            self.assertFalse(_pss_foreign_unchanged(before, dict(advanced, **{alias: {}}), {current_id}))
            self.assertFalse(_pss_foreign_unchanged(before, dict(advanced, **{'sensor.foreign_new': {}}), {current_id}))
            self.assertFalse(_pss_foreign_unchanged(before, {current_id: advanced[current_id]}, {current_id}))

    def test_native_refresh_and_expiry_allow_only_observed_foreign_drift(self):
        original = row('sensor.foreign', '3')
        refreshed = row('sensor.foreign', '3', '2026-01-02T00:00:00+00:00')
        self.assertTrue(_restore_housekeeping(store(original), store(refreshed),
                                               {'sensor.foreign': refreshed}, set()))
        future = datetime(2026, 1, 10, tzinfo=timezone.utc)
        self.assertTrue(_restore_housekeeping(store(original), store(), {}, set(), now=future))
        self.assertFalse(_restore_housekeeping(store(original), store(), {}, set(),
                                               now=datetime(2026, 1, 5, tzinfo=timezone.utc)))
        self.assertFalse(_restore_housekeeping(store(original), store(),
                                                {'sensor.foreign': original}, set()))

    def test_foreign_edit_and_owned_expiry_are_not_housekeeping(self):
        original = row('sensor.foreign', '3')
        edit = row('sensor.foreign', '4')
        self.assertFalse(_restore_housekeeping(store(original), store(edit),
                                                {'sensor.foreign': original}, set()))
        self.assertFalse(_restore_housekeeping(store(original), store(edit),
                                                {'sensor.foreign': edit}, set()))
        self.assertFalse(_restore_housekeeping(store(), store(edit),
                                                {'sensor.foreign': edit}, set()))
        self.assertFalse(_restore_housekeeping(store(original), store(), {}, {'sensor.foreign'}))

    def test_group_name_and_derived_id_are_one_narrow_effect(self):
        before = {'id': 'stable', 'platform': 'powercalc', 'entity_id': 'sensor.old',
                  'suggested_object_id': 'old', 'name': None, 'unique_id': 'fixed'}
        after = dict(before, entity_id='sensor.new', suggested_object_id='new', name='New')
        self.assertTrue(_powercalc_group_suggested_move(before, after))
        self.assertFalse(_powercalc_group_suggested_move(before, dict(after, unique_id='foreign')))
        self.assertFalse(_powercalc_group_suggested_move(before, dict(after, suggested_object_id='other')))
