"""Lightweight behavioral regressions; does not import or start Home Assistant."""
import asyncio
from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest


COMPONENT = Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer'
package = ModuleType('renamer_activation_regression')
package.__path__ = [str(COMPONENT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__ + '.yaml_validation', COMPONENT / 'yaml_validation.py')
validation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validation
spec.loader.exec_module(validation)


class ActivationTests(unittest.IsolatedAsyncioTestCase):
    def receipt(self, domains=('automation', 'script'), unsupported=()):
        return validation.ValidationReceipt('/unused', (), (), (), domains, unsupported, '2025.6.0')

    async def test_forward_file_write_and_new_reverse_plan_keep_independent_source_identity(self):
        from custom_components.ha_entity_renamer.yaml_plan import plan_yaml
        from custom_components.ha_entity_renamer.file_mutations import apply_file_patch
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / 'config'; root.mkdir(); (root / '.storage').mkdir()
            (root / 'configuration.yaml').write_text('script: !include scripts.yaml\n')
            (root / 'scripts.yaml').write_text('fixture:\n  sequence:\n    - action: homeassistant.update_entity\n      target:\n        entity_id: sensor.old\n')
            forward = plan_yaml(root, {'sensor.old': 'sensor.new'})
            snapshots = {row.path: row for row in forward.files}
            patch = forward.patches[0]
            written = apply_file_patch(root, patch, snapshots[patch.path], 'stage_forward')
            reverse = plan_yaml(root, {'sensor.new': 'sensor.old'})
            script = next(row for row in reverse.files if row.path == 'scripts.yaml')
            self.assertEqual(script.inode, written['inode'])
            before, after = parent / 'before', parent / 'after'
            before.mkdir(); after.mkdir()
            files, _ = validation._stage(root, reverse, before, after)
            self.assertTrue(any(row[0] == 'scripts.yaml' for row in files))

    async def test_cancelled_staging_drains_before_caller_can_remove_private_files(self):
        entered, release = asyncio.Event(), asyncio.Event()
        finished = []
        async def work():
            entered.set()
            await release.wait()
            finished.append(True)
        hass = SimpleNamespace(async_add_executor_job=lambda *args: asyncio.create_task(work()))
        task = asyncio.create_task(validation._settled_executor(hass, lambda: None))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(finished, [True])

    async def test_role_revocation_after_reload_prevents_next_domain_reload(self):
        calls, allowed = [], [True]
        async def call(domain, service, data, **kwargs):
            calls.append(domain)
            allowed[0] = False
        def guard():
            if not allowed[0]:
                raise PermissionError('revoked')
        hass = SimpleNamespace(services=SimpleNamespace(has_service=lambda *args: True, async_call=call))
        with self.assertRaises(validation.YAMLValidationError):
            await validation.async_activate_yaml(hass, self.receipt(), context=None, guard=guard)
        self.assertEqual(calls, ['automation'])

    async def test_unknown_domains_and_missing_services_block_before_any_reload(self):
        calls = []
        services = SimpleNamespace(has_service=lambda domain, service: domain == 'automation',
                                   async_call=lambda *args, **kwargs: calls.append(args))
        hass = SimpleNamespace(services=services)
        with self.assertRaisesRegex(validation.YAMLValidationError, 'unsupported'):
            validation.assert_activation_available(hass, self.receipt(('template',), ('template',)))
        with self.assertRaisesRegex(validation.YAMLValidationError, 'unavailable'):
            validation.assert_activation_available(hass, self.receipt())
        self.assertEqual(calls, [])

    async def test_native_calls_are_sequential_and_partial_failure_is_not_success(self):
        calls = []
        async def call(domain, service, data, **kwargs):
            calls.append((domain, service, kwargs))
            if domain == 'script':
                raise RuntimeError('private diagnostic must not escape')
        hass = SimpleNamespace(services=SimpleNamespace(has_service=lambda *args: True, async_call=call))
        context = object()
        with self.assertRaisesRegex(validation.YAMLValidationError, '^yaml_activation_unconfirmed$'):
            await validation.async_activate_yaml(hass, self.receipt(), context=context, guard=lambda: None)
        self.assertEqual([item[:2] for item in calls], [('automation', 'reload'), ('script', 'reload')])
        self.assertTrue(all(item[2] == {'blocking': True, 'context': context} for item in calls))

    async def test_core_customize_uses_core_reload_and_requires_readback(self):
        calls = []
        async def call(*args, **kwargs):
            calls.append(args)
        hass = SimpleNamespace(services=SimpleNamespace(has_service=lambda *args: True, async_call=call))
        result = await validation.async_activate_yaml(hass, self.receipt(('homeassistant',)),
            context=None, guard=lambda: None)
        self.assertEqual(calls[0][:2], ('homeassistant', 'reload_core_config'))
        self.assertEqual(result['status'], 'reloads_completed_readback_required')


if __name__ == '__main__':
    unittest.main()
