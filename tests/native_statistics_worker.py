"""Isolated real-clock Recorder qualification; launched by native_statistics_probe.

No clock injection, SQL writes, live configuration, or production API calls.
The process waits for HA's own scheduled statistics compilation outside build FIFO.
"""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time
import traceback
from datetime import datetime, timedelta

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

parser = argparse.ArgumentParser()
parser.add_argument('--late-statistics-birth', action='store_true')
ARGS = parser.parse_args()
SOURCE = {}
LATE_EVIDENCE = {}

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from custom_components.ha_entity_renamer.native_registry import NativeRegistryIO, NativeRegistryBridge
from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard


def report(status, **details):
    temporary = ROOT / 'status.pending'
    temporary.write_text(json.dumps({'status': status, 'pid': os.getpid(),
                                     'time': time.time(), **SOURCE, **LATE_EVIDENCE, **details}))
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


class HarnessUnknown(AssertionError):
    """The required natural late-birth precondition was not observed."""


def bind_source_copy():
    manifest_path = ROOT / 'source.json'
    if not manifest_path.exists():
        if ARGS.late_statistics_birth:
            raise HarnessUnknown('source_manifest_missing')
        return  # Preserve direct invocation of the original default worker.
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    SOURCE.update(source_head=manifest['source_head'], source_dirty=manifest['source_dirty'],
                  source_manifest_sha256=hashlib.sha256(raw).hexdigest(),
                  worker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((ROOT / 'custom_components/ha_entity_renamer').glob('*.py'))}
    SOURCE['component_sha256'] = hashlib.sha256(
        json.dumps(actual, sort_keys=True).encode()).hexdigest()
    if (actual != manifest['component'] or SOURCE['worker_sha256'] != manifest['worker_sha256']
            or manifest['mode'] != ('late_statistics_birth' if ARGS.late_statistics_birth
                                    else 'existing_statistics')):
        raise HarnessUnknown('copied_source_manifest_mismatch')


def read_late_series(recorder, entity_id):
    # Entire real metadata + physical ShortTerm rows, SELECT only. Keep every
    # column, including row IDs and timestamp values; do not seed/import data.
    def value(item):
        return {'datetime': item.isoformat()} if isinstance(item, datetime) else item
    with session_scope(session=recorder.get_session(), read_only=True) as session:
        metadata = session.execute(select(StatisticsMeta).where(
            StatisticsMeta.statistic_id == entity_id)).scalar_one_or_none()
        if metadata is None:
            return {'metadata': None, 'rows': []}
        rows = session.execute(select(StatisticsShortTerm).where(
            StatisticsShortTerm.metadata_id == metadata.id).order_by(StatisticsShortTerm.id)).scalars()
        return {'metadata': {column.name: value(getattr(metadata, column.name))
                             for column in StatisticsMeta.__table__.columns},
                'rows': [{column.name: value(getattr(row, column.name))
                          for column in StatisticsShortTerm.__table__.columns} for row in rows]}


async def late_statistics_birth(hass, recorder, guard, io, bridge, meter, old, new, mapping, cycle):
    from homeassistant.const import __version__
    registry = er.async_get(hass)
    entry = registry.async_get(old)
    LATE_EVIDENCE.update(mode='late_statistics_birth', stage='prepare', native_version=__version__,
                         old_id=old, new_id=new, registry_uuid=entry.id,
                         prepare_statistics_absent=False, birth_after_forward=False,
                         reverse_called=False, live_config_access=False,
                         candidate_mechanism='before_position_recorder_snapshot_equals_original')
    report('waiting_late_prepare', full_test='PENDING')
    await io.flush('entity')
    snapshot = await guard.snapshot(mapping)
    if snapshot['statistics']:
        raise HarnessUnknown('statistics_already_present_before_prepare')
    try:
        prepared = await bridge.prepare('entity', entry.id, {'entity_id': new}, lambda: None)
    except Exception:
        if (await guard.snapshot(mapping))['statistics']:
            raise HarnessUnknown('statistics_birth_raced_prepare') from None
        raise
    baseline = json.loads(prepared.recorder_before_json)
    LATE_EVIDENCE['original_recorder'] = baseline
    if baseline['statistics']:
        raise HarnessUnknown('prepared_statistics_not_absent')
    LATE_EVIDENCE.update(prepare_statistics_absent=True, stage='forward')
    try:
        await bridge.apply(prepared, lambda: None)
    except Exception as error:
        # A birth before the forward effect does not reproduce AFTER-forward
        # birth. Preserve the real refusal, classify only that exact precondition.
        still_old = registry.async_get(old)
        if (meter.entity_id == old and still_old is not None and still_old.id == entry.id
                and (await guard.snapshot(mapping))['statistics']):
            LATE_EVIDENCE.update(error_type=type(error).__name__, error_reason=str(error))
            raise HarnessUnknown('statistics_birth_raced_forward_preflight') from error
        raise
    forward = await bridge.inspect(prepared)
    LATE_EVIDENCE['forward_evidence'] = asdict(forward)
    assert forward.position == 'after', forward
    assert meter.entity_id == new and registry.async_get(new).id == entry.id
    if (await guard.snapshot(mapping))['statistics']:
        raise HarnessUnknown('statistics_birth_not_observed_after_forward')
    # Ordinary native value publication. HA's real five-minute scheduler alone
    # will create the metadata/point. No clock movement or statistics import.
    meter._attr_native_value = 45
    meter.async_write_ha_state()
    await guard.snapshot(mapping)
    LATE_EVIDENCE['stage'] = 'natural_statistics_cycle'
    report('waiting_late_statistics_birth', full_test='PENDING', cycle_deadline_seconds=420)
    try:
        metadata_id, points = await cycle(new)
    except AssertionError as error:
        raise HarnessUnknown('no_natural_late_statistics_point_within_420_seconds') from error
    born = await recorder.async_add_executor_job(read_late_series, recorder, new)
    old_series = await recorder.async_add_executor_job(read_late_series, recorder, old)
    assert born['metadata']['id'] == metadata_id
    assert born['metadata']['source'] == 'recorder'
    if not born['rows'] or points[-1]['state'] != 45:
        raise HarnessUnknown('natural_late_point_did_not_include_updated_meter')
    assert old_series == {'metadata': None, 'rows': []}
    LATE_EVIDENCE.update(birth_after_forward=True, born_series=born, born_metadata_id=metadata_id,
                         before_reverse_old_series=old_series, stage='before_reverse')
    before_reverse = await bridge.inspect(prepared)
    LATE_EVIDENCE['before_reverse_evidence'] = asdict(before_reverse)
    assert before_reverse.position == 'after', before_reverse
    report('waiting_late_reverse', full_test='PENDING')

    async def capture_reverse():
        # Read only after the single compensate attempt; never replay UNKNOWN.
        evidence = await bridge.inspect(prepared)
        restored = await recorder.async_add_executor_job(read_late_series, recorder, old)
        other = await recorder.async_add_executor_job(read_late_series, recorder, new)
        metadata = await guard.snapshot(mapping)
        observed = registry.async_get(old)
        LATE_EVIDENCE.update(reverse_evidence=asdict(evidence), reverse_old_series=restored,
                             reverse_new_series=other, reverse_recorder=metadata,
                             reverse_registry_before=(observed is not None and observed.id == entry.id),
                             reverse_registry_after=(registry.async_get(new) is not None),
                             reverse_runtime_before=(meter.entity_id == old),
                             reverse_old_state_present=(hass.states.get(old) is not None),
                             reverse_new_state_absent=(hass.states.get(new) is None),
                             reverse_recorder_equals_original=(metadata == baseline),
                             reverse_metadata_exact=(restored['metadata'] ==
                                                     dict(born['metadata'], statistic_id=old)),
                             reverse_captured_rows_preserved=(
                                 restored['rows'][:len(born['rows'])] == born['rows']),
                             reverse_new_series_absent=(other == {'metadata': None, 'rows': []}),
                             reverse_metadata_id=(restored['metadata']['id']
                                                  if restored['metadata'] is not None else None))
        return evidence, restored, other

    LATE_EVIDENCE.update(stage='reverse', reverse_called=True)
    try:
        await bridge.compensate(prepared, lambda: None)
    except BaseException as error:
        LATE_EVIDENCE.update(failure_stage='reverse', error_type=type(error).__name__,
                             error_reason=str(error))
        try:
            await capture_reverse()
        except BaseException as readback_error:
            LATE_EVIDENCE['reverse_capture_error'] = type(readback_error).__name__
        raise  # An effect_uncertain refusal remains FAIL, never expected-PASS.
    evidence, restored, other = await capture_reverse()
    assert evidence.position == 'before', evidence
    expected_meta = dict(born['metadata'], statistic_id=old)
    assert restored['metadata'] == expected_meta
    # Native points may be appended, but every captured physical row stays exact.
    assert restored['rows'][:len(born['rows'])] == born['rows']
    assert other == {'metadata': None, 'rows': []}
    assert meter.entity_id == old and registry.async_get(old).id == entry.id
    LATE_EVIDENCE['stage'] = 'complete'
    report('passed', real_clock=True, late_statistics_preserved=True,
           metadata_identity_preserved=True, full_test='passed')


async def run():
    if ARGS.late_statistics_birth:
        LATE_EVIDENCE.update(mode='late_statistics_birth', stage='setup')
    bind_source_copy()
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

        if ARGS.late_statistics_birth:
            await late_statistics_birth(hass, recorder, guard, io, bridge, meter,
                                        old, new, mapping, cycle)
            return

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
    report('harness_unknown' if isinstance(error, HarnessUnknown) else 'failed',
           error_type=type(error).__name__, error_reason=str(error), live_config_access=False)
    raise
