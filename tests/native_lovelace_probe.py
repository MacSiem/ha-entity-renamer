"""Exercise actual HA Store/LovelaceStorage in an isolated SSD fixture directory.

No running HA API, live config file, or private configuration is read or changed.
This qualifies the native boundary, not an installed end-to-end migration.
"""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('reference_planner', 'persistence', 'dashboard_plan', 'lovelace_native')
sources = {name: (ROOT / 'custom_components/ha_entity_renamer' / (name + '.py')).read_text()
           for name in NAMES}
probe = '''
import asyncio
import sys
import tempfile
import types
from pathlib import Path
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.dashboard import LovelaceStorage
from homeassistant.const import __version__
package = types.ModuleType('migration_probe')
package.__path__ = []
sys.modules['migration_probe'] = package
for name, source in SOURCES.items():
    module = types.ModuleType('migration_probe.' + name)
    module.__package__ = 'migration_probe'
    sys.modules[module.__name__] = module
    exec(compile(source, '<' + name + '>', 'exec'), module.__dict__)
from migration_probe.lovelace_native import NativeLovelace
from migration_probe.persistence import PersistenceError

async def run(root):
    hass = HomeAssistant(root)
    default = LovelaceStorage(hass, None)
    metadata = {'id': 'fixture', 'url_path': 'fixture-board', 'mode': 'storage',
                'require_admin': True, 'title': 'Fixture'}
    named = LovelaceStorage(hass, metadata)
    hass.data[LOVELACE_DATA] = types.SimpleNamespace(dashboards={None: default, 'fixture-board': named})
    await Store(hass, 1, 'lovelace_dashboards').async_save({'items': [metadata]})
    before = {'views': [{'cards': [{'type': 'entity', 'entity': 'sensor.fixture_old'}]}]}
    await named.async_save(before)
    # Force a real cold Lovelace cache, while preserving disk and native Store.
    named._data = None
    bridge = NativeLovelace(hass)
    descriptors, yaml = await bridge.enumerate()
    assert len(descriptors) == 2 and not yaml
    empty = next(d for d in descriptors if d.route is None)
    assert (await bridge.plan(empty, {'sensor.fixture_old': 'sensor.fixture_new'}))[1] is None
    descriptor = next(d for d in descriptors if d.route is not None)
    snapshot, plan = await bridge.plan(descriptor, {'sensor.fixture_old': 'sensor.fixture_new'})
    assert not plan.markers and len(plan.changes) == 1
    prepared = bridge.prepare(snapshot, plan)
    await bridge.write(prepared, lambda: None)
    assert await bridge.verify(descriptor, prepared.after_revision)
    # A fresh manager must read the persisted result, not the previous cache.
    fresh = LovelaceStorage(hass, metadata)
    hass.data[LOVELACE_DATA].dashboards['fixture-board'] = fresh
    assert await bridge.verify(descriptor, prepared.after_revision)
    # Reverse patch uses the fresh inode/content rather than the stale baseline.
    rollback_snapshot, rollback = await bridge.plan(descriptor, {'sensor.fixture_new': 'sensor.fixture_old'})
    restore = bridge.prepare(rollback_snapshot, rollback)
    await bridge.write(restore, lambda: None)
    assert await bridge.verify(descriptor, snapshot.revision)
    # Model HA's documented logged-but-not-raised Store failure using native code.
    from homeassistant.util.file import WriteError
    async def fail(_): raise WriteError('synthetic fixture write failure')
    fresh._store._async_write_data = fail
    current, forward = await bridge.plan(descriptor, {'sensor.fixture_old': 'sensor.fixture_new'})
    try:
        await bridge.write(bridge.prepare(current, forward), lambda: None)
    except PersistenceError:
        assert bridge._failed
    else:
        raise AssertionError('native silent Store failure was accepted')
    await hass.async_block_till_done()
    await hass.async_stop(force=True)
    print({'ha': __version__, 'native_lovelace': 'PASS', 'cold_cache': 'PASS',
           'fresh_manager_readback': 'PASS', 'reverse_patch': 'PASS',
           'silent_store_failure': 'PASS', 'live_config_access': False})

with tempfile.TemporaryDirectory(prefix='renamer223-lovelace-probe-', dir='/config') as root:
    asyncio.run(run(root))
'''
container = 'ha-renamer-qa222'
inspected = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
assert inspected['State']['Running']
mounts = [item.get('Source', '') for item in inspected['Mounts'] if item['Destination'] == '/config']
assert len(mounts) == 1 and '/ha-tools-v4-20261016/renamer222-native/' in mounts[0]
completed = subprocess.run(['docker', 'exec', '-i', container, 'python', '-B', '-'],
    input='SOURCES = ' + repr(sources) + '\n' + probe,
    text=True, capture_output=True, timeout=60)
print(completed.stdout)
print(completed.stderr)
completed.check_returncode()
