"""Pure full-document registry delta validation; NEVER write authorization.

Both documents are authentic observations supplied by a trusted caller. Source
must be the immutable baseline or the previous proven full image, never a fresh
current document with expected fields overlaid on it. An audit is REQUIRED,
including when modified_at is absent/unchanged. This module binds its exact
values and digests but cannot prove the audit's provenance or native causality.
No HA calls, storage access, current-state check or transaction-phase inference.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re

from .native_registry import PreparedRegistryChange

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
_SCHEMAS = {'2025.6.0': {'entity': (1, 17), 'device': (1, 9)},
            '2026.10.2': {'entity': (1, 23), 'device': (3, 4)}}


class RegistryTransitionError(ValueError):
    """Sanitized refusal: no source content or underlying exception text."""


def _fail(reason):
    raise RegistryTransitionError(reason) from None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail('registry_transition_duplicate_key')
        result[key] = value
    return result


def _bounded(value, depth=0, budget=None):
    if budget is None:
        budget = [1_000_000]
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        _fail('registry_transition_limit')
    if type(value) is dict:
        for item in value.values():
            _bounded(item, depth + 1, budget)
    elif type(value) is list:
        for item in value:
            _bounded(item, depth + 1, budget)
    elif type(value) is float and not math.isfinite(value):
        _fail('registry_transition_nonfinite')


def _constant(_):
    _fail('registry_transition_nonfinite')


def _loads(raw):
    if type(raw) is not bytes or len(raw) > MAX_DOCUMENT_BYTES:
        _fail('registry_transition_document_invalid')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                            parse_constant=_constant)
        _bounded(result)
        return result
    except RegistryTransitionError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail('registry_transition_document_invalid')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode('ascii')


def _equal(left, right):
    # Python equality alone treats True == 1 == 1.0. Preserve JSON types at
    # EVERY depth, including opaque nested settings, options and deleted rows.
    return _json(left) == _json(right)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prepared_registry_digest(prepared):
    """Digest exact original preparation, including its private observer fields."""
    if type(prepared) is not PreparedRegistryChange:
        _fail('registry_transition_preparation_invalid')
    if (any(type(value) is not str for value in asdict(prepared).values())
            or sum(len(value) for value in asdict(prepared).values()) > MAX_DOCUMENT_BYTES):
        _fail('registry_transition_preparation_invalid')
    return _sha(_json(asdict(prepared)))


@dataclass(frozen=True)
class NativeRegistryEffectAudit:
    """Trusted native effect observation, NOT an authorization capability.

    Caller authenticates/binds this to durable unique intent, operation, original
    plan fingerprint/ref, effect direction and independent observer evidence.
    Merely constructing this class proves none of those facts. Digests bind the
    exact observed bytes and original PreparedRegistryChange. modified_at values
    are exact strings, or None for field absence (JSON null is unsupported).
    Only this single target-row audit field is supported by this mapping.
    """
    kind: str
    identity: str
    direction: str
    source_sha256: str
    result_sha256: str
    prepared_sha256: str
    modified_at_before: str | None
    modified_at_after: str | None


@dataclass(frozen=True)
class RegistryDeltaEvidence:
    """Structural comparison result only; no current-state/causality guarantee."""
    kind: str
    identity: str
    direction: str
    source_sha256: str
    result_sha256: str
    prepared_sha256: str


def _document(raw, kind, version):
    doc = _loads(raw)
    major, minor = _SCHEMAS[version][kind]
    key = 'core.' + kind + '_registry'
    if (type(doc) is not dict or not {'version', 'minor_version', 'key', 'data'} <= doc.keys()
            or type(doc['version']) is not int or doc['version'] != major
            or type(doc['minor_version']) is not int or doc['minor_version'] != minor
            or type(doc['key']) is not str or doc['key'] != key
            or type(doc['data']) is not dict):
        _fail('registry_transition_schema_invalid')
    lists = ['entities', 'deleted_entities'] if kind == 'entity' else ['devices', 'deleted_devices']
    if version == '2026.10.2':
        if kind == 'entity' and type(doc['data'].get('settings')) is not dict:
            _fail('registry_transition_schema_invalid')
        if kind == 'device':
            lists.append('child_devices')
    if any(type(doc['data'].get(key)) is not list or
           any(type(row) is not dict for row in doc['data'][key]) for key in lists):
        _fail('registry_transition_schema_invalid')
    # Unknown fields are opaque and must survive unchanged. They never grant a
    # path that may change; version/minor_version still use exact native anchors.
    return doc, lists


def _target(doc, lists, identity):
    active = doc['data'][lists[0]]
    ids = [row.get('id') for row in active]
    if (any(type(item) is not str or not item for item in ids)
            or len(set(ids)) != len(ids) or ids.count(identity) != 1
            or any(row.get('id') == identity for key in lists[1:] for row in doc['data'][key])):
        _fail('registry_transition_identity_invalid')
    return active[ids.index(identity)]


def _audit_value(row, expected):
    if expected is None:
        return 'modified_at' not in row
    return (type(expected) is str and type(row.get('modified_at')) is str
            and row['modified_at'] == expected)


def validate_registry_transition(source_raw, result_raw, prepared, *, version,
                                 direction, native_effect_audit):
    """Validate exactly one entity/device rename in two full storage envelopes.

    Reverse consumes a full proven AFTER image (including its newer audit), not
    the original raw baseline. Caller supplies the trusted reverse native audit.
    This function never authorizes transitions, replays effects, checks disk or
    replaces any YAML fence. Existing callers remain unchanged.
    """
    digest = prepared_registry_digest(prepared)
    if (type(version) is not str or version not in _SCHEMAS
            or prepared.kind not in ('entity', 'device')
            or type(prepared.identity) is not str or not prepared.identity
            or type(direction) is not str or direction not in ('forward', 'reverse')):
        _fail('registry_transition_scope_invalid')
    if (type(source_raw) is not bytes or type(result_raw) is not bytes
            or max(len(source_raw), len(result_raw)) > MAX_DOCUMENT_BYTES):
        _fail('registry_transition_document_invalid')
    audit = native_effect_audit
    if (type(audit) is not NativeRegistryEffectAudit
            or any(type(getattr(audit, field)) is not str for field in
                   ('kind', 'identity', 'direction', 'source_sha256', 'result_sha256', 'prepared_sha256'))
            or (audit.kind, audit.identity, audit.direction) !=
               (prepared.kind, prepared.identity, direction)
            or audit.prepared_sha256 != digest
            or audit.source_sha256 != _sha(source_raw)):
        _fail('registry_transition_audit_invalid')
    if type(result_raw) is not bytes or audit.result_sha256 != _sha(result_raw):
        _fail('registry_transition_audit_invalid')
    try:
        before = _loads(prepared.before_json.encode('utf-8'))
        after = _loads(prepared.after_json.encode('utf-8'))
    except UnicodeError:
        _fail('registry_transition_preparation_invalid')
    allowed = {'entity_id', 'name'} if prepared.kind == 'entity' else {'name_by_user'}
    if (type(before) is not dict or type(after) is not dict
            or before.get('id') != prepared.identity or after.get('id') != prepared.identity
            or not allowed <= before.keys() or before.keys() != after.keys()):
        _fail('registry_transition_preparation_invalid')
    changed = {key for key in before if not _equal(before[key], after[key])}
    if not changed or not changed <= allowed:
        _fail('registry_transition_preparation_invalid')
    for row in (before, after):
        if any(row[key] is not None and type(row[key]) is not str for key in allowed):
            _fail('registry_transition_preparation_invalid')
        if prepared.kind == 'entity' and (type(row['entity_id']) is not str or
                re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z0-9_]+', row['entity_id']) is None):
            _fail('registry_transition_preparation_invalid')
    if prepared.kind == 'entity' and before['entity_id'].split('.')[0] != after['entity_id'].split('.')[0]:
        _fail('registry_transition_preparation_invalid')
    source, lists = _document(source_raw, prepared.kind, version)
    result, _ = _document(result_raw, prepared.kind, version)
    source_row = _target(source, lists, prepared.identity)
    result_row = _target(result, lists, prepared.identity)
    origin, destination = (before, after) if direction == 'forward' else (after, before)
    if (not _audit_value(source_row, audit.modified_at_before)
            or not _audit_value(result_row, audit.modified_at_after)
            or ('modified_at' in source_row) != ('modified_at' in result_row)):
        _fail('registry_transition_audit_invalid')
    # Only the target row's exact audited timestamp may differ from the original
    # preparation. Neither untouched rows nor other target audit fields normalize.
    row_content = {key: value for key, value in source_row.items() if key != 'modified_at'}
    prepared_content = {key: value for key, value in origin.items() if key != 'modified_at'}
    if (not _equal(row_content, prepared_content)
            or ('modified_at' in origin) != ('modified_at' in source_row)):
        _fail('registry_transition_source_mismatch')
    if prepared.kind == 'entity' and any(
            row['id'] != prepared.identity and row.get('entity_id') == destination['entity_id']
            for row in source['data']['entities']):
        _fail('registry_transition_destination_occupied')
    # Derive the only acceptable result from IMMUTABLE SOURCE, never result/current.
    expected = deepcopy(source)
    expected_row = _target(expected, lists, prepared.identity)
    for key in changed:
        expected_row[key] = deepcopy(destination[key])
    if audit.modified_at_after is not None:
        expected_row['modified_at'] = audit.modified_at_after
    if prepared.kind == 'entity' and 'entity_id' in changed:
        # Both pinned Core versions pop the old dictionary key and append the
        # returned entry under its new ID. Model ONLY that deterministic native
        # relocation. Every untouched row retains its exact value and relative
        # order; name-only/device writes never gain reorder permission.
        rows = expected['data']['entities']
        rows.remove(expected_row)
        rows.append(expected_row)
    if not _equal(expected, result):
        _fail('registry_transition_foreign_delta')
    return RegistryDeltaEvidence(prepared.kind, prepared.identity, direction,
                                 audit.source_sha256, audit.result_sha256, digest)
