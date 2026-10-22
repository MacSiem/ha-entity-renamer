"""Regression scenario for both owned native HA containers; owner runs via FIFO.

No live HA setup is necessary: native validation runs in its own Python process.
Set RENAMER_COMPONENT_DIR to the frozen candidate component directory, and
RENAMER_TEST_SCRATCH to a private SSD-mounted directory outside the config tree.
"""
import asyncio
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace


def load_modules():
    component = Path(os.environ['RENAMER_COMPONENT_DIR'])
    package = ModuleType('renamer_validation_regression')
    package.__path__ = [str(component)]
    sys.modules[package.__name__] = package
    modules = []
    for name in ('yaml_plan', 'yaml_validation', 'yaml_validation_worker'):
        spec = importlib.util.spec_from_file_location(package.__name__ + '.' + name, component / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


async def run_activation(scratch, planner, validation):
    from homeassistant import loader, auth
    from homeassistant.core import HomeAssistant, Context
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.setup import async_setup_component
    from homeassistant.bootstrap import async_load_base_functionality
    import socket
    with tempfile.TemporaryDirectory(prefix='native-yaml-activation-', dir=scratch) as directory:
        root = Path(directory)
        (root / '.storage').mkdir()
        (root / 'configuration.yaml').write_text('script: !include scripts.yaml\n')
        script_path = root / 'scripts.yaml'
        original = ('fixture:\n  sequence:\n    - action: renamer_probe.capture\n'
                    '      data:\n        value: "{{ states(\'sensor.old\') }}"\n')
        script_path.write_text(original)
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
            from homeassistant.config import async_hass_config_yaml
            assert await async_setup_component(hass, 'script', await async_hass_config_yaml(hass))
            await hass.async_start()
            package = sys.modules[validation.__package__]
            import importlib
            leases_module = importlib.import_module(package.__name__ + '.yaml_editor_locks')
            mutations = importlib.import_module(package.__name__ + '.file_mutations')
            leases = leases_module.NativeYAMLEditorLocks(hass)
            hass.states.async_set('sensor.old', '41')
            async def invoke(expected):
                captured.clear()
                await hass.services.async_call('script', 'fixture', {}, blocking=True, context=Context())
                assert captured == [expected], captured
            await invoke('41')
            for mapping, source, target, value in [
                ({'sensor.old': 'sensor.new'}, 'sensor.old', 'sensor.new', '42'),
                ({'sensor.new': 'sensor.old'}, 'sensor.new', 'sensor.old', '43'),
            ]:
                plan = planner.plan_yaml(root, mapping)
                assert plan.ready and len(plan.patches) == 1, plan.issues
                async with leases.hold(lambda: None):
                    try:
                        receipt = await validation.async_validate_proposed(hass, plan, scratch)
                    except validation.YAMLValidationError:
                        print({'fixture_source_drift': [(item.path,
                            hashlib.sha256((root / item.path).read_bytes()).hexdigest() == item.sha256,
                            (root / item.path).stat().st_ino == item.inode)
                            for item in plan.files]}, flush=True)
                        raise
                    validation.assert_proposal_matches(receipt, plan)
                    validation.assert_activation_available(hass, receipt)
                    await hass.async_add_executor_job(validation.assert_validation_current, receipt)
                    snapshots = {item.path: item for item in plan.files}
                    patch = plan.patches[0]
                    written = await hass.async_add_executor_job(mutations.apply_file_patch,
                        root, patch, snapshots[patch.path], 'native_' + value)
                    assert written['sha256'] == hashlib.sha256(patch.proposed_bytes).hexdigest()
                    hass.states.async_remove(source)
                    hass.states.async_set(target, value)
                    activated = await validation.async_activate_yaml(hass, receipt,
                        context=Context(), guard=lambda: None)
                    assert activated['status'] == 'reloads_completed_readback_required'
                await invoke(value)
            assert script_path.read_text() == original
            print('PASS: native script reload, editor locks, guarded file write, live values and reverse migration')
        finally:
            await hass.async_stop(force=True)


async def run():
    planner, validation, worker = load_modules()
    scratch = Path(os.environ['RENAMER_TEST_SCRATCH']).resolve()
    with tempfile.TemporaryDirectory(prefix='native-yaml-regression-', dir=scratch) as directory:
        root = Path(directory)
        (root / 'packages').mkdir()
        (root / 'configuration.yaml').write_text('homeassistant:\n  packages: !include_dir_named packages\nscript: !include scripts.yaml\n')
        (root / 'scripts.yaml').write_text('direct:\n  sequence:\n    - action: homeassistant.update_entity\n      target:\n        entity_id: sensor.old\n')
        (root / 'packages' / 'nested.yaml').write_text('script:\n  packaged:\n    sequence:\n      - action: homeassistant.update_entity\n        target:\n          entity_id: sensor.old\n  secret_ref:\n    sequence:\n      - action: persistent_notification.create\n        data:\n          message: !secret source_message\n')
        (root / 'secrets.yaml').write_text('source_message: sensor.secret_reference\n')
        async def executor(function, *args):
            return await asyncio.to_thread(function, *args)
        hass = SimpleNamespace(config=SimpleNamespace(config_dir=str(root)), async_add_executor_job=executor)
        original_dir = hass.config.config_dir
        plan = planner.plan_yaml(root, {'sensor.old': 'sensor.new'})
        assert plan.ready and len(plan.patches) == 2, plan.issues
        receipt = await validation.async_validate_proposed(hass, plan, scratch)
        assert receipt.changed_domains == ('script',), receipt
        assert receipt.ready_for_apply
        assert hass.config.config_dir == original_dir
        assert b'sensor.old' in (root / 'scripts.yaml').read_bytes()
        assert any(path == 'secrets.yaml' for path, *_ in receipt.source_files)
        validation.assert_proposal_matches(receipt, plan)
        validation.assert_validation_current(receipt)
        # Changing a secret invalidates the same receipt even though planner skips !secret.
        (root / 'secrets.yaml').write_text('source_message: sensor.changed_secret\n')
        try:
            validation.assert_validation_current(receipt)
        except validation.YAMLValidationError as error:
            assert str(error) == 'validation_source_changed'
        else:
            raise AssertionError('stale secret accepted')
        # Missing secrets are native parse failures, without leaked expanded values.
        (root / 'secrets.yaml').write_text('{}\n')
        try:
            await validation.async_validate_proposed(hass, plan, scratch)
        except validation.YAMLValidationError as error:
            assert str(error) == 'native_config_rejected'
        else:
            raise AssertionError('missing secret accepted')
        (root / 'secrets.yaml').write_text('source_message: sensor.secret_reference\n')
        # An unrelated invalid domain blocks a valid script proposal (native warnings).
        with (root / 'configuration.yaml').open('a') as stream:
            stream.write('input_boolean:\n  invalid:\n    unexpected_schema_key: true\n')
        plan = planner.plan_yaml(root, {'sensor.old': 'sensor.new'})
        try:
            await validation.async_validate_proposed(hass, plan, scratch)
        except validation.YAMLValidationError as error:
            assert str(error) == 'native_config_rejected'
        else:
            raise AssertionError('unrelated schema warning accepted')
        assert worker.changed_domains({'template': [1]}, {'template': [2]}) == (['template'], ['template'])
        assert worker.changed_domains({'homeassistant': {'customize': {'sensor.old': {}}}},
                                     {'homeassistant': {'customize': {'sensor.new': {}}}}) == (['homeassistant'], [])
        assert worker.changed_domains({'homeassistant': {'auth_providers': []}},
                                     {'homeassistant': {'auth_providers': [{'type': 'trusted_networks'}]}}) == (['homeassistant'], ['homeassistant'])
        assert worker.changed_domains({'scene': [{'platform': 'other', 'entity_id': 'sensor.old'}]},
                                     {'scene': [{'platform': 'other', 'entity_id': 'sensor.new'}]}) == (['scene'], ['scene'])
    await run_activation(scratch, planner, validation)
    assert not list(scratch.glob('renamer-validation-*')), 'private staging left behind'
    print('PASS: native whole proposal, package/include/secret, warning rejection, source drift, coverage')


if __name__ == '__main__':
    asyncio.run(run())
