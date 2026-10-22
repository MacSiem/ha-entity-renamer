"""Native manager bridge. Caller supplies transaction journal/guard/barriers.

No direct .storage access, no registry rename, and no disk durability claim.
This module intentionally imports no Home Assistant at import time.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from .helper_plan import HelperPlan, plan_helper, rebase_after_listeners, snapshot_equal


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return deepcopy(value)


def read_entry(hass, entry_id: str) -> dict:
    """Read a detached snapshot through ConfigEntries, never cached adapter state.

    Contains local private config: journal locally only, never emit to telemetry.
    Native listener changes modified_at; it is deliberately not an identity field.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise ValueError('helper_entry_missing')
    return {
        'domain': entry.domain,
        'identity': {key: getattr(entry, key) for key in
                     ('entry_id', 'unique_id', 'title', 'version', 'minor_version', 'source')},
        'data': _plain(entry.data), 'options': _plain(entry.options),
        'subentries': {key: {
            'identity': {name: getattr(sub, name) for name in
                         ('subentry_id', 'subentry_type', 'unique_id', 'title')},
            'data': _plain(sub.data),
        } for key, sub in entry.subentries.items()},
    }


async def apply_entry(hass, plan: HelperPlan, *, guard, settle, record_intent,
                      verify_runtime, validate_snapshot) -> dict:
    """Apply one entry inside owner's exclusive transaction lock.

    guard(): synchronous fresh admin/operation authorization, raises on failure.
    settle(): async barrier for relevant registry listeners/reloads (must be
      bounded by owner; an ACK or arbitrary sleep is insufficient).
    validate_snapshot(after): async installed-version/capability and domain schema
      validation; raises for unsupported HA/Powercalc versions or layouts.
    record_intent(before, after): async durable local journal BEFORE mutation.
    verify_runtime(after): async independent domain runtime verification, raises
      unless loaded helper dependencies now use targets. Required even on no-op.

    Result verifies manager readback/runtime only. Owner still must prove manager
    persistence through supported flush/restart and journal whole-transaction
    progress. An exception after intent/write is UNKNOWN: inspect before retry.
    """
    if plan.blocked:
        raise ValueError('helper_coverage_blocked')
    entry_id = plan.before['identity']['entry_id']
    guard()
    await settle()
    guard()
    current = read_entry(hass, entry_id)
    desired = rebase_after_listeners(plan, current)
    if not snapshot_equal(desired['subentries'], current['subentries']):
        raise ValueError('helper_subentry_schema_unsupported')
    await validate_snapshot(desired)
    guard()
    if not snapshot_equal(read_entry(hass, entry_id), current):
        raise ValueError('helper_snapshot_conflict')
    await record_intent(current, desired)
    # Journaling awaits: protect against any intervening edit or removed entry.
    guard()
    if not snapshot_equal(read_entry(hass, entry_id), current):
        raise ValueError('helper_snapshot_conflict')
    changed = not snapshot_equal(desired, current)
    if changed:
        entry = hass.config_entries.async_get_entry(entry_id)
        # No await between final compare and native synchronous mutation.
        hass.config_entries.async_update_entry(
            entry, data=desired['data'], options=desired['options'])
        if desired['domain'] != 'powercalc':
            # options_flow_reloads only applies to OptionsFlowWithReload, not
            # direct manager writes. Do not infer automatic reload from it.
            guard()
            if not await hass.config_entries.async_reload(entry_id):
                raise ValueError('helper_reload_failed')
        # Powercalc add_update_listener reloads self and parents; don't duplicate.
        await settle()
        guard()
    if not snapshot_equal(read_entry(hass, entry_id), desired):
        raise ValueError('helper_readback_mismatch')
    await verify_runtime(desired)
    guard()
    if not snapshot_equal(read_entry(hass, entry_id), desired):
        raise ValueError('helper_changed_during_runtime_verification')
    return {'manager_readback': 'verified', 'runtime': 'verified',
            'persistence': 'unknown', 'adapter_write': changed}


def inventory(hass, mapping):
    """All entries, including disabled/unloaded and all subentries.

    Any failure to enumerate/read MUST be a transaction coverage blocker.
    Never restrict inventory to registry entries in the renamed domain.
    """
    return tuple(plan_helper(read_entry(hass, entry.entry_id), mapping)
                 for entry in hass.config_entries.async_entries())
