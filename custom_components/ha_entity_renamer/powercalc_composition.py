"""Immutable SAME-ORIGINAL Powercalc composition; no execution authority.

Only the existing PreparedStore may persist this codec. It is a prerequisite
for a separate typed lifecycle and installed server, not an admission or a
reason to bypass the legacy loaded-Powercalc refusal.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import stat
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from .native_input_baseline import NativeInputBaseline, _envelope, _json, _loads
from .native_template_composition import _history, _ledger
from .powercalc_candidate_sources import CandidatePowercalcSources, PlannedPowercalcDocuments
from .powercalc_previous_state_original import PowercalcGroupOriginal, PowercalcPreviousOriginal, _disk_shape
from .powercalc_previous_state_payload import _owned_state
from .powercalc_source_binding import OriginalPowercalcSources
from .template_source_evidence import OriginalTemplateSources, CandidateTemplateSources, seal_template_sources
from .yaml_source_binding import NativeSourceDomain, OriginalSourceEvidence
from .yaml_source_ledger import Definition, Finding, IncludeHop, Origin, SourceLedger

_PROFILE = 'powercalc_source_own_binding_v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
_TYPES = {item.__name__: item for item in (Definition, Finding, IncludeHop, Origin, SourceLedger,
    NativeSourceDomain, OriginalSourceEvidence, OriginalPowercalcSources, CandidatePowercalcSources,
    PlannedPowercalcDocuments, PowercalcGroupOriginal, PowercalcPreviousOriginal,
    OriginalTemplateSources, CandidateTemplateSources)}


def _need(value):
    if not value:
        raise ValueError('powercalc_composition_rejected') from None


def _typed(value, annotation, *, encode):
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        for item in args:
            try:
                return _typed(value, item, encode=encode)
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
        except (TypeError, ValueError):
            _need(False)
    if origin is tuple:
        _need(type(value) is (tuple if encode else list))
        types = (args[0],) * len(value) if len(args) == 2 and args[1] is Ellipsis else args
        _need(len(types) == len(value))
        result = [_typed(row, typ, encode=encode) for row, typ in zip(value, types, strict=True)]
        return result if encode else tuple(result)
    _need(annotation in _TYPES.values())
    names = {item.name for item in fields(annotation)}
    if encode:
        _need(type(value) is annotation)
        values = {name: getattr(value, name) for name in names}
    else:
        _need(type(value) is dict and set(value) == {'type', 'fields'}
              and value['type'] == annotation.__name__ and type(value['fields']) is dict
              and set(value['fields']) == names)
        values = value['fields']
    hints = get_type_hints(annotation, localns=_TYPES)
    result = {name: _typed(values[name], typ, encode=encode) for name, typ in hints.items()}
    return {'type': annotation.__name__, 'fields': result} if encode else annotation(**result)


def _identity(value, directory):
    _need(type(value) is tuple and len(value) == 3
          and all(type(item) is int and item >= 0 for item in value)
          and (stat.S_ISDIR(value[2]) if directory else stat.S_ISREG(value[2])))


def _previous(previous, baseline, evidence):
    _need(type(previous) is PowercalcPreviousOriginal
          and previous.receipt_sha256 == hashlib.sha256(baseline.receipt_json).hexdigest()
          and previous.sources_sha256 == evidence.powercalc_sources.sha256
          and previous.core_version == baseline.receipt.version
          and previous.core_version in ('2026.10.2', '2026.10.4', '2026.11.0', '2026.11.1')
          and type(previous.groups) is tuple and bool(previous.groups))
    _identity(previous.root_identity, True)
    _identity(previous.storage_identity, True)
    if previous.disk_raw is None:
        _need(previous.disk_identity is None)
    else:
        _need(type(previous.disk_raw) is bytes)
        _identity(previous.disk_identity, False)
        _disk_shape(previous.disk_raw)
    memory = _loads(previous.memory_json)
    _need(type(memory) is dict and _json(memory) == previous.memory_json)
    registry_file = baseline.files[0]
    _need(registry_file.key == 'core.entity_registry' and registry_file.raw is not None)
    registry = _envelope(registry_file.raw, registry_file.key, previous.core_version)['data']['entities']
    by_id = {row['id']: row for row in registry}
    _need(len(by_id) == len(registry))
    graph = _loads(evidence.powercalc_sources.closed_graph_json)
    configured = {}
    for cfg in graph['groups']:
        if cfg['config'].get('group_type') != 'custom':
            continue
        origin = cfg['origin']
        if origin.startswith('entry:'):
            owner = origin[6:]
        else:
            _need(origin == 'yaml:' + cfg['unique_id'] and type(cfg.get('members')) is list)
            owner = None
        key = owner, cfg['unique_id'] + '_energy'
        _need(key not in configured)
        configured[key] = cfg
    seen_configured = set()
    seen_groups = set()
    for group in previous.groups:
        _need(type(group) is PowercalcGroupOriginal and type(group.entity_id) is str
              and type(group.registry_id) is str and group.registry_id not in seen_groups
              and group.entity_id in memory and type(memory[group.entity_id]) is dict
              and type(group.members) is tuple and bool(group.members)
              and group.members == tuple(sorted(group.members)))
        row = by_id.get(group.registry_id)
        _need(type(row) is dict and row['entity_id'] == group.entity_id and row['platform'] == 'powercalc'
              and row.get('disabled_by') is None)
        key = row.get('config_entry_id'), row['unique_id']
        _need(key in configured and key not in seen_configured)
        seen_configured.add(key)
        cfg = configured[key]
        seen_groups.add(group.registry_id)
        members = {}
        for pair in group.members:
            _need(type(pair) is tuple and len(pair) == 2 and all(type(x) is str for x in pair))
            name, uuid = pair
            child = by_id.get(uuid)
            _need(name not in members and type(child) is dict and child['entity_id'] == name
                  and child['platform'] == 'powercalc')
            members[name] = uuid
        if key[0] is None:
            expected = {member[2]: member[3] for member in cfg['members']}
            _need(len(expected) == len(cfg['members']) and members == expected)
        else:
            _need(set(members) == set(cfg['config'].get('group_energy_entities', [])))
        _need(set(memory[group.entity_id]) <= set(members))
        for name, state in memory[group.entity_id].items():
            _need(_owned_state(state, name))
        try:
            _need(type(group.full_total) is str and Decimal(group.full_total).is_finite()
                  and str(Decimal(group.full_total)) == group.full_total)
        except InvalidOperation:
            _need(False)
    _need(len(seen_groups) == len(configured) and set(memory) == {group.entity_id for group in previous.groups})


@dataclass(frozen=True)
class PowercalcComposition:
    evidence: OriginalSourceEvidence
    previous: PowercalcPreviousOriginal
    prepared_digests: tuple[tuple[str, str], ...]
    source_history: dict
    consumer_history: dict
    coverage_sha256: str
    energy_original_physical: tuple[str, int, int, int, int, int] | None = None

    def assert_baseline(self, baseline, *, _mixed_template=False):
        _need(type(_mixed_template) is bool and type(baseline) is NativeInputBaseline
              and type(self.evidence) is OriginalSourceEvidence)
        evidence, receipt = self.evidence, baseline.receipt
        evidence.assert_receipt(receipt)
        _need(type(evidence.template_sources) is OriginalTemplateSources
              and type(evidence.powercalc_sources) is OriginalPowercalcSources
              and type(evidence.powercalc_candidate_sources) is CandidatePowercalcSources
              and evidence.powercalc_sources.receipt_json == baseline.receipt_json
              and evidence.powercalc_sources.closed_graph_json is not None
              and set(receipt.unsupported_domains) <= ({'powercalc', 'template'} if _mixed_template else {'powercalc'})
              and set(receipt.changed_domains) <= ({'powercalc', 'template', 'automation', 'script'}
                                                     if _mixed_template else {'powercalc'})
              and (not _mixed_template or 'powercalc' in receipt.changed_domains))
        template = evidence.template_sources
        _ledger(template.ledger, 2)
        _need(not template.ledger.findings
              and template.ledger.original_graph_sha256 == evidence.ledger.original_graph_sha256
              and (_mixed_template or not template.ledger.definitions and template.rows in (None, ())))
        native = {'version': receipt.version, 'projection': template.projection,
                  'rows': None if template.rows is None else [list(row) for row in template.rows]}
        _need(seal_template_sources(receipt, template.ledger, native) == template)
        if evidence.template_candidate_sources is not None:
            _need(type(evidence.template_candidate_sources) is CandidateTemplateSources)
            evidence.template_candidate_sources.assert_original(receipt, template)
            _need(_mixed_template or evidence.template_candidate_sources.sources == template)
        _ledger(evidence.ledger, 1)
        _ledger(evidence.powercalc_sources.ledger, 3)
        _need(evidence.ledger.original_graph_sha256 ==
              evidence.powercalc_sources.ledger.original_graph_sha256)
        evidence.powercalc_candidate_sources.assert_original(receipt, evidence.powercalc_sources)
        _previous(self.previous, baseline, evidence)
        planned_energy = evidence.powercalc_candidate_sources.documents.energy
        if planned_energy is None:
            _need(self.energy_original_physical is None)
        else:
            physical = self.energy_original_physical
            _need(type(physical) is tuple and len(physical) == 6
                  and type(physical[0]) is str and _SHA.fullmatch(physical[0])
                  and all(type(value) is int and value >= 0 for value in physical[1:])
                  and physical[2] != 0 and stat.S_ISREG(physical[3])
                  and physical[0] == hashlib.sha256(planned_energy[0]).hexdigest())
            sources = {row[0]: row[1:] for row in receipt.source_files}
            _need(sources.get('.storage/energy') == tuple(physical[:3]))
            storage = next((row for row in receipt.source_directories if row[0] == '.storage'), None)
            _need(storage is not None and any(entry == ('energy', physical[3], physical[1], physical[2])
                                              for entry in storage[1]))
        _need(type(self.prepared_digests) is tuple and bool(self.prepared_digests)
              and self.prepared_digests == tuple(sorted(self.prepared_digests)))
        ids = set()
        for pair in self.prepared_digests:
            _need(type(pair) is tuple and len(pair) == 2 and type(pair[0]) is str
                  and _ID.fullmatch(pair[0]) and pair[0] not in ids
                  and type(pair[1]) is str and _SHA.fullmatch(pair[1]))
            ids.add(pair[0])
        _need(type(self.coverage_sha256) is str and _SHA.fullmatch(self.coverage_sha256))
        _history(self.source_history)
        _history(self.consumer_history)


def encode_powercalc_composition(value, baseline):
    _need(type(value) is PowercalcComposition)
    evidence = _typed(value.evidence, OriginalSourceEvidence, encode=True)
    previous = _typed(value.previous, PowercalcPreviousOriginal, encode=True)
    value.assert_baseline(baseline)
    return {'schema': 1, 'profile': _PROFILE, 'evidence': evidence, 'previous': previous,
            'prepared_digests': [list(row) for row in value.prepared_digests],
            'source_history': _history(value.source_history),
            'consumer_history': _history(value.consumer_history), 'coverage_sha256': value.coverage_sha256,
            'energy_original_physical': None if value.energy_original_physical is None
                                        else list(value.energy_original_physical)}


def decode_powercalc_composition(payload, baseline):
    _need(type(payload) is dict and set(payload) == {'schema', 'profile', 'evidence', 'previous',
          'prepared_digests', 'source_history', 'consumer_history', 'coverage_sha256',
          'energy_original_physical'}
          and type(payload['schema']) is int and payload['schema'] == 1 and payload['profile'] == _PROFILE)
    result = PowercalcComposition(_typed(payload['evidence'], OriginalSourceEvidence, encode=False),
        _typed(payload['previous'], PowercalcPreviousOriginal, encode=False),
        _typed(payload['prepared_digests'], tuple[tuple[str, str], ...], encode=False),
        _history(payload['source_history']), _history(payload['consumer_history']), payload['coverage_sha256'],
        _typed(payload['energy_original_physical'], tuple[str, int, int, int, int, int] | None, encode=False))
    result.assert_baseline(baseline)
    return result
