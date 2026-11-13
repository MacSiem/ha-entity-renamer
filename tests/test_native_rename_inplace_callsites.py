"""Static classifier gates for the Core 11 in-place rename call sites."""
import dis
import unittest

from custom_components.ha_entity_renamer.native_rename_emission_classifier import (
    NativeRenameEmissionError, _call_offsets,
)


def source_handler(source, states, old_entity_id, changed):
    if not changed:
        source.async_write_ha_state()
        return
    states.async_remove(old_entity_id)
    source.async_write_ha_state()


class InplaceCallsitesTest(unittest.TestCase):
    def test_final_write_and_old_id_removal_are_separate_exact_calls(self):
        last = _call_offsets(source_handler, 'async_write_ha_state', occurrence=-1)
        first = _call_offsets(source_handler, 'async_write_ha_state', occurrence=0)
        removed = _call_offsets(source_handler, 'async_remove', first_argument='old_entity_id')
        self.assertTrue(last and first and removed)
        self.assertFalse(last & first or last & removed or first & removed)
        with self.assertRaises(NativeRenameEmissionError):
            _call_offsets(source_handler, 'async_write_ha_state')
        with self.assertRaises(NativeRenameEmissionError):
            _call_offsets(source_handler, 'async_remove', first_argument='foreign_id')
        calls = {item.offset for item in dis.get_instructions(source_handler) if item.opname == 'CALL'}
        self.assertEqual(len(calls & last), 1)
        self.assertEqual(len(calls & first), 1)
        self.assertEqual(len(calls & removed), 1)
