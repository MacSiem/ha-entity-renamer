"""Owner-run isolated native readback regression (never executes fixture actions).

Run only through the owner's SSD/FIFO native HA harness on 2025.6.0/2026.10.2.
This scenario is a regression source, not evidence of a completed native run.
"""
import asyncio
from dataclasses import replace
import importlib
import os
from pathlib import Path
import socket
import tempfile

from native_yaml_validation_scenario import load_modules


DOCUMENT = '''homeassistant:
  customize:
    sensor.old:
      icon: mdi:test-tube
script:
  readback_fixture:
    alias: Readback fixture
    sequence:
      - action: renamer_probe.capture
        data:
          entity_id: sensor.old
automation:
  - id: readback_fixture
    alias: Readback fixture
    initial_state: false
    triggers:
      - trigger: event
        event_type: renamer_observer_event_never_fired
    actions:
      - action: renamer_probe.capture
        data:
          entity_id: sensor.old
scene:
  - id: readback_fixture
    name: Readback fixture
    entities:
      sensor.old: 'off'
'''


async def run(document=DOCUMENT, registration_checks=None):
    planner, validation, _ = load_modules()
    package = validation.__package__
    yaml_tx = importlib.import_module(package + '.yaml_transaction')
    tx = importlib.import_module(package + '.transaction')
    observer_module = importlib.import_module(package + '.yaml_runtime_observer')
    from homeassistant import loader, auth, core_config
    from homeassistant.core import HomeAssistant, Context
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.setup import async_setup_component
    from homeassistant.bootstrap import async_load_base_functionality
    from homeassistant.config import async_hass_config_yaml
    from homeassistant.helpers.entity_component import DATA_INSTANCES
    scratch = Path(os.environ['RENAMER_TEST_SCRATCH'])
    with tempfile.TemporaryDirectory(prefix='native-yaml-observer-', dir=scratch) as folder:
        root = Path(folder)
        (root / '.storage').mkdir()
        path = root / 'configuration.yaml'
        path.write_text(document)
        hass = HomeAssistant(str(root))
        calls = []
        async def capture(call):
            calls.append(call)
            raise AssertionError('observer_must_never_execute_actions')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        http_config = {'http': {'server_host': '127.0.0.1', 'server_port': port}}
        try:
            loader.async_setup(hass)
            hass.config_entries = ConfigEntries(hass, http_config)
            assert (await async_load_base_functionality(hass)) is not False
            hass.auth = await auth.auth_manager_from_config(hass, [], [])
            assert await async_setup_component(hass, 'http', http_config)
            from homeassistant.components.config import automation, script, scene
            for editor in (automation, script, scene):
                assert editor.async_setup(hass)
            hass.services.async_register('renamer_probe', 'capture', capture)
            config = await async_hass_config_yaml(hass)
            await core_config.async_process_ha_core_config(hass, config['homeassistant'])
            for domain in ('script', 'automation', 'scene'):
                assert await async_setup_component(hass, domain, config)
            await hass.async_start()
            await hass.async_block_till_done()
            observer = observer_module.NativeYAMLRuntimeReadback(hass)
            plan = planner.plan_yaml(root, {'sensor.old': 'sensor.new'})
            assert plan.ready, plan.issues
            change = await yaml_tx.prepare_yaml(hass, plan, scratch, guard=lambda: None)
            assert set(change.receipt.changed_domains) == {'homeassistant', 'script', 'automation', 'scene'}
            # Diagnostics contain only this synthetic fixture's domain/error
            # codes and projection hashes, never expanded configuration.
            import sys
            worker = importlib.import_module(package + '.yaml_validation_worker')
            def trace(frame, event, arg):
                if (event == 'exception' and not isinstance(arg[1], (StopIteration, StopAsyncIteration))
                        and frame.f_code.co_name in ('_expected_runtime', 'async_process_component_config',
                                                     'async_validate_config', '_async_validate_config_item')):
                    print({'expected_runtime_error': type(arg[1]).__name__,
                           'domain': frame.f_locals.get('domain'), 'error': str(arg[1])}, flush=True)
                return trace
            sys.settrace(trace)
            try:
                await worker._expected_runtime(str(root), change.receipt.changed_domains)
            finally:
                sys.settrace(None)
            for domain, before, after in change.receipt.runtime_expected:
                try:
                    projection, _ = observer._projection(domain)
                    actual = worker._fingerprint(projection)
                    print({'runtime_projection': domain, 'before_matches': actual == before,
                           'after_matches': actual == after}, flush=True)
                except Exception as error:
                    print({'runtime_projection': domain, 'error': type(error).__name__,
                           'reason': str(error)}, flush=True)
            assert all(old and new and old != new
                       for _, old, new in change.receipt.runtime_expected), change.receipt.runtime_expected
            assert await observer.position(change) == tx.Position.BEFORE
            # Old durable receipts decode but have no independent native proof.
            no_proof = replace(change, receipt=replace(change.receipt, runtime_expected=()))
            assert await observer.position(no_proof) == tx.Position.UNKNOWN
            assert await observer.verify_inventory(None, rollback=False) is False
            assert await observer.verify_inventory(None, rollback=True) is False
            assert yaml_tx.decode_yaml(__import__('dataclasses').asdict(change)) == change
            # Source bytes alone cannot advance the runtime position.
            path.write_bytes(plan.patches[0].proposed_bytes)
            assert await observer.position(change) == tx.Position.BEFORE
            # Reload one native consumer only: mixed graph is UNKNOWN.
            await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            assert await observer.position(change) == tx.Position.UNKNOWN
            for domain, service in (('automation', 'reload'), ('scene', 'reload'),
                                    ('homeassistant', 'reload_core_config')):
                await hass.services.async_call(domain, service, {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            assert await observer.position(change) == tx.Position.AFTER
            if registration_checks is not None:
                await registration_checks(hass, observer, change, tx)
            # A real pending native platform task must prevent settled proof.
            component = hass.data[DATA_INSTANCES]['script']
            platform = next(iter(component._platforms.values()))
            gate = asyncio.Event()
            pending = asyncio.create_task(gate.wait())
            platform._tasks.append(pending)
            try:
                assert await observer.position(change) == tx.Position.UNKNOWN
            finally:
                platform._tasks.remove(pending)
                gate.set()
                await pending
            # A native reload suspended in its real handler is not quiescent.
            entered, release = asyncio.Event(), asyncio.Event()
            original_prepare = component.async_prepare_reload
            async def delayed_prepare(*args, **kwargs):
                entered.set()
                await release.wait()
                return await original_prepare(*args, **kwargs)
            component.async_prepare_reload = delayed_prepare
            reload_task = asyncio.create_task(hass.services.async_call(
                'script', 'reload', {}, blocking=True, context=Context()))
            try:
                await entered.wait()
                assert await observer.position(change) == tx.Position.UNKNOWN
            finally:
                release.set()
                await reload_task
                component.async_prepare_reload = original_prepare
            await hass.async_block_till_done()
            assert await observer.position(change) == tx.Position.AFTER
            # Stale native runtime cannot pass just because raw_config agrees.
            script_entity = next(iter(component.entities))
            sequence = script_entity.script.sequence
            script_entity.script.sequence = []
            try:
                assert await observer.position(change) == tx.Position.UNKNOWN
            finally:
                script_entity.script.sequence = sequence
            assert await observer.position(change) == tx.Position.AFTER
            path.write_text(document)
            for domain, service in (('script', 'reload'), ('automation', 'reload'),
                                    ('scene', 'reload'), ('homeassistant', 'reload_core_config')):
                await hass.services.async_call(domain, service, {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            assert await observer.position(change) == tx.Position.BEFORE
            assert calls == [], 'fixture actions were executed'
            print('PASS: native read-only YAML projections; source-only/mixed/registrations/unknown/pending/tampered/rollback; no fixture action calls')
        finally:
            await hass.async_stop(force=True)


if __name__ == '__main__':
    asyncio.run(run())
