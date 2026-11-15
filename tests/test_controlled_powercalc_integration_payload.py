"""Exact Integration payload binds nullable YAML ownership without capabilities."""
import hashlib
import unittest

from custom_components.ha_entity_renamer import native_restore_state_payload as codec
from test_native_restore_state_payload import sample


class IntegrationPayloadScopeTests(unittest.TestCase):
    def payload(self, entry, exact='1.23456789'):
        before = sample()
        before['extra_data'] = {'native_value': '1.23', 'native_unit_of_measurement': 'kWh',
                                'source_entity': 'sensor.source', 'last_valid_state': exact if exact != '0' else None}
        raw = codec._canonical(before)
        after = codec._parse(raw)
        after['extra_data']['native_value'] = exact
        after['extra_data']['last_valid_state'] = exact
        capture = {'core_version': '2026.10.4', 'entry_id': entry, 'helper_uuid': 'actual-uuid',
                   'entity_id': 'sensor.old', 'full_state_text': exact,
                   'last_valid_state_text': exact, 'before_sha256': hashlib.sha256(raw).hexdigest()}
        return codec.PreparedIntegrationPayload('2026.10.4', raw, codec._canonical(after), codec._canonical(capture))

    def test_yaml_absence_and_config_entry_are_distinct_typed_provenance(self):
        yaml = self.payload(None)
        entry = self.payload('entry-uuid')
        self.assertNotEqual(yaml.capture_json, entry.capture_json)
        self.assertEqual(codec._parse(self.payload(None, '0').after_json)['extra_data']['last_valid_state'], '0')
        for wrong in (False, 1, ''):
            with self.subTest(entry=wrong), self.assertRaises(codec.PayloadRejected):
                self.payload(wrong)
