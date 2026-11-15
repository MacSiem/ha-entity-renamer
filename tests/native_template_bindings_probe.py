"""Run only pure planner regressions in native Jinja; no HA API/config or writes.

Owner must launch through the authorized test wrapper. Preparing this file does
not constitute test execution. Only repository source/test code is executed;
Jinja source is exclusively lexed/parsed by reference_planner.
"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222'),
                    default='ha-renamer-qa222')
args = parser.parse_args()
source = (ROOT / 'custom_components/ha_entity_renamer/reference_planner.py').read_text()
existing = (ROOT / 'tests/test_reference_planner.py').read_text()
# Skip the existing filesystem bootstrap: the proposal is loaded in memory.
existing = 'import unittest, importlib.util\n' + existing[existing.index('from reference_planner import ('):]
bindings = (ROOT / 'tests/test_template_bindings.py').read_text()
program = r'''
import sys, types, unittest, json
import jinja2
planner = types.ModuleType('reference_planner')
sys.modules[planner.__name__] = planner
exec(compile(SOURCE, '<proposed-reference-planner>', 'exec'), planner.__dict__)
suite = unittest.TestSuite()
for name, source in [('test_reference_planner', EXISTING), ('test_template_bindings', BINDINGS)]:
    module = types.ModuleType(name)
    module.__file__ = '<in-memory-pure-regression>'
    sys.modules[name] = module
    exec(compile(source, '<' + name + '>', 'exec'), module.__dict__)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps({'jinja': jinja2.__version__, 'tests': result.testsRun,
                  'failures': len(result.failures), 'errors': len(result.errors),
                  'skipped': len(result.skipped), 'success': result.wasSuccessful(),
                  'live_config_access': False, 'template_execution': False}))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''
completed = subprocess.run(
    ['docker', 'exec', '-i', args.container, 'python', '-B', '-'],
    input='SOURCE=' + repr(source) + '\nEXISTING=' + repr(existing) +
          '\nBINDINGS=' + repr(bindings) + '\n' + program,
    text=True, capture_output=True, timeout=60)
print(completed.stdout)
print(completed.stderr)
completed.check_returncode()
