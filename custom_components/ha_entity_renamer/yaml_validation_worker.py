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


async def check(before_root, proposed_root):
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
    return {'status': 'valid', 'version': __version__, 'changed_domains': changed,
            'unsupported_domains': unsupported}


def main():
    logging.disable(logging.CRITICAL)
    try:
        from homeassistant import runner
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            result = asyncio.run(check(sys.argv[1], sys.argv[2]),
                                 loop_factory=getattr(runner, 'create_event_loop', None))
    except BaseException:
        result = {'status': 'checker_failed'}
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
