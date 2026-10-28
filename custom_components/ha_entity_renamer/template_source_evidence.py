"""ORIGINAL physical Template definitions + same native checker's digests.

This is source evidence only. No live consumer/listener, ConfigEntry coverage,
reload readiness or migration authority follows from a matched source inventory.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
from .yaml_source_ledger import SourceLedger

_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _need(value):
    if not value:
        raise ValueError('original_template_source_evidence_rejected') from None


@dataclass(frozen=True)
class OriginalTemplateSources:
    ledger: SourceLedger
    projection: str | None
    rows: tuple[tuple[str, str, str], ...] | None
    source_matched: bool

    @property
    def runtime_verified(self):
        return False

    @property
    def migration_ready(self):
        return False

    @property
    def sha256(self):
        raw = json.dumps({'ledger': self.ledger.sha256, 'projection': self.projection,
            'rows': self.rows, 'source_matched': self.source_matched},
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        return hashlib.sha256(raw).hexdigest()


def seal_template_sources(receipt, ledger, native):
    """Private output of the SAME ORIGINAL staging/check; never a decoder grant.

    Caller binds ledger.original_graph_sha256 to its actual Plan before this
    call, as existing seal_original_sources does. Source membership is checked
    again here against the exact receipt's physical files. No secrets exported.
    """
    _need(type(ledger) is SourceLedger and ledger.schema == 2)
    sources = {row[0]: row[1:] for row in receipt.source_files}
    _need(all(row.origin.path in sources and row.key_origin.path in sources
              for row in ledger.definitions))
    _need(type(native) is dict and set(native) == {'version', 'projection', 'rows'}
          and native['version'] == receipt.version)
    projection, rows = native['projection'], native['rows']
    if projection is None:
        _need(rows is None)
        return OriginalTemplateSources(ledger, None, None, False)
    _need(type(projection) is str and _SHA.fullmatch(projection) and type(rows) is list)
    for row in rows:
        _need(type(row) is list and len(row) == 3
              and type(row[0]) is str and row[0].startswith('template.')
              and type(row[1]) is str and bool(row[1])
              and type(row[2]) is str and _SHA.fullmatch(row[2]))
    keys = [(row[0], row[1]) for row in rows]
    _need(keys == sorted(set(keys)))
    matched = not ledger.findings
    if matched:
        expected = sorted((row.domain, row.native_key) for row in ledger.definitions)
        _need(all(row.disposition == 'candidate' and row.native_key is not None
                  for row in ledger.definitions) and keys == expected)
    return OriginalTemplateSources(ledger, projection,
        tuple(tuple(row) for row in rows), matched)


@dataclass(frozen=True)
class CandidateTemplateSources:
    """Private proposed checker output, bound to the immutable ORIGINAL receipt.

    No live state is learned. This does not authorize an effect or make Template
    activation supported. Source identities must be unchanged by this proposal.
    """
    original_receipt_json: bytes
    original_sources_sha256: str
    proposed_files: tuple[tuple[str, str], ...]
    sources: OriginalTemplateSources

    @property
    def sha256(self):
        return hashlib.sha256(json.dumps([
            self.original_receipt_json.decode('utf-8'), self.original_sources_sha256,
            self.proposed_files, self.sources.sha256], sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    @property
    def migration_ready(self):
        return False

    def assert_original(self, receipt, original):
        from .native_input_baseline import _receipt_bytes
        _need(type(original) is OriginalTemplateSources
              and self.original_receipt_json == _receipt_bytes(receipt)
              and self.original_sources_sha256 == original.sha256
              and self.proposed_files == receipt.proposed_files
              and self.sources.ledger == original.ledger)


def seal_template_candidate(receipt, original, native):
    """Same proposed native checker + ORIGINAL source inventory; no live parser.

    The existing seal's caller has already bound Plan and ORIGINAL byte bundle.
    A proposal that adds/removes/changes source identity cannot use this subset.
    Unsupported/duplicate source inventories retain their unmatched disposition.
    """
    from .native_input_baseline import _receipt_bytes
    _need(type(original) is OriginalTemplateSources)
    candidate = seal_template_sources(receipt, original.ledger, native)
    if original.rows is not None and candidate.rows is not None:
        _need(tuple(row[:2] for row in candidate.rows)
              == tuple(row[:2] for row in original.rows))
    else:
        _need(original.rows is None and candidate.rows is None)
    return CandidateTemplateSources(_receipt_bytes(receipt), original.sha256,
                                    receipt.proposed_files, candidate)


@dataclass(frozen=True)
class OriginalTemplateStateSources:
    """Explicit v3 source contract; deliberately a different legacy-incompatible type.

    Serialized declarations never confer actual consumer/writer capability.
    native_json seals normalized config digests, complete inventory, runtime
    feature obligations and actual planner proof. OriginalTemplateSources and
    all its historical hashes stay unchanged.
    """
    ledger: SourceLedger
    native_json: bytes
    source_matched: bool

    @property
    def sha256(self):
        return hashlib.sha256(self.ledger.sha256.encode() + b'\0' + self.native_json).hexdigest()

    @property
    def runtime_verified(self):
        return False

    @property
    def migration_ready(self):
        return False


def _state_profile_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def seal_template_state_sources(receipt, ledger, native, *, plan, mapping):
    """Internal opt-in handshake, bound to actual Plan and receipt, source only.

    Caller must bind ledger.original_graph_sha256 to its ORIGINAL byte graph,
    as seal_original_sources already does. Legacy seal_template_sources rejects
    this distinct strict schema; no legacy runtime consumer accepts this type.
    """
    from .yaml_plan import Plan, _mapping
    _need(type(plan) is Plan and plan.ready and type(ledger) is SourceLedger and ledger.schema == 2)
    _need(type(mapping) is dict and _mapping(mapping) == mapping)
    _need(tuple(sorted((p.path, hashlib.sha256(p.proposed_bytes).hexdigest())
                      for p in plan.patches)) == receipt.proposed_files)
    _need(type(native) is dict and set(native) == {
        'profile', 'version', 'projection', 'rows', 'families', 'states', 'reference_proof'})
    _need(native['profile'] == 'template_state_source_v3' and native['version'] == receipt.version)
    # Reuse the established exact physical inventory association. No runtime
    # meaning follows from this legacy-shaped view of just the source rows.
    source = seal_template_sources(receipt, ledger, {
        key: native[key] for key in ('version', 'projection', 'rows')})
    _need(type(native['families']) is list)
    for row in native['families']:
        _need(type(row) is list and len(row) == 3 and type(row[0]) is str
              and type(row[1]) is str and row[2] in ('state', 'trigger'))
    _need([r[:2] for r in native['families']] == [r[:2] for r in native['rows']])
    _need(type(native['states']) is list)
    state_keys = []
    for row in native['states']:
        _need(type(row) is list and len(row) == 4
              and row[0] in ('template.sensor', 'template.binary_sensor')
              and type(row[1]) is str and bool(row[1])
              and type(row[2]) is str and _SHA.fullmatch(row[2]))
        state_keys.append((row[0], row[1]))
        features = row[3]
        _need(type(features) is dict and set(features) == {
            'slots', 'variables', 'block_actions', 'block_conditions', 'availability', 'delay_on', 'delay_off',
            'auto_off', 'pending_delay_proof_required'})
        _need(all(type(features[k]) is bool for k in features if k != 'slots')
              and features['pending_delay_proof_required'] == any(
                  features[k] for k in ('delay_on', 'delay_off', 'auto_off')))
        _need(type(features['slots']) is list)
        paths = []
        for slot in features['slots']:
            _need(type(slot) is dict and set(slot) == {'path', 'body_sha256',
                'names', 'calls', 'filters', 'tests', 'attributes', 'this_identity',
                'this_memory', 'time', 'local_bindings'})
            _need(type(slot['path']) is list and bool(slot['path']) and all(
                type(p) is str or type(p) is int and p >= 0 for p in slot['path']))
            paths.append(tuple(slot['path']))
            _need(type(slot['body_sha256']) is str and _SHA.fullmatch(slot['body_sha256']))
            _need(all(type(slot[k]) is bool for k in ('this_identity', 'this_memory')))
            for key in ('names', 'calls', 'filters', 'tests', 'attributes', 'time', 'local_bindings'):
                _need(type(slot[key]) is list and all(type(v) is str and bool(v) for v in slot[key])
                      and slot[key] == sorted(set(slot[key])))
            _need(set(slot['time']) <= set(slot['names']))
        _need(len(paths) == len(set(paths)))
    _need(state_keys == sorted(set(state_keys)) and state_keys == sorted(
        (r[0], r[1]) for r in native['families'] if r[2] == 'state'
        and r[0] in ('template.sensor', 'template.binary_sensor')))
    proof = native['reference_proof']
    _need(type(proof) is dict and set(proof) == {
        'planner', 'mapping', 'files', 'variable_bindings_sha256'})
    _need(proof['planner'] == 'yaml_plan_source_reference_v1'
          and proof['mapping'] == [list(row) for row in sorted(mapping.items())])
    sources = {r[0]: r[1] for r in receipt.source_files}
    proposed = dict(receipt.proposed_files)
    expected = sorted([p.path, p.sha256, proposed.get(p.path, p.sha256)] for p in plan.files)
    _need(all(sources.get(p.path) == p.sha256 for p in plan.files) and proof['files'] == expected)
    variables = [[p.path, p.source_line, p.entity_id, p.producer_occurrences,
        p.yaml_occurrences, p.accounted_occurrences, list(p.consumer_counts), p.approved]
        for p in plan.variable_bindings]
    _need(proof['variable_bindings_sha256'] == hashlib.sha256(_state_profile_json(variables)).hexdigest())
    raw = _state_profile_json(native)
    _need(len(raw) <= 16_000_000)
    return OriginalTemplateStateSources(ledger, raw, source.source_matched)


@dataclass(frozen=True)
class CandidateTemplateStateSources:
    original_receipt_json: bytes
    original_sources_sha256: str
    proposed_files: tuple[tuple[str, str], ...]
    sources: OriginalTemplateStateSources

    @property
    def migration_ready(self):
        return False

    @property
    def sha256(self):
        return hashlib.sha256(_state_profile_json([
            self.original_receipt_json.decode('utf-8'), self.original_sources_sha256,
            self.proposed_files, self.sources.sha256])).hexdigest()

    def assert_original(self, receipt, original):
        from .native_input_baseline import _receipt_bytes
        _need(type(original) is OriginalTemplateStateSources
              and self.original_receipt_json == _receipt_bytes(receipt)
              and self.original_sources_sha256 == original.sha256
              and self.proposed_files == receipt.proposed_files
              and self.sources.ledger == original.ledger)


def seal_template_state_candidate(receipt, original, native, *, plan, mapping):
    from .native_input_baseline import _receipt_bytes
    _need(type(original) is OriginalTemplateStateSources)
    candidate = seal_template_state_sources(receipt, original.ledger, native, plan=plan, mapping=mapping)
    before = json.loads(original.native_json)
    after = json.loads(candidate.native_json)
    _need([r[:2] for r in before['rows']] == [r[:2] for r in after['rows']]
          and [r[:2] for r in before['states']] == [r[:2] for r in after['states']]
          and before['families'] == after['families']
          and before['reference_proof'] == after['reference_proof'])
    return CandidateTemplateStateSources(_receipt_bytes(receipt), original.sha256,
                                        receipt.proposed_files, candidate)
