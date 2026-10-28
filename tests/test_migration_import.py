"""Strict import of the approved proposal format; no file paths or effects."""
import copy
import json
import unittest

from test_migration_manifest import module


class ManifestImportTests(unittest.TestCase):
    def source(self):
        return {'at': '2026-10-20', 'checks': {'ha_writes': 0, 'proposed_entities': 1},
            'entities': [
                {'entity_id': 'sensor.old', 'proposed_entity_id': 'sensor.new',
                 'proposed_name': 'New reading', 'device_id': '', 'action': 'Zmiana ID i nazwy',
                 'references': 'Descriptive inventory, never a prepared instruction'},
                {'entity_id': 'sensor.keep', 'proposed_entity_id': 'sensor.keep',
                 'current_name': 'Keep', 'proposed_name': 'Keep'}],
            'devices': [{'device_id': 'device-a', 'current_name': 'Old', 'proposed_name': 'New',
                         'entities': 1, 'disabled_entities': 0}],
            'statistics': [{'statistic_id': 'sensor.old', 'proposed_statistic_id': 'sensor.new',
                            'has_sum': True, 'has_mean': False}]}

    def decode(self, document=None, **selection):
        return module.decode_import(json.dumps(self.source() if document is None else document), **selection)

    def test_full_proposal_normalizes_only_changes_and_drops_descriptive_metadata(self):
        proposal = self.decode()
        self.assertEqual(proposal, {
            'entities': [{'entity_id': 'sensor.old', 'proposed_entity_id': 'sensor.new',
                          'proposed_name': 'New reading', 'device_id': None}],
            'devices': [{'device_id': 'device-a', 'proposed_name': 'New'}],
            'statistics': [{'statistic_id': 'sensor.old', 'proposed_statistic_id': 'sensor.new'}]})
        self.assertNotIn('references', json.dumps(proposal))
        self.assertEqual(len(module.parse_manifest(proposal).entities), 1)

    def test_approved_full_inventory_size_can_exceed_two_megabytes_before_normalization(self):
        result = module.decode_import(' ' * 2_100_000 + json.dumps(self.source()))
        self.assertEqual(len(result['entities']), 1)

    def test_explicit_selection_keeps_linked_statistics_and_does_not_infer_device_changes(self):
        proposal = self.decode(entity_ids=['sensor.old'], device_ids=[])
        self.assertEqual(proposal['devices'], [])
        self.assertEqual(len(proposal['statistics']), 1)
        with self.assertRaises(ValueError): self.decode(entity_ids=['sensor.missing'], device_ids=[])
        with self.assertRaises(ValueError): self.decode(entity_ids=['sensor.old'] * 2, device_ids=[])
        with self.assertRaises(ValueError): self.decode(entity_ids=[], device_ids=[])

    def test_unknown_root_or_row_fields_cannot_supply_steps_paths_receipts_or_hidden_mutations(self):
        for field in ('steps', 'root', 'prepared_ref', 'new_area'):
            document = self.source(); document[field] = 'untrusted'
            with self.assertRaises(ValueError): self.decode(document)
            document = self.source(); document['entities'][0][field] = 'untrusted'
            with self.assertRaises(ValueError): self.decode(document)

    def test_duplicate_json_keys_constants_oversize_and_wrong_types_refuse(self):
        for raw in ('{"entities":[],"entities":[]}', '{"checks":{"ha_writes":NaN}}',
                    '[]', ' ' * 4_000_001):
            with self.assertRaises(ValueError): module.decode_import(raw)
        for family, field, value in (('entities', 'entity_id', None),
                                     ('entities', 'references', {}),
                                     ('devices', 'entities', True),
                                     ('statistics', 'has_sum', 1)):
            document = self.source(); document[family][0][field] = value
            with self.assertRaises(ValueError): self.decode(document)

    def test_collision_domain_and_unlinked_statistics_rules_are_reused(self):
        for field, value in (('proposed_entity_id', 'light.new'), ('proposed_name', {})):
            document = self.source(); document['entities'][0][field] = value
            with self.assertRaises(ValueError): self.decode(document)
        document = self.source(); document['statistics'][0]['proposed_statistic_id'] = 'sensor.foreign'
        with self.assertRaises(ValueError): self.decode(document)

    def test_import_never_mutates_the_supplied_object(self):
        document = self.source(); before = copy.deepcopy(document)
        self.decode(document)
        self.assertEqual(document, before)
