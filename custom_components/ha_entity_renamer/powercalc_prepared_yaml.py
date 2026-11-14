"""Typed SAME-ORIGINAL Powercalc YAML preparation; no activation capability."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os

from .native_input_baseline import _receipt_bytes
from .powercalc_candidate_sources import CandidatePowercalcSources
from .powercalc_source_binding import OriginalPowercalcSources
from .transaction import Refused
from .yaml_plan import Limits, _Reader
from .yaml_transaction import PreparedYAML, YAMLFile, _decode_yaml, _digest, _hash, _sha
from .yaml_validation import assert_proposal_matches, assert_validation_current


@dataclass(frozen=True)
class PreparedPowercalcYAML(PreparedYAML):
    original_sources_sha256: str
    candidate_sources_sha256: str

    @property
    def identity(self):
        payload = asdict(self)
        if self.receipt.checker_repairs_snapshot is None:
            payload['receipt'].pop('checker_repairs_snapshot', None)
        if self.receipt.source_links is None:
            payload['receipt'].pop('source_links', None)
        return 'powercalc_yaml:' + _digest(payload)

    def checked(self):
        if decode_powercalc_yaml(asdict(self)) != self:
            raise ValueError('noncanonical_prepared_powercalc_yaml')
        return self


def decode_powercalc_yaml(value):
    if type(value) is not dict or set(value) != {
            'receipt', 'files', 'original_sources_sha256', 'candidate_sources_sha256'}:
        raise ValueError('invalid_prepared_powercalc_yaml')
    original = _sha(value['original_sources_sha256'])
    candidate = _sha(value['candidate_sources_sha256'])
    base = _decode_yaml({key: value[key] for key in ('receipt', 'files')}, powercalc_mode=True)
    return PreparedPowercalcYAML(base.receipt, base.files, original, candidate)


def capture_powercalc_yaml(receipt, evidence, plan):
    """Read ORIGINAL files once, bound to the checker's typed candidate."""
    original, candidate = evidence.powercalc_sources, evidence.powercalc_candidate_sources
    if (type(original) is not OriginalPowercalcSources or type(candidate) is not CandidatePowercalcSources
            or original.receipt_json != _receipt_bytes(receipt)):
        raise ValueError('powercalc_yaml_original_required')
    candidate.assert_original(receipt, original)
    assert_proposal_matches(receipt, plan)
    assert_validation_current(receipt)
    reader = _Reader(receipt.config_root, Limits())
    try:
        rows = []
        for patch in plan.patches:
            before, info = reader.read(patch.path)
            if _hash(before) != patch.expected_sha256:
                raise Refused('yaml_source_changed')
            rows.append(YAMLFile(patch.path, before.hex(), patch.proposed_bytes.hex(),
                                 info.st_dev, info.st_ino))
        result = PreparedPowercalcYAML(receipt, tuple(rows), original.sha256, candidate.sha256).checked()
        assert_validation_current(receipt)
        return result
    finally:
        os.close(reader.root)


def assert_powercalc_yaml_composition(chain, change):
    """Reopen the immutable preparation; never upgrade a read-only chain."""
    from .powercalc_composition import PowercalcComposition
    from .native_template_state_yaml import PreparedMixedPowercalcYAML
    from .controlled_powercalc_composition import ControlledPowercalcComposition
    if type(change) not in (PreparedPowercalcYAML, PreparedMixedPowercalcYAML):
        raise Refused('powercalc_yaml_preparation_required')
    baseline, composition = chain.preparations.resolve_composition(chain.plan.native_inputs)
    if (baseline != chain.baseline
            or type(composition) is not (ControlledPowercalcComposition if type(change) is PreparedMixedPowercalcYAML else PowercalcComposition)
            or baseline.receipt != change.receipt
            or composition.evidence.powercalc_sources.sha256 != change.original_sources_sha256
            or composition.evidence.powercalc_candidate_sources.sha256 != change.candidate_sources_sha256
            or set(dict(composition.prepared_digests)) != {step.prepared_ref for step in chain.plan.steps}):
        raise Refused('powercalc_yaml_original_composition_changed')
    for reference, digest in composition.prepared_digests:
        chain.preparations.resolve(reference, digest)
    # Every candidate registry ID movement must be an explicit queued native
    # registry step, and every such step must occur in the candidate image.
    # Do not infer source-ID changes solely from the candidate Powercalc graph.
    from .native_registry import PreparedRegistryChange
    from .native_input_baseline import _envelope, _json
    from .prepared import decode_change
    documents = composition.evidence.powercalc_candidate_sources.documents.documents
    before, after = {}, {}
    for key, old, new in documents:
        old_doc, new_doc = _envelope(old, key, baseline.receipt.version), _envelope(new, key, baseline.receipt.version)
        if key in ('core.entity_registry', 'core.device_registry'):
            from copy import deepcopy
            kind = 'entity' if key == 'core.entity_registry' else 'device'
            member = 'entities' if kind == 'entity' else 'devices'
            if kind == 'entity':
                before = {row['id']: row['entity_id'] for row in old_doc['data']['entities']}
                after = {row['id']: row['entity_id'] for row in new_doc['data']['entities']}
            expected_doc = deepcopy(old_doc)
            rows = expected_doc['data'][member]
            seen = set()
            for step in chain.plan.steps:
                if step.adapter != 'registry':
                    continue
                digest = dict(composition.prepared_digests)[step.prepared_ref]
                prepared = decode_change(chain.preparations.resolve(step.prepared_ref, digest))
                if type(prepared) is not PreparedRegistryChange or prepared.kind != kind:
                    continue
                matches = [row for row in rows if row.get('id') == prepared.identity]
                if (len(matches) != 1 or prepared.identity in seen
                        or _json(matches[0]) != _json(prepared.before)):
                    raise Refused('powercalc_candidate_native_effect_unprepared')
                seen.add(prepared.identity)
                index = rows.index(matches[0])
                rows[index] = deepcopy(prepared.after)
                if kind == 'entity' and prepared.mapping:
                    rows.append(rows.pop(index))
            if _json(expected_doc) != _json(new_doc):
                raise Refused('powercalc_candidate_native_effect_unprepared')
        elif key == 'core.config_entries':
            from copy import deepcopy
            from .powercalc_group_metadata import PreparedPowercalcGroupMetadata
            from .helper_transaction import PreparedHelper
            from .helper_plan import snapshot_equal
            from .native_config_transition import _projection
            expected_doc = deepcopy(old_doc)
            seen = set()
            for step in chain.plan.steps:
                if step.adapter != 'helpers':
                    continue
                digest = dict(composition.prepared_digests)[step.prepared_ref]
                prepared = decode_change(chain.preparations.resolve(step.prepared_ref, digest))
                if (type(prepared) not in (PreparedPowercalcGroupMetadata, PreparedHelper)
                        or type(prepared) is PreparedHelper and
                           (type(composition) is not ControlledPowercalcComposition
                            or prepared.before.get('domain') not in (('template', 'homekit') if composition.external_source_proof else ('template',)))):
                    raise Refused('powercalc_candidate_native_effect_unprepared')
                prepared.plan()
                matches = [row for row in expected_doc['data']['entries']
                           if row.get('entry_id') == prepared.before['identity']['entry_id']]
                identity = prepared.before['identity']['entry_id']
                if len(matches) != 1 or identity in seen:
                    raise Refused('powercalc_candidate_native_effect_unprepared')
                seen.add(identity)
                if type(prepared) is PreparedPowercalcGroupMetadata:
                    if (matches[0].get('domain') != 'powercalc'
                            or _json(matches[0]['data']) != _json(prepared.before['data'])):
                        raise Refused('powercalc_candidate_native_effect_unprepared')
                    matches[0]['data'] = deepcopy(prepared.after['data'])
                else:
                    if not snapshot_equal(_projection(matches[0]), prepared.before):
                        raise Refused('powercalc_candidate_native_effect_unprepared')
                    matches[0]['options'] = deepcopy(prepared.after['options'])
            if _json(expected_doc) != _json(new_doc):
                raise Refused('powercalc_candidate_native_effect_unprepared')
        elif _json(old_doc) != _json(new_doc):
            raise Refused('powercalc_candidate_native_effect_unprepared')
    if set(before) != set(after):
        raise Refused('powercalc_candidate_registry_membership_changed')
    expected = {identity: (before[identity], after[identity]) for identity in before
                if before[identity] != after[identity]}
    actual = {}
    for step in chain.plan.steps:
        if step.adapter != 'registry':
            continue
        digest = dict(composition.prepared_digests)[step.prepared_ref]
        prepared = decode_change(chain.preparations.resolve(step.prepared_ref, digest))
        if type(prepared) is PreparedRegistryChange and prepared.kind == 'entity' and prepared.mapping:
            if prepared.identity in actual:
                raise Refused('powercalc_duplicate_registry_identity')
            actual[prepared.identity] = (prepared.before['entity_id'], prepared.after['entity_id'])
    if actual != expected:
        raise Refused('powercalc_candidate_registry_mapping_incomplete')
    old = json.loads(composition.evidence.powercalc_sources.closed_graph_json)
    new = json.loads(composition.evidence.powercalc_candidate_sources.sources.closed_graph_json)
    old_bindings = {origin: rows for origin, rows in old['bindings']}
    new_bindings = {origin: rows for origin, rows in new['bindings']}
    if set(old_bindings) != set(new_bindings):
        raise Refused('powercalc_candidate_dependency_membership_changed')
    for origin, old_rows in old_bindings.items():
        new_rows = new_bindings[origin]
        if len(old_rows) != len(new_rows):
            raise Refused('powercalc_candidate_dependency_membership_changed')
        for prior, target in zip(old_rows, new_rows, strict=True):
            if (prior[0], prior[2:]) != (target[0], target[2:]):
                raise Refused('powercalc_candidate_dependency_identity_changed')
            if prior[1] != target[1] and actual.get(prior[2]) != (prior[1], target[1]):
                raise Refused('powercalc_candidate_dependency_not_queued')
