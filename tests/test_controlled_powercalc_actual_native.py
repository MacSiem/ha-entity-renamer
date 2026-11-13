"""Core 11.1 explicit-pair Powercalc graph through the installed migration path."""
import asyncio
from decimal import Decimal
import json
import os
import shutil
import sys
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

    async def test_live_yaml_consumers_forward_fresh_inverse(self):
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import StatisticsTask
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.core import callback
        from homeassistant.setup import async_setup_component
        from homeassistant.util import dt as dt_util
        from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
        from custom_components.ha_entity_renamer.transaction import Refused
        from ordinary_network_scope import native_dummy_mdns
        import yaml

        hass, runtime, root = self.f.hass, self._runtime(), self.f.root
        source = self.sources[10]
        old_source = source.entity_id
        leaf = self.entities['fixture-meter-10'].entity_id
        mapping = {old_source: 'light.controlled_yaml_source', leaf: 'sensor.controlled_yaml_power'}
        sensor = 'sensor.controlled_yaml_readout'
        script = 'controlled_yaml_read'
        files = {domain: root / f'{domain}.yaml' for domain in ('automation', 'script', 'template')}
        conf = root / 'configuration.yaml'
        conf.write_text(conf.read_text() + ''.join(f'{domain}: !include {path.name}\n'
                                                  for domain, path in files.items()))
        read = "{{ states('" + leaf + "') | float(-1) }}"
        def action(event):
            return {'event': event, 'event_data': {'watts': read}}

        state_trigger = {'trigger': 'state', 'entity_id': leaf, 'to': ['10', '10.0']}
        def automation(key, actions, trigger=state_trigger):
            return {'id': key, 'alias': key, 'initial_state': True,
                    'triggers': [trigger], 'actions': actions}

        programs = {
            'template': [{'sensor': [{'name': 'Controlled YAML readout',
                'unique_id': 'controlled_yaml_readout',
                'state': "{{ states('" + leaf + "') | float(-1) * 2 }}"}]}],
            'automation': [automation('controlled_yaml_on', [
                {'delay': '{{ 120 if long | default(false) else 0 }}'},
                {'action': 'script.' + script}, action('controlled_yaml_on_result')]),
                automation('controlled_yaml_off', [action('controlled_yaml_off_result')]),
                automation('controlled_yaml_foreign', [{'event': 'controlled_yaml_foreign_result'}],
                           {'trigger': 'event', 'event_type': 'controlled_yaml_foreign_probe'})],
            'script': {script: {'sequence': [
                {'delay': '{{ 120 if long | default(false) else 0 }}'},
                action('controlled_yaml_script_result')]},
                'controlled_yaml_foreign': {'sequence': [{'event': 'controlled_yaml_foreign_script'}]}},
        }
        for domain, path in files.items():
            path.write_text(yaml.safe_dump(programs[domain], sort_keys=False))
        config = await async_hass_config_yaml(hass)
        for domain in ('template', 'script', 'automation'):
            self.assertTrue(await async_setup_component(hass, domain, config), domain)
        await hass.async_block_till_done()
        report = {}
        def save():
            Path('/qa/controlled-yaml-consumers.json').write_text(json.dumps(report, sort_keys=True))
        self.addCleanup(save)
        from custom_components.ha_entity_renamer import transaction
        monitor = sys.monitoring
        slot = next(i for i in range(6) if monitor.get_tool(i) is None)
        monitor.use_tool_id(slot, 'controlled YAML consumers diagnosis')
        def trace(code, offset, error):
            path = Path(code.co_filename)
            if 'ha_entity_renamer' in path.parts and isinstance(error, (transaction.Refused, ValueError,
                    AttributeError, KeyError)) and len(str(error)) < 180:
                row = (path.name, code.co_name, str(error))
                reasons = report.setdefault('reasons', [])
                if row not in reasons:
                    reasons.append(row)
        monitor.register_callback(slot, monitor.events.RAISE, trace)
        monitor.set_events(slot, monitor.events.RAISE)
        def stop():
            monitor.set_events(slot, 0)
            monitor.register_callback(slot, monitor.events.RAISE, None)
            monitor.free_tool_id(slot)
        self.addCleanup(stop)

        seen = {key: [] for key in ('on', 'off', 'script')}
        for key, events in seen.items():
            @callback
            def collect(event, values=events):
                values.append(float(event.data['watts']))
            self.addCleanup(hass.bus.async_listen(f'controlled_yaml_{key}_result', collect))

        def entity(domain, key):
            return next(e for e in hass.data[domain].entities if e.unique_id == key)

        foreign = {domain: entity(domain, 'controlled_yaml_foreign') for domain in ('automation', 'script')}
        await entity('automation', 'controlled_yaml_off').async_turn_off()

        async def flush():
            await hass.data['powercalc_group_storage'].persist_states()
            await self._flush_documents()
            await get_instance(hass).async_block_till_done()

        async def wait_for(predicate, detail=lambda: None):
            for _ in range(500):
                if predicate():
                    return
                await asyncio.sleep(.01)
            self.fail(f'native consumer stimulus did not settle: {detail()}')

        async def signal(label, mapping):
            active_id, power_id = (mapping.get(key, key) for key in (old_source, leaf))
            for events in seen.values():
                events.clear()
            def current():
                return tuple(None if (state := hass.states.get(key)) is None else state.state
                             for key in (active_id, power_id, sensor))

            await source.async_turn_on(brightness=255)
            await wait_for(lambda: current()[1] is not None and float(current()[1]) == 10, current)
            await source.async_turn_off()
            await wait_for(lambda: current()[0] == 'off' and current()[2] is not None
                           and float(current()[2]) != 20, current)
            for events in seen.values():
                events.clear()
            await source.async_turn_on(brightness=255)
            await wait_for(lambda: len(seen['on']) == 1 and len(seen['script']) == 1
                           and current()[2] is not None and float(current()[2]) == 20,
                           lambda: (current(), seen))
            watts = float(hass.states.get(power_id).state)
            self.assertEqual(watts, 10)
            self.assertEqual(float(hass.states.get(sensor).state), watts * 2)
            self.assertEqual(seen, {'on': [watts], 'off': [], 'script': [watts]})
            await hass.services.async_call('script', script, {}, blocking=True)
            self.assertEqual(seen['script'], [watts, watts])
            self.assertTrue(hass.states.is_state(active_id, 'on'))
            self.assertTrue(entity('automation', 'controlled_yaml_on').is_on)
            self.assertFalse(entity('automation', 'controlled_yaml_off').is_on)
            for domain, obj in foreign.items():
                self.assertIs(entity(domain, obj.unique_id), obj)
            report[label] = {'ids': [active_id, power_id], 'template': hass.states.get(sensor).state,
                             'events': {key: list(items) for key, items in seen.items()}}
            save()

        native_dummy_mdns(hass)
        await signal('baseline', {})
        await flush()
        history = NativeRecorderGuard(hass)
        await history.async_synchronize()
        now = dt_util.utcnow()
        get_instance(hass).queue_task(StatisticsTask(now.replace(minute=now.minute // 5 * 5,
                                                                second=0, microsecond=0), False))
        await history.async_synchronize()
        initial = {path: path.read_bytes() for path in (*files.values(), conf, self.package)}

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        async def prepare():
            return await runtime.native_yaml_prepare(hass, mapping, actor_id='yaml-consumer-fixture',
                                                     guard=lambda: True, refresh=refresh)

        path = files['script']
        programs['script'][script]['sequence'][-1]['event_data']['watts'] = "{{ states(target_id) }}"
        path.write_text(yaml.safe_dump(programs['script'], sort_keys=False))
        # Reload real config; a static-disk/live mismatch alone must not
        # be the reason the dynamic reference is refused.
        await hass.services.async_call('script', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        dynamic = path.read_bytes()
        with self.assertRaises((ValueError, Refused)) as refusal:
            await prepare()
        self.assertEqual(path.read_bytes(), dynamic)
        self.assertEqual({p: p.read_bytes() for p in initial if p is not path},
                         {p: raw for p, raw in initial.items() if p is not path})
        self.assertIs(source.registry_entry, self.f.entity.async_get(old_source))
        report['dynamic_refused'] = str(refusal.exception)
        path.write_bytes(initial[path])
        await hass.services.async_call('script', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        await flush()

        automation_obj = entity('automation', 'controlled_yaml_on')
        script_obj = entity('script', script)
        await hass.services.async_call('automation', 'trigger',
            {'entity_id': automation_obj.entity_id, 'variables': {'long': True}}, blocking=False)
        await hass.services.async_call('script', script, {'long': True}, blocking=False)
        await wait_for(lambda: automation_obj.action_script.is_running and script_obj.script.is_running)
        report['active_before'] = True
        review = await prepare()
        op, fingerprint = review['operation_id'], review['fingerprint']
        report['review'] = review
        save()
        result = await runtime.native_yaml_apply(hass, op, fingerprint, actor_id='yaml-consumer-fixture',
                                                 guard=lambda: True, refresh=refresh)
        report['apply'] = result
        save()
        self.assertEqual(result['state'], 'applied')
        self.assertFalse(automation_obj.action_script.is_running)
        self.assertFalse(script_obj.script.is_running)
        report['active_stopped'] = True
        await signal('forward', mapping)
        await flush()
        inspected = await runtime.recovery_inspect(hass, op, fingerprint, guard=lambda: True, refresh=refresh)
        report['inspect'] = inspected
        save()
        self.assertTrue(inspected['eligible'], inspected)
        result = await runtime.recovery_restore(hass, op, fingerprint, guard=lambda: True, refresh=refresh)
        report['restore'] = result
        save()
        self.assertEqual(result['state'], 'rolled_back')
        await signal('inverse', {})
        self.assertEqual({p: p.read_bytes() for p in initial}, initial)
        native_dummy_mdns(hass)

    async def test_explicit_leaf_forward_fresh_inverse(self):
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import StatisticsTask
        from homeassistant.util import dt as dt_util
        from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
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

        monitor = sys.monitoring
        slot = next(i for i in range(6) if monitor.get_tool(i) is None)
        monitor.use_tool_id(slot, 'controlled Powercalc leaf diagnosis')
        def trace(code, offset, error):
            path = Path(code.co_filename)
            value = str(error)
            if 'ha_entity_renamer' in path.parts and isinstance(error, (Refused, ValueError)) and len(value) < 160:
                if code.co_name == '_need' and value == 'controlled_powercalc_handoff_setup_graph_unverified':
                    caller = sys._getframe(1).f_back
                    rows = caller.f_locals.get('first', ()) if caller else ()
                    evidence['failed_graph'] = [(r.entity_id, r.relation_kind, r.reason)
                                                for r in rows if not r.source_verified]
                    capture = caller.f_locals.get('captured') if caller else None
                    if capture:
                        evidence['setup_objects'] = [
                            {k: row[k] for k in ('type', 'entity_id', 'references', 'active_platforms')}
                            for row in json.loads(capture.payload_json)['objects']]
                row = (path.name, code.co_name, value)
                reasons = evidence.setdefault('reasons', [])
                if row not in reasons:
                    reasons.append(row)
        monitor.register_callback(slot, monitor.events.RAISE, trace)
        monitor.set_events(slot, monitor.events.RAISE)
        def stop_monitor():
            monitor.set_events(slot, 0)
            monitor.register_callback(slot, monitor.events.RAISE, None)
            monitor.free_tool_id(slot)
        self.addCleanup(stop_monitor)

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
            history = NativeRecorderGuard(hass)
            await history.async_synchronize()
            now = dt_util.utcnow()
            period = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
            get_instance(hass).queue_task(StatisticsTask(period, False))
            await history.async_synchronize()
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
    return unittest.TestSuite(ControlledActualGraphTests(name) for name in
        ('test_explicit_leaf_forward_fresh_inverse', 'test_live_yaml_consumers_forward_fresh_inverse'))
