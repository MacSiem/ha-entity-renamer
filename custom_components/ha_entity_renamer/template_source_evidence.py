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
