"""Isolated native HA checker. Executed by path in a separate Python process.

Never use scripts.check_config.check(): that wrapper patches YAML loaders.
Only the native async_check_config API is called here. stdout/stderr are private.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout, redirect_stderr
import json
import logging
import os
import sys


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


async def _expected_runtime(root, domains, *, source_rows=None):
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
                    if domain == 'script':
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


async def check(before_root, proposed_root, *, with_sources=False):
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
    old_runtime = await _expected_runtime(before_root, original_domains, source_rows=source_rows)
    new_runtime = await _expected_runtime(proposed_root, changed)
    result = {'status': 'valid', 'version': __version__, 'changed_domains': changed,
              'unsupported_domains': unsupported,
              'runtime_expected': [(domain, old_runtime.get(domain), new_runtime.get(domain))
                                   for domain in changed]}
    if with_sources:
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
                                       with_sources=len(sys.argv) == 4 and sys.argv[3] == '--source-evidence'),
                                 loop_factory=getattr(runner, 'create_event_loop', None))
    except BaseException:
        result = {'status': 'checker_failed'}
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
