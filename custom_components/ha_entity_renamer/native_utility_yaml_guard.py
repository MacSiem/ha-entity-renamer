"""Fail closed for selected sources of loaded YAML Utility Meter consumers.

YAML Utility meters are platform-discovered, not ConfigEntries, and Core 11.1
has no utility_meter.reload. A changed source document alone is not activation.
"""
from collections import Counter
from .transaction import Refused


class UtilityYAMLActivationUnavailable(Refused):
    def __init__(self, rows):
        self.sources = tuple(rows)
        super().__init__('utility_yaml_native_activation_unavailable')


def selected_yaml_sources(data, entry_ids, mapping):
    if type(data) is not dict or type(mapping) is not dict:
        raise Refused('utility_yaml_inventory_unavailable')
    counts = Counter()
    for meter, config in data.items():
        if type(meter) is not str or type(config) is not dict:
            raise Refused('utility_yaml_inventory_unavailable')
        if meter in entry_ids:
            continue
        source = config.get('source')
        if type(source) is not str:
            raise Refused('utility_yaml_source_unknown')
        if source in mapping:
            counts[source] += 1
    return tuple({'entity_id': source, 'reason': 'utility_yaml_native_activation_unavailable',
                  'consumer_count': counts[source]} for source in sorted(counts))


def assert_utility_yaml_not_affected(hass, mapping):
    from homeassistant.components.utility_meter.const import DATA_UTILITY
    if 'utility_meter' not in hass.config.components:
        return
    data = hass.data.get(DATA_UTILITY)
    entries = hass.config_entries.async_entries('utility_meter')
    rows = selected_yaml_sources(data, {entry.entry_id for entry in entries}, mapping)
    if rows:
        raise UtilityYAMLActivationUnavailable(rows)
