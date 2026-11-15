"""Prepare a native Script-key registry image; never perform a mutation.

The current Root still refuses this image at its effect qualifier. The future
effect must independently bind a native service/lifecycle and caller census,
authenticate both service events and use the existing journal/receipt chain.
"""
from __future__ import annotations

from dataclasses import replace
import json

from .native_registry import PreparedRegistryChange, _content
from .native_registry_transition import _native_script_unique_move
from .native_own_id_inventory import NativeOwnIDInventory
from .transaction import Refused


def own_script_registry_preparation(change, inventory):
    """Derive exact forward native fields from ORIGINAL and actual owners.

    This creates no writer capability. In particular, the inverse native call
    records the then-current unique ID as previous_unique_id; the original
    preparation is retained rather than overwritten by that effect.
    """
    if type(change) is not PreparedRegistryChange or type(inventory) is not NativeOwnIDInventory:
        raise Refused('own_script_registry_preparation_scope') from None
    inventory.check()
    before, after = change.before, change.after
    entity, old, new = inventory.selected_entity, inventory.old, inventory.new
    row = entity.registry_entry
    if (change.kind != 'entity' or change.identity != row.id
            or before.get('platform') != 'script' or before.get('entity_id') != old
            or after.get('entity_id') != new or before.get('unique_id') != old.removeprefix('script.')
            or new == old or not new.startswith('script.')
            or 'previous_unique_id' not in before
            or {k: v for k, v in before.items() if k != 'entity_id'}
                != {k: v for k, v in after.items() if k != 'entity_id'}):
        raise Refused('own_script_registry_preparation_changed') from None
    from homeassistant.helpers import entity_registry as er
    from .native_registry import NativeRegistryIO, _row
    io = NativeRegistryIO(inventory.hass)
    registry = er.async_get(inventory.hass)
    target_unique = new.removeprefix('script.')
    if (registry is not io.registries['entity'] or registry.async_get(old) is not row
            or registry.async_get(new) is not None
            or registry.async_get_entity_id('script', 'script', target_unique) is not None
            or _content(_row(io.document('entity'), 'entity', change.identity)) != _content(before)):
        raise Refused('own_script_registry_preparation_collision') from None
    derived = dict(after, unique_id=target_unique, previous_unique_id=before['unique_id'])
    from homeassistant.const import __version__
    if __version__ in ('2026.10.2', '2026.10.4'):
        # Core will derive this exact value from the Script key at next setup.
        # Include it in this same original, receipted native mutation.
        if before.get('suggested_object_id') != before['unique_id']:
            raise Refused('own_script_suggested_object_id_unqualified') from None
        derived['suggested_object_id'] = target_unique
    if inventory.default_name:
        if before.get('original_name') != before['unique_id']:
            raise Refused('own_script_default_name_registry_unqualified') from None
        derived['original_name'] = target_unique
        # Keep the visible friendly name while Core's default source name
        # tracks the new YAML key, including across a full later startup.
        derived['name'] = before.get('name') or before['unique_id']
    if not _native_script_unique_move(before, derived):
        raise Refused('own_script_registry_preparation_invalid') from None
    inventory.check()
    return replace(change, after_json=json.dumps(derived, sort_keys=True))
