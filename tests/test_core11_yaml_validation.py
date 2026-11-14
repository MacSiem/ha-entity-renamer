"""Native Core 11 YAML checker for a synthetic source reference."""
import importlib
import tempfile
from pathlib import Path
import unittest

import test_native_input_baseline as fixture


@unittest.skipUnless(fixture.HAS_HA, 'requires actual Home Assistant')
class Core11YAMLValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_core11_script_source_change_and_invalid_configuration(self):
        from homeassistant.const import __version__
        if __version__ != '2026.11.0':
            self.skipTest('Core 11 validation only')
        worker = importlib.import_module(fixture.PACKAGE + '.yaml_validation_worker')
        with tempfile.TemporaryDirectory(prefix='core11-yaml-') as tmp:
            root = Path(tmp)
            before, after = root / 'before', root / 'after'
            for directory, entity_id in ((before, 'sensor.original'), (after, 'sensor.renamed')):
                directory.mkdir()
                (directory / 'configuration.yaml').write_text(
                    'script:\n  fixture:\n    sequence:\n'
                    '      - variables:\n          source: "{{ states(\'' + entity_id + '\') }}"\n')
            result = await worker.check(str(before), str(after))
            self.assertEqual(result['status'], 'valid')
            self.assertIn('script', result['changed_domains'])
            self.assertNotIn('script', result['unsupported_domains'])
            (after / 'configuration.yaml').write_text('script: [invalid]\n')
            result = await worker.check(str(before), str(after))
            self.assertNotEqual(result['status'], 'valid')
