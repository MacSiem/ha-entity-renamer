"""Actual stateful utility counter through the mixed native receipt lifecycle."""
import test_receipted_helper_native as fixture


class NativeReceiptedUtilityTests(fixture.NativeReceiptedHelperTests):
    helper_domain = 'utility_meter'
    with_yaml = True


class NativeReceiptedUtilitySourceGapTests(NativeReceiptedUtilityTests):
    source_gap_during_unload = True


class NativeReceiptedUtilitySourceGapDuringRenameTests(NativeReceiptedUtilitySourceGapTests):
    source_gap_during_rename = True


class NativeUtilityRootAckLossTests(NativeReceiptedUtilityTests):
    lifecycle_save_fault = 'root'


class NativeUtilityIntentAckLossTests(NativeReceiptedUtilityTests):
    lifecycle_save_fault = 'intent'


class NativeUtilityReceiptAckLossTests(NativeReceiptedUtilityTests):
    lifecycle_save_fault = 'receipt'


class NativeUtilitySetupIntentAckLossTests(NativeReceiptedUtilityTests):
    setup_save_fault = 'intent'


class NativeUtilitySetupReceiptAckLossTests(NativeReceiptedUtilityTests):
    setup_save_fault = 'receipt'
