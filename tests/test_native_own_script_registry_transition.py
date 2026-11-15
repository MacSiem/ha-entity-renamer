"""Pure native Script registry-delta model; no HA call and no authorization."""
from copy import deepcopy
import json
import unittest

import test_native_registry_transition as base


class NativeOwnScriptRegistryTransitionTests(unittest.TestCase):
    def images(self, version):
        source_version = '2025.6.0' if version == '2025.6.0' else '2026.10.2'
        before, _, _ = base.fixture(version=source_version)
        original = base.target(before)
        original.update(platform='script', entity_id='script.old', unique_id='old',
                        previous_unique_id='older')
        desired = dict(original, entity_id='script.new', unique_id='new',
                       previous_unique_id='old')
        change = base.native.PreparedRegistryChange('entity', 'stable',
            json.dumps(original), json.dumps(desired))
        after = deepcopy(before)
        row = base.target(after)
        row.update(desired)
        row['modified_at'] = '2026-02-02T00:00:00+00:00'
        after['data']['entities'].remove(row)
        after['data']['entities'].append(row)
        restored = deepcopy(after)
        row = base.target(restored)
        row.update(original)
        # This is exactly what the reverse native API writes, rather than the
        # older value that happened to exist in the immutable ORIGINAL.
        row['previous_unique_id'] = 'new'
        row['modified_at'] = '2026-03-03T00:00:00+00:00'
        restored['data']['entities'].remove(row)
        restored['data']['entities'].append(row)
        return before, after, restored, change

    def test_exact_forward_and_fresh_inverse_native_audit_both_schemas(self):
        for version in ('2025.6.0', '2026.10.4'):
            with self.subTest(version=version):
                before, after, restored, change = self.images(version)
                self.assertEqual(base.validate(before, after, change, version).direction, 'forward')
                self.assertEqual(base.validate(after, restored, change, version, 'reverse').direction,
                                 'reverse')
                self.assertEqual(base.target(restored)['previous_unique_id'], 'new')
                self.assertEqual(json.loads(change.before_json)['previous_unique_id'], 'older')

    def test_default_name_preserves_friendly_override_and_native_startup_identity(self):
        for version in ('2025.6.0', '2026.10.4'):
            with self.subTest(version=version):
                before, after, restored, change = self.images(version)
                original = json.loads(change.before_json)
                original.update(name=None, original_name='old')
                desired = json.loads(change.after_json)
                desired.update(name='old', original_name='new')
                change = base.native.PreparedRegistryChange('entity', 'stable',
                    json.dumps(original), json.dumps(desired))
                after_stamp = base.target(after)['modified_at']
                restored_stamp = base.target(restored)['modified_at']
                base.target(before).update(original)
                base.target(after).update(desired)
                base.target(after)['modified_at'] = after_stamp
                base.target(restored).update(original)
                base.target(restored)['previous_unique_id'] = 'new'
                base.target(restored)['modified_at'] = restored_stamp
                base.validate(before, after, change, version)
                base.validate(after, restored, change, version, 'reverse')
                for forged_fields in ({'name': 'foreign'}, {'original_name': 'foreign'}):
                    forged_after = deepcopy(after)
                    base.target(forged_after).update(forged_fields)
                    with self.assertRaises(base.transition.RegistryTransitionError):
                        base.validate(before, forged_after, change, version)

    def test_current_native_suggested_key_is_exact_and_foreign_drift_refused(self):
        before, after, restored, change = self.images('2026.10.4')
        original, desired = json.loads(change.before_json), json.loads(change.after_json)
        original['suggested_object_id'] = 'old'
        desired['suggested_object_id'] = 'new'
        change = base.native.PreparedRegistryChange('entity', 'stable', json.dumps(original), json.dumps(desired))
        base.target(before)['suggested_object_id'] = 'old'
        base.target(after)['suggested_object_id'] = 'new'
        base.target(restored)['suggested_object_id'] = 'old'
        base.validate(before, after, change, '2026.10.4')
        base.validate(after, restored, change, '2026.10.4', 'reverse')
        for bad in ('foreign', None):
            forged = deepcopy(after)
            base.target(forged)['suggested_object_id'] = bad
            with self.assertRaises(base.transition.RegistryTransitionError):
                base.validate(before, forged, change, '2026.10.4')
        with self.assertRaises(base.transition.RegistryTransitionError):
            base.validate(before, after, change, '2025.6.0')

    def test_foreign_unique_history_and_collision_refuse(self):
        for direction, alteration in (('forward', 'previous'), ('reverse', 'previous'),
                                      ('forward', 'unique_collision'), ('forward', 'other_row')):
            before, after, restored, change = self.images('2025.6.0')
            source, result = (before, after) if direction == 'forward' else (after, restored)
            if alteration == 'previous':
                base.target(result)['previous_unique_id'] = 'foreign'
            elif alteration == 'unique_collision':
                other_source = source['data']['entities'][1]
                other_result = next(row for row in result['data']['entities'] if row['id'] == other_source['id'])
                for row in (other_source, other_result):
                    row.update(platform='script', entity_id='script.other', unique_id='new')
            else:
                next(row for row in result['data']['entities'] if row['id'] == 'untouched')['options'] = {}
            with self.subTest(direction=direction, alteration=alteration):
                with self.assertRaises(base.transition.RegistryTransitionError):
                    base.validate(source, result, change, '2025.6.0', direction)

    def test_non_script_or_non_native_preparation_is_not_admitted(self):
        before, after, _, change = self.images('2025.6.0')
        for patch in ({'previous_unique_id': None}, {'platform': 'automation'},
                      {'entity_id': 'script.different'}, {'unique_id': 'other'}):
            desired = json.loads(change.after_json)
            desired.update(patch)
            forged = base.native.PreparedRegistryChange('entity', 'stable', change.before_json,
                                                        json.dumps(desired))
            candidate = deepcopy(after)
            base.target(candidate).update(patch)
            with self.subTest(patch=patch):
                with self.assertRaises(base.transition.RegistryTransitionError):
                    base.validate(before, candidate, forged, '2025.6.0')
