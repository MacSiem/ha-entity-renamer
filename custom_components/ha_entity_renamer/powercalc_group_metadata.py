"""Typed Powercalc group metadata move bound to one ORIGINAL group UUID.

The vendor writes this field during setup. Moving it while the witnessed group
is inactive lets the existing ConfigEntry receipt account for the exact native
write before setup instead of accepting unproven disk drift afterward.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re

from .helper_plan import HelperPlan
from .lovelace_native import revision
from .prepared import canonical
from .reference_planner import Change
from .transaction import Refused

_ENTITY = re.compile(r'sensor\.[a-z0-9_]+\Z', re.ASCII)


@dataclass(frozen=True)
class PreparedPowercalcGroupMetadata:
    before: dict
    after: dict
    mapping: dict[str, str]

    @property
    def identity(self):
        return revision({'domain': self.before['domain'], 'identity': self.before['identity']})

    @property
    def before_revision(self):
        return revision(self.before)

    @property
    def after_revision(self):
        return revision(self.after)

    def plan(self):
        before, _ = canonical(self.before)
        after, _ = canonical(self.after)
        mapping, _ = canonical(self.mapping)
        if (type(before) is not dict or set(before) != {'domain', 'identity', 'data', 'options', 'subentries'}
                or before.get('domain') != 'powercalc' or type(before.get('data')) is not dict
                or type(mapping) is not dict or len(mapping) != 1):
            raise Refused('powercalc_group_metadata_shape')
        old, new = next(iter(mapping.items()))
        if (type(old) is not str or type(new) is not str or not _ENTITY.fullmatch(old)
                or not _ENTITY.fullmatch(new) or old == new
                or before['data'].get('_energy_entity') != old):
            raise Refused('powercalc_group_metadata_mapping')
        expected = deepcopy(before)
        expected['data']['_energy_entity'] = new
        if canonical(expected)[1] != canonical(after)[1]:
            raise Refused('powercalc_group_metadata_foreign_change')
        return HelperPlan(before, after,
            (Change(('data', '_energy_entity'), 'value', old, new),), (), ())


def prepare_group_metadata(before, old, new):
    original, _ = canonical(before)
    target = deepcopy(original)
    if type(target.get('data')) is not dict:
        raise Refused('powercalc_group_metadata_shape')
    target['data']['_energy_entity'] = new
    result = PreparedPowercalcGroupMetadata(original, target, {old: new})
    result.plan()
    return result


def assert_original_group(chain, change):
    """Rebind the metadata to ORIGINAL group entry and its queued UUID move."""
    from .native_input_baseline import _envelope
    from .native_registry import PreparedRegistryChange
    from .powercalc_composition import PowercalcComposition
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    if type(change) is not PreparedPowercalcGroupMetadata:
        raise Refused('powercalc_group_metadata_type')
    change.plan()
    baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
    if baseline != chain.baseline or type(composition) not in (PowercalcComposition, ControlledPowercalcComposition):
        raise Refused('powercalc_group_metadata_original')
    old, new = next(iter(change.mapping.items()))
    groups = [group for group in composition.previous.groups if group.entity_id == old]
    registry = _envelope(next(row.raw for row in baseline.files if row.key == 'core.entity_registry'),
                         'core.entity_registry', baseline.receipt.version)['data']['entities']
    matches = [row for row in registry if len(groups) == 1 and row['id'] == groups[0].registry_id]
    entry_id = change.before['identity']['entry_id']
    if (len(matches) != 1 or matches[0].get('platform') != 'powercalc'
            or matches[0].get('config_entry_id') != entry_id):
        raise Refused('powercalc_group_metadata_entry')
    moves = [item for item in chain.changes if type(item) is PreparedRegistryChange
             and item.identity == groups[0].registry_id and item.mapping == {old: new}]
    group_positions = [index for index, item in enumerate(chain.changes)
                       if type(item) is PreparedRegistryChange and item in moves]
    metadata_positions = [index for index, item in enumerate(chain.changes)
                          if type(item) is PreparedPowercalcGroupMetadata and item == change]
    if (len(moves) != 1 or len(group_positions) != 1 or len(metadata_positions) != 1
            or group_positions[0] >= metadata_positions[0]):
        raise Refused('powercalc_group_metadata_registry_move')
