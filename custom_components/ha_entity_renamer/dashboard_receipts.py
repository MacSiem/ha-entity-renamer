"""Typed storage-dashboard transitions in the existing ORIGINAL receipt chain."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import stat

from .dashboard_plan import plan_dashboard
from .lovelace_native import PreparedDashboard, revision
from .native_registry import PreparedRegistryChange
from .native_registry_receipts import RegistryIntent, _exact_json_equal, _freeze_json
from .transaction import Refused
from .yaml_plan import Limits, _Reader


def _need(value, reason):
    if not value:
        raise Refused('dashboard_receipt_' + reason)


def validate_prepared(chain, change):
    """Recompute the semantic change from the same registry preparations."""
    from .prepared import decode_change
    _need(type(change) is PreparedDashboard and change.before.config is not None
          and change.before.file_identity is not None, 'prepared_required')
    baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
    _need(baseline == chain.baseline, 'original_required')
    digests = dict(composition.prepared_digests)
    mapping = {}
    for step in chain.plan.steps:
        if step.adapter != 'registry':
            continue
        _need(step.prepared_ref in digests, 'registry_preparation_missing')
        value = decode_change(chain.preparations.resolve(step.prepared_ref, digests[step.prepared_ref]))
        _need(type(value) is PreparedRegistryChange, 'registry_preparation_invalid')
        for old, new in value.mapping.items():
            _need(old not in mapping and new not in mapping.values(), 'registry_mapping_ambiguous')
            mapping[old] = new
    planned = plan_dashboard(change.before.config, mapping)
    _need(bool(planned.changes) and not planned.markers and revision(planned.value) == revision(change.after),
          'semantic_mapping_mismatch')
    path = '.storage/' + change.before.dashboard.key
    original = next((row for row in chain.original_receipt.source_files if row[0] == path), None)
    device, inode, digest = change.before.file_identity
    _need(original == (path, digest, device, inode), 'original_source_mismatch')


def _physical(value):
    _need(type(value) in (tuple, list) and len(value) == 6
          and type(value[0]) is str and len(value[0]) == 64
          and all(c in '0123456789abcdef' for c in value[0])
          and all(type(item) is int and item >= 0 for item in value[1:])
          and value[2] != 0 and stat.S_ISREG(value[3]), 'physical_shape')
    return list(value)


def capture_original_metadata(receipt, changes):
    """Capture owner/mode before the chain root; never recapture on reopen."""
    from .yaml_validation import assert_validation_current
    assert_validation_current(receipt)
    reader = _Reader(receipt.config_root, Limits(max_file_bytes=128_000_000))
    try:
        result = {}
        for change in changes:
            path = '.storage/' + change.before.dashboard.key
            _need(path not in result, 'duplicate_source')
            raw, info = reader.read(path)
            digest = hashlib.sha256(raw).hexdigest()
            _need((info.st_dev, info.st_ino, digest) == change.before.file_identity,
                  'original_source_changed')
            result[path] = [digest, info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]
        assert_validation_current(receipt)
        return validate_original_metadata(receipt, changes, result)
    finally:
        os.close(reader.root)


def validate_original_metadata(receipt, changes, metadata):
    _need(type(metadata) is dict, 'original_metadata_required')
    sources = {row[0]: row[1:] for row in receipt.source_files}
    paths = {'.storage/' + change.before.dashboard.key for change in changes}
    _need(set(metadata) == paths and len(paths) == len(changes), 'original_metadata_scope')
    result = {}
    for path in paths:
        physical = _physical(metadata[path])
        _need(tuple(physical[:3]) == sources.get(path), 'original_metadata_changed')
        result[path] = physical
    return result


@dataclass(frozen=True)
class DashboardObservation:
    snapshot: object
    physical: tuple[str, int, int, int, int, int]


async def observe(native, change, guard):
    """Settled native cache and independently named, no-follow disk identity."""
    from .native_input_baseline import _drained
    guard()
    first = await native.read(change.before.dashboard)
    guard()
    _need(first.file_identity is not None, 'file_missing')
    def physical():
        reader = _Reader(native.hass.config.config_dir, Limits(max_file_bytes=128_000_000))
        try:
            raw, info = reader.read('.storage/' + change.before.dashboard.key)
            digest = hashlib.sha256(raw).hexdigest()
            _need((info.st_dev, info.st_ino, digest) == first.file_identity, 'file_changed')
            return (digest, info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
        finally:
            os.close(reader.root)
    result = await _drained(native.hass.async_add_executor_job, physical)
    guard()
    second = await native.read(change.before.dashboard)
    guard()
    _need(first == second, 'native_changed')
    return DashboardObservation(first, result)


def _expected(change, direction):
    if direction == 'forward':
        return change.before.config, change.before.revision, change.after, change.after_revision
    _need(direction == 'rollback', 'direction')
    return change.after, change.after_revision, change.before.config, change.before.revision


def fold_receipt(chain, states, sources, intent, receipt):
    binding = intent['binding']
    change = chain._original_change(binding['index'])
    path = '.storage/' + change.before.dashboard.key
    _, old_rev, _, new_rev = _expected(change, binding['direction'])
    before, after = intent.get('dashboard_before'), receipt.get('dashboard_after')
    _need(type(before) is dict and set(before) == {'revision', 'physical'}
          and type(after) is dict and set(after) == {'revision', 'physical'}
          and before['revision'] == old_rev and after['revision'] == new_rev
          and _physical(before['physical']) == sources.get(path)
          and _exact_json_equal(receipt.get('result'), _freeze_json(chain._summary(states))), 'binding')
    old, new = _physical(before['physical']), _physical(after['physical'])
    _need((old[1], old[3], old[4], old[5]) == (new[1], new[3], new[4], new[5]), 'metadata_changed')
    result = dict(sources)
    result[path] = new
    _need(_exact_json_equal(receipt.get('input_result'), _freeze_json(result)), 'source_graph_mismatch')
    return result


async def begin(chain, context, observations, dashboard):
    _need(chain.writable and type(dashboard) is DashboardObservation, 'live_observation_required')
    binding = chain._context(context)
    _need(context.step.adapter == 'dashboard', 'phase_required')
    states, head, sequence, completed, pending = chain._reconstruct()
    _need(pending is None and chain.active is None, 'unresolved_intent')
    chain._phase_state(context, completed)
    chain._observe(observations, states)
    sources = chain._input_sources(states)
    change = chain._original_change(context.index)
    old, old_rev, _, _ = _expected(change, binding['direction'])
    path = '.storage/' + change.before.dashboard.key
    _need(dashboard.snapshot.config == old and dashboard.snapshot.revision == old_rev
          and _physical(dashboard.physical) == sources.get(path)
          and dashboard.snapshot.file_identity == (dashboard.physical[1], dashboard.physical[2], dashboard.physical[0]),
          'source_changed')
    payload = {'kind': 'dashboard_intent', 'chain': chain.root_id, 'binding': binding,
               'source_chain': head, 'sequence': sequence, 'source': chain._summary(states),
               'dashboard_before': {'revision': old_rev, 'physical': list(dashboard.physical)}}
    ref = await chain.store.create(chain._slot('intent', context.index, binding['direction']), payload)
    chain.active = RegistryIntent(ref)
    chain._remember((states, head, sequence, completed, ref))
    return chain.active


async def complete(chain, intent, context, before, after, witness):
    from .dashboard_transaction import DashboardEffect
    _need(chain.writable and type(intent) is RegistryIntent and intent is chain.active
          and type(witness) is DashboardEffect, 'live_effect_required')
    previous, current = witness.claim(chain, intent, context)
    binding = chain._context(context)
    states, head, sequence, completed, pending = chain._reconstruct()
    row = chain.store.read(intent.reference[0])
    _need(pending == intent.reference and row is not None and row['digest'] == intent.reference[1]
          and row['payload']['binding'] == binding, 'intent_changed')
    chain._observe(before, states)
    chain._observe(after, states)
    change = chain._original_change(context.index)
    old, old_rev, new, new_rev = _expected(change, binding['direction'])
    _need(previous.snapshot.config == old and previous.snapshot.revision == old_rev
          and current.snapshot.config == new and current.snapshot.revision == new_rev
          and list(previous.physical) == row['payload']['dashboard_before']['physical'], 'native_result')
    sources = chain._input_sources(states)
    result = dict(sources)
    result['.storage/' + change.before.dashboard.key] = list(current.physical)
    receipt = {'kind': 'dashboard_receipt', 'chain': chain.root_id, 'binding': binding,
               'source_chain': head, 'sequence': sequence, 'intent': list(intent.reference),
               'dashboard_after': {'revision': new_rev, 'physical': list(current.physical)},
               'input_result': result, 'result': chain._summary(states)}
    fold_receipt(chain, states, sources, row['payload'], receipt)
    ref = await chain.store.create(chain._slot('receipt', context.index, binding['direction']), receipt)
    chain.active = None
    chain._cache = None
    chain._reconstruct()
    witness.completed(ref)
    return ref


def live_inputs(chain, intent, context, observed):
    """Pin exact before/after file while this actual native call is in flight."""
    from .accepted_input_state import AcceptedInputState
    _need(chain.writable and type(intent) is RegistryIntent and chain.active is intent
          and type(observed) is DashboardObservation, 'live_intent_required')
    binding = chain._context(context)
    states, head, _, _, pending = chain._reconstruct()
    row = chain.store.read(intent.reference[0])
    _need(pending == intent.reference and row is not None and row['digest'] == intent.reference[1]
          and row['payload']['binding'] == binding, 'intent_changed')
    change = chain._original_change(context.index)
    old, old_rev, new, new_rev = _expected(change, binding['direction'])
    prior = _physical(row['payload']['dashboard_before']['physical'])
    actual = _physical(observed.physical)
    _need((observed.snapshot.config == old and observed.snapshot.revision == old_rev and actual == prior)
          or (observed.snapshot.config == new and observed.snapshot.revision == new_rev), 'live_value_changed')
    _need((prior[1], prior[3], prior[4], prior[5]) == (actual[1], actual[3], actual[4], actual[5])
          and observed.snapshot.file_identity == (actual[1], actual[2], actual[0]), 'live_file_changed')
    sources = chain._input_sources(states)
    sources['.storage/' + change.before.dashboard.key] = actual
    def live():
        _, current_head, _, _, current_pending = chain._reconstruct()
        _need(chain.writable and chain.active is intent and current_pending == intent.reference
              and current_head == head, 'live_intent_expired')
    return AcceptedInputState(chain.baseline.receipt_json, head, sources,
        {key: value['document'] for key, value in states.items()}, live_check=live)
