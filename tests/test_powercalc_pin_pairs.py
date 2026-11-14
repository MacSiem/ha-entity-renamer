"""Official Powercalc catalog pairs; these do not qualify runtime behavior."""
import importlib
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixture

binding = importlib.import_module(fixture.PACKAGE + '.powercalc_source_binding')


class PowercalcPinPairTests(unittest.TestCase):
    def test_catalogs_keep_old_pair_and_distinguish_127(self):
        old = binding._pins('v1.26.1')
        new = binding._pins('v1.27.0')
        self.assertEqual(binding._pins(), old)
        self.assertEqual(binding._PAIRS['2026.10.4'][0], 'v1.26.1')
        self.assertEqual(binding._PAIRS['2026.11.0'][0], 'v1.27.0')
        self.assertNotIn('issues.py', old)
        self.assertIn('issues.py', new)
        self.assertNotEqual(old['manifest.json'], new['manifest.json'])
        with self.assertRaises(ValueError):
            binding._pins('v1.27.1')

    def test_corrupt_catalog_is_not_accepted(self):
        with patch.object(binding, '_sha', return_value='0' * 64):
            with self.assertRaises(ValueError):
                binding._pins('v1.27.0')
