"""Synthetic structural cases ONLY; no native calls, provenance or AC claims.

Forged dataclasses are intentionally constructed here. Acceptance proves only
comparison semantics; trusted caller must authenticate original binding and IO.
Owner runs integrated frozen candidate through the required FIFO test wrapper.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

COMPONENT = Path(os.environ.get('RENAMER_COMPONENT_DIR',
                 str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')))
PACKAGE = '_config_transition_regression'
package = ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
transition = importlib.import_module(PACKAGE + '.native_config_transition')
helper = importlib.import_module(PACKAGE + '.helper_transaction')
native = importlib.import_module(PACKAGE + '.helper_native')


def raw(value):
    return json.dumps(value, ensure_ascii=True).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def target(doc):
    return next(row for row in doc['data']['entries'] if row['entry_id'] == 'stable')


def projection(row):
    # Independent fixture projection mirrors public read_entry contract, not a
    # source/result overlay. Its raw native row remains intact in both documents.
    keys = ('entry_id', 'unique_id', 'title', 'version', 'minor_version', 'source',
            'disabled_by', 'pref_disable_new_entities', 'pref_disable_polling')
    return {'domain': row['domain'], 'identity': {key: row[key] for key in keys},
            'data': deepcopy(row['data']), 'options': deepcopy(row['options']),
            'subentries': {sub['subentry_id']: {
                'identity': {key: sub[key] for key in
                             ('subentry_id', 'subentry_type', 'unique_id', 'title')},
                'data': deepcopy(sub['data'])} for sub in row['subentries']}}


def fixture(domain='group'):
    subs = [{'subentry_id': key, 'subentry_type': 'test', 'unique_id': None,
             'title': key, 'data': {'opaque': [True, 1, 1.0]}, 'future': [key]}
            for key in ('sub-one', 'sub-two')]
    row = {'entry_id': 'stable', 'unique_id': None, 'domain': domain,
           'title': 'Helper', 'version': 1, 'minor_version': 1, 'source': 'user',
           'disabled_by': None, 'pref_disable_new_entities': False,
           'pref_disable_polling': False, 'created_at': 'created', 'modified_at': 't0',
           'discovery_keys': {'opaque': [['test', 'id']]}, 'subentries': subs,
           'data': {'opaque': [True, 1, 1.0]},
           'options': {'entities': ['sensor.old', 'sensor.keep', 'sensor.old'],
                       'name': 'group', 'opaque': [False, 1, 1.0]},
           'future': {'nested': [True, 1, 1.0]}}
    if domain == 'powercalc':
        row['data'] = {'power_sensor_id': 'sensor.old',
                       'cost': {'energy_sensor_id': 'sensor.old', 'rate': 3},
                       'opaque': [True, 1, 1.0]}
        row['options'] = {'opaque': [False, 1, 1.0]}
    elif domain == 'threshold':
        row['options'] = {'entity_id': 'sensor.old', 'lower': 4}
    other = deepcopy(row)
    other['entry_id'] = 'other'
    before = {'version': 1, 'minor_version': 5, 'key': 'core.config_entries',
              'data': {'entries': [row, other], 'deleted_entries': [{'entry_id': 'deleted'}],
                       'future': [True, 1, 1.0]}, 'future': {'count': 1}}
    prepared = helper.prepare_helper(projection(row), {'sensor.old': 'sensor.new'})
    after = deepcopy(before)
    target(after)['data'] = deepcopy(prepared.after['data'])
    target(after)['options'] = deepcopy(prepared.after['options'])
    target(after)['modified_at'] = 't1'
    return before, after, prepared


def audit(before, after, prepared, direction='forward'):
    return transition.NativeConfigEffectAudit('stable', direction, sha(raw(before)),
        sha(raw(after)), transition.prepared_helper_digest(prepared),
        target(before)['modified_at'], target(after)['modified_at'])


def stamp(before, after, changed=True):
    return native.NativeConfigMutation('stable', raw(target(before)).decode(),
                                      raw(target(after)).decode(), changed)


def validate(before, after, prepared, version='2026.10.2', direction='forward', **overrides):
    kwargs = {'native_effect_audit': audit(before, after, prepared, direction),
              'native_mutation': stamp(before, after)}
    kwargs.update(overrides)
    return transition.validate_config_transition(raw(before), raw(after), prepared,
        version=version, direction=direction, **kwargs)


class ConfigTransitionTests(unittest.TestCase):
    def test_forward_reverse_pinned_versions_preserve_original_and_order(self):
        for version in ('2025.6.0', '2026.10.2'):
            for domain in ('group', 'powercalc', 'threshold'):
                with self.subTest(version=version, domain=domain):
                    before, after, prepared = fixture(domain)
                    original = deepcopy((before, after, prepared))
                    self.assertEqual(validate(before, after, prepared, version).direction, 'forward')
                    restored = deepcopy(before)
                    target(restored)['modified_at'] = 't2'
                    self.assertEqual(validate(after, restored, prepared, version, 'reverse').direction,
                                     'reverse')
                    self.assertEqual((before, after, prepared), original)

    def test_foreign_delta_refuses_even_with_forged_fully_matching_audit_and_stamp(self):
        for scope in ('envelope', 'data_unknown', 'deleted', 'entry_order', 'entry_add',
                      'other_audit', 'other_options', 'target_unknown', 'created', 'discovery',
                      'options', 'data', 'sub_data', 'sub_unknown', 'sub_order', 'sub_identity'):
            before, after, prepared = fixture()
            row = target(after)
            if scope == 'envelope': after['future']['count'] = 1.0
            elif scope == 'data_unknown': after['data']['future'][0] = 1
            elif scope == 'deleted': after['data']['deleted_entries'] = []
            elif scope == 'entry_order': after['data']['entries'].reverse()
            elif scope == 'entry_add': after['data']['entries'].append({'entry_id': 'alien'})
            elif scope == 'other_audit': after['data']['entries'][1]['modified_at'] = 'foreign'
            elif scope == 'other_options': after['data']['entries'][1]['options']['alien'] = True
            elif scope == 'target_unknown': row['future']['nested'][1] = 1.0
            elif scope == 'created': row['created_at'] = 'foreign'
            elif scope == 'discovery': row['discovery_keys'] = {}
            elif scope == 'options': row['options']['opaque'][0] = 0
            elif scope == 'data': row['data']['opaque'][1] = 1.0
            elif scope == 'sub_data': row['subentries'][0]['data']['opaque'][0] = 1
            elif scope == 'sub_unknown': row['subentries'][0]['future'] = []
            elif scope == 'sub_order': row['subentries'].reverse()
            elif scope == 'sub_identity': row['subentries'][0]['title'] = 'foreign'
            with self.subTest(scope=scope), self.assertRaises(transition.ConfigTransitionError):
                validate(before, after, prepared)

    def test_source_projection_must_match_original_preparation(self):
        before, after, prepared = fixture()
        for doc in (before, after):
            target(doc)['options']['opaque'][1] = 1.0
        with self.assertRaisesRegex(transition.ConfigTransitionError, 'source_mismatch'):
            validate(before, after, prepared)

    def test_raw_hashes_and_all_audit_bindings_are_mandatory(self):
        before, after, prepared = fixture()
        valid = audit(before, after, prepared)
        variants = [None, {}, *[replace(valid, **{field: 'wrong'}) for field in vars(valid)]]
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises(transition.ConfigTransitionError):
                validate(before, after, prepared, native_effect_audit=candidate)
        # Equivalent JSON formatting changes raw hash binding.
        with self.assertRaises(transition.ConfigTransitionError):
            transition.validate_config_transition(raw(before) + b' ', raw(after), prepared,
                version='2026.10.2', direction='forward', native_effect_audit=valid,
                native_mutation=stamp(before, after))

    def test_stamp_requires_exact_full_rows_identity_and_real_bool(self):
        before, after, prepared = fixture()
        valid = stamp(before, after)
        reduced = deepcopy(target(after))
        reduced.pop('future')
        variants = [None, {}, replace(valid, entry_id='other'), replace(valid, changed=1),
                    replace(valid, changed=False), replace(valid, before_json=valid.after_json),
                    replace(valid, after_json=raw(reduced).decode()),
                    replace(valid, after_json='{"x":1,"x":2}'),
                    replace(valid, after_json='{"x":NaN}')]
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises(transition.ConfigTransitionError):
                validate(before, after, prepared, native_mutation=candidate)

    def test_listener_partial_and_no_call_are_explicitly_unsupported(self):
        before, after, prepared = fixture('threshold')
        # Ordinary adapter-owned threshold full delta is supported. A source
        # already rewritten by a listener cannot be called the original source.
        partial = deepcopy(after)
        target(partial)['modified_at'] = 'listener-time'
        with self.assertRaises(transition.ConfigTransitionError):
            validate(partial, after, prepared)
        with self.assertRaises(transition.ConfigTransitionError):
            validate(after, after, prepared, native_mutation=None)
        with self.assertRaisesRegex(transition.ConfigTransitionError, 'native_noop_unsupported'):
            validate(after, after, prepared, native_mutation=stamp(after, after, False))

    def test_reverse_rejects_foreign_result_and_stale_forward_stamp(self):
        before, after, prepared = fixture()
        restored = deepcopy(before)
        target(restored)['modified_at'] = 't2'
        with self.assertRaises(transition.ConfigTransitionError):
            validate(after, restored, prepared, direction='reverse', native_mutation=stamp(before, after))
        restored['data']['entries'][1]['title'] = 'foreign'
        with self.assertRaises(transition.ConfigTransitionError):
            validate(after, restored, prepared, direction='reverse')

    def test_duplicate_ids_missing_target_and_schema_types_refuse(self):
        for which in ('before', 'after'):
            for mutation in ('duplicate_other_id', 'duplicate_target', 'duplicate_sub_id',
                             'sub_as_map', 'missing_target', 'major_bool', 'minor_float', 'key'):
                before, after, prepared = fixture()
                original_audit, original_stamp = audit(before, after, prepared), stamp(before, after)
                doc = before if which == 'before' else after
                if mutation == 'duplicate_other_id':
                    doc['data']['entries'].append(deepcopy(doc['data']['entries'][1]))
                elif mutation == 'duplicate_target':
                    doc['data']['entries'].append(deepcopy(target(doc)))
                elif mutation == 'duplicate_sub_id':
                    target(doc)['subentries'][1]['subentry_id'] = 'sub-one'
                elif mutation == 'sub_as_map': target(doc)['subentries'] = {}
                elif mutation == 'missing_target': doc['data']['entries'].pop(0)
                elif mutation == 'major_bool': doc['version'] = True
                elif mutation == 'minor_float': doc['minor_version'] = 5.0
                elif mutation == 'key': doc['key'] = 'core.entity_registry'
                with self.subTest(which=which, mutation=mutation), self.assertRaises(transition.ConfigTransitionError):
                    transition.validate_config_transition(raw(before), raw(after), prepared,
                        version='2026.10.2', direction='forward',
                        native_effect_audit=replace(original_audit, source_sha256=sha(raw(before)),
                                                    result_sha256=sha(raw(after))),
                        native_mutation=original_stamp)

    def test_malformed_raw_nonfinite_depth_and_size_refuse(self):
        before, after, prepared = fixture()
        valid = audit(before, after, prepared)
        for invalid in (b'{"a":0,"a":1}', b'{"a":NaN}', b'{"a":1e999}', b'\xff',
                        b'[]', b'[' * 70 + b'0' + b']' * 70, 'not bytes'):
            with self.subTest(invalid=invalid), self.assertRaises(transition.ConfigTransitionError):
                transition.validate_config_transition(invalid, raw(after), prepared,
                    version='2026.10.2', direction='forward', native_effect_audit=valid,
                    native_mutation=stamp(before, after))
        with patch.object(transition, 'MAX_DOCUMENT_BYTES', len(raw(before)) - 1):
            with self.assertRaises(transition.ConfigTransitionError):
                validate(before, after, prepared)

    def test_target_audit_absence_null_and_source_identity_types_refuse(self):
        for field, value in (('modified_at', None), ('created_at', None),
                             ('version', True), ('minor_version', 1.0),
                             ('pref_disable_polling', 0), ('discovery_keys', [])):
            before, after, prepared = fixture()
            original_audit, original_stamp = audit(before, after, prepared), stamp(before, after)
            target(before)[field] = value
            with self.subTest(field=field), self.assertRaises(transition.ConfigTransitionError):
                transition.validate_config_transition(raw(before), raw(after), prepared,
                    version='2026.10.2', direction='forward',
                    native_effect_audit=replace(original_audit, source_sha256=sha(raw(before))),
                    native_mutation=replace(original_stamp, before_json=raw(target(before)).decode()))

    def test_prepared_digest_binds_nested_types_and_mapping(self):
        before, after, prepared = fixture()
        original = transition.prepared_helper_digest(prepared)
        altered = deepcopy(prepared)
        altered.before['data']['opaque'][1] = 1.0
        self.assertNotEqual(original, transition.prepared_helper_digest(altered))
        altered = replace(prepared, mapping={'sensor.old': 'sensor.wrong'})
        self.assertNotEqual(original, transition.prepared_helper_digest(altered))
        with self.assertRaises(transition.ConfigTransitionError):
            validate(before, after, altered)
        for version, direction in (('2027.1.0', 'forward'), ('2026.10.2', 'undo')):
            with self.assertRaises(transition.ConfigTransitionError):
                validate(before, after, prepared, version, direction)


if __name__ == '__main__':
    unittest.main()
