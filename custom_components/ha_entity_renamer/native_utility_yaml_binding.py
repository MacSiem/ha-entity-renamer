"""Bind the native Utility YAML population to the original checker seals.

This is read-only qualification, not permission to detach or write. The live
handoff and coordinator must each hold their independent durable intent/fences.
"""
from __future__ import annotations

from copy import deepcopy
from .native_utility_yaml_handoff import UtilityYAMLHandoff
from .native_utility_yaml_projection import native_projection
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('utility_yaml_binding_' + reason)


def _projection(hass):
    from homeassistant.components.utility_meter.const import DATA_UTILITY, DATA_TARIFF_SENSORS
    data = hass.data.get(DATA_UTILITY)
    _need(type(data) is dict, 'inventory')
    entries = {entry.entry_id for entry in hass.config_entries.async_entries('utility_meter')}
    meters = {}
    for name, info in data.items():
        if name in entries:
            continue
        _need(type(name) is str and type(info) is dict, 'inventory')
        meters[name] = deepcopy({key:value for key, value in info.items() if key != DATA_TARIFF_SENSORS})
    return native_projection({'utility_meter': meters})


class UtilityYAMLBinding:
    @classmethod
    def capture(cls, hass, mapping, runtime_expected):
        from homeassistant.const import __version__
        from .yaml_validation_worker import _fingerprint
        _need(__version__ == '2026.11.1' and type(mapping) is dict and bool(mapping), 'scope')
        seals = [row for row in runtime_expected if row[0] == 'utility_meter']
        _need(len(seals) == 1 and len(seals[0]) == 3
              and all(type(value) is str and len(value) == 64 for value in seals[0][1:])
              and seals[0][1] != seals[0][2], 'checker_seal')
        rows = _projection(hass)
        _need(_fingerprint(rows) == seals[0][1], 'source_mismatch')
        candidate = deepcopy(rows)
        affected = set()
        for name, config in candidate:
            source = config.get('source')
            _need(type(source) is str, 'source_unavailable')
            if source in mapping:
                affected.add(source)
                config['source'] = mapping[source]
        _need(bool(affected) and _fingerprint(candidate) == seals[0][2], 'candidate_mismatch')
        handoff = UtilityYAMLHandoff.capture(hass, {old: mapping[old] for old in affected})
        _need({row.old for row in handoff.rows} == affected, 'actor_population')
        obj = cls()
        obj.hass, obj.handoff, obj.mapping = hass, handoff, dict(mapping)
        obj.original, obj.candidate, obj.seals = rows, candidate, tuple(seals[0])
        return obj

    def check_prefix(self, active):
        _need(type(active) is frozenset and active <= set(self.mapping), 'prefix')
        expected = deepcopy(self.original)
        for _, config in expected:
            source = config['source']
            if source in active:
                config['source'] = self.mapping[source]
        self.handoff.check_population()
        _need(_projection(self.hass) == expected, 'runtime_projection_changed')
        return True
