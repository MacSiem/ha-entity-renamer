"""Synthetic regression sources; tests NOT RUN by patch-artifact worker."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.ha_entity_renamer.helper_native import (
    NativeConfigMutation, _mutate_entry, apply_entry, read_entry)
from custom_components.ha_entity_renamer.helper_plan import plan_helper
from custom_components.ha_entity_renamer.helper_transaction import (
    HelperTransactionAdapter, prepare_helper)
from custom_components.ha_entity_renamer.prepared import decode_change, encode_change
from custom_components.ha_entity_renamer.transaction import Step


MAP = {'sensor.before': 'sensor.after'}


def fixture():
    events = []
    entry = SimpleNamespace(entry_id='helper', domain='group', unique_id='fixed',
        title='Display', version=1, minor_version=1, source='user', disabled_by=None,
        pref_disable_new_entities=False, pref_disable_polling=False, data={},
        options={'entities': ['sensor.before']}, subentries={},
        state=SimpleNamespace(value='loaded'), modified_at='before-audit')
    # The full storage projection carries fields omitted by helper read_entry.
    def as_dict():
        events.append('row')
        return dict(entry_id=entry.entry_id, domain=entry.domain, data=entry.data,
            options=entry.options, modified_at=entry.modified_at,
            created_at='created-audit', discovery_keys={'original': ['a', 'b']},
            subentries=[{'subentry_id': 'second', 'data': {'value': True}},
                        {'subentry_id': 'first', 'data': {'value': 1.0}}],
            unknown={'native_date': datetime(2026, 10, 25, tzinfo=timezone.utc)})
    entry.as_dict = as_dict
    def update(actual, **values):
        events.append('update')
        changed = actual.data != values['data'] or actual.options != values['options']
        if changed:
            actual.data, actual.options = deepcopy(values['data']), deepcopy(values['options'])
            actual.modified_at = 'native-audit'
        return changed
    async def reload(_):
        events.append('reload')
        entry.modified_at = 'reload-audit'
        return True
    manager = SimpleNamespace(async_get_entry=lambda _: entry,
        async_update_entry=update, async_reload=reload)
    hass = SimpleNamespace(config_entries=manager)
    def encoder(value):
        events.append('encode')
        return json.dumps(value, default=lambda v: v.isoformat(), allow_nan=False).encode()
    module = SimpleNamespace(_orjson_bytes_default_encoder=encoder)
    return hass, entry, events, module


class ConfigMutationTests(unittest.TestCase):
    def test_exact_native_rows_and_bool_false_are_recorded(self):
        hass, entry, events, module = fixture()
        with patch.dict(sys.modules, {'homeassistant.helpers.json': module}):
            stamp = _mutate_entry(hass.config_entries, entry, data=entry.data,
                                  options=entry.options)
        self.assertIs(type(stamp), NativeConfigMutation)
        self.assertIs(stamp.changed, False)
        self.assertEqual(stamp.before_json, stamp.after_json)
        self.assertEqual(events, ['row', 'encode', 'update', 'row', 'encode'])
        row = json.loads(stamp.before_json)
        self.assertEqual(row['modified_at'], 'before-audit')
        self.assertEqual(row['created_at'], 'created-audit')
        self.assertEqual(row['discovery_keys'], {'original': ['a', 'b']})
        self.assertEqual([r['subentry_id'] for r in row['subentries']], ['second', 'first'])
        self.assertIs(type(row['subentries'][0]['data']['value']), bool)
        self.assertIs(type(row['subentries'][1]['data']['value']), float)
        self.assertEqual(row['unknown']['native_date'], '2026-10-25T00:00:00+00:00')
        with self.assertRaises(FrozenInstanceError):
            stamp.changed = True

    def test_non_bool_return_is_unknown_but_after_is_still_serialized(self):
        hass, entry, events, module = fixture()
        original = hass.config_entries.async_update_entry
        def update(*args, **kwargs):
            original(*args, **kwargs)
            return 1  # Must not coerce an incompatible native contract into True.
        hass.config_entries.async_update_entry = update
        with patch.dict(sys.modules, {'homeassistant.helpers.json': module}):
            stamp = _mutate_entry(hass.config_entries, entry, data={}, options={})
        self.assertIsNone(stamp)
        self.assertEqual(events, ['row', 'encode', 'update', 'row', 'encode'])
        self.assertEqual(entry.options, {})

    def test_failed_after_serialization_never_fabricates_stamp_or_retries(self):
        hass, entry, events, module = fixture()
        original = entry.as_dict
        def broken_after():
            if 'update' in events:
                raise ValueError('serialization unavailable after native call')
            return original()
        entry.as_dict = broken_after
        with patch.dict(sys.modules, {'homeassistant.helpers.json': module}):
            stamp = _mutate_entry(hass.config_entries, entry, data={}, options={})
        self.assertIsNone(stamp)
        self.assertEqual(events.count('update'), 1)
        self.assertEqual(entry.options, {})


class StampPropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_captures_before_reload_and_retains_existing_callbacks(self):
        hass, entry, events, module = fixture()
        before = read_entry(hass, 'helper')
        plan = plan_helper(before, MAP)
        async def settle(): events.append('settle')
        async def intent(*_): events.append('intent')
        async def verify(*_): events.append('runtime')
        async def validate(*_): pass
        with patch.dict(sys.modules, {'homeassistant.helpers.json': module}):
            result = await apply_entry(hass, plan, guard=lambda: None, settle=settle,
                record_intent=intent, verify_runtime=verify, validate_snapshot=validate)
        stamp = result['_native_config_mutation']
        self.assertIs(stamp.changed, True)
        self.assertEqual(stamp.entry_id, 'helper')
        self.assertEqual(json.loads(stamp.before_json)['modified_at'], 'before-audit')
        self.assertEqual(json.loads(stamp.after_json)['modified_at'], 'native-audit')
        self.assertEqual(entry.modified_at, 'reload-audit')
        self.assertEqual(events, ['settle', 'intent', 'row', 'encode', 'update',
                                  'row', 'encode', 'reload', 'settle', 'runtime'])
        self.assertEqual(result['persistence'], 'unknown')
        self.assertIs(result['adapter_write'], True)
        self.assertEqual(json.loads(stamp.after_json)['options'], plan.after['options'])
        entry.options['foreign'] = 'later'
        self.assertNotIn('foreign', json.loads(stamp.after_json)['options'])

    async def test_already_native_noop_has_no_fabricated_stamp_or_call(self):
        hass, entry, events, module = fixture()
        entry.domain, entry.options = 'powercalc', {}
        entry.data = {'entity_id': 'sensor.before'}
        plan = plan_helper(read_entry(hass, 'helper'), MAP)
        entry.data = deepcopy(plan.after['data'])
        async def callback(*_): pass
        with patch.dict(sys.modules, {'homeassistant.helpers.json': module}):
            result = await apply_entry(hass, plan, guard=lambda: None, settle=callback,
                record_intent=callback, verify_runtime=callback, validate_snapshot=callback)
        self.assertIsNone(result['_native_config_mutation'])
        self.assertIs(result['adapter_write'], False)
        self.assertEqual(events, [])

    async def test_adapter_propagates_trusted_result_after_flush_both_directions(self):
        hass, entry, _, _ = fixture()
        change = prepare_helper(read_entry(hass, 'helper'), MAP)
        self.assertEqual(change, decode_change(encode_change(change)))
        self.assertEqual(set(encode_change(change)['data']), {'before', 'after', 'mapping'})
        step = Step('helper', 'helpers', change.identity, change.before_revision,
                    change.after_revision, 'prepared')
        events = []
        async def callback(*_): pass
        async def flush(desired, guard):
            guard()
            events.append('flush')
        adapter = HelperTransactionAdapter(hass, lambda _: change, resolve_step=lambda _: None,
            settle=callback, validate_snapshot=callback, verify_activation=callback,
            verify_runtime=callback, verify_inventory=callback,
            verify_original_inventory=callback, persistence=SimpleNamespace(flush=flush))
        async def apply_stub(_, plan, **kwargs):
            events.append('apply')
            entry.data, entry.options = deepcopy(plan.after['data']), deepcopy(plan.after['options'])
            return result
        result = {'_native_config_mutation': NativeConfigMutation('helper', '{}', '{}', True)}
        with patch('custom_components.ha_entity_renamer.helper_transaction.apply_entry',
                   side_effect=apply_stub):
            self.assertIs(await adapter.apply(step, lambda: None), result)
            self.assertIs(await adapter.compensate(step, lambda: None), result)
        self.assertEqual(events, ['apply', 'flush', 'apply', 'flush'])

    async def test_adapter_preserves_apply_native_result_and_flush_failure(self):
        hass, entry, _, _ = fixture()
        entry.domain, entry.options = 'powercalc', {}
        entry.data = {'entity_id': 'sensor.before'}
        change = prepare_helper(read_entry(hass, 'helper'), MAP)
        entry.data = deepcopy(change.after['data'])
        adapter = object.__new__(HelperTransactionAdapter)
        adapter._change = lambda _: change
        adapter.hass = hass
        async def callback(*_): pass
        adapter.settle = adapter.validate_snapshot = callback
        adapter.verify_activation = adapter.verify_runtime = callback
        adapter.persistence = SimpleNamespace(flush=AsyncMock())
        step = SimpleNamespace(caused_by=('registry',))
        result = {'_native_config_mutation': None, 'adapter_write': False}
        with patch('custom_components.ha_entity_renamer.helper_transaction.apply_entry',
                   new=AsyncMock(return_value=result)):
            self.assertIs(await adapter.apply_native(step, lambda: None), result)
            adapter.persistence.flush.side_effect = OSError('uncertain persistence')
            with self.assertRaisesRegex(OSError, 'uncertain persistence'):
                await adapter.apply_native(step, lambda: None)


if __name__ == '__main__':
    unittest.main()
