"""Actual HA unload/own-write/setup probes; owner runs current/minimum via FIFO.

No lifecycle recovery, transaction readiness or product AC is established here.
The source receipt is captured once and must survive supported unload/drain.
"""
from copy import deepcopy
from datetime import timedelta
import importlib
import json
import logging
import statistics
from types import MappingProxyType
import unittest

# Import the fixture MODULE, not its TestCase: unittest must not discover the
# inherited Threshold experiment twice merely because this module was imported.
import test_native_helper_quiescence as quiescence


class NativeHelperQuiescenceMatrixTests(quiescence.NativeHelperQuiescenceTests):
    test_threshold_supported_unload_own_mutations_setup_and_reverse = None

    async def _matrix(self, kind, activation):
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.components.binary_sensor import BinarySensorEntity
        from homeassistant.config_entries import (
            ConfigEntry, ConfigEntryState, ConfigEntryDisabler)
        from homeassistant.helpers import entity_registry as er, entity_platform as ep
        from homeassistant.helpers.entity_component import EntityComponent

        f, m = self.f, self.modules
        hass = f.hass
        binary = kind in ('group_any', 'group_all')
        domain = 'group' if binary else 'min_max'
        source_domain = 'binary_sensor' if binary else 'sensor'
        initial = (True, False) if binary else (41, 3, 11)

        class NumericSource(SensorEntity):
            _attr_should_poll = False
            _attr_native_unit_of_measurement = 'kWh'
            def __init__(self, index, value):
                self._attr_name = f'Quiescence matrix source {index}'
                self._attr_unique_id = f'quiescence-matrix-source-{index}'
                self._attr_native_value = value

        class BinarySource(BinarySensorEntity):
            _attr_should_poll = False
            def __init__(self, index, value):
                self._attr_name = f'Quiescence matrix source {index}'
                self._attr_unique_id = f'quiescence-matrix-source-{index}'
                self._attr_is_on = value

        component = EntityComponent(logging.getLogger(__name__), source_domain,
                                    hass, timedelta(seconds=60))
        cls = BinarySource if binary else NumericSource
        sources = [cls(i, value) for i, value in enumerate(initial)]
        await component.async_add_entities(sources)
        old = sources[0].entity_id
        new = source_domain + '.quiescence_matrix_changed'
        references = [source.entity_id for source in sources]
        source_uuids = [f.entity.async_get(ref).id for ref in references]
        self.assertIsNone(f.entity.async_get(new))
        self.assertIsNone(hass.states.get(new))
        options = ({'name': 'Quiescence matrix helper', 'group_type': 'binary_sensor',
                    'entities': references, 'all': kind == 'group_all', 'hide_members': False}
                   if binary else {'name': 'Quiescence matrix helper',
                    'entity_ids': references, 'type': kind, 'round_digits': 2})
        helper = ConfigEntry(domain=domain, title='Quiescence matrix helper', data={},
            options=options, discovery_keys=MappingProxyType({}), source='user',
            version=1, minor_version=1, subentries_data=[],
            unique_id='quiescence-matrix-helper', pref_disable_polling=True)
        await hass.config_entries.async_add(helper)
        await hass.async_block_till_done()
        self.assertIs(helper.state, ConfigEntryState.LOADED)
        rows = er.async_entries_for_config_entry(f.entity, helper.entry_id)
        self.assertEqual(len(rows), 1)
        helper_uuid, helper_id = rows[0].id, rows[0].entity_id
        f.entity.async_update_entity(helper_id, hidden_by=er.RegistryEntryHider.USER,
                                    name='Preserved matrix name')
        await hass.async_block_till_done()
        listener_count = len(helper.update_listeners)
        actual_class = getattr(importlib.import_module(
            f'homeassistant.components.{domain}.{source_domain}'),
            'BinarySensorGroup' if binary else 'MinMaxSensor')

        def native_entities():
            return [entity for platform in ep.async_get_platforms(hass, domain)
                    for entity in platform.entities.values() if entity.entity_id == helper_id]

        def native(reference):
            entities = native_entities()
            self.assertEqual(len(entities), 1)
            entity = entities[0]
            self.assertIs(type(entity), actual_class)
            self.assertIs(entity.hass, hass)
            self.assertEqual(entity.registry_entry.id, helper_uuid)
            expected = [reference, *references[1:]]
            self.assertEqual(list(entity._entity_ids), expected)
            if binary:
                self.assertIs(entity.mode, all if kind == 'group_all' else any)
            else:
                self.assertEqual(entity._sensor_type, kind)
                self.assertEqual(entity._round_digits, 2)
            return entity

        def expected(values):
            if binary:
                return 'on' if (all(values) if kind == 'group_all' else any(values)) else 'off'
            return {'min': min, 'max': max, 'mean': statistics.mean,
                    'median': statistics.median, 'range': lambda v: max(v) - min(v),
                    'sum': sum}[kind](values)

        def output(values):
            state = hass.states.get(helper_id)
            self.assertIsNotNone(state)
            if binary:
                self.assertEqual(state.state, expected(values))
            else:
                self.assertAlmostEqual(float(state.state), round(expected(values), 2), places=2)
                self.assertEqual(state.attributes['unit_of_measurement'], 'kWh')

        native(old)
        output(initial)
        if activation == 'not_loaded':
            self.assertTrue(await hass.config_entries.async_unload(helper.entry_id))
        elif activation == 'disabled':
            self.assertTrue(await hass.config_entries.async_set_disabled_by(
                helper.entry_id, ConfigEntryDisabler.USER))
        await hass.async_block_till_done()
        before = m['helper_native'].read_entry(hass, helper.entry_id)
        activation_before = (helper.state, helper.disabled_by,
            helper.pref_disable_new_entities, helper.pref_disable_polling)
        prepared = m['helper_transaction'].prepare_helper(before, {old: new})
        baseline = await self._flush_documents(initialize=True)
        original_receipt = quiescence.fixtures.receipt_for(f.root, f.version)
        original_inputs = await quiescence.fixtures.baseline.async_capture_native_input_baseline(
            hass, original_receipt, guard=lambda: None)
        source_row = next(row for row in json.loads(baseline['core.entity_registry'])
                          ['data']['entities'] if row['id'] == source_uuids[0])
        registry_change = m['native_registry'].PreparedRegistryChange('entity',
            source_uuids[0], json.dumps(source_row), json.dumps(dict(source_row, entity_id=new)))
        io = m['native_registry'].NativeRegistryIO(hass)
        helper_registry_before = deepcopy(next(row for row in
            json.loads(baseline['core.entity_registry'])['data']['entities']
            if row['id'] == helper_uuid))

        def inactive():
            self.assertIs(helper.state, ConfigEntryState.NOT_LOADED)
            self.assertFalse(native_entities())
            self.assertFalse(helper.update_listeners)
            # HA can retain an unavailable registry placeholder; absence of an
            # active platform entity and real stimulus below prove no activation.
            state = hass.states.get(helper_id)
            if state is not None:
                self.assertEqual(state.state, 'unavailable')

        async def unload():
            if activation == 'loaded':
                self.assertTrue(await hass.config_entries.async_unload(helper.entry_id))
            await hass.async_block_till_done()
            inactive()

        async def restore(reference):
            if activation == 'loaded':
                self.assertTrue(await hass.config_entries.async_setup(helper.entry_id))
                await hass.async_block_till_done()
                native(reference)
                output(initial)
                self.assertEqual(len(helper.update_listeners), listener_count)
            else:
                inactive()
            self.assertEqual((helper.state, helper.disabled_by,
                helper.pref_disable_new_entities, helper.pref_disable_polling), activation_before)
            self.assertEqual(m['helper_native'].read_entry(hass, helper.entry_id),
                             prepared.after if reference == new else before)
            self.assertEqual([(row.id, row.entity_id) for row in
                er.async_entries_for_config_entry(f.entity, helper.entry_id)],
                [(helper_uuid, helper_id)])
            self.assertEqual(f.entity.async_get(helper_id).hidden_by, er.RegistryEntryHider.USER)

        async def stimulate(reference):
            self.assertEqual([source.entity_id for source in sources], [reference, *references[1:]])
            self.assertEqual([f.entity.async_get(source.entity_id).id for source in sources], source_uuids)
            self.assertTrue(all(component.get_entity(source.entity_id) is source for source in sources))
            vectors = ((False, False), (True, True), initial) if binary else ((1, 3, 11), initial)
            for values in vectors:
                for source, value in zip(sources, values):
                    if binary:
                        source._attr_is_on = value
                    else:
                        source._attr_native_value = value
                    source.async_write_ha_state()
                await hass.async_block_till_done()
                if activation == 'loaded':
                    native(reference)
                    output(values)
                else:
                    inactive()

        await unload()
        quiescence.fixtures.validation.assert_validation_current(original_receipt)
        self.assertEqual(await quiescence.fixtures.baseline.async_capture_native_input_baseline(
            hass, original_receipt, guard=lambda: None), original_inputs)
        self._same_documents(baseline, await self._flush_documents())
        registry_stamp = io.mutate(registry_change, registry_change.after)
        await hass.async_block_till_done()
        registry_after = await self._flush_documents()
        self._prove_registry(baseline, registry_after, registry_change, registry_stamp, 'forward')
        self.assertEqual(m['helper_native'].read_entry(hass, helper.entry_id), before)
        inactive()
        self.assertEqual(sources[0].entity_id, new)
        self.assertIsNone(hass.states.get(old))
        stamp = m['helper_native']._mutate_entry(hass.config_entries, helper,
            data=prepared.after['data'], options=prepared.after['options'])
        config_after = await self._flush_documents()
        self._prove_config(registry_after, config_after, prepared, stamp, 'forward')
        inactive()
        await restore(new)
        await stimulate(new)
        self._same_documents(config_after, await self._flush_documents())

        await unload()
        self._same_documents(config_after, await self._flush_documents())
        reverse_stamp = m['helper_native']._mutate_entry(hass.config_entries, helper,
            data=prepared.before['data'], options=prepared.before['options'])
        config_reverse = await self._flush_documents()
        self._prove_config(config_after, config_reverse, prepared, reverse_stamp, 'reverse')
        inactive()
        reverse_registry_stamp = io.mutate(registry_change, registry_change.before)
        await hass.async_block_till_done()
        registry_reverse = await self._flush_documents()
        self._prove_registry(config_reverse, registry_reverse, registry_change,
                             reverse_registry_stamp, 'reverse')
        inactive()
        self.assertIsNone(hass.states.get(new))
        await restore(old)
        await stimulate(old)
        final = await self._flush_documents()
        self._same_documents(registry_reverse, final)
        self.assertEqual(next(row for row in json.loads(final['core.entity_registry'])
            ['data']['entities'] if row['id'] == helper_uuid), helper_registry_before)
        expected_documents = {key: json.loads(raw) for key, raw in baseline.items()}
        rows = expected_documents['core.entity_registry']['data']['entities']
        expected_source = next(row for row in rows if row['id'] == source_uuids[0])
        if 'modified_at' in expected_source:
            expected_source['modified_at'] = json.loads(reverse_registry_stamp.after_json)['modified_at']
        rows.remove(expected_source)
        rows.append(expected_source)
        expected_helper = next(row for row in expected_documents['core.config_entries']
                               ['data']['entries'] if row['entry_id'] == helper.entry_id)
        expected_helper['modified_at'] = json.loads(reverse_stamp.after_json)['modified_at']
        self.assertEqual(expected_documents, {key: json.loads(raw) for key, raw in final.items()})


def _case(kind, activation):
    async def test(self):
        await self._matrix(kind, activation)
    test.__name__ = f'test_{kind}_{activation}_unload_own_mutations_and_reverse'
    return test


for _kind in ('min', 'max', 'mean', 'median', 'range', 'sum', 'group_any', 'group_all'):
    for _activation in ('loaded', 'not_loaded', 'disabled'):
        _test = _case(_kind, _activation)
        setattr(NativeHelperQuiescenceMatrixTests, _test.__name__, _test)


if __name__ == '__main__':
    unittest.main()
