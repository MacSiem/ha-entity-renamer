"""Exact Utility YAML source proof, without granting activation authority."""
from copy import deepcopy


def source_only(before, after, mapping):
    """Match the whole effective configuration, changing only mapped sources."""
    if (type(before) is not dict or type(after) is not dict
            or type(mapping) is not dict or not mapping
            or any(type(k) is not str or type(v) is not str for k, v in mapping.items())
            or len(set(mapping.values())) != len(mapping)
            or any({key for key in config if type(key) is str and key.split(' ', 1)[0] == 'utility_meter'}
                   != {'utility_meter'} for config in (before, after))):
        return False
    old, new = before.get('utility_meter'), after.get('utility_meter')
    if type(old) is not dict or type(new) is not dict or not old:
        return False
    candidate = deepcopy(old)
    for meter, config in candidate.items():
        if type(meter) is not str or type(config) is not dict:
            return False
        source = config.get('source')
        if type(source) is not str:
            return False
        config['source'] = mapping.get(source, source)
    return candidate == new and candidate != old


def native_projection(checked):
    """Project already schema-validated meters; the worker owns normalization."""
    meters = checked.get('utility_meter')
    if type(meters) is not dict or any(type(name) is not str or type(cfg) is not dict
                                       for name, cfg in meters.items()):
        raise ValueError('utility_yaml_native_projection_unavailable')
    return [[name, meters[name]] for name in sorted(meters)]
