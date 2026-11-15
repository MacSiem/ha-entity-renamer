"""Numeric Template entry and Script own key share one native transaction."""
import json
import os
from pathlib import Path
import sys
import traceback
from types import MappingProxyType
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class ControlledScriptEntryNativeTests(mixed.NativeTemplateMixedYAMLServerTests):
    async def test_script_entry_one_plan_round_trip(self):
        from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, source_mapping, uuids, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(source_mapping.items()))
        script_old, script_new = 'script.mixed_source', 'script.mixed_renamed'
        path = root / 'mixed_scripts.yaml'
        path.write_text(path.read_text() + '\nstatic_caller:\n  sequence:\n  - action: script.mixed_source\n')
        await hass.services.async_call('script', 'reload', {}, blocking=True)
        entry = ConfigEntry(domain='template', title='Controlled entry', data={},
            options={'template_type': 'sensor', 'name': 'Controlled entry',
                     'state': "{{ states('" + old + "') | int(0) * 2 }}"},
            version=TemplateConfigFlowHandler.VERSION, minor_version=TemplateConfigFlowHandler.MINOR_VERSION,
            source='user', discovery_keys=MappingProxyType({}), subentries_data=[], unique_id=None,
            pref_disable_polling=True)
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        self.assertIs(entry.state, ConfigEntryState.LOADED)
        consumers = [entity for platform in async_get_platforms(hass, 'template')
                     if platform.config_entry is entry for entity in platform.entities.values()]
        self.assertEqual(len(consumers), 1)
        consumer_id = consumers[0].entity_id
        consumer_uuid = self.f.entity.async_get(consumer_id).id
        script_uuid = self.f.entity.async_get(script_old).id
        events = []
        self.addCleanup(hass.bus.async_listen('mixed_yaml_script_result', lambda event: events.append(event.data)))
        output = Path(os.environ['RENAMER_BATCH_EVIDENCE']) / 'script-entry-native.json'
        evidence = {'mapping': {old: new, script_old: script_new}, 'script_uuid': script_uuid,
                    'consumer_uuid': consumer_uuid, 'snapshots': {}, 'apply_attempted': False,
                    'inverse_attempted': False}
        monitor = sys.monitoring
        monitor_slot = next(index for index in range(6) if monitor.get_tool(index) is None)
        monitor.use_tool_id(monitor_slot, 'controlled Script entry graph diagnostic')
        def observe_exception(code, offset, error):
            if code.co_name == 'verify_graph_monitor' and str(error) == 'graph-monitor-probe':
                evidence['graph_monitor_ready'] = True
                return
            if (code.co_name != '_fail' or str(error) != 'registry_receipt_input_graph_changed'
                    or not code.co_filename.endswith('/accepted_input_state.py')):
                return
            try:
                caller = sys._getframe(1).f_back
                if caller is None or caller.f_code.co_name != 'assert_current':
                    return
                values = caller.f_locals
                expected = {row[0]: row[1:] for row in values.get('expected', ())}
                current = {row[0]: row[1:] for row in values.get('current', ())}
                evidence.setdefault('input_graph_diffs', []).append({
                    'directory': values.get('directory'), 'entries': [
                        (name, expected.get(name), current.get(name))
                        for name in sorted(expected.keys() | current.keys())
                        if expected.get(name) != current.get(name)]})
            except BaseException as problem:
                evidence['graph_monitor_error'] = type(problem).__name__
        monitor.register_callback(monitor_slot, monitor.events.RAISE, observe_exception)
        monitor.set_events(monitor_slot, monitor.events.RAISE)
        def stop_monitor():
            monitor.set_events(monitor_slot, 0)
            monitor.register_callback(monitor_slot, monitor.events.RAISE, None)
            monitor.free_tool_id(monitor_slot)
        self.addCleanup(stop_monitor)
        def verify_graph_monitor():
            raise RuntimeError('graph-monitor-probe')
        with self.assertRaisesRegex(RuntimeError, 'graph-monitor-probe'):
            verify_graph_monitor()
        self.assertTrue(evidence.get('graph_monitor_ready'))
        files = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')
        def save(): output.write_text(json.dumps(evidence, sort_keys=True, default=str))
        async def capture(label):
            await self._flush_documents()
            evidence['snapshots'][label] = {
                'files': {name: (root / name).read_text() for name in files},
                'docs': {key: json.loads((root / '.storage' / key).read_bytes()) for key in
                         ('core.config_entries', 'core.entity_registry', 'core.device_registry')},
                'entry_state': entry.state.value, 'consumer_state': hass.states.get(consumer_id).state}
            save()
        async def call(value):
            self.producer._attr_native_value = value
            self.producer.async_write_ha_state()
            await hass.async_block_till_done()
            count = len(events)
            await hass.services.async_call('script', 'static_caller', {}, blocking=True)
            await hass.async_block_till_done()
            evidence.setdefault('samples', []).append({'source': value, 'consumer': hass.states.get(consumer_id).state,
                                                       'event': events[-1] if len(events) > count else None})
            save()
            self.assertGreater(len(events), count)
            self.assertEqual(hass.states.get(consumer_id).state, str(value * 2))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = None
        try:
            await call(5)
            await capture('before')
            review = await runtime.native_yaml_prepare(hass, evidence['mapping'], actor_id='script-entry-qa',
                                                       guard=lambda: None, refresh=refresh)
            evidence['review'] = review
            evidence['apply_attempted'] = True
            save()
            evidence['apply'] = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='script-entry-qa', guard=lambda: None, refresh=refresh)
            await capture('after')
            self.assertEqual(evidence['apply']['state'], 'applied')
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            self.assertEqual(self.f.entity.async_get(script_new).id, script_uuid)
            self.assertEqual(self.f.entity.async_get(consumer_id).id, consumer_uuid)
            await call(13)
            evidence['inspect'] = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                                                                 guard=lambda: None, refresh=refresh)
            save()
            self.assertTrue(evidence['inspect']['eligible'])
            evidence['inverse_attempted'] = True
            save()
            evidence['restore'] = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                                                                     guard=lambda: None, refresh=refresh)
            await capture('restored')
            self.assertEqual(evidence['restore']['state'], 'rolled_back')
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertEqual(self.f.entity.async_get(script_old).id, script_uuid)
            await call(19)
            evidence['final_inspect'] = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                                                                       guard=lambda: None, refresh=refresh)
            save()
            self.assertEqual((evidence['final_inspect']['state'], evidence['final_inspect']['reason']),
                             ('rolled_back', 'restored_verified'))
            self.assertEqual(evidence['snapshots']['before']['files'], evidence['snapshots']['restored']['files'])
        except BaseException as error:
            evidence['failure'] = ''.join(traceback.format_exception(error))
            save()
            raise
        finally:
            primary = sys.exception()
            try:
                await capture('final')
            except BaseException as error:
                evidence['final_error'] = type(error).__name__ + ':' + str(error)
                save()
                if primary is None:
                    raise
                primary.add_note('final capture: ' + type(error).__name__)
