"""Native Core 11 / Powercalc 1.27, normal stop and fresh inverse."""
import asyncio
from contextlib import closing
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

from test_energy_continuity_mixed_cold_native import MixedPowercalcColdPhase
from test_receipted_helper_native import NativeReceiptedHelperTests
from test_powercalc_profile_energy_native import PowercalcMixedGraphNativeTests
from test_powercalc_public_cold_native import PowercalcColdPhase
from test_r9_cost_statistics_oracle import calculate


FIRST = 'graph-first_energy'
SECOND = 'graph-second_energy'
PRICE = Decimal('0.75')
PSS = 'powercalc_group'
RESTORE = 'core.restore_state'
FOREIGN = 'sensor.unrelated_group'
ACTOR = 'core11-mixed-energy'
CORE_VERSIONS = ('2026.11.0', '2026.11.1')
QA = Path('/qa')


class MixedPhase(PowercalcColdPhase):
    _add_graph_lights = PowercalcMixedGraphNativeTests._add_graph_lights
    _load_graph = PowercalcMixedGraphNativeTests._load_graph
    async def _startup_services(self, hass, root):
        import socket
        from homeassistant import auth
        from homeassistant.components.http.config import async_get_and_load_store
        from homeassistant.setup import async_setup_component

        self.with_yaml = False
        await NativeReceiptedHelperTests._before_start(self, hass, root)
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.assertTrue(await async_setup_component(hass, 'http', {'http': {
            'server_host': '127.0.0.1', 'server_port': port}}))
        store = await async_get_and_load_store(hass)
        if store.pending is not None:
            await store.async_promote_pending()
        self.assertTrue(await async_setup_component(hass, 'config', {}))

    _flush_documents = PowercalcMixedGraphNativeTests._flush_documents

    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        from homeassistant.setup import async_setup_component
        from types import SimpleNamespace

        self.assertIn(__version__, CORE_VERSIONS)
        self.core111 = __version__ == '2026.11.1'
        self.phase = os.environ['RENAMER_POWER_COLD_PHASE']
        root = Path(os.environ['RENAMER_POWER_COLD_CONFIG']).resolve(strict=True)
        self.meta_path = QA / 'core11-mixed-manifest.json'
        self.meta = json.loads(self.meta_path.read_text()) if self.phase == 'resume' else {}
        self.evidence = {'pid': os.getpid(), 'phase': self.phase, 'core': __version__}
        self.addCleanup(self._preserve_report)
        if self.phase == 'resume':
            self.assertNotEqual(self.meta['pid'], os.getpid())
            self.assertEqual(self._files(root), self.meta['files'])
            self.installed_component = root / 'custom_components/ha_entity_renamer'
            self.public_source_sha256 = self.meta['component_sha256']
        hass = HomeAssistant(str(root))
        self.addAsyncCleanup(hass.async_stop)
        if self.phase == 'forward':
            (root / 'configuration.yaml').write_bytes(b'')
            await self._before_loader(hass, root)
            card = 'www/ha-entity-renamer.js'
            source = Path(__file__).resolve().parents[1] / 'ha-entity-renamer.js'
            target = self.installed_component / card
            target.parent.mkdir()
            shutil.copyfile(source, target)
            self.public_source_sha256[card] = hashlib.sha256(target.read_bytes()).hexdigest()
            shutil.copytree(os.environ['POWERCALC_COMPONENT_DIR'], root / 'custom_components/powercalc',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await loader.async_get_custom_components(hass)
        self.assertIsNot(await async_load_base_functionality(hass), False)
        self.f = SimpleNamespace(hass=hass, root=root, version=__version__,
                                 entity=er.async_get(hass), device=dr.async_get(hass))
        self.f.managers = (self.f.entity, self.f.device, hass.config_entries)
        if self.phase == 'forward':
            await self._before_start(hass, root)
        else:
            await self._startup_services(hass, root)
        self.assertTrue(await async_setup_component(hass, 'homeassistant', {}))
        if self.phase == 'forward':
            await PowercalcMixedGraphNativeTests._setup_real_vendor(self)
            await self._price()
            await self._configure_consumers()
        else:
            await self._add_graph_lights()
            await self._load_graph()
            await self._price()
            self.assertTrue(await async_setup_component(hass, 'ha_entity_renamer', {}))
            self.backend_entry_id = self.meta['backend_entry_id']
            self.entry = hass.config_entries.async_get_entry(self.meta['pc_entry'])
        self.addAsyncCleanup(self._final_capture)
        await self._load_consumers()
        await hass.async_start()
        await hass.async_block_till_done()
        # These lights are driven solely by fixture actions after startup.
        for source in self.sources:
            await source.async_turn_off()
        await hass.async_block_till_done()
        self.assertEqual(self.sources[2].entity_id,
                         'light.graph_model_target' if self.phase == 'resume' else 'light.graph_model')
        if self.phase == 'forward':
            await self._flush_documents(initialize=True)
        else:
            self.cold_evidence = self.evidence
            await MixedPowercalcColdPhase._await_cold_readiness(self)

    def _network_evidence(self):
        from ordinary_network_scope import native_dummy_mdns
        return native_dummy_mdns(self.f.hass)

    def _files(self, root):
        result = {}
        for path in sorted([root / 'configuration.yaml', root / 'graph_scripts.yaml',
                            *((root / '.storage').glob('*'))]):
            if path.is_file():
                info = path.stat()
                result[str(path.relative_to(root))] = [
                    info.st_dev, info.st_ino, hashlib.sha256(path.read_bytes()).hexdigest()]
        return result

    def _storage(self, name):
        return json.loads((self.f.root / '.storage' / name).read_bytes())['data']

    def _graph(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        return {entity.unique_id: entity for platform in async_get_platforms(self.f.hass, 'powercalc')
                for entity in platform.entities.values()}

    async def _price(self):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.helpers.entity_component import EntityComponent

        class Tariff(SensorEntity):
            _attr_name = 'Fixture shared price'
            _attr_unique_id = 'fixture-shared-price'
            _attr_native_value = PRICE
            _attr_native_unit_of_measurement = 'EUR/kWh'
            _attr_should_poll = False

        price = Tariff()
        component = EntityComponent(logging.getLogger(__name__), 'sensor', self.f.hass, timedelta(seconds=60))
        await component.async_add_entities([price])
        self.tariff = price

    async def _configure_consumers(self):
        import probatio
        from homeassistant.components.energy.data import ENERGY_SOURCE_SCHEMA, async_get_manager
        from homeassistant.components.integration.config_flow import ConfigFlowHandler as IntegrationConfigFlow
        from homeassistant.config_entries import ConfigEntry

        hass = self.f.hass
        graph = self._graph()
        groups = [graph[key].entity_id for key in (FIRST, SECOND)]
        power = graph['graph-fixed'].entity_id
        manager = await async_get_manager(hass)
        preferences = manager.default_preferences()
        preferences['energy_sources'] = probatio.Schema(ENERGY_SOURCE_SCHEMA)([
            {'type': 'grid', 'stat_energy_from': entity_id, 'stat_energy_to': None,
             'stat_cost': None, 'entity_energy_price': self.tariff.entity_id,
             'number_energy_price': None, 'cost_adjustment_day': 0.0} for entity_id in groups])
        preferences['device_consumption'] = [{'stat_consumption': entity_id} for entity_id in groups]
        await manager.async_update(preferences)
        await manager._store.async_save(manager.data)
        options = {'source': power, 'name': 'Graph integral', 'unit_prefix': 'k',
                   'unit_time': 'h', 'method': 'left', 'round': 9}
        entry = ConfigEntry(domain='integration', title=options['name'], data={}, options=options,
            discovery_keys=MappingProxyType({}), source='user', version=IntegrationConfigFlow.VERSION,
            minor_version=IntegrationConfigFlow.MINOR_VERSION, subentries_data=[],
            unique_id='integration-graph')
        await hass.config_entries.async_add(entry)
        self.assertEqual(entry.state.value, 'loaded')
        group = groups[0]
        group_power = graph['graph-first'].entity_id
        (self.f.root / 'graph_scripts.yaml').write_text(
            'graph_consumer:\n  sequence:\n    - event: graph_consumer_read\n'
            f'      event_data:\n        kwh: "{{{{ states(\'{group}\') }}}}"\n'
            + (f'        watts: "{{{{ states(\'{group_power}\') }}}}"\n' if self.core111 else ''))
        with (self.f.root / 'configuration.yaml').open('a') as stream:
            stream.write('\nscript: !include graph_scripts.yaml\n')

    async def _load_consumers(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component

        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'energy', {}))
        self.assertTrue(await async_setup_component(hass, 'script', await async_hass_config_yaml(hass)))
        await hass.async_block_till_done()
        await hass.services.async_call('script', 'reload', {}, blocking=True)
        await hass.async_block_till_done()

    def _preserve_report(self):
        (QA / ('core11-mixed-' + self.phase + '.json')).write_text(
            json.dumps(self.evidence, sort_keys=True, default=str))

    async def _final_capture(self):
        if self.evidence.get('stopped'):
            return
        try:
            await self._capture('final')
        except Exception as error:
            self.evidence['final_capture_error'] = repr(error)
            raise

    async def _capture(self, label):
        from homeassistant.components.recorder import get_instance

        hass, root = self.f.hass, self.f.root
        target = QA / f'core11-mixed-{self.phase}-{label}.sqlite'

        def backup():
            with closing(sqlite3.connect((root / 'fixture.db').as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(target)) as destination:
                    source.backup(destination)

        self.evidence[label] = image = {'files': self._files(root)}
        await get_instance(hass).async_block_till_done()
        await hass.async_add_executor_job(backup)
        image['network'] = self._network_evidence()
        image['entities'] = {key: [entity.entity_id, entity.registry_entry.id,
                                    str(getattr(entity, '_native_value_exact', None))]
                             for key, entity in self._graph().items()}
        for name in ('energy', PSS, RESTORE, 'core.entity_registry', 'core.config_entries',
                     'core.device_registry'):
            path = root / '.storage' / name
            if path.exists():
                shutil.copyfile(path, QA / f'core11-mixed-{self.phase}-{label}.{name}')
        return target

    def _costs(self):
        from homeassistant.components.energy.sensor import EnergyCostSensor
        from homeassistant.helpers.entity_platform import async_get_platforms

        hass = self.f.hass
        entities = [entity for platform in async_get_platforms(hass, 'energy')
                    for entity in platform.entities.values() if type(entity) is EnergyCostSensor]
        keys = hass.data['energy']['cost_sensors']
        self.assertEqual(len(entities), 2)
        self.assertEqual(set(keys.values()), {entity.entity_id for entity in entities})
        return {key: next(entity for entity in entities if entity.entity_id == value)
                for key, value in keys.items()}

    def _assert_price(self, name):
        self.assertEqual(Decimal(self.f.hass.states.get(name).state), PRICE)
        self.assertEqual(self.tariff.entity_id, name)
        energy = self._storage('energy')
        self.assertEqual([row['entity_energy_price'] for row in energy['energy_sources']], [name, name])
        self.assertEqual([row['number_energy_price'] for row in energy['energy_sources']], [None, None])
        self.assertEqual([row['stat_energy_from'] for row in energy['energy_sources']],
                         [row['stat_consumption'] for row in energy['device_consumption']])
        for cost in self._costs().values():
            self.assertEqual(cost._config['entity_energy_price'], name)

    async def _pulse(self, label):
        hass = self.f.hass
        meters = [self._graph()[key].entity_id for key in (FIRST, SECOND)]
        power = self._graph()['graph-fixed'].entity_id

        def read():
            costs = self._costs()
            return [(Decimal(hass.states.get(meter).state),
                     Decimal(str(costs[meter].native_value))) for meter in meters]

        before = read()
        fixed = self.sources[0]
        await fixed.async_turn_on(brightness=255)
        try:
            await hass.async_block_till_done()
            watts = Decimal(hass.states.get(power).state)
            self.assertEqual(watts, Decimal(5))
            if self.core111:
                graph = self._graph()
                group_power = graph['graph-first'].entity_id
                # The real LUT member draws standby power with its light off.
                standby = Decimal(hass.states.get(graph['graph-model'].entity_id).state)
                expected = watts + standby
                async with asyncio.timeout(10):
                    while Decimal(hass.states.get(group_power).state) != expected:
                        await asyncio.sleep(.05)
                observed = Decimal(str((await self._script_read())['watts']))
                self.assertEqual(observed, expected)
                self.evidence[label + '_script_on'] = {'group_id': group_power, 'watts': str(observed),
                                                       'leaf_watts': str(watts), 'standby': str(standby)}
            await asyncio.sleep(3)
        finally:
            await fixed.async_turn_off()
            await hass.async_block_till_done()
        async with asyncio.timeout(30):
            while True:
                after = read()
                deltas = [(energy - original, cost - prior)
                          for (energy, cost), (original, prior) in zip(after, before, strict=True)]
                if all(energy > 0 and cost > 0 for energy, cost in deltas):
                    break
                await asyncio.sleep(.2)
        for energy, cost in deltas:
            self.assertLess(abs(cost - energy * PRICE), Decimal('0.000000001'))
        self.evidence[label] = {'w': str(watts), 'kwh_cost': deltas, 'tariff': str(PRICE)}

        message = await self._script_read()
        self.assertEqual(Decimal(str(message['kwh'])), after[0][0])
        self.evidence[label]['script'] = message

    async def _script_read(self):
        hass = self.f.hass
        messages = []
        unlisten = hass.bus.async_listen('graph_consumer_read', lambda event: messages.append(event.data))
        try:
            await hass.services.async_call('script', 'graph_consumer', {}, blocking=True)
            await hass.async_block_till_done()
        finally:
            unlisten()
        self.assertEqual(len(messages), 1)
        return messages[0]

    async def _point(self, entity_id, after=None):
        from homeassistant.components.recorder import get_instance

        def latest():
            path = self.f.root / 'fixture.db'
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    'SELECT s.*, m.statistic_id FROM statistics_short_term s '
                    'JOIN statistics_meta m ON m.id=s.metadata_id '
                    'WHERE m.statistic_id=? ORDER BY s.start_ts DESC LIMIT 1',
                    (entity_id,)).fetchone()
                return dict(row) if row is not None else None

        recorder = get_instance(self.f.hass)
        async with asyncio.timeout(610):
            while True:
                point = await recorder.async_add_executor_job(latest)
                if point is not None and (after is None or point['start_ts'] > after):
                    return {key: str(value) for key, value in point.items()}
                await asyncio.sleep(2)

    def _assert_cost_restore(self, costs, *, stopped):
        by_uuid = {row.get('entity_registry_id'): row for row in self._storage(RESTORE)
                   if row.get('entity_registry_id') is not None}
        for cost in costs.values():
            row = by_uuid[cost.registry_entry.id]
            self.assertEqual('last_energy_sensor_state' in row['extra_data'], stopped)
            if stopped:
                self.assertEqual(row['extra_data']['last_energy_sensor_state']['entity_id'],
                                 cost._config['stat_energy_from'])

    async def _call(self, method, *args, **kwargs):
        async def refresh():
            self.guard()
        runtime = self._runtime()
        trace = []
        monitor = sys.monitoring
        tool = next(i for i in range(6) if monitor.get_tool(i) is None)
        monitor.use_tool_id(tool, 'Core 11 Energy fixture exceptions')
        from custom_components.ha_entity_renamer.transaction import Refused

        def raised(code, offset, error):
            if (isinstance(error, (Refused, ValueError, KeyError, AttributeError))
                    and '/custom_components/ha_entity_renamer/' in code.co_filename):
                trace.append((Path(code.co_filename).name, code.co_name, str(error)))

        monitor.register_callback(tool, monitor.events.RAISE, raised)
        monitor.set_events(tool, monitor.events.RAISE)
        try:
            return await getattr(runtime, method)(self.f.hass, *args, guard=self.guard,
                                                   refresh=refresh, **kwargs)
        finally:
            monitor.set_events(tool, 0)
            monitor.register_callback(tool, monitor.events.RAISE, None)
            monitor.free_tool_id(tool)
            self.evidence[method + '_trace'] = list(dict.fromkeys(trace))[-35:]

    def guard(self):
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id), self.entry)

    async def test_generated_stat_rate_pending_refuses_until_native_save(self):
        from copy import deepcopy
        import probatio
        from homeassistant.components.energy.data import ENERGY_SOURCE_SCHEMA, async_get_manager
        from homeassistant.helpers.entity_platform import async_get_platforms
        from custom_components.ha_entity_renamer.energy_native import NativeEnergy
        from custom_components.ha_entity_renamer.persistence import PersistenceError

        hass = self.f.hass
        manager = await async_get_manager(hass)
        native = NativeEnergy(hass, manager)
        original = await native.read()
        power_id = self._graph()['graph-fixed'].entity_id
        config = deepcopy(original.value)
        config['energy_sources'][0]['power_config'] = {'stat_rate_inverted': power_id}
        config['energy_sources'] = probatio.Schema(ENERGY_SOURCE_SCHEMA)(config['energy_sources'])
        await manager.async_update(config)
        await hass.async_block_till_done()
        await manager._store.async_save(manager.data)
        settled = await native.read()
        stat_rate = settled.value['energy_sources'][0]['stat_rate']
        power_sensor = [obj for platform in async_get_platforms(hass, 'energy')
                        for obj in platform.entities.values() if obj.entity_id == stat_rate]
        self.assertEqual(len(power_sensor), 1)
        self.assertEqual(power_sensor[0].registry_entry.platform, 'energy')
        uuid = power_sensor[0].registry_entry.id

        new_rate = 'sensor.graph_generated_rate_target'
        snapshot, plan = await native.plan({stat_rate: new_rate})
        with self.assertRaisesRegex(PersistenceError, 'energy_generated_identity_requires_plan'):
            native.prepare(snapshot, plan)
        self.assertEqual(await native.read(), settled)

        observed = self.f.entity.async_update_entity(stat_rate, new_entity_id=new_rate)
        await hass.async_block_till_done()
        self.assertEqual(observed.id, uuid)
        self.assertEqual(manager.data['energy_sources'][0]['stat_rate'], new_rate)
        self.assertEqual(self._storage('energy'), settled.value)
        with self.assertRaisesRegex(PersistenceError, 'energy_native_write_pending'):
            await native.read()
        self.assertGreater(manager._store._delay_handle.when() - hass.loop.time(), 55)
        self.evidence['stat_rate_pending'] = {'before': stat_rate, 'after': new_rate, 'uuid': uuid,
                                             'disk_unchanged': True}
        async with asyncio.timeout(75):
            while manager._store._data is not None or manager._store._write_lock.locked():
                await asyncio.sleep(.25)
        saved = await native.read()
        expected = deepcopy(settled.value)
        expected['energy_sources'][0]['stat_rate'] = new_rate
        self.assertEqual(saved.value, expected)
        self.assertNotEqual(saved.file_identity, settled.file_identity)
        self.evidence['stat_rate_native_save'] = True

        self.f.entity.async_update_entity(new_rate, new_entity_id=stat_rate)
        await hass.async_block_till_done()
        await manager.async_update(original.value)
        await manager._store.async_save(manager.data)
        self.assertEqual((await native.read()).value, original.value)

    async def _prime_counters(self):
        # Populate real native integration and group snapshots before any prepare.
        for source in self.sources:
            await source.async_turn_on(brightness=255)
        await self.f.hass.async_block_till_done()
        await asyncio.sleep(2)
        for source in self.sources:
            await source.async_turn_off()
        await self.f.hass.async_block_till_done()

    def _counter_seam(self):
        """Observe the actual unload and first publication without changing either."""
        from homeassistant.core import callback
        from custom_components.ha_entity_renamer import controlled_powercalc_handoff as handoff

        seam = self.evidence['counter_seam'] = {'removed': {}, 'first_numeric': {}}
        snapshot = handoff.snapshot_removed
        watch = handoff._watch_restored_counters

        def removed(hass, population, **kwargs):
            payload = snapshot(hass, population, **kwargs)
            for row in population.counters:
                entity = row.entity
                group = row.kind == 'group'
                exact = entity._native_value_exact if group else entity._state
                last = None if group else str(entity._last_valid_state)
                self.assertIs(type(exact), Decimal)
                seam['removed'][row.registry_id] = (str(exact), last)
            return payload

        def installed(hass, population):
            observed, stop = watch(hass, population)
            targets = {row['after_id']: row['uuid'] for row in population}

            @callback
            def publish(event):
                state = event.data.get('new_state')
                if state is not None and state.entity_id in targets:
                    try:
                        numeric = Decimal(state.state)
                    except ArithmeticError:
                        return
                    if numeric.is_finite():
                        seam['first_numeric'].setdefault(targets[state.entity_id], state.state)

            remove = hass.bus.async_listen('state_changed', publish)
            seam['restored'] = observed

            def cleanup():
                remove()
                stop()

            return observed, cleanup

        self.enterContext(patch.object(handoff, 'snapshot_removed', removed))
        self.enterContext(patch.object(handoff, '_watch_restored_counters', installed))

    def _assert_counter_seam(self):
        seam = self.evidence['counter_seam']
        restored = {row['uuid']: (row['value'], row['last_valid']) for row in seam['restored'].values()}
        self.assertEqual(len(restored), 6)
        self.assertEqual(restored, seam['removed'])
        self.assertEqual(set(restored), set(seam['first_numeric']))
        for uuid, value in seam['first_numeric'].items():
            self.assertEqual(Decimal(value), round(Decimal(restored[uuid][0]), 6), uuid)

    def _assert_graph_ids(self, original, mapping):
        graph = self._graph()
        self.assertEqual(set(graph), set(original))
        for key, (name, uuid, _) in original.items():
            entity = graph[key]
            self.assertEqual((entity.entity_id, entity.registry_entry.id),
                             (mapping.get(name, name), uuid), key)
        for group, leaf in (('graph-first', 'graph-model'), ('graph-second', 'graph-linear')):
            for suffix in ('', '_energy'):
                self.assertEqual(graph[group + suffix]._entities,
                                 {graph[key + suffix].entity_id for key in ('graph-fixed', leaf)})

    def _assert_foreign(self, expected):
        self.assertEqual(self._storage(PSS)[FOREIGN], expected)
        self.assertEqual({key: row.as_dict() for key, row in
                          self.f.hass.data['powercalc_group_storage'].states[FOREIGN].items()}, expected)

    async def test_flow(self):
        from homeassistant.core import State, callback
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE

        hass, root = self.f.hass, self.f.root
        graph = self._graph()
        restore = hass.data[DATA_RESTORE_STATE]
        pss = hass.data['powercalc_group_storage']
        if self.core111:
            from custom_components.ha_entity_renamer.native_automation_transition import NativeAutomationTransition
            from custom_components.ha_entity_renamer.transaction import Refused

            self.assertIsNone(hass.data.get('automation'))
            transition = NativeAutomationTransition(hass, allow_running=True)
            self.assertEqual(transition.original, ())
            transition.check_native()
            with self.assertRaises(Refused):
                await transition.prepare_preserving_enabled()
            self.evidence['absent_automation'] = 'empty transition; reload refused'
            self._counter_seam()
        if self.phase == 'forward':
            self.assertEqual(len(graph), 12)
            pss.set_entity_state(FOREIGN, 'sensor.unrelated_member',
                                 State('sensor.unrelated_member', '41.125'))
            await pss.persist_states()
            foreign = self._storage(PSS)[FOREIGN]
            if self.core111:
                await self._prime_counters()
            await self._pulse('baseline_pulse')
            costs = self._costs()
            cost_ids = {key: costs[graph[key].entity_id].entity_id for key in (FIRST, SECOND)}
            cost_baseline = {key: await self._point(name) for key, name in cost_ids.items()}
            first, second = graph[FIRST].entity_id, graph[SECOND].entity_id
            group_point = await self._point(first)
            price_id = self.tariff.entity_id
            mapping = {self.yaml_source: 'light.graph_model_target', first: 'sensor.graph_energy_target',
                       second: 'sensor.graph_second_energy_target', price_id: 'sensor.graph_price_target'}
            if self.core111:
                mapping.update({graph['graph-model'].entity_id: 'sensor.graph_leaf_power_target',
                                graph['graph-first'].entity_id: 'sensor.graph_group_power_target'})
            await restore.async_dump_states()
            self._assert_cost_restore(costs, stopped=False)
            await self._flush_documents()
            await self._capture('baseline')
            start = {key: [entity.entity_id, entity.registry_entry.id,
                           str(getattr(entity, '_native_value_exact', getattr(entity, '_state', None)))]
                     for key, entity in graph.items()}
            first_publications = {}

            @callback
            def publish(event):
                row = event.data.get('new_state')
                if row is not None and row.state not in ('unknown', 'unavailable'):
                    first_publications.setdefault(row.entity_id, row.state)

            review = await self._call('native_yaml_prepare', mapping, actor_id=ACTOR)
            op = (review['operation_id'], review['fingerprint'])
            self.evidence['review'] = review
            remove = hass.bus.async_listen('state_changed', publish)
            try:
                applied = await self._call('native_yaml_apply', *op, actor_id=ACTOR)
            finally:
                remove()
            self.evidence['apply'] = applied
            self.evidence['first_publications'] = first_publications
            self.assertEqual(applied['state'], 'applied', applied)
            self._assert_price(mapping[price_id])
            if self.core111:
                self._assert_counter_seam()
                self._assert_graph_ids(start, mapping)
                self._assert_foreign(foreign)
            after = self._graph()
            for key, (name, uuid, value) in start.items():
                entity = after[key]
                self.assertEqual((entity.entity_id, entity.registry_entry.id), (mapping.get(name, name), uuid))
                if key.endswith('_energy') and not self.core111:
                    actual = getattr(entity, '_native_value_exact', getattr(entity, '_state', None))
                    self.assertEqual(actual.as_tuple(), Decimal(value).as_tuple(), key)
                    self.assertEqual(Decimal(first_publications[entity.entity_id]),
                                     Decimal(hass.states.get(entity.entity_id).state), key)
            self.assertEqual(self._storage(PSS)[FOREIGN]['sensor.unrelated_member']['state'], '41.125')
            await restore.async_dump_states()
            self._assert_cost_restore(self._costs(), stopped=False)
            await self._capture('applied')
            await self._pulse('forward_pulse')
            await self._capture('before_stop')
            costs = self._costs()
            await hass.async_stop()
            self.evidence['stopped'] = True
            self._assert_cost_restore(costs, stopped=True)
            self.meta_path.write_text(json.dumps({
                'pid': os.getpid(), 'files': self._files(root), 'op': op, 'mapping': mapping,
                'graph': start, 'foreign': foreign, 'cost_baseline': cost_baseline, 'cost_ids': cost_ids,
                'cost_uuid': {row.entity_id: row.registry_entry.id for row in costs.values()},
                'first': first, 'second': second, 'group_point': group_point, 'price': price_id,
                'pc_entry': self.entry.entry_id, 'backend_entry_id': self.backend_entry_id,
                'component_sha256': self.public_source_sha256}, sort_keys=True))
            return

        meta = self.meta
        op = meta['op']
        self._assert_price(meta['mapping'][meta['price']])
        await self._capture('cold')
        if self.core111:
            self._assert_graph_ids(meta['graph'], meta['mapping'])
            self._assert_foreign(meta['foreign'])
        self.assertEqual({row.entity_id: row.registry_entry.id for row in self._costs().values()},
                         meta['cost_uuid'])
        for cost in self._costs().values():
            self.assertLess(abs(Decimal(str(cost.native_value))), Decimal('0.00001'))
        inspected = await self._call('recovery_inspect', *op)
        self.evidence['inspect'] = inspected
        self.assertEqual(inspected['state'], 'applied', inspected)
        self.assertIs(inspected['eligible'], True)
        reverse = await self._call('native_yaml_reverse', *op)
        self.evidence['reverse'] = reverse
        self.assertEqual(reverse['state'], 'rolled_back', reverse)
        self._assert_price(meta['price'])
        if self.core111:
            self._assert_counter_seam()
            self._assert_graph_ids(meta['graph'], {})
            self._assert_foreign(meta['foreign'])
            self.assertEqual({row.entity_id: row.registry_entry.id for row in self._costs().values()},
                             meta['cost_uuid'])
        self.assertEqual({key: (entity.entity_id, entity.registry_entry.id)
                          for key, entity in self._graph().items()},
                         {key: tuple(row[:2]) for key, row in meta['graph'].items()})
        await self._pulse('inverse_pulse')
        self.assertEqual(self._storage(PSS)[FOREIGN]['sensor.unrelated_member']['state'], '41.125')
        for filename in ('configuration.yaml', 'graph_scripts.yaml'):
            self.assertEqual(self._files(root)[filename][2],
                             json.loads((QA / 'core11-mixed-forward.json').read_text())['baseline']['files'][filename][2])
        after = await self._call('recovery_inspect', *op)
        self.evidence['final_inspect'] = after
        self.assertEqual((after['state'], after['reason'], after['eligible']),
                         ('rolled_back', 'restored_verified', False), after)
        after_pulse = {key: await self._point(name) for key, name in meta['cost_ids'].items()}
        points = {key: await self._point(name, float(after_pulse[key]['start_ts']))
                  for key, name in meta['cost_ids'].items()}
        database = await self._capture('recorded')
        for key, name in meta['cost_ids'].items():
            baseline, target = meta['cost_baseline'][key], points[key]
            self.assertEqual(baseline['metadata_id'], target['metadata_id'])
            evidence = calculate(database, name, baseline, target)
            self.evidence.setdefault('physical_cost', {})[key] = evidence
            self.assertGreater(evidence['valid_states'], 0)
            if self.core111:
                self.assertGreater(Decimal(evidence['calculated_sum']), 0)
                self.assertGreater(evidence['native_resets'], 0)
            self.assertLess(abs(Decimal(evidence['calculated_sum']) - Decimal(evidence['recorder_sum'])),
                            Decimal('0.000000001'))


class Core11MixedColdNative(unittest.TestCase):
    def test_native_core11_energy_stat_rate(self):
        self._run_phases(('forward',), 'test_generated_stat_rate_pending_refuses_until_native_save')

    def test_native_core11_mixed_cold_round_trip(self):
        self._run_phases(('forward', 'resume'), 'test_flow')

    def _run_phases(self, phases, method):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='core11-energy-', dir=parent) as directory:
            root = Path(directory)
            try:
                for phase in phases:
                    env = dict(os.environ, RENAMER_POWER_COLD_CONFIG=directory,
                               RENAMER_POWER_COLD_PHASE=phase, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
                    result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                        'MixedPhase.' + method], env=env, capture_output=True, text=True, timeout=2300)
                    (QA / ('core11-mixed-' + phase + '.stdout')).write_text(result.stdout)
                    (QA / ('core11-mixed-' + phase + '.stderr')).write_text(result.stderr)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertIn('Ran 1 test', result.stderr)
                    self.assertNotIn('skipped=', result.stderr)
            finally:
                shutil.copytree(root, QA / 'core11-mixed-final-config')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(Core11MixedColdNative)


if __name__ == '__main__':
    unittest.main()
