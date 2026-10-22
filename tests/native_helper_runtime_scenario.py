"""Active native Threshold helper in the isolated linked migration probe."""
from types import MappingProxyType, SimpleNamespace


async def create_helper(hass, old, new):
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState
    from homeassistant.helpers import entity_registry as er
    from homeassistant.components.threshold.config_flow import CONFIG_SCHEMA
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

    async def settle():
        await hass.async_block_till_done()

    async def validate(snapshot):
        assert snapshot['domain'] == 'threshold'
        assert snapshot['identity'] == before['identity']
        assert not snapshot['data'] and not snapshot['subentries']
        # Flow validation precedes _validate_mode adding absent limits as None.
        CONFIG_SCHEMA({k: v for k, v in snapshot['options'].items() if v is not None})
        assert snapshot['options']['lower'] is None and snapshot['options']['upper'] == 42.5

    async def activation(snapshot):
        assert entry.state is ConfigEntryState.LOADED
        row = registry.async_get(entity_id)
        assert row.id == identity and row.unique_id == entry.entry_id
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.attributes['entity_id'] == snapshot['options']['entity_id']

    async def runtime(snapshot):
        await activation(snapshot)
        state = hass.states.get(entity_id)
        source = hass.states.get(snapshot['options']['entity_id'])
        assert source is not None
        assert state.attributes['sensor_value'] == float(source.state)
        assert state.state == ('on' if float(source.state) > 42.5 else 'off')

    async def verify(plan, forward):
        expected = change.after if forward else change.before
        await settle()
        assert read_entry(hass, entry.entry_id) == expected
        assert await persistence.inspect_current(expected)
        await runtime(expected)
        return True

    def adapter(resolve, resolve_step):
        return HelperTransactionAdapter(hass, resolve, resolve_step=resolve_step,
            settle=settle, validate_snapshot=validate, verify_activation=activation,
            verify_runtime=runtime, verify_inventory=lambda p: verify(p, True),
            verify_original_inventory=lambda p: verify(p, False), persistence=persistence)

    await runtime(before)
    return SimpleNamespace(change=change, adapter=adapter, runtime=runtime,
                           before=before, after=change.after)
