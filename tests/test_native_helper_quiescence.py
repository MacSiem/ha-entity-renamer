"""Actual isolated Threshold unload/own-write/setup experiment.

Run by the owner through FIFO on each installed HA runtime. No current-only
NativeHelperObservers/Persistence, fake callbacks, patches or running HA API.
This probes a possible migration primitive; it is not a transaction/AC proof.
"""
from copy import deepcopy
from datetime import timedelta
import importlib
import json
import logging
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import unittest

import test_native_input_baseline as fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class NativeHelperQuiescenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.bootstrap import async_load_base_functionality
        from homeassistant.config_entries import ConfigEntries
        from homeassistant.const import __version__
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        directory = fixtures.scratch()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        (root / 'configuration.yaml').write_bytes(b'script: {}\n')
        hass = HomeAssistant(str(root))
        self.addAsyncCleanup(hass.async_stop, force=True)
        # Some consumers alias asyncSetUp without inheriting this class. Their
        # historical path remains a noop unless they opt in to this test hook.
        before_loader = getattr(self, '_before_loader', None)
        if before_loader is not None:
            await before_loader(hass, root)
        loader.async_setup(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await loader.async_get_custom_components(hass)
        self.assertIsNot(await async_load_base_functionality(hass), False)
        await self._before_start(hass, root)
        await hass.async_start()
        self.f = SimpleNamespace(hass=hass, root=root, version=__version__,
            entity=er.async_get(hass), device=dr.async_get(hass))
        self.f.managers = (self.f.entity, self.f.device, hass.config_entries)
        self.modules = {name: importlib.import_module(fixtures.PACKAGE + '.' + name)
                        for name in ('native_registry', 'native_registry_transition',
                                     'helper_native', 'helper_transaction',
                                     'native_config_transition')}

    async def _before_loader(self, hass, root):
        """Optional public component installation before native loader discovery."""

    async def _before_start(self, hass, root):
        """Optional actual-component bootstrap for derived integration probes."""

    async def _flush_documents(self, *, initialize=False):
        """Actual native Core Stores; independently read every full envelope."""
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        f = self.f
        await f.hass.async_block_till_done()
        for manager in f.managers:
            store = manager._store
            if (initialize or store._data is not None or store._delay_handle is not None
                    or store._unsub_final_write_listener is not None):
                await store.async_save(manager._data_to_save())
        await f.hass.async_block_till_done()
        result = {}
        for manager in f.managers:
            store = manager._store
            raw = Path(store.path).read_bytes()
            document = json.loads(raw)
            self.assertEqual(document['key'], store.key)
            self.assertEqual((document['version'], document['minor_version']),
                             (store.version, store.minor_version))
            self.assertEqual(document['data'],
                json.loads(_orjson_bytes_default_encoder(manager._data_to_save())))
            self.assertIsNone(store._data)
            self.assertIsNone(store._delay_handle)
            self.assertIsNone(store._unsub_final_write_listener)
            self.assertFalse(store._write_lock.locked())
            result[store.key] = raw
        self.assertEqual(set(result), {'core.entity_registry', 'core.device_registry',
                                       'core.config_entries'})
        return result

    def _same_documents(self, source, result):
        # Native JSON spelling is not a permission to change envelope/content.
        self.assertEqual({k: fixtures.baseline._json(json.loads(v)) for k, v in source.items()},
                         {k: fixtures.baseline._json(json.loads(v)) for k, v in result.items()})

    def _prove_registry(self, source, result, change, stamp, direction):
        transition = self.modules['native_registry_transition']
        key = 'core.entity_registry'
        audit = transition.NativeRegistryEffectAudit('entity', change.identity, direction,
            fixtures.digest(source[key]), fixtures.digest(result[key]),
            transition.prepared_registry_digest(change),
            json.loads(stamp.before_json).get('modified_at'),
            json.loads(stamp.after_json).get('modified_at'))
        transition.validate_registry_transition(source[key], result[key], change,
            version=self.f.version, direction=direction, native_effect_audit=audit)
        for other in ('core.config_entries', 'core.device_registry'):
            self.assertEqual(source[other], result[other])

    def _prove_config(self, source, result, change, stamp, direction):
        transition = self.modules['native_config_transition']
        key = 'core.config_entries'
        self.assertIsNotNone(stamp)
        self.assertTrue(stamp.changed)
        audit = transition.NativeConfigEffectAudit(change.before['identity']['entry_id'],
            direction, fixtures.digest(source[key]), fixtures.digest(result[key]),
            transition.prepared_helper_digest(change),
            json.loads(stamp.before_json)['modified_at'],
            json.loads(stamp.after_json)['modified_at'])
        transition.validate_config_transition(source[key], result[key], change,
            version=self.f.version, direction=direction,
            native_effect_audit=audit, native_mutation=stamp)
        for other in ('core.entity_registry', 'core.device_registry'):
            self.assertEqual(source[other], result[other])

    async def test_threshold_supported_unload_own_mutations_setup_and_reverse(self):
        from homeassistant.components.sensor import (
            SensorEntity, SensorDeviceClass, SensorStateClass)
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_component import EntityComponent

        f, m = self.f, self.modules
        hass = f.hass
        class Meter(SensorEntity):
            _attr_name = 'Renamer Quiescence Meter'
            _attr_unique_id = 'renamer-quiescence-meter'
            _attr_native_value = 41
            _attr_native_unit_of_measurement = 'kWh'
            _attr_device_class = SensorDeviceClass.ENERGY
            _attr_state_class = SensorStateClass.TOTAL_INCREASING
            _attr_should_poll = False

        component = EntityComponent(logging.getLogger(__name__), 'sensor', hass,
                                    timedelta(seconds=60))
        meter = Meter()
        await component.async_add_entities([meter])
        old, new = meter.entity_id, 'sensor.renamer_quiescence_changed'
        self.assertIs(component.get_entity(old), meter)
        self.assertIsNone(f.entity.async_get(new))
        self.assertIsNone(hass.states.get(new))
        self.assertEqual(hass.states.get(old).state, '41')
        helper = ConfigEntry(domain='threshold', title='Quiescence threshold fixture',
            data={}, options={'name': 'Quiescence threshold fixture', 'entity_id': old,
                               'hysteresis': 0.0, 'lower': None, 'upper': 42.5},
            discovery_keys=MappingProxyType({}), source='user', version=1,
            minor_version=2, subentries_data=[], unique_id='quiescence-helper',
            pref_disable_polling=True)
        await hass.config_entries.async_add(helper)
        await hass.async_block_till_done()
        self.assertIs(helper.state, ConfigEntryState.LOADED)
        update_listener_count = len(helper.update_listeners)
        self.assertTrue(helper._on_unload)
        rows = er.async_entries_for_config_entry(f.entity, helper.entry_id)
        self.assertEqual(len(rows), 1)
        helper_uuid, helper_id = rows[0].id, rows[0].entity_id
        # Non-default user registry preference must survive unload/setup twice.
        f.entity.async_update_entity(helper_id, hidden_by=er.RegistryEntryHider.USER,
                                    name='Preserved threshold name')
        await hass.async_block_till_done()
        self.assertEqual(hass.states.get(helper_id).state, 'off')
        source_uuid = f.entity.async_get(old).id
        before = m['helper_native'].read_entry(hass, helper.entry_id)
        prepared = m['helper_transaction'].prepare_helper(before, {old: new})
        baseline = await self._flush_documents(initialize=True)
        original_receipt = fixtures.receipt_for(f.root, f.version)
        original_inputs = await fixtures.baseline.async_capture_native_input_baseline(
            hass, original_receipt, guard=lambda: None)
        source_row = next(row for row in json.loads(baseline['core.entity_registry'])
                          ['data']['entities'] if row['id'] == source_uuid)
        registry_change = m['native_registry'].PreparedRegistryChange(
            'entity', source_uuid, json.dumps(source_row),
            json.dumps(dict(source_row, entity_id=new)))
        io = m['native_registry'].NativeRegistryIO(hass)
        helper_registry_before = deepcopy(next(row for row in
            json.loads(baseline['core.entity_registry'])['data']['entities']
            if row['id'] == helper_uuid))
        activation_before = (helper.state, helper.disabled_by,
            helper.pref_disable_new_entities, helper.pref_disable_polling)

        def inactive():
            from homeassistant.helpers import entity_platform as ep
            self.assertIs(helper.state, ConfigEntryState.NOT_LOADED)
            self.assertFalse(any(entity.entity_id == helper_id for platform in
                ep.async_get_platforms(hass, 'threshold') for entity in platform.entities.values()))
            # Native HA retains a registry-derived unavailable placeholder for
            # an unloaded integration. It is not an active helper/consumer.
            state = hass.states.get(helper_id)
            self.assertIsNotNone(state)
            self.assertEqual(state.state, 'unavailable')
            self.assertEqual(dict(state.attributes), {'restored': True,
                'friendly_name': 'Preserved threshold name', 'supported_features': 0})
            self.assertFalse(helper.update_listeners)

        async def unload():
            self.assertTrue(await hass.config_entries.async_unload(helper.entry_id))
            # Keep this barrier in the test task (no new task, sleep or fake ACK).
            await hass.async_block_till_done()
            inactive()
            # The Threshold source listener is registered with async_on_unload
            # in both pinned cores. The rename+unchanged-options check below is
            # the independent behavioral probe of its actual removal.

        async def loaded(reference):
            self.assertTrue(await hass.config_entries.async_setup(helper.entry_id))
            await hass.async_block_till_done()
            self.assertEqual((helper.state, helper.disabled_by,
                helper.pref_disable_new_entities, helper.pref_disable_polling),
                activation_before)
            self.assertEqual(len(helper.update_listeners), update_listener_count)
            self.assertTrue(helper._on_unload)
            self.assertEqual(m['helper_native'].read_entry(hass, helper.entry_id),
                             prepared.after if reference == new else before)
            current = er.async_entries_for_config_entry(f.entity, helper.entry_id)
            self.assertEqual([(row.id, row.entity_id) for row in current],
                             [(helper_uuid, helper_id)])
            self.assertEqual(f.entity.async_get(helper_id).hidden_by,
                             er.RegistryEntryHider.USER)
            state = hass.states.get(helper_id)
            self.assertIsNotNone(state)
            self.assertEqual(state.attributes['entity_id'], reference)
            self.assertEqual(state.state, 'off')

        async def stimulate(reference):
            self.assertEqual(meter.entity_id, reference)
            self.assertIs(component.get_entity(reference), meter)
            self.assertEqual(f.entity.async_get(reference).id, source_uuid)
            meter._attr_native_value = 43
            meter.async_write_ha_state()
            await hass.async_block_till_done()
            self.assertEqual(hass.states.get(reference).state, '43')
            self.assertEqual(hass.states.get(helper_id).state, 'on')
            self.assertEqual(float(hass.states.get(helper_id)
                                   .attributes['sensor_value']), 43)
            meter._attr_native_value = 41
            meter.async_write_ha_state()
            await hass.async_block_till_done()
            self.assertEqual(hass.states.get(helper_id).state, 'off')
            self.assertEqual(float(hass.states.get(helper_id)
                                   .attributes['sensor_value']), 41)

        await unload()
        # Quiescing must preserve the ORIGINAL source fence, not obtain a new
        # receipt by accepting arbitrary inputs after an activation change.
        fixtures.validation.assert_validation_current(original_receipt)
        self.assertEqual(await fixtures.baseline.async_capture_native_input_baseline(
            hass, original_receipt, guard=lambda: None), original_inputs)
        self._same_documents(baseline, await self._flush_documents())
        registry_stamp = io.mutate(registry_change, registry_change.after)
        await hass.async_block_till_done()
        registry_after = await self._flush_documents()
        self._prove_registry(baseline, registry_after, registry_change,
                             registry_stamp, 'forward')
        self.assertEqual(helper.options['entity_id'], old)
        inactive()
        self.assertIs(component.get_entity(new), meter)
        self.assertEqual(meter.entity_id, new)
        self.assertEqual(hass.states.get(new).state, '41')
        self.assertIsNone(hass.states.get(old))
        self.assertEqual(m['helper_native'].read_entry(hass, helper.entry_id), before)
        # The full config bytes stayed equal across the registry event above;
        # only this own synchronous native call may now move the reference.
        stamp = m['helper_native']._mutate_entry(hass.config_entries, helper,
            data=prepared.after['data'], options=prepared.after['options'])
        config_after = await self._flush_documents()
        self._prove_config(registry_after, config_after, prepared, stamp, 'forward')
        inactive()
        await loaded(new)
        self._same_documents(config_after, await self._flush_documents())
        await stimulate(new)
        self._same_documents(config_after, await self._flush_documents())

        # Reverse ordinary path: unload, own config reverse, registry reverse,
        # supported setup. Every source is the previously proven full result.
        await unload()
        self._same_documents(config_after, await self._flush_documents())
        reverse_stamp = m['helper_native']._mutate_entry(hass.config_entries, helper,
            data=prepared.before['data'], options=prepared.before['options'])
        config_reverse = await self._flush_documents()
        self._prove_config(config_after, config_reverse, prepared, reverse_stamp, 'reverse')
        inactive()
        registry_reverse_stamp = io.mutate(registry_change, registry_change.before)
        await hass.async_block_till_done()
        registry_reverse = await self._flush_documents()
        self._prove_registry(config_reverse, registry_reverse, registry_change,
                             registry_reverse_stamp, 'reverse')
        self.assertEqual(helper.options['entity_id'], old)
        self.assertIs(helper.state, ConfigEntryState.NOT_LOADED)
        self.assertIsNone(hass.states.get(new))
        await loaded(old)
        await stimulate(old)
        final = await self._flush_documents()
        self._same_documents(registry_reverse, final)
        self.assertEqual(next(row for row in json.loads(final['core.entity_registry'])
            ['data']['entities'] if row['id'] == helper_uuid), helper_registry_before)
        # Independent original-to-final comparison: only exact observed own
        # timestamps and the documented pop/append of the source target row.
        expected = {key: json.loads(raw) for key, raw in baseline.items()}
        expected_source = next(row for row in expected['core.entity_registry']
                               ['data']['entities'] if row['id'] == source_uuid)
        observed_source = json.loads(registry_reverse_stamp.after_json)
        if 'modified_at' in expected_source:
            expected_source['modified_at'] = observed_source['modified_at']
        rows = expected['core.entity_registry']['data']['entities']
        rows.remove(expected_source)
        rows.append(expected_source)
        expected_helper = next(row for row in expected['core.config_entries']
                               ['data']['entries'] if row['entry_id'] == helper.entry_id)
        expected_helper['modified_at'] = json.loads(reverse_stamp.after_json)['modified_at']
        self.assertEqual(expected, {key: json.loads(raw) for key, raw in final.items()})


if __name__ == '__main__':
    unittest.main()
