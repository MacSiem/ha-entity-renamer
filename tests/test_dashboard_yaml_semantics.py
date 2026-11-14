"""Semantic qualification does not authorize or perform writes."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from custom_components.ha_entity_renamer.dashboard_yaml_semantics import qualify_dashboard_yaml
from custom_components.ha_entity_renamer.yaml_plan import Edit, plan_yaml


class DashboardYAMLSemanticsTests(unittest.TestCase):
    mapping = {'sensor.old': 'sensor.new'}

    def fixture(self, text):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'configuration.yaml').write_text('lovelace:\n  dashboards:\n    board:\n'
            '      mode: yaml\n      filename: board.yaml\n')
        raw = text.encode()
        (root / 'board.yaml').write_bytes(raw)
        return plan_yaml(root, self.mapping), raw

    def test_exact_map_reference_and_foreign_labels(self):
        plan, raw = self.fixture('views:\n- title: Label sensor.old\n  cards:\n'
            '  - type: map\n    entities: [sensor.old]\n')
        before, after = qualify_dashboard_yaml(plan, 'board.yaml', raw, self.mapping)
        self.assertEqual(before['views'][0]['cards'][0]['entities'], ['sensor.old'])
        self.assertEqual(after['views'][0]['cards'][0]['entities'], ['sensor.new'])
        self.assertEqual(after['views'][0]['title'], 'Label sensor.old')

    def test_custom_card_is_migrated_and_dynamic_schema_refused(self):
        plan, raw = self.fixture('views:\n- cards:\n  - type: custom:foreign\n    entity: sensor.old\n')
        before, after = qualify_dashboard_yaml(plan, 'board.yaml', raw, self.mapping)
        self.assertEqual(after['views'][0]['cards'][0]['entity'], 'sensor.new')
        plan, raw = self.fixture('views:\n- cards:\n  - type: map\n    entities: ["{{ states.sensor.old }}"]\n')
        with self.assertRaises(ValueError):
            qualify_dashboard_yaml(plan, 'board.yaml', raw, self.mapping)

    def test_foreign_bytes_and_forged_candidate_are_refused(self):
        plan, raw = self.fixture('views:\n- cards:\n  - type: entity\n    entity: sensor.old\n')
        with self.assertRaises(ValueError):
            qualify_dashboard_yaml(plan, 'board.yaml', raw + b'\n', self.mapping)
        patch, = plan.patches
        with self.assertRaises(ValueError):
            qualify_dashboard_yaml(replace(plan, patches=(replace(patch, proposed_bytes=b'views: []'),)),
                                   'board.yaml', raw, self.mapping)

    def test_foreign_json_numeric_type_and_signed_zero_are_refused(self):
        for before, after in ((b'1', b'true'), (b'1', b'1.0'), (b'0.0', b'-0.0')):
            with self.subTest(before=before, after=after):
                plan, raw = self.fixture('views:\n- cards:\n  - type: map\n'
                    '    default_zoom: ' + before.decode() + '\n    entities: [sensor.old]\n')
                patch, = plan.patches
                start = raw.index(b'default_zoom: ') + len(b'default_zoom: ')
                edits = tuple(sorted((*patch.edits, Edit(start, start + len(before), before, after)),
                                     key=lambda item: item.start))
                proposed = raw
                for edit in reversed(edits):
                    proposed = proposed[:edit.start] + edit.after + proposed[edit.end:]
                forged = replace(plan, patches=(replace(patch, edits=edits, proposed_bytes=proposed),))
                with self.assertRaises(ValueError):
                    qualify_dashboard_yaml(forged, 'board.yaml', raw, self.mapping)

    def test_unknown_yaml_tag_and_duplicate_key_are_refused(self):
        for text in ('views: !secret board\n', 'views: []\nviews: []\n'):
            with self.subTest(text=text):
                plan, raw = self.fixture(text)
                with self.assertRaises(ValueError):
                    qualify_dashboard_yaml(plan, 'board.yaml', raw, self.mapping)
