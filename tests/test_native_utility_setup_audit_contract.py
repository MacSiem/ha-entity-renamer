"""Authored only: owner runs through FIFO on both exact installed pins.

Offline tests exercise schema/replay, not historical authenticity. Native tests
use the real factory and sys.setprofile; no HA function/field/cache replacement.
No test here qualifies lifecycle, persistence, recorder, product overhead or AC.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import importlib
import sys
import unittest

import test_utility_meter_observers as fixture

u = fixture.u
a = importlib.import_module(fixture.fixtures.PACKAGE + '.native_utility_setup_audit')


def evidence(core='2026.10.2'):
    doc = fixture.baseline_document()
    doc['core'] = core
    if core == '2025.6.0':
        doc['before']['identity']['minor_version'] = 1
        doc['after']['identity']['minor_version'] = 1
        for key in ('aliases_v2', 'object_id_base'):
            doc['helper_rows'][0].pop(key)
    doc['helper_rows'][0]['original_device_class'] = 'energy'
    doc['helper_rows'][0]['options'] = {'sensor': {'suggested_display_precision': 2},
                                      'foreign_domain': {'preserved': True}}
    baseline = u.decode_runtime_baseline({'entry_id': 'entry', 'payload': doc})
    rows = deepcopy(doc['helper_rows'])
    # A previous accepted setup effect can change only this audit timestamp.
    rows[0]['modified_at'] = '2026-10-25T10:01:00+00:00'
    binding = a._bind(baseline, doc['before'], rows)[2]
    current = deepcopy(rows[0])
    calls, events = [], []
    steps = [('original_device_class', None), ('options', {'foreign_domain': {'preserved': True}}),
             ('original_device_class', 'energy'), ('options', deepcopy(current['options']))]
    for index, (name, value) in enumerate(steps):
        before = deepcopy(current)
        current[name] = deepcopy(value)
        current['modified_at'] = '2026-10-25T10:02:0' + str(index) + '+00:00'
        payload = {'action': 'update', 'entity_id': current['entity_id'],
                   'changes': {name: before[name]}}
        path = (['update', 'get_or_create', 'add'] if core == '2025.6.0'
                else ['get_or_create', 'add_impl']) if index == 0 else (
                    ['options', 'precision', 'sensor_add', 'finish_add', 'add'] if index == 1
                    else ['update', 'state', 'write_state', 'finish_add', 'add'] if index == 2
                    else ['options', 'precision', 'sensor_update', 'registry_callback'])
        args = {name: deepcopy(value)}
        if index == 0:
            # Exact native get_or_create forwards these explicit unchanged args.
            args['config_entry_id'] = 'entry'
            args['capabilities'] = before['capabilities']
        if index == 2:
            # Entity._async_write_ha_state explicitly forwards these preserved
            # values in addition to the changing original_device_class.
            for field in ('capabilities', 'supported_features') + (
                    ('original_name',) if core == '2026.10.2' else ()):
                args[field] = before[field]
        names = a._COMMON_ARGS | (a._EXTRA_ARGS if core == '2026.10.2' else set())
        calls.append({'index': index, 'before': before, 'after': deepcopy(current),
                      'arguments': args, 'undefined': sorted(names - args.keys()),
                      'cause': {'kind': 'platform_add', 'call': None} if index < 3 else
                               {'kind': 'registry_event', 'call': 2},
                      'path': path, 'emission': deepcopy(payload)})
        events.append({'call': index, 'payload': deepcopy(payload)})
    value = {'schema': 1, 'core': core, 'python': '3.14' if core == '2026.10.2' else '3.13',
             'entry_id': 'entry', 'source_pins': a._SOURCES[core], 'binding': binding,
             'calls': calls, 'events': events, 'final_rows': [current]}
    return baseline, doc['before'], rows, value


class SetupAuditOfflineContractTests(unittest.TestCase):
    def replay(self, inputs):
        baseline, snapshot, rows, value = inputs
        audit = a.decode_setup_audit({'entry_id': 'entry', 'payload': value})
        return a.validate_setup_audit(baseline, snapshot, rows, audit)

    def test_both_pins_replay_and_codec_is_frozen_and_detached(self):
        for core in a._SOURCES:
            inputs = evidence(core)
            baseline, snapshot, rows, value = inputs
            audit = a.decode_setup_audit({'entry_id': 'entry', 'payload': value})
            result = self.replay(inputs)
            self.assertEqual(result, value['final_rows'])
            self.assertEqual(a.encode_setup_audit(audit)['payload'], value)
            self.assertNotEqual(result[0]['modified_at'], rows[0]['modified_at'])
            self.assertEqual(a._without_stamp(result[0]), a._without_stamp(rows[0]))
            with self.assertRaises(FrozenInstanceError):
                audit.payload_json = '{}'
            value['calls'][0]['before']['name'] = 'changed'
            self.assertIsNone(a.encode_setup_audit(audit)['payload']['calls'][0]['before']['name'])

    def test_order_is_call_order_even_when_event_delivery_is_queued(self):
        inputs = evidence()
        value = inputs[-1]
        # Event dispatch order is not row mutation order, and has no UUID/time
        # substitute for lineage. Calls remain in native CALL order.
        value['events'] = [value['events'][0], value['events'][1],
                           value['events'][3], value['events'][2]]
        self.replay(inputs)
        value['calls'][2], value['calls'][3] = value['calls'][3], value['calls'][2]
        with self.assertRaises(a.UtilitySetupAuditError):
            self.replay(inputs)

    def test_native_noop_has_no_event_and_no_invented_timestamp(self):
        inputs = evidence()
        value = inputs[-1]
        last = deepcopy(value['calls'][2])
        last.update(index=3, before=deepcopy(value['calls'][2]['after']),
                    after=deepcopy(value['calls'][2]['after']), emission=None)
        # No-op before the original fourth call. It must not authorize events.
        value['calls'].insert(3, last)
        value['calls'][4]['index'] = 4
        value['events'][-1]['call'] = 4
        self.replay(inputs)
        value['calls'][4]['cause']['call'] = 3
        with self.assertRaisesRegex(a.UtilitySetupAuditError, 'lineage'):
            self.replay(inputs)

    def test_tamper_foreign_and_write_undo_never_become_setup_effect(self):
        for mutation in ('uuid', 'preference', 'name_undo', 'before_stamp',
                         'after_stamp', 'old_values', 'source', 'baseline', 'path',
                         'cause', 'final', 'unsupported', 'explicit_foreign_argument'):
            inputs = evidence()
            baseline, snapshot, rows, value = inputs
            call = value['calls'][0]
            if mutation == 'uuid':
                call['after']['id'] = 'different'
            elif mutation == 'preference':
                call['after']['hidden_by'] = 'user'
            elif mutation == 'name_undo':
                call['arguments']['name'] = 'foreign'
                call['undefined'].remove('name')
                call['after']['name'] = 'foreign'
            elif mutation == 'before_stamp':
                call['before']['modified_at'] = '2026-10-25T09:00:00+00:00'
            elif mutation == 'after_stamp':
                call['after']['modified_at'] = call['before']['modified_at']
            elif mutation == 'old_values':
                call['emission']['changes']['original_device_class'] = None
            elif mutation == 'source':
                rows[0]['entity_id'] = 'sensor.foreign'
            elif mutation == 'baseline':
                value['binding']['baseline_digest'] = '0' * 64
            elif mutation == 'path':
                call['path'] = ['update', 'foreign_callback', 'add']
            elif mutation == 'cause':
                value['calls'][3]['cause']['call'] = 3
            elif mutation == 'final':
                value['final_rows'][0]['options'] = {}
            elif mutation == 'unsupported':
                value['core'] = '2026.10.3'
            elif mutation == 'explicit_foreign_argument':
                args = value['calls'][2]['arguments']
                args['name'] = value['calls'][2]['before']['name']
                value['calls'][2]['undefined'].remove('name')
            with self.subTest(mutation=mutation), self.assertRaises(a.UtilitySetupAuditError):
                self.replay(inputs)

    def test_schema_duplicate_drop_fake_event_and_limits_fail_closed(self):
        for mutation in ('schema', 'opaque', 'extra', 'bool_index', 'duplicate_event',
                         'missing_event', 'fake_event', 'undefined_duplicate', 'calls_limit',
                         'events_limit', 'oversized', 'future_python'):
            inputs = evidence()
            value = inputs[-1]
            if mutation == 'schema':
                value['schema'] = True
            elif mutation == 'opaque':
                value['calls'][0]['arguments']['name'] = object()
            elif mutation == 'extra':
                value['authority'] = True
            elif mutation == 'bool_index':
                value['calls'][0]['index'] = False
            elif mutation == 'duplicate_event':
                value['events'].append(deepcopy(value['events'][0]))
            elif mutation == 'missing_event':
                value['events'].pop()
            elif mutation == 'fake_event':
                value['events'][0]['payload']['changes'] = {}
            elif mutation == 'undefined_duplicate':
                value['calls'][0]['undefined'].append('name')
            elif mutation == 'calls_limit':
                value['calls'] *= 17
            elif mutation == 'events_limit':
                value['events'] *= 33
            elif mutation == 'oversized':
                value['calls'][0]['before']['name'] = 'x' * 9000
            elif mutation == 'future_python':
                value['python'] = '3.15'
            with self.subTest(mutation=mutation), self.assertRaises(a.UtilitySetupAuditError):
                self.replay(inputs)
        baseline, snapshot, rows, value = evidence()
        raw = a._canonical(value)
        duplicate = raw.replace('"schema":1', '"schema":1,"schema":1')
        for bad in (duplicate, ' ' + raw):
            with self.assertRaises(a.UtilitySetupAuditError):
                a.encode_setup_audit(a.NativeUtilitySetupAuditData('entry', bad))


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'installed native HA dependencies required')
class SetupAuditNativeContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ActualUtilityMeterObserverTests.asyncSetUp
    _before_start = fixture.ActualUtilityMeterObserverTests._before_start
    _fixture = fixture.ActualUtilityMeterObserverTests._fixture
    _emit = fixture.ActualUtilityMeterObserverTests._emit

    async def prepare(self):
        observer = await self._fixture()
        baseline, = observer.runtime_baselines()
        hass = self.f.hass
        self.assertIsNone(sys.getprofile())
        self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
        await hass.async_block_till_done()
        rows = observer._rows(self.entry.entry_id)
        return baseline, deepcopy(self.before), rows

    async def test_actual_setup_calls_events_and_new_object_preserve_full_rows(self):
        baseline, snapshot, rows = await self.prepare()
        old = self.entity
        hass = self.f.hass
        with a.NativeUtilitySetupAudit(hass, baseline, snapshot, rows, guard=lambda: True) as collector:
            from homeassistant.const import Platform
            self.assertEqual(collector._native({Platform.SENSOR: {'precision': 2}}),
                             {'sensor': {'precision': 2}})
            self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
            await hass.async_block_till_done()
            audit = collector.finish()
            replayed = a.validate_setup_audit(baseline, snapshot, rows, audit)
            self.assertEqual(replayed, self.observer._rows(self.entry.entry_id))
            self.assertIsNot(collector._new_entity, old)
            self.assertEqual(collector.finish(), audit)
        self.assertIsNone(sys.getprofile())
        payload = a.encode_setup_audit(audit)['payload']
        self.assertTrue(payload['calls'])
        self.assertTrue(payload['events'])
        self.assertEqual(a._without_stamp(rows[0]), a._without_stamp(replayed[0]))
        # No assumed global count of writes/events; every actual mutation binds.
        self.assertEqual(len(payload['events']), sum(c['emission'] is not None
                                                    for c in payload['calls']))

    async def test_existing_profiler_is_refused_before_monitor_or_setup(self):
        baseline, snapshot, rows = await self.prepare()
        def other(frame, event, arg):
            return None
        sys.setprofile(other)
        try:
            with self.assertRaisesRegex(a.UtilitySetupAuditError, 'existing_profile'):
                with a.NativeUtilitySetupAudit(self.f.hass, baseline, snapshot, rows,
                                               guard=lambda: True):
                    self.fail('entry must not be entered')
            self.assertIs(sys.getprofile(), other)
            self.assertEqual(self.entry.state.value, 'not_loaded')
        finally:
            sys.setprofile(None)

    async def test_foreign_correct_public_api_write_and_undo_poison_audit(self):
        baseline, snapshot, rows = await self.prepare()
        hass = self.f.hass
        with self.assertRaises(a.UtilitySetupAuditError):
            with a.NativeUtilitySetupAudit(hass, baseline, snapshot, rows, guard=lambda: True) as collector:
                self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
                await hass.async_block_till_done()
                self.assertIsNone(collector._error, 'native setup must succeed before fault injection')
                old_name = rows[0]['name']
                self.f.entity.async_update_entity(rows[0]['entity_id'], name='Foreign')
                self.f.entity.async_update_entity(rows[0]['entity_id'], name=old_name)
                await hass.async_block_till_done()
                collector.finish()
        self.assertIsNone(sys.getprofile())

    async def test_fake_bus_event_cannot_authorize_native_registry_callback(self):
        baseline, snapshot, rows = await self.prepare()
        hass = self.f.hass
        with self.assertRaises(a.UtilitySetupAuditError):
            with a.NativeUtilitySetupAudit(hass, baseline, snapshot, rows, guard=lambda: True) as collector:
                self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
                await hass.async_block_till_done()
                self.assertIsNone(collector._error, 'native setup must succeed before fault injection')
                hass.bus.async_fire_internal('entity_registry_updated', {
                    'action': 'update', 'entity_id': rows[0]['entity_id'],
                    'changes': {'original_device_class': None}})
                await hass.async_block_till_done()
                collector.finish()
        self.assertIsNone(sys.getprofile())

    async def test_profile_loss_does_not_overwrite_replacement_and_monitor_unsubscribes(self):
        baseline, snapshot, rows = await self.prepare()
        hass = self.f.hass
        def other(frame, event, arg):
            return None
        collector = a.NativeUtilitySetupAudit(hass, baseline, snapshot, rows, guard=lambda: True)
        try:
            with self.assertRaises(a.UtilitySetupAuditError):
                with collector:
                    self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
                    await hass.async_block_till_done()
                    self.assertIsNone(collector._error, 'native setup must succeed before fault injection')
                    sys.setprofile(other)
                    collector.finish()
            self.assertIs(sys.getprofile(), other)
            self.assertIsNone(collector._unsubscribe)
        finally:
            sys.setprofile(None)

    async def test_profile_replaced_then_restored_is_still_poisoned(self):
        baseline, snapshot, rows = await self.prepare()
        hass = self.f.hass
        with self.assertRaises(a.UtilitySetupAuditError):
            with a.NativeUtilitySetupAudit(hass, baseline, snapshot, rows, guard=lambda: True) as collector:
                own = sys.getprofile()
                sys.setprofile(None)
                sys.setprofile(own)
                collector.finish()
        self.assertIsNone(sys.getprofile())

    async def test_profile_callback_overflow_never_raises_into_ha_writer(self):
        baseline, snapshot, rows = await self.prepare()
        hass = self.f.hass
        collector = a.NativeUtilitySetupAudit(hass, baseline, snapshot, rows, guard=lambda: True)
        with self.assertRaises(a.UtilitySetupAuditError):
            with collector:
                # Fill private collector bookkeeping only. Native methods remain
                # untouched; native setup must succeed despite poisoned evidence.
                collector._calls[:] = [None] * a._MAX_CALLS
                self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
                await hass.async_block_till_done()
                self.assertIsNotNone(collector._error)
                collector.finish()
        self.assertIsNone(sys.getprofile())

    async def test_foreign_noop_is_a_foreign_call_and_finish_outside_context_refuses(self):
        baseline, snapshot, rows = await self.prepare()
        hass = self.f.hass
        collector = a.NativeUtilitySetupAudit(hass, baseline, snapshot, rows, guard=lambda: True)
        with self.assertRaises(a.UtilitySetupAuditError):
            with collector:
                self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
                await hass.async_block_till_done()
                self.assertIsNone(collector._error, 'native setup must succeed before fault injection')
                # No mutation/event is needed to discover an unknown caller.
                self.f.entity.async_update_entity(rows[0]['entity_id'], name=rows[0]['name'])
                collector.finish()
        self.assertIsNone(sys.getprofile())
        with self.assertRaises(a.UtilitySetupAuditError):
            collector.finish()
