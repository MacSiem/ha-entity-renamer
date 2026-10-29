"""Shared read-only pinned-code proof for the two bounded native unload audits."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import sys

from .native_utility_setup_audit import _code_objects


def prove_methods(root, pins, refs):
    """Return exact descriptors and codes; never install or replace a method.

    pins must be the caller's fixed supported source table, never user input.
    Utility keeps its existing full source table and exact method qualifiers.
    """
    root = Path(root)
    methods, codes, compiled = [], [], {}
    for owner, name in refs:
        descriptor = getattr(owner, name)
        method = descriptor.fget if type(descriptor) is property else descriptor
        code = getattr(method, '__code__', None)
        if code is None:
            raise ValueError('native_unload_code_required')
        path = Path(code.co_filename)
        if path.resolve() != path or not path.is_relative_to(root):
            raise ValueError('native_unload_code_path')
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        if relative not in pins or hashlib.sha256(raw).hexdigest() != pins[relative]:
            raise ValueError('native_unload_source_pin')
        if str(path) not in compiled:
            compiled[str(path)] = tuple(_code_objects(compile(raw, str(path), 'exec',
                dont_inherit=True, optimize=sys.flags.optimize)))
        qualifier = ('Entity.__async_remove_impl' if name == '_Entity__async_remove_impl'
                     else name if inspect.ismodule(owner) else owner.__name__ + '.' + name)
        if not any(code == item and item.co_qualname == qualifier
                   for item in compiled[str(path)]):
            raise ValueError('native_unload_code_changed')
        methods.append(descriptor)
        codes.append(code)
    return tuple(methods), tuple(codes)


def unchanged(refs, methods, codes):
    """Recheck bindings and code identity without invoking descriptors."""
    for (owner, name), saved, code in zip(refs, methods, codes, strict=True):
        current = getattr(owner, name)
        method = current.fget if type(current) is property else current
        if current != saved or getattr(method, '__code__', None) is not code:
            raise ValueError('native_unload_binding_changed')
