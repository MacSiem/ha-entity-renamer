"""Authored only; owner runs through FIFO on both exact Core pins.

Positive runtime evidence uses real ConfigEntries flow, UtilityMeterSensor,
EntityComponent and producer events. Codec tests need no HA installation.
These tests do not prove lifecycle, recorder/history or release acceptance.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import importlib
import json
import logging
import unittest

import test_native_input_baseline as fixtures
import test_native_helper_quiescence as quiescence

u = importlib.import_module(fixtures.PACKAGE + '.utility_meter_observers')
h = importlib.import_module(fixtures.PACKAGE + '.helper_observers')


def baseline_document():
    identity = {'entry_id': 'entry', 'unique_id': None, 'title': 'Utility',
                'version': 2, 'minor_version': 2, 'source': 'user', 'disabled_by': None,
                'pref_disable_polling': False, 'pref_disable_new_entities': False}
    before = {'domain': 'utility_meter', 'identity': identity, 'data': {}, 'subentries': {},
              'options': dict(u._BASIC, name='Utility', source='sensor.old')}
    after = deepcopy(before)
    after['options']['source'] = 'sensor.new'
    static = {k: None for k in u._STATIC}
    static.update(_last_period='0', _last_reset='2026-10-25T10:00:00+00:00',
                  _input_device_class='energy', _attr_native_unit_of_measurement='kWh',
                  _sensor_delta_values=False, _sensor_net_consumption=False,
                  _sensor_periodically_resetting=False, _sensor_always_available=False)
    row = {k: None for k in u._ROW_KEYS | {'aliases_v2', 'object_id_base'}}
    row.update(id='helper-uuid', entity_id='sensor.utility', unique_id='entry',
               platform='utility_meter', config_entry_id='entry',
               aliases=[], aliases_v2=[], labels=[], categories={}, options={},
               has_entity_name=False, created_at='2026-10-25T10:00:00+00:00',
               modified_at='2026-10-25T10:00:00+00:00')
    return {'version': 1, 'domain': 'utility_meter', 'core': '2026.10.2', 'entry_id': 'entry',
            'before': before, 'after': after, 'source_identity': ['source-uuid', 'physical',
                'producer', None, None], 'source_before': 'sensor.old',
            'source_after': 'sensor.new', 'source_unit': 'kWh', 'source_class': 'energy',
            's0': '111', 'a0': '11', 'static': static, 'helper_rows': [row]}


class UtilityBaselineCodecTests(unittest.TestCase):
    def decode(self, value):
        return u.decode_runtime_baseline({'entry_id': 'entry', 'payload': value})

    def test_roundtrip_is_detached_frozen_and_bound_to_original_preparation(self):
        doc = baseline_document()
        baseline = self.decode(doc)
        self.assertEqual(u.encode_runtime_baseline(baseline)['payload'], doc)
        self.assertTrue(u.verify_baseline_binding(baseline, doc['before'], doc['after']))
        doc['static']['_last_period'] = '9'
        self.assertEqual(u.encode_runtime_baseline(baseline)['payload']['static']['_last_period'], '0')
        with self.assertRaises(AttributeError):
            baseline.payload_json = '{}'

    def test_strict_codec_refuses_unknown_noncanonical_duplicate_and_corrupt_data(self):
        doc = baseline_document()
        variants = []
        for key, value in [('version', 2), ('domain', 'integration'), ('core', '2026.10.3'),
                           ('entry_id', 'foreign'), ('a0', 'NaN'), ('a0', '11.0'),
                           ('s0', '0'), ('s0', '-1'), ('a0', '1e999'), ('a0', True),
                           ('source_identity', ['fake']), ('source_before', 'switch.old')]:
            variant = deepcopy(doc)
            variant[key] = value
            variants.append(variant)
        unknown = deepcopy(doc)
        unknown['object_id'] = 999
        variants.append(unknown)
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(h.ObservationUnavailable):
                self.decode(variant)
        with self.assertRaises(h.ObservationUnavailable):
            u.decode_runtime_baseline({'entry_id': 'entry', 'payload': doc, 'authority': True})
        raw = u._canonical(doc)
        with self.assertRaises(h.ObservationUnavailable):
            u.encode_runtime_baseline(u.UtilityMeterRuntimeBaseline('entry', raw.replace(
                '"version":1', '"version":1,"version":1')))
        with self.assertRaises(h.ObservationUnavailable):
            u.encode_runtime_baseline(u.UtilityMeterRuntimeBaseline('entry', ' ' + raw))

    def test_modes_extra_options_subentries_and_changed_preparations_are_refused(self):
        doc = baseline_document()
        baseline = self.decode(doc)
        for key, value in [('tariffs', ['peak']), ('cycle', 'daily'), ('offset', 1),
                           ('delta_values', True), ('net_consumption', True),
                           ('periodically_resetting', True), ('always_available', True),
                           ('cron', '* * * * *')]:
            variant = deepcopy(doc)
            variant['before']['options'][key] = value
            with self.subTest(key=key), self.assertRaises(h.ObservationUnavailable):
                self.decode(variant)
        variant = deepcopy(doc)
        variant['after']['identity']['title'] = 'foreign'
        with self.assertRaises(h.ObservationUnavailable):
            self.decode(variant)
        for side in ('before', 'after'):
            changed = deepcopy(doc[side])
            changed['identity']['pref_disable_polling'] = True
            args = [doc['before'], doc['after']]
            args[0 if side == 'before' else 1] = changed
            with self.assertRaises(h.ObservationUnavailable):
                u.verify_baseline_binding(baseline, *args)
        variant = deepcopy(doc)
        variant['helper_rows'].append(deepcopy(variant['helper_rows'][0]))
        with self.assertRaises(h.ObservationUnavailable):
            self.decode(variant)
        variant = deepcopy(doc)
        variant['helper_rows'][0]['config_entry_id'] = 'foreign'
        with self.assertRaises(h.ObservationUnavailable):
            self.decode(variant)

    def test_bounds_and_nonfinite_do_not_round_into_valid_evidence(self):
        for number in ('NaN', 'Infinity', '-Infinity', '1e100', '1e-100', '9' * 97):
            with self.subTest(number=number), self.assertRaises(h.ObservationUnavailable):
                u._number(number)
        self.assertEqual(u._number(Decimal('1.23456789012345678901234567890123456789')),
                         '1.23456789012345678901234567890123456789')
        deep = []
        for _ in range(20):
            deep = [deep]
        with self.assertRaises(h.ObservationUnavailable):
            u._canonical(deep)


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class ActualUtilityMeterObserverTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = quiescence.NativeHelperQuiescenceTests.asyncSetUp

    async def _before_start(self, hass, root):
        from homeassistant.setup import async_setup_component
        self.assertTrue(await async_setup_component(hass, 'utility_meter', {}))

    async def _fixture(self):
        from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
        from homeassistant.helpers.entity_component import EntityComponent
        from homeassistant.config_entries import ConfigEntryState
        class Source(SensorEntity):
            _attr_name = 'Utility Observer Source'
            _attr_unique_id = 'utility-observer-source'
            _attr_native_value = Decimal('100')
            _attr_native_unit_of_measurement = 'kWh'
            _attr_device_class = SensorDeviceClass.ENERGY
            _attr_state_class = SensorStateClass.TOTAL_INCREASING
            _attr_should_poll = False
        component = EntityComponent(logging.getLogger(__name__), 'sensor', self.f.hass,
                                    timedelta(seconds=60))
        self.source = Source()
        await component.async_add_entities([self.source])
        self.old, self.new = self.source.entity_id, 'sensor.utility_observer_changed'
        self.source_uuid = self.f.entity.async_get(self.old).id
        options = dict(u._BASIC, name='Utility Observer', source=self.old)
        result = await self.f.hass.config_entries.flow.async_init('utility_meter',
            context={'source': 'user'}, data=options)
        self.assertEqual(result['type'], 'create_entry')
        self.entry = result['result']
        await self.f.hass.async_block_till_done()
        self.assertIs(self.entry.state, ConfigEntryState.LOADED)
        await self._emit('107')
        await self._emit('111')
        self.before = u.read_entry(self.f.hass, self.entry.entry_id)
        self.proof = (h.RegistryMappingProof(self.old, self.new, self.source_uuid),)
        self.observer = await h.NativeHelperObservers.capture(self.f.hass, [self.before], self.proof)
        self.assertIs(type(self.observer), u.NativeUtilityMeterObservers)
        self.entity, _ = self.observer._activation(self.before)
        self.assertEqual(self.entity.native_value, Decimal('11'))
        return self.observer

    def _registry_document(self):
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        return json.loads(_orjson_bytes_default_encoder(self.f.entity._data_to_save()))

    async def _emit(self, value, *, unit=None):
        self.source._attr_native_value = Decimal(value)
        if unit is not None:
            self.source._attr_native_unit_of_measurement = unit
        self.source.async_write_ha_state()
        await self.f.hass.async_block_till_done()

    async def test_real_factory_capture_is_readonly_then_accepts_independent_delta(self):
        observer = await self._fixture()
        rows = self._registry_document()
        original_entry = u.read_entry(self.f.hass, self.entry.entry_id)
        state = self.f.hass.states.get(self.entity.entity_id)
        restore = deepcopy(self.entity.extra_restore_state_data.as_dict())
        jobs = observer._jobs(self.entry.entry_id)
        self.assertTrue(await observer.verify_runtime(self.before))
        self.assertEqual(self.f.hass.states.get(self.entity.entity_id), state)
        self.assertEqual(self.entity.extra_restore_state_data.as_dict(), restore)
        self.assertEqual(observer._jobs(self.entry.entry_id), jobs)
        self.assertEqual(self._registry_document(), rows)
        self.assertEqual(u.read_entry(self.f.hass, self.entry.entry_id), original_entry)
        baselines = observer.runtime_baselines()
        self.assertEqual(len(baselines), 1)
        baseline = u.decode_runtime_baseline(u.encode_runtime_baseline(baselines[0]))
        self.assertTrue(u.verify_baseline_binding(baseline, self.before,
                                                observer.after[self.entry.entry_id]))
        await self._emit('116')
        self.assertEqual(self.entity.native_value, Decimal('16'))
        self.assertTrue(await observer.verify_runtime(self.before))
        self.assertEqual(observer.runtime_baselines(), baselines)  # no current recapture
        self.assertTrue(await observer.verify_original_inventory(None))

    async def test_foreign_source_physical_identity_refused_with_current_valid_source(self):
        observer = await self._fixture()
        self.f.entity.async_update_entity(self.old, new_unique_id='foreign-physical-identity')
        with self.assertRaisesRegex(h.ObservationUnavailable, 'registry_identity_changed'):
            await observer.verify_runtime(self.before)

    async def test_extra_wrong_source_subscription_is_not_activation(self):
        from homeassistant.helpers.event import async_track_state_change_event
        observer = await self._fixture()
        unsubscribe = async_track_state_change_event(self.f.hass, ['sensor.foreign'],
                                                     self.entity.async_reading)
        self.addCleanup(unsubscribe)
        with self.assertRaisesRegex(h.ObservationUnavailable, 'callback_ownership'):
            await observer.verify_runtime(self.before)

    async def test_unproven_source_uuid_and_duplicate_helper_are_refused(self):
        await self._fixture()
        wrong = (h.RegistryMappingProof(self.old, self.new, 'foreign-source-uuid'),)
        with self.assertRaisesRegex(h.ObservationUnavailable, 'proof_not_native'):
            await h.NativeHelperObservers.capture(self.f.hass, [self.before], wrong)
        with self.assertRaisesRegex(h.ObservationUnavailable, 'duplicate_entry'):
            await h.NativeHelperObservers.capture(self.f.hass, [self.before, self.before], self.proof)

    async def test_unit_change_and_foreign_helper_metadata_are_explicit_refusals(self):
        observer = await self._fixture()
        await self._emit('116', unit='Wh')
        with self.assertRaises(h.ObservationUnavailable):
            await observer.verify_runtime(self.before)
        self.f.entity.async_update_entity(self.entity.entity_id, name='Foreign changed name')
        with self.assertRaisesRegex(h.ObservationUnavailable, 'registry_changed'):
            await observer.verify_runtime(self.before)

    async def test_unloaded_capture_never_sets_up_or_claims_counter_baseline(self):
        observer = await self._fixture()
        self.assertTrue(await self.f.hass.config_entries.async_unload(self.entry.entry_id))
        await self.f.hass.async_block_till_done()
        before = u.read_entry(self.f.hass, self.entry.entry_id)
        rows = self._registry_document()
        inactive = await h.NativeHelperObservers.capture(self.f.hass, [before], self.proof)
        self.assertEqual(inactive.runtime_baselines(), ())
        self.assertTrue(await inactive.verify_runtime(before))
        self.assertEqual(self.entry.state.value, 'not_loaded')
        self.assertEqual(self._registry_document(), rows)
        self.assertEqual(inactive._jobs(self.entry.entry_id), [])

    async def test_disabled_entry_stays_inactive_without_counter_baseline(self):
        from homeassistant.config_entries import ConfigEntryDisabler
        await self._fixture()
        await self.f.hass.config_entries.async_set_disabled_by(
            self.entry.entry_id, ConfigEntryDisabler.USER)
        await self.f.hass.async_block_till_done()
        before = u.read_entry(self.f.hass, self.entry.entry_id)
        observer = await h.NativeHelperObservers.capture(self.f.hass, [before], self.proof)
        self.assertEqual(observer.runtime_baselines(), ())
        self.assertTrue(await observer.verify_runtime(before))
        self.assertEqual(self.entry.state.value, 'not_loaded')
        self.assertIs(self.entry.disabled_by, ConfigEntryDisabler.USER)

    async def test_loaded_disabled_entity_and_mixed_scope_block_before_observation(self):
        from homeassistant.helpers import entity_registry as er
        await self._fixture()
        self.f.entity.async_update_entity(self.entity.entity_id,
                                         disabled_by=er.RegistryEntryDisabler.USER)
        await self.f.hass.async_block_till_done()
        with self.assertRaises(h.ObservationUnavailable):
            await h.NativeHelperObservers.capture(self.f.hass, [self.before], self.proof)
        foreign = deepcopy(self.before)
        foreign['domain'] = 'integration'
        with self.assertRaisesRegex(h.ObservationUnavailable, 'mixed_scope'):
            await h.NativeHelperObservers.capture(self.f.hass, [self.before, foreign], self.proof)
