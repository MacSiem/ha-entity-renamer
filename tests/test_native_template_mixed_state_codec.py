"""Bounded private mixed-state codec cases. Owner runs on frozen candidate.

Synthetic byte receipts test format rejection only; the native composition
roundtrip separately requires actual checker/source/program seals.
"""
import copy
from dataclasses import asdict, replace
import unittest

import test_native_template_state_yaml as selective

legacy, subject = selective.legacy, selective.subject


class MixedStateYAMLCodecTests(unittest.TestCase):
    def change(self, domains=('automation', 'script', 'template')):
        old = selective.SelectiveYAMLCodecTests().change()
        expected = tuple((domain, None, None) if domain == 'template'
                         else (domain, 'b' * 64, 'c' * 64) for domain in domains)
        receipt = replace(old.receipt, changed_domains=domains, runtime_expected=expected)
        return subject.PreparedMixedTemplateStateYAML(receipt, old.files, old.source_program_sha256)

    def test_exact_kind_roundtrip_and_identity_for_each_mixed_scope(self):
        for domains in (('automation', 'template'), ('script', 'template'),
                        ('automation', 'script', 'template')):
            with self.subTest(domains=domains):
                change = self.change(domains).checked()
                raw = legacy.prepared.encode_change(change)
                self.assertEqual(raw['kind'], 'template_mixed_state_yaml')
                decoded = legacy.prepared.decode_change(raw)
                self.assertIs(type(decoded), subject.PreparedMixedTemplateStateYAML)
                self.assertEqual(decoded, change)
                self.assertEqual(decoded.identity, 'template_mixed_state_yaml:' +
                                 legacy.module._digest(asdict(change)))
                self.assertNotEqual(decoded.identity,
                    replace(decoded, source_program_sha256='d' * 64).identity)
                self.assertEqual(decoded.receipt.runtime_expected[-1], ('template', None, None))
                self.assertEqual(decoded.receipt.unsupported_domains, ('template',))
                self.assertFalse(decoded.receipt.ready_for_apply)
                for kind in ('yaml', 'template_state_yaml'):
                    with self.assertRaises(ValueError):
                        legacy.prepared.decode_change(dict(raw, kind=kind))
                with self.assertRaises(ValueError):
                    subject.decode_template_state_yaml(raw['data'])

    def test_mixed_gate_rejects_incomplete_or_foreign_domain_proofs(self):
        raw = legacy.prepared.encode_change(self.change())
        for mutate in (
            lambda d: d.update(extra=True),
            lambda d: d.update(source_program_sha256='x' * 64),
            lambda d: d['receipt'].update(changed_domains=['template']),
            lambda d: d['receipt'].update(changed_domains=['template', 'script', 'automation']),
            lambda d: d['receipt'].update(changed_domains=['script', 'script', 'template']),
            lambda d: d['receipt'].update(changed_domains=['scene', 'template']),
            lambda d: d['receipt'].update(unsupported_domains=['automation']),
            lambda d: d['receipt'].pop('runtime_expected'),
            lambda d: d['receipt']['runtime_expected'].pop(0),
            lambda d: d['receipt']['runtime_expected'].append(['automation', 'b' * 64, 'c' * 64]),
            lambda d: d['receipt']['runtime_expected'][0].__setitem__(1, None),
            lambda d: d['receipt']['runtime_expected'][1].__setitem__(2, None),
            lambda d: d['receipt']['runtime_expected'][0].__setitem__(2, 'b' * 64),
            lambda d: d['receipt']['runtime_expected'][1].__setitem__(1, 'invalid'),
            lambda d: d['receipt']['runtime_expected'].__setitem__(2, ['scene', None, None]),
            lambda d: d['files'][0].update(before_hex='00'),
            lambda d: d['files'][0].update(path='../outside.yaml'),
        ):
            altered = copy.deepcopy(raw)
            mutate(altered['data'])
            with self.subTest(data=altered), self.assertRaises(ValueError):
                legacy.prepared.decode_change(altered)

    def test_scope_predicate_checks_exact_rows_before_source_program(self):
        check = legacy.module._assert_mixed_state_scope
        receipt = self.change().receipt
        check(receipt)
        for altered in (
            replace(receipt, runtime_expected=list(receipt.runtime_expected)),
            replace(receipt, runtime_expected=tuple(list(row) for row in receipt.runtime_expected)),
            replace(receipt, runtime_expected=receipt.runtime_expected[:-1]),
            replace(receipt, unsupported_domains=('script',)),
            replace(receipt, changed_domains=('template',)),
        ):
            with self.assertRaises(ValueError):
                check(altered)

    def test_old_selective_scope_and_subclasses_do_not_gain_new_kind(self):
        old = selective.SelectiveYAMLCodecTests().change().checked()
        encoded = legacy.prepared.encode_change(old)
        self.assertEqual(encoded['kind'], 'template_state_yaml')
        self.assertEqual(old.identity, 'template_state_yaml:' + legacy.module._digest(asdict(old)))
        with self.assertRaises(ValueError):
            legacy.prepared.decode_change(dict(encoded, kind='template_mixed_state_yaml'))
        class Derived(subject.PreparedMixedTemplateStateYAML):
            pass
        mixed = self.change()
        with self.assertRaises(ValueError):
            legacy.prepared.encode_change(Derived(mixed.receipt, mixed.files, mixed.source_program_sha256))

    def test_legacy_mixed_yaml_bytes_identity_and_none_refusal_remain_exact(self):
        mixed = self.change()
        receipt = replace(mixed.receipt, unsupported_domains=(), runtime_expected=tuple(
            (domain, 'b' * 64, 'c' * 64) for domain in mixed.receipt.changed_domains))
        old = legacy.module.PreparedYAML(receipt, mixed.files).checked()
        encoded = legacy.prepared.encode_change(old)
        self.assertEqual(encoded, {'kind': 'yaml', 'data':
                         legacy.prepared.canonical(asdict(old))[0]})
        self.assertEqual(old.identity, 'yaml:' + legacy.module._digest(asdict(old)))
        self.assertEqual(legacy.prepared.decode_change(encoded), old)
        encoded['data']['receipt']['runtime_expected'][-1][1:] = [None, None]
        with self.assertRaises(ValueError):
            legacy.prepared.decode_change(encoded)
