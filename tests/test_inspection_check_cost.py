"""Pure synchronous check; real receipt chain, no HA or physical disk admission."""
import asyncio
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_native_mixed_input_receipts as mixed

observer = importlib.import_module(mixed.base.PACKAGE + '.native_template_yaml')


def checked(f, state, *, guard=lambda: None):
    obj = object.__new__(observer.NativeTemplateYAMLRuntimeReadback)
    obj.hass = SimpleNamespace(config=SimpleNamespace(config_dir=f['receipt'].config_root))
    obj.chain, obj.receipt, obj.evidence, obj.guard = f['chain'], f['receipt'], object(), guard
    obj.assert_reload_current = lambda: None  # native reload proof is outside this pure fixture
    obj._check(state)


class InspectionCheckCostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = await mixed.mixed_fixture()
        self.state = self.f['chain'].reconstruct_inputs()

    async def test_non_live_check_reconstructs_once_for_match_and_once_for_independent_bound(self):
        chain = self.f['chain']
        with patch.object(chain, '_reconstruct', wraps=chain._reconstruct) as reconstruct:
            checked(self.f, self.state)
        self.assertEqual(reconstruct.call_count, 2)

    async def test_foreign_document_source_guard_and_durable_drift_still_refuse(self):
        checked(self.f, self.state)
        self.state._documents['core.entity_registry']['data']['entities'][2]['name'] = 'foreign'
        with self.assertRaises(ValueError):
            checked(self.f, self.state)
        self.state = self.f['chain'].reconstruct_inputs()
        self.state._sources['scripts.yaml'][0] = '0' * 64
        with self.assertRaises(ValueError):
            checked(self.f, self.state)
        self.state = self.f['chain'].reconstruct_inputs()
        with self.assertRaises(ValueError):
            checked(self.f, self.state, guard=lambda: False)
        await asyncio.sleep(0)
        self.f['store'].rows[self.f['chain'].root_id]['payload']['fingerprint'] = 'foreign'
        with self.assertRaises(ValueError):
            checked(self.f, self.state)

    async def test_independent_bound_and_live_hook_are_still_called(self):
        def denied():
            raise ValueError('independent evidence changed')
        self.state._evidence_check = denied
        with self.assertRaisesRegex(ValueError, 'independent evidence changed'):
            checked(self.f, self.state)
        self.state._evidence_check = None
        self.state._live_check = denied
        with self.assertRaisesRegex(ValueError, 'independent evidence changed'):
            checked(self.f, self.state)

    async def test_preparation_drift_and_runtime_root_change_after_await_refuse(self):
        checked(self.f, self.state)
        await asyncio.sleep(0)
        self.f['preparations'].plans['yaml-change']['payload']['data']['files'][0]['after_hex'] = '00'
        with self.assertRaises(ValueError):
            checked(self.f, self.state)
        self.f = await mixed.mixed_fixture()
        self.state = self.f['chain'].reconstruct_inputs()
        obj = object.__new__(observer.NativeTemplateYAMLRuntimeReadback)
        obj.hass = SimpleNamespace(config=SimpleNamespace(config_dir='/foreign/runtime'))
        obj.chain, obj.receipt, obj.evidence, obj.guard = self.f['chain'], self.f['receipt'], object(), lambda: None
        obj.assert_reload_current = lambda: None
        with self.assertRaises(ValueError):
            obj._check(self.state)

    async def test_live_state_keeps_document_only_comparison_and_invokes_hook(self):
        seen = []
        self.state._live_check = lambda: seen.append('checked')
        self.state._sources['scripts.yaml'][2] += 1  # live partial inode is not the accepted image
        checked(self.f, self.state)
        self.assertEqual(seen, ['checked'])
