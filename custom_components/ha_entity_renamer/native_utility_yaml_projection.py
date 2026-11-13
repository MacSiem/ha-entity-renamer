"""Exact Utility YAML source proof, without granting activation authority."""
from copy import deepcopy
from collections.abc import Mapping


def _exact(left, right):
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return (all(isinstance(key, str) for key in (*left, *right))
                and left.keys() == right.keys()
                and all(_exact(left[key], right[key]) for key in left))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_exact(a, b) for a, b in zip(left, right))
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    return type(left) is type(right) and type(left) in (type(None), bool, int, float) and left == right


def source_only(before, after, mapping):
    """Match the whole effective configuration, changing only mapped sources."""
    if (not isinstance(before, Mapping) or not isinstance(after, Mapping)
            or type(mapping) is not dict or not mapping
            or any(type(k) is not str or type(v) is not str for k, v in mapping.items())
            or len(set(mapping.values())) != len(mapping)
            or any({key for key in config if isinstance(key, str) and key.split(' ', 1)[0] == 'utility_meter'}
                   != {'utility_meter'} for config in (before, after))):
        return False
    old, new = before.get('utility_meter'), after.get('utility_meter')
    if not isinstance(old, Mapping) or not isinstance(new, Mapping) or not old:
        return False
    candidate = deepcopy(old)
    for meter, config in candidate.items():
        if not isinstance(meter, str) or not isinstance(config, dict):
            return False
        source = config.get('source')
        if not isinstance(source, str):
            return False
        config['source'] = mapping.get(source, source)
    return _exact(candidate, new) and not _exact(candidate, old)


def native_projection(checked):
    """Project already schema-validated meters; the worker owns normalization."""
    meters = checked.get('utility_meter')
    if type(meters) is not dict or any(type(name) is not str or type(cfg) is not dict
                                       for name, cfg in meters.items()):
        raise ValueError('utility_yaml_native_projection_unavailable')
    return [[name, meters[name]] for name in sorted(meters)]
