"""Scratch candidates retain all nonselected native ConfigEntry data."""
from copy import deepcopy
import json
import unittest

from custom_components.ha_entity_renamer.controlled_template_entry_candidate import candidate
from custom_components.ha_entity_renamer.helper_transaction import prepare_helper
from custom_components.ha_entity_renamer.native_config_transition import _projection
from custom_components.ha_entity_renamer.native_candidate_validation import _entry_before_document


class CandidateTests(unittest.TestCase):
    def test_staged_two_helpers_preserve_direction_and_foreign_rows(self):
        row = {'domain': 'template', 'entry_id': 'numeric', 'unique_id': None,
            'title': 'Numeric', 'version': 1, 'minor_version': 1, 'source': 'user',
            'disabled_by': None, 'pref_disable_new_entities': False, 'pref_disable_polling': True,
            'created_at': '2026-01-01T00:00:00+00:00', 'modified_at': '2026-01-01T00:00:00+00:00',
            'discovery_keys': {}, 'data': {}, 'subentries': [],
            'options': {'template_type': 'sensor', 'name': 'Numeric',
                        'state': "{{ states('sensor.source') | int(0) * 2 }}"}}
        pc = dict(deepcopy(row), domain='powercalc', entry_id='pc',
                  data={'_energy_entity': 'sensor.old'}, options={})
        foreign = dict(deepcopy(row), domain='other', entry_id='foreign', data={'value': 7}, options={})
        base = {'version': 1, 'minor_version': 5, 'key': 'core.config_entries',
                'data': {'entries': [row, pc, foreign], 'foreign': [True, 1]}}
        plan = prepare_helper(_projection(row), {'sensor.source': 'sensor.renamed'})
        after = deepcopy(base)
        after['data']['entries'][0]['options'] = deepcopy(plan.after['options'])
        after['data']['entries'][1]['data']['_energy_entity'] = 'sensor.new'
        reverse = prepare_helper(plan.after, {'sensor.renamed': 'sensor.source'})
        for first, last, change in ((base, after, plan), (after, base, reverse)):
            for flip in (False, True):
                before, current = deepcopy(first), deepcopy(last)
                if flip:
                    before['data']['entries'].reverse(); current['data']['entries'].reverse()
                encoded = json.dumps(before).encode()
                result = _entry_before_document(encoded, json.dumps(current).encode(), '2026.10.4', change)
                self.assertEqual(json.loads(result), before)
                stale = deepcopy(before)
                next(r for r in stale['data']['entries'] if r['entry_id'] == 'numeric')['title'] = 'foreign'
                with self.assertRaises(ValueError):
                    _entry_before_document(json.dumps(stale).encode(), json.dumps(current).encode(),
                                           '2026.10.4', change)

    def test_candidate_preserves_full_document_and_rejects_stale_source(self):
        row = {'domain': 'template', 'entry_id': 'numeric', 'unique_id': None,
            'title': 'Numeric', 'version': 1, 'minor_version': 1, 'source': 'user',
            'disabled_by': None, 'pref_disable_new_entities': False, 'pref_disable_polling': True,
            'created_at': '2026-01-01T00:00:00+00:00', 'modified_at': '2026-01-01T00:00:00+00:00',
            'discovery_keys': {}, 'data': {}, 'subentries': [],
            'options': {'template_type': 'sensor', 'name': 'Numeric',
                        'state': "{{ states('sensor.source') | int(0) * 2 }}"}, 'foreign_field': {'a': 1}}
        foreign = dict(deepcopy(row), entry_id='foreign')
        doc = {'version': 1, 'minor_version': 5, 'key': 'core.config_entries',
               'data': {'entries': [row, foreign], 'foreign': [True, 1]}}
        plan = prepare_helper(_projection(row), {'sensor.source': 'sensor.renamed'})
        raw = json.dumps(doc).encode()
        expected = deepcopy(doc)
        expected['data']['entries'][0]['options'] = deepcopy(plan.after['options'])
        self.assertEqual(candidate(raw, '2026.10.4', plan), expected)
        self.assertEqual(json.loads(raw), doc)
        for field, value in (('title', 'foreign'), ('options', {'state': 'stale'})):
            stale = deepcopy(doc); stale['data']['entries'][0][field] = value
            with self.assertRaises(ValueError):
                candidate(json.dumps(stale).encode(), '2026.10.4', plan)
        duplicate = deepcopy(doc); duplicate['data']['entries'].append(deepcopy(row))
        with self.assertRaises(ValueError):
            candidate(json.dumps(duplicate).encode(), '2026.10.4', plan)
