"""Owner-run native adapter scenario in both frozen HA container candidates.

Uses a real script execution as its independent runtime observer. This is scoped
fixture evidence, not a production whole-inventory observer or recorder proof.
Environment and FIFO requirements match native_yaml_validation_scenario.py.
"""
import asyncio
import importlib
import os
from pathlib import Path
import socket
import tempfile

from native_yaml_validation_scenario import load_modules


async def run():
    planner, validation, _ = load_modules()
    package = validation.__package__
    yaml_tx = importlib.import_module(package + '.yaml_transaction')
    tx = importlib.import_module(package + '.transaction')
    codec = importlib.import_module(package + '.prepared')
    from homeassistant import loader, auth
    from homeassistant.core import HomeAssistant, Context
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.setup import async_setup_component
    from homeassistant.bootstrap import async_load_base_functionality
    from homeassistant.config import async_hass_config_yaml
    scratch = Path(os.environ['RENAMER_TEST_SCRATCH'])
    with tempfile.TemporaryDirectory(prefix='native-yaml-transaction-', dir=scratch) as folder:
        root = Path(folder)
        (root / '.storage').mkdir()
        (root / 'configuration.yaml').write_text('script: !include scripts.yaml\n')
        original = ('fixture:\n  sequence:\n    - action: renamer_probe.capture\n'
                    '      data:\n        value: "{{ states(\'sensor.old\') }}"\n')
        (root / 'scripts.yaml').write_text(original)
        hass = HomeAssistant(str(root))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        config = {'http': {'server_host': '127.0.0.1', 'server_port': port}}
        captured = []
        async def capture(call):
            captured.append(str(call.data['value']))
        try:
            loader.async_setup(hass)
            hass.config_entries = ConfigEntries(hass, config)
            assert (await async_load_base_functionality(hass)) is not False
            hass.auth = await auth.auth_manager_from_config(hass, [], [])
            assert await async_setup_component(hass, 'http', config)
            from homeassistant.components.config import automation, script, scene
            for editor in (automation, script, scene):
                assert editor.async_setup(hass)
            hass.services.async_register('renamer_probe', 'capture', capture)
            assert await async_setup_component(hass, 'script', await async_hass_config_yaml(hass))
            await hass.async_start()
            hass.states.async_set('sensor.old', '41')
            hass.states.async_set('sensor.new', '42')
            await hass.async_block_till_done()  # Never drain while holding editor locks.
            script_ids = set(hass.states.async_entity_ids('script'))
            class Observer:
                async def position(self, change):
                    captured.clear()
                    await hass.services.async_call('script', 'fixture', {}, blocking=True, context=Context())
                    if captured == ['41']:
                        return tx.Position.BEFORE
                    if captured == ['42']:
                        return tx.Position.AFTER
                    return tx.Position.UNKNOWN
                async def verify_inventory(self, plan, *, rollback):
                    expected = tx.Position.BEFORE if rollback else tx.Position.AFTER
                    return (await self.position(None) == expected
                        and set(hass.states.async_entity_ids('script')) == script_ids
                        and hass.states.get('sensor.old').state == '41'
                        and hass.states.get('sensor.new').state == '42')
            locks = importlib.import_module(package + '.yaml_editor_locks').NativeYAMLEditorLocks(hass)
            async with locks.hold(lambda: None):
                source_plan = planner.plan_yaml(root, {'sensor.old': 'sensor.new'})
                change = await yaml_tx.prepare_yaml(hass, source_plan, scratch, guard=lambda: None)
            store = codec.native_prepared_store(hass)
            await store.initialize()
            digest = await store.create('native_yaml_fixture', codec.encode_change(change))
            # Reopen durable immutable preparations; adapter never sees a request payload.
            reopened = codec.native_prepared_store(hass)
            await reopened.initialize()
            expected_ref = 'native_yaml_fixture:' + digest
            def resolver(ref):
                assert ref == expected_ref
                return codec.decode_change(reopened.resolve('native_yaml_fixture', digest))
            adapter = yaml_tx.YAMLTransactionAdapter(hass, resolver, locks, Observer(), context=Context())
            step = tx.Step('yaml_fixture', 'yaml', change.identity, change.before_revision,
                           change.after_revision, expected_ref)
            journal = importlib.import_module(package + '.journal').native_journal(hass)
            await journal.initialize()
            plan = tx.Plan('native_yaml_plan', ('yaml',), ('yaml',), (step,),
                           'native-script-fixture-scoped', ('yaml',))
            coordinator = tx.Coordinator({'yaml': adapter}, journal, lambda: None,
                                         required_scopes=('yaml',))
            assert await adapter.inspect(step) == tx.Position.BEFORE
            applied = await coordinator.apply(plan, plan.fingerprint, 'native_yaml_operation')
            assert applied.status == 'applied', applied
            assert await adapter.inspect(step) == tx.Position.AFTER
            fresh_journal = importlib.import_module(package + '.journal').native_journal(hass)
            await fresh_journal.initialize()
            fresh = tx.Coordinator({'yaml': adapter}, fresh_journal, lambda: None,
                                   required_scopes=('yaml',))
            duplicate = await fresh.apply(plan, plan.fingerprint, 'native_yaml_operation')
            assert duplicate == applied
            restored = await fresh.rollback('native_yaml_operation')
            assert restored.status == 'rolled_back', restored
            assert await adapter.inspect(step) == tx.Position.BEFORE
            assert (root / 'scripts.yaml').read_text() == original
            print('PASS: immutable/journal reopen without replay, native YAML saga source CAS, script execution readback, guarded rollback')
        finally:
            await hass.async_stop(force=True)


if __name__ == '__main__':
    asyncio.run(run())
