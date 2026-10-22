"""Isolated real-clock Recorder qualification; launched by native_statistics_probe.

No clock injection, SQL writes, live configuration, or production API calls.
The process waits for HA's own scheduled statistics compilation outside build FIFO.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import time
import traceback
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant import loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.config_entries import ConfigEntries
from homeassistant.setup import async_setup_component
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import StatisticsMeta, StatisticsShortTerm
from homeassistant.components.recorder.util import session_scope
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from custom_components.ha_entity_renamer.native_registry import NativeRegistryIO, NativeRegistryBridge
from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard


def report(status, **details):
    temporary = ROOT / 'status.pending'
    temporary.write_text(json.dumps({'status': status, 'pid': os.getpid(),
                                     'time': time.time(), **details}))
    temporary.replace(ROOT / 'status.json')


class Meter(SensorEntity):
    _attr_name = 'Renamer Statistics Fixture'
    _attr_unique_id = 'renamer-statistics-fixture'
    _attr_native_value = 41
    _attr_native_unit_of_measurement = 'kWh'
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = False


def read_statistics(recorder, entity_id):
    with session_scope(session=recorder.get_session(), read_only=True) as session:
        metadata = session.execute(select(StatisticsMeta).where(
            StatisticsMeta.statistic_id == entity_id)).scalar_one_or_none()
        if metadata is None:
            return None, []
        rows = session.execute(select(StatisticsShortTerm).where(
            StatisticsShortTerm.metadata_id == metadata.id).order_by(StatisticsShortTerm.start_ts)).scalars()
        return metadata.id, [{column.name: getattr(row, column.name)
                              for column in StatisticsShortTerm.__table__.columns
                              if column.name not in ('created', 'start', 'last_reset')}
                             for row in rows]


async def run():
    config_root = ROOT / 'fixture'
    config_root.mkdir()
    hass = HomeAssistant(str(config_root))
    config = {'recorder': {'db_url': 'sqlite:///' + str(config_root / 'fixture.db'),
                          'commit_interval': 0, 'auto_purge': False, 'auto_repack': False}}
    try:
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, config)
        assert (await async_load_base_functionality(hass)) is not False
        from homeassistant.helpers.recorder import async_initialize_recorder
        async_initialize_recorder(hass)
        assert await async_setup_component(hass, 'recorder', config)
        assert await async_setup_component(hass, 'sensor', config)
        component = EntityComponent(logging.getLogger('statistics_fixture'), 'sensor', hass,
                                    timedelta(seconds=60))
        meter = Meter()
        await component.async_add_entities([meter])
        await hass.async_start()
        recorder = get_instance(hass)
        guard = NativeRecorderGuard(hass)
        io = NativeRegistryIO(hass)
        bridge = NativeRegistryBridge(io, guard)
        old, new = meter.entity_id, 'sensor.renamer_statistics_changed'
        mapping = {old: new}
        meter._attr_native_value = 42
        meter.async_write_ha_state()
        await guard.snapshot(mapping)

        async def cycle(entity_id, after_start=None):
            deadline = time.monotonic() + 420
            while time.monotonic() < deadline:
                metadata_id, rows = await recorder.async_add_executor_job(read_statistics, recorder, entity_id)
                if rows and (after_start is None or rows[-1]['start_ts'] > after_start):
                    return metadata_id, rows
                await asyncio.sleep(2)
            raise AssertionError('Native scheduled statistics cycle did not produce a point')

        report('waiting_baseline', live_config_access=False)
        metadata_id, before = await cycle(old)
        assert before[-1]['state'] == 42, before
        registry = er.async_get(hass)
        entry = registry.async_get(old)
        await io.flush('entity')
        prepared = await bridge.prepare('entity', entry.id, {'entity_id': new}, lambda: None)
        await bridge.apply(prepared, lambda: None)
        assert (await bridge.inspect(prepared)).position == 'after'
        assert meter.entity_id == new and registry.async_get(new).id == entry.id
        meter._attr_native_value = 45
        meter.async_write_ha_state()
        await guard.snapshot(mapping)
        report('waiting_after_rename', baseline_points=len(before), metadata_id=metadata_id,
               baseline_sum=before[-1]['sum'], live_config_access=False)
        after_id, after = await cycle(new, before[-1]['start_ts'])
        assert after_id == metadata_id
        assert after[:len(before)] == before
        assert after[-1]['state'] == 45
        assert abs(after[-1]['sum'] - before[-1]['sum'] - 3) < 1e-8
        assert (await recorder.async_add_executor_job(read_statistics, recorder, old)) == (None, [])
        await bridge.compensate(prepared, lambda: None)
        assert (await bridge.inspect(prepared)).position == 'before'
        restored_id, restored = await recorder.async_add_executor_job(read_statistics, recorder, old)
        assert restored_id == metadata_id and restored == after
        assert meter.entity_id == old and registry.async_get(old).id == entry.id
        report('passed', real_clock=True, historical_statistics_preserved=True,
               native_new_cycle=True, sum_delta=3, metadata_identity_preserved=True,
               rollback_statistics_preserved=True, live_config_access=False)
    finally:
        await hass.async_stop(force=True)


try:
    asyncio.run(run())
except BaseException as error:
    traceback.print_exc()
    report('failed', error_type=type(error).__name__, live_config_access=False)
    raise
