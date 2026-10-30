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


def _qualified_metadata_expression(text):
    """Pure output expressions with finite literal entity dependencies; never render.

    No globals, attribute access, variables, statements, time or arbitrary calls.
    Conditional branches may observe a subset of these literal dependencies.
    """
    from jinja2 import Environment, nodes
    tree = Environment().parse(text)
    allowed = (nodes.Output, nodes.TemplateData, nodes.Const, nodes.Name,
        nodes.Call, nodes.Filter, nodes.Add, nodes.Sub, nodes.Mul, nodes.Div,
        nodes.FloorDiv, nodes.Mod, nodes.Neg, nodes.Pos, nodes.Concat,
        nodes.And, nodes.Or, nodes.Not, nodes.Compare, nodes.Operand,
        nodes.CondExpr, nodes.List, nodes.Tuple, nodes.Dict, nodes.Pair)
    dependencies = set()
    callees = {id(call.node) for call in tree.find_all(nodes.Call)}
    for node in tree.find_all(nodes.Node):
        if not isinstance(node, allowed):
            raise ValueError('template_metadata_expression_unverified')
        if isinstance(node, nodes.Name) and (id(node) not in callees
                or node.name not in ('states', 'is_state', 'state_attr')):
            raise ValueError('template_metadata_expression_unverified')
        if isinstance(node, nodes.Call):
            if (not isinstance(node.node, nodes.Name)
                    or node.node.name not in ('states', 'is_state', 'state_attr')
                    or len(node.args) != (1 if node.node.name == 'states' else 2)
                    or any(not isinstance(arg, nodes.Const) or type(arg.value) is not str
                           for arg in node.args)
                    or not re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z0-9_]+', node.args[0].value)
                    or node.kwargs or node.dyn_args is not None or node.dyn_kwargs is not None):
                raise ValueError('template_metadata_expression_unverified')
            dependencies.add(node.args[0].value)
        if isinstance(node, nodes.Filter) and (node.name not in ('int', 'float', 'round', 'string', 'lower', 'upper')
                or node.kwargs or node.dyn_args is not None or node.dyn_kwargs is not None
                or any(not isinstance(arg, nodes.Const) for arg in node.args)):
            raise ValueError('template_metadata_expression_unverified')
    return tuple(sorted(dependencies))


def _pure_template_metadata(cfg):
    """Exact original bodies and attribute keys, shared by checker and live proof."""
    from collections.abc import Mapping
    from homeassistant.helpers.template import Template
    from homeassistant.util.yaml.objects import NodeStrClass
    result = []
    for key in ('availability', 'icon', 'picture'):
        value = cfg.get(key)
        if value is not None and type(value) is not Template:
            raise ValueError('template_metadata_type_unverified')
        if value is not None:
            _qualified_metadata_expression(value.template)
        result.append(None if value is None else value.template)
    attributes = cfg.get('attributes')
    if attributes is not None and not isinstance(attributes, Mapping):
        raise ValueError('template_metadata_attributes_unverified')
    values = []
    for key, value in (attributes or {}).items():
        if (type(key) not in (str, NodeStrClass) or not key or key in ('friendly_name', 'icon', 'entity_picture',
                'unit_of_measurement', 'device_class', 'state_class', 'assumed_state', 'supported_features')
                or type(value) is not Template):
            raise ValueError('template_metadata_attributes_unverified')
        _qualified_metadata_expression(value.template)
        values.append([key, value.template])
    result.append(sorted(values))
    return result


def simple_template_sensor_semantics(cfg):
    """Explicit state/pure metadata semantics; preserve old state-only v1 bytes.

    Used by the SAME original checker and the actual runtime consumer adapter.
    Other native configs keep their existing full source projection + UNKNOWN.
    """
    from homeassistant.helpers.template import Template
    allowed = {'state', 'name', 'unique_id', 'unit_of_measurement', 'device_class',
        'state_class', 'availability', 'attributes', 'icon', 'picture', 'variables',
        'last_reset', 'device_id', 'default_entity_id', 'object_id',
        'raw_configs', 'raw_blueprint_inputs'}
    if set(cfg) - allowed or any(cfg.get(key) for key in ('variables', 'last_reset',
            'device_id', 'raw_blueprint_inputs')):
        raise ValueError('sensor_semantics_subset_unverified')
    state, name = cfg.get('state'), cfg.get('name')
    if type(state) is not Template or type(name) is not Template or not name.is_static:
        raise ValueError('sensor_semantics_subset_unverified')
    result = ['state_sensor_v1', state.template, name.template,
        cfg.get('unit_of_measurement'), cfg.get('device_class'), cfg.get('state_class')]
    metadata = _pure_template_metadata(cfg)
    if any(value is not None for value in metadata[:3]) or metadata[3]:
        result[0] = 'state_sensor_metadata_v2'
        result.append([cfg.get('default_entity_id'), cfg.get('object_id')])
        result.append(metadata)
    return result


def simple_template_binary_semantics(cfg):
    """Named literal boolean state, optional pure metadata, no delays/effects."""
    from homeassistant.helpers.template import Template
    allowed = {'state', 'name', 'unique_id', 'device_class', 'availability',
        'attributes', 'icon', 'picture', 'variables', 'device_id',
        'default_entity_id', 'object_id', 'raw_configs', 'raw_blueprint_inputs',
        'delay_on', 'delay_off', 'auto_off', 'unit_of_measurement'}
    if (set(cfg) - allowed or any(cfg.get(key) is not None for key in
            ('delay_on', 'delay_off', 'auto_off', 'device_id',
             'unit_of_measurement', 'raw_blueprint_inputs'))
            or cfg.get('variables')):
        raise ValueError('binary_semantics_subset_unverified')
    state, name, device_class = cfg.get('state'), cfg.get('name'), cfg.get('device_class')
    if (type(state) is not Template or type(name) is not Template or not name.is_static
            or (device_class is not None and not isinstance(device_class, str))):
        raise ValueError('binary_semantics_subset_unverified')
    _qualified_binary_expression(state.template)
    result = ['state_binary_v1', state.template, name.template, device_class]
    metadata = _pure_template_metadata(cfg)
    if any(value is not None for value in metadata[:3]) or metadata[3]:
        result[0] = 'state_binary_metadata_v2'
        result.append([cfg.get('default_entity_id'), cfg.get('object_id')])
        result.append(metadata)
    return result


def _qualified_binary_expression(text):
    """Exactly one literal is_state(entity, state) call, without evaluation."""
    from jinja2 import Environment, nodes
    tree = Environment().parse(text)
    if (len(tree.body) != 1 or not isinstance(tree.body[0], nodes.Output)
            or len(tree.body[0].nodes) != 1):
        raise ValueError('binary_expression_subset_unverified')
    call = tree.body[0].nodes[0]
    if (not isinstance(call, nodes.Call) or not isinstance(call.node, nodes.Name)
            or call.node.name != 'is_state' or len(call.args) != 2
            or any(not isinstance(arg, nodes.Const) or type(arg.value) is not str
                   for arg in call.args)
            or not re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z0-9_]+', call.args[0].value)
            or call.kwargs or call.dyn_args is not None or call.dyn_kwargs is not None):
        raise ValueError('binary_expression_subset_unverified')
    return (call.args[0].value,)


def template_state_runtime_projection(rows):
    """Preserve sensor-only v1 bytes; qualify every new/mixed key by domain."""
    if (not rows or any(row[0] not in ('template.sensor', 'template.binary_sensor')
                        for row in rows)
            or len({(row[0], row[1]) for row in rows}) != len(rows)):
        raise ValueError('template_activation_inventory_unverified')
    if all(row[0] == 'template.sensor' for row in rows):
        return ['template_state_sensors_v1', sorted([key, digest] for _, key, digest in rows)]
    return ['template_state_entities_v2', sorted(rows)]


def _template_entity_keys(block, platform, root=None, *, full_state=False):
    """Same native schema: complete raw/checked bijection, physical coordinates.

    A raw occurrence supplies the key; full normalized schema output supplies
    its unique association with checked data. List order/name alone never do.
    """
    from pathlib import Path
    from homeassistant.const import __version__
    from homeassistant.components.template import sensor, binary_sensor
    from homeassistant.util.yaml.objects import NodeDictClass
    if __package__:
        from .template_physical_identity import physical_template_key
    else:
        # The checker runs by absolute path with -I: its directory is not an
        # import search path. Load only our explicitly colocated source file.
        from importlib.util import module_from_spec, spec_from_file_location
        spec = spec_from_file_location('_renamer_template_physical_identity',
            Path(__file__).with_name('template_physical_identity.py'))
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        physical_template_key = module.physical_template_key
    entities = list(block.get(platform, ()))
    if all(type(entity.get('unique_id')) is str and entity['unique_id'] for entity in entities):
        prefix = block.get('unique_id')
        return [(prefix + '-' if prefix else '') + entity['unique_id'] for entity in entities]
    if root is None or block.raw_config is None:
        raise ValueError('template_physical_origin_unverified')
    raw = block.raw_config.get(platform, ())
    raw = [raw] if isinstance(raw, dict) else list(raw)
    module = sensor if platform == 'sensor' else binary_sensor
    schema = getattr(module, ('SENSOR_' if platform == 'sensor' else 'BINARY_SENSOR_')
                     + ('SCHEMA' if __version__ == '2025.6.0' else 'YAML_SCHEMA'))
    candidates = []
    for item in raw:
        if item.get('unique_id') is not None:
            continue
        if type(item) is not NodeDictClass or any(item.get(k) is not None for k in
                ('object_id', 'default_entity_id')):
            raise ValueError('template_physical_shape_unverified')
        filename, line = getattr(item, '__config_file__', None), getattr(item, '__line__', None)
        if type(filename) is not str or type(line) is not int:
            raise ValueError('template_physical_annotation_unverified')
        path, base = Path(filename), Path(root)
        if not path.is_absolute() or not path.is_relative_to(base):
            raise ValueError('template_physical_path_unverified')
        checked = schema(item)
        if not full_state:
            semantic = (simple_template_sensor_semantics(checked) if platform == 'sensor'
                        else simple_template_binary_semantics(checked))
            if platform == 'sensor':
                _qualified_state_expression(semantic[1])
        candidates.append((_fingerprint(checked), physical_template_key(
            'template.' + platform, path.relative_to(base).as_posix(), line)))
    result = []
    for entity in entities:
        unique = entity.get('unique_id')
        if type(unique) is str and unique:
            prefix = block.get('unique_id')
            result.append((prefix + '-' if prefix else '') + unique)
            continue
        matches = [key for digest, key in candidates if digest == _fingerprint(entity)]
        if len(matches) != 1 or matches[0] in result:
            raise ValueError('template_physical_bijection_ambiguous')
        result.append(matches[0])
    if len(candidates) != sum(entity.get('unique_id') is None for entity in entities):
        raise ValueError('template_physical_inventory_changed')
    return result


def _template_source_projection(checked, root=None, *, full_state=False):
    """Native validated ORIGINAL blocks + every platform entity, no rendering.

    Distinct from _expected_runtime: a source row alone grants no activation.
    Numeric state and literal binary rows can be compared with native consumers;
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
            for entity, key in zip(cfg.get(platform, ()),
                    _template_entity_keys(cfg, platform, root, full_state=full_state), strict=True):
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
            if (not full_state and mode == 'state' and domain == 'template.sensor' and not any(
                    cfg.get(key) for key in ('variables', 'actions', 'conditions'))):
                try:
                    payload = [block_key, simple_template_sensor_semantics(entity)]
                except ValueError:
                    pass  # unsupported remains full source inventory, not empty
            elif (not full_state and mode == 'state' and domain == 'template.binary_sensor' and not any(
                    cfg.get(key) for key in ('variables', 'actions', 'conditions'))):
                try:
                    payload = [block_key, simple_template_binary_semantics(entity)]
                except ValueError:
                    pass  # every unsupported binary config remains inventoried
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


def qualified_template_state_projection(checked, root=None):
    """Whole modern YAML inventory restricted to the two explicit state subsets.

    Other platforms/coordinators/config shapes never qualify an activation.
    Source inventories remain available through the independent full projection.
    """
    rows, physical = [], []
    for block in checked.get('template', ()):
        if (block.raw_config is None or block.raw_blueprint_inputs is not None
                or set(block) - {'unique_id', 'sensor', 'binary_sensor', 'variables', 'actions', 'conditions'}
                or any(block.get(k) for k in ('variables', 'actions', 'conditions'))
                or not (block.get('sensor') or block.get('binary_sensor'))):
            raise ValueError('template_activation_subset_unverified')
        for platform in ('sensor', 'binary_sensor'):
            for entity, key in zip(block.get(platform, ()),
                    _template_entity_keys(block, platform, root), strict=True):
                if platform == 'sensor':
                    semantic = simple_template_sensor_semantics(entity)
                    _qualified_state_expression(semantic[1])
                else:
                    semantic = simple_template_binary_semantics(entity)
                digest = _fingerprint(semantic)
                rows.append(['template.' + platform, key, digest])
                if entity.get('unique_id') is None:
                    physical.append(('template.' + platform, digest))
    # Across blocks too: indistinguishable live stateless consumers cannot be
    # assigned a physical source by counts, suffix IDs or iteration order.
    if len(set(physical)) != len(physical):
        raise ValueError('template_physical_semantics_ambiguous')
    return template_state_runtime_projection(rows)


# Explicit internal source contract. It never replaces the legacy runtime digest.
TEMPLATE_STATE_SOURCE_PROFILE = 'template_state_source_v3'


def _source_module(name):
    """Load colocated product code under -I; never from the configuration root."""
    from importlib import import_module
    from pathlib import Path
    from types import ModuleType
    if __package__:
        return import_module('.' + name, __package__)
    package_name = '_renamer_isolated_source_profile'
    if package_name not in sys.modules:
        package = ModuleType(package_name)
        package.__path__ = [str(Path(__file__).resolve().parent)]
        sys.modules[package_name] = package
    return import_module(package_name + '.' + name)


def full_template_state_semantics(block, platform, entity):
    """Full native-normalized state config, without a second Jinja evaluator.

    The caller must have obtained block/entity from native schema validation.
    Context includes effective shared defaults, variables and flags. Sibling
    entities belong to the separate whole-source inventory, so editing one
    sibling body does not change another consumer's semantic digest. Template constructors
    supply their exact native .strip() bodies through _normal, never our strip.
    This is a source declaration, not permission to run or rebind anything.
    """
    if platform not in ('sensor', 'binary_sensor') or 'triggers' in block or 'trigger' in block:
        raise ValueError('template_state_profile_family')
    from homeassistant.components.template.const import PLATFORMS
    context = {key: value for key, value in block.items() if key not in PLATFORMS}
    return ['template_state_entity_v3', platform, context, entity]


def _template_state_features(block, entity):
    """Declare proof obligations for every native Template; parse, never render.

    No node/filter/operator admission list: native Jinja/schema own validity.
    Name/attribute/call/filter/test inventories let the runtime authenticate its
    actual environment, including aliases or functions outside the time list.
    Any use of this other than direct this.entity_id conservatively needs memory
    proof. Declarations cannot stand in for that actual lifecycle proof.
    """
    from collections.abc import Mapping
    from homeassistant.helpers.template import Template
    from homeassistant.helpers.script_variables import ScriptVariables
    from jinja2 import Environment, nodes
    slots = []
    def visit(value, path, depth=0):
        if depth > 64:
            raise ValueError('template_state_profile_depth')
        if isinstance(value, Template):
            tree = Environment(extensions=('jinja2.ext.loopcontrols', 'jinja2.ext.do')).parse(value.template)
            parents = {id(child): parent for parent in tree.find_all(nodes.Node)
                       for child in parent.iter_child_nodes()}
            names = sorted({n.name for n in tree.find_all(nodes.Name) if n.ctx == 'load'})
            this_nodes = [n for n in tree.find_all(nodes.Name) if n.name == 'this' and n.ctx == 'load']
            identity = [n for n in this_nodes if isinstance(parents.get(id(n)), nodes.Getattr)
                        and parents[id(n)].attr == 'entity_id']
            calls = sorted({n.node.name for n in tree.find_all(nodes.Call)
                            if isinstance(n.node, nodes.Name)})
            slots.append({'path': path, 'body_sha256': _fingerprint(value),
                'names': names, 'calls': calls,
                'filters': sorted({n.name for n in tree.find_all(nodes.Filter)}),
                'tests': sorted({n.name for n in tree.find_all(nodes.Test)}),
                'attributes': sorted({n.attr for n in tree.find_all(nodes.Getattr)}),
                'this_identity': bool(identity), 'this_memory': len(identity) != len(this_nodes),
                'time': sorted(set(names) & {'now', 'utcnow', 'today_at', 'relative_time', 'time_since', 'time_until'}),
                'local_bindings': sorted({n.name for n in tree.find_all(nodes.Name) if n.ctx in ('store', 'param')})})
        elif isinstance(value, ScriptVariables):
            visit(value.as_dict(), path + ['$variables'], depth + 1)
        elif isinstance(value, Mapping):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError('template_state_profile_key')
                visit(child, path + [str(key)], depth + 1)
        elif isinstance(value, (tuple, list)):
            for index, child in enumerate(value):
                visit(child, path + [index], depth + 1)
    visit(entity, ['entity'])
    from homeassistant.components.template.const import PLATFORMS
    for key, value in block.items():
        if key not in PLATFORMS:
            visit(value, ['block', str(key)])
    return {'slots': slots, 'variables': bool(block.get('variables') or entity.get('variables')),
        'block_actions': bool(block.get('actions')), 'block_conditions': bool(block.get('conditions')),
        'availability': entity.get('availability') is not None,
        'delay_on': entity.get('delay_on') is not None,
        'delay_off': entity.get('delay_off') is not None,
        'auto_off': entity.get('auto_off') is not None,
        'pending_delay_proof_required': any(entity.get(k) is not None for k in ('delay_on', 'delay_off', 'auto_off'))}


def template_state_source_projection(checked, root=None):
    """V3 complete inventory plus explicit sensor/binary source semantics.

    Trigger/action/button/switch rows remain distinct full inventory. They do
    not acquire state-consumer qualification from this profile.
    """
    projection = _template_source_projection(checked, root, full_state=True)
    states = []
    for block in checked.get('template', ()):
        if 'triggers' in block or 'trigger' in block:
            continue
        for platform in ('sensor', 'binary_sensor'):
            for entity, key in zip(block.get(platform, ()),
                    _template_entity_keys(block, platform, root, full_state=True), strict=True):
                states.append(['template.' + platform, key,
                    _fingerprint(full_template_state_semantics(block, platform, entity)),
                    _template_state_features(block, entity)])
    return {'profile': TEMPLATE_STATE_SOURCE_PROFILE,
        'projection': _fingerprint(projection),
        'rows': [[r[0], r[1], _fingerprint(r)] for r in projection],
        'families': [[r[0], r[1], ('trigger' if r[0] == 'template.trigger' or
            (r[0] not in ('template.state', 'template.trigger') and
             any(k in r[2][1] for k in ('triggers', 'trigger'))) else 'state')] for r in projection],
        'states': sorted(states, key=lambda r: (r[0], r[1]))}


def _template_reference_proof(before_root, proposed_root, mapping):
    """Reuse actual source planner/dataflow, bind exact ORIGINAL/proposal files.

    A native-validated config says nothing about source rewrite safety. Rebuild
    the existing full YAML plan, including cross-slot variable provenance, and
    reject every dynamic/unresolved/shadowed marker before emitting profile v3.
    No caller-supplied ready flag or serialized planner result is trusted.
    """
    import hashlib
    from pathlib import Path
    planner = _source_module('yaml_plan')
    plan = planner.plan_yaml(before_root, mapping)
    if not plan.ready:
        raise ValueError('template_state_profile_source_plan_rejected')
    proposed = {p.path: p.proposed_bytes for p in plan.patches}
    rows = []
    for item in plan.files:
        old = (Path(before_root) / item.path).read_bytes()
        expected = proposed.get(item.path, old)
        if (hashlib.sha256(old).hexdigest() != item.sha256
                or (Path(proposed_root) / item.path).read_bytes() != expected):
            raise ValueError('template_state_profile_proposal_changed')
        rows.append([item.path, item.sha256, hashlib.sha256(expected).hexdigest()])
    # The parent receipt's graph/directory fences remain mandatory; this is
    # only a source proof and must never reconstruct writer authority.
    return {'planner': 'yaml_plan_source_reference_v1', 'mapping': [list(row) for row in sorted(mapping.items())],
        'files': sorted(rows),
        'variable_bindings_sha256': hashlib.sha256(json.dumps([
            [p.path, p.source_line, p.entity_id, p.producer_occurrences,
             p.yaml_occurrences, p.accounted_occurrences, list(p.consumer_counts), p.approved]
            for p in plan.variable_bindings], sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode()).hexdigest()}


async def _expected_template_sources(hass, config, *, state_profile=None, with_state_program=False, program_source_paths=()):
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
            if state_profile is not None:
                state_profile.update(template_state_source_projection(checked, hass.config.config_dir))
                if with_state_program:
                    state_profile['_config_program'] = _source_module('template_state_program').native_state_descriptors(
                        checked, hass.config.config_dir, source_paths=program_source_paths)
            projection = _template_source_projection(checked, hass.config.config_dir)
        else:
            projection = []  # exact absent modern YAML; legacy stays ledger UNKNOWN
            if state_profile is not None:
                state_profile.update(template_state_source_projection({}, hass.config.config_dir))
                if with_state_program:
                    state_profile['_config_program'] = _source_module('template_state_program').native_state_descriptors(
                        {}, hass.config.config_dir, source_paths=program_source_paths)
        return {'version': __version__, 'projection': _fingerprint(projection),
                'rows': [[row[0], row[1], _fingerprint(row)] for row in projection]}
    except Exception:
        if state_profile is not None and (not state_profile or with_state_program and '_config_program' not in state_profile):
            raise  # explicit v3 failure never silently degrades to legacy UNKNOWN
        return {'version': __version__, 'projection': None, 'rows': None}


async def _expected_runtime(root, domains, *, source_rows=None, template_sources=None,
                            template_state_profile=None, template_state_program=False, template_program_sources=(),
                            dependent_descriptors=None):
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
        if __version__ in ('2026.10.2', '2026.10.4'):
            # Current native trigger validation requires its own platform
            # registry initialized even when no automation is activated.
            from homeassistant.helpers import trigger
            await trigger.async_setup(hass)
        config = await async_hass_config_yaml(hass)
        if template_sources is not None:
            template_sources.update(await _expected_template_sources(
                hass, config, state_profile=template_state_profile, with_state_program=template_state_program, program_source_paths=template_program_sources))
        for domain in domains:
            try:
                if domain in ('automation', 'script') and not any(
                        key.split(' ', 1)[0] == domain for key in config):
                    # Exact empty ORIGINAL native config, not an absent runtime
                    # entity silently omitted from a nonempty source family.
                    result[domain] = _fingerprint([])
                    if source_rows is not None:
                        source_rows[domain] = []
                    if dependent_descriptors is not None:
                        dependent_descriptors[domain] = []
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
                        projection = qualified_template_state_projection(checked, hass.config.config_dir)
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
                if dependent_descriptors is not None and domain in ('automation', 'script'):
                    dependent_descriptors[domain] = _normal(projection)
                if source_rows is not None and domain in ('automation', 'script'):
                    source_rows[domain] = [[row[0], _fingerprint(row[1]), _fingerprint(row)]
                                           for row in projection]
            except Exception:
                # Per-domain unsupported proof never degrades validation itself.
                result[domain] = None
                if source_rows is not None and domain in ('automation', 'script'):
                    source_rows[domain] = None
                if dependent_descriptors is not None and domain in ('automation', 'script'):
                    dependent_descriptors[domain] = None
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



def template_entry_semantic(snapshot):
    """Pure expected narrow semantics, independent of native schema approval."""
    if (type(snapshot) is not dict or set(snapshot) != {'domain','identity','data','options','subentries'}
            or snapshot['domain'] != 'template' or snapshot['data'] != {}
            or snapshot['subentries'] != {} or type(snapshot['options']) is not dict
            or set(snapshot['options']) != {'template_type','name','state'}
            or snapshot['options']['template_type'] != 'sensor'):
        raise ValueError('template_entry_subset_unverified')
    identity, options = snapshot['identity'], snapshot['options']
    fields = {'entry_id','unique_id','title','version','minor_version','source','disabled_by',
              'pref_disable_new_entities','pref_disable_polling'}
    if (type(identity) is not dict or set(identity) != fields
            or identity['source'] != 'user' or identity['disabled_by'] is not None
            or type(identity['title']) is not str
            or type(identity['version']) is not int or identity['version'] < 1
            or type(identity['minor_version']) is not int or identity['minor_version'] < 0
            or type(identity['entry_id']) is not str or not identity['entry_id']
            or identity['unique_id'] is not None and (type(identity['unique_id']) is not str
                                                     or not identity['unique_id'])
            or any(type(identity[f]) is not bool for f in ('pref_disable_new_entities','pref_disable_polling'))
            or any(type(options[f]) is not str or not options[f].strip() for f in ('name','state'))):
        raise ValueError('template_entry_identity_unverified')
    return ['state_sensor_v1',options['state'],options['name'],None,None,None]


def template_entry_seal(request, version):
    """Digest exact private preparation inputs; no schema approval by this hash."""
    import hashlib
    rows = [[], []]
    refs, entries = set(), set()
    if type(request) not in (tuple,list) or not request or len(request) > 100:
        raise ValueError('template_entry_request_invalid')
    for ref, digest, payload in request:
        if (type(ref) is not str or not ref or ref in refs or type(digest) is not str
                or type(payload) is not dict or set(payload) != {'kind','data'}
                or payload['kind'] != 'helper' or set(payload['data']) != {'before','after','mapping'}):
            raise ValueError('template_entry_request_invalid')
        encoded = json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if len(encoded) > 16_000_000 or hashlib.sha256(encoded).hexdigest() != digest:
            raise ValueError('template_entry_preparation_changed')
        before, after = payload['data']['before'], payload['data']['after']
        old, new = template_entry_semantic(before), template_entry_semantic(after)
        key = before['identity']['entry_id']
        if (key in entries or before['identity'] != after['identity']
                or before['options']['name'] != after['options']['name'] or old == new):
            raise ValueError('template_entry_change_unverified')
        helper_digest = hashlib.sha256(json.dumps(payload['data'],sort_keys=True,
            separators=(',',':'),allow_nan=False).encode()).hexdigest()
        for result, snapshot, semantic in zip(rows,(before,after),(old,new),strict=True):
            result.append([version,ref,digest,helper_digest,key,snapshot,semantic])
        refs.add(ref); entries.add(key)
    # Plain JSON only: pure receipt reopen must not import a native runtime.
    return ('template.config_entries',*(hashlib.sha256(json.dumps(
        sorted(v,key=lambda row:row[1]),sort_keys=True,separators=(',',':'),
        allow_nan=False).encode()).hexdigest() for v in rows))


def _template_entry_projection(row):
    fields = ('entry_id','unique_id','title','version','minor_version','source','disabled_by',
              'pref_disable_new_entities','pref_disable_polling')
    return {'domain':row['domain'],'identity':{k:row[k] for k in fields},
            'data':row['data'],'options':row['options'],
            'subentries':{s['subentry_id']:{'identity':{k:s[k] for k in
                ('subentry_id','subentry_type','unique_id','title')},'data':s['data']}
                for s in row.get('subentries',[])}}


def _check_template_entries(root, request, version):
    from pathlib import Path
    from homeassistant.components.template import sensor
    from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
    seal = template_entry_seal(request,version)
    document = json.loads((Path(root)/'.storage/core.config_entries').read_bytes())
    if (set(document) != {'version','minor_version','key','data'}
            or document['key'] != 'core.config_entries'
            or (document['version'],document['minor_version']) != (1,5)
            or set(document['data']) != {'entries'}):
        raise ValueError('template_entry_original_schema')
    for _,_,payload in request:
        before,after = (payload['data'][k] for k in ('before','after'))
        matches = [row for row in document['data']['entries']
                   if row['entry_id'] == before['identity']['entry_id']]
        if len(matches) != 1 or _template_entry_projection(matches[0]) != before:
            raise ValueError('template_entry_original_changed')
        for snapshot in (before,after):
            ident = snapshot['identity']
            if (ident['version'],ident['minor_version']) != (
                    TemplateConfigFlowHandler.VERSION,TemplateConfigFlowHandler.MINOR_VERSION):
                raise ValueError('template_entry_migration_unsupported')
            options = dict(snapshot['options']);options.pop('template_type')
            schema = sensor.SENSOR_CONFIG_SCHEMA if version == '2025.6.0' else sensor.SENSOR_CONFIG_ENTRY_SCHEMA
            semantic = simple_template_sensor_semantics(schema(options))
            if semantic != template_entry_semantic(snapshot):
                raise ValueError('template_entry_semantics_changed')
            _qualified_state_expression(snapshot['options']['state'])
    return seal


async def check(before_root, proposed_root, *, with_sources=False, powercalc_profiles=False,
                powercalc_candidate=False, native_helpers=(), template_source_profile=None,
                template_reference_mapping=None, template_state_program=False,
                dependent_reference_mapping=None):
    from homeassistant.const import __version__
    from homeassistant.scripts.check_config import async_check_config
    if __version__ not in ('2025.6.0', '2026.10.2', '2026.10.4'):
        return {'status': 'unsupported_runtime'}
    if template_source_profile is not None and (
            template_source_profile != TEMPLATE_STATE_SOURCE_PROFILE or not with_sources
            or type(template_reference_mapping) is not dict):
        raise ValueError('template_state_profile_request_invalid')
    if template_source_profile is None and template_reference_mapping is not None:
        raise ValueError('template_state_profile_request_invalid')
    if type(template_state_program) is not bool or template_state_program and template_source_profile is None:
        raise ValueError('template_state_profile_request_invalid')
    reference_proof = (_template_reference_proof(before_root, proposed_root, template_reference_mapping)
                       if template_source_profile is not None else None)
    dependent_proof = None
    if dependent_reference_mapping is not None:
        if not with_sources or native_helpers or powercalc_profiles or powercalc_candidate:
            raise ValueError('dependent_config_program_rejected')
        dependent_proof = _template_reference_proof(before_root, proposed_root, dependent_reference_mapping)
        program_module = _source_module('dependent_config_program')
        paths = tuple(row[0] for row in dependent_proof['files'])
        program_module.assert_reference_only(before_root, paths)
        program_module.assert_reference_only(proposed_root, paths)
    checked = await async_check_config(proposed_root)
    if checked.errors or checked.warnings:
        # Native diagnostics may contain expanded !secret values. Return counts only.
        return {'status': 'invalid', 'errors': len(checked.errors),
                'warnings': len(checked.warnings), 'version': __version__}
    before, after = await _effective(before_root), await _effective(proposed_root)
    changed, unsupported = changed_domains(before, after)
    helper_seal = _check_template_entries(before_root,native_helpers,__version__) if native_helpers else None
    if not changed and helper_seal is None:
        return {'status': 'no_effective_config_change'}
    source_rows = {} if with_sources else None
    original_domains = sorted(set(changed) | {'automation', 'script'}) if with_sources else changed
    template_sources = {} if with_sources else None
    old_state_profile = {} if template_source_profile is not None else None
    new_state_profile = {} if template_source_profile is not None else None
    old_dependent = {} if dependent_proof is not None else None
    new_dependent = {} if dependent_proof is not None else None
    old_runtime = await _expected_runtime(before_root, original_domains,
        source_rows=source_rows, template_sources=template_sources,
        template_state_profile=old_state_profile, template_state_program=template_state_program,
        template_program_sources=tuple(row[0] for row in reference_proof['files']) if template_state_program else (),
        dependent_descriptors=old_dependent)
    candidate_templates = {} if with_sources else None
    candidate_domains = sorted(set(changed) | {'automation', 'script'}) if dependent_proof is not None else changed
    new_runtime = await _expected_runtime(proposed_root, candidate_domains,
        template_sources=candidate_templates, template_state_profile=new_state_profile,
        template_state_program=template_state_program,
        template_program_sources=tuple(row[0] for row in reference_proof['files']) if template_state_program else (),
        dependent_descriptors=new_dependent)
    if (with_sources and 'template' in changed
            and set(changed) <= {'automation', 'script', 'template'}
            and all(old_runtime.get(domain) is not None and new_runtime.get(domain) is not None
                    and old_runtime[domain] != new_runtime[domain] for domain in changed)
            and old_runtime.get('template') is not None
            and new_runtime.get('template') is not None
            and old_runtime['template'] != new_runtime['template']):
        unsupported = [domain for domain in unsupported if domain != 'template']
    result = {'status': 'valid', 'version': __version__, 'changed_domains': changed,
              'unsupported_domains': unsupported,
              'runtime_expected': [(domain, old_runtime.get(domain), new_runtime.get(domain))
                                   for domain in changed]}
    if dependent_proof is not None:
        result['dependent_config_program'] = await program_module.native_prefix_images(
            before_root, dependent_reference_mapping, dependent_proof, old_dependent, new_dependent,
            _expected_runtime)
    if template_source_profile is not None:
        if template_state_program:
            result['template_state_config_program'] = old_state_profile.pop('_config_program')
            result['template_candidate_state_config_program'] = new_state_profile.pop('_config_program')
        for value in (old_state_profile, new_state_profile):
            value.update(version=__version__, reference_proof=reference_proof)
        result['template_state_source_profile'] = old_state_profile
        result['template_candidate_state_source_profile'] = new_state_profile
    if helper_seal is not None:
        result['runtime_expected'].append(helper_seal)
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


def _template_mapping_request(arguments, *, flag='--template-reference-mapping', selected='--template-state-source-v3'):
    """Strict bounded new CLI payload; the legacy command shape is unchanged."""
    if selected not in arguments and flag not in arguments:
        return None
    if arguments.count(selected) != 1 or arguments.count(flag) != 1:
        raise ValueError('template_state_profile_request_invalid')
    index = arguments.index(flag)
    if index + 1 >= len(arguments):
        raise ValueError('template_state_profile_request_invalid')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('template_state_profile_request_invalid')
            result[key] = value
        return result
    with open(arguments[index + 1], 'rb') as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError('template_state_profile_request_invalid')
    value = json.loads(raw, object_pairs_hook=unique)
    if type(value) is not dict:
        raise ValueError('template_state_profile_request_invalid')
    return value


def main():
    logging.disable(logging.CRITICAL)
    try:
        from homeassistant import runner
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            result = asyncio.run(check(sys.argv[1], sys.argv[2],
                                       with_sources='--source-evidence' in sys.argv[3:],
                                       powercalc_profiles='--powercalc-profile-inputs' in sys.argv[3:],
                                       powercalc_candidate='--powercalc-candidate-inputs' in sys.argv[3:],
                                       template_source_profile=(TEMPLATE_STATE_SOURCE_PROFILE
                                           if '--template-state-source-v3' in sys.argv[3:] else None),
                                       template_reference_mapping=_template_mapping_request(sys.argv[3:]),
                                       template_state_program='--template-state-config-program' in sys.argv[3:],
                                       dependent_reference_mapping=_template_mapping_request(sys.argv[3:],
                                           flag='--dependent-reference-mapping', selected='--dependent-config-program'),
                                       native_helpers=(json.load(open(sys.argv[sys.argv.index('--native-helpers')+1]))
                                           if '--native-helpers' in sys.argv[3:] else ())),
                                 loop_factory=getattr(runner, 'create_event_loop', None))
    except BaseException as error:
        result = {'status': 'checker_failed', 'diagnostic': _checker_diagnostic(error)}
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
