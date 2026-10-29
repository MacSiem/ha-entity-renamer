"""Actual native ConfigEntry documents through strict full delta validation.

Synthetic inactive group entry: storage schema/call proof, not helper activation.
"""
from copy import deepcopy
import importlib
import json
from types import MappingProxyType
import unittest

import test_native_input_baseline as fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class NativeConfigTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_full_native_config_forward_reverse_preserves_other_entries(self):
        f = fixtures.NativeBaselineTests('test_actual_native_serialization_and_store_full_baseline')
        await f.asyncSetUp()
        self.addAsyncCleanup(f.asyncTearDown)
        from homeassistant.config_entries import ConfigEntry
        native = importlib.import_module(fixtures.PACKAGE + '.helper_native')
        helper = importlib.import_module(fixtures.PACKAGE + '.helper_transaction')
        transition = importlib.import_module(fixtures.PACKAGE + '.native_config_transition')
        observation = importlib.import_module(fixtures.PACKAGE + '.native_input_observation')
        entry = ConfigEntry(domain='group', title='Inactive native config delta', data={},
            options={'entities': [f.row.entity_id, f.other.entity_id],
                     'opaque_preserve': {'types': [True, 1, 1.0]}},
            discovery_keys=MappingProxyType({}), source='user', version=1, minor_version=1,
            subentries_data=[{'subentry_id': 'native-child', 'subentry_type': 'fixture',
                             'title': 'Preserved child', 'unique_id': None,
                             'data': {'opaque': [False, 1.0]}}], unique_id='native-config-delta')
        # Deliberately inactive, isolated synthetic entry. No integration setup.
        f.hass.config_entries._entries[entry.entry_id] = entry
        await f.hass.config_entries._store.async_save(f.hass.config_entries._data_to_save())
        await f.hass.async_block_till_done()
        f.receipt = fixtures.receipt_for(f.root, f.version)  # New preparation, before ANY effect.
        captured = await f.capture()
        source = captured.files[2].raw
        change = helper.prepare_helper(native.read_entry(f.hass, entry.entry_id),
                                       {f.row.entity_id: 'sensor.config_delta_renamed'})
        source_original = deepcopy(json.loads(source))
        for direction in ('forward', 'reverse'):
            desired = change.after if direction == 'forward' else change.before
            stamp = native._mutate_entry(f.hass.config_entries, entry,
                                         data=desired['data'], options=desired['options'])
            self.assertIs(stamp.changed, True)
            await f.hass.async_block_till_done()
            await f.hass.config_entries._store.async_save(f.hass.config_entries._data_to_save())
            observed = await observation.async_observe_native_inputs(f.hass, f.receipt,
                                                                   guard=f.guard)
            result = observed.files[2].raw
            audit = transition.NativeConfigEffectAudit(entry.entry_id, direction,
                fixtures.digest(source), fixtures.digest(result),
                transition.prepared_helper_digest(change),
                json.loads(stamp.before_json)['modified_at'],
                json.loads(stamp.after_json)['modified_at'])
            evidence = transition.validate_config_transition(source, result, change,
                version=f.version, direction=direction, native_effect_audit=audit,
                native_mutation=stamp)
            self.assertEqual(evidence.entry_id, entry.entry_id)
            self.assertEqual(observed.original_receipt_json, captured.receipt_json)
            self.assertEqual([row['entry_id'] for row in json.loads(result)['data']['entries']],
                             [row['entry_id'] for row in source_original['data']['entries']])
            other_before = [row for row in source_original['data']['entries']
                            if row['entry_id'] != entry.entry_id]
            other_after = [row for row in json.loads(result)['data']['entries']
                           if row['entry_id'] != entry.entry_id]
            self.assertEqual(other_before, other_after)
            source = result
        restored = next(row for row in json.loads(source)['data']['entries']
                        if row['entry_id'] == entry.entry_id)
        original = next(row for row in source_original['data']['entries']
                        if row['entry_id'] == entry.entry_id)
        self.assertEqual({k: v for k, v in restored.items() if k != 'modified_at'},
                         {k: v for k, v in original.items() if k != 'modified_at'})
        with self.assertRaises(ValueError):
            fixtures.validation.assert_validation_current(f.receipt)


if __name__ == '__main__':
    unittest.main()
