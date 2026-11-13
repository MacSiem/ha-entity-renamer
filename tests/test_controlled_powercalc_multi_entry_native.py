"""One retained ordinary plan for two real Powercalc ConfigEntry sources.

The fixture is synthetic; the native result is evidence only when the installed
backend, receipts, independent readback and cleanup all succeed.
"""
import asyncio
from decimal import Decimal
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from test_controlled_powercalc_actual_native import ControlledActualGraphTests


class ControlledMultiEntryTests(ControlledActualGraphTests):
    async def test_two_entries_real_ui_one_review_forward_fresh_inverse(self):
        self.use_public_transport = True
        self.use_browser_transport = True
        await self.test_two_entries_one_review_forward_fresh_inverse()

    async def test_two_entries_public_websocket_one_review_forward_fresh_inverse(self):
        self.use_public_transport = True
        await self.test_two_entries_one_review_forward_fresh_inverse()

    async def test_two_entries_one_review_forward_fresh_inverse(self):
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
        entries = self.entries[:2]
        before_entries = {e.entry_id: dict(e.data) for e in entries}
        foreign_entries = {e.entry_id: dict(e.data) for e in self.entries[2:]}
        names = {uid: entity.entity_id for uid, entity in self.entities.items()}
        uuids = {uid: entity.registry_entry.id for uid, entity in self.entities.items()}
        sources = [self.sources[i].entity_id for i in (10, 11)]
        mapping = {old: f'light.renamed_fixture_source_{i}' for i, old in enumerate(sources)}
        for i in (0, 10, 11):
            for suffix in ('', '_energy'):
                uid = f'fixture-meter-{i}{suffix}'
                mapping[names[uid]] = f'sensor.renamed_fixture_{i}{suffix}'
        expected_entries = {entry_id: {**before, 'entity_id': mapping[before['entity_id']],
                            '_power_entity': mapping[before['_power_entity']],
                            '_energy_entity': mapping[before['_energy_entity']]}
                            for entry_id, before in before_entries.items()}
        out = Path('/qa/controlled-pc-multi-entry.json')
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
            self.assertEqual({e.entry_id: dict(e.data) for e in self.entries[2:]}, foreign_entries)

        try:
            native_dummy_mdns(hass)
            public = getattr(self, 'use_public_transport', False)
            browser_ui = getattr(self, 'use_browser_transport', False)
            if public:
                from aiohttp import ClientSession
                from datetime import timedelta
                from types import MappingProxyType
                from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
                from homeassistant.components.sensor import SensorEntity
                from homeassistant.config_entries import ConfigEntry, ConfigEntryState
                from homeassistant.helpers.entity_component import DATA_INSTANCES
                class OrdinarySensor(SensorEntity):
                    _attr_unique_id = 'synthetic-multi-ordinary'
                    _attr_name = 'Measurement'
                    _attr_has_entity_name = True
                    _attr_native_value = 1
                    _attr_should_poll = False
                    _attr_device_info = {'identifiers': {('synthetic-multi-queue', 'ordinary-device')},
                                         'name': 'Synthetic ordinary device'}
                class UtilitySource(OrdinarySensor):
                    _attr_unique_id = 'synthetic-multi-utility-source'
                    _attr_name = 'Utility source'
                ordinary_entry = ConfigEntry(domain='threshold', title='Synthetic ordinary owner',
                    data={}, options={'name': 'Synthetic ordinary helper',
                                      'entity_id': self.sources[0].entity_id,
                                      'lower': 20, 'upper': None, 'hysteresis': 0},
                    discovery_keys=MappingProxyType({}), source='user', version=1,
                    minor_version=1, subentries_data=[], unique_id='synthetic-multi-ordinary-owner')
                await hass.config_entries.async_add(ordinary_entry)
                await hass.async_block_till_done()
                self.assertIs(ordinary_entry.state, ConfigEntryState.LOADED)
                ordinary_device = self.f.device.async_get_or_create(
                    config_entry_id=ordinary_entry.entry_id,
                    identifiers={('synthetic-multi-queue', 'ordinary-device')})
                component = hass.data[DATA_INSTANCES]['sensor']
                ordinary_entity = OrdinarySensor()
                await component.async_add_entities([ordinary_entity])
                ordinary_id = ordinary_entity.entity_id
                platform = ordinary_entity.platform
                await platform.async_remove_entity(ordinary_id)
                if browser_ui:
                    self.f.entity.async_update_entity(ordinary_id, name='Synthetic original name')
                    ordinary_device = self.f.device.async_update_device(
                        ordinary_device.id, name_by_user='Synthetic original device')
                platform.config_entry = ordinary_entry
                ordinary_entity = OrdinarySensor()
                await platform.async_add_entities([ordinary_entity])
                if browser_ui:
                    utility_entity = UtilitySource()
                    await platform.async_add_entities([utility_entity])
                await hass.async_block_till_done()
                ordinary = self.f.entity.async_get(ordinary_id)
                self.assertIsNotNone(ordinary)
                self.assertEqual(ordinary.device_id, ordinary_device.id)
                self.assertIs(ordinary_entity.device_entry, self.f.device.async_get(ordinary_device.id))
                ordinary_name_before = ordinary.name
                device_name_before = ordinary_device.name_by_user
                if browser_ui:
                    utility_id = utility_entity.entity_id
                    utility_row = self.f.entity.async_get(utility_id)
                    self.assertIs(utility_entity.registry_entry, utility_row)
                    self.assertEqual(utility_row.device_id, ordinary_device.id)
                    mapping[utility_id] = 'sensor.p4_utility_source'
                # Native YAML consumers span both selected producers. They are
                # loaded through HA before ORIGINAL is captured and checked
                # independently after forward and inverse.
                import yaml
                from homeassistant.config import async_hass_config_yaml
                from homeassistant.setup import async_setup_component
                leaf_a, leaf_b = names['fixture-meter-10'], names['fixture-meter-11']
                programs = {
                    'template': [{'unique_id': 'multi-public-consumer', 'sensor': [{
                        'name': 'Multi public value', 'unique_id': 'value',
                        'state': "{{ states('" + leaf_a + "') }}"}]}],
                    'automation': [{'id': 'multi_public_automation', 'alias': 'Multi public automation',
                        'triggers': [{'trigger': 'state', 'entity_id': leaf_a}],
                        'actions': [{'event': 'multi_public_automation_result',
                                     'event_data': {'value': "{{ states('" + leaf_b + "') }}"}}]}],
                    'script': {'multi_public_script': {'sequence': [{'event': 'multi_public_script_result',
                        'event_data': {'value': "{{ states('" + leaf_b + "') }}"}}]}},
                }
                if hasattr(self, 'boot_automation'):
                    programs['automation'].insert(0, self.boot_automation)
                files = {domain: self.f.root / ('multi-public-' + domain + '.yaml') for domain in programs}
                for domain, path in files.items():
                    path.write_text(yaml.safe_dump(programs[domain], sort_keys=False))
                with (self.f.root / 'configuration.yaml').open('a') as stream:
                    for domain, path in files.items():
                        if domain == 'automation' and hasattr(self, 'boot_automation'):
                            continue  # Registered before the one actual startup.
                        stream.write('\n' + domain + ': !include ' + path.name + '\n')
                    if browser_ui:
                        stream.write('\nhomekit:\n  name: P4 Synthetic Bridge\n  port: 21198\n'
                            '  ip_address: 192.0.2.1\n  advertise_ip: 192.0.2.1\n'
                            '  filter:\n    include_entities:\n      - ' + sources[0] + '\n'
                            'utility_meter:\n  p4_meter:\n    source: ' + utility_id +
                            '\n    cycle: daily\n')
                config = await async_hass_config_yaml(hass)
                for domain in programs:
                    if domain in hass.config.components:
                        await hass.services.async_call(domain, 'reload', {}, blocking=True)
                    else:
                        self.assertTrue(await async_setup_component(hass, domain, config), domain)
                if browser_ui:
                    for domain in ('utility_meter', 'homekit'):
                        if domain == 'utility_meter' and domain in hass.config.components:
                            from homeassistant.components.utility_meter import async_setup, CONFIG_SCHEMA, DATA_UTILITY
                            self.assertFalse(hass.data[DATA_UTILITY])
                            self.assertTrue(await async_setup(hass, CONFIG_SCHEMA(config)))
                        else:
                            self.assertTrue(await async_setup_component(hass, domain, config), domain)
                    await hass.async_block_till_done()
                    await hass.services.async_call('homekit', 'reload', {}, blocking=True)
                    await hass.async_block_till_done()
                    external_config_before = (self.f.root / 'configuration.yaml').read_bytes()
                    bridge_entry = next(e for e in hass.config_entries.async_entries('homekit')
                                        if e.data['name'] == 'P4 Synthetic Bridge')
                    from custom_components.ha_entity_renamer.native_homekit_filter import _bridge_identity
                    bridge_identity = _bridge_identity(bridge_entry)
                    def aid(entity_id):
                        matches = [key for key, value in bridge_entry.runtime_data.homekit.bridge.accessories.items()
                                   if getattr(value, 'entity_id', None) == entity_id]
                        self.assertEqual(len(matches), 1)
                        return matches[0]
                    original_aid = aid(sources[0])
                    def utility_meter():
                        rows = [entity for platform in async_get_platforms(hass, 'utility_meter')
                                for entity in platform.entities.values()
                                if getattr(entity, '_parent_meter', None) == 'p4_meter']
                        self.assertEqual(len(rows), 1)
                        return rows[0]
                await hass.async_block_till_done()
                if hasattr(self, 'boot_automation'):
                    self.assertEqual(len(self.boot_actions), 1, 'automation reload must not synthesize a boot')
                yaml_before = {domain: path.read_bytes() for domain, path in files.items()}
                from homeassistant.components.energy.data import async_get_manager
                energy = await async_get_manager(hass)
                preferences = energy.default_preferences()
                energy_ids = [names['fixture-meter-10_energy'], names['fixture-meter-11_energy']]
                preferences['device_consumption'] = [{'stat_consumption': name} for name in energy_ids]
                await energy.async_update(preferences)
                await energy._store.async_save(energy.data)
                energy_before = json.loads((self.f.root / '.storage/energy').read_bytes())['data']
                from copy import deepcopy
                from homeassistant.components.lovelace.const import LOVELACE_DATA
                from homeassistant.helpers.storage import Store
                dashboard_metadata = {'id': 'p4_multi', 'url_path': 'p4-multi', 'mode': 'storage',
                                      'require_admin': True, 'show_in_sidebar': True,
                                      'title': 'Synthetic multi queue', 'icon': 'mdi:home'}
                if not browser_ui:
                    self.assertNotIn('lovelace', hass.config.components)
                    await Store(hass, 1, 'lovelace_dashboards').async_save({'items': [dashboard_metadata]})
                    self.assertTrue(await async_setup_component(hass, 'lovelace', await async_hass_config_yaml(hass)))
                dashboard = hass.data[LOVELACE_DATA].dashboards['p4-multi']
                dashboard_before = {'views': [{'title': 'Synthetic', 'cards': [
                    {'type': 'entities', 'entities': [leaf_a, leaf_b]},
                    {'type': 'custom:button-card', 'entity': leaf_a,
                     'triggers_update': [leaf_a, leaf_b], 'name': 'Unchanged label'}]}]}
                dashboard_after = deepcopy(dashboard_before)
                cards = dashboard_after['views'][0]['cards']
                cards[0]['entities'] = [mapping[leaf_a], mapping[leaf_b]]
                cards[1]['entity'] = mapping[leaf_a]
                cards[1]['triggers_update'] = [mapping[leaf_a], mapping[leaf_b]]
                await dashboard.async_save(dashboard_before)
                dashboard_path = self.f.root / '.storage/lovelace.p4_multi'
                self.assertEqual(json.loads(dashboard_path.read_bytes())['data']['config'], dashboard_before)
                client = ClientSession()
                self.addAsyncCleanup(client.close)
                from custom_components.ha_entity_renamer.frontend import CARD_PATH
                async with client.get('http://127.0.0.1:' + str(hass.http.server_port) + CARD_PATH) as response:
                    self.assertEqual(response.status, 200)
                    served = await response.read()
                digest = hashlib.sha256(served).hexdigest()
                self.assertEqual(digest, self.public_source_sha256['www/ha-entity-renamer.js'])
                self.assertEqual(served, (self.installed_component / 'www/ha-entity-renamer.js').read_bytes())
                evidence['card_served_sha256'] = digest
                async def connect(label, group):
                    user = await hass.auth.async_create_user(label, group_ids=[group])
                    token = await hass.auth.async_create_refresh_token(user,
                        client_id='https://renamer.invalid/multi-queue', access_token_expiration=timedelta(hours=5))
                    access = hass.auth.async_create_access_token(token, remote_ip='127.0.0.1')
                    socket = await client.ws_connect('http://127.0.0.1:' + str(hass.http.server_port) + '/api/websocket')
                    self.addAsyncCleanup(socket.close)
                    self.assertEqual((await socket.receive_json(timeout=10))['type'], 'auth_required')
                    await socket.send_json({'type': 'auth', 'access_token': access})
                    self.assertEqual((await socket.receive_json(timeout=10))['type'], 'auth_ok')
                    return socket, access
                admin, admin_access = await connect('Synthetic multi administrator', GROUP_ID_ADMIN)
                user, user_access = await connect('Synthetic multi nonadministrator', GROUP_ID_USER)
                await hass.auth._store._store.async_save(hass.auth._store._data_to_save())
                request_id = 0
                async def request(socket, command, **payload):
                    nonlocal request_id
                    request_id += 1
                    await socket.send_json({'id': request_id, 'type': 'ha_entity_renamer/' + command, **payload})
                    return await socket.receive_json(timeout=2400)
                selected = [{'entity_id': old, 'new_entity_id': new} for old, new in mapping.items()]
                selected.append({'entity_id': ordinary_id, 'name': 'Synthetic ordinary name'})
                devices = [{'device_id': ordinary_device.id, 'name_by_user': 'Synthetic ordinary device'}]
                denied = await request(user, 'prepare', entities=selected, devices=devices)
                self.assertFalse(denied['success'])
                self.assertEqual(denied.get('error', {}).get('code'), 'unauthorized')
                evidence['nonadmin_denied'] = denied.get('error', {}).get('code')
                if browser_ui:
                    directory = Path(os.environ['RENAMER_MULTI_UI'])
                    token = directory / 'render-token'
                    token.write_text(admin_access); token.chmod(0o600)
                    token = directory / 'render-user-token'
                    token.write_text(user_access); token.chmod(0o600)
                    entities = []
                    for old, new in mapping.items():
                        entity = self.f.entity.async_get(old)
                        self.assertIsNotNone(entity)
                        entities.append({'old': old, 'new': new, 'device': entity.device_id})
                    entities.append({'old': ordinary_id, 'new': ordinary_id,
                                     'name': 'Synthetic ordinary name', 'device': ordinary.device_id})
                    browser_request = {'label': 'multi-ui', 'port': hass.http.server_port,
                                       'entities': entities, 'device': ordinary_device.id,
                                       'device_name': 'Synthetic ordinary device',
                                       'original_device_name': device_name_before,
                                       'ordinary_original_name': ordinary_name_before}
                    async def ui_wait(label):
                        path = directory / (label + '.json')
                        for _ in range(24000):
                            if path.is_file():
                                value = json.loads(path.read_text())
                                self.assertNotIn('error', value, value)
                                return value
                            await asyncio.sleep(.1)
                        self.fail('browser fixture timed out: ' + label)
                    def ui_continue(label):
                        (directory / (label + '.go')).write_text('go')
            await pulse()
            if browser_ui:
                # Supply real numeric samples after Utility subscribes, so the
                # retained value and native reset deadline have actual inputs.
                for value in (2, 3):
                    utility_entity._attr_native_value = value
                    utility_entity.async_write_ha_state()
                    await hass.async_block_till_done()
            await baseline()
            if browser_ui:
                meter = utility_meter()
                self.assertIsNotNone(meter._next_reset)
                self.assertIsNotNone(meter.native_value)
                meter_before = (meter.entity_id, meter.native_value, meter._next_reset)
            if public:
                # Independent physical oracle for every native registry row,
                # including foreign IDs, device associations, units and labels.
                # Only HA's audit timestamp is excluded.
                def registry_rows(kind):
                    path = self.f.root / ('.storage/core.' + kind + '_registry')
                    rows = json.loads(path.read_bytes())['data'][{'entity': 'entities', 'device': 'devices'}[kind]]
                    return {row['id']: {key: value for key, value in row.items() if key != 'modified_at'}
                            for row in rows}
                original_entities = registry_rows('entity')
                original_devices = registry_rows('device')
                expected_entities = deepcopy(original_entities)
                for row in expected_entities.values():
                    row['entity_id'] = mapping.get(row['entity_id'], row['entity_id'])
                    if row['id'] == ordinary.id:
                        row['name'] = 'Synthetic ordinary name'
                expected_devices = deepcopy(original_devices)
                expected_devices[ordinary_device.id]['name_by_user'] = 'Synthetic ordinary device'
            history = NativeRecorderGuard(hass)
            await history.async_synchronize()
            now = dt_util.utcnow()
            period = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
            get_instance(hass).queue_task(StatisticsTask(period, False))
            await history.async_synchronize()
            if public:
                # Independent physical Recorder oracle, separate from the
                # product's history guards. Preserve every preexisting row;
                # subsequent native samples can append new rows.
                import sqlite3
                def history_rows():
                    result = {}
                    with sqlite3.connect((self.f.root / 'fixture.db').as_uri() + '?mode=ro', uri=True) as db:
                        for table, primary, excluded in (
                            ('states', 'state_id', {'last_reported_ts'}),
                            ('state_attributes', 'attributes_id', set()),
                            ('statistics_short_term', 'id', set()),
                            ('statistics', 'id', set()),
                            ('states_meta', 'metadata_id', {'entity_id'}),
                            ('statistics_meta', 'id', {'statistic_id'}),
                        ):
                            columns = [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')
                                       if row[1] not in excluded]
                            self.assertIn(primary, columns)
                            rows = db.execute('SELECT ' + ','.join(columns) + ' FROM ' + table).fetchall()
                            index = columns.index(primary)
                            result[table] = {row[index]: row for row in rows}
                    return result
                original_history = await get_instance(hass).async_add_executor_job(history_rows)
                self.assertTrue(original_history['states'])
                async def verify_history():
                    await history.async_synchronize()
                    current = await get_instance(hass).async_add_executor_job(history_rows)
                    for table, rows in original_history.items():
                        self.assertTrue(all(current[table].get(key) == row for key, row in rows.items()), table)
            foreign_power = inventory()['fixture-meter-0']
            standby = foreign_power._standby_power
            foreign_power._standby_power = Decimal('0.4')
            try:
                with self.assertRaises((ValueError, Refused)):
                    await runtime.native_yaml_prepare(hass, mapping, actor_id=actor, guard=guard, refresh=refresh)
            finally:
                foreign_power._standby_power = standby
            self.assertEqual(self.package.read_bytes(), original_package)
            self.assertEqual({e.entry_id: dict(e.data) for e in entries}, before_entries)
            if browser_ui:
                request_path = directory / 'request.tmp'
                request_path.write_text(json.dumps(browser_request)); request_path.replace(directory / 'request.json')
                review = await ui_wait('review')
                op, fingerprint = review['operation_id'], review['fingerprint']
            elif public:
                reply = await request(admin, 'prepare', entities=selected, devices=devices)
                self.assertTrue(reply.get('success'), reply)
                review = reply['result']
            else:
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
            if browser_ui:
                ui_continue('apply')
                result = await ui_wait('applied')
            elif public:
                reply = await request(admin, 'apply', operation_id=op, fingerprint=fingerprint)
                self.assertTrue(reply.get('success'), reply)
                result = reply['result']
            else:
                result = await runtime.native_yaml_apply(hass, op, fingerprint, actor_id=actor, guard=guard, refresh=refresh)
            evidence['apply'] = result
            save()
            self.assertEqual(result['state'], 'applied')
            if public:
                self.assertEqual(self.f.entity.async_get(ordinary_id).name, 'Synthetic ordinary name')
                self.assertEqual(self.f.device.async_get(ordinary_device.id).name_by_user,
                                 'Synthetic ordinary device')
                for domain, path in files.items():
                    for expected_old in ((leaf_a, leaf_b) if domain == 'automation' else
                                         (leaf_a,) if domain == 'template' else (leaf_b,)):
                        self.assertNotIn(expected_old, path.read_text())
                        self.assertIn(mapping[expected_old], path.read_text())
                disk_energy = json.loads((self.f.root / '.storage/energy').read_bytes())['data']
                self.assertEqual([row['stat_consumption'] for row in disk_energy['device_consumption']],
                                 [mapping[name] for name in energy_ids])
                self.assertEqual(energy.data, disk_energy)
                self.assertEqual(json.loads(dashboard_path.read_bytes())['data']['config'], dashboard_after)
                self.assertEqual(await dashboard.async_load(False), dashboard_after)
                self.assertEqual(registry_rows('entity'), expected_entities)
                self.assertEqual(registry_rows('device'), expected_devices)
                await verify_history()
                if browser_ui:
                    meter = utility_meter()
                    self.assertEqual((meter.entity_id, meter.native_value, meter._next_reset), meter_before)
                    self.assertEqual(meter._sensor_source_id, mapping[utility_id])
                    self.assertIsNotNone(meter._collecting)
                    self.assertEqual((_bridge_identity(bridge_entry), aid(mapping[sources[0]])),
                                     (bridge_identity, original_aid))
                if hasattr(self, 'boot_automation'):
                    self.assertEqual(len(self.boot_actions), 1, 'forward reload must not synthesize a boot')
            verify_seam(0, mapping)
            verify_ids(mapping)
            self.assertEqual({e.entry_id: dict(e.data) for e in entries}, expected_entries)
            for old, new in mapping.items():
                if old in names.values() and old not in sources:
                    self.assertNotIn(old, self.package.read_text())
                    self.assertIn(new, self.package.read_text())
            verify_foreign()
            initial = counter_values(inventory())
            await pulse()
            self.assertTrue(all(Decimal(counter_values(inventory())[uid][0]) > Decimal(initial[uid][0])
                                for uid in ('fixture-group-a_energy', 'fixture-group-b_energy')))
            await baseline()
            if public:
                reply = await request(admin, 'recovery/inspect', operation_id=op, fingerprint=fingerprint)
                self.assertTrue(reply.get('success'), reply)
                inspected = reply['result']
            else:
                inspected = await runtime.recovery_inspect(hass, op, fingerprint, guard=guard, refresh=refresh)
            evidence['inspect'] = inspected
            save()
            if browser_ui:
                self.assertEqual(inspected['state'], 'applied', inspected)
                self.assertFalse(inspected['eligible'], inspected)
            else:
                self.assertTrue(inspected['eligible'], inspected)
            if browser_ui:
                ui_continue('restore')
                result = await ui_wait('restored')
            elif public:
                reply = await request(admin, 'recovery/restore', operation_id=op, fingerprint=fingerprint)
                self.assertTrue(reply.get('success'), reply)
                result = reply['result']
            else:
                result = await runtime.recovery_restore(hass, op, fingerprint, guard=guard, refresh=refresh)
            evidence['restore'] = result
            save()
            if browser_ui:
                self.assertEqual((result['state'], result['kind']), ('applied', 'fresh_inverse'))
            else:
                self.assertEqual(result['state'], 'rolled_back')
            if public:
                self.assertEqual(self.f.entity.async_get(ordinary_id).name, ordinary_name_before)
                self.assertEqual(self.f.device.async_get(ordinary_device.id).name_by_user,
                                 device_name_before)
                self.assertEqual({domain: path.read_bytes() for domain, path in files.items()}, yaml_before)
                self.assertEqual(json.loads((self.f.root / '.storage/energy').read_bytes())['data'], energy_before)
                self.assertEqual(energy.data, energy_before)
                self.assertEqual(json.loads(dashboard_path.read_bytes())['data']['config'], dashboard_before)
                self.assertEqual(await dashboard.async_load(False), dashboard_before)
                self.assertEqual(registry_rows('entity'), original_entities)
                self.assertEqual(registry_rows('device'), original_devices)
                await verify_history()
                if browser_ui:
                    meter = utility_meter()
                    self.assertEqual((meter.entity_id, meter.native_value, meter._next_reset), meter_before)
                    self.assertEqual(meter._sensor_source_id, utility_id)
                    self.assertIsNotNone(meter._collecting)
                    self.assertEqual((_bridge_identity(bridge_entry), aid(sources[0])),
                                     (bridge_identity, original_aid))
                    self.assertEqual((self.f.root / 'configuration.yaml').read_bytes(), external_config_before)
                if hasattr(self, 'boot_automation'):
                    self.assertEqual(len(self.boot_actions), 1, 'inverse reload must not synthesize a boot')
            verify_seam(1, {new: old for old, new in mapping.items()})
            verify_ids({})
            self.assertEqual({e.entry_id: dict(e.data) for e in entries}, before_entries)
            self.assertEqual(self.package.read_bytes(), original_package)
            verify_foreign()
            initial = counter_values(inventory())
            await pulse()
            self.assertTrue(all(Decimal(counter_values(inventory())[uid][0]) > Decimal(initial[uid][0])
                                for uid in ('fixture-group-a_energy', 'fixture-group-b_energy')))
            native_dummy_mdns(hass)
        finally:
            save()
            destination = os.environ.get('RENAMER_BATCH_EVIDENCE')
            if destination:
                import shutil
                storage = self.f.root / '.storage'
                target = Path(destination) / 'multi-storage'
                if storage.is_dir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    for source in storage.iterdir():
                        if source.is_file() and 'ha_entity_renamer' in source.name:
                            copied = target / source.name
                            shutil.copyfile(source, copied)
                            copied.chmod(0o600)


class ControlledMultiEntryColdPublicTests(ControlledMultiEntryTests):
    async def _before_start(self, hass, root):
        from homeassistant.core import callback
        from homeassistant.util import dt as dt_util
        await super()._before_start(hass, root)
        self.boot_events, self.boot_actions = [], []
        self.boot_begin = dt_util.utcnow()
        @callback
        def started(event):
            self.boot_events.append(event)
        @callback
        def action(event):
            self.boot_actions.append(event)
        self.addCleanup(hass.bus.async_listen('homeassistant_start', started))
        self.addCleanup(hass.bus.async_listen('p4_multi_actual_boot', action))

    async def _setup_vendor_forward(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        import yaml
        await super()._setup_vendor_forward()
        self.boot_automation = {'id': 'p4_multi_actual_boot', 'alias': 'P4 one actual boot',
            'triggers': [{'trigger': 'homeassistant', 'event': 'start'}],
            'actions': [{'event': 'p4_multi_actual_boot'}]}
        path = self.f.root / 'multi-public-automation.yaml'
        path.write_text(yaml.safe_dump([self.boot_automation], sort_keys=False))
        with (self.f.root / 'configuration.yaml').open('a') as stream:
            stream.write('\nautomation: !include ' + path.name + '\n')
        self.assertTrue(await async_setup_component(self.f.hass, 'automation',
                                                     await async_hass_config_yaml(self.f.hass)))

    async def asyncSetUp(self):
        from homeassistant.util import dt as dt_util
        from test_controlled_powercalc_actual_native import assert_one_native_cold_start
        await super().asyncSetUp()
        assert_one_native_cold_start(self, self.boot_events, self.boot_begin, dt_util.utcnow())
        self.assertEqual(len(self.boot_actions), 1, 'one real automation start action')


class ControlledMultiEntryColdUITests(ControlledMultiEntryColdPublicTests):
    async def _startup_services(self, hass, root):
        await super()._startup_services(hass, root)
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        from homeassistant.helpers.storage import Store
        from homeassistant.components.onboarding import OnboardingStorage, STORAGE_KEY, STORAGE_VERSION
        from homeassistant.components.onboarding.const import DOMAIN, STEPS
        from homeassistant.components.onboarding.views import _BaseOnboardingStepView
        self.assertTrue(await async_setup_component(hass, DOMAIN, {}))
        onboarding = hass.data[DOMAIN]
        if not onboarding.onboarded:
            store = OnboardingStorage(hass, STORAGE_VERSION, STORAGE_KEY, private=True)
            step = _BaseOnboardingStepView(onboarding.steps, store)
            for name in STEPS:
                if name not in onboarding.steps['done']:
                    step.step = name
                    await step._async_mark_done(hass)
        dashboards = [
            {'id': 'p4_multi', 'url_path': 'p4-multi', 'mode': 'storage',
             'require_admin': True, 'show_in_sidebar': True,
             'title': 'Synthetic multi queue', 'icon': 'mdi:home'},
            {'id': 'p4_ui', 'url_path': 'p4-ui', 'mode': 'storage',
             'require_admin': False, 'show_in_sidebar': True,
             'title': 'Synthetic Renamer UI', 'icon': 'mdi:test-tube'},
        ]
        await Store(hass, 1, 'lovelace_dashboards').async_save({'items': dashboards})
        self.assertTrue(await async_setup_component(hass, 'lovelace', await async_hass_config_yaml(hass)))
        self.assertTrue(await async_setup_component(hass, 'frontend', {}))
        self.assertTrue(await async_setup_component(hass, 'api', {}))
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        await hass.data[LOVELACE_DATA].dashboards['p4-ui'].async_save({'views': [
            {'title': 'Synthetic Renamer', 'cards': [{'type': 'custom:ha-entity-renamer'}]}]})


def load_tests(loader, tests, pattern):
    import unittest
    if os.environ.get('RENAMER_MULTI_UI'):
        return unittest.TestSuite([ControlledMultiEntryColdUITests('test_two_entries_real_ui_one_review_forward_fresh_inverse')])
    return unittest.TestSuite([ControlledMultiEntryColdPublicTests('test_two_entries_public_websocket_one_review_forward_fresh_inverse')])
