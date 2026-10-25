"""Server-owned, read-only dependency capture and conflict preview.

Inventory completeness and semantic/runtime support are distinct. Detached
captures are preview evidence, never mutation authority or recovery baselines.
Only this backend chooses roots, native readers and resource identities.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
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


def _plain(value):
    """Thaw only detached server snapshots, including immutable native trees."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError('discovery_snapshot_type')


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


def _runtime_family(hass, domain):
    """Reuse exact native consumer projections without executing user actions."""
    from homeassistant.const import __version__
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from .yaml_runtime_observer import NativeYAMLRuntimeReadback, _pending_native_work
    from .yaml_validation_worker import _normal, _fingerprint
    try:
        if (__version__ not in ('2025.6.0', '2026.10.2')
                or _pending_native_work(hass, (domain,))):
            raise ValueError('runtime_unsettled')
        rows, identities = NativeYAMLRuntimeReadback(hass)._projection(domain)
        component = hass.data.get(DATA_INSTANCES, {}).get(domain)
        locators = {entity.unique_id: entity.entity_id for entity in component.entities}
        if (len(locators) != len(rows) or any(type(row[0]) is not str
                                            or row[0] not in locators for row in rows)):
            raise ValueError('runtime_identity_ambiguous')
        digest = _fingerprint(rows)
        family = _family(domain, {'scope': 'native_runtime_entities',
                                 'projection': _normal(rows)},
            [(locators[row[0]], row[0], _fingerprint(row)) for row in rows],
            blockers=('runtime_source_binding_unverified',))
        return family, (id(component), identities, digest)
    except PermissionError:
        return _unknown(domain, 'read_access_denied', denied=True), None
    except (ValueError, KeyError, TypeError, AttributeError):
        return _unknown(domain, 'runtime_inventory_unavailable'), None


def _consumer_family(name, outcome, mapping):
    if outcome.status != 'COMPLETE':
        return _unknown(name, outcome.reason, denied=outcome.status == 'DENIED')
    value = outcome.value
    blockers, rows = [], []
    if name == 'dashboard':
        from .dashboard_plan import plan_dashboard
        for snapshot in value.snapshots:
            row = _plain(snapshot)
            rows.append(('storage:' + _json(snapshot.dashboard.route).decode(),
                         snapshot.dashboard.key, _sha(_json(row))))
            if snapshot.config is not None:
                try:
                    blockers.extend(marker.code for marker in
                                    plan_dashboard(_plain(snapshot.config), mapping).markers)
                except (ValueError, TypeError, KeyError):
                    blockers.append('dashboard_semantics_unavailable')
        for source in value.yaml_sources:
            identity = 'yaml:' + _json(source.route).decode()
            rows.append((identity, identity, _sha(_json(_plain(source)))))
    elif name == 'energy':
        from .energy_plan import plan_energy
        rows.append(('energy', 'energy', value.revision))
        if value.value is not None:
            try:
                blockers.extend(marker.code for marker in
                                plan_energy(_plain(value.value), mapping).markers)
            except (ValueError, TypeError, KeyError):
                blockers.append('energy_semantics_unavailable')
    else:
        raise ValueError('discovery_consumer_family')
    return _family(name, _plain(value), rows, blockers=blockers)


async def async_discover(hass, proposal, guard):
    """Capture native sources and dependent families without any mutation API."""
    from homeassistant.const import __version__
    from .native_discovery_inputs import async_observe_discovery_inputs
    from .helper_native import inventory
    from .yaml_plan import plan_yaml
    from .native_input_baseline import _drained
    from .recorder_guard import NativeRecorderGuard, prepare as recorder_preflight
    from .history_guard import NativeHistoryGuard
    from .native_discovery_consumers import async_observe_discovery_consumers
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
    consumers = await async_observe_discovery_consumers(hass, guard=guard)
    guard()
    for name in ('dashboard', 'energy'):
        families[name] = _consumer_family(name, getattr(consumers, name), mapping)
    yaml_roots = (tuple(sorted({row.filename for row in consumers.dashboard.value.yaml_sources}))
                  if consumers.dashboard.status == 'COMPLETE' else ())
    runtime_tokens = {}
    for domain in ('automation', 'script'):
        families[domain], runtime_tokens[domain] = _runtime_family(hass, domain)
    # Runtime-known dashboard roots come from registered native managers.
    try:
        yaml = await _drained(hass.async_add_executor_job,
            lambda: plan_yaml(hass.config.config_dir, mapping, yaml_dashboards=yaml_roots))
        guard()
        payload = {'files': [asdict(row) for row in yaml.files],
                   'directories': [asdict(row) for row in yaml.directories],
                   'edges': [asdict(row) for row in yaml.edges]}
        families['yaml_sources'] = _family('yaml_sources', payload,
            [(row.path, row.path, row.sha256) for row in yaml.files],
            blockers=tuple(issue.code for issue in yaml.issues) +
                     ('runtime_source_coverage_unverified',))
        if consumers.dashboard.status == 'COMPLETE':
            files = {row.path: row for row in yaml.files}
            for source in consumers.dashboard.value.yaml_sources:
                file = files.get(source.filename)
                if (file is None or file.sha256 != source.sha256
                        or (file.device, file.inode) != source.file_identity[:2]):
                    raise ValueError('yaml_dashboard_source_changed')
        yaml_again = await _drained(hass.async_add_executor_job,
            lambda: plan_yaml(hass.config.config_dir, mapping, yaml_dashboards=yaml_roots))
        guard()
        if yaml_again != yaml:
            families['yaml_sources'] = _unknown('yaml_sources', 'source_changed_during_capture')
    except PermissionError:
        families['yaml_sources'] = _unknown('yaml_sources', 'read_access_denied', denied=True)
    except (ValueError, OSError):
        families['yaml_sources'] = _unknown('yaml_sources', 'yaml_graph_unavailable')
    guard()
    try:
        recorder = NativeRecorderGuard(hass)
        history = NativeHistoryGuard(recorder)
        metadata = await recorder.snapshot(mapping)
        guard()
        recorder_preflight(mapping, metadata)
        historical = await history.capture(mapping)
        guard()
        fresh_metadata = await recorder.snapshot(mapping)
        guard()
        unchanged = await history.verify(historical)
        guard()
        if fresh_metadata != metadata or unchanged is not True:
            raise ValueError('history_changed')
        # This is explicitly the mapping's old+new metadata scope, not a claim
        # to enumerate every Recorder series in the installation.
        scope = sorted(set(mapping) | set(mapping.values()))
        for name, section, stable_key in (
                ('history', 'states', 'metadata_id'),
                ('statistics', 'statistics', 'id')):
            rows = metadata[section]
            families[name] = _family(name,
                {'scope_entity_ids': scope, 'metadata': rows,
                 'history_snapshot': historical},
                [(source, section + ':' + str(row[stable_key]), _sha(_json(row)))
                 for source, row in rows.items()])
    except PermissionError:
        for name in ('history', 'statistics'):
            families[name] = _unknown(name, 'read_access_denied', denied=True)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError):
        for name in ('history', 'statistics'):
            families[name] = _unknown(name, 'recorder_scope_unavailable')
    guard()
    final_consumers = await async_observe_discovery_consumers(hass, guard=guard)
    guard()
    for name in ('dashboard', 'energy'):
        first, final = getattr(consumers, name), getattr(final_consumers, name)
        if first.status == 'COMPLETE' and final != first:
            families[name] = (_consumer_family(name, final, mapping)
                if final.status != 'COMPLETE' else _unknown(name, 'source_changed_during_capture'))
            conflicts.append('source_changed_during_capture')
            if name == 'dashboard':
                families['yaml_sources'] = _unknown('yaml_sources', 'source_changed_during_capture')
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
    for domain, token in runtime_tokens.items():
        if token is not None:
            final_family, final_token = _runtime_family(hass, domain)
            if token != final_token or final_family != families[domain]:
                families[domain] = _unknown(domain, 'source_changed_during_capture')
                conflicts.append('source_changed_during_capture')
    return DiscoveryCapture(uuid.uuid4().hex, __version__, proposal_json, bound,
        tuple(families[name] for name in FAMILIES), tuple(sorted(set(conflicts))))
