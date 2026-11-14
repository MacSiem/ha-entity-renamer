"""Typed Powercalc ConfigEntry rewrite bound to ORIGINAL producer UUIDs.

The vendor writes this field during setup. Moving it while the witnessed group
is inactive lets the existing ConfigEntry receipt account for the exact native
write before setup instead of accepting unproven disk drift afterward.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re

from .helper_plan import HelperPlan, plan_helper
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
        paths = entry_paths(before, mapping)
        expected = deepcopy(before)
        changes = []
        for key, old, new in paths:
            expected['data'][key] = new
            changes.append(Change(('data', key), 'value', old, new))
        if canonical(expected)[1] != canonical(after)[1]:
            raise Refused('powercalc_group_metadata_foreign_change')
        return HelperPlan(before, after, tuple(changes), (), ())


def entry_paths(before, mapping):
    """The only references in a qualified entry that the inactive write owns."""
    if (type(before) is not dict or set(before) != {'domain', 'identity', 'data', 'options', 'subentries'}
            or before.get('domain') != 'powercalc' or type(before.get('data')) is not dict
            or before['options'] or before['subentries']
            or type(mapping) is not dict or not mapping):
        raise Refused('powercalc_group_metadata_shape')
    data = before['data']
    if data.get('sensor_type') == 'virtual_power' and data.get('mode') in ('fixed', 'linear'):
        fields = ('entity_id', '_power_entity', '_energy_entity')
    else:
        fields = ('_energy_entity',)
    paths = [(key, data[key], mapping[data[key]]) for key in fields
             if type(data.get(key)) is str and data[key] in mapping]
    if (set(mapping) != {old for _, old, _ in paths} or len(paths) != len(mapping)
            or any(type(new) is not str or old == new or old.split('.')[0] != new.split('.')[0]
                   or (key != 'entity_id' and (not _ENTITY.fullmatch(old) or not _ENTITY.fullmatch(new)))
                   for key, old, new in paths)):
        raise Refused('powercalc_group_metadata_mapping')
    return paths


def controlled_entry_change(before, mapping):
    data = before['data']
    fields = (('entity_id', '_power_entity', '_energy_entity')
              if data.get('sensor_type') == 'virtual_power' and data.get('mode') in ('fixed', 'linear')
              else ('_energy_entity',) if data.get('sensor_type') == 'group' else ())
    selected = {data[key]: mapping[data[key]] for key in fields
                if type(data.get(key)) is str and data[key] in mapping}
    if not selected:
        return None
    original, _ = canonical(before)
    target = deepcopy(original)
    paths = entry_paths(original, selected)
    for key, _, new in paths:
        target['data'][key] = new
    generic = plan_helper(original, mapping)
    expected_changes = {('data', key) for key, _, _ in paths if key == 'entity_id'}
    expected_markers = {(('data', key), 'unsupported_helper_reference')
                        for key, _, _ in paths if key != 'entity_id'}
    if data.get('sensor_type') == 'group':
        if data.get('force_calculate_group_energy') is not False:
            raise Refused('powercalc_group_metadata_group_scope')
        expected_markers.add((('data', 'force_calculate_group_energy'),
                              'powercalc_group_membership_unverified'))
    if ({change.path for change in generic.changes} != expected_changes
            or {(marker.path, marker.code) for marker in generic.markers} != expected_markers):
        raise Refused('powercalc_group_metadata_coverage')
    return PreparedPowercalcGroupMetadata(original, target, selected)


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
    registry = _envelope(next(row.raw for row in baseline.files if row.key == 'core.entity_registry'),
                         'core.entity_registry', baseline.receipt.version)['data']['entities']
    entries = _envelope(next(row.raw for row in baseline.files if row.key == 'core.config_entries'),
                        'core.config_entries', baseline.receipt.version)['data']['entries']
    entry_id = change.before['identity']['entry_id']
    from .native_config_transition import _projection
    original = [row for row in entries if row['entry_id'] == entry_id]
    if len(original) != 1 or canonical(_projection(original[0]))[1] != canonical(change.before)[1]:
        raise Refused('powercalc_group_metadata_original')
    paths = entry_paths(change.before, change.mapping)
    graph = __import__('json').loads(composition.evidence.powercalc_sources.closed_graph_json)
    profile = next((row for row in graph['profiles'] if row['origin'] == 'entry:' + entry_id), None)
    for key, old, new in paths:
        matched = [row for row in registry if row['entity_id'] == old]
        if len(matched) != 1:
            raise Refused('powercalc_group_metadata_entry')
        row = matched[0]
        if profile is None:
            valid = key == '_energy_entity' and row.get('platform') == 'powercalc' and (
                row.get('config_entry_id') == entry_id and any(group.entity_id == old
                and group.registry_id == row['id'] for group in composition.previous.groups))
        elif key == 'entity_id':
            valid = profile['source'] == old and profile.get('source_registry', [None])[0] == row['id']
        else:
            valid = (row.get('platform') == 'powercalc' and row.get('config_entry_id') == entry_id
                     and row.get('unique_id') == profile['unique_id'] + ('_energy' if key == '_energy_entity' else ''))
        positions = [index for index, item in enumerate(chain.changes)
                     if type(item) is PreparedRegistryChange and item.identity == row['id']
                     and item.mapping == {old: new}]
        metadata = [index for index, item in enumerate(chain.changes) if item == change]
        if not valid or len(positions) != 1 or len(metadata) != 1 or positions[0] >= metadata[0]:
            raise Refused('powercalc_group_metadata_registry_move')
