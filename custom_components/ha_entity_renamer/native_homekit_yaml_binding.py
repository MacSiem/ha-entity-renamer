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

    def verify_foreign(self):
        """Foreign bridges stay active and retain their exact native filter."""
        from homeassistant.components.homekit import FILTER_SCHEMA
        from .helper_native import read_entry
        from .native_homekit_filter import _bridge_identity
        captured = self.phase.capture
        ids = tuple(row['identity']['entry_id'] for row in captured.entries)
        _need(tuple(sorted(entry.entry_id for entry in self.hass.config_entries.async_entries('homekit'))) == ids,
              'bridge_population_changed')
        for before, after, identity in zip(captured.entries, captured.expected,
                                            captured.bridge_identity, strict=True):
            if not snapshot_equal(before, after):
                continue
            entry_id = before['identity']['entry_id']
            entry = self.hass.config_entries.async_get_entry(entry_id)
            _need(entry is not None and entry.state.value == 'loaded'
                  and snapshot_equal(read_entry(self.hass, entry_id), before)
                  and _bridge_identity(entry) == identity, 'foreign_bridge_changed')
            native = entry.runtime_data.homekit._filter.get_filter()
            expected = FILTER_SCHEMA(before['options'].get('filter', {})).get_filter()
            _need(all(native(entity_id) == expected(entity_id)
                      for pair in self.mapping.items() for entity_id in pair), 'foreign_filter_changed')
        return True

    def verify(self, position):
        """Recheck every loaded bridge, including unaffected bridge identities."""
        from homeassistant.components.homekit import FILTER_SCHEMA
        from .helper_native import read_entry
        from .native_homekit_filter import _bridge_identity
        _need(position in ('before', 'after'), 'position')
        captured = self.phase.capture
        expected = captured.entries if position == 'before' else captured.expected
        ids = tuple(row['identity']['entry_id'] for row in expected)
        _need(tuple(sorted(entry.entry_id for entry in self.hass.config_entries.async_entries('homekit'))) == ids,
              'bridge_population_changed')
        for snapshot, identity in zip(expected, captured.bridge_identity, strict=True):
            entry_id = snapshot['identity']['entry_id']
            entry = self.hass.config_entries.async_get_entry(entry_id)
            _need(entry is not None and entry.state.value == 'loaded'
                  and snapshot_equal(read_entry(self.hass, entry_id), snapshot)
                  and _bridge_identity(entry) == identity, 'bridge_changed')
            native = entry.runtime_data.homekit._filter.get_filter()
            expected_filter = FILTER_SCHEMA(snapshot['options'].get('filter', {})).get_filter()
            _need(all(native(entity_id) == expected_filter(entity_id)
                      for pair in self.mapping.items() for entity_id in pair), 'runtime_filter_changed')
        return True
