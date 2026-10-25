"""Server-owned ORIGINAL source/native evidence; never mutation authority.

Only hashes/keys leave the same isolated original checker. No replacement
receipt, changed projection semantics, native load or configuration execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .native_input_baseline import _receipt_bytes
from .yaml_source_ledger import SourceLedger, source_graph_digest
from .template_source_evidence import OriginalTemplateSources, CandidateTemplateSources
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
    template_sources: OriginalTemplateSources | None = None
    template_candidate_sources: CandidateTemplateSources | None = None

    @property
    def migration_ready(self):
        return False

    @property
    def sha256(self):
        import hashlib
        import json
        payload = {'receipt': self.receipt_json.decode('utf-8'),
                   'ledger': self.ledger.sha256,
                   'domains': [asdict(row) for row in self.domains]}
        if self.template_sources is not None:
            payload['template_sources'] = self.template_sources.sha256
        if self.template_candidate_sources is not None:
            payload['template_candidate_sources'] = self.template_candidate_sources.sha256
        return hashlib.sha256(json.dumps(payload, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def domain_projection(self, domain):
        _need(domain in _DOMAINS)
        return next(row.projection for row in self.domains if row.domain == domain)

    def assert_receipt(self, receipt):
        try:
            _need(_receipt_bytes(receipt) == self.receipt_json)
        except (ValueError, TypeError):
            raise SourceBindingError('original_source_receipt_mismatch') from None


def seal_original_sources(receipt, plan, ledger, native, *,
                          template_ledger=None, template_native=None,
                          template_candidate_native=None):
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
        template_sources = None
        if template_ledger is not None or template_native is not None:
            from .template_source_evidence import seal_template_sources
            _need(type(template_ledger) is SourceLedger
                  and template_ledger.original_graph_sha256 == source_graph_digest(plan))
            template_sources = seal_template_sources(receipt, template_ledger, template_native)
        candidate = None
        if template_candidate_native is not None:
            from .template_source_evidence import seal_template_candidate
            _need(template_sources is not None)
            candidate = seal_template_candidate(receipt, template_sources,
                                                template_candidate_native)
        return OriginalSourceEvidence(ledger, receipt_json, tuple(domains), template_sources, candidate)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SourceBindingError('original_source_evidence_rejected') from None


@dataclass(frozen=True)
class BoundSourceRow:
    domain: str
    native_key: str
    entity_id: str
    registry_id: str
    registry_row_sha256: str
    disposition: str


@dataclass(frozen=True)
class BoundSourceRuntime:
    evidence_sha256: str
    rows: tuple[BoundSourceRow, ...]
    blockers: tuple[str, ...]
    tokens: tuple

    @property
    def verified(self):
        return not self.blockers

    @property
    def migration_ready(self):
        return False


def _runtime_sample(hass, evidence, observed):
    """Same native projections, plus full ORIGINAL registry/object bijection."""
    import hashlib
    import json
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity_component import DATA_INSTANCES, EntityComponent
    from .yaml_runtime_observer import NativeYAMLRuntimeReadback, _pending_native_work
    from .yaml_validation_worker import _fingerprint
    if _pending_native_work(hass, _DOMAINS):
        raise SourceBindingError('runtime_source_runtime_unavailable')
    native = next(row for row in observed.files if row.key == 'core.entity_registry')
    registry_rows = json.loads(native.manager_json)['entities']
    registry = hass.data.get(er.DATA_REGISTRY)
    _need(type(registry) is er.EntityRegistry)
    observer = NativeYAMLRuntimeReadback(hass)
    rows, tokens, blockers = [], [], set()
    for expected in evidence.domains:
        domain = expected.domain
        source_keys = set(evidence.ledger.keys_for_native_projection(domain))
        component = hass.data.get(DATA_INSTANCES, {}).get(domain)
        entities = tuple(component.entities) if component is not None else ()
        if component is None:
            _need(not entities)
            projection, identity = [], ()
        else:
            _need(type(component) is EntityComponent and component.hass is hass)
            projection, identity = observer._projection(domain)
            for entity in entities:
                _need(entity.platform in component._platforms.values()
                      and entity.platform.entities.get(entity.entity_id) is entity
                      and registry.async_get(entity.entity_id) is entity.registry_entry)
        tokens.append((domain, id(component), identity))
        actual_rows = tuple((row[0], _fingerprint(row[1]), _fingerprint(row))
                            for row in projection)
        if (expected.projection is None or expected.rows is None):
            blockers.add('runtime_source_native_projection_unavailable')
        elif (_fingerprint(projection) != expected.projection or actual_rows != expected.rows):
            blockers.add('runtime_source_projection_mismatch')
        live = {entity.unique_id: entity for entity in entities}
        if len(live) != len(entities):
            raise SourceBindingError('runtime_source_runtime_unavailable')
        candidates = [row for row in registry_rows if row.get('platform') == domain
                      and row.get('entity_id', '').split('.', 1)[0] == domain]
        grouped = {}
        for row in candidates:
            grouped.setdefault(row['unique_id'], []).append(row)
        for key in sorted(source_keys | set(grouped) | set(live)):
            entries = grouped.get(key, ())
            if len(entries) != 1:
                blockers.add('runtime_source_registry_identity_unverified')
                continue
            row, entity = entries[0], live.get(key)
            current = registry.async_get(row['entity_id'])
            _need(current is not None and current.id == row['id']
                  and current.unique_id == key and current.platform == domain)
            if key not in source_keys:
                disposition = 'runtime_only' if entity is not None else 'registry_only'
                if entity is not None:
                    blockers.add('runtime_source_inventory_mismatch')
            elif row.get('disabled_by') is not None:
                disposition = 'registry_disabled'
                blockers.add('runtime_source_disabled_entry_unverified')
            elif entity is None:
                disposition = 'runtime_missing'
                blockers.add('runtime_source_registry_identity_unverified')
            else:
                disposition = 'loaded'
                _need(entity.entity_id == row['entity_id'] and entity.registry_entry is current)
            digest = hashlib.sha256(json.dumps(row, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            rows.append(BoundSourceRow(domain, key, row['entity_id'], row['id'], digest, disposition))
    return tuple(rows), tuple(sorted(blockers)), tuple(tokens)


async def async_bind_original_runtime(hass, receipt, evidence, *, guard):
    """Detached source/runtime observation before preparation; never a write fence.

    The exact ORIGINAL receipt guards all staging/auxiliary inputs. Existing
    native full-three readers bind raw storage and manager identity. A final
    source read and synchronous manager/runtime sample close late-await drift;
    apply still needs its own original CAS, lease and accepted receipt chain.
    """
    import asyncio
    import hashlib
    from .native_discovery_inputs import (async_observe_discovery_inputs,
                                          assert_discovery_managers_current)
    from .yaml_validation import _settled_executor, assert_validation_current
    def check():
        if guard() is False:
            raise PermissionError('runtime_source_permission_denied')
    check()
    _need(type(evidence) is OriginalSourceEvidence)
    evidence.assert_receipt(receipt)
    def result(rows=(), blockers=(), tokens=()):
        return BoundSourceRuntime(evidence.sha256, rows, blockers, tokens)
    if evidence.ledger.findings:
        return result(blockers=('runtime_source_inventory_unverified',))
    try:
        observed = await async_observe_discovery_inputs(hass, guard=check)
        check()
        _need(observed.version == receipt.version and observed.config_root == receipt.config_root)
        files = {path: (digest, device, inode) for path, digest, device, inode in receipt.source_files}
        for row in observed.files:
            _need(files.get('.storage/' + row.key) == (
                hashlib.sha256(row.raw).hexdigest(), *row.identity[:2]))
        await _settled_executor(hass, assert_validation_current, receipt)
        check()
    except PermissionError:
        raise
    except ValueError:
        return result(blockers=('runtime_source_inputs_changed',))
    try:
        first = _runtime_sample(hass, evidence, observed)
        await asyncio.sleep(0)
        check()
        await _settled_executor(hass, assert_validation_current, receipt)
        check()
        assert_discovery_managers_current(hass, observed, guard=check)
        last = _runtime_sample(hass, evidence, observed)
        if first != last:
            return result(blockers=('runtime_source_observation_changed',))
        return result(*last)
    except PermissionError:
        raise
    except (ValueError, KeyError, AttributeError, TypeError):
        return result(blockers=('runtime_source_runtime_unavailable',))
