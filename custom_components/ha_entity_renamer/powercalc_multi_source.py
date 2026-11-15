"""Qualify multiple selected producers against the native, bound graph."""
from .transaction import Refused


def qualify_multi_entry_sources(sources, rows, registry):
    """Require distinct fixed entries with their own verified power/energy pair.

    This is a read-only admission check. It does not authorize a write, replace
    native graph binding, or infer atomicity across the resulting plan steps.
    """
    def need(value):
        if not value:
            raise Refused('powercalc_multi_source_unqualified')

    need(type(sources) is tuple and len(sources) > 1 and len(set(sources)) == len(sources)
         and all(type(value) is str and value for value in sources)
         and type(rows) is tuple and type(registry) is list)
    registry_by_id = {row.get('entity_id'): row for row in registry}
    need(len(registry_by_id) == len(registry))
    used_entries, used_uuids = set(), set()
    for source in sources:
        source_row = registry_by_id.get(source)
        need(type(source_row) is dict and type(source_row.get('id')) is str)
        powers = [row for row in rows if row.source == source and row.source_verified
                  and row.relation_kind == 'profile_fixed' and type(row.origin) is str
                  and row.origin.startswith('entry:')]
        need(len(powers) == 1)
        power = powers[0]
        entry = power.origin.removeprefix('entry:')
        native_power = registry_by_id.get(power.entity_id)
        energies = [row for row in rows if row.source_verified and row.relation_kind == 'energy_integral'
                    and row.origin == 'profile:' + power.origin
                    and row.edges == ((power.entity_id, row.entity_id, 1),)]
        need(bool(entry) and entry not in used_entries and len(energies) == 1
             and power.source_registry_id == source_row['id']
             and power.edges == ((source, power.entity_id, 1),)
             and type(native_power) is dict and native_power.get('id') == power.registry_id
             and native_power.get('config_entry_id') == entry)
        energy = energies[0]
        native_energy = registry_by_id.get(energy.entity_id)
        need(type(native_energy) is dict and native_energy.get('id') == energy.registry_id
             and native_energy.get('config_entry_id') == entry
             and all(type(value) is str and value for value in (power.registry_id, energy.registry_id)))
        ids = {source_row['id'], power.registry_id, energy.registry_id}
        need(len(ids) == 3 and not ids & used_uuids)
        used_entries.add(entry)
        used_uuids.update(ids)
