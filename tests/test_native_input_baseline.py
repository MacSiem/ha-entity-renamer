"""Full native input baseline regressions; owner runs on frozen candidate via FIFO.

The same source works under native_unit_probe (default component path), and the
new native_input_baseline_probe (explicit RENAMER_COMPONENT_DIR). All fixtures
are synthetic private trees, never the running container's /config storage.
"""
import asyncio
import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType, MappingProxyType
import unittest
from unittest.mock import patch

COMPONENT = Path(os.environ.get('RENAMER_COMPONENT_DIR',
                 str(Path(__file__).resolve().parents[1] / 'custom_components/ha_entity_renamer')))
PACKAGE = 'renamer_input_baseline_regression'
if PACKAGE not in sys.modules:
    package = ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules[PACKAGE] = package
import importlib
baseline = importlib.import_module(PACKAGE + '.native_input_baseline')
validation = importlib.import_module(PACKAGE + '.yaml_validation')
KEYS = baseline._KEYS


def scratch():
    # Native runner sets TMPDIR and RENAMER_TEST_SCRATCH on the owned SSD mount.
    return tempfile.TemporaryDirectory(prefix='native-input-baseline-',
                                      dir=os.environ.get('RENAMER_TEST_SCRATCH'))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def receipt_for(root, version='2026.10.2'):
    root = Path(root).resolve()
    source_paths = [p for p in root.iterdir() if p.is_file()]
    if (root / '.storage').exists():
        source_paths += [p for p in (root / '.storage').iterdir() if p.is_file()]
    sources = tuple(sorted((str(p.relative_to(root)), digest(p.read_bytes()),
                            p.stat().st_dev, p.stat().st_ino) for p in source_paths))
    directories = []
    for relative in ('.', '.storage'):
        directory = root / relative
        if not directory.exists():
            continue
        names = tuple(sorted(set(KEYS) | {'core.area_registry'})) if relative == '.storage' else None
        entries = tuple(sorted((p.name, (s := p.lstat()).st_mode, s.st_dev, s.st_ino)
                               for p in directory.iterdir() if names is None or p.name in names))
        directories.append((relative, entries, names))
    return validation.ValidationReceipt(str(root), sources, tuple(directories),
                (('configuration.yaml', digest(b'sensor.new')),), ('script',), (), version,
                (('script', digest(b'before'), digest(b'after')),))


def envelope(key, data, version='2026.10.2'):
    major, minor = baseline._SCHEMAS[version][KEYS.index(key)]
    # Preserve deliberate formatting, top-level key order and raw audit values.
    return json.dumps({'data': data, 'key': key, 'minor_version': minor, 'version': major},
                      indent=2, ensure_ascii=False).encode('utf-8') + b'\n'


def synthetic_documents():
    return ({'entities': [
                {'id': 'one', 'entity_id': 'sensor.old', 'modified_at': '2026-01-01T01:02:03Z',
                 'unknown_native_field': {'nested': [1, False, None]}},
                {'id': 'two', 'entity_id': 'sensor.untouched', 'modified_at': '2026-02-02T02:03:04Z'}],
             'deleted_entities': [{'id': 'gone', 'modified_at': '2020-01-01T00:00:00Z'}],
             'settings': {'entity_id_parts': ['name']}},
            {'devices': [{'id': 'device', 'labels': ['second', 'first'], 'modified_at': 'original'}],
             'child_devices': [], 'deleted_devices': [{'id': 'deleted', 'orphaned_timestamp': 11.5}]},
            {'entries': [{'entry_id': 'entry', 'data': {'nested': ['first', 'second']},
                          'options': {'foreign_option': {'preserve': True}},
                          'subentries': [{'subentry_id': 'child', 'data': {'ref': 'sensor.old'}}],
                          'created_at': 'original-created', 'modified_at': 'original-modified'}]})


class BaselineFileTests(unittest.TestCase):
    version = '2026.10.2'

    def setUp(self):
        self.directory = scratch()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / '.storage').mkdir(mode=0o700)
        (self.root / 'configuration.yaml').write_bytes(b'script: {}\n')
        self.documents = synthetic_documents()
        for key, document in zip(KEYS, self.documents):
            p = self.root / '.storage' / key
            p.write_bytes(envelope(key, document, self.version))
            p.chmod(0o640)
        (self.root / '.storage/core.area_registry').write_bytes(b'{"areas": []}')
        self.receipt = receipt_for(self.root, self.version)

    def capture(self, receipt=None):
        pinned = baseline._Pinned(receipt or self.receipt, tuple(map(baseline._json, self.documents)))
        try:
            return pinned.capture()
        finally:
            pinned.close()

    def test_raw_audit_order_unknowns_mode_and_codec_are_immutable(self):
        captured = self.capture()
        self.assertEqual(captured.receipt, self.receipt)
        for key, item, document in zip(KEYS, captured.files, self.documents):
            self.assertEqual(item.raw, (self.root / '.storage' / key).read_bytes())
            self.assertEqual(item.manager_json, baseline._json(document))
            self.assertEqual(stat.S_IMODE(item.identity[2]), 0o640)
        self.assertIn(b'unknown_native_field', captured.files[0].raw)
        self.assertIn(b'2026-02-02T02:03:04Z', captured.files[0].raw)
        encoded = baseline.encode_baseline(captured)
        decoded = baseline.decode_baseline(encoded)
        self.assertEqual(decoded, captured)
        self.assertEqual(decoded.digest, captured.digest)
        self.assertEqual(decoded.files[0].raw, captured.files[0].raw)
        self.assertEqual(baseline.encode_baseline(decoded), encoded)
        with self.assertRaises(FrozenInstanceError):
            captured.files[0].raw = b'changed'
        with self.assertRaises(baseline.NativeInputBaselineError):
            baseline.assert_baseline_receipt(captured, replace(self.receipt, changed_domains=('automation',)))

    def test_untouched_row_audit_and_order_are_never_normalized(self):
        for mutation in ('audit', 'reorder', 'deleted', 'subentry', 'foreign_option'):
            with self.subTest(mutation=mutation):
                changed = deepcopy(self.documents)
                if mutation == 'audit':
                    changed[0]['entities'][1]['modified_at'] = 'foreign-audit'
                elif mutation == 'reorder':
                    changed[0]['entities'].reverse()
                elif mutation == 'deleted':
                    changed[0]['deleted_entities'].clear()
                elif mutation == 'subentry':
                    changed[2]['entries'][0]['subentries'][0]['data']['ref'] = 'sensor.foreign'
                else:
                    changed[2]['entries'][0]['options']['foreign_option']['preserve'] = False
                pinned = baseline._Pinned(self.receipt, tuple(map(baseline._json, changed)))
                with self.assertRaises(baseline.NativeInputBaselineError):
                    try:
                        pinned.capture()
                    finally:
                        pinned.close()

    def test_all_receipt_sources_fenced_before_and_after_read(self):
        pinned = baseline._Pinned(self.receipt, tuple(map(baseline._json, self.documents)))
        try:
            pinned.capture()
            (self.root / '.storage/core.area_registry').write_bytes(b'{"areas": ["foreign"]}')
            with self.assertRaises(validation.YAMLValidationError):
                pinned.verify()
        finally:
            pinned.close()
        with self.assertRaises(validation.YAMLValidationError):
            self.capture()

    def test_chmod_and_equal_bytes_replacement_inode_refuse(self):
        path = self.root / '.storage' / KEYS[0]
        path.chmod(0o600)
        with self.assertRaises(validation.YAMLValidationError):
            self.capture()
        path.chmod(0o640)
        replacement = self.root / 'new-inode'
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o640)
        os.replace(replacement, path)
        with self.assertRaises(validation.YAMLValidationError):
            self.capture()

    def test_descriptor_is_pinned_through_named_path_check_and_fsync(self):
        pinned = baseline._Pinned(self.receipt, tuple(map(baseline._json, self.documents)))
        try:
            with patch.object(baseline.os, 'fsync', wraps=os.fsync) as barrier:
                pinned.capture()
                self.assertEqual(barrier.call_count, 5)  # three files, storage, config
            path, fd, expected = pinned.handles[0]
            named = self.root / path
            original = named.read_bytes()
            replacement = self.root / '.storage/new-inode'
            replacement.write_bytes(original)
            replacement.chmod(0o640)
            os.replace(replacement, named)
            self.assertEqual(os.fstat(fd).st_ino, expected[1])
            self.assertNotEqual(named.stat().st_ino, expected[1])
            with self.assertRaises(baseline.NativeInputBaselineError):
                pinned._named()
            with self.assertRaises((baseline.NativeInputBaselineError, validation.YAMLValidationError)):
                pinned.verify()
        finally:
            descriptors = [fd for _, fd, _ in pinned.handles]
            pinned.close()
        for fd in descriptors:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_barrier_failure_refuses_and_closes_descriptors(self):
        pinned = baseline._Pinned(self.receipt, tuple(map(baseline._json, self.documents)))
        with patch.object(baseline.os, 'fsync', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                pinned.capture()
        self.assertEqual(pinned.handles, [])
        self.assertIsNone(pinned.reader)

    def test_full_envelope_schema_duplicate_keys_and_nonfinite_refuse(self):
        key = KEYS[0]
        original = (self.root / '.storage' / key).read_bytes()
        for mutation in ('extra', 'version', 'minor', 'bool', 'duplicate', 'nonfinite', 'schema'):
            with self.subTest(mutation=mutation):
                document = json.loads(original)
                if mutation == 'extra': document['unknown_envelope'] = True
                elif mutation == 'version': document['version'] += 1
                elif mutation == 'minor': document['minor_version'] += 1
                elif mutation == 'bool': document['version'] = True
                elif mutation == 'schema': document['data']['unknown_top_level'] = []
                raw = json.dumps(document).encode()
                if mutation == 'duplicate': raw = raw[:-1] + b',"version":1}'
                if mutation == 'nonfinite': raw = raw.replace(b'"version": 1', b'"version": 1e999')
                with self.assertRaises(baseline.NativeInputBaselineError):
                    baseline._envelope(raw, key, self.receipt.version)

    def test_absence_requires_complete_membership_fence_and_never_becomes_creation(self):
        for key in KEYS:
            path = self.root / '.storage' / key
            path.rename(path.with_name('quarantine-' + key))
        # Quarantine is outside the receipt's selected storage inputs.
        for p in (self.root / '.storage').glob('quarantine-*'):
            p.rename(self.root / p.name)
        empty = ({'entities': [], 'deleted_entities': [], 'settings': {'entity_id_parts': []}},
                 {'devices': [], 'child_devices': [], 'deleted_devices': []}, {'entries': []})
        self.documents = empty
        self.receipt = receipt_for(self.root, self.version)
        captured = self.capture()
        self.assertTrue(all(f.raw is None and f.identity is None for f in captured.files))
        self.assertEqual(baseline.decode_baseline(baseline.encode_baseline(captured)), captured)
        path = self.root / '.storage' / KEYS[2]
        path.write_bytes(envelope(KEYS[2], empty[2], self.version))
        with self.assertRaises(validation.YAMLValidationError):
            self.capture()  # absent original receipt still cannot accept creation
        invalid = replace(self.receipt, source_directories=tuple(
            (p, entries, ('core.area_registry',) if p == '.storage' else names)
            for p, entries, names in self.receipt.source_directories))
        with self.assertRaises(baseline.NativeInputBaselineError):
            baseline._native_receipt(invalid)

    def test_codec_rejects_tamper_unknown_duplicate_bool_format_and_limits(self):
        raw = baseline.encode_baseline(self.capture())
        data = json.loads(raw)
        mutations = []
        item = deepcopy(data); item['unexpected'] = True; mutations.append(baseline._json(item))
        item = deepcopy(data); item['format'] = True; mutations.append(baseline._json(item))
        item = deepcopy(data); item['files'][0]['raw'] = base64.b64encode(b'{}').decode(); mutations.append(baseline._json(item))
        item = deepcopy(data); item['files'][0]['identity'][2] = True; mutations.append(baseline._json(item))
        item = deepcopy(data); item['files'].reverse(); mutations.append(baseline._json(item))
        mutations.extend([raw[:-1] + b',"format":1}', b' ' + raw,
                          b'{"format":NaN}', b'[' * 100 + b']' * 100])
        for value in mutations:
            with self.subTest(length=len(value)), self.assertRaises(baseline.NativeInputBaselineError):
                baseline.decode_baseline(value)
        with patch.object(baseline, 'MAX_ENCODED_BYTES', 10):
            with self.assertRaises(baseline.NativeInputBaselineError):
                baseline.decode_baseline(raw)
        with patch.object(baseline, 'MAX_TOTAL_BYTES', 10):
            with self.assertRaises(baseline.NativeInputBaselineError):
                self.capture()


HAS_HA = importlib.util.find_spec('homeassistant') is not None


@unittest.skipUnless(HAS_HA, 'native HA dependencies required')
class NativeBaselineTests(unittest.IsolatedAsyncioTestCase):
    """Actual current/min constructors, fragments, Store writes and independent reads."""
    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import entity_registry as er, device_registry as dr
        from homeassistant.const import __version__
        self.version = __version__
        self.directory = scratch()
        self.root = Path(self.directory.name).resolve()
        (self.root / '.storage').mkdir(mode=0o700)
        (self.root / 'configuration.yaml').write_bytes(b'script: {}\n')
        self.hass = HomeAssistant(str(self.root))
        loader.async_setup(self.hass)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        await self.hass.config_entries.async_initialize()
        # Real constructor signatures differ from older fake ConfigEntries(hass).
        self.device = dr.DeviceRegistry(self.hass)
        self.hass.data[dr.DATA_REGISTRY] = self.device
        await self.device.async_load()
        self.entity = er.EntityRegistry(self.hass)
        self.hass.data[er.DATA_REGISTRY] = self.entity
        await self.entity.async_load()
        self.entry = ConfigEntry(version=1, minor_version=1, domain='input_boolean',
            title='Synthetic baseline', source='user', unique_id='baseline-entry',
            data={'unknown_preserved_data': {'nested': [1, True, None]}},
            options={'foreign_options': {'sequence': ['second', 'first']}},
            discovery_keys=MappingProxyType({}), subentries_data=[{
                'subentry_type': 'fixture', 'title': 'Synthetic child', 'unique_id': 'child',
                'data': {'entity_id': 'sensor.old', 'unknown_subentry_field': [1, 2]}}])
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        self.native_device = self.device.async_get_or_create(config_entry_id=self.entry.entry_id,
                               identifiers={('input_boolean', 'baseline-device')}, name='Baseline')
        self.row = self.entity.async_get_or_create('sensor', 'input_boolean', 'baseline-old',
                       suggested_object_id='baseline_old', config_entry=self.entry,
                       device_id=self.native_device.id)
        self.other = self.entity.async_get_or_create('sensor', 'input_boolean', 'baseline-other',
                       suggested_object_id='baseline_other', config_entry=self.entry,
                       device_id=self.native_device.id)
        deleted = self.entity.async_get_or_create('sensor', 'input_boolean', 'baseline-deleted',
                       suggested_object_id='baseline_deleted', config_entry=self.entry)
        self.entity.async_remove(deleted.entity_id)
        await self.hass.async_start()
        await self.hass.async_block_till_done()
        self.managers = (self.entity, self.device, self.hass.config_entries)
        for manager in self.managers:
            await manager._store.async_save(manager._data_to_save())
        await self.hass.async_block_till_done()
        self.receipt = receipt_for(self.root, self.version)
        self.guards = 0

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    def guard(self):
        self.guards += 1

    async def capture(self, receipt=None):
        return await baseline.async_capture_native_input_baseline(
            self.hass, receipt or self.receipt, guard=self.guard)

    async def test_actual_native_serialization_and_store_full_baseline(self):
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        captured = await self.capture()
        self.assertEqual(self.guards, 3)
        self.assertEqual(captured.receipt, self.receipt)
        for item, manager in zip(captured.files, self.managers):
            self.assertEqual(item.raw, Path(manager._store.path).read_bytes())
            expected = json.loads(_orjson_bytes_default_encoder(manager._data_to_save()))
            self.assertEqual(json.loads(item.manager_json), expected)
            stored = json.loads(item.raw)
            self.assertEqual(stored['data'], expected)
            self.assertEqual((stored['version'], stored['minor_version']),
                             (manager._store.version, manager._store.minor_version))
        self.assertEqual(len(json.loads(captured.files[0].manager_json)['deleted_entities']), 1)
        entry = json.loads(captured.files[2].manager_json)['entries'][0]
        self.assertEqual(entry['options']['foreign_options']['sequence'], ['second', 'first'])
        self.assertTrue(entry['subentries'])
        self.assertEqual(baseline.decode_baseline(baseline.encode_baseline(captured)), captured)

    async def test_native_manager_samples_validate_each_decoded_tree_once(self):
        expected = tuple(baseline._json(baseline._loads(
            __import__('homeassistant.helpers.json', fromlist=['_orjson_bytes_default_encoder'])
            ._orjson_bytes_default_encoder(manager._data_to_save())))
            for manager in self.managers)
        original = baseline._bounded_json
        roots = []
        def bounded(value, depth=0, budget=None):
            if depth == 0:
                roots.append(value)
            return original(value, depth, budget)
        with patch.object(baseline, '_bounded_json', bounded):
            observed = baseline._Managers(self.hass, self.receipt)
            self.assertEqual(observed.initial, expected)
            self.assertEqual(observed.sample(), expected)
        self.assertEqual(len(roots), 6, 'one strict validation per fresh tree, not two')
        # A later native sample remains fresh; changing an unrelated row is
        # rejected even though the earlier decoded tree was already validated.
        self.entity.async_update_entity(self.other.entity_id, name='Foreign change')
        await self.entity._store.async_save(self.entity._data_to_save())
        await self.hass.async_block_till_done()
        with self.assertRaises(baseline.NativeInputBaselineError):
            observed.check()

    async def test_no_native_load_save_or_flush_during_capture(self):
        async def forbidden(*args, **kwargs):
            raise AssertionError('native effect during read-only capture')
        from homeassistant.helpers.storage import Store
        with patch.object(Store, 'async_save', forbidden), patch.object(Store, 'async_load', forbidden):
            await self.capture()

    async def test_native_mutation_returns_exact_api_row_audit_before_any_await(self):
        native = importlib.import_module(PACKAGE + '.native_registry')
        from homeassistant.helpers import entity_registry as er
        # Registry-only audit fixture: no restored unavailable placeholder may
        # occupy the old ID during the direct synchronous reverse API call.
        row = self.entity.async_get_or_create('sensor', 'input_boolean', 'audit-only',
            suggested_object_id='baseline_audit_only', config_entry=self.entry,
            disabled_by=er.RegistryEntryDisabler.USER)
        self.entity.async_get_or_create('sensor', 'input_boolean', 'audit-untouched',
            suggested_object_id='baseline_audit_untouched', config_entry=self.entry,
            disabled_by=er.RegistryEntryDisabler.USER)
        await self.entity._store.async_save(self.entity._data_to_save())
        source_raw = Path(self.entity._store.path).read_bytes()
        io = native.NativeRegistryIO(self.hass)
        before = next(r for r in io.document('entity')['entities'] if r['id'] == row.id)
        after = dict(before, entity_id='sensor.baseline_renamed')
        change = native.PreparedRegistryChange('entity', row.id,
                    json.dumps(before), json.dumps(after))
        stamp = io.mutate(change, after)
        self.assertEqual(json.loads(stamp.before_json), before)
        observed = next(r for r in io.document('entity')['entities'] if r['id'] == row.id)
        self.assertEqual(json.loads(stamp.after_json), observed)
        self.assertEqual(stamp.identity, row.id)
        self.assertEqual(stamp.kind, 'entity')
        self.assertEqual(observed['entity_id'], after['entity_id'])
        await self.entity._store.async_save(self.entity._data_to_save())
        result_raw = Path(self.entity._store.path).read_bytes()
        transition = importlib.import_module(PACKAGE + '.native_registry_transition')
        def prove(source, result, stamp, direction):
            audit = transition.NativeRegistryEffectAudit('entity', row.id, direction,
                digest(source), digest(result), transition.prepared_registry_digest(change),
                json.loads(stamp.before_json)['modified_at'],
                json.loads(stamp.after_json)['modified_at'])
            return transition.validate_registry_transition(source, result, change,
                version=self.version, direction=direction, native_effect_audit=audit)
        prove(source_raw, result_raw, stamp, 'forward')
        reverse = io.mutate(change, before)
        self.assertEqual(json.loads(reverse.before_json), observed)
        restored = next(r for r in io.document('entity')['entities'] if r['id'] == row.id)
        self.assertEqual(json.loads(reverse.after_json), restored)
        self.assertEqual(restored['entity_id'], before['entity_id'])
        self.assertEqual(restored['id'], before['id'])
        await self.entity._store.async_save(self.entity._data_to_save())
        reverse_raw = Path(self.entity._store.path).read_bytes()
        prove(result_raw, reverse_raw, reverse, 'reverse')

    async def test_native_prepared_store_and_journal_reopen_preserve_full_baseline(self):
        prepared = importlib.import_module(PACKAGE + '.prepared')
        journal = importlib.import_module(PACKAGE + '.journal')
        tx = importlib.import_module(PACKAGE + '.transaction')
        captured = await self.capture()
        store = prepared.native_prepared_store(self.hass)
        await store.initialize()
        reference = await store.create_native_inputs('batch-native-inputs', captured)
        plan = tx.Plan('synthetic-batch', ('registry',), ('registry',),
            (tx.Step('one', 'registry', 'identity', 'before', 'after', 'prepared-change'),),
            'synthetic-coverage', ('registry',), native_inputs=reference)
        log = journal.native_journal(self.hass)
        await log.initialize()
        await log.create(tx.Record('synthetic-operation', plan, phases=('pending',)))
        fresh_store = prepared.native_prepared_store(self.hass)
        fresh_log = journal.native_journal(self.hass)
        await fresh_store.initialize()
        await fresh_log.initialize()
        reopened = await fresh_log.load('synthetic-operation')
        self.assertEqual(reopened.plan.fingerprint, plan.fingerprint)
        self.assertEqual(fresh_store.resolve_native_inputs(
            reopened.plan.native_inputs, self.receipt), captured)
        payload = (self.root / '.storage/ha_entity_renamer.prepared').read_bytes()
        self.assertIn(b'native_input_baseline', payload)
        # These stores are private. No names/config bytes belong in Plan/journal.
        self.assertNotIn(b'unknown_preserved_data', json.dumps(asdict(reopened)).encode())

    async def test_pending_delay_write_and_final_listener_refuse(self):
        for store in (m._store for m in self.managers):
            for attribute in ('_data', '_delay_handle', '_unsub_final_write_listener', '_load_future'):
                with self.subTest(key=store.key, attribute=attribute):
                    old = getattr(store, attribute)
                    setattr(store, attribute, object())
                    try:
                        with self.assertRaisesRegex(baseline.NativeInputBaselineError, 'native_write_pending'):
                            await self.capture()
                    finally:
                        setattr(store, attribute, old)
            await store._write_lock.acquire()
            try:
                with self.assertRaises(baseline.NativeInputBaselineError): await self.capture()
            finally:
                store._write_lock.release()

    async def test_schema_store_path_and_manager_drift_refuse(self):
        for store in (m._store for m in self.managers):
            for attribute, value in (('version', store.version + 1), ('minor_version', store.minor_version + 1),
                                     ('key', 'foreign'), ('path', str(self.root / 'foreign'))):
                old = getattr(store, attribute)
                setattr(store, attribute, value)
                try:
                    with self.assertRaises(baseline.NativeInputBaselineError): await self.capture()
                finally:
                    setattr(store, attribute, old)
        from homeassistant.helpers import entity_registry as er
        old = self.hass.data[er.DATA_REGISTRY]
        self.hass.data[er.DATA_REGISTRY] = object()
        try:
            with self.assertRaises(baseline.NativeInputBaselineError): await self.capture()
        finally:
            self.hass.data[er.DATA_REGISTRY] = old
        with self.assertRaises(baseline.NativeInputBaselineError):
            await self.capture(replace(self.receipt, version='9999.1.0'))

    async def test_refreshed_receipt_cannot_authorize_foreign_untouched_row(self):
        # Hash/mode/inode and receipt all describe actual bytes, but managers
        # independently disagree. This defeats simply accepting a fresh hash.
        path = Path(self.entity._store.path)
        original = path.read_bytes()
        for mutation in ('audit', 'unknown', 'reorder', 'deleted', 'envelope'):
            with self.subTest(mutation=mutation):
                document = json.loads(original)
                rows = document['data']['entities']
                if mutation == 'audit': rows[1]['modified_at'] = '2000-01-01T00:00:00+00:00'
                elif mutation == 'unknown': rows[1]['unknown_native_field'] = 'foreign'
                elif mutation == 'reorder': rows.reverse()
                elif mutation == 'deleted': document['data']['deleted_entities'].clear()
                else: document['minor_version'] += 1
                path.write_bytes(json.dumps(document).encode())
                try:
                    with self.assertRaises(baseline.NativeInputBaselineError):
                        await self.capture(receipt_for(self.root, self.version))
                finally:
                    path.write_bytes(original)
        self.receipt = receipt_for(self.root, self.version)
        await self.capture()

    async def test_refreshed_receipt_cannot_hide_config_subentry_or_foreign_option(self):
        path = Path(self.hass.config_entries._store.path)
        original = path.read_bytes()
        for target in ('options', 'subentries', 'created_at', 'unknown_entry_field'):
            document = json.loads(original)
            row = document['data']['entries'][0]
            if target == 'options': row['options']['foreign_options']['sequence'].reverse()
            elif target == 'subentries': row['subentries'][0]['data']['entity_id'] = 'sensor.foreign'
            else: row[target] = 'foreign'
            path.write_bytes(json.dumps(document).encode())
            try:
                with self.assertRaises(baseline.NativeInputBaselineError):
                    await self.capture(receipt_for(self.root, self.version))
            finally:
                path.write_bytes(original)

    async def test_source_inode_mode_and_symlink_refuse(self):
        path = Path(self.device._store.path)
        original_mode = path.stat().st_mode
        path.chmod(0o640 if stat.S_IMODE(original_mode) != 0o640 else 0o600)
        with self.assertRaises(baseline.NativeInputBaselineError): await self.capture()
        path.chmod(stat.S_IMODE(original_mode))
        replacement = self.root / '.storage/replacement'
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(stat.S_IMODE(original_mode))
        os.replace(replacement, path)
        with self.assertRaises(baseline.NativeInputBaselineError): await self.capture()
        self.receipt = receipt_for(self.root, self.version)
        path.rename(self.root / '.storage/quarantined-original')
        path.symlink_to('quarantined-original')
        with self.assertRaises(baseline.NativeInputBaselineError): await self.capture()
        # teardown must not trigger delayed native writes to this synthetic link

    async def test_guard_and_managers_checked_after_each_await(self):
        original = self.hass.async_add_executor_job
        calls = 0
        async def executor(function, *args):
            nonlocal calls
            result = await original(function, *args)
            calls += 1
            if calls == 1:
                self.hass.config_entries._store._data = {'synthetic': True}
            return result
        try:
            with patch.object(self.hass, 'async_add_executor_job', executor):
                with self.assertRaisesRegex(baseline.NativeInputBaselineError, 'native_write_pending'):
                    await self.capture()
        finally:
            self.hass.config_entries._store._data = None
        self.assertGreaterEqual(self.guards, 2)

    async def test_manager_document_change_between_awaits_refuses(self):
        original = self.hass.async_add_executor_job
        changed = False
        async def executor(function, *args):
            nonlocal changed
            result = await original(function, *args)
            if not changed:
                changed = True
                # Real native mutation after disk read; its delayed save is also
                # evidence of unsettled persistence, never an automatic flush.
                self.entity.async_update_entity(self.other.entity_id, name='foreign-manager-change')
            return result
        with patch.object(self.hass, 'async_add_executor_job', executor):
            with self.assertRaises(baseline.NativeInputBaselineError): await self.capture()

    async def test_foreign_unrelated_input_during_final_await_refuses(self):
        original = self.hass.async_add_executor_job
        calls = 0
        async def executor(function, *args):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / 'configuration.yaml').write_bytes(b'script: {foreign: true}\n')
            return await original(function, *args)
        with patch.object(self.hass, 'async_add_executor_job', executor):
            with self.assertRaises(baseline.NativeInputBaselineError): await self.capture()

    async def test_repeated_cancellation_drains_before_releasing_pinned_descriptors(self):
        original = self.hass.async_add_executor_job
        entered, release = asyncio.Event(), asyncio.Event()
        descriptors = []
        async def executor(function, *args):
            result = await original(function, *args)
            if getattr(function, '__name__', '') == 'capture':
                descriptors.extend(fd for _, fd, _ in function.__self__.handles)
                entered.set()
                await release.wait()
            return result
        with patch.object(self.hass, 'async_add_executor_job', executor):
            task = asyncio.create_task(self.capture())
            try:
                await asyncio.wait_for(entered.wait(), timeout=10)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertEqual(len(descriptors), 3)
                for fd in descriptors:
                    self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        for fd in descriptors:
            with self.assertRaises(OSError):
                os.fstat(fd)

    async def test_guard_loss_after_await_refuses(self):
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic lease loss')
        with self.assertRaises(baseline.NativeInputBaselineError):
            await baseline.async_capture_native_input_baseline(self.hass, self.receipt, guard=guard)


class Core104BaselineFileTests(BaselineFileTests):
    """Identical native 1/23, 3/4, 1/5 layouts; exact version-4 receipts."""

    version = '2026.10.4'


if __name__ == '__main__':
    unittest.main()
