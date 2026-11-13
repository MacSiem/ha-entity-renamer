"""Typed native group-total restore payload from an actual unload receipt.

No cache or disk write and no lifecycle authority. The copied native extra data
contains the exact captured group total instead of the vendor's rounded display
value; native RestoreSensor will parse the standard Decimal representation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from . import native_restore_state_payload as native


class PowercalcGroupRestoreError(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise PowercalcGroupRestoreError('powercalc_group_restore_' + reason) from None


@dataclass(frozen=True)
class PowercalcGroupRestorePayload:
    before_json: bytes
    after_json: bytes
    full_total: str
    precision: int
    unit: str


def prepare(stored, target, *, full_total, precision, unit):
    return _prepare(stored, target, full_total=full_total, precision=precision, unit=unit,
                    same_id=False)


def prepare_same_id(stored, *, full_total, precision, unit):
    """Prepare exact restore data for an unchanged group in a global reload.

    This is detached data only; an inactive owner and CAS on the actual
    directional preimage are required before applying it.
    """
    api = native._native()
    source = native._capture(stored, api)['state']['entity_id']
    return _prepare(stored, source, full_total=full_total, precision=precision, unit=unit,
                    same_id=True)


def _prepare(stored, target, *, full_total, precision, unit, same_id):
    from homeassistant.components.sensor import SensorExtraStoredData
    _need(type(target) is str and target.startswith('sensor.')
          and type(full_total) is str and type(precision) is int and type(unit) is str and unit,
          'typed_capture_required')
    try:
        total = Decimal(full_total)
        _need(total.is_finite() and str(total) == full_total, 'actual_decimal_required')
    except InvalidOperation:
        _need(False, 'actual_decimal_required')
    api = native._native()
    before = native._capture(stored, api)
    source = before['state']['entity_id']
    _need((source == target) is same_id and source.split('.', 1)[0] == target.split('.', 1)[0]
          and type(before['extra_data']) is dict, 'native_source_or_target')
    prior = SensorExtraStoredData.from_dict(before['extra_data'])
    _need(type(prior) is SensorExtraStoredData and prior.native_unit_of_measurement == unit
          and prior.native_value == round(total, precision), 'native_total_not_bound')
    after = native._parse(native._canonical(before))
    after['state']['entity_id'] = target
    after['extra_data'] = SensorExtraStoredData(total, unit).as_dict()
    native._shape(after)
    return PowercalcGroupRestorePayload(native._canonical(before), native._canonical(after),
                                        full_total, precision, unit)


def materialize(payload):
    """Create the native target from actual captured payload, exact readback."""
    _need(type(payload) is PowercalcGroupRestorePayload, 'typed_payload_required')
    api = native._native()
    _, Context, State, _, _, StoredState, RestoredExtraData, _, _ = api
    before, after = native._parse(payload.before_json), native._parse(payload.after_json)
    native._shape(before); native._shape(after)
    from homeassistant.components.sensor import SensorExtraStoredData
    total = Decimal(payload.full_total)
    expected = native._parse(native._canonical(before))
    expected['state']['entity_id'] = after['state']['entity_id']
    expected['extra_data'] = SensorExtraStoredData(total, payload.unit).as_dict()
    _need(total.is_finite() and str(total) == payload.full_total
          and type(payload.precision) is int and native._canonical(expected) == payload.after_json,
          'payload_changed')
    row, context = after['state'], after['state']['context']
    state = State(row['entity_id'], row['state'], row['attributes'],
        last_changed=native._timestamp(row['last_changed']),
        last_updated=native._timestamp(row['last_updated']),
        last_reported=native._timestamp(row['last_reported']),
        context=Context(id=context['id'], user_id=context['user_id'], parent_id=context['parent_id']))
    args = (state, RestoredExtraData(after['extra_data']), native._timestamp(after['last_seen']))
    clone = StoredState(*args, after['entity_registry_id']) if api[0] in ('2026.11.0', '2026.11.1') else StoredState(*args)
    _need(native._canonical(native._capture(clone, api)) == payload.after_json, 'native_clone_changed')
    return clone
