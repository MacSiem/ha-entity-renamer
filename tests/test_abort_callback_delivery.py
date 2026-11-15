"""Peer contra-QA: real asyncio callback survives _OperandClockOwner.close() refusal.

Targets the full 60m candidate source, NOT HA/Core. Never imports or starts HA.
A genuine asyncio.TimerHandle and actual candidate close() are used. The
stubbed event callback only records deliveries, rather than invoking HA.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest


def load_exact_clock():
    path = Path(os.environ.get("CLOCK_MODULE", Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer/powercalc_operand_clock.py')).resolve(strict=True)
    pkgname = "renamer_clock_abort_peer"
    pkg = types.ModuleType(pkgname)
    pkg.__path__ = [str(path.parent)]
    sys.modules[pkgname] = pkg
    for suffix, values in {
        "native_unload_code_proof": {"unchanged": lambda *_: None},
        "native_utility_setup_audit": {"_code_objects": lambda *_: ()},
        "powercalc_source_binding": {"_json": lambda *_: b"{}", "_dispatch_token": lambda *_: None},
    }.items():
        stub = types.ModuleType(pkgname + "." + suffix)
        stub.__dict__.update(values)
        sys.modules[stub.__name__] = stub
    name = pkgname + ".powercalc_operand_clock"
    spec = importlib.util.spec_from_file_location(name, path)
    clock = importlib.util.module_from_spec(spec)
    sys.modules[name] = clock
    spec.loader.exec_module(clock)
    assert Path(clock.__file__).resolve() == path
    return clock


clock = load_exact_clock()


class AbortCleanupPeer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop = asyncio.get_running_loop()
        self.hass = object()
        self.deliveries = []
        self.handles = []

        def record_dispatch(hass, job):
            self.deliveries.append(("callback_delivered", hass is self.hass, id(job)))

        self.dispatch = record_dispatch
        self.owner = object.__new__(clock._OperandClockOwner)
        self.owner.scope = types.SimpleNamespace(loop=self.loop, hass=self.hass)
        self.owner.event = types.SimpleNamespace(_run_async_call_action=self.dispatch)
        self.owner.states = {}
        self.owner._issued_timers = {}

    async def asyncTearDown(self):
        for handle in self.handles:
            handle.cancel()

    def issue(self, name="sensor.energy_static"):
        action = clock._OperandTimerAction(self.owner, name)
        job = types.SimpleNamespace(target=action)
        handle = self.loop.call_later(0.018, self.dispatch, self.hass, job)
        self.handles.append(handle)
        row = types.SimpleNamespace(
            handle=handle, issued_handle=handle, action=action, job=job
        )
        self.owner.states[name] = row
        self.owner._issued_timers[name] = (handle, job, action)
        return row, handle

    async def test_control_intact_issued_handle_never_dispatches_after_close(self):
        row, handle = self.issue()
        self.owner.close()
        self.assertTrue(handle.cancelled())
        self.assertIsNone(row.issued_handle)
        await asyncio.sleep(0.035)
        self.assertEqual(self.deliveries, [])

    async def test_control_replaced_handle_preserves_foreign_and_cancels_issued(self):
        row, owned = self.issue()
        foreign = self.loop.call_later(0.05, self.dispatch, self.hass, row.job)
        self.handles.append(foreign)
        row.handle = foreign
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        self.assertTrue(owned.cancelled())
        self.assertFalse(foreign.cancelled())
        self.assertIsNone(row.issued_handle)
        await asyncio.sleep(0.035)
        self.assertEqual(self.deliveries, [])

    async def test_control_externally_cancelled_issued_handle_cannot_dispatch(self):
        _row, handle = self.issue()
        handle.cancel()
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        await asyncio.sleep(0.035)
        self.assertEqual(self.deliveries, [])

    async def test_negative_rebound_job_must_not_dispatch_after_two_refusals(self):
        row, issued = self.issue()
        row.job = object()  # issue ledger still retains the actual original TimerHandle
        for _ in range(2):  # caller currently closes in except and again in finally
            with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
                self.owner.close()
        await asyncio.sleep(0.035)
        self.assertEqual(
            self.deliveries, [],
            "unsafe abort: the actual issued callback fired after BOTH close() refusals"
        )
        self.assertTrue(issued.cancelled(), "issued handle must be quiesced")

    async def test_negative_rebound_action_must_not_dispatch_after_refusal(self):
        row, issued = self.issue()
        row.action = object()
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        await asyncio.sleep(0.035)
        self.assertEqual(
            self.deliveries, [],
            "unsafe abort: originally issued callback still ran after action identity changed"
        )
        self.assertTrue(issued.cancelled(), "issued handle must be quiesced")

    async def test_negative_mutated_original_job_target_still_quiesces_timer(self):
        row, issued = self.issue()
        row.job.target = object()
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        await asyncio.sleep(0.035)
        self.assertTrue(issued.cancelled())
        self.assertEqual(self.deliveries, [])

    async def test_negative_rebound_issued_field_must_not_cancel_foreign_same_shape(self):
        row, owned = self.issue()
        foreign = self.loop.call_later(0.2, self.dispatch, self.hass, row.job)
        self.handles.append(foreign)
        row.issued_handle = foreign
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        self.assertFalse(foreign.cancelled())
        self.assertTrue(owned.cancelled())
        await asyncio.sleep(0.035)
        self.assertEqual(self.deliveries, [])

    async def test_negative_rebound_native_callback_args_quiesces_original(self):
        row, issued = self.issue()
        row.handle._args = (self.hass, types.SimpleNamespace(target=object()))
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_cleanup_foreign_timer"):
            self.owner.close()
        self.assertTrue(issued.cancelled())
        await asyncio.sleep(0.035)
        self.assertEqual(self.deliveries, [])

    async def test_valid_second_close_is_idempotent(self):
        self.issue()
        self.owner.close()
        self.owner.close()
        await asyncio.sleep(0.035)
        self.assertEqual(self.deliveries, [])

    async def test_arm_records_real_timer_identity_before_cleanup(self):
        import contextvars
        name = "sensor.energy_actual_arm"
        row = types.SimpleNamespace(
            handle=None, issued_handle=None, job=None, action=None, deadline=None
        )
        self.owner.states[name] = row
        self.owner.scope.causal = contextvars.ContextVar("issued_clock_test")
        self.owner.scope.marker = object()
        self.owner.timer_proofs = []
        self.owner._typed_timer_proof = lambda _name: {"entity_id": _name}

        def fake_native_call_at(hass, action, deadline):
            self.assertIs(hass, self.hass)
            job = types.SimpleNamespace(target=action)
            handle = self.loop.call_at(deadline, self.dispatch, hass, job)
            self.handles.append(handle)
            return handle.cancel

        self.owner.event.async_call_at = fake_native_call_at
        self.owner._arm(name, self.loop.time() + 0.15)
        self.assertIs(
            self.owner._issued_timers[name][0], row.handle,
            "_arm must preserve the returned native TimerHandle identity"
        )
        self.assertIs(self.owner._issued_timers[name][1], row.job)
        self.assertIs(self.owner._issued_timers[name][2], row.action)
        handle = row.handle
        self.owner.close()
        self.assertTrue(handle.cancelled())
        await asyncio.sleep(0.035)
        self.assertEqual(self.deliveries, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
