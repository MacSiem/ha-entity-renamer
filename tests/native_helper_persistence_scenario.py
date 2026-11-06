"""Real ConfigEntries Store boundary in the probe's isolated temporary HA."""
from copy import deepcopy
from types import MappingProxyType


async def exercise(hass):
    from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryDisabler
    from .helper_native import read_entry
    from .helper_persistence import NativeHelperPersistence
    from .persistence import PersistenceError

    hass.config_entries = ConfigEntries(hass, {})
    manager = hass.config_entries
    # Fixture seeding only: no integration activation is claimed by this probe.
    def entry(unique):
        return ConfigEntry(domain='group', data={}, options={'entities': ['sensor.old']},
            discovery_keys=MappingProxyType({}), title='Fixture ' + unique, unique_id=unique,
            source='user', version=1, minor_version=1, subentries_data=[],
            disabled_by=ConfigEntryDisabler.USER)
    selected, unaffected = entry('selected'), entry('unaffected')
    for item in (selected, unaffected):
        manager._entries[item.entry_id] = item
    untouched = deepcopy(unaffected.as_dict())
    native = NativeHelperPersistence(hass)
    original = read_entry(hass, selected.entry_id)
    await native.flush(original, lambda: None)
    before = await native.read()
    manager.async_update_entry(selected, options={'entities': ['sensor.new']})
    try:
        await native.read()
    except PersistenceError as err:
        assert str(err) == 'config_entries_write_pending'
    else:
        raise AssertionError('accepted delayed manager save as durable')
    expected = read_entry(hass, selected.entry_id)
    written = await native.flush(expected, lambda: None)
    assert written != before and unaffected.as_dict() == untouched
    fresh = ConfigEntries(hass, {})
    assert await fresh._store.async_load() == written
    manager.async_update_entry(selected, options=original['options'])
    await native.flush(original, lambda: None)
    assert read_entry(hass, selected.entry_id) == original
    assert unaffected.as_dict() == untouched
    # A swallowed native failure must never be reported as a successful flush.
    async def silent_failure(*args): pass
    manager._store._async_write_data = silent_failure
    manager.async_update_entry(selected, options={'entities': ['sensor.new']})
    try:
        await native.flush(read_entry(hass, selected.entry_id), lambda: None)
    except PersistenceError:
        assert native.failed
    else:
        raise AssertionError('accepted native silent write failure')
    print({'native_helper_persistence': 'PASS', 'delayed_save_detection': 'PASS',
           'fresh_config_entries_store': 'PASS', 'unaffected_entry': 'PASS',
           'helper_activation': 'NOT_TESTED'})
