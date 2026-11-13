"""Native isolated Utility checker refuses any non-source YAML edit."""
import importlib
import unittest
import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class UtilityYAMLWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_schema_seals_and_foreign_config_refusal(self):
        base = fixture.NativeBaselineTests(methodName='runTest')
        await base.asyncSetUp()
        self.addAsyncCleanup(base.asyncTearDown)
        before, after = base.root / 'utility-before', base.root / 'utility-after'
        before.mkdir(); after.mkdir()
        def write(root, source, foreign_cycle='weekly'):
            (root / 'configuration.yaml').write_text(
                'utility_meter:\n  selected:\n    source: ' + source + '\n    cycle: daily\n'
                '  foreign:\n    source: sensor.foreign\n    cycle: ' + foreign_cycle + '\n')
        write(before, 'sensor.old'); write(after, 'sensor.new')
        worker = importlib.import_module(fixture.PACKAGE + '.native_utility_yaml_worker')
        result = await worker.check(str(before), str(after), {'sensor.old':'sensor.new'})
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['runtime_expected'][0], 'utility_meter')
        self.assertNotEqual(*result['runtime_expected'][1:])
        write(after, 'sensor.new', 'monthly')
        with self.assertRaises(ValueError):
            await worker.check(str(before), str(after), {'sensor.old':'sensor.new'})
