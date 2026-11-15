"""Behavior of the mixed fixture's observable stimulus and delayed measurements."""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_energy_continuity_mixed_cold_native import MixedPowercalcColdPhase


class MixedEnergyHarnessTests(unittest.IsolatedAsyncioTestCase):
    def make_phase(self):
        phase = MixedPowercalcColdPhase(methodName='test_phase')
        phase.mode, phase.phase = 'migration', 'forward'
        phase.cold_evidence = {
            'before': {'entities': {'sensor.public_sum_energy': {'uuid': 'group-uuid'}}},
            'apply': {'cost': {'registry_id': 'cost-uuid'}}}
        states = {'sensor.synthetic_new_sum_energy': SimpleNamespace(state='0.0001'),
                  'sensor.public_sum_energy_cost': SimpleNamespace(state='0.00005'),
                  'sensor.public_static_power': SimpleNamespace(state='10'),
                  'light.source': SimpleNamespace(state='off', entity_id='light.source')}
        registry = {'sensor.synthetic_new_sum_energy': SimpleNamespace(id='group-uuid'),
                    'sensor.public_sum_energy_cost': SimpleNamespace(id='cost-uuid')}
        hass = SimpleNamespace(states=states, data={'energy': {'cost_sensors': {
            'sensor.synthetic_new_sum_energy': 'sensor.public_sum_energy_cost'}}},
            async_block_till_done=AsyncMock())
        phase.f = SimpleNamespace(hass=hass, entity=SimpleNamespace(async_get=registry.get))
        async def turn_on(**_):
            states['light.source'].state = 'on'
            states['sensor.synthetic_new_sum_energy'].state = '0.000104'
            states['sensor.public_sum_energy_cost'].state = '0.000053'
        async def turn_off():
            states['light.source'].state = 'off'
        phase.sources = [SimpleNamespace(entity_id='light.source', async_turn_on=turn_on,
                                         async_turn_off=turn_off)]
        return phase

    async def test_observed_uuid_source_and_price_delta(self):
        phase = self.make_phase()
        await phase._pulse_light(255)
        self.assertEqual(phase.cold_evidence['stimulus_observations'][0]['energy_kwh'], '0.000004')
        self.assertEqual(phase.cold_evidence['stimulus_observations'][0]['cost'], '0.000003')
        self.assertEqual(phase.f.hass.states['light.source'].state, 'off')
        self.assertNotIn('measurement_failures', phase.cold_evidence)

    async def test_timeout_records_measurement_and_turns_source_off(self):
        phase = self.make_phase()
        class ImmediateTimeout:
            async def __aenter__(self):
                raise TimeoutError
            async def __aexit__(self, *args):
                return False
        with patch('test_energy_continuity_mixed_cold_native.asyncio.timeout', return_value=ImmediateTimeout()):
            await phase._pulse_light(255)
        self.assertEqual(phase.cold_evidence['measurement_failures'][0]['phase'], 'published_stimulus_forward')
        self.assertEqual(phase.f.hass.states['light.source'].state, 'off')

    async def test_cold_sum_waits_for_late_native_publication(self):
        phase = self.make_phase()
        phase.meta = {'stored_total': '0.1'}
        phase.initial_members = {}
        phase.sample_events = []
        group = SimpleNamespace(entity_id='sensor.group', _native_value_exact=Decimal('0.0'))
        self.assertIsNone(phase._verify_cold_sum(group))
        self.assertEqual(phase._cold_sum_pending, 'sensor.group')
        group._native_value_exact = Decimal('0.1')
        phase._find_group = lambda _: group
        await phase._settle_cold_sum()
        self.assertIsNone(phase._cold_sum_pending)
        self.assertNotIn('measurement_failures', phase.cold_evidence)

    async def test_inspect_requires_eligibility_before_any_inverse(self):
        phase = self.make_phase()
        phase.phase, phase.meta = 'resume', {'user_id': 'same-user'}
        phase.cold_evidence['requests'] = []
        phase._capture = AsyncMock()
        phase.f.hass.http = SimpleNamespace(server_port=1234)
        auth_store = SimpleNamespace(_store=SimpleNamespace(async_save=AsyncMock()),
                                     _data_to_save=lambda: {})
        phase.f.hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True)),
            async_create_refresh_token=AsyncMock(return_value=object()),
            async_create_access_token=lambda *_args, **_kwargs: 'synthetic-token', _store=auth_store)
        sent = []
        class Socket:
            def __init__(self):
                self.results = iter([{'type': 'auth_required'}, {'type': 'auth_ok'},
                    {'success': True, 'result': {'state': 'applied', 'eligible': False}}])
            async def receive_json(self, **_kwargs):
                return next(self.results)
            async def send_json(self, value):
                sent.append(value.get('type'))
            async def close(self):
                pass
        client = SimpleNamespace(ws_connect=AsyncMock(return_value=Socket()), close=AsyncMock())
        with patch('aiohttp.ClientSession', return_value=client):
            request, _, client_witness = await phase._transport()
            with self.assertRaises(AssertionError):
                await request(1, 'recovery/inspect', operation_id='op', fingerprint='fp')
            await client_witness.close()
        self.assertEqual(sent, ['auth', 'ha_entity_renamer/recovery/inspect'])

    async def test_failed_measurement_does_not_prevent_fresh_inspect(self):
        phase = self.make_phase()
        phase.phase, phase.meta = 'resume', {'user_id': 'same-user'}
        phase.cold_evidence['requests'] = []
        async def capture(label):
            if label == 'apply':
                phase.cold_evidence['apply'] = {'cost': {'energy_id': 'sensor.synthetic_new_sum_energy',
                    'price': 0.75, 'count': 0, 'actual_ids': [], 'entity_id': 'sensor.cost'}}
        phase._capture = capture
        phase.f.hass.http = SimpleNamespace(server_port=1234)
        auth_store = SimpleNamespace(_store=SimpleNamespace(async_save=AsyncMock()),
                                     _data_to_save=lambda: {})
        phase.f.hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True)),
            async_create_refresh_token=AsyncMock(return_value=object()),
            async_create_access_token=lambda *_args, **_kwargs: 'synthetic-token', _store=auth_store)
        sent = []
        class Socket:
            def __init__(self):
                self.results = iter([{'type': 'auth_required'}, {'type': 'auth_ok'},
                    {'success': True, 'result': {'state': 'applied', 'eligible': True}}])
            async def receive_json(self, **_kwargs):
                return next(self.results)
            async def send_json(self, value):
                sent.append(value.get('type'))
            async def close(self):
                pass
        client = SimpleNamespace(ws_connect=AsyncMock(side_effect=lambda *_args: Socket()), close=AsyncMock())
        with patch('aiohttp.ClientSession', return_value=client):
            request, _, client_witness = await phase._transport()
            self.assertTrue((await request(1, 'apply'))['success'])
            self.assertTrue((await request(2, 'recovery/inspect'))['result']['eligible'])
            await client_witness.close()
        self.assertEqual(phase.cold_evidence['measurement_failures'][0]['phase'], 'sample_apply')
        self.assertEqual(sent, ['auth', 'ha_entity_renamer/apply',
                                'auth', 'ha_entity_renamer/recovery/inspect'])
