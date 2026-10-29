"""Private typed entry/modern YAML evidence codec; never authority or live tokens.

Stored only inside the existing immutable native_input_baseline preparation.
Its existing PreparedStore canonical envelope supplies size/structure limits;
this codec adds no unrelated record/string/depth caps.
Powercalc remains unsupported; installed admission checks the precise Template slice.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from .native_input_baseline import NativeInputBaseline
from .template_source_evidence import (OriginalTemplateSources, CandidateTemplateSources,
                                       seal_template_sources)
from .yaml_source_binding import OriginalSourceEvidence, NativeSourceDomain
from .yaml_source_ledger import (SourceLedger, Definition, Finding, Origin, IncludeHop)

_SHA = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
_TYPES = {t.__name__: t for t in (OriginalSourceEvidence, NativeSourceDomain,
    SourceLedger, Definition, Finding, Origin, IncludeHop, OriginalTemplateSources,
    CandidateTemplateSources)}
_PROFILE = 'template_numeric_entry_reverse_v1'
_MIXED_PROFILE = 'template_dependent_yaml_v1'


def _need(value):
    if not value:
        raise ValueError('template_composition_rejected') from None


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _hints(cls):
    # These two TYPE_CHECKING-only families are explicitly unsupported, never
    # imported or decoded by this narrow entry codec.
    return get_type_hints(cls, localns={'OriginalPowercalcSources': type(None),
                                      'CandidatePowercalcSources': type(None)})


def _typed(value, annotation, *, encode):
    """Only declared scalar/tuple/dataclass fields; no arbitrary object decoder."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        for option in args:
            try:
                return _typed(value, option, encode=encode)
            except ValueError:
                pass
        _need(False)
    if annotation is type(None):
        _need(value is None)
        return None
    if annotation in (str, int, bool):
        _need(type(value) is annotation)
        return value
    if annotation is bytes:
        if encode:
            _need(type(value) is bytes)
            return base64.b64encode(value).decode('ascii')
        _need(type(value) is str)
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            _need(False)
    if origin is tuple:
        _need(type(value) is (tuple if encode else list))
        if len(args) == 2 and args[1] is Ellipsis:
            annotations = (args[0],) * len(value)
        else:
            _need(len(args) == len(value))
            annotations = args
        result = [_typed(v, a, encode=encode)
                  for v, a in zip(value, annotations, strict=True)]
        return result if encode else tuple(result)
    _need(annotation in _TYPES.values())
    cls = annotation
    names = {f.name for f in fields(cls)}
    if encode:
        _need(type(value) is cls)
        values = {name: getattr(value, name) for name in names}
    else:
        _need(type(value) is dict and set(value) == {'type', 'fields'}
              and value['type'] == cls.__name__ and type(value['fields']) is dict
              and set(value['fields']) == names)
        values = value['fields']
    decoded = {name: _typed(values[name], typ, encode=encode)
               for name, typ in _hints(cls).items()}
    return {'type': cls.__name__, 'fields': decoded} if encode else cls(**decoded)


def _ledger(ledger, schema):
    _need(type(ledger) is SourceLedger and ledger.schema == schema
          and type(ledger.original_graph_sha256) is str
          and _SHA.fullmatch(ledger.original_graph_sha256)
          and not ledger.findings)
    payload = {k: v for k, v in asdict(ledger).items() if k != 'sha256'}
    _need(ledger.sha256 == _hash(payload))
    for row in ledger.definitions:
        _need(row.disposition == 'candidate' and row.native_key is not None
              and _SHA.fullmatch(row.source_ast_sha256))
        for origin in (row.origin, row.key_origin):
            _need(origin.path and not origin.path.startswith('/')
                  and '..' not in origin.path.split('/')
                  and all(type(n) is int and n >= 0 for n in
                    (origin.start_line, origin.start_column, origin.end_line, origin.end_column)))


def _history(value):
    _need(type(value) is dict and set(value) ==
          {'schema', 'state_ids', 'statistic_ids', 'content'}
          and type(value['schema']) is int and value['schema'] == 1)
    for key in ('state_ids', 'statistic_ids'):
        ids = value[key]
        _need(type(ids) is list
              and all(type(i) is int and i >= 1 for i in ids)
              and ids == sorted(set(ids)))
    rows = value['content']
    _need(type(rows) is dict and set(rows) == {'states', 'short_term', 'hourly', 'attributes'})
    for name, row in rows.items():
        expected = {'count', 'sha256'} | ({'ceiling'} if name != 'attributes' else set())
        _need(type(row) is dict and set(row) == expected
              and type(row['count']) is int and row['count'] >= 0
              and type(row['sha256']) is str and _SHA.fullmatch(row['sha256']))
        if name != 'attributes':
            n = row['ceiling']
            _need(n is None or (type(n) is int and n >= 1))
    return deepcopy(value)


@dataclass(frozen=True)
class TemplateComposition:
    evidence: OriginalSourceEvidence
    prepared_digests: tuple[tuple[str, str], ...]
    source_history: dict
    consumer_history: dict
    coverage_sha256: str
    physical_consumers: tuple[tuple[str, str, str], ...] = ()

    def _assert_changed_scope(self, receipt):
        _need(receipt.changed_domains == ('template',)
              and len(receipt.runtime_expected) == 1
              and receipt.runtime_expected[0][0] == 'template'
              and None not in receipt.runtime_expected[0][1:]
              and receipt.runtime_expected[0][1] != receipt.runtime_expected[0][2])

    def assert_baseline(self, baseline):
        _need(type(baseline) is NativeInputBaseline)
        receipt = baseline.receipt
        evidence = self.evidence
        _need(type(evidence) is OriginalSourceEvidence
              and evidence.powercalc_sources is None and evidence.powercalc_candidate_sources is None
              and receipt.ready_for_apply)
        # Existing entry-only compositions retain their empty-YAML profile.
        # The YAML server may persist the SAME ORIGINAL plus its sealed
        # candidate for the qualified modern Template graph only.
        if receipt.proposed_files or receipt.changed_domains:
            _need(bool(receipt.proposed_files) and evidence.template_candidate_sources is not None)
            self._assert_changed_scope(receipt)
        evidence.assert_receipt(receipt)
        _ledger(evidence.ledger, 1)
        _need(tuple(row.domain for row in evidence.domains) == ('automation', 'script'))
        old = {domain: before for domain, before, _ in receipt.runtime_expected}
        for row in evidence.domains:
            _need(type(row) is NativeSourceDomain)
            if row.projection is None:
                _need(row.rows is None)
            else:
                _need(type(row.projection) is str and _SHA.fullmatch(row.projection)
                      and type(row.rows) is tuple)
                for item in row.rows:
                    _need(type(item) is tuple and len(item) == 3 and type(item[0]) is str
                          and all(type(x) is str and _SHA.fullmatch(x) for x in item[1:]))
                evidence.ledger.match_native_keys(row.domain, tuple(item[0] for item in row.rows))
            _need(row.domain not in old or old[row.domain] == row.projection)
        original = evidence.template_sources
        _need(type(original) is OriginalTemplateSources)
        _ledger(original.ledger, 2)
        _need(original.ledger.original_graph_sha256 == evidence.ledger.original_graph_sha256)
        native = {'version': receipt.version, 'projection': original.projection,
                  'rows': None if original.rows is None else [list(r) for r in original.rows]}
        _need(seal_template_sources(receipt, original.ledger, native) == original)
        candidate = evidence.template_candidate_sources
        if candidate is not None:
            _need(type(candidate) is CandidateTemplateSources)
            candidate.assert_original(receipt, original)
            other = candidate.sources
            _need(seal_template_sources(receipt, other.ledger,
                {'version': receipt.version, 'projection': other.projection,
                 'rows': None if other.rows is None else [list(r) for r in other.rows]}) == other)
        _need(type(self.prepared_digests) is tuple and self.prepared_digests
              and self.prepared_digests == tuple(sorted(self.prepared_digests)))
        seen = set()
        for row in self.prepared_digests:
            _need(type(row) is tuple and len(row) == 2 and type(row[0]) is str
                  and _ID.fullmatch(row[0]) and row[0] not in seen
                  and type(row[1]) is str and _SHA.fullmatch(row[1]))
            seen.add(row[0])
        _need(type(self.coverage_sha256) is str and _SHA.fullmatch(self.coverage_sha256))
        _history(self.source_history)
        _history(self.consumer_history)
        from .template_physical_identity import is_physical_definition, physical_template_key
        expected = {(row.domain.removeprefix('template.'), row.native_key)
                    for row in original.ledger.definitions if is_physical_definition(row)}
        _need(type(self.physical_consumers) is tuple
              and all(type(row) is tuple and len(row) == 3
                      and all(type(value) is str for value in row) for row in self.physical_consumers)
              and len(self.physical_consumers) == len(expected)
              and {(domain, key) for domain, key, _ in self.physical_consumers} == expected
              and len({entity_id for _, _, entity_id in self.physical_consumers}) == len(expected))
        for domain, key, entity_id in self.physical_consumers:
            _need(type(entity_id) is str and re.fullmatch(domain + r'\.[a-z0-9_]+', entity_id))
            definition = next(row for row in original.ledger.definitions
                              if (row.domain, row.native_key) == ('template.' + domain, key))
            _need(key == physical_template_key(definition.domain, definition.origin.path,
                                              definition.origin.start_line))


@dataclass(frozen=True)
class MixedTemplateComposition(TemplateComposition):
    """Separate durable profile; historical entry/YAML bytes remain unchanged."""

    def _assert_changed_scope(self, receipt):
        domains = receipt.changed_domains
        _need(type(domains) is tuple and 'template' in domains
              and set(domains) <= {'automation', 'script', 'template'}
              and len(domains) in (2, 3)
              and tuple(sorted(set(domains))) == domains
              and tuple(row[0] for row in receipt.runtime_expected) == domains
              and all(None not in row[1:] and row[1] != row[2]
                      for row in receipt.runtime_expected))


def encode_composition(composition, baseline):
    from .native_template_state_composition import TemplateStateComposition, encode_state_composition
    if type(composition) is TemplateStateComposition:
        return encode_state_composition(composition, baseline)
    _need(type(composition) in (TemplateComposition, MixedTemplateComposition))
    # Type-check fields before semantic validation, including unsupported families.
    evidence = _typed(composition.evidence, OriginalSourceEvidence, encode=True)
    composition.assert_baseline(baseline)
    profile = _MIXED_PROFILE if type(composition) is MixedTemplateComposition else _PROFILE
    result = {'schema': 1, 'profile': profile, 'evidence': evidence,
            'prepared_digests': [list(row) for row in composition.prepared_digests],
            'source_history': _history(composition.source_history),
            'consumer_history': _history(composition.consumer_history),
            'coverage_sha256': composition.coverage_sha256}
    if composition.physical_consumers:
        result['schema'] = 2
        result['physical_consumers'] = [list(row) for row in composition.physical_consumers]
    return result


def decode_composition(payload, baseline):
    if type(payload) is dict and payload.get('profile') == 'template_state_binding_v3':
        from .native_template_state_composition import decode_state_composition
        return decode_state_composition(payload, baseline)
    _need(type(payload) is dict and type(payload.get('schema')) is int
          and payload['schema'] in (1, 2))
    extra = {'physical_consumers'} if payload['schema'] == 2 else set()
    _need(set(payload) == {'schema', 'profile', 'evidence', 'prepared_digests',
        'source_history', 'consumer_history', 'coverage_sha256'} | extra
        and payload['profile'] in (_PROFILE, _MIXED_PROFILE))
    physical = (_typed(payload['physical_consumers'], tuple[tuple[str, str, str], ...], encode=False)
                if payload['schema'] == 2 else ())
    _need(payload['schema'] == 1 or bool(physical))
    digests = _typed(payload['prepared_digests'], tuple[tuple[str, str], ...], encode=False)
    cls = MixedTemplateComposition if payload['profile'] == _MIXED_PROFILE else TemplateComposition
    value = cls(_typed(payload['evidence'], OriginalSourceEvidence, encode=False),
        digests, _history(payload['source_history']), _history(payload['consumer_history']),
        payload['coverage_sha256'], physical)
    value.assert_baseline(baseline)
    return value
