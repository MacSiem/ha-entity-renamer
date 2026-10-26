"""Start a real-clock isolated fixture; the startup receipt is NOT a test pass.

Read status.json for terminal evidence. The owned fixture retains its synthetic
database and log on SSD. No global FIFO slot is held during timer waiting.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222',), default='ha-renamer-qa222')
parser.add_argument('--run-name', required=True)
parser.add_argument('--late-statistics-birth', action='store_true')
args = parser.parse_args()
assert args.run_name.isascii() and args.run_name.replace('-', '').isalnum()
info = json.loads(subprocess.check_output(['docker', 'inspect', args.container], text=True))[0]
mount = next(item['Source'] for item in info['Mounts'] if item['Destination'] == '/config')
assert info['State']['Running'] and '/ha-tools-v4-20261016/renamer222-' in mount
host = Path(mount) / ('statistics-' + args.run_name)
host.mkdir()  # Existing operation must be read, never implicitly repeated.
component = host / 'custom_components/ha_entity_renamer'
component.mkdir(parents=True)
source_head = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
for source in (ROOT / 'custom_components/ha_entity_renamer').glob('*.py'):
    shutil.copyfile(source, component / source.name)
shutil.copyfile(ROOT / 'tests/native_statistics_worker.py', host / 'worker.py')
# Bind the detached copy to its source revision and exact copied bytes.
manifest = {
    'source_head': source_head,
    'source_dirty': bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'])),
    'mode': 'late_statistics_birth' if args.late_statistics_birth else 'existing_statistics',
    'component': {source.name: hashlib.sha256(source.read_bytes()).hexdigest()
                  for source in sorted(component.glob('*.py'))},
    'worker_sha256': hashlib.sha256((host / 'worker.py').read_bytes()).hexdigest(),
}
assert source_head == subprocess.check_output(
    ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(), 'source HEAD changed during copy'
assert manifest['component'] == {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((ROOT / 'custom_components/ha_entity_renamer').glob('*.py'))
}, 'source files changed during copy'
assert manifest['worker_sha256'] == hashlib.sha256(
    (ROOT / 'tests/native_statistics_worker.py').read_bytes()).hexdigest(), 'worker changed during copy'
(host / 'source.json').write_text(json.dumps(manifest, sort_keys=True))
container_path = '/config/' + host.name
# Arguments contain only the fixed path and the validated alphanumeric run name.
mode_arg = ' --late-statistics-birth' if args.late_statistics_birth else ''
command = f'exec python -B {container_path}/worker.py{mode_arg} >{container_path}/worker.log 2>&1'
subprocess.run(['docker', 'exec', '-d', args.container, 'sh', '-c', command], check=True)
deadline = time.monotonic() + 45
while time.monotonic() < deadline:
    status_file = host / 'status.json'
    if status_file.exists():
        status = json.loads(status_file.read_text())
        print(json.dumps({'startup': status['status'], 'evidence': str(status_file),
                          'full_test': (status['status'] if status['status'] in ('passed', 'failed', 'harness_unknown')
                                        else 'PENDING')}))
        raise SystemExit(1 if status['status'] in ('failed', 'harness_unknown') else 0)
    time.sleep(.25)
raise SystemExit('Startup status unknown: inspect owned worker.log; do not retry this run name')
