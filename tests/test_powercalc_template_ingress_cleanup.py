"""Fault and cancellation handling for deferred native Template events."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


# Load the small ingress module without requiring an installed Home Assistant.
PACKAGE = '_ingress_cleanup_test'
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')]
sys.modules[PACKAGE] = package
consumers = ModuleType(PACKAGE + '.native_template_consumers')
def need(value, reason):
    if not value:
        raise ValueError(reason)
consumers._need, consumers._bound = need, lambda *args: True
sys.modules[consumers.__name__] = consumers
rename = ModuleType(PACKAGE + '.native_template_rename_ingress')
rename.NativeTemplateRenameIngress = type('NativeTemplateRenameIngress', (), {})
sys.modules[rename.__name__] = rename
proof = ModuleType(PACKAGE + '.native_unload_code_proof')
proof.unchanged = lambda *args: None
sys.modules[proof.__name__] = proof
spec = importlib.util.spec_from_file_location(PACKAGE + '.native_template_powercalc_ingress',
    Path(package.__path__[0]) / 'native_template_powercalc_ingress.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IngressCleanupTests(unittest.IsolatedAsyncioTestCase):
    def make_ingress(self, action=None):
        ingress = module.PowercalcTemplateIngress()
        original, wrapper = object(), object()
        filtered = SimpleNamespace(_action=wrapper if action is None else action)
        job = SimpleNamespace(target=wrapper)
        ingress.rows = [(filtered, original, wrapper, (job,))]
        ingress.pending, ingress.error, ingress.monitor_slot = {}, None, None
        ingress.hass = ingress.event = None
        ingress.refs = ingress.methods = ingress.codes = ()
        return ingress, filtered, job, original, wrapper

    def test_foreign_action_and_callback_failure_still_flush_all(self):
        foreign = object()
        ingress, filtered, job, _, wrapper = self.make_ingress(foreign)
        seen = []
        def fails(event):
            seen.append(event)
            raise RuntimeError('callback failed')
        ingress.pending = {1: (fails, 'first', None), 2: (seen.append, 'second', None)}
        with self.assertRaisesRegex(ValueError, 'ingress_replaced') as caught:
            ingress.close()
        self.assertEqual(seen, ['first', 'second'])
        self.assertEqual(ingress.pending, {})
        self.assertIs(filtered._action, foreign)
        self.assertIs(job.target, wrapper)
        self.assertIn('callback failed', ' '.join(caught.exception.__notes__))

    def test_jobs_failure_and_cancelled_callback_flush_all(self):
        ingress, filtered, job, _, wrapper = self.make_ingress()
        seen = []
        def cancelled(event):
            seen.append(event)
            raise asyncio.CancelledError('callback cancelled')
        ingress.pending = {1: (cancelled, 'first', None), 2: (seen.append, 'second', None)}
        with patch.object(rename.NativeTemplateRenameIngress, 'native_jobs', side_effect=RuntimeError('jobs failed'), create=True):
            with self.assertRaisesRegex(RuntimeError, 'jobs failed') as caught:
                ingress.close()
        self.assertEqual(seen, ['first', 'second'])
        self.assertIs(filtered._action, wrapper)
        self.assertIs(job.target, wrapper)
        self.assertIn('callback cancelled', ' '.join(caught.exception.__notes__))

    async def test_task_cancellation_flushes_original_event(self):
        ingress, filtered, job, original, wrapper = self.make_ingress()
        seen = []
        ingress.pending = {1: (seen.append, 'event', None)}
        ready = asyncio.Event()
        async def reload():
            try:
                ready.set()
                await asyncio.Event().wait()
            finally:
                ingress.close()
        with patch.object(rename.NativeTemplateRenameIngress, 'native_jobs', return_value=[job], create=True):
            task = asyncio.create_task(reload())
            await ready.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(seen, ['event'])
        self.assertIs(filtered._action, original)
        self.assertIs(job.target, original)
