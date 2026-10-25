"""Actual isolated native discovery, no mutation or readiness claim."""
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_native_input_baseline as fixture

subject = importlib.import_module('custom_components.ha_entity_renamer.discovery')
backend = importlib.import_module('custom_components.ha_entity_renamer')


class DiscoveryShapeTests(unittest.TestCase):
    def test_duplicate_stable_ids_and_source_ids_refuse(self):
        digest = 'a' * 64
        for rows in ((('a', 'same', digest), ('b', 'same', digest)),
                     (('same', 'a', digest), ('same', 'b', digest))):
            with self.assertRaisesRegex(ValueError, 'ambiguous_identity'):
                subject._family('entity_registry', {}, rows)

    def test_proposal_never_accepts_client_steps_roots_or_empty_intent(self):
        for document in ({}, {'steps': []}, {'roots': ['configuration.yaml']},
                         {'entities': [], 'prepared_ref': 'untrusted'}):
            with self.assertRaisesRegex(ValueError, 'invalid_proposal'):
                subject._proposal(document)

    def test_unknown_is_never_an_empty_complete_inventory(self):
        family = subject._unknown('energy', 'source_inventory_unavailable')
        self.assertEqual(family.status, 'UNKNOWN')
        self.assertIsNone(family.revision)
        self.assertIsNone(family.payload_json)


@unittest.skipUnless(fixture.HAS_HA, 'native HA dependencies required')
class NativeDiscoveryFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = fixture.NativeBaselineTests('test_actual_native_serialization_and_store_full_baseline')
        await self.f.asyncSetUp()
        self.addAsyncCleanup(self.f.asyncTearDown)
        self.runtime = backend.Runtime('fixture', object(), object())
        self.f.hass.data[backend.DATA_RUNTIME] = self.runtime
        options = json.loads(json.dumps(dict(self.f.entry.options)))
        options['foreign_options']['entity_id'] = self.f.row.entity_id
        self.f.hass.config_entries.async_update_entry(self.f.entry, options=options)
        await self.f.hass.config_entries._store.async_save(
            self.f.hass.config_entries._data_to_save())
        await self.f.hass.async_block_till_done()
        self.proposal = {'entities': [{'entity_id': self.f.row.entity_id,
            'proposed_entity_id': 'sensor.discovery_destination',
            'device_id': self.f.row.device_id}], 'devices': [], 'statistics': []}

    async def discover(self):
        async def refresh():
            self.f.guard()
        return await self.runtime.discover(self.f.hass, self.proposal,
                                          guard=self.f.guard, refresh=refresh)

    async def test_actual_sources_bind_complete_native_inventory_and_private_summary(self):
        from homeassistant.helpers.storage import Store
        def forbidden(*args, **kwargs):
            raise AssertionError('read-only discovery attempted load/save')
        original = {Path(manager._store.path): Path(manager._store.path).read_bytes()
                    for manager in self.f.managers}
        with patch.object(Store, 'async_save', forbidden), patch.object(Store, 'async_load', forbidden):
            capture = await self.discover()
        families = {row.name: row for row in capture.families}
        self.assertEqual(tuple(families), subject.FAMILIES)
        for name in ('entity_registry', 'device_registry', 'config_entries'):
            self.assertEqual(families[name].status, 'COMPLETE', families[name].reason)
        bound = json.loads(capture.bound_manifest_json)
        self.assertEqual(bound['entities'][0]['registry_id'], self.f.row.id)
        self.assertEqual(len(families['entity_registry'].rows), 2)
        self.assertEqual(len(families['config_entries'].rows), 1)
        self.assertTrue(families['config_entries'].blockers)
        preview = subject.compose_preview(capture)
        self.assertFalse(preview['migration_ready'])
        self.assertFalse(preview['inventory_complete'])
        self.assertFalse(preview['plan_supported'])
        raw = json.dumps(preview)
        for private in (self.f.row.id, self.f.entry.entry_id, str(self.f.root),
                        'unknown_preserved_data', 'foreign_options'):
            self.assertNotIn(private, raw)
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)

    async def test_actual_foreign_member_change_during_capture_cannot_keep_complete(self):
        source = importlib.import_module(backend.__name__ + '.native_discovery_inputs')
        observe = source.async_observe_discovery_inputs
        calls = 0
        async def changing(*args, **kwargs):
            nonlocal calls
            result = await observe(*args, **kwargs)
            calls += 1
            if calls == 1:
                self.f.entity.async_update_entity(self.f.other.entity_id, name='Foreign retained name')
                await self.f.entity._store.async_save(self.f.entity._data_to_save())
            return result
        with patch.object(source, 'async_observe_discovery_inputs', changing):
            capture = await self.discover()
        self.assertEqual(calls, 2)
        self.assertIsNone(capture.bound_manifest_json)
        self.assertIn('source_changed_during_capture', capture.conflicts)
        for family in capture.families:
            if family.name in ('entity_registry', 'device_registry', 'config_entries'):
                self.assertEqual(family.status, 'UNKNOWN')
        self.assertEqual(self.f.entity.async_get(self.f.other.entity_id).name, 'Foreign retained name')

    async def test_yaml_edit_during_last_native_await_invalidates_first_graph(self):
        source = importlib.import_module(backend.__name__ + '.native_discovery_inputs')
        observe = source.async_observe_discovery_inputs
        calls = 0
        async def late_edit(*args, **kwargs):
            nonlocal calls
            result = await observe(*args, **kwargs)
            calls += 1
            if calls == 2:
                (self.f.root / 'configuration.yaml').write_text(
                    'script: {}\n# foreign late edit retained\n')
            return result
        with patch.object(source, 'async_observe_discovery_inputs', late_edit):
            capture = await self.discover()
        self.assertEqual(calls, 2)
        family = next(row for row in capture.families if row.name == 'yaml_sources')
        self.assertEqual(family.status, 'UNKNOWN')
        self.assertEqual(family.reason, 'source_changed_during_capture')
        self.assertIn('source_changed_during_capture', capture.conflicts)
        self.assertIn('foreign late edit retained',
                      (self.f.root / 'configuration.yaml').read_text())
        self.assertFalse(subject.compose_preview(capture)['migration_ready'])

    async def test_native_permission_denied_and_missing_evidence_are_distinct(self):
        source = importlib.import_module(backend.__name__ + '.native_discovery_inputs')
        for error, status in ((PermissionError('private'), 'DENIED'), (ValueError('private'), 'UNKNOWN')):
            with self.subTest(status=status), patch.object(source, 'async_observe_discovery_inputs',
                                                          side_effect=error):
                capture = await self.discover()
                family = next(row for row in capture.families if row.name == 'entity_registry')
                self.assertEqual(family.status, status)
                self.assertIsNone(family.revision)
                self.assertNotIn('private', json.dumps(capture.summary()))
