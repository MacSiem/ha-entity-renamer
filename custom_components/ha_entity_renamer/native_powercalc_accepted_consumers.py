"""Read-only Powercalc source facts at one acknowledged ORIGINAL chain head.

No reload/lifecycle, replacement capture, baseline, journal or apply capability.
Only precompiled original/proposed images can be selected. G3-G6 stay closed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json

from .native_input_baseline import _KEYS, _envelope
from .native_registry_receipts import RegistryReceiptChain
from .native_discovery_inputs import (
    async_observe_discovery_inputs, assert_discovery_managers_current,
)
from .powercalc_runtime_observer import PowercalcRuntimeCapture, _guard
from .powercalc_source_binding import (
    PowercalcVendor, OriginalPowercalcSources, PowercalcBoundRow,
    _sample, _need, _sha,
)
from .powercalc_profile_binding import runtime_current
from .powercalc_profile_inputs import PreparedProfileFiles, read_bundle
from .powercalc_candidate_sources import CandidatePowercalcSources
from .yaml_source_binding import OriginalSourceEvidence
from .yaml_validation import _settled_executor


@dataclass(frozen=True)
class AcceptedPowercalcConsumers:
    version: str
    evidence_sha256: str
    operation_id: str
    fingerprint: str
    head: str
    source_position: str
    original_runtime_sha256: str
    rows: tuple[PowercalcBoundRow, ...]
    relationships: tuple[tuple[str, str, str, int], ...]
    energy_settings_sha256: str | None

    @property
    def migration_ready(self):
        return False

    @property
    def family_complete(self):
        return False

    @property
    def status(self):
        return 'UNKNOWN'


def _documents(state, observed):
    sources, documents = state.sources(), state.documents()
    _need(set(documents) == {row.key for row in observed.files},
          'accepted_native_inventory_changed')
    for row in observed.files:
        expected = sources.get('.storage/' + row.key)
        _need(expected is not None
              and [hashlib.sha256(row.raw).hexdigest(), *row.identity] == expected[:4]
              and (expected[4] is None or [row.uid, row.gid] == expected[4:])
              and json.loads(row.manager_json) == documents[row.key]['data'],
              'accepted_native_sources_changed')


def _literal_documents(planned, accepted, original, chain, *, replay_owned_registry_order=False):
    """Full images, with ONLY proven native own-call modified_at stamps.

    The chain has already replayed and validated each complete native effect.
    Planned metadata must retain its literal ORIGINAL value. It cannot prescribe
    a wildcard. Only a prepared row's native timestamp may be the value in the
    immutable accepted receipt. Foreign writes still fail the whole-source CAS.
    """
    expected = deepcopy(planned)
    if replay_owned_registry_order:
        # Core's entity-ID writer removes the old dictionary key and appends
        # the new one on every actual call, including inverse. Replay only the
        # already validated SAME-chain mutations, never a current row/order.
        rows = expected['core.entity_registry']['data']['entities']
        for _, receipt in chain._setup_history:
            if receipt.get('kind') != 'registry_receipt':
                continue
            mutation = receipt.get('mutation', {})
            if mutation.get('kind') != 'entity':
                continue
            before = json.loads(mutation['before_json'])
            after = json.loads(mutation['after_json'])
            if before.get('entity_id') == after.get('entity_id'):
                continue
            matches = [i for i, row in enumerate(rows) if row.get('id') == mutation.get('identity')]
            _need(len(matches) == 1, 'accepted_registry_order_identity_changed')
            rows.append(rows.pop(matches[0]))
    owned = {key: set() for key in _KEYS}
    reversed_script_rows = {}
    for step, change in zip(chain.plan.steps, chain.changes, strict=True):
        if step.adapter == 'registry':
            owned['core.' + change.kind + '_registry'].add(change.identity)
            from .native_registry_transition import _native_script_unique_move
            if change.kind == 'entity' and _native_script_unique_move(change.before, change.after):
                for _, receipt in chain._setup_history:
                    mutation = receipt.get('mutation', {})
                    if (receipt.get('kind') == 'registry_receipt'
                            and receipt.get('binding', {}).get('direction') == 'rollback'
                            and mutation.get('kind') == 'entity'
                            and mutation.get('identity') == change.identity):
                        observed = json.loads(mutation['after_json'])
                        if (observed.get('entity_id') == change.before['entity_id']
                                and observed.get('unique_id') == change.before['unique_id']
                                and observed.get('previous_unique_id') == change.after['unique_id']):
                            reversed_script_rows[change.identity] = observed
        elif step.adapter == 'helpers':
            owned['core.config_entries'].add(change.before['identity']['entry_id'])
    for key, collection, identity in (
            ('core.entity_registry', 'entities', 'id'),
            ('core.device_registry', 'devices', 'id'),
            ('core.config_entries', 'entries', 'entry_id')):
        before = {row[identity]: row for row in original[key]['data'][collection]}
        actual = {row[identity]: row for row in accepted[key]['data'][collection]}
        for row in expected[key]['data'][collection]:
            uid = row[identity]
            if uid not in owned[key] or uid not in before or uid not in actual:
                continue
            if (key == 'core.entity_registry' and uid in reversed_script_rows
                    and row == before[uid] and actual[uid] == reversed_script_rows[uid]):
                row['previous_unique_id'] = reversed_script_rows[uid]['previous_unique_id']
            if ('modified_at' in row and 'modified_at' in before[uid]
                    and 'modified_at' in actual[uid]
                    and row['modified_at'] == before[uid]['modified_at']):
                row['modified_at'] = actual[uid]['modified_at']
    # Energy can recreate its generated cost entity after the source rename.
    # Apply only the already reconstructed, typed same-chain cost receipts;
    # no value is taken from the current registry as an authority.
    from .native_registry_receipts import _exact_json_equal, _freeze_json
    rows = expected['core.entity_registry']['data']['entities']
    for _, receipt in chain._setup_history:
        if receipt.get('kind') != 'powercalc_energy_receipt':
            continue
        effect = receipt.get('cost_registry_after')
        for change in (() if effect is None else effect['changes']):
            matches = [row for row in rows if row.get('id') == change['id']]
            _need(len(matches) == 1, 'accepted_cost_registry_identity_changed')
            row = matches[0]
            before, after = change['before'], change['after']
            expected_before = deepcopy(before)
            expected_before['modified_at'] = row.get('modified_at')
            _need(_exact_json_equal(row, _freeze_json(expected_before)),
                  'accepted_cost_registry_preimage_changed')
            row['suggested_object_id'] = after['suggested_object_id']
            row['modified_at'] = after['modified_at']
        projection = getattr(chain, '_effective_cold', None)
        if projection is not None and receipt.get('binding', {}).get('direction') == 'forward':
            for update in projection['updates']:
                matches = [row for row in rows if row.get('id') == update['registry_id']]
                _need(len(matches) == 1 and matches[0].get('modified_at') == update['before'],
                      'accepted_cold_registry_preimage_changed')
                matches[0]['modified_at'] = update['after']
    return expected == accepted


def _source_image(chain, evidence, state):
    receipt = chain.original_receipt
    original_documents = {row.key: _envelope(row.raw, row.key, receipt.version)
                          for row in chain.baseline.files}
    candidate = evidence.powercalc_candidate_sources
    has_energy = (type(candidate) is CandidatePowercalcSources
                  and candidate.documents.energy is not None)
    excluded = {'.storage/' + key for key in _KEYS}
    if has_energy:
        excluded.add('.storage/energy')
    original_files = {path: digest for path, digest, *_ in receipt.source_files if path not in excluded}
    current_files = {path: row[0] for path, row in state.sources().items() if path not in excluded}
    energy_position = 'original'
    if has_energy:
        indexes = [index for index, step in enumerate(chain.plan.steps) if step.adapter == 'energy']
        _need(len(indexes) == 1, 'candidate_energy_step_required')
        _, _, _, completed, pending = chain._reconstruct()
        _need(pending is None, 'candidate_energy_intent_pending')
        energy_position = 'proposed' if completed.get(indexes[0]) == 'forward' else 'original'
    accepted = state.documents()
    has_inverse = any(receipt.get('kind') == 'registry_receipt'
                      and receipt.get('binding', {}).get('direction') == 'rollback'
                      for _, receipt in chain._setup_history)
    if (energy_position == 'original' and current_files == original_files
            and _literal_documents(original_documents, accepted, original_documents, chain,
                                   replay_owned_registry_order=has_inverse)):
        return 'original', evidence
    _need(type(candidate) is CandidatePowercalcSources, 'candidate_sources_required')
    candidate.assert_original(receipt, evidence.powercalc_sources)
    for key, before, _ in candidate.documents.documents:
        _need(_envelope(before, key, receipt.version) == original_documents[key],
              'candidate_chain_original_mismatch')
    proposed_files = dict(original_files)
    edits = dict(receipt.proposed_files)
    _need(set(edits) <= set(original_files), 'candidate_file_graph_changed')
    proposed_files.update(edits)
    proposed_documents = {key: _envelope(after, key, receipt.version)
                          for key, _, after in candidate.documents.documents}
    native_proposed = _literal_documents(proposed_documents, accepted, original_documents, chain)
    if current_files == proposed_files and native_proposed and (not has_energy or energy_position == 'proposed'):
        return 'proposed', replace(evidence, powercalc_sources=candidate.sources)
    # An acknowledged native prefix may precede the YAML-last step. It is
    # observable only when BOTH independently compiled Powercalc graphs are
    # literally identical; a source-ID half-migration cannot use this branch.
    if (energy_position == 'original' and current_files == original_files and native_proposed
            and candidate.sources.native_json == evidence.powercalc_sources.native_json
            and candidate.sources.closed_graph_json == evidence.powercalc_sources.closed_graph_json):
        return 'original_yaml_proposed_native', evidence
    _need(False, 'accepted_image_unverified')


def _accepted_sources(chain, state, receipt, vendor=None):
    """Executor-only read of the SAME durable stores, then FULL source fence.

    Loaded immutable rows alone cannot hide a disappeared/changed preparation or
    receipt file. Compare the original VerifiedStore identity; never reload,
    repair, initialize or adopt its newly read content.
    """
    from .persistence import _snapshot
    if vendor is not None:
        # SAME executor job as the accepted fence. HA public source files live
        # outside config_root; no extra await may separate their final pin read
        # from the final whole accepted graph read and synchronous suffix.
        vendor.sources()
    for store in (chain.preparations.store, chain.store.store):
        _need(store._loaded and not store._failed and store._identity is not None,
              'accepted_durable_store_unavailable')
        _, identity = _snapshot(store._path, store._key)
        _need(identity == store._identity, 'accepted_durable_store_changed')
    state.assert_current(receipt)


def _prepare_profiles(receipt, selected):
    # Executor only. The compiler already parsed all variants; no cache warming,
    # library resolution or adoption of freshly read bytes as configuration.
    graph = json.loads(selected.powercalc_sources.closed_graph_json)
    expected = tuple(tuple(row) for row in graph['files'])
    actual = tuple(sorted((path, _sha(raw)) for path, raw in read_bundle(receipt.config_root).items()))
    physical = {row[0]: row[1] for row in receipt.source_files}
    _need(actual == expected and all(physical.get(path) == digest for path, digest in expected),
          'accepted_profile_bytes_changed')
    return PreparedProfileFiles(selected.receipt_json,
                                selected.powercalc_sources.closed_graph_json, actual)


async def async_observe_accepted_powercalc_consumers(hass, chain, evidence, captured, *, guard):
    """Detached facts at a completed SAME-chain prefix; never a mutation grant.

    The ORIGINAL runtime capture remains mandatory. Only the existing proven
    dynamic-strategy exception is allowed. Replacement entities/source binding
    from a future lifecycle require G4 proof, deliberately not supplied here.
    """
    _need(type(chain) is RegistryReceiptChain and type(evidence) is OriginalSourceEvidence
          and type(captured) is PowercalcRuntimeCapture,
          'accepted_chain_and_original_capture_required')
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    _, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
    controlled = type(composition) is ControlledPowercalcComposition
    receipt = chain.original_receipt
    evidence.assert_receipt(receipt)
    _need(type(evidence.powercalc_sources) is OriginalPowercalcSources
          and evidence.powercalc_sources.closed_graph_json is not None
          and captured.version == receipt.version
          and (receipt.version in ('2026.10.2', '2026.10.4')
               or (receipt.version in ('2026.11.0', '2026.11.1') and controlled)),
          'accepted_profile_scope_required')
    state = chain.reconstruct_inputs()  # Pending intent refuses, never sampled away.

    def check():
        _guard(guard)
        evidence.assert_receipt(chain.original_receipt)
        from .native_template_state_yaml import retained_powercalc_original
        if retained_powercalc_original(chain) is None:
            _need(chain.preparations.resolve_native_inputs(chain.plan.native_inputs, receipt) == chain.baseline,
                  'accepted_original_preparation_changed')
        _need(state.same_evidence(chain.reconstruct_inputs()), 'accepted_head_changed')

    check()
    vendor = PowercalcVendor(hass, with_profiles=True)
    position, selected = _source_image(chain, evidence, state)
    await _settled_executor(hass, vendor.sources)
    check()
    prepared = await _settled_executor(hass, _prepare_profiles, receipt, selected)
    check()
    await _settled_executor(hass, _accepted_sources, chain, state, receipt)
    check()
    observed = await async_observe_discovery_inputs(hass, guard=check)
    check()
    _need(observed.version == receipt.version and observed.config_root == receipt.config_root,
          'accepted_native_root_changed')
    _documents(state, observed)
    vendor.bindings()
    removed = frozenset()
    if controlled:
        from .native_registry_receipts import _event_id
        from .controlled_powercalc_setup_receipts import validate_handoff
        from .controlled_powercalc_population import removed_group_indexes
        direction = 'forward' if position == 'proposed' else 'rollback'
        key = _event_id(['controlled_pc_population', chain.root_id, direction, 'receipt'])
        saved = chain.store.read(key)
        if saved is not None:
            handoff = validate_handoff(chain, [key, saved['digest']], direction)
            removed = removed_group_indexes(hass, captured, handoff, vendor)
    first = _sample(hass, vendor, selected, observed, captured, prepared,
                    removed_group_ids=removed)
    _need(bool(first) and all(row.source_verified for row in first), 'accepted_runtime_unverified')
    runtime_current(hass, captured, first, guard=check)
    # LAST await: final vendor/HA public code pins and FULL accepted graph in
    # ONE executor job. The suffix is synchronous native observation only.
    await _settled_executor(hass, _accepted_sources, chain, state, receipt, vendor)
    check()
    assert_discovery_managers_current(hass, observed, guard=check)
    _documents(state, observed)
    vendor.bindings()
    if removed:
        _need(removed == removed_group_indexes(hass, captured, handoff, vendor),
              'accepted_removed_group_index_changed')
    second = _sample(hass, vendor, selected, observed, captured, prepared,
                     removed_group_ids=removed)
    runtime_current(hass, captured, second, guard=check)
    _need(first == second, 'accepted_runtime_changed_during_observation')
    check()
    relationships = tuple(sorted((row.relation_kind, source, target, sign)
        for row in second if row.relation_kind is not None
        for source, target, sign in row.edges))
    settings = json.loads(selected.powercalc_sources.closed_graph_json)['energy_settings_sha256']
    return AcceptedPowercalcConsumers(receipt.version, evidence.sha256, chain.operation_id,
        chain.plan.fingerprint, state.head, position, _sha(captured.payload_json),
        second, relationships, settings)
