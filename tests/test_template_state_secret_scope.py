"""Template descriptors never export expanded secret or environment values."""
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixture

planner = importlib.import_module(fixture.PACKAGE + '.yaml_plan')
subject = importlib.import_module(fixture.PACKAGE + '.template_state_program')


class TemplateSecretScopeTests(unittest.TestCase):
    def run_scope(self, files):
        with fixture.scratch() as directory:
            root = Path(directory)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            plan = planner.plan_yaml(root, {})
            return subject._assert_template_secret_scope(root, tuple(r.path for r in plan.files))

    def test_unrelated_secret_in_root_and_package_allowed(self):
        ledger = self.run_scope({
            'configuration.yaml': 'device_tracker: {password: !secret private}\nhomeassistant:\n  packages: !include_dir_named packages\ntemplate: !include template.yaml\n',
            'packages/other.yaml': 'shell_command: {send: !secret command}\n',
            'template.yaml': '- sensor: [{unique_id: x, state: "1"}]\n'})
        self.assertFalse(ledger.findings)

    def test_template_secret_variants_rejected_without_value(self):
        cases = [
            {'configuration.yaml': 'template: [{sensor: [{unique_id: x, state: !secret secret_name}]}]'},
            {'configuration.yaml': 'template: [{trigger: [{trigger: state, entity_id: !secret secret_name}], sensor: [{unique_id: x, state: "1"}]}]'},
            {'configuration.yaml': 'template: [{sensor: [{unique_id: x, state: !env_var PRIVATE_ENV}]}]'},
            {'configuration.yaml': 'template: [{sensor: [{unique_id: x, attributes: {!secret secret_name: value}, state: "1"}]}]'},
            {'configuration.yaml': 'other: &shared !secret secret_name\ntemplate: [{sensor: [{unique_id: x, state: *shared}]}]'},
            {'configuration.yaml': 'template: !include template.yaml\n', 'template.yaml': '- sensor: !include sensors.yaml\n', 'sensors.yaml': '- unique_id: x\n  state: !secret secret_name\n'},
            {'configuration.yaml': 'homeassistant:\n  packages: !include_dir_named packages\n', 'packages/one.yaml': 'template: [{sensor: [{unique_id: x, state: !secret secret_name}]}]\n'},
            {'configuration.yaml': 'template: [{light: [{unique_id: x, state: !secret secret_name}]}]'},
        ]
        for files in cases:
            with self.subTest(files=list(files)), self.assertRaises(ValueError) as error:
                self.run_scope(files)
            self.assertNotIn('secret_name', str(error.exception))
            self.assertNotIn('PRIVATE_ENV', str(error.exception))

    def test_native_inventory_mismatch_cannot_authorize_scope(self):
        with fixture.scratch() as directory:
            root = Path(directory)
            (root / 'configuration.yaml').write_text(
                'template: [{sensor: [{unique_id: x, state: "1"}]}]\n')
            worker = importlib.import_module(fixture.PACKAGE + '.yaml_validation_worker')
            with patch.object(worker, '_template_source_projection', return_value=[]):
                with self.assertRaisesRegex(ValueError, 'secret_scope_unverified'):
                    subject.native_state_descriptors({}, root, source_paths=('configuration.yaml',))

    def test_unrecognized_template_shape_and_missing_source_fail_closed(self):
        with self.assertRaises(ValueError):
            self.run_scope({'configuration.yaml': 'template: !secret secret_name\n'})
        with fixture.scratch() as directory:
            root = Path(directory)
            (root / 'configuration.yaml').write_text('template: []\n')
            with self.assertRaises(ValueError):
                subject._assert_template_secret_scope(root, ('configuration.yaml', 'missing.yaml'))


if __name__ == '__main__':
    unittest.main()
