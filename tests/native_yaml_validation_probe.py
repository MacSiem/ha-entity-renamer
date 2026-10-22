"""Run whole-proposal validation with isolated native HA on the owned SSD mount."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222'),
                    default='ha-renamer-qa222')
mode = parser.add_mutually_exclusive_group()
mode.add_argument('--transaction', action='store_true')
mode.add_argument('--observer', action='store_true')
args = parser.parse_args()
info = json.loads(subprocess.check_output(['docker', 'inspect', args.container], text=True))[0]
mounts = [item['Source'] for item in info['Mounts'] if item['Destination'] == '/config']
assert info['State']['Running'] and len(mounts) == 1
assert '/ha-tools-v4-20261016/renamer222-' in mounts[0]
sources = {p.name: p.read_text() for p in
           (ROOT / 'custom_components/ha_entity_renamer').glob('*.py')}
scenario_name = ('native_yaml_runtime_observer_scenario.py' if args.observer else
                 'native_yaml_transaction_scenario.py' if args.transaction else
                 'native_yaml_validation_scenario.py')
scenario = (ROOT / 'tests' / scenario_name).read_text()
support = (ROOT / 'tests/native_yaml_validation_scenario.py').read_text()
program = r'''
import os, pathlib, runpy, sys, tempfile
with tempfile.TemporaryDirectory(prefix='renamer-validation-probe-', dir='/config') as directory:
    root = pathlib.Path(directory)
    component = root / 'component'
    component.mkdir()
    for name, source in SOURCES.items():
        (component / name).write_text(source)
    (root / 'native_yaml_validation_scenario.py').write_text(SUPPORT)
    sys.path.insert(0, str(root))
    scenario = root / 'scenario.py'
    scenario.write_text(SCENARIO)
    os.environ['RENAMER_COMPONENT_DIR'] = str(component)
    os.environ['RENAMER_TEST_SCRATCH'] = directory
    os.environ['TMPDIR'] = directory
    tempfile.tempdir = directory
    os.chdir(root)
    try:
        runpy.run_path(str(scenario), run_name='__main__')
    finally:
        os.chdir('/')
'''
result = subprocess.run(['docker', 'exec', '-i', args.container, 'python', '-B', '-'],
    input='SOURCES = ' + repr(sources) + '\nSCENARIO = ' + repr(scenario) + '\nSUPPORT = ' + repr(support) + '\n' + program,
    text=True, capture_output=True, timeout=300)
print(result.stdout)
print(result.stderr)
result.check_returncode()
