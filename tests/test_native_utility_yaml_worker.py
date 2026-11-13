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

    async def test_isolated_process_emits_only_hashes(self):
        import asyncio
        import json
        import sys
        base = fixture.NativeBaselineTests(methodName='runTest')
        await base.asyncSetUp()
        self.addAsyncCleanup(base.asyncTearDown)
        before, after = base.root / 'cli-before', base.root / 'cli-after'
        before.mkdir(); after.mkdir()
        for root, entity in ((before, 'sensor.old'), (after, 'sensor.new')):
            (root / 'configuration.yaml').write_text(
                'utility_meter:\n  selected:\n    source: ' + entity + '\n    cycle: daily\n')
        mapping = base.root / 'mapping.json'
        mapping.write_text(json.dumps({'sensor.old':'sensor.new'}))
        mapping.chmod(0o600)
        worker = fixture.COMPONENT / 'native_utility_yaml_worker.py'
        process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-B', str(worker),
            str(before), str(after), str(mapping), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(stderr, b'')
        self.assertNotIn(b'sensor.old', stdout)
        self.assertNotIn(b'sensor.new', stdout)
        result = json.loads(stdout)
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(len(result['runtime_expected'][1]), 64)
