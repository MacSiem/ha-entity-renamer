"""Fence the pinned vendor's unowned source-rename subscriptions during reload."""
from importlib import import_module

from .native_unload_code_proof import prove_methods, unchanged
from .native_utility_setup_audit import _SOURCES, _code_objects
from .powercalc_reload_witness import _Reload
from .powercalc_source_binding import _pins
from .transaction import Refused


class ControlledEntryListeners:
    # Reuse the actual job/filter and closure census of the earlier reload.
    rename_listeners = _Reload.rename_listeners

    def __init__(self, hass, vendor, entries):
        from homeassistant.core import EventBus
        self.hass, self.entries = hass, entries
        self.vendor_refs = ((import_module('custom_components.powercalc.sensor'),
                             '_register_entity_id_change_listener'),)
        self.core_refs = ((EventBus, '_async_remove_listener'),)
        self.vendor = vendor
        self.pairs = None

    def prove(self):
        vendor = self.vendor
        pin = '2026.11.0' if vendor.core_version == '2026.11.1' else vendor.core_version
        return (prove_methods(vendor.root, _pins(vendor.vendor_version), self.vendor_refs),
                prove_methods(vendor.ha_root, _SOURCES[pin], self.core_refs))

    def _check(self):
        unchanged(self.vendor_refs, *self.vendor_proof)
        unchanged(self.core_refs, *self.core_proof)
        if (tuple(self.hass.config_entries.async_entries('powercalc')) != self.entries
                or self.hass.bus._dispatching or self.hass.bus._event_queue):
            raise Refused('controlled_powercalc_entry_listener_scope')

    def _current(self):
        from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
        rows, refs = self.rename_listeners()
        pairs = {}
        for key, (job, _, event_filter, entry) in refs.items():
            matches = [pair for pair in self.hass.bus._listeners.get(EVENT_ENTITY_REGISTRY_UPDATED, ())
                       if pair[0] is job and pair[1] is event_filter]
            if len(matches) != 1:
                raise Refused('controlled_powercalc_entry_listener_pair')
            pairs[key] = matches[0]
        return rows, pairs

    def _verify_sources(self, rows):
        expected = {(entry.entry_id, entry.data['entity_id']) for entry in self.entries
                    if entry.disabled_by is None and type(entry.data.get('entity_id')) is str}
        observed = {(row['entry_id'], row['source_entity_id']) for row in rows.values()}
        if observed != expected or len(rows) != len(expected):
            raise Refused('controlled_powercalc_entry_listener_source')

    def capture(self, proofs):
        self.vendor_proof, self.core_proof = proofs
        codes = tuple(_code_objects(self.vendor_proof[1][0]))
        def nested(name):
            matches = [code for code in codes if code.co_name == name]
            if len(matches) != 1:
                raise Refused('controlled_powercalc_entry_listener_code')
            return matches[0]
        self.rename_target = nested('_entity_rename_listener')
        self.rename_filter = nested('_filter_entity_id')
        self._check()
        rows, self.pairs = self._current()
        self._verify_sources(rows)

    def detach(self):
        from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
        self._check()
        _, current = self._current()
        if current.keys() != self.pairs.keys() or any(current[k] is not pair for k, pair in self.pairs.items()):
            raise Refused('controlled_powercalc_entry_listener_changed')
        for pair in self.pairs.values():
            self.hass.bus._async_remove_listener(EVENT_ENTITY_REGISTRY_UPDATED, pair)
        self.require_inactive()

    def require_inactive(self):
        self._check()
        if self._current()[0]:
            raise Refused('controlled_powercalc_entry_listener_active')

    def verify_activated(self):
        self._check()
        rows, pairs = self._current()
        self._verify_sources(rows)
        if any(pair is before for pair in pairs.values() for before in self.pairs.values()):
            raise Refused('controlled_powercalc_entry_listener_retained')
