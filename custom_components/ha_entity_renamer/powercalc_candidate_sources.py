"""Private proposed Powercalc facts from the SAME isolated validation operation.

Full planned native documents are trusted backend inputs, never current dumps.
No decoder, apply capability, activation support or replacement receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .native_input_baseline import _KEYS, _envelope, _receipt_bytes
from .powercalc_source_binding import (
    OriginalPowercalcSources, seal_powercalc_sources, _need, _json, _sha,
)


@dataclass(frozen=True)
class PlannedPowercalcDocuments:
    """Full three before/proposed bytes supplied before native validation."""
    documents: tuple[tuple[str, bytes, bytes], ...]

    def checked(self, version):
        _need(type(self.documents) is tuple and len(self.documents) == len(_KEYS),
              'candidate_full_three_required')
        _need(all(type(row) is tuple and len(row) == 3 for row in self.documents),
              'candidate_document_shape')
        _need(tuple(row[0] for row in self.documents) == _KEYS,
              'candidate_full_three_required')
        for key, before, after in self.documents:
            _envelope(before, key, version)
            _envelope(after, key, version)
        return self


def stage_candidate_documents(planned, before_root, proposed_root):
    """Only private, already-created checker trees are written."""
    from homeassistant.const import __version__
    _need(type(planned) is PlannedPowercalcDocuments, 'candidate_documents_required')
    planned.checked(__version__)
    for key, before, after in planned.documents:
        original = Path(before_root) / '.storage' / key
        proposed = Path(proposed_root) / '.storage' / key
        _need(original.read_bytes() == before and proposed.read_bytes() == before,
              'candidate_original_document_changed')
        proposed.write_bytes(after)


@dataclass(frozen=True)
class CandidatePowercalcSources:
    original_receipt_json: bytes
    original_sources_sha256: str
    proposed_files: tuple[tuple[str, str], ...]
    documents: PlannedPowercalcDocuments
    sources: OriginalPowercalcSources

    @property
    def sha256(self):
        return _sha(_json([self.original_receipt_json.decode(),
            self.original_sources_sha256, self.proposed_files,
            [(key, _sha(before), _sha(after)) for key, before, after in self.documents.documents],
            self.sources.sha256]))

    @property
    def migration_ready(self):
        return False

    @property
    def family_complete(self):
        return False

    def assert_original(self, receipt, original):
        _need(type(original) is OriginalPowercalcSources
              and self.original_receipt_json == _receipt_bytes(receipt)
              and original.receipt_json == self.original_receipt_json
              and self.original_sources_sha256 == original.sha256
              and self.proposed_files == receipt.proposed_files
              and type(self.documents) is PlannedPowercalcDocuments
              and type(self.sources) is OriginalPowercalcSources
              and self.sources.receipt_json == self.original_receipt_json
              and self.sources.ledger == original.ledger,
              'candidate_original_mismatch')
        self.documents.checked(receipt.version)
        physical = {row[0]: row[1] for row in receipt.source_files}
        _need(all(physical.get('.storage/' + key) == _sha(before)
                  for key, before, _ in self.documents.documents),
              'candidate_original_full_three_changed')


def seal_powercalc_candidate(receipt, plan, original, planned, native, document_hashes):
    """Consume private SAME checker output, never parse live candidate sources."""
    _need(type(original) is OriginalPowercalcSources
          and original.closed_graph_json is not None
          and type(planned) is PlannedPowercalcDocuments,
          'candidate_original_required')
    planned.checked(receipt.version)
    expected = [[key, _sha(before), _sha(after)] for key, before, after in planned.documents]
    _need(document_hashes == expected, 'candidate_staged_full_three_changed')
    candidate = seal_powercalc_sources(receipt, plan, original.ledger, native)
    import json
    old, new = json.loads(original.closed_graph_json), json.loads(candidate.closed_graph_json)
    # This prerequisite changes references, not source identity or profile bytes.
    _need(old['files'] == new['files']
          and [(r['origin'], r['unique_id']) for r in old['profiles']] ==
              [(r['origin'], r['unique_id']) for r in new['profiles']]
          and [(r['origin'], r['unique_id']) for r in old['groups']] ==
              [(r['origin'], r['unique_id']) for r in new['groups']],
          'candidate_inventory_changed')
    # Source and derived-selector names may move; native physical identity must
    # remain the same. Membership/order/roles are compiled in both staged images.
    def physical(graph):
        return [(origin, [(role, uuid, uid, platform) for role, _, uuid, uid, platform in rows])
                for origin, rows in graph['bindings']]
    _need(physical(old) == physical(new), 'candidate_dependency_identity_changed')
    result = CandidatePowercalcSources(_receipt_bytes(receipt), original.sha256,
                                       receipt.proposed_files, planned, candidate)
    result.assert_original(receipt, original)
    return result
