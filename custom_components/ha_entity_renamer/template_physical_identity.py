"""Actual physical YAML source coordinates, never registry/unique identity."""
import json
from pathlib import PurePosixPath

_PREFIX = '@physical-template-v1:'


def physical_template_key(domain, path, line):
    if (domain not in ('template.sensor', 'template.binary_sensor')
            or type(path) is not str or not path or path.startswith('.storage/')
            or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
            or str(PurePosixPath(path)) != path or type(line) is not int or line < 1):
        raise ValueError('template_physical_origin_invalid')
    return _PREFIX + json.dumps([domain, path, line], separators=(',', ':'), ensure_ascii=True)


def is_physical_definition(definition):
    # A named user UID may contain ANY string, including our spelling. The
    # actual key-origin relation in the sealed AST disambiguates its type.
    return (definition.domain in ('template.sensor', 'template.binary_sensor')
        and definition.key_origin == definition.origin
        and definition.native_key == physical_template_key(definition.domain,
            definition.origin.path, definition.origin.start_line))
