"""Exercise actual HA Store/LovelaceStorage in an isolated SSD fixture directory.

No running HA API, live config file, or private configuration is read or changed.
This qualifies the native boundary, not an installed end-to-end migration.
"""
from pathlib import Path
import json
import argparse
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('reference_planner', 'persistence', 'dashboard_plan', 'lovelace_native', 'transaction', 'journal', 'energy_plan', 'energy_native', 'helper_plan', 'helper_native', 'helper_persistence')
sources = {name: (ROOT / 'custom_components/ha_entity_renamer' / (name + '.py')).read_text()
           for name in NAMES}
sources['native_helper_persistence_scenario'] = (ROOT / 'tests/native_helper_persistence_scenario.py').read_text()
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
    from migration_probe.journal import native_journal
    from migration_probe.transaction import Plan, Step, Record
    journal = native_journal(hass)
    assert journal.store._writer.__self__._atomic_writes is True
    await journal.initialize()
    frozen = Plan('fixture', ('scope',), ('scope',),
        (Step('one', 'fixture', 'stable', 'before', 'after', 'local'),), 'proof', ('fixture',))
    record = Record('fixture_op', frozen, phases=('intent',), status='unknown')
    await journal.create(record)
    fresh_journal = native_journal(hass)
    await fresh_journal.initialize()
    assert await fresh_journal.load('fixture_op') == record
    assert len(await fresh_journal.active()) == 1
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
    async def fail(*args): raise WriteError('synthetic fixture write failure')
    fresh._store._async_write_data = fail
    current, forward = await bridge.plan(descriptor, {'sensor.fixture_old': 'sensor.fixture_new'})
    try:
        await bridge.write(bridge.prepare(current, forward), lambda: None)
    except PersistenceError:
        assert bridge._failed
    else:
        raise AssertionError('native silent Store failure was accepted')
    from homeassistant.components.energy.data import EnergyManager
    from migration_probe.energy_native import NativeEnergy
    manager = EnergyManager(hass)
    await manager.async_initialize()
    energy = NativeEnergy(hass, manager)
    assert (await energy.plan({'sensor.fixture_old': 'sensor.fixture_new'}))[1] is None
    preferences = {'energy_sources': [{'type': 'solar', 'stat_energy_from': 'sensor.fixture_old'}],
                   'device_consumption': [{'stat_consumption': 'sensor.fixture_old'}],
                   'device_consumption_water': []}
    if __version__ == '2025.6.0':
        preferences.pop('device_consumption_water')
    await manager.async_update(preferences)
    await manager._store.async_save(manager.data)
    updates = []
    async def observed(): updates.append(True)
    manager.async_listen_updates(observed)
    initial, energy_plan = await energy.plan({'sensor.fixture_old': 'sensor.fixture_new'})
    ready = energy.prepare(initial, energy_plan)
    await energy.write(ready, lambda: None)
    assert len(updates) == 1
    assert await energy.verify(ready.after_revision)
    reloaded_manager = EnergyManager(hass)
    await reloaded_manager.async_initialize()
    assert reloaded_manager.data == ready.after
    after, reverse = await energy.plan({'sensor.fixture_new': 'sensor.fixture_old'})
    await energy.write(energy.prepare(after, reverse), lambda: None)
    assert len(updates) == 2 and await energy.verify(initial.revision)
    print({'native_energy': 'PASS', 'energy_listener': 'PASS', 'energy_reverse': 'PASS',
           'fresh_energy_manager': 'PASS'})
    if __version__ == '2026.10.2':
        from migration_probe.native_helper_persistence_scenario import exercise
        await exercise(hass)
    await hass.async_block_till_done()
    await hass.async_stop(force=True)
    print({'ha': __version__, 'native_lovelace': 'PASS', 'cold_cache': 'PASS',
           'fresh_manager_readback': 'PASS', 'reverse_patch': 'PASS',
           'silent_store_failure': 'PASS', 'atomic_journal_readback': 'PASS', 'live_config_access': False})

with tempfile.TemporaryDirectory(prefix='renamer223-lovelace-probe-', dir='/config') as root:
    asyncio.run(run(root))
'''
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222'), default='ha-renamer-qa222')
container = parser.parse_args().container
inspected = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
assert inspected['State']['Running']
mounts = [item.get('Source', '') for item in inspected['Mounts'] if item['Destination'] == '/config']
expected = 'native' if container == 'ha-renamer-qa222' else 'minimum'
assert len(mounts) == 1 and '/ha-tools-v4-20261016/renamer222-' + expected + '/' in mounts[0]
completed = subprocess.run(['docker', 'exec', '-i', container, 'python', '-B', '-'],
    input='SOURCES = ' + repr(sources) + '\n' + probe,
    text=True, capture_output=True, timeout=60)
print(completed.stdout)
print(completed.stderr)
completed.check_returncode()
