"""Real numeric Template ConfigEntry and YAML/A/S in one ordinary operation."""
import asyncio
import json
import os
from pathlib import Path
import traceback
import sys
from types import MappingProxyType
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class ControlledTemplateEntryNativeTests(mixed.NativeTemplateMixedYAMLServerTests):
    async def test_registered_entry_with_yaml_and_dependents_round_trip(self):
        from homeassistant.components.template.config_flow import TemplateConfigFlowHandler
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers.entity_platform import async_get_platforms
        runtime, mapping, uuids, dependents, original_files = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
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
        # Observe the actual native method without replacing it or listeners.
        from homeassistant.config_entries import ConfigEntries
        reload_code = ConfigEntries.async_reload.__code__
        monitor = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(monitor, 'controlled template native reload count')
        reloads, automation_state_observations = [], []
        from types import CodeType
        transition_code = self.module('native_automation_transition').NativeAutomationTransition.prepare_preserving_enabled.__code__
        observed_code = next(code for code in transition_code.co_consts
                             if isinstance(code, CodeType) and code.co_name == 'observed')
        def on_reload(code, offset):
            frame = sys._getframe(1)
            if (frame.f_code is reload_code and frame.f_locals.get('self') is hass.config_entries
                    and frame.f_locals.get('entry_id') == entry.entry_id):
                reloads.append(entry.entry_id)
            elif frame.f_code is observed_code:
                event = frame.f_locals['event']
                owner = frame.f_locals['self']
                old, new = event.data.get('old_state'), event.data.get('new_state')
                automation_state_observations.append({'id': event.data.get('entity_id'),
                    'old': None if old is None else old.state,
                    'new': None if new is None else new.state,
                    'stage': owner.in_preserving_stage, 'replacing': sorted(owner.replacing_ids),
                    'watched': sorted(owner.entity_ids)})
        sys.monitoring.register_callback(monitor, sys.monitoring.events.PY_START, on_reload)
        sys.monitoring.set_local_events(monitor, reload_code, sys.monitoring.events.PY_START)
        sys.monitoring.set_local_events(monitor, observed_code, sys.monitoring.events.PY_START)
        def clear_monitor():
            sys.monitoring.set_local_events(monitor, observed_code, 0)
            sys.monitoring.set_local_events(monitor, reload_code, 0)
            sys.monitoring.register_callback(monitor, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(monitor)
        self.addCleanup(clear_monitor)
        await self._flush_documents()
        evidence = Path(os.environ['RENAMER_BATCH_EVIDENCE']) / 'controlled-entry-native.json'
        result = {'original_mapping': mapping, 'source_uuids': uuids, 'consumer_uuid': consumer_uuid,
                  'dependent_uuids': dependents, 'review': None, 'apply': None, 'inspect': None,
                  'restore': None, 'snapshots': {}, 'samples': {}, 'reloads': reloads,
                  'failure': None, 'apply_attempted': False, 'automation_state_observations': automation_state_observations}
        async def capture(label):
            await self._flush_documents()
            files = {path: (root / path).read_text() for path in original_files}
            docs = {key: json.loads((root / '.storage' / key).read_bytes()) for key in
                    ('core.config_entries', 'core.entity_registry', 'core.device_registry')}
            result['snapshots'][label] = {'files': files, 'docs': docs,
                'entry_state': entry.state.value, 'consumer_state': hass.states.get(consumer_id).state}
            evidence.write_text(json.dumps(result, sort_keys=True, default=str))
        events = {'automation': [], 'script': []}
        self.addCleanup(hass.bus.async_listen('mixed_yaml_automation_result',
            lambda event: events['automation'].append(str(event.data['value']))))
        self.addCleanup(hass.bus.async_listen('mixed_yaml_script_result',
            lambda event: events['script'].append(str(event.data['value']))))
        async def sample(label, value):
            self.producer._attr_native_value = value
            self.producer.async_write_ha_state()
            await hass.async_block_till_done()
            automation = next(row[2] for row in dependents if row[0] == 'automation')
            await hass.services.async_call('automation', 'trigger', {'entity_id': automation}, blocking=True)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True)
            await hass.async_block_till_done()
            result['samples'][label] = {'source': value, 'entry': hass.states.get(consumer_id).state,
                'automation': events['automation'][-1] if events['automation'] else None,
                'script': events['script'][-1] if events['script'] else None}
            evidence.write_text(json.dumps(result, sort_keys=True, default=str))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            await sample('before', 5)
            await capture('before')
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='controlled-entry-qa',
                guard=lambda: None, refresh=refresh)
            result['review'] = review
            evidence.write_text(json.dumps(result, sort_keys=True, default=str))
            result['apply_attempted'] = True
            evidence.write_text(json.dumps(result, sort_keys=True, default=str))
            applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='controlled-entry-qa', guard=lambda: None, refresh=refresh)
            result['apply'] = applied
            await sample('after', 13)
            await capture('after')
            self.assertEqual(applied['state'], 'applied')
            self.assertEqual(len(reloads), 1, 'one native reload during forward')
            self.assertEqual(set(review['dependencies']['domains']), {'automation', 'script', 'template'})
            refs = review['dependencies']['config_entries']
            self.assertEqual(refs, [{'entity_id': consumer_id, 'before': old, 'after': new}])
            for path, raw in original_files.items():
                self.assertEqual(result['snapshots']['after']['files'][path].encode(),
                                 raw.replace(old.encode(), new.encode()))
            self.assertEqual(self.f.entity.async_get(new).id, uuids[old])
            self.assertEqual(self.f.entity.async_get(consumer_id).id, consumer_uuid)
            self.assertIn(new, entry.options['state'])
            self.assertEqual(result['samples']['after'],
                {'source': 13, 'entry': '26', 'automation': '13', 'script': '13'})
            inspection = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh)
            result['inspect'] = inspection
            evidence.write_text(json.dumps(result, sort_keys=True, default=str))
            self.assertEqual(inspection['state'], 'applied')
            self.assertTrue(inspection['eligible'])
            restored = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh)
            result['restore'] = restored
            await sample('restore', 19)
            await capture('restore')
            self.assertEqual(restored['state'], 'rolled_back')
            self.assertEqual(len(reloads), 2, 'one native reload during inverse')
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertEqual(self.f.entity.async_get(consumer_id).id, consumer_uuid)
            self.assertIn(old, entry.options['state'])
            self.assertEqual(result['samples']['restore'],
                {'source': 19, 'entry': '38', 'automation': '19', 'script': '19'})
            for path, raw in original_files.items():
                self.assertEqual((root / path).read_bytes(), raw)
            from controlled_template_entry_oracle import assert_documents
            before = result['snapshots']['before']['docs']
            for label in ('after', 'restore'):
                assert_documents(before, result['snapshots'][label]['docs'], entry_id=entry.entry_id,
                    source_uuid=uuids[old], old=old, new=new, reverse=label == 'restore')
            from copy import deepcopy
            for fault in ('entry', 'foreign'):
                bad = deepcopy(result['snapshots']['after']['docs'])
                if fault == 'entry':
                    next(row for row in bad['core.config_entries']['data']['entries']
                         if row['entry_id'] == entry.entry_id)['title'] = 'Foreign change'
                else:
                    bad['core.device_registry']['data']['devices'].append({'id': 'foreign-added'})
                with self.assertRaises(ValueError):
                    assert_documents(before, bad, entry_id=entry.entry_id,
                        source_uuid=uuids[old], old=old, new=new, reverse=False)

        except BaseException as error:
            result['failure'] = ''.join(traceback.format_exception(error))
            if result['apply_attempted'] and result['review'] is not None:
                try:
                    result['failure_inspect'] = await runtime.recovery_inspect(hass,
                        result['review']['operation_id'], result['review']['fingerprint'],
                        guard=lambda: None, refresh=refresh)
                except BaseException as inspect_error:
                    result['failure_inspect_error'] = type(inspect_error).__name__
            raise
        finally:
            try:
                await capture('final')
            except BaseException as error:
                result['final_read_error'] = type(error).__name__
            evidence.write_text(json.dumps(result, sort_keys=True, default=str))
