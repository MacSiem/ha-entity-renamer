"""Observe changed native inputs without refreshing YAML authorization."""
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixture_module


@unittest.skipUnless(fixture_module.HAS_HA, 'native HA dependencies required')
class NativeObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixture_module.NativeBaselineTests(
            'test_actual_native_serialization_and_store_full_baseline')
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)
        self.subject = importlib.import_module(fixture_module.PACKAGE + '.native_input_observation')

    async def observe(self):
        return await self.subject.async_observe_native_inputs(self.fixture.hass,
            self.fixture.receipt, guard=self.fixture.guard)

    async def test_unchanged_full_observation_preserves_original_receipt_binding(self):
        baseline = await self.fixture.capture()
        observed = await self.observe()
        self.assertEqual(observed.files, baseline.files)
        self.assertEqual(observed.original_receipt_json, baseline.receipt_json)
        self.assertEqual(observed.version, self.fixture.version)

    async def test_changed_native_observation_never_refreshes_original_validation(self):
        original = self.fixture.receipt
        self.fixture.entity.async_update_entity(self.fixture.row.entity_id, name='Owned native name')
        await self.fixture.entity._store.async_save(self.fixture.entity._data_to_save())
        observed = await self.observe()
        self.assertEqual(observed.original_receipt_json,
                         fixture_module.baseline._receipt_bytes(original))
        self.assertEqual(observed.files[0].raw, Path(self.fixture.entity._store.path).read_bytes())
        self.assertIn(b'Owned native name', observed.files[0].raw)
        with self.assertRaises(fixture_module.validation.YAMLValidationError):
            fixture_module.validation.assert_validation_current(original)
        with self.assertRaises(ValueError):
            await self.fixture.capture()
        self.assertFalse(hasattr(observed, 'receipt'))

    async def test_foreign_non_native_file_or_membership_still_refuses(self):
        path = self.fixture.root / 'configuration.yaml'
        path.write_bytes(b'script: {foreign: true}\n')
        with self.assertRaises(ValueError):
            await self.observe()

    async def test_foreign_native_mode_and_cache_disk_disagreement_refuse(self):
        path = Path(self.fixture.entity._store.path)
        previous_mode = path.stat().st_mode & 0o777
        path.chmod(0o640 if previous_mode != 0o640 else 0o600)
        with self.assertRaises(ValueError):
            await self.observe()
        path.chmod(previous_mode)
        import json
        data = json.loads(path.read_bytes())
        data['data']['entities'][0]['unknown_field'] = 'foreign'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            await self.observe()

    async def test_guard_loss_after_first_executor_refuses(self):
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('scope lost')
        with self.assertRaises(ValueError):
            await self.subject.async_observe_native_inputs(self.fixture.hass,
                self.fixture.receipt, guard=guard)


if __name__ == '__main__':
    unittest.main()
