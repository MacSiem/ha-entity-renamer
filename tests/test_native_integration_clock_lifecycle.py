"""Existing source-clock actual fixture composed with shared lifecycle receipts.

Executable draft: owner runs through FIFO on the two pinned Core versions.
Own-ID+timer and saved-source-UUID-without-helper-step are not claimed here.
"""
import unittest

import test_native_integration_source_clock as positive


@unittest.skipUnless(positive.fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationClockLifecycleTests(positive.NativeIntegrationSourceClockTests):
    shared_lifecycle=True


class NativeIntegrationClockRightLifecycleTests(NativeIntegrationClockLifecycleTests):
    method='right'


class NativeIntegrationClockTrapezoidalLifecycleTests(NativeIntegrationClockLifecycleTests):
    method='trapezoidal'


class NativeIntegrationClockNaturalZeroLifecycleTests(NativeIntegrationClockLifecycleTests):
    initial_power='0'
