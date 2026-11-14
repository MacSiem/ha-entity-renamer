"""Bound live reference checks for the two HA Tools storage-backed cards."""
from collections.abc import Mapping


def _contains_reference(value, ids, depth=0):
    if depth > 80:
        raise ValueError('ha_tools_storage_depth_unqualified')
    if isinstance(value, Mapping):
        return any(_contains_reference(k, ids, depth + 1) or
                   _contains_reference(v, ids, depth + 1) for k, v in value.items())
    if isinstance(value, (tuple, list)):
        return any(_contains_reference(v, ids, depth + 1) for v in value)
    if isinstance(value, str):
        return value in ids or (value.startswith('entity:') and value[7:] in ids)
    if value is None or type(value) in (bool, int, float):
        return False
    raise ValueError('ha_tools_storage_shape_unqualified')


def _types(value, found):
    if isinstance(value, Mapping):
        if isinstance(value.get('type'), str): found.add(value['type'])
        for child in value.values(): _types(child, found)
    elif isinstance(value, (tuple, list)):
        for child in value: _types(child, found)


class HAToolsFence:
    """Pin actual stores, checking only selected reference membership."""
    def __init__(self, hass, mapping, configs):
        found = set()
        for config in configs: _types(config, found)
        domains = []
        if 'custom:ha-frigate-privacy' in found: domains.append('ha_frigate_privacy')
        if 'custom:ha-vacuum-water-monitor' in found: domains.append('ha_vacuum_water_monitor')
        self.hass, self.mapping = hass, dict(mapping)
        self.ids = frozenset((*mapping, *mapping.values()))
        self.rows = []
        for domain in domains:
            bucket = hass.data.get(domain)
            if type(bucket) is not dict or (store := bucket.get('storage')) is None:
                raise ValueError('ha_tools_storage_unavailable')
            entries = tuple(hass.config_entries.async_entries(domain))
            if len(entries) != 1:
                raise ValueError('ha_tools_entry_unavailable')
            self.rows.append((domain, store, entries))
        self.check()

    def check(self):
        for domain, store, entries in self.rows:
            bucket = self.hass.data.get(domain)
            if (type(bucket) is not dict or bucket.get('storage') is not store or
                    tuple(self.hass.config_entries.async_entries(domain)) != entries or
                    type(getattr(store, '_data', None)) is not dict or
                    any(getattr(getattr(entry, 'state', None), 'value', None) != 'loaded'
                        for entry in entries)):
                raise ValueError('ha_tools_storage_changed')
            if (_contains_reference(store._data, self.ids) or any(
                    _contains_reference(entry.data, self.ids) or
                    _contains_reference(entry.options, self.ids) for entry in entries)):
                raise ValueError('ha_tools_storage_reference_unsupported')
            if domain == 'ha_frigate_privacy':
                from homeassistant.helpers import entity_registry as er
                registry = er.async_get(self.hass)
                suffixes = ('_enabled', '_detect', '_recordings', '_snapshots', '_motion',
                            '_audio', '_audio_detection', '_review_alerts', '_review_detections')
                for old, new in self.mapping.items():
                    domain_name = old.split('.')[0]
                    if domain_name not in {'camera', 'switch'}: continue
                    if new.split('.')[0] != domain_name:
                        raise ValueError('ha_tools_frigate_selector_unqualified')
                    row = registry.async_get(old) or registry.async_get(new)
                    if row is None:
                        raise ValueError('ha_tools_frigate_selector_unqualified')
                    if row.platform != 'frigate': continue
                    if domain_name == 'switch' and (old.endswith(suffixes) or new.endswith(suffixes)):
                        raise ValueError('ha_tools_frigate_selector_unqualified')
                    if domain_name == 'camera':
                        for camera in (old, new):
                            for suffix in ('_detect', '_recordings'):
                                switch = 'switch.' + camera.split('.', 1)[1] + suffix
                                candidate = registry.async_get(switch)
                                if (candidate is not None and candidate.platform == 'frigate'
                                        and self.hass.states.get(switch) is not None):
                                    raise ValueError('ha_tools_frigate_selector_unqualified')
