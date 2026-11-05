import copy
from datetime import datetime, timedelta, timezone
import json
import unittest

from batch_system_effects import fixture_restore_dump, http_auto_revert


def raw(value):
    return json.dumps(value).encode()


class BatchSystemEffectsTests(unittest.TestCase):
    def test_http_timeout_is_directional_and_preserves_every_other_field(self):
        before = {'key': 'http', 'version': 1, 'data': {'stable': {'port': 8123},
                  'pending': {'error': None, 'port': 8123, 'origins': []}}}
        after = copy.deepcopy(before)
        after['data']['pending']['error'] = 'not_promoted'
        self.assertEqual(raw(before), http_auto_revert(raw(before), raw(after)))
        for change in ('other_error', 'foreign_field', 'auth_like_field', 'reverse'):
            bad = copy.deepcopy(after)
            if change == 'other_error': bad['data']['pending']['error'] = 'other'
            if change == 'foreign_field': bad['data']['stable']['port'] = 9999
            if change == 'auth_like_field': bad['data']['pending']['origins'] = ['foreign']
            if change == 'reverse': bad['data']['pending']['error'] = None
            with self.subTest(change=change), self.assertRaises(ValueError):
                http_auto_revert(raw(before), raw(bad))
        for original, foreign in ((1, True), (1, 1.0), (0.0, -0.0)):
            with self.subTest(original=original, foreign=foreign, kind=type(foreign).__name__):
                first = copy.deepcopy(before)
                first['data']['stable']['port'] = original
                second = copy.deepcopy(first)
                second['data']['pending']['error'] = 'not_promoted'
                second['data']['stable']['port'] = foreign
                with self.assertRaises(ValueError):
                    http_auto_revert(raw(first), raw(second))

    def test_periodic_dump_admits_only_exact_owned_fixture_rows(self):
        started = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
        seen = (started + timedelta(minutes=15, seconds=5)).isoformat()
        event = (started + timedelta(minutes=5)).isoformat()
        base = {'version': 1, 'minor_version': 1, 'key': 'core.restore_state', 'data': []}
        specs = [
            ('sensor.runtime_consumer', '2', {'friendly_name': 'Runtime consumer'}, {'native_unit_of_measurement': None, 'native_value': 2}, False),
            ('binary_sensor.runtime_binary', 'off', {'friendly_name': 'Runtime binary'}, None, False),
            ('automation.mixed_source_observer', 'on', {'friendly_name': 'Mixed source observer', 'current': 0, 'id': 'mixed_source', 'mode': 'single', 'last_triggered': event}, None, True),
            ('script.mixed_source', 'off', {'friendly_name': 'mixed_source', 'current': 0, 'mode': 'single', 'last_triggered': event}, None, False),
        ]
        result = copy.deepcopy(base)
        for entity, state, attrs, extra, parent in specs:
            result['data'].append({'last_seen': seen, 'extra_data': extra, 'state': {
                'entity_id': entity, 'state': state, 'attributes': attrs,
                'last_changed': event, 'last_updated': event, 'last_reported': event,
                'context': {'id': '0' * 26, 'parent_id': '1' * 26 if parent else None, 'user_id': None}}})
        verify = lambda obj: fixture_restore_dump(raw(base), raw(obj), fixture_started=started,
                                                  snapshot_at=started + timedelta(minutes=18))
        self.assertEqual(raw(base), verify(result))
        for change in ('foreign_entity', 'foreign_attribute', 'timestamp', 'context', 'extra',
                       'header_bool', 'header_float', 'attributes_list', 'attribute_bool', 'attribute_float',
                       'extra_bool', 'extra_float'):
            bad = copy.deepcopy(result)
            if change == 'foreign_entity': bad['data'][0]['state']['entity_id'] = 'sensor.foreign'
            if change == 'foreign_attribute': bad['data'][0]['state']['attributes']['extra'] = 'foreign'
            if change == 'timestamp': bad['data'][0]['last_seen'] = event
            if change == 'context': bad['data'][0]['state']['context']['user_id'] = 'foreign'
            if change == 'extra': bad['data'][0]['extra_data']['native_value'] = 3
            if change == 'header_bool': bad['version'] = True
            if change == 'header_float': bad['minor_version'] = 1.0
            if change == 'attributes_list': bad['data'][0]['state']['attributes'] = [['friendly_name', 'Runtime consumer']]
            if change == 'attribute_bool': bad['data'][2]['state']['attributes']['current'] = False
            if change == 'attribute_float': bad['data'][2]['state']['attributes']['current'] = 0.0
            if change == 'extra_bool': bad['data'][0]['extra_data']['native_value'] = True
            if change == 'extra_float': bad['data'][0]['extra_data']['native_value'] = 2.0
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify(bad)
        before_zero = copy.deepcopy(base)
        before_zero['minor_version'] = 0.0
        after_zero = copy.deepcopy(result)
        after_zero['minor_version'] = -0.0
        with self.assertRaises(ValueError):
            fixture_restore_dump(raw(before_zero), raw(after_zero), fixture_started=started,
                                 snapshot_at=started + timedelta(minutes=18))
