"""Private durable full-state source composition, never a runtime capability.

Uses the existing ORIGINAL envelope and journal. Legacy composition bytes and
admission remain unchanged. Reopening rechecks the actual retained planner and
source seals; it does not authorize selective effects or qualify consumers.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import json
import re

from .native_input_baseline import NativeInputBaseline
from .native_template_composition import (_TYPES, _typed, _ledger, _history, _need,
    _SHA, _ID)
from .template_source_evidence import (OriginalTemplateStateSources,
    CandidateTemplateStateSources, seal_template_state_sources,
    seal_template_state_candidate)
from .template_state_program import StateConfigProgram
from .yaml_source_binding import OriginalSourceEvidence, seal_original_sources
from .yaml_plan import (Plan, FileSnapshot, DirectorySnapshot, IncludeEdge, FilePatch,
    Edit, Issue, VariableBindingProof, TemplateVariableBinding, PowercalcSourceInputs, _mapping)

_PROFILE = 'template_state_binding_v3'
_MIXED_PROFILE = 'template_mixed_state_binding_v1'
_TYPES.update({t.__name__: t for t in (OriginalTemplateStateSources,
    CandidateTemplateStateSources, StateConfigProgram, Plan, FileSnapshot, DirectorySnapshot,
    IncludeEdge, FilePatch, Edit, Issue, VariableBindingProof, TemplateVariableBinding, PowercalcSourceInputs)})


def _native(source, version):
    return {'version': version, 'projection': source.projection,
            'rows': None if source.rows is None else [list(row) for row in source.rows]}


@dataclass(frozen=True)
class TemplateStateComposition:
    evidence: OriginalSourceEvidence
    original_sources: OriginalTemplateStateSources
    candidate_sources: CandidateTemplateStateSources
    state_config_program: StateConfigProgram
    yaml_plan: Plan
    mapping: tuple[tuple[str, str], ...]
    prepared_digests: tuple[tuple[str, str], ...]
    source_history: dict
    consumer_history: dict
    coverage_sha256: str
    physical_consumers: tuple[tuple[str, str, str], ...] = ()

    @property
    def migration_ready(self):
        return False

    def _assert_changed_scope(self, receipt):
        _need(receipt.changed_domains == ('template',) and bool(receipt.proposed_files)
              and set(receipt.unsupported_domains) <= {'template'})

    def assert_baseline(self, baseline):
        _need(type(baseline) is NativeInputBaseline and type(self.yaml_plan) is Plan
              and self.yaml_plan.ready and self.yaml_plan.powercalc_inputs is None)
        receipt, evidence = baseline.receipt, self.evidence
        self._assert_changed_scope(receipt)
        _need(type(evidence) is OriginalSourceEvidence
              and evidence.powercalc_sources is None and evidence.powercalc_candidate_sources is None
              and evidence.template_sources is not None
              and evidence.template_candidate_sources is not None)
        _need(type(self.mapping) is tuple and bool(self.mapping)
              and all(type(row) is tuple and len(row) == 2 for row in self.mapping)
              and self.mapping == tuple(sorted(self.mapping))
              and len(dict(self.mapping)) == len(self.mapping))
        mapping = _mapping(dict(self.mapping))
        _ledger(evidence.ledger, 1)
        original = evidence.template_sources
        _ledger(original.ledger, 2)
        rebuilt = seal_original_sources(receipt, self.yaml_plan, evidence.ledger,
            {'version': receipt.version, 'domains': [
                [row.domain, row.projection, None if row.rows is None else [list(r) for r in row.rows]]
                for row in evidence.domains]},
            template_ledger=original.ledger,
            template_native=_native(original, receipt.version),
            template_candidate_native=_native(evidence.template_candidate_sources.sources,
                                             receipt.version))
        _need(rebuilt == evidence and type(self.original_sources) is OriginalTemplateStateSources
              and type(self.candidate_sources) is CandidateTemplateStateSources
              and self.original_sources.ledger == original.ledger
              and self.original_sources.source_matched
              and self.candidate_sources.sources.source_matched)
        before = seal_template_state_sources(receipt, original.ledger,
            json.loads(self.original_sources.native_json), plan=self.yaml_plan, mapping=mapping)
        after = seal_template_state_candidate(receipt, before,
            json.loads(self.candidate_sources.sources.native_json), plan=self.yaml_plan, mapping=mapping)
        _need(before == self.original_sources and after == self.candidate_sources)
        self.candidate_sources.assert_original(receipt, self.original_sources)
        _need(type(self.state_config_program) is StateConfigProgram)
        self.state_config_program.assert_sources(self.original_sources, self.candidate_sources,
                                                plan=self.yaml_plan, mapping=mapping)
        _need(type(self.prepared_digests) is tuple and bool(self.prepared_digests)
              and self.prepared_digests == tuple(sorted(self.prepared_digests)))
        seen = set()
        for row in self.prepared_digests:
            _need(type(row) is tuple and len(row) == 2 and type(row[0]) is str
                  and _ID.fullmatch(row[0]) and row[0] not in seen
                  and type(row[1]) is str and _SHA.fullmatch(row[1]))
            seen.add(row[0])
        _need(type(self.coverage_sha256) is str and _SHA.fullmatch(self.coverage_sha256)
              and self.coverage_sha256 == self.state_config_program.sha256)
        _history(self.source_history)
        _history(self.consumer_history)
        from .template_physical_identity import is_physical_definition, physical_template_key
        expected = {(row.domain.removeprefix('template.'), row.native_key)
                    for row in original.ledger.definitions if is_physical_definition(row)}
        physical = self.physical_consumers
        _need(type(physical) is tuple and all(type(row) is tuple and len(row) == 3
              and all(type(v) is str for v in row) for row in physical)
              and len(physical) == len(expected)
              and {(domain, key) for domain, key, _ in physical} == expected
              and len({entity_id for _, _, entity_id in physical}) == len(expected))
        for domain, key, entity_id in physical:
            _need(re.fullmatch(domain + r'\.[a-z0-9_]+', entity_id))
            definition = next(row for row in original.ledger.definitions
                              if (row.domain, row.native_key) == ('template.' + domain, key))
            _need(key == physical_template_key(definition.domain, definition.origin.path,
                                              definition.origin.start_line))


@dataclass(frozen=True)
class MixedTemplateStateComposition(TemplateStateComposition):
    """Separate selective mixed profile, carrying the SAME ORIGINAL seals."""

    def _assert_changed_scope(self, receipt):
        from .yaml_transaction import _assert_mixed_state_scope
        _assert_mixed_state_scope(receipt)
        _need(bool(receipt.proposed_files))


def encode_state_composition(value, baseline):
    return _encode_state_composition(value, baseline, TemplateStateComposition, _PROFILE)


def encode_mixed_state_composition(value, baseline):
    return _encode_state_composition(value, baseline, MixedTemplateStateComposition, _MIXED_PROFILE)


def _encode_state_composition(value, baseline, cls, profile):
    _need(type(value) is cls)
    result = {'schema': 3, 'profile': profile,
        'evidence': _typed(value.evidence, OriginalSourceEvidence, encode=True),
        'original_sources': _typed(value.original_sources, OriginalTemplateStateSources, encode=True),
        'candidate_sources': _typed(value.candidate_sources, CandidateTemplateStateSources, encode=True),
        'state_config_program': _typed(value.state_config_program, StateConfigProgram, encode=True),
        'yaml_plan': _typed(value.yaml_plan, Plan, encode=True),
        'mapping': _typed(value.mapping, tuple[tuple[str, str], ...], encode=True),
        'prepared_digests': _typed(value.prepared_digests, tuple[tuple[str, str], ...], encode=True),
        'source_history': _history(value.source_history),
        'consumer_history': _history(value.consumer_history),
        'coverage_sha256': value.coverage_sha256,
        'physical_consumers': _typed(value.physical_consumers, tuple[tuple[str, str, str], ...], encode=True)}
    value.assert_baseline(baseline)
    return result


def decode_state_composition(payload, baseline):
    return _decode_state_composition(payload, baseline, TemplateStateComposition, _PROFILE)


def decode_mixed_state_composition(payload, baseline):
    return _decode_state_composition(payload, baseline, MixedTemplateStateComposition, _MIXED_PROFILE)


def _decode_state_composition(payload, baseline, cls, profile):
    _need(type(payload) is dict and set(payload) == {'schema', 'profile', 'evidence',
        'original_sources', 'candidate_sources', 'state_config_program', 'yaml_plan', 'mapping', 'prepared_digests',
        'source_history', 'consumer_history', 'coverage_sha256', 'physical_consumers'}
        and type(payload['schema']) is int and payload['schema'] == 3
        and payload['profile'] == profile)
    value = cls(
        _typed(payload['evidence'], OriginalSourceEvidence, encode=False),
        _typed(payload['original_sources'], OriginalTemplateStateSources, encode=False),
        _typed(payload['candidate_sources'], CandidateTemplateStateSources, encode=False),
        _typed(payload['state_config_program'], StateConfigProgram, encode=False),
        _typed(payload['yaml_plan'], Plan, encode=False),
        _typed(payload['mapping'], tuple[tuple[str, str], ...], encode=False),
        _typed(payload['prepared_digests'], tuple[tuple[str, str], ...], encode=False),
        _history(payload['source_history']), _history(payload['consumer_history']),
        payload['coverage_sha256'],
        _typed(payload['physical_consumers'], tuple[tuple[str, str, str], ...], encode=False))
    value.assert_baseline(baseline)
    return value


def _retained_fields(value):
    """Detach every accepted field, retaining exact types and immutable bytes.

    No hash-only shortcut, shared mutable list/dict or executable property is
    retained. The ordinary typed codec authenticates the initial structure.
    """
    kind = type(value)
    if value is None or kind in (str, bytes, int, bool):
        return (kind, value)
    if kind in (tuple, list):
        return (kind, tuple(_retained_fields(item) for item in value))
    if kind is dict:
        return (kind, frozenset((_retained_fields(key), _retained_fields(item))
                                for key, item in value.items()))
    if kind in (TemplateStateComposition, MixedTemplateStateComposition) or kind in _TYPES.values():
        return (kind, tuple((field.name, _retained_fields(getattr(value, field.name)))
                             for field in fields(kind)))
    from .transaction import Refused
    raise Refused('template_state_retained_type_changed')


def retain_state_composition(value, baseline):
    """Validate once, then compare ALL original fields at each live fence.

    Only immutable source evidence is retained. Authorization, live HA graphs,
    accepted receipt prefixes and disk observations are never cached here.
    """
    from .native_template_yaml_server import _baseline_stamp
    from .transaction import Refused
    from .native_template_composition import encode_composition
    wire = encode_composition(value, baseline)
    original, original_baseline = _retained_fields(value), _baseline_stamp(baseline)
    def check():
        if (_retained_fields(value) != original
                or _baseline_stamp(baseline) != original_baseline):
            raise Refused('template_state_retained_original_changed')
    return wire, check
