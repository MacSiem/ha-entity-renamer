"""Run synthetic Python tests with HA's installed dependencies in an owned container.

Repository sources and an optional full public Powercalc fixture are copied to
a new temporary directory on the owned SSD mount. No existing HA configuration, API or database is opened.
"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222'), default='ha-renamer-qa222')
parser.add_argument('--pattern', default='test*.py')
parser.add_argument('--repeat', type=int, default=1)
parser.add_argument('--timeout', type=int, default=600, help='Container suite timeout in seconds')
parser.add_argument('--test-filter', default='', help='Only discovered test IDs containing this string')
parser.add_argument('--powercalc-component-dir', type=Path, help='Full public Powercalc fixture copied only to the isolated suite directory')
args = parser.parse_args()
assert args.repeat >= 1 and args.timeout > 0 and '/' not in args.pattern and '\\' not in args.pattern
if not any(path.is_file() for path in (ROOT/'tests').glob(args.pattern)):
    parser.error('test pattern selected no source files')
info = json.loads(subprocess.check_output(['docker', 'inspect', args.container], text=True))[0]
assert info['State']['Running']
mounts = [m['Source'] for m in info['Mounts'] if m['Destination'] == '/config']
assert len(mounts) == 1 and '/ha-tools-v4-20261016/renamer222-' in mounts[0]
paths = list((ROOT/'custom_components/ha_entity_renamer').glob('*.py'))
paths += list((ROOT/'custom_components/ha_entity_renamer').glob('*.json'))
paths += list((ROOT/'tests').glob('test*.py'))
paths += [ROOT/'ha-entity-renamer.js', ROOT/'scripts/stage_hacs_companion.py']
sources = {str(p.relative_to(ROOT)): p.read_text() for p in paths}
if args.powercalc_component_dir is not None:
    vendor_root = args.powercalc_component_dir.resolve(strict=True)
    if not vendor_root.is_relative_to(Path('/Volumes/Samsung_SSD')):
        parser.error('public vendor fixture must be stored on the owned SSD')
    manifest = json.loads((vendor_root / 'manifest.json').read_text())
    if manifest.get('domain') != 'powercalc':
        parser.error('Powercalc public component fixture required')
    for vendor_file in vendor_root.rglob('*'):
        if vendor_file.is_symlink():
            parser.error('public vendor fixture cannot contain symlinks')
        if vendor_file.is_file():
            name = vendor_file.relative_to(vendor_root).as_posix()
            sources['_public_vendor/powercalc/' + name] = vendor_file.read_text()
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
    if VENDOR_FIXTURE:
        os.environ['POWERCALC_COMPONENT_DIR'] = str(root / '_public_vendor/powercalc')
    sys.path.insert(0, str(root))
    suite = unittest.TestSuite(unittest.defaultTestLoader.discover('tests', pattern=PATTERN)
                               for _ in range(REPEAT))
    if TEST_FILTER:
        def selected(items):
            for item in items:
                if isinstance(item, unittest.TestSuite):
                    yield from selected(item)
                elif TEST_FILTER in item.id():
                    yield item
        suite = unittest.TestSuite(selected(suite))
    if suite.countTestCases() == 0:
        raise RuntimeError('test discovery selected no test cases')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'tests': result.testsRun, 'failures': len(result.failures),
                     'errors': len(result.errors), 'skipped': len(result.skipped),
                     'success': result.wasSuccessful(), 'live_config_access': False}))
    os.chdir('/')
    success = result.wasSuccessful()
raise SystemExit(0 if success else 1)
'''
completed = subprocess.run(['docker','exec','-i',args.container,'python','-X','faulthandler','-B','-'],
    input='SOURCES = '+repr(sources)+'\nPATTERN = '+repr(args.pattern)+'\nREPEAT = '+repr(args.repeat)+'\nTEST_FILTER = '+repr(args.test_filter)+'\nVENDOR_FIXTURE = '+repr(args.powercalc_component_dir is not None)+'\n'+program, text=True, capture_output=True, timeout=args.timeout)
print(completed.stdout)
print(completed.stderr)
completed.check_returncode()
