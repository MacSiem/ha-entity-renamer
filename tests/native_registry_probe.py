"""Native registry + real Recorder event probe in a separate HA runtime.

Uses an isolated SSD directory and synthetic SensorEntity. Never calls the running
QA or production APIs, loads their configuration, or opens their database.
Full statistics-cycle and installed-product QA remain separate requirements.
"""
from pathlib import Path
import argparse
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('reference_planner', 'persistence', 'dashboard_plan', 'lovelace_native',
         'recorder_guard', 'native_registry', 'energy_plan', 'energy_native',
         'transaction', 'journal', 'helper_plan', 'helper_native', 'helper_persistence',
         'prepared', 'helper_transaction', 'helper_observers', 'native_adapters')
sources = {name: (ROOT / 'custom_components/ha_entity_renamer' / (name + '.py')).read_text()
           for name in NAMES}
sources['native_helper_runtime_scenario'] = (ROOT / 'tests/native_helper_runtime_scenario.py').read_text()
sources['native_linked_scenario'] = (ROOT / 'tests/native_linked_scenario.py').read_text()
probe = '''
import asyncio
import logging
import os
import sys
import tempfile
import types
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.components.recorder import get_instance
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.helpers.entity_component import EntityComponent
from sqlalchemy import select
from homeassistant.components.recorder.db_schema import States
from homeassistant.components.recorder.util import session_scope
from homeassistant.const import __version__
package = types.ModuleType('migration_probe'); package.__path__ = []
sys.modules['migration_probe'] = package
for name, source in SOURCES.items():
    module = types.ModuleType('migration_probe.' + name); module.__package__ = 'migration_probe'
    sys.modules[module.__name__] = module
    exec(compile(source, '<' + name + '>', 'exec'), module.__dict__)
from migration_probe.native_registry import NativeRegistryIO, NativeRegistryBridge
from migration_probe.recorder_guard import NativeRecorderGuard

class Meter(SensorEntity):
    _attr_name = 'Renamer Probe Meter'
    _attr_unique_id = 'renamer-probe-meter'
    _attr_native_value = 41
    _attr_native_unit_of_measurement = 'kWh'
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = False


def points(recorder, metadata_id, ceiling=None):
    with session_scope(session=recorder.get_session(), read_only=True) as session:
        query = select(States.state_id, States.state, States.last_updated_ts).where(States.metadata_id == metadata_id)
        if ceiling is not None: query = query.where(States.state_id <= ceiling)
        return [tuple(row) for row in session.execute(query.order_by(States.state_id))]

async def run(root):
    os.chdir(root)
    hass = HomeAssistant(root)
    config = {'recorder': {'db_url': 'sqlite:///' + root + '/fixture.db', 'commit_interval': 0,
                            'auto_purge': False, 'auto_repack': False},
              'http': {'server_port': 0}}
    try:
        from homeassistant import loader
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.config_entries import ConfigEntries
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, config)
        await loader.async_get_custom_components(hass)
        assert (await async_load_base_functionality(hass)) is not False
        from homeassistant.helpers.recorder import async_initialize_recorder
        async_initialize_recorder(hass)
        assert await async_setup_component(hass, 'recorder', config)
        component = EntityComponent(logging.getLogger('probe'), 'sensor', hass, timedelta(seconds=60))
        meter = Meter()
        await component.async_add_entities([meter])
        await hass.async_start()
        recorder = get_instance(hass)
        guard = NativeRecorderGuard(hass)
        io = NativeRegistryIO(hass)
        old = meter.entity_id
        mapping = {old: 'sensor.renamer_probe_changed'}
        meter._attr_native_value = 42
        meter.async_write_ha_state()
        baseline = await guard.snapshot(mapping)
        registry = er.async_get(hass)
        entry = registry.async_get(old)
        assert entry is not None
        await io.flush('entity')
        bridge = NativeRegistryBridge(io, guard)
        prepared = await bridge.prepare('entity', entry.id, {'entity_id': mapping[old]}, lambda: None)
        metadata_id = baseline['states'][old]['metadata_id']
        history = await recorder.async_add_executor_job(points, recorder, metadata_id)
        assert len(history) >= 1
        ceiling = history[-1][0]
        linked = None
        if LINKED:
            from migration_probe.native_linked_scenario import make_linked
            async def history_check():
                return await recorder.async_add_executor_job(points, recorder, metadata_id, ceiling) == history
            linked = await make_linked(hass, bridge, prepared, history_check, helpers=HELPERS,
                                       helper_kind=HELPER_KIND, component=component)
            applied = await linked.coordinator.apply(linked.plan, linked.plan.fingerprint, 'linked_op')
            assert applied.status == 'applied', applied
            assert len(linked.updates) == 1
        else:
            await bridge.apply(prepared, lambda: None)
        evidence = await bridge.inspect(prepared)
        assert evidence.position == 'after', evidence
        assert meter.entity_id == mapping[old]
        assert registry.async_get(mapping[old]).id == entry.id
        assert await recorder.async_add_executor_job(points, recorder, metadata_id, ceiling) == history
        meter._attr_native_value = 43
        meter.async_write_ha_state()
        await guard.snapshot(mapping)
        current = await recorder.async_add_executor_job(points, recorder, metadata_id)
        assert float(current[-1][1]) == 43 and current[-1][0] > ceiling, (history, current, hass.states.get(meter.entity_id).state)
        if linked and linked.helper:
            if linked.helper.auxiliary:
                linked.helper.auxiliary._attr_native_value = 5
                linked.helper.auxiliary.async_write_ha_state()
            await linked.helper.runtime(linked.helper.after)
        if linked:
            await linked.reopen()
            replay = await linked.coordinator.apply(linked.plan, linked.plan.fingerprint, 'linked_op')
            assert replay.status == 'applied' and len(linked.updates) == 1
            undone = await linked.coordinator.rollback('linked_op')
            assert undone.status == 'rolled_back', undone
            assert len(linked.updates) == 2
            print({'linked_native_migration': 'PASS', 'durable_reopen_no_replay': 'PASS',
                   'whole_fixture_scope_readback': 'PASS', 'linked_compensation': 'PASS'})
        else:
            await bridge.compensate(prepared, lambda: None)
        if linked and linked.helper:
            if linked.helper.auxiliary:
                linked.helper.auxiliary._attr_native_value = 2
                linked.helper.auxiliary.async_write_ha_state()
            meter._attr_native_value = 41
            meter.async_write_ha_state()
            await hass.async_block_till_done()
            await linked.helper.runtime(linked.helper.before)
            print({'helper_kind': HELPER_KIND, 'native_helper_activation': 'PASS', 'source_change_after_rename': 'PASS',
                   'source_change_after_rollback': 'PASS', 'helper_stable_identity': 'PASS'})
        assert (await bridge.inspect(prepared)).position == 'before'
        assert meter.entity_id == old and registry.async_get(old).id == entry.id
        assert await recorder.async_add_executor_job(points, recorder, metadata_id, ceiling) == history
        print({'ha': __version__, 'native_registry_event': 'PASS', 'sensor_object_identity': 'PASS',
               'recorder_metadata_identity': 'PASS', 'historical_points': 'PASS',
               'new_state_point': 'PASS', 'compensation': 'PASS',
               'new_statistics_cycle': 'NOT_TESTED', 'live_config_access': False})
    finally:
        await hass.async_stop(force=True)

with tempfile.TemporaryDirectory(prefix='renamer223-registry-probe-', dir='/config') as root:
    asyncio.run(run(root))
'''
parser = argparse.ArgumentParser()
parser.add_argument('--container', choices=('ha-renamer-qa222', 'ha-renamer-min222'), default='ha-renamer-qa222')
parser.add_argument('--linked', action='store_true')
parser.add_argument('--helpers', action='store_true')
parser.add_argument('--helper-kind', choices=('threshold', 'min', 'max', 'mean', 'median', 'range', 'sum'), default='threshold')
args = parser.parse_args()
container = args.container
assert not args.helpers or args.linked, '--helpers requires --linked'
assert not args.linked or container == 'ha-renamer-qa222', 'Linked Energy/Lovelace schema currently qualified on current HA only'
inspected = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
assert inspected['State']['Running']
mounts = [item.get('Source', '') for item in inspected['Mounts'] if item['Destination'] == '/config']
assert len(mounts) == 1 and '/ha-tools-v4-20261016/renamer222-' in mounts[0]
completed = subprocess.run(['docker', 'exec', '-i', container, 'python', '-B', '-'],
    input='SOURCES = ' + repr(sources) + '\nLINKED = ' + repr(args.linked) + '\nHELPERS = ' + repr(args.helpers) + '\nHELPER_KIND = ' + repr(args.helper_kind) + '\n' + probe,
    text=True, capture_output=True, timeout=120)
print(completed.stdout)
print(completed.stderr)
completed.check_returncode()
