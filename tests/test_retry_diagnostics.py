"""Value-free diagnostics for refused native retry comparisons."""
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer import _retry_difference, _retry_receipt
from custom_components.ha_entity_renamer.transaction import Refused


class RetryDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.old = SimpleNamespace(key='core.example', raw=b'{"data":{"x":1},"version":1}',
                                   manager_json=b'{"x":1}', identity=(1, 2, 0o100600))

    def test_mode_serialization_envelope_and_manager_drift(self):
        cases = (
            (replace_row(self.old, identity=(1, 3, 0o100644)), ('mode_changed',), (True, True, True)),
            (replace_row(self.old, raw=b'{ "version":1, "data": {"x":1} }'), ('raw_changed',), (True, True, True)),
            (replace_row(self.old, raw=b'{"data":{"x":1},"version":2}'), ('raw_changed',), (True, False, True)),
            (replace_row(self.old, raw=b'{"data":{"x":2},"version":1}', manager_json=b'{"x":2}'),
             ('raw_changed', 'manager_changed'), (False, False, False)),
        )
        for row, changed, semantic in cases:
            with self.subTest(changed=changed, semantic=semantic):
                report = _retry_difference(self.old, row)
                self.assertEqual({k for k in ('key_changed', 'raw_changed', 'manager_changed', 'mode_changed')
                                  if report[k]}, set(changed))
                self.assertEqual(tuple(report[k] for k in ('manager_semantic_equal',
                                 'envelope_semantic_equal', 'disk_data_semantic_equal')), semantic)
                self.assertTrue(report['old_disk_manager_equal'])
                self.assertTrue(report['new_disk_manager_equal'])
                self.assertNotIn('core.example', repr(report))

    def test_refusal_logs_only_closed_diagnostic(self):
        baseline = SimpleNamespace(receipt=SimpleNamespace(version='v', config_root='/private'),
                                   files=(self.old,))
        observed = SimpleNamespace(version='v', config_root='/private', files=(
            replace_row(self.old, raw=b'{"data":{"x":2},"version":1}'),))
        with self.assertLogs('custom_components.ha_entity_renamer', level='WARNING') as logs:
            with self.assertRaises(Refused):
                _retry_receipt(baseline, observed)
        self.assertIn("'raw_changed': True", logs.output[0])
        self.assertNotIn('/private', logs.output[0])
        self.assertNotIn('core.example', logs.output[0])
        self.assertNotIn('"x"', logs.output[0])

    def test_invalid_and_disk_manager_disagreement_are_closed(self):
        bad = replace_row(self.old, raw=b'not json', manager_json=b'{"x":2}')
        report = _retry_difference(self.old, bad)
        self.assertIsNone(report['envelope_semantic_equal'])
        self.assertIsNone(report['new_disk_manager_equal'])
        self.assertFalse(report['manager_semantic_equal'])
        mismatch = replace_row(self.old, manager_json=b'{"x":2}')
        self.assertFalse(_retry_difference(self.old, mismatch)['new_disk_manager_equal'])


def replace_row(row, **kwargs):
    return SimpleNamespace(**(vars(row) | kwargs))


if __name__ == '__main__':
    unittest.main()
