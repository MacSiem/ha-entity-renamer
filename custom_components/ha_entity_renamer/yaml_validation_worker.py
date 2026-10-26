"""Isolated native HA checker. Executed by path in a separate Python process.

Never use scripts.check_config.check(): that wrapper patches YAML loaders.
Only the native async_check_config API is called here. stdout/stderr are private.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout, redirect_stderr
import json
import re
import logging
import os
import sys


# Only explicit source-owned machine literals may leave the isolated process.
# Never emit exception str/repr, native checker errors, locals or chained errors.
_DIAGNOSTIC_REASONS = frozenset({
    'powercalc_original_native_schema_rejected',
    'powercalc_profile_alternative_loader_unverified',
    'powercalc_profile_csv_shape',
    'powercalc_profile_csv_size',
    'powercalc_profile_directory_limit',
    'powercalc_profile_empty_lut',
    'powercalc_profile_entry_options_unverified',
    'powercalc_profile_fresh_plan_required',
    'powercalc_profile_group_variant_unverified',
    'powercalc_profile_input_depth',
    'powercalc_profile_input_limit',
    'powercalc_profile_inputs_changed',
    'powercalc_profile_lut_missing',
    'powercalc_profile_lut_modes_unverified',
    'powercalc_profile_lut_shape',
    'powercalc_profile_manual_subprofiles_unverified',
    'powercalc_profile_mode_unverified',
    'powercalc_profile_nonfinite_lut',
    'powercalc_profile_offline_energy_globals_required',
    'powercalc_profile_original_profile_bytes_changed',
    'powercalc_profile_original_sources_required',
    'powercalc_profile_profile_adjustment_unverified',
    'powercalc_profile_profile_depth',
    'powercalc_profile_profile_directory_limit',
    'powercalc_profile_profile_file_type',
    'powercalc_profile_profile_loader_unverified',
    'powercalc_profile_profile_membership',
    'powercalc_profile_profile_receipt_mismatch',
    'powercalc_profile_profile_size',
    'powercalc_profile_profile_total_limit',
    'powercalc_profile_root_changed',
    'powercalc_profile_root_required',
    'powercalc_profile_selector_override_unverified',
    'powercalc_profile_selector_targets_unverified',
    'powercalc_profile_selector_variant_unverified',
    'powercalc_profile_source_registry_required',
    'powercalc_profile_source_variant_unverified',
    'powercalc_profile_standby_unverified',
    'powercalc_profile_strategy_unverified',
    'powercalc_profile_unsafe_input',
    'powercalc_profile_yaml_overlap',
})
_DIAGNOSTIC_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,95}\Z')
_DIAGNOSTIC_FILE = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}\.py\Z')
_DIAGNOSTIC_REASON = re.compile(r'powercalc_[a-z0-9_]{1,95}\Z')
_CHECKER_FAILURE_STATUSES = frozenset({
    'checker_failed', 'invalid', 'unsupported_runtime', 'no_effective_config_change', 'valid'})


def safe_checker_diagnostic(value):
    """Revalidate a tiny diagnostic schema, including the subprocess boundary."""
    if type(value) is not dict:
        return {}
    result = {}
    name = value.get('exception_type')
    if type(name) is str and _DIAGNOSTIC_IDENT.fullmatch(name):
        result['exception_type'] = name
    reason = value.get('reason')
    if (type(reason) is str and reason in _DIAGNOSTIC_REASONS
            and _DIAGNOSTIC_REASON.fullmatch(reason)):
        result['reason'] = reason
    frames = value.get('traceback')
    if type(frames) is list:
        clean = []
        for frame in frames[-8:]:
            if type(frame) is not dict:
                continue
            file, function, line = frame.get('file'), frame.get('function'), frame.get('line')
            if (type(file) is str and _DIAGNOSTIC_FILE.fullmatch(file)
                    and type(function) is str and _DIAGNOSTIC_IDENT.fullmatch(function)
                    and type(line) is int and 0 < line <= 1_000_000):
                clean.append({'file': file, 'function': function, 'line': line})
        if clean:
            result['traceback'] = clean
    return result


def _checker_diagnostic(error):
    """Metadata only: filename basename, code name, line, and static reason."""
    value = {'exception_type': type(error).__name__}
    args = BaseException.args.__get__(error)
    if type(args) is tuple and len(args) == 1 and type(args[0]) is str:
        # Membership in the literal catalog is mandatory, not prefix alone.
        if args[0] in _DIAGNOSTIC_REASONS:
            value['reason'] = args[0]
    frames, node = [], BaseException.__traceback__.__get__(error)
    special = {'<module>': 'module', '<lambda>': 'lambda', '<listcomp>': 'listcomp',
               '<dictcomp>': 'dictcomp', '<setcomp>': 'setcomp', '<genexpr>': 'genexpr'}
    while node is not None:
        code = node.tb_frame.f_code
        frames.append({'file': os.path.basename(code.co_filename),
                       'function': special.get(code.co_name, code.co_name), 'line': node.tb_lineno})
        node = node.tb_next
    value['traceback'] = frames[-8:]
    return safe_checker_diagnostic(value)


def checker_failure_note(result, returncode):
    """No opaque worker fields or arbitrary values cross into exception notes."""
    value = {}
    if type(returncode) is int and -128 <= returncode <= 255:
        value['returncode'] = returncode
    if type(result) is dict:
        status = result.get('status')
        if type(status) is str and status in _CHECKER_FAILURE_STATUSES:
            value['status'] = status
        for key in ('errors', 'warnings'):
            count = result.get(key)
            if type(count) is int and 0 <= count <= 1_000_000:
                value[key] = count
        diagnostic = safe_checker_diagnostic(result.get('diagnostic'))
        if diagnostic:
            value['diagnostic'] = diagnostic
    return 'native_checker_diagnostic=' + json.dumps(value, sort_keys=True, separators=(',', ':'))



async def _effective(root):
    from homeassistant import loader
    from homeassistant.core import HomeAssistant
    from homeassistant.config import async_hass_config_yaml
    hass = HomeAssistant(root)
    loader.async_setup(hass)
    try:
        config = await async_hass_config_yaml(hass)
        if isinstance(config.get('homeassistant'), dict):
            config['homeassistant'].pop('packages', None)
        return config
    finally:
        await hass.async_stop(force=True)


def changed_domains(before, after):
    """Compare merged native YAML, including labelled domain sections/packages."""
    missing = object()
    keys = set(before) | set(after)
    changed = sorted({key.split(' ', 1)[0] for key in keys
                      if before.get(key, missing) != after.get(key, missing)})
    unsupported = set(changed) - {'automation', 'script', 'scene', 'homeassistant'}
    if 'homeassistant' in changed:
        old, new = before.get('homeassistant') or {}, after.get('homeassistant') or {}
        if any(old.get(key, missing) != new.get(key, missing)
               for key in set(old) | set(new)
               if key not in {'customize', 'customize_domain', 'customize_glob'}):
            unsupported.add('homeassistant')
    if 'scene' in changed:
        # scene.reload only resets the native Home Assistant platform.
        for config in (before, after):
            for key, value in config.items():
                if key.split(' ', 1)[0] != 'scene':
                    continue
                items = value if isinstance(value, list) else [value]
                if any(isinstance(item, dict) and item.get('platform') not in (None, 'homeassistant')
                       for item in items):
                    unsupported.add('scene')
    return changed, sorted(unsupported)



def _normal(value):
    """Bounded, typed native config encoding; never render templates or use repr."""
    from collections.abc import Mapping
    from datetime import date, datetime, time, timedelta
    from homeassistant.core import State
    from homeassistant.helpers.template import Template
    from homeassistant.helpers.script_variables import ScriptVariables
    def encode(item, depth=0):
        if depth > 64:
            raise ValueError('runtime_projection_depth')
        if item is None or type(item) in (bool, int, float):
            return item
        if isinstance(item, str):
            return str(item)
        if isinstance(item, Template):
            return {'$template': item.template}
        if isinstance(item, ScriptVariables):
            return {'$variables': encode(item.as_dict(), depth + 1)}
        if isinstance(item, State):
            return {'$state': [item.entity_id, item.state, encode(item.attributes, depth + 1)]}
        if isinstance(item, timedelta):
            return {'$timedelta': item.total_seconds()}
        if isinstance(item, (date, datetime, time)):
            return {'$datetime': item.isoformat()}
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('runtime_projection_key')
            # Preserve native insertion order (variables can depend on it), and
            # tag maps so user keys cannot impersonate Template/State markers.
            return {'$map': [[str(key), encode(child, depth + 1)]
                             for key, child in item.items()]}
        if isinstance(item, (list, tuple)):
            return [encode(child, depth + 1) for child in item]
        # No generic class/string conversion: unsupported native objects fail closed.
        raise ValueError('runtime_projection_type')
    return encode(value)


def _fingerprint(value):
    import hashlib
    payload = json.dumps(_normal(value), sort_keys=True, separators=(',', ':'),
                         allow_nan=False).encode()
    if len(payload) > 16_000_000:
        raise ValueError('runtime_projection_size')
    return hashlib.sha256(payload).hexdigest()


def simple_template_sensor_semantics(cfg):
    """One explicit state-only native sensor subset; no render or conversion.

    Used by the SAME original checker and the actual runtime consumer adapter.
    Other native configs keep their existing full source projection + UNKNOWN.
    """
    from homeassistant.helpers.template import Template
    allowed = {'state', 'name', 'unique_id', 'unit_of_measurement', 'device_class',
        'state_class', 'availability', 'attributes', 'icon', 'picture', 'variables',
        'last_reset', 'device_id', 'default_entity_id', 'object_id',
        'raw_configs', 'raw_blueprint_inputs'}
    if set(cfg) - allowed or any(cfg.get(key) for key in ('availability',
            'attributes', 'icon', 'picture', 'variables', 'last_reset',
            'device_id', 'raw_blueprint_inputs')):
        raise ValueError('sensor_semantics_subset_unverified')
    state, name = cfg.get('state'), cfg.get('name')
    if type(state) is not Template or type(name) is not Template or not name.is_static:
        raise ValueError('sensor_semantics_subset_unverified')
    return ['state_sensor_v1', state.template, name.template,
        cfg.get('unit_of_measurement'), cfg.get('device_class'), cfg.get('state_class')]


def _template_source_projection(checked):
    """Native validated ORIGINAL blocks + every platform entity, no rendering.

    Distinct from _expected_runtime: Template apply remains unsupported. The
    state_sensor_v1 row can be checked by the separate read-only consumer proof;
    all other native conversion/defaults/variables remain full source rows.
    """
    from homeassistant.components.template.const import PLATFORMS
    rows = []
    for cfg in checked.get('template', ()):
        if cfg.raw_config is None or cfg.raw_blueprint_inputs is not None:
            raise ValueError('template_blueprint_or_raw_source_unverified')
        mode = 'trigger' if 'triggers' in cfg else 'state'
        entities = []
        for platform in PLATFORMS:
            for entity in cfg.get(platform, ()):
                key = entity.get('unique_id')
                if type(key) is not str or not key:
                    raise ValueError('template_stable_id_unverified')
                prefix = cfg.get('unique_id')
                if prefix:
                    key = prefix + '-' + key
                # Include parent trigger/variables/action context in the entity
                # digest; a matching entity body alone is insufficient later.
                entities.append(['template.' + platform, key, entity])
        keys = sorted((row[0], row[1]) for row in entities)
        if not keys:
            raise ValueError('template_block_identity_unverified')
        prefix = cfg.get('unique_id')
        block_key = ('id:' + prefix if prefix else 'entities:' +
            json.dumps(keys, separators=(',', ':'), ensure_ascii=True))
        rows.append(['template.' + mode, block_key, cfg])
        for domain, key, entity in entities:
            payload = [block_key, cfg, entity]  # preserve previous source proof
            if (mode == 'state' and domain == 'template.sensor' and not any(
                    cfg.get(key) for key in ('variables', 'actions', 'conditions'))):
                try:
                    payload = [block_key, simple_template_sensor_semantics(entity)]
                except ValueError:
                    pass  # unsupported remains full source inventory, not empty
            rows.append([domain, key, payload])
    keys = [(row[0], row[1]) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('template_duplicate_native_key')
    return sorted(rows, key=lambda row: (row[0], row[1]))


def _qualified_state_expression(text):
    """Pure Jinja AST qualifier for the first numeric producer expression slice.

    No time/dynamic/domain/variable lookup, render or alternate template parser.
    Broader state templates retain source inventory but not activation support.
    """
    from jinja2 import Environment, nodes
    tree = Environment().parse(text)
    if (len(tree.body) != 1 or not isinstance(tree.body[0], nodes.Output)
            or len(tree.body[0].nodes) != 1):
        raise ValueError('template_expression_subset_unverified')
    allowed = (nodes.Output, nodes.Const, nodes.Name, nodes.Call, nodes.Filter,
               nodes.Add, nodes.Sub, nodes.Mul, nodes.Div, nodes.FloorDiv,
               nodes.Mod, nodes.Pow, nodes.Neg, nodes.Pos)
    calls = 0
    for node in tree.find_all(nodes.Node):
        if not isinstance(node, allowed):
            raise ValueError('template_expression_subset_unverified')
        if isinstance(node, nodes.Name) and node.name != 'states':
            raise ValueError('template_expression_subset_unverified')
        if isinstance(node, nodes.Call):
            if (not isinstance(node.node, nodes.Name) or node.node.name != 'states'
                    or len(node.args) != 1 or not isinstance(node.args[0], nodes.Const)
                    or type(node.args[0].value) is not str
                    or not re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z0-9_]+', node.args[0].value)
                    or node.kwargs or node.dyn_args is not None or node.dyn_kwargs is not None):
                raise ValueError('template_expression_subset_unverified')
            calls += 1
        if isinstance(node, nodes.Filter) and (node.name not in ('int', 'float', 'round')
                or node.kwargs or node.dyn_args is not None or node.dyn_kwargs is not None
                or any(not isinstance(arg, nodes.Const) for arg in node.args)):
            raise ValueError('template_expression_subset_unverified')
    if not calls:
        raise ValueError('template_expression_subset_unverified')


def qualified_template_state_projection(checked):
    """Whole modern YAML family restricted to named state-only sensors.

    Other platforms/coordinators/config shapes never qualify an activation.
    Source inventories remain available through the independent full projection.
    """
    rows = []
    for block in checked.get('template', ()):
        if (block.raw_config is None or block.raw_blueprint_inputs is not None
                or set(block) - {'unique_id', 'sensor', 'variables', 'actions', 'conditions'}
                or any(block.get(k) for k in ('variables', 'actions', 'conditions'))
                or not block.get('sensor')):
            raise ValueError('template_activation_subset_unverified')
        for entity in block['sensor']:
            key = entity.get('unique_id')
            if type(key) is not str or not key:
                raise ValueError('template_activation_identity_unverified')
            if block.get('unique_id'):
                key = block['unique_id'] + '-' + key
            semantic = simple_template_sensor_semantics(entity)
            _qualified_state_expression(semantic[1])
            rows.append([key, _fingerprint(semantic)])
    if not rows or len({row[0] for row in rows}) != len(rows):
        raise ValueError('template_activation_inventory_unverified')
    return ['template_state_sensors_v1', sorted(rows)]


async def _expected_template_sources(hass, config):
    """Same ORIGINAL checker instance/config; no factory, setup or rendering."""
    from homeassistant.const import __version__
    from homeassistant.config import async_process_component_and_handle_errors
    from homeassistant.loader import async_get_integration
    try:
        if any(key.split(' ', 1)[0] == 'template' for key in config):
            integration = await async_get_integration(hass, 'template')
            checked = await async_process_component_and_handle_errors(hass, config, integration)
            if checked is None:
                raise ValueError('template_native_source_unverified')
            projection = _template_source_projection(checked)
        else:
            projection = []  # exact absent modern YAML; legacy stays ledger UNKNOWN
        return {'version': __version__, 'projection': _fingerprint(projection),
                'rows': [[row[0], row[1], _fingerprint(row)] for row in projection]}
    except Exception:
        return {'version': __version__, 'projection': None, 'rows': None}


async def _expected_runtime(root, domains, *, source_rows=None, template_sources=None):
    """Native schema projections in the isolated validator, without component setup.

    No scripts/actions/templates are run and no scene is activated. The trusted
    receipt stores only digests of these expectations, never resolved secrets.
    """
    from homeassistant import loader
    from homeassistant.core import HomeAssistant
    from homeassistant.config import (async_hass_config_yaml,
        async_process_component_and_handle_errors, config_per_platform)
    from homeassistant.loader import async_get_integration
    hass = HomeAssistant(root)
    loader.async_setup(hass)
    result = {}
    try:
        from homeassistant.const import __version__
        if __version__ == '2026.10.2':
            # Current native trigger validation requires its own platform
            # registry initialized even when no automation is activated.
            from homeassistant.helpers import trigger
            await trigger.async_setup(hass)
        config = await async_hass_config_yaml(hass)
        if template_sources is not None:
            template_sources.update(await _expected_template_sources(hass, config))
        for domain in domains:
            try:
                if domain in ('automation', 'script') and not any(
                        key.split(' ', 1)[0] == domain for key in config):
                    # Exact empty ORIGINAL native config, not an absent runtime
                    # entity silently omitted from a nonempty source family.
                    result[domain] = _fingerprint([])
                    if source_rows is not None:
                        source_rows[domain] = []
                    continue
                if domain == 'homeassistant':
                    from homeassistant.core_config import _CUSTOMIZE_CONFIG_SCHEMA
                    from homeassistant.helpers.entity_values import EntityValues
                    core = config.get(domain) or {}
                    # Package customizations have independent merge precedence.
                    # Until projected via the native core processor, fail closed.
                    if any('homeassistant' in package
                           for package in core.get('packages', {}).values()):
                        raise ValueError('package_customize_not_covered')
                    keys = ('customize', 'customize_domain', 'customize_glob')
                    cfg = _CUSTOMIZE_CONFIG_SCHEMA({key: core[key] for key in keys if key in core})
                    values = EntityValues(*(cfg[key] for key in keys))
                    projection = [values._exact, values._domain,
                        [(pattern.pattern, attrs) for pattern, attrs in (values._glob or {}).items()]]
                else:
                    integration = await async_get_integration(hass, domain)
                    checked = await async_process_component_and_handle_errors(
                        hass, config, integration)
                    if checked is None:
                        raise ValueError('native_projection_rejected')
                    projection = []
                    if domain == 'template':
                        projection = qualified_template_state_projection(checked)
                    elif domain == 'script':
                        for key, cfg in checked[domain].items():
                            if cfg.raw_config is None or cfg.raw_blueprint_inputs is not None:
                                raise ValueError('blueprint_or_invalid_script')
                            projection.append([key, cfg.raw_config, cfg['sequence'],
                                cfg['mode'], cfg['max'], cfg['max_exceeded'], cfg.get('variables'),
                                cfg.get('icon'), cfg['description'], cfg['fields'], cfg['trace']])
                    elif domain == 'automation':
                        for cfg in checked[domain]:
                            # Conditions and trigger variables still need independent proof.
                            # Enabled static event/state registrations are observed separately.
                            if (not cfg.get('id') or cfg.raw_config is None
                                    or cfg.raw_blueprint_inputs is not None
                                    or cfg.get('conditions') or cfg.get('trigger_variables')):
                                raise ValueError('automation_not_covered')
                            projection.append([cfg['id'], cfg.raw_config, cfg['triggers'],
                                cfg['actions'], cfg['mode'], cfg['max'], cfg['max_exceeded'],
                                cfg.get('variables'), cfg['trace']])
                    elif domain == 'scene':
                        for platform, cfg in config_per_platform(checked, domain):
                            if platform != 'homeassistant':
                                raise ValueError('foreign_scene_platform')
                            for scene in cfg['states']:
                                if not scene.get('id'):
                                    raise ValueError('scene_without_stable_id')
                                projection.append([scene['id'], scene['name'], scene.get('icon'),
                                                   scene['entities']])
                    else:
                        raise ValueError('unsupported_domain')
                    if domain == 'template':
                        result[domain] = _fingerprint(projection)
                        continue
                    if len({row[0] for row in projection}) != len(projection):
                        raise ValueError('duplicate_runtime_id')
                    projection.sort(key=lambda row: row[0])
                result[domain] = _fingerprint(projection)
                if source_rows is not None and domain in ('automation', 'script'):
                    source_rows[domain] = [[row[0], _fingerprint(row[1]), _fingerprint(row)]
                                           for row in projection]
            except Exception:
                # Per-domain unsupported proof never degrades validation itself.
                result[domain] = None
                if source_rows is not None and domain in ('automation', 'script'):
                    source_rows[domain] = None
        return result
    finally:
        await hass.async_stop(force=True)


def powercalc_fixed_semantics(cfg):
    """Read-only scalar fixed subset, shared by ORIGINAL checker/runtime binder.

    Return None for every unverified profile/dynamic/adjustment alternative.
    ConfigEntry private generated IDs are checked separately against registry.
    """
    from decimal import Decimal
    allowed = {'name', 'entity_id', 'unique_id', 'fixed', 'create_energy_sensor',
               'sensor_type', '_power_entity', '_energy_entity'}
    if (not isinstance(cfg, dict) or set(cfg) - allowed
            or cfg.get('sensor_type', 'virtual_power') != 'virtual_power'
            or cfg.get('create_energy_sensor') is not False
            or type(cfg.get('entity_id')) is not str):
        return None
    fixed = cfg.get('fixed')
    if not isinstance(fixed, dict) or set(fixed) != {'power'} or type(fixed['power']) not in (int, float):
        return None
    value = Decimal(str(fixed['power']))
    if not value.is_finite():
        return None
    return {'source': cfg['entity_id'], 'power': str(value)}


async def _expected_powercalc_sources(root, *, profiles=False, bindings=False):
    """Projection in the SAME isolated ORIGINAL checker; no vendor setup."""
    from homeassistant import loader
    from homeassistant.core import HomeAssistant
    from homeassistant.const import __version__
    from homeassistant.config import async_hass_config_yaml, async_process_component_and_handle_errors
    from homeassistant.loader import async_get_integration
    hass = HomeAssistant(root)
    loader.async_setup(hass)
    try:
        cfg = await async_hass_config_yaml(hass)
        checked = await async_process_component_and_handle_errors(
            hass, cfg, await async_get_integration(hass, 'powercalc'))
        if checked is None:
            raise ValueError('powercalc_original_native_schema_rejected')
        pc = checked.get('powercalc', {})
        rows = [[row.get('unique_id'), _fingerprint(row), powercalc_fixed_semantics(row)]
                for row in pc.get('sensors', [])]
        result = {'version': __version__, 'rows': rows,
                  'globals': {key: _fingerprint(value) for key, value in pc.items() if key != 'sensors'}}
        if profiles:
            # Companion is product code beside this -I worker, not config input.
            import importlib.util
            from pathlib import Path
            spec = importlib.util.spec_from_file_location('_renamer_profile_inputs',
                Path(__file__).with_name('powercalc_profile_inputs.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            result['closed_graph'] = await module.compile_closed_graph(
                hass, checked, root, with_bindings=bindings)
        return result
    finally:
        await hass.async_stop(force=True)


async def check(before_root, proposed_root, *, with_sources=False, powercalc_profiles=False,
                powercalc_candidate=False):
    from homeassistant.const import __version__
    from homeassistant.scripts.check_config import async_check_config
    if __version__ not in ('2025.6.0', '2026.10.2'):
        return {'status': 'unsupported_runtime'}
    checked = await async_check_config(proposed_root)
    if checked.errors or checked.warnings:
        # Native diagnostics may contain expanded !secret values. Return counts only.
        return {'status': 'invalid', 'errors': len(checked.errors),
                'warnings': len(checked.warnings), 'version': __version__}
    before, after = await _effective(before_root), await _effective(proposed_root)
    changed, unsupported = changed_domains(before, after)
    if not changed:
        return {'status': 'no_effective_config_change'}
    source_rows = {} if with_sources else None
    original_domains = sorted(set(changed) | {'automation', 'script'}) if with_sources else changed
    template_sources = {} if with_sources else None
    old_runtime = await _expected_runtime(before_root, original_domains,
        source_rows=source_rows, template_sources=template_sources)
    candidate_templates = {} if with_sources else None
    new_runtime = await _expected_runtime(proposed_root, changed,
        template_sources=candidate_templates)
    if (with_sources and changed == ['template']
            and old_runtime.get('template') is not None
            and new_runtime.get('template') is not None
            and old_runtime['template'] != new_runtime['template']):
        unsupported = [domain for domain in unsupported if domain != 'template']
    result = {'status': 'valid', 'version': __version__, 'changed_domains': changed,
              'unsupported_domains': unsupported,
              'runtime_expected': [(domain, old_runtime.get(domain), new_runtime.get(domain))
                                   for domain in changed]}
    if with_sources:
        if os.path.isfile(os.path.join(before_root, 'custom_components/powercalc/manifest.json')):
            result['powercalc_sources'] = await _expected_powercalc_sources(
                before_root, profiles=powercalc_profiles, bindings=powercalc_candidate)
            if powercalc_candidate:
                if not powercalc_profiles:
                    raise ValueError('powercalc_profile_original_sources_required')
                result['powercalc_candidate_sources'] = await _expected_powercalc_sources(
                    proposed_root, profiles=True, bindings=True)
                # Hash exact full planned documents AFTER both isolated reads.
                # No live manager dump or second validation root is consulted.
                import hashlib
                from pathlib import Path
                keys = ('core.entity_registry', 'core.device_registry', 'core.config_entries')
                result['powercalc_candidate_documents'] = [[key,
                    hashlib.sha256((Path(before_root) / '.storage' / key).read_bytes()).hexdigest(),
                    hashlib.sha256((Path(proposed_root) / '.storage' / key).read_bytes()).hexdigest()]
                    for key in keys]
        result['template_sources'] = template_sources
        result['template_candidate_sources'] = candidate_templates
        result['source_runtime'] = {'version': __version__, 'domains': [
            [domain, old_runtime.get(domain), source_rows.get(domain)]
            for domain in ('automation', 'script')]}
    return result


def main():
    logging.disable(logging.CRITICAL)
    try:
        from homeassistant import runner
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            result = asyncio.run(check(sys.argv[1], sys.argv[2],
                                       with_sources='--source-evidence' in sys.argv[3:],
                                       powercalc_profiles='--powercalc-profile-inputs' in sys.argv[3:],
                                       powercalc_candidate='--powercalc-candidate-inputs' in sys.argv[3:]),
                                 loop_factory=getattr(runner, 'create_event_loop', None))
    except BaseException as error:
        result = {'status': 'checker_failed', 'diagnostic': _checker_diagnostic(error)}
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
