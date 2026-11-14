"""Source-executed fail-closed interval guard, NOT native HA/Core qualification.

The actual _OperandClockOwner._area callsite is loaded from an exact pinned
owner proposal. A transparent Decimal writer probe checks that no native
_update_integral writer could be reached with reversed/untyped timestamps.
Positive cases only confirm the guard does not change the calling convention.
"""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo


def load_actual_source():
    source = Path(os.environ.get("CLOCK_MODULE", Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer/powercalc_operand_clock.py')).resolve()
    root = "isolated_operand_time_guard"
    module = types.ModuleType(root)
    module.__path__ = [str(source.parent)]
    sys.modules[root] = module
    for name, attrs in {
        "native_unload_code_proof": {"unchanged": lambda *_: None},
        "native_utility_setup_audit": {"_code_objects": lambda *_: ()},
        "powercalc_source_binding": {"_json": lambda *_: b"{}", "_dispatch_token": lambda *_: None},
    }.items():
        stub = types.ModuleType(root + "." + name)
        stub.__dict__.update(attrs)
        sys.modules[stub.__name__] = stub
    name = root + ".powercalc_operand_clock"
    spec = importlib.util.spec_from_file_location(name, source)
    target = importlib.util.module_from_spec(spec)
    sys.modules[name] = target
    spec.loader.exec_module(target)
    assert Path(target.__file__).resolve() == source
    return target


actual = load_actual_source()


class Trigger(Enum):
    StateEvent = 1
    TimeElapsed = 2


class DecimalProbe:
    def validate_states(self, before, after):
        return Decimal(before), Decimal(after)

    def calculate_area_with_two_states(self, elapsed, before, after):
        return elapsed * before

    def calculate_area_with_one_state(self, elapsed, before):
        return elapsed * before


class WriterProbe:
    def __init__(self):
        self._state = Decimal("10")
        self._method = DecimalProbe()
        self._last_integration_time = None
        self._last_integration_trigger = None
        self.writes = []

    def _update_integral(self, value):
        self.writes.append(value)
        self._state += value


class SourceOperandTimeBoundary(unittest.TestCase):
    def setUp(self):
        self.base = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)
        self.engine = WriterProbe()
        self.row = actual._Clock(object(), object(), self.engine)
        self.row.value = Decimal("8.51")
        self.row.endpoint = self.base
        self.row.anchor = self.base
        self.row.trigger = Trigger.StateEvent
        self.owner = object.__new__(actual._OperandClockOwner)
        self.owner.states = {"sensor.energy_pair": self.row}
        self.owner.integration = types.SimpleNamespace(_IntegrationTrigger=Trigger)
        self.owner.depth = 0
        self.owner.steps = []

    def refuse_without_writing(self, at, *, kind, next_value=None):
        previous = (self.engine._state, self.row.endpoint, self.row.anchor,
                    self.row.trigger, self.row.value)
        with self.assertRaisesRegex(ValueError, "powercalc_operand_clock_nonmonotonic_integral_interval"):
            self.owner._area("sensor.energy_pair", at, kind=kind, next_value=next_value)
        self.assertEqual(self.engine.writes, [])
        self.assertEqual(self.owner.steps, [])
        self.assertEqual((self.engine._state, self.row.endpoint, self.row.anchor,
                          self.row.trigger, self.row.value), previous)
        self.assertEqual(self.owner.depth, 0)

    def test_negative_operand_returned_time_must_not_decrease_energy(self):
        self.refuse_without_writing(
            self.base - timedelta(microseconds=1),
            kind="operand", next_value=Decimal("1.31"))

    def test_negative_actual_timer_delivery_must_not_decrease_energy(self):
        self.row.trigger = Trigger.TimeElapsed
        self.refuse_without_writing(self.base - timedelta(seconds=2), kind="timer")

    def test_negative_resume_after_timeelapsed_anchor_must_not_write(self):
        self.row.trigger = Trigger.TimeElapsed
        self.refuse_without_writing(
            self.base - timedelta(milliseconds=4),
            kind="resume", next_value=Decimal("2.71"))

    def test_naive_callback_time_must_be_explicitly_refused(self):
        self.refuse_without_writing(self.base.replace(tzinfo=None), kind="timer")

    def test_invalid_endpoint_type_must_be_explicitly_refused(self):
        self.refuse_without_writing("2026-10-26T20:00:00+00:00", kind="operand",
                                    next_value=Decimal("2.71"))

    def test_zero_time_is_not_a_negative_interval(self):
        self.owner._area("sensor.energy_pair", self.base,
                         kind="operand", next_value=Decimal("1.31"))
        self.assertEqual(self.engine.writes, [Decimal(0)])
        self.assertEqual(self.row.value, Decimal("1.31"))
        self.assertEqual(self.owner.depth, 0)

    def test_forward_time_preserves_decimal_writer_path(self):
        self.owner._area("sensor.energy_pair", self.base + timedelta(seconds=3),
                         kind="operand", next_value=Decimal("1.31"))
        self.assertEqual(self.engine.writes, [Decimal("25.53")])
        self.assertEqual(self.engine._state, Decimal("35.53"))
        self.assertEqual(self.owner.depth, 0)

    def test_aware_time_offset_same_instant_is_zero(self):
        offset = timezone(timedelta(hours=2))
        same = self.base.astimezone(offset)
        self.owner._area("sensor.energy_pair", same, kind="timer")
        self.assertEqual(self.engine.writes, [Decimal(0)])
        self.assertEqual(self.owner.depth, 0)

    def test_second_review_real_dst_fallback_same_zone_must_use_absolute_seconds(self):
        tz = ZoneInfo("Europe/Warsaw")
        # 2026-10-25 Europe/Warsaw 02:15 CEST -> 02:30 CET.
        # Actual elapsed is 75 minutes, NOT 15 wall-clock minutes.
        self.row.endpoint = datetime(2026, 10, 25, 2, 15, tzinfo=tz, fold=0)
        self.row.anchor = self.row.endpoint
        end = datetime(2026, 10, 25, 2, 30, tzinfo=tz, fold=1)
        self.owner._area("sensor.energy_pair", end,
                         kind="operand", next_value=Decimal("1.31"))
        self.assertEqual(self.engine.writes, [Decimal(75 * 60) * Decimal("8.51")])

    def test_second_review_real_dst_fallback_wall_time_reversed_but_absolute_forward(self):
        tz = ZoneInfo("Europe/Warsaw")
        self.row.endpoint = datetime(2026, 10, 25, 2, 30, tzinfo=tz, fold=0)
        self.row.anchor = self.row.endpoint
        end = datetime(2026, 10, 25, 2, 15, tzinfo=tz, fold=1)
        self.owner._area("sensor.energy_pair", end,
                         kind="operand", next_value=Decimal("1.31"))
        self.assertEqual(self.engine.writes, [Decimal(45 * 60) * Decimal("8.51")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
