"""Private proposed Powercalc facts from the SAME isolated validation operation.

Full planned native documents are trusted backend inputs, never current dumps.
No decoder, apply capability, activation support or replacement receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .native_input_baseline import _KEYS, _envelope, _receipt_bytes, _loads
from .powercalc_source_binding import (
    OriginalPowercalcSources, seal_powercalc_sources, _need, _json, _sha,
)


@dataclass(frozen=True)
class PlannedPowercalcDocuments:
    """Full three before/proposed bytes supplied before native validation."""
    documents: tuple[tuple[str, bytes, bytes], ...]
    energy: tuple[bytes, bytes] | None = None

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
        if self.energy is not None:
            _need(type(self.energy) is tuple and len(self.energy) == 2
                  and all(type(raw) is bytes for raw in self.energy), 'candidate_energy_shape')
            for raw in self.energy:
                document = _loads(raw)
                _need(type(document) is dict and set(document) == {'version', 'minor_version', 'key', 'data'}
                      and type(document['version']) is int and document['version'] == 1
                      and type(document['minor_version']) is int and document['minor_version'] == 3
                      and document['key'] == 'energy' and type(document['data']) is dict,
                      'candidate_energy_envelope')
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
    if planned.energy is not None:
        before, after = planned.energy
        original = Path(before_root) / '.storage/energy'
        proposed = Path(proposed_root) / '.storage/energy'
        _need(original.read_bytes() == before and proposed.read_bytes() == before,
              'candidate_original_energy_changed')
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
        value = [self.original_receipt_json.decode(), self.original_sources_sha256, self.proposed_files,
                 [(key, _sha(before), _sha(after)) for key, before, after in self.documents.documents],
                 self.sources.sha256]
        if self.documents.energy is not None:
            value.append([_sha(raw) for raw in self.documents.energy])
        return _sha(_json(value))

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
        if self.documents.energy is not None:
            _need(physical.get('.storage/energy') == _sha(self.documents.energy[0]),
                  'candidate_original_energy_changed')


def seal_powercalc_candidate(receipt, plan, original, planned, native, document_hashes, energy_hashes=None):
    """Consume private SAME checker output, never parse live candidate sources."""
    _need(type(original) is OriginalPowercalcSources
          and original.closed_graph_json is not None
          and type(planned) is PlannedPowercalcDocuments,
          'candidate_original_required')
    planned.checked(receipt.version)
    expected = [[key, _sha(before), _sha(after)] for key, before, after in planned.documents]
    _need(document_hashes == expected, 'candidate_staged_full_three_changed')
    if planned.energy is not None:
        _need(energy_hashes == [_sha(raw) for raw in planned.energy], 'candidate_staged_energy_changed')
    candidate = seal_powercalc_sources(receipt, plan, original.ledger, native)
    import json
    old, new = json.loads(original.closed_graph_json), json.loads(candidate.closed_graph_json)
    # Read-only recognition of the new shapes has no corresponding lifecycle proof.
    _need(receipt.version != '2026.11.1'
          and all(not group.get('explicit_pairs') for graph in (old, new) for group in graph['groups'])
          and all(not row.get('standby_power') and 'gamma_curve' not in row.get('settings', {})
                  for graph in (old, new) for row in graph['profiles'])
          and not any(path == '.storage' and any(row[0] == 'powercalc_profiles' for row in rows)
                      for path, rows, _ in receipt.source_directories),
          'recognition_only_graph')
    # This prerequisite changes references, not source identity or profile bytes.
    _need(old['files'] == new['files']
          and [(r['origin'], r['unique_id']) for r in old['profiles']] ==
              [(r['origin'], r['unique_id']) for r in new['profiles']]
          and [(r['origin'], r['unique_id']) for r in old['groups']] ==
              [(r['origin'], r['unique_id']) for r in new['groups']],
          'candidate_inventory_changed')
    _need([(r['origin'], r.get('custom_model_directory')) for r in old['profiles']] ==
          [(r['origin'], r.get('custom_model_directory')) for r in new['profiles']],
          'candidate_custom_locator_changed')
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
