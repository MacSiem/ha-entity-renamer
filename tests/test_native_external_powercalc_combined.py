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
            'stat_energy_from': self.sum_energy.entity_id, 'stat_energy_to': None,
            'stat_cost': None, 'entity_energy_price': None, 'number_energy_price': 0.75,
            'cost_adjustment_day': 0.0}])
        await manager.async_update(data)
        await manager._store.async_save(manager.data)
        config = root / 'configuration.yaml'
        with config.open('a') as out:
            out.write('\nhomekit:\n  name: Synthetic PC Bridge\n  port: 21198\n'
                '  ip_address: 192.0.2.1\n  advertise_ip: 192.0.2.1\n'
                '  filter:\n    include_entities:\n      - ' + self.yaml_source + '\n'
                'utility_meter:\n  external_pc_meter:\n    source: ' + self.sum_energy.entity_id + '\n    cycle: daily\n')
        loaded = await async_hass_config_yaml(hass)
        for domain in ('energy', 'utility_meter', 'homekit'):
            self.assertTrue(await async_setup_component(hass, domain, loaded))
        await hass.async_block_till_done()

    async def test_external_powercalc_energy_public_flow(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.components.energy.data import async_get_manager
        hass = self.f.hass
        await hass.services.async_call('homekit', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        await self._flush_documents()
        old_light, new_light = self.yaml_source, 'light.external_pc_target'
        old_energy, new_energy = self.sum_energy.entity_id, 'sensor.external_pc_energy_target'
        mapping = {old_light: new_light, old_energy: new_energy}
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
        def meter():
            rows = [entity for platform in async_get_platforms(hass, 'utility_meter')
                    for entity in platform.entities.values() if getattr(entity, '_parent_meter', None) == 'external_pc_meter']
            self.assertEqual(len(rows), 1)
            return rows[0]
        original_meter = (meter().entity_id, meter().native_value, meter()._next_reset)
        runtime = self._runtime()
        async def refresh(): self.guard()
        async def transition(selected):
            review = await runtime.native_yaml_prepare(hass, selected, actor_id='external-pc-qa',
                guard=lambda: None, refresh=refresh)
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
        self.assertEqual((meter().entity_id, meter().native_value, meter()._next_reset), original_meter)
        self.assertEqual(meter()._sensor_source_id, new_energy)
        self.assertIsNotNone(meter()._collecting)
        self.assertEqual((aid(new_light), bridge._bridge_identity(entry)), (original_aid, identity))
        self.assertEqual((await async_get_manager(hass)).data['energy_sources'][0]['stat_energy_from'], new_energy)
        inspected = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(inspected['state'], 'applied', inspected)
        await transition({new: old for old, new in mapping.items()})
        self.assertEqual((aid(old_light), bridge._bridge_identity(entry)), (original_aid, identity))
        self.assertEqual((await async_get_manager(hass)).data['energy_sources'][0]['stat_energy_from'], old_energy)
