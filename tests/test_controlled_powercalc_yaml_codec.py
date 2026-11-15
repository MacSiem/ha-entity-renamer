"""The shared YAML preparation is typed but does not authorize effects."""
from dataclasses import asdict, replace
import hashlib
import unittest

from custom_components.ha_entity_renamer.native_template_state_yaml import PreparedMixedPowercalcYAML
from custom_components.ha_entity_renamer.prepared import encode_change, decode_change
from custom_components.ha_entity_renamer.yaml_transaction import ValidationReceipt, YAMLFile


class ControlledPowercalcYAMLCodecTests(unittest.TestCase):
    def test_roundtrip_and_reject_wrong_scope_or_seal(self):
        before, after = b'entity: sensor.old\n', b'entity: sensor.new\n'
        digest = lambda value: hashlib.sha256(value).hexdigest()
        receipt = ValidationReceipt('/config', (('configuration.yaml', digest(before), 1, 2),),
            (('.', (('configuration.yaml', 33152, 1, 2),), ('configuration.yaml',)),),
            (('configuration.yaml', digest(after)),), ('automation', 'powercalc', 'template'),
            ('powercalc', 'template'), '2026.10.4',
            (('automation', '1' * 64, '2' * 64), ('powercalc', '3' * 64, '4' * 64),
             ('template', None, None)))
        value = PreparedMixedPowercalcYAML(receipt,
            (YAMLFile('configuration.yaml', before.hex(), after.hex(), 1, 2),),
            'a' * 64, 'b' * 64, 'c' * 64)
        wire = encode_change(value)
        self.assertEqual(decode_change(wire), value)
        template_only = replace(value, receipt=replace(receipt,
            changed_domains=('powercalc', 'template'),
            runtime_expected=receipt.runtime_expected[1:]))
        self.assertEqual(decode_change(encode_change(template_only)), template_only)
        powercalc_only = replace(value, receipt=replace(receipt, changed_domains=('powercalc',),
            unsupported_domains=('powercalc',), runtime_expected=(receipt.runtime_expected[1],)))
        self.assertEqual(decode_change(encode_change(powercalc_only)), powercalc_only)
        for changed in (replace(value, powercalc_original_sha256='x' * 64),
                        replace(value, receipt=replace(receipt, unsupported_domains=('template',)))):
            with self.assertRaises(ValueError):
                encode_change(changed)
        other = dict(wire, kind='yaml')
        with self.assertRaises(ValueError):
            decode_change(other)
