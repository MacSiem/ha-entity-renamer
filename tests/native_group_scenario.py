"""Two active binary groups through the real helper/registry coordinator."""
import logging
from datetime import timedelta
from types import MappingProxyType


async def run_groups(hass, bridge):
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.config_entries import ConfigEntry, ConfigEntryState
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.entity_component import EntityComponent
    from .helper_native import read_entry
    from .helper_persistence import NativeHelperPersistence
    from .helper_transaction import prepare_helper, HelperTransactionAdapter
    from .helper_observers import NativeHelperObservers, RegistryMappingProof
    from .native_registry import RegistryTransactionAdapter
    from .prepared import native_prepared_store, encode_change, decode_change
    from .journal import native_journal
    from .transaction import Coordinator, Plan, Step

    class Source(BinarySensorEntity):
        _attr_should_poll = False
        def __init__(self, name, value):
            self._attr_name = name
            self._attr_unique_id = name
            self._attr_is_on = value
    component = EntityComponent(logging.getLogger('group-probe'), 'binary_sensor', hass, timedelta(seconds=60))
    first, second = Source('Renamer group first', True), Source('Renamer group second', False)
    await component.async_add_entities([first, second])
    registry = er.async_get(hass)
    old, new = first.entity_id, 'binary_sensor.renamer_group_renamed'
    identity = registry.async_get(old).id
    entries = []
    for mode in (False, True):
        entry = ConfigEntry(domain='group', title='Renamer binary group ' + str(mode),
            data={}, options={'name': 'Renamer binary group ' + str(mode),
                              'group_type': 'binary_sensor', 'entities': [old, second.entity_id],
                              'all': mode, 'hide_members': False},
            discovery_keys=MappingProxyType({}), source='user', version=1, minor_version=1,
            subentries_data=[], unique_id='renamer-group-' + str(mode))
        await hass.config_entries.async_add(entry)
        entries.append(entry)
    await hass.async_block_till_done()
    assert all(e.state is ConfigEntryState.LOADED for e in entries)
    persistence = NativeHelperPersistence(hass)
    snapshots = [read_entry(hass, e.entry_id) for e in entries]
    for snapshot in snapshots:
        await persistence.flush(snapshot, lambda: None)
    observer = await NativeHelperObservers.capture(hass, snapshots,
        (RegistryMappingProof(old, new, identity),))
    await bridge.io.flush('entity')
    original = bridge.io.document('entity')
    change = await bridge.prepare('entity', identity, {'entity_id': new}, lambda: None)
    changes = {'registry': change}
    changes.update({f'helper{i}': prepare_helper(snapshot, {old: new}) for i, snapshot in enumerate(snapshots)})
    prepared = native_prepared_store(hass)
    await prepared.initialize()
    digest = await prepared.create('groups', {key: encode_change(value) for key, value in changes.items()})
    def resolve(key): return decode_change(prepared.resolve('groups', digest)[key])
    steps = (Step('registry', 'registry', identity, change.before_revision, change.after_revision, 'registry'),)
    steps += tuple(Step(key, 'helpers', value.identity, value.before_revision, value.after_revision, key)
                   for key, value in changes.items() if key != 'registry')
    catalog = {step.key: step for step in steps}

    async def helper_inventory(plan, forward):
        await (observer.verify_inventory(plan) if forward else observer.verify_original_inventory(plan))
        for snapshot in (observer.after if forward else observer.before).values():
            assert await persistence.inspect_current(snapshot)
        return True

    async def registry_inventory(plan, forward):
        assert (await bridge.inspect(change)).position == ('after' if forward else 'before')
        assert [r for r in original['entities'] if r['id'] != identity] == [
            r for r in bridge.io.document('entity')['entities'] if r['id'] != identity]
        return True

    adapters = {'registry': RegistryTransactionAdapter(bridge, resolve,
        lambda p: registry_inventory(p, True), lambda p: registry_inventory(p, False)),
        'helpers': HelperTransactionAdapter(hass, resolve, resolve_step=catalog.__getitem__,
            settle=observer.settle, validate_snapshot=observer.validate_snapshot,
            verify_activation=observer.verify_activation, verify_runtime=observer.verify_runtime,
            verify_inventory=lambda p: helper_inventory(p, True),
            verify_original_inventory=lambda p: helper_inventory(p, False), persistence=persistence)}
    scopes = ('fixture_group_registry', 'fixture_binary_groups')
    plan = Plan('groups', scopes, scopes, steps, digest, tuple(adapters))
    journal = native_journal(hass)
    await journal.initialize()
    coordinator = Coordinator(adapters, journal, lambda: None, required_scopes=scopes)
    result = await coordinator.apply(plan, plan.fingerprint, 'group_op')
    assert result.status == 'applied', result
    for value in (True, False):
        first._attr_is_on = second._attr_is_on = value
        first.async_write_ha_state(); second.async_write_ha_state()
        await observer.verify_inventory(plan)
    result = await coordinator.rollback('group_op')
    assert result.status == 'rolled_back', result
    for value in (True, False):
        first._attr_is_on = second._attr_is_on = value
        first.async_write_ha_state(); second.async_write_ha_state()
        await observer.verify_original_inventory(plan)
    assert first.entity_id == old and registry.async_get(old).id == identity
    print({'binary_groups_all_any': 'PASS', 'two_active_producers': 'PASS',
           'forward_reverse_events': 'PASS', 'full_fixture_identity': 'PASS'})
