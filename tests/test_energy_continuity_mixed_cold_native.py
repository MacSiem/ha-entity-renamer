"""Real process restart for the current YAML/Template/Powercalc composition."""
import base64
import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import test_powercalc_public_cold_native as cold


class MixedPowercalcColdPhase(cold.PowercalcColdPhase):
    def _network_evidence(self):
        from ordinary_network_scope import native_dummy_mdns
        try:
            return native_dummy_mdns(self.f.hass)
        except ValueError as error:
            tables = {name: (Path('/proc/net') / name).read_text()
                      for name in ('tcp', 'tcp6', 'udp', 'udp6')}
            sockets = {}
            for fd in Path('/proc/self/fd').iterdir():
                try:
                    value = os.readlink(fd)
                except OSError:
                    continue
                if value.startswith('socket:'):
                    sockets[fd.name] = value
            self.cold_evidence.setdefault('network_refusals', []).append(
                {'reason': str(error), 'tables': tables, 'socket_fds': sockets})
            raise

    async def _await_cold_readiness(self):
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        import time
        started = time.monotonic()
        async with asyncio.timeout(240):
            while True:
                pending = [manager._store.key for manager in self.f.managers
                    if (manager._store._data is not None or manager._store._delay_handle is not None
                        or manager._store._unsub_final_write_listener is not None
                        or manager._store._write_lock.locked() or manager._store._load_future is not None)]
                if not pending:
                    break
                await asyncio.sleep(0.25)
        images = {}
        for manager in self.f.managers:
            memory = json.loads(_orjson_bytes_default_encoder(manager._data_to_save()))
            disk = json.loads(Path(manager._store.path).read_bytes())['data']
            self.assertEqual(memory, disk)
            images[manager._store.key] = {'memory_equals_disk': True}
        async with asyncio.timeout(15):
            while True:
                try:
                    self._network_evidence()
                    break
                except ValueError:
                    await asyncio.sleep(0.25)
        self.cold_evidence['cold_readiness'] = {'seconds': time.monotonic() - started, 'stores': images}

    def _measurement_failure(self, phase, error):
        self.cold_evidence.setdefault('measurement_failures', []).append(
            {'phase': phase, 'type': type(error).__name__})

    def _verify_cold_sum(self, group):
        try:
            result = super()._verify_cold_sum(group)
            self._cold_sum_pending = None
            return result
        except AssertionError:
            # Allow asynchronous native publications to settle before judging.
            self._cold_sum_pending = group.entity_id
            return None

    async def _settle_cold_sum(self):
        group_id = getattr(self, '_cold_sum_pending', None)
        if group_id is None:
            return
        try:
            async with asyncio.timeout(45):
                while True:
                    try:
                        super()._verify_cold_sum(self._find_group(group_id))
                        self._cold_sum_pending = None
                        return
                    except AssertionError:
                        await asyncio.sleep(0.25)
        except TimeoutError as error:
            self._measurement_failure('cold_sum', error)
            self._cold_sum_pending = None

    async def _pulse_light(self, brightness):
        if self.mode != 'migration' and self.phase == 'forward':
            return await super()._pulse_light(brightness)
        hass = self.f.hass
        group_id = ('sensor.synthetic_new_sum_energy' if self.phase == 'forward'
                    else 'sensor.public_sum_energy')
        if self.phase == 'forward':
            reference = self.cold_evidence['apply']
            group_uuid = self.cold_evidence['before']['entities']['sensor.public_sum_energy']['uuid']
        else:
            forward = json.loads(Path('/qa/cold-mixed-' + self.mode + '-forward.json').read_text())
            reference = forward['before' if self.mode == 'migration' else 'before_shutdown']
            group_uuid = self.meta['group_uuid']
        cost_uuid = reference['cost']['registry_id']
        samples = self.cold_evidence.setdefault('stimulus_observations', [])
        try:
            async with asyncio.timeout(45):
                baseline = None
                while baseline is None:
                    cost_id = hass.data['energy']['cost_sensors'].get(group_id)
                    group, cost = hass.states.get(group_id), hass.states.get(cost_id) if cost_id else None
                    group_row = self.f.entity.async_get(group_id)
                    cost_row = self.f.entity.async_get(cost_id) if cost_id else None
                    if (group and cost and group_row and cost_row and group_row.id == group_uuid
                            and cost_row.id == cost_uuid):
                        try:
                            baseline = (Decimal(group.state), Decimal(cost.state))
                            if not all(value.is_finite() for value in baseline):
                                baseline = None
                        except Exception:
                            baseline = None
                    if baseline is None:
                        await asyncio.sleep(0.25)
                await self.sources[0].async_turn_on(brightness=brightness)
                while True:
                    source = hass.states.get(self.sources[0].entity_id)
                    power = hass.states.get('sensor.public_static_power')
                    group, cost = hass.states.get(group_id), hass.states.get(cost_id)
                    group_row, cost_row = self.f.entity.async_get(group_id), self.f.entity.async_get(cost_id)
                    if (source and source.state == 'on' and power and group and cost and group_row and cost_row
                            and group_row.id == group_uuid and cost_row.id == cost_uuid):
                        try:
                            power_value, energy, money = Decimal(power.state), Decimal(group.state), Decimal(cost.state)
                            delta_energy, delta_cost = energy - baseline[0], money - baseline[1]
                            if (power_value > 0 and delta_energy > 0 and delta_cost > 0
                                    and abs(delta_cost - delta_energy * Decimal('0.75')) < Decimal('0.000000001')):
                                samples.append({'phase': self.phase, 'source': source.entity_id,
                                    'power_w': str(power_value), 'energy_kwh': str(delta_energy),
                                    'price_per_kwh': '0.75', 'cost': str(delta_cost),
                                    'group_uuid': group_uuid, 'cost_uuid': cost_uuid})
                                break
                        except Exception:
                            pass
                    await asyncio.sleep(0.25)
        except TimeoutError as error:
            self._measurement_failure('published_stimulus_' + self.phase, error)
        finally:
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()

    async def _startup_services(self, hass, root):
        # Keep the synthetic HTTP server on loopback in the network-none
        # namespace so the Zeroconf FD census remains meaningful.
        self.with_yaml = True
        await cold.receipted.NativeReceiptedHelperTests._before_start(self, hass, root)
        # HA migrates this YAML to a trial HTTP configuration. Confirm the
        # fixture's own loopback binding so cold startup keeps it after the
        # native five-minute trial instead of reverting to wildcard listeners.
        from homeassistant.components.http.config import async_get_and_load_store
        http_config = await async_get_and_load_store(hass)
        if http_config.pending is not None:
            await http_config.async_promote_pending()

    async def asyncSetUp(self):
        self.group_fixture_options = {'energy_sensor_precision': 6}
        self.cold_evidence = {'phase': os.environ['RENAMER_POWER_COLD_PHASE'],
                              'mode': os.environ['RENAMER_POWER_COLD_MODE'], 'requests': []}
        await super().asyncSetUp()
        if self.phase != 'forward':
            await self._load_mixed_yaml()
            await self._load_energy()
        # Template can already be initialized by another integration before
        # the fixture's includes are written. Reload each actual YAML owner.
        for domain in ('template', 'automation', 'script'):
            if self.f.hass.services.has_service(domain, 'reload'):
                await self.f.hass.services.async_call(domain, 'reload', {}, blocking=True)
        await self.f.hass.async_block_till_done()

    async def _setup_vendor_forward(self):
        await super()._setup_vendor_forward()
        root = self.f.root
        (root / 'r9_template.yaml').write_text(
            '- unique_id: r9-cold-template\n  sensor:\n  - name: R9 cold template\n'
            '    unique_id: value\n    state: "{{ states(\'' + self.yaml_source + '\') }}"\n')
        (root / 'r9_automations.yaml').write_text(
            '- id: r9_cold_automation\n  alias: R9 cold automation\n'
            '  triggers:\n  - trigger: state\n    entity_id: ' + self.yaml_source + '\n'
            '  actions:\n  - event: r9_cold_automation\n')
        (root / 'r9_scripts.yaml').write_text(
            'r9_cold_caller:\n  sequence:\n  - event: r9_cold_caller\n'
            '    event_data:\n      value: "{{ states(\'' + self.yaml_source + '\') }}"\n')
        with (root / 'configuration.yaml').open('a') as stream:
            stream.write('\ntemplate: !include r9_template.yaml\n'
                         'automation: !include r9_automations.yaml\nscript: !include r9_scripts.yaml\n')
        await self._load_mixed_yaml()
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.components.energy.data import ENERGY_SOURCE_SCHEMA
        import probatio
        manager = await async_get_manager(self.f.hass)
        preferences = deepcopy(manager.data)
        preferences['energy_sources'] = probatio.Schema(ENERGY_SOURCE_SCHEMA)([{'type': 'grid',
            'stat_energy_from': self.sum_energy.entity_id, 'stat_energy_to': None,
            'stat_cost': None, 'entity_energy_price': None, 'number_energy_price': 0.75,
            'cost_adjustment_day': 0.0}])
        await manager.async_update(preferences)
        await manager._store.async_save(manager.data)
        await self._load_energy()

    async def _load_energy(self):
        from homeassistant.setup import async_setup_component
        self.assertTrue(await async_setup_component(self.f.hass, 'energy', {}))
        await self.f.hass.async_block_till_done()
        from homeassistant.components.energy.data import async_get_manager
        manager = await async_get_manager(self.f.hass)
        energy_id = manager.data['energy_sources'][0]['stat_energy_from']
        cost_id = self.f.hass.data['energy']['cost_sensors'][energy_id]
        self.assertIsNotNone(self.f.hass.states.get(cost_id))

    async def _load_mixed_yaml(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        config = await async_hass_config_yaml(self.f.hass)
        for domain in ('template', 'automation', 'script'):
            self.assertTrue(await async_setup_component(self.f.hass, domain, config))
        await self.f.hass.async_block_till_done()

    async def _capture(self, label):
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.db_schema import Statistics, StatisticsMeta, StatisticsShortTerm
        from homeassistant.components.recorder.util import session_scope
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.helpers.entity_platform import async_get_platforms
        from sqlalchemy import select
        root = self.f.root
        recorder = get_instance(self.f.hass)
        await recorder.async_block_till_done()
        database = Path('/qa/cold-mixed-' + self.mode + '-' + self.phase + '-' + label + '.sqlite')
        def copy_database():
            source = sqlite3.connect((root / 'fixture.db').as_uri() + '?mode=ro', uri=True)
            try:
                target = sqlite3.connect(database)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
        await self.f.hass.async_add_executor_job(copy_database)
        def statistics():
            result = {}
            with session_scope(session=recorder.get_session(), read_only=True) as session:
                for entity_id in ('sensor.public_sum_energy', 'sensor.synthetic_new_sum_energy',
                                  'sensor.public_sum_energy_cost', 'sensor.synthetic_new_sum_energy_cost'):
                    meta = session.execute(select(StatisticsMeta).where(
                        StatisticsMeta.statistic_id == entity_id)).scalar_one_or_none()
                    if meta is None:
                        result[entity_id] = None
                        continue
                    metadata = {column.name: str(getattr(meta, column.name))
                                for column in StatisticsMeta.__table__.columns}
                    series = {}
                    for name, table in (('short', StatisticsShortTerm), ('full', Statistics)):
                        rows = session.execute(select(table).where(table.metadata_id == meta.id)
                                               .order_by(table.id)).scalars()
                        series[name] = [{column.name: str(getattr(row, column.name))
                                         for column in table.__table__.columns} for row in rows]
                    result[entity_id] = {'metadata': metadata, **series}
            return result
        recorder_rows = await recorder.async_add_executor_job(statistics)
        raw = {}
        for name in ('core.restore_state', 'powercalc_group', 'core.entity_registry',
                     'core.config_entries', 'core.device_registry', 'energy', 'auth',
                     'ha_entity_renamer.migrations', 'ha_entity_renamer.registry_receipts'):
            path = root / '.storage' / name
            raw[name] = base64.b64encode(path.read_bytes()).decode() if path.is_file() else None
        for name in ('configuration.yaml', 'r9_template.yaml', 'r9_automations.yaml', 'r9_scripts.yaml'):
            path = root / name
            raw[name] = base64.b64encode(path.read_bytes()).decode() if path.is_file() else None
        entities = {entity.entity_id: {'uuid': entity.registry_entry.id,
                    'group_exact': str(getattr(entity, '_native_value_exact', None)),
                    'state': str(getattr(entity, '_state', None)),
                    'last_valid': str(getattr(entity, '_last_valid_state', None))}
                    for platform in async_get_platforms(self.f.hass, 'powercalc')
                    for entity in platform.entities.values()}
        manager = await async_get_manager(self.f.hass)
        grid = manager.data['energy_sources'][0]
        energy_id = grid['stat_energy_from']
        cost_id = self.f.hass.data['energy']['cost_sensors'].get(energy_id)
        from homeassistant.components.energy.sensor import EnergyCostSensor
        cost_entities = [entity for platform in async_get_platforms(self.f.hass, 'energy')
                         for entity in platform.entities.values() if type(entity) is EnergyCostSensor]
        cost = None if cost_id is None else self.f.hass.states.get(cost_id)
        energy = self.f.hass.states.get(energy_id)
        registry = None if cost_id is None else self.f.entity.async_get(cost_id)
        self.cold_evidence[label] = {'raw': raw, 'entities': entities, 'statistics': recorder_rows,
                                     'cost': {'count': len(cost_entities),
                                         'entity_id': cost_id, 'energy_id': energy_id,
                                         'price': grid['number_energy_price'],
                                         'registry_id': None if registry is None else registry.id,
                                         'unique_id': None if registry is None else registry.unique_id,
                                         'actual_ids': [entity.entity_id for entity in cost_entities],
                                         'native_value': None if not cost_entities else str(cost_entities[0].native_value),
                                         'state': None if cost is None else cost.as_dict(),
                                         'energy_state': None if energy is None else energy.as_dict()},
                                     'network': None}
        try:
            self.cold_evidence[label]['network'] = self._network_evidence()
        finally:
            Path('/qa/cold-mixed-' + self.mode + '-' + self.phase + '.json').write_text(
                json.dumps(self.cold_evidence, sort_keys=True, default=str))

    async def _transport(self):
        from aiohttp import ClientSession
        from homeassistant.auth.const import GROUP_ID_ADMIN
        ws = client = access = None
        async def connect():
            nonlocal client, access
            if client is not None:
                return
            hass = self.f.hass
            if self.phase == 'forward':
                user = await hass.auth.async_create_user('Synthetic cold administrator', group_ids=[GROUP_ID_ADMIN])
                self.user_id = user.id
            else:
                user = await hass.auth.async_get_user(self.meta['user_id'])
                self.assertIsNotNone(user)
            self.assertTrue(user.is_admin)
            refresh_token = await hass.auth.async_create_refresh_token(
                user, client_id='https://renamer.invalid/powercalc-cold',
                access_token_expiration=timedelta(hours=5))
            access = hass.auth.async_create_access_token(refresh_token, remote_ip='127.0.0.1')
            await hass.auth._store._store.async_save(hass.auth._store._data_to_save())
            client = ClientSession()
            self.addAsyncCleanup(client.close)
        async def request(index, command, **payload):
            nonlocal ws
            await connect()
            hass = self.f.hass
            ws = await client.ws_connect('http://127.0.0.1:' + str(hass.http.server_port) + '/api/websocket')
            try:
                self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_required')
                await ws.send_json({'type': 'auth', 'access_token': access})
                self.assertEqual((await ws.receive_json(timeout=10))['type'], 'auth_ok')
                await ws.send_json({'id': index, 'type': 'ha_entity_renamer/' + command, **payload})
                return await ws.receive_json(timeout=3600)
            finally:
                await ws.close()
        if (self.phase != 'forward' or self.mode != 'migration'
                or getattr(self, 'preflight_only', False)):
            await connect()
        async def compare_retained_cost(before, after):
            old = before['cost']['entity_id']
            new = after['cost']['entity_id']
            self.assertEqual(old, new)
            self.assertEqual(before['cost']['registry_id'], after['cost']['registry_id'])
            previous = before['statistics'][old]
            current = after['statistics'][new]
            self.assertIsNotNone(previous)
            self.assertTrue(previous['short'])
            self.assertIsNotNone(current)
            self.assertEqual(previous['metadata']['id'], current['metadata']['id'])
            self.assertIsNone(after['statistics']['sensor.synthetic_new_sum_energy_cost'])
            for table in ('short', 'full'):
                by_id = {row['id']: row for row in current[table]}
                self.assertTrue(all(by_id.get(row['id']) == row for row in previous[table]))
        async def observed(index, command, **payload):
            if command == 'recovery/inspect' and self.phase == 'resume':
                await self._settle_cold_sum()
            if command == 'prepare':
                if not getattr(self, 'preflight_only', False):
                    from homeassistant.components.recorder import get_instance
                    from homeassistant.components.recorder.db_schema import StatisticsMeta, StatisticsShortTerm
                    from homeassistant.components.recorder.util import session_scope
                    from sqlalchemy import select
                    recorder = get_instance(self.f.hass)
                    def read_statistics(entity_id):
                        with session_scope(session=recorder.get_session(), read_only=True) as session:
                            metadata = session.execute(select(StatisticsMeta).where(
                                StatisticsMeta.statistic_id == entity_id)).scalar_one_or_none()
                            if metadata is None:
                                return None, []
                            rows = session.execute(select(StatisticsShortTerm).where(
                                StatisticsShortTerm.metadata_id == metadata.id).order_by(StatisticsShortTerm.id)).scalars()
                            return metadata.id, [{column.name: str(getattr(row, column.name))
                                for column in StatisticsShortTerm.__table__.columns} for row in rows]
                    async with asyncio.timeout(600):
                        while True:
                            pairs = {entity_id: await recorder.async_add_executor_job(read_statistics, entity_id)
                                     for entity_id in (self.sum_energy.entity_id,
                                                       self.sum_energy.entity_id + '_cost')}
                            if all(metadata_id is not None and rows for metadata_id, rows in pairs.values()):
                                break
                            await asyncio.sleep(2)
                    self.cold_evidence['natural_statistics_before'] = {entity_id: {
                        'metadata_id': metadata_id, 'short_rows': rows}
                        for entity_id, (metadata_id, rows) in pairs.items()}
                # Do not let a WebSocket expire while waiting for the native
                # statistics cycle. No prepare or Apply was sent before this.
                await connect()
                # Fixture setup and the natural Recorder wait can schedule Core
                # registry saves. Settle and independently read the native
                # documents before asking the product to seal its ORIGINAL.
                self.cold_evidence['pending_core_stores_before_settle'] = [
                    manager._store.key for manager in self.f.managers
                    if (manager._store._data is not None
                        or manager._store._delay_handle is not None
                        or manager._store._unsub_final_write_listener is not None)]
                await self._flush_documents()
                await self._capture('before')
            await connect()
            reply = await request(index, command, **payload)
            self.cold_evidence['requests'].append({'command': command, 'reply': reply})
            if (command == 'recovery/inspect' and reply.get('success')
                    and reply.get('result', {}).get('state') == 'applied'):
                self.assertIs(reply['result'].get('eligible'), True)
            if command == 'prepare' and reply.get('success'):
                from custom_components.ha_entity_renamer.controlled_powercalc_composition import ControlledPowercalcComposition
                operation = reply['result']['operation_id']
                plan = self._runtime().reviews[operation].plan
                _, composition = self._runtime().preparations.resolve_composition(plan.native_inputs)
                self.assertIs(type(composition), ControlledPowercalcComposition)
                dependencies = reply['result']['dependencies']
                self.assertIn('template', dependencies['domains'])
                self.assertIn('powercalc', dependencies['domains'])
                self.cold_evidence['operation'] = operation
                self.cold_evidence['fingerprint'] = reply['result']['fingerprint']
            await self._capture(command.replace('/', '_'))
            if reply.get('success') and command in ('prepare', 'apply', 'recovery/restore'):
                try:
                    sample = self.cold_evidence[command.replace('/', '_')]['cost']
                    expected = ('sensor.synthetic_new_sum_energy' if command == 'apply'
                                else 'sensor.public_sum_energy')
                    self.assertEqual(sample['energy_id'], expected)
                    self.assertEqual(sample['price'], 0.75)
                    self.assertEqual(sample['count'], 1)
                    self.assertEqual(sample['actual_ids'], [sample['entity_id']])
                    if command == 'apply':
                        await compare_retained_cost(self.cold_evidence['before'], self.cold_evidence['apply'])
                    elif command == 'recovery/restore':
                        forward = json.loads(Path('/qa/cold-mixed-migration-forward.json').read_text())
                        await compare_retained_cost(forward['apply'], self.cold_evidence['recovery_restore'])
                except (AssertionError, InvalidOperation, KeyError, TypeError) as error:
                    self._measurement_failure('sample_' + command, error)
            return reply
        phase = self
        class SocketWitness:
            async def close(self):
                if phase.phase == 'forward':
                    await phase._capture('before_shutdown')
                    if phase.mode == 'migration':
                        try:
                            before = phase.cold_evidence['apply']['cost']
                            after = phase.cold_evidence['before_shutdown']['cost']
                            phase.assertEqual(after['count'], 1)
                            delta_energy = Decimal(after['energy_state']['state']) - Decimal(before['energy_state']['state'])
                            delta_cost = Decimal(after['native_value']) - Decimal(before['native_value'])
                            phase.assertGreater(delta_energy, 0)
                            phase.assertGreater(delta_cost, 0)
                            phase.assertLess(abs(delta_cost - delta_energy * Decimal('0.75')), Decimal('0.000000001'))
                        except (AssertionError, InvalidOperation, KeyError, TypeError) as error:
                            phase._measurement_failure('before_shutdown', error)
                if ws is not None:
                    await ws.close()
        class ClientWitness:
            async def close(self):
                if client is not None:
                    await client.close()
        return observed, SocketWitness(), ClientWitness()

    async def test_phase(self):
        primary = None
        try:
            if self.phase == 'resume':
                await self._await_cold_readiness()
            await self._capture('startup')
            await super().test_phase()
            if self.phase == 'resume':
                await self._settle_cold_sum()
                try:
                    await self._capture('before_cost_stimulus')
                    before = self.cold_evidence['before_cost_stimulus']['cost']
                    self.assertEqual(before['count'], 1)
                    energy_before = Decimal(before['energy_state']['state'])
                    cost_before = Decimal(before['native_value'])
                    await self._pulse_light(255)
                    await self._capture('after_cost_stimulus')
                    after = self.cold_evidence['after_cost_stimulus']['cost']
                    self.assertEqual(after['count'], 1)
                    delta_energy = Decimal(after['energy_state']['state']) - energy_before
                    delta_cost = Decimal(after['native_value']) - cost_before
                    self.assertGreater(delta_energy, 0)
                    self.assertGreater(delta_cost, 0)
                    self.assertLess(abs(delta_cost - delta_energy * Decimal('0.75')), Decimal('0.000000001'))
                    self.cold_evidence['actual_cost_delta'] = {'energy_kwh': str(delta_energy),
                        'price_per_kwh': '0.75', 'cost': str(delta_cost)}
                except (AssertionError, InvalidOperation, KeyError, TypeError) as error:
                    self._measurement_failure('after_inverse_stimulus', error)
                if self.mode == 'migration':
                    try:
                        from test_r9_cost_statistics_oracle import calculate
                        forward = json.loads(Path('/qa/cold-mixed-migration-forward.json').read_text())
                        cost_id = forward['before']['cost']['entity_id']
                        prior = forward['before']['statistics'][cost_id]
                        await self._capture('before_cost_statistics')
                        sampled = self.cold_evidence['before_cost_statistics']['statistics'][cost_id]
                        self.assertTrue(prior['short'])
                        self.assertTrue(sampled['short'])
                        await self._wait_cost_point(float(sampled['short'][-1]['start_ts']))
                        await self._capture('after_cost_statistics')
                        observed = self.cold_evidence['after_cost_statistics']['statistics'][cost_id]
                        self.assertEqual(prior['metadata']['id'], observed['metadata']['id'])
                        self.assertIsNone(self.cold_evidence['after_cost_statistics']['statistics'][
                            'sensor.synthetic_new_sum_energy_cost'])
                        for table in ('short', 'full'):
                            rows = {row['id']: row for row in observed[table]}
                            self.assertTrue(all(rows.get(row['id']) == row for row in prior[table]))
                        initial, final = prior['short'][-1], observed['short'][-1]
                        oracle = calculate(Path('/qa/cold-mixed-migration-resume-after_cost_statistics.sqlite'),
                                           cost_id, initial, final)
                        self.cold_evidence['native_cost_statistics_oracle'] = oracle
                        self.assertGreater(Decimal(final['sum']), Decimal(initial['sum']))
                        self.assertGreater(Decimal(final['state']), 0)
                        self.assertGreater(oracle['valid_states'], 0)
                        self.assertGreaterEqual(oracle['native_resets'], 2)
                        self.assertLess(abs(Decimal(oracle['calculated_sum']) - Decimal(oracle['recorder_sum'])),
                                        Decimal('0.000000001'))
                    except (AssertionError, InvalidOperation, KeyError, TypeError, TimeoutError) as error:
                        self._measurement_failure('cost_statistics', error)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                operation = self.cold_evidence.get('operation') or (self.meta or {}).get('operation')
                fingerprint = self.cold_evidence.get('fingerprint') or (self.meta or {}).get('fingerprint')
                if operation and fingerprint:
                    async def refresh(): return None
                    try:
                        self.cold_evidence['final_inspect'] = await self._runtime().recovery_inspect(
                            self.f.hass, operation, fingerprint, guard=lambda: True, refresh=refresh)
                    except Exception as error:
                        self.cold_evidence['final_inspect_error'] = type(error).__name__
                await self._capture('final')
            except BaseException as error:
                self.cold_evidence['final_error'] = type(error).__name__
                Path('/qa/cold-mixed-' + self.mode + '-' + self.phase + '.json').write_text(
                    json.dumps(self.cold_evidence, sort_keys=True, default=str))
                if primary is None:
                    raise
        if self.phase == 'resume':
            failures = list(self.cold_evidence.get('measurement_failures', []))
            if self.mode == 'migration':
                forward = json.loads(Path('/qa/cold-mixed-migration-forward.json').read_text())
                failures += forward.get('measurement_failures', [])
            self.assertFalse(failures, failures)

    async def test_preflight(self):
        from homeassistant.components.recorder import get_instance
        self.preflight_only = True
        await self.f.hass.async_block_till_done()
        await self.sources[0].async_turn_on(brightness=255)
        await asyncio.sleep(2)
        await self.sources[0].async_turn_off()
        await self.f.hass.async_block_till_done()
        await asyncio.sleep(2)
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        await self.sum_energy._prev_state_store.persist_states()
        await self.f.hass.data[DATA_RESTORE_STATE].async_dump_states()
        await self._flush_documents()
        await get_instance(self.f.hass).async_block_till_done()
        request, _, _ = await self._transport()
        monitor = sys.monitoring
        slot = next(index for index in range(6) if monitor.get_tool(index) is None)
        monitor.use_tool_id(slot, 'r9 preflight native refusal')
        failures = self.cold_evidence['native_failures'] = []
        def observe(code, offset, error):
            if '/custom_components/ha_entity_renamer/' not in code.co_filename:
                return
            value = str(error)
            reason = value if re.fullmatch(r'[a-z][a-z0-9_]{2,119}', value) else None
            row = {'file': Path(code.co_filename).name, 'function': code.co_name,
                   'line': next((n for a, b, n in code.co_lines() if a <= offset < b), None),
                   'type': type(error).__name__, 'code': reason}
            if row not in failures:
                failures.append(row)
                del failures[:-100]
        monitor.register_callback(slot, monitor.events.RAISE, observe)
        monitor.set_events(slot, monitor.events.RAISE)
        try:
            reply = await request(1, 'prepare', entities=[
                {'entity_id': self.sources[0].entity_id, 'new_entity_id': 'light.public_static_target'},
                {'entity_id': self.sum_energy.entity_id, 'new_entity_id': 'sensor.synthetic_new_sum_energy'}])
            self.assertTrue(reply.get('success'), reply)
            cost = self.cold_evidence['prepare']['cost']
            self.assertEqual(cost['count'], 1)
            self.assertGreater(Decimal(cost['energy_state']['state']), 0)
            self.assertGreater(Decimal(cost['native_value']), 0)
        finally:
            monitor.set_events(slot, 0)
            monitor.register_callback(slot, monitor.events.RAISE, None)
            monitor.free_tool_id(slot)
            await self._capture('final')

    async def test_native_cost_rebind(self):
        from homeassistant.components.energy.data import async_get_manager
        from homeassistant.helpers import entity_registry as er
        hass = self.f.hass
        old = self.sum_energy.entity_id
        new = 'sensor.r9_native_cost_rebind'
        try:
            await self.sources[0].async_turn_on(brightness=255)
            await asyncio.sleep(2)
            await self.sources[0].async_turn_off()
            await hass.async_block_till_done()
            await asyncio.sleep(2)
            if getattr(self, 'with_cost_statistics', False):
                await self._wait_cost_point(None)
            await self._capture('before')
            before = self.cold_evidence['before']['cost']
            self.assertEqual(before['count'], 1)
            self.assertGreater(Decimal(before['native_value']), 0)
            self.assertIsNotNone(before['registry_id'])
            source_uuid = self.sum_energy.registry_entry.id
            if getattr(self, 'pre_remove_cost', False):
                from homeassistant.components.energy.sensor import EnergyCostSensor
                from homeassistant.helpers.entity_platform import async_get_platforms
                costs = [entity for platform in async_get_platforms(hass, 'energy')
                         for entity in platform.entities.values() if type(entity) is EnergyCostSensor]
                self.assertEqual(len(costs), 1)
                await costs[0].async_remove()
                self.cold_evidence['removed_native_cost'] = str(costs[0].native_value)
                await self._capture('native_removed')
            registry = er.async_get(hass)
            registry.async_update_entity(old, new_entity_id=new)
            await hass.async_block_till_done()
            self.assertEqual(registry.async_get(new).id, source_uuid)
            if getattr(self, 'inspect_cost_stores', False):
                await self._flush_documents()
            manager = await async_get_manager(hass)
            preferences = deepcopy(manager.data)
            preferences['energy_sources'][0]['stat_energy_from'] = new
            preferences['device_consumption'][0]['stat_consumption'] = new
            await manager.async_update(preferences)
            await manager._store.async_save(manager.data)
            await hass.async_block_till_done()
            if getattr(self, 'inspect_cost_stores', False):
                from homeassistant.helpers.json import _orjson_bytes_default_encoder
                details = {}
                for core in self.f.managers:
                    store = core._store
                    disk = json.loads(Path(store.path).read_bytes())['data']
                    memory = json.loads(_orjson_bytes_default_encoder(core._data_to_save()))
                    details[store.key] = {'pending': bool(store._data is not None or
                        store._delay_handle is not None or store._unsub_final_write_listener is not None),
                        'memory_equals_disk': memory == disk}
                    if store.key == 'core.entity_registry':
                        before_rows = {row['id']: row for row in disk['entities']}
                        after_rows = {row['id']: row for row in memory['entities']}
                        details[store.key]['changes'] = [
                            {'entity_id': after_rows.get(key, before_rows.get(key, {})).get('entity_id'),
                             'fields': sorted(field for field in set(before_rows.get(key, {})) |
                                              set(after_rows.get(key, {}))
                                              if before_rows.get(key, {}).get(field) != after_rows.get(key, {}).get(field))}
                            for key in before_rows.keys() | after_rows.keys()
                            if before_rows.get(key) != after_rows.get(key)]
                self.cold_evidence['native_core_stores_after_rebind'] = details
                from custom_components.ha_entity_renamer.powercalc_energy_receipts import _cost_registry_projection
                entity_store = registry._store
                old_document = json.loads(Path(entity_store.path).read_bytes())
                new_document = deepcopy(old_document)
                new_document['data'] = json.loads(_orjson_bytes_default_encoder(registry._data_to_save()))
                changes = _cost_registry_projection(old_document, new_document,
                    [{'registry_id': before['registry_id'], 'entity_id': before['entity_id'],
                      'after_source': new}])
                self.assertEqual(len(changes), 1)
                await self._flush_documents()
                self.assertEqual(json.loads(Path(entity_store.path).read_bytes()), new_document)
                self.cold_evidence['native_cost_registry_projection_verified'] = True
            await self._capture('after')
            after = self.cold_evidence['after']['cost']
            self.assertEqual(after['energy_id'], new)
            self.assertEqual(after['count'], 1)
            self.assertEqual(after['registry_id'], before['registry_id'])
            self.assertEqual(after['unique_id'], before['unique_id'])
            self.assertEqual(after['entity_id'], before['entity_id'])
            self.assertEqual(after['actual_ids'], [before['entity_id']])
            if getattr(self, 'with_cost_statistics', False):
                self.assertEqual(before['state']['attributes']['state_class'], 'total')
                self.assertNotEqual(before['state']['attributes']['last_reset'],
                                    after['state']['attributes']['last_reset'])
            if getattr(self, 'pre_remove_cost', False):
                await self.sources[0].async_turn_on(brightness=255)
                await asyncio.sleep(2)
                await self.sources[0].async_turn_off()
                await hass.async_block_till_done()
                await asyncio.sleep(2)
                await self._capture('stimulus')
                self.assertEqual(self.cold_evidence['stimulus']['cost']['count'], 1)
                if getattr(self, 'with_cost_statistics', False):
                    initial = self.cold_evidence['before']['statistics'][before['entity_id']]
                    old_point = initial['short'][-1]
                    await self._wait_cost_point(float(old_point['start_ts']))
                    await self._capture('recorded')
                    latest = self.cold_evidence['recorded']['statistics'][before['entity_id']]
                    self.assertEqual(initial['metadata'], latest['metadata'])
                    for table in ('short', 'full'):
                        by_id = {row['id']: row for row in latest[table]}
                        self.assertTrue(all(by_id.get(row['id']) == row for row in initial[table]))
                    point = latest['short'][-1]
                    expected = (Decimal(old_point['sum']) +
                                Decimal(self.cold_evidence['removed_native_cost']) - Decimal(old_point['state']) +
                                Decimal(point['state']))
                    actual = Decimal(point['sum'])
                    self.assertGreater(Decimal(point['state']), 0)
                    self.cold_evidence['cost_statistics_check'] = {
                        'initial_sum': old_point['sum'], 'initial_state': old_point['state'],
                        'old_final': self.cold_evidence['removed_native_cost'], 'new_state': point['state'],
                        'expected_sum': str(expected), 'actual_sum': str(actual)}
                    self.assertLess(abs(expected - actual), Decimal('0.000000001'))
        finally:
            await self._capture('final')

    async def test_native_cost_rebind_after_remove(self):
        self.pre_remove_cost = True
        await self.test_native_cost_rebind()

    async def test_native_cost_store_effect(self):
        self.pre_remove_cost = True
        self.inspect_cost_stores = True
        await self.test_native_cost_rebind()

    async def test_native_cost_rebind_recorded(self):
        self.pre_remove_cost = True
        self.with_cost_statistics = True
        await self.test_native_cost_rebind()

    async def _wait_cost_point(self, after_start):
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.db_schema import StatisticsMeta, StatisticsShortTerm
        from homeassistant.components.recorder.util import session_scope
        from sqlalchemy import select
        recorder = get_instance(self.f.hass)
        cost_id = 'sensor.public_sum_energy_cost'
        def read():
            with session_scope(session=recorder.get_session(), read_only=True) as session:
                meta = session.execute(select(StatisticsMeta).where(
                    StatisticsMeta.statistic_id == cost_id)).scalar_one_or_none()
                if meta is None:
                    return False
                points = session.execute(select(StatisticsShortTerm).where(
                    StatisticsShortTerm.metadata_id == meta.id).order_by(StatisticsShortTerm.start_ts)).scalars().all()
                return bool(points and (after_start is None or points[-1].start_ts > after_start))
        async with asyncio.timeout(600):
            while not await recorder.async_add_executor_job(read):
                await asyncio.sleep(2)


@unittest.skipUnless(cold.backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class EnergyContinuityMixedColdNative(unittest.TestCase):
    def test_native_cost_rebind_probe(self):
        self._run_preflight_case('test_native_cost_rebind')

    def test_native_cost_rebind_after_remove_probe(self):
        self._run_preflight_case('test_native_cost_rebind_after_remove')

    def test_native_cost_store_effect_probe(self):
        self._run_preflight_case('test_native_cost_store_effect')

    def test_native_cost_rebind_recorded_probe(self):
        self._run_preflight_case('test_native_cost_rebind_recorded')

    def test_mixed_cold_preflight(self):
        self._run_preflight_case('test_preflight')

    def _run_preflight_case(self, selected):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='r9-cold-preflight-', dir=parent) as directory:
            root = Path(directory) / 'fixture'
            root.mkdir()
            env = dict(os.environ, RENAMER_POWER_COLD_CONFIG=str(root), RENAMER_POWER_COLD_MODE='migration',
                       RENAMER_POWER_COLD_META=str(Path(directory) / 'unused.json'),
                       RENAMER_POWER_COLD_GROUP_ONLY='0', RENAMER_POWER_COLD_PHASE='forward')
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                'MixedPowercalcColdPhase.' + selected], env=env, capture_output=True, text=True, timeout=1200)
            Path('/qa/r9-cold-preflight.stderr').write_text(result.stderr)
            Path('/qa/r9-cold-preflight.stdout').write_text(result.stdout)
            if result.returncode:
                shutil.copytree(root, '/qa/r9-cold-preflight-failed-config')
                print(result.stderr, flush=True)
            self.assertEqual(result.returncode, 0)

    def test_mixed_cold_and_fresh_inverse(self):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='r9-mixed-cold-', dir=parent) as directory:
            evidence = Path('/qa/r9-mixed-cold-evidence') / Path(directory).name
            evidence.mkdir(parents=True, exist_ok=False)
            for mode in ('control', 'migration'):
                root = Path(directory) / mode
                root.mkdir()
                env = dict(os.environ, RENAMER_POWER_COLD_CONFIG=str(root), RENAMER_POWER_COLD_MODE=mode,
                           RENAMER_POWER_COLD_META=str(Path(directory) / (mode + '-oracle.json')),
                           RENAMER_POWER_COLD_GROUP_ONLY='0')
                for phase, expected in (('forward', 73), ('resume', 0)):
                    env['RENAMER_POWER_COLD_PHASE'] = phase
                    result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                        'MixedPowercalcColdPhase.test_phase'], env=env, capture_output=True,
                        text=True, timeout=3600)
                    (evidence / (mode + '-' + phase + '.stdout')).write_text(result.stdout)
                    (evidence / (mode + '-' + phase + '.stderr')).write_text(result.stderr)
                    oracle = Path(directory) / (mode + '-oracle.json')
                    if oracle.exists():
                        shutil.copyfile(oracle, evidence / (mode + '-oracle.json'))
                    if result.returncode != expected:
                        shutil.copytree(root, evidence / (mode + '-' + phase + '-failed-config'))
                        print(result.stderr, flush=True)
                    self.assertEqual(result.returncode, expected)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(EnergyContinuityMixedColdNative)


if __name__ == '__main__':
    unittest.main()
