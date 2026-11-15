"""Native Script own-ID caller inventory qualification; no mutation claim."""
import importlib
import json
import unittest

import test_native_helper_quiescence as fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeOwnIDInventoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = fixture.NativeHelperQuiescenceTests._before_start

    def module(self, name):
        return importlib.import_module(fixture.fixtures.PACKAGE + '.' + name)

    async def _scripts(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        path = self.f.root / 'configuration.yaml'
        path.write_text('script:\n  own_worker:\n    alias: Own worker\n    sequence:\n'
            '    - event: own_id_native_effect\n      event_data: {marker: worker}\n'
            '  static_caller:\n    sequence:\n    - action: script.own_worker\n')
        config = await async_hass_config_yaml(self.f.hass)
        self.assertTrue(await async_setup_component(self.f.hass, 'script', config))
        await self.f.hass.async_block_till_done()
        return path

    async def test_actual_owned_service_and_static_caller_are_bound_and_continue(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        await self._scripts()
        hass = self.f.hass
        events = []
        self.addCleanup(hass.bus.async_listen('own_id_native_effect', events.append))
        context = Context()
        await hass.services.async_call('script', 'static_caller', {}, blocking=True, context=context)
        await hass.async_block_till_done()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].context.id, context.id)
        entities = {e.unique_id: e for e in hass.data[DATA_INSTANCES]['script'].entities}
        worker, caller = entities['own_worker'], entities['static_caller']
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        self.assertIs(inventory.selected_entity, worker)
        self.assertIs(inventory.selected_script, worker.script)
        self.assertIs(inventory.service, hass.services._services['script']['own_worker'])
        self.assertEqual(inventory.callers, (('script', caller.unique_id, 'direct', 'script.own_worker'),))
        inventory.check()
        audit = await self.module('native_own_script_service_audit').NativeOwnScriptServiceAudit.capture(
            hass, inventory, guard=lambda: None)
        audit.check()
        next_context = Context()
        await hass.services.async_call('script', 'static_caller', {}, blocking=True, context=next_context)
        await hass.async_block_till_done()
        self.assertEqual([e.context.id for e in events], [context.id, next_context.id])
        audit.check()
        # A new operation samples the post-execution cache/run graph.
        refreshed = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        refreshed.check()

    async def test_foreign_service_replacement_refuses_without_overwriting_it(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        original = inventory.service
        async def foreign(call):
            return None
        hass.services.async_register('script', 'own_worker', foreign,
            schema=original.schema, supports_response=original.supports_response)
        replacement = hass.services._services['script']['own_worker']
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check()
            self.assertIs(hass.services._services['script']['own_worker'], replacement)
        finally:
            hass.services.async_register('script', 'own_worker', original.job.target,
                schema=original.schema, supports_response=original.supports_response)

    async def test_foreign_script_unique_source_refuses_without_overwriting_it(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        entity, service = inventory.selected_entity, inventory.service
        original = entity._attr_unique_id
        self.assertIs(entity.__dict__['__attr_unique_id'], original)
        self.assertIs(entity.__dict__['unique_id'], original)
        self.assertNotIn('_attr_unique_id', entity.__dict__)
        foreign = 'foreign_unique_script'
        # The native _attr_ setter invalidates the descriptor cache itself.
        entity._attr_unique_id = foreign
        try:
            self.assertNotIn('unique_id', entity.__dict__)
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertIs(entity._attr_unique_id, foreign)
            self.assertIs(hass.services._services['script']['own_worker'], service)
        finally:
            entity._attr_unique_id = original
            self.assertIs(entity.unique_id, original)
        inventory.check_service()

    async def test_foreign_backing_with_stale_native_cache_refuses(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        entity, service = inventory.selected_entity, inventory.service
        original = entity.__dict__['__attr_unique_id']
        cached = entity.__dict__['unique_id']
        foreign = 'foreign_unique_script'
        # A direct foreign slot edit bypasses native invalidation and must not
        # be hidden by the retained original cached property.
        entity.__dict__['__attr_unique_id'] = foreign
        try:
            self.assertIs(entity.unique_id, cached)
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertIs(entity.__dict__['__attr_unique_id'], foreign)
            self.assertIs(hass.services._services['script']['own_worker'], service)
        finally:
            entity.__dict__['__attr_unique_id'] = original
        inventory.check_service()

    async def test_foreign_script_unique_storage_refuses_without_overwriting_it(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        entity, service = inventory.selected_entity, inventory.service
        original = entity.__dict__
        foreign = dict(original)
        entity.__dict__ = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertIs(entity.__dict__, foreign)
            self.assertIs(hass.services._services['script']['own_worker'], service)
        finally:
            entity.__dict__ = original
        inventory.check_service()

    async def test_foreign_native_unique_descriptor_refuses_after_audit(self):
        from homeassistant.helpers.entity import Entity
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        audit = await self.module('native_own_script_service_audit').NativeOwnScriptServiceAudit.capture(
            hass, inventory, guard=lambda: None)
        native = vars(Entity)['unique_id']
        class ForeignDescriptor:
            def __get__(self, instance, owner):
                return inventory.service_key
        foreign = ForeignDescriptor()
        Entity.unique_id = foreign
        try:
            with self.assertRaises((self.module('transaction').Refused, ValueError)):
                audit.check()
            self.assertIs(vars(Entity)['unique_id'], foreign)
            self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
        finally:
            Entity.unique_id = native
        audit.check()

    async def test_foreign_native_attr_descriptor_refuses_after_audit(self):
        from homeassistant.components.script import ScriptEntity
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        audit = await self.module('native_own_script_service_audit').NativeOwnScriptServiceAudit.capture(
            hass, inventory, guard=lambda: None)
        had_local = '_attr_unique_id' in vars(ScriptEntity)
        original = vars(ScriptEntity).get('_attr_unique_id')
        foreign = property(lambda entity: inventory.service_key)
        ScriptEntity._attr_unique_id = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                audit.check()
            self.assertIs(vars(ScriptEntity)['_attr_unique_id'], foreign)
            self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
        finally:
            if had_local:
                ScriptEntity._attr_unique_id = original
            else:
                del ScriptEntity._attr_unique_id
        audit.check()

    async def test_foreign_service_schema_edit_refuses_without_overwriting_it(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        service = inventory.service
        original = service.schema
        foreign = object()
        service.schema = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check()
            self.assertIs(service.schema, foreign)
            self.assertIs(hass.services._services['script']['own_worker'], service)
        finally:
            service.schema = original

    async def test_foreign_service_description_edit_refuses_without_overwriting_it(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        description = inventory.description
        original = description['name']
        description['name'] = 'Foreign description name'
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertEqual(description['name'], 'Foreign description name')
            self.assertIs(inventory.description_cache[('script', 'own_worker')], description)
        finally:
            description['name'] = original

    async def test_foreign_service_alias_refuses_without_removing_it(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        table, service = hass.services._services['script'], inventory.service
        table['foreign_alias'] = service
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertIs(table['foreign_alias'], service)
            self.assertIs(table['own_worker'], service)
        finally:
            del table['foreign_alias']

    async def test_foreign_description_alias_after_capture_refuses_without_removal(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        cache, description = inventory.description_cache, inventory.description
        cache[('script', 'foreign_description_alias')] = description
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertIs(cache[('script', 'foreign_description_alias')], description)
            self.assertIs(cache[('script', 'own_worker')], description)
            self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
        finally:
            del cache[('script', 'foreign_description_alias')]

    async def test_existing_destination_description_refuses_without_removal(self):
        from homeassistant.helpers import service as service_helper
        await self._scripts()
        hass = self.f.hass
        cache = hass.data[service_helper.SERVICE_DESCRIPTION_CACHE]
        key = ('script', 'own_worker_next')
        self.assertNotIn(key, cache)
        foreign = {'name': 'foreign'}
        cache[key] = foreign
        try:
            with self.assertRaises(self.module('transaction').Refused):
                self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
                    hass, {'script.own_worker': 'script.own_worker_next'})
            self.assertIs(cache[key], foreign)
            self.assertTrue(hass.services.has_service('script', 'own_worker'))
            self.assertFalse(hass.services.has_service('script', 'own_worker_next'))
        finally:
            del cache[key]

    async def test_late_foreign_listener_static_service_call_refuses_without_effect(self):
        from homeassistant.core import callback
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        calls = []
        @callback
        def foreign_listener(event):
            calls.append(event)
            hass.async_create_task(hass.services.async_call('script', 'own_worker', {}, blocking=True))
        remove = hass.bus.async_listen('own_foreign_service_caller', foreign_listener)
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertEqual(calls, [])
            self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
            self.assertFalse(hass.services.has_service('script', 'own_worker_next'))
            self.assertTrue(any(job.target is foreign_listener for job, _ in
                                hass.bus._listeners['own_foreign_service_caller']))
        finally:
            remove()

    async def test_late_foreign_listener_dynamic_script_call_refuses_without_effect(self):
        from homeassistant.core import callback
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        calls = []
        @callback
        def foreign_listener(event):
            calls.append(event)
            hass.async_create_task(hass.services.async_call('script', event.data['service'], {}, blocking=True))
        remove = hass.bus.async_listen('own_foreign_dynamic_caller', foreign_listener)
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertEqual(calls, [])
            self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
            self.assertFalse(hass.services.has_service('script', 'own_worker_next'))
            self.assertTrue(any(job.target is foreign_listener for job, _ in
                                hass.bus._listeners['own_foreign_dynamic_caller']))
        finally:
            remove()

    async def test_late_foreign_listener_dynamic_domain_refuses_without_effect(self):
        from homeassistant.core import callback
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        @callback
        def foreign_listener(event):
            hass.async_create_task(hass.services.async_call(event.data['domain'], event.data['service'], {}))
        remove = hass.bus.async_listen('own_foreign_dynamic_domain', foreign_listener)
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
            self.assertTrue(any(job.target is foreign_listener for job, _ in
                                hass.bus._listeners['own_foreign_dynamic_domain']))
        finally:
            remove()

    async def test_late_foreign_listener_wrapped_service_call_refuses_without_effect(self):
        from homeassistant.core import callback
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        calls = []
        async def invoke_service():
            calls.append('started')
            await hass.services.async_call('script', 'own_worker', {}, blocking=True)
        @callback
        def foreign_listener(event):
            hass.async_create_task(invoke_service())
        remove = hass.bus.async_listen('own_foreign_wrapped_caller', foreign_listener)
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertEqual(calls, [])
            self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
            self.assertTrue(any(job.target is foreign_listener for job, _ in
                                hass.bus._listeners['own_foreign_wrapped_caller']))
        finally:
            remove()

    async def test_late_native_service_event_listener_refuses_without_removal(self):
        from homeassistant.core import callback
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        @callback
        def foreign(event):
            return None
        remove = hass.bus.async_listen('service_registered', foreign)
        try:
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertTrue(any(job.target is foreign for job, _ in
                                hass.bus._listeners['service_registered']))
        finally:
            remove()

    async def test_service_object_in_real_listener_closure_refuses_without_write(self):
        from homeassistant.core import callback
        await self._scripts()
        hass = self.f.hass
        service = hass.services._services['script']['own_worker']
        observations = []
        @callback
        def foreign(event):
            observations.append(service)
        remove = hass.bus.async_listen('service_registered', foreign)
        try:
            with self.assertRaises(self.module('transaction').Refused):
                self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
                    hass, {'script.own_worker': 'script.own_worker_next'})
            self.assertIs(hass.services._services['script']['own_worker'], service)
            self.assertEqual(observations, [])
            self.assertTrue(any(job.target is foreign for job, _ in
                                hass.bus._listeners['service_registered']))
        finally:
            remove()

    async def test_unrelated_native_match_all_progress_is_not_global_epoch(self):
        from homeassistant.core import MATCH_ALL, callback
        await self._scripts()
        hass = self.f.hass
        observations = []
        @callback
        def observer(event):
            observations.append(event)
        remove = hass.bus.async_listen(MATCH_ALL, observer)
        try:
            inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
                hass, {'script.own_worker': 'script.own_worker_next'})
            hass.bus.async_fire('own_unrelated_event', {'number': 7})
            await hass.async_block_till_done()
            self.assertTrue(any(event.event_type == 'own_unrelated_event' for event in observations))
            inventory.check_service()
        finally:
            remove()

    async def test_late_service_alias_inside_existing_listener_is_refused(self):
        from homeassistant.core import callback
        await self._scripts()
        hass = self.f.hass
        shared = {}
        @callback
        def observer(event):
            if shared:
                return None
        remove = hass.bus.async_listen('service_registered', observer)
        try:
            inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
                hass, {'script.own_worker': 'script.own_worker_next'})
            shared['foreign_service'] = inventory.service
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertIs(shared['foreign_service'], inventory.service)
            self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
        finally:
            shared.clear()
            remove()

    async def test_listener_event_selection_tracks_only_relevant_set_membership(self):
        from homeassistant.core import callback
        await self._scripts()
        hass = self.f.hass
        event_types = {'unrelated'}
        @callback
        def observer(event):
            if event.event_type in event_types:
                return None
        remove = hass.bus.async_listen('service_registered', observer)
        try:
            inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
                hass, {'script.own_worker': 'script.own_worker_next'})
            event_types.add('other_unrelated')
            inventory.check_service()
            event_types.add('service_registered')
            with self.assertRaises(self.module('transaction').Refused):
                inventory.check_service()
            self.assertIn('service_registered', event_types)
        finally:
            remove()

    async def test_actual_late_static_caller_is_not_adopted(self):
        from homeassistant.core import Context
        path = await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        with path.open('a') as stream:
            stream.write('  late_caller:\n    sequence:\n    - action: script.own_worker\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        with self.assertRaises(self.module('transaction').Refused):
            inventory.check()
        self.assertTrue(hass.services.has_service('script', 'late_caller'))

    async def test_dynamic_script_service_is_refused_before_effect(self):
        from homeassistant.core import Context
        path = await self._scripts()
        hass = self.f.hass
        with path.open('a') as stream:
            stream.write('  dynamic_caller:\n    sequence:\n    - action: "{{ requested_service }}"\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        with self.assertRaises(self.module('transaction').Refused):
            self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
                hass, {'script.own_worker': 'script.own_worker_next'})

    async def test_script_default_name_key_dependency_is_refused_before_effect(self):
        from homeassistant.core import Context
        path = await self._scripts()
        path.write_text(path.read_text().replace('    alias: Own worker\n', '', 1))
        hass = self.f.hass
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        original = hass.services._services['script']['own_worker']
        with self.assertRaises(self.module('transaction').Refused):
            self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
                hass, {'script.own_worker': 'script.own_worker_next'})
        self.assertIs(hass.services._services['script']['own_worker'], original)
        self.assertFalse(hass.services.has_service('script', 'own_worker_next'))

    async def test_stable_service_inverse_requires_exact_original_preparation(self):
        from dataclasses import replace
        from homeassistant.core import Context
        path = await self._scripts()
        path.write_text(path.read_text().replace('    alias: Own worker\n', '', 1))
        hass = self.f.hass
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        native = self.module('native_registry')
        io = native.NativeRegistryIO(hass)
        actual = io.registries['entity'].async_get('script.own_worker')
        after = native._row(io.document('entity'), 'entity', actual.id)
        before = dict(after, entity_id='script.own_worker_alias')
        change = native.PreparedRegistryChange('entity', actual.id, json.dumps(before), json.dumps(after))
        Inventory = self.module('native_own_id_inventory').NativeOwnIDInventory
        # Without the retained original, this direction could be a true
        # service key move and must continue to refuse the default-name shape.
        with self.assertRaises(self.module('transaction').Refused):
            Inventory.capture(hass, {'script.own_worker': 'script.own_worker_alias'})
        inventory = Inventory.capture(hass, {'script.own_worker': 'script.own_worker_alias'},
                                      stable_change=change)
        self.assertTrue(inventory.stable_service)
        self.assertIsNone(inventory.service_event_census)
        inventory.check()
        forged = replace(change, after_json=json.dumps(dict(after, unique_id='other_worker')))
        with self.assertRaises(self.module('transaction').Refused):
            Inventory.capture(hass, {'script.own_worker': 'script.own_worker_alias'},
                              stable_change=forged)
        self.assertIs(io.registries['entity'].async_get('script.own_worker'), actual)
        self.assertIs(hass.services._services['script']['own_worker'], inventory.service)
        self.assertFalse(hass.services.has_service('script', 'own_worker_alias'))

    async def test_script_unique_preparation_uses_native_row_and_refuses_collision(self):
        await self._scripts()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.own_worker': 'script.own_worker_next'})
        native = self.module('native_registry')
        io = native.NativeRegistryIO(hass)
        before = native._row(io.document('entity'), 'entity', inventory.selected_entity.registry_entry.id)
        requested = dict(before, entity_id='script.own_worker_next')
        change = native.PreparedRegistryChange('entity', before['id'], json.dumps(before), json.dumps(requested))
        prepare = self.module('native_own_script_registry_preparation').own_script_registry_preparation
        result = prepare(change, inventory)
        self.assertEqual(result.before, before)
        self.assertEqual(result.after['unique_id'], 'own_worker_next')
        self.assertEqual(result.after['previous_unique_id'], 'own_worker')
        self.assertIs(io.registries['entity'].async_get('script.own_worker'), inventory.selected_entity.registry_entry)
        self.assertIsNone(io.registries['entity'].async_get('script.own_worker_next'))
        self.assertTrue(hass.services.has_service('script', 'own_worker'))
        self.assertFalse(hass.services.has_service('script', 'own_worker_next'))
        bridge = native.NativeRegistryBridge(io, object())
        bridge._fresh(result, before, lambda: None)
        # Another native registry row may own the target unique ID under a
        # different entity ID; the destination entity/service checks alone do
        # not establish uniqueness.
        foreign = io.registries['entity'].async_get_or_create('script', 'script', 'own_worker_next',
                                                              suggested_object_id='own_unique_taken')
        self.assertEqual(foreign.entity_id, 'script.own_unique_taken')
        with self.assertRaises(self.module('transaction').Refused):
            prepare(change, inventory)
        # Collision appears after the earlier preparation and under another
        # entity ID. The bridge must refuse synchronously before native API;
        # on minimum Core that API could otherwise pop the old entity first.
        with self.assertRaises(native.RegistryConflict):
            bridge._fresh(result, before, lambda: None)
        self.assertIs(io.registries['entity'].async_get(foreign.entity_id), foreign)
        self.assertIs(io.registries['entity'].async_get('script.own_worker'), inventory.selected_entity.registry_entry)
