"""Owner-run actual native HA current/min name regression; synthetic fixture only."""
from dataclasses import replace
from types import MappingProxyType


async def run_names(hass, bridge, meter):
    from homeassistant.const import __version__
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState
    from homeassistant.helpers import entity_registry as er, device_registry as dr
    from .native_registry import RegistryConflict
    from .prepared import encode_change, decode_change

    io = bridge.io
    native_runtime = bridge.runtime_probe
    def observed_runtime(change, target):
        try:
            return native_runtime(change, target)
        except Exception as error:
            # All inputs here are synthetic; observer errors contain codes only.
            print({'native_name_observer_failure': type(error).__name__,
                   'reason': str(error), 'kind': change.kind}, flush=True)
            raise
    bridge.runtime_probe = observed_runtime
    registry = er.async_get(hass)
    meter_row = registry.async_get(meter.entity_id)
    before_id, before_unique = meter_row.id, meter_row.unique_id
    registry.async_update_entity(meter.entity_id, name='Native baseline meter')
    await io.settle()
    await io.flush('entity')
    for desired in ('Native renamed meter', None, ''):
        change = await bridge.prepare('entity', before_id, {'name': desired}, lambda: None)
        assert change.runtime_before_json
        # Recovery uses serialized preparation; no process-local observer cache.
        change = decode_change(encode_change(change))
        await bridge.apply(change, lambda: None)
        proof = await bridge.inspect(change)
        assert proof.position == 'after' and proof.runtime is True and proof.durability is True, proof
        row = registry.async_get(meter.entity_id)
        assert row.id == before_id and row.unique_id == before_unique
        assert meter.registry_entry is row
        await bridge.compensate(change, lambda: None)
        assert (await bridge.inspect(change)).position == 'before'
        missing = replace(change, runtime_before_json='')
        assert (await bridge.inspect(missing)).position == 'unknown'
        state = hass.states.get(meter.entity_id)
        attributes = dict(state.attributes, friendly_name='injected stale runtime name')
        hass.states.async_set(meter.entity_id, state.state, attributes)
        assert (await bridge.inspect(change)).position == 'unknown'
        meter.async_write_ha_state()
        await io.settle()

    # Real threshold config entry supplies a supported HA-owned device binding.
    entry = ConfigEntry(domain='threshold', title='Native name fixture', data={},
        options={'name': 'Native threshold name fixture', 'entity_id': meter.entity_id,
                 'lower': 20, 'upper': None, 'hysteresis': 0},
        discovery_keys=MappingProxyType({}), source='user', version=1,
        minor_version=2 if __version__ == '2026.10.2' else 1,
        subentries_data=[], unique_id='renamer-name-fixture')
    await hass.config_entries.async_add(entry)
    await io.settle()
    assert entry.state is ConfigEntryState.LOADED, entry.state
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(config_entry_id=entry.entry_id,
        identifiers={('threshold', 'native-name-fixture')}, name='Native probe device')
    device = devices.async_update_device(device.id, name_by_user='Native baseline device')
    registry.async_update_entity(meter.entity_id, device_id=device.id)
    # Device listeners are installed at native entity addition, not by a late
    # manual device_id edit on an already-active entity. Re-add a real producer
    # against its same preconfigured registry identity before testing names.
    platform = meter.platform
    await platform.async_remove_entity(meter.entity_id)
    meter = type(meter)()
    meter._attr_has_entity_name = True
    # Native platform setup assigns its ConfigEntry before adding device-info
    # entities; mirror that setup in this isolated synthetic platform.
    platform.config_entry = entry
    meter._attr_device_info = {'identifiers': {('threshold', 'native-name-fixture')},
                               'name': 'Native probe device'}
    registry.async_update_entity(meter_row.entity_id, name='Explicit meter override', has_entity_name=True)
    await platform.async_add_entities([meter])
    await io.settle()
    assert meter.registry_entry.id == before_id
    assert meter.device_entry is devices.async_get(device.id)
    await io.flush('entity')
    await io.flush('device')
    for desired in ('Native renamed device', None, ''):
        change = await bridge.prepare('device', device.id, {'name_by_user': desired}, lambda: None)
        await bridge.apply(change, lambda: None)
        proof = await bridge.inspect(decode_change(encode_change(change)))
        assert proof.position == 'after' and proof.runtime is True and proof.durability is True, proof
        assert devices.async_get(device.id).identifiers == device.identifiers
        assert registry.async_get(meter.entity_id).name == 'Explicit meter override'
        await bridge.compensate(change, lambda: None)
        assert (await bridge.inspect(change)).position == 'before'

    # Exercise actual device-derived name, without an entity-name override.
    registry.async_update_entity(meter.entity_id, name=None)
    await io.settle()
    await io.flush('entity')
    change = await bridge.prepare('device', device.id, {'name_by_user': 'Derived name'}, lambda: None)
    await bridge.apply(change, lambda: None)
    proof = await bridge.inspect(change)
    assert proof.position == 'after' and proof.runtime is True, proof
    assert 'Derived name' in hass.states.get(meter.entity_id).attributes['friendly_name']
    await bridge.compensate(change, lambda: None)

    # Disabled row remains absent; no invented active value is asserted.
    disabled = registry.async_get_or_create('sensor', 'threshold', 'native-disabled-name',
        config_entry=entry, disabled_by=er.RegistryEntryDisabler.USER,
        device_id=device.id, suggested_object_id='native_disabled_name')
    await io.settle()
    await io.flush('entity')
    change = await bridge.prepare('entity', disabled.id, {'name': 'Disabled rename'}, lambda: None)
    await bridge.apply(change, lambda: None)
    assert hass.states.get(disabled.entity_id) is None
    assert (await bridge.inspect(change)).position == 'after'
    await bridge.compensate(change, lambda: None)
    # Native device observer covers mixed active/disabled scope as well.
    change = await bridge.prepare('device', device.id, {'name_by_user': 'Mixed scope'}, lambda: None)
    await bridge.apply(change, lambda: None)
    assert (await bridge.inspect(change)).position == 'after'
    await bridge.compensate(change, lambda: None)
    # Device scope includes disabled + active entities and rejects membership drift.
    change = await bridge.prepare('device', device.id, {'name_by_user': 'Membership guard'}, lambda: None)
    registry.async_update_entity(disabled.entity_id, name='Foreign preference')
    assert (await bridge.inspect(change)).position == 'unknown'
    registry.async_update_entity(disabled.entity_id, name=None)
    await io.settle()
    await io.flush('entity')
    # Native config-entry inactive lifecycle, beyond an individually disabled row.
    from homeassistant.config_entries import ConfigEntryDisabler
    threshold_rows = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert threshold_rows
    threshold_id = next(row.id for row in threshold_rows if row.platform == 'threshold'
                        and row.entity_id.startswith('binary_sensor.'))
    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await io.settle()
    assert entry.state is ConfigEntryState.NOT_LOADED
    await io.flush('entity')
    change = await bridge.prepare('entity', threshold_id, {'name': 'Inactive entry rename'}, lambda: None)
    await bridge.apply(change, lambda: None)
    assert (await bridge.inspect(change)).position == 'after'
    await bridge.compensate(change, lambda: None)

    print({'ha': __version__, 'native_name_only': 'PASS', 'device_name_by_user': 'PASS',
           'durable_preparation_reopen': 'PASS', 'inactive_absence': 'PASS',
           'stale_runtime_refused': 'PASS', 'foreign_preferences_refused': 'PASS',
           'scope': 'synthetic isolated native fixture; no installed-product claim'})
