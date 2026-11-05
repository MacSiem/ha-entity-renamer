"""Synthetic chain folding checks; do not establish native writer coverage."""
from types import SimpleNamespace
import stat
import unittest

from custom_components.ha_entity_renamer.dashboard_receipts import fold_receipt, validate_original_metadata
from custom_components.ha_entity_renamer.lovelace_native import Dashboard, Snapshot, PreparedDashboard, revision
from custom_components.ha_entity_renamer.native_template_yaml_server import _dashboard_plan
from custom_components.ha_entity_renamer.transaction import Plan, Step


class DashboardReceiptTests(unittest.TestCase):
    def test_exact_plan_shape_only(self):
        def step(kind, index):
            return Step(str(index), kind, str(index), 'before', 'after', str(index))
        scopes = ('registry', 'dashboard', 'yaml')
        plan = Plan('plan', scopes, scopes, tuple(step(kind, i) for i, kind in
            enumerate(('registry', 'dashboard', 'yaml'))), 'coverage', scopes)
        self.assertTrue(_dashboard_plan(plan))
        for kinds in (('dashboard', 'registry', 'yaml'), ('registry', 'yaml', 'dashboard'),
                      ('registry', 'yaml'), ('dashboard', 'yaml')):
            with self.subTest(kinds=kinds):
                self.assertFalse(_dashboard_plan(Plan('plan', scopes, scopes,
                    tuple(step(kind, i) for i, kind in enumerate(kinds)), 'coverage', scopes)))

    def test_original_metadata_is_exact_and_rejects_foreign_scope(self):
        config = {'views': []}
        change = PreparedDashboard(Snapshot(Dashboard(None, 'lovelace', 'identity'), config,
            revision(config), (7, 100, 'a' * 64)), {'views': [{'title': 'new'}]}, 'revision')
        path = '.storage/lovelace'
        receipt = SimpleNamespace(source_files=((path, 'a' * 64, 7, 100),))
        metadata = {path: ['a' * 64, 7, 100, stat.S_IFREG | 0o600, 501, 20]}
        self.assertEqual(validate_original_metadata(receipt, [change], metadata), metadata)
        with self.assertRaises(Exception):
            validate_original_metadata(receipt, [change], {**metadata, '.storage/foreign': metadata[path]})
        with self.assertRaises(Exception):
            validate_original_metadata(receipt, [change], {path: ['b' * 64, 7, 100, stat.S_IFREG | 0o600, 501, 20]})

    def test_forward_and_inverse_preserve_foreign_sources_and_file_metadata(self):
        old, new = {'views': []}, {'views': [{'title': 'new'}]}
        change = PreparedDashboard(Snapshot(Dashboard(None, 'lovelace', 'identity'), old,
            revision(old), (7, 100, 'a' * 64)), new, revision(new))
        chain = SimpleNamespace(_original_change=lambda _: change, _summary=lambda _: {'registry': 'same'})
        path = '.storage/lovelace'
        initial = ['a' * 64, 7, 100, stat.S_IFREG | 0o600, 501, 20]
        forward = ['b' * 64, 7, 101, stat.S_IFREG | 0o600, 501, 20]
        restored = ['c' * 64, 7, 102, stat.S_IFREG | 0o600, 501, 20]
        foreign = ['d' * 64, 7, 200, stat.S_IFREG | 0o600, 501, 20]
        def event(direction, before, after, old_rev, new_rev, input_result):
            return ({'binding': {'index': 0, 'direction': direction},
                     'dashboard_before': {'revision': old_rev, 'physical': before}},
                    {'dashboard_after': {'revision': new_rev, 'physical': after},
                     'input_result': input_result, 'result': {'registry': 'same'}})
        sources = {path: initial, 'foreign': foreign}
        after_sources = {path: forward, 'foreign': foreign}
        intent, receipt = event('forward', initial, forward, revision(old), revision(new), after_sources)
        self.assertEqual(fold_receipt(chain, {}, sources, intent, receipt), after_sources)
        restored_sources = {path: restored, 'foreign': foreign}
        intent, receipt = event('rollback', forward, restored, revision(new), revision(old), restored_sources)
        self.assertEqual(fold_receipt(chain, {}, after_sources, intent, receipt), restored_sources)
        receipt['dashboard_after']['physical'] = [*restored[:4], 502, 20]
        with self.assertRaises(Exception):
            fold_receipt(chain, {}, after_sources, intent, receipt)
        receipt['dashboard_after']['physical'] = restored
        receipt['input_result'] = {path: restored, 'foreign': [*foreign[:4], 502, 20]}
        with self.assertRaises(Exception):
            fold_receipt(chain, {}, after_sources, intent, receipt)
