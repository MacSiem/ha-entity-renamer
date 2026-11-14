"""Retire only the native stale group alias owned by this live reload."""
from __future__ import annotations

import json

from .powercalc_phase_session import PowercalcPhaseSession
from .powercalc_runtime_observer import PowercalcRuntimeCapture
from .transaction import Refused


def retire_group_alias(hass, chain, phase, captured):
    """Detach an exact inactive predecessor, without acquiring foreign authority."""
    if (type(phase) is not PowercalcPhaseSession or phase.lease.chain is not chain
            or phase.phase != 'active_receipted' or type(captured) is not PowercalcRuntimeCapture):
        raise Refused('powercalc_retired_group_live_phase_required')
    current = phase.verify_activation()
    moves = phase.lifecycle.moves
    if len(moves) != 1:
        raise Refused('powercalc_retired_group_move_required')
    old, new = moves[0].before, moves[0].after
    old_entity, new_entity = phase.scope.old.get(old), current.get(old)
    if (old_entity is None or new_entity is not phase.scope.added.get(old)
            or old_entity is new_entity or new_entity.entity_id != new):
        raise Refused('powercalc_retired_group_replacement_changed')
    inventory = json.loads(captured.payload_json)['objects']
    found = [row for row in inventory if row['object_id'] == str(id(old_entity))]
    if not found:
        return False
    vendor = hass.data.get('powercalc')
    index = vendor.get('group_entities') if type(vendor) is dict else None
    if (len(found) != 1 or found[0]['entity_id'] != old
            or found[0]['registry_id'] is not None or found[0]['active_platforms'] != []
            or found[0]['disposition'] != 'index_only_or_external_wrapper'
            or found[0]['references'] != ['vendor/group_entities/' + old]
            or type(index) is not dict or index.get(old) is not old_entity
            or hass.states.get(old) is not None):
        raise Refused('powercalc_retired_group_foreign_or_active')
    # No await between ownership check, removal and exact foreign readback.
    others = {key: value for key, value in index.items() if key != old}
    phase.verify_activation()
    if index.get(old) is not old_entity:
        raise Refused('powercalc_retired_group_binding_changed')
    del index[old]
    if (set(index) != set(others)
            or any(index[key] is not value for key, value in others.items())):
        raise Refused('powercalc_retired_group_foreign_changed')
    phase.verify_activation()
    return True
