"""Pure full core.config_entries comparison; never a native write capability.

Trusted caller supplies the immutable original baseline (or proven full forward
result for reverse), original PreparedStore binding, and an actual synchronous
call stamp. A dataclass does NOT authenticate any of these. No lease, phase,
listener causality, intent durability, manager/disk coherence or authorization is
proved here. Listener partials and already-native/no-call effects are unsupported.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math

from .helper_native import NativeConfigMutation
from .helper_transaction import PreparedHelper

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
_SCHEMAS = {'2025.6.0': (1, 5), '2026.10.2': (1, 5), '2026.10.4': (1, 5)}
_IDENTITY = ('entry_id', 'unique_id', 'title', 'version', 'minor_version',
             'source', 'disabled_by', 'pref_disable_new_entities', 'pref_disable_polling')
_SUB_IDENTITY = ('subentry_id', 'subentry_type', 'unique_id', 'title')


class ConfigTransitionError(ValueError):
    """Sanitized structural refusal; never contains private source data."""


def _fail(reason):
    raise ConfigTransitionError('config_transition_' + reason) from None


def _bounded(value, depth=0, budget=None):
    if budget is None:
        budget = [1_000_000]
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        _fail('limit')
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            _fail('json_invalid')
        for item in value.values():
            _bounded(item, depth + 1, budget)
    elif type(value) is list:
        for item in value:
            _bounded(item, depth + 1, budget)
    elif type(value) is float:
        if not math.isfinite(value):
            _fail('nonfinite')
    elif value is not None and type(value) not in (str, bool, int):
        _fail('json_invalid')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail('duplicate_key')
        result[key] = value
    return result


def _constant(_):
    _fail('nonfinite')


def _loads(raw):
    if type(raw) is not bytes or len(raw) > MAX_DOCUMENT_BYTES:
        _fail('document_invalid')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                            parse_constant=_constant)
        _bounded(result)
        return result
    except ConfigTransitionError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail('document_invalid')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode('ascii')


def _equal(left, right):
    # Typed JSON semantics at every depth: True, 1 and 1.0 are distinct.
    return _json(left) == _json(right)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prepared_helper_digest(prepared):
    """Bind all three exact fields. Caller MUST compare original trusted digest.

    Frozen PreparedHelper contains mutable dictionaries. Recomputing this hash
    from a substituted preparation is no evidence of its original provenance.
    No second full native baseline is stored in PreparedHelper.
    """
    if type(prepared) is not PreparedHelper:
        _fail('preparation_invalid')
    payload = {'before': prepared.before, 'after': prepared.after,
               'mapping': prepared.mapping}
    try:
        _bounded(payload)
        encoded = _json(payload)
        if len(encoded) > MAX_DOCUMENT_BYTES:
            _fail('limit')
        return _sha(encoded)
    except ConfigTransitionError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail('preparation_invalid')


@dataclass(frozen=True)
class NativeConfigEffectAudit:
    """Mandatory caller-authenticated observation; instance type is NOT proof.

    Caller binds to original baseline/ref, plan fingerprint, durable unique
    intent, operation, direction, and actual call stamp via trusted private IO.
    Timestamps are exact native strings; missing/null timestamps are unsupported.
    """
    entry_id: str
    direction: str
    source_sha256: str
    result_sha256: str
    prepared_sha256: str
    modified_at_before: str
    modified_at_after: str


@dataclass(frozen=True)
class ConfigDeltaEvidence:
    """Structural result only; does not authorize or prove effect provenance."""
    entry_id: str
    direction: str
    source_sha256: str
    result_sha256: str
    prepared_sha256: str


def _unique(rows, key):
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        _fail('schema_invalid')
    identities = [row.get(key) for row in rows]
    if (any(type(identity) is not str or not identity for identity in identities)
            or len(set(identities)) != len(identities)):
        _fail('identity_invalid')
    return identities


def _document(raw, version):
    doc = _loads(raw)
    major, minor = _SCHEMAS[version]
    if (type(doc) is not dict or type(doc.get('version')) is not int
            or doc['version'] != major or type(doc.get('minor_version')) is not int
            or doc['minor_version'] != minor or doc.get('key') != 'core.config_entries'
            or type(doc.get('data')) is not dict):
        _fail('schema_invalid')
    rows = doc['data'].get('entries')
    _unique(rows, 'entry_id')
    for row in rows:
        if 'subentries' in row:
            _unique(row['subentries'], 'subentry_id')
    return doc


def _target(doc, entry_id):
    for row in doc['data']['entries']:
        if row['entry_id'] == entry_id:
            return row
    _fail('identity_invalid')


def _projection(row):
    # EXACT helper_native.read_entry shape, using actual storage key names.
    try:
        if (type(row['data']) is not dict or type(row['options']) is not dict
                or any(type(row[key]) is not str for key in
                       ('domain', 'entry_id', 'title', 'source', 'created_at'))
                or not row['domain']
                or any(type(row[key]) is not int for key in ('version', 'minor_version'))
                or any(type(row[key]) is not bool for key in
                       ('pref_disable_new_entities', 'pref_disable_polling'))
                or any(row[key] is not None and type(row[key]) is not str
                       for key in ('unique_id', 'disabled_by'))
                or type(row['discovery_keys']) is not dict):
            _fail('schema_invalid')
        _unique(row['subentries'], 'subentry_id')
        subs = {}
        for sub in row['subentries']:
            if (type(sub['data']) is not dict
                    or any(type(sub[key]) is not str for key in
                           ('subentry_id', 'subentry_type', 'title'))
                    or (sub['unique_id'] is not None and type(sub['unique_id']) is not str)):
                _fail('schema_invalid')
            subs[sub['subentry_id']] = {
                'identity': {key: sub[key] for key in _SUB_IDENTITY}, 'data': sub['data']}
        return {'domain': row['domain'],
                'identity': {key: row[key] for key in _IDENTITY},
                'data': row['data'], 'options': row['options'], 'subentries': subs}
    except (KeyError, TypeError):
        _fail('schema_invalid')


def _stamp_row(value):
    if type(value) is not str:
        _fail('stamp_invalid')
    try:
        row = _loads(value.encode('utf-8'))
    except UnicodeError:
        _fail('stamp_invalid')
    if type(row) is not dict:
        _fail('stamp_invalid')
    return row


def validate_config_transition(source_raw, result_raw, prepared, *, version,
                               direction, native_effect_audit, native_mutation):
    """Validate one ordinary adapter-owned forward/reverse full-document delta.

    Expected comes ONLY from deepcopy(source), exact recomputed plan data/options
    paths and exact audited native modified_at. List order, opaque fields and
    every unrelated value remain strict. Reverse uses a previously proven full
    forward result, including its audit; never refresh source from live state.
    Listener partial/no-op transitions require a different future proof contract.
    """
    digest = prepared_helper_digest(prepared)
    if (type(version) is not str or version not in _SCHEMAS
            or type(direction) is not str or direction not in ('forward', 'reverse')):
        _fail('scope_invalid')
    try:
        plan = prepared.plan()
        entry_id = prepared.before['identity']['entry_id']
    except Exception:
        _fail('preparation_invalid')
    if type(entry_id) is not str or not entry_id or not plan.changes:
        _fail('preparation_invalid')
    source, result = _document(source_raw, version), _document(result_raw, version)
    source_row, result_row = _target(source, entry_id), _target(result, entry_id)
    audit = native_effect_audit
    if (type(audit) is not NativeConfigEffectAudit
            or any(type(value) is not str or not value for value in vars(audit).values())
            or (audit.entry_id, audit.direction) != (entry_id, direction)
            or audit.source_sha256 != _sha(source_raw)
            or audit.result_sha256 != _sha(result_raw)
            or audit.prepared_sha256 != digest
            or type(source_row.get('modified_at')) is not str
            or type(result_row.get('modified_at')) is not str
            or source_row['modified_at'] != audit.modified_at_before
            or result_row['modified_at'] != audit.modified_at_after):
        _fail('audit_invalid')
    stamp = native_mutation
    if (type(stamp) is not NativeConfigMutation or type(stamp.entry_id) is not str
            or stamp.entry_id != entry_id or type(stamp.changed) is not bool):
        _fail('stamp_invalid')
    # Ordinary full plan writes must really change HA. No false/no-call/listener
    # attribution is inferred from an eventual matching result.
    if not stamp.changed:
        _fail('native_noop_unsupported')
    if (not _equal(_stamp_row(stamp.before_json), source_row)
            or not _equal(_stamp_row(stamp.after_json), result_row)):
        _fail('stamp_mismatch')
    origin, destination = ((prepared.before, prepared.after) if direction == 'forward'
                           else (prepared.after, prepared.before))
    if not _equal(_projection(source_row), origin):
        _fail('source_mismatch')
    expected = deepcopy(source)
    expected_row = _target(expected, entry_id)
    for change in plan.changes:
        path = change.path
        if (change.location != 'value' or not path or path[0] not in ('data', 'options')):
            _fail('plan_scope_unsupported')
        try:
            parent, desired = expected_row, destination
            for key in path[:-1]:
                parent, desired = parent[key], desired[key]
            parent[path[-1]] = deepcopy(desired[path[-1]])
        except (KeyError, IndexError, TypeError):
            _fail('preparation_invalid')
    expected_row['modified_at'] = audit.modified_at_after
    if (not _equal(_projection(expected_row), destination)
            or not _equal(expected, result)):
        _fail('foreign_delta')
    return ConfigDeltaEvidence(entry_id, direction, audit.source_sha256,
                               audit.result_sha256, digest)
