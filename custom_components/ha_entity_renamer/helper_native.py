"""Native manager bridge. Caller supplies transaction journal/guard/barriers.

No direct .storage access, no registry rename, and no disk durability claim.
This module intentionally imports no Home Assistant at import time.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from .helper_plan import HelperPlan, plan_helper, rebase_after_listeners, snapshot_equal
from .transaction import Refused


class UnsupportedEntryReferences(Refused):
    """A reviewed ConfigEntry scope is unsupported; contains metadata only."""
    def __init__(self, plans, mapping):
        import re
        def references(plan):
            values = [change.before for change in plan.changes]
            def strings(value):
                if type(value) is str:
                    yield value
                elif type(value) is dict:
                    for key, child in value.items():
                        if type(key) is str:
                            yield key
                        yield from strings(child)
                elif type(value) is list:
                    for child in value:
                        yield from strings(child)
            for marker in plan.markers:
                value = plan.before
                try:
                    for part in marker.path:
                        value = value[part]
                except (KeyError, IndexError, TypeError):
                    continue
                values.extend(strings(value))
            return sorted(old for old in mapping if any(
                re.search(r'(?<![A-Za-z0-9_.])' + re.escape(old) + r'(?![A-Za-z0-9_.])', value)
                for value in values))
        self.sources = tuple({'domain': plan.before['domain'],
            'entry_id': plan.before['identity']['entry_id'],
            'references': references(plan),
            'reasons': sorted({marker.code for marker in plan.markers}) or ['unsupported_config_entry_change']}
            for plan in plans if plan.blocked or plan.changes)
        super().__init__('unsupported_config_entry_scope')


def partition_entry_coverage(plans, mapping):
    """Separate proven affected entries from unverified, unrelated scope."""
    rows = UnsupportedEntryReferences(plans, mapping).sources
    return (tuple(row for row in rows if row['references']),
            tuple(row for row in rows if not row['references']))



@dataclass(frozen=True)
class NativeConfigMutation:
    """Private storage rows around one synchronous ConfigEntries update.

    This is a call observation, not listener provenance, a full-document receipt
    or a durability claim. Never expose these local private bytes to clients.
    """
    entry_id: str
    before_json: str
    after_json: str
    changed: bool


def _config_row_json(entry):
    """Use HA's storage encoder; unavailable evidence stays explicitly unknown."""
    try:
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        row = entry.as_dict()
        if row['entry_id'] != entry.entry_id:
            return None
        return _orjson_bytes_default_encoder(row).decode('utf-8')
    except Exception:
        # Evidence capture must not change existing native update/reload behavior.
        # Missing/incompatible serialization is not a fabricated partial snapshot.
        return None


def _mutate_entry(manager, entry, *, data, options):
    """Capture both actual rows before any reload/listener await, even on False."""
    entry_id = entry.entry_id
    before_json = _config_row_json(entry)
    changed = manager.async_update_entry(entry, data=data, options=options)
    # ConfigEntries returns bool, NOT an entry. Serialize the same actual entry.
    after_json = _config_row_json(entry)
    if (before_json is None or after_json is None or type(changed) is not bool
            or entry.entry_id != entry_id):
        return None
    return NativeConfigMutation(entry_id, before_json, after_json, changed)


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
    identity = {key: getattr(entry, key) for key in
                ('entry_id', 'unique_id', 'title', 'version', 'minor_version', 'source')}
    disabled = entry.disabled_by
    identity.update(disabled_by=getattr(disabled, 'value', disabled),
                    pref_disable_new_entities=entry.pref_disable_new_entities,
                    pref_disable_polling=entry.pref_disable_polling)
    return {
        'domain': entry.domain,
        'identity': identity,
        'data': _plain(entry.data), 'options': _plain(entry.options),
        'subentries': {key: {
            'identity': {name: getattr(sub, name) for name in
                         ('subentry_id', 'subentry_type', 'unique_id', 'title')},
            'data': _plain(sub.data),
        } for key, sub in entry.subentries.items()},
    }


def _controlled_template_explicit_reload(entry, native_listener):
    """Use HA's own registered Template listener when present, else reload once."""
    listeners = tuple(entry.update_listeners)
    if not listeners:
        return True
    if native_listener is not None and len(listeners) == 1 and listeners[0] is native_listener:
        return False
    raise ValueError('controlled_template_reload_contract_unavailable')


async def apply_entry(hass, plan: HelperPlan, *, guard, settle, record_intent,
                      verify_runtime, validate_snapshot, before_mutation=None,
                      controlled_template_reload=False) -> dict:
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
    progress. The private _native_config_mutation field is the actual call stamp,
    or None when no call occurred or native serialization/return was unavailable.
    None never proves a listener effect or a native no-op. adapter_write retains
    its existing meaning (an adapter call was attempted), independent of HA's
    returned bool. An exception after intent/write is UNKNOWN: inspect before retry.
    """
    if type(controlled_template_reload) is not bool:
        raise ValueError('helper_reload_policy_invalid')
    if controlled_template_reload and plan.before['domain'] != 'template':
        raise ValueError('helper_reload_policy_invalid')
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
    mutation = None
    changed = not snapshot_equal(desired, current)
    if changed:
        entry = hass.config_entries.async_get_entry(entry_id)
        lifecycle = entry.state.value
        if lifecycle not in ('loaded', 'not_loaded'):
            raise ValueError('helper_lifecycle_unsettled')
        if before_mutation is not None:
            # Trusted receipt intent and whole-input fence, after lifecycle checks.
            await before_mutation()
            guard()
            if not snapshot_equal(read_entry(hass, entry_id), current):
                raise ValueError('helper_snapshot_conflict')
            entry = hass.config_entries.async_get_entry(entry_id)
            lifecycle = entry.state.value
            if lifecycle not in ('loaded', 'not_loaded'):
                raise ValueError('helper_lifecycle_unsettled')
        explicit_reload = desired['domain'] != 'powercalc' and lifecycle == 'loaded'
        if controlled_template_reload:
            # Older Template releases install this exact native update listener;
            # newer releases do not. Refuse any other listener population before
            # the write instead of inferring behaviour from a patch version.
            from homeassistant.components import template as native_template
            if lifecycle != 'loaded':
                raise ValueError('controlled_template_reload_contract_unavailable')
            explicit_reload = _controlled_template_explicit_reload(
                entry, getattr(native_template, 'config_entry_update_listener', None))
        # No await between final compare and native synchronous mutation.
        mutation = _mutate_entry(
            hass.config_entries, entry, data=desired['data'], options=desired['options'])
        if explicit_reload:
            # options_flow_reloads only applies to OptionsFlowWithReload, not
            # direct manager writes. Do not infer automatic reload from it.
            guard()
            if not await hass.config_entries.async_reload(entry_id):
                raise ValueError('helper_reload_failed')
        # Previously unloaded/disabled entries must remain inactive. The required
        # activation observer checks that native update listeners did not load them.
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
            'persistence': 'unknown', 'adapter_write': changed,
            '_native_config_mutation': mutation}


def inventory(hass, mapping):
    """All entries, including disabled/unloaded and all subentries.

    Any failure to enumerate/read MUST be a transaction coverage blocker.
    Never restrict inventory to registry entries in the renamed domain.
    """
    return tuple(plan_helper(read_entry(hass, entry.entry_id), mapping)
                 for entry in hass.config_entries.async_entries())
