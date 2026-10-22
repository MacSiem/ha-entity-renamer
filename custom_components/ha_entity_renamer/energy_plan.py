"""Reference-only Energy preference plan; preserve consumers, prices and labels."""
from __future__ import annotations

from dataclasses import replace

from .reference_planner import Consumer, Context, Marker, Scope, Syntax, plan_references

REFERENCE_FIELDS = frozenset({
    'stat_energy_from', 'stat_energy_to', 'stat_cost', 'stat_compensation',
    'entity_energy_price', 'entity_energy_price_export', 'stat_rate',
    'stat_rate_inverted', 'stat_rate_from', 'stat_rate_to', 'stat_soc',
    'stat_consumption', 'included_in_stat',
})
CONTAINERS = frozenset({'energy_sources', 'device_consumption', 'device_consumption_water',
                        'flow_from', 'flow_to', 'power', 'power_config'})
PASSIVE_FIELDS = frozenset({'type', 'name', 'number_energy_price', 'number_energy_price_export',
                            'cost_adjustment_day', 'capacity', 'config_entry_solar_forecast'})


def plan_energy(preferences, mapping):
    """Caller must also validate the resulting schema with the installed HA.

    New reference-bearing HA fields fail coverage instead of being silently
    omitted. This does not invent sources, alter prices, or merge statistics.
    """
    scopes, markers = [], []

    def visit(value, path=()):
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + (index,))
        elif isinstance(value, dict):
            for key, child in value.items():
                current = path + (key,)
                if key in REFERENCE_FIELDS:
                    if child is not None and not isinstance(child, str):
                        raise ValueError('invalid_energy_reference')
                    scopes.append(Scope(current, Syntax.ENTITY_TREE))
                elif key in CONTAINERS:
                    if not isinstance(child, (dict, list)):
                        raise ValueError('invalid_energy_container')
                    visit(child, current)
                elif key not in PASSIVE_FIELDS:
                    markers.append(Marker(current, 'unknown_energy_field', 'Energy schema requires an adapter update'))
        else:
            raise ValueError('invalid_energy_preferences')

    if type(preferences) is not dict:
        raise ValueError('invalid_energy_preferences')
    visit(preferences)
    result = plan_references(preferences, mapping, Context(Consumer.ENERGY, tuple(scopes)))
    # A known, empty Energy configuration has no references to move.
    retained = tuple(marker for marker in result.markers if marker.code != 'no_supported_scopes')
    return replace(result, markers=retained + tuple(markers), coverage='energy_preferences_schema')
