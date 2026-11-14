"""Read-only Template consumer proof at an accepted ORIGINAL receipt-chain head.

No rebaseline, setup, reload, render, journal, new authority or mutation. The
same proposed checker supplies YAML semantic digests before any live effect.
This component does not make Template transaction/lifecycle support complete.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
import inspect
import json

# Reuse ca6ce4f or later: exact RenderInfo filters and native source/code pins.
from .native_template_consumers import _Native, _need, _sample, TemplateConsumerRow
from .native_discovery_inputs import (
    async_observe_discovery_inputs, assert_discovery_managers_current,
)
from .native_registry_receipts import RegistryReceiptChain
from .yaml_source_binding import OriginalSourceEvidence
from .yaml_validation import _settled_executor


@dataclass(frozen=True)
class AcceptedTemplateConsumers:
    version: str
    evidence_sha256: str
    operation_id: str
    fingerprint: str
    head: str
    yaml_position: str
    rows: tuple[TemplateConsumerRow, ...]
    tokens: tuple

    @property
    def migration_ready(self):
        return False

    @property
    def family_complete(self):
        return False


def _source_image(receipt, evidence, state):
    """Select only immutable original/proposed hashes; never adopt live YAML.

    All non-storage graph files participate, including ancillary !secret inputs.
    Reverse may have new inodes: accepted receipt replay, not a hash whitelist,
    proves those identities before this semantic selector is called.
    """
    original = {path: digest for path, digest, *_ in receipt.source_files
                if not path.startswith('.storage/')}
    proposed = dict(original)
    edits = dict(receipt.proposed_files)
    _need(set(edits) <= set(original), 'template_candidate_graph_unverified')
    proposed.update(edits)
    current = {path: row[0] for path, row in state.sources().items()
               if not path.startswith('.storage/')}
    if current == original:
        return 'original', evidence
    _need(current == proposed and current != original,
          'template_candidate_image_unverified')
    candidate = evidence.template_candidate_sources
    from .template_source_evidence import CandidateTemplateSources
    _need(type(candidate) is CandidateTemplateSources,
          'template_candidate_sources_required')
    candidate.assert_original(receipt, evidence.template_sources)
    return 'proposed', replace(evidence, template_sources=candidate.sources)


def _documents(state, observed):
    sources, documents = state.sources(), state.documents()
    _need(set(documents) == {row.key for row in observed.files},
          'template_accepted_native_inventory_changed')
    for row in observed.files:
        expected = sources.get('.storage/' + row.key)
        _need(expected is not None
              and [hashlib.sha256(row.raw).hexdigest(), *row.identity] == expected[:4]
              and (expected[4] is None or [row.uid, row.gid] == expected[4:])
              and json.loads(row.manager_json) == documents[row.key]['data'],
              'template_accepted_native_sources_changed')


async def async_observe_accepted_template_consumers(hass, chain, evidence, *, guard):
    """Observe a completed durable prefix, including fresh reverse, without adoption.

    The caller holds existing coordinator/editor/lifecycle scopes. Pending
    intents refuse. Reconstructing the SAME chain closes stale-head/preparation
    loss at every checkpoint. Results are detached facts, never effect grants.
    """
    _need(type(chain) is RegistryReceiptChain
          and type(evidence) is OriginalSourceEvidence,
          'template_accepted_chain_required')
    receipt = chain.original_receipt
    evidence.assert_receipt(receipt)
    state = chain.reconstruct_inputs()

    def check():
        value = guard()
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            _need(False, 'synchronous_guard_required')
        _need(value is None or value is True, 'guard_rejected')
        evidence.assert_receipt(chain.original_receipt)
        _need(state.same_evidence(chain.reconstruct_inputs()),
              'template_accepted_head_changed')

    check()
    native = _Native(hass)
    _need(native.version == receipt.version, 'native_version_changed')
    await _settled_executor(hass, native.sources)
    check()
    await _settled_executor(hass, state.assert_current, receipt)
    check()
    observed = await async_observe_discovery_inputs(hass, guard=check)
    check()
    _documents(state, observed)
    position, selected = _source_image(receipt, evidence, state)
    native.bindings()
    first = _sample(native, selected, observed)
    await asyncio.sleep(0)
    check()
    await _settled_executor(hass, native.sources)
    check()
    # LAST await is the full accepted graph fence, after source-pin IO. No later
    # await can let a foreign source change hide behind a previously good fence.
    await _settled_executor(hass, state.assert_current, receipt)
    check()
    assert_discovery_managers_current(hass, observed, guard=check)
    _documents(state, observed)
    native.bindings()
    second = _sample(native, selected, observed)
    _need(first == second, 'native_consumer_observation_changed')
    check()
    return AcceptedTemplateConsumers(native.version, evidence.sha256,
        chain.operation_id, chain.plan.fingerprint, state.head, position, *second)
