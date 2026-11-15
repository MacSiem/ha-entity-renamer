"""Derive the group setup's native suggested ID inside the original plan."""
from dataclasses import replace
import json

from .native_registry import PreparedRegistryChange
from .native_registry_transition import _powercalc_group_suggested_move
from .powercalc_previous_state_original import PowercalcGroupOriginal
from .transaction import Refused


def prepare_group_registry(change, group, version, *, controlled=False):
    """Prepare only the exact current-Core, original-group derived transition."""
    if (type(change) is not PreparedRegistryChange or type(group) is not PowercalcGroupOriginal
            or (version not in ('2026.10.2', '2026.10.4')
                and not (controlled is True and version in ('2026.11.0', '2026.11.1'))) or change.kind != 'entity'
            or change.identity != group.registry_id or change.before.get('entity_id') != group.entity_id
            or not change.mapping):
        raise Refused('powercalc_group_registry_preparation_scope')
    return prepare_derived_registry(change)


def prepare_derived_registry(change):
    # The caller must bind this projection to the exact native population.
    before, after = change.before, change.after
    if (before.get('suggested_object_id') != before['entity_id'].removeprefix('sensor.')
            or {k: v for k, v in before.items() if k not in ('entity_id', 'name')} !=
               {k: v for k, v in after.items() if k not in ('entity_id', 'name')}):
        raise Refused('powercalc_group_registry_suggested_unqualified')
    derived = dict(after, suggested_object_id=after['entity_id'].removeprefix('sensor.'))
    if not _powercalc_group_suggested_move(before, derived):
        raise Refused('powercalc_group_registry_preparation_invalid')
    return replace(change, after_json=json.dumps(derived, sort_keys=True))
