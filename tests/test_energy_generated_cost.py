"""Typed generated-cost scope and durable Energy receipt validation."""
from copy import deepcopy
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from custom_components.ha_entity_renamer.energy_native import (
    EnergySnapshot, NativeEnergy, PreparedEnergy, generated_cost_moves, revision)
from custom_components.ha_entity_renamer.persistence import PersistenceError
from custom_components.ha_entity_renamer.powercalc_energy_receipts import (
    _cost_registry_advance, _cost_registry_projection, _generated_cost_evidence)
from custom_components.ha_entity_renamer.powercalc_energy_transaction import _cost_registry_preimage
from custom_components.ha_entity_renamer.native_powercalc_accepted_consumers import _literal_documents
from custom_components.ha_entity_renamer.transaction import Refused


def grid(source='sensor.old', *, cost=None, price=0.75):
    return {'type': 'grid', 'stat_energy_from': source, 'stat_cost': cost,
            'entity_energy_price': None, 'number_energy_price': price}


class GeneratedCostTests(unittest.TestCase):
    def test_accepted_powercalc_image_replays_only_receipted_cost_fields(self):
        original = {'core.entity_registry': {'data': {'entities': [
            {'id': 'costuuid', 'entity_id': 'sensor.cost', 'suggested_object_id': 'old_cost',
             'modified_at': '2026-11-06T07:00:00+00:00', 'name': 'user name'},
            {'id': 'foreignuuid', 'entity_id': 'sensor.foreign', 'name': 'foreign'}]}},
            'core.device_registry': {'data': {'devices': []}},
            'core.config_entries': {'data': {'entries': []}}}
        before = deepcopy(original['core.entity_registry']['data']['entities'][0])
        after = deepcopy(before)
        after.update(suggested_object_id='new_cost', modified_at='2026-11-06T08:00:00+00:00')
        restored = deepcopy(before)
        restored['modified_at'] = '2026-11-06T09:00:00+00:00'
        def receipt(left, right):
            return {'kind': 'powercalc_energy_receipt', 'cost_registry_after': {'changes': [
                {'id': 'costuuid', 'before': left, 'after': right}]}}
        chain = SimpleNamespace(plan=SimpleNamespace(steps=()), changes=(),
            _setup_history=[('forward', receipt(before, after))])
        applied = deepcopy(original)
        applied['core.entity_registry']['data']['entities'][0] = deepcopy(after)
        self.assertTrue(_literal_documents(original, applied, original, chain))
        foreign = deepcopy(applied)
        foreign['core.entity_registry']['data']['entities'][1]['name'] = 'foreign edit'
        self.assertFalse(_literal_documents(original, foreign, original, chain))
        chain._setup_history.append(('rollback', receipt(after, restored)))
        inverse = deepcopy(original)
        inverse['core.entity_registry']['data']['entities'][0] = deepcopy(restored)
        self.assertTrue(_literal_documents(original, inverse, original, chain))
        invalid = deepcopy(original)
        invalid['core.entity_registry']['data']['entities'][0]['name'] = 'unexpected'
        with self.assertRaises(ValueError):
            _literal_documents(invalid, inverse, original, chain)

        # A generated cost may have no forward registry delta at all.
        chain._setup_history = [('forward', {'kind': 'powercalc_energy_receipt',
            'binding': {'direction': 'forward'}})]
        chain._effective_cold = {'updates': [{'registry_id': 'costuuid',
            'before': before['modified_at'], 'after': after['modified_at']}]}
        cold = deepcopy(original)
        cold['core.entity_registry']['data']['entities'][0]['modified_at'] = after['modified_at']
        self.assertTrue(_literal_documents(original, cold, original, chain))
        cold['core.entity_registry']['data']['entities'][1]['name'] = 'foreign edit'
        self.assertFalse(_literal_documents(original, cold, original, chain))
        rollback_before = deepcopy(before)
        rollback_before['modified_at'] = after['modified_at']
        rollback_after = deepcopy(before)
        rollback_after['modified_at'] = restored['modified_at']
        chain._setup_history.append(('rollback', receipt(rollback_before, rollback_after)))
        self.assertTrue(_literal_documents(original, inverse, original, chain))
        inverse_foreign = deepcopy(inverse)
        inverse_foreign['core.entity_registry']['data']['entities'][1]['name'] = 'foreign edit'
        self.assertFalse(_literal_documents(original, inverse_foreign, original, chain))

    def test_foreign_live_registry_or_pending_save_refused(self):
        original = {'entities': [{'id': 'foreign', 'disabled': False, 'number': 1}]}
        states = {'core.entity_registry': {'document': {'data': original}}}
        store = SimpleNamespace(_write_lock=asyncio.Lock(), _load_future=None, _data=None,
                                _delay_handle=None, _unsub_final_write_listener=None)
        live = deepcopy(original)
        registry = SimpleNamespace(_store=store, _data_to_save=lambda: live)
        _cost_registry_preimage(registry, states)
        live['entities'][0]['number'] = 1.0
        with self.assertRaises(Refused):
            _cost_registry_preimage(registry, states)
        live['entities'][0]['number'] = 1
        live['entities'][0]['disabled'] = True
        with self.assertRaises(Refused):
            _cost_registry_preimage(registry, states)
        live['entities'][0]['disabled'] = False
        store._delay_handle = object()
        with self.assertRaises(Refused):
            _cost_registry_preimage(registry, states)

    def test_registry_projection_accepts_only_native_bound_suggestion(self):
        original = {'data': {'entities': [
            {'id': 'costuuid', 'entity_id': 'sensor.cost', 'suggested_object_id': 'old_cost',
             'modified_at': '2026-11-06T07:00:00+00:00', 'name': 'user name'},
            {'id': 'foreignuuid', 'entity_id': 'sensor.foreign', 'name': 'foreign'}]}}
        cost = [{'registry_id': 'costuuid', 'entity_id': 'sensor.cost', 'after_source': 'sensor.new'}]
        updated = deepcopy(original)
        updated['data']['entities'][0].update(suggested_object_id='new_cost',
                                               modified_at='2026-11-06T08:00:00+00:00')
        changes = _cost_registry_projection(original, updated, cost)
        states = {'core.entity_registry': {'document': original, 'sha256': 'a' * 64,
                                            'identity': [1, 2, 33152]}}
        _cost_registry_advance(states, cost, {'changes': changes, 'sha256': 'b' * 64,
                                               'identity': [1, 3, 33152]})
        self.assertEqual(states['core.entity_registry']['document'], updated)
        for target, field, value in ((0, 'suggested_object_id', 'wrong_cost'),
                                     (0, 'name', 'foreign edit'), (1, 'name', 'foreign edit')):
            foreign = deepcopy(updated)
            foreign['data']['entities'][target][field] = value
            with self.assertRaises(Refused):
                _cost_registry_projection(original, foreign, cost)

    def test_only_changed_implicit_cost_is_rebound(self):
        before = {'energy_sources': [grid()]}
        after = {'energy_sources': [grid('sensor.new')]}
        self.assertEqual(generated_cost_moves(before, after), (('sensor.old', 'sensor.new'),))
        self.assertEqual(generated_cost_moves(before, before), ())
        before['energy_sources'][0]['stat_cost'] = 'sensor.explicit_cost'
        after['energy_sources'][0]['stat_cost'] = 'sensor.explicit_cost'
        self.assertEqual(generated_cost_moves(before, after), ())

    def test_unsupported_generator_and_duplicate_are_rejected(self):
        before, after = {'energy_sources': [grid()]}, {'energy_sources': [grid('sensor.new')]}
        before['energy_sources'][0]['type'] = after['energy_sources'][0]['type'] = 'gas'
        with self.assertRaises(PersistenceError):
            generated_cost_moves(before, after)
        before, after = {'energy_sources': [grid(), grid()]}, {'energy_sources': [grid('sensor.new'), grid('sensor.other')]}
        with self.assertRaises(PersistenceError):
            generated_cost_moves(before, after)

    def test_durable_identity_history_and_foreign_rejection_both_directions(self):
        before, after = {'energy_sources': [grid()]}, {'energy_sources': [grid('sensor.new')]}
        change = PreparedEnergy(EnergySnapshot(before, 'before', None), after, 'after')
        digest = 'a' * 64
        content = {name: {'count': 0, 'sha256': digest, **({} if name == 'attributes' else {'ceiling': None})}
                   for name in ('states', 'short_term', 'hourly', 'attributes')}
        metadata = {'states': {'sensor.cost': {'metadata_id': 7}},
                    'statistics': {'sensor.cost': {'id': 8, 'source': 'recorder'}}}
        for direction, old, new in (('forward', 'sensor.old', 'sensor.new'),
                                    ('rollback', 'sensor.new', 'sensor.old')):
            registry = {'core.entity_registry': {'document': {'data': {'entities': [
                {'entity_id': new, 'id': 'meteruuid'},
                {'entity_id': 'sensor.cost', 'id': 'costuuid', 'platform': 'energy',
                 'unique_id': 'meteruuid_grid_cost'}]}}}}
            initial = {'before_source': old, 'after_source': new, 'entity_id': 'sensor.cost',
                'registry_id': 'costuuid', 'unique_id': 'meteruuid_grid_cost', 'source_registry_id': 'meteruuid',
                'native_before': {'value': '0.25', 'last_reset': None}, 'metadata_before': metadata,
                'history_before': {'schema': 1, 'state_ids': [7], 'statistic_ids': [8], 'content': content}}
            result = {key: initial[key] for key in ('before_source', 'after_source', 'entity_id',
                'registry_id', 'unique_id', 'source_registry_id')}
            result.update(native_after={'value': '0.0', 'last_reset': None},
                          metadata_after=metadata, history_verified=True)
            _generated_cost_evidence(change, direction, registry, [initial], [result])
            for key in ('registry_id', 'source_registry_id', 'entity_id'):
                foreign = deepcopy(result)
                foreign[key] += '_foreign'
                with self.assertRaises(Refused):
                    _generated_cost_evidence(change, direction, registry, [initial], [foreign])
            altered = deepcopy(result)
            altered['metadata_after']['statistics']['sensor.cost']['id'] = 99
            with self.assertRaises(Refused):
                _generated_cost_evidence(change, direction, registry, [initial], [altered])
            altered = deepcopy(result)
            altered['native_after']['value'] = 'NaN'
            with self.assertRaises(Refused):
                _generated_cost_evidence(change, direction, registry, [initial], [altered])
            altered = deepcopy(initial)
            altered['history_before']['statistic_ids'] = [9]
            with self.assertRaises(Refused):
                _generated_cost_evidence(change, direction, registry, [altered], [result])
            with self.assertRaises(Refused):
                _generated_cost_evidence(change, direction, registry, [initial], [])


class GeneratedCostFenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_fence_and_identity_reject_before_detach(self):
        for direction in ('forward', 'rollback'):
            for conflict in ('fence', 'identity', 'after_detach'):
                with self.subTest(direction=direction, conflict=conflict):
                    old, new = (('sensor.old', 'sensor.new') if direction == 'forward'
                                else ('sensor.new', 'sensor.old'))
                    before, after = {'energy_sources': [grid(old)]}, {'energy_sources': [grid(new)]}
                    snapshot = EnergySnapshot(before, revision(before), None)
                    change = PreparedEnergy(snapshot, after, revision(after))
                    native = object.__new__(NativeEnergy)
                    native._lock, native._failed, native.allow_generated_costs = asyncio.Lock(), False, True
                    native.hass = object()
                    native.manager = type('Manager', (), {})()
                    native.manager.data = before
                    native.manager.async_update = AsyncMock()
                    native._settled = lambda: None
                    native.read = AsyncMock(return_value=snapshot)
                    entity = type('Cost', (), {})()
                    entity.async_remove = AsyncMock()
                    ids = ['costuuid', 'meteruuid_grid_cost', 'sensor.cost', 'meteruuid']
                    native._generated_cost = lambda *_: (entity, *ids)
                    native._cost_value = lambda _: {'value': '0.25', 'last_reset': None}
                    metadata = {'states': {}, 'statistics': {}}
                    preimage = [{'before_source': old, 'after_source': new, 'registry_id': ids[0],
                        'unique_id': ids[1], 'entity_id': ids[2], 'source_registry_id': ids[3],
                        'native_before': native._cost_value(entity), 'metadata_before': metadata,
                        'history_before': {}}]
                    recorder = type('Recorder', (), {'snapshot': AsyncMock(return_value=metadata),
                        'async_synchronize': AsyncMock()})()
                    history = type('History', (), {'verify': AsyncMock(return_value=True)})()
                    calls = 0
                    async def fence():
                        nonlocal calls
                        calls += 1
                        if conflict == 'fence' or (conflict == 'after_detach' and calls == 2):
                            raise Refused('foreign_change')
                        if conflict == 'identity':
                            ids[0] = 'foreignuuid'
                    with patch('custom_components.ha_entity_renamer.recorder_guard.NativeRecorderGuard', return_value=recorder), \
                         patch('custom_components.ha_entity_renamer.history_guard.NativeHistoryGuard', return_value=history):
                        with self.assertRaises((Refused, PersistenceError)):
                            await native.write(change, lambda: None, cost_preimage=preimage, before_cost_effect=fence)
                    if conflict == 'after_detach':
                        entity.async_remove.assert_awaited_once()
                    else:
                        entity.async_remove.assert_not_awaited()
                    native.manager.async_update.assert_not_awaited()
                    self.assertTrue(native._failed)


if __name__ == '__main__':
    unittest.main()
