"""Research probe: CPython call/return evidence, not migration acceptance.

No HA writer/function/state is replaced. Native setup alone performs changes.
The profiler is diagnostic and always removed; no product code consumes it yet.
"""
from copy import deepcopy
import json
import sys
import unittest

import test_utility_meter_observers as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'native HA dependencies required')
class NativeUtilitySetupAuditProbe(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ActualUtilityMeterObserverTests.asyncSetUp
    _before_start = fixture.ActualUtilityMeterObserverTests._before_start
    _fixture = fixture.ActualUtilityMeterObserverTests._fixture
    _emit = fixture.ActualUtilityMeterObserverTests._emit

    async def test_actual_native_call_return_and_event_identity_can_be_observed(self):
        from homeassistant.core import Event, EventBus
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        observer = await self._fixture()
        hass = self.f.hass
        helper_id = self.entity.entity_id
        helper_uuid = self.entity.registry_entry.id
        self.assertIsNone(sys.getprofile(), 'existing profiler must never be replaced')
        self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
        await hass.async_block_till_done()
        def row(value):
            return json.loads(_orjson_bytes_default_encoder(value.as_storage_fragment))
        original = row(self.f.entity.async_get(helper_id))
        writer_code = er.EntityRegistry._async_update_entity.__code__
        fire_code = EventBus.async_fire_internal.__code__
        event_code = Event.__init__.__code__
        calls, active, emitted, events, errors = [], {}, {}, {}, []
        def ancestors(frame):
            rows = []
            frame = frame.f_back
            while frame is not None and len(rows) < 24:
                local = frame.f_locals
                obj = local.get('self')
                native_event = local.get('event')
                rows.append((frame.f_code.co_qualname,
                    type(obj).__module__ + '.' + type(obj).__qualname__,
                    id(native_event) if native_event is not None else None))
                frame = frame.f_back
            return rows
        def profile(frame, action, result):
            try:
                code, local = frame.f_code, frame.f_locals
                if code is writer_code and local.get('self') is self.f.entity:
                    if action == 'call':
                        target = self.f.entity.async_get(local['entity_id'])
                        value = {'before': row(target), 'callers': ancestors(frame),
                                 'frame_id': id(frame)}
                        calls.append(value)  # Native writes may synchronously nest.
                        active[id(frame)] = value
                    elif action == 'return':
                        value = active.pop(id(frame))
                        value['after'] = row(result)
                        value['manager'] = row(self.f.entity.async_get(result.entity_id))
                        value['return_uuid'] = result.id
                elif code is fire_code and action == 'call':
                    if local.get('event_type') == 'entity_registry_updated':
                        data = local.get('event_data')
                        parent = frame.f_back
                        if parent is not None and parent.f_code is writer_code:
                            emitted[id(data)] = (data, deepcopy(data), id(parent),
                                row(self.f.entity.async_get(data['entity_id'])))
                elif code is event_code and action == 'call':
                    if local.get('event_type') == 'entity_registry_updated':
                        data = local.get('data')
                        if id(data) in emitted:
                            events[id(local['self'])] = (local['self'], id(data))
            except BaseException as error:
                # A profiler exception must not alter execution of the HA writer.
                errors.append(type(error).__name__)
        sys.setprofile(profile)
        try:
            self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
            await hass.async_block_till_done()
            self.assertIs(sys.getprofile(), profile)
        finally:
            sys.setprofile(None)
        self.assertIsNone(sys.getprofile())
        self.assertEqual(errors, [])
        self.assertFalse(active)
        self.assertTrue(calls)
        previous = original
        for call in calls:
            self.assertEqual(call['before'], previous)
            matching = [item for item in emitted.values() if item[2] == call['frame_id']]
            if call['before'] != call['after']:
                self.assertEqual(len(matching), 1)
                self.assertEqual(call['after'], matching[0][3])
            else:
                self.assertEqual(matching, [])
            # Nested synchronous registry callbacks may have committed a later
            # audited row before this outer native function returns.
            self.assertEqual(call['manager']['id'], helper_uuid)
            self.assertEqual(call['return_uuid'], helper_uuid)
            previous = call['after']
        self.assertEqual(previous, row(self.f.entity.async_get(helper_id)))
        self.assertEqual({k: v for k, v in previous.items() if k != 'modified_at'},
                         {k: v for k, v in original.items() if k != 'modified_at'})
        self.assertNotEqual(previous['modified_at'], original['modified_at'])
        self.assertTrue(emitted)
        self.assertTrue(events)
        self.assertEqual(len(events), len(emitted))
        self.assertTrue(all(data == saved for data, saved, _, _ in emitted.values()))
        # Persist only a compact synthetic diagnostic. Lineage is observed here;
        # no claim that these callers form a sufficient authorization contract.
        print(json.dumps({'probe': 'native_utility_setup_calls',
            'mutations': [{'changed': sorted(k for k in call['before']
                            if call['before'][k] != call['after'][k]),
                           'callers': call['callers']} for call in calls],
            'native_emissions': len(emitted), 'event_objects': len(events)}))
