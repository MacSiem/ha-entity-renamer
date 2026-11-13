"""Historical destination collisions must stop a rename before registry writes."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('recorder_guard', Path(__file__).parents[1] / 'custom_components/ha_entity_renamer/recorder_guard.py')
guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)


class RecorderGuardTests(unittest.TestCase):
    mapping = {'sensor.old': 'sensor.new'}

    def baseline(self):
        return {'states': {'sensor.old': {'metadata_id': 42}}, 'statistics': {'sensor.old': {
            'id': 7, 'source': 'recorder', 'unit_of_measurement': 'kWh', 'has_sum': True, 'has_mean': False}}}

    def migrated(self):
        return {kind: {'sensor.new': next(iter(rows.values()))} for kind, rows in self.baseline().items()}

    def test_native_migration_keeps_primary_keys_and_metadata(self):
        baseline = guard.prepare(self.mapping, self.baseline())
        self.assertTrue(guard.verify(self.mapping, baseline, self.migrated()))

    def test_orphan_historical_or_statistical_destination_blocks(self):
        for kind in ('states', 'statistics'):
            data = self.baseline(); data[kind]['sensor.new'] = {'id': 999}
            with self.assertRaisesRegex(guard.RecorderConflict, 'destination_occupied'): guard.prepare(self.mapping, data)

    def test_external_statistic_not_claimed_as_native_migration(self):
        data = self.baseline(); data['statistics']['sensor.old']['source'] = 'other'
        with self.assertRaisesRegex(guard.RecorderConflict, 'external_statistic'): guard.prepare(self.mapping, data)

    def test_ack_without_history_migration_is_not_pass(self):
        self.assertFalse(guard.verify(self.mapping, self.baseline(), self.baseline()))

    def test_new_primary_key_or_unit_change_is_not_continuity(self):
        for field, value in [('id', 8), ('unit_of_measurement', 'Wh'), ('has_sum', False)]:
            after = self.migrated(); after['statistics']['sensor.new'][field] = value
            self.assertFalse(guard.verify(self.mapping, self.baseline(), after))

    def test_lost_series_and_recreated_old_reference_are_rejected(self):
        for kind in ('states', 'statistics'):
            after = self.migrated(); after[kind].clear()
            self.assertFalse(guard.verify(self.mapping, self.baseline(), after))
            after = self.migrated(); after[kind]['sensor.old'] = {'id': 998}
            self.assertFalse(guard.verify(self.mapping, self.baseline(), after))

    def test_no_existing_history_is_not_invented(self):
        before = {'states': {}, 'statistics': {}}
        self.assertTrue(guard.verify(self.mapping, before, copy.deepcopy(before)))
        self.assertEqual(guard.prepare(self.mapping, before), before)

    def test_reverse_accepts_recorder_series_born_after_original_prepare(self):
        original = self.baseline()
        original['statistics'] = {}
        saved = copy.deepcopy(original)
        # The actual native cycle supplies the metadata; this unit test covers
        # comparison semantics only, not provenance or a statistics-cycle PASS.
        self.assertTrue(guard.verify(self.mapping, original, self.migrated()))
        self.assertTrue(guard.verify_rollback(self.mapping, original, self.baseline()))
        self.assertEqual(original, saved)
        foreign = self.baseline()
        foreign['statistics']['sensor.old']['source'] = 'external'
        self.assertFalse(guard.verify_rollback(self.mapping, original, foreign))

    def test_reverse_retains_original_metadata_and_rejects_alias_or_foreign_baseline(self):
        self.assertTrue(guard.verify_rollback(self.mapping, self.baseline(), self.baseline()))
        for kind, field, value in [('states', 'metadata_id', 99),
                                   ('statistics', 'id', 8),
                                   ('statistics', 'unit_of_measurement', 'Wh'),
                                   ('statistics', 'has_sum', False)]:
            with self.subTest(kind=kind, field=field):
                changed = self.baseline()
                changed[kind]['sensor.old'][field] = value
                self.assertFalse(guard.verify_rollback(self.mapping, self.baseline(), changed))
        for kind in ('states', 'statistics'):
            with self.subTest(kind=kind):
                missing = self.baseline()
                missing[kind].clear()
                self.assertFalse(guard.verify_rollback(self.mapping, self.baseline(), missing))
                duplicate = self.baseline()
                duplicate[kind]['sensor.new'] = {'id': 999}
                self.assertFalse(guard.verify_rollback(self.mapping, self.baseline(), duplicate))
                self.assertFalse(guard.verify_rollback(self.mapping, duplicate, self.baseline()))

    def test_snapshot_detached_from_mutable_caller(self):
        data = self.baseline(); saved = guard.prepare(self.mapping, data)
        data['states']['sensor.old']['metadata_id'] = 99
        self.assertEqual(saved['states']['sensor.old']['metadata_id'], 42)


if __name__ == '__main__': unittest.main()
