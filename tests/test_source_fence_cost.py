"""Prepared evidence stays exactly bound on every reconstruction."""
import ast
from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('_source_fence_mixed',
    Path(__file__).with_name('test_native_mixed_input_receipts.py'))
mixed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mixed)
receipts = mixed.receipts


class SourceFenceCostTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_binding_check_fences_once_and_rejects_drift(self):
        # Execute the actual observer check with a real synthetic receipt chain.
        binding = importlib.import_module(mixed.base.PACKAGE + '.native_template_state_binding')
        program_type = importlib.import_module(mixed.base.PACKAGE + '.template_state_program').StateConfigProgram
        fixture = await mixed.mixed_fixture()
        chain = fixture['chain']
        mapping = {old: new for change in fixture['changes'] for old, new in change.mapping.items()}
        program = program_type('a', 'b', b'{}', b'{}', tuple(mapping.items()), (), (), b'[]')
        state = chain.reconstruct_inputs()
        arguments = {'live_intent': None, 'live_context': None, 'live_calls': ()}
        prefix = binding.accepted_state_prefix(chain, program, input_state=state, **arguments)
        observer = next(node for node in ast.parse(Path(binding.__file__).read_text()).body
                        if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == 'async_observe_accepted_state_bindings')
        check = next(node for node in observer.body
                     if isinstance(node, ast.FunctionDef) and node.name == 'check')
        source = ast.fix_missing_locations(ast.Module(body=[check], type_ignores=[]))
        guard_current = True
        def guard():
            return guard_current
        scope = dict(guard=guard, inspect=__import__('inspect'), _need=binding._need,
                     accepted_prefix=prefix, accepted_state_prefix=binding.accepted_state_prefix,
                     chain=chain, program=program, state=state, arguments=arguments)
        exec(compile(source, binding.__file__, 'exec'), scope)
        with patch.object(binding, '_state_inputs', wraps=binding._state_inputs) as inputs:
            scope['check']()
        self.assertEqual(inputs.call_count, 1)
        guard_current = False
        with self.assertRaises(ValueError):
            scope['check']()
        guard_current = True
        row = fixture['preparations'].plans['yaml-change']['payload']
        row['data']['receipt']['source_files'][0][1] = '0' * 64
        with self.assertRaises((ValueError, receipts.RegistryReceiptError)):
            scope['check']()

    def test_detached_evidence_distinguishes_signed_zero_and_nested_type_drift(self):
        original = {'nested': [0.0, -0.0, 1, True, {'x': 1.5}]}
        evidence = receipts._freeze_json(original)
        self.assertTrue(receipts._exact_json_equal(deepcopy(original), evidence))
        for changed in (
            {'nested': [-0.0, -0.0, 1, True, {'x': 1.5}]},
            {'nested': [0.0, 0.0, 1, True, {'x': 1.5}]},
            {'nested': [0.0, -0.0, 1.0, True, {'x': 1.5}]},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(receipts._exact_json_equal(changed, evidence))
        self.assertTrue(receipts._exact_json_equal(deepcopy(original), evidence))

    def test_receipt_tuple_snapshot_catches_nested_identity_and_type_changes(self):
        original = ('/config', (('file', 1, 2),), (('dir', (('file', 33188),)),))
        evidence = receipts._freeze_json(original)
        self.assertTrue(receipts._exact_json_equal(original, evidence))
        self.assertFalse(receipts._exact_json_equal(('/config', (('file', True, 2),), original[2]), evidence))
        self.assertFalse(receipts._exact_json_equal(('/config', (('file', 1, 3),), original[2]), evidence))
        self.assertFalse(receipts._exact_json_equal(('/config', [['file', 1, 2]], original[2]), evidence))
        self.assertTrue(receipts._exact_json_equal(original, evidence))

    def test_yaml_fold_computes_identity_once_and_rejects_wrong_call_binding(self):
        accepted = importlib.import_module(mixed.base.PACKAGE + '.accepted_input_state')
        transaction = importlib.import_module(mixed.base.PACKAGE + '.yaml_transaction')
        class Change:
            files = tuple(SimpleNamespace(path=name) for name in ('one', 'two', 'three'))
            calls = 0
            @property
            def identity(self):
                self.calls += 1
                return 'original-identity'
        change = Change()
        ids = ['yaml_' + transaction._digest(('original-identity', row.path, False))[:64]
               for row in change.files]
        self.assertEqual(accepted.fold_yaml_calls(change, {}, [], 'forward', ids, partial=True), {})
        self.assertEqual(change.calls, 1)
        with self.assertRaises(receipts.RegistryReceiptError):
            accepted.fold_yaml_calls(change, {}, [], 'forward', ids[:-1] + ['foreign'], partial=True)

    async def test_reconstruction_skips_only_yaml_decode_and_returns_detached_value(self):
        f = await mixed.mixed_fixture()
        chain = f['chain']
        with patch.object(receipts, 'decode_change', wraps=receipts.decode_change) as decoder:
            first = chain._original_change(3)
            second = chain._original_change(3)
            chain.reconstruct_inputs()
        self.assertEqual(first, f['yaml'])
        self.assertEqual(second, f['yaml'])
        self.assertIsNot(first, second)
        self.assertTrue(all(call.args[0]['kind'] != 'yaml' for call in decoder.call_args_list))
        self.assertEqual(len(decoder.call_args_list), 3)  # registry steps still re-resolve

    async def test_every_stored_node_and_binding_revalidated_even_with_cached_chain(self):
        f = await mixed.mixed_fixture()
        chain = f['chain']
        chain.reconstruct_inputs()
        row = f['preparations'].plans['yaml-change']['payload']
        original = deepcopy(row)
        for mutate in (
            lambda r: r['data']['receipt']['source_files'][0].__setitem__(1, '0' * 64),
            lambda r: r['data']['receipt']['source_files'][0].__setitem__(2, True),
            lambda r: r['data']['receipt']['source_directories'].append(['extra', [], None]),
            lambda r: r['data']['files'][0].__setitem__('after_hex', '00'),
        ):
            row.clear(); row.update(deepcopy(original)); mutate(row)
            with self.assertRaises(receipts.RegistryReceiptError):
                chain.reconstruct_inputs()
        row.clear(); row.update(original)
        chain.changes[3] = replace(f['yaml'], files=())
        with self.assertRaises(receipts.RegistryReceiptError):
            chain.reconstruct_inputs()
        chain.changes[3] = f['yaml']
        chain.root['bindings'][3]['yaml_digest'] = '0' * 64
        with self.assertRaises(receipts.RegistryReceiptError):
            chain._original_change(3)
