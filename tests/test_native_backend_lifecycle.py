"""Actual config-flow setup + native backend reload before same-ORIGINAL reverse.

Owner runs EACH case in its own native_unit_probe invocation through FIFO. Core
and Python cache native custom_components modules process-wide; this test never
clears/rewrites those caches. This is a running-HA component reload, not a cold HA
process restart or complete HACS/API/UI qualification.
"""
import gc
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys
import unittest
import weakref

import test_native_helper_quiescence as quiescence
import test_native_template_configentry_activation as activation
import test_receipted_helper_native as receipted


_PACKAGE = 'custom_components.ha_entity_renamer'


def installed_module(name):
    """Normal Python import of the native loader's actual component namespace."""
    return importlib.import_module(_PACKAGE + '.' + name)


@unittest.skipUnless(activation.fixtures.HAS_HA, 'actual pinned HA required')
class NativeBackendLifecycleTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only fixture/scenario methods, never inherit the existing 24 tests.
    asyncSetUp = quiescence.NativeHelperQuiescenceTests.asyncSetUp
    _flush_documents = activation.NativeTemplateConfigEntryActivationTests._flush_documents
    _scope = activation.NativeTemplateConfigEntryActivationTests._scope
    _full_flow = activation.NativeTemplateConfigEntryActivationTests._full_flow
    _same_native_documents = activation.NativeTemplateConfigEntryActivationTests._same_native_documents
    emit = activation.NativeTemplateConfigEntryActivationTests.emit
    with_yaml = True  # Existing Recorder + real auth/HTTP on an ephemeral port.

    async def _before_loader(self, hass, root):
        self.assertEqual(Path(hass.config.config_dir).resolve(), root)
        self.assertNotIn('custom_components', sys.modules,
            'Run one backend lifecycle case per fresh native_unit_probe process; no cache editing')
        source = activation.fixtures.COMPONENT.resolve(strict=True)
        package_root = root / 'custom_components'
        component = package_root / 'ha_entity_renamer'
        component.mkdir(parents=True, mode=0o700)
        # An ordinary package marker makes native loader's import stay anchored
        # to THIS config_dir after its temporary sys.path mount is removed.
        (package_root / '__init__.py').write_bytes(b'')
        self.public_source_sha256 = {}
        for path in sorted(source.rglob('*')):
            self.assertFalse(path.is_symlink(), 'public candidate must not contain symlinks')
            if not path.is_file() or path.suffix not in ('.py', '.json'):
                continue
            relative = path.relative_to(source)
            target = component / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            self.public_source_sha256[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),
                             self.public_source_sha256[relative.as_posix()])
        self.assertIn('__init__.py', self.public_source_sha256)
        self.assertIn('config_flow.py', self.public_source_sha256)
        self.assertIn('manifest.json', self.public_source_sha256)
        self.assertIn('translations/en.json', self.public_source_sha256)
        self.installed_component = component.resolve()

    async def _before_start(self, hass, root):
        # NativeHelperQuiescenceTests invokes this AFTER loader discovery. The
        # public source install belongs exclusively in _before_loader above.
        await receipted.NativeReceiptedHelperTests._before_start(self, hass, root)
        from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
        from homeassistant.data_entry_flow import FlowResultType
        result = await hass.config_entries.flow.async_init('ha_entity_renamer',
                                                          context={'source': SOURCE_USER})
        self.assertEqual(result['type'], FlowResultType.CREATE_ENTRY, result)
        entry = result['result']
        self.assertIs(hass.config_entries.async_get_entry(entry.entry_id), entry)
        self.assertEqual(entry.source, SOURCE_USER)
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        self.backend_entry_id = entry.entry_id  # Retain only ID, never Runtime.
        backend = self._loaded_backend()
        self.assertIs(type(entry.runtime_data), backend.Runtime)
        self.assertIs(hass.data.get(backend.DATA_RUNTIME), entry.runtime_data)
        self.assertEqual(entry.runtime_data.entry_id, entry.entry_id)
        self.assertIn('http', hass.config.components)
        self.assertIn('websocket_api', hass.config.components)
        self.assertIn('ha_entity_renamer', hass.config.components)

    def _loaded_backend(self):
        backend = importlib.import_module(_PACKAGE)
        self.assertEqual(Path(backend.__file__).resolve(), self.installed_component / '__init__.py')
        self.assertEqual(backend.Runtime.__module__, _PACKAGE)
        self.assertEqual(Path(installed_module('config_flow').__file__).resolve(),
                         self.installed_component / 'config_flow.py')
        for name in ('__init__.py', 'config_flow.py', 'manifest.json'):
            self.assertEqual(hashlib.sha256((self.installed_component / name).read_bytes()).hexdigest(),
                             self.public_source_sha256[name])
        return backend

    def _assert_backend_original_row(self, baseline):
        """Exact whole foreign row from ORIGINAL; not merely helper projection."""
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        document = json.loads(next(row.manager_json for row in baseline.files
                                   if row.key == 'core.config_entries'))
        rows = [row for row in document['entries'] if row['entry_id'] == self.backend_entry_id]
        self.assertEqual(len(rows), 1)
        entry = self.f.hass.config_entries.async_get_entry(self.backend_entry_id)
        self.assertIsNotNone(entry)
        self.assertEqual(json.loads(_orjson_bytes_default_encoder(entry.as_dict())), rows[0])

    async def _reload_backend_for_reverse(self, operation, fingerprint, baseline_digest,
                                          original_receipt_json):
        """Only public native async_reload; all old backend references are weak."""
        from homeassistant.config_entries import ConfigEntryState
        hass = self.f.hass
        backend = self._loaded_backend()
        entry = hass.config_entries.async_get_entry(self.backend_entry_id)
        self.assertIsNotNone(entry)
        old_runtime = entry.runtime_data
        self.assertIs(type(old_runtime), backend.Runtime)
        self.assertIs(hass.data.get(backend.DATA_RUNTIME), old_runtime)
        self.assertFalse(old_runtime.operation_lock.locked())
        old_refs = tuple(weakref.ref(value) for value in (old_runtime, old_runtime.journal,
                            old_runtime.preparations, old_runtime.operation_lock))
        del old_runtime
        self.assertTrue(await hass.config_entries.async_reload(self.backend_entry_id))
        await hass.async_block_till_done()
        gc.collect()
        self.assertTrue(all(ref() is None for ref in old_refs),
                        'native reload retained an old backend Runtime/journal/preparations/lock')
        self.assertIs(hass.config_entries.async_get_entry(self.backend_entry_id), entry)
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        runtime = entry.runtime_data
        self.assertIs(type(runtime), backend.Runtime)
        self.assertIs(hass.data.get(backend.DATA_RUNTIME), runtime)
        self.assertTrue(runtime.loaded)
        self.assertFalse(runtime.unloading)
        self.assertFalse(runtime.operation_lock.locked())
        self._loaded_backend()
        # These are the stores opened by actual backend setup, not hand-built
        # test objects. Decode from the same immutable ORIGINAL, without import.
        record = await runtime.journal.load(operation)
        self.assertIsNotNone(record)
        self.assertEqual((record.status, record.direction), ('applied', 'forward'))
        self.assertEqual(record.plan.fingerprint, fingerprint)
        baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self.assertEqual(baseline.digest, baseline_digest)
        self.assertEqual(baseline.receipt_json, original_receipt_json)
        self.assertEqual(type(composition.evidence).__module__, _PACKAGE + '.yaml_source_binding')
        self._assert_backend_original_row(baseline)
        return installed_module

    async def _run_backend_lifecycle(self, entry_unique_id):
        detached = await self._scope(entry_unique_id=entry_unique_id, full_flow=True,
                                     fresh_reverse=True)
        values, old_refs = detached
        gc.collect()
        self.assertTrue(all(ref() is None for ref in old_refs),
                        'forward proof objects survived their completed caller frames')
        from native_template_composition_scenario import fresh_reverse_scenario
        await fresh_reverse_scenario(self, *values, native_backend_lifecycle=True)

    async def test_native_backend_named_reload_before_same_original_reverse(self):
        await self._run_backend_lifecycle('template-entry-native-backend')

    async def test_native_backend_gui_none_reload_before_same_original_reverse(self):
        await self._run_backend_lifecycle(None)


def load_tests(loader, tests, pattern):
    # Imported scenario modules are helpers, not inherited suites to rerun.
    return loader.loadTestsFromTestCase(NativeBackendLifecycleTests)


if __name__ == '__main__':
    unittest.main()
