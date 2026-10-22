"""Synthetic native registry + Lovelace + Energy coordinator scenario.

Loaded by native_registry_probe into an isolated HA. It deliberately declares
only these fixture scopes, not arbitrary HA/configuration coverage.
"""
from copy import deepcopy
from types import SimpleNamespace


async def make_linked(hass, bridge, registry_change, history_check, helpers=False, helper_kind="threshold", component=None):
    from homeassistant.components.energy.data import EnergyManager
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import LovelaceStorage
    from homeassistant.helpers.storage import Store
    from .native_registry import RegistryTransactionAdapter, _content
    from .lovelace_native import NativeLovelace
    from .energy_native import NativeEnergy
    from .native_adapters import LovelaceTransactionAdapter, EnergyTransactionAdapter
    from .prepared import native_prepared_store, encode_change, decode_change
    from .journal import native_journal
    from .transaction import Coordinator, Plan, Step

    old, new = next(iter(registry_change.mapping.items()))
    helper = None
    if helpers:
        from .native_helper_runtime_scenario import create_helper
        helper = await create_helper(hass, old, new, helper_kind, component)
        await bridge.io.flush('entity')
    original_registry = bridge.io.document('entity')
    default = LovelaceStorage(hass, None)
    metadata = {'id': 'unaffected', 'url_path': 'unchanged-board', 'mode': 'storage',
                'require_admin': True, 'title': 'Unchanged fixture'}
    unchanged = LovelaceStorage(hass, metadata)
    hass.data[LOVELACE_DATA] = SimpleNamespace(dashboards={None: default, metadata['url_path']: unchanged})
    await Store(hass, 1, 'lovelace_dashboards').async_save({'items': [metadata]})
    await default.async_save({'views': [{'cards': [{'type': 'entity', 'entity': old}]}]})
    await unchanged.async_save({'views': [{'cards': [{'type': 'entity', 'entity': 'sensor.unaffected'}]}]})
    lovelace = NativeLovelace(hass)
    descriptors, yaml = await lovelace.enumerate()
    assert not yaml
    snapshots = {item.key: await lovelace.read(item) for item in descriptors}
    selected = next(item for item in descriptors if item.route is None)
    snapshot, planned = await lovelace.plan(selected, {old: new})
    dashboard_change = lovelace.prepare(snapshot, planned)

    manager = EnergyManager(hass)
    await manager.async_initialize()
    await manager.async_update({'energy_sources': [{'type': 'solar', 'stat_energy_from': old}],
        'device_consumption': [{'stat_consumption': old}], 'device_consumption_water': []})
    await manager._store.async_save(manager.data)
    updates = []
    async def listener(): updates.append(deepcopy(manager.data))
    manager.async_listen_updates(listener)
    energy = NativeEnergy(hass, manager)
    snapshot, planned = await energy.plan({old: new})
    energy_change = energy.prepare(snapshot, planned)

    prepared = native_prepared_store(hass)
    await prepared.initialize()
    changes = {'registry': registry_change, 'dashboard': dashboard_change, 'energy': energy_change}
    if helper:
        changes['helper'] = helper.change
    digest = await prepared.create('linked', {key: encode_change(change) for key, change in changes.items()})
    holder = {'store': prepared}
    def resolve(key): return decode_change(holder['store'].resolve('linked', digest)[key])

    async def registry_verify(plan, forward):
        expected = 'after' if forward else 'before'
        if (await bridge.inspect(resolve('registry'))).position != expected: return False
        original_outside = [row for row in original_registry['entities'] if row['id'] != registry_change.identity]
        current_outside = [row for row in bridge.io.document('entity')['entities'] if row['id'] != registry_change.identity]
        return original_outside == current_outside and await history_check()

    async def dashboard_verify(plan, forward):
        current, yaml = await lovelace.enumerate()
        if current != descriptors or yaml: return False
        for descriptor in current:
            actual = await lovelace.read(descriptor)
            expected = dashboard_change.after if forward and descriptor == selected else snapshots[descriptor.key].config
            if actual.config != expected: return False
        return True

    async def energy_verify(plan, forward):
        actual = await energy.read()
        return actual.value == (energy_change.after if forward else energy_change.before.value)

    adapters = {
        'registry': RegistryTransactionAdapter(bridge, resolve,
            lambda p: registry_verify(p, True), lambda p: registry_verify(p, False)),
        'dashboard': LovelaceTransactionAdapter(lovelace, resolve,
            lambda p: dashboard_verify(p, True), lambda p: dashboard_verify(p, False)),
        'energy': EnergyTransactionAdapter(energy, resolve,
            lambda p: energy_verify(p, True), lambda p: energy_verify(p, False)),
    }
    steps = (
        Step('registry', 'registry', registry_change.identity, registry_change.before_revision,
             registry_change.after_revision, 'registry'),
        Step('dashboard', 'dashboard', dashboard_change.before.dashboard.identity,
             dashboard_change.before.revision, dashboard_change.after_revision, 'dashboard'),
        Step('energy', 'energy', 'energy', energy_change.before.revision, energy_change.after_revision, 'energy'),
    )
    scopes = ('fixture_registry', 'fixture_dashboards', 'fixture_energy')
    if helper:
        step = Step('helper', 'helpers', helper.change.identity, helper.change.before_revision,
                    helper.change.after_revision, 'helper',
                    ('registry',) if helper.change.plan().listener_paths else ())
        steps += (step,)
        catalog = {item.key: item for item in steps}
        adapters['helpers'] = helper.adapter(resolve, catalog.__getitem__)
        scopes += ('fixture_helpers',)
    plan = Plan('linked', scopes, scopes, steps, digest, tuple(adapters))
    journal = native_journal(hass)
    await journal.initialize()
    def guard():
        if not bridge.io.running(): raise RuntimeError('fixture_not_running')
    result = SimpleNamespace(coordinator=Coordinator(adapters, journal, guard, required_scopes=scopes),
                             plan=plan, updates=updates, helper=helper)
    async def reopen():
        fresh = native_prepared_store(hass)
        await fresh.initialize()
        holder['store'] = fresh
        fresh_journal = native_journal(hass)
        await fresh_journal.initialize()
        result.coordinator = Coordinator(adapters, fresh_journal, guard, required_scopes=scopes)
    result.reopen = reopen
    return result
