"""Read-only binding of native HomeKit YAML seals and helper actors."""
from .helper_plan import snapshot_equal
from .native_homekit_filter import HomeKitFilterPhase
from .native_homekit_helper_observers import NativeHomeKitHelperObservers
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('homekit_yaml_binding_' + reason)


class HomeKitYAMLBinding:
    @classmethod
    async def capture(cls, hass, mapping, receipt, registry_proofs, *, guard):
        from pathlib import Path
        from .native_input_baseline import _drained
        from .yaml_validation import ValidationReceipt, assert_validation_current
        _need(type(receipt) is ValidationReceipt and receipt.version == '2026.11.1'
              and receipt.config_root == str(Path(hass.config.config_dir).resolve())
              and 'homekit' in receipt.changed_domains and 'homekit' in receipt.unsupported_domains,
              'original_receipt')
        guard()
        await _drained(hass.async_add_executor_job, lambda: assert_validation_current(receipt))
        guard()
        phase = HomeKitFilterPhase(hass, mapping, receipt.runtime_expected)
        _need(phase.position == 'before', 'original_position')
        changed = [(before, after) for before, after in zip(
            phase.capture.entries, phase.capture.expected, strict=True) if not snapshot_equal(before, after)]
        _need(bool(changed), 'affected_bridges_required')
        observer = await NativeHomeKitHelperObservers.capture(hass,
            [before for before, _ in changed], registry_proofs)
        for before, after in changed:
            key = before['identity']['entry_id']
            _need(snapshot_equal(observer.after[key], after), 'candidate_mismatch')
        guard(); phase.check()
        await _drained(hass.async_add_executor_job, lambda: assert_validation_current(receipt))
        guard(); phase.check()
        obj = cls()
        obj.hass, obj.mapping, obj.receipt = hass, dict(mapping), receipt
        obj.phase, obj.observer = phase, observer
        return obj
