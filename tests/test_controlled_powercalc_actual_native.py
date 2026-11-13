"""Core 11.1 explicit-pair Powercalc graph through the installed migration path."""
import asyncio
from decimal import Decimal
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_powercalc_actual_profiles_native as actual
import test_powercalc_public_cold_native as cold


@unittest.skipUnless(actual.fixtures.HAS_HA, 'requires pinned native HA')
class ControlledActualGraphTests(cold.PowercalcColdPhase):
    namespace_source_bytes = b'# Synthetic Powercalc namespace\n'
    _setup_vendor_forward = actual.ActualProfileGraphNativeTests._setup_real_vendor
    remote_bytes = actual.ActualProfileGraphNativeTests.remote_bytes
    write_package = actual.ActualProfileGraphNativeTests.write_package

    from test_core11_energy_mixed_cold_native import MixedPhase
    _startup_services = MixedPhase._startup_services

    async def _before_loader(self, hass, root):
        await super()._before_loader(hass, root)
        target = self.installed_component / 'www/ha-entity-renamer.js'
        target.parent.mkdir()
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'ha-entity-renamer.js', target)

    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix='controlled-pc-leaf-',
                                                dir=os.environ.get('RENAMER_TEST_SCRATCH', tempfile.gettempdir()))
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.dict(os.environ, {'RENAMER_POWER_COLD_PHASE': 'forward',
            'RENAMER_POWER_COLD_MODE': 'migration', 'RENAMER_POWER_COLD_CONFIG': directory.name,
            'RENAMER_POWER_COLD_META': directory.name + '/unused'}))
        await super().asyncSetUp()

    async def test_explicit_leaf_forward_fresh_inverse(self):
        from homeassistant.components.recorder import get_instance
        from homeassistant.core import State, callback
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from custom_components.ha_entity_renamer.transaction import Refused
        from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
        from custom_components.ha_entity_renamer import controlled_powercalc_handoff as handoff
        from ordinary_network_scope import native_dummy_mdns

        hass, runtime = self.f.hass, self._runtime()
        original_package = self.package.read_bytes()
        pss, restore = hass.data['powercalc_group_storage'], hass.data[DATA_RESTORE_STATE]
        entry = self.entries[0]
        before_entry = dict(entry.data)
        foreign_entries = {e.entry_id: dict(e.data) for e in self.entries[1:]}
        names = {uid: entity.entity_id for uid, entity in self.entities.items()}
        uuids = {uid: entity.registry_entry.id for uid, entity in self.entities.items()}
        source = self.sources[10].entity_id
        mapping = {source: 'light.renamed_fixture_source'}
        for i in (0, 10):
            for suffix in ('', '_energy'):
                uid = f'fixture-meter-{i}{suffix}'
                mapping[names[uid]] = f'sensor.renamed_fixture_{i}{suffix}'
        expected_entry = {**before_entry, 'entity_id': mapping[source],
                          '_power_entity': mapping[before_entry['_power_entity']],
                          '_energy_entity': mapping[before_entry['_energy_entity']]}
        out = Path('/qa/controlled-pc-leaf.json')
        evidence = {}
        def save():
            out.write_text(json.dumps(evidence, sort_keys=True, default=str))

        def inventory():
            return {e.unique_id: e for p in async_get_platforms(hass, 'powercalc')
                    for e in p.entities.values()}

        async def pulse():
            for light in self.sources:
                await light.async_turn_on(brightness=255)
            await hass.async_block_till_done()
            await asyncio.sleep(2)
            for light in self.sources:
                await light.async_turn_off()
            await hass.async_block_till_done()

        def counter_values(values):
            return {uid: (str(e._native_value_exact), None) if uid.startswith('fixture-group-')
                         else (str(e._state), str(e._last_valid_state))
                    for uid, e in values.items() if uid.endswith('_energy')}

        def verify_ids(moves):
            current = inventory()
            self.assertEqual(set(current), set(names))
            for uid, entity in current.items():
                self.assertEqual((entity.entity_id, entity.registry_entry.id),
                                 (moves.get(names[uid], names[uid]), uuids[uid]), uid)
            for uid, members in self.members.items():
                for suffix in ('', '_energy'):
                    self.assertEqual(current[uid + suffix]._entities, {
                        current[f'fixture-meter-{i}' + suffix].entity_id for i in members})
            return current

        removed, first_numeric, prior_pss, staged_pss = [], [], [], []
        observe_snapshot = handoff.snapshot_removed
        observe_watch = handoff._watch_restored_counters
        def capture_removed(*args, **kwargs):
            values = observe_snapshot(*args, **kwargs)
            counters = {c.entity.unique_id: c.entity for c in args[1].counters}
            removed.append(counter_values(counters))
            prior_pss.append({c.entity.unique_id: {
                c.entity_id: {name: state.as_dict() for name, state in pss.states[c.entity_id].items()}}
                for c in args[1].counters if c.kind == 'group'})
            return values

        def capture_watch(hass_arg, population):
            values, stop = observe_watch(hass_arg, population)
            by_name = {row['after_id']: next(uid for uid, value in uuids.items()
                                           if value == row['uuid']) for row in population}
            events = {}
            @callback
            def changed(event):
                state = event.data.get('new_state')
                if state is not None and state.entity_id in by_name:
                    try:
                        number = Decimal(state.state)
                    except ArithmeticError:
                        return
                    if number.is_finite():
                        events.setdefault(by_name[state.entity_id], state.state)
            remove_listener = hass_arg.bus.async_listen('state_changed', changed)
            first_numeric.append((values, events))
            staged_pss.append({by_name[row['after_id']]: {
                row['after_id']: {name: state.as_dict() for name, state in pss.states[row['after_id']].items()}}
                for row in population if row['kind'] == 'group'})
            def cleanup():
                remove_listener()
                stop()
            return values, cleanup

        self.enterContext(patch.object(handoff, 'snapshot_removed', capture_removed))
        self.enterContext(patch.object(handoff, '_watch_restored_counters', capture_watch))
        def verify_seam(index, moves):
            values, events = first_numeric[index]
            body = {uid: (row['value'], row['last_valid']) for uid, value in uuids.items()
                    for row in values.values() if row['uuid'] == value}
            evidence[f'seam_{index}'] = {'removed': removed[index], 'exact': body, 'numeric': events,
                                         'pss_removed': prior_pss[index], 'pss_staged': staged_pss[index]}
            save()
            self.assertEqual(removed[index], body)
            self.assertEqual(set(events), set(body))
            for uid, value in events.items():
                self.assertEqual(Decimal(value), round(Decimal(body[uid][0]), 6), uid)
            expected = {uid: {group: {moves.get(name, name): {**row, 'entity_id': moves.get(name, name)}
                                      for name, row in members.items()}
                              for group, members in groups.items()}
                        for uid, groups in prior_pss[index].items()}
            self.assertEqual(expected, staged_pss[index])

        async def refresh():
            return None
        guard = lambda: True
        actor = 'synthetic-leaf-actor'
        async def baseline():
            await pss.persist_states()
            await restore.async_dump_states()
            await self._flush_documents()
            await get_instance(hass).async_block_till_done()

        foreign = State('sensor.foreign_fixture_member', '3.75')
        pss.set_entity_state('sensor.foreign_fixture_group', foreign.entity_id, foreign)
        def verify_foreign():
            data = json.loads((self.f.root / '.storage/powercalc_group').read_bytes())['data']
            self.assertEqual(data['sensor.foreign_fixture_group'][foreign.entity_id]['state'], '3.75')
            self.assertEqual(self.remote_bytes(), self.remote_original)
            self.assertEqual({e.entry_id: dict(e.data) for e in self.entries[1:]}, foreign_entries)

        try:
            native_dummy_mdns(hass)
            await pulse()
            await baseline()
            foreign_power = inventory()['fixture-meter-0']
            standby = foreign_power._standby_power
            foreign_power._standby_power = Decimal('0.4')
            try:
                with self.assertRaises((ValueError, Refused)):
                    await runtime.native_yaml_prepare(hass, mapping, actor_id=actor, guard=guard, refresh=refresh)
            finally:
                foreign_power._standby_power = standby
            self.assertEqual(self.package.read_bytes(), original_package)
            self.assertEqual(dict(entry.data), before_entry)
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id=actor, guard=guard, refresh=refresh)
            evidence['review'] = review
            save()
            op, fingerprint = review['operation_id'], review['fingerprint']
            async with runtime.operation_lock:
                bound = await NativeTemplateStateServer.bind_prepared(hass, op, runtime.reviews[op].plan,
                                                                       guard=guard, refresh=refresh)
                try:
                    self.assertEqual(dict(bound.composition.mapping), mapping)
                    self.assertEqual(len(bound.composition.previous.groups), 2)
                    self.assertTrue(all(g.entity_id not in mapping for g in bound.composition.previous.groups))
                finally:
                    bound.close()
            result = await runtime.native_yaml_apply(hass, op, fingerprint, actor_id=actor, guard=guard, refresh=refresh)
            evidence['apply'] = result
            save()
            self.assertEqual(result['state'], 'applied')
            verify_seam(0, mapping)
            verify_ids(mapping)
            self.assertEqual(dict(entry.data), expected_entry)
            for old, new in mapping.items():
                if old != source:
                    self.assertNotIn(old, self.package.read_text())
                    self.assertIn(new, self.package.read_text())
            verify_foreign()
            initial = counter_values(inventory())
            await pulse()
            self.assertTrue(all(Decimal(counter_values(inventory())[uid][0]) > Decimal(initial[uid][0])
                                for uid in ('fixture-group-a_energy', 'fixture-group-b_energy')))
            await baseline()
            inspected = await runtime.recovery_inspect(hass, op, fingerprint, guard=guard, refresh=refresh)
            evidence['inspect'] = inspected
            save()
            self.assertTrue(inspected['eligible'], inspected)
            result = await runtime.recovery_restore(hass, op, fingerprint, guard=guard, refresh=refresh)
            evidence['restore'] = result
            save()
            self.assertEqual(result['state'], 'rolled_back')
            verify_seam(1, {new: old for old, new in mapping.items()})
            verify_ids({})
            self.assertEqual(dict(entry.data), before_entry)
            self.assertEqual(self.package.read_bytes(), original_package)
            verify_foreign()
            initial = counter_values(inventory())
            await pulse()
            self.assertTrue(all(Decimal(counter_values(inventory())[uid][0]) > Decimal(initial[uid][0])
                                for uid in ('fixture-group-a_energy', 'fixture-group-b_energy')))
            native_dummy_mdns(hass)
        finally:
            save()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite((ControlledActualGraphTests('test_explicit_leaf_forward_fresh_inverse'),))
