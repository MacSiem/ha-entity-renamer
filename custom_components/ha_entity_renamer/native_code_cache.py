"""Bounded cache of pure compilation, never of source or runtime validation."""
from functools import lru_cache
from types import CodeType


@lru_cache(maxsize=32)
def compiled_code_objects(source: bytes, filename: str, optimize: int):
    """Reuse code only for identical freshly read bytes and compiler inputs.

    Callers still read and authenticate sources and inspect live bindings on
    every check. No path, stat, function identity or previous verdict is cached.
    """
    root = compile(source, filename, 'exec', dont_inherit=True, optimize=optimize)

    def walk(code):
        yield code
        for value in code.co_consts:
            if type(value) is CodeType:
                yield from walk(value)

    return tuple(walk(root))
