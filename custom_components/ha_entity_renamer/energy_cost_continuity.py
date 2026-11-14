"""Preserve a qualified native Energy cost across a source-ID rebind."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from .persistence import PersistenceError


def _need(value):
    if not value:
        raise PersistenceError('energy_generated_cost_continuity_unqualified')


def capture(hass, entity, row):
    """Capture only a settled cost and matching live source before native removal."""
    from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
    from homeassistant.core import State
    source = hass.states.get(row['after_source'])
    baseline = entity._last_energy_sensor_state
    _need(type(source) is State and type(baseline) is State
          and baseline.entity_id == row['before_source']
          and source.entity_id == row['after_source'])
    try:
        previous = Decimal(baseline.state)
        _need(previous.is_finite() and previous >= 0)
        if source.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            current = Decimal(source.state)
            _need(current.is_finite() and current >= 0)
    except (InvalidOperation, ValueError):
        _need(False)
    for key in ('unit_of_measurement', 'state_class'):
        _need(source.attributes.get(key) == baseline.attributes.get(key))
    _need(entity.native_value is not None and entity.last_reset is not None)
    return (source, baseline, entity.native_value, entity.last_reset)


@contextmanager
def transfer(hass, manager, captured):
    """Seed only qualifying new native entities before their first publication."""
    from homeassistant.components.energy.sensor import EnergyCostSensor, SensorManager
    from homeassistant.const import __version__
    from homeassistant.helpers import entity_registry as er
    listeners = [fn for fn in manager._update_listeners
                 if type(getattr(fn, '__self__', None)) is SensorManager
                 and getattr(fn, '__func__', None) is SensorManager._process_manager_data]
    _need(len(listeners) == 1)
    sensor_manager = listeners[0].__self__
    _need(sensor_manager.manager is manager)
    original = sensor_manager.async_add_entities
    _need(callable(original) and 'async_add_entities' in sensor_manager.__dict__)
    registry = er.async_get(hass)
    pending = dict(captured)
    if __version__ in ('2026.11.0', '2026.11.1'):
        from homeassistant.components.energy.sensor import EnergyCostSensorExtraStoredData
        from homeassistant.helpers import restore_state
        restore = restore_state.async_get(hass)
        for row, _ in pending.values():
            stored = restore.async_get_stored_state(row['entity_id'])
            extra = None if stored is None else stored.extra_data
            _need(extra is None or EnergyCostSensorExtraStoredData.from_dict(extra.as_dict()) is None)
    else:
        _need(__version__ in ('2026.10.2', '2026.10.4'))

    def add(entities, *args, **kwargs):
        for entity in entities:
            if type(entity) is not EnergyCostSensor:
                continue
            source_id = entity._config.get('stat_energy_from')
            if source_id not in pending:
                continue
            row, (source, baseline, value, reset) = pending.pop(source_id)
            source_entry = registry.async_get(source_id)
            cost_entry = registry.async_get(row['entity_id'])
            _need(source_entry is not None and source_entry.id == row['source_registry_id']
                  and cost_entry is not None and cost_entry.id == row['registry_id']
                  and cost_entry.unique_id == row['unique_id']
                  and entity._adapter.source_type == 'grid'
                  and entity._adapter.stat_energy_key == 'stat_energy_from'
                  and entity._last_energy_sensor_state is None
                  and hass.states.get(source_id) is source)
            entity._attr_native_value = value
            entity._attr_last_reset = reset
            entity._last_energy_sensor_state = baseline
        return original(entities, *args, **kwargs)

    sensor_manager.async_add_entities = add
    try:
        yield
        _need(not pending)
    finally:
        sensor_manager.async_add_entities = original
