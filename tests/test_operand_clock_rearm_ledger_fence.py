"""60m independent fail-closed repair: one real asyncio TimerHandle can be orphaned by _arm().

Source: the 60m Powercalc draft, pinned by CLOCK_MODULE. No Home Assistant
installation, configuration, runtime or native QA is touched.
The state-loss injection is synthetic. Its native reachability is UNKNOWN.
"""

from __future__ import annotations

import asyncio
import contextvars
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


def load_clock():
    path = Path(os.environ.get("CLOCK_MODULE", Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer/powercalc_operand_clock.py')).resolve(strict=True)
    package = "renamer60_rearm_ledger_fence"
    root = types.ModuleType(package)
    root.__path__ = [str(path.parent)]
    sys.modules[package] = root
    for suffix, attrs in {
        "native_unload_code_proof": {"unchanged": lambda *_: None},
        "native_utility_setup_audit": {"_code_objects": lambda *_: ()},
        "powercalc_source_binding": {
            "_json": lambda *_: b"{}", "_dispatch_token": lambda *_: None,
        },
    }.items():
        module = types.ModuleType(package + "." + suffix)
        module.__dict__.update(attrs)
        sys.modules[module.__name__] = module
    name = package + ".powercalc_operand_clock"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == path
    return module


class JobType(Enum):
    Callback = "Callback"


class HassJob:
    def __init__(self, target):
        self.target = target
        self.job_type = JobType.Callback


def install_native_like_job_types():
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    core = types.ModuleType("homeassistant.core")
    core.HassJob = HassJob
    core.HassJobType = JobType
    return {"homeassistant": ha, "homeassistant.core": core}


clock = load_clock()


class IssuedLedgerRearmPeer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Scope the synthetic types to this isolated source-boundary test.
        modules = patch.dict(sys.modules, install_native_like_job_types())
        modules.start()
        self.addCleanup(modules.stop)
        self.loop = asyncio.get_running_loop()
        self.hass = object()
        self.received = []
        self.handles = []
        self.name = "sensor.energy_direct"
        self.marker = object()
        self.causal = contextvars.ContextVar("renamer90_causal")

        def dispatch(hass, job):
            self.received.append((hass is self.hass, job.target))

        self.dispatch = dispatch

        def native_like_call_at(hass, action, deadline):
            self.assertIs(hass, self.hass)
            job = HassJob(action)
            handle = self.loop.call_at(deadline, dispatch, hass, job)
            self.handles.append(handle)
            return handle.cancel

        self.owner = object.__new__(clock._OperandClockOwner)
        self.owner.scope = types.SimpleNamespace(
            loop=self.loop, hass=self.hass, causal=self.causal, marker=self.marker
        )
        self.owner.event = types.SimpleNamespace(
            _run_async_call_action=dispatch, async_call_at=native_like_call_at
        )
        self.owner.timer_action_code = clock._OperandTimerAction.__call__.__code__
        self.owner._issued_timers = {}
        self.owner.timer_proofs = []
        self.row = types.SimpleNamespace(
            handle=None, issued_handle=None, job=None, action=None,
            deadline=None, anchor=datetime.now(timezone.utc),
            value=Decimal("2.5"), engine=object()
        )
        self.owner.states = {self.name: self.row}

    async def asyncTearDown(self):
        for handle in self.handles:
            handle.cancel()

    def arm(self, delay):
        self.owner._arm(self.name, self.loop.time() + delay)
        return self.row.handle

    async def test_control_actual_source_arm_rearm_close(self):
        first = self.arm(0.019)
        second = self.arm(0.2)
        self.assertTrue(first.cancelled())
        self.assertIs(self.owner._issued_timers[self.name][0], second)
        self.owner.close()
        self.assertTrue(second.cancelled())
        await asyncio.sleep(0.05)
        self.assertEqual(self.received, [])


    async def test_negative_lost_mutable_handle_must_cancel_and_refuse_rearm(self):
        first = self.arm(0.019)
        self.row.handle = None  # synthetic drift, native reachability unknown
        with self.assertRaisesRegex(ValueError, "typed_timer_ownership_changed"):
            self.arm(0.20)
        self.assertTrue(first.cancelled(), "issued timer must be cancelled before refusal")
        self.owner.close()
        await asyncio.sleep(0.05)
        self.assertEqual(self.received, [], "no late callback following refusal")

    async def test_negative_foreign_issued_field_never_cancels_foreign(self):
        first = self.arm(0.019)
        foreign = self.loop.call_later(0.2, self.dispatch, self.hass, HassJob(object()))
        self.handles.append(foreign)
        self.row.handle = None
        self.row.issued_handle = foreign
        with self.assertRaisesRegex(ValueError, "typed_timer_ownership_changed"):
            self.arm(0.22)
        self.assertTrue(first.cancelled())
        self.assertFalse(foreign.cancelled(), "foreign TimerHandle must survive")
        with self.assertRaisesRegex(ValueError, "cleanup_foreign_timer"):
            self.owner.close()
        self.assertFalse(foreign.cancelled())
        await asyncio.sleep(0.05)
        self.assertEqual(self.received, [])

    async def test_negative_foreign_action_after_handle_loss(self):
        first = self.arm(0.019)
        self.row.handle = None
        self.row.action = object()
        with self.assertRaisesRegex(ValueError, "typed_timer_ownership_changed"):
            self.arm(0.20)
        self.assertTrue(first.cancelled())
        with self.assertRaisesRegex(ValueError, "cleanup_foreign_timer"):
            self.owner.close()
        await asyncio.sleep(0.05)
        self.assertEqual(self.received, [])

    async def test_control_foreign_rebound_is_refused_without_foreign_cancel(self):
        first = self.arm(0.019)
        foreign = self.loop.call_later(0.2, self.dispatch, self.hass, HassJob(object()))
        self.handles.append(foreign)
        self.row.handle = foreign
        with self.assertRaisesRegex(ValueError, "typed_timer_ownership_changed"):
            self.arm(0.22)
        with self.assertRaisesRegex(ValueError, "cleanup_foreign_timer"):
            self.owner.close()
        self.assertTrue(first.cancelled())
        self.assertFalse(foreign.cancelled())
        await asyncio.sleep(0.05)
        self.assertEqual(self.received, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
