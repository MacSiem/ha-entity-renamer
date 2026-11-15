"""Canonical HA-native JSON snapshot without dropping State attributes."""
from __future__ import annotations

from collections.abc import Mapping
import math

from .native_input_baseline import _loads
from .transaction import Refused


def native_snapshot(value):
    """Use HA's native encoder and require an exact byte-level round trip.

    HA State attribute keys can be string enums. The native encoder preserves
    their JSON key values; the detached JSON image remains suitable for the
    existing receipt codec. Reject nonfinite floats before native encoding so
    they cannot silently become null, and retain signed zero as encoded.
    """
    from homeassistant.helpers.json import _orjson_bytes_default_encoder
    pending = [(value, 0)]
    budget = 1_000_000
    while pending:
        item, depth = pending.pop()
        budget -= 1
        if budget < 0 or depth > 64:
            raise Refused('powercalc_native_snapshot_limit')
        if type(item) is float and not math.isfinite(item):
            raise Refused('powercalc_native_snapshot_nonfinite')
        if isinstance(item, Mapping):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)
    try:
        encoded = _orjson_bytes_default_encoder(value)
        result = _loads(encoded)
        if _orjson_bytes_default_encoder(result) != encoded:
            raise Refused('powercalc_native_snapshot_roundtrip')
        return result
    except Refused:
        raise
    except (ValueError, TypeError, OverflowError) as error:
        raise Refused('powercalc_native_snapshot_invalid') from error
