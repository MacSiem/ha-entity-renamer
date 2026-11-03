"""Prepared evidence stays exactly bound on every reconstruction."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('_source_fence_mixed',
    Path(__file__).with_name('test_native_mixed_input_receipts.py'))
mixed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mixed)
receipts = mixed.receipts


class SourceFenceCostTests(unittest.IsolatedAsyncioTestCase):
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
