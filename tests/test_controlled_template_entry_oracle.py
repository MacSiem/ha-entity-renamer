"""Independent oracle detects omitted and foreign native source changes."""
from copy import deepcopy
import unittest
from controlled_template_entry_oracle import assert_documents


class OracleTests(unittest.TestCase):
    def test_exact_entry_and_foreign_documents_in_both_directions(self):
        stamp = '2026-01-01T00:00:00+00:00'
        before = {'core.config_entries': {'data': {'entries': [
            {'entry_id': 'entry', 'modified_at': stamp, 'title': 'Entry',
             'options': {'state': "{{ states('sensor.old') | int(0) * 2 }}"}},
            {'entry_id': 'foreign', 'foreign': [True, 1]}]}},
            'core.entity_registry': {'data': {'entities': [
                {'id': 'source', 'entity_id': 'sensor.old', 'modified_at': stamp, 'foreign': True},
                {'id': 'other', 'entity_id': 'sensor.other', 'foreign': {'x': 1}},
                {'id': 'third', 'entity_id': 'sensor.third', 'foreign': False}]}},
            'core.device_registry': {'data': {'devices': [{'id': 'device', 'name': 'unchanged'}]}}}
        after = deepcopy(before)
        after['core.entity_registry']['data']['entities'][0]['entity_id'] = 'sensor.new'
        after['core.config_entries']['data']['entries'][0]['options']['state'] = "{{ states('sensor.new') | int(0) * 2 }}"
        args = dict(entry_id='entry', source_uuid='source', old='sensor.old', new='sensor.new')
        rows = after['core.entity_registry']['data']['entities']
        rows.append(rows.pop(0))
        self.assertTrue(assert_documents(before, after, reverse=False, **args))
        self.assertTrue(assert_documents(before, before, reverse=True, **args))
        for change in (
            lambda d: d['core.config_entries']['data']['entries'][0]['options'].__setitem__('state', "{{ states('sensor.old') | int(0) * 2 }}"),
            lambda d: d['core.config_entries']['data']['entries'][0].__setitem__('title', 'changed'),
            lambda d: next(row for row in d['core.entity_registry']['data']['entities'] if row['id'] == 'source').__setitem__('foreign', 1),
            lambda d: next(row for row in d['core.entity_registry']['data']['entities'] if row['id'] == 'other').__setitem__('foreign', {}),
            lambda d: d['core.device_registry']['data']['devices'][0].__setitem__('name', 'changed'),
            lambda d: next(row for row in d['core.entity_registry']['data']['entities'] if row['id'] == 'other').__setitem__('id', 'foreign-uuid'),
            lambda d: d['core.entity_registry']['data']['entities'].pop(0),
            lambda d: d['core.entity_registry']['data']['entities'].reverse(),
        ):
            bad = deepcopy(after)
            change(bad)
            with self.assertRaises(ValueError):
                assert_documents(before, bad, reverse=False, **args)
