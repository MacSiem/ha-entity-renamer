"""Real isolated Recorder metadata races; owner executes both Core via FIFO.

No fake history, seeded DB ids/ceilings/expected rows, native monkeypatch proof,
SQL mutations of our own, running HA access, or migration readiness claim.
Wrappers only position a real native queued metadata mutation in a negative test.
"""
import importlib
import json
from datetime import timedelta
import unittest
from unittest.mock import patch

import test_native_helper_quiescence as fixture

backend = importlib.import_module('custom_components.ha_entity_renamer')
subject = importlib.import_module(backend.__name__ + '.discovery')
recorder_module = importlib.import_module(backend.__name__ + '.recorder_guard')
history_module = importlib.import_module(backend.__name__ + '.history_guard')


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeRecorderLateFenceTests(unittest.IsolatedAsyncioTestCase):
    _flush_documents = fixture.NativeHelperQuiescenceTests._flush_documents

    async def _before_start(self, hass, root):
        from homeassistant.const import __version__
        from homeassistant.helpers.recorder import async_initialize_recorder
        from homeassistant.setup import async_setup_component
        if __version__ not in ('2025.6.0', '2026.10.2'):
            self.skipTest('exact native Recorder Core required')
        async_initialize_recorder(hass)
        self.assertTrue(await async_setup_component(hass, 'recorder', {'recorder': {
            'db_url': 'sqlite:///' + str(root / 'fixture.db'), 'commit_interval': 0,
            'auto_purge': False, 'auto_repack': False}}))

    async def asyncSetUp(self):
        await fixture.NativeHelperQuiescenceTests.asyncSetUp(self)
        from homeassistant.components.sensor import SensorEntity
        from homeassistant.helpers.entity_component import EntityComponent
        import logging
        class Producer(SensorEntity):
            _attr_unique_id = 'recorder-late-fence-source'
            _attr_name = 'Recorder late fence source'
            _attr_native_value = 11
            _attr_native_unit_of_measurement = 'kWh'
            _attr_should_poll = False
        component = EntityComponent(logging.getLogger(__name__), 'sensor', self.f.hass,
                                    timedelta(seconds=60))
        self.producer = Producer()
        await component.async_add_entities([self.producer])
        self.old = self.producer.entity_id
        self.new = 'sensor.recorder_late_fence_destination'
        self.foreign = 'sensor.recorder_late_fence_foreign'
        self.mapping = {self.old: self.new}
        self.guard = recorder_module.NativeRecorderGuard(self.f.hass)
        self.runtime = backend.Runtime('native-late-fence', object(), object())
        self.f.hass.data[backend.DATA_RUNTIME] = self.runtime
        self.proposal = {'entities': [{'entity_id': self.old,
            'proposed_entity_id': self.new}], 'devices': [], 'statistics': []}
        self.import_hour(self.old)
        self.initial = await self.guard.snapshot(self.mapping)
        self.assertIn(self.old, self.initial['states'])
        self.assertIn(self.old, self.initial['statistics'])
        self.assertNotIn(self.new, self.initial['statistics'])
        self.assertEqual(self.initial['statistics'][self.old]['unit_of_measurement'], 'kWh')
        await self._flush_documents(initialize=True)
        self.mutations = []
        # Genuine positive prerequisite, all reads performed by native guards.
        # Never calibrate expected history counts/ids to make this pass.
        initial_capture = await subject.async_discover(self.f.hass, self.proposal, lambda: None)
        for name in ('history', 'statistics'):
            row = next(row for row in initial_capture.families if row.name == name)
            self.assertEqual(row.status, 'COMPLETE', row.reason)
            payload = json.loads(row.payload_json)
            self.assertEqual(payload['metadata'], self.initial[
                'states' if name == 'history' else 'statistics'])
        self.assertFalse(initial_capture.summary()['migration_ready'])

    def import_hour(self, statistic_id):
        from homeassistant.components.recorder.statistics import async_import_statistics
        from homeassistant.components.recorder.models import StatisticMeanType
        from homeassistant.util import dt as dt_util
        metadata = {'statistic_id': statistic_id, 'source': 'recorder',
            'name': 'Native late fence fixture', 'unit_of_measurement': 'kWh',
            'has_sum': True, 'mean_type': StatisticMeanType.NONE}
        if self.f.version == '2026.10.2':
            metadata['unit_class'] = 'energy'
        start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        async_import_statistics(self.f.hass, metadata,
                                [{'start': start, 'state': 11.0, 'sum': 11.0}])

    async def mutate(self, kind):
        from homeassistant.components.recorder.tasks import UpdateStatisticsMetadataTask
        from homeassistant.helpers.typing import UNDEFINED
        if kind == 'membership':
            self.import_hour(self.new)
        else:
            arguments = {'on_done': None, 'statistic_id': self.old,
                'new_statistic_id': self.foreign if kind == 'statistic_id' else UNDEFINED,
                'new_unit_of_measurement': 'Wh' if kind == 'unit' else UNDEFINED}
            if self.f.version == '2026.10.2':
                arguments['new_unit_class'] = 'energy' if kind == 'unit' else UNDEFINED
            self.guard.instance.queue_task(UpdateStatisticsMetadataTask(**arguments))
        # Genuine native barrier + native SQL readback proves the mutation landed.
        current = await self.guard.snapshot({**self.mapping, self.foreign: self.foreign})
        self.assertNotEqual(current['statistics'], self.initial['statistics'])
        if kind == 'unit':
            self.assertEqual(current['statistics'][self.old]['unit_of_measurement'], 'Wh')
            self.assertEqual(current['statistics'][self.old]['id'],
                             self.initial['statistics'][self.old]['id'])
        elif kind == 'statistic_id':
            self.assertNotIn(self.old, current['statistics'])
            self.assertEqual(current['statistics'][self.foreign]['id'],
                             self.initial['statistics'][self.old]['id'])
        else:
            self.assertIn(self.new, current['statistics'])
            self.assertEqual(current['statistics'][self.old], self.initial['statistics'][self.old])
        self.mutations.append(kind)

    async def capture_or_refusal(self, awaitable):
        try:
            return await awaitable
        except ValueError:
            return None  # explicit source-fence refusal is also acceptable

    def assert_refused(self, capture):
        if capture is None:
            return
        families = {row.name: row for row in capture.families}
        self.assertNotEqual(families['statistics'].status, 'COMPLETE',
            'late native Recorder mutation retained stale COMPLETE statistics')
        self.assertFalse(capture.summary()['migration_ready'])

    async def test_unit_change_after_real_history_verify_cannot_keep_complete(self):
        original = history_module.NativeHistoryGuard.verify
        calls = []
        async def verify_then_mutate(native, snapshot):
            result = await original(native, snapshot)
            self.assertIs(result, True)
            calls.append(snapshot)
            await self.mutate('unit')
            return result
        with patch.object(history_module.NativeHistoryGuard, 'verify', verify_then_mutate):
            capture = await self.capture_or_refusal(subject.async_discover(
                self.f.hass, self.proposal, lambda: None))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.mutations, ['unit'])
        self.assert_refused(capture)

    async def test_statistic_id_change_after_final_consumer_await_cannot_keep_complete(self):
        consumers = importlib.import_module(backend.__name__ + '.native_discovery_consumers')
        original = consumers.async_observe_discovery_consumers
        calls = []
        async def observe_then_mutate(*args, **kwargs):
            result = await original(*args, **kwargs)
            calls.append(result)
            if len(calls) == 2:
                await self.mutate('statistic_id')
            return result
        with patch.object(consumers, 'async_observe_discovery_consumers', observe_then_mutate):
            capture = await self.capture_or_refusal(subject.async_discover(
                self.f.hass, self.proposal, lambda: None))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.mutations, ['statistic_id'])
        self.assert_refused(capture)

    async def test_destination_member_added_after_final_yaml_await_cannot_keep_complete(self):
        baseline = importlib.import_module(backend.__name__ + '.native_input_baseline')
        original = baseline._drained
        calls = []
        async def drained_then_mutate(*args, **kwargs):
            result = await original(*args, **kwargs)
            calls.append(result)
            if len(calls) == 3:
                await self.mutate('membership')
            return result
        with patch.object(baseline, '_drained', drained_then_mutate):
            capture = await self.capture_or_refusal(subject.async_discover(
                self.f.hass, self.proposal, lambda: None))
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.mutations, ['membership'])
        self.assert_refused(capture)

    async def test_runtime_final_authorization_refresh_cannot_return_stale_complete(self):
        calls = []
        async def refresh():
            calls.append(None)
            if len(calls) == 2:
                await self.mutate('unit')
        capture = await self.capture_or_refusal(self.runtime.discover(
            self.f.hass, self.proposal, guard=lambda: None, refresh=refresh))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.mutations, ['unit'])
        self.assert_refused(capture)

    async def test_websocket_final_real_user_refresh_cannot_send_stale_complete(self):
        await self._websocket_final_user_refresh(preview=False)

    async def test_websocket_preview_final_real_user_refresh_cannot_send_stale_complete(self):
        await self._websocket_final_user_refresh(preview=True)

    async def _websocket_final_user_refresh(self,*,preview):
        from homeassistant import auth
        self.f.hass.auth = await auth.auth_manager_from_config(self.f.hass, [], [])
        from homeassistant.auth.const import GROUP_ID_ADMIN
        user = await self.f.hass.auth.async_create_user('Native fixture administrator',
                                                       group_ids=[GROUP_ID_ADMIN])
        self.assertTrue(user.is_admin)
        self.assertTrue(user.is_active)
        ws = importlib.import_module(backend.__name__ + '.websocket_api')
        class Connection:
            def __init__(self, user):
                self.user, self.output = user, None
            def send_result(self, ident, result):
                self.output = ('result', result)
            def send_error(self, ident, code, message):
                self.output = ('error', code)
        connection = Connection(user)
        manager_type = type(self.f.hass.auth)
        original = manager_type.async_get_user
        calls = []
        async def read_real_user_then_mutate(manager, user_id):
            result = await original(manager, user_id)
            calls.append(user_id)
            # Handler pre-read, Runtime pre/post reads, then handler output read.
            if len(calls) == 4:
                await self.mutate('unit')
            return result
        with patch.object(manager_type, 'async_get_user', read_real_user_then_mutate):
            await ws._read_discovery(self.f.hass, connection,
                {'id': 1, 'proposal': self.proposal}, preview=preview)
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.mutations, ['unit'])
        self.assertIsNotNone(connection.output)
        if connection.output[0] == 'result':
            rows = {row['family']: row for row in connection.output[1]['families']}
            self.assertNotEqual(rows['statistics']['status'], 'COMPLETE',
                'handler sent COMPLETE after its last native metadata race')
        else:
            self.assertEqual(connection.output, ('error', 'discovery_unavailable'))
