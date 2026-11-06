"""Isolated ordinary review/apply/recovery with native active A/S reloads."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class ControlledReloadNativeTests(mixed.NativeTemplateMixedYAMLServerTests):
    async def test_busy_actor_dual_84b19(self):
        from homeassistant.core import Context
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers import area_registry, floor_registry, label_registry
        runtime, mapping, uuids, dependents, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        managers = []
        for module, member in ((floor_registry, 'floors'), (label_registry, 'labels'), (area_registry, 'areas')):
            manager = hass.data.get(module.DATA_REGISTRY)
            if manager is None:
                await module.async_load(hass)
                manager = hass.data[module.DATA_REGISTRY]
            elif not hasattr(manager, member):
                await manager.async_load()
            managers.append(manager)
        floors, labels, areas = managers
        floor = floors.async_create('Controlled reload floor', level=2)
        label = labels.async_create('Controlled reload label', color='blue')
        area = areas.async_create('Controlled reload room', floor_id=floor.floor_id, labels={label.label_id})
        self.f.entity.async_update_entity(next(iter(mapping)), area_id=area.id, labels={label.label_id})
        for manager in managers:
            await manager._store.async_save(manager._data_to_save())
        for name, marker in (('mixed_automations.yaml', '  actions:\n'),
                             ('mixed_scripts.yaml', '  sequence:\n')):
            path = root / name
            path.write_text(path.read_text().replace(marker, marker +
                '  - delay: "{{ 120 if long | default(false) else 0 }}"\n'))
        for domain in ('automation', 'script'):
            await hass.services.async_call(domain, 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await hass.auth.async_get_users()
        auth_store = hass.auth._store
        await auth_store._store.async_save(auth_store._data_to_save())
        await self._flush_documents()
        automation, = hass.data['automation'].entities
        await hass.services.async_call('automation', 'trigger',
            {'entity_id': automation.entity_id, 'variables': {'long': True}}, blocking=False)
        await hass.services.async_call('script', 'mixed_source', {'long': True}, blocking=False)
        script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities if e.unique_id == 'mixed_source')
        for _ in range(1000):
            if automation.action_script.is_running and script.script.is_running:
                break
            await asyncio.sleep(.001)
        evidence = {'active_before': [automation.action_script.is_running, script.script.is_running]}
        events = {'automation': [], 'script': []}
        for domain in events:
            remove = hass.bus.async_listen('mixed_yaml_' + domain + '_result',
                lambda event, domain=domain: events[domain].append(str(event.data['value'])))
            self.addCleanup(remove)
        files = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')
        def snapshot():
            from batch_readback import native_empty_auth_snapshot, native_location_snapshot
            stores = {}
            for name in ('auth', 'core.config_entries', 'core.device_registry', 'core.area_registry',
                         'core.floor_registry', 'core.label_registry'):
                path = root / '.storage' / name
                stores[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            return {'files': {name: (root / name).read_text() for name in files},
                    'registry': {row['entity_id']: row for row in
                        json.loads(_orjson_bytes_default_encoder(self.f.entity._data_to_save()))['entities']},
                    'stores': stores, 'auth': native_empty_auth_snapshot(hass, root),
                    'locations': native_location_snapshot(hass, root)}
        evidence['before'] = snapshot()
        evidence_path = Path(os.environ.get('RENAMER_CONTROLLED_EVIDENCE', '/qa/controlled-evidence.json'))
        def save():
            evidence_path.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        try:
            self.assertEqual(evidence['active_before'], [True, True])
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
                guard=lambda: None, refresh=refresh)
            evidence['review'] = review
            save()
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='native-admin', guard=lambda: None, refresh=refresh)
            evidence['apply'] = result
            evidence['after'] = snapshot()
            save()
            self.assertEqual(result['state'], 'applied')
            self.assertFalse(script.script.is_running)
            self.assertTrue(all(row['status'] == 'COMPLETE' for row in evidence['before']['locations'].values()))
            self.assertEqual(evidence['before']['locations'], evidence['after']['locations'])
            self.assertEqual(evidence['before']['stores'], evidence['after']['stores'])
            events['automation'].clear(); events['script'].clear()
            await self.stimulus(11)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            evidence['future_after'] = {key: list(value) for key, value in events.items()}
            save()
            self.assertEqual(evidence['future_after'], {'automation': ['11'], 'script': ['11']})
            for old in mapping:
                for key, row in evidence['before']['registry'].items():
                    if key != old:
                        self.assertEqual(row, evidence['after']['registry'][key])
                before_row = dict(evidence['before']['registry'][old])
                after_row = dict(evidence['after']['registry'][mapping[old]])
                for field in ('entity_id', 'modified_at'):
                    before_row.pop(field, None); after_row.pop(field, None)
                self.assertEqual(before_row, after_row)
            inspection = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            evidence['inspect'] = inspection
            save()
            self.assertTrue(inspection['eligible'])
            restored = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            evidence['restore'] = restored
            evidence['restored'] = snapshot()
            save()
            self.assertEqual(restored['state'], 'rolled_back')
            for name in files:
                self.assertEqual(evidence['before']['files'][name], evidence['restored']['files'][name])
            self.assertEqual(evidence['before']['stores'], evidence['restored']['stores'])
            self.assertEqual(evidence['before']['locations'], evidence['restored']['locations'])
            for old, new in mapping.items():
                self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
                before_row = dict(evidence['before']['registry'][old])
                restored_row = dict(evidence['restored']['registry'][old])
                before_row.pop('modified_at', None); restored_row.pop('modified_at', None)
                self.assertEqual(before_row, restored_row)
            events['automation'].clear(); events['script'].clear()
            await self.stimulus(17)
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True, context=Context())
            await hass.async_block_till_done()
            evidence['future_restored'] = {key: list(value) for key, value in events.items()}
            save()
            self.assertEqual(evidence['future_restored'], {'automation': ['17'], 'script': ['17']})
        finally:
            evidence['final'] = snapshot()
            save()

    async def test_disabled_initial_true_no_trigger_71cd2(self):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        path = root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: protected_off\n  alias: Protected off\n  initial_state: true\n'
                '  triggers:\n  - trigger: event\n    event_type: controlled_off_probe\n'
                '  actions:\n  - event: controlled_off_fired\n    event_data:\n'
                '      source: "{{ states(\'' + old + '\') }}"\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        off = next(entity for entity in hass.data['automation'].entities if entity.unique_id == 'protected_off')
        await off.async_turn_off()
        await self._flush_documents()
        fired, transitions = [], []
        self.addCleanup(hass.bus.async_listen('controlled_off_fired', lambda event: fired.append(True)))
        def state(event):
            if event.data.get('entity_id') == off.entity_id:
                new = event.data.get('new_state')
                transitions.append(None if new is None else new.state)
        self.addCleanup(hass.bus.async_listen('state_changed', state))
        evidence = {'before_enabled': off._is_enabled, 'transitions': transitions, 'fired': fired,
                    'probe_count': 0, 'probe_unavailable_count': 0, 'probe_at_add': 0}
        names = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')
        def read_files(): return {name: (root / name).read_text() for name in names}
        evidence['before_files'] = read_files()
        evidence_path = Path('/qa/disabled-evidence.json')
        def save(): evidence_path.write_text(json.dumps(evidence, default=str, sort_keys=True))
        from unittest.mock import patch
        component = hass.data['automation']
        original_add = component.async_add_entities
        async def probe_before_add(entities):
            current = hass.states.get(off.entity_id)
            if current is None or current.state == 'unavailable':
                evidence['probe_at_add'] += 1
                hass.bus.async_fire('controlled_off_probe')
                await asyncio.sleep(0)
            return await original_add(entities)
        self.enterContext(patch.object(component, 'async_add_entities', side_effect=probe_before_add))
        save()
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        stop = asyncio.Event()
        async def probe():
            while not stop.is_set():
                evidence['probe_count'] += 1
                current = hass.states.get(off.entity_id)
                if current is None or current.state == 'unavailable':
                    evidence['probe_unavailable_count'] += 1
                hass.bus.async_fire('controlled_off_probe')
                await asyncio.sleep(.01)
        task = asyncio.create_task(probe())
        try:
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin', guard=lambda: None, refresh=refresh)
            evidence['review'] = review; save()
            with mixed.native_diagnostics(self.module):
                result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='native-admin', guard=lambda: None, refresh=refresh)
            evidence['apply'] = result
            current = next(entity for entity in hass.data['automation'].entities if entity.unique_id == 'protected_off')
            evidence['after_enabled'] = current._is_enabled; save()
            self.assertEqual(result['state'], 'applied')
            self.assertFalse(current._is_enabled)
            self.assertNotIn('on', transitions)
            self.assertFalse(fired)
            self.assertGreaterEqual(evidence['probe_at_add'], 1)
            inspection = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'], guard=lambda: None, refresh=refresh)
            evidence['inspect'] = inspection; save()
            self.assertTrue(inspection['eligible'])
            restored = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'], guard=lambda: None, refresh=refresh)
            evidence['restore'] = restored
            current = next(entity for entity in hass.data['automation'].entities if entity.unique_id == 'protected_off')
            evidence['restored_enabled'] = current._is_enabled; save()
            self.assertEqual(restored['state'], 'rolled_back')
            self.assertFalse(current._is_enabled)
            self.assertNotIn('on', transitions)
            self.assertFalse(fired)
            self.assertGreaterEqual(evidence['probe_at_add'], 2)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            if 'review' in evidence:
                try:
                    evidence['exception_inspect'] = await runtime.recovery_inspect(hass,
                        evidence['review']['operation_id'], evidence['review']['fingerprint'],
                        guard=lambda: None, refresh=refresh)
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = {'type': type(inspect_error).__name__,
                        'message': str(inspect_error)}
                save()
            raise
        finally:
            stop.set(); await task
            evidence['final'] = {'fired_count': len(fired), 'transitions': list(transitions), 'files': read_files()}
            save()

    async def test_stage_probe_91dc7(self):
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        path = root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: protected_off\n  alias: Protected off\n  initial_state: true\n'
                '  triggers:\n  - trigger: event\n    event_type: controlled_off_probe\n'
                '  actions:\n  - event: controlled_off_fired\n    event_data:\n'
                '      source: "{{ states(\'' + old + '\') }}"\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        off = next(e for e in hass.data['automation'].entities if e.unique_id == 'protected_off')
        await off.async_turn_off()
        await self._flush_documents()
        evidence = {'before_file': path.read_text(), 'samples': [], 'events': []}
        output = Path('/qa/stage-probe-evidence.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        def event(row):
            old_state, new_state = row.data.get('old_state'), row.data.get('new_state')
            if row.data.get('entity_id', '').startswith('automation.'):
                evidence['events'].append({'id': row.data['entity_id'],
                    'before': None if old_state is None else old_state.state,
                    'after': None if new_state is None else new_state.state})
        self.addCleanup(hass.bus.async_listen('state_changed', event))
        transition = self.module('native_automation_transition').NativeAutomationTransition(hass, allow_running=True)
        original_check = transition.check_preserving_enabled
        def checked():
            try:
                rows = transition._entities()
                sample = {'stage': getattr(transition, 'in_preserving_stage', None),
                    'conflict': getattr(transition, 'state_conflict', None),
                    'expected': getattr(transition, 'expected_enabled', None),
                    'rows': [(key, entity._is_enabled, entity.entity_id) for key, entity in rows]}
            except BaseException as error:
                sample = {'sample_error': type(error).__name__}
            evidence['samples'].append(sample); save()
            return original_check()
        transition.check_preserving_enabled = checked
        try:
            await transition.prepare_preserving_enabled()
            evidence['prepared'] = True; save()
            path.write_text(path.read_text().replace(old, new))
            evidence['candidate_file'] = path.read_text(); save()
            await transition.stage_preserving_enabled()
            evidence['completed'] = True; save()
            current = next(e for e in hass.data['automation'].entities if e.unique_id == 'protected_off')
            self.assertFalse(current._is_enabled)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            raise
        finally:
            transition.close_preserving_enabled()
            evidence['final_file'] = path.read_text(); save()

    async def test_automation_state_listener_thread_8ab(self):
        """Read HA job dispatch and observed listener thread, without migration."""
        import inspect
        import sys
        import threading
        from types import CodeType
        from homeassistant.core import EventBus
        _, _, _, _, _ = await self.data()
        hass = self.f.hass
        entity = next(iter(hass.data['automation'].entities))
        transition = self.module('native_automation_transition').NativeAutomationTransition(hass, allow_running=True)
        code = next(item for item in transition.prepare_preserving_enabled.__func__.__code__.co_consts
                    if isinstance(item, CodeType) and item.co_name == 'observed')
        slot = next(i for i in range(6) if sys.monitoring.get_tool(i) is None)
        sys.monitoring.use_tool_id(slot, 'automation listener dispatch probe')
        rows = []
        def observed(current, offset):
            frame = sys._getframe(1)
            event = frame.f_locals['event']
            if event.data.get('entity_id') == entity.entity_id:
                rows.append({'thread': threading.get_ident(), 'owner_thread': transition.thread})
        sys.monitoring.register_callback(slot, sys.monitoring.events.PY_START, observed)
        sys.monitoring.set_local_events(slot, code, sys.monitoring.events.PY_START)
        result = {'observations': rows, 'eventbus_listen_source': inspect.getsource(EventBus.async_listen)}
        output = Path('/qa/automation-listener-thread.json')
        def save(): output.write_text(json.dumps(result, sort_keys=True, default=str))
        save()
        try:
            await transition.prepare_preserving_enabled()
            if entity._is_enabled:
                await entity.async_turn_off()
            else:
                await entity.async_turn_on()
            await hass.async_block_till_done()
            save()
            self.assertTrue(rows)
            self.assertTrue(all(row['thread'] == row['owner_thread'] for row in rows))
        finally:
            transition.close_preserving_enabled()
            sys.monitoring.set_local_events(slot, code, 0)
            sys.monitoring.register_callback(slot, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(slot)
            save()

    async def test_stage_foreign_off_12aaf(self):
        from unittest.mock import patch
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        path = root / 'mixed_automations.yaml'
        transition = self.module('native_automation_transition').NativeAutomationTransition(hass, allow_running=True)
        evidence = {'before_file': path.read_text(), 'injected': False}
        output = Path('/qa/foreign-stage-evidence.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        await transition.prepare_preserving_enabled()
        original_add = transition.component.async_add_entities
        async def add_then_foreign(entities):
            await original_add(entities)
            entity = next(e for e in transition.component.entities if e.unique_id == 'mixed_source')
            foreign_context = Context()
            foreign = asyncio.create_task(hass.services.async_call('automation', 'turn_off',
                {'entity_id': entity.entity_id}, blocking=True, context=foreign_context))
            evidence['separate_task'] = foreign is not asyncio.current_task()
            await foreign
            evidence['injected'] = True
            evidence['foreign_enabled'] = entity._is_enabled
            save()
            await asyncio.sleep(0)
        save()
        try:
            path.write_text(path.read_text().replace(old, new))
            evidence['candidate_file'] = path.read_text(); save()
            with patch.object(transition.component, 'async_add_entities', side_effect=add_then_foreign):
                try:
                    await transition.stage_preserving_enabled()
                except BaseException as error:
                    evidence['stage_error'] = {'type': type(error).__name__, 'message': str(error)}
                    save()
                else:
                    evidence['stage_error'] = None; save()
            current = next(e for e in transition.component.entities if e.unique_id == 'mixed_source')
            evidence['after_enabled'] = current._is_enabled; save()
            self.assertTrue(evidence['injected'])
            self.assertIsNotNone(evidence['stage_error'])
            self.assertFalse(evidence['after_enabled'])
        finally:
            transition.close_preserving_enabled()
            evidence['final_file'] = path.read_text()
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source'), None)
            evidence['final_enabled'] = None if current is None else current._is_enabled
            save()

    async def test_changed_user_state_before_inverse_2e4a8(self):
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source')
        await entity.async_turn_off()
        await self._flush_documents()
        evidence = {'before_enabled': entity._is_enabled}
        output = Path('/qa/inverse-user-state.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        try:
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin', guard=lambda: None, refresh=refresh)
            evidence['review'] = review; save()
            result = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                actor_id='native-admin', guard=lambda: None, refresh=refresh)
            evidence['apply'] = result; save()
            self.assertEqual(result['state'], 'applied')
            entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source')
            self.assertFalse(entity._is_enabled)
            await hass.services.async_call('automation', 'turn_on', {'entity_id': entity.entity_id},
                blocking=True, context=Context())
            evidence['user_enabled'] = entity._is_enabled; save()
            self.assertTrue(entity._is_enabled)
            inspect = await runtime.recovery_inspect(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            evidence['inspect'] = inspect; save()
            self.assertTrue(inspect['eligible'])
            restored = await runtime.native_yaml_reverse(hass, result['operation_id'], result['fingerprint'],
                guard=lambda: None, refresh=refresh)
            evidence['restore'] = restored
            entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source')
            evidence['restored_enabled'] = entity._is_enabled; save()
            self.assertEqual(restored['state'], 'rolled_back')
            self.assertTrue(entity._is_enabled)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            if 'review' in evidence:
                try:
                    evidence['exception_inspect'] = await runtime.recovery_inspect(hass,
                        review['operation_id'], review['fingerprint'], guard=lambda: None, refresh=refresh)
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = type(inspect_error).__name__
                save()
            raise
        finally:
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source'), None)
            evidence['final_enabled'] = None if current is None else current._is_enabled
            save()

    async def test_native_own_automation_readd_probe_91b(self):
        """Observe actual Core registry re-add with a disabled initial-true automation."""
        from homeassistant.core import Context
        _, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: owned_probe\n  alias: Owned probe\n  initial_state: true\n'
                '  triggers:\n  - trigger: event\n    event_type: owned_probe_event\n'
                '  actions:\n  - event: owned_probe_fired\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
        await entity.async_turn_off()
        await self._flush_documents()
        old, new = entity.entity_id, 'automation.owned_probe_next'
        events, transitions = [], []
        self.addCleanup(hass.bus.async_listen('owned_probe_fired', lambda event: events.append(True)))
        self.addCleanup(hass.bus.async_listen('state_changed', lambda event: transitions.append(
            [event.data.get('entity_id'), None if event.data.get('new_state') is None
             else event.data['new_state'].state]) if event.data.get('entity_id') in (old, new) else None))
        registry = self.f.entity
        before = registry.async_get(old)
        evidence = {'old': old, 'new': new, 'uuid': before.id,
                    'initial_state': entity._initial_state, 'enabled_before': entity._is_enabled,
                    'events': events, 'transitions': transitions}
        output = Path('/qa/own-automation-readd-probe.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        try:
            registry.async_update_entity(old, new_entity_id=new)
            await hass.async_block_till_done()
            current = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
            evidence['same_entity'] = current is entity
            evidence['enabled_after'] = current._is_enabled
            evidence['entity_id_after'] = current.entity_id
            evidence['uuid_after'] = registry.async_get(new).id
            hass.bus.async_fire('owned_probe_event')
            await hass.async_block_till_done()
            evidence['events_after_probe'] = len(events)
            save()
            self.assertFalse(evidence['enabled_before'])
            self.assertEqual(evidence['uuid'], evidence['uuid_after'])
            self.assertEqual(evidence['entity_id_after'], new)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            raise
        finally:
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe'), None)
            evidence['final'] = {'entity_id': None if current is None else current.entity_id,
                'enabled': None if current is None else current._is_enabled,
                'events': len(events), 'transitions': list(transitions)}
            save()

    async def test_native_own_automation_readd_guard_probe_4be(self):
        """Observe scoped initial-state protection during actual Core registry re-add."""
        from homeassistant.core import Context
        _, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: owned_probe\n  alias: Owned probe\n  initial_state: true\n'
                '  triggers:\n  - trigger: event\n    event_type: owned_probe_event\n'
                '  actions:\n  - event: owned_probe_fired\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
        await entity.async_turn_off()
        await self._flush_documents()
        old, new = entity.entity_id, 'automation.owned_probe_next'
        events, transitions = [], []
        self.addCleanup(hass.bus.async_listen('owned_probe_fired', lambda event: events.append(True)))
        self.addCleanup(hass.bus.async_listen('state_changed', lambda event: transitions.append(
            [event.data.get('entity_id'), None if event.data.get('new_state') is None
             else event.data['new_state'].state]) if event.data.get('entity_id') in (old, new) else None))
        registry = self.f.entity
        before = registry.async_get(old)
        evidence = {'old': old, 'new': new, 'uuid': before.id,
                    'initial_state': entity._initial_state, 'enabled_before': entity._is_enabled,
                    'events': events, 'transitions': transitions}
        output = Path('/qa/own-automation-guard-probe.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        transition = self.module('native_automation_transition').NativeAutomationTransition(hass, allow_running=True)
        try:
            await transition.prepare_preserving_enabled()
            token = transition.begin_registry_readd(old, new, before.id)
            registry.async_update_entity(old, new_entity_id=new)
            await self.module('native_registry').NativeRegistryIO(hass).settle()
            await transition.finish_registry_readd(token)
            await hass.async_block_till_done()
            current = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
            evidence['same_entity'] = current is entity
            evidence['enabled_after'] = current._is_enabled
            evidence['entity_id_after'] = current.entity_id
            evidence['uuid_after'] = registry.async_get(new).id
            hass.bus.async_fire('owned_probe_event')
            await hass.async_block_till_done()
            evidence['events_after_probe'] = len(events)
            evidence['initial_restored'] = entity._initial_state
            save()
            self.assertFalse(evidence['enabled_before'])
            self.assertEqual(evidence['uuid'], evidence['uuid_after'])
            self.assertEqual(evidence['entity_id_after'], new)
            self.assertFalse(evidence['enabled_after'])
            self.assertEqual(evidence['events_after_probe'], 0)
            self.assertIs(evidence['initial_restored'], True)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            raise
        finally:
            transition.close_preserving_enabled()
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe'), None)
            evidence['final'] = {'entity_id': None if current is None else current.entity_id,
                'enabled': None if current is None else current._is_enabled,
                'events': len(events), 'transitions': list(transitions)}
            save()

    async def test_native_automation_registry_bridge_readd_4d9(self):
        """Exercise the native registry bridge in both directions, without a journal."""
        from homeassistant.core import Context
        _, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: owned_probe\n  alias: Owned probe\n  initial_state: true\n'
                '  triggers:\n  - trigger: event\n    event_type: owned_probe_event\n'
                '  actions:\n  - event: owned_probe_fired\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
        await entity.async_turn_off()
        await self._flush_documents()
        old, new = entity.entity_id, 'automation.owned_probe_next'
        events, transitions = [], []
        self.addCleanup(hass.bus.async_listen('owned_probe_fired', lambda event: events.append(True)))
        self.addCleanup(hass.bus.async_listen('state_changed', lambda event: transitions.append(
            [event.data.get('entity_id'), None if event.data.get('new_state') is None
             else event.data['new_state'].state]) if event.data.get('entity_id') in (old, new) else None))
        registry = self.f.entity
        before = registry.async_get(old)
        evidence = {'old': old, 'new': new, 'uuid': before.id,
                    'initial_state': entity._initial_state, 'enabled_before': entity._is_enabled,
                    'events': events, 'transitions': transitions}
        output = Path('/qa/own-automation-bridge-probe.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        transition = self.module('native_automation_transition').NativeAutomationTransition(hass, allow_running=True)
        try:
            recorder = self.module('recorder_guard').NativeRecorderGuard(hass)
            bridge = self.module('native_registry').NativeRegistryBridge(
                self.module('native_registry').NativeRegistryIO(hass), recorder,
                automation_transition=transition)
            change = await bridge.prepare('entity', before.id, {'entity_id': new}, lambda: None)
            await transition.prepare_preserving_enabled()
            evidence['apply_attempted'] = True; save()
            await bridge.apply(change, lambda: None)
            evidence['apply_observation'] = (await bridge.inspect(change)).position; save()
            await hass.async_block_till_done()
            current = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
            evidence['same_entity'] = current is entity
            evidence['enabled_after'] = current._is_enabled
            evidence['entity_id_after'] = current.entity_id
            evidence['uuid_after'] = registry.async_get(new).id
            hass.bus.async_fire('owned_probe_event')
            await hass.async_block_till_done()
            evidence['events_after_probe'] = len(events)
            evidence['initial_restored'] = entity._initial_state
            evidence['inverse_attempted'] = True; save()
            await bridge.compensate(change, lambda: None)
            evidence['inverse_observation'] = (await bridge.inspect(change)).position
            evidence['reverse_enabled'] = entity._is_enabled
            evidence['reverse_id'] = entity.entity_id
            save()
            self.assertEqual(evidence['apply_observation'], 'after')
            self.assertEqual(evidence['inverse_observation'], 'before')
            self.assertEqual(evidence['reverse_id'], old)
            self.assertFalse(evidence['reverse_enabled'])
            self.assertFalse(evidence['enabled_before'])
            self.assertEqual(evidence['uuid'], evidence['uuid_after'])
            self.assertEqual(evidence['entity_id_after'], new)
            self.assertFalse(evidence['enabled_after'])
            self.assertEqual(evidence['events_after_probe'], 0)
            self.assertIs(evidence['initial_restored'], True)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}
            if evidence.get('apply_attempted'):
                try:
                    evidence['exception_position'] = (await bridge.inspect(change)).position
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = type(inspect_error).__name__
            save()
            raise
        finally:
            transition.close_preserving_enabled()
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe'), None)
            evidence['final'] = {'entity_id': None if current is None else current.entity_id,
                'enabled': None if current is None else current._is_enabled,
                'events': len(events), 'transitions': list(transitions)}
            save()

    async def test_native_automation_direct_witness_readd_91f(self):
        """Exercise the existing rename witness with native Automation re-add."""
        from homeassistant.core import Context
        _, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: owned_probe\n  alias: Owned probe\n  initial_state: true\n'
                '  triggers:\n  - trigger: event\n    event_type: owned_probe_event\n'
                '  actions:\n  - event: owned_probe_fired\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
        await entity.async_turn_off()
        await self._flush_documents()
        old, new = entity.entity_id, 'automation.owned_probe_next'
        events, transitions = [], []
        self.addCleanup(hass.bus.async_listen('owned_probe_fired', lambda event: events.append(True)))
        self.addCleanup(hass.bus.async_listen('state_changed', lambda event: transitions.append(
            [event.data.get('entity_id'), None if event.data.get('new_state') is None
             else event.data['new_state'].state]) if event.data.get('entity_id') in (old, new) else None))
        registry = self.f.entity
        before = registry.async_get(old)
        evidence = {'old': old, 'new': new, 'uuid': before.id,
                    'initial_state': entity._initial_state, 'enabled_before': entity._is_enabled,
                    'events': events, 'transitions': transitions}
        output = Path('/qa/own-automation-witness-probe.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        transition = self.module('native_automation_transition').NativeAutomationTransition(hass, allow_running=True)
        try:
            recorder = self.module('recorder_guard').NativeRecorderGuard(hass)
            bridge = self.module('native_registry').NativeRegistryBridge(
                self.module('native_registry').NativeRegistryIO(hass), recorder,
                automation_transition=transition)
            change = await bridge.prepare('entity', before.id, {'entity_id': new}, lambda: None)
            await transition.prepare_preserving_enabled()
            witness = await self.module('native_rename_emission_classifier').NativeRenameEmissions.prepare(
                bridge.io, change, change.after, guard=lambda: None)
            evidence['apply_attempted'] = True; save()
            token = transition.begin_registry_readd(old, new, before.id)
            try:
                witness.mutate()
                await witness.finish()
                await bridge.io.settle(change)
                await transition.finish_registry_readd(token)
            finally:
                witness.close()
            await hass.async_block_till_done()
            current = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
            evidence['same_entity'] = current is entity
            evidence['enabled_after'] = current._is_enabled
            evidence['entity_id_after'] = current.entity_id
            evidence['uuid_after'] = registry.async_get(new).id
            hass.bus.async_fire('owned_probe_event')
            await hass.async_block_till_done()
            evidence['events_after_probe'] = len(events)
            evidence['initial_restored'] = entity._initial_state
            save()
            self.assertFalse(evidence['enabled_before'])
            self.assertEqual(evidence['uuid'], evidence['uuid_after'])
            self.assertEqual(evidence['entity_id_after'], new)
            self.assertFalse(evidence['enabled_after'])
            self.assertEqual(evidence['events_after_probe'], 0)
            self.assertIs(evidence['initial_restored'], True)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}
            if evidence.get('apply_attempted'):
                try:
                    evidence['exception_position'] = (await bridge.inspect(change)).position
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = type(inspect_error).__name__
            save()
            raise
        finally:
            transition.close_preserving_enabled()
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe'), None)
            evidence['final'] = {'entity_id': None if current is None else current.entity_id,
                'enabled': None if current is None else current._is_enabled,
                'events': len(events), 'transitions': list(transitions)}
            save()

    async def test_native_automation_bridge_foreign_on_9b7(self):
        """Exercise independent user ON while the native registry re-add settles."""
        from homeassistant.core import Context
        _, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        with path.open('a') as stream:
            stream.write('- id: owned_probe\n  alias: Owned probe\n  initial_state: true\n'
                '  triggers:\n  - trigger: event\n    event_type: owned_probe_event\n'
                '  actions:\n  - event: owned_probe_fired\n')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe')
        await entity.async_turn_off()
        await self._flush_documents()
        old, new = entity.entity_id, 'automation.owned_probe_next'
        events, transitions = [], []
        self.addCleanup(hass.bus.async_listen('owned_probe_fired', lambda event: events.append(True)))
        self.addCleanup(hass.bus.async_listen('state_changed', lambda event: transitions.append(
            [event.data.get('entity_id'), None if event.data.get('new_state') is None
             else event.data['new_state'].state]) if event.data.get('entity_id') in (old, new) else None))
        registry = self.f.entity
        before = registry.async_get(old)
        evidence = {'old': old, 'new': new, 'uuid': before.id,
                    'initial_state': entity._initial_state, 'enabled_before': entity._is_enabled,
                    'events': events, 'transitions': transitions}
        output = Path('/qa/own-automation-foreign-probe.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        transition = self.module('native_automation_transition').NativeAutomationTransition(hass, allow_running=True)
        try:
            recorder = self.module('recorder_guard').NativeRecorderGuard(hass)
            io = self.module('native_registry').NativeRegistryIO(hass)
            bridge = self.module('native_registry').NativeRegistryBridge(io, recorder,
                automation_transition=transition)
            change = await bridge.prepare('entity', before.id, {'entity_id': new}, lambda: None)
            await transition.prepare_preserving_enabled()
            original_settle = io.settle
            injected = False
            async def settle_with_foreign(prepared=None):
                nonlocal injected
                await original_settle(prepared)
                if transition.in_preserving_stage and not injected:
                    injected = True
                    async def foreign_on():
                        await hass.services.async_call('automation', 'turn_on',
                            {'entity_id': new}, blocking=True, context=Context())
                    await asyncio.create_task(foreign_on())
                    await hass.async_block_till_done()
            io.settle = settle_with_foreign
            evidence['apply_attempted'] = True; save()
            try:
                await bridge.apply(change, lambda: None)
            except self.module('transaction').Refused as error:
                evidence['apply_error'] = str(error)
            evidence['foreign_injected'] = injected
            evidence['actual_enabled'] = entity._is_enabled
            evidence['actual_id'] = entity.entity_id
            evidence['actual_initial'] = entity._initial_state
            evidence['exception_position'] = (await bridge.inspect(change)).position
            save()
            self.assertTrue(injected)
            self.assertEqual(evidence['apply_error'], 'automation_external_state_changed')
            self.assertTrue(evidence['actual_enabled'])
            self.assertEqual(evidence['actual_id'], new)
            self.assertIs(evidence['actual_initial'], True)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}
            if evidence.get('apply_attempted'):
                try:
                    evidence['exception_position'] = (await bridge.inspect(change)).position
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = type(inspect_error).__name__
            save()
            raise
        finally:
            transition.close_preserving_enabled()
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'owned_probe'), None)
            evidence['final'] = {'entity_id': None if current is None else current.entity_id,
                'enabled': None if current is None else current._is_enabled,
                'events': len(events), 'transitions': list(transitions)}
            save()

    async def test_controlled_own_automation_review_8f2(self):
        """Public review admission for source plus own Automation ID; no Apply."""
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source')
        await entity.async_turn_off()
        old, new = entity.entity_id, 'automation.mixed_source_renamed'
        mapping = {**mapping, old: new}
        await self._flush_documents()
        files = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')
        evidence = {'mapping': mapping, 'enabled_before': entity._is_enabled,
                    'before_files': {name: (root / name).read_text() for name in files}}
        output = Path('/qa/own-automation-review.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        try:
            evidence['review'] = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
                guard=lambda: None, refresh=refresh)
            save()
            self.assertEqual(evidence['review']['entity_count'], 2)
            self.assertFalse(entity._is_enabled)
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            raise
        finally:
            evidence['final_files'] = {name: (root / name).read_text() for name in files}
            evidence['final_enabled'] = entity._is_enabled
            evidence['journal'] = [row.status for row in await runtime.journal.all()]
            save()
        self.assertEqual(evidence['before_files'], evidence['final_files'])
        self.assertEqual(evidence['journal'], [])

    async def test_controlled_own_automation_roundtrip_9d3(self):
        """Own Automation ID + source, OFF safety, future trigger and fresh inverse."""
        from homeassistant.core import Context
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        runtime, mapping, source_uuids, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source')
        await entity.async_turn_off()
        old, new = entity.entity_id, 'automation.mixed_source_renamed'
        mapping = {**mapping, old: new}
        own_uuid = self.f.entity.async_get(old).id
        events = []
        self.addCleanup(hass.bus.async_listen('mixed_yaml_automation_result',
            lambda event: events.append(str(event.data['value']))))
        files = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')
        evidence = {'mapping': mapping, 'own_uuid': own_uuid, 'events': events,
                    'apply_attempted': False, 'inverse_attempted': False, 'snapshots': {}}
        output = Path('/qa/own-automation-roundtrip.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        async def capture(label):
            await self._flush_documents()
            registry = json.loads(_orjson_bytes_default_encoder(self.f.entity._data_to_save()))
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source'), None)
            evidence['snapshots'][label] = {'files': {name: (root / name).read_text() for name in files},
                'entity_registry': registry, 'id': None if current is None else current.entity_id,
                'enabled': None if current is None else current._is_enabled,
                'uuid': None if current is None or current.registry_entry is None else current.registry_entry.id}
            save()
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        await capture('before')
        try:
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
                guard=lambda: None, refresh=refresh)
            evidence['review'] = review; save()
            evidence['apply_attempted'] = True; save()
            with mixed.native_diagnostics(self.module):
                applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='native-admin', guard=lambda: None, refresh=refresh)
            evidence['apply'] = applied
            await capture('after')
            await self.stimulus(11)
            evidence['off_events_after'] = list(events); save()
            self.assertEqual(applied['state'], 'applied')
            self.assertFalse(evidence['snapshots']['after']['enabled'])
            self.assertEqual(evidence['snapshots']['after']['uuid'], own_uuid)
            self.assertEqual(evidence['off_events_after'], [])
            inspect = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh)
            evidence['inspect'] = inspect; save()
            self.assertTrue(inspect['eligible'])
            evidence['inverse_attempted'] = True; save()
            restored = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh)
            evidence['restore'] = restored
            await capture('restored')
            await self.stimulus(17)
            evidence['off_events_restored'] = list(events); save()
            self.assertEqual(restored['state'], 'rolled_back')
            self.assertFalse(evidence['snapshots']['restored']['enabled'])
            self.assertEqual(evidence['snapshots']['restored']['uuid'], own_uuid)
            self.assertEqual(evidence['off_events_restored'], [])
            self.assertEqual(evidence['snapshots']['before']['files'], evidence['snapshots']['restored']['files'])
            for source, uuid in source_uuids.items():
                self.assertEqual(self.f.entity.async_get(source).id, uuid)
            await hass.services.async_call('automation', 'turn_on', {'entity_id': old}, blocking=True, context=Context())
            await self.stimulus(19)
            evidence['future_events'] = list(events); save()
            self.assertIn('19', evidence['future_events'])
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            if evidence['apply_attempted']:
                try:
                    evidence['exception_inspect'] = await runtime.recovery_inspect(hass,
                        evidence['review']['operation_id'], evidence['review']['fingerprint'],
                        guard=lambda: None, refresh=refresh)
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = {'type': type(inspect_error).__name__, 'message': str(inspect_error)}
                save()
            raise
        finally:
            try:
                await capture('final')
            except BaseException as final_error:
                evidence['final_error'] = {'type': type(final_error).__name__, 'message': str(final_error)}
            save()

    async def test_controlled_own_automation_name_roundtrip_f84(self):
        """Own Automation ID and name, OFF safety, future trigger and fresh inverse."""
        from homeassistant.core import Context
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        runtime, mapping, source_uuids, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        entity = next(e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source')
        await entity.async_turn_off()
        old, new = entity.entity_id, 'automation.mixed_source_renamed'
        mapping = {**mapping, old: new}
        own_uuid = self.f.entity.async_get(old).id
        events = []
        self.addCleanup(hass.bus.async_listen('mixed_yaml_automation_result',
            lambda event: events.append(str(event.data['value']))))
        files = ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')
        evidence = {'mapping': mapping, 'own_uuid': own_uuid, 'events': events,
                    'apply_attempted': False, 'inverse_attempted': False, 'snapshots': {}}
        output = Path('/qa/own-automation-name-roundtrip.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        async def capture(label):
            await self._flush_documents()
            registry = json.loads(_orjson_bytes_default_encoder(self.f.entity._data_to_save()))
            current = next((e for e in hass.data['automation'].entities if e.unique_id == 'mixed_source'), None)
            evidence['snapshots'][label] = {'files': {name: (root / name).read_text() for name in files},
                'entity_registry': registry, 'id': None if current is None else current.entity_id,
                'enabled': None if current is None else current._is_enabled,
                'uuid': None if current is None or current.registry_entry is None else current.registry_entry.id,
                'name': None if current is None or current.registry_entry is None else current.registry_entry.name}
            save()
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        await capture('before')
        try:
            review = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin', entity_names={old: 'New automation label'},
                guard=lambda: None, refresh=refresh)
            evidence['review'] = review; save()
            evidence['apply_attempted'] = True; save()
            with mixed.native_diagnostics(self.module, sink=evidence):
                applied = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='native-admin', guard=lambda: None, refresh=refresh)
            evidence['apply'] = applied
            await capture('after')
            await self.stimulus(11)
            evidence['off_events_after'] = list(events); save()
            self.assertEqual(applied['state'], 'applied')
            self.assertFalse(evidence['snapshots']['after']['enabled'])
            self.assertEqual(evidence['snapshots']['after']['uuid'], own_uuid)
            self.assertEqual(evidence['snapshots']['after']['name'], 'New automation label')
            self.assertEqual(evidence['off_events_after'], [])
            inspect = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh)
            evidence['inspect'] = inspect; save()
            self.assertTrue(inspect['eligible'])
            evidence['inverse_attempted'] = True; save()
            restored = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh)
            evidence['restore'] = restored
            await capture('restored')
            await self.stimulus(17)
            evidence['off_events_restored'] = list(events); save()
            self.assertEqual(restored['state'], 'rolled_back')
            self.assertFalse(evidence['snapshots']['restored']['enabled'])
            self.assertEqual(evidence['snapshots']['restored']['uuid'], own_uuid)
            self.assertEqual(evidence['snapshots']['restored']['name'], evidence['snapshots']['before']['name'])
            self.assertEqual(evidence['off_events_restored'], [])
            self.assertEqual(evidence['snapshots']['before']['files'], evidence['snapshots']['restored']['files'])
            for source, uuid in source_uuids.items():
                self.assertEqual(self.f.entity.async_get(source).id, uuid)
            await hass.services.async_call('automation', 'turn_on', {'entity_id': old}, blocking=True, context=Context())
            await self.stimulus(19)
            evidence['future_events'] = list(events); save()
            self.assertIn('19', evidence['future_events'])
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            if evidence['apply_attempted']:
                try:
                    evidence['exception_inspect'] = await runtime.recovery_inspect(hass,
                        evidence['review']['operation_id'], evidence['review']['fingerprint'],
                        guard=lambda: None, refresh=refresh)
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = {'type': type(inspect_error).__name__, 'message': str(inspect_error)}
                save()
            raise
        finally:
            try:
                await capture('final')
            except BaseException as final_error:
                evidence['final_error'] = {'type': type(final_error).__name__, 'message': str(final_error)}
            save()

    async def _coverage_entry_case(self, *, affected):
        from homeassistant.config_entries import ConfigEntry
        from types import MappingProxyType
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        old = next(iter(mapping))
        value = ('{{ states("' + old + '") }}') if affected else '{{ states(variable) }}'
        entry = ConfigEntry(version=1, minor_version=1, domain='synthetic_custom',
            title='Synthetic coverage entry', source='user', unique_id='coverage-entry',
            data={'expression': value}, options={}, discovery_keys=MappingProxyType({}), subentries_data=[])
        hass.config_entries._entries[entry.entry_id] = entry
        await self._flush_documents(initialize=True)
        evidence = {'affected': affected, 'entry_id': entry.entry_id,
                    'before_files': {name: (root / name).read_text() for name in
                        ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')}}
        output = Path('/qa/coverage-entry-' + ('affected' if affected else 'unrelated') + '.json')
        def save(): output.write_text(json.dumps(evidence, default=str, sort_keys=True))
        save()
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        try:
            try:
                result = await runtime.native_yaml_prepare(hass, mapping, actor_id='native-admin',
                    guard=lambda: None, refresh=refresh)
                evidence['review'] = result
            except self.module('helper_native').UnsupportedEntryReferences as error:
                evidence['blocked_sources'] = error.sources
            save()
            if affected:
                self.assertNotIn('review', evidence)
                self.assertEqual(evidence['blocked_sources'][0]['entry_id'], entry.entry_id)
                self.assertEqual(evidence['blocked_sources'][0]['references'], [old])
            else:
                self.assertNotIn('blocked_sources', evidence)
                self.assertTrue(any(row['source'] == 'config_entry:synthetic_custom/' + entry.entry_id
                    and row['reason'] == 'dynamic_reference_unverified'
                    for row in evidence['review']['dependencies']['coverage']))
            self.assertIsNotNone(self.f.entity.async_get(old))
        finally:
            evidence['final_files'] = {name: (root / name).read_text() for name in evidence['before_files']}
            evidence['journal'] = [row.status for row in await runtime.journal.all()]
            save()
        self.assertEqual(evidence['before_files'], evidence['final_files'])
        self.assertEqual(evidence['journal'], [])

    async def test_coverage_entry_unrelated_56a(self):
        await self._coverage_entry_case(affected=False)

    async def test_coverage_entry_affected_56b(self):
        await self._coverage_entry_case(affected=True)
