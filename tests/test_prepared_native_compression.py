"""SOURCE codec regressions and ONE owner-only real Core 2026.10.4 qualification.

Not executed by the artifact author. Owner runs via native_unit_probe + FIFO.
SOURCE fixtures below are codec data, never positive NativeSource admission.
Native qualification uses genuine constructors/APIs/Stores and isolated scratch.
"""
import base64
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import importlib
import json
from pathlib import Path
import stat
import unittest
from unittest.mock import patch
import zlib

import test_native_input_baseline as fixtures
from test_native_input_preparation import Disk

native = fixtures.baseline
prepared = importlib.import_module(fixtures.PACKAGE + '.prepared')
tx = importlib.import_module(fixtures.PACKAGE + '.transaction')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def source_documents(padding=0):
    return ({'entities': [{'id': 'original', 'entity_id': 'sensor.original',
                          'unknown_native_field': 'A' * padding}],
             'deleted_entities': [{'id': 'deleted', 'modified_at': 'original'}],
             'settings': {'entity_id_parts': ['name']}},
            {'devices': [], 'child_devices': [], 'deleted_devices': []},
            {'entries': []})


def source_baseline(padding=0):
    """Deterministic SOURCE-only complete codec tree with fixed original IDs."""
    documents = source_documents(padding)
    files, sources, entries = [], [], []
    for index, (key, data) in enumerate(zip(native._KEYS, documents)):
        raw = fixtures.envelope(key, data, '2026.10.4')
        identity = (1, index + 100, stat.S_IFREG | 0o640)
        files.append(native.NativeInputFile(key, raw, identity, native._json(data)))
        sources.append(('.storage/' + key, sha(raw), *identity[:2]))
        entries.append((key, identity[2], *identity[:2]))
    sources.append(('configuration.yaml', sha(b'template: []\n'), 1, 200))
    receipt = fixtures.validation.ValidationReceipt(
        '/Volumes/Samsung_SSD/source-codec-fixture', tuple(sorted(sources)),
        (('.', (('.storage', stat.S_IFDIR | 0o700, 1, 201),
                 ('configuration.yaml', stat.S_IFREG | 0o640, 1, 200)), None),
         ('.storage', tuple(sorted(entries)), tuple(sorted(native._KEYS)))),
        (), (), (), '2026.10.4', ())
    return native.NativeInputBaseline(native._receipt_bytes(receipt), tuple(files))


def old_payload(baseline, composition=None):
    value = {'kind': 'native_input_baseline', 'schema': 1,
             'codec': base64.b64encode(native.encode_baseline(baseline)).decode('ascii'),
             'baseline_digest': baseline.digest}
    if composition is not None:
        module = importlib.import_module(fixtures.PACKAGE + '.native_template_composition')
        value.update(schema=2, composition=module.encode_composition(composition, baseline))
    return value


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def compressed_payload(baseline, *, level=-1):
    payload = old_payload(baseline)
    payload.update(schema=3, encoding='zlib+base64', codec=base64.b64encode(
        zlib.compress(native.encode_baseline(baseline), level)).decode('ascii'))
    return payload


def source_composition(baseline, physical=False):
    """Valid typed SOURCE codec evidence; no runtime admission is claimed."""
    ledger = importlib.import_module(fixtures.PACKAGE + '.yaml_source_ledger')
    binding = importlib.import_module(fixtures.PACKAGE + '.yaml_source_binding')
    templates = importlib.import_module(fixtures.PACKAGE + '.template_source_evidence')
    module = importlib.import_module(fixtures.PACKAGE + '.native_template_composition')
    identity = importlib.import_module(fixtures.PACKAGE + '.template_physical_identity')
    def seal(schema, definitions=()):
        value = ledger.SourceLedger(schema, '0' * 64, definitions, (), '')
        payload = {k: v for k, v in asdict(value).items() if k != 'sha256'}
        return replace(value, sha256=sha(json_bytes(payload)))
    definitions, consumers = (), ()
    if physical:
        origin = ledger.Origin('configuration.yaml', ('v:0', 'i:0'), 1, 0, 3, 1, ())
        key = identity.physical_template_key('template.sensor', origin.path, origin.start_line)
        definitions = (ledger.Definition('template.sensor', key, 'template', None,
                       origin, origin, '1' * 64, 'candidate'),)
        consumers = (('sensor', key, 'sensor.actual_native_uidless'),)
    template_ledger = seal(2, definitions)
    rows = [[row.domain, row.native_key, '2' * 64] for row in definitions]
    template_sources = templates.seal_template_sources(baseline.receipt, template_ledger,
        {'version': baseline.receipt.version, 'projection': '3' * 64, 'rows': rows})
    evidence = binding.OriginalSourceEvidence(seal(1), baseline.receipt_json,
        (binding.NativeSourceDomain('automation', None, None),
         binding.NativeSourceDomain('script', None, None)), template_sources)
    history = {'schema': 1, 'state_ids': [], 'statistic_ids': [], 'content': {
        name: dict(count=0, sha256='4' * 64, **({} if name == 'attributes' else {'ceiling': None}))
        for name in ('states', 'short_term', 'hourly', 'attributes')}}
    return module.TemplateComposition(evidence, (('change', '5' * 64),),
        history, deepcopy(history), '6' * 64, consumers)


class SourceCompressionTests(unittest.IsolatedAsyncioTestCase):
    async def fresh(self, disk=None):
        store = prepared.PreparedStore(disk or Disk())
        await store.initialize()
        return store

    async def persist_payload(self, payload, baseline):
        store = await self.fresh()
        digest = await store.create('inputs', payload)
        return store, ('inputs', digest, baseline.digest)

    async def test_large_real_limit_full_original_survives_fresh_store(self):
        original = source_baseline(26_800_000)
        old = old_payload(original)
        self.assertEqual(len(native.encode_baseline(original)), 71_469_388)
        self.assertEqual(len(json_bytes(old)), 95_292_659)
        self.assertGreater(len(json_bytes(old)), 60 * 1024 * 1024)
        with self.assertRaises(ValueError):
            prepared.canonical(old)
        store = await self.fresh()
        reference = await store.create_native_inputs('large', original)
        self.assertEqual(store.resolve(*reference[:2])['schema'], 3)
        self.assertLess(len(json_bytes(store.resolve(*reference[:2]))), prepared._MAX_BYTES)
        plan = tx.Plan('batch', ('registry',), ('registry',),
            (tx.Step('one', 'registry', 'original', 'before', 'after', 'change'),),
            'coverage', ('registry',), native_inputs=reference)
        fingerprint = plan.fingerprint
        journal = importlib.import_module(fixtures.PACKAGE + '.journal')
        log = journal.DurableJournal(Disk())
        await log.initialize()
        await log.create(tx.Record('operation', plan, phases=('pending',)))
        fresh_log = journal.DurableJournal(log.store)
        await fresh_log.initialize()
        restored_plan = (await fresh_log.load('operation')).plan
        reopened = await self.fresh(store.store)
        result = reopened.resolve_native_inputs(restored_plan.native_inputs, original.receipt)
        self.assertEqual(result, original)
        self.assertEqual(result.receipt_json, original.receipt_json)
        self.assertEqual(result.digest, original.digest)
        self.assertEqual(restored_plan.fingerprint, fingerprint)
        for actual, expected in zip(result.files, original.files):
            self.assertEqual((actual.raw, actual.manager_json, actual.identity),
                             (expected.raw, expected.manager_json, expected.identity))
        print('SOURCE_LARGE_SIZES', json.dumps({
            'raw_files': [len(item.raw) for item in original.files],
            'manager_files': [len(item.manager_json) for item in original.files],
            'native_codec': len(native.encode_baseline(original)),
            'old_payload': len(json_bytes(old)),
            'new_payload': len(json_bytes(store.resolve(*reference[:2])))}))

    async def test_small_old_schema1_and_schema2_bytes_digests_exact(self):
        baseline = source_baseline()
        for composition in (None, source_composition(baseline), source_composition(baseline, True)):
            expected = old_payload(baseline, composition)
            store = await self.fresh()
            reference = await store.create_native_inputs('old', baseline, composition=composition)
            self.assertEqual(json_bytes(store.resolve(*reference[:2])), json_bytes(expected))
            self.assertEqual(reference[1], sha(json_bytes(expected)))
            reopened = await self.fresh(store.store)
            self.assertEqual(reopened.resolve_native_inputs(reference, baseline.receipt), baseline)
            if composition is not None:
                self.assertEqual(reopened.resolve_composition(reference), (baseline, composition))

    async def test_new_outer_preserves_both_inner_compositions_and_uidless_ids(self):
        baseline = source_baseline()
        for physical in (False, True):
            composition = source_composition(baseline, physical)
            old = old_payload(baseline, composition)
            # SOURCE boundary control only: production limit remains unchanged.
            with patch.object(prepared, '_MAX_BYTES', len(json_bytes(old)) - 1):
                store = await self.fresh()
                ref = await store.create_native_inputs('composed', baseline, composition=composition)
                payload = store.resolve(*ref[:2])
                self.assertEqual(payload['schema'], 3)
                self.assertEqual(payload['composition'], old['composition'])
                reopened = await self.fresh(store.store)
                self.assertEqual(reopened.resolve_composition(ref), (baseline, composition))
                self.assertEqual(reopened.resolve_native_inputs(ref, baseline.receipt), baseline)
                changed = deepcopy(payload)
                changed['composition']['coverage_sha256'] = 'bad'
                bad, badref = await self.persist_payload(changed, baseline)
                with self.assertRaises(ValueError):
                    bad.resolve_native_inputs(badref, baseline.receipt)
                with self.assertRaises(ValueError):
                    bad.resolve_composition(badref)

    async def test_strict_stream_and_uncompressed_bounds(self):
        baseline = source_baseline()
        original_raw = native.encode_baseline(baseline)
        valid = compressed_payload(baseline)
        encoded = base64.b64decode(valid['codec'])
        for raw in (encoded[:-1], encoded + b'garbage', encoded + encoded,
                    b'not-zlib', encoded[:5] + b'broken' + encoded[7:],
                    zlib.compress(b' ' + original_raw),
                    zlib.compress(original_raw[:-1] + b',"format":1}'),
                    zlib.compress(b'A' * 4_000_000)):
            payload = dict(valid, codec=base64.b64encode(raw).decode('ascii'))
            store, reference = await self.persist_payload(payload, baseline)
            with patch.object(native, 'MAX_ENCODED_BYTES', len(original_raw)):
                with self.assertRaises(ValueError):
                    store.resolve_native_inputs(reference, baseline.receipt)
        # Inclusive maximum and distinct valid compression bytes are accepted.
        for level in (0, 1, 9):
            store, reference = await self.persist_payload(compressed_payload(baseline, level=level), baseline)
            with patch.object(native, 'MAX_ENCODED_BYTES', len(original_raw)):
                self.assertEqual(store.resolve_native_inputs(reference, baseline.receipt), baseline)
            with patch.object(native, 'MAX_ENCODED_BYTES', len(original_raw) - 1):
                with self.assertRaises(ValueError):
                    store.resolve_native_inputs(reference, baseline.receipt)

    async def test_unknown_fields_schema_codec_and_receipt_reference_refuse(self):
        baseline = source_baseline()
        value = compressed_payload(baseline)
        variants = [dict(value, schema=True), dict(value, schema=4),
                    dict(value, encoding='gzip+base64'), dict(value, extra=1),
                    dict(value, codec='!'), dict(value, baseline_digest='0' * 64)]
        for field in ('encoding', 'codec', 'baseline_digest'):
            item = dict(value); item.pop(field); variants.append(item)
        for payload in variants:
            store, reference = await self.persist_payload(payload, baseline)
            with self.assertRaises(ValueError):
                store.resolve_native_inputs(reference, baseline.receipt)
        store, reference = await self.persist_payload(value, baseline)
        with self.assertRaises(ValueError):
            store.resolve_native_inputs(reference, replace(baseline.receipt, changed_domains=('script',)))
        for index in (0, 1, 2):
            tampered = list(reference); tampered[index] = 'missing' if index == 0 else '0' * 64
            with self.assertRaises(ValueError):
                store.resolve_native_inputs(tuple(tampered), baseline.receipt)
        with self.assertRaises(ValueError):
            store.resolve_composition(reference)
        # Re-signing outer payload does not authorize altering native baseline.
        other = source_baseline(1)
        changed = dict(value, codec=compressed_payload(other)['codec'])
        store, reference = await self.persist_payload(changed, baseline)
        with self.assertRaises(ValueError):
            store.resolve_native_inputs(reference, baseline.receipt)

    async def test_compression_selection_before_single_uncertain_write(self):
        baseline = source_baseline(10_000)
        disk = Disk()
        disk.fail = True
        store = await self.fresh(disk)
        with patch.object(prepared, '_MAX_BYTES', 16_000), patch.object(
                disk, 'save', wraps=disk.save) as save:
            with self.assertRaises(OSError):
                await store.create_native_inputs('uncertain', baseline)
            self.assertEqual(save.call_count, 1)
            self.assertIsNone(store.plans)
            reopened = await self.fresh(disk)
            row = disk.payload['plans']['uncertain']
            self.assertEqual(row['payload']['schema'], 3)
            self.assertEqual(reopened.resolve_native_inputs(
                ('uncertain', row['digest'], baseline.digest), baseline.receipt), baseline)

    async def test_selected_compression_still_obeys_existing_payload_cap_before_write(self):
        baseline = source_baseline(10_000)
        store = await self.fresh()
        with patch.object(prepared, '_MAX_BYTES', 16_000), patch.object(
                prepared.zlib, 'compress', return_value=b'X' * 30_000), patch.object(
                store.store, 'save', wraps=store.store.save) as save:
            with self.assertRaises(ValueError):
                await store.create_native_inputs('too-large', baseline)
            self.assertEqual(save.call_count, 0)
            self.assertEqual(store.plans, {})


@unittest.skipUnless(fixtures.HAS_HA, 'native Core 2026.10.4 required')
class NativeLargeCompressionQualification(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.NativeBaselineTests.asyncSetUp
    asyncTearDown = fixtures.NativeBaselineTests.asyncTearDown
    guard = fixtures.NativeBaselineTests.guard
    capture = fixtures.NativeBaselineTests.capture

    async def test_real_registry_store_full_capture_and_prepared_reopen(self):
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        if self.version != '2026.10.4':
            self.skipTest('one bounded qualification on actual Core 2026.10.4')
        # Real APIs/constructors, including deleted entries. No hand-built
        # positive native source. Few ordinary rows keep admission cost bounded;
        # preserved native names make the full native document ~27 MB.
        for index in range(128):
            row = self.entity.async_get_or_create('sensor', 'input_boolean',
                'large-original-' + str(index), config_entry=self.entry,
                suggested_object_id='large_original_' + str(index),
                disabled_by=er.RegistryEntryDisabler.USER)
            self.entity.async_update_entity(row.entity_id,
                name='Native original name ' + str(index) + ':' + 'N' * 222_000)
            if index % 16 == 0:
                self.entity.async_remove(row.entity_id)
        await self.hass.async_block_till_done()
        for manager in self.managers:
            await manager._store.async_save(manager._data_to_save())
        await self.hass.async_block_till_done()
        self.receipt = fixtures.receipt_for(self.root, self.version)
        captured = await self.capture()
        original_path = Path(self.entity._store.path)
        self.assertGreater(original_path.stat().st_size, 24_000_000)
        self.assertLess(original_path.stat().st_size, native.MAX_FILE_BYTES)
        registry = json.loads(captured.files[0].manager_json)
        self.assertGreaterEqual(len(registry['deleted_entities']), 9)
        for manager, item in zip(self.managers, captured.files):
            self.assertEqual(item.raw, Path(manager._store.path).read_bytes())
            self.assertEqual(json.loads(item.manager_json),
                json.loads(_orjson_bytes_default_encoder(manager._data_to_save())))
        old = old_payload(captured)
        self.assertGreater(len(json_bytes(old)), prepared._MAX_BYTES)
        store = prepared.native_prepared_store(self.hass)
        await store.initialize()
        reference = await store.create_native_inputs('large-native-original', captured)
        plan = tx.Plan('native-batch', ('registry',), ('registry',),
            (tx.Step('one', 'registry', self.row.id, 'before', 'after', 'change'),),
            'coverage', ('registry',), native_inputs=reference)
        log_module = importlib.import_module(fixtures.PACKAGE + '.journal')
        log = log_module.native_journal(self.hass)
        await log.initialize()
        await log.create(tx.Record('native-operation', plan, phases=('pending',)))
        fresh_log = log_module.native_journal(self.hass)
        await fresh_log.initialize()
        reopened_plan = (await fresh_log.load('native-operation')).plan
        fresh = prepared.native_prepared_store(self.hass)
        await fresh.initialize()
        result = fresh.resolve_native_inputs(reopened_plan.native_inputs, self.receipt)
        self.assertEqual(result, captured)
        self.assertEqual(reopened_plan.fingerprint, plan.fingerprint)
        self.assertEqual(result.digest, captured.digest)
        self.assertEqual(result.receipt_json, captured.receipt_json)
        for item, original in zip(result.files, captured.files):
            self.assertEqual((item.raw, item.manager_json, item.identity),
                             (original.raw, original.manager_json, original.identity))
        self.assertEqual(json.loads(result.files[0].manager_json)['deleted_entities'],
                         registry['deleted_entities'])
        print('NATIVE_LARGE_SIZES', json.dumps({
            'raw_files': [len(item.raw) for item in captured.files],
            'manager_files': [len(item.manager_json) for item in captured.files],
            'native_codec': len(native.encode_baseline(captured)),
            'old_payload': len(json_bytes(old)),
            'new_payload': len(json_bytes(fresh.resolve(*reference[:2])))}))


if __name__ == '__main__':
    unittest.main()
