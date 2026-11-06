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

    def _own_script_composition(self, controlled=False):
        from custom_components.ha_entity_renamer.native_script_program_composition import ScriptTemplateStateComposition
        from custom_components.ha_entity_renamer.yaml_source_ledger import extract_source_ledger
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'configuration.yaml').write_text(
            'script: !include scripts.yaml\ntemplate: !include template.yaml\n')
        (root / 'scripts.yaml').write_text(
            "'worker':\n  alias: worker\n  sequence:\n  - event: probe\n"
            'caller:\n  sequence:\n  - action: script.worker\n')
        (root / 'template.yaml').write_text(
            '- sensor:\n  - name: Observer\n    state: "{{ states(\'script.worker\') }}"\n')
        mapping = {'script.worker': 'script.new_worker'}
        plan = plan_yaml(root, mapping, owned_script_services=('script.worker',))
        self.assertTrue(plan.ready, plan.issues)
        original = {row.path: (root / row.path).read_bytes() for row in plan.files}
        ledger = extract_source_ledger(plan, original)
        if controlled:
            from custom_components.ha_entity_renamer.controlled_script_composition import ControlledScriptComposition
            composition = object.__new__(ControlledScriptComposition)
        else:
            composition = object.__new__(ScriptTemplateStateComposition)
        object.__setattr__(composition, 'mapping', tuple(mapping.items()))
        object.__setattr__(composition, 'yaml_plan', plan)
        object.__setattr__(composition, 'evidence', SimpleNamespace(ledger=ledger))
        return composition, original

    def test_owned_script_key_review_exposes_definition_and_references_separately(self):
        composition, _ = self._own_script_composition()
        rows = {row['path']: row for row in dependency_files(composition)}
        self.assertEqual(rows['scripts.yaml']['references'], [
            {'before': 'script.worker', 'after': 'script.new_worker', 'occurrences': 1},
            {'kind': 'script_key', 'before': 'worker', 'after': 'new_worker', 'occurrences': 1}])
        self.assertEqual(rows['scripts.yaml']['edit_count'], 2)
        self.assertEqual(rows['template.yaml']['references'], [
            {'before': 'script.worker', 'after': 'script.new_worker', 'occurrences': 1}])
        self.assertNotIn('alias', json.dumps(list(rows.values())))

    def test_controlled_script_key_review_exposes_verified_definition(self):
        composition, _ = self._own_script_composition(controlled=True)
        rows = {row['path']: row for row in dependency_files(composition)}
        self.assertEqual(rows['scripts.yaml']['references'], [
            {'before': 'script.worker', 'after': 'script.new_worker', 'occurrences': 1},
            {'kind': 'script_key', 'before': 'worker', 'after': 'new_worker', 'occurrences': 1}])
        self.assertNotIn('alias', json.dumps(list(rows.values())))

    def test_powercalc_script_key_review_requires_typed_script_program(self):
        from custom_components.ha_entity_renamer.controlled_powercalc_composition import ControlledPowercalcComposition
        from custom_components.ha_entity_renamer.dependent_config_program import DependentConfigProgram
        script, _ = self._own_script_composition(controlled=True)
        composition = object.__new__(ControlledPowercalcComposition)
        for field in ('mapping', 'yaml_plan', 'evidence'):
            object.__setattr__(composition, field, getattr(script, field))
        object.__setattr__(composition, 'dependent_config_program', object.__new__(DependentConfigProgram))
        rows = {row['path']: row for row in dependency_files(composition)}
        self.assertIn({'kind': 'script_key', 'before': 'worker', 'after': 'new_worker', 'occurrences': 1},
                      rows['scripts.yaml']['references'])
        object.__setattr__(composition, 'dependent_config_program', None)
        with self.assertRaises(Refused):
            dependency_files(composition)

    def test_equal_unowned_scalar_edit_refuses_full_review(self):
        self._assert_equal_unowned_scalar_refused(controlled=False)
        self._assert_equal_unowned_scalar_refused(controlled=True)

    def _assert_equal_unowned_scalar_refused(self, controlled):
        composition, originals = self._own_script_composition(controlled=controlled)
        plan = composition.yaml_plan
        patch = next(row for row in plan.patches if row.path == 'scripts.yaml')
        raw = originals['scripts.yaml']
        start = raw.index(b'worker', raw.index(b'alias:'))
        foreign = Edit(start, start + len(b'worker'), b'worker', b'new_worker')
        edits = tuple(sorted((*patch.edits, foreign), key=lambda item: item.start))
        proposed, cursor = [], 0
        for edit in edits:
            proposed.extend((raw[cursor:edit.start], edit.after))
            cursor = edit.end
        proposed.append(raw[cursor:])
        forged = replace(patch, edits=edits, proposed_bytes=b''.join(proposed))
        object.__setattr__(composition, 'yaml_plan', replace(plan, patches=tuple(
            forged if row is patch else row for row in plan.patches)))
        with self.assertRaises(Refused):
            dependency_files(composition)

    def test_discovery_reuses_owned_script_planner_and_detects_source_change(self):
        from custom_components.ha_entity_renamer.discovery import _scan_yaml
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'configuration.yaml').write_text('script: !include scripts.yaml\n')
            path = root / 'scripts.yaml'
            path.write_text('worker:\n  sequence:\n  - action: script.worker\n')
            mapping = {'script.worker': 'script.new_worker'}
            expected = plan_yaml(root, mapping, owned_script_services=('script.worker',))
            self.assertTrue(expected.ready, expected.issues)
            self.assertEqual(_scan_yaml(root, mapping, (), ('script.worker',)), expected)
            self.assertNotEqual(_scan_yaml(root, mapping, (), ()), expected)
            path.write_text(path.read_text() + '# foreign edit\n')
            self.assertNotEqual(_scan_yaml(root, mapping, (), ('script.worker',)), expected)

    def test_script_key_without_typed_owned_composition_refuses(self):
        composition, _ = self._own_script_composition()
        unowned = SimpleNamespace(mapping=composition.mapping, yaml_plan=composition.yaml_plan,
                                  evidence=composition.evidence)
        with self.assertRaises(Refused):
            dependency_files(unowned)
