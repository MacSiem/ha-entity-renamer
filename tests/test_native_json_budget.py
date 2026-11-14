"""Shared native JSON complexity limits, including realistic registry scale."""
import importlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

COMPONENT = Path(os.environ.get('RENAMER_COMPONENT_DIR',
    str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')))
PACKAGE = '_native_json_budget_regression'
package = ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
baseline = importlib.import_module(PACKAGE + '.native_input_baseline')
registry = importlib.import_module(PACKAGE + '.native_registry_transition')
config = importlib.import_module(PACKAGE + '.native_config_transition')


class NativeJsonBudgetTests(unittest.TestCase):
    def test_large_registry_all_consumers(self):
        # 1,100,004 values: exceeds the old limit and the observed registry.
        raw = b'{"data":{"entities":[' + b'{"x":0},' * 549_999 + b'{"x":0}]}}'
        self.assertLess(len(raw), baseline.MAX_FILE_BYTES)
        for module in (baseline, registry, config):
            with self.subTest(module=module.__name__):
                value = module._loads(raw)
                self.assertEqual(len(value['data']['entities']), 550_000)
                if module is baseline:
                    self.assertEqual(json.loads(module._json(value)), value)
                del value

    def test_actual_node_boundary(self):
        values = [None] * (baseline.MAX_JSON_NODES - 1)
        baseline._bounded_json(values)
        values.append(None)
        with self.assertRaises(baseline.NativeInputBaselineError):
            baseline._bounded_json(values)

    def test_node_limit_and_depth_still_reject(self):
        for module, error in ((baseline, baseline.NativeInputBaselineError),
                              (registry, registry.RegistryTransitionError),
                              (config, config.ConfigTransitionError)):
            with self.subTest(module=module.__name__), patch.object(module, 'MAX_JSON_NODES', 3):
                module._loads(b'[0,0]')
                with self.assertRaises(error):
                    module._loads(b'[0,0,0]')
            with self.subTest(module=module.__name__):
                with self.assertRaises(error):
                    module._loads(b'[' * 65 + b'0' + b']' * 65)
                if module is baseline:
                    with patch.object(module, 'MAX_JSON_NODES', 3), self.assertRaises(error):
                        module._json([0, 0, 0])
