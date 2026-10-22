"""Active native Threshold helper in the isolated linked migration probe."""
from types import MappingProxyType, SimpleNamespace


async def create_helper(hass, old, new, kind="threshold", component=None, inactive=False):
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState
    from homeassistant.helpers import entity_registry as er
    from .helper_observers import NativeHelperObservers, RegistryMappingProof
    from .helper_native import read_entry
    from .helper_persistence import NativeHelperPersistence
    from .helper_transaction import prepare_helper, HelperTransactionAdapter

    auxiliary = None
    if kind == 'threshold':
        domain, minor = 'threshold', 2
        options = {'name': 'Renamer threshold fixture', 'entity_id': old,
                   'hysteresis': 0.0, 'lower': None, 'upper': 42.5}
    else:
        from homeassistant.components.sensor import SensorEntity
        assert kind in ('min', 'max', 'mean', 'median', 'range', 'sum')
        assert component is not None
        class Auxiliary(SensorEntity):
            _attr_name = 'Renamer auxiliary fixture'
            _attr_unique_id = 'renamer-auxiliary-fixture'
            _attr_native_value = 3
            _attr_native_unit_of_measurement = 'kWh'
            _attr_should_poll = False
        auxiliary = Auxiliary()
        await component.async_add_entities([auxiliary])
        domain, minor = 'min_max', 1
        options = {'name': 'Renamer aggregation fixture',
                   'entity_ids': [old, auxiliary.entity_id], 'type': kind, 'round_digits': 2}
    entry = ConfigEntry(domain=domain, title='Renamer helper fixture',
        data={}, options=options,
        discovery_keys=MappingProxyType({}), source='user', version=1, minor_version=minor,
        subentries_data=[], unique_id='renamer-helper-fixture')
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED, entry.state
    registry = er.async_get(hass)
    rows = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(rows) == 1
    identity, entity_id = rows[0].id, rows[0].entity_id
    if inactive:
        from homeassistant.config_entries import ConfigEntryDisabler
        assert await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
    persistence = NativeHelperPersistence(hass)
    before = read_entry(hass, entry.entry_id)
    await persistence.flush(before, lambda: None)
    change = prepare_helper(before, {old: new})

    observer = await NativeHelperObservers.capture(hass, (before,),
        (RegistryMappingProof(old, new, registry.async_get(old).id),))
    settle = observer.settle
    validate = observer.validate_snapshot
    activation = observer.verify_activation
    runtime = observer.verify_runtime

    async def verify(plan, forward):
        expected = change.after if forward else change.before
        await settle()
        assert read_entry(hass, entry.entry_id) == expected
        assert await persistence.inspect_current(expected)
        await (observer.verify_inventory(plan) if forward else observer.verify_original_inventory(plan))
        return True

    def adapter(resolve, resolve_step):
        return HelperTransactionAdapter(hass, resolve, resolve_step=resolve_step,
            settle=settle, validate_snapshot=validate, verify_activation=activation,
            verify_runtime=runtime, verify_inventory=lambda p: verify(p, True),
            verify_original_inventory=lambda p: verify(p, False), persistence=persistence)

    await runtime(before)
    return SimpleNamespace(change=change, adapter=adapter, runtime=runtime,
                           before=before, after=change.after, auxiliary=auxiliary)
