"""Owner-run native enabled automation regression; source only, not run evidence.

Only startup/reloads attach synthetic listeners. Never turn on/off an automation,
fire fixture events, change fixture sensor state, or invoke an action. Corruption
checks temporarily remove registry entries or replace closure cells, restoring
exact objects in finally blocks; no unsubscribe or listener callback is invoked.
"""
import asyncio

import native_yaml_runtime_observer_scenario as base

DOCUMENT = base.DOCUMENT.replace('initial_state: false', 'initial_state: true').replace(
    "scene:\n", """  - id: readback_state_fixture
    alias: Readback state fixture
    initial_state: true
    triggers:
      - trigger: state
        entity_id: sensor.old
        from: 'off'
        to: 'on'
    actions:
      - action: renamer_probe.capture
        data:
          entity_id: sensor.old
scene:
""")


async def registration_checks(hass, observer, change, tx):
    from homeassistant.core import HassJob
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    from homeassistant.helpers import event as event_helper
    entities = list(hass.data[DATA_INSTANCES]['automation'].entities)
    event_entity = next(entity for entity in entities if entity.unique_id == 'readback_fixture')
    state_entity = next(entity for entity in entities if entity.unique_id == 'readback_state_fixture')
    assert event_entity._is_enabled is True and state_entity._is_enabled is True
    expected = await observer.position(change)
    assert expected == tx.Position.AFTER
    event_types = event_entity._trigger_config[0]['event_type']
    key = event_types[0].template
    entries = hass.bus._listeners[key]
    saved = list(entries)
    # Detach callback still exists but the actual native event registration is gone.
    entries.clear()
    try:
        assert event_entity._async_detach_triggers is not None
        assert await observer.position(change) == tx.Position.UNKNOWN
    finally:
        entries[:] = saved
    assert await observer.position(change) == expected
    job, _ = entries[0]
    listener = job.target
    cells = dict(zip(listener.__code__.co_freevars, listener.__closure__))
    action_job = cells['job'].cell_contents
    cells['job'].cell_contents = HassJob(state_entity._async_trigger_if_enabled)
    try:
        assert await observer.position(change) == tx.Position.UNKNOWN
    finally:
        cells['job'].cell_contents = action_job
    # Native state dispatch must exist on the bus as well as the keyed registry.
    tracker = event_helper._KEYED_TRACK_STATE_CHANGE
    data = hass.data[tracker.key]
    state_key = state_entity._trigger_config[0]['entity_id'][0]
    members = data.callbacks[state_key]
    saved_members = list(members)
    members.clear()
    try:
        assert state_entity._async_detach_triggers is not None
        assert await observer.position(change) == tx.Position.UNKNOWN
    finally:
        members[:] = saved_members
    bus_entries = hass.bus._listeners[tracker.event_type]
    saved_bus = list(bus_entries)
    bus_entries.clear()
    try:
        assert await observer.position(change) == tx.Position.UNKNOWN
    finally:
        bus_entries[:] = saved_bus
    # A registration replacement between samples must not retain proof.
    original_sample = observer._sample
    samples = 0
    def changed_registration(candidate):
        nonlocal samples
        result = original_sample(candidate)
        samples += 1
        if samples == 1:
            entries.clear()
        return result
    observer._sample = changed_registration
    try:
        assert await observer.position(change) == tx.Position.UNKNOWN
    finally:
        entries[:] = saved
        observer._sample = original_sample
    # Static listener metadata must retain the trigger's native index/config.
    trigger_data = cells['trigger_data'].cell_contents
    cells['trigger_data'].cell_contents = {**trigger_data, 'idx': 'stale'}
    try:
        assert await observer.position(change) == tx.Position.UNKNOWN
    finally:
        cells['trigger_data'].cell_contents = trigger_data
    assert await observer.position(change) == expected
    assert event_entity._is_enabled is True and state_entity._is_enabled is True


if __name__ == '__main__':
    asyncio.run(base.run(DOCUMENT, registration_checks))
