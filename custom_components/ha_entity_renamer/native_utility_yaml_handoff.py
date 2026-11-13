"""Selective native remove/factory/add for one active YAML Utility source.

Ephemeral primitive only. Caller owns registry/YAML locks, durable effect
intent, full inventory fence and independent receipt before allowing Apply.
Failure after removal is UNKNOWN and must never trigger implicit replay.
"""
from __future__ import annotations

from copy import deepcopy
import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import sys

from .transaction import Refused


def _need(value, code):
    if not value:
        raise Refused('utility_yaml_handoff_' + code)


def _installed():
    import homeassistant
    from homeassistant.const import __version__
    from homeassistant.components.utility_meter import sensor
    from homeassistant.helpers import entity_platform, restore_state
    from .native_utility_setup_audit import _SOURCES, _code_objects
    from .native_utility_source_gap import _EXTRA
    _need(__version__ == '2026.11.1' and sys.implementation.name == 'cpython', 'runtime')
    root = Path(homeassistant.__file__).resolve().parent
    paths = ('components/utility_meter/__init__.py', 'components/utility_meter/sensor.py',
             'helpers/entity_platform.py', 'helpers/restore_state.py', 'helpers/event.py')
    compiled = {}
    for path in paths:
        file = root / path
        raw = file.read_bytes()
        expected = _SOURCES[__version__].get(path, _EXTRA[__version__].get(path))
        _need(file.resolve() == file and sha256(raw).hexdigest() == expected, 'source_pin')
        compiled[str(file)] = tuple(_code_objects(compile(raw, str(file), 'exec',
                                                          dont_inherit=True, optimize=sys.flags.optimize)))
    for fn in (sensor.async_setup_platform, entity_platform.EntityPlatform.async_remove_entity,
               entity_platform.EntityPlatform.async_add_entities,
               restore_state.RestoreStateData.async_restore_entity_removed):
        code = fn.__code__
        _need(code.co_filename in compiled and any(code == item for item in compiled[code.co_filename]), 'native_code')
    return sensor


def _static(entity):
    state = entity.hass.states.get(entity.entity_id)
    _need(state is not None and entity.extra_restore_state_data is not None, 'state_missing')
    return (entity.entity_id, state.state, deepcopy(entity.extra_restore_state_data.as_dict()),
            entity._next_reset, entity._attr_native_unit_of_measurement)


@dataclass(frozen=True)
class Row:
    meter: str
    old: str
    new: str
    entity_id: str
    source_uuid: str
    platform: object
    entity: object
    before: tuple
    info: dict


class UtilityYAMLHandoff:
    def __init__(self, hass, mapping, rows, foreign, data):
        self.hass, self.mapping, self.rows, self.foreign, self.data = hass, dict(mapping), rows, foreign, data
        self.uncertain = False
        self.pending = None

    @classmethod
    def capture(cls, hass, mapping):
        sensor = _installed()
        from homeassistant.components.utility_meter.const import DATA_UTILITY, DATA_TARIFF_SENSORS
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers import entity_registry as er
        data = hass.data.get(DATA_UTILITY)
        _need(type(data) is dict and type(mapping) is dict and mapping, 'inventory')
        entries = {entry.entry_id for entry in hass.config_entries.async_entries('utility_meter')}
        registry = er.async_get(hass)
        sources = {old: registry.async_get(old) for old in mapping}
        _need(all(row is not None for row in sources.values()), 'source_registry')
        platforms = tuple(async_get_platforms(hass, 'utility_meter'))
        actors = tuple((platform, entity) for platform in platforms
                       for entity in platform.entities.values()
                       if type(entity) is sensor.UtilityMeterSensor and platform.config_entry is None)
        rows, foreign = [], []
        for platform, entity in actors:
            meter = entity._parent_meter
            info = data.get(meter)
            _need(type(info) is dict and meter not in entries, 'meter_identity')
            old = info.get('source')
            if old not in mapping:
                foreign.append((platform, entity, meter, deepcopy({k:v for k,v in info.items() if k != DATA_TARIFF_SENSORS})))
                continue
            _need(entity._sensor_source_id == old and entity.unique_id is None
                  and entity._tariff is None and entity._tariff_entity is None
                  and info.get(DATA_TARIFF_SENSORS) == [entity] and entity._collecting is not None,
                  'unsupported_meter')
            rows.append(Row(meter, old, mapping[old], entity.entity_id, sources[old].id, platform, entity,
                            _static(entity), deepcopy({k:v for k,v in info.items() if k != DATA_TARIFF_SENSORS})))
        expected = {key for key, info in data.items() if key not in entries and type(info) is dict and info.get('source') in mapping}
        _need(bool(rows) and {row.meter for row in rows} == expected and len({row.meter for row in rows}) == len(rows), 'population')
        return cls(hass, mapping, tuple(rows), tuple(foreign), data)

    def check_foreign(self):
        from homeassistant.components.utility_meter.const import DATA_TARIFF_SENSORS
        for platform, entity, meter, info in self.foreign:
            _need(platform.entities.get(entity.entity_id) is entity and self.data.get(meter) is not None
                  and {k:v for k,v in self.data[meter].items() if k != DATA_TARIFF_SENSORS} == info,
                  'foreign_changed')

    async def detach_group(self, old, *, guard, record_intent):
        """Detach all consumers of one source BEFORE its registry rename."""
        _need(not self.uncertain and self.pending is None, 'ownership')
        from homeassistant.components.utility_meter.const import DATA_TARIFF_SENSORS
        from homeassistant.helpers import entity_registry as er
        _installed(); guard(); self.check_foreign()
        rows = tuple(row for row in self.rows if row.old == old)
        _need(bool(rows), 'source_scope')
        source = self.hass.states.get(old)
        registry = er.async_get(self.hass)
        native = registry.async_get(old)
        _need(source is not None and native is not None and registry.async_get(rows[0].new) is None
              and all(native.id == row.source_uuid and row.new == rows[0].new for row in rows),
              'original_source_unavailable')
        for row in rows:
            _need(row.platform.entities.get(row.entity_id) is row.entity and _static(row.entity) == row.before
                  and self.data[row.meter][DATA_TARIFF_SENSORS] == [row.entity], 'meter_changed')
        await record_intent(rows)
        guard(); self.check_foreign()
        _need(self.hass.states.get(old) is source and all(_static(row.entity) == row.before for row in rows),
              'source_changed')
        self.uncertain = True
        for row in rows:
            await row.platform.async_remove_entity(row.entity_id)
            guard(); self.check_foreign()
            _need(self.hass.states.get(old) is source and row.entity_id not in row.platform.entities,
                  'source_changed_during_detach')
        self.pending = (old, rows, source.state, dict(source.attributes))

    async def attach_group(self, old, *, guard):
        """Native factory/add AFTER rename; require one unchanged source sample."""
        from homeassistant.components.utility_meter.const import DATA_TARIFF_SENSORS, CONF_METER
        from homeassistant.helpers import entity_registry as er
        _need(self.uncertain and self.pending is not None and self.pending[0] == old, 'detach_required')
        _, rows, prior_value, prior_attributes = self.pending
        sensor = _installed(); guard(); self.check_foreign()
        source = self.hass.states.get(rows[0].new)
        registry = er.async_get(self.hass)
        native = registry.async_get(rows[0].new)
        _need(source is not None and source.state == prior_value and dict(source.attributes) == prior_attributes
              and native is not None and all(native.id == row.source_uuid for row in rows)
              and registry.async_get(old) is None, 'renamed_source_unavailable')
        result = []
        for row in rows:
            _need(self.hass.states.get(row.new) is source, 'source_changed_during_attach')
            info = self.data[row.meter]
            _need({k:v for k,v in info.items() if k != DATA_TARIFF_SENSORS} == row.info
                  and info[DATA_TARIFF_SENSORS] == [row.entity], 'meter_info_changed')
            info['source'] = row.new
            info[DATA_TARIFF_SENSORS].clear()
            made = []
            await sensor.async_setup_platform(self.hass, {}, lambda entities: made.extend(entities),
                                              {row.meter: {CONF_METER: row.meter}})
            guard(); _need(len(made) == 1 and info[DATA_TARIFF_SENSORS] == made, 'factory')
            await row.platform.async_add_entities(made)
            await asyncio.sleep(0)
            guard(); self.check_foreign()
            new_entity = made[0]
            _need(self.hass.states.get(row.new) is source and new_entity.entity_id == row.entity_id
                  and row.platform.entities.get(row.entity_id) is new_entity
                  and new_entity._sensor_source_id == row.new and new_entity._collecting is not None
                  and _static(new_entity) == row.before, 'runtime_readback')
            result.append(new_entity)
        self.pending = None
        self.uncertain = False
        return tuple(result)
