"""One actual mixed native/helper/include_dir YAML operation, owner-run via FIFO."""
import test_receipted_helper_native as fixture


class NativeMixedYAMLFlowTests(fixture.NativeReceiptedHelperTests):
    with_yaml = True
