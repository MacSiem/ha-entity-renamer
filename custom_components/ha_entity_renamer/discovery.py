"""Server-owned, read-only dependency capture and conflict preview.

Inventory completeness and semantic/runtime support are distinct. Detached
captures are preview evidence, never mutation authority or recovery baselines.
Only this backend chooses roots, native readers and resource identities.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import uuid

from .manifest import bind_registry, parse_manifest

FAMILIES = ('entity_registry', 'device_registry', 'automation', 'script',
            'dashboard', 'template', 'energy', 'powercalc', 'history',
            'statistics', 'external', 'config_entries', 'yaml_sources')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class Family:
    name: str
    status: str
    reason: str
    payload_json: bytes | None
    rows: tuple[tuple[str, str, str], ...]
    blockers: tuple[str, ...] = ()

    @property
    def revision(self):
        return None if self.payload_json is None else _sha(self.payload_json)


def _family(name, payload, rows, *, blockers=()):
    rows = tuple(sorted(rows))
    if (name not in FAMILIES or len({r[0] for r in rows}) != len(rows)
            or len({r[1] for r in rows}) != len(rows)):
        raise ValueError('discovery_ambiguous_identity')
    return Family(name, 'COMPLETE', '', _json(payload), rows,
                  tuple(sorted(set(blockers))))


def _unknown(name, reason, *, denied=False):
    return Family(name, 'DENIED' if denied else 'UNKNOWN', reason, None, ())


@dataclass(frozen=True)
class DiscoveryCapture:
    capture_id: str
    version: str
    proposal_json: bytes
    bound_manifest_json: bytes | None
    families: tuple[Family, ...]
    conflicts: tuple[str, ...]

    @property
    def digest(self):
        return _sha(_json([self.version, _sha(self.proposal_json),
            None if self.bound_manifest_json is None else _sha(self.bound_manifest_json),
            [[f.name, f.status, f.reason, f.revision, f.rows, f.blockers]
             for f in self.families], self.conflicts]))

    def summary(self):
        # No local identifiers, YAML, ConfigEntry options/data, paths or secrets.
        return {'capture_id': self.capture_id, 'digest': self.digest,
            'version': self.version, 'migration_ready': False,
            'families': [{'family': f.name, 'status': f.status,
                'reason': f.reason, 'count': len(f.rows) if f.status == 'COMPLETE' else None,
                'revision': f.revision, 'blockers': list(f.blockers)} for f in self.families],
            'conflicts': list(self.conflicts)}


def compose_preview(capture):
    """The initial read-only composer; never manufactures Steps or permission."""
    if (type(capture) is not DiscoveryCapture
            or tuple(f.name for f in capture.families) != FAMILIES):
        raise ValueError('discovery_capture_invalid')
    result = capture.summary()
    manifest = parse_manifest(json.loads(capture.proposal_json))
    result['changes'] = {'entities': len(manifest.entities), 'devices': len(manifest.devices),
                         'statistics': len(manifest.statistics)}
    result['inventory_complete'] = all(f.status == 'COMPLETE' for f in capture.families)
    result['plan_supported'] = (result['inventory_complete'] and not capture.conflicts
                               and not any(f.blockers for f in capture.families))
    # Further preparation/lifecycle/full continuity gates remain mandatory.
    return result


def _proposal(document):
    try:
        raw = _json(document)
        if len(raw) > 2_000_000:
            raise ValueError
        detached = json.loads(raw)
        if (type(detached) is not dict or set(detached) - {'entities', 'devices', 'statistics'}):
            raise ValueError
        manifest = parse_manifest(detached)
        if not manifest.entities and not manifest.devices:
            raise ValueError
        return raw, manifest
    except (TypeError, ValueError, RecursionError):
        raise ValueError('discovery_invalid_proposal') from None


def _native_families(observed):
    documents = {row.key: json.loads(row.manager_json) for row in observed.files}
    entities = documents['core.entity_registry']['entities']
    devices = documents['core.device_registry']['devices']
    entries = documents['core.config_entries']['entries']
    result = {}
    for family, key, rows, locator in (
            ('entity_registry', 'core.entity_registry', entities, 'entity_id'),
            ('device_registry', 'core.device_registry', devices, 'id'),
            ('config_entries', 'core.config_entries', entries, 'entry_id')):
        identity = 'entry_id' if family == 'config_entries' else 'id'
        file = next(row for row in observed.files if row.key == key)
        payload = {'document': documents[key], 'disk_sha256': _sha(file.raw),
                   'identity': file.identity, 'uid': file.uid, 'gid': file.gid}
        result[family] = _family(family, payload,
            [(row[locator], row[identity], _sha(_json(row))) for row in rows])
    # ConfigEntry domain classification is inventory only. Its real semantic
    # planners are read separately through helper_native, preserving all fields.
    for domain in ('template', 'powercalc'):
        rows = [row for row in entries if row['domain'] == domain]
        partial = _family(domain, rows,
            [(row['entry_id'], row['entry_id'], _sha(_json(row))) for row in rows])
        result[domain] = Family(domain, 'UNKNOWN', 'runtime_source_coverage_unverified',
                               partial.payload_json, partial.rows)
    return result, entities, devices


async def async_discover(hass, proposal, guard):
    """Capture native sources and dependent families without any mutation API."""
    from homeassistant.const import __version__
    from .native_discovery_inputs import async_observe_discovery_inputs
    from .helper_native import inventory
    from .yaml_plan import plan_yaml
    from .native_input_baseline import _drained
    proposal_json, manifest = _proposal(proposal)
    mapping = {row.old_id: row.new_id for row in manifest.entities if row.old_id != row.new_id}
    families = {name: _unknown(name, 'source_inventory_unavailable') for name in FAMILIES}
    conflicts, bound = [], None
    native = None
    guard()
    try:
        native = await async_observe_discovery_inputs(hass, guard=guard)
        guard()
        values, entities, devices = _native_families(native)
        families.update(values)
        try:
            bound = _json(asdict(bind_registry(manifest, entities, devices)))
        except ValueError:
            conflicts.append('registry_mapping_conflict')
        plans = inventory(hass, mapping)
        # Preserve all ConfigEntry/subentry planner markers, including domains
        # outside the rename target and unchanged consumers.
        markers = tuple(marker.code for plan in plans for marker in plan.markers)
        current = families['config_entries']
        families['config_entries'] = Family(current.name, current.status, current.reason,
            current.payload_json, current.rows, tuple(sorted(set(markers))))
    except PermissionError:
        for name in ('entity_registry', 'device_registry', 'config_entries', 'template', 'powercalc'):
            families[name] = _unknown(name, 'read_access_denied', denied=True)
    except (ValueError, OSError, KeyError, TypeError):
        for name in ('entity_registry', 'device_registry', 'config_entries', 'template', 'powercalc'):
            families[name] = _unknown(name, 'native_input_unavailable')
    guard()
    # YAML dashboard filenames are supplied only by server runtime discovery.
    # Until that runtime-source binding is proved, keep those family claims
    # UNKNOWN rather than silently dropping sources from the graph.
    try:
        yaml = await _drained(hass.async_add_executor_job,
            lambda: plan_yaml(hass.config.config_dir, mapping))
        guard()
        payload = {'files': [asdict(row) for row in yaml.files],
                   'directories': [asdict(row) for row in yaml.directories],
                   'edges': [asdict(row) for row in yaml.edges]}
        families['yaml_sources'] = _family('yaml_sources', payload,
            [(row.path, row.path, row.sha256) for row in yaml.files],
            blockers=tuple(issue.code for issue in yaml.issues) +
                     ('runtime_source_coverage_unverified',))
        yaml_again = await _drained(hass.async_add_executor_job,
            lambda: plan_yaml(hass.config.config_dir, mapping))
        guard()
        if yaml_again != yaml:
            families['yaml_sources'] = _unknown('yaml_sources', 'source_changed_during_capture')
    except PermissionError:
        families['yaml_sources'] = _unknown('yaml_sources', 'read_access_denied', denied=True)
    except (ValueError, OSError):
        families['yaml_sources'] = _unknown('yaml_sources', 'yaml_graph_unavailable')
    guard()
    # A member added during any await invalidates every native family, even
    # when it was not a changed target. Never learn a replacement snapshot.
    if native is not None:
        try:
            final = await async_observe_discovery_inputs(hass, guard=guard)
            guard()
            if final != native:
                raise ValueError('changed')
        except (ValueError, OSError):
            for name in ('entity_registry', 'device_registry', 'config_entries', 'template', 'powercalc'):
                families[name] = _unknown(name, 'source_changed_during_capture')
            bound = None
            conflicts.append('source_changed_during_capture')
    guard()
    return DiscoveryCapture(uuid.uuid4().hex, __version__, proposal_json, bound,
        tuple(families[name] for name in FAMILIES), tuple(sorted(set(conflicts))))
