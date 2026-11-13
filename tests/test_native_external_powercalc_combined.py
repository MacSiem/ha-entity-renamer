"""Public mixed native PC/Energy, HomeKit and Utility lifecycle fixture."""
import json
import os
from copy import deepcopy
from pathlib import Path
import unittest

import test_controlled_powercalc_public_native as fixture


@unittest.skipUnless(fixture.cold.backend_fixture.activation.fixtures.HAS_HA, 'actual pinned HA required')
class ExternalPowercalcCombinedTests(fixture.ControlledPowercalcPublicNativeTests):
    async def _before_loader(self, hass, root):
        import shutil
        await super()._before_loader(hass, root)
        card = Path(__file__).resolve().parent.parent / 'ha-entity-renamer.js'
        target = root / 'custom_components/ha_entity_renamer/www/ha-entity-renamer.js'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(card, target)

    async def _startup_services(self, hass, root):
        from homeassistant import auth
        from homeassistant.setup import async_setup_component
        self.with_yaml = False
        try:
            await fixture.cold.receipted.NativeReceiptedHelperTests._before_start(self, hass, root)
        finally:
            self.with_yaml = True
        hass.auth = await auth.auth_manager_from_config(hass, [], [])
        self.assertTrue(await async_setup_component(hass, 'http', {}))
        self.assertTrue(await async_setup_component(hass, 'config', {}))

    async def _setup_vendor_forward(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        from homeassistant.components.energy.data import async_get_manager, ENERGY_SOURCE_SCHEMA
        import probatio
        await super()._setup_vendor_forward()
        hass, root = self.f.hass, self.f.root
        manager = await async_get_manager(hass)
        data = deepcopy(manager.data)
        data['energy_sources'] = probatio.Schema(ENERGY_SOURCE_SCHEMA)([{'type': 'grid',
            'stat_energy_from': source, 'stat_energy_to': None,
            'stat_cost': None, 'entity_energy_price': None, 'number_energy_price': 0.75,
            'cost_adjustment_day': 0.0} for source in (self.sum_energy.entity_id, self.static_energy.entity_id)])
        await manager.async_update(data)
        await manager._store.async_save(manager.data)
        self.assertTrue(await async_setup_component(hass, 'energy', {}))
        await hass.async_block_till_done()
        self.cost_sources = [hass.data['energy']['cost_sensors'][source]
                             for source in (self.sum_energy.entity_id, self.static_energy.entity_id)]
        from homeassistant.components.energy.sensor import EnergyCostSensor
        from homeassistant.helpers.entity_platform import async_get_platforms
        costs = [entity for platform in async_get_platforms(hass, 'energy') for entity in platform.entities.values()
                 if entity.entity_id in self.cost_sources]
        self.assertEqual(len(costs), 2)
        self.assertTrue(all(type(cost) is EnergyCostSensor for cost in costs))
        config = root / 'configuration.yaml'
        with config.open('a') as out:
            out.write('\nhomekit:\n  name: Synthetic PC Bridge\n  port: 21198\n'
                '  ip_address: 192.0.2.1\n  advertise_ip: 192.0.2.1\n'
                '  filter:\n    include_entities:\n      - ' + self.yaml_source + '\n'
                'utility_meter:\n  external_pc_meter:\n    source: ' + self.cost_sources[0] + '\n    cycle: daily\n'
                '  external_pc_meter_second:\n    source: ' + self.cost_sources[1] + '\n    cycle: daily\n')
        loaded = await async_hass_config_yaml(hass)
        for domain in ('utility_meter', 'homekit'):
            if domain == 'utility_meter' and domain in hass.config.components:
                from homeassistant.components.utility_meter import CONFIG_SCHEMA, DATA_UTILITY, async_setup
                self.assertFalse(hass.data[DATA_UTILITY])
                self.assertTrue(await async_setup(hass, CONFIG_SCHEMA(loaded)))
            else:
                self.assertTrue(await async_setup_component(hass, domain, loaded))
        await hass.async_block_till_done()

    async def test_external_powercalc_energy_public_flow(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.components.energy.data import async_get_manager
        hass = self.f.hass
        await hass.services.async_call('homekit', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        await self._flush_documents()
        # Compile native statistics before the reviewed history baseline, as in
        # the other real Powercalc fixtures. This avoids a clock-dependent first
        # statistics metadata creation during the transaction.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import StatisticsTask
        from homeassistant.util import dt as dt_util
        from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
        history = NativeRecorderGuard(hass)
        await history.async_synchronize()
        now = dt_util.utcnow()
        period = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
        get_instance(hass).queue_task(StatisticsTask(period, False))
        await history.async_synchronize()
        old_light, new_light = self.yaml_source, 'light.external_pc_target'
        old_energy, new_energy = self.sum_energy.entity_id, 'sensor.external_pc_energy_target'
        targets = ('sensor.external_utility_target', 'sensor.external_utility_second_target')
        mapping = {old_light: new_light, old_energy: new_energy, **dict(zip(self.cost_sources, targets))}
        original_ids = {old: self.f.entity.async_get(old).id for old in mapping}
        entry = hass.config_entries.async_entries('homekit')[0]
        bridge = self.module('native_homekit_filter') if hasattr(self, 'module') else __import__(
            'custom_components.ha_entity_renamer.native_homekit_filter', fromlist=['_bridge_identity'])
        identity = bridge._bridge_identity(entry)
        def aid(entity_id):
            rows = [aid for aid, obj in entry.runtime_data.homekit.bridge.accessories.items()
                    if getattr(obj, 'entity_id', None) == entity_id]
            self.assertEqual(len(rows), 1)
            return rows[0]
        original_aid = aid(old_light)
        def meter(name):
            rows = [entity for platform in async_get_platforms(hass, 'utility_meter')
                    for entity in platform.entities.values() if getattr(entity, '_parent_meter', None) == name]
            self.assertEqual(len(rows), 1)
            return rows[0]
        names = ('external_pc_meter', 'external_pc_meter_second')
        original_meters = [(meter(name).entity_id, meter(name).native_value, meter(name)._next_reset)
                           for name in names]
        def check_meter(name, source, baseline):
            from decimal import Decimal
            current = meter(name)
            self.assertEqual((current.entity_id, current._next_reset), (baseline[0], baseline[2]))
            if baseline[1] is not None:
                self.assertIsNotNone(current.native_value)
                self.assertGreaterEqual(Decimal(str(current.native_value)), Decimal(str(baseline[1])))
            elif current.native_value is not None:
                self.assertGreaterEqual(Decimal(str(current.native_value)), Decimal(0))
            self.assertEqual(current._sensor_source_id, source)
            self.assertIsNotNone(current._collecting)
        runtime = self._runtime()
        async def refresh(): pass
        async def transition(selected):
            review = await runtime.native_yaml_prepare(hass, selected, actor_id='external-pc-qa',
                guard=lambda: None, refresh=refresh)
            self.assertTrue({'homekit', 'utility_meter', 'powercalc', 'energy'} <= set(review['dependencies']['domains']))
            try:
                result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='external-pc-qa', guard=lambda: None, refresh=refresh)
            finally:
                record = await runtime.journal.load(review['operation_id'])
                if record is not None and os.environ.get('RENAMER_BATCH_EVIDENCE'):
                    path = Path(os.environ['RENAMER_BATCH_EVIDENCE']) / 'external-pc-journal.json'
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, 'w') as out:
                        json.dump({'status': record.status, 'reason': record.reason, 'phases': record.phases,
                                   'adapters': [step.adapter for step in record.plan.steps]}, out)
            self.assertEqual(result['state'], 'applied', result)
            return review
        review = await transition(mapping)
        self.assertEqual(self.f.entity.async_get(new_light).id, original_ids[old_light])
        self.assertEqual(self.f.entity.async_get(new_energy).id, original_ids[old_energy])
        for name, target, original in zip(names, targets, original_meters):
            check_meter(name, target, original)
        self.assertEqual((aid(new_light), bridge._bridge_identity(entry)), (original_aid, identity))
        self.assertEqual((await async_get_manager(hass)).data['energy_sources'][0]['stat_energy_from'], new_energy)
        inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(inspected['state'], 'applied', inspected)
        await transition({new: old for old, new in mapping.items()})
        for name, source, original in zip(names, self.cost_sources, original_meters):
            check_meter(name, source, original)
        self.assertEqual((aid(old_light), bridge._bridge_identity(entry)), (original_aid, identity))
        self.assertEqual((await async_get_manager(hass)).data['energy_sources'][0]['stat_energy_from'], old_energy)
