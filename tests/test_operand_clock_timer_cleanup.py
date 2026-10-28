"""Exact source-class real asyncio.TimerHandle cleanup acceptance, no HA/Core import.

This is a support-only boundary test of an unfinished draft, not native product QA.
"""
import asyncio
import importlib.util
import os
import pathlib
import sys
import types
import unittest


def _load_source():
    source = pathlib.Path(os.environ.get("CLOCK_MODULE", pathlib.Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer/powercalc_operand_clock.py')).resolve()
    root = "isolated_renamer_clock_test"
    root_module = sys.modules.setdefault(root, types.ModuleType(root))
    root_module.__path__ = [str(source.parent)]
    for name, attributes in {
        "native_unload_code_proof": {"unchanged": lambda *_: None},
        "native_utility_setup_audit": {"_code_objects": lambda *_: ()},
        "powercalc_source_binding": {"_json": lambda *_: b"{}", "_dispatch_token": lambda *_: None},
    }.items():
        stub = types.ModuleType(root + "." + name)
        stub.__dict__.update(attributes)
        sys.modules[stub.__name__] = stub
    module_name = root + ".powercalc_operand_clock"
    spec = importlib.util.spec_from_file_location(module_name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    assert pathlib.Path(module.__file__).resolve() == source
    return module


clock = _load_source()


def real_dispatch(hass, job):
    return None


class TimerCleanupSourceBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop = asyncio.get_running_loop()
        self.hass = object()
        self.owner = object.__new__(clock._OperandClockOwner)
        self.owner.scope = types.SimpleNamespace(loop=self.loop, hass=self.hass)
        self.owner.event = types.SimpleNamespace(_run_async_call_action=real_dispatch)
        self.owner.states = {}
        self.owner._issued_timers = {}
        self.handles = []

    async def asyncTearDown(self):
        for handle in self.handles:
            handle.cancel()

    def add_owned(self, name):
        action = clock._OperandTimerAction(self.owner, name)
        job = types.SimpleNamespace(target=action)
        handle = self.loop.call_at(self.loop.time() + 3600, real_dispatch, self.hass, job)
        self.handles.append(handle)
        row = types.SimpleNamespace(handle=handle, action=action, job=job, issued_handle=handle)
        self.owner.states[name] = row
        self.owner._issued_timers[name] = (handle, job, action)
        return row, handle

    def foreign_handle(self):
        foreign = self.loop.call_at(self.loop.time() + 3600, real_dispatch, self.hass,
                                    types.SimpleNamespace(target=object()))
        self.handles.append(foreign)
        return foreign

    async def test_valid_owned_real_timer_is_cancelled(self):
        row, owned = self.add_owned("sensor.energy_valid")
        self.owner.close()
        self.assertTrue(owned.cancelled())
        self.assertIsNone(row.handle)
        self.assertIsNone(row.action)
        self.assertIsNone(row.job)

    async def test_foreign_timer_on_same_loop_is_not_cancelled(self):
        row, owned = self.add_owned("sensor.energy_static")
        foreign = self.foreign_handle()
        row.handle = foreign
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        self.assertFalse(foreign.cancelled(), "must not cancel a foreign scheduled callback")
        self.assertTrue(owned.cancelled(), "issued original must be safely cancelled")

    async def test_same_shape_foreign_handle_cannot_spoof_issued_identity(self):
        row, original = self.add_owned("sensor.energy_static")
        # Same callback, owner action AND HassJob, but DIFFERENT TimerHandle.
        # The earlier patch compared just these fields, so it adopted this timer.
        substitute = self.loop.call_at(self.loop.time() + 3600, real_dispatch,
                                       self.hass, row.job)
        self.handles.append(substitute)
        row.handle = substitute
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        self.assertFalse(substitute.cancelled(), "same-shape foreign handle must survive")
        self.assertTrue(original.cancelled(), "issued original remains ours to clean")

    async def test_two_owned_timers_cleanup_is_complete(self):
        left, lhandle = self.add_owned("sensor.energy_static")
        right, rhandle = self.add_owned("sensor.energy_selector")
        self.owner.close()
        self.assertTrue(lhandle.cancelled())
        self.assertTrue(rhandle.cancelled())
        self.assertIsNone(left.issued_handle)
        self.assertIsNone(right.issued_handle)

    async def test_externally_cancelled_timer_refuses_success(self):
        row, issued = self.add_owned("sensor.energy_static")
        other, intact = self.add_owned("sensor.energy_selector")
        issued.cancel()
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        self.assertTrue(issued.cancelled())
        self.assertTrue(intact.cancelled())
        self.assertIsNone(other.issued_handle)
        self.assertIs(row.issued_handle, issued)

    async def test_foreign_timer_does_not_skip_other_owned_cleanup(self):
        replaced, originally_owned = self.add_owned("sensor.energy_static")
        intact, owned = self.add_owned("sensor.energy_selector")
        foreign = self.foreign_handle()
        replaced.handle = foreign
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        self.assertFalse(foreign.cancelled())
        self.assertTrue(owned.cancelled(), "cleanup must still cancel the independently proven owned handle")
        self.assertIsNone(intact.handle)
        self.assertTrue(originally_owned.cancelled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
