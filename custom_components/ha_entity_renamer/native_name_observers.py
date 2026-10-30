"""Read-only native name observation, HA 2025.6.0 and 2026.10.2/2026.10.4 only.

Capture under the coordinator lock before mutation. The serialized baseline is
part of the immutable server preparation, allowing read-only crash recovery.
No synthetic states, forced state writes, or cached friendly-name calculation.
"""
from __future__ import annotations

import json

from .helper_observers import require, registry_identity
from .helper_native import read_entry


class NativeNameObserver:
    def __init__(self, io):
        self.io, self.hass = io, io.hass

    def _version(self):
        from homeassistant.const import __version__
        require(__version__ in ('2025.6.0', '2026.10.2', '2026.10.4'), 'name_ha_version_unsupported')
        return __version__

    def _rows(self, change):
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(self.hass)
        if change.kind == 'device':
            return er.async_entries_for_device(registry, change.identity,
                                               include_disabled_entities=True)
        # A combined ID/name update retains this immutable native UUID. The
        # original ID can legitimately be absent after the single native call.
        rows = [row for row in registry.entities.values() if row.id == change.identity]
        require(len(rows) == 1 and rows[0].entity_id in
                (change.before['entity_id'], change.after['entity_id']), 'name_entity_missing')
        return rows

    def _object(self, row):
        from homeassistant.helpers import entity_platform as ep
        candidates = [entity for platform in ep.async_get_platforms(self.hass, row.platform)
                      for entity in platform.entities.values()
                      if entity.entity_id == row.entity_id
                      or (entity.registry_entry is not None
                          and entity.registry_entry.id == row.id)]
        require(len(candidates) <= 1, 'name_platform_binding_ambiguous')
        return candidates[0] if candidates else None

    def _entry(self, row):
        if row.config_entry_id is None:
            return None
        entry = self.hass.config_entries.async_get_entry(row.config_entry_id)
        require(entry is not None and entry.state.value in ('loaded', 'not_loaded'),
                'name_entry_missing_or_unsettled')
        from .native_registry import revision
        return {'snapshot_revision': revision(read_entry(self.hass, row.config_entry_id)),
                'state': entry.state.value}

    def _sample(self, row):
        entity, state, entry = self._object(row), self.hass.states.get(row.entity_id), self._entry(row)
        active = entity is not None
        if not active:
            require(state is None or (entry is not None and entry['state'] == 'not_loaded'
                    and row.disabled_by in (None, 'config_entry')
                    and state.state == 'unavailable' and state.attributes.get('restored') is True),
                    'name_inactive_state_present')
            require(row.disabled_by is not None or (entry is not None and
                    entry['state'] == 'not_loaded'), 'name_unexplained_runtime_absence')
        else:
            require(row.disabled_by is None and (entry is None or entry['state'] == 'loaded'),
                    'name_active_lifecycle_mismatch')
            require(state is not None and state.state not in ('unknown', 'unavailable'),
                    'name_live_state_unavailable')
            require(entity.hass is self.hass and entity.entity_id == row.entity_id
                    and entity.registry_entry is row and entity.unique_id == row.unique_id,
                    'name_native_object_registry_mismatch')
        return entity, state, entry, active

    def capture(self, change):
        version = self._version()
        rows = self._rows(change)
        # A device with no associated entities proves no runtime effect, not an
        # independently activated device. Fail closed instead of vacuous True.
        require(bool(rows), 'name_no_observable_entities')
        document = self.io.document('entity')
        baseline = {}
        devices = self.io.document('device')
        device_ids = {row.device_id for row in rows if row.device_id is not None}
        for row in rows:
            _, state, entry, active = self._sample(row)
            native = next(item for item in document['entities'] if item['id'] == row.id)
            baseline[row.id] = {'row': native, 'identity': list(registry_identity(row)),
                                'entry': entry, 'active': active,
                                'inactive_state': (state.state if state is not None else None) if not active else None,
                                'inactive_attributes': ({k: v for k, v in state.attributes.items()
                                    if k != 'friendly_name'} if state is not None else None) if not active else None}
        payload = {'ha': version, 'entities': baseline,
                   'devices': {row['id']: row for row in devices['devices']
                               if row['id'] in device_ids}}
        # Canonical JSON also detaches nested native preference mappings.
        return json.dumps(payload, sort_keys=True)

    def verify(self, change, target, *, expected_documents=None):
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        from homeassistant.helpers.typing import UNDEFINED
        from .native_registry import _content

        version = self._version()
        require(bool(change.runtime_before_json), 'name_runtime_baseline_missing')
        baseline = json.loads(change.runtime_before_json)
        require(baseline['ha'] == version, 'name_runtime_version_drift')
        # Trusted caller may supply ORIGINAL baseline + durable accepted own
        # registry deltas. Only related registry rows use those expectations;
        # runtime lifecycle/preferences/state remain pinned to original capture.
        # A fresh live document or result overlay is never a valid provider.
        expected_entities = expected_devices = None
        if expected_documents is not None:
            expected_entities = {row['id']: row for row in
                expected_documents['core.entity_registry']['data']['entities']}
            expected_devices = {row['id']: row for row in
                expected_documents['core.device_registry']['data']['devices']}
        rows = self._rows(change)
        require(bool(rows) and {row.id for row in rows} == set(baseline['entities']),
                'name_entity_membership_changed')
        native_rows = {row['id']: row for row in self.io.document('entity')['entities']}
        device_rows = {row['id']: row for row in self.io.document('device')['devices']}
        for identity, original in baseline['devices'].items():
            expected = (target if change.kind == 'device' and identity == change.identity
                        else expected_devices.get(identity) if expected_devices is not None else original)
            require(identity in device_rows and _content(device_rows[identity]) == _content(expected),
                    'name_related_device_drift')
        for row in rows:
            saved = baseline['entities'][row.id]
            expected = (target if change.kind == 'entity' else
                        expected_entities.get(row.id) if expected_entities is not None else saved['row'])
            require(_content(native_rows[row.id]) == _content(expected)
                    and list(registry_identity(row)) == saved['identity'],
                    'name_entity_identity_or_preferences_changed')
            entity, state, entry, active = self._sample(row)
            require(entry == saved['entry'] and active is saved['active'], 'name_lifecycle_or_preferences_changed')
            if not active:
                require((state.state if state is not None else None) == saved['inactive_state']
                        and ({k: v for k, v in state.attributes.items() if k != 'friendly_name'}
                             if state is not None else None) == saved['inactive_attributes'],
                        'name_inactive_state_or_attributes_changed')
                if state is not None:
                    friendly = (er.async_get_legacy_friendly_name(self.hass, row)
                                if version in ('2026.10.2', '2026.10.4') else row.name or row.original_name)
                    require(state.attributes.get('friendly_name') == (friendly if friendly else None),
                            'name_inactive_friendly_name_stale')
                continue
            if row.device_id and entity.has_entity_name:
                require(entity.device_entry is dr.async_get(self.hass).async_get(row.device_id),
                        'name_native_device_binding_stale')
            original = entity.name
            if original is UNDEFINED:
                original = None
            if version in ('2026.10.2', '2026.10.4'):
                friendly = er.async_get_legacy_friendly_name(self.hass, row, original_name=original)
                expected_friendly = friendly if friendly else None
            else:
                friendly = original
                if entity.has_entity_name and entity.device_entry is not None:
                    device = entity.device_entry
                    device_name = device.name_by_user or device.name
                    if original is None and entity.use_device_name:
                        friendly = device_name
                    elif device_name:
                        friendly = f'{device_name} {original}'
                expected_friendly = row.name or friendly
            require(state.attributes.get('friendly_name') == expected_friendly,
                    'name_live_friendly_name_stale')
        return True
