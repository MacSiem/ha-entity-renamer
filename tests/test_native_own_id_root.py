"""Requested ordinary own-ID Root behavior, not an executed qualification."""
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeOwnIDRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_template_own_only_ordinary_and_fresh_inverse(self):
        await self._case('template', False)

    async def test_template_source_and_own_ordinary_and_fresh_inverse(self):
        await self._case('template', True)

    async def test_template_own_id_script_caller_without_template_dependency(self):
        await self._case('template', False, observer=False, template_caller=True)

    async def test_template_source_and_own_id_script_caller(self):
        await self._case('template', True, observer=False, template_caller=True)

    async def test_template_configured_self_id_snapshot_forward_and_inverse(self):
        await self._case('template', True, self_variable=True)

    async def test_automation_own_only_ordinary_and_fresh_inverse(self):
        await self._case('automation', False)

    async def test_automation_source_and_own_ordinary_and_fresh_inverse(self):
        await self._case('automation', True)

    async def test_script_own_only_ordinary_and_fresh_inverse(self):
        await self._case('script', False)

    async def test_script_source_and_own_ordinary_and_fresh_inverse(self):
        await self._case('script', True)

    async def test_script_service_move_foreign_schema_after_review_refuses(self):
        await self._case('script', False, moving_service=True, foreign='service_schema')

    async def test_script_service_move_late_native_caller_refuses(self):
        await self._case('script', False, moving_service=True, foreign='late_caller')

    async def test_script_service_move_late_foreign_listener_refuses(self):
        await self._case('script', False, moving_service=True, foreign='late_listener')

    async def test_script_service_move_late_service_event_listener_refuses(self):
        await self._case('script', False, moving_service=True, foreign='late_service_event_listener')

    async def test_script_service_move_foreign_scene_fence_after_review_refuses(self):
        await self._case('script', False, moving_service=True, foreign='foreign_scene_fence')

    async def test_script_service_move_foreign_actual_registry_effect_is_uncertain(self):
        await self._case('script', False, moving_service=True, foreign='uncertain')

    async def test_script_suggested_foreign_native_edit_after_intent_refuses(self):
        await self._case('script', False, moving_service=True, foreign='suggested_after_intent')

    async def test_script_equal_registry_payload_from_foreign_ancestry_is_not_owned(self):
        await self._case('script', False, moving_service=True, foreign='equal_registry_event')

    async def test_script_stable_service_without_explicit_name_fresh_inverse(self):
        await self._case('script', False, script_alias=False)

    async def test_script_native_service_key_ordinary_and_fresh_inverse(self):
        await self._case('script', False, moving_service=True)

    async def test_script_native_service_key_source_and_own_fresh_inverse(self):
        await self._case('script', True, moving_service=True)

    async def test_script_native_service_key_without_explicit_alias(self):
        await self._case('script', False, moving_service=True, script_alias=False)

    async def test_script_key_binder_forward_reverse_and_exact_name_review(self):
        await self._case('script', True, moving_service=True, script_alias=False, binder_regression=True)

    async def test_script_default_name_native_unload_readd_drift_refuses_inverse(self):
        await self._case('script', False, moving_service=True, script_alias=False, native_cycle=True)

    async def test_script_default_name_with_existing_user_registry_name(self):
        await self._case('script', False, moving_service=True, script_alias=False,
                         registry_name='Existing user name')

    async def test_script_default_name_foreign_native_slot_refuses_before_write(self):
        await self._case('script', False, moving_service=True, script_alias=False, foreign='script_name_slot')

    async def test_script_default_name_foreign_script_owner_refuses_before_write(self):
        await self._case('script', False, moving_service=True, script_alias=False, foreign='script_owner_alias')

    async def test_script_default_name_foreign_storage_alias_refuses_before_write(self):
        await self._case('script', False, moving_service=True, script_alias=False, foreign='script_storage_alias')

    async def test_script_service_foreign_entity_owner_refuses_before_write(self):
        await self._case('script', False, moving_service=True, foreign='script_entity_alias')

    async def test_script_service_key_without_template_dependency(self):
        await self._case('script', False, observer=False, moving_service=True)

    async def test_template_own_only_without_yaml_references(self):
        await self._case('template', False, observer=False)

    async def test_automation_own_only_without_yaml_references(self):
        await self._case('automation', False, observer=False)

    async def test_automation_own_id_native_caller_without_template_dependency(self):
        await self._case('automation', False, observer=False, automation_caller=True)

    async def test_automation_own_and_source_native_caller(self):
        await self._case('automation', True, automation_caller=True)

    async def test_script_stable_service_without_yaml_or_explicit_alias(self):
        await self._case('script', False, observer=False, script_alias=False)

    async def test_automation_own_and_source_native_caller_diagnostic(self):
        # Read-only native exception/position monitor, as used by the existing
        # mixed-Root tests. No callback, result, or effect is substituted.
        with mixed.native_diagnostics(self.module):
            await self._case('automation', True, automation_caller=True)

    async def test_automation_own_graph_foreign_actual_row_is_uncertain(self):
        await self._case('automation', True, automation_caller=True, foreign='uncertain')

    async def test_template_foreign_this_alias_after_review_refuses_without_effect(self):
        await self._case('template', False, foreign='this_alias')

    async def test_template_foreign_cleanup_after_review_refuses_without_effect(self):
        await self._case('template', False, foreign='cleanup')

    async def test_template_this_replaced_after_review_refuses_without_effect(self):
        await self._case('template', False, foreign='this_replacement')

    async def test_script_late_caller_after_review_refuses_without_effect(self):
        await self._case('script', False, foreign='late_caller')

    async def test_script_late_listener_caller_after_review_refuses_without_effect(self):
        await self._case('script', False, foreign='late_listener')

    async def test_automation_late_listener_caller_after_review_refuses_without_effect(self):
        await self._case('automation', False, foreign='late_automation_listener')

    async def test_script_foreign_service_schema_after_review_refuses_without_effect(self):
        await self._case('script', False, foreign='service_schema')

    async def test_script_foreign_service_description_after_review_refuses_without_effect(self):
        await self._case('script', False, foreign='service_description')

    async def test_script_foreign_unique_source_after_review_refuses_without_effect(self):
        await self._case('script', False, foreign='unique_source')

    async def test_template_foreign_actual_event_makes_effect_uncertain(self):
        await self._case('template', False, foreign='uncertain')

    async def _named_script_data(self):
        from homeassistant.core import Context
        runtime, _, _, _, _ = await self.data()
        path = self.f.root / 'mixed_scripts.yaml'
        path.write_text('mixed_source:\n  alias: Own ID root script\n'
                        '  sequence:\n  - event: own_named_script_result\n')
        await self.f.hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await self.f.hass.async_block_till_done()
        await self._flush_documents()
        return runtime

    async def test_script_service_event_automation_refuses_before_any_effect(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES

        runtime = await self._named_script_data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: own_foreign_service_event\n  alias: Foreign service event\n'
                '  initial_state: true\n  triggers:\n  - trigger: event\n'
                '    event_type: service_registered\n  actions:\n'
                '  - event: own_foreign_service_action\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        script = next(entity for entity in hass.data[DATA_INSTANCES]['script'].entities
                      if entity.unique_id == 'mixed_source')
        self.assertEqual(script.entity_id, 'script.mixed_source')
        old, new = script.entity_id, 'script.own_id_service_next'
        row, service = script.registry_entry, hass.services._services['script']['mixed_source']
        files = {name: (root / name).read_bytes() for name in
                 ('configuration.yaml', 'template.yaml', 'mixed_scripts.yaml', 'mixed_automations.yaml')}
        results = []
        self.addCleanup(hass.bus.async_listen('own_foreign_service_action', results.append))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with self.assertRaises((self.module('transaction').Refused, ValueError)):
            await runtime.native_yaml_prepare(hass, {old: new}, actor_id='own-id-native-admin',
                                             guard=lambda: None, refresh=refresh)
        self.assertIs(self.f.entity.async_get(old), row)
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertIs(hass.services._services['script']['mixed_source'], service)
        self.assertFalse(hass.services.has_service('script', 'own_id_service_next'))
        self.assertEqual(results, [])
        self.assertEqual({name: (root / name).read_bytes() for name in files}, files)

    async def test_script_unrelated_native_event_trigger_is_not_service_listener(self):
        from homeassistant.core import Context
        runtime = await self._named_script_data()
        hass, path = self.f.hass, self.f.root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: own_unrelated_event\n  alias: Unrelated event\n'
                '  initial_state: true\n  triggers:\n  - trigger: event\n'
                '    event_type: own_unrelated_control\n  actions:\n'
                '  - event: own_unrelated_action\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.mixed_source': 'script.own_id_service_next'})
        inventory.check()
        self.assertEqual(inventory.selected_entity.entity_id, 'script.mixed_source')
        self.assertTrue(hass.services.has_service('script', 'mixed_source'))
        self.assertFalse(hass.services.has_service('script', 'own_id_service_next'))

    async def test_script_native_recorder_service_recipient_and_unrelated_progress(self):
        runtime = await self._named_script_data()
        hass = self.f.hass
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.mixed_source': 'script.own_id_service_next'})
        proof = await self.module('native_own_script_service_recipients').NativeOwnScriptServiceRecipients.capture(
            hass, inventory, guard=lambda: None)
        proof.check()
        hass.bus.async_fire('own_unrelated_recorder_event', {'value': 3})
        await hass.async_block_till_done()
        proof.check()
        self.assertTrue(proof.recorder.is_alive())
        self.assertTrue(hass.services.has_service('script', 'mixed_source'))
        self.assertFalse(hass.services.has_service('script', 'own_id_service_next'))
        excluded = proof.recorder.exclude_event_types
        was_excluded = 'service_registered' in excluded
        if was_excluded:
            excluded.remove('service_registered')
        else:
            excluded.add('service_registered')
        try:
            with self.assertRaises(self.module('transaction').Refused):
                proof.check()
            self.assertEqual('service_registered' in excluded, not was_excluded)
        finally:
            if was_excluded:
                excluded.add('service_registered')
            else:
                excluded.remove('service_registered')

    async def test_script_exact_scene_fence_recipient_and_foreign_listener_refusal(self):
        from homeassistant.core import callback
        runtime = await self._named_script_data()
        hass = self.f.hass
        scene = self.module('native_empty_scene').NativeEmptySceneFence(hass)
        self.addCleanup(scene.close)
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.mixed_source': 'script.own_id_service_next'})
        recipients = self.module('native_own_script_service_recipients').NativeOwnScriptServiceRecipients
        proof = await recipients.capture(hass, inventory, guard=lambda: None, scene_fence=scene)
        proof.check()
        before = tuple((name, tuple(hass.bus._listeners.get(name, ())))
                       for name in ('service_removed', 'service_registered'))
        self.assertEqual(tuple(len(entries) for _, entries in before), (1, 1))
        self.assertIs(before[0][1][0][0].target, before[1][1][0][0].target)
        foreign_events = []
        @callback
        def foreign(event):
            foreign_events.append(event)
        remove = hass.bus.async_listen('service_registered', foreign)
        try:
            # An additional real foreign event listener is not authorized by
            # possession of the real native scene token, before or after proof.
            with self.assertRaises(self.module('transaction').Refused):
                proof.check()
            fresh = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
                hass, {'script.mixed_source': 'script.own_id_service_next'})
            with self.assertRaises(self.module('transaction').Refused):
                await recipients.capture(hass, fresh, guard=lambda: None, scene_fence=scene)
            self.assertTrue(any(job.target is foreign for job, _ in
                                hass.bus._listeners['service_registered']))
            self.assertEqual(foreign_events, [])
            self.assertTrue(hass.services.has_service('script', 'mixed_source'))
            self.assertFalse(hass.services.has_service('script', 'own_id_service_next'))
        finally:
            remove()
        proof.check()
        self.assertFalse(scene.changed)

    async def test_script_scene_recipient_latches_actual_native_scene_activity(self):
        from homeassistant.setup import async_setup_component
        runtime = await self._named_script_data()
        hass = self.f.hass
        self.assertTrue(await async_setup_component(hass, 'scene', {'scene': []}))
        await hass.async_block_till_done()
        scene = self.module('native_empty_scene').NativeEmptySceneFence(hass)
        self.addCleanup(scene.close)
        inventory = self.module('native_own_id_inventory').NativeOwnIDInventory.capture(
            hass, {'script.mixed_source': 'script.own_id_service_next'})
        recipients = self.module('native_own_script_service_recipients').NativeOwnScriptServiceRecipients
        proof = await recipients.capture(hass, inventory, guard=lambda: None, scene_fence=scene)
        proof.check()
        try:
            # Real native scene service; its call_service/state notifications
            # must continue to latch activity while the token is admitted.
            await hass.services.async_call('scene', 'create', {'scene_id': 'own_recipient_probe'}, blocking=True)
            await hass.async_block_till_done()
            self.assertIsNotNone(hass.states.get('scene.own_recipient_probe'))
            self.assertTrue(scene.changed)
            with self.assertRaises(self.module('transaction').Refused):
                proof.check_retained()
            self.assertTrue(hass.services.has_service('script', 'mixed_source'))
            self.assertFalse(hass.services.has_service('script', 'own_id_service_next'))
        finally:
            scene.close()
            if hass.states.get('scene.own_recipient_probe') is not None:
                await hass.services.async_call('scene', 'delete',
                    {'entity_id': 'scene.own_recipient_probe'}, blocking=True)
                await hass.async_block_till_done()

    async def _case(self, family, include_source, observer=True, foreign=None, moving_service=False,
                    self_variable=False, script_alias=True, automation_caller=False, template_caller=False,
                    native_cycle=False, registry_name=None, binder_regression=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, source_map, _, _, original = await self.data()
        hass, root = self.f.hass, self.f.root
        source_old, source_new = next(iter(source_map.items()))
        script_path = root / 'mixed_scripts.yaml'
        automation_path = root / 'mixed_automations.yaml'
        template_path = root / 'template.yaml'
        script_path.write_text('mixed_source:\n'
            + ('  alias: Own ID root script\n' if script_alias else '')
            + '  sequence:\n  - event: own_id_root_result\n'
            '    event_data:\n      value: "{{ states(' + repr(source_old) + ') }}"\n')
        automation_path.write_text('- id: mixed_source\n  alias: Own ID root automation\n'
            '  initial_state: true\n  triggers:\n  - trigger: state\n'
            '    entity_id: ' + source_old + '\n  actions:\n  - action: script.mixed_source\n')
        for domain in ('script', 'automation'):
            await hass.services.async_call(domain, 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        automation = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities if e.unique_id == 'mixed_source')
        template = next(e for p in async_get_platforms(hass, 'template') if p.domain == 'sensor'
                        for e in p.entities.values() if e.unique_id == 'scope-value')
        chosen = {'script': script, 'automation': automation, 'template': template}[family]
        if family == 'script' and not moving_service:
            # The admitted stable-service profile starts with an entity alias;
            # its native YAML unique ID and real service already own the target.
            self.f.entity.async_update_entity(script.entity_id, new_entity_id='script.own_id_root_alias')
            await hass.async_block_till_done()
            await self._flush_documents()
            script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities
                          if e.unique_id == 'mixed_source')
            chosen = script
        own_old = chosen.entity_id
        if registry_name is not None:
            self.assertEqual(family, 'script')
            self.f.entity.async_update_entity(own_old, name=registry_name)
            await hass.async_block_till_done()
        if template_caller:
            self.assertEqual(family, 'template')
            with script_path.open('a') as handle:
                handle.write('own_template_caller:\n  sequence:\n  - event: own_template_caller_result\n'
                             '    event_data:\n      value: "{{ states(' + repr(own_old) + ') }}"\n')
            await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        if automation_caller:
            self.assertEqual(family, 'automation')
            with script_path.open('a') as handle:
                handle.write('own_automation_caller:\n  sequence:\n  - action: automation.trigger\n'
                             '    target:\n      entity_id: ' + own_old + '\n')
            await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        own_new = ('script.own_id_service_new' if moving_service else
                   'script.mixed_source' if family == 'script' else
                   own_old.split('.', 1)[0] + '.own_id_root_next')
        # An actual Template consumer binds the selected own-ID. The selected
        # Template's this binding is observable without changing its value.
        text = template_path.read_text()
        if family == 'template':
            marker = '    name: Runtime consumer\n'
            self.assertIn(marker, text)
            self_id = ('    attributes:\n      self_id: "{{ this.entity_id if states('
                       + repr(source_old) + ') else this.entity_id }}"\n'
                       '      self_echo: "{{ this.state }}"\n')
            if self_variable:
                from homeassistant.const import __version__
                expression = '{{ this.entity_id if this is defined else \'unknown\' }}'
                if __version__ == '2025.6.0':
                    text = text.replace(marker, marker + '    variables:\n      self_ref: "'
                                        + expression + '"\n', 1)
                else:
                    header = '- unique_id: scope\n'
                    self.assertIn(header, text)
                    text = text.replace(header, header + '  variables:\n    self_ref: "'
                                        + expression + '"\n', 1)
                self_id += '      self_snapshot: "{{ self_ref }}"\n'
            text = text.replace(marker, marker + self_id, 1)
        if observer:
            text += ('- unique_id: own_probe\n  sensor:\n  - unique_id: observer\n'
                     '    name: Own ID observer\n    state: "{{ states(' + repr(own_old) + ') }}"\n')
        template_path.write_text(text)
        await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        template = next(e for p in async_get_platforms(hass, 'template') if p.domain == 'sensor'
                        for e in p.entities.values() if e.unique_id == 'scope-value')
        if family == 'template':
            chosen = template
            self.assertEqual(chosen.entity_id, own_old)
        self.identities = {(p.domain, e.unique_id): (e.entity_id,
            None if (r := self.f.entity.async_get(e.entity_id)) is None else r.id)
            for p in async_get_platforms(hass, 'template') for e in p.entities.values()}
        for path in (script_path, automation_path, template_path):
            original[path.name] = path.read_bytes()
        await self._flush_documents()
        mapping = {own_old: own_new}
        if include_source:
            mapping[source_old] = source_new
        uuids = {old: self.f.entity.async_get(old).id for old in mapping}
        script_native, action_script = script.script, automation.action_script
        initial_registry_name = self.f.entity.async_get(own_old).name
        initial_friendly_name = hass.states.get(own_old).attributes.get('friendly_name')
        if family == 'script' and moving_service and not script_alias:
            self.assertNotIn('alias', script.raw_config)
            self.assertEqual(script.script.name, own_old.split('.', 1)[1])
        template_tracker_before = template._template_result_info
        script_service = hass.services._services['script']['mixed_source']
        service_schema, service_response = script_service.schema, script_service.supports_response
        if family == 'script' and moving_service:
            import json
            from homeassistant.const import EVENT_SERVICE_REGISTERED, EVENT_SERVICE_REMOVED
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.db_schema import Events, EventData, EventTypes
            from homeassistant.components.recorder.util import session_scope
            from sqlalchemy import func, select
            recorder = get_instance(hass)
            self.assertNotIn(EVENT_SERVICE_REGISTERED, recorder.exclude_event_types)
            self.assertNotIn(EVENT_SERVICE_REMOVED, recorder.exclude_event_types)
            recorder_guard = self.module('recorder_guard').NativeRecorderGuard(hass)
            await recorder_guard.snapshot({})
            def latest_event():
                with session_scope(session=recorder.get_session(), read_only=True) as session:
                    return session.scalar(select(func.max(Events.event_id))) or 0
            first_event = await recorder.async_add_executor_job(latest_event)
            def stored_events():
                with session_scope(session=recorder.get_session(), read_only=True) as session:
                    query = (select(Events.event_id, EventTypes.event_type, EventData.shared_data)
                        .join(EventTypes, Events.event_type_id == EventTypes.event_type_id)
                        .join(EventData, Events.data_id == EventData.data_id)
                        .where(Events.event_id > first_event,
                               EventTypes.event_type.in_((EVENT_SERVICE_REMOVED, EVENT_SERVICE_REGISTERED)))
                        .order_by(Events.event_id))
                    result = []
                    for _, kind, raw in session.execute(query):
                        data = json.loads(raw) if raw is not None else {}
                        if (data.get('domain') == 'script' and data.get('service') in
                                (own_old.split('.', 1)[1], own_new.split('.', 1)[1])):
                            result.append((kind, data['service']))
                    return result
            async def service_events():
                # Real Recorder barrier and native persisted events; no extra
                # service-event listener or sys.monitoring callback is added.
                await recorder_guard.snapshot({})
                return await recorder.async_add_executor_job(stored_events)
        events = []
        self.addCleanup(hass.bus.async_listen('own_id_root_result', events.append))
        history = self.module('history_guard').NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass))
        template_events = []
        if template_caller:
            self.addCleanup(hass.bus.async_listen('own_template_caller_result', template_events.append))

        async def call_template_caller(current):
            if not template_caller:
                return
            context, count = Context(), len(template_events)
            await hass.services.async_call('script', 'own_template_caller', {}, blocking=True, context=context)
            await hass.async_block_till_done()
            self.assertEqual(len(template_events), count + 1)
            self.assertEqual(str(template_events[-1].data['value']), hass.states.get(current).state)
            self.assertEqual(template_events[-1].context.id, context.id)

        async def drive(value):
            count = len(events)
            context = Context()
            self.producer.async_set_context(context)
            self.producer._attr_native_value = value
            self.producer.async_write_ha_state()
            await hass.async_block_till_done()
            self.assertEqual(len(events), count + 1)
            self.assertEqual(str(events[-1].data['value']), str(value))
            self.assertEqual(events[-1].context.parent_id, context.id)

        async def call_service(current):
            if family == 'template':
                return
            count, context = len(events), Context()
            if family == 'automation':
                await hass.services.async_call('automation', 'trigger', {'entity_id': current},
                                               blocking=True, context=context)
            else:
                await hass.services.async_call('script', current.split('.', 1)[1], {},
                                               blocking=True, context=context)
            await hass.async_block_till_done()
            self.assertEqual(len(events), count + 1)
            if family == 'automation':
                self.assertEqual(events[-1].context.parent_id, context.id)
                if automation_caller:
                    caller_context = Context()
                    await hass.services.async_call('script', 'own_automation_caller', {},
                                                   blocking=True, context=caller_context)
                    await hass.async_block_till_done()
                    self.assertEqual(len(events), count + 2)
                    self.assertEqual(events[-1].context.parent_id, caller_context.id)
            else:
                self.assertEqual(events[-1].context.id, context.id)

        def identities(current):
            self.assertIs(script.script, script_native)
            self.assertIs(automation.action_script, action_script)
            self.assertIsNotNone(template._template_result_info)
            self.assertIs(chosen, {'script': script, 'automation': automation, 'template': template}[family])
            self.assertEqual(chosen.entity_id, current)
            self.assertEqual(self.f.entity.async_get(current).id, uuids[own_old])
            if family == 'script':
                if moving_service and not script_alias:
                    self.assertNotIn('alias', script.raw_config)
                    self.assertEqual(script.script.name, current.split('.', 1)[1])
                    self.assertEqual(hass.states.get(current).attributes.get('friendly_name'), initial_friendly_name)
                key = current.split('.', 1)[1] if moving_service else 'mixed_source'
                service = hass.services._services['script'][key]
                self.assertIs(service.job.target.__self__, script)
                self.assertIs(service.schema, service_schema)
                self.assertIs(service.supports_response, service_response)
            if family == 'template':
                self.assertTrue(all(track.variables['this']._entity_id == current
                    for track in template._template_result_info._track_templates))
                # ``this.entity_id`` intentionally does not collect a native
                # dependency. ``this.state`` does: the actual RenderInfo must
                # follow the selected UUID in both directions, without keeping
                # the old state-change subscription.
                self_infos = [info for track, info in
                    ((track, template._template_result_info._info.get(track.template))
                     for track in template._template_result_info._track_templates)
                    if 'this.state' in track.template.template]
                self.assertEqual(len(self_infos), 1)
                self.assertIn(current, self_infos[0].entities)
                self.assertNotIn(own_new if current == own_old else own_old,
                                 self_infos[0].entities)
                if self_variable:
                    self.assertTrue(all(track.variables['self_ref'] == current
                                        for track in template._template_result_info._track_templates))

        await drive(17)
        await call_template_caller(own_old)
        await call_service(own_new if family == 'script' and not moving_service else own_old)
        identities(own_old)
        baseline_history = await history.capture(mapping)
        own_metadata = (await history.guard.snapshot({own_old: own_new})
                        if family in ('template', 'automation') else None)
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        events_before_apply = len(events)
        template_events_before_apply = len(template_events)
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='own-id-native-admin',
            guard=lambda: None, refresh=refresh)
        self.assertEqual({row['entity_id'] for row in review['changes']}, set(mapping))
        if family == 'script' and moving_service:
            admission = runtime.reviews[review['operation_id']].own_admission
            census = admission.inventory.service_event_census
            self.assertTrue(census.scene_projection)
            self.assertTrue(census.scene_fence.closed)
            admission.check()  # Review Root retired its own latch; no new Root exists yet.
            selected_review = next(row for row in review['changes'] if row['entity_id'] == own_old)
            expected_fields = {'entity_id': {'before': own_old, 'after': own_new}}
            desired_name = (initial_registry_name or own_old.split('.', 1)[1]
                            if not script_alias else initial_registry_name)
            if desired_name != initial_registry_name:
                expected_fields['name'] = {'before': initial_registry_name, 'after': desired_name}
            self.assertEqual(selected_review, {'entity_id': own_old, 'fields': expected_fields})
            # The exact YAML definition key is part of the ordinary review,
            # distinct from references to the full service/entity ID.
            key_rows = [(file['path'], ref) for file in review['dependencies']['files']
                        for ref in file['references'] if ref.get('kind') == 'script_key']
            self.assertEqual(key_rows, [('mixed_scripts.yaml', {
                'kind': 'script_key', 'before': own_old.split('.', 1)[1],
                'after': own_new.split('.', 1)[1], 'occurrences': 1})])
        binder_changes = binder_composition = binder_mapping = None
        if binder_regression:
            import json
            from dataclasses import replace
            plan = runtime.reviews[review['operation_id']].plan
            _, binder_composition = runtime.preparations.resolve_composition(plan.native_inputs)
            digests = dict(binder_composition.prepared_digests)
            binder_changes = tuple(self.module('prepared').decode_change(
                runtime.preparations.resolve(step.prepared_ref, digests[step.prepared_ref]))
                for step in plan.steps if step.adapter == 'registry')
            binder_mapping = self.module('native_template_yaml_server')._registry_mapping
            self.assertEqual(binder_mapping(binder_changes, binder_composition), mapping)
            own_change = next(change for change in binder_changes if change.identity == uuids[own_old])
            self.assertEqual((own_change.before['unique_id'], own_change.after['unique_id'],
                              own_change.after['previous_unique_id']),
                             (own_old.split('.', 1)[1], own_new.split('.', 1)[1], own_old.split('.', 1)[1]))
            self.assertEqual((own_change.before['name'], own_change.after['name']),
                             (initial_registry_name, initial_registry_name or own_old.split('.', 1)[1]))
            # Each malformed ORIGINAL preparation must be refused before any
            # effect; this never grants an API capability or mutates a row.
            for key, value in (('unique_id', 'foreign'), ('previous_unique_id', 'foreign'),
                               ('original_name', 'foreign'), ('name', 'foreign'),
                               ('platform', 'automation'), ('id', 'foreign')):
                forged_after = dict(own_change.after, **{key: value})
                forged = replace(own_change, after_json=json.dumps(forged_after, sort_keys=True))
                altered = tuple(forged if item is own_change else item for item in binder_changes)
                with self.subTest(binder_foreign=key):
                    with self.assertRaises(self.module('transaction').Refused):
                        binder_mapping(altered, binder_composition)
            source_change = next(change for change in binder_changes if change.identity == uuids[source_old])
            forged = replace(source_change, after_json=json.dumps(
                dict(source_change.after, unique_id='foreign'), sort_keys=True))
            with self.assertRaises(self.module('transaction').Refused):
                binder_mapping(tuple(forged if item is source_change else item for item in binder_changes),
                               binder_composition)
            # This call is read-only: UUIDs and files still equal reviewed input.
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual({name: (root / name).read_bytes() for name in original}, original)
        if not observer and not include_source:
            if moving_service or automation_caller or template_caller:
                self.assertGreater(review['dependencies']['yaml_file_count'], 0)
            else:
                self.assertEqual(review['dependencies']['yaml_file_count'], 0)
        if foreign is not None and foreign not in ('uncertain', 'suggested_after_intent', 'equal_registry_event'):
            if foreign == 'late_caller':
                self.assertEqual(family, 'script')
                with script_path.open('a') as handle:
                    handle.write('late_own_id_caller:\n  sequence:\n  - action: script.mixed_source\n')
                await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
                await hass.async_block_till_done()
                await self._flush_documents()
            elif foreign not in ('service_schema', 'service_description', 'unique_source', 'late_listener', 'late_automation_listener', 'late_service_event_listener', 'foreign_scene_fence', 'script_name_slot', 'script_owner_alias', 'script_storage_alias', 'script_entity_alias'):
                self.assertEqual(family, 'template')
            physical_before = {name: (root / name).read_bytes() for name in original}
            physical_state = hass.states.get(own_old)
            if foreign == 'late_caller':
                self.assertTrue(hass.services.has_service('script', 'late_own_id_caller'))
                def restore():
                    pass
            elif foreign == 'service_schema':
                self.assertEqual(family, 'script')
                owned_service = hass.services._services['script']['mixed_source']
                saved_schema, foreign_schema = owned_service.schema, object()
                owned_service.schema = foreign_schema
                def restore():
                    owned_service.schema = saved_schema
            elif foreign == 'service_description':
                from homeassistant.helpers import service as service_helper
                self.assertEqual(family, 'script')
                description = hass.data[service_helper.SERVICE_DESCRIPTION_CACHE][('script', 'mixed_source')]
                saved_description = description['name']
                description['name'] = 'Foreign description name'
                def restore():
                    description['name'] = saved_description
            elif foreign == 'late_listener':
                from homeassistant.core import callback
                self.assertEqual(family, 'script')
                foreign_calls = []
                @callback
                def late_foreign_listener(event):
                    foreign_calls.append(event)
                    hass.async_create_task(hass.services.async_call(
                        'script', 'turn_on', {'entity_id': own_old}, blocking=True))
                remove_foreign_listener = hass.bus.async_listen('own_late_foreign_call', late_foreign_listener)
                def restore():
                    remove_foreign_listener()
            elif foreign == 'late_service_event_listener':
                from homeassistant.core import callback
                self.assertTrue(moving_service)
                foreign_calls = []
                @callback
                def late_service_listener(event):
                    foreign_calls.append(event)
                remove_foreign_listener = hass.bus.async_listen('service_registered', late_service_listener)
                def restore():
                    remove_foreign_listener()
            elif foreign == 'foreign_scene_fence':
                self.assertTrue(moving_service)
                foreign_scene = self.module('native_empty_scene').NativeEmptySceneFence(hass)
                def restore():
                    foreign_scene.close()
            elif foreign == 'late_automation_listener':
                from homeassistant.core import callback
                self.assertEqual(family, 'automation')
                foreign_calls = []
                @callback
                def late_foreign_listener(event):
                    foreign_calls.append(event)
                    hass.async_create_task(hass.services.async_call(
                        'automation', 'trigger', {'entity_id': own_old}, blocking=True))
                remove_foreign_listener = hass.bus.async_listen('own_late_foreign_call', late_foreign_listener)
                def restore():
                    remove_foreign_listener()
            elif foreign == 'script_entity_alias':
                from homeassistant.core import callback
                self.assertTrue(moving_service)
                retained_entity, foreign_calls = script, []
                @callback
                def foreign_entity_owner(event):
                    foreign_calls.append((event, retained_entity))
                remove_foreign_listener = hass.bus.async_listen('own_foreign_entity', foreign_entity_owner)
                def restore():
                    remove_foreign_listener()
            elif foreign == 'script_storage_alias':
                from homeassistant.core import callback
                self.assertTrue(moving_service and not script_alias)
                retained_storage, foreign_calls = script.__dict__, []
                @callback
                def foreign_storage_owner(event):
                    foreign_calls.append((event, retained_storage))
                remove_foreign_listener = hass.bus.async_listen('own_foreign_storage', foreign_storage_owner)
                def restore():
                    remove_foreign_listener()
            elif foreign == 'script_owner_alias':
                from homeassistant.core import callback
                self.assertTrue(moving_service and not script_alias)
                retained_script, foreign_calls = script.script, []
                @callback
                def foreign_script_owner(event):
                    foreign_calls.append((event, retained_script))
                remove_foreign_listener = hass.bus.async_listen('own_foreign_script_owner', foreign_script_owner)
                def restore():
                    remove_foreign_listener()
            elif foreign == 'script_name_slot':
                self.assertTrue(moving_service and not script_alias)
                original_script_name = script.script.name
                script.script.name = 'foreign native name'
                def restore():
                    script.script.name = original_script_name
            elif foreign == 'unique_source':
                self.assertEqual(family, 'script')
                original_unique = script._attr_unique_id
                script._attr_unique_id = 'foreign_script_unique_source'
                self.assertNotIn('unique_id', script.__dict__)
                def restore():
                    script._attr_unique_id = original_unique
            elif foreign == 'this_replacement':
                from homeassistant.helpers.template import TemplateStateFromEntityId
                variables = template._template_result_info._track_templates[0].variables
                saved = variables['this']
                foreign_this = TemplateStateFromEntityId(hass, own_old, collect=saved._collect)
                variables['this'] = foreign_this
                def restore():
                    variables['this'] = saved
            elif foreign == 'this_alias':
                other = next(e for p in async_get_platforms(hass, 'template') for e in p.entities.values()
                             if e is not template and getattr(e, '_template_result_info', None) is not None)
                variables = other._template_result_info._track_templates[0].variables
                saved = variables['this']
                variables['this'] = template._template_result_info._track_templates[0].variables['this']
                def restore():
                    variables['this'] = saved
            else:
                def foreign_cleanup():
                    return None
                template._on_remove.append(foreign_cleanup)
                def restore():
                    template._on_remove.remove(foreign_cleanup)
            try:
                with self.assertRaises((self.module('transaction').Refused, ValueError)) as refusal:
                    await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                        actor_id='own-id-native-admin', guard=lambda: None, refresh=refresh)
                self.assertIs(hass.states.get(own_old), physical_state)
                self.assertEqual(self.f.entity.async_get(own_old).id, uuids[own_old])
                self.assertIsNone(self.f.entity.async_get(own_new))
                self.assertEqual(len(events), events_before_apply)
                self.assertEqual({name: (root / name).read_bytes() for name in original}, physical_before)
                if foreign == 'foreign_scene_fence':
                    self.assertEqual(str(refusal.exception), 'own_script_service_listener_unsupported')
                    self.assertFalse(foreign_scene.closed)
                    foreign_scene.check()
                if foreign == 'service_schema':
                    self.assertIs(owned_service.schema, foreign_schema)
                if foreign == 'service_description':
                    self.assertEqual(description['name'], 'Foreign description name')
                if foreign == 'this_replacement':
                    self.assertIs(variables['this'], foreign_this)
                if foreign == 'script_entity_alias':
                    self.assertEqual(foreign_calls, [])
                    self.assertTrue(any(job.target is foreign_entity_owner for job, _ in
                                        hass.bus._listeners['own_foreign_entity']))
                if foreign == 'script_storage_alias':
                    self.assertEqual(foreign_calls, [])
                    self.assertTrue(any(job.target is foreign_storage_owner for job, _ in
                                        hass.bus._listeners['own_foreign_storage']))
                if foreign == 'script_owner_alias':
                    self.assertEqual(foreign_calls, [])
                    self.assertTrue(any(job.target is foreign_script_owner for job, _ in
                                        hass.bus._listeners['own_foreign_script_owner']))
                if foreign == 'script_name_slot':
                    self.assertEqual(script.script.name, 'foreign native name')
                    self.assertEqual(await service_events(), [])
                if foreign == 'unique_source':
                    self.assertEqual(script._attr_unique_id, 'foreign_script_unique_source')
                    self.assertEqual(script.unique_id, 'foreign_script_unique_source')
                if foreign == 'late_service_event_listener':
                    self.assertEqual(foreign_calls, [])
                    self.assertEqual(await service_events(), [])
                    self.assertTrue(any(job.target is late_service_listener for job, _ in
                                        hass.bus._listeners['service_registered']))
                if foreign in ('late_listener', 'late_automation_listener'):
                    self.assertEqual(foreign_calls, [])
                    self.assertTrue(any(job.target is late_foreign_listener for job, _ in
                                        hass.bus._listeners['own_late_foreign_call']))
            finally:
                restore()
            await drive(23)
            return
        if foreign == 'suggested_after_intent':
            import sys
            from homeassistant.const import __version__
            self.assertIn(__version__, ('2026.10.2', '2026.10.4'))
            code = self.module('native_registry').NativeRegistryIO.mutate.__code__
            tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
            observed = []
            sys.monitoring.use_tool_id(tool, 'own script foreign suggested after intent')
            def inject(actual_code, offset):
                frame = sys._getframe(1)
                io = frame.f_locals.get('self')
                capability = getattr(io, '_script_service_capability', None)
                if capability is None or observed:
                    return
                node = capability.node
                self.assertIs(node.root.chain.active, node.intent)
                self.assertEqual(node.context.record.phases[node.context.index], 'intent')
                self.assertFalse(capability.handoff.registry_calls)
                row = self.f.entity._async_update_entity(own_old, suggested_object_id='foreign_suggested')
                observed.append(row)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, inject)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            try:
                try:
                    outcome = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                        actor_id='own-id-native-admin', guard=lambda: None, refresh=refresh)
                except (self.module('transaction').Refused, ValueError):
                    outcome = None
                await hass.async_block_till_done()
                self.assertEqual(len(observed), 1, 'foreign edit must execute after the durable intent')
                self.assertTrue(outcome is None or outcome['state'] != 'applied')
                current = self.f.entity.async_get(own_old)
                self.assertIs(current, observed[0])
                self.assertEqual((current.id, current.suggested_object_id),
                                 (uuids[own_old], 'foreign_suggested'))
                self.assertIsNone(self.f.entity.async_get(own_new))
                self.assertTrue(hass.services.has_service('script', own_old.split('.', 1)[1]))
                self.assertFalse(hass.services.has_service('script', own_new.split('.', 1)[1]))
                self.assertEqual(len(events), events_before_apply)
                self.assertEqual({name: (root / name).read_bytes() for name in original}, original)
                record = await runtime.journal.load(review['operation_id'])
                self.assertNotEqual(record.status, 'applied')
            finally:
                sys.monitoring.set_local_events(tool, code, 0)
                sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
                sys.monitoring.free_tool_id(tool)
            return
        if foreign == 'equal_registry_event':
            import sys
            from homeassistant.core import callback
            from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
            classifier = self.module('native_rename_emission_classifier').NativeRenameEmissions
            code = classifier.prepare.__func__.__code__
            tool = next(index for index in range(6) if sys.monitoring.get_tool(index) is None)
            witnesses, delivered, echoed, decisions = [], [], [], []
            sys.monitoring.use_tool_id(tool, 'own script foreign equal registry occurrence')
            def observe(actual_code, offset, result):
                if type(result) is classifier:
                    witnesses.append(result)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, observe)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_RETURN)
            @callback
            def foreign_echo(event):
                if event.data.get('entity_id') != own_new:
                    return
                delivered.append(event)
                decisions.append(any(witness._owned_event(event) for witness in witnesses))
                if not echoed:
                    echoed.append(event.data)
                    hass.bus.async_fire_internal(EVENT_ENTITY_REGISTRY_UPDATED, event.data, context=event.context)
            remove = hass.bus.async_listen(EVENT_ENTITY_REGISTRY_UPDATED, foreign_echo)
            try:
                try:
                    outcome = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                        actor_id='own-id-native-admin', guard=lambda: None, refresh=refresh)
                except (self.module('transaction').Refused, ValueError):
                    outcome = None
                await hass.async_block_till_done()
                self.assertEqual(len(echoed), 1)
                self.assertGreaterEqual(len(delivered), 2)
                self.assertIs(delivered[0].data, delivered[1].data)
                self.assertTrue(witnesses, 'must observe the real native witness')
                self.assertEqual(decisions[:2], [True, False],
                                 'only the original writer occurrence may be owned')
                self.assertEqual(len(events), events_before_apply)
                self.assertEqual(len(template_events), template_events_before_apply)
                if outcome is not None:
                    self.assertIn(outcome['state'], ('applied', 'UNKNOWN', 'unknown'))
            finally:
                remove()
                sys.monitoring.set_local_events(tool, code, 0)
                sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
                sys.monitoring.free_tool_id(tool)
            return
        if foreign == 'uncertain':
            from homeassistant.core import callback
            foreign_rows = []
            @callback
            def on_actual_state(event):
                if (event.data.get('entity_id') == own_new and event.data.get('new_state') is not None
                        and not foreign_rows):
                    current = self.f.entity.async_get(own_new)
                    if current is not None:
                        foreign_rows.append(self.f.entity.async_update_entity(own_new, name='Foreign edit'))
            remove = hass.bus.async_listen('state_changed', on_actual_state)
            try:
                try:
                    outcome = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                        actor_id='own-id-native-admin', guard=lambda: None, refresh=refresh)
                except (self.module('transaction').Refused, ValueError):
                    outcome = None
                await hass.async_block_till_done()
                self.assertTrue(foreign_rows)
                self.assertTrue(outcome is None or outcome['state'] != 'applied')
                current = self.f.entity.async_get(own_new)
                self.assertEqual((current.id, current.name), (uuids[own_old], 'Foreign edit'))
                record = await runtime.journal.load(review['operation_id'])
                self.assertNotEqual(record.status, 'applied')
                with self.assertRaises((self.module('transaction').Refused, ValueError)):
                    await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                                                      guard=lambda: None, refresh=refresh)
                self.assertEqual(self.f.entity.async_get(own_new).name, 'Foreign edit')
            finally:
                remove()
            return
        try:
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='own-id-native-admin', guard=lambda: None, refresh=refresh)
        except self.module('transaction').Refused:
            if family == 'automation':
                # The native coordinator keeps uncertain status; inspect its
                # actual durable record without reconciling or retrying it.
                try:
                    failed = await runtime.journal.load(review['operation_id'])
                    print('OWN_AUTOMATION_JOURNAL', None if failed is None else
                          (failed.status, failed.direction, failed.reason, failed.phases,
                           tuple(step.adapter for step in failed.plan.steps)))
                except Exception as error:
                    print('OWN_AUTOMATION_JOURNAL_UNAVAILABLE', type(error).__name__)
            raise
        self.assertEqual(result['state'], 'applied')
        if family == 'script' and moving_service:
            await hass.async_block_till_done()
            self.assertEqual(await service_events(), [(EVENT_SERVICE_REMOVED, own_old.split('.', 1)[1]),
                                              (EVENT_SERVICE_REGISTERED, own_new.split('.', 1)[1])])
        self.assertEqual(len(events), events_before_apply, 'rename itself must not run an automation or script action')
        self.assertEqual(len(template_events), template_events_before_apply)
        record = await runtime.journal.load(result['operation_id'])
        baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
        original_ref, receipt = record.plan.native_inputs, baseline.receipt_json
        self.assertIsNone(self.f.entity.async_get(own_old))
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
        identities(own_new)
        if own_metadata is not None:
            self.assertTrue(self.module('recorder_guard').verify(
                {own_old: own_new}, own_metadata,
                await history.guard.snapshot({own_old: own_new})))
        if family == 'template':
            # The handoff must bind the real tracker installed by Root, while
            # retaining the same entity and native self-subscriptions.
            template_tracker_forward = template._template_result_info
            self.assertIsNot(template_tracker_forward, template_tracker_before)
            self.assertIn(template_tracker_forward.async_remove, template._on_remove)
            self.assertNotIn(template_tracker_before.async_remove, template._on_remove)
        if family == 'script':
            self.assertTrue(hass.services.has_service('script', own_new.split('.', 1)[1]))
            if moving_service:
                self.assertFalse(hass.services.has_service('script', own_old.split('.', 1)[1]))
                native_row = self.f.entity.async_get(own_new)
                self.assertEqual((native_row.unique_id, native_row.previous_unique_id),
                                 (own_new.split('.', 1)[1], own_old.split('.', 1)[1]))
                if not script_alias:
                    self.assertEqual(native_row.original_name, own_new.split('.', 1)[1])
                    self.assertEqual(native_row.name, initial_registry_name or own_old.split('.', 1)[1])
        await call_template_caller(own_new)
        await call_service(own_new)
        await drive(13)
        if family == 'script' and moving_service:
            # A natural reload reads the new YAML key and native unique ID.
            # Matching live config must retain this entity, Service and program;
            # registration under the new ID cannot be a transient map alias.
            native_service = hass.services._services['script'][own_new.split('.', 1)[1]]
            count = len(events)
            await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            self.assertEqual(len(events), count)
            self.assertEqual(len(await service_events()), 2)
            self.assertIn(script, hass.data[DATA_INSTANCES]['script'].entities)
            self.assertIs(hass.services._services['script'][own_new.split('.', 1)[1]], native_service)
            identities(own_new)
            await call_service(own_new)
            if native_cycle:
                # Actual native lifecycle: remove this idle entity and let
                # the ordinary Script reload recreate it from the committed
                # YAML. This runs EntityPlatform.async_get_or_create, the same
                # original_name update path used at Core startup.
                saved_row = self.f.entity.async_get(own_new)
                await script.platform.async_remove_entity(own_new)
                await hass.async_block_till_done()
                # Core retains an unavailable/restored registry placeholder
                # when an enabled registered entity is unloaded normally.
                from homeassistant.const import STATE_UNAVAILABLE
                placeholder = hass.states.get(own_new)
                self.assertIsNotNone(placeholder)
                self.assertEqual(placeholder.state, STATE_UNAVAILABLE)
                self.assertIs(placeholder.attributes.get('restored'), True)
                self.assertNotIn(script, hass.data[DATA_INSTANCES]['script'].entities)
                self.assertIs(self.f.entity.async_get(own_new), saved_row)
                self.assertFalse(hass.services.has_service('script', own_new.split('.', 1)[1]))
                await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
                await hass.async_block_till_done()
                readded_row = self.f.entity.async_get(own_new)
                self.assertIsNotNone(readded_row)
                self.assertEqual((readded_row.id, readded_row.unique_id, readded_row.original_name,
                                  readded_row.name),
                                 (saved_row.id, saved_row.unique_id, saved_row.original_name, saved_row.name))
                self.assertEqual(readded_row.id, uuids[own_old])
                script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities
                              if e.unique_id == own_new.split('.', 1)[1])
                script_native, chosen = script.script, script
                identities(own_new)
                await call_service(own_new)
                # Introduce one real foreign registry edit after re-add;
                # the retained operation has no authority to absorb it.
                self.f.entity.async_update_entity(own_new, icon='mdi:alert')
                await hass.async_block_till_done()
                await self._flush_documents()
        if family == 'template':
            self.assertEqual(hass.states.get(own_new).attributes['self_id'], own_new)
            self.assertEqual(str(hass.states.get(own_new).attributes['self_echo']),
                             hass.states.get(own_new).state)
            if self_variable:
                self.assertEqual(hass.states.get(own_new).attributes['self_snapshot'], own_new)
        events_before_inverse = len(events)
        template_events_before_inverse = len(template_events)
        if native_cycle:
            # Re-add caused a real native registry write outside the retained
            # operation. The original receipt must refuse that unproven drift.
            registry_path = root / '.storage' / 'core.entity_registry'
            registry_before = registry_path.read_bytes()
            files_before = {name: (root / name).read_bytes() for name in original}
            record_before = await runtime.journal.load(result['operation_id'])
            with self.assertRaisesRegex(self.module('native_registry_receipts').RegistryReceiptError,
                                        'registry_receipt_input_source_changed'):
                await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                    guard=lambda: None, refresh=refresh)
            self.assertEqual(registry_path.read_bytes(), registry_before)
            self.assertEqual({name: (root / name).read_bytes() for name in original}, files_before)
            self.assertEqual(await runtime.journal.load(result['operation_id']), record_before)
            self.assertEqual(len(events), events_before_inverse)
            self.assertEqual(len(template_events), template_events_before_inverse)
            identities(own_new)
            await call_service(own_new)
            return
        if binder_regression:
            # Inspect reopens the SAME original plan and projects the very same
            # definition-key change before the one-shot fresh inverse.
            inspected = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            self.assertTrue(inspected['eligible'])
            frozen = inspected['frozen_review']['dependencies']['files']
            self.assertEqual(frozen, review['dependencies']['files'])
            self.assertEqual(binder_mapping(binder_changes, binder_composition), mapping)
        inverse = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
            guard=lambda: None, refresh=refresh)
        self.assertEqual(inverse['state'], 'rolled_back')
        if binder_regression:
            self.assertEqual(binder_mapping(binder_changes, binder_composition), mapping)
        if family == 'script' and moving_service:
            await hass.async_block_till_done()
            expected_service_events = [(EVENT_SERVICE_REMOVED, own_old.split('.', 1)[1]),
                (EVENT_SERVICE_REGISTERED, own_new.split('.', 1)[1])]
            if native_cycle:
                expected_service_events += [(EVENT_SERVICE_REMOVED, own_new.split('.', 1)[1]),
                    (EVENT_SERVICE_REGISTERED, own_new.split('.', 1)[1])]
            expected_service_events += [(EVENT_SERVICE_REMOVED, own_new.split('.', 1)[1]),
                (EVENT_SERVICE_REGISTERED, own_old.split('.', 1)[1])]
            self.assertEqual(await service_events(), expected_service_events)
        reopened, restored = runtime.preparations.resolve_composition(record.plan.native_inputs)
        self.assertEqual((record.plan.native_inputs, reopened.receipt_json, restored),
                         (original_ref, receipt, composition))
        self.assertEqual(len(events), events_before_inverse)
        self.assertEqual(len(template_events), template_events_before_inverse)
        identities(own_old)
        if own_metadata is not None:
            self.assertTrue(self.module('recorder_guard').verify_rollback(
                {own_old: own_new}, own_metadata,
                await history.guard.snapshot({own_old: own_new})))
        if family == 'template':
            self.assertIsNot(template._template_result_info, template_tracker_forward)
            self.assertIn(template._template_result_info.async_remove, template._on_remove)
            self.assertNotIn(template_tracker_forward.async_remove, template._on_remove)
        if family == 'script' and moving_service:
            native_row = self.f.entity.async_get(own_old)
            self.assertEqual((native_row.unique_id, native_row.previous_unique_id),
                             (own_old.split('.', 1)[1], own_new.split('.', 1)[1]))
            if not script_alias:
                self.assertEqual(native_row.original_name, own_old.split('.', 1)[1])
                self.assertEqual(native_row.name, initial_registry_name)
        self.assertIsNone(self.f.entity.async_get(own_new))
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
        for path, contents in original.items():
            self.assertEqual((root / path).read_bytes(), contents)
        await call_template_caller(own_old)
        await call_service(own_new if family == 'script' and not moving_service else own_old)
        await drive(23)
        if family == 'template':
            self.assertEqual(hass.states.get(own_old).attributes['self_id'], own_old)
            self.assertEqual(str(hass.states.get(own_old).attributes['self_echo']),
                             hass.states.get(own_old).state)
            if self_variable:
                self.assertEqual(hass.states.get(own_old).attributes['self_snapshot'], own_old)
        self.assertTrue(await history.verify(baseline_history))
        if family == 'script':
            # A later natural integration reload must still register the real
            # unique-ID service and preserve the registry UUID/alias relation.
            if not moving_service:
                self.assertFalse(hass.services.has_service('script', own_old.split('.', 1)[1]))
            else:
                self.assertFalse(hass.services.has_service('script', own_new.split('.', 1)[1]))
            await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            reloaded = next(e for e in hass.data[DATA_INSTANCES]['script'].entities
                            if e.unique_id == 'mixed_source')
            self.assertEqual((reloaded.entity_id, reloaded.registry_entry.id), (own_old, uuids[own_old]))
            service = hass.services._services['script']['mixed_source']
            self.assertIs(service.job.target.__self__, reloaded)
            if not moving_service:
                self.assertFalse(hass.services.has_service('script', own_old.split('.', 1)[1]))
            await call_service(own_old if moving_service else own_new)
