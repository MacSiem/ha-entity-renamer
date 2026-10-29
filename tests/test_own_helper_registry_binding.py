"""Synthetic original-byte bindings; authored only, owner runs via FIFO."""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from types import SimpleNamespace
import unittest

import test_native_registry_receipts as base
import test_native_utility_setup_audit_contract as audit_fixture

lifecycle = importlib.import_module(base.PACKAGE + '.native_helper_lifecycle')
u = importlib.import_module(base.PACKAGE + '.utility_meter_observers')


class OwnHelperBindingTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self, corrupt=None):
        f = await base.fixture()
        doc = audit_fixture.fixture.baseline_document()
        doc['after'] = deepcopy(doc['before'])
        doc['source_after'] = doc['source_before']
        helper = doc['helper_rows'][0]
        source = dict(zip(('id', 'unique_id', 'platform', 'config_entry_id', 'config_subentry_id'),
                          doc['source_identity']), entity_id=doc['source_before'])
        config = dict(doc['before']['identity'], domain='utility_meter', data={},
                      options=deepcopy(doc['before']['options']), subentries=[],
                      created_at=helper['created_at'], modified_at=helper['modified_at'],
                      discovery_keys={})
        observations = list(f['observations'])
        for index, data in ((0, {'entities': [helper, source], 'deleted_entities': [], 'settings': {}}),
                            (2, {'entries': [config]})):
            envelope = json.loads(observations[index].raw)
            envelope['data'] = data
            observations[index] = replace(observations[index], raw=base.raw(envelope))
        receipt = replace(f['receipt'], source_files=tuple(sorted(
            ('.storage/' + row.key, base.sha(row.raw), *row.identity[:2]) for row in observations)))
        original = base.baseline.NativeInputBaseline(base.baseline._receipt_bytes(receipt), tuple(
            base.baseline.NativeInputFile(row.key, row.raw, row.identity,
                base.baseline._json(json.loads(row.raw)['data'])) for row in observations))
        native_ref = await f['preparations'].create_native_inputs('own-id-original', original)
        before = deepcopy(helper)
        if corrupt:
            before[corrupt] = 'foreign'
        change = base.native.PreparedRegistryChange('entity', helper['id'], json.dumps(before),
            json.dumps(dict(before, entity_id='sensor.utility_new')))
        digest = await f['preparations'].create('own-id-change', base.prepared.encode_change(change))
        step = base.tx.Step('own-id', 'registry', change.identity, change.before_revision,
                           change.after_revision, 'own-id-change')
        plan = base.tx.Plan('own-id-only', ('registry',), ('registry',), (step,),
                           'synthetic', ('registry',), native_ref)
        async def yes(*args):
            return True
        obj = lifecycle.NativeHelperLifecycle(f['store'], None, 'own-id-op', plan,
            receipt, {'own-id-change': digest}, guard=lambda: True,
            verify_original_native_inputs=yes, settle=yes, verify_inactive=yes,
            verify_runtime=yes, verify_accepted_native_chain=yes)
        baseline = u.decode_runtime_baseline({'entry_id': 'entry', 'payload': doc})
        return obj, doc, baseline

    async def test_own_id_only_uses_original_config_and_original_unchanged_source(self):
        obj, doc, baseline = await self.fixture()
        self.assertEqual(obj.ids, ('entry',))
        self.assertEqual(obj.helper_changes, {})
        self.assertEqual(obj.helper_snapshots['entry'], (doc['before'], doc['before']))
        original = [{'entry_id': 'entry', 'domain': 'utility_meter', 'state': 'loaded'}]
        encoded = obj._bind_runtime_baselines(original, (baseline,))
        self.assertEqual(encoded[0]['payload'], doc)
        bad = deepcopy(doc)
        bad['source_identity'][1] = 'forged'
        forged = u.decode_runtime_baseline({'entry_id': 'entry', 'payload': bad})
        with self.assertRaises(lifecycle.HelperLifecycleError):
            obj._bind_runtime_baselines(original, (forged,))

    async def test_own_target_requires_original_row_domain_and_uuid(self):
        for corrupt in ('unique_id', 'platform', 'config_entry_id', 'id'):
            with self.subTest(corrupt=corrupt), self.assertRaises(lifecycle.HelperLifecycleError):
                await self.fixture(corrupt)


class OwnSetupAuditBindingTests(unittest.TestCase):
    def test_new_id_requires_explicit_original_preparation_and_direction(self):
        a, u = audit_fixture.a, audit_fixture.u
        doc = audit_fixture.fixture.baseline_document()
        baseline = u.decode_runtime_baseline({'entry_id': 'entry', 'payload': doc})
        # This audit package loads in a separate test package; use its exact type.
        native = importlib.import_module(a.__package__ + '.native_registry')
        before = doc['helper_rows'][0]
        after = dict(before, entity_id='sensor.utility_new')
        change = native.PreparedRegistryChange('entity', before['id'], json.dumps(before), json.dumps(after))
        with self.assertRaises(a.UtilitySetupAuditError):
            a._bind(baseline, doc['after'], [after])
        self.assertEqual(a._bind(baseline, doc['after'], [after],
            own_registry_change=change, direction='forward')[1], [after])
        self.assertEqual(a._bind(baseline, doc['before'], [before],
            own_registry_change=change, direction='rollback')[1], [before])
        for rows, direction in (([before], 'forward'), ([after], 'rollback'), ([after], None)):
            with self.assertRaises(a.UtilitySetupAuditError):
                a._bind(baseline, doc['before'], rows, own_registry_change=change, direction=direction)
        forged = dict(after, unique_id='foreign')
        with self.assertRaises(a.UtilitySetupAuditError):
            a._bind(baseline, doc['after'], [forged], own_registry_change=change, direction='forward')
