"""Reject malformed persisted controlled Script programs before reopening."""
import unittest
from types import SimpleNamespace

from custom_components.ha_entity_renamer.controlled_script_composition import (
    ControlledScriptComposition, _ScriptEntryBase, decode_controlled_script_composition)


class ControlledScriptCompositionTests(unittest.TestCase):
    def test_entry_seal_is_required_and_unique_for_script_base(self):
        digest_a, digest_b = 'a' * 64, 'b' * 64
        receipt = SimpleNamespace(changed_domains=('script', 'template'), unsupported_domains=(),
            proposed_files=('scripts.yaml',), runtime_expected=(('script', digest_a, digest_b),
                ('template', digest_a, digest_b), ('template.config_entries', digest_a, digest_b)))
        for cls in (ControlledScriptComposition, _ScriptEntryBase):
            object.__new__(cls)._assert_changed_scope(receipt)
        receipt.runtime_expected += (('template.config_entries', digest_a, digest_b),)
        with self.assertRaises(ValueError):
            object.__new__(ControlledScriptComposition)._assert_changed_scope(receipt)

    def test_malformed_envelopes_refuse_before_any_native_access(self):
        for value in (None, {}, {'schema': True, 'profile': 'template_controlled_script_reload_v1'},
                      {'schema': 1, 'profile': 'template_controlled_script_reload_v1', 'base': {}}):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                decode_controlled_script_composition(value, None)
