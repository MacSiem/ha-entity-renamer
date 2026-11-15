"""Checker request and snapshot filesystem work stays off the HA loop."""
import asyncio
import base64
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.ha_entity_renamer import yaml_validation as subject


class ValidationIOExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_request_and_snapshot_on_executor(self):
        loop_thread = threading.get_ident()
        hass = SimpleNamespace(async_add_executor_job=lambda fn, *args: asyncio.to_thread(fn, *args))
        with tempfile.TemporaryDirectory(dir='/Volumes/Samsung_SSD') as temp:
            root = Path(temp)
            storage = root / '.storage'
            storage.mkdir()
            (storage / 'repairs.issue_registry').write_bytes(b'{"issues":[]}')
            request = root / 'request.json'
            calls = []
            def write(path, value):
                calls.append(threading.get_ident())
                subject._write_private_request(path, value)
            original = Path.read_bytes
            def read(path):
                calls.append(threading.get_ident())
                return original(path)
            await subject._settled_executor(hass, write, request, '{"safe":true}')
            plan = SimpleNamespace(files=(), directories=(), powercalc_inputs=None)
            with patch.object(Path, 'read_bytes', read):
                snapshot = await subject._settled_executor(hass, subject._checker_repairs_snapshot,
                    plan, root, (('.storage', (), ('repairs.issue_registry',)),), None, None)
            self.assertEqual(snapshot, ('present', base64.b64encode(b'{"issues":[]}').decode()))
            self.assertEqual(request.read_text(), '{"safe":true}')
            self.assertEqual(stat.S_IMODE(request.stat().st_mode), 0o600)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(thread != loop_thread for thread in calls))
            plan.files = (SimpleNamespace(path='.storage/repairs.issue_registry'),)
            self.assertIsNone(await subject._settled_executor(hass, subject._checker_repairs_snapshot,
                plan, root, (('.storage', (), ('repairs.issue_registry',)),), None, None))


if __name__ == '__main__':
    unittest.main()
