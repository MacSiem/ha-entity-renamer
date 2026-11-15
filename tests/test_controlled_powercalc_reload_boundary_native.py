"""Isolated vendor reload boundary; no Renamer operation or replay."""
import asyncio
from decimal import Decimal
import json
import sys
import unittest

import test_powercalc_numeric_continuity_native as numeric


@unittest.skipUnless(numeric.accepted.fixture.HAS_HA, 'actual supported HA required')
class NativePowercalcReloadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = numeric.PowercalcNumericContinuityNativeTests.namespace_source_bytes
    _flush_documents = numeric.PowercalcNumericContinuityNativeTests._flush_documents
    guard = numeric.PowercalcNumericContinuityNativeTests.guard
    module = numeric.PowercalcNumericContinuityNativeTests.module

    async def _setup_real_vendor(self):
        if self._testMethodName == 'test_native_group_handoff_then_future_stimulus':
            from homeassistant.helpers.recorder import async_initialize_recorder
            from homeassistant.setup import async_setup_component
            hass = self.f.hass
            async_initialize_recorder(hass)
            self.assertTrue(await async_setup_component(hass, 'recorder', {'recorder': {
                'db_url': 'sqlite:///' + str(self.f.root / 'fixture.db'),
                'commit_interval': 0, 'auto_purge': False, 'auto_repack': False}}))
        await numeric.PowercalcNumericContinuityNativeTests._setup_real_vendor(self)

    async def asyncSetUp(self):
        self.group_fixture_options = {'energy_sensor_precision': 2}
        await numeric.PowercalcNumericContinuityNativeTests.asyncSetUp(self)

    async def test_direct_native_group_reload_boundary(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        from custom_components.powercalc.sensors.group.custom import GroupedEnergySensor
        from ordinary_network_scope import native_dummy_mdns
        hass, group = self.f.hass, self.sum_energy
        print('NETWORK_BEFORE', native_dummy_mdns(hass))
        await self.sources[0].async_turn_on(brightness=255)
        async with asyncio.timeout(12):
            while not (type(group._native_value_exact) is Decimal and group._native_value_exact > 0
                       and group._native_value_exact != round(group._native_value_exact, 2)):
                await asyncio.sleep(0.05)
        await self.sources[0].async_turn_off()
        await self.sources[1].async_turn_off()
        await hass.async_block_till_done()
        await asyncio.sleep(2)
        await hass.async_block_till_done()
        before = group._native_value_exact
        members_before = {key: state.as_dict() for key, state in
                          group._prev_state_store.states[group.entity_id].items()}
        uuid, entity_id = group.registry_entry.id, group.entity_id
        self.assertNotEqual(before, round(before, 2))
        self.assertTrue(await hass.config_entries.async_reload(self.group.entry_id))
        await hass.async_block_till_done()
        rows = [entity for platform in async_get_platforms(hass, 'powercalc')
                for entity in platform.entities.values()
                if type(entity) is GroupedEnergySensor and entity.entity_id == entity_id]
        self.assertEqual(len(rows), 1)
        after = rows[0]
        members_after = {key: state.as_dict() for key, state in
                         after._prev_state_store.states[entity_id].items()}
        self.assertEqual(after.registry_entry.id, uuid)
        self.assertEqual(members_before, members_after, 'member drift makes precision observation inconclusive')
        self.assertEqual(after._native_value_exact, round(before, 2))
        self.assertNotEqual(after._native_value_exact, before)
        print('NETWORK_AFTER', native_dummy_mdns(hass))
        print('NATIVE_RELOAD_PRECISION_BOUNDARY', {'before': str(before),
              'after': str(after._native_value_exact), 'registry_uuid_stable': True,
              'pss_members_unchanged': True})

    async def test_native_group_handoff_then_future_stimulus(self):
        import base64
        from pathlib import Path
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from custom_components.powercalc.sensors.group.custom import GroupedEnergySensor
        from ordinary_network_scope import native_dummy_mdns
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.db_schema import StatisticsMeta, StatisticsShortTerm
        from homeassistant.components.recorder.util import session_scope
        from sqlalchemy import select
        hass, group = self.f.hass, self.sum_energy
        old, new = group.entity_id, 'sensor.pc_boundary_target'
        registry = self.f.entity
        uid = group.registry_entry.id
        await self.sources[0].async_turn_on(brightness=255)
        async with asyncio.timeout(12):
            while not (type(group._native_value_exact) is Decimal and group._native_value_exact > 0
                       and group._native_value_exact != round(group._native_value_exact, 2)):
                await asyncio.sleep(0.05)
        await self.sources[0].async_turn_off()
        await self.sources[1].async_turn_off()
        await hass.async_block_till_done()
        await asyncio.sleep(2)
        await hass.async_block_till_done()
        print('NETWORK_BEFORE_HANDOFF', native_dummy_mdns(hass))
        pss = group._prev_state_store
        restore = hass.data[DATA_RESTORE_STATE]
        recorder = get_instance(hass)
        def full_statistics(entity_id):
            def value(item):
                return {'datetime': item.isoformat()} if hasattr(item, 'isoformat') else item
            with session_scope(session=recorder.get_session(), read_only=True) as session:
                meta = session.execute(select(StatisticsMeta).where(
                    StatisticsMeta.statistic_id == entity_id)).scalar_one_or_none()
                if meta is None:
                    return {'metadata': None, 'rows': []}
                rows = session.execute(select(StatisticsShortTerm).where(
                    StatisticsShortTerm.metadata_id == meta.id).order_by(StatisticsShortTerm.id)).scalars()
                return {'metadata': {col.name: value(getattr(meta, col.name))
                                     for col in StatisticsMeta.__table__.columns},
                        'rows': [{col.name: value(getattr(row, col.name))
                                  for col in StatisticsShortTerm.__table__.columns} for row in rows]}
        # Same real scheduled-cycle condition as native_statistics_worker: no clock or SQL writes.
        async with asyncio.timeout(420):
            while True:
                initial_stats = await recorder.async_add_executor_job(full_statistics, old)
                if initial_stats['metadata'] is not None and initial_stats['rows']:
                    break
                await asyncio.sleep(2)
        from homeassistant.components.energy.data import async_get_manager
        energy_module = self.module('energy_native')
        energy = energy_module.NativeEnergy(hass, await async_get_manager(hass))
        energy_before, energy_plan = await energy.plan({old: new})
        self.assertTrue(energy_plan.changes)
        energy_change = energy.prepare(energy_before, energy_plan)
        recorder_guard = self.module('recorder_guard').NativeRecorderGuard(hass)
        history = self.module('history_guard').NativeHistoryGuard(recorder_guard)
        history_before = await history.capture({old: new})
        recorder_before = await recorder_guard.preflight({old: new})
        async def readback(label):
            def capture():
                result = {}
                for key in ('auth', 'core.entity_registry', 'core.device_registry', 'core.config_entries',
                            'core.restore_state', 'powercalc_group', 'energy'):
                    path = self.f.root / '.storage' / key
                    result[key] = base64.b64encode(path.read_bytes()).decode() if path.is_file() else None
                return result
            raw = await hass.async_add_executor_job(capture)
            raw['registry'] = {'old': registry.async_get(old) is not None,
                               'new': registry.async_get(new) is not None}
            raw['pss_keys'] = sorted(pss.states)
            raw['restore_keys'] = sorted(name for name in (old, new) if name in restore.last_states)
            raw['statistics_old'] = await recorder.async_add_executor_job(full_statistics, old)
            raw['statistics_new'] = await recorder.async_add_executor_job(full_statistics, new)
            raw['energy'] = (await energy.read()).value
            payload = json.dumps(raw, sort_keys=True)
            await hass.async_add_executor_job((Path('/qa') / ('handoff-' + label + '.json')).write_text, payload)
            print('HANDOFF_READBACK', label, flush=True)
            return raw
        async def check_statistics(entity_id):
            current = await recorder.async_add_executor_job(full_statistics, entity_id)
            self.assertIsNotNone(current['metadata'])
            original_meta = dict(initial_stats['metadata'])
            current_meta = dict(current['metadata'])
            self.assertEqual(original_meta.pop('statistic_id'), old)
            self.assertEqual(current_meta.pop('statistic_id'), entity_id)
            self.assertEqual(current_meta, original_meta)
            rows = {row['id']: row for row in current['rows']}
            self.assertTrue(initial_stats['rows'])
            self.assertTrue(all(rows.get(row['id']) == row for row in initial_stats['rows']))
        await pss.persist_states()
        await restore.async_dump_states()
        baseline_raw = await readback('before')
        try:
            await energy.write(energy_change, self.guard)
            self.assertTrue(await hass.config_entries.async_unload(self.group.entry_id))
            await hass.async_block_till_done()
            actual_total = group._native_value_exact
            self.assertNotEqual(actual_total, round(actual_total, 2))
            self.assertNotIn(new, pss.states)
            self.assertIn(old, pss.states)
            self.assertIn(old, restore.last_states)
            self.assertNotIn(new, restore.last_states)
            original_pss = {key: {member: state.as_dict() for member, state in rows.items()}
                            for key, rows in pss.states.items()}
            original_stored = restore.last_states[old]
            restore_codec = self.module('native_restore_state_payload')
            original_restore = restore_codec._canonical(restore_codec._capture(original_stored, restore_codec._native()))
            helper = self.module('powercalc_group_restore')
            payload = helper.prepare(original_stored, new, full_total=str(actual_total),
                                     precision=group._rounding_digits,
                                     unit=str(group.native_unit_of_measurement))
            clone = self.module('powercalc_pss_handoff')._clone
            pss.states[new] = {key: clone(state, key) for key, state in pss.states[old].items()}
            await pss.persist_states()
            restore.last_states[new] = helper.materialize(payload)
            await restore.async_dump_states()
            pss_raw = await hass.async_add_executor_job(Path(pss.store.path).read_bytes)
            print('HANDOFF_RAW_PSS', pss_raw.decode())
            self.assertEqual(json.loads(pss_raw)['data'][old], original_pss[old])
            renamed = registry.async_update_entity(old, new_entity_id=new)
            self.assertEqual(renamed.id, uid)
            entry_data = dict(self.group.data)
            self.assertEqual(entry_data['_energy_entity'], old)
            entry_data['_energy_entity'] = new
            hass.config_entries.async_update_entry(self.group, data=entry_data)
            await hass.async_block_till_done()
            self.assertTrue(await hass.config_entries.async_setup(self.group.entry_id))
            await hass.async_block_till_done()
            rows = [entity for platform in async_get_platforms(hass, 'powercalc')
                    for entity in platform.entities.values()
                    if type(entity) is GroupedEnergySensor and entity.entity_id == new]
            self.assertEqual(len(rows), 1)
            active = rows[0]
            self.assertEqual(active.registry_entry.id, uid)
            self.assertEqual(active._native_value_exact, actual_total)
            self.assertEqual({key: state.as_dict() for key, state in pss.states[old].items()}, original_pss[old])
            self.assertTrue(await history.verify(history_before))
            await check_statistics(new)
            recorded = await recorder_guard.snapshot({old: new})
            self.assertTrue(self.module('recorder_guard').verify({old: new}, recorder_before, recorded))
            self.assertEqual((await energy.read()).value, energy_change.after)
            after_raw = await readback('after')
            self.assertEqual(after_raw['auth'], baseline_raw['auth'])
            self.assertEqual(after_raw['core.device_registry'], baseline_raw['core.device_registry'])
            await self.sources[0].async_turn_on(brightness=255)
            async with asyncio.timeout(12):
                while active._native_value_exact <= actual_total:
                    await asyncio.sleep(0.05)
            self.assertEqual({key: state.as_dict() for key, state in pss.states[old].items()}, original_pss[old])
            print('NATIVE_HANDOFF_FUTURE', {'original': str(actual_total),
                  'later': str(active._native_value_exact), 'uuid_stable': True})
            # Fresh independent readback must establish the whole forward state
            # before the single inverse; any exception skips inverse entirely.
            self.assertEqual(registry.async_get(new).id, uid)
            self.assertIsNone(registry.async_get(old))
            self.assertTrue(await history.verify(history_before))
            self.assertTrue(self.module('recorder_guard').verify(
                {old: new}, recorder_before, await recorder_guard.snapshot({old: new})))
            self.assertEqual((await energy.read()).value, energy_change.after)
            self.assertEqual(restore_codec._canonical(restore_codec._capture(
                restore.last_states[old], restore_codec._native())), original_restore)
            self.assertEqual({key: {member: state.as_dict() for member, state in rows.items()}
                              for key, rows in pss.states.items() if key not in (old, new)},
                             {key: rows for key, rows in original_pss.items() if key != old})
            self.assertTrue(await hass.config_entries.async_unload(self.group.entry_id))
            await hass.async_block_till_done()
            inverse_total = active._native_value_exact
            self.assertEqual({key: state.as_dict() for key, state in pss.states[old].items()}, original_pss[old])
            inverse_source = restore.last_states[new]
            inverse_payload = helper.prepare(inverse_source, old, full_total=str(inverse_total),
                                             precision=active._rounding_digits,
                                             unit=str(active.native_unit_of_measurement))
            pss.states[old] = {key: clone(state, key) for key, state in pss.states[new].items()}
            await pss.persist_states()
            restore.last_states[old] = helper.materialize(inverse_payload)
            await restore.async_dump_states()
            self.assertEqual(registry.async_update_entity(new, new_entity_id=old).id, uid)
            entry_data = dict(self.group.data)
            self.assertEqual(entry_data['_energy_entity'], new)
            entry_data['_energy_entity'] = old
            hass.config_entries.async_update_entry(self.group, data=entry_data)
            await hass.async_block_till_done()
            self.assertTrue(await hass.config_entries.async_setup(self.group.entry_id))
            await hass.async_block_till_done()
            current_energy = await energy.read()
            self.assertEqual(current_energy.value, energy_change.after)
            await energy.write(energy_module.PreparedEnergy(current_energy, energy_before.value,
                                                             energy_before.revision), self.guard)
            restored = [entity for platform in async_get_platforms(hass, 'powercalc')
                        for entity in platform.entities.values()
                        if type(entity) is GroupedEnergySensor and entity.entity_id == old]
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0].registry_entry.id, uid)
            self.assertEqual(restored[0]._native_value_exact, inverse_total)
            self.assertTrue(await history.verify(history_before))
            await check_statistics(old)
            self.assertTrue(self.module('recorder_guard').verify_rollback(
                {old: new}, recorder_before, await recorder_guard.snapshot({old: new})))
            self.assertEqual((await energy.read()).value, energy_before.value)
            restored_raw = await readback('restored')
            self.assertEqual(restored_raw['auth'], baseline_raw['auth'])
            self.assertEqual(restored_raw['core.device_registry'], baseline_raw['core.device_registry'])
            await self.sources[0].async_turn_off()
            await self.sources[0].async_turn_on(brightness=128)
            async with asyncio.timeout(12):
                while restored[0]._native_value_exact <= inverse_total:
                    await asyncio.sleep(0.05)
            print('NETWORK_AFTER_HANDOFF', native_dummy_mdns(hass))
        finally:
            primary = sys.exception()
            try:
                await readback('final')
            except Exception as error:
                print('HANDOFF_FINAL_READBACK_ERROR', type(error).__name__, str(error), flush=True)
                if primary is None:
                    raise
                primary.add_note('final readback failed: ' + type(error).__name__)
            print('HANDOFF_FINAL_READONLY', {'old_registry': registry.async_get(old) is not None,
                  'new_registry': registry.async_get(new) is not None,
                  'old_pss': old in pss.states, 'new_pss': new in pss.states,
                  'old_restore': old in restore.last_states, 'new_restore': new in restore.last_states})
