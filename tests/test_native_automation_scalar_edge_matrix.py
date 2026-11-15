"""Actual scalar render/CV failure and computed-zero Root boundaries."""
import unittest
import test_native_automation_scalar_duration_root as scalar


def load_tests(loader, tests, pattern):
    names = (
        'test_scalar_render_error_after_genuine_pending_cancels_without_new_timer',
        'test_scalar_cv_error_after_genuine_pending_cancels_without_new_timer',
        'test_scalar_computed_zero_native_api_natural_expiry_full_root_and_fresh_inverse',
    )
    return unittest.TestSuite(scalar.NativeAutomationScalarDurationRootTests(name)
                              for name in names)
