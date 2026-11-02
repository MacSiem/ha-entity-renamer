"""Parity against the pre-extraction canonical implementation."""
import hashlib
import json
import unittest
from unittest.mock import patch

from custom_components.ha_entity_renamer import prepared
from custom_components.ha_entity_renamer.reference_planner import (
    Context, Consumer, Limits, plan_references,
)


def legacy_canonical(value):
    """Frozen oracle: preserve validation, encoding, limit and parse behavior."""
    checked = plan_references(value, {}, Context(Consumer.AUTOMATION, ()),
                              limits=Limits(max_string_length=prepared._MAX_BYTES)).value
    raw = json.dumps(checked, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode()) > prepared._MAX_BYTES:
        raise ValueError('preparation_size_limit')
    return json.loads(raw), hashlib.sha256(raw.encode()).hexdigest()


class CanonicalDigestTests(unittest.TestCase):
    def assert_equivalent(self, value):
        expected_tree, expected_digest = legacy_canonical(value)
        self.assertEqual(prepared.canonical(value), (expected_tree, expected_digest))
        self.assertEqual(prepared.canonical_digest(value), expected_digest)

    def assert_same_rejection(self, value):
        with self.assertRaises((ValueError, TypeError)) as expected:
            legacy_canonical(value)
        for function in (prepared.canonical, prepared.canonical_digest):
            with self.subTest(function=function.__name__):
                with self.assertRaises(type(expected.exception)) as actual:
                    function(value)
                self.assertEqual(actual.exception.args, expected.exception.args)

    def test_supported_values_match_legacy_bytes_and_digest(self):
        shared = {'nested': ['sensor.a']}
        for value in (None, True, False, 0, -4, 2 ** 80, 1.25, -0.0, 1e100,
                      '', 'Zażółć 😀', '\ud800', 'quote"\\\n', [], {},
                      {2: 'two', 1: 'one'}, {'z': [None, True, 3.5], 'a': {}},
                      [shared, shared]):
            with self.subTest(value=value):
                self.assert_equivalent(value)
        self.assertEqual(prepared.canonical({1: 'one'})[0], {'1': 'one'})

    def test_mutation_is_validated_and_hashed_on_every_call(self):
        value = {'rows': [{'entity_id': 'sensor.a'}]}
        before = prepared.canonical_digest(value)
        value['rows'][0]['entity_id'] = 'sensor.b'
        after = prepared.canonical_digest(value)
        self.assertNotEqual(before, after)
        self.assert_equivalent(value)
        value['rows'].append(object())
        self.assert_same_rejection(value)

    def test_canonical_tree_remains_detached_in_both_directions(self):
        value = {'rows': [{'entity_id': 'sensor.a'}]}
        tree, digest = prepared.canonical(value)
        value['rows'][0]['entity_id'] = 'sensor.b'
        self.assertEqual(tree, {'rows': [{'entity_id': 'sensor.a'}]})
        self.assertEqual(digest, prepared.canonical_digest(tree))
        tree['rows'].clear()
        self.assertEqual(value, {'rows': [{'entity_id': 'sensor.b'}]})

    def test_digest_does_not_reparse_json(self):
        value = {'rows': ['sensor.a', 'Zażółć 😀']}
        expected = legacy_canonical(value)[1]
        with patch.object(prepared.json, 'loads', side_effect=AssertionError('reparse')):
            self.assertEqual(prepared.canonical_digest(value), expected)

    def test_invalid_values_keep_legacy_rejections(self):
        class CustomString(str):
            pass
        cycle = []
        cycle.append(cycle)
        deep = None
        for _ in range(65):
            deep = [deep]
        for value in (cycle, deep, [None] * 100_000, object(), (1, 2), b'bytes',
                      CustomString('custom'), {False: 1}, {1.5: 1},
                      {'a': 1, 2: 2}, float('nan'), float('inf'),
                      float('-inf'), {'nested': [float('nan')]}):
            with self.subTest(kind=type(value).__name__):
                self.assert_same_rejection(value)

    def test_string_and_serialized_byte_limits_keep_legacy_boundary(self):
        # Small substituted cap exercises both original checks without 60 MiB allocation.
        with patch.object(prepared, '_MAX_BYTES', 32):
            self.assert_equivalent('a' * 30)  # quotes bring encoded size to exactly 32.
            self.assert_equivalent('ą' * 5)   # default ensure_ascii expands each to six bytes.
            for value in ('a' * 31, 'a' * 33, 'ą' * 6,
                          ['a' * 20, 'b' * 20], {'a' * 33: None}):
                with self.subTest(value=value):
                    self.assert_same_rejection(value)


if __name__ == '__main__':
    unittest.main()
