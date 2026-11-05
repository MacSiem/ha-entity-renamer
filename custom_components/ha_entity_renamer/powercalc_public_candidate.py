"""Derive the private checker image for one source and one Powercalc group.

This is an input to the existing SAME-ORIGINAL validator, not a receipt or
authority to write. Every selected row is rebound to native preparations after
validation and the full before image must match the captured baseline.
"""
from __future__ import annotations

import json

from .energy_native import PreparedEnergy
from .native_input_baseline import _KEYS, _envelope
from .powercalc_candidate_sources import PlannedPowercalcDocuments
from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('powercalc_public_candidate_' + reason)


def build_candidate(original, mapping, version, energy=None):
    """Build full proposed documents from exact immutable bytes only."""
    _need(type(original) is tuple and len(original) == len(_KEYS)
          and tuple(row[0] for row in original) == _KEYS
          and all(type(row) is tuple and len(row) == 2 and type(row[1]) is bytes for row in original),
          'full_three_required')
    _need(type(mapping) is dict and len(mapping) == 2
          and all(type(a) is str and type(b) is str and a != b for a, b in mapping.items())
          and len(set(mapping.values())) == 2 and not set(mapping) & set(mapping.values()),
          'source_and_group_mapping_required')
    documents = {key: _envelope(raw, key, version) for key, raw in original}
    rows = documents['core.entity_registry']['data']['entities']
    selected = [row for row in rows if row['entity_id'] in mapping]
    _need(len(selected) == 2 and len({row['entity_id'] for row in selected}) == 2
          and not any(row['entity_id'] in mapping.values() for row in rows), 'registry_scope')
    groups = [row for row in selected if row['platform'] == 'powercalc']
    sources = [row for row in selected if row['platform'] != 'powercalc']
    _need(len(groups) == len(sources) == 1
          and sources[0]['platform'] not in ('template', 'automation', 'script'),
          'one_owned_group_required')
    group, source = groups[0], sources[0]
    old_group, new_group = group['entity_id'], mapping[group['entity_id']]
    _need(old_group.startswith('sensor.') and new_group.startswith('sensor.')
          and group.get('suggested_object_id') == old_group.removeprefix('sensor.'),
          'group_suggested_identity')
    entries = documents['core.config_entries']['data']['entries']
    matches = [row for row in entries if row['entry_id'] == group.get('config_entry_id')]
    _need(len(matches) == 1 and matches[0]['domain'] == 'powercalc'
          and type(matches[0]['data']) is dict and matches[0]['data'].get('_energy_entity') == old_group,
          'group_entry_metadata')
    source['entity_id'] = mapping[source['entity_id']]
    group['entity_id'] = new_group
    group['suggested_object_id'] = new_group.removeprefix('sensor.')
    matches[0]['data']['_energy_entity'] = new_group
    # The pinned native registry removes and reinserts in execution order.
    for row in (source, group):
        rows.append(rows.pop(rows.index(row)))
    planned_energy = None
    if energy is not None:
        _need(type(energy) is tuple and len(energy) == 2 and type(energy[0]) is bytes
              and type(energy[1]) is PreparedEnergy, 'energy_preparation')
        before, change = energy
        payload = json.loads(before)
        _need(type(payload) is dict and payload.get('key') == 'energy'
              and payload.get('data') == change.before.value, 'energy_original_changed')
        payload['data'] = change.after
        planned_energy = (before, json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                             allow_nan=False).encode())
    return PlannedPowercalcDocuments(tuple((key, raw, json.dumps(documents[key], sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()) for key, raw in original), planned_energy)
