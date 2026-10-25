"""Actual stateful utility counter through the mixed native receipt lifecycle."""
import test_receipted_helper_native as fixture


class NativeReceiptedUtilityTests(fixture.NativeReceiptedHelperTests):
    helper_domain = 'utility_meter'
    with_yaml = True
