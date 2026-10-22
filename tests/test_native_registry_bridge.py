"""Synthetic bridge contract tests; no Home Assistant dependency or HA writes.

Load isolated modules to avoid importing the integration's runtime __init__.
Run under the owner's frozen-source test receipt, not against a live HA.
"""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "ha_entity_renamer"
PACKAGE = "_native_registry_test_package"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package


def load(name):
    spec = importlib.util.spec_from_file_location(PACKAGE + "." + name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recorder_module = load("recorder_guard")
bridge_module = load("native_registry")
Bridge = bridge_module.NativeRegistryBridge
Conflict = bridge_module.RegistryConflict


class IO:
    def __init__(self):
        self.data = {"entities": [{"id": "stable", "unique_id": "integration-unique",
                     "platform": "test", "device_id": "device", "name": "Before",
                     "entity_id": "sensor.before", "disabled_by": None,
                     "area_id": "office", "options": {"opaque": True},
                     "modified_at": "baseline"}],
                     "devices": [{"id": "device", "name_by_user": "Before",
                                  "identifiers": [["test", "physical-device"]]}]}
        self.saved = deepcopy(self.data)
        self.updates = []
        self.flushes = 0
        self.is_running = True
        self.is_pending = False
        self.runtime_ok = True
        self.fail_save = False
        self.after_settle = None
        self.after_disk = None

    def running(self):
        return self.is_running

    def document(self, kind):
        return deepcopy(self.data)

    def pending(self, kind):
        return self.is_pending

    def runtime(self, change, target):
        return self.runtime_ok

    async def settle(self):
        await asyncio.sleep(0)
        if self.after_settle:
            callback, self.after_settle = self.after_settle, None
            callback()

    def mutate(self, change, target):
        self.updates.append(deepcopy(target))
        rows = self.data["entities" if change.kind == "entity" else "devices"]
        row = next(row for row in rows if row["id"] == change.identity)
        row.update(target)
        row["modified_at"] = "native-audit"
        self.is_pending = True

    async def flush(self, kind):
        self.flushes += 1
        if not self.fail_save:
            self.saved = deepcopy(self.data)
        self.is_pending = False  # Models HA's swallowed save error too.
        await asyncio.sleep(0)

    async def disk(self, kind):
        result = deepcopy(self.saved)
        await asyncio.sleep(0)
        if self.after_disk:
            callback, self.after_disk = self.after_disk, None
            callback()
        return result


class Recorder:
    def __init__(self, io):
        self.io = io
        self.baseline = {"states": {"sensor.before": {"metadata_id": 7}},
                         "statistics": {"sensor.before": {"id": 9, "source": "recorder"}}}
        self.refuse = False
        self.migrate = True
        self.after_preflight = None

    async def preflight(self, mapping):
        snapshot = await self.snapshot(mapping)
        if self.after_preflight:
            self.after_preflight()
        if self.refuse:
            snapshot["states"]["sensor.after"] = {"metadata_id": 22}
        return recorder_module.prepare(mapping, snapshot)

    async def snapshot(self, mapping):
        await asyncio.sleep(0)
        snapshot = deepcopy(self.baseline)
        if self.migrate and self.io.data["entities"][0]["entity_id"] == "sensor.after":
            for rows in snapshot.values():
                rows["sensor.after"] = rows.pop("sensor.before")
        return snapshot


class History:
    def __init__(self):
        self.content = {'states': [41, 42], 'hourly': [10, 11]}
        self.after_capture = None

    async def capture(self, mapping):
        result = deepcopy(self.content)
        if self.after_capture:
            self.after_capture()
        return result

    async def verify(self, snapshot):
        return snapshot == self.content


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.io = IO()
        self.recorder = Recorder(self.io)
        self.history = History()
        self.bridge = Bridge(self.io, self.recorder, history_guard=self.history)
        self.valid = True

    def guard(self):
        if not self.valid:
            raise Conflict("authorization_revoked")

    async def prepare(self):
        return await self.bridge.prepare("entity", "stable", {"entity_id": "sensor.after",
                                                               "name": "After"}, self.guard)

    async def test_one_native_write_preserves_identity_and_content(self):
        change = await self.prepare()
        await self.bridge.apply(change, self.guard)
        report = await self.bridge.inspect(change)
        self.assertEqual(report.position, "after")
        self.assertEqual(len(self.io.updates), 1)
        self.assertEqual(self.io.flushes, 1)
        self.assertEqual(self.io.data["entities"][0]["unique_id"], "integration-unique")
        self.assertEqual(self.io.data["entities"][0]["options"], {"opaque": True})
        self.assertTrue(report.complete_history)

    async def test_history_change_prevents_write_even_with_same_metadata(self):
        change = await self.prepare()
        self.history.content['hourly'][0] = 999
        evidence = await self.bridge.inspect(change)
        self.assertTrue(evidence.recorder_metadata)
        self.assertFalse(evidence.complete_history)
        self.assertEqual(evidence.position, 'unknown')
        with self.assertRaises(Conflict):
            await self.bridge.apply(change, self.guard)
        self.assertEqual(self.io.updates, [])

    async def test_missing_history_baseline_is_never_accepted(self):
        from dataclasses import replace
        change = replace(await self.prepare(), history_before_json='')
        self.assertEqual((await self.bridge.inspect(change)).position, 'unknown')
        with self.assertRaises(Conflict):
            await self.bridge.apply(change, self.guard)
        self.assertEqual(self.io.updates, [])

    async def test_guard_revoked_during_history_capture_refuses_prepare(self):
        self.history.after_capture = lambda: setattr(self, 'valid', False)
        with self.assertRaises(Conflict):
            await self.prepare()
        self.assertEqual(self.io.updates, [])

    async def test_fresh_guard_after_inspection_await_prevents_mutation(self):
        change = await self.prepare()
        self.io.after_settle = lambda: setattr(self, "valid", False)
        with self.assertRaises(Conflict):
            await self.bridge.apply(change, self.guard)
        self.assertEqual(self.io.updates, [])

    async def test_guard_revoked_after_native_mutation_prevents_flush(self):
        change = await self.prepare()
        original_mutate = self.io.mutate
        def mutate(*args):
            original_mutate(*args)
            self.io.after_settle = lambda: setattr(self, "valid", False)
        self.io.mutate = mutate
        with self.assertRaises(Conflict):
            await self.bridge.apply(change, self.guard)
        self.assertEqual(len(self.io.updates), 1)
        self.assertEqual(self.io.flushes, 0)

    async def test_foreign_edit_during_recorder_preflight_is_refused(self):
        self.recorder.after_preflight = lambda: self.io.data["entities"][0].update(area_id="foreign")
        with self.assertRaises(Conflict):
            await self.prepare()
        self.assertEqual(self.io.updates, [])

    async def test_swallowed_native_write_failure_remains_unknown(self):
        change = await self.prepare()
        self.io.fail_save = True
        with self.assertRaises(Conflict):
            await self.bridge.apply(change, self.guard)
        evidence = await self.bridge.inspect(change)
        self.assertEqual(evidence.position, "unknown")
        self.assertFalse(evidence.durability)
        self.assertEqual(len(self.io.updates), 1)

    async def test_recovery_inspection_does_not_flush_or_replay(self):
        change = await self.prepare()
        self.assertEqual((await self.bridge.inspect(change)).position, "before")
        self.io.mutate(change, change.after)
        self.assertEqual((await self.bridge.inspect(change)).position, "unknown")
        self.assertEqual(self.io.flushes, 0)
        self.assertEqual(len(self.io.updates), 1)
        self.io.saved = deepcopy(self.io.data)
        self.io.is_pending = False
        self.assertEqual((await self.bridge.inspect(change)).position, "after")
        self.assertEqual(self.io.flushes, 0)
        self.assertEqual(len(self.io.updates), 1)

    async def test_foreign_revision_is_conflict(self):
        change = await self.prepare()
        self.io.data["entities"][0]["area_id"] = "foreign"
        self.assertEqual((await self.bridge.inspect(change)).position, "conflict")
        with self.assertRaises(Conflict):
            await self.bridge.apply(change, self.guard)
        self.assertEqual(self.io.updates, [])

    async def test_disk_read_race_is_unknown(self):
        change = await self.prepare()
        self.io.after_disk = lambda: self.io.data["entities"][0].update(name="foreign")
        self.assertEqual((await self.bridge.inspect(change)).position, "unknown")

    async def test_recorder_destination_collision_refused(self):
        self.recorder.refuse = True
        with self.assertRaises(recorder_module.RecorderConflict):
            await self.prepare()
        self.assertEqual(self.io.updates, [])

    async def test_missing_recorder_migration_is_unknown(self):
        change = await self.prepare()
        self.recorder.migrate = False
        with self.assertRaises(Conflict):
            await self.bridge.apply(change, self.guard)
        evidence = await self.bridge.inspect(change)
        self.assertFalse(evidence.recorder_metadata)
        self.assertEqual(evidence.position, "unknown")

    async def test_unobserved_runtime_is_unknown(self):
        self.io.runtime_ok = None
        with self.assertRaises(Conflict):
            await self.prepare()
        self.assertEqual(self.io.updates, [])

    async def test_compensation_uses_one_guarded_reverse_update(self):
        change = await self.prepare()
        await self.bridge.apply(change, self.guard)
        await self.bridge.compensate(change, self.guard)
        self.assertEqual((await self.bridge.inspect(change)).position, "before")
        self.assertEqual(len(self.io.updates), 2)

    async def test_device_name_retains_identifiers(self):
        change = await self.bridge.prepare("device", "device", {"name_by_user": "After"}, self.guard)
        await self.bridge.apply(change, self.guard)
        self.assertEqual((await self.bridge.inspect(change)).position, "after")
        self.assertEqual(self.io.data["devices"][0]["identifiers"], [["test", "physical-device"]])

    async def test_malformed_native_patch_refused(self):
        for updates in ({"entity_id": None}, {"entity_id": "light.after"},
                        {"unique_id": "replacement"}, {"name": {"bad": "value"}}):
            with self.assertRaises(Conflict):
                await self.bridge.prepare("entity", "stable", updates, self.guard)
        self.assertEqual(self.io.updates, [])


if __name__ == "__main__":
    unittest.main()
