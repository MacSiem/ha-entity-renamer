"""Native Core 11 / Powercalc 1.27, normal stop and fresh inverse."""
import asyncio
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

from test_energy_continuity_mixed_cold_native import MixedPowercalcColdPhase
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
QA = Path('/qa')


class MixedPhase(PowercalcColdPhase):
    _add_graph_lights = PowercalcMixedGraphNativeTests._add_graph_lights
    _load_graph = PowercalcMixedGraphNativeTests._load_graph
    _startup_services = MixedPowercalcColdPhase._startup_services
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

        self.assertEqual(__version__, '2026.11.0')
        self.phase = os.environ['RENAMER_POWER_COLD_PHASE']
        root = Path(os.environ['RENAMER_POWER_COLD_CONFIG']).resolve(strict=True)
        self.meta_path = QA / 'core11-mixed-manifest.json'
        self.meta = json.loads(self.meta_path.read_text()) if self.phase == 'resume' else {}
        self.evidence = {'pid': os.getpid(), 'phase': self.phase}
        self.addCleanup(self._preserve_report)
        if self.phase == 'resume':
            self.assertNotEqual(self.meta['pid'], os.getpid())
            self.assertEqual(self._files(root), self.meta['files'])
            self.installed_component = root / 'custom_components/ha_entity_renamer'
            self.public_source_sha256 = self.meta['component_sha256']
        hass = HomeAssistant(str(root))
        self.addAsyncCleanup(hass.async_stop, force=True)
        if self.phase == 'forward':
            (root / 'configuration.yaml').write_bytes(b'')
            await self._before_loader(hass, root)
            card = 'www/ha-entity-renamer.js'
            source = Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer' / card
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
        await self._load_consumers()
        await hass.async_start()
        await hass.async_block_till_done()
        # These lights are driven solely by fixture actions after startup.
        for source in self.sources:
            await source.async_turn_off()
        await hass.async_block_till_done()
        if self.phase == 'forward':
            await self._flush_documents(initialize=True)
        else:
            self.cold_evidence = self.evidence
            await MixedPowercalcColdPhase._await_cold_readiness(self)
        self.addAsyncCleanup(self._final_capture)

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
        registered = self.f.entity.async_get_entity_id('sensor', 'sensor', price.unique_id)
        if registered:
            price.entity_id = registered
        component = EntityComponent(logging.getLogger(__name__), 'sensor', self.f.hass, timedelta(seconds=60))
        await component.async_add_entities([price])
        self.tariff = price

    async def _configure_consumers(self):
        import probatio
        from homeassistant.components.energy.data import ENERGY_SOURCE_SCHEMA, async_get_manager
        from homeassistant.components.integration.config_flow import ConfigFlowHandler as IntegrationConfigFlow
        from homeassistant.components.utility_meter.config_flow import ConfigFlowHandler as UtilityMeterConfigFlow
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
        for domain, flow, options in (
            ('integration', IntegrationConfigFlow, {'source': power, 'name': 'Graph integral',
             'unit_prefix': 'k', 'unit_time': 'h', 'method': 'left', 'round': 9}),
            ('utility_meter', UtilityMeterConfigFlow, {'source': groups[1], 'name': 'Graph utility',
             'cycle': 'none', 'offset': 0, 'tariffs': [], 'net_consumption': False,
             'delta_values': False, 'periodically_resetting': False, 'always_available': False}),
        ):
            entry = ConfigEntry(domain=domain, title=options['name'], data={}, options=options,
                discovery_keys=MappingProxyType({}), source='user', version=flow.VERSION,
                minor_version=flow.MINOR_VERSION, subentries_data=[], unique_id=domain + '-graph')
            await hass.config_entries.async_add(entry)
            self.assertEqual(entry.state.value, 'loaded', domain)
        group = groups[0]
        (self.f.root / 'graph_scripts.yaml').write_text(
            'graph_consumer:\n  sequence:\n    - event: graph_consumer_read\n'
            f'      event_data:\n        kwh: "{{{{ states(\'{group}\') }}}}"\n')
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
            with sqlite3.connect((root / 'fixture.db').as_uri() + '?mode=ro', uri=True) as source:
                with sqlite3.connect(target) as destination:
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

        messages = []
        unlisten = hass.bus.async_listen('graph_consumer_read', lambda event: messages.append(event.data))
        try:
            await hass.services.async_call('script', 'graph_consumer', {}, blocking=True)
            await hass.async_block_till_done()
        finally:
            unlisten()
        self.assertEqual(len(messages), 1)
        self.assertEqual(Decimal(str(messages[0]['kwh'])), after[0][0])

    async def _point(self, entity_id, after=None):
        from homeassistant.components.recorder import get_instance

        def latest():
            path = self.f.root / 'fixture.db'
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
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
        return await getattr(runtime, method)(self.f.hass, *args, guard=self.guard,
                                               refresh=refresh, **kwargs)

    def guard(self):
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id), self.entry)

    async def test_flow(self):
        from homeassistant.core import State, callback
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE

        hass, root = self.f.hass, self.f.root
        graph = self._graph()
        restore = hass.data[DATA_RESTORE_STATE]
        pss = hass.data['powercalc_group_storage']
        if self.phase == 'forward':
            self.assertEqual(len(graph), 12)
            pss.set_entity_state(FOREIGN, 'sensor.unrelated_member',
                                 State('sensor.unrelated_member', '41.125'))
            await pss.persist_states()
            await self._pulse('baseline_pulse')
            costs = self._costs()
            cost_ids = {key: costs[graph[key].entity_id].entity_id for key in (FIRST, SECOND)}
            cost_baseline = {key: await self._point(name) for key, name in cost_ids.items()}
            first, second = graph[FIRST].entity_id, graph[SECOND].entity_id
            group_point = await self._point(first)
            price_id = self.tariff.entity_id
            mapping = {self.yaml_source: 'light.graph_model_target', first: 'sensor.graph_energy_target',
                       price_id: 'sensor.graph_price_target'}
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
            after = self._graph()
            for key, (name, uuid, value) in start.items():
                entity = after[key]
                self.assertEqual((entity.entity_id, entity.registry_entry.id), (mapping.get(name, name), uuid))
                if key.endswith('_energy'):
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
            await hass.async_stop(force=True)
            self.evidence['stopped'] = True
            self._assert_cost_restore(costs, stopped=True)
            self.meta_path.write_text(json.dumps({
                'pid': os.getpid(), 'files': self._files(root), 'op': op, 'mapping': mapping,
                'graph': start, 'cost_baseline': cost_baseline, 'cost_ids': cost_ids,
                'cost_uuid': {row.entity_id: row.registry_entry.id for row in costs.values()},
                'first': first, 'second': second, 'group_point': group_point, 'price': price_id,
                'pc_entry': self.entry.entry_id, 'backend_entry_id': self.backend_entry_id,
                'component_sha256': self.public_source_sha256}, sort_keys=True))
            return

        meta = self.meta
        op = meta['op']
        self._assert_price(meta['mapping'][meta['price']])
        await self._capture('cold')
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
            self.assertLess(abs(Decimal(evidence['calculated_sum']) - Decimal(evidence['recorder_sum'])),
                            Decimal('0.000000001'))


class Core11MixedColdNative(unittest.TestCase):
    def test_native_core11_mixed_cold_round_trip(self):
        parent = os.environ.get('RENAMER_TEST_SCRATCH') or tempfile.gettempdir()
        with tempfile.TemporaryDirectory(prefix='core11-energy-', dir=parent) as directory:
            root = Path(directory)
            try:
                for phase in ('forward', 'resume'):
                    env = dict(os.environ, RENAMER_POWER_COLD_CONFIG=directory,
                               RENAMER_POWER_COLD_PHASE=phase, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
                    result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                        'MixedPhase.test_flow'], env=env, capture_output=True, text=True, timeout=2300)
                    (QA / ('core11-mixed-' + phase + '.stdout')).write_text(result.stdout)
                    (QA / ('core11-mixed-' + phase + '.stderr')).write_text(result.stderr)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertNotIn('skipped=', result.stderr)
            finally:
                shutil.copytree(root, QA / 'core11-mixed-final-config')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(Core11MixedColdNative)


if __name__ == '__main__':
    unittest.main()
