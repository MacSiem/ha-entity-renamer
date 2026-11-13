"""Two separately reviewed Powercalc parts and their fresh inverse plans."""
import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

from test_controlled_powercalc_actual_native import ControlledActualGraphTests


class ControlledPowercalcPartsTests(ControlledActualGraphTests):
    async def test_separate_parts_a_then_b_and_fresh_reverse(self):
        from ordinary_history_diagnostic import history_from_frame
        monitor = sys.monitoring
        slot = next(i for i in range(6) if monitor.get_tool(i) is None)
        monitor.use_tool_id(slot, 'parts history mismatch')
        history_diffs = []
        exceptions = []
        from ordinary_energy_diagnostic import energy_exception
        def trace(event, code, offset, error):
            captured = energy_exception(code, error, component=True)
            if captured is not None:
                captured["event"] = event
                if captured not in exceptions:
                    exceptions.append(captured)
            if code.co_name == '_need' and str(error) == 'template_yaml_server_history_identity_changed':
                history_diffs.append(history_from_frame(sys._getframe(1)))
        monitor.register_callback(slot, monitor.events.RAISE,
                                  lambda code, offset, error: trace("raise", code, offset, error))
        monitor.register_callback(slot, monitor.events.EXCEPTION_HANDLED,
                                  lambda code, offset, error: trace("handled", code, offset, error))
        monitor.register_callback(slot, monitor.events.PY_UNWIND,
                                  lambda code, offset, error: trace("unwind", code, offset, error))
        monitor.set_events(slot, monitor.events.RAISE | monitor.events.EXCEPTION_HANDLED | monitor.events.PY_UNWIND)
        try:
            await self._parts()
        finally:
            monitor.set_events(slot, 0)
            monitor.register_callback(slot, monitor.events.RAISE, None)
            monitor.register_callback(slot, monitor.events.EXCEPTION_HANDLED, None)
            monitor.register_callback(slot, monitor.events.PY_UNWIND, None)
            monitor.free_tool_id(slot)
            destination = os.environ.get('RENAMER_BATCH_EVIDENCE')
            storage = self.f.root / '.storage'
            if destination:
                target = Path(destination) / 'parts-storage'
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                exception_file = target / 'exceptions.json'
                exception_file.write_text(json.dumps(exceptions, default=str))
                exception_file.chmod(0o600)
                diagnostic = target / 'history-diffs.json'
                diagnostic.write_text(json.dumps(history_diffs, default=str))
                diagnostic.chmod(0o600)
                for item in storage.iterdir() if storage.is_dir() else ():
                    if item.is_file() and 'ha_entity_renamer' in item.name:
                        copied = target / item.name
                        shutil.copyfile(item, copied)
                        copied.chmod(0o600)

    async def _parts(self):
        from homeassistant.components.recorder import get_instance
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from custom_components.ha_entity_renamer import controlled_powercalc_handoff as handoff
        from ordinary_network_scope import native_dummy_mdns

        hass, runtime = self.f.hass, self._runtime()
        native_dummy_mdns(hass)
        names = {uid: entity.entity_id for uid, entity in self.entities.items()}
        uuids = {uid: entity.registry_entry.id for uid, entity in self.entities.items()}
        entries = {entry.entry_id: dict(entry.data) for entry in self.entries}
        source = {i: self.sources[i].entity_id for i in (10, 11, 12)}
        parts = []
        for i, suffix in ((10, 'a'), (11, 'b')):
            parts.append({source[i]: 'light.p4_part_' + suffix,
                names[f'fixture-meter-{i}']: 'sensor.p4_part_' + suffix,
                names[f'fixture-meter-{i}_energy']: 'sensor.p4_part_' + suffix + '_energy'})
        original_package = self.package.read_bytes()
        foreign_id = self.entries[2].entry_id
        self.assertEqual(entries[foreign_id]['entity_id'], source[12])
        pss, restore = hass.data['powercalc_group_storage'], hass.data[DATA_RESTORE_STATE]
        removed, restored = [], []
        snapshot, watch = handoff.snapshot_removed, handoff._watch_restored_counters

        def snapshot_removed(*args, **kwargs):
            result = snapshot(*args, **kwargs)
            removed.append({uuid: (kind, payload) for uuid, _, _, kind, payload in result})
            return result

        def watch_restored(*args, **kwargs):
            result, stop = watch(*args, **kwargs)
            restored.append(result)
            return result, stop

        self.enterContext(patch.object(handoff, 'snapshot_removed', snapshot_removed))
        self.enterContext(patch.object(handoff, '_watch_restored_counters', watch_restored))

        def inventory():
            return {entity.unique_id: entity for platform in async_get_platforms(hass, 'powercalc')
                    for entity in platform.entities.values()}

        def verify(moves):
            current = inventory()
            self.assertEqual(set(current), set(names))
            for uid, entity in current.items():
                self.assertEqual((entity.entity_id, entity.registry_entry.id),
                                 (moves.get(names[uid], names[uid]), uuids[uid]), uid)
            for uid, members in self.members.items():
                for suffix in ('', '_energy'):
                    self.assertEqual(current[uid + suffix]._entities, {
                        current[f'fixture-meter-{i}' + suffix].entity_id for i in members})
            for index, entry in enumerate(self.entries):
                expected = dict(entries[entry.entry_id])
                if index in (0, 1):
                    for key in ('entity_id', '_power_entity', '_energy_entity'):
                        expected[key] = moves.get(expected[key], expected[key])
                self.assertEqual(dict(entry.data), expected)
            self.assertEqual(dict(self.entries[2].data), entries[foreign_id])

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        async def settle():
            await pss.persist_states()
            await restore.async_dump_states()
            await self._flush_documents()
            await get_instance(hass).async_block_till_done()

        async def transition(mapping):
            await settle()
            before = len(removed)
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='p4-parts',
                                                       guard=lambda: None, refresh=refresh)
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                                                      actor_id='p4-parts', guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied', result)
            inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                                                        guard=lambda: None, refresh=refresh)
            self.assertEqual(inspected['state'], 'applied', inspected)
            self.assertEqual((len(removed), len(restored)), (before + 1, before + 1))
            seen = {row['uuid']: row for row in restored[-1].values()}
            self.assertEqual(set(seen), set(removed[-1]))
            for uid, (kind, payload) in removed[-1].items():
                capture = {} if kind == 'group' else json.loads(payload.capture_json)
                expected = payload.full_total if kind == 'group' else capture['full_state_text']
                self.assertEqual(Decimal(seen[uid]['value']).as_tuple(), Decimal(expected).as_tuple())
                if kind != 'group':
                    self.assertEqual(Decimal(seen[uid]['last_valid']).as_tuple(),
                                     Decimal(capture['last_valid_state_text']).as_tuple())
            return review

        for light in self.sources:
            await light.async_turn_on(brightness=255)
        await hass.async_block_till_done()
        await asyncio.sleep(2)
        for light in self.sources:
            await light.async_turn_off()
        await hass.async_block_till_done()
        # Seal the native series only after real pulses and a completed period;
        # a later first statistics compilation would change the strict history
        # identity after review, legitimately making recovery unprovable.
        from datetime import timedelta
        from homeassistant.components.recorder.tasks import StatisticsTask
        from homeassistant.util import dt as dt_util
        from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
        history = NativeRecorderGuard(hass)
        await history.async_synchronize()
        now = dt_util.utcnow()
        period = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0) + timedelta(minutes=5)
        await asyncio.sleep(max(0, (period - dt_util.utcnow()).total_seconds()))
        get_instance(hass).queue_task(StatisticsTask(period, False))
        await history.async_synchronize()
        series = {names[f'fixture-meter-{i}{suffix}']
                  for i in (10, 11) for suffix in ('', '_energy')}
        metadata = await history.snapshot({entity_id: entity_id for entity_id in series})
        self.assertTrue(series <= set(metadata['statistics']), metadata['statistics'])
        verify({})
        a, b = parts
        await transition(a)
        verify(a)  # A changed; B and C retain their UUIDs, IDs and entry data.
        await transition(b)
        verify({**a, **b})  # Fresh B review observes already transformed A.
        await transition({new: old for old, new in b.items()})
        verify(a)
        await transition({new: old for old, new in a.items()})
        verify({})
        self.assertEqual(self.package.read_bytes(), original_package)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([ControlledPowercalcPartsTests('test_separate_parts_a_then_b_and_fresh_reverse')])
