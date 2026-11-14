"""Contained custom integration aliases remain bound across native validation."""
import asyncio
from dataclasses import replace
import importlib
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
            "script:\n  fixture:\n    sequence:\n      - variables:\n          source: \"{{ states('sensor.old') }}\"\n")
        self.components = self.root / 'custom_components'
        self.target = self.components / 'fixture' / 'integrations' / 'alias_fixture'
        self.target.mkdir(parents=True)
        (self.target / '__init__.py').write_text('VALUE = 1\n')
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
                self.assertEqual((link / '__init__.py').read_bytes(), b'VALUE = 1\n')
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
        native = await validation.async_validate_proposed(hass, self.plan, self.parent)
        self.assertEqual(native.changed_domains, ('script',))
        self.assertEqual(native.source_links, receipt.source_links)
        validation.assert_validation_current(native)
        (self.target / '__init__.py').write_text('VALUE = 2\n')
        with self.assertRaises(validation.YAMLValidationError):
            validation.assert_validation_current(native)

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
