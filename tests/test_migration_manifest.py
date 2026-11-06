"""Manifest import must never broaden the user's selected rename scope."""
import importlib.util
from pathlib import Path
import sys
import unittest

path = Path(__file__).parents[1] / 'custom_components/ha_entity_renamer/manifest.py'
spec = importlib.util.spec_from_file_location('renamer_manifest', path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ManifestTests(unittest.TestCase):
    def manifest(self):
        return {
            'devices': [{'device_id': 'device-a', 'current_name': 'Old room', 'proposed_name': 'New room'}],
            'entities': [
                {'entity_id': 'sensor.old', 'proposed_entity_id': 'sensor.new', 'proposed_name': 'New reading', 'device_id': 'device-a'},
                {'entity_id': 'sensor.keep', 'proposed_entity_id': 'sensor.keep', 'current_name': 'Keep', 'proposed_name': 'Keep'},
            ],
            'statistics': [
                {'statistic_id': 'sensor.old', 'proposed_statistic_id': 'sensor.new'},
                {'statistic_id': 'sensor.orphan', 'proposed_statistic_id': 'sensor.orphan'},
            ],
        }

    def registry(self):
        return [{'id': 'stable-a', 'entity_id': 'sensor.old', 'unique_id': 'hardware-a', 'platform': 'demo', 'device_id': 'device-a', 'name': 'Old reading', 'disabled_by': 'user'},
                {'id': 'stable-b', 'entity_id': 'sensor.keep', 'unique_id': 'hardware-b', 'platform': 'demo', 'device_id': None, 'name': 'Keep'}]

    def test_only_actual_changes_and_linked_statistics_are_imported(self):
        result = module.parse_manifest(self.manifest())
        self.assertEqual(len(result.entities), 1)
        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.statistics, {'sensor.old': 'sensor.new'})

    def test_snapshot_binds_stable_identity_and_preserves_disabled_entity(self):
        result = module.bind_registry(module.parse_manifest(self.manifest()), self.registry(), [{'id': 'device-a', 'name_by_user': 'Old room'}])
        self.assertEqual(result.entities[0].registry_id, 'stable-a')
        self.assertEqual(result.entities[0].unique_id, 'hardware-a')
        self.assertEqual(result.entities[0].disabled_by, 'user')

    def test_missing_source_is_not_assumed_already_applied(self):
        rows = self.registry(); rows[0]['entity_id'] = 'sensor.new'
        with self.assertRaisesRegex(ValueError, 'source_missing'):
            module.bind_registry(module.parse_manifest(self.manifest()), rows, [{'id': 'device-a'}])

    def test_foreign_destination_is_rejected_before_any_change(self):
        rows = self.registry() + [{'id': 'foreign', 'entity_id': 'sensor.new'}]
        with self.assertRaisesRegex(ValueError, 'destination_occupied'):
            module.bind_registry(module.parse_manifest(self.manifest()), rows, [{'id': 'device-a'}])

    def test_duplicate_source_and_destination_rejected(self):
        for row in [dict(self.manifest()['entities'][0]), {'entity_id': 'sensor.other', 'proposed_entity_id': 'sensor.new'}]:
            manifest = self.manifest(); manifest['entities'].append(row)
            with self.assertRaises(ValueError): module.parse_manifest(manifest)

    def test_domain_change_or_invalid_id_rejected(self):
        for invalid in ['light.new', 'sensor.NEW', 'sensor.new.bad', '../sensor.new', 'sensor.new\n']:
            manifest = self.manifest(); manifest['entities'][0]['proposed_entity_id'] = invalid
            with self.assertRaises(ValueError): module.parse_manifest(manifest)

    def test_unrelated_statistics_cannot_be_renamed(self):
        manifest = self.manifest(); manifest['statistics'][1]['proposed_statistic_id'] = 'sensor.other'
        with self.assertRaisesRegex(ValueError, 'unlinked_statistic'): module.parse_manifest(manifest)

    def test_device_mismatch_and_missing_stable_identity_rejected(self):
        for key, value in [('device_id', 'other'), ('unique_id', None), ('id', None)]:
            rows = self.registry(); rows[0][key] = value
            with self.assertRaises(ValueError): module.bind_registry(module.parse_manifest(self.manifest()), rows, [{'id': 'device-a'}])

    def test_batch_selects_exact_rows_and_does_not_repeat_device_rename(self):
        plan = module.parse_manifest(self.manifest())
        batch = plan.select(['sensor.old'], device_ids=['device-a'])
        self.assertEqual(len(batch.devices), 1)
        self.assertEqual(batch.statistics, {'sensor.old': 'sensor.new'})
        empty_devices = plan.select(['sensor.old'], device_ids=[])
        self.assertEqual(empty_devices.devices, ())
        with self.assertRaises(ValueError): plan.select(['sensor.missing'])
        with self.assertRaises(ValueError): plan.select(['sensor.old', 'sensor.old'])

    def test_input_is_not_mutated(self):
        import copy
        manifest = self.manifest(); before = copy.deepcopy(manifest)
        module.bind_registry(module.parse_manifest(manifest), self.registry(), [{'id': 'device-a'}])
        self.assertEqual(before, manifest)

    def test_chains_and_cycles_are_rejected_until_executor_supports_them(self):
        for second_target in ['sensor.last', 'sensor.old']:
            manifest = self.manifest()
            manifest['entities'].append({'entity_id': 'sensor.new', 'proposed_entity_id': second_target})
            with self.assertRaisesRegex(ValueError, 'overlapping_ids'): module.parse_manifest(manifest)

    def test_non_string_name_or_oversized_input_is_rejected(self):
        manifest = self.manifest(); manifest['entities'][0]['proposed_name'] = {'unsafe': 'value'}
        with self.assertRaises(ValueError): module.parse_manifest(manifest)
        with self.assertRaises(ValueError): module.parse_manifest({'entities': [None] * 10001})


if __name__ == '__main__': unittest.main()
