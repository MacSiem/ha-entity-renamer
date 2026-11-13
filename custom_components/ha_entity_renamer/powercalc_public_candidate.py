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


def build_candidate(original, mapping, version, energy=None, *, mixed=False, source_id=None, entity_names=None, device_names=None,
                    template_entry=None, owned_script=None):
    """Build full proposed documents from exact immutable bytes only."""
    _need(type(original) is tuple and len(original) == len(_KEYS)
          and tuple(row[0] for row in original) == _KEYS
          and all(type(row) is tuple and len(row) == 2 and type(row[1]) is bytes for row in original),
          'full_three_required')
    _need(type(mixed) is bool and type(mapping) is dict and (bool(mapping) if mixed else len(mapping) == 2)
          and all(type(a) is str and type(b) is str and a != b for a, b in mapping.items())
          and len(set(mapping.values())) == len(mapping) and not set(mapping) & set(mapping.values()),
          'source_and_group_mapping_required')
    _need(owned_script is None or (mixed and type(owned_script) is tuple and len(owned_script) == 2
          and type(owned_script[0]) is str and owned_script[0] in mapping
          and type(owned_script[1]) is bool), 'script_owner_unqualified')
    documents = {key: _envelope(raw, key, version) for key, raw in original}
    rows = documents['core.entity_registry']['data']['entities']
    selected = [row for row in rows if row['entity_id'] in mapping]
    _need(len(selected) == len(mapping) and len({row['entity_id'] for row in selected}) == len(mapping)
          and not any(row['entity_id'] in mapping.values() for row in rows), 'registry_scope')
    groups = [row for row in selected if row['platform'] == 'powercalc']
    sources = [row for row in selected if row['platform'] not in ('powercalc', 'template', 'automation', 'script')
               and (not mixed or row['entity_id'] == source_id)]
    _need(len(sources) == 1 and (mixed or len(groups) == 1 and source_id is None),
          'owned_source_required')
    source = sources[0]
    entity_names = {} if entity_names is None else entity_names
    device_names = {} if device_names is None else device_names
    _need(type(entity_names) is dict and type(device_names) is dict
          and all(type(k) is str and type(v) is str for k, v in entity_names.items())
          and all(type(k) is str and type(v) is str for k, v in device_names.items())
          and (mixed or not entity_names and not device_names), 'name_scope')
    entries = documents['core.config_entries']['data']['entries']
    derived_groups = {}
    for group in groups:
        old_group = group['entity_id']
        new_group = mapping[old_group]
        _need(old_group.startswith('sensor.') and new_group.startswith('sensor.'),
              'group_sensor_identity')
        entry_id = group.get('config_entry_id')
        if mixed and entry_id is None:
            # A YAML group still derives its suggested ID from its unchanged
            # name. The native registry move carries only the user entity ID.
            continue
        _need(group.get('suggested_object_id') == old_group.removeprefix('sensor.'),
              'group_suggested_identity')
        matches = [row for row in entries if row['entry_id'] == entry_id]
        _need(len(matches) == 1 and matches[0]['domain'] == 'powercalc'
              and type(matches[0]['data']) is dict and matches[0]['data'].get('_energy_entity') == old_group,
              'group_entry_metadata')
        matches[0]['data']['_energy_entity'] = new_group
        derived_groups[old_group] = new_group.removeprefix('sensor.')
    ordered = (sorted(selected, key=lambda row: row['entity_id']) if mixed else [source, groups[0]])
    for row in ordered:
        old = row['entity_id']
        row['entity_id'] = mapping[old]
        if old in entity_names:
            row['name'] = entity_names[old]
        if old in derived_groups:
            row['suggested_object_id'] = derived_groups[old]
        if owned_script is not None and old == owned_script[0]:
            _need(type(owned_script) is tuple and len(owned_script) == 2
                  and type(owned_script[1]) is bool and row['platform'] == 'script'
                  and old.startswith('script.') and mapping[old].startswith('script.')
                  and row.get('unique_id') == old.removeprefix('script.')
                  and 'previous_unique_id' in row, 'script_owner_unqualified')
            original_key, next_key = row['unique_id'], mapping[old].removeprefix('script.')
            row['unique_id'], row['previous_unique_id'] = next_key, original_key
            if version in ('2026.10.2', '2026.10.4'):
                _need(row.get('suggested_object_id') == original_key, 'script_suggested_unqualified')
                row['suggested_object_id'] = next_key
            if owned_script[1]:
                _need(row.get('original_name') == original_key, 'script_name_unqualified')
                row['original_name'] = next_key
                row['name'] = row.get('name') or original_key
        rows.append(rows.pop(rows.index(row)))
    for old, name in sorted(entity_names.items()):
        if old in mapping:
            continue
        hits = [row for row in rows if row['entity_id'] == old]
        _need(len(hits) == 1, 'name_entity_scope')
        if hits[0].get('name') != name:
            hits[0]['name'] = name
    devices = documents['core.device_registry']['data']['devices']
    for identity, name in sorted(device_names.items()):
        hits = [row for row in devices if row['id'] == identity]
        _need(len(hits) == 1, 'name_device_scope')
        hits[0]['name_by_user'] = name
    if template_entry is not None:
        from .helper_transaction import PreparedHelper
        from .helper_plan import snapshot_equal
        from .yaml_validation_worker import _template_entry_projection, template_entry_semantic
        _need(mixed and type(template_entry) is PreparedHelper and len(template_entry.mapping) == 1
              and set(template_entry.mapping.items()) <= set(mapping.items()), 'template_entry_scope')
        planned = template_entry.plan()
        _need([item.path for item in planned.changes] == [('options', 'state')], 'template_entry_scope')
        template_entry_semantic(template_entry.before)
        template_entry_semantic(template_entry.after)
        matching = [row for row in entries
                    if row.get('entry_id') == template_entry.before['identity']['entry_id']]
        _need(len(matching) == 1 and snapshot_equal(_template_entry_projection(matching[0]),
                                                   template_entry.before), 'template_entry_original')
        matching[0]['options'] = template_entry.after['options'].copy()
    planned_energy = None
    if energy is not None:
        from .native_registry_receipts import _exact_json_equal, _freeze_json
        _need(type(energy) is tuple and len(energy) == 2 and type(energy[0]) is bytes
              and type(energy[1]) is PreparedEnergy, 'energy_preparation')
        before, change = energy
        payload = json.loads(before)
        _need(type(payload) is dict and payload.get('key') == 'energy'
              and type(payload.get('data')) is dict and type(change.before.value) is dict
              and _exact_json_equal(payload.get('data'), _freeze_json(change.before.value)),
              'energy_original_changed')
        payload['data'] = change.after
        planned_energy = (before, json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                             allow_nan=False).encode())
    return PlannedPowercalcDocuments(tuple((key, raw, json.dumps(documents[key], sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()) for key, raw in original), planned_energy)
