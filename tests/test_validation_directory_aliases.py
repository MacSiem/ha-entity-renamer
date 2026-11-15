"""Contained custom integration aliases remain bound across native validation."""
import asyncio
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import test_native_input_baseline as fixture

validation = fixture.validation
planner = importlib.import_module(fixture.PACKAGE + '.yaml_plan')
baseline = fixture.baseline


class DirectoryAliasTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = fixture.scratch()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.root = self.parent / 'config'
        self.root.mkdir()
        (self.root / 'configuration.yaml').write_text(
            "alias_fixture: {}\nscript:\n  fixture:\n    alias: !secret fixture_name\n    sequence:\n      - variables:\n          source: \"{{ states('sensor.old') }}\"\n"
            "template:\n  - sensor:\n      - unique_id: scope_test\n        state: \"{{ states('sensor.old') }}\"\n")
        (self.root / 'secrets.yaml').write_text('fixture_name: synthetic_private_value\n')
        self.components = self.root / 'custom_components'
        self.target = self.components / 'fixture' / 'integrations' / 'alias_fixture'
        self.target.mkdir(parents=True)
        (self.target / '__init__.py').write_text('import voluptuous as vol\nCONFIG_SCHEMA = vol.Schema({vol.Optional("alias_fixture"): dict}, extra=vol.ALLOW_EXTRA)\n')
        (self.target / 'manifest.json').write_text(json.dumps({'domain': 'alias_fixture', 'name': 'Alias fixture',
            'version': '1.0.0', 'documentation': 'https://example.invalid', 'codeowners': []}))
        self.alias = self.components / 'alias_fixture'
        # Absolute source link through a different name for the config root.
        self.root_alias = self.parent / 'config_alias'
        self.root_alias.symlink_to(self.root, target_is_directory=True)
        self.alias.symlink_to(self.root_alias / self.target.relative_to(self.root), target_is_directory=True)
        self.plan = planner.plan_yaml(self.root, {'sensor.old': 'sensor.new'})
        self.assertTrue(self.plan.ready)

    def stage(self):
        with tempfile.TemporaryDirectory(dir=self.parent) as tmp:
            before, after = Path(tmp) / 'before', Path(tmp) / 'after'
            before.mkdir(); after.mkdir()
            sources, directories, links = validation._stage(self.root, self.plan, before, after)
            for stage in (before, after):
                link = stage / 'custom_components/alias_fixture'
                self.assertTrue(link.is_symlink())
                self.assertFalse(os.path.isabs(os.readlink(link)))
                self.assertEqual(link.resolve(), stage / self.target.relative_to(self.root))
                self.assertEqual((link / 'manifest.json').read_bytes(), (self.target / 'manifest.json').read_bytes())
            receipt = validation.ValidationReceipt(str(self.root), sources, directories, (),
                                                    ('script',), (), '2026.11.1', source_links=links)
            self.assertEqual(baseline._receipt(baseline._receipt_bytes(receipt)), receipt)
            return receipt

    async def test_native_check_and_fence(self):
        receipt = self.stage()
        validation.assert_validation_current(receipt)
        async def executor(function, *args):
            return await asyncio.to_thread(function, *args)
        hass = SimpleNamespace(config=SimpleNamespace(config_dir=str(self.root_alias)),
                               async_add_executor_job=executor)
        bundle = await validation.async_validate_proposed_with_template_state_sources(
            hass, self.plan, {'sensor.old': 'sensor.new'}, self.parent, with_state_program=True)
        native = bundle.receipt
        self.assertEqual(native.changed_domains, ('script', 'template'))
        self.assertEqual(native.source_links, receipt.source_links)
        validation.assert_validation_current(native)
        (self.target / '__init__.py').write_text('VALUE = 2\n')
        with self.assertRaises(validation.YAMLValidationError):
            validation.assert_validation_current(native)

    async def test_native_template_secret_refused_without_exposing_value(self):
        config = self.root / 'configuration.yaml'
        text = config.read_text()
        text = text.replace("state: \"{{ states('sensor.old') }}\"", 'state: !secret template_state')
        config.write_text(text)
        (self.root / 'secrets.yaml').write_text(
            'fixture_name: fixture\ntemplate_state: \"{{ states(\'sensor.old\') }}\"\n')
        plan = planner.plan_yaml(self.root, {'sensor.old': 'sensor.new'})
        async def executor(function, *args):
            return await asyncio.to_thread(function, *args)
        hass = SimpleNamespace(config=SimpleNamespace(config_dir=str(self.root)),
                               async_add_executor_job=executor)
        with self.assertRaises(validation.YAMLValidationError) as error:
            await validation.async_validate_proposed_with_template_state_sources(
                hass, plan, {'sensor.old': 'sensor.new'}, self.parent, with_state_program=True)
        self.assertIn('SourceLedgerError', str(getattr(error.exception, '__notes__', ())))
        self.assertNotIn("states('sensor.old')", str(error.exception))
        self.assertNotIn("states('sensor.old')", str(getattr(error.exception, '__notes__', ())))

    async def test_candidate_staging_preserves_alias_and_fence(self):
        storage = self.root / '.storage'
        storage.mkdir()
        for key in baseline._KEYS:
            (storage / key).write_bytes(b'{}')
        receipt = self.stage()
        patch = self.plan.patches[0]
        config = self.root / patch.path
        before_bytes = config.read_bytes()
        config.write_bytes(patch.proposed_bytes)
        accepted = importlib.import_module(fixture.PACKAGE + '.accepted_input_state')
        candidate = importlib.import_module(fixture.PACKAGE + '.native_candidate_validation')
        transaction = importlib.import_module(fixture.PACKAGE + '.yaml_transaction')
        sources = {}
        for path, _, _, _ in receipt.source_files:
            source = self.root / path
            info = source.stat()
            sources[path] = [fixture.digest(source.read_bytes()), info.st_dev, info.st_ino,
                             info.st_mode, info.st_uid, info.st_gid]
        state = accepted.AcceptedInputState(baseline._receipt_bytes(receipt), 'head', sources, {})
        info = config.stat()
        change = SimpleNamespace(files=(transaction.YAMLFile(patch.path, before_bytes.hex(),
                                patch.proposed_bytes.hex(), info.st_dev, info.st_ino),))
        with tempfile.TemporaryDirectory(dir=self.parent) as directory:
            before, after = Path(directory) / 'before', Path(directory) / 'after'
            before.mkdir(); after.mkdir()
            candidate._stage(state, receipt, change, False, before, after)
            for stage in (before, after):
                link = stage / 'custom_components/alias_fixture'
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.resolve(), stage / self.target.relative_to(self.root))
            self.alias.unlink()
            self.alias.symlink_to(self.target.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                state.assert_current(receipt)

    async def test_retarget_replacement_rejected(self):
        receipt = self.stage()
        other = self.components / 'other'; other.mkdir()
        self.alias.unlink()
        self.alias.symlink_to(other, target_is_directory=True)
        with self.assertRaises(validation.YAMLValidationError):
            validation.assert_validation_current(receipt)

    async def test_escape_dangling_and_cycle_rejected(self):
        for target in (self.parent, self.components / 'missing', self.alias):
            with self.subTest(target=target):
                self.alias.unlink()
                self.alias.symlink_to(target, target_is_directory=True)
                with self.assertRaises(validation.YAMLValidationError):
                    self.stage()

    async def test_nested_link_rejected(self):
        (self.target / 'nested').symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(validation.YAMLValidationError):
            self.stage()


if __name__ == '__main__':
    unittest.main()
