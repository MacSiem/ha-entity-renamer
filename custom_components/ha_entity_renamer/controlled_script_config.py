"""Prepare one native Script configuration from an already sealed prefix.

This module prepares no entity and grants no write authority. The transaction
must authenticate ORIGINAL and the accepted prefix independently before using
this result, and must never treat it as evidence that an effect happened.
"""
from __future__ import annotations

from .transaction import Refused


def _need(value, reason):
    if not value:
        raise Refused('controlled_script_config_' + reason) from None


def _raw(value, depth=0):
    """Decode only plain native raw YAML; reject opaque/native marker values."""
    _need(depth <= 64, 'depth')
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) is list:
        return [_raw(item, depth + 1) for item in value]
    _need(type(value) is dict and set(value) == {'$map'} and type(value['$map']) is list,
          'raw_type_unqualified')
    result = {}
    for pair in value['$map']:
        _need(type(pair) is list and len(pair) == 2, 'raw_key_unqualified')
        key = pair[0]
        if type(key) is dict and set(key) == {'$int'} and type(key['$int']) is int:
            key = key['$int']
        else:
            _need(type(key) is str, 'raw_key_unqualified')
        _need(key not in result, 'raw_key_unqualified')
        result[key] = _raw(pair[1], depth + 1)
    return result


async def prepare_script(hass, program, active, key, *, guard):
    """Validate exactly one selected script using HA's actual config parser."""
    from .dependent_config_program import DependentConfigProgram
    from .yaml_validation_worker import _normal
    from homeassistant.components.script import _prepare_script_config
    from homeassistant.components.script.config import async_validate_config_item, ValidationStatus

    _need(type(program) is DependentConfigProgram and type(active) is frozenset
          and type(key) is str and key and callable(guard), 'scope')
    guard()
    scripts = dict(program.rows(active)).get('script')
    _need(type(scripts) is list, 'script_image_missing')
    matches = [row for row in scripts if type(row) is list and len(row) == 11 and row[0] == key]
    _need(len(matches) == 1, 'script_image_ambiguous')
    expected = matches[0]
    raw = _raw(expected[1])
    _need(type(raw) is dict, 'raw_config_unqualified')
    validated = await async_validate_config_item(hass, key, raw)
    guard()
    _need(validated is not None and validated.validation_status is ValidationStatus.OK
          and validated.raw_blueprint_inputs is None, 'native_validation_failed')
    projection = [key, validated.raw_config, validated['sequence'], validated['mode'],
                  validated['max'], validated['max_exceeded'], validated.get('variables'),
                  validated.get('icon'), validated['description'], validated['fields'], validated['trace']]
    _need(_normal(projection) == expected, 'native_projection_changed')
    prepared = await _prepare_script_config(hass, {'script': {key: validated}})
    guard()
    _need(type(prepared) is list and len(prepared) == 1 and prepared[0].key == key
          and prepared[0].validation_status is ValidationStatus.OK, 'native_prepare_failed')
    return prepared[0]
