"""Owner-only native current/min full baseline runner; use the build receipt FIFO.

Copies ONLY frozen candidate sources to a NEW private directory on the owned SSD
/config mount. The tested HomeAssistant and every Store use subdirectories of
that private tree; the running instance's config/registries are never opened.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222'),
                    default='ha-renamer-qa222')
args = parser.parse_args()
info = json.loads(subprocess.check_output(['docker', 'inspect', args.container], text=True))[0]
mounts = [item['Source'] for item in info['Mounts'] if item['Destination'] == '/config']
assert info['State']['Running'] and len(mounts) == 1
assert '/ha-tools-v4-20261016/renamer222-' in mounts[0]
component = Path(os.environ.get('RENAMER_COMPONENT_DIR',
                              str(ROOT / 'custom_components/ha_entity_renamer')))
sources = {p.name: p.read_text() for p in component.glob('*.py')}
scenario = (ROOT / 'tests/test_native_input_baseline.py').read_text()
program = r'''
import os, pathlib, runpy, sys, tempfile
with tempfile.TemporaryDirectory(prefix='renamer-input-baseline-probe-', dir='/config') as directory:
    root = pathlib.Path(directory)
    component = root / 'component'
    component.mkdir(mode=0o700)
    for name, source in SOURCES.items():
        (component / name).write_text(source)
    test = root / 'test_native_input_baseline.py'
    test.write_text(SCENARIO)
    os.environ['RENAMER_COMPONENT_DIR'] = str(component)
    os.environ['RENAMER_TEST_SCRATCH'] = directory
    os.environ['TMPDIR'] = directory
    tempfile.tempdir = directory
    os.chdir(root)
    sys.argv = [str(test), '-v']
    try:
        runpy.run_path(str(test), run_name='__main__')
    finally:
        os.chdir('/')
'''
completed = subprocess.run(['docker', 'exec', '-i', args.container, 'python', '-B', '-'],
    input='SOURCES = ' + repr(sources) + '\nSCENARIO = ' + repr(scenario) + '\n' + program,
    text=True, capture_output=True, timeout=180)
print(completed.stdout)
print(completed.stderr)
completed.check_returncode()
