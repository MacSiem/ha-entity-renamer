"""Run synthetic Python tests with HA's installed dependencies in an owned container.

Repository sources and an optional full public Powercalc fixture are copied to
a new temporary directory on the owned SSD mount. No existing HA configuration, API or database is opened.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222', 'ha-renamer-core104222'), default='ha-renamer-qa222')
parser.add_argument('--pattern', default='test*.py')
parser.add_argument('--repeat', type=int, default=1)
parser.add_argument('--timeout', type=int, default=600, help='Container suite timeout in seconds')
parser.add_argument('--test-filter', default='', help='Only discovered test IDs containing this string')
parser.add_argument('--powercalc-component-dir', type=Path, help='Full public Powercalc fixture copied only to the isolated suite directory')
parser.add_argument('--powercalc-profile-dir', type=Path, help='Exact public binary profile inputs copied only into the isolated suite directory')
parser.add_argument('--owned-ha-sources', action='store_true',
                    help='Copy installed public HA package before imports for isolated source-mutation negatives')
args = parser.parse_args()
if args.owned_ha_sources and args.container != 'ha-renamer-qa222':
    parser.error('owned HA public-source fixture is current-only')
assert args.repeat >= 1 and args.timeout > 0 and '/' not in args.pattern and '\\' not in args.pattern
if not any(path.is_file() for path in (ROOT/'tests').glob(args.pattern)):
    parser.error('test pattern selected no source files')
info = json.loads(subprocess.check_output(['docker', 'inspect', args.container], text=True))[0]
assert info['State']['Running']
mounts = [m['Source'] for m in info['Mounts'] if m['Destination'] == '/config']
assert len(mounts) == 1 and '/ha-tools-v4-20261016/renamer222-' in mounts[0]
paths = list((ROOT/'custom_components/ha_entity_renamer').glob('*.py'))
# Preserve genuine public translations for native config-flow bootstrap too.
paths += list((ROOT/'custom_components/ha_entity_renamer').rglob('*.json'))
paths += list((ROOT/'tests').glob('test*.py'))
paths += [ROOT/'tests/native_template_composition_scenario.py']
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
binary_sources = {}
if args.powercalc_profile_dir is not None:
    profile_root = args.powercalc_profile_dir.resolve(strict=True)
    if not profile_root.is_relative_to(Path('/Volumes/Samsung_SSD')):
        parser.error('public profile fixture must be on owned SSD')
    raw_manifest = (profile_root / 'PUBLIC-INPUTS.json').read_bytes()
    profile_manifest = json.loads(raw_manifest)
    if profile_manifest.get('commit') != 'fcb7736b2cbf64649487c1708d10093f25ec1b21':
        parser.error('immutable official v1.26.1 profile input manifest required')
    for row in profile_manifest['blobs']:
        if not row['selected']:
            continue
        name = row['path']
        if (not name.startswith('profile_library/') or Path(name).is_absolute()
                or any(piece in ('', '.', '..') for piece in name.split('/'))):
            parser.error('invalid public profile path')
        file = profile_root / name
        if file.is_symlink() or any(parent.is_symlink() for parent in file.parents
                                    if parent.is_relative_to(profile_root)):
            parser.error('public profile symlink refused')
        raw = file.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            parser.error('public profile content mismatch')
        binary_sources['_public_profiles/' + name] = base64.b64encode(raw).decode('ascii')
    binary_sources['_public_profiles/PUBLIC-INPUTS.json'] = base64.b64encode(raw_manifest).decode('ascii')
program = r'''
import os, sys, tempfile, pathlib, unittest, json, base64
with tempfile.TemporaryDirectory(prefix='renamer-unit-', dir='/config') as directory:
    root = pathlib.Path(directory)
    for name, source in SOURCES.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for name, encoded in BINARY_SOURCES.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded, validate=True))
    os.chdir(root)
    os.environ['TMPDIR'] = directory
    tempfile.tempdir = directory
    if VENDOR_FIXTURE:
        os.environ['POWERCALC_COMPONENT_DIR'] = str(root / '_public_vendor/powercalc')
    if PROFILE_FIXTURE:
        os.environ['POWERCALC_PROFILE_DIR'] = str(root / '_public_profiles')
    if OWNED_HA_SOURCES:
        # Before test discovery or ANY HA import: normal Python imports compile
        # the genuine copied bytes with their actual owned co_filename paths.
        import importlib.util, shutil
        if any(name == 'homeassistant' or name.startswith('homeassistant.') for name in sys.modules):
            raise RuntimeError('HA must not be imported before owned source isolation')
        spec = importlib.util.find_spec('homeassistant')
        if spec is None or spec.origin is None:
            raise RuntimeError('installed public HA package unavailable')
        installed = pathlib.Path(spec.origin).resolve().parent
        owned_parent = root / '_owned_ha_sources'
        owned = owned_parent / 'homeassistant'
        shutil.copytree(installed, owned, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        os.environ['RENAMER_TEST_SCRATCH'] = directory
        os.environ['RENAMER_OWNED_HA_SOURCE_ROOT'] = str(owned)
        sys.path.insert(0, str(owned_parent))
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
    input='SOURCES = '+repr(sources)+'\nBINARY_SOURCES = '+repr(binary_sources)+'\nPROFILE_FIXTURE = '+repr(args.powercalc_profile_dir is not None)+'\nPATTERN = '+repr(args.pattern)+'\nREPEAT = '+repr(args.repeat)+'\nTEST_FILTER = '+repr(args.test_filter)+'\nVENDOR_FIXTURE = '+repr(args.powercalc_component_dir is not None)+'\nOWNED_HA_SOURCES = '+repr(args.owned_ha_sources)+'\n'+program, text=True, capture_output=True, timeout=args.timeout)
print(completed.stdout)
print(completed.stderr)
completed.check_returncode()
