"""Detached inverse images for a validated Powercalc handoff preimage.

No HA objects are changed and this function grants no mutation authority.
"""
from copy import deepcopy

from .native_registry_receipts import _exact_json_equal, _freeze_json
from .transaction import Refused


def reverse_images(handoff, pss, restore):
    def need(value):
        if not value:
            raise Refused('controlled_powercalc_prefix_images_unverified')

    need(type(handoff) is dict and handoff.get('schema') == 3
         and type(pss) is dict and type(restore) is dict
         and type(handoff.get('population')) is list
         and type(handoff.get('pss_owned')) is dict
         and type(handoff.get('pss_owned_before')) is dict
         and type(handoff.get('restore_owned')) is dict)
    owned = handoff['pss_owned']
    need(all(key in pss and _exact_json_equal(pss[key], _freeze_json(value))
             for key, value in owned.items())
         and set(handoff['pss_owned_before']) <= set(owned)
         and all(key in restore and _exact_json_equal(restore[key], _freeze_json(value))
                 for key, value in handoff['restore_owned'].items()))
    prior_pss = {key: deepcopy(value) for key, value in pss.items() if key not in owned}
    prior_pss.update(deepcopy(handoff['pss_owned_before']))
    prior_restore = deepcopy(restore)
    clones = {}
    for row in handoff['population']:
        need(type(row) is dict and type(row.get('uuid')) is str and row['uuid'] not in clones
             and type(row.get('before_id')) is str and type(row.get('after_id')) is str
             and type(row.get('payload')) is dict and type(row['payload'].get('after')) is dict)
        before, after = row['before_id'], row['after_id']
        source = row['payload']['after']
        need(type(source.get('state')) is dict and source['state'].get('entity_id') == after
             and source.get('entity_registry_id') == row['uuid'] and after in prior_restore
             and after in handoff['restore_owned']
             and _exact_json_equal(source, _freeze_json(handoff['restore_owned'][after]))
             and (before == after or before not in prior_restore))
        restored = deepcopy(source)
        restored['state']['entity_id'] = before
        if before != after:
            del prior_restore[after]
        prior_restore[before] = restored
        clones[row['uuid']] = restored
    return prior_pss, prior_restore, clones


def materialize_reverse_images(pss, clones):
    """Prove native constructors reproduce detached data, without storing it."""
    from homeassistant.core import State
    from homeassistant.helpers.json import json_bytes
    from .native_input_baseline import _loads
    from .native_restore_state_payload import _native, _capture, _canonical, _timestamp
    api = _native()
    version, _, _, _, _, StoredState, RestoredExtraData, _, _ = api
    if version not in ('2026.11.0', '2026.11.1') or type(pss) is not dict or type(clones) is not dict:
        raise Refused('controlled_powercalc_prefix_materialization_unverified')
    groups = {}
    for group, members in pss.items():
        if type(group) is not str or type(members) is not dict:
            raise Refused('controlled_powercalc_prefix_materialization_unverified')
        groups[group] = {}
        for name, row in members.items():
            if row is None:
                groups[group][name] = None
                continue
            state = State.from_dict(row) if type(row) is dict else None
            if (type(state) is not State or state.entity_id != name
                    or not _exact_json_equal(_loads(json_bytes(state.as_dict())), _freeze_json(row))):
                raise Refused('controlled_powercalc_prefix_materialization_unverified')
            groups[group][name] = state
    stored = {}
    for uuid, row in clones.items():
        if type(uuid) is not str or type(row) is not dict or row.get('entity_registry_id') != uuid:
            raise Refused('controlled_powercalc_prefix_materialization_unverified')
        state = State.from_dict(row.get('state'))
        extra = None if row.get('extra_data') is None else RestoredExtraData(row['extra_data'])
        clone = StoredState(state, extra, _timestamp(row['last_seen']), uuid)
        if type(state) is not State or _canonical(_capture(clone, api)) != _canonical(row):
            raise Refused('controlled_powercalc_prefix_materialization_unverified')
        stored[uuid] = clone
    return groups, stored
