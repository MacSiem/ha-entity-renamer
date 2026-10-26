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


def _literal_documents(planned, accepted, original, chain):
    """Full images, with ONLY proven native own-call modified_at stamps.

    The chain has already replayed and validated each complete native effect.
    Planned metadata must retain its literal ORIGINAL value. It cannot prescribe
    a wildcard. Only a prepared row's native timestamp may be the value in the
    immutable accepted receipt. Foreign writes still fail the whole-source CAS.
    """
    expected = deepcopy(planned)
    owned = {key: set() for key in _KEYS}
    for step, change in zip(chain.plan.steps, chain.changes, strict=True):
        if step.adapter == 'registry':
            owned['core.' + change.kind + '_registry'].add(change.identity)
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
            if ('modified_at' in row and 'modified_at' in before[uid]
                    and 'modified_at' in actual[uid]
                    and row['modified_at'] == before[uid]['modified_at']):
                row['modified_at'] = actual[uid]['modified_at']
    return expected == accepted


def _source_image(chain, evidence, state):
    receipt = chain.original_receipt
    original_documents = {row.key: _envelope(row.raw, row.key, receipt.version)
                          for row in chain.baseline.files}
    original_files = {path: digest for path, digest, *_ in receipt.source_files
                      if path not in {'.storage/' + key for key in _KEYS}}
    current_files = {path: row[0] for path, row in state.sources().items()
                     if path not in {'.storage/' + key for key in _KEYS}}
    accepted = state.documents()
    if (current_files == original_files
            and _literal_documents(original_documents, accepted, original_documents, chain)):
        return 'original', evidence
    candidate = evidence.powercalc_candidate_sources
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
    if current_files == proposed_files and native_proposed:
        return 'proposed', replace(evidence, powercalc_sources=candidate.sources)
    # An acknowledged native prefix may precede the YAML-last step. It is
    # observable only when BOTH independently compiled Powercalc graphs are
    # literally identical; a source-ID half-migration cannot use this branch.
    if (current_files == original_files and native_proposed
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
    receipt = chain.original_receipt
    evidence.assert_receipt(receipt)
    _need(type(evidence.powercalc_sources) is OriginalPowercalcSources
          and evidence.powercalc_sources.closed_graph_json is not None
          and captured.version == receipt.version == '2026.10.2',
          'accepted_profile_scope_required')
    state = chain.reconstruct_inputs()  # Pending intent refuses, never sampled away.

    def check():
        _guard(guard)
        evidence.assert_receipt(chain.original_receipt)
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
    first = _sample(hass, vendor, selected, observed, captured, prepared)
    _need(bool(first) and all(row.source_verified for row in first), 'accepted_runtime_unverified')
    runtime_current(hass, captured, first, guard=check)
    # LAST await: final vendor/HA public code pins and FULL accepted graph in
    # ONE executor job. The suffix is synchronous native observation only.
    await _settled_executor(hass, _accepted_sources, chain, state, receipt, vendor)
    check()
    assert_discovery_managers_current(hass, observed, guard=check)
    _documents(state, observed)
    vendor.bindings()
    second = _sample(hass, vendor, selected, observed, captured, prepared)
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
