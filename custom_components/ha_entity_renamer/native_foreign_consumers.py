"""Read-only coexistence fence for the observed Core 11 consumer shapes.

This is not a migration adapter for these integrations. Unknown shapes,
selected references and changes to their configuration remain refusals.
"""
from __future__ import annotations

from datetime import timedelta
from enum import Enum
import hashlib
import ast
import os
import re
import shlex
import threading

from .transaction import Refused
from .reference_planner import _syntax_template


def _need(ok):
    if not ok:
        raise Refused('foreign_consumer_scope_unverified') from None


def _template(text, mapping):
    from jinja2 import Environment, nodes
    _need(not any(re.search(r'(?<![a-zA-Z0-9_])' + re.escape(identity)
                            + r'(?![a-zA-Z0-9_])', text)
                  for identity in (*mapping, *mapping.values())))
    planned, markers = _syntax_template(text, mapping, 10000)
    _need(not markers and planned == text)
    try:
        ast = Environment(extensions=['jinja2.ext.do', 'jinja2.ext.loopcontrols']).parse(text)
        _need(not any(ast.find_all((nodes.Import, nodes.FromImport, nodes.Macro, nodes.CallBlock))))
        assigned = {a.target.name for a in ast.find_all(nodes.Assign)
                    if isinstance(a.target, nodes.Name)}
        for call in ast.find_all(nodes.Call):
            fn = call.node
            _need((isinstance(fn, nodes.Name) and fn.name in {
                'states', 'is_state', 'is_state_attr', 'state_attr', 'expand', 'has_value',
                'now', 'utcnow', 'as_timestamp', 'float', 'int', 'strptime', 'range', 'dict', 'iif'})
                or (isinstance(fn, nodes.Getattr) and fn.attr == 'get'
                    and isinstance(fn.node, nodes.Name) and fn.node.name in assigned))
        for node in ast.find_all(nodes.Filter):
            _need(node.name in {'default', 'float', 'int', 'string', 'round', 'lower', 'upper',
                                'trim', 'length', 'first', 'last', 'list', 'replace', 'join',
                                'sum', 'min', 'max', 'unique', 'sort', 'is_number'})
    except Refused:
        raise
    except Exception:
        _need(False)


def _freeze(value, mapping, source_templates, *, depth=0):
    """Freeze configuration only; never inspect telemetry or execute templates."""
    from homeassistant.helpers.template import Template
    from homeassistant.helpers.trigger_template_entity import ValueTemplate
    _need(depth < 40)
    if type(value) in (Template, ValueTemplate):
        text = value.template
        _need(type(text) is str)
        if '{{' in text or '{%' in text or '{#' in text:
            _template(text, mapping)
            source_templates.add(text)
        return ('template', text)
    if type(value) is str:
        _need(not any(re.search(r'(?<![a-zA-Z0-9_])' + re.escape(old)
                                + r'(?![a-zA-Z0-9_])', value)
                      for old in (*mapping, *mapping.values())))
        if '{{' in value or '{%' in value or '{#' in value:
            _template(value, mapping)
            source_templates.add(value)
        return value
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, Enum):
        return ('enum', type(value).__module__, type(value).__qualname__,
                _freeze(value.value, mapping, source_templates, depth=depth + 1))
    if type(value) is timedelta:
        return ('timedelta', value.total_seconds())
    if type(value) in (list, tuple):
        return tuple(_freeze(v, mapping, source_templates, depth=depth + 1) for v in value)
    if isinstance(value, dict):
        _need(all(type(k) is str for k in value))
        return tuple((_freeze(k, mapping, source_templates, depth=depth + 1),
                      _freeze(v, mapping, source_templates, depth=depth + 1))
                     for k, v in sorted(value.items()))
    _need(False)


def _python_source(raw, mapping):
    """Qualify only fixed local Python inputs and fixed Recorder entity IDs."""
    try:
        tree = ast.parse(raw.decode('utf-8'))
    except (SyntaxError, UnicodeError):
        _need(False)
    nodes = list(ast.walk(tree))
    _need(len(nodes) <= 30000)
    parents = {id(child): parent for parent in nodes for child in ast.iter_child_nodes(parent)}
    for node in nodes:
        if isinstance(node, ast.Constant) and type(node.value) is str:
            _need(not any(re.search(r'(?<![a-zA-Z0-9_])' + re.escape(identity)
                                    + r'(?![a-zA-Z0-9_])', node.value)
                          for identity in (*mapping, *mapping.values())))
        if isinstance(node, ast.Import):
            _need(all(a.name in {'re', 'subprocess', 'sys', 'datetime', 'json', 'os',
                                 'pathlib', 'sqlite3', 'zoneinfo'} and a.asname is None
                      for a in node.names))
        if isinstance(node, ast.ImportFrom):
            _need(node.level == 0 and node.module in {'datetime', 'pathlib', 'zoneinfo'}
                  and all(a.asname is None and a.name != '*' for a in node.names))
        if isinstance(node, ast.Name):
            _need(node.id not in {'eval', 'exec', 'compile', 'getattr', 'setattr',
                                  'delattr', 'vars', 'globals', 'locals', '__import__'})
            if node.id in ('os', 'subprocess', 'sqlite3', 'sys') and isinstance(node.ctx, ast.Load):
                parent = parents[id(node)]
                allowed = {'os': {'environ'}, 'subprocess': {'run'},
                           'sqlite3': {'connect', 'Row'}, 'sys': {'argv'}}
                _need(isinstance(parent, ast.Attribute) and parent.attr in allowed[node.id])
                if parent.attr in ('run', 'connect'):
                    _need(isinstance(parents.get(id(parent)), ast.Call)
                          and parents[id(parent)].func is parent)
    calls = [n for n in nodes if isinstance(n, ast.Call)]
    sql = [n for n in calls if isinstance(n.func, ast.Attribute)
           and n.func.attr in ('execute', 'executemany', 'executescript')]
    process = [n for n in calls if isinstance(n.func, ast.Attribute)
               and isinstance(n.func.value, ast.Name) and n.func.value.id == 'subprocess']
    if not sql:
        # Only a fixed argv invocation of ffmpeg is admitted; no shell.
        _need(len(process) == 1 and process[0].func.attr == 'run'
              and len(process[0].args) == 1 and isinstance(process[0].args[0], ast.List)
              and process[0].args[0].elts and isinstance(process[0].args[0].elts[0], ast.Constant)
              and process[0].args[0].elts[0].value == 'ffmpeg'
              and all(k.arg in ('capture_output', 'text', 'timeout') for k in process[0].keywords))
        return
    _need(len(sql) == 1 and not process and sql[0].func.attr == 'execute'
          and len(sql[0].args) == 2 and isinstance(sql[0].args[0], ast.Constant)
          and type(sql[0].args[0].value) is str and isinstance(sql[0].args[1], ast.Tuple)
          and len(sql[0].args[1].elts) == 2)
    statement = ' '.join(sql[0].args[0].value.lower().split())
    _need(statement.startswith('select ') and 'states_meta' in statement
          and re.search(r'\bentity_id\s*=\s*\?', statement) is not None
          and statement.count('?') == 2 and ';' not in statement)
    functions = [n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and sql[0] in ast.walk(n)]
    _need(len(functions) == 1 and len(functions[0].args.args) >= 2)
    function = functions[0]
    entity_parameter = function.args.args[1].arg
    _need(isinstance(sql[0].args[1].elts[0], ast.Name)
          and sql[0].args[1].elts[0].id == entity_parameter)
    _need(not any(isinstance(n, ast.Name) and n.id == entity_parameter
                  and isinstance(n.ctx, (ast.Store, ast.Del)) for n in ast.walk(function)))
    sites = [n for n in calls if isinstance(n.func, ast.Name) and n.func.id == function.name]
    references = [n for n in nodes if isinstance(n, ast.Name) and n.id == function.name]
    _need(sites and len(references) == len(sites))
    for site in sites:
        _need(len(site.args) >= 2 and isinstance(site.args[1], ast.Subscript)
              and isinstance(site.args[1].value, ast.Name)
              and isinstance(site.args[1].slice, ast.Constant)
              and type(site.args[1].slice.value) is str)
        cfg = site.args[1].value.id
        owners = [f for f in nodes if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and site in ast.walk(f)]
        _need(len(owners) == 1)
        stores = [n for n in ast.walk(owners[0]) if isinstance(n, ast.Name)
                  and n.id == cfg and isinstance(n.ctx, ast.Store)]
        assignments = [n for n in ast.walk(owners[0]) if isinstance(n, ast.Assign)
                       and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                       and n.targets[0].id == cfg]
        _need(len(stores) == len(assignments) == 1
              and isinstance(assignments[0].value, ast.Subscript)
              and isinstance(assignments[0].value.value, ast.Name))
        source_name = assignments[0].value.value.id
        sources = [n for n in nodes if isinstance(n, ast.Assign) and len(n.targets) == 1
                   and isinstance(n.targets[0], ast.Name) and n.targets[0].id == source_name]
        _need(len(sources) == 1 and len([n for n in nodes if isinstance(n, ast.Name)
              and n.id == source_name and isinstance(n.ctx, ast.Store)]) == 1)
        try:
            source = ast.literal_eval(sources[0].value)
        except (ValueError, TypeError, SyntaxError):
            _need(False)
        _need(type(source) is dict and source and all(type(v) is dict and all(
              value is None or type(value) in (str, bool, int, float) for value in v.values())
              for v in source.values()))
        key = site.args[1].slice.value
        _need(all(key in row and type(row[key]) is str for row in source.values()))
        # No mutation or method dispatch through either the selected row or the
        # literal table is admitted; their SQL identifiers stay literal.
        for node in nodes:
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                _need(node.value.id not in (cfg, source_name) or isinstance(node.ctx, ast.Load))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                _need(not (isinstance(node.func.value, ast.Name)
                           and node.func.value.id in (cfg, source_name))
                      or node.func.attr == 'get')
            if isinstance(node, ast.Name) and node.id == cfg and isinstance(node.ctx, ast.Load):
                _need(isinstance(parents[id(node)], ast.Subscript))
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == source_name:
                parent = parents[id(node)]
                _need(isinstance(parent, ast.Subscript) or (
                    isinstance(parent, ast.Attribute) and parent.attr == 'get') or (
                    isinstance(parent, ast.Assign) and len(parent.targets) == 1
                    and isinstance(parent.targets[0], ast.Name) and parent.targets[0].id == cfg))
            if isinstance(node, ast.Name) and node.id == source_name and isinstance(node.ctx, ast.Load):
                parent = parents[id(node)]
                _need(isinstance(parent, (ast.Subscript, ast.For, ast.comprehension, ast.Compare))
                      or (isinstance(parent, ast.Attribute) and parent.attr == 'get'))
                if isinstance(parent, ast.Attribute):
                    call = parents.get(id(parent))
                    outer = parents.get(id(call)) if isinstance(call, ast.Call) else None
                    _need(isinstance(call, ast.Call) and call.func is parent
                          and isinstance(outer, ast.Attribute) and outer.attr == 'get')


class NativeForeignConsumers:
    """Pin current native entities and their source-bearing configuration."""

    def __init__(self, hass, mapping, device_names):
        from homeassistant.const import __version__
        from homeassistant.core import callback
        from homeassistant.helpers.dispatcher import async_dispatcher_connect
        from homeassistant.components.mqtt.discovery import MQTT_DISCOVERY_UPDATED, MQTT_DISCOVERY_NEW
        from homeassistant.components.mqtt.const import SUPPORTED_COMPONENTS
        from homeassistant.components.mqtt.models import DATA_MQTT
        _need(__version__ == '2026.11.1' and type(mapping) is dict and type(device_names) is dict
              and all(type(key) is str for key in device_names))
        self.hass, self.mapping, self.thread = hass, dict(mapping), threading.get_ident()
        self.device_ids = frozenset(device_names)
        self.closed = self.changed = False
        self.removers = []
        self.expiry = None
        self.sources_verified = False
        self.scripts = ()

        @callback
        def changed(*_):
            self.changed = True

        try:
            data = hass.data.get(DATA_MQTT)
            if 'mqtt' in hass.config.components:
                _need(data is not None and not data.discovery_pending_discovered
                      and not data.discovery_discovered_and_disabled and not data.debug_info_triggers)
                for key in data.discovery_already_discovered:
                    _need(type(key) is tuple and len(key) == 2)
                    self.removers.append(async_dispatcher_connect(
                        hass, MQTT_DISCOVERY_UPDATED.format(*key), changed))
                for component in SUPPORTED_COMPONENTS:
                    self.removers.append(async_dispatcher_connect(
                        hass, MQTT_DISCOVERY_NEW.format(component, 'mqtt'), changed))
            @callback
            def registry_changed(event):
                from homeassistant.helpers import entity_registry as er
                identity = event.data.get('entity_id')
                row = er.async_get(hass).async_get(identity) if type(identity) is str else None
                if identity in getattr(self, 'foreign_ids', ()) or (row is not None
                        and row.platform in ('mqtt', 'rest', 'command_line')):
                    self.changed = True
            self.removers.append(hass.bus.async_listen('entity_registry_updated', registry_changed))
            self.image, self.yaml_templates, self.yaml_commands = self._sample()
        except BaseException:
            self.close()
            raise

    def _sample(self):
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.entity_platform import EntityPlatform, async_get_platforms
        from homeassistant.components.mqtt.models import DATA_MQTT, MqttData
        from homeassistant.components.mqtt.sensor import MqttSensor
        from homeassistant.components.mqtt.switch import MqttSwitch
        from homeassistant.components.rest.sensor import RestSensor
        from homeassistant.components.rest.data import RestData
        from homeassistant.components.command_line.sensor import CommandSensor, CommandSensorData
        from .yaml_runtime_observer import _pending_native_work

        hass = self.hass
        mapping = {**self.mapping, **{device: device for device in self.device_ids}}
        templates = {'rest': set(), 'command_line': set()}
        registry = er.async_get(hass)
        _need(not _pending_native_work(hass, ('mqtt', 'rest', 'command_line')))
        images, foreign_ids, commands = [], set(), set()
        for name, types in (('mqtt', (MqttSensor, MqttSwitch)), ('rest', (RestSensor,)),
                            ('command_line', (CommandSensor,))):
            loaded = name in hass.config.components
            platforms = async_get_platforms(hass, name)
            entries = hass.config_entries.async_entries(name)
            if not loaded:
                _need(not platforms and not entries)
                images.append((name, False))
                continue
            if name == 'mqtt':
                _need(len(entries) == 1 and entries[0].state.value == 'loaded')
            else:
                _need(not entries)
            registry_rows = {row.entity_id: row for row in registry.entities.values()
                             if row.platform == name and row.disabled_by is None}
            foreign_ids.update(registry_rows)
            _need(not (set(registry_rows) & (set(mapping) | set(mapping.values()))))
            _need(not any(row.device_id in self.device_ids for row in registry_rows.values()))
            found = {}
            platform_rows = []
            for platform in platforms:
                _need(type(platform) is EntityPlatform and platform.hass is hass
                      and platform.platform_name == name and platform._setup_complete
                      and platform._async_cancel_retry_setup is None
                      and isinstance(platform._tasks, list)
                      and all(task.done() for task in platform._tasks))
                if name == 'mqtt':
                    _need(platform.config_entry is entries[0])
                else:
                    _need(platform.config_entry is None)
                entity_rows = []
                for entity_id, entity in platform.entities.items():
                    _need(type(entity) in types and entity.hass is hass
                          and entity.entity_id == entity_id and entity_id in registry_rows
                          and entity_id not in found)
                    found[entity_id] = entity
                    strings = set() if name == 'mqtt' else templates[name]
                    config = _freeze(entity._config, mapping, strings)
                    if name == 'mqtt':
                        discovery = _freeze(entity._discovery_data, mapping, set())
                        detail = (config, discovery)
                    else:
                        value = _freeze(entity._value_template, mapping, strings)
                        if name == 'rest':
                            rest = entity.rest
                            _need(type(rest) is RestData and rest._hass is hass)
                            detail = (config, value, id(rest),
                                      _freeze(entity._resource_template, mapping, strings),
                                      _freeze(rest._resource, mapping, strings)
                                      if entity._resource_template is None else None,
                                      _freeze(rest._headers, mapping, strings),
                                      _freeze(rest._params, mapping, strings),
                                      _freeze(rest._request_data, mapping, strings))
                        else:
                            data = entity.data
                            _need(type(data) is CommandSensorData and data.hass is hass)
                            commands.add(data.command)
                            detail = (config, value, id(data),
                                      _freeze(data.command, mapping, strings))
                    row = registry_rows[entity_id]
                    entity_rows.append((entity_id, id(entity), row.id, row.unique_id,
                                        row.config_entry_id, row.device_id, detail))
                platform_rows.append((id(platform), platform.domain, id(platform.entities),
                                      tuple(sorted(entity_rows))))
            _need(set(found) == set(registry_rows))
            extra = None
            if name == 'mqtt':
                data = hass.data.get(DATA_MQTT)
                _need(type(data) is MqttData and not data.discovery_pending_discovered
                      and not data.discovery_discovered_and_disabled and not data.debug_info_triggers)
                extra = (id(data), id(data.client),
                         _freeze(data.config, mapping, set()),
                         tuple(sorted(data.discovery_already_discovered)))
            images.append((name, True, tuple(id(e) for e in entries),
                           tuple(sorted(platform_rows)), extra))
        # A digest keeps config values, which may contain credentials, out of the
        # retained image and every diagnostic message.
        self.foreign_ids = frozenset(foreign_ids)
        digest = hashlib.sha256(repr(tuple(images)).encode()).digest()
        return (digest, tuple((name, frozenset(values)) for name, values in sorted(templates.items())),
                frozenset(commands))

    def bind_sources(self, yaml_plan, root):
        """Bind runtime templates to the same already-pinned YAML graph."""
        import yaml
        import os
        from .yaml_plan import _Reader, Limits
        values = {'rest': set(), 'command_line': set()}
        active = set()
        def walk(node, domain, depth=0):
            _need(depth < 100 and id(node) not in active)
            active.add(id(node))
            if isinstance(node, yaml.ScalarNode):
                values[domain].add(node.value)
            elif isinstance(node, (yaml.SequenceNode, yaml.MappingNode)):
                children = node.value if isinstance(node, yaml.SequenceNode) else (
                    item for pair in node.value for item in pair)
                for child in children:
                    walk(child, domain, depth + 1)
            active.remove(id(node))
        reader = _Reader(root, Limits())
        try:
            for item in yaml_plan.files:
                raw, stat = reader.read(item.path)
                _need(hashlib.sha256(raw).hexdigest() == item.sha256
                      and (stat.st_dev, stat.st_ino, len(raw)) == (item.device, item.inode, item.size))
                node = yaml.compose(raw.decode('utf-8'), Loader=yaml.SafeLoader)
                if isinstance(node, yaml.MappingNode):
                    for key, value in node.value:
                        if not isinstance(key, yaml.ScalarNode):
                            continue
                        domain = key.value.split(' ', 1)[0]
                        if domain in values:
                            walk(value, domain)
                        elif domain in ('sensor', 'binary_sensor', 'switch') and isinstance(value, yaml.SequenceNode):
                            for child in value.value:
                                if not isinstance(child, yaml.MappingNode):
                                    continue
                                platform = next((v.value for k, v in child.value
                                    if isinstance(k, yaml.ScalarNode) and k.value == 'platform'
                                    and isinstance(v, yaml.ScalarNode)), None)
                                if platform in values:
                                    walk(child, platform)
        finally:
            os.close(reader.root)
        _need(all(templates <= values[name] for name, templates in self.yaml_templates))
        _need(self.yaml_commands <= values['command_line'])
        self.scripts = self._script_image(root, qualify=True)
        self.sources_verified = True

    def _script_image(self, root, *, qualify):
        from .yaml_plan import _Reader, Limits
        paths = set()
        for command in self.yaml_commands:
            _need(type(command) is str and not any(c in command for c in ';&|$`\n\r'))
            try:
                args = shlex.split(command)
            except ValueError:
                _need(False)
            _need(len(args) >= 2 and args[0] == 'python3' and args[1].startswith('/config/')
                  and args[1].endswith('.py'))
            paths.add(args[1][len('/config/'):])
        reader = _Reader(root, Limits())
        image = []
        try:
            for path in sorted(paths):
                raw, info = reader.read(path)
                if qualify:
                    mapping = {**self.mapping, **{device: device for device in self.device_ids}}
                    _python_source(raw, mapping)
                image.append((path, info.st_dev, info.st_ino, info.st_size,
                              info.st_mtime_ns, hashlib.sha256(raw).digest()))
        finally:
            os.close(reader.root)
        return tuple(image)

    def check(self, *, require_sources=False):
        _need(not self.closed and not self.changed and (not require_sources or self.sources_verified))
        if threading.get_ident() != self.thread:
            return
        image, templates, commands = self._sample()
        _need(image == self.image and templates == self.yaml_templates
              and commands == self.yaml_commands and not self.changed)
        if self.sources_verified and self.yaml_commands:
            from pathlib import Path
            _need(self._script_image(Path(self.hass.config.config_dir).resolve(), qualify=False) == self.scripts)

    def close(self):
        if not self.closed:
            self.closed = True
            if self.expiry is not None:
                self.expiry.cancel()
                self.expiry = None
            for remove in self.removers:
                remove()
            self.removers.clear()
