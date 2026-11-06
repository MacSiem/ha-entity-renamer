"""Narrow actual Template handoff fixture; Root qualification is separate."""
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeOwnIDTemplateHandoffTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = mixed.NativeTemplateMixedYAMLServerTests.asyncSetUp
    _before_start = mixed.NativeTemplateMixedYAMLServerTests._before_start
    _flush_documents = mixed.NativeTemplateMixedYAMLServerTests._flush_documents
    prepare = mixed.NativeTemplateMixedYAMLServerTests.prepare
    emit = mixed.NativeTemplateMixedYAMLServerTests.emit
    _runtime = mixed.NativeTemplateMixedYAMLServerTests._runtime
    module = mixed.NativeTemplateMixedYAMLServerTests.module
    stimulus = mixed.NativeTemplateMixedYAMLServerTests.stimulus
    data = mixed.NativeTemplateMixedYAMLServerTests.data

    async def _prepared(self):
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.data(with_automation=False)
        await self.stimulus(17)
        hass = self.f.hass
        source = next(entity for platform in async_get_platforms(hass, 'template')
                      if platform.domain == 'sensor' for entity in platform.entities.values()
                      if entity.unique_id == 'scope-value')
        old, new = source.entity_id, 'sensor.native_own_handoff_target'
        native = self.module('native_registry')
        io = native.NativeRegistryIO(hass)
        bridge = native.NativeRegistryBridge(io, self.module('recorder_guard').NativeRecorderGuard(hass))
        change = await bridge.prepare('entity', source.registry_entry.id, {'entity_id': new}, lambda: None)
        return hass, source, old, new, io, change

    async def test_same_template_entity_tracker_uuid_forward_inverse(self):
        hass, source, old, new, io, change = await self._prepared()
        Handoff = self.module('native_source_handoff').NativeSourceHandoff
        This = self.module('native_own_id_this').NativeOwnIDThisTransport
        identity, tracker = source.registry_entry.id, source._template_result_info
        from homeassistant.helpers import restore_state
        restore = hass.data.get(restore_state.DATA_RESTORE_STATE)
        has_restore_owner = isinstance(source, restore_state.RestoreEntity)
        last_states = tuple(restore.last_states.items()) if has_restore_owner else ()
        if has_restore_owner:
            self.assertIs(restore.entities[old], source)
        # This direct primitive test excludes self-dependent subscriptions. The
        # production Root must transport those via the owned binding graph.
        self.assertTrue(all(old not in render.entities for render in tracker._info.values()))
        for target in (change.after, change.before):
            destination = target['entity_id']
            handoff = await Handoff.prepare(io, change, target, guard=lambda: None)
            this = This.capture(hass, source, destination, guard=lambda: None)
            this.install()
            handoff.bind_installed_this(this)
            try:
                handoff.mutate()
            finally:
                handoff.close()
            await io.settle(change)
            await self._flush_documents()
            self.assertEqual(source.entity_id, destination)
            self.assertEqual(source.registry_entry.id, identity)
            self.assertIs(source._template_result_info, tracker)
            if has_restore_owner:
                self.assertIs(restore.entities[destination], source)
                self.assertNotIn(old if destination == new else new, restore.entities)
                self.assertEqual(tuple(restore.last_states.items()), last_states)
            self.assertTrue(all(track.variables['this']._entity_id == destination
                                for track in tracker._track_templates))
            await self.emit(23 if destination == new else 29)
            self.assertEqual(hass.states.get(destination).state, '46' if destination == new else '58')
        self.assertIsNone(self.f.entity.async_get(new))

    async def test_late_foreign_cleanup_refused_before_registry(self):
        hass, source, old, new, io, change = await self._prepared()
        Handoff = self.module('native_source_handoff').NativeSourceHandoff
        handoff = await Handoff.prepare(io, change, change.after, guard=lambda: None)
        def foreign():
            return None
        source._on_remove.append(foreign)
        try:
            with self.assertRaises(self.module('native_source_handoff').NativeSourceHandoffError):
                handoff.mutate()
            self.assertFalse(handoff.attempted)
            self.assertIs(source._on_remove[-1], foreign)
            self.assertIsNotNone(self.f.entity.async_get(old))
            self.assertIsNone(self.f.entity.async_get(new))
        finally:
            source._on_remove.remove(foreign)
            handoff.close()
