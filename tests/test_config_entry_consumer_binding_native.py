"""Native integer-key binding regression; owner runs on both pinned HA cores.

Reverting R1 must fail the positive case at the binder, not at a fake reader.
Uses the existing private SSD fixture, real ConfigEntry/ConfigSubentry,
ConfigEntries._data_to_save, HA encoder, discovery source capture and inventory.
No running HA instance's storage is opened. Never qualifies migration readiness.
"""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from pathlib import Path
from types import MappingProxyType
import unittest

import test_native_input_baseline as fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class NativeConfigEntryConsumerBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixtures.NativeBaselineTests(
            'test_actual_native_serialization_and_store_full_baseline')
        await self.f.asyncSetUp()
        self.addAsyncCleanup(self.f.asyncTearDown)
        self.assertIn(self.f.version, ('2025.6.0', '2026.10.2'))
        self.binding = importlib.import_module(fixtures.PACKAGE + '.native_configentry_binding')
        self.helper = importlib.import_module(fixtures.PACKAGE + '.helper_native')
        self.discovery_inputs = importlib.import_module(fixtures.PACKAGE + '.native_discovery_inputs')
        from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler, ConfigSubentry
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        self.encoder = _orjson_bytes_default_encoder
        child = ConfigSubentry(subentry_id='binding-child', subentry_type='fixture',
            title='Integer-key child', unique_id=None,
            data=MappingProxyType({'channels': {1: 'child'}, 'types': [False, 0, 0.0]}))
        self.entry = ConfigEntry(domain='input_boolean', title='Integer-key binding',
            source='user', version=1, minor_version=1, unique_id='binding-entry',
            disabled_by=ConfigEntryDisabler.USER, discovery_keys=MappingProxyType({}),
            data={'channels': {1: 'data', 'literal': 'kept'},
                  'bounds': {-(1 << 63): 'lower', (1 << 64) - 1: 'upper'},
                  'types': [False, 0, 0.0], 'sequence': ('first', 'second')},
            options={'channels': {-2: 'options'}, 'types': [False, 0, 0.0]},
            subentries_data=[child.as_dict()])
        self.assertIs(type(self.entry.subentries['binding-child']), ConfigSubentry)
        # Private fixture manager only; no integration setup or listener writes.
        self.f.hass.config_entries._entries[self.entry.entry_id] = self.entry
        await self.f.hass.config_entries._store.async_save(
            self.f.hass.config_entries._data_to_save())
        await self.f.hass.async_block_till_done()
        self.observed = await self.discovery_inputs.async_observe_discovery_inputs(
            self.f.hass, guard=self.f.guard)
        self.plans = self.helper.inventory(self.f.hass, {})
        self.plan_index = next(i for i, plan in enumerate(self.plans)
                               if plan.before['identity']['entry_id'] == self.entry.entry_id)

    def changed_plans(self, path, value):
        plans = list(self.plans)
        state = deepcopy(plans[self.plan_index].before)
        parent = state
        for part in path[:-1]:
            parent = parent[part]
        parent[path[-1]] = value
        plans[self.plan_index] = replace(plans[self.plan_index], before=state)
        return tuple(plans)

    def assert_refused(self, plans):
        with self.assertRaises(self.binding.SourceBindingError) as error:
            self.binding.bind_source_consumer(self.observed, plans)
        self.assertEqual(str(error.exception), 'configentry_source_consumer_unbound')

    def test_native_integer_key_projection_binds_data_options_and_subentry(self):
        source = self.observed.files[2]
        raw = Path(self.f.hass.config_entries._store.path).read_bytes()
        actual_manager = json.loads(self.encoder(self.f.hass.config_entries._data_to_save()))
        self.assertEqual(source.raw, raw)
        self.assertEqual(json.loads(source.manager_json), actual_manager)
        self.assertEqual(json.loads(raw)['data'], actual_manager)
        saved = next(row for row in actual_manager['entries']
                     if row['entry_id'] == self.entry.entry_id)
        # Literal expectations are independent of the binder's projection.
        self.assertEqual(saved['data']['channels'], {'1': 'data', 'literal': 'kept'})
        self.assertEqual(saved['data']['bounds'],
                         {'-9223372036854775808': 'lower', '18446744073709551615': 'upper'})
        self.assertEqual(saved['options']['channels'], {'-2': 'options'})
        self.assertEqual(saved['subentries'][0]['data']['channels'], {'1': 'child'})
        live = self.helper.read_entry(self.f.hass, self.entry.entry_id)
        self.assertEqual(live['data']['channels'], {1: 'data', 'literal': 'kept'})
        self.assertEqual(live['options']['channels'], {-2: 'options'})
        self.assertEqual(live['subentries']['binding-child']['data']['channels'], {1: 'child'})
        self.assertEqual(live['data']['sequence'], ['first', 'second'])
        for root in (saved['data'], saved['options'], saved['subentries'][0]['data']):
            self.assertEqual(tuple(type(v) for v in root['types']), (bool, int, float))
        result = self.binding.bind_source_consumer(self.observed, self.plans)
        self.assertEqual(result.entries, len(self.f.hass.config_entries.async_entries()))
        self.assertFalse(result.can_write)
        self.assertFalse(result.can_promote)
        self.discovery_inputs.assert_discovery_managers_current(
            self.f.hass, self.observed, guard=self.f.guard)
        self.assertEqual(Path(self.f.hass.config_entries._store.path).read_bytes(), raw)

    def test_colliding_integer_and_string_keys_refuse_even_equal_values(self):
        for path, value in (
                (('data', 'channels'), {1: 'data', '1': 'data', 'literal': 'kept'}),
                (('options', 'channels'), {-2: 'options', '-2': 'options'}),
                (('subentries', 'binding-child', 'data', 'channels'), {1: 'child', '1': 'child'})):
            with self.subTest(path=path):
                # HA encoding permits duplicate JSON keys: binder must not
                # erase this collision through an encode/decode comparison.
                self.assert_refused(self.changed_plans(path, value))

    def test_bool_float_null_subclass_and_unknown_keys_are_not_integer_keys(self):
        class IntegerSubclass(int):
            pass
        for key in (True, 1.0, None, IntegerSubclass(1), object()):
            with self.subTest(key_type=type(key).__name__):
                with self.assertRaises(self.binding.SourceBindingError):
                    self.binding._serial({key: 'data'})
                self.assert_refused(self.changed_plans(('data', 'channels'), {key: 'data'}))

    def test_integer_keys_outside_native_64_bit_contract_refuse(self):
        for key in (-(1 << 63) - 1, 1 << 64):
            with self.subTest(key=key):
                with self.assertRaises(self.binding.SourceBindingError):
                    self.binding._serial({key: 'data'})
                self.assert_refused(self.changed_plans(('data', 'channels'), {key: 'data'}))

    def test_bool_integer_and_float_value_drift_still_refuses(self):
        for root in (('data',), ('options',), ('subentries', 'binding-child', 'data')):
            for index, value in ((0, 0), (1, 0.0), (2, 0)):
                with self.subTest(root=root, index=index):
                    self.assert_refused(self.changed_plans(root + ('types', index), value))

    def test_non_json_values_are_not_native_key_normalization(self):
        for value in ({1, 2}, object(), float('nan'), float('inf')):
            with self.subTest(value_type=type(value).__name__):
                self.assert_refused(self.changed_plans(('data', 'channels'), {'1': value}))

    def test_full_set_requires_unchanged_disabled_entry(self):
        self.assertIsNotNone(self.entry.disabled_by)
        self.assertEqual(self.entry.state.value, 'not_loaded')
        self.assert_refused(tuple(plan for plan in self.plans
                                  if plan.before['identity']['entry_id'] != self.entry.entry_id))


if __name__ == '__main__':
    unittest.main(verbosity=2)
