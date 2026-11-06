"""Independent, bounded filesystem readback for synthetic HA migrations.

The caller supplies the original snapshot, a fresh snapshot and independently
specified expected byte edits. This is evidence, not a migration engine. A
clean comparison is deliberately *not* a completeness verdict: remote consumers,
binary formats and omitted roots are explicitly reported as unverified.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import re

REQUIRED_FAMILIES = frozenset(('entity_registry', 'device_registry', 'area_registry',
    'floor_registry', 'label_registry', 'config_entries', 'yaml_graph',
    'dashboard', 'automation', 'script', 'template', 'integrations'))
_ORIGINAL = object()


@dataclass(frozen=True)
class Edit:
    path: str
    before: bytes
    after: bytes
    count: int


@dataclass(frozen=True)
class JSONStoreEdit(Edit):
    """Explicit native JSON Store edit; only serialization whitespace may differ."""


@dataclass(frozen=True)
class RowChange:
    """Independently specified own field change in a native registry row."""
    document: str
    uuid: str
    field: str
    before: object
    after: object
    # Native inverse may record its own immediate predecessor (e.g. Script
    # previous_unique_id). Every such value must be explicit, not ignored.
    restored: object = _ORIGINAL


@dataclass(frozen=True)
class ConsumerCheck:
    """Independent observed behavior against an explicit expected outcome."""
    name: str
    original: object
    expected_after: object
    observed: object


def discovery_coverage(capture):
    """Reuse the product's detached discovery only as coverage, never oracle.

    Its unknowns and blockers remain explicit; extra native registries and
    external consumers are not inferred from a successful three-document read.
    """
    rows = tuple(capture.families)
    names = [row.name for row in rows]
    if len(names) != len(set(names)):
        raise ValueError('ambiguous discovery families')
    result = {}
    aliases = {'yaml_sources': 'yaml_graph'}
    for row in rows:
        if row.name not in REQUIRED_FAMILIES and row.name not in ('yaml_sources', 'external'):
            continue
        if row.status == 'COMPLETE' and not row.blockers:
            status = 'COMPLETE'
        else:
            details = ','.join(sorted(row.blockers)) or row.reason or row.status
            status = row.status + ':' + details
        result[aliases.get(row.name, row.name)] = status
    external = result.pop('external', 'UNKNOWN:external_not_enumerated')
    return result, external


def _location_normalize(value):
    """Normalize only HA's unordered alias/label sets, not registry row order."""
    if isinstance(value, dict):
        return {k: (sorted(v) if k in ('aliases', 'labels') and isinstance(v, list)
                    and all(isinstance(x, str) for x in v) else _location_normalize(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_location_normalize(item) for item in value]
    return value


def _requires_semantics(name, raw):
    if b'{{' in raw or b'{%' in raw:
        return True
    # Custom Lovelace cards may consume IDs through their own implementation,
    # including when a registered YAML route has an arbitrary filename or an
    # encoded JSON colon and no literal old-ID token. Unrecognized occurrences
    # conservatively require a named consumer observation.
    if re.search(rb'custom\s*:', raw, re.IGNORECASE):
        return True
    if name.startswith('.storage/') or name.lower().endswith('.json'):
        try:
            return any('custom:' in value.lower() for value in _json_strings(_strict_json(raw)))
        except (ValueError, UnicodeError, RecursionError):
            pass  # Strict JSON diagnostics separately retain the UNKNOWN.
    return False


def _semantic_reason(name, raw):
    if b'{{' in raw or b'{%' in raw:
        return 'template_consumer_unverified'
    return 'custom_dashboard_consumer_unverified'


def native_location_snapshot(hass, root):
    """Read existing native area/floor/label managers and their disk envelopes.

    Never instantiate or save a manager. Absent/unsettled managers and missing
    or mismatching disk documents are specific UNKNOWNs, not empty inventories.
    Caller invokes on the HA event loop after settling native stores.
    """
    result = {}
    for family, module_name, members in (
            ('area_registry', 'area_registry', 'areas'),
            ('floor_registry', 'floor_registry', 'floors'),
            ('label_registry', 'label_registry', 'labels')):
        module = importlib.import_module('homeassistant.helpers.' + module_name)
        manager = hass.data.get(module.DATA_REGISTRY)
        path = Path(root) / '.storage' / module.STORAGE_KEY
        if manager is None or not hasattr(manager, members):
            result[family] = {'status': 'UNKNOWN:manager_not_loaded'}
            continue
        if path.is_symlink() or not path.is_file():
            result[family] = {'status': 'UNKNOWN:disk_document_unavailable'}
            continue
        try:
            native_before = _location_normalize(manager._data_to_save())
            first = path.stat()
            if first.st_size > 16_000_000:
                raise ValueError('location document too large')
            raw = path.read_bytes()
            second = path.stat()
            if (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) != (
                    second.st_dev, second.st_ino, second.st_size, second.st_mtime_ns, second.st_ctime_ns):
                raise ValueError('location source changed')
            disk = _location_normalize(_strict_json(raw)['data'])
            native_after = _location_normalize(manager._data_to_save())
            if not _same(native_before, disk) or not _same(native_before, native_after):
                raise ValueError('location runtime and disk differ')
            result[family] = {'status': 'COMPLETE', 'data': native_before,
                              'disk_sha256': hashlib.sha256(raw).hexdigest()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            result[family] = {'status': 'UNKNOWN:runtime_disk_unsettled_or_invalid'}
    return result


def native_empty_auth_snapshot(hass, root):
    """Qualify the empty or HTTP-content-only native fixture without exporting it.

    All values stay in memory: the returned evidence contains only the hash
    and a status. Nonempty authority, policies, references, dynamic expressions,
    unstable files and manager/disk mismatch remain explicit UNKNOWN.
    """
    path = Path(root) / '.storage/auth'
    unknown = {'status': 'UNKNOWN:private_auth_unverified'}
    try:
        manager = hass.auth._store
        store = manager._store
        if (not manager._loaded or store.key != 'auth' or store.version != 1
                or Path(store.path) != path or path.is_symlink() or not path.is_file()):
            return unknown
        first = path.stat()
        if first.st_size > 16_000_000:
            return unknown
        raw = path.read_bytes()
        last = path.stat()
        if (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) != (
                last.st_dev, last.st_ino, last.st_size, last.st_mtime_ns, last.st_ctime_ns):
            return unknown
        document = _strict_json(raw)
        data = document['data']
        if (document['key'] != 'auth' or document['version'] != 1
                or set(data) != {'users', 'groups', 'credentials', 'refresh_tokens'}
                or data['credentials'] != []
                or not isinstance(data['groups'], list) or len(data['groups']) != 3
                or any(type(group) is not dict or set(group) != {'id', 'name'} for group in data['groups'])
                or {group['id'] for group in data['groups']} != {'system-admin', 'system-users', 'system-read-only'}
                or not _same(data, manager._data_to_save())):
            return unknown
        if data['users'] or data['refresh_tokens']:
            if len(data['users']) != 1 or len(data['refresh_tokens']) != 1:
                return unknown
            user, token = data['users'][0], data['refresh_tokens'][0]
            if (set(user) != {'id', 'group_ids', 'is_owner', 'is_active', 'name', 'system_generated', 'local_only'}
                    or set(token) != {'id', 'user_id', 'client_id', 'client_name', 'client_icon', 'token_type',
                                     'created_at', 'access_token_expiration', 'token', 'jwt_key', 'last_used_at',
                                     'last_used_ip', 'expire_at', 'credential_id', 'version'}
                    or user['name'] != 'Home Assistant Content' or user['system_generated'] is not True
                    or user['is_active'] is not True or user['is_owner'] is not False
                    or user['group_ids'] != ['system-read-only'] or token['user_id'] != user['id']
                    or token['client_id'] is not None or token['credential_id'] is not None
                    or hass.data.get('http.auth') != token['id']):
                return unknown
            http_path = Path(root) / '.storage/http.auth'
            if http_path.is_symlink() or not http_path.is_file():
                return unknown
            http_document = _strict_json(http_path.read_bytes())
            if http_document.get('key') != 'http.auth' or http_document.get('data') != {'content_user': user['id']}:
                return unknown
        elif (getattr(hass, 'data', {}).get('http.auth') is not None
              or (Path(root) / '.storage/http.auth').exists()):
            return unknown
        entity = re.compile(r'(?<![\w.])[a-z_][a-z0-9_]*\.[a-z0-9_]+(?![\w.])')
        # Token material is an opaque credential, not a configuration expression.
        names = [group['name'] for group in data['groups']] + [user['name'] for user in data['users']]
        if any(not isinstance(value, str) or entity.search(value) or '{{' in value or '{%' in value for value in names):
            return unknown
        return {'status': 'COMPLETE', 'disk_sha256': hashlib.sha256(raw).hexdigest()}
    except (OSError, AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return unknown


def capture_tree(root, destination, *, inventory_files, families, external_consumers,
                 external_inventory_complete=False, recorder_databases=(), native_locations=None,
                 native_auth=None):
    """Capture all local configuration candidates, independent of mutation scope.

    ``inventory_files`` is the detached native/YAML discovery list of
    (relative path, SHA256). It supplies roots/coverage, never expected edits.
    Every other YAML/JSON/Store is captured too, including disabled and
    out-of-graph sources. Unsupported code, secrets, binary or unavailable
    sources and unknown external inventory remain explicit coverage gaps.
    The destination must be a new, private directory outside the source tree.
    """
    root, destination = Path(root), Path(destination)
    if root.is_symlink():
        raise ValueError('symlink source root')
    root = root.resolve(strict=True)
    if not root.is_dir() or destination.exists() or destination.is_symlink():
        raise ValueError('invalid capture directory')
    if destination.resolve().is_relative_to(root):
        raise ValueError('capture destination inside source')
    if not families or any(type(k) is not str or type(v) is not str for k, v in families.items()):
        raise ValueError('explicit discovery family status required')
    families = dict(families)
    locations = native_locations or {}
    authority = native_auth or {'status': 'UNKNOWN:private_auth_unverified'}
    for name, data in locations.items():
        if name not in ('area_registry', 'floor_registry', 'label_registry'):
            raise ValueError('invalid native location registry')
        families[name] = data['status']
    expected = dict(inventory_files)
    if len(expected) != len(inventory_files):
        raise ValueError('ambiguous inventory path')
    gaps = [('family:' + name, status) for name, status in sorted(families.items())
            if status != 'COMPLETE']
    gaps += [('family:' + name, 'not_enumerated') for name in sorted(REQUIRED_FAMILIES - families.keys())]
    gaps += [('family:' + name, 'runtime_snapshot_not_provided')
             for name in ('area_registry', 'floor_registry', 'label_registry')
             if families.get(name) == 'COMPLETE' and name not in locations]
    if not external_inventory_complete:
        gaps.append(('external_inventory', 'not_enumerated'))
    gaps += [('external:' + name, status) for name, status in sorted(external_consumers.items())
             if status != 'COMPLETE']
    destination.mkdir(mode=0o700, parents=True)
    captured, inventory_seen, semantic_sources, separate, directories = {}, set(), [], [], []
    # Capture only configuration-bearing source types; logs and databases are
    # not mistaken for text configuration or a Recorder continuity proof.
    paths = sorted(root.rglob('*'))
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            gaps.append((relative, 'symlink_unverified'))
            continue
        if path.is_dir():
            if relative not in ('.storage/ha_entity_renamer_backups', '.storage/ha_entity_renamer_pending'):
                directories.append(relative)
            continue
        if not path.is_file():
            gaps.append((relative, 'non_regular_source'))
            continue
        if (relative not in expected and re.fullmatch(
                r'\.storage/ha_entity_renamer_backups/yaml_[0-9a-f]{64}-[0-9a-f]{64}\.yaml-backup', relative)):
            separate.append((relative, 'renamer_backup_not_active_source'))
            continue
        if relative == '.storage/auth' and authority.get('status') == 'COMPLETE':
            if hashlib.sha256(path.read_bytes()).hexdigest() != authority.get('disk_sha256'):
                raise ValueError('private authority changed during capture')
            continue
        if (path.name in ('secrets.yaml', 'auth', 'auth_provider.homeassistant')
                or relative.startswith('.storage/ha_entity_renamer.')):
            if relative in expected or path.name in ('secrets.yaml', 'auth', 'auth_provider.homeassistant'):
                gaps.append((relative, 'private_or_authority_source_unverified'))
            continue
        candidate = (path.suffix.lower() in ('.yaml', '.yml', '.json')
                     or relative.startswith('.storage/') or relative in expected)
        if not candidate:
            recorder = next((db for db in recorder_databases
                             if relative in (db, db + '-wal', db + '-shm')), None)
            if recorder is not None:
                separate.append((relative, 'recorder_continuity_separate'))
            elif path.suffix.lower() in ('.db', '.sqlite', '.sqlite3') or relative.endswith(('-wal', '-shm')):
                gaps.append((relative, 'database_consumer_unverified'))
            elif path.suffix.lower() in ('.py', '.js', '.ts', '.sh'):
                gaps.append((relative, 'executable_consumer_unverified'))
            elif path.suffix.lower() != '.log':
                gaps.append((relative, 'unclassified_source'))
            continue
        try:
            first = path.stat()
            if first.st_size > 16_000_000:
                gaps.append((relative, 'source_too_large'))
                continue
            raw = path.read_bytes()
            second = path.stat()
            if (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) != (
                    second.st_dev, second.st_ino, second.st_size, second.st_mtime_ns, second.st_ctime_ns):
                raise ValueError('source changed during capture: ' + relative)
            digest = hashlib.sha256(raw).hexdigest()
            if relative in expected:
                if digest != expected[relative]:
                    raise ValueError('discovery source changed: ' + relative)
                inventory_seen.add(relative)
            try:
                raw.decode('utf-8')
                if b'\0' in raw:
                    raise UnicodeError('binary')
            except UnicodeError:
                gaps.append((relative, 'opaque_source'))
            target = destination / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(raw)
            captured[relative] = digest
            if _requires_semantics(relative, raw):
                semantic_sources.append(relative)
        except PermissionError:
            gaps.append((relative, 'read_denied'))
    gaps += [(name, 'discovered_source_missing') for name in sorted(set(expected) - inventory_seen)]
    if authority.get('status') == 'COMPLETE' and not (root / '.storage/auth').is_file():
        raise ValueError('private authority disappeared during capture')
    for name, info in locations.items():
        if info['status'] == 'COMPLETE':
            path = '.storage/core.' + name
            if captured.get(path) != info['disk_sha256']:
                raise ValueError('native location source changed: ' + name)
            if not _same(_location_normalize(_strict_json((destination / path).read_bytes())['data']), info['data']):
                raise ValueError('native location capture mismatch: ' + name)
    # A changed traversal membership is not a coherent point-in-time capture.
    if [p.relative_to(root).as_posix() for p in sorted(root.rglob('*'))] != [
            p.relative_to(root).as_posix() for p in paths]:
        raise ValueError('source membership changed during capture')
    return {'files': captured, 'gaps': sorted(set(gaps)),
            'semantic_sources': sorted(semantic_sources), 'separate': sorted(separate),
            'directories': sorted(directories),
            'native_locations': locations,
            'native_auth': authority,
            'external_consumers': dict(external_consumers),
            'complete': not gaps}


def _files(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('snapshot root must be a real directory')
    result = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('symlink is outside bounded readback: ' + str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
        elif not path.is_dir():
            raise ValueError('non-regular entry: ' + str(path))
    return result


def _same(a, b):
    # Python equality conflates False/0 and True/1, which JSON preserves.
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def _strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('non-finite JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _json_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _json_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_strings(child)


def _registry(before, after, mappings, reverse, changes=(), *, group='entities', key_field='id'):
    """Compare native document and UUID rows including explicit own changes."""
    b, a = _strict_json(before), _strict_json(after)
    b_rows = b['data'].pop(group)
    a_rows = a['data'].pop(group)
    owned = {u for u, _, _ in mappings} | {change.uuid for change in changes}
    def split(rows, expected, original):
        ids = [r[key_field] for r in rows]
        if len(ids) != len(set(ids)) or any(ids.count(uuid) != 1 for uuid in owned):
            raise ValueError('ambiguous registry UUID')
        own, times = {}, {}
        for uuid in owned:
            row = dict(rows[ids.index(uuid)])
            if uuid in expected and row['entity_id'] != expected[uuid]:
                raise ValueError('unexpected registry entity ID')
            for change in (c for c in changes if c.uuid == uuid):
                value = (change.before if original else
                         (change.before if change.restored is _ORIGINAL else change.restored)
                         if reverse else change.after)
                if change.field not in row or not _same(row[change.field], value):
                    raise ValueError('unexpected explicit own field: ' + change.field)
                row[change.field] = change.before
            try:
                stamp = datetime.fromisoformat(row.pop('modified_at').replace('Z', '+00:00'))
                if stamp.utcoffset() is None:
                    raise ValueError('naive timestamp')
                times[uuid] = stamp
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise ValueError('unverified native modified_at') from error
            if uuid in expected:
                row.pop('entity_id')
            own[uuid] = row
        return own, [r for r in rows if r[key_field] not in owned], times
    own_b, foreign_b, times_b = split(b_rows, {u: o for u, o, _ in mappings}, True)
    own_a, foreign_a, times_a = split(a_rows, {u: o if reverse else n for u, o, n in mappings}, False)
    return (_same(b, a) and _same(own_b, own_a) and _same(foreign_b, foreign_a)
            and all(times_a[u] >= times_b[u] for u in times_b))


def compare(original, observed, *, old=None, new=None, uuid=None, mappings=None, edits=(), row_changes=(),
            reverse=False, captures=None, consumer_checks=(), consumer_inventory=None,
            consumer_inventory_complete=False, source_consumers=None):
    """Compare all captured files to SAME original; return findings and coverage.

    Edits refer to literal original bytes and exact expected multiplicities.
    Reverse compares directly to the original without applying those edits.
    Whole-document registry comparison preserves foreign order and all fields;
    only the source row's native modified_at and position are relaxed.
    """
    row_changes = tuple(row_changes)
    consumer_checks = tuple(consumer_checks)
    if mappings is None:
        mappings = [] if uuid is None and old is None and new is None else [(uuid, old, new)]
    mappings = tuple(mappings)
    if ((not mappings and not row_changes) or any(not all(isinstance(x, str) and x for x in row) or len(row) != 3
                            for row in mappings)
            or len({row[0] for row in mappings}) != len(mappings)
            or len({row[1] for row in mappings}) != len(mappings)
            or len({row[2] for row in mappings}) != len(mappings)
            or bool({row[1] for row in mappings} & {row[2] for row in mappings})
            or any(o.partition('.')[0] != n.partition('.')[0] or '.' not in o or '.' not in n
                   for _, o, n in mappings)):
        raise ValueError('invalid or ambiguous batch mapping')
    before, after = _files(original), _files(observed)
    findings = []
    allowed = {'.storage/core.entity_registry': 'entities', '.storage/core.device_registry': 'devices',
               '.storage/core.area_registry': 'areas', '.storage/core.floor_registry': 'floors',
               '.storage/core.label_registry': 'labels'}
    change_keys = [(c.document, c.uuid, c.field) for c in row_changes]
    if (len(set(change_keys)) != len(change_keys)
            or any(c.document not in allowed or not c.uuid or not c.field
                   or c.field in ('id', 'entity_id', 'modified_at') for c in row_changes)):
        raise ValueError('invalid or ambiguous own row changes')
    if any(c.document not in before or c.document not in after for c in row_changes):
        raise ValueError('changed registry document missing')
    if set(before) != set(after):
        findings.append(('file_membership', sorted(set(before) ^ set(after))))
    by_path = {}
    for edit in edits:
        if (type(edit) not in (Edit, JSONStoreEdit) or not edit.before or edit.count <= 0
                or (type(edit) is JSONStoreEdit and edit.path != '.storage/lovelace'
                    and not edit.path.startswith('.storage/lovelace.'))):
            raise ValueError('invalid expected edit')
        by_path.setdefault(edit.path, []).append(edit)
        if edit.path not in before or before[edit.path].count(edit.before) != edit.count:
            raise ValueError('expected edit does not match original: ' + edit.path)
    if any(any(type(edit) is JSONStoreEdit for edit in group)
           and not all(type(edit) is JSONStoreEdit for edit in group) for group in by_path.values()):
        raise ValueError('mixed JSON and byte edits are ambiguous')
    registry = '.storage/core.entity_registry'
    if registry not in before or registry not in after:
        findings.append(('missing_entity_registry', registry))
    for name in sorted(set(before) & set(after)):
        if name == registry:
            if not _registry(before[name], after[name], mappings, reverse,
                             [c for c in row_changes if c.document == name]):
                findings.append(('registry_changed', name))
            continue
        if name == '.storage/core.device_registry' and any(c.document == name for c in row_changes):
            if not _registry(before[name], after[name], (), reverse,
                             [c for c in row_changes if c.document == name], group='devices'):
                findings.append(('registry_changed', name))
            continue
        location_keys = {'.storage/core.area_registry': ('areas', 'id'),
                         '.storage/core.floor_registry': ('floors', 'floor_id'),
                         '.storage/core.label_registry': ('labels', 'label_id')}
        if name in location_keys and any(c.document == name for c in row_changes):
            group, key = location_keys[name]
            bdoc = json.dumps(_location_normalize(_strict_json(before[name]))).encode()
            adoc = json.dumps(_location_normalize(_strict_json(after[name]))).encode()
            if not _registry(bdoc, adoc, (), reverse, [c for c in row_changes if c.document == name],
                             group=group, key_field=key):
                findings.append(('registry_changed', name))
            continue
        expected = before[name]
        if not reverse and name in by_path:
            for edit in by_path[name]:
                expected = expected.replace(edit.before, edit.after)
        json_store = name in by_path and all(type(edit) is JSONStoreEdit for edit in by_path[name])
        if json_store:
            try:
                matches = json.dumps(_strict_json(expected), sort_keys=True, separators=(',', ':'), allow_nan=False) == json.dumps(
                    _strict_json(after[name]), sort_keys=True, separators=(',', ':'), allow_nan=False)
            except (ValueError, TypeError, UnicodeError):
                matches = False
        else:
            matches = expected == after[name]
        if not matches:
            findings.append(('unexpected_change', name))
    # Search independently across *all* captured UTF-8 text, not just edits.
    # Exact tokens are useful omissions evidence, but not a semantic inventory.
    tokens = [re.compile(rb'(?<![a-zA-Z0-9_\.])' + re.escape((new if reverse else old).encode())
                         + rb'(?![a-zA-Z0-9_])') for _, old, new in mappings]
    opaque = []
    semantic_gaps = []
    stale = []
    for name, data in sorted(after.items()):
        try:
            data.decode('utf-8')
            if b'\0' in data:
                raise UnicodeError('binary')
        except UnicodeError:
            opaque.append(name)
            continue
        found = any(token.search(data) for token in tokens)
        if name.startswith('.storage/') or name.lower().endswith('.json'):
            try:
                parsed = _strict_json(data)
                found = found or any(token.search(value.encode('utf-8'))
                                     for value in _json_strings(parsed) for token in tokens)
            except (ValueError, UnicodeError, RecursionError):
                semantic_gaps.append((name, 'json_semantics_unverified'))
        elif name.lower().endswith(('.yaml', '.yml')) and re.search(
                rb'\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})', data):
            semantic_gaps.append((name, 'yaml_escaped_scalar_unverified'))
        if found:
            stale.append(name)
    # Baseline ambiguity cannot be erased by a later syntactically valid file.
    for name, data in sorted(before.items()):
        try:
            data.decode('utf-8')
            if b'\0' in data:
                raise UnicodeError('binary')
            if name.startswith('.storage/') or name.lower().endswith('.json'):
                _strict_json(data)
            elif name.lower().endswith(('.yaml', '.yml')) and re.search(
                    rb'\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})', data):
                semantic_gaps.append((name, 'yaml_escaped_scalar_unverified'))
        except UnicodeError:
            semantic_gaps.append((name, 'original_opaque_source'))
        except (ValueError, RecursionError):
            semantic_gaps.append((name, 'original_json_semantics_unverified'))
    if stale:
        findings.append(('remaining_reference', stale))
    gaps = list(semantic_gaps)
    semantic_sources, external_names = set(), set()
    if captures is None:
        gaps.append(('capture', 'full_source_inventory_not_provided'))
    else:
        if len(captures) != 2:
            raise ValueError('original and observed captures required')
        if captures[0]['directories'] != captures[1]['directories']:
            findings.append(('directory_membership', sorted(set(captures[0]['directories']) ^ set(captures[1]['directories']))))
        left_auth, right_auth = (info.get('native_auth', {}) for info in captures)
        if left_auth.get('status') == right_auth.get('status') == 'COMPLETE':
            if left_auth['disk_sha256'] != right_auth['disk_sha256']:
                findings.append(('private_authority_changed', '.storage/auth'))
        elif (left_auth.get('status') == 'COMPLETE') != (right_auth.get('status') == 'COMPLETE'):
            findings.append(('private_authority_changed_or_unverified', '.storage/auth'))
        if captures[0]['external_consumers'] != captures[1]['external_consumers']:
            first, second = captures[0]['external_consumers'], captures[1]['external_consumers']
            findings.append(('external_inventory_changed', sorted(
                name for name in set(first) | set(second) if first.get(name) != second.get(name))))
        for family, (path, group, key_field) in {
                'area_registry': ('.storage/core.area_registry', 'areas', 'id'),
                'floor_registry': ('.storage/core.floor_registry', 'floors', 'floor_id'),
                'label_registry': ('.storage/core.label_registry', 'labels', 'label_id')}.items():
            left = captures[0].get('native_locations', {}).get(family, {})
            right = captures[1].get('native_locations', {}).get(family, {})
            if left.get('status') == right.get('status') == 'COMPLETE':
                changes = [c for c in row_changes if c.document == path]
                before_doc = json.dumps({'data': left['data']}).encode()
                after_doc = json.dumps({'data': right['data']}).encode()
                if not _registry(before_doc, after_doc, (), reverse, changes, group=group, key_field=key_field):
                    findings.append(('native_registry_changed', family))
        for label, info, files in zip(('original', 'observed'), captures, (before, after), strict=True):
            digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
            if info['files'] != digests:
                raise ValueError('captured evidence changed: ' + label)
            for family, location in info.get('native_locations', {}).items():
                if location.get('status') != 'COMPLETE':
                    continue
                path = '.storage/core.' + family
                if (digests.get(path) != location.get('disk_sha256') or path not in files
                        or not _same(_location_normalize(_strict_json(files[path])['data']), location['data'])):
                    raise ValueError('native location evidence changed: ' + label + ':' + family)
            detected = sorted(name for name, data in files.items() if _requires_semantics(name, data))
            if info['semantic_sources'] != detected:
                raise ValueError('semantic source evidence changed: ' + label)
            semantic_sources.update(detected)
            external_names.update(name for name, status in info['external_consumers'].items()
                                  if status == 'COMPLETE')
            gaps.extend((label + ':' + path, reason) for path, reason in info['gaps'])
    if not consumer_inventory_complete or consumer_inventory is None:
        gaps.append(('consumers', 'semantic_inventory_not_provided'))
        gaps.extend((path, _semantic_reason(path, after.get(path, before.get(path, b''))))
                    for path in sorted(semantic_sources))
    else:
        if len({check.name for check in consumer_checks}) != len(consumer_checks):
            raise ValueError('ambiguous consumer evidence')
        checks = {check.name: check for check in consumer_checks}
        for name in sorted(external_names):
            if consumer_inventory.get(name) != 'SUPPORTED' or name not in checks:
                gaps.append(('external:' + name, 'behavior_not_observed'))
        for name, status in sorted(consumer_inventory.items()):
            if status != 'SUPPORTED':
                gaps.append(('consumer:' + name, status))
            elif name not in checks:
                gaps.append(('consumer:' + name, 'behavior_not_observed'))
        if set(checks) - set(consumer_inventory):
            raise ValueError('consumer evidence absent from inventory')
        for check in consumer_checks:
            expected_value = check.original if reverse else check.expected_after
            if not _same(check.observed, expected_value):
                findings.append(('consumer_outcome', check.name))
        bindings = source_consumers or {}
        for path in sorted(semantic_sources):
            names = bindings.get(path, ())
            if not names:
                gaps.append((path, _semantic_reason(path, after.get(path, before.get(path, b'')))))
            else:
                for name in names:
                    if consumer_inventory.get(name) != 'SUPPORTED' or name not in checks:
                        gaps.append((path, 'missing_observation:' + name))
    gaps.extend((name, 'opaque_source') for name in opaque)
    return {'findings': findings, 'captured_files': len(before), 'opaque_files': opaque,
            'unverified': sorted(set(gaps)), 'complete': not gaps and not findings}
