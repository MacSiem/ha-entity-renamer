"""Isolated Core Utility schema and exact-source proof; emits only digests.

This worker supplies evidence only. It does not authorize YAML activation or
change the shared checker's unsupported-domain decision.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from importlib import util
import json
from pathlib import Path
import sys


def _source(name):
    path = Path(__file__).with_name(name + '.py')
    spec = util.spec_from_file_location('_renamer_utility_' + name, path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _projection(root, worker, projection):
    from homeassistant import loader
    from homeassistant.config import async_hass_config_yaml, async_process_component_and_handle_errors
    from homeassistant.core import HomeAssistant
    from homeassistant.loader import async_get_integration
    hass = HomeAssistant(root)
    loader.async_setup(hass)
    try:
        config = await async_hass_config_yaml(hass)
        integration = await async_get_integration(hass, 'utility_meter')
        checked = await async_process_component_and_handle_errors(hass, config, integration)
        if checked is None:
            raise ValueError('native_schema_rejected')
        return worker._fingerprint(projection.native_projection(checked))
    finally:
        await hass.async_stop(force=True)


async def check(before_root, after_root, mapping):
    from homeassistant.const import __version__
    if __version__ != '2026.11.1':
        raise ValueError('unsupported_core')
    worker = _source('yaml_validation_worker')
    projection = _source('native_utility_yaml_projection')
    before = await worker._effective(before_root)
    after = await worker._effective(after_root)
    if not projection.source_only(before, after, mapping):
        raise ValueError('source_difference_unqualified')
    old = await _projection(before_root, worker, projection)
    new = await _projection(after_root, worker, projection)
    if old == new:
        raise ValueError('native_projection_unchanged')
    return {'status':'valid', 'version':__version__, 'runtime_expected':['utility_meter', old, new]}


def main():
    try:
        if len(sys.argv) != 4:
            raise ValueError('arguments')
        mapping = json.loads(Path(sys.argv[3]).read_text())
        # Native loader diagnostics may contain expanded secrets.
        with open('/dev/null', 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            result = asyncio.run(check(sys.argv[1], sys.argv[2], mapping))
    except BaseException:
        result = {'status':'invalid'}
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0 if result['status'] == 'valid' else 1


if __name__ == '__main__':
    sys.exit(main())
