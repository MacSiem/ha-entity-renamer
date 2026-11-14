"""Exact affected native safety cohort; no inherited or completed matrices.

All bodies use actual Core fixtures, native effects and original readbacks.
The owner runs this cohort through FIFO on minimum and current Core.
"""
import unittest

import test_native_automation_duration_prefix_root as duration
import test_native_automation_prefix_root as ordinary


def load_tests(loader, tests, pattern):
    selected = (
        'test_partial_duration_keys_write_restores_before_native_api',
        'test_partial_duration_map_clear_restores_before_native_api',
        'test_partial_duration_wrapper_registration_restores_before_native_api',
        'test_partial_retained_foreign_route_conflict_is_preserved_before_any_cleanup',
        'test_partial_retained_two_routes_restore_shared_native_callback_lists',
        'test_partial_retained_unstarted_route_preserves_owned_config_projection',
        'test_native_pending_context_drift_cannot_acknowledge_active_expiry',
        'test_native_pending_expiry_during_active_root_keeps_one_action_and_fresh_inverse',
    )
    suite = unittest.TestSuite(duration.NativeAutomationDurationPrefixRootTests(name)
                               for name in selected)
    # The occurrence-monitor lifetime also changed for the existing no-for
    # Root. Reuse its real normal and post-ADD authorization-loss bodies.
    suite.addTest(ordinary.NativeAutomationPrefixRootTests(
        'test_meaningful_17_to_29_native_actions_once_and_fresh_inverse'))
    suite.addTest(ordinary.NativeAutomationPrefixRootTests(
        'test_post_add_authorization_loss_still_delivers_meaningful_native_action_once'))
    return suite
