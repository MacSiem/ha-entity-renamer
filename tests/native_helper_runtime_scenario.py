"""Active native Threshold helper in the isolated linked migration probe."""
from types import MappingProxyType, SimpleNamespace


async def create_helper(hass, old, new):
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState
    from homeassistant.helpers import entity_registry as er
    from .helper_observers import NativeHelperObservers, RegistryMappingProof
    from .helper_native import read_entry
    from .helper_persistence import NativeHelperPersistence
    from .helper_transaction import prepare_helper, HelperTransactionAdapter

    entry = ConfigEntry(domain='threshold', title='Renamer threshold fixture',
        data={}, options={'name': 'Renamer threshold fixture', 'entity_id': old,
                          'hysteresis': 0.0, 'lower': None, 'upper': 42.5},
        discovery_keys=MappingProxyType({}), source='user', version=1, minor_version=2,
        subentries_data=[], unique_id='renamer-threshold-fixture')
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED, entry.state
    registry = er.async_get(hass)
    rows = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(rows) == 1
    identity, entity_id = rows[0].id, rows[0].entity_id
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
                           before=before, after=change.after)
