"""Synthetic structural delta cases; no Home Assistant import or native calls.

Owner runs the integrated frozen candidate via FIFO; fixtures prove structural
comparison only, never trusted provenance or authorization of native effects.
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

COMPONENT = Path(os.environ.get('RENAMER_COMPONENT_DIR',
                 str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')))
PACKAGE = '_registry_transition_regression'
package = ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package
transition = importlib.import_module(PACKAGE + '.native_registry_transition')
native = importlib.import_module(PACKAGE + '.native_registry')


def raw(doc):
    return json.dumps(doc, ensure_ascii=True).encode('ascii')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture(kind='entity', version='2026.10.2', audit=True):
    row = {'id': 'stable', 'name': 'Old', 'entity_id': 'sensor.old',
           'unique_id': 'integration-id', 'options': {'opaque': [False, 1.0, None]}}
    if kind == 'device':
        row = {'id': 'stable', 'name_by_user': 'Old', 'identifiers': [['test', 'physical']],
               'unknown': {'nested': [True, 7, None]}}
    if audit:
        row['modified_at'] = '2026-01-01T00:00:00+00:00'
    other = deepcopy(row)
    other['id'] = 'untouched'
    if kind == 'entity':
        other['entity_id'] = 'sensor.untouched'
    third = deepcopy(other)
    third['id'] = 'untouched-two'
    if kind == 'entity':
        third['entity_id'] = 'sensor.untouched_two'
    plural = 'entities' if kind == 'entity' else 'devices'
    data = {plural: [row, other, third], 'deleted_' + plural: [{'id': 'deleted', 'unknown': [False]}, {'id': 'deleted-two'}],
            'opaque_top': {'flag': True}}
    if version == '2026.10.2':
        data.update({'settings': {'entity_id_parts': ['name']}} if kind == 'entity'
                    else {'child_devices': [{'id': 'child', 'opaque': [1]}]})
    schemas = {'2025.6.0': {'entity': (1, 17), 'device': (1, 9)},
               '2026.10.2': {'entity': (1, 23), 'device': (3, 4)}}
    major, minor = schemas[version][kind]
    before = {'version': major, 'minor_version': minor, 'key': 'core.' + kind + '_registry',
              'data': data, 'opaque_envelope': {'counter': 1}}
    after_row = dict(row, **({'name': None, 'entity_id': 'sensor.new'} if kind == 'entity'
                             else {'name_by_user': None}))
    prepared = native.PreparedRegistryChange(kind, 'stable', json.dumps(row), json.dumps(after_row))
    after = deepcopy(before)
    after['data'][plural][0] = after_row
    if kind == 'entity':
        after['data'][plural].append(after['data'][plural].pop(0))
    if audit:
        after_row['modified_at'] = '2026-02-02T00:00:00+00:00'
    return before, after, prepared


def target(document, kind='entity'):
    plural = 'entities' if kind == 'entity' else 'devices'
    return next(row for row in document['data'][plural] if row['id'] == 'stable')


def make_audit(before, after, prepared, direction='forward'):
    key = 'entities' if prepared.kind == 'entity' else 'devices'
    old = next((r for r in before['data'][key] if r.get('id') == prepared.identity), {}).get('modified_at')
    new = next((r for r in after['data'][key] if r.get('id') == prepared.identity), {}).get('modified_at')
    return transition.NativeRegistryEffectAudit(prepared.kind, prepared.identity, direction,
        digest(raw(before)), digest(raw(after)), transition.prepared_registry_digest(prepared), old, new)


def validate(before, after, prepared, version='2026.10.2', direction='forward', audit=None):
    return transition.validate_registry_transition(raw(before), raw(after), prepared,
        version=version, direction=direction,
        native_effect_audit=audit if audit is not None else make_audit(before, after, prepared, direction))


class RegistryTransitionTests(unittest.TestCase):
    def test_only_deterministic_native_id_relocation_preserves_other_order(self):
        before, after, prepared = fixture()
        self.assertEqual([r['id'] for r in after['data']['entities']],
                         ['untouched', 'untouched-two', 'stable'])
        validate(before, after, prepared)
        for order in (['stable', 'untouched', 'untouched-two'],
                      ['untouched-two', 'untouched', 'stable']):
            candidate = deepcopy(after)
            rows = {r['id']: r for r in candidate['data']['entities']}
            candidate['data']['entities'] = [rows[key] for key in order]
            with self.assertRaises(transition.RegistryTransitionError):
                validate(before, candidate, prepared)

    def test_both_schemas_kinds_forward_reverse_and_audit_absence(self):
        for version in ('2025.6.0', '2026.10.2'):
            for kind in ('entity', 'device'):
                for has_audit in (False, True):
                    with self.subTest(version=version, kind=kind, audit=has_audit):
                        before, after, prepared = fixture(kind, version, has_audit)
                        original = deepcopy((before, after, prepared))
                        self.assertEqual(validate(before, after, prepared, version).direction, 'forward')
                        restored = deepcopy(before)
                        key = 'entities' if kind == 'entity' else 'devices'
                        if has_audit:
                            restored['data'][key][0]['modified_at'] = '2026-03-03T00:00:00+00:00'
                        if kind == 'entity':
                            restored['data'][key].append(restored['data'][key].pop(0))
                        result = validate(after, restored, prepared, version, 'reverse')
                        self.assertEqual(result.result_sha256, digest(raw(restored)))
                        self.assertEqual((before, after, prepared), original)

    def test_entity_name_only_and_id_only(self):
        for key in ('name', 'entity_id'):
            before, after, prepared = fixture()
            origin = json.loads(prepared.before_json)
            target = dict(origin, **{key: json.loads(prepared.after_json)[key]})
            prepared = replace(prepared, after_json=json.dumps(target))
            after = deepcopy(before)
            after['data']['entities'][0] = target
            if key == 'entity_id':
                after['data']['entities'].append(after['data']['entities'].pop(0))
            validate(before, after, prepared)

    def test_foreign_delta_refused_even_when_supplied_audit_binds_result_bytes(self):
        # Rebuilding the byte-bound audit is intentionally allowed in this fixture:
        # a forged audit must never whitelist an unrelated field or row.
        for kind in ('entity', 'device'):
            for mutation in ('other_audit', 'reorder', 'deleted', 'deleted_order', 'unknown',
                             'envelope', 'settings', 'target_foreign', 'target_type', 'nested_type',
                             'child', 'membership', 'rename_other', 'audit_add'):
                with self.subTest(kind=kind, mutation=mutation):
                    before, after, prepared = fixture(kind)
                    plural = 'entities' if kind == 'entity' else 'devices'
                    data = after['data']
                    if mutation == 'other_audit': data[plural][1]['modified_at'] = 'foreign'
                    elif mutation == 'reorder': data[plural].reverse()
                    elif mutation == 'deleted': data['deleted_' + plural][0]['unknown'] = [1]
                    elif mutation == 'deleted_order': data['deleted_' + plural].reverse()
                    elif mutation == 'unknown': data['opaque_top']['flag'] = 1
                    elif mutation == 'envelope': after['opaque_envelope']['counter'] = 1.0
                    elif mutation == 'settings': data.setdefault('settings', {})['alien'] = True
                    elif mutation == 'target_foreign': target(after, kind)['area_id'] = 'other'
                    elif mutation == 'target_type': target(after, kind)['id'] = 1
                    elif mutation == 'nested_type':
                        field = 'options' if kind == 'entity' else 'unknown'
                        target(after, kind)[field] = {'nested': [1]}
                    elif mutation == 'child': data.setdefault('child_devices', []).append({'id': 'x'})
                    elif mutation == 'membership': data[plural].append({'id': 'new'})
                    elif mutation == 'rename_other': data[plural][1]['name_by_user'] = 'foreign'
                    elif mutation == 'audit_add': target(after, kind)['created_at'] = 'foreign'
                    with self.assertRaises(transition.RegistryTransitionError):
                        validate(before, after, prepared)

    def test_reverse_foreign_changes_and_stale_original_audit_refuse(self):
        before, after, prepared = fixture()
        restored = deepcopy(before)
        target(restored)['modified_at'] = '2026-03-03T00:00:00+00:00'
        restored['data']['entities'].append(restored['data']['entities'].pop(0))
        for mutation in ('other', 'order', 'deleted', 'target', 'stale_audit'):
            candidate = deepcopy(restored)
            audit = make_audit(after, candidate, prepared, 'reverse')
            if mutation == 'other': candidate['data']['entities'][1]['modified_at'] = 'foreign'
            elif mutation == 'order': candidate['data']['entities'].reverse()
            elif mutation == 'deleted': candidate['data']['deleted_entities'] = []
            elif mutation == 'target': target(candidate)['options']['opaque'][1] = 1
            elif mutation == 'stale_audit':
                audit = replace(audit, modified_at_before=target(before)['modified_at'])
            # Bind actual modified bytes so the strict structural rule is exercised.
            audit = replace(audit, result_sha256=digest(raw(candidate)))
            with self.subTest(mutation=mutation):
                with self.assertRaises(transition.RegistryTransitionError):
                    validate(after, candidate, prepared, direction='reverse', audit=audit)

    def test_schema_and_timestamp_and_duplicate_identity_refuse(self):
        for mutation in ('schema', 'minor_bool', 'duplicate', 'missing', 'deleted_id',
                         'audit_null', 'audit_removed', 'destination', 'wrong_key'):
            with self.subTest(mutation=mutation):
                before, after, prepared = fixture()
                if mutation == 'schema': after['minor_version'] += 1
                elif mutation == 'minor_bool': after['version'] = True
                elif mutation == 'duplicate': after['data']['entities'][1]['id'] = 'stable'
                elif mutation == 'missing': after['data']['entities'].pop(0)
                elif mutation == 'deleted_id': after['data']['deleted_entities'][0]['id'] = 'stable'
                elif mutation == 'audit_null': target(after)['modified_at'] = None
                elif mutation == 'audit_removed': target(after).pop('modified_at')
                elif mutation == 'destination':
                    before['data']['entities'][1]['entity_id'] = 'sensor.new'
                    after['data']['entities'][1]['entity_id'] = 'sensor.new'
                elif mutation == 'wrong_key': after['key'] = 'core.device_registry'
                with self.assertRaises(transition.RegistryTransitionError):
                    validate(before, after, prepared)

    def test_preparation_foreign_delta_and_invalid_values_refuse(self):
        for patch in ({'unique_id': 'foreign'}, {'id': 'foreign'}, {'name': 1},
                      {'entity_id': None}, {'entity_id': 'switch.new'}, {'modified_at': 'foreign'}):
            before, after, prepared = fixture()
            target = dict(json.loads(prepared.after_json), **patch)
            prepared = replace(prepared, after_json=json.dumps(target))
            next(r for r in after['data']['entities'] if r['id'] == 'stable').update(target)
            with self.assertRaises(transition.RegistryTransitionError):
                validate(before, after, prepared)

    def test_source_full_target_must_match_original_prepared_content(self):
        before, after, prepared = fixture()
        before['data']['entities'][0]['options']['opaque'][0] = 0
        target(after)['options']['opaque'][0] = 0
        with self.assertRaises(transition.RegistryTransitionError):
            validate(before, after, prepared)

    def test_audit_mandatory_exact_and_bound_to_direction_preparation_and_bytes(self):
        before, after, prepared = fixture()
        audit = make_audit(before, after, prepared)
        variants = [None, {}, replace(audit, modified_at_after='other'),
                    replace(audit, modified_at_before='other'), replace(audit, kind='device'),
                    replace(audit, identity='other'), replace(audit, direction='reverse'),
                    replace(audit, prepared_sha256='0' * 64),
                    replace(audit, source_sha256='0' * 64), replace(audit, result_sha256='0' * 64)]
        for invalid in variants:
            with self.subTest(audit=invalid):
                with self.assertRaises(transition.RegistryTransitionError):
                    transition.validate_registry_transition(raw(before), raw(after), prepared,
                        version='2026.10.2', direction='forward', native_effect_audit=invalid)

    def test_malformed_json_duplicate_keys_nonfinite_and_type_input_refuse(self):
        before, after, prepared = fixture()
        for bad in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'[]', b'\xff', None):
            audit = make_audit(before, after, prepared)
            if type(bad) is bytes:
                audit = replace(audit, source_sha256=digest(bad))
            with self.subTest(raw=bad):
                with self.assertRaises(transition.RegistryTransitionError):
                    transition.validate_registry_transition(bad, raw(after), prepared,
                        version='2026.10.2', direction='forward', native_effect_audit=audit)

    def test_noop_and_unsupported_scope_refuse(self):
        before, after, prepared = fixture()
        for version, direction, change in [('2027.1.0', 'forward', prepared),
                ('2026.10.2', 'undo', prepared),
                ('2026.10.2', 'forward', replace(prepared, after_json=prepared.before_json))]:
            with self.assertRaises(transition.RegistryTransitionError):
                validate(before, after, change, version, direction)


if __name__ == '__main__':
    unittest.main()
