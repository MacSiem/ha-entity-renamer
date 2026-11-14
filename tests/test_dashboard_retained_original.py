"""The retained dashboard semantic check never adopts changed ORIGINAL data."""
import json
import stat
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import test_prepared_native_compression as fixture
from importlib import import_module

native, prepared, tx = fixture.native, fixture.prepared, fixture.tx
pkg = fixture.fixtures.PACKAGE
rr = import_module(pkg + '.native_registry_receipts')
nr = import_module(pkg + '.native_registry')
lov = import_module(pkg + '.lovelace_native')
dr = import_module(pkg + '.dashboard_receipts')


async def make_chain():
    baseline = fixture.source_baseline(1000)
    before = {'views': [{'cards': [{'type': 'entity', 'entity': 'sensor.original'}]}]}
    after = {'views': [{'cards': [{'type': 'entity', 'entity': 'sensor.renamed'}]}]}
    dashboard = lov.PreparedDashboard(lov.Snapshot(lov.Dashboard(None, 'lovelace', 'identity'),
        before, lov.revision(before), (1, 400, 'a' * 64)), after, lov.revision(after))
    directories = tuple((path, tuple(sorted((*entries, ('lovelace', stat.S_IFREG | 0o600, 1, 400)))),
                         tuple(sorted((*names, 'lovelace')))) if path == '.storage'
                        else (path, entries, names)
                        for path, entries, names in baseline.receipt.source_directories)
    receipt = replace(baseline.receipt, source_files=tuple(sorted((*baseline.receipt.source_files,
        ('.storage/lovelace', 'a' * 64, 1, 400)))), source_directories=directories)
    baseline = native.NativeInputBaseline(native._receipt_bytes(receipt), baseline.files)
    store = prepared.PreparedStore(fixture.Disk())
    await store.initialize()
    source = fixture.source_documents(1000)[0]['entities'][0]
    registry = nr.PreparedRegistryChange('entity', 'original', json.dumps(source),
        json.dumps({**source, 'entity_id': 'sensor.renamed'}))
    digests = {'change': await store.create('change', prepared.encode_change(registry)),
               'dashboard': await store.create('dashboard', prepared.encode_change(dashboard))}
    composition = replace(fixture.source_composition(baseline),
                          prepared_digests=tuple(sorted(digests.items())))
    reference = await store.create_native_inputs('native', baseline, composition=composition)
    steps = (tx.Step('r', 'registry', registry.identity, registry.before_revision,
                     registry.after_revision, 'change'),
             tx.Step('d', 'dashboard', 'identity', dashboard.before.revision,
                     dashboard.after_revision, 'dashboard'))
    plan = tx.Plan('bench', ('registry', 'dashboard'), ('registry', 'dashboard'), steps,
                   'coverage', ('registry', 'dashboard'), reference)
    sidecar = rr.RegistryReceiptStore(fixture.Disk(), store)
    await sidecar.initialize()
    metadata = {'.storage/lovelace': ['a' * 64, 1, 400, stat.S_IFREG | 0o600, 501, 20]}
    with patch.object(dr, 'capture_original_metadata', return_value=metadata):
        chain = await rr.RegistryReceiptChain.start(sidecar, 'bench', plan, receipt, digests,
                                                     dashboard_original_metadata=metadata)
    return chain


class RetainedDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuse_and_each_original_fence(self):
        chain = await make_chain()
        with patch.object(dr, 'validate_prepared', wraps=dr.validate_prepared) as validate:
            for _ in range(3):
                chain._original_change(1)
            self.assertEqual(validate.call_count, 0)
        for ref in ('native', 'change', 'dashboard'):
            row = chain.preparations.plans[ref]
            original = deepcopy(row)
            row['payload']['foreign'] = True
            with self.subTest(ref=ref), self.assertRaisesRegex(rr.RegistryReceiptError, 'dashboard_original_preparation_changed'):
                chain._dashboard_preparations_unchanged(1)
            chain.preparations.plans[ref] = original
            chain._original_change(1)
        for field in ('plan', 'original_receipt', 'baseline'):
            original = getattr(chain, field)
            if field == 'plan':
                altered = replace(original, plan_id='foreign')
            elif field == 'original_receipt':
                altered = replace(original, config_root='/foreign')
            else:
                altered = replace(original, receipt_json=original.receipt_json + b' ')
            setattr(chain, field, altered)
            with self.subTest(field=field), self.assertRaisesRegex(rr.RegistryReceiptError, 'dashboard_original_preparation_changed'):
                chain._dashboard_preparations_unchanged(1)
            setattr(chain, field, original)
            chain._original_change(1)
        original = deepcopy(chain.root['bindings'][1])
        chain.root['bindings'][1]['foreign'] = True
        with self.assertRaisesRegex(rr.RegistryReceiptError, 'dashboard_original_preparation_changed'):
            chain._dashboard_preparations_unchanged(1)
        chain.root['bindings'][1] = original
        for target in (chain.changes[1].before.config, chain.changes[1].after):
            target['foreign'] = True
            with self.assertRaisesRegex(rr.RegistryReceiptError, 'dashboard_preparation_mismatch'):
                chain._original_change(1)
            del target['foreign']
            chain._original_change(1)
        chain.preparations.plans = None
        with self.assertRaisesRegex(ValueError, 'preparations_unavailable'):
            chain._original_change(1)

    async def test_nested_types_fallback_and_live_guard(self):
        chain = await make_chain()
        # Exact-type comparison rejects bool/int equality inside every relevant row.
        for ref, path in (('native', ('schema',)), ('change', ('data', 'kind')),
                          ('dashboard', ('data', 'before', 'config', 'views'))):
            row = chain.preparations.plans[ref]
            original = deepcopy(row)
            node = row['payload']
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = True
            with self.subTest(ref=ref), self.assertRaisesRegex(
                    rr.RegistryReceiptError, 'dashboard_original_preparation_changed'):
                chain._dashboard_preparations_unchanged(1)
            chain.preparations.plans[ref] = original
        chain._dashboard_preparation_snapshot = None
        with patch.object(dr, 'validate_prepared', wraps=dr.validate_prepared) as validate:
            chain._original_change(1)
            self.assertEqual(validate.call_count, 1)
        class Stale(Exception):
            pass
        def guard():
            raise Stale('lost authority')
        with self.assertRaisesRegex(Stale, 'lost authority'):
            await dr.observe(None, None, guard)
