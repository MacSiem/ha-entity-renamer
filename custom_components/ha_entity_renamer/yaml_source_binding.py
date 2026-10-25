"""Server-owned ORIGINAL source/native evidence; never mutation authority.

Only hashes/keys leave the same isolated original checker. No replacement
receipt, changed projection semantics, native load or configuration execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .native_input_baseline import _receipt_bytes
from .yaml_source_ledger import SourceLedger, source_graph_digest
from .yaml_validation import assert_proposal_matches

_DOMAINS = ('automation', 'script')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


class SourceBindingError(ValueError):
    """Sanitized source/evidence rejection without config contents."""


def _need(value):
    if not value:
        raise SourceBindingError('original_source_evidence_rejected') from None


@dataclass(frozen=True)
class NativeSourceDomain:
    domain: str
    projection: str | None
    rows: tuple[tuple[str, str, str], ...] | None


@dataclass(frozen=True)
class OriginalSourceEvidence:
    ledger: SourceLedger
    receipt_json: bytes
    domains: tuple[NativeSourceDomain, ...]

    @property
    def migration_ready(self):
        return False

    def domain_projection(self, domain):
        _need(domain in _DOMAINS)
        return next(row.projection for row in self.domains if row.domain == domain)

    def assert_receipt(self, receipt):
        try:
            _need(_receipt_bytes(receipt) == self.receipt_json)
        except (ValueError, TypeError):
            raise SourceBindingError('original_source_receipt_mismatch') from None


def seal_original_sources(receipt, plan, ledger, native):
    """Consume private output from the SAME original staging/check operation.

    This function validates binding/layout; it cannot turn client-provided
    hashes into proof. Installed composition owns the Plan, byte bundle,
    subprocess and this detached object. No public decoder or authority token.
    """
    try:
        receipt_json = _receipt_bytes(receipt)
        assert_proposal_matches(receipt, plan)
        _need(type(ledger) is SourceLedger
              and ledger.original_graph_sha256 == source_graph_digest(plan))
        sources = {row[0]: row[1:] for row in receipt.source_files}
        _need(all(sources.get(row.path) == (row.sha256, row.device, row.inode)
                  for row in plan.files))
        _need(type(native) is dict and set(native) == {'version', 'domains'}
              and native['version'] == receipt.version
              and type(native['domains']) is list and len(native['domains']) == 2)
        domains = []
        for item in native['domains']:
            _need(type(item) is list and len(item) == 3)
            domain, projection, rows = item
            _need(domain in _DOMAINS)
            if projection is None:
                _need(rows is None)
            else:
                _need(type(projection) is str and _SHA.fullmatch(projection)
                      and type(rows) is list and len(rows) <= 100_000)
                for row in rows:
                    _need(type(row) is list and len(row) == 3
                          and type(row[0]) is str and 0 < len(row[0]) <= 255
                          and all(type(value) is str and _SHA.fullmatch(value)
                                  for value in row[1:]))
                keys = [row[0] for row in rows]
                _need(keys == sorted(set(keys)))
                if not ledger.findings:
                    ledger.match_native_keys(domain, tuple(keys))
            domains.append(NativeSourceDomain(domain, projection,
                tuple(tuple(row) for row in rows) if rows is not None else None))
        _need(tuple(row.domain for row in domains) == _DOMAINS)
        old = {domain: before for domain, before, _ in receipt.runtime_expected}
        _need(all(row.domain not in old or row.projection == old[row.domain]
                  for row in domains))
        return OriginalSourceEvidence(ledger, receipt_json, tuple(domains))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SourceBindingError('original_source_evidence_rejected') from None
