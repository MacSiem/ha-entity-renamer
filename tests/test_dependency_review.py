"""Public review must account for every admitted edit without exporting config."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from custom_components.ha_entity_renamer.dependency_review import dependency_files
from custom_components.ha_entity_renamer.transaction import Refused
from custom_components.ha_entity_renamer.yaml_plan import Edit, FilePatch, plan_yaml


class DependencyReviewTests(unittest.TestCase):
    def test_real_include_plan_reports_files_and_exact_substitutions_without_source_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "configuration.yaml").write_text("template: !include template.yaml\nscript: !include scripts.yaml\n")
            (root / "template.yaml").write_text("- sensor:\n  - name: Private synthetic label\n    state: \"{{ states('sensor.old') | int(0) + states('sensor.old') | int(0) }}\"\n")
            (root / "scripts.yaml").write_text("example:\n  sequence:\n  - action: homeassistant.update_entity\n    target:\n      entity_id: sensor.old\n")
            plan = plan_yaml(root, {"sensor.old": "sensor.new"})
            self.assertTrue(plan.ready, plan.issues)
            rows = dependency_files(SimpleNamespace(mapping=(("sensor.old", "sensor.new"),), yaml_plan=plan))
            self.assertEqual({r["path"]: r["references"] for r in rows}, {
                "template.yaml": [{"before": "sensor.old", "after": "sensor.new", "occurrences": 2}],
                "scripts.yaml": [{"before": "sensor.old", "after": "sensor.new", "occurrences": 1}]})
            self.assertEqual(sum(r["edit_count"] for r in rows), sum(len(p.edits) for p in plan.patches))
            output = json.dumps(rows)
            for private in ("Private synthetic label", "{{", "int(0)", str(root)):
                self.assertNotIn(private, output)

    def test_only_removed_exact_references_count_not_unchanged_literal_or_prefix(self):
        before = b"sensor.old sensor.old_suffix sensor.old"
        after = b"sensor.new sensor.old_suffix sensor.old"
        patch = FilePatch("template.yaml", "0" * 64, (Edit(0,len(before),before,after),), after)
        composition = SimpleNamespace(mapping=(("sensor.old", "sensor.new"),), yaml_plan=SimpleNamespace(patches=(patch,)))
        self.assertEqual(dependency_files(composition)[0]["references"][0]["occurrences"], 1)

    def test_unaccounted_edit_refuses_entire_review_instead_of_omitting_it(self):
        edits=(Edit(0,10,b"sensor.old",b"sensor.new"), Edit(12,19,b"private",b"changed"))
        patch=FilePatch("template.yaml", "0" * 64, edits, b"unused")
        composition=SimpleNamespace(mapping=(("sensor.old", "sensor.new"),), yaml_plan=SimpleNamespace(patches=(patch,)))
        with self.assertRaises(Refused):
            dependency_files(composition)
