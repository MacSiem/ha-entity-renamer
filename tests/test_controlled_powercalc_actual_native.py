"""Core 11.1 explicit-pair Powercalc graph through the installed migration path."""
import asyncio
from decimal import Decimal
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_powercalc_actual_profiles_native as actual
import test_powercalc_public_cold_native as cold


def assert_one_native_cold_start(test, events, boot_begin, boot_end):
    """P4 cold test: native bus capture must contain one real boot event."""
    from homeassistant.core import EventOrigin
    test.assertEqual(len(events), 1)
    event, = events
    test.assertEqual(event.event_type, 'homeassistant_start')
    test.assertEqual(event.origin, EventOrigin.local)
    test.assertLessEqual(boot_begin, event.time_fired)
    test.assertLessEqual(event.time_fired, boot_end)


@unittest.skipUnless(actual.fixtures.HAS_HA, 'requires pinned native HA')
class ControlledActualGraphTests(cold.PowercalcColdPhase):
    namespace_source_bytes = b'# Synthetic Powercalc namespace\n'
    _setup_vendor_forward = actual.ActualProfileGraphNativeTests._setup_real_vendor
    remote_bytes = actual.ActualProfileGraphNativeTests.remote_bytes
    write_package = actual.ActualProfileGraphNativeTests.write_package

    from test_core11_energy_mixed_cold_native import MixedPhase
    _startup_services = MixedPhase._startup_services

    async def _before_loader(self, hass, root):
        await super()._before_loader(hass, root)
        target = self.installed_component / 'www/ha-entity-renamer.js'
        target.parent.mkdir()
        source = (Path(__file__).resolve().parents[1] / 'custom_components' /
                  'ha_entity_renamer' / 'www' / 'ha-entity-renamer.js')
        shutil.copyfile(source, target)
        self.public_source_sha256['www/ha-entity-renamer.js'] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),
                         self.public_source_sha256['www/ha-entity-renamer.js'])

    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix='controlled-pc-leaf-',
                                                dir=os.environ.get('RENAMER_TEST_SCRATCH', tempfile.gettempdir()))
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.dict(os.environ, {'RENAMER_POWER_COLD_PHASE': 'forward',
            'RENAMER_POWER_COLD_MODE': 'migration', 'RENAMER_POWER_COLD_CONFIG': directory.name,
            'RENAMER_POWER_COLD_META': directory.name + '/unused'}))
        await super().asyncSetUp()

    async def test_live_yaml_consumers_forward_fresh_inverse(self):
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import StatisticsTask
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.config_entries import ConfigEntry
        from types import MappingProxyType
        from homeassistant.core import Context, callback
        from homeassistant.components.conversation import async_handle_sentence_triggers
        from homeassistant.components.conversation.agent_manager import get_agent_manager
        from homeassistant.components.conversation.chat_log import ChatLog
        from homeassistant.components.conversation.default_agent import DefaultAgent
        from homeassistant.components.conversation.models import ConversationInput
        from homeassistant.components.webhook import async_handle_webhook
        from homeassistant.components.zha.helpers import get_zha_data
        from homeassistant.helpers import event as native_event
        from homeassistant.util.aiohttp import MockRequest
        from homeassistant.util.async_ import get_scheduled_timer_handles
        from zha.application.const import ZHA_EVENT
        from homeassistant.setup import async_setup_component
        from homeassistant.util import dt as dt_util
        from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
        from custom_components.ha_entity_renamer.transaction import Refused
        from ordinary_network_scope import native_dummy_mdns
        import yaml

        hass, runtime, root = self.f.hass, self._runtime(), self.f.root
        source = self.sources[10]
        old_source = source.entity_id
        leaf = self.entities['fixture-meter-10'].entity_id
        mapping = {old_source: 'light.controlled_yaml_source', leaf: 'sensor.controlled_yaml_power'}
        script = 'controlled_yaml_read'
        # Keep the synthetic coordinator offline, while giving HA's native
        # device validator the actual same-domain ConfigEntry it requires.
        zha_entry = ConfigEntry(domain='zha', title='Synthetic offline coordinator',
            unique_id='synthetic-coordinator', data={}, options={},
            discovery_keys=MappingProxyType({}), source='user', version=1,
            minor_version=1, subentries_data=[])
        hass.config_entries._entries[zha_entry.entry_id] = zha_entry
        hass.config_entries._async_schedule_save()
        ieee = '00:11:22:33:44:55:66:77'
        zha_device = self.f.device.async_get_or_create(
            config_entry_id=zha_entry.entry_id, identifiers={('zha', ieee)})
        zha_payload = {'device_ieee': ieee, 'command': 'synthetic_press', 'endpoint_id': 1}
        # The native ZHA trigger supports its own persisted trigger-cache path
        # when a coordinator is offline. The validator, attach and event
        # predicates below are unmodified production implementations.
        get_zha_data(hass).device_trigger_cache[zha_device.id] = (
            ieee, {('remote_button_short_press', 'button'): {
                'command': 'synthetic_press', 'endpoint_id': 1}})
        action_triggers = {
            'webhook': {'trigger': 'webhook', 'webhook_id': 'controlled-synthetic',
                        'allowed_methods': ['POST'], 'local_only': True},
            'conversation': {'trigger': 'conversation', 'command': ['Report synthetic meter']},
            'sun': {'trigger': 'sun', 'event': 'sunrise'},
            'time_pattern': {'trigger': 'time_pattern', 'hours': 17, 'minutes': 13, 'seconds': 19},
            'device': {'trigger': 'device', 'domain': 'zha', 'device_id': zha_device.id,
                       'type': 'remote_button_short_press', 'subtype': 'button'},
        }
        files = {domain: root / f'{domain}.yaml' for domain in ('automation', 'script', 'template')}
        conf = root / 'configuration.yaml'
        conf.write_text(conf.read_text() + ''.join(f'{domain}: !include {path.name}\n'
                                                  for domain, path in files.items()))
        read = "{{ states('" + leaf + "') | float(-1) }}"
        def action(event):
            return {'event': event, 'event_data': {'watts': read}}

        state_trigger = {'trigger': 'state', 'entity_id': leaf, 'to': ['10', '10.0', '10.00']}
        def automation(key, actions, trigger=state_trigger):
            return {'id': key, 'alias': key, 'initial_state': True,
                    'triggers': [trigger], 'actions': actions}

        # One transaction drives native state/duration, numeric and template
        # triggers plus a static event path and a separately owned actor.
        state_trigger['for'] = {'milliseconds': 25}
        state_trigger['entity_id'] = [leaf, 'sensor.controlled_yaml_unused']
        pulse_conditions = [
            {'condition': 'template', 'value_template':
                "{{ trigger.entity_id == '" + leaf + "' }}"},
            {'condition': 'numeric_state', 'entity_id': leaf, 'above': 5},
            {'condition': 'state', 'entity_id': old_source, 'state': 'on'},
        ]
        def branch_read():
            return {'choose': [{'conditions': pulse_conditions[1:],
                'sequence': [{'repeat': {'count': 1, 'sequence': [
                    {'parallel': [{'action': 'script.' + script,
                                   'data': {'power_ids': [leaf]}}]}]}}]}],
                'default': [{'wait_template': '{{ true }}'}]}
        numeric_trigger = {'trigger': 'numeric_state', 'entity_id': leaf, 'above': 5}
        template_trigger = {'trigger': 'template', 'value_template':
            "{{ expand(['" + leaf + "']) | map(attribute='state') | map('float', 0) | sum > 5 }}"}
        programs = {
            'template': [{'sensor': [{'name': 'Controlled YAML readout',
                'unique_id': 'controlled_yaml_readout',
                'state': "{{ states('" + leaf + "') | float(-1) * 2 }}"}]}],
            'automation': [{**automation('controlled_yaml_on', [
                {'delay': '{{ 120 if long | default(false) else 0 }}'},
                branch_read(), action('controlled_yaml_on_result')]), 'conditions': pulse_conditions},
                automation('controlled_yaml_numeric', [action('controlled_yaml_numeric_result')], numeric_trigger),
                automation('controlled_yaml_template', [action('controlled_yaml_template_result')], template_trigger),
                automation('controlled_yaml_action_only', [action('controlled_yaml_action_only_result')],
                           {'trigger': 'event', 'event_type': 'controlled_yaml_read_action'}),

                *(automation('controlled_yaml_' + kind,
                    [action('controlled_yaml_' + kind + '_result')], trigger)
                    for kind, trigger in action_triggers.items()),
                automation('controlled_yaml_keyed', [
                    {'event': 'controlled_yaml_keyed_result', 'event_data': {
                        'watts': '{{ states(selected) | float(-1) }}',
                        'label': '{{ labels[trigger.entity_id] }}'}}],
                    {'trigger': 'state', 'entity_id': leaf, 'to': ['10', '10.0', '10.00']})
                    | {'variables': {'labels': {leaf: 'Plain label'},
                                     'meters': {leaf: leaf},
                                     'selected': '{{ meters.get(trigger.entity_id) }}'}},
                automation('controlled_yaml_off', [action('controlled_yaml_off_result')]),
                automation('controlled_yaml_foreign', [{'event': 'controlled_yaml_foreign_result'}],
                           {'trigger': 'time_pattern', 'minutes': '/5'})],
            'script': {script: {'fields': {'power_ids': {'description': 'Optional power input'}}, 'sequence': [
                {'delay': '{{ 120 if long | default(false) else 0 }}'},
                {'variables': {'items': "{{ power_ids if power_ids is list else [power_ids] }}",
                               'local': {'input': {'power': leaf}, 'label': 'Plain label'}}},
                {'repeat': {'for_each': '{{ items }}', 'sequence': [
                    {'if': [{'condition': 'template', 'value_template': "{{ power_ids is defined }}"}],
                     'then': [{'event': 'controlled_yaml_argument_result', 'event_data':
                               {'watts': "{{ states(repeat.item) | float(-1) }}"}}]}]}},
                {'event': 'controlled_yaml_script_result', 'event_data':
                 {'watts': "{{ states(local.input.power) | float(-1) }}"}}]},

                'controlled_yaml_mixed': {'fields': {'preview': {}, 'selection': {}}, 'sequence': [
                    {'variables': {'groups': {'first': {'targets': [leaf], 'label': 'Plain label'}},
                                   'selected': "{{ groups.get(selection, {}) }}"}},
                    {'if': "{{ preview | default(false) }}", 'then': [
                        {'variables': {'result': {'targets': '{{ selected.targets }}'}}},
                        {'stop': 'preview', 'response_variable': 'result'}]},
                    {'action': 'script.' + script, 'data': {'power_ids': '{{ selected.targets }}'}},
                    {'variables': {'records': "{% set ns = namespace(values=[]) %}"
                        "{% for item in selected.targets %}"
                        "{% set ns.values = ns.values + [{'entity_id': item, 'state': states(item)}] %}"
                        "{% endfor %}{{ ns.values }}"}},
                    {'variables': {'result': {'targets': '{{ selected.targets }}', 'records': '{{ records }}'}}},
                    {'stop': 'executed', 'response_variable': 'result'}]},
                'controlled_yaml_foreign': {'sequence': [{'event': 'controlled_yaml_foreign_script'}]}},
        }
        for domain, path in files.items():
            path.write_text(yaml.safe_dump(programs[domain], sort_keys=False))
        config = await async_hass_config_yaml(hass)
        # Install the native sentence matcher without the unrelated global
        # exposure bootstrap that replaces every entity's registry object.
        agent = DefaultAgent(hass)
        await agent.async_added_to_hass()
        self.addAsyncCleanup(agent.async_will_remove_from_hass)
        await get_agent_manager(hass).async_setup_default_agent(agent)
        for domain in ('webhook', 'sun', 'template', 'script', 'automation'):
            if domain in hass.config.components:
                await hass.services.async_call(domain, 'reload', {}, blocking=True)
            else:
                self.assertTrue(await async_setup_component(hass, domain, config), domain)
        await hass.async_block_till_done()
        from homeassistant.helpers.entity_platform import async_get_platforms
        template_entities = [e for p in async_get_platforms(hass, 'template')
                             for e in p.entities.values()]
        self.assertEqual(len(template_entities), 1,
                         [(row.entity_id, row.platform) for row in self.f.entity.entities.values()
                          if row.platform == 'template'])
        template_entity, = template_entities
        sensor = template_entity.entity_id
        report = {'template_id': sensor}
        def save():
            Path('/qa/controlled-yaml-consumers.json').write_text(json.dumps(report, sort_keys=True))
        self.addCleanup(save)
        from custom_components.ha_entity_renamer import transaction
        monitor = sys.monitoring
        slot = next(i for i in range(6) if monitor.get_tool(i) is None)
        monitor.use_tool_id(slot, 'controlled YAML consumers diagnosis')
        def trace(code, offset, error):
            path = Path(code.co_filename)
            if 'ha_entity_renamer' in path.parts and isinstance(error, (transaction.Refused, ValueError,
                    AttributeError, KeyError)) and len(str(error)) < 180:
                row = (path.name, code.co_name, str(error))
                reasons = report.setdefault('reasons', [])
                if row not in reasons:
                    reasons.append(row)
                if code.co_name == '_compare_controlled_powercalc' and str(error) == (
                        'accepted_candidate_powercalc_native_graph_mismatch'):
                    locals_ = sys._getframe(1).f_locals
                    actual_graph = locals_['result']
                    expected_graph = locals_['expected']
                    def differences(before, after, path=()):
                        if type(before) is dict and type(after) is dict and before.keys() == after.keys():
                            return [item for key in before for item in differences(before[key], after[key], (*path, key))]
                        if type(before) is list and type(after) is list and len(before) == len(after):
                            return [item for index, (a, b) in enumerate(zip(before, after, strict=True))
                                    for item in differences(a, b, (*path, index))]
                        return [] if before == after else [[path, before, after]]
                    report['inverse_graph_delta'] = [differences(wanted, actual_graph.get(key))
                        for key, wanted in zip(('powercalc_sources', 'powercalc_candidate_sources'),
                                               expected_graph, strict=True)]
                    report['inverse_checker_status'] = {k: actual_graph.get(k) for k in
                        ('status', 'errors', 'warnings', 'diagnostic', 'runtime_expected')}
                    save()
        monitor.register_callback(slot, monitor.events.RAISE, trace)
        monitor.set_events(slot, monitor.events.RAISE)
        def stop():
            monitor.set_events(slot, 0)
            monitor.register_callback(slot, monitor.events.RAISE, None)
            monitor.free_tool_id(slot)
        self.addCleanup(stop)

        seen = {key: [] for key in ('on', 'off', 'script', 'argument', 'numeric',
                                     'template', 'action_only', 'keyed', *action_triggers)}
        labels = []
        for key, events in seen.items():
            @callback
            def collect(event, values=events):
                values.append(float(event.data['watts']))
                if 'label' in event.data:
                    labels.append(event.data['label'])
            self.addCleanup(hass.bus.async_listen(f'controlled_yaml_{key}_result', collect))

        def entity(domain, key):
            return next(e for e in hass.data[domain].entities if e.unique_id == key)

        stable_uuids = {(domain, obj.unique_id): obj.registry_entry.id
                        for domain in ('automation', 'script') for obj in hass.data[domain].entities}
        foreign = {domain: entity(domain, 'controlled_yaml_foreign') for domain in ('automation', 'script')}
        await entity('automation', 'controlled_yaml_off').async_turn_off()

        async def flush():
            from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
            await hass.data['powercalc_group_storage'].persist_states()
            await hass.data[DATA_RESTORE_STATE].async_dump_states()
            await self._flush_documents()
            await get_instance(hass).async_block_till_done()

        async def wait_for(predicate, detail=lambda: None):
            for _ in range(500):
                if predicate():
                    return
                await asyncio.sleep(.01)
            self.fail(f'native consumer stimulus did not settle: {detail()}')

        async def signal_action_triggers():
            # Feed a non-matching method, sentence and ZHA command first. The
            # test drives the same native ingress used by the normal actors.
            def request(method):
                return MockRequest(b'{}', 'synthetic fixture', method=method, remote='127.0.0.1')
            await async_handle_webhook(hass, 'controlled-synthetic', request('GET'))
            async def sentence(text):
                # Use the actual sentence-trigger ingress without processing
                # unrelated built-in intents, which initialize exposure
                # options on every entity and change the fixture's registry.
                user_input = ConversationInput(text, Context(), None, None, None, 'en',
                                               'conversation.home_assistant')
                return await async_handle_sentence_triggers(
                    hass, user_input, ChatLog(hass, 'synthetic-fixture'))
            self.assertIsNone(await sentence('Report unrelated fixture'))
            hass.bus.async_fire(ZHA_EVENT, {**zha_payload, 'command': 'unrelated'})
            await hass.async_block_till_done()
            self.assertFalse(any(seen[key] for key in ('webhook', 'conversation', 'device')))
            await async_handle_webhook(hass, 'controlled-synthetic', request('POST'))
            self.assertIsNotNone(await sentence('Report synthetic meter'))
            hass.bus.async_fire(ZHA_EVENT, zha_payload)
            await hass.async_block_till_done()

            # The synthetic clock advances only the two real native scheduled
            # handles. Do not invoke Automation actions or trigger closures.
            for kind, suffix in (('sun', 'handle_event'), ('time_pattern', '_pattern_time_change_listener')):
                candidates = []
                for handle in get_scheduled_timer_handles(hass.loop):
                    tracker = handle._callback
                    if (not handle.cancelled() and type(tracker) is native_event._TrackPointUTCTime
                            and tracker.job.target.__qualname__.endswith(suffix)):
                        if kind == 'time_pattern':
                            owner = tracker.job.target.__self__
                            if owner.job.name != 'track time change 17:13:19 local=True':
                                continue
                        candidates.append((handle, tracker.utc_point_in_time))
                self.assertEqual(len(candidates), 1, (kind, candidates))
                handle, when = candidates[0]
                with (patch.object(native_event, 'time_tracker_utcnow', return_value=when),
                      patch.object(native_event, 'time_tracker_timestamp', return_value=when.timestamp())):
                    handle._run()
                    handle.cancel()
                await hass.async_block_till_done()

        async def signal(label, mapping):
            active_id, power_id = (mapping.get(key, key) for key in (old_source, leaf))
            for events in seen.values():
                events.clear()
            def current():
                return tuple(None if (state := hass.states.get(key)) is None else state.state
                             for key in (active_id, power_id, sensor))

            await source.async_turn_on(brightness=255)
            await wait_for(lambda: current()[1] is not None and float(current()[1]) == 10, current)
            await source.async_turn_off()
            await wait_for(lambda: current()[0] == 'off' and current()[2] is not None
                           and float(current()[2]) != 20, current)
            for events in seen.values():
                events.clear()
            labels.clear()
            await source.async_turn_on(brightness=255)
            await wait_for(lambda: len(seen['on']) == 1 and len(seen['script']) == 1
                           and current()[2] is not None and float(current()[2]) == 20,
                           lambda: (current(), seen))
            watts = float(hass.states.get(power_id).state)
            self.assertEqual(watts, 10)
            self.assertEqual(float(hass.states.get(sensor).state), watts * 2)
            hass.bus.async_fire('controlled_yaml_read_action')
            await wait_for(lambda: len(seen['action_only']) == 1, lambda: seen)
            await signal_action_triggers()
            self.assertEqual(seen, {'on': [watts], 'off': [], 'script': [watts], 'argument': [watts],
                                    'numeric': [watts], 'template': [watts], 'action_only': [watts],
                                    'keyed': [watts], **{key: [watts] for key in action_triggers}})
            self.assertEqual(labels, ['Plain label'])
            await hass.services.async_call('script', script, {'power_ids': power_id}, blocking=True)
            self.assertEqual(seen['script'], [watts, watts])
            preview = await hass.services.async_call('script', 'controlled_yaml_mixed',
                {'selection': 'first', 'preview': True}, blocking=True, return_response=True)
            self.assertEqual(preview, {'targets': [power_id]})
            executed = await hass.services.async_call('script', 'controlled_yaml_mixed',
                {'selection': 'first', 'preview': False}, blocking=True, return_response=True)
            self.assertEqual(executed, {'targets': [power_id],
                                        'records': [{'entity_id': power_id, 'state': hass.states.get(power_id).state}]})
            self.assertEqual(seen['argument'], [watts, watts, watts])
            self.assertTrue(hass.states.is_state(active_id, 'on'))
            self.assertTrue(entity('automation', 'controlled_yaml_on').is_on)
            self.assertFalse(entity('automation', 'controlled_yaml_off').is_on)
            for domain, obj in foreign.items():
                self.assertIs(entity(domain, obj.unique_id), obj)
            self.assertEqual({(domain, obj.unique_id): obj.registry_entry.id
                              for domain in ('automation', 'script') for obj in hass.data[domain].entities},
                             stable_uuids)
            report[label] = {'ids': [active_id, power_id], 'template': hass.states.get(sensor).state,
                             'events': {key: list(items) for key, items in seen.items()}}
            save()

        native_dummy_mdns(hass)
        for light in self.sources:
            await light.async_turn_on(brightness=255)
        await hass.async_block_till_done()
        await asyncio.sleep(2)
        for light in self.sources:
            await light.async_turn_off()
        await hass.async_block_till_done()
        await signal('baseline', {})
        # Diagnostic failure in one independent actor must not erase evidence
        # for another; the strict full-domain projection still refuses.
        from custom_components.ha_entity_renamer import native_controlled_yaml_core111 as core111
        from custom_components.ha_entity_renamer.discovery import _runtime_row_diagnostics
        from custom_components.ha_entity_renamer.yaml_runtime_observer import NativeYAMLRuntimeReadback
        native_registration = core111.automation_registration_token
        rejected = entity('automation', 'controlled_yaml_action_only')
        kept = entity('automation', 'controlled_yaml_on')
        def one_failure(actor):
            if actor is rejected:
                raise ValueError('native_dynamic_trigger_not_covered')
            return native_registration(actor)
        with patch.object(core111, 'automation_registration_token', side_effect=one_failure):
            diagnostic = {row[0]: row[1:] for row in _runtime_row_diagnostics(hass, 'automation')}
            self.assertEqual(diagnostic[rejected.entity_id], ('UNKNOWN', 'native_dynamic_trigger_not_covered'))
            self.assertEqual(diagnostic[kept.entity_id], ('COMPLETE', ''))
            with self.assertRaises(ValueError):
                NativeYAMLRuntimeReadback(hass)._projection('automation')
        await flush()
        history = NativeRecorderGuard(hass)
        await history.async_synchronize()
        now = dt_util.utcnow()
        get_instance(hass).queue_task(StatisticsTask(now.replace(minute=now.minute // 5 * 5,
                                                                second=0, microsecond=0), False))
        await history.async_synchronize()
        initial = {path: path.read_bytes() for path in (*files.values(), conf, self.package)}

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        async def prepare():
            return await runtime.native_yaml_prepare(hass, mapping, actor_id='yaml-consumer-fixture',
                                                     guard=lambda: True, refresh=refresh)

        path = files['automation']
        branch = programs['automation'][0]['actions'][1]
        branch['choose'][0]['sequence'][0]['repeat']['sequence'][0]['parallel'][0]['data']['power_ids'] = '{{ unresolved }}'
        path.write_text(yaml.safe_dump(programs['automation'], sort_keys=False))
        # Reload real config; a static-disk/live mismatch alone must not
        # be the reason the dynamic reference is refused.
        await hass.services.async_call('automation', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        dynamic = path.read_bytes()
        with self.assertRaises((ValueError, Refused)) as refusal:
            await prepare()
        self.assertEqual(path.read_bytes(), dynamic)
        self.assertEqual({p: p.read_bytes() for p in initial if p is not path},
                         {p: raw for p, raw in initial.items() if p is not path})
        self.assertIs(source.registry_entry, self.f.entity.async_get(old_source))
        report['dynamic_refused'] = str(refusal.exception)
        path.write_bytes(initial[path])
        await hass.services.async_call('automation', 'reload', {}, blocking=True)
        await hass.async_block_till_done()
        await flush()

        automation_obj = entity('automation', 'controlled_yaml_on')
        script_obj = entity('script', script)
        await hass.services.async_call('automation', 'trigger',
            {'entity_id': automation_obj.entity_id, 'variables': {'long': True}}, blocking=False)
        await hass.services.async_call('script', script, {'long': True}, blocking=False)
        await wait_for(lambda: automation_obj.action_script.is_running and script_obj.script.is_running)
        report['active_before'] = True
        review = await prepare()
        op, fingerprint = review['operation_id'], review['fingerprint']
        report['review'] = review
        save()
        result = await runtime.native_yaml_apply(hass, op, fingerprint, actor_id='yaml-consumer-fixture',
                                                 guard=lambda: True, refresh=refresh)
        report['apply'] = result
        save()
        self.assertEqual(result['state'], 'applied')
        self.assertFalse(automation_obj.action_script.is_running)
        self.assertFalse(script_obj.script.is_running)
        report['active_stopped'] = True
        await signal('forward', mapping)
        await flush()
        inspected = await runtime.recovery_inspect(hass, op, fingerprint, guard=lambda: True, refresh=refresh)
        report['inspect'] = inspected
        save()
        self.assertTrue(inspected['eligible'], inspected)
        result = await runtime.recovery_restore(hass, op, fingerprint, guard=lambda: True, refresh=refresh)
        report['restore'] = result
        save()
        self.assertEqual(result['state'], 'rolled_back')
        await signal('inverse', {})
        self.assertEqual({p: p.read_bytes() for p in initial}, initial)
        native_dummy_mdns(hass)

    async def test_explicit_leaf_forward_fresh_inverse(self):
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import StatisticsTask
        from homeassistant.util import dt as dt_util
        from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
        from homeassistant.core import State, callback
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from custom_components.ha_entity_renamer.transaction import Refused
        from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
        from custom_components.ha_entity_renamer import controlled_powercalc_handoff as handoff
        from ordinary_network_scope import native_dummy_mdns

        hass, runtime = self.f.hass, self._runtime()
        original_package = self.package.read_bytes()
        pss, restore = hass.data['powercalc_group_storage'], hass.data[DATA_RESTORE_STATE]
        entry = self.entries[0]
        before_entry = dict(entry.data)
        foreign_entries = {e.entry_id: dict(e.data) for e in self.entries[1:]}
        names = {uid: entity.entity_id for uid, entity in self.entities.items()}
        uuids = {uid: entity.registry_entry.id for uid, entity in self.entities.items()}
        source = self.sources[10].entity_id
        mapping = {source: 'light.renamed_fixture_source'}
        for i in (0, 10):
            for suffix in ('', '_energy'):
                uid = f'fixture-meter-{i}{suffix}'
                mapping[names[uid]] = f'sensor.renamed_fixture_{i}{suffix}'
        expected_entry = {**before_entry, 'entity_id': mapping[source],
                          '_power_entity': mapping[before_entry['_power_entity']],
                          '_energy_entity': mapping[before_entry['_energy_entity']]}
        out = Path('/qa/controlled-pc-leaf.json')
        evidence = {}
        def save():
            out.write_text(json.dumps(evidence, sort_keys=True, default=str))

        monitor = sys.monitoring
        slot = next(i for i in range(6) if monitor.get_tool(i) is None)
        monitor.use_tool_id(slot, 'controlled Powercalc leaf diagnosis')
        def trace(code, offset, error):
            path = Path(code.co_filename)
            value = str(error)
            if 'ha_entity_renamer' in path.parts and isinstance(error, (Refused, ValueError)) and len(value) < 160:
                if code.co_name == '_need' and value == 'controlled_powercalc_handoff_setup_graph_unverified':
                    caller = sys._getframe(1).f_back
                    rows = caller.f_locals.get('first', ()) if caller else ()
                    evidence['failed_graph'] = [(r.entity_id, r.relation_kind, r.reason)
                                                for r in rows if not r.source_verified]
                    capture = caller.f_locals.get('captured') if caller else None
                    if capture:
                        evidence['setup_objects'] = [
                            {k: row[k] for k in ('type', 'entity_id', 'references', 'active_platforms')}
                            for row in json.loads(capture.payload_json)['objects']]
                row = (path.name, code.co_name, value)
                reasons = evidence.setdefault('reasons', [])
                if row not in reasons:
                    reasons.append(row)
        monitor.register_callback(slot, monitor.events.RAISE, trace)
        monitor.set_events(slot, monitor.events.RAISE)
        def stop_monitor():
            monitor.set_events(slot, 0)
            monitor.register_callback(slot, monitor.events.RAISE, None)
            monitor.free_tool_id(slot)
        self.addCleanup(stop_monitor)

        def inventory():
            return {e.unique_id: e for p in async_get_platforms(hass, 'powercalc')
                    for e in p.entities.values()}

        async def pulse():
            for light in self.sources:
                await light.async_turn_on(brightness=255)
            await hass.async_block_till_done()
            await asyncio.sleep(2)
            for light in self.sources:
                await light.async_turn_off()
            await hass.async_block_till_done()

        def counter_values(values):
            return {uid: (str(e._native_value_exact), None) if uid.startswith('fixture-group-')
                         else (str(e._state), str(e._last_valid_state))
                    for uid, e in values.items() if uid.endswith('_energy')}

        def verify_ids(moves):
            current = inventory()
            self.assertEqual(set(current), set(names))
            for uid, entity in current.items():
                self.assertEqual((entity.entity_id, entity.registry_entry.id),
                                 (moves.get(names[uid], names[uid]), uuids[uid]), uid)
            for uid, members in self.members.items():
                for suffix in ('', '_energy'):
                    self.assertEqual(current[uid + suffix]._entities, {
                        current[f'fixture-meter-{i}' + suffix].entity_id for i in members})
            return current

        removed, first_numeric, prior_pss, staged_pss = [], [], [], []
        observe_snapshot = handoff.snapshot_removed
        observe_watch = handoff._watch_restored_counters
        def capture_removed(*args, **kwargs):
            values = observe_snapshot(*args, **kwargs)
            counters = {c.entity.unique_id: c.entity for c in args[1].counters}
            removed.append(counter_values(counters))
            prior_pss.append({c.entity.unique_id: {
                c.entity_id: {name: state.as_dict() for name, state in pss.states[c.entity_id].items()}}
                for c in args[1].counters if c.kind == 'group'})
            return values

        def capture_watch(hass_arg, population):
            values, stop = observe_watch(hass_arg, population)
            by_name = {row['after_id']: next(uid for uid, value in uuids.items()
                                           if value == row['uuid']) for row in population}
            events = {}
            @callback
            def changed(event):
                state = event.data.get('new_state')
                if state is not None and state.entity_id in by_name:
                    try:
                        number = Decimal(state.state)
                    except ArithmeticError:
                        return
                    if number.is_finite():
                        events.setdefault(by_name[state.entity_id], state.state)
            remove_listener = hass_arg.bus.async_listen('state_changed', changed)
            first_numeric.append((values, events))
            staged_pss.append({by_name[row['after_id']]: {
                row['after_id']: {name: state.as_dict() for name, state in pss.states[row['after_id']].items()}}
                for row in population if row['kind'] == 'group'})
            def cleanup():
                remove_listener()
                stop()
            return values, cleanup

        self.enterContext(patch.object(handoff, 'snapshot_removed', capture_removed))
        self.enterContext(patch.object(handoff, '_watch_restored_counters', capture_watch))
        def verify_seam(index, moves):
            values, events = first_numeric[index]
            body = {uid: (row['value'], row['last_valid']) for uid, value in uuids.items()
                    for row in values.values() if row['uuid'] == value}
            evidence[f'seam_{index}'] = {'removed': removed[index], 'exact': body, 'numeric': events,
                                         'pss_removed': prior_pss[index], 'pss_staged': staged_pss[index]}
            save()
            self.assertEqual(removed[index], body)
            self.assertEqual(set(events), set(body))
            for uid, value in events.items():
                self.assertEqual(Decimal(value), round(Decimal(body[uid][0]), 6), uid)
            expected = {uid: {group: {moves.get(name, name): {**row, 'entity_id': moves.get(name, name)}
                                      for name, row in members.items()}
                              for group, members in groups.items()}
                        for uid, groups in prior_pss[index].items()}
            self.assertEqual(expected, staged_pss[index])

        async def refresh():
            return None
        guard = lambda: True
        actor = 'synthetic-leaf-actor'
        async def baseline():
            await pss.persist_states()
            await restore.async_dump_states()
            await self._flush_documents()
            await get_instance(hass).async_block_till_done()

        foreign = State('sensor.foreign_fixture_member', '3.75')
        pss.set_entity_state('sensor.foreign_fixture_group', foreign.entity_id, foreign)
        def verify_foreign():
            data = json.loads((self.f.root / '.storage/powercalc_group').read_bytes())['data']
            self.assertEqual(data['sensor.foreign_fixture_group'][foreign.entity_id]['state'], '3.75')
            self.assertEqual(self.remote_bytes(), self.remote_original)
            self.assertEqual({e.entry_id: dict(e.data) for e in self.entries[1:]}, foreign_entries)

        try:
            native_dummy_mdns(hass)
            await pulse()
            await baseline()
            history = NativeRecorderGuard(hass)
            await history.async_synchronize()
            now = dt_util.utcnow()
            period = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
            get_instance(hass).queue_task(StatisticsTask(period, False))
            await history.async_synchronize()
            foreign_power = inventory()['fixture-meter-0']
            standby = foreign_power._standby_power
            foreign_power._standby_power = Decimal('0.4')
            try:
                with self.assertRaises((ValueError, Refused)):
                    await runtime.native_yaml_prepare(hass, mapping, actor_id=actor, guard=guard, refresh=refresh)
            finally:
                foreign_power._standby_power = standby
            self.assertEqual(self.package.read_bytes(), original_package)
            self.assertEqual(dict(entry.data), before_entry)
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id=actor, guard=guard, refresh=refresh)
            evidence['review'] = review
            save()
            op, fingerprint = review['operation_id'], review['fingerprint']
            async with runtime.operation_lock:
                bound = await NativeTemplateStateServer.bind_prepared(hass, op, runtime.reviews[op].plan,
                                                                       guard=guard, refresh=refresh)
                try:
                    self.assertEqual(dict(bound.composition.mapping), mapping)
                    self.assertEqual(len(bound.composition.previous.groups), 2)
                    self.assertTrue(all(g.entity_id not in mapping for g in bound.composition.previous.groups))
                finally:
                    bound.close()
            result = await runtime.native_yaml_apply(hass, op, fingerprint, actor_id=actor, guard=guard, refresh=refresh)
            evidence['apply'] = result
            save()
            self.assertEqual(result['state'], 'applied')
            verify_seam(0, mapping)
            verify_ids(mapping)
            self.assertEqual(dict(entry.data), expected_entry)
            for old, new in mapping.items():
                if old != source:
                    self.assertNotIn(old, self.package.read_text())
                    self.assertIn(new, self.package.read_text())
            verify_foreign()
            initial = counter_values(inventory())
            await pulse()
            self.assertTrue(all(Decimal(counter_values(inventory())[uid][0]) > Decimal(initial[uid][0])
                                for uid in ('fixture-group-a_energy', 'fixture-group-b_energy')))
            await baseline()
            inspected = await runtime.recovery_inspect(hass, op, fingerprint, guard=guard, refresh=refresh)
            evidence['inspect'] = inspected
            save()
            self.assertTrue(inspected['eligible'], inspected)
            result = await runtime.recovery_restore(hass, op, fingerprint, guard=guard, refresh=refresh)
            evidence['restore'] = result
            save()
            self.assertEqual(result['state'], 'rolled_back')
            verify_seam(1, {new: old for old, new in mapping.items()})
            verify_ids({})
            self.assertEqual(dict(entry.data), before_entry)
            self.assertEqual(self.package.read_bytes(), original_package)
            verify_foreign()
            initial = counter_values(inventory())
            await pulse()
            self.assertTrue(all(Decimal(counter_values(inventory())[uid][0]) > Decimal(initial[uid][0])
                                for uid in ('fixture-group-a_energy', 'fixture-group-b_energy')))
            native_dummy_mdns(hass)
        finally:
            save()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ControlledActualGraphTests(name) for name in
        ('test_explicit_leaf_forward_fresh_inverse', 'test_live_yaml_consumers_forward_fresh_inverse'))
