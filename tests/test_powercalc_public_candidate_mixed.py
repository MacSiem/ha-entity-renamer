"""Full detached candidate for one qualified mixed-family ORIGINAL."""
import json
import unittest

from custom_components.ha_entity_renamer.native_input_baseline import _KEYS, _SCHEMAS
from custom_components.ha_entity_renamer.powercalc_public_candidate import build_candidate
from custom_components.ha_entity_renamer.energy_native import EnergySnapshot, PreparedEnergy


class MixedCandidateTests(unittest.TestCase):
    def original(self):
        entities = [
            {'id': 'a', 'entity_id': 'sensor.producer', 'platform': 'demo', 'name': None, 'foreign': [True, 1, 1.0, -0.0]},
            {'id': 'g', 'entity_id': 'sensor.group', 'platform': 'powercalc', 'config_entry_id': 'entry',
             'suggested_object_id': 'group', 'name': None},
            {'id': 'd', 'entity_id': 'sensor.disabled', 'platform': 'demo', 'disabled_by': 'user', 'name': None},
            {'id': 'other', 'entity_id': 'sensor.other', 'platform': 'demo', 'name': None},
        ]
        values = ({'entities': entities, 'deleted_entities': [], 'settings': {'keep': True}},
                  {'devices': [{'id': 'device', 'name_by_user': None, 'foreign': {'x': 1}}],
                   'deleted_devices': [], 'child_devices': []},
                  {'entries': [{'entry_id': 'entry', 'domain': 'powercalc',
                                'data': {'_energy_entity': 'sensor.group', 'foreign': [1, True]}}]})
        return tuple((key, json.dumps({'key': key, 'version': _SCHEMAS['2026.10.4'][i][0],
            'minor_version': _SCHEMAS['2026.10.4'][i][1], 'data': values[i]}).encode())
            for i, key in enumerate(_KEYS))

    def test_mixed_single_candidate_preserves_foreign_and_moves_selected_once(self):
        mapping = {'sensor.producer': 'sensor.new_producer', 'sensor.group': 'sensor.new_group',
                   'sensor.disabled': 'sensor.new_disabled'}
        result = build_candidate(self.original(), mapping, '2026.10.4', mixed=True,
            source_id='sensor.producer', entity_names={'sensor.producer': 'Producer', 'sensor.other': 'Other'},
            device_names={'device': 'Device'})
        docs = {key: json.loads(after)['data'] for key, _, after in result.documents}
        rows = docs['core.entity_registry']['entities']
        self.assertEqual([row['entity_id'] for row in rows],
            ['sensor.other', 'sensor.new_disabled', 'sensor.new_group', 'sensor.new_producer'])
        selected = {row['id']: row for row in rows}
        self.assertEqual(selected['a']['name'], 'Producer')
        self.assertEqual(json.dumps(selected['a']['foreign']), '[true, 1, 1.0, -0.0]')
        self.assertEqual(selected['d']['disabled_by'], 'user')
        self.assertEqual(selected['other']['name'], 'Other')
        self.assertEqual(docs['core.device_registry']['devices'][0]['name_by_user'], 'Device')
        self.assertEqual(docs['core.config_entries']['entries'][0]['data']['_energy_entity'], 'sensor.new_group')
        with self.assertRaises(Exception):
            build_candidate(self.original(), mapping, '2026.10.4', mixed=True, source_id='sensor.absent')
        with self.assertRaises(Exception):
            build_candidate(self.original(), mapping, '2026.10.4', mixed=True, source_id='sensor.producer',
                            entity_names={'sensor.absent': 'X'})
        with self.assertRaises(Exception):
            build_candidate(self.original(), mapping, '2026.10.4')

    def test_multi_source_candidate_requires_all_named_source_rows(self):
        original = list(self.original())
        document = json.loads(original[0][1])
        document['data']['entities'].append({'id': 'b', 'entity_id': 'sensor.second',
                                             'platform': 'demo', 'name': None})
        original[0] = (original[0][0], json.dumps(document).encode())
        mapping = {'sensor.producer': 'sensor.new_producer', 'sensor.second': 'sensor.new_second'}
        candidate = build_candidate(tuple(original), mapping, '2026.10.4', mixed=True,
                                    source_ids=('sensor.producer', 'sensor.second'))
        rows = json.loads(candidate.documents[0][2])['data']['entities']
        self.assertEqual({r['id']: r['entity_id'] for r in rows if r['id'] in ('a', 'b')},
                         {'a': 'sensor.new_producer', 'b': 'sensor.new_second'})
        for sources in ((), ('sensor.producer', 'sensor.producer'), ('sensor.producer', 'sensor.absent')):
            with self.subTest(sources=sources), self.assertRaises(Exception):
                build_candidate(tuple(original), mapping, '2026.10.4', mixed=True, source_ids=sources)

    def test_two_entry_candidate_preserves_each_entry_own_power_energy(self):
        docs = [json.loads(raw) for _, raw in self.original()]
        for i, doc in enumerate(docs):
            doc['version'], doc['minor_version'] = _SCHEMAS['2026.11.1'][i]
        docs[0]['data']['entities'].append({'id': 'b', 'entity_id': 'sensor.second',
                                            'platform': 'demo', 'name': None})
        entries = []
        for name, source in (('a', 'sensor.producer'), ('b', 'sensor.second')):
            entries.append({'domain': 'powercalc', 'entry_id': name, 'unique_id': name,
                'title': name, 'version': 1, 'minor_version': 1, 'source': 'user',
                'disabled_by': None, 'pref_disable_new_entities': False, 'pref_disable_polling': True,
                'created_at': '2026-01-01T00:00:00+00:00', 'discovery_keys': {},
                'data': {'entity_id': source, 'sensor_type': 'virtual_power', 'mode': 'fixed',
                         'fixed': {'power': 10}, '_power_entity': 'sensor.power_' + name,
                         '_energy_entity': 'sensor.energy_' + name}, 'options': {}, 'subentries': [],
                'foreign': {'preserved': True}})
        docs[2]['data']['entries'] = entries
        original = tuple((key, json.dumps(doc).encode()) for key, doc in zip(_KEYS, docs))
        mapping = {'sensor.producer': 'sensor.new_producer', 'sensor.second': 'sensor.new_second'}
        result = build_candidate(original, mapping, '2026.11.1', mixed=True,
                                 source_ids=('sensor.producer', 'sensor.second'))
        after = json.loads(result.documents[2][2])['data']['entries']
        for before, changed in zip(entries, after):
            expected = json.loads(json.dumps(before))
            expected['data']['entity_id'] = mapping[before['data']['entity_id']]
            self.assertEqual(changed, expected)

    def test_energy_preimage_compares_json_scalar_types(self):
        mapping = {'sensor.producer': 'sensor.new_producer', 'sensor.group': 'sensor.new_group'}
        raw = json.dumps({'key': 'energy', 'data': {'foreign': True}}).encode()
        wrong = PreparedEnergy(EnergySnapshot({'foreign': 1}, 'before', None), {}, 'after')
        with self.assertRaisesRegex(Exception, 'energy_original_changed'):
            build_candidate(self.original(), mapping, '2026.10.4', (raw, wrong))
        raw = json.dumps({'key': 'energy', 'data': {'foreign': -0.0}}).encode()
        wrong = PreparedEnergy(EnergySnapshot({'foreign': 0.0}, 'before', None), {}, 'after')
        with self.assertRaisesRegex(Exception, 'energy_original_changed'):
            build_candidate(self.original(), mapping, '2026.10.4', (raw, wrong))
        prepared = PreparedEnergy(EnergySnapshot({'foreign': -0.0}, 'before', None),
                                  {'foreign': -0.0, 'source': 'sensor.new_group'}, 'after')
        candidate = build_candidate(self.original(), mapping, '2026.10.4', (raw, prepared))
        self.assertEqual(candidate.energy[0], raw)
        self.assertEqual(json.loads(candidate.energy[1])['data'], prepared.after)

    def test_candidate_keeps_script_identity_fields_and_foreign(self):
        original = list(self.original())
        data = json.loads(original[0][1]); data['data']['entities'].append({
            'id': 'script-uuid', 'entity_id': 'script.call', 'platform': 'script',
            'unique_id': 'call', 'previous_unique_id': None, 'suggested_object_id': 'call',
            'original_name': 'call', 'name': None, 'foreign': {'preserved': True}})
        original[0] = (original[0][0], json.dumps(data).encode())
        mapping = {'sensor.producer': 'sensor.new_producer', 'sensor.group': 'sensor.new_group',
                   'script.call': 'script.new_call'}
        result = build_candidate(tuple(original), mapping, '2026.10.4', mixed=True,
            source_id='sensor.producer', owned_script=('script.call', True))
        rows = json.loads(result.documents[0][2])['data']['entities']
        row = next(item for item in rows if item['id'] == 'script-uuid')
        self.assertEqual((row['entity_id'], row['unique_id'], row['previous_unique_id'],
            row['suggested_object_id'], row['original_name'], row['name']),
            ('script.new_call', 'new_call', 'call', 'new_call', 'new_call', 'call'))
        self.assertEqual(row['foreign'], {'preserved': True})
        for invalid in (('script.missing', True), ('script.call', 1), ('script.call',)):
            with self.subTest(invalid=invalid), self.assertRaises(Exception):
                build_candidate(tuple(original), mapping, '2026.10.4', mixed=True,
                    source_id='sensor.producer', owned_script=invalid)

    def test_one_candidate_preserves_powercalc_and_numeric_template_changes(self):
        from custom_components.ha_entity_renamer.helper_transaction import prepare_helper
        from custom_components.ha_entity_renamer.native_config_transition import _projection
        original = list(self.original())
        document = json.loads(original[2][1])
        entry = {'domain': 'template', 'entry_id': 'numeric', 'unique_id': None,
            'title': 'Numeric', 'version': 1, 'minor_version': 1, 'source': 'user',
            'disabled_by': None, 'pref_disable_new_entities': False, 'pref_disable_polling': True,
            'created_at': '2026-01-01T00:00:00+00:00', 'modified_at': '2026-01-01T00:00:00+00:00',
            'discovery_keys': {}, 'data': {}, 'subentries': [],
            'options': {'template_type': 'sensor', 'name': 'Numeric',
                        'state': "{{ states('sensor.producer') | int(0) * 2 }}"}, 'foreign_field': {'a': 1}}
        document['data']['entries'].append(entry)
        original[2] = (original[2][0], json.dumps(document).encode())
        mapping = {'sensor.producer': 'sensor.new_producer', 'sensor.group': 'sensor.new_group'}
        template = prepare_helper(_projection(entry), {'sensor.producer': mapping['sensor.producer']})
        candidate = build_candidate(tuple(original), mapping, '2026.10.4', mixed=True,
            source_id='sensor.producer', template_entry=template)
        entries = json.loads(candidate.documents[2][2])['data']['entries']
        group = next(item for item in entries if item['entry_id'] == 'entry')
        numeric = next(item for item in entries if item['entry_id'] == 'numeric')
        self.assertEqual(group['data']['_energy_entity'], 'sensor.new_group')
        self.assertEqual(numeric['options'], template.after['options'])
        self.assertEqual(numeric['foreign_field'], {'a': 1})
        entry['options']['state'] = "{{ states('sensor.group') | float(0) * 2 }}"
        document['data']['entries'][1] = entry
        original[2] = (original[2][0], json.dumps(document).encode())
        group_entry = prepare_helper(_projection(entry), {'sensor.group': mapping['sensor.group']})
        group_candidate = build_candidate(tuple(original), mapping, '2026.10.4', mixed=True,
            source_id='sensor.producer', template_entry=group_entry)
        group_entries = json.loads(group_candidate.documents[2][2])['data']['entries']
        self.assertEqual(next(item for item in group_entries if item['entry_id'] == 'numeric')['options'],
                         group_entry.after['options'])
        altered = list(original); bad = json.loads(altered[2][1]); bad['data']['entries'][1]['title'] = 'foreign'
        altered[2] = (altered[2][0], json.dumps(bad).encode())
        with self.assertRaises(Exception):
            build_candidate(tuple(altered), mapping, '2026.10.4', mixed=True,
                source_id='sensor.producer', template_entry=group_entry)
