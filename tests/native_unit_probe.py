"""Run synthetic Python tests with HA's installed dependencies in an owned container.

Only repository sources are copied to a new temporary directory on the owned SSD
mount. No existing HA configuration, API or database is opened.
"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222'), default='ha-renamer-qa222')
args = parser.parse_args()
info = json.loads(subprocess.check_output(['docker', 'inspect', args.container], text=True))[0]
assert info['State']['Running']
mounts = [m['Source'] for m in info['Mounts'] if m['Destination'] == '/config']
assert len(mounts) == 1 and '/ha-tools-v4-20261016/renamer222-' in mounts[0]
paths = list((ROOT/'custom_components/ha_entity_renamer').glob('*.py'))
paths += list((ROOT/'tests').glob('test*.py'))
paths += [ROOT/'ha-entity-renamer.js']
sources = {str(p.relative_to(ROOT)): p.read_text() for p in paths}
program = r'''
import os, sys, tempfile, pathlib, unittest, json
with tempfile.TemporaryDirectory(prefix='renamer-unit-', dir='/config') as directory:
    root = pathlib.Path(directory)
    for name, source in SOURCES.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    os.chdir(root)
    os.environ['TMPDIR'] = directory
    tempfile.tempdir = directory
    sys.path.insert(0, str(root))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests'))
    print(json.dumps({'tests': result.testsRun, 'failures': len(result.failures),
                     'errors': len(result.errors), 'skipped': len(result.skipped),
                     'success': result.wasSuccessful(), 'live_config_access': False}))
    os.chdir('/')
    success = result.wasSuccessful()
raise SystemExit(0 if success else 1)
'''
completed = subprocess.run(['docker','exec','-i',args.container,'python','-B','-'],
    input='SOURCES = '+repr(sources)+'\n'+program, text=True, capture_output=True, timeout=120)
print(completed.stdout)
print(completed.stderr)
completed.check_returncode()
