"""Remaining real duration cancellation/replacement/admission transitions.

Reuse the existing full Root bodies, without rediscovering completed cases.
"""
import unittest

import test_native_automation_duration_prefix_root as duration


def load_tests(loader, tests, pattern):
    selected = (
        'test_genuine_new_id_change_cancels_native_pending_and_fresh_inverse_keeps_terminal',
        'test_meaningful_pair_cancels_old_native_pending_and_starts_one_own_duration',
        'test_queued_foreign_native_event_cancels_new_own_duration_once',
        'test_pre_effect_authorization_loss_restores_only_own_duration_transport',
        'test_foreign_cancel_route_is_refused_before_effect_without_cleaning_it',
        'test_native_match_all_overwrite_orphan_refuses_before_effect_and_is_preserved',
    )
    return unittest.TestSuite(duration.NativeAutomationDurationPrefixRootTests(name)
                              for name in selected)
