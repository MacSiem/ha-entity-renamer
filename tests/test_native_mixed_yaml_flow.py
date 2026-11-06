"""One actual mixed native/helper/include_dir YAML operation, owner-run via FIFO."""
import test_receipted_helper_native as fixture


class NativeMixedYAMLFlowTests(fixture.NativeReceiptedHelperTests):
    with_yaml = True


class NativeMixedYAMLIntentAckLossTests(NativeMixedYAMLFlowTests):
    yaml_save_fault = 'intent'


class NativeMixedYAMLReceiptAckLossTests(NativeMixedYAMLFlowTests):
    yaml_save_fault = 'receipt'
