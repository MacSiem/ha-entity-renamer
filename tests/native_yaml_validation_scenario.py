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
    assert not list(scratch.glob('renamer-validation-*')), 'private staging left behind'
    print('PASS: native whole proposal, package/include/secret, warning rejection, source drift, coverage')


if __name__ == '__main__':
    asyncio.run(run())
