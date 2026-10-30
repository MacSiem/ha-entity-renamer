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
import signal
import sys
import uuid

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
if args.owned_ha_sources and args.container not in ('ha-renamer-qa222', 'ha-renamer-core104222'):
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
paths += list((ROOT/'tests').glob(args.pattern))
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
probe_token = uuid.uuid4().hex
pidfile = '/config/renamer-unit-' + probe_token + '.pid'
program = r'''
import os, sys, tempfile, pathlib, unittest, json, base64
import ctypes, signal, time
# This guardian stays outside the tests and reaps children even if they create
# sessions. Linux subreaper adoption keeps orphaned grandchildren in our scope.
if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
    raise OSError(ctypes.get_errno(), 'cannot establish probe subreaper')
start = pathlib.Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
with open(os.open(PIDFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as record:
    json.dump({'pid': os.getpid(), 'start': start, 'token': PROBE_TOKEN}, record)
stopping = False
def terminate(signum, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, terminate)
child = os.fork()
if child:
    status = None
    while True:
        try:
            pid, code = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == child:
            status = code
        if pid == 0:
            time.sleep(0.05)
    raise SystemExit(124 if stopping else os.waitstatus_to_exitcode(status))
signal.signal(signal.SIGTERM, signal.SIG_DFL)
with tempfile.TemporaryDirectory(prefix='renamer-unit-' + PROBE_TOKEN + '-', dir='/config') as directory:
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
# A separate exec can only signal processes after validating their exact identity.
# The guardian above, rather than Docker CLI's host group, owns and reaps them.
cleanup_program = r'''
import json, os, pathlib, signal, sys, time
container_root = pathlib.Path('/config')
token, pidfile = sys.argv[1:]
def process(pid):
    try:
        base = pathlib.Path('/proc') / str(pid)
        fields = (base / 'stat').read_text().rsplit(')', 1)[1].split()
        argv = (base / 'cmdline').read_bytes().split(b'\0')
        return {'pid': pid, 'ppid': int(fields[1]), 'start': fields[19], 'argv': argv}
    except (FileNotFoundError, ProcessLookupError):
        return None
def census():
    return {int(p.name): row for p in pathlib.Path('/proc').iterdir()
            if p.name.isdecimal() and (row := process(int(p.name))) is not None}
def marked(row):
    return row['argv'][:-1] == [b'python', b'-X', b'faulthandler', b'-B', b'-', token.encode()]
rows = census()
record_path = pathlib.Path(pidfile)
if record_path.exists():
    record = json.loads(record_path.read_text())
    root = rows.get(record['pid'])
    if root is not None and (record['token'] != token or root['start'] != record['start'] or not marked(root)):
        raise RuntimeError('probe identity mismatch; cleanup refused')
else:
    root = None
if root is None:
    matches = {pid: row for pid, row in rows.items() if marked(row)}
    roots = [row for row in matches.values() if row['ppid'] not in matches]
    if len(roots) > 1:
        raise RuntimeError('ambiguous probe identity; cleanup refused')
    root = roots[0] if roots else None
owned = {}
if root is not None:
    owned[root['pid']] = root
    def refresh():
        rows = census()
        changed = True
        while changed:
            changed = False
            for pid, row in rows.items():
                parent = rows.get(row['ppid'])
                known = owned.get(row['ppid'])
                if pid not in owned and parent and known and parent['start'] == known['start']:
                    owned[pid] = row
                    changed = True
    def alive(row):
        current = process(row['pid'])
        return current is not None and current['start'] == row['start']
    def send(row, sig):
        if alive(row):
            try:
                os.kill(row['pid'], sig)
            except ProcessLookupError:
                pass
    refresh()
    for row in list(owned.values())[::-1]:
        send(row, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while any(alive(row) for row in owned.values()) and time.monotonic() < deadline:
        refresh()
        time.sleep(0.05)
    refresh()
    for pid, row in owned.items():
        if pid != root['pid']:
            send(row, signal.SIGKILL)
    # Allow the guardian to reap all adopted children before its own exit.
    deadline = time.monotonic() + 5
    while any(alive(row) for row in owned.values()) and time.monotonic() < deadline:
        time.sleep(0.05)
    if any(alive(row) for row in owned.values()):
        raise RuntimeError('probe cleanup incomplete; owned processes remain')
# Interrupted fixtures remain local for diagnosis, never recursively deleted.
quarantine = container_root / ('renamer-unit-quarantine-' + token)
for path in container_root.glob('renamer-unit-' + token + '-*'):
    quarantine.mkdir(mode=0o700, exist_ok=True)
    path.rename(quarantine / path.name)
if record_path.exists():
    quarantine.mkdir(mode=0o700, exist_ok=True)
    record_path.rename(quarantine / record_path.name)
print(json.dumps({'probe_cleanup': True, 'owned_processes_remaining': 0}))
'''
class ProbeInterrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum


def interrupt_probe(signum, frame):
    # The FIFO runner terminates its host process group. Convert that signal
    # into normal stack unwinding so the separately authenticated container
    # guardian cleanup in finally always runs. Ignore repeated host signals
    # while reaping; never signal an unverified container PID directly.
    for handled in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(handled, signal.SIG_IGN)
    raise ProbeInterrupted(signum)


for handled in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(handled, interrupt_probe)

payload = 'SOURCES = '+repr(sources)+'\nBINARY_SOURCES = '+repr(binary_sources)+'\nPROFILE_FIXTURE = '+repr(args.powercalc_profile_dir is not None)+'\nPATTERN = '+repr(args.pattern)+'\nREPEAT = '+repr(args.repeat)+'\nTEST_FILTER = '+repr(args.test_filter)+'\nVENDOR_FIXTURE = '+repr(args.powercalc_component_dir is not None)+'\nOWNED_HA_SOURCES = '+repr(args.owned_ha_sources)+'\nPROBE_TOKEN = '+repr(probe_token)+'\nPIDFILE = '+repr(pidfile)+'\n'+program
try:
    completed = subprocess.run(['docker','exec','-i',args.container,'python','-X','faulthandler','-B','-',probe_token],
        input=payload, text=True, capture_output=True, timeout=args.timeout)
except ProbeInterrupted as interrupted:
    print(json.dumps({'probe_interrupted_by_signal': interrupted.signum}), flush=True)
    raise SystemExit(128 + interrupted.signum)
except subprocess.TimeoutExpired as timeout:
    # TimeoutExpired carries bytes even with text=True. Flush partial evidence
    # before cleanup or the original timeout is raised to the build receipt.
    for stream, partial in ((sys.stdout, timeout.stdout), (sys.stderr, timeout.stderr)):
        if partial:
            stream.write(partial.decode('utf-8', errors='replace') if isinstance(partial, bytes) else partial)
        stream.flush()
    raise
else:
    print(completed.stdout, flush=True)
    print(completed.stderr, file=sys.stderr, flush=True)
    completed.check_returncode()
finally:
    cleanup = subprocess.run(['docker','exec','-i',args.container,'python','-B','-',probe_token,pidfile],
        input=cleanup_program, text=True, capture_output=True, timeout=20)
    print(cleanup.stdout, flush=True)
    print(cleanup.stderr, file=sys.stderr, flush=True)
    cleanup.check_returncode()
