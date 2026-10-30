"""Actual native rename emission identity, not root authorization or ingress QA.

No filtering, constructed Event/State, handler call, fake clock or source result.
This component is a prerequisite for explicit pending/A/S; it grants no readiness.
"""
import asyncio
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeRenameEmissionTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    data = server.NativeTemplateYAMLServerTests.data
    stimulus = server.NativeTemplateYAMLServerTests.stimulus
    module = server.NativeTemplateYAMLServerTests.module
    _runtime = server.NativeTemplateYAMLServerTests._runtime

    async def test_actual_forward_inverse_binds_original_native_events_and_completed_tasks(self):
        runtime, mapping, uuids, original = await self.data(variant='sensor')
        hass = self.f.hass
        old, new = next(iter(mapping.items()))
        # Import before any mutation. The observer must own the one genuine API
        # call, rather than infer causality from values/UUID after another call.
        classifier = self.module('native_rename_emission_classifier')
        native = self.module('native_registry')
        io = native.NativeRegistryIO(hass)
        bridge = native.NativeRegistryBridge(io, self.module('recorder_guard').NativeRecorderGuard(hass))
        change = await bridge.prepare('entity', uuids[old], {'entity_id': new}, lambda: None)
        received = []
        def collect(event):
            if event.data.get('entity_id') in (old, new):
                received.append(event)
        self.addCleanup(hass.bus.async_listen('state_changed', collect))
        owner = asyncio.current_task()
        def guard():
            self.assertIs(asyncio.current_task(), owner)
            self.assertIs(io.hass, hass)
        renamed = False
        try:
            witness = await classifier.NativeRenameEmissions.prepare(io, change, change.after, guard=guard)
            mutation = witness.mutate()
            renamed = True
            self.assertIs(type(mutation), native.NativeRegistryMutation)
            proof = await witness.finish()
            self.assertEqual((proof['rename_remove'], proof['rename_add']), (1, 1))
            self.assertTrue(proof['source_tasks_complete'])
            self.assertTrue(proof['dispatches_complete'])
            own = [event for event in received if witness.is_owned_rename(event)]
            self.assertEqual(len(own), 2)
            removed = next(event for event in own if event.data['new_state'] is None)
            added = next(event for event in own if event.data['old_state'] is None)
            self.assertEqual((removed.data['entity_id'], added.data['entity_id']), (old, new))
            self.assertIs(added.data['new_state'], hass.states.get(new))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        finally:
            # Restore through a separately prepared witness. Never repeat the
            # forward API call after uncertainty or construct a replacement row.
            if renamed and self.f.entity.async_get(new) is not None:
                inverse = await classifier.NativeRenameEmissions.prepare(io, change, change.before, guard=guard)
                inverse.mutate()
                inverse_proof = await inverse.finish()
                self.assertEqual((inverse_proof['rename_remove'], inverse_proof['rename_add']), (1, 1))
        self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertEqual((self.f.root / 'template.yaml').read_bytes(), original)
        await self.stimulus(11)
