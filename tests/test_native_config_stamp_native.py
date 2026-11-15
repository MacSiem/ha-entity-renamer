"""Actual Core ConfigEntry call rows, boolean no-op and independent raw read.

Isolated synthetic config entry. This qualifies the call primitive, not helper
domain activation, listener provenance or migration readiness.
"""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import unittest

import test_native_input_baseline as fixtures


@unittest.skipUnless(fixtures.HAS_HA, 'native HA dependencies required')
class NativeConfigStampTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_update_noop_and_reverse_keep_full_storage_rows(self):
        f = fixtures.NativeBaselineTests('test_actual_native_serialization_and_store_full_baseline')
        await f.asyncSetUp()
        self.addAsyncCleanup(f.asyncTearDown)
        helper = importlib.import_module(fixtures.PACKAGE + '.helper_native')
        observation = importlib.import_module(fixtures.PACKAGE + '.native_input_observation')
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        entry = f.entry
        original = json.loads(_orjson_bytes_default_encoder(entry.as_dict()))
        options = deepcopy(original['options'])
        options['call_stamp_fixture'] = {'entity_id': 'sensor.renamed', 'preserve_types': [True, 1, 1.0]}
        stamp = helper._mutate_entry(f.hass.config_entries, entry,
                                    data=entry.data, options=options)
        self.assertIs(type(stamp), helper.NativeConfigMutation)
        self.assertIs(stamp.changed, True)
        self.assertEqual(json.loads(stamp.before_json), original)
        returned = json.loads(stamp.after_json)
        self.assertEqual(returned, json.loads(_orjson_bytes_default_encoder(entry.as_dict())))
        self.assertEqual({k: v for k, v in original.items() if k not in ('options', 'modified_at')},
                         {k: v for k, v in returned.items() if k not in ('options', 'modified_at')})
        self.assertIn('created_at', returned)
        self.assertIn('discovery_keys', returned)
        self.assertTrue(returned['subentries'])
        self.assertEqual(returned['options'], options)
        self.assertNotEqual(returned['modified_at'], original['modified_at'])
        noop = helper._mutate_entry(f.hass.config_entries, entry,
                                   data=entry.data, options=entry.options)
        self.assertIs(noop.changed, False)
        self.assertEqual(noop.before_json, noop.after_json)
        self.assertEqual(json.loads(noop.after_json), returned)
        await f.hass.async_block_till_done()
        await f.hass.config_entries._store.async_save(f.hass.config_entries._data_to_save())
        observed = await observation.async_observe_native_inputs(f.hass, f.receipt, guard=f.guard)
        observed_row = next(row for row in json.loads(observed.files[2].raw)['data']['entries']
                            if row['entry_id'] == entry.entry_id)
        self.assertEqual(observed_row, returned)
        self.assertEqual(observed.files[2].raw, Path(f.hass.config_entries._store.path).read_bytes())
        reverse = helper._mutate_entry(f.hass.config_entries, entry,
                                      data=original['data'], options=original['options'])
        self.assertIs(reverse.changed, True)
        self.assertEqual(json.loads(reverse.before_json), returned)
        restored = json.loads(reverse.after_json)
        self.assertEqual({k: v for k, v in restored.items() if k != 'modified_at'},
                         {k: v for k, v in original.items() if k != 'modified_at'})
        await f.hass.async_block_till_done()
        await f.hass.config_entries._store.async_save(f.hass.config_entries._data_to_save())
        observed = await observation.async_observe_native_inputs(f.hass, f.receipt, guard=f.guard)
        self.assertEqual(next(row for row in json.loads(observed.files[2].raw)['data']['entries']
                              if row['entry_id'] == entry.entry_id), restored)
        with self.assertRaises(ValueError):
            fixtures.validation.assert_validation_current(f.receipt)


if __name__ == '__main__':
    unittest.main()
