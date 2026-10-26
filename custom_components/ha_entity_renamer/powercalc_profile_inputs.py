"""Same-Plan offline LUT input read-set and isolated ORIGINAL compiler.

Only the isolated checker creates official library/profile objects. Live readers
never resolve a profile, execute its calculator, warm a cache or write a Store.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat


def need(value, reason):
    if not value:
        raise ValueError('powercalc_profile_' + reason) from None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode()).hexdigest()


def _tree(value):
    if type(value) is float:
        need(math.isfinite(value), 'nonfinite_lut')
        return ['float', value.hex()]
    if type(value) is int:
        return ['int', value]
    need(type(value) is dict and all(type(k) is int for k in value), 'lut_shape')
    return [[k, _tree(v)] for k, v in sorted(value.items())]


def table_digest(table):
    return digest(_tree(table))


def parse_table(raw, mode, *, compressed):
    """Pure bounded decoder of captured ORIGINAL bytes; never a cache writer."""
    if compressed:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as source:
            raw = source.read(10 * 1024 * 1024 + 1)
    need(len(raw) <= 10 * 1024 * 1024, 'csv_size')
    rows = csv.reader(io.StringIO(raw.decode('utf-8')))
    next(rows)
    table = {}
    for row in rows:
        if mode == 'brightness':
            need(len(row) == 2, 'csv_shape')
            table[int(row[0])] = float(row[1])
        elif mode == 'color_temp':
            need(len(row) == 3, 'csv_shape')
            table.setdefault(int(row[0]), {})[int(row[1])] = float(row[2])
        elif mode == 'hs':
            need(len(row) == 4, 'csv_shape')
            table.setdefault(int(row[0]), {}).setdefault(int(row[1]), {})[int(row[2])] = float(row[3])
        else:
            need(False, 'mode_unverified')
    need(bool(table), 'empty_lut')
    table_digest(table)
    return table


@dataclass(frozen=True)
class PreparedProfileFiles:
    """Detached comparison input, never a receipt or new source authority."""
    receipt_json: bytes
    closed_graph_json: bytes
    files: tuple[tuple[str, str], ...]


def prepare_profile_files(receipt, original):
    """Executor-only physical read against the SAME sealed ORIGINAL graph.

    The isolated worker already compiled all variants. Do not decode gzip/CSV
    again in LIVE samples or adopt newly read content as a configuration graph.
    """
    from .native_input_baseline import _receipt_bytes
    from .powercalc_source_binding import OriginalPowercalcSources
    from .yaml_validation import assert_validation_current
    need(type(original) is OriginalPowercalcSources
         and original.receipt_json == _receipt_bytes(receipt)
         and type(original.closed_graph_json) is bytes, 'original_sources_required')
    graph = json.loads(original.closed_graph_json)
    expected = tuple(tuple(row) for row in graph['files'])
    sources = {row[0]: row[1] for row in receipt.source_files}
    need(all(sources.get(path) == checksum for path, checksum in expected),
         'profile_receipt_mismatch')
    bundle = read_bundle(receipt.config_root)
    actual = tuple(sorted((path, hashlib.sha256(raw).hexdigest()) for path, raw in bundle.items()))
    need(actual == expected, 'original_profile_bytes_changed')
    assert_validation_current(receipt)
    return PreparedProfileFiles(original.receipt_json, original.closed_graph_json, actual)


def _capture_inputs(root):
    from .yaml_plan import _Reader, Limits, FileSnapshot, PowercalcSourceInputs
    from .yaml_validation import _STORAGE, _TREES
    from .native_input_baseline import _open_absolute_directory
    limits = Limits()
    held = _open_absolute_directory(root)
    reader = None
    files, directories, total = [], [], 0
    try:
        reader = _Reader(root, limits)
        a, b = os.fstat(held), os.fstat(reader.root)
        need((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino), 'root_changed')
        def directory(path, names=None):
            fd = os.dup(reader.root) if path == '.' else reader.open(path, directory=True)
            try:
                rows = tuple(sorted((entry.name, (s := entry.stat(follow_symlinks=False)).st_mode,
                    s.st_dev, s.st_ino) for entry in os.scandir(fd)
                    if names is None or entry.name in names))
            finally:
                os.close(fd)
            need(len(rows) <= limits.max_directory_entries, 'directory_limit')
            directories.append((path, rows, None if names is None else tuple(sorted(names))))
            return rows
        directory('.', _TREES | {'.storage', 'powercalc', 'powercalc-custom-models',
                                 'configuration.yaml', 'secrets.yaml'})
        directory('powercalc', {'profiles'})
        directory('.storage', _STORAGE | {'energy', 'powercalc_profiles'})
        directory('custom_components/powercalc')
        for unsupported in ('powercalc-custom-models', '.storage/powercalc_profiles',
                            'custom_components/powercalc/custom_data'):
            try:
                fd = reader.open(unsupported, directory=True)
            except FileNotFoundError:
                continue
            os.close(fd)
            need(False, 'alternative_loader_unverified')
        def capture_file(path):
            nonlocal total
            raw, info = reader.read(path)
            total += len(raw)
            need(total <= limits.max_total_bytes and len(files) < limits.max_files, 'input_limit')
            files.append(FileSnapshot(path, hashlib.sha256(raw).hexdigest(), len(raw),
                                      info.st_dev, info.st_ino))
        def walk(path, depth=0):
            need(depth <= limits.max_depth, 'input_depth')
            for name, mode, _, _ in directory(path):
                child = path + '/' + name
                if stat.S_ISDIR(mode):
                    walk(child, depth + 1)
                else:
                    need(stat.S_ISREG(mode), 'unsafe_input')
                    capture_file(child)
        walk('powercalc/profiles')
        try:
            capture_file('.storage/energy')
        except FileNotFoundError:
            pass
        return PowercalcSourceInputs(tuple(sorted(files, key=lambda r: r.path)),
                                     tuple(sorted(directories)))
    finally:
        if reader is not None:
            os.close(reader.root)
        os.close(held)


async def async_add_powercalc_source_inputs(hass, plan, *, guard):
    """Prepare one explicit same-Plan local profile read-set before validation."""
    from dataclasses import replace
    from .yaml_plan import Plan
    from .yaml_validation import _settled_executor
    from .powercalc_source_binding import PowercalcVendor
    from .powercalc_runtime_observer import _guard
    need(type(plan) is Plan and plan.powercalc_inputs is None, 'fresh_plan_required')
    _guard(guard)
    vendor = PowercalcVendor(hass, with_profiles=True)
    await _settled_executor(hass, vendor.sources)
    _guard(guard)
    inputs = await _settled_executor(hass, _capture_inputs, hass.config.config_dir)
    _guard(guard)
    def final():
        vendor.sources()
        need(_capture_inputs(hass.config.config_dir) == inputs, 'inputs_changed')
    await _settled_executor(hass, final)
    vendor.bindings()
    _guard(guard)
    need(not ({r.path for r in plan.files} & {r.path for r in inputs.files}), 'yaml_overlap')
    return replace(plan, powercalc_inputs=inputs)


def source_config(cfg):
    """One local LUT source subset, with Energy explicit; no hidden overrides."""
    allowed = {'name', 'entity_id', 'unique_id', 'sensor_type', 'manufacturer', 'model',
        'mode', 'create_energy_sensor', '_power_entity', '_energy_entity'}
    need(type(cfg) is dict and not set(cfg) - allowed
         and cfg.get('sensor_type', 'virtual_power') == 'virtual_power'
         and cfg.get('mode', 'lut') == 'lut' and cfg.get('create_energy_sensor') is True
         and type(cfg.get('entity_id')) is str and cfg['entity_id'].startswith('light.')
         and type(cfg.get('manufacturer')) is str and type(cfg.get('model')) is str,
         'source_variant_unverified')
    return {key: cfg[key] for key in ('entity_id', 'manufacturer', 'model')}


def profile_json(value):
    need(type(value) is dict and value.get('calculation_strategy') == 'lut', 'strategy_unverified')
    controls = ('linked_profile', 'linked_lut', 'fields', 'sensor_config', 'multiply_factor',
        'calculation_enabled_condition', 'fixed_config', 'linear_config', 'composite_config',
        'multi_switch_config', 'playbook_config', 'only_self_usage')
    need(not any(value.get(k) for k in controls) and not value.get('standby_power_on'),
         'profile_adjustment_unverified')
    for key in ('standby_power', 'standby_power_on'):
        number = value.get(key, 0)
        need(type(number) in (int, float) and math.isfinite(number), 'standby_unverified')


def compile_profile(directory, base, subs, bundle):
    """Versioned ORIGINAL graph, including every inactive native sub-profile."""
    profile_json(base)
    selector = base.get('sub_profile_select')
    if selector is not None:
        need(type(selector) is dict and set(selector) == {'default', 'matchers'}
             and type(selector['matchers']) is list and len(selector['matchers']) == 1,
             'selector_variant_unverified')
        matcher = selector['matchers'][0]
        need(type(matcher) is dict and set(matcher) == {'type', 'entity_id', 'map'}
             and matcher['type'] == 'entity_state' and type(matcher['entity_id']) is str
             and type(matcher['map']) is dict
             and all(type(k) is str and type(v) is str for k, v in matcher['map'].items()),
             'selector_variant_unverified')
        names = {r[0] for r in subs}
        need(selector['default'] in names and set(matcher['map'].values()) <= names,
             'selector_targets_unverified')
    else:
        need(not subs, 'manual_subprofiles_unverified')
    variants = []
    for name, override in subs if subs else [(None, {})]:
        value = dict(base)
        value.update(override)
        profile_json(value)
        need(value.get('sub_profile_select') == selector, 'selector_override_unverified')
        prefix = directory + ('/' + name if name else '') + '/'
        modes = {}
        for mode in ('brightness', 'color_temp', 'hs'):
            plain, compressed = prefix + mode + '.csv', prefix + mode + '.csv.gz'
            path = compressed if compressed in bundle else plain
            if path in bundle:
                modes[mode] = {'path': path, 'raw_sha256': hashlib.sha256(bundle[path]).hexdigest(),
                    'table_sha256': table_digest(parse_table(bundle[path], mode,
                                                           compressed=path.endswith('.gz')))}
        need(bool(modes), 'lut_missing')
        candidates = {Path(p).name.split('.', 1)[0] for p in bundle
                      if p.startswith(prefix) and '/' not in p[len(prefix):]
                      and p.endswith(('.csv', '.csv.gz'))}
        need(candidates == set(modes), 'lut_modes_unverified')
        variants.append({'name': name, 'json_sha256': digest(value), 'modes': modes})
    return {'directory': directory, 'base_sha256': digest(base),
            'sub_profiles_sha256': digest(subs), 'selector_sha256': digest(selector),
            'variants': variants}


def read_bundle(root):
    """Bounded every-component no-follow read; never adopt new file bytes."""
    need(Path(root).is_absolute() and str(Path(root)) == str(root), 'root_required')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for piece in Path(root).parts[1:]:
            child = os.open(piece, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        for piece in ('powercalc', 'profiles'):
            child = os.open(piece, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        result, total = {}, 0
        def walk(directory, prefix, depth=0):
            nonlocal total
            need(depth <= 48, 'profile_depth')
            entries = list(os.scandir(directory))
            need(len(entries) <= 10_000, 'profile_directory_limit')
            for row in entries:
                name = prefix + '/' + row.name
                info = row.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(row.name, flags, dir_fd=directory)
                    try:
                        walk(child, name, depth + 1)
                    finally:
                        os.close(child)
                else:
                    need(stat.S_ISREG(info.st_mode), 'profile_file_type')
                    child = os.open(row.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                    dir_fd=directory)
                    try:
                        raw = b''
                        while chunk := os.read(child, min(65536, 2_000_001 - len(raw))):
                            raw += chunk
                            need(len(raw) <= 2_000_000, 'profile_size')
                    finally:
                        os.close(child)
                    total += len(raw)
                    need(total <= 16_000_000 and len(result) < 512, 'profile_total_limit')
                    result[name] = raw
        walk(fd, 'powercalc/profiles')
        return result
    finally:
        os.close(fd)


async def compile_closed_graph(hass, checked, root):
    """Called ONLY by the isolated ORIGINAL worker, never by live observers."""
    from custom_components.powercalc.power_profile.library import ProfileLibrary, ModelInfo
    from homeassistant.helpers import entity_registry as er, device_registry as dr
    pc = checked.get('powercalc', {})
    allowed_globals = {'sensors', 'discovery', 'disable_library_download', 'enable_analytics',
        'create_standby_group', 'create_energy_sensors', 'create_cost_sensors', 'create_utility_meters',
        'group_power_update_interval', 'group_energy_update_interval', 'energy_update_interval',
        'energy_integration_method', 'energy_sensor_precision', 'energy_sensor_unit_prefix',
        'power_sensor_precision'}
    need(not set(pc) - allowed_globals and pc.get('disable_library_download') is True
         and pc.get('create_energy_sensors') is True and pc.get('enable_analytics') is False
         and pc.get('create_standby_group') is False and pc.get('create_cost_sensors') is False
         and pc.get('create_utility_meters') is False and pc.get('discovery', {}).get('enabled') is False
         and pc.get('group_power_update_interval') == 0 and pc.get('group_energy_update_interval') == 0
         and pc.get('energy_integration_method', 'left') == 'left'
         and pc.get('energy_sensor_unit_prefix') == 'k', 'offline_energy_globals_required')
    bundle = read_bundle(root)
    # Real native registries load the staged ORIGINAL. No fake SourceEntity/cache.
    dr.async_setup(hass)
    await dr.async_load(hass)
    # EntityRegistry._async_load reads the already-loaded DeviceRegistry.
    await er.async_load(hass)
    library = ProfileLibrary(hass, ProfileLibrary.create_loader(hass, skip_remote_loader=True))
    await library.initialize()
    from custom_components.powercalc.common import create_source_entity
    entries_path = Path(root) / '.storage/core.config_entries'
    entries = json.loads(entries_path.read_bytes())['data']['entries']
    originals = [(('yaml:' + cfg['unique_id']), cfg, cfg.get('unique_id')) for cfg in pc.get('sensors', [])]
    groups = []
    for entry in entries:
        if entry['domain'] != 'powercalc':
            continue
        need(entry['options'] == {} and not entry.get('subentries'), 'entry_options_unverified')
        cfg = entry['data']
        origin = 'entry:' + entry['entry_id']
        if cfg.get('sensor_type') == 'group':
            allowed = {'name', 'unique_id', 'sensor_type', 'group_type', 'group_power_entities',
                'group_energy_entities', 'entity_id', 'subtract_entities', 'create_energy_sensor',
                'force_calculate_group_energy', 'group_power_update_interval',
                'group_energy_update_interval', '_power_entity', '_energy_entity'}
            need(not set(cfg) - allowed and cfg.get('group_type') in ('custom', 'subtract')
                 and cfg.get('create_energy_sensor') is True, 'group_variant_unverified')
            groups.append({'origin': origin, 'config_sha256': digest(cfg), 'config': cfg,
                           'unique_id': cfg.get('unique_id') or entry['unique_id'] or entry['entry_id']})
        else:
            originals.append((origin, cfg, cfg.get('unique_id') or entry['unique_id']))
    profiles = []
    for origin, cfg, configured_unique_id in originals:
        semantic = source_config(cfg)
        source = create_source_entity(semantic['entity_id'], hass)
        need(source.entity_entry is er.async_get(hass).async_get(semantic['entity_id']),
             'source_registry_required')
        profile = await library.get_profile(ModelInfo(semantic['manufacturer'], semantic['model']), source)
        directory = Path(profile.get_model_directory(root_only=True)).relative_to(root).as_posix()
        need(directory.startswith('powercalc/profiles/'), 'profile_loader_unverified')
        data = compile_profile(directory, profile._base_json_data, profile._sub_profiles, bundle)
        data.update(origin=origin, source=semantic['entity_id'], manufacturer=profile.manufacturer,
                    model=profile.model, config_sha256=digest(cfg),
                    unique_id=configured_unique_id or source.unique_id)
        profiles.append(data)
    need(bool(profiles) and len({r['origin'] for r in profiles}) == len(profiles), 'profile_membership')
    energy_path = Path(root) / '.storage/energy'
    settings = None if not energy_path.exists() else json.loads(energy_path.read_bytes())['data']
    refs = []
    def references(value):
        if type(value) is dict:
            for key, child in value.items():
                if key.startswith('stat_') and type(child) is str:
                    refs.append(child)
                references(child)
        elif type(value) is list:
            for child in value:
                references(child)
    references(settings)
    return {'schema': 1, 'profiles': sorted(profiles, key=lambda r: r['origin']),
            'groups': sorted(groups, key=lambda r: r['origin']),
            'files': sorted([path, hashlib.sha256(raw).hexdigest()] for path, raw in bundle.items()),
            'energy_settings_sha256': None if settings is None else digest(settings),
            'energy_refs': sorted(set(refs))}
