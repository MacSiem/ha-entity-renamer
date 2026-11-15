"""Narrow native HomeKit YAML filter reload; caller owns journal and source CAS.

HomeKit's reload service reloads *all* entries and reimports YAML. It does not
reset pairings. This adapter proves filter configuration and bridge identity;
it does not prove an iOS client's exported accessory identity or pairing state.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re

from .transaction import Refused
from .helper_native import read_entry
from .reference_planner import Consumer, Context, Scope, Syntax, plan_references


def _need(ok, reason):
    if not ok:
        raise Refused('homekit_filter_' + reason)


def plan_filter(options, mapping):
    """Only native filter lists; unknown options are preserved as-is."""
    checked = plan_references(options, mapping, Context(Consumer.DASHBOARD, ())).value
    _need(type(checked) is dict and type(checked.get('filter', {})) is dict, 'invalid_options')
    scopes = []
    filt = checked.get('filter', {})
    for key in ('include_entities', 'exclude_entities'):
        if key not in filt:
            continue
        val = filt[key]
        _need(type(val) is list and all(type(item) is str and re.fullmatch(
            r'[a-z_][a-z0-9_]*\.[a-z0-9_]+', item, re.ASCII) for item in val), 'invalid_list')
        for index in range(len(val)):
            scopes.append(Scope(('filter', key, index), Syntax.ENTITY_TREE))
    result = plan_references(checked, mapping, Context(Consumer.DASHBOARD, tuple(scopes)))
    _need(not any(m.code != 'no_supported_scopes' for m in result.markers), 'unsupported_reference')
    for key in ('include_entities', 'exclude_entities'):
        vals = result.value.get('filter', {}).get(key, [])
        _need(len(vals) == len(set(vals)), 'duplicate_reference')
    return result


@dataclass(frozen=True)
class HomeKitFilterCapture:
    entries: tuple[dict, ...]
    expected: tuple[dict, ...]
    mapping: dict
    bridge_identity: tuple = field(repr=False)


def _bridge_identity(entry):
    homekit = getattr(getattr(entry, 'runtime_data', None), 'homekit', None)
    driver = getattr(homekit, 'driver', None)
    state = getattr(driver, 'state', None)
    _need(state is not None and hasattr(state, 'paired_clients'), 'bridge_not_started')
    return (state.mac, bool(state.paired), frozenset(state.paired_clients))


def capture(hass, mapping):
    """Capture every bridge; no configuration or persistent secret is emitted."""
    from homeassistant.const import __version__
    _need(__version__ == '2026.11.1', 'unsupported_core')
    _need(hass.services.has_service('homekit', 'reload'), 'reload_unavailable')
    entries = tuple(sorted(hass.config_entries.async_entries('homekit'), key=lambda e: e.entry_id))
    _need(bool(entries), 'bridge_missing')
    before, after, identities = [], [], []
    from homeassistant.components.homekit import FILTER_SCHEMA
    affected = False
    for entry in entries:
        row = read_entry(hass, entry.entry_id)
        # First YAML import contains an ephemeral index which native reload
        # removes. Qualify only already-normalized entries: otherwise the
        # reload has a second, unplanned ConfigEntry data effect.
        _need(row['identity']['source'] != 'import' or 'index' not in row['data'],
              'import_index_not_normalized')
        proposed = deepcopy(row)
        result = plan_filter(row['options'], mapping)
        if result.changes:
            _need(row['identity']['source'] == 'import', 'ui_config_unsupported')
            _need(getattr(entry, 'runtime_data', None) is not None, 'bridge_not_loaded')
            proposed['options'] = result.value
            prior = FILTER_SCHEMA(row['options'].get('filter', {})).get_filter()
            following = FILTER_SCHEMA(result.value.get('filter', {})).get_filter()
            _need(all(prior(old) == following(new) for old, new in mapping.items()),
                  'filter_membership_changed')
            affected = True
        before.append(row); after.append(proposed)
        identities.append(_bridge_identity(entry))
    _need(affected, 'no_affected_bridge')
    return HomeKitFilterCapture(tuple(before), tuple(after), dict(mapping), tuple(identities))


async def reload_after_yaml(hass, captured, *, verify_source, guard, record_intent, settle):
    """Invoke HA reload once after verified source change; uncertain outcome stops.

    verify_source must independently check the exact accepted YAML postimage.
    The caller must fence native editors, journal intent and own recovery. Never
    replay on an exception or cancellation without independently classifying it.
    """
    _need(type(captured) is HomeKitFilterCapture, 'capture_required')
    guard()
    ids = tuple(row['identity']['entry_id'] for row in captured.entries)
    _need(tuple(sorted(e.entry_id for e in hass.config_entries.async_entries('homekit'))) == ids,
          'inventory_changed')
    _need(tuple(read_entry(hass, key) for key in ids) == captured.entries, 'entry_changed')
    await verify_source()
    guard()
    await record_intent(captured)
    guard()
    await hass.services.async_call('homekit', 'reload', {}, blocking=True)
    await settle()
    guard()
    _need(tuple(read_entry(hass, key) for key in ids) == captured.expected, 'entry_readback')
    _need(tuple(_bridge_identity(hass.config_entries.async_get_entry(key)) for key in ids)
          == captured.bridge_identity, 'bridge_identity_changed')
    for old, new in captured.mapping.items():
        for before, after in zip(captured.entries, captured.expected):
            if before == after:
                continue
            entry = hass.config_entries.async_get_entry(after['identity']['entry_id'])
            homekit = getattr(getattr(entry, 'runtime_data', None), 'homekit', None)
            _need(homekit is not None, 'bridge_not_loaded')
            filt = homekit._filter.get_filter()
            # Verify the loaded filter matches the native schema's predicate,
            # including all domain/glob exclusions; identity/export is separate.
            from homeassistant.components.homekit import FILTER_SCHEMA
            expected = FILTER_SCHEMA(after['options'].get('filter', {})).get_filter()
            _need(filt(old) == expected(old) and filt(new) == expected(new), 'runtime_filter')
    return captured.expected


def _projection(rows):
    """Same canonical imported-bridge projection as the isolated checker."""
    from homeassistant.components.homekit.const import BRIDGE_NAME, DEFAULT_PORT
    from .yaml_validation_worker import _canonical_homekit_filter
    return sorted([[row['data'].get('name', BRIDGE_NAME),
                    row['data'].get('port', DEFAULT_PORT),
                    _canonical_homekit_filter(row['options'].get('filter', {}))]
                   for row in rows if row['identity']['source'] == 'import'],
                  key=lambda row: (row[0], row[1]))


class HomeKitFilterPhase:
    """Live phase bound to both ORIGINAL checker seals and native bridge state."""
    def __init__(self, hass, mapping, runtime_expected):
        from .yaml_validation_worker import _fingerprint
        seals = [row for row in runtime_expected if row[0] == 'homekit']
        _need(len(seals) == 1 and None not in seals[0][1:] and seals[0][1] != seals[0][2],
              'checker_seal_missing')
        self.hass, self.mapping = hass, dict(mapping)
        self.old_digest, self.new_digest = seals[0][1:]
        entries = tuple(sorted(hass.config_entries.async_entries('homekit'), key=lambda e: e.entry_id))
        rows = tuple(read_entry(hass, entry.entry_id) for entry in entries)
        digest = _fingerprint(_projection(rows))
        _need(digest in (self.old_digest, self.new_digest), 'baseline_projection')
        self.position = 'before' if digest == self.old_digest else 'after'
        selected = self.mapping if self.position == 'before' else {v: k for k, v in self.mapping.items()}
        self.capture = capture(hass, selected)
        desired = self.new_digest if self.position == 'before' else self.old_digest
        _need(_fingerprint(_projection(self.capture.expected)) == desired, 'candidate_projection')
        self.active = False

    def check(self):
        _need(not self.active, 'reload_pending')
        ids = tuple(row['identity']['entry_id'] for row in self.capture.entries)
        _need(tuple(sorted(e.entry_id for e in self.hass.config_entries.async_entries('homekit'))) == ids,
              'inventory_changed')
        _need(tuple(read_entry(self.hass, key) for key in ids) == self.capture.entries,
              'entry_changed')
        _need(tuple(_bridge_identity(self.hass.config_entries.async_get_entry(key)) for key in ids)
              == self.capture.bridge_identity, 'bridge_identity_changed')

    async def activate(self, *, direction, verify_source, guard, record_intent, settle):
        expected = 'before' if direction == 'forward' else 'after'
        _need(direction in ('forward', 'rollback') and self.position == expected, 'wrong_position')
        self.check()
        self.active = True
        try:
            await reload_after_yaml(self.hass, self.capture, verify_source=verify_source,
                guard=guard, record_intent=record_intent, settle=settle)
            self.position = 'after' if direction == 'forward' else 'before'
            selected = {v: k for k, v in self.mapping.items()} if self.position == 'after' else self.mapping
            self.capture = capture(self.hass, selected)
        except BaseException:
            # Active stays set after an uncertain native effect: no replay.
            raise
        self.active = False
        self.check()
