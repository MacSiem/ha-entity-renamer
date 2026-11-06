"""Read-only ownership checks for Core 11's two native RestoreState maps.

No native lookup is called here: async_get_stored_state can mutate a State.
The caller owns all physical preimages, effects and receipts.
"""
from __future__ import annotations


def owned_slot(data, source, target, registry_id, *, required):
    from homeassistant.const import __version__
    from homeassistant.core import State
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.restore_state import DATA_RESTORE_STATE, RestoreStateData, StoredState
    from .transaction import Refused

    def need(value):
        if not value:
            raise Refused('restore_uuid_ownership_unverified')

    need(__version__ == '2026.11.0' and type(data) is RestoreStateData
         and data.hass.data.get(DATA_RESTORE_STATE) is data
         and type(source) is str and type(target) is str
         and type(registry_id) is str and bool(registry_id)
         and type(required) is bool)
    registry = er.async_get(data.hass)
    row = registry.async_get(source)
    need(row is not None and row.id == registry_id
         and (source == target or registry.async_get(target) is None))
    by_id, by_uuid = data.last_states_by_entity_id, data.last_states_by_entity_registry_id
    need(type(by_id) is dict and type(by_uuid) is dict
         and source not in by_id and target not in by_id)
    for key, item in by_uuid.items():
        need(type(key) is str and type(item) is StoredState and type(item.state) is State
             and item.entity_registry_id == key)
        if key != registry_id:
            need(item.state.entity_id not in (source, target))
    item = by_uuid.get(registry_id)
    need((not required and item is None) or
         (type(item) is StoredState and item.entity_registry_id == registry_id
          and item.state.entity_id in (source, target)))
    return item
