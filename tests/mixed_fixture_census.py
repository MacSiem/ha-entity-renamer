"""Independent census for the bounded native mixed-YAML fixture.

The collector reads HA's loaded entities, configuration, registered dashboard
managers, config entries and process network. It never receives a product plan,
journal or a caller-supplied COMPLETE flag. Anything outside its narrow, actual
fixture semantics remains a named gap. This is not an inventory of arbitrary
remote machines, and a private HA installation is not its input.
"""
from pathlib import Path
from collections.abc import Mapping
import asyncio
import importlib
import json
import re
import socket
import os
import hashlib

ENTITY = re.compile(r'(?<![\w.])([a-z_][a-z0-9_]*\.[a-z0-9_]+)(?![\w.])')
YAML_DOMAINS = frozenset(('automation', 'script', 'template'))
STIMULUS_VALUES = (5, 13)
# Infrastructure here is not declared an entity consumer. Every other loaded
# component is retained as a specific integration gap pending a real adapter.
CORE_DOMAINS = frozenset(('homeassistant', 'automation', 'script', 'template', 'sensor',
                          'binary_sensor', 'recorder', 'blueprint', 'http', 'trace',
                          'template.sensor', 'template.binary_sensor', 'lovelace', 'frontend',
                          'api', 'websocket_api', 'config', 'onboarding'))
# Only these built-ins have their external roles separately checked below;
# uncertain domains are never silently promoted by being infrastructure.
NETWORK_CAPABLE = frozenset(('http', 'api', 'websocket_api', 'mobile_app', 'ssdp',
                             'zeroconf', 'dhcp', 'bluetooth', 'network'))
FRONTEND_SYSTEM = frozenset(('device_automation', 'diagnostics', 'file_upload', 'map_tiles',
                             'repairs', 'search', 'system_log'))


def native_frontend_snapshot(hass, root):
    """Qualify only the actual isolated frontend's source-free repair store."""
    from batch_readback import _strict_json
    unknown = {'status': 'UNKNOWN:frontend_system_sources_unverified'}
    try:
        manager = hass.data['issue_registry']
        store = manager._store
        path = root / '.storage/repairs.issue_registry'
        if (path.is_symlink() or not path.is_file() or Path(store.path) != path
                or store._data is not None or store._delay_handle is not None):
            return unknown
        first = path.stat()
        if first.st_size > 2_000_000:
            return unknown
        raw = path.read_bytes()
        last = path.stat()
        if (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) != (
                last.st_dev, last.st_ino, last.st_size, last.st_mtime_ns, last.st_ctime_ns):
            return unknown
        document = _strict_json(raw)
        data = document['data']
        if (document.get('key') != 'repairs.issue_registry' or data != manager._data_to_save()
                or refs(data) or any('{{' in value or '{%' in value for value in _strings(data))):
            return unknown
        return {'status': 'COMPLETE', 'disk_sha256': hashlib.sha256(raw).hexdigest()}
    except (OSError, AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return unknown


def _walk(value, errors, path='$', depth=0, budget=None):
    """Detached, finite JSON-like source/runtime tree; never stringify secrets."""
    if budget is None:
        budget = [100_000]
    budget[0] -= 1
    if depth > 48 or budget[0] < 0:
        errors.append((path, 'source_complexity_limit'))
        return None
    if isinstance(value, str):
        return str(value)
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, (list, tuple)):
        return [_walk(item, errors, path + '[]', depth + 1, budget) for item in value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            errors.append((path, 'non_string_key'))
            return None
        if len({str(key) for key in value}) != len(value):
            errors.append((path, 'normalized_duplicate_key'))
            return None
        return {str(key): _walk(child, errors, path + '.' + str(key), depth + 1, budget)
                for key, child in value.items()}
    # HA's real Template wrapper retains the exact source expression.
    if type(value).__module__.startswith('homeassistant.helpers.template') and isinstance(getattr(value, 'template', None), str):
        return value.template
    errors.append((path, 'unhandled_type:' + type(value).__module__ + '.' + type(value).__qualname__))
    return None


def _strings(value):
    if type(value) is str:
        yield value
    elif type(value) is list:
        for item in value:
            yield from _strings(item)
    elif type(value) is dict:
        for key, item in value.items():
            yield key
            yield from _strings(item)


def refs(value):
    return sorted({match.group(1) for value in _strings(value) for match in ENTITY.finditer(value)})


def _dynamic_dashboard_strategy(config):
    if not isinstance(config, dict):
        return False
    views = config.get('views', [])
    return ('strategy' in config or isinstance(views, list)
            and any(isinstance(view, dict) and 'strategy' in view for view in views))


def audit(observed):
    """Derive source/consumer facts and gaps from collected observations.

    This routine validates a detached sample; it cannot authenticate a caller
    that supplies fabricated samples. Only collect_native below binds to HA.
    """
    gaps = list(observed['collection_errors'])
    config = observed['configuration']
    if type(config) is not dict:
        raise ValueError('configuration root is not a mapping')
    unknown_roots = sorted(set(config) - YAML_DOMAINS - {'lovelace'})
    gaps.extend(('config:' + domain, 'unclassified_integration_root') for domain in unknown_roots)
    loaded = set(observed['loaded_components'])
    auth = observed.get('native_auth')
    qualified_auth = (type(auth) is dict and set(auth) == {'before', 'after'}
        and auth['before'] == auth['after'] and type(auth['before']) is dict
        and auth['before'].get('status') == 'COMPLETE'
        and type(auth['before'].get('disk_sha256')) is str
        and re.fullmatch(r'[0-9a-f]{64}', auth['before']['disk_sha256']) is not None)
    frontend = observed.get('native_frontend')
    qualified_frontend = (type(frontend) is dict and set(frontend) == {'before', 'after'}
        and frontend['before'] == frontend['after'] and type(frontend['before']) is dict
        and frontend['before'].get('status') == 'COMPLETE'
        and type(frontend['before'].get('disk_sha256')) is str
        and re.fullmatch(r'[0-9a-f]{64}', frontend['before']['disk_sha256']) is not None
        and 'frontend' in loaded and not (FRONTEND_SYSTEM & set(config))
        and not any(row['domain'] in FRONTEND_SYSTEM for row in observed['entries']))
    classified = (CORE_DOMAINS | ({'auth'} if qualified_auth else set())
                  | (FRONTEND_SYSTEM if qualified_frontend else set()))
    gaps.extend(('integration:' + domain, 'unclassified_loaded_integration')
                for domain in sorted(loaded - classified))
    # Mere presence of a network-capable integration is not a proof of external
    # inactivity: it remains explicit unless it is a known local bootstrap and
    # there are no actual network interfaces/listeners in the isolated fixture.
    network = observed['network']
    if (network['interfaces_up'] != ['lo'] or network['nonloopback_sockets']
            or network['loopback_connections'] or network['unix_sockets'] or network['foreign_processes']):
        gaps.append(('external', 'network_endpoints_present'))
    if network['unreadable']:
        gaps.append(('external', 'network_evidence_unavailable'))
    active_external = sorted(loaded & NETWORK_CAPABLE)
    gaps.extend(('integration:' + domain, 'network_capable_component_unclassified')
                for domain in active_external if domain not in ('http', 'api', 'websocket_api', 'network'))
    if active_external and (network['interfaces_up'] != ['lo'] or network['nonloopback_sockets']
                            or network['loopback_connections'] or network['unix_sockets'] or network['foreign_processes']):
        gaps.extend(('integration:' + domain, 'active_external_endpoint_unclassified') for domain in active_external)
    graph = observed['graph']
    for entry in observed['entries']:
        # A disabled registry row does not make a loaded ConfigEntry inactive.
        if entry['state'] != 'not_loaded':
            gaps.append(('config_entry:' + entry['id'], 'loaded_integration_unclassified:' + entry['domain']))
        if entry['live_entity_ids']:
            gaps.append(('config_entry:' + entry['id'], 'live_entities_in_unclassified_entry'))
        if observed['source'] in entry['refs']:
            gaps.append(('config_entry:' + entry['id'], 'configured_reference_requires_integration_adapter'))
        if entry['dynamic']:
            gaps.append(('config_entry:' + entry['id'], 'dynamic_configuration_unverified'))
        if entry['state'] == 'not_loaded' and (not entry['registry_count'] or entry['enabled_count']):
            gaps.append(('config_entry:' + entry['id'], 'unloaded_enabled_or_unbound_integration_unverified'))
    source = observed['source']
    bindings = observed['bindings']
    source_specs = observed['source_specs']
    known_ids = {source} | {row['entity_id'] for family in bindings.values() for row in family.values()}
    gaps.extend(('runtime:' + entity_id, 'unclassified_loaded_entity')
                for entity_id in observed.get('runtime_entities', []) if entity_id not in known_ids)
    for domain in ('automation', 'script', 'template'):
        expected = source_specs[domain]
        runtime = bindings[domain]
        configured_refs = refs(config.get(domain))
        source_refs = sorted({reference for values in expected.values() for reference in values})
        if configured_refs != source_refs:
            gaps.append((domain, 'loaded_configuration_source_mismatch'))
        if sorted(expected) != sorted(runtime):
            gaps.append((domain, 'source_runtime_identity_mismatch'))
        for identity, source_refs in expected.items():
            actual = runtime.get(identity)
            if actual is None:
                continue
            if domain != 'template' and sorted(source_refs) != sorted(actual['refs']):
                gaps.append((domain + ':' + identity, 'source_runtime_references_mismatch'))
            if actual['blueprint']:
                gaps.append((domain + ':' + identity, 'active_blueprint_unverified'))
            if source in source_refs and domain + ':' + identity not in observed['observed_consumers']:
                gaps.append((domain + ':' + identity, 'behavior_not_observed'))
    for route in observed['dashboards']:
        if route['status'] != 'COMPLETE':
            gaps.append(('dashboard:' + route['route'], route['status']))
        elif route['path'] not in graph and route['mode'] == 'yaml':
            gaps.append(('dashboard:' + route['route'], 'yaml_route_outside_include_graph'))
        for card in route.get('cards', []):
            if card['type'].startswith('custom:'):
                gaps.append(('dashboard:' + route['route'], 'custom_card_unsupported:' + card['type']))
            elif card['type'] not in ('map', 'entity', 'entities'):
                gaps.append(('dashboard:' + route['route'], 'card_type_unverified:' + card['type']))
            elif source in card['refs']:
                gaps.append(('dashboard:' + route['route'], 'active_card_render_not_observed:' + card['type']))
    for path in observed['outside_consumers']:
        gaps.append(('outside:' + path, 'reference_outside_active_graph'))
    external_gaps = [item for item in gaps if item[0].startswith(('external', 'integration:', 'config_entry:', 'config:', 'outside:', 'runtime:'))]
    external_empty = not external_gaps and not observed['collection_errors']
    return {'source': source, 'graph': graph, 'bindings': bindings,
            'source_specs': source_specs, 'dashboards': observed['dashboards'],
            'config_roots': sorted(config), 'entries': observed['entries'], 'loaded_components': sorted(loaded),
            'samples': observed['samples'], 'observed_consumers': observed['observed_consumers'],
            'runtime_entities': observed.get('runtime_entities', []),
            'inactive_blueprints': observed['inactive_blueprints'],
            'external': {'network': network, 'network_capable_components': active_external,
                         'outside_consumers': observed['outside_consumers'],
                         'empty_in_bounded_fixture': external_empty},
            'unverified': sorted(set(tuple(x) for x in gaps)), 'complete': not gaps}


def consumer_evidence(original, observed, *, allow_unrendered=False, script_renames=None):
    """Bind comparator checks to independent census observations for this fixture."""
    from batch_readback import ConsumerCheck
    for census in (original, observed):
        if census['complete']:
            continue
        gaps = census['unverified']
        if (not allow_unrendered or not gaps or not census['external']['empty_in_bounded_fixture']
                or any(not subject.startswith('dashboard:') or not reason.startswith('active_card_render_not_observed:')
                       for subject, reason in gaps)):
            raise ValueError('both independently collected censuses must be complete')
    if original['inactive_blueprints'] != observed['inactive_blueprints']:
        raise ValueError('blueprint activity changed')
    inventory, checks = {}, []
    script_renames = script_renames or {}
    bindings = {'mixed_automations.yaml': [], 'mixed_scripts.yaml': [], 'template.yaml': []}
    for family, path in (('automation', 'mixed_automations.yaml'), ('script', 'mixed_scripts.yaml'),
                         ('template', 'template.yaml')):
        identities = sorted(original['source_specs'][family])
        mapped = {identity: script_renames.get(identity, identity) if family == 'script' else identity for identity in identities}
        if len(set(mapped.values())) != len(identities) or sorted(mapped.values()) != sorted(observed['source_specs'][family]):
            raise ValueError('consumer identity membership changed')
        for identity in identities:
            name = family + ':' + identity
            observed_name = family + ':' + mapped[identity]
            if name not in original['observed_consumers'] or observed_name not in observed['observed_consumers']:
                raise ValueError('consumer behavior not independently observed')
            if (family == 'template' and original['bindings'][family][identity]['entity_id']
                    != observed['bindings'][family][identity]['entity_id']):
                raise ValueError('template consumer entity identity changed')
            def values(census):
                if family == 'template':
                    return [sample['template'][identity] for sample in census['samples']]
                return [sample[family] for sample in census['samples']]
            before, after = values(original), values(observed)
            inventory[name] = 'SUPPORTED'
            checks.append(ConsumerCheck(name, before, before, after))
            bindings[path].append(name)
    for path in original['inactive_blueprints']:
        if not path.startswith('blueprints/'):
            raise ValueError('invalid inactive blueprint path')
        name = 'inactive:' + path
        inventory[name] = 'SUPPORTED'
        checks.append(ConsumerCheck(name, True, True, True))
        bindings[path] = [name]
    return inventory, checks, bindings


def census_coverage(census, discovered):
    """Resolve only fixture families checked by a complete independent census."""
    result = dict(discovered)
    if census['complete'] and census['external']['empty_in_bounded_fixture']:
        for family in ('automation', 'script', 'template', 'yaml_graph', 'dashboard', 'integrations'):
            result[family] = 'COMPLETE'
        return result, {}
    return result, {'independent_census': 'UNKNOWN:fixture_not_complete'}


async def fresh_inverse_after_apply(runtime, hass, operation_id, fingerprint, applied, refresh):
    """Inspect every apply outcome and never replay or infer permission to undo."""
    inspected = await runtime.recovery_inspect(hass, operation_id, fingerprint,
                                               guard=lambda: None, refresh=refresh)
    inverse = None
    if inspected.get('eligible') and inspected.get('state') == 'applied':
        inverse = await runtime.native_yaml_reverse(hass, operation_id, fingerprint,
                                                    guard=lambda: None, refresh=refresh)
        if inverse.get('state') != 'rolled_back':
            raise RuntimeError('fresh inverse did not complete')
    elif applied is not None and applied.get('state') == 'applied':
        raise RuntimeError('applied operation was not eligible for fresh inverse')
    return inspected, inverse


def _network_evidence():
    # /proc is from the owned network namespace; do not print IPs/ports.
    out = {'interfaces': [], 'interfaces_up': [], 'processes': [], 'foreign_processes': [],
           'nonloopback_sockets': 0, 'loopback_listeners': 0, 'loopback_connections': 0,
           'unix_sockets': 0, 'unreadable': False}
    try:
        out['interfaces'] = sorted(name for _, name in socket.if_nameindex())
        out['interfaces_up'] = [name for name in out['interfaces']
            if int((Path('/sys/class/net') / name / 'flags').read_text().strip(), 16) & 1]
        processes = {}
        for pid in (name for name in os.listdir('/proc') if name.isdigit()):
            proc = Path('/proc') / pid
            try:
                fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                processes[int(pid)] = {'parent': int(fields[1]), 'start': fields[19],
                                       'name': (proc / 'comm').read_text().strip()}
            except FileNotFoundError:
                continue
        records = list(Path('/config').glob('renamer-unit-*.pid'))
        if len(records) != 1:
            raise ValueError('unique owned guardian record required')
        record = json.loads(records[0].read_text())
        guardian = record['pid']
        if guardian not in processes or processes[guardian]['start'] != record['start']:
            raise ValueError('guardian identity changed')
        owned = {guardian}
        while True:
            expanded = owned | {pid for pid, row in processes.items() if row['parent'] in owned and pid != 1}
            if expanded == owned:
                break
            owned = expanded
        owned.add(1)  # Container init is separately owned by the harness.
        out['processes'] = sorted(row['name'] for row in processes.values())
        out['foreign_processes'] = sorted(row['name'] for pid, row in processes.items() if pid not in owned)
        owned = set()
        for fd in Path('/proc/self/fd').iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith('socket:[') and target.endswith(']'):
                owned.add(target[8:-1])
        for filename in ('tcp', 'tcp6', 'udp', 'udp6'):
            lines = (Path('/proc/net') / filename).read_text().splitlines()[1:]
            for line in lines:
                fields = line.split()
                address = fields[1].split(':')[0].upper()
                loopback = address in ('0100007F', '00000000000000000000000001000000')
                # A wildcard or non-loopback listener can be an external root.
                if not loopback:
                    out['nonloopback_sockets'] += 1
                else:
                    remote = fields[2].split(':')[0]
                    if int(remote, 16):
                        out['loopback_connections'] += 1
                    else:
                        out['loopback_listeners'] += 1
        for line in (Path('/proc/net') / 'unix').read_text().splitlines()[1:]:
            fields = line.split()
            # Anonymous socketpairs wholly owned by this process are internal
            # event-loop plumbing. Named or foreign sockets remain unverified.
            if len(fields) < 7 or fields[6] not in owned or len(fields) > 7:
                out['unix_sockets'] += 1
    except (OSError, IndexError, ValueError):
        out['unreadable'] = True
    return out


def _cards(value):
    if isinstance(value, dict):
        if isinstance(value.get('type'), str):
            yield {'type': value['type'], 'refs': refs(value)}
        for key in ('views', 'cards', 'card', 'elements'):
            if key in value:
                yield from _cards(value[key])
    elif isinstance(value, list):
        for child in value:
            yield from _cards(child)


def _dashboard_census(hass, root, config, errors):
    """Read native registered routes and full manager/disk contents."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import LovelaceStorage, LovelaceYAML
    from batch_readback import _strict_json
    import yaml
    data = hass.data.get(LOVELACE_DATA)
    if data is None:
        if 'lovelace' in hass.config.components or 'lovelace' in config:
            errors.append(('dashboard', 'loaded_manager_unavailable'))
        return [], []
    if 'lovelace' not in hass.config.components:
        errors.append(('dashboard', 'manager_without_loaded_integration'))
    resources = data.resources
    if resources is None or getattr(resources, 'loaded', False) is not True:
        errors.append(('dashboard:resources', 'resource_inventory_unloaded'))
    else:
        items = resources.async_items()
        if items is None:
            errors.append(('dashboard:resources', 'resource_inventory_unavailable'))
        elif list(items):
            errors.append(('dashboard:resources', 'active_custom_resources_unverified'))
    managers = data.dashboards
    definitions = data.yaml_dashboards
    if not isinstance(managers, Mapping) or not isinstance(definitions, Mapping) or None not in managers:
        raise ValueError(f'dashboard manager census unavailable: managers={type(managers)!r}, '
                         f'definitions={type(definitions)!r}, routes={list(managers) if isinstance(managers, Mapping) else None!r}')
    routes, yaml_roots = [], []
    found_yaml = set()
    for route, manager in sorted(managers.items(), key=lambda item: str(item[0])):
        label = '<default>' if route is None else route
        if manager.hass is not hass or manager.url_path != route:
            routes.append({'route': label, 'status': 'native_manager_identity_changed', 'mode': 'unknown', 'path': ''})
            continue
        if type(manager) is LovelaceStorage:
            path = Path(manager._store.path)
            if path.is_symlink() or not path.is_file() or manager._data is None:
                routes.append({'route': label, 'status': 'storage_or_runtime_not_settled', 'mode': 'storage', 'path': ''})
                continue
            document = _strict_json(path.read_bytes())
            if document.get('data') != manager._data or not isinstance(manager._data.get('config'), dict):
                routes.append({'route': label, 'status': 'storage_runtime_mismatch_or_autogenerated', 'mode': 'storage', 'path': ''})
                continue
            relative = path.relative_to(root).as_posix()
            if _dynamic_dashboard_strategy(manager._data['config']):
                routes.append({'route': label, 'status': 'dynamic_strategy_unverified',
                               'mode': 'storage', 'path': relative})
                continue
            routes.append({'route': label, 'status': 'COMPLETE', 'mode': 'storage', 'path': relative,
                           'cards': list(_cards(manager._data['config']))})
        elif type(manager) is LovelaceYAML:
            meta = definitions.get(route)
            if not isinstance(meta, dict) or manager.config != {**meta, 'url_path': route}:
                routes.append({'route': label, 'status': 'yaml_registration_mismatch', 'mode': 'yaml', 'path': ''})
                continue
            path = Path(manager.path)
            try:
                relative = path.relative_to(root).as_posix()
                if path.is_symlink() or not path.is_file():
                    raise ValueError('missing YAML dashboard')
                parsed = yaml.safe_load(path.read_text())
                if not isinstance(parsed, dict):
                    raise ValueError('dashboard root')
            except (OSError, ValueError, yaml.YAMLError):
                routes.append({'route': label, 'status': 'yaml_source_unavailable', 'mode': 'yaml', 'path': ''})
                continue
            found_yaml.add(route)
            yaml_roots.append(relative)
            if _dynamic_dashboard_strategy(parsed):
                routes.append({'route': label, 'status': 'dynamic_strategy_unverified',
                               'mode': 'yaml', 'path': relative})
                continue
            routes.append({'route': label, 'status': 'COMPLETE', 'mode': 'yaml', 'path': relative,
                           'cards': list(_cards(parsed))})
        else:
            routes.append({'route': label, 'status': 'unknown_native_manager_type', 'mode': 'unknown', 'path': ''})
    if found_yaml != set(definitions):
        errors.append(('dashboard', 'unbound_yaml_route'))
    inventory = root / '.storage/lovelace_dashboards'
    storage_routes = {route: manager for route, manager in managers.items()
                      if route is not None and type(manager) is LovelaceStorage}
    if inventory.exists():
        try:
            if inventory.is_symlink():
                raise ValueError('dashboard inventory symlink')
            data = _strict_json(inventory.read_bytes())['data']['items']
            indexed = {row['url_path']: row for row in data}
            if len(indexed) != len(data) or set(indexed) != set(storage_routes):
                raise ValueError('dashboard storage membership mismatch')
            if any(storage_routes[route].config != row for route, row in indexed.items()):
                raise ValueError('dashboard storage metadata mismatch')
        except (OSError, ValueError, KeyError, TypeError):
            errors.append(('dashboard', 'storage_inventory_unverified'))
    elif storage_routes:
        errors.append(('dashboard', 'storage_inventory_missing'))
    return routes, sorted(yaml_roots)


async def _stimulus(hass, producer, bindings, script_service="mixed_source"):
    """Two real source events and two actual service calls; restore source."""
    events = {'automation': [], 'script': []}
    removes = [hass.bus.async_listen('mixed_yaml_' + name + '_result',
               lambda event, name=name: events[name].append(str(event.data['value']))) for name in events]
    original = producer.native_value
    samples = []
    try:
        for value in STIMULUS_VALUES:
            for target in events.values():
                target.clear()
            producer._attr_native_value = value
            producer.async_write_ha_state()
            await asyncio.wait_for(hass.async_block_till_done(), 20)
            await asyncio.wait_for(hass.services.async_call('script', script_service, {}, blocking=True), 20)
            await asyncio.wait_for(hass.async_block_till_done(), 20)
            templates = {name: hass.states.get(row['entity_id']).state
                         for name, row in bindings['template'].items()}
            samples.append({'source': value, 'automation': list(events['automation']),
                            'script': list(events['script']), 'template': templates})
    finally:
        for remove in removes:
            remove()
        producer._attr_native_value = original
        producer.async_write_ha_state()
        await asyncio.wait_for(hass.async_block_till_done(), 20)
    return samples


def _observed_consumers(bindings, samples, script_service="mixed_source", selected_script="mixed_source"):
    """Require both discriminating fixture stimuli and their expected outcomes."""
    if tuple(sample.get('source') for sample in samples) != STIMULUS_VALUES:
        return []
    observed = []
    if len(bindings['automation']) == 1 and all(sample['automation'] == [str(sample['source'])] for sample in samples):
        observed.extend('automation:' + name for name in bindings['automation'])
    expected_scripts = {selected_script, script_service}
    if set(bindings['script']) == expected_scripts and all(sample['script'] == [str(sample['source'])] for sample in samples):
        observed.extend('script:' + name for name in bindings['script'])
    for name in bindings['template']:
        if name.startswith('sensor:'):
            expected = [str(value * 2) for value in STIMULUS_VALUES]
        elif name.startswith('binary_sensor:'):
            expected = ['on', 'off']
        else:
            continue
        if [sample['template'].get(name) for sample in samples] == expected:
            observed.append('template:' + name)
    return sorted(observed)


def _runtime_bindings(hass, errors):
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.helpers.entity_platform import async_get_platforms
    bindings = {'automation': {}, 'script': {}, 'template': {}}
    tokens = []
    components = hass.data.get(DATA_INSTANCES, {})
    for domain in ('automation', 'script'):
        component = components.get(domain)
        if component is None:
            errors.append((domain, 'native_component_not_loaded'))
            continue
        for entity in component.entities:
            if entity.hass is not hass or not isinstance(entity.unique_id, str):
                errors.append((domain, 'runtime_entity_identity_unverified'))
                continue
            if entity.unique_id in bindings[domain]:
                errors.append((domain, 'duplicate_runtime_identity'))
                continue
            raw = _walk(entity.raw_config, errors, domain + ':' + entity.unique_id)
            entry = entity.registry_entry
            if entry is None or entry.entity_id != entity.entity_id:
                errors.append((domain + ':' + entity.unique_id, 'registry_binding_missing'))
            if domain == 'automation':
                if entity._is_enabled and entity._async_detach_triggers is None:
                    errors.append((domain + ':' + entity.unique_id, 'enabled_trigger_not_attached'))
            else:
                service = hass.services._services.get('script', {}).get(entity.unique_id)
                if getattr(getattr(getattr(service, 'job', None), 'target', None), '__self__', None) is not entity:
                    errors.append((domain + ':' + entity.unique_id, 'service_owner_unverified'))
            bindings[domain][entity.unique_id] = {'entity_id': entity.entity_id,
                'refs': refs(raw), 'blueprint': getattr(entity, '_blueprint_inputs', None) is not None}
            tokens.append((domain, entity.unique_id, id(entity), id(entry), id(entity.raw_config)))
    for platform in async_get_platforms(hass, 'template'):
        for entity in platform.entities.values():
            identity = platform.domain + ':' + str(entity.unique_id)
            if entity.hass is not hass or not entity.unique_id:
                errors.append((identity, 'runtime_entity_identity_unverified'))
                continue
            if identity in bindings['template']:
                errors.append((identity, 'duplicate_runtime_identity'))
                continue
            entry = entity.registry_entry
            if entry is None or entry.entity_id != entity.entity_id:
                errors.append((identity, 'registry_binding_missing'))
            bindings['template'][identity] = {'entity_id': entity.entity_id, 'refs': [],
                                               'blueprint': getattr(entity, '_blueprint_inputs', None) is not None}
            tokens.append(('template', identity, id(entity), id(entry)))
    return bindings, tuple(sorted(tokens))


def _unique_yaml(raw):
    import yaml
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError('duplicate YAML key')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return yaml.load(raw, Loader=UniqueLoader)


def _plan_graph(plan_yaml, root, mapping, dashboard_roots):
    # The existing planner's exact public contract is a tuple, not a list.
    roots = tuple(dashboard_roots)
    if any(type(path) is not str for path in roots):
        raise ValueError('invalid dashboard root')
    return plan_yaml(str(root), mapping, yaml_dashboards=roots)


def _entry_census(hass, errors):
    registry = hass.data.get(__import__('homeassistant.helpers.entity_registry', fromlist=['DATA_REGISTRY']).DATA_REGISTRY)
    if registry is None:
        raise ValueError('entity registry unavailable')
    entries = []
    for entry in hass.config_entries.async_entries():
        associated = [row for row in registry.entities.values() if row.config_entry_id == entry.entry_id]
        live = sorted(row.entity_id for row in associated if hass.states.get(row.entity_id) is not None)
        configuration = _walk({'data': entry.data, 'options': entry.options}, errors,
                              'config_entry:' + entry.entry_id)
        entries.append({'id': entry.entry_id, 'domain': entry.domain, 'state': entry.state.value,
                        'live_entity_ids': live, 'refs': refs(configuration),
                        'registry_count': len(associated), 'enabled_count': sum(row.disabled_by is None for row in associated),
                        'dynamic': any('{{' in value or '{%' in value for value in _strings(configuration))})
    return sorted(entries, key=lambda entry: entry['id'])


async def collect_native(hass, root, producer, mapping, *, script_service="mixed_source", selected_script="mixed_source"):
    """Independently enumerate actual fixture sources, owners and behavior."""
    from homeassistant.config import async_hass_config_yaml

    root = Path(root).resolve(strict=True)
    if len(mapping) != 1:
        raise ValueError('bounded fixture expects one source mapping')
    source = next(iter(mapping))
    if producer.hass is not hass or producer.entity_id != source or hass.states.get(source) is None:
        raise ValueError('source is not the actual loaded producer')
    errors = []
    from batch_readback import native_empty_auth_snapshot
    auth_before = native_empty_auth_snapshot(hass, root)
    frontend_before = native_frontend_snapshot(hass, root)
    config = _walk(await asyncio.wait_for(async_hass_config_yaml(hass), 30), errors)
    dashboards, dashboard_roots = _dashboard_census(hass, root, config, errors)
    dashboard_snapshot = json.dumps(dashboards, sort_keys=True, allow_nan=False)
    loaded_components = sorted(hass.config.components)
    network = _network_evidence()
    for route in dashboards:
        for card in route.get('cards', []):
            card['state_readback'] = {entity_id: (None if (state := hass.states.get(entity_id)) is None else
                {'state': state.state, 'latitude': state.attributes.get('latitude'),
                 'longitude': state.attributes.get('longitude')}) for entity_id in card['refs']}
    plan_yaml = importlib.import_module('renamer_input_baseline_regression.yaml_plan').plan_yaml
    graph = await hass.async_add_executor_job(lambda: _plan_graph(plan_yaml, root, mapping, dashboard_roots))
    errors.extend(('graph:' + issue.path, issue.code) for issue in graph.issues)
    source_specs = {'automation': {}, 'script': {}, 'template': {}}
    source_data = {}
    graph_hashes = {row.path: row.sha256 for row in graph.files}
    for path in ('mixed_automations.yaml', 'mixed_scripts.yaml', 'template.yaml'):
        source_file = root / path
        if source_file.is_symlink() or not source_file.is_file():
            raise ValueError('source file is missing or symlinked')
        raw = source_file.read_bytes()
        if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != graph_hashes.get(path):
            raise ValueError('source disagrees with graph')
        source_data[path] = _unique_yaml(raw)
    automation = source_data['mixed_automations.yaml'] or []
    script = source_data['mixed_scripts.yaml'] or {}
    template = source_data['template.yaml'] or []
    for row in automation:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            raise ValueError('unidentified automation source')
        if row['id'] in source_specs['automation']:
            raise ValueError('duplicate automation identity')
        source_specs['automation'][row['id']] = refs(_walk(row, errors))
    for key, value in script.items():
        source_specs['script'][key] = refs(_walk(value, errors))
    for block in template:
        if not isinstance(block, dict) or not isinstance(block.get('unique_id'), str):
            raise ValueError('unidentified template block')
        for domain in ('sensor', 'binary_sensor'):
            for row in block.get(domain, []):
                identity = domain + ':' + block['unique_id'] + '-' + row['unique_id']
                if identity in source_specs['template']:
                    raise ValueError('duplicate template identity')
                source_specs['template'][identity] = refs(_walk(row, errors))
    bindings, initial_tokens = _runtime_bindings(hass, errors)
    runtime_entities = sorted(state.entity_id for state in hass.states.async_all())
    active_blueprints = any(row['blueprint'] for family in bindings.values() for row in family.values())
    declared_blueprints = any('use_blueprint' in value for value in _strings(config))
    if declared_blueprints and not active_blueprints:
        errors.append(('blueprints', 'configured_blueprint_not_bound_to_runtime'))
    blueprint_absence_proven = (not active_blueprints and not declared_blueprints and not errors
        and all(set(source_specs[domain]) == set(bindings[domain]) for domain in source_specs))
    if not blueprint_absence_proven:
        errors.append(('blueprints', 'inactive_source_census_unverified'))
    entries = _entry_census(hass, errors)
    graph_paths = sorted(row.path for row in graph.files)
    for required in ('configuration.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml', 'template.yaml'):
        if required not in graph_paths:
            errors.append((required, 'active_source_outside_graph'))
    # Scan every captured local YAML/JSON/Store outside the native graph, with
    # no reliance on the mutation selection. Binary/private sources are handled
    # by the existing capture_tree verifier and never silently included here.
    outside, inactive_blueprints = [], []
    known_stores = frozenset(('core.entity_registry', 'core.device_registry', 'core.config_entries',
        'core.area_registry', 'core.floor_registry', 'core.label_registry', 'core.restore_state',
        'core.uuid', 'auth', 'auth_provider.homeassistant', 'http', 'http.auth', 'onboarding',
        'lovelace_dashboards', 'lovelace_resources', 'repairs.issue_registry'))
    active_dashboard_paths = {row['path'] for row in dashboards if row['status'] == 'COMPLETE'}
    paths = sorted(root.rglob('*'))
    if len(paths) > 10_000:
        raise ValueError('fixture file count limit')
    total = 0
    for path in paths:
        if path.is_symlink():
            errors.append((path.relative_to(root).as_posix(), 'symlink_unverified'))
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in graph_paths or rel in active_dashboard_paths:
            continue
        if rel.startswith('.storage/'):
            key = rel.removeprefix('.storage/')
            if key in known_stores or key.startswith('ha_entity_renamer.'):
                continue
            if key.startswith('ha_entity_renamer_backups/'):
                continue
            errors.append((rel, 'unclassified_storage_source'))
            continue
        if rel.startswith('blueprints/') and blueprint_absence_proven:
            inactive_blueprints.append(rel)
            continue
        if rel in ('fixture.db', 'fixture.db-shm', 'fixture.db-wal'):
            continue
        if path.suffix.lower() in ('.py', '.js', '.ts', '.sh', '.db', '.sqlite', '.sqlite3'):
            if rel not in ('fixture.db',):
                errors.append((rel, 'unclassified_executable_or_database'))
            continue
        if path.suffix.lower() not in ('.yaml', '.yml', '.json'):
            if path.suffix.lower() not in ('.log',):
                errors.append((rel, 'unclassified_file'))
            continue
        if path.name == 'secrets.yaml':
            errors.append((rel, 'private_external_source_unverified'))
            continue
        try:
            size = path.stat().st_size
            total += size
            if size > 2_000_000 or total > 16_000_000:
                raise ValueError('outside source size limit')
            content = path.read_text()
        except (OSError, UnicodeError):
            errors.append((rel, 'outside_source_unreadable'))
            continue
        if source in refs(content):
            outside.append(rel)
        elif ('{{' in content or '{%' in content or 'custom:' in content.lower()
              or re.search(r'\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2})', content)):
            errors.append((rel, 'dynamic_outside_source_unverified'))
    samples = await _stimulus(hass, producer, bindings, script_service)
    observed_consumers = _observed_consumers(bindings, samples, script_service, selected_script)
    final_bindings, final_tokens = _runtime_bindings(hass, errors)
    if final_bindings != bindings or final_tokens != initial_tokens:
        errors.append(('runtime', 'bindings_changed_during_census'))
    if sorted(state.entity_id for state in hass.states.async_all()) != runtime_entities:
        errors.append(('runtime', 'entity_inventory_changed_during_census'))
    final_graph = await hass.async_add_executor_job(lambda: _plan_graph(plan_yaml, root, mapping, dashboard_roots))
    if final_graph != graph:
        errors.append(('graph', 'sources_changed_during_census'))
    if _entry_census(hass, errors) != entries:
        errors.append(('integrations', 'config_entries_changed_during_census'))
    if sorted(hass.config.components) != loaded_components:
        errors.append(('integrations', 'loaded_components_changed_during_census'))
    final_dashboards, final_roots = _dashboard_census(hass, root, config, errors)
    if json.dumps(final_dashboards, sort_keys=True, allow_nan=False) != dashboard_snapshot or final_roots != dashboard_roots:
        errors.append(('dashboard', 'inventory_changed_during_census'))
    if _network_evidence() != network:
        errors.append(('external', 'network_changed_during_census'))
    auth_after = native_empty_auth_snapshot(hass, root)
    frontend_after = native_frontend_snapshot(hass, root)
    observed = {'configuration': config, 'source': source, 'graph': graph_paths,
        'native_auth': {'before': auth_before, 'after': auth_after},
        'native_frontend': {'before': frontend_before, 'after': frontend_after},
        'source_specs': source_specs, 'bindings': bindings, 'entries': entries, 'runtime_entities': runtime_entities,
        'loaded_components': loaded_components, 'network': network,
        'dashboards': dashboards, 'observed_consumers': sorted(observed_consumers), 'samples': samples,
        'outside_consumers': outside, 'inactive_blueprints': inactive_blueprints,
        'collection_errors': errors}
    # Validate serialization before emitting or deriving a result. NaN, custom
    # objects and recursive data are not silently converted into success.
    json.dumps(observed, allow_nan=False)
    return audit(observed)
