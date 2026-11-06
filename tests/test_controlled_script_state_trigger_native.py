"""Real native state-trigger behavior around Script own-ID rename/restore."""
import asyncio
import json
from pathlib import Path
import unittest

import test_native_template_mixed_yaml_server as mixed


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class ControlledScriptStateTrigger(mixed.NativeTemplateMixedYAMLServerTests):
    async def test_state_trigger_enabled_and_disabled_roundtrip_z38(self):
        from homeassistant.core import Context
        from homeassistant.helpers.json import _orjson_bytes_default_encoder
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        scripts, automations = root / 'mixed_scripts.yaml', root / 'mixed_automations.yaml'
        scripts.write_text(scripts.read_text().replace('  sequence:\n', '  sequence:\n'
            '  - delay: "{{ 120 if long | default(false) else 0 }}"\n', 1)
            + '\nstatic_caller:\n  sequence:\n  - action: script.mixed_source\n')
        for suffix in ('on', 'off'):
            automations.write_text(automations.read_text() + f'''
- id: script_watch_{suffix}
  alias: Script watch {suffix}
  initial_state: true
  trigger:
    - platform: state
      entity_id: script.mixed_source
  action:
    - event: script_watch_{suffix}_fired
''')
        await hass.services.async_call('script', 'reload', {}, blocking=True, context=Context())
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.services.async_call('automation', 'turn_off',
            {'entity_id': 'automation.script_watch_off'}, blocking=True)
        await hass.async_block_till_done()
        await self._flush_documents()
        entity = next(e for e in hass.data['script'].entities if e.unique_id == 'mixed_source')
        old, new = entity.entity_id, 'script.state_trigger_renamed'
        uuid = self.f.entity.async_get(old).id
        events = {'on': [], 'off': []}
        removers = [hass.bus.async_listen('script_watch_' + name + '_fired',
                    lambda event, name=name: events[name].append(event.event_type)) for name in events]
        output = Path('/qa/script-state-trigger.json')
        evidence = {'apply_attempted': False, 'inverse_attempted': False, 'uuid': uuid}
        def save(): output.write_text(json.dumps(evidence, sort_keys=True, default=str))
        def snapshot():
            return {'registry': json.loads(_orjson_bytes_default_encoder(self.f.entity._data_to_save())),
                'files': {name: (root / name).read_text() for name in
                    ('configuration.yaml', 'template.yaml', 'mixed_automations.yaml', 'mixed_scripts.yaml')},
                'on': hass.states.get('automation.script_watch_on').state,
                'off': hass.states.get('automation.script_watch_off').state,
                'events': {key: list(value) for key, value in events.items()}}
        def clear():
            for value in events.values(): value.clear()
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        review = None
        try:
            await hass.services.async_call('script', 'mixed_source', {'long': True}, blocking=False)
            for _ in range(1000):
                if entity.script.is_running: break
                await asyncio.sleep(.001)
            await asyncio.sleep(.05); clear()
            evidence['before'] = snapshot(); evidence['active_before'] = entity.script.is_running; save()
            review = await runtime.native_yaml_prepare(hass, {**mapping, old: new}, actor_id='native-admin',
                guard=lambda: None, refresh=refresh)
            evidence['review'] = review; save()
            evidence['apply_attempted'] = True; save()
            with mixed.native_diagnostics(self.module, sink=evidence):
                evidence['apply'] = await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                    actor_id='native-admin', guard=lambda: None, refresh=refresh)
            save()
            if evidence['apply'].get('state') != 'applied':
                raise RuntimeError('apply did not complete: ' + str(evidence['apply'].get('state')))
            await asyncio.sleep(.05)
            evidence['after'] = snapshot(); save()
            clear()
            await hass.services.async_call('script', 'static_caller', {}, blocking=True)
            await asyncio.sleep(.05)
            evidence['forward_stimulus'] = {key: len(value) for key, value in events.items()}; save()
            clear()
            evidence['inspect'] = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                guard=lambda: None, refresh=refresh); save()
            if evidence['inspect'].get('eligible') is True:
                evidence['inverse_attempted'] = True; save()
                evidence['inverse'] = await runtime.native_yaml_reverse(hass, review['operation_id'], review['fingerprint'],
                    guard=lambda: None, refresh=refresh); save()
                await asyncio.sleep(.05)
                evidence['restored'] = snapshot(); save()
                evidence['restored_inspect'] = await runtime.recovery_inspect(hass, review['operation_id'], review['fingerprint'],
                    guard=lambda: None, refresh=refresh); save()
                clear()
                await hass.services.async_call('script', 'static_caller', {}, blocking=True)
                await asyncio.sleep(.05)
                evidence['restored_stimulus'] = {key: len(value) for key, value in events.items()}; save()
            self.assertTrue(evidence['active_before'])
            self.assertEqual((evidence['before']['on'], evidence['before']['off']), ('on', 'off'))
            self.assertEqual((evidence['after']['on'], evidence['after']['off']), ('on', 'off'))
            self.assertEqual(evidence['after']['events'], {'on': [], 'off': []})
            self.assertGreater(evidence['forward_stimulus']['on'], 0)
            self.assertEqual(evidence['forward_stimulus']['off'], 0)
            self.assertTrue(evidence['inspect']['eligible'])
            self.assertEqual(evidence['inverse']['state'], 'rolled_back')
            self.assertEqual((evidence['restored']['on'], evidence['restored']['off']), ('on', 'off'))
            self.assertEqual(evidence['restored']['events'], {'on': [], 'off': []})
            self.assertGreater(evidence['restored_stimulus']['on'], 0)
            self.assertEqual(evidence['restored_stimulus']['off'], 0)
            self.assertEqual(evidence['restored_inspect']['reason'], 'restored_verified')
            self.assertEqual(self.f.entity.async_get(old).id, uuid)
            self.assertIsNone(self.f.entity.async_get(new))
            self.assertEqual(evidence['before']['files'], evidence['restored']['files'])
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            if evidence['apply_attempted'] and not evidence['inverse_attempted'] and review is not None:
                try:
                    evidence['exception_inspect'] = await runtime.recovery_inspect(hass, review['operation_id'],
                        review['fingerprint'], guard=lambda: None, refresh=refresh)
                except BaseException as inspect_error:
                    evidence['exception_inspect_error'] = {'type': type(inspect_error).__name__, 'message': str(inspect_error)}
                save()
            raise
        finally:
            try:
                evidence['final'] = snapshot()
            except BaseException as error:
                evidence['final_error'] = {'type': type(error).__name__, 'message': str(error)}
                if 'primary_error' not in evidence:
                    save(); raise
            finally:
                for remove in removers: remove()
            save()

    async def test_interrupted_cutover_restores_native_trigger_h71(self):
        """One injected failure after actual detach; verify trigger by stimulus."""
        from unittest.mock import patch
        from homeassistant.core import Context
        runtime, mapping, _, _, _ = await self.data()
        hass, root = self.f.hass, self.f.root
        path = root / 'mixed_automations.yaml'
        path.write_text(path.read_text() + '''
- id: interrupted_watch
  alias: Interrupted watch
  initial_state: true
  trigger:
    - platform: state
      entity_id: script.mixed_source
  action:
    - event: interrupted_watch_fired
''')
        await hass.services.async_call('automation', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        await self._flush_documents()
        actor = next(e for e in hass.data['automation'].entities if e.unique_id == 'interrupted_watch')
        entity = next(e for e in hass.data['script'].entities if e.unique_id == 'mixed_source')
        old, new = entity.entity_id, 'script.interrupted_target'
        events = []
        remove_listener = hass.bus.async_listen('interrupted_watch_fired', lambda event: events.append(event.event_type))
        cancelled = getattr(self, '_inject_cancel', False)
        foreign_gap = getattr(self, '_inject_foreign_gap', False)
        output = Path('/qa/script-foreign-gap.json' if foreign_gap else '/qa/script-cancelled-trigger.json' if cancelled else '/qa/script-interrupted-trigger.json')
        evidence = {'apply_attempted': False, 'inverse_attempted': False, 'events': events}
        def save(): output.write_text(json.dumps(evidence, sort_keys=True, default=str))
        def observe():
            state = hass.states.get(actor.entity_id)
            return {'state': None if state is None else state.state, 'enabled': actor._is_enabled,
                    'attached': actor._async_detach_triggers is not None,
                    'owner_same': hass.data['automation'].get_entity(actor.entity_id) is actor,
                    'events': list(events)}
        async def refresh(): self.assertTrue(runtime.operation_lock.locked())
        async def fail_after_suspend(cutover):
            evidence['injected_boundary'] = observe()
            evidence['injected_error'] = {'type': 'CancelledError' if cancelled else 'RuntimeError',
                                          'message': 'injected_after_native_detach'}
            save()
            if cancelled:
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            raise RuntimeError('injected_after_native_detach')
        review = None
        try:
            evidence['before'] = observe(); save()
            review = await runtime.native_yaml_prepare(hass, {**mapping, old: new}, actor_id='native-admin',
                guard=lambda: None, refresh=refresh)
            evidence['review'] = review; save()
            cutover_module = self.module('controlled_script_cutover')
            cutover_type = cutover_module.ControlledScriptCutover
            original_finish = cutover_module._finish_native_call
            foreign_tasks = []
            async def intercept(operation, fence, after=None):
                if foreign_gap and getattr(operation, '__self__', None) is actor and getattr(operation, '__name__', '') == '_async_enable':
                    evidence['gap_parent_before_scheduling'] = observe()
                    evidence['native_enable_entered'] = False; save()
                    async def foreign_off():
                        await actor.async_turn_off(stop_actions=False)
                        evidence['gap_foreign_completed'] = observe(); save()
                    async def observed_enable():
                        evidence['native_enable_entered'] = True; save()
                        return await operation()
                    foreign_tasks.append(asyncio.create_task(foreign_off()))
                    return await original_finish(observed_enable, fence, after)
                return await original_finish(operation, fence, after)
            evidence['apply_attempted'] = True; save()
            try:
                with patch.object(cutover_type, 'remove', fail_after_suspend), patch.object(cutover_module, '_finish_native_call', intercept):
                    await runtime.native_yaml_apply(hass, review['operation_id'], review['fingerprint'],
                        actor_id='native-admin', guard=lambda: None, refresh=refresh)
            except BaseException as error:
                evidence['apply_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            if foreign_tasks:
                await asyncio.gather(*foreign_tasks)
                evidence['foreign_turn_off_completed'] = True
            evidence['after_error'] = observe(); save()
            try:
                evidence['inspect'] = await runtime.recovery_inspect(hass, review['operation_id'],
                    review['fingerprint'], guard=lambda: None, refresh=refresh)
            except BaseException as error:
                evidence['inspect_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
            await hass.services.async_call('script', 'mixed_source', {}, blocking=True)
            await asyncio.sleep(.05)
            evidence['after_stimulus'] = observe(); save()
            self.assertIn('apply_error', evidence)
            self.assertFalse(evidence['injected_boundary']['enabled'])
            self.assertFalse(evidence['injected_boundary']['attached'])
            self.assertEqual(evidence['after_error']['state'], 'off' if foreign_gap else 'on')
            self.assertEqual(evidence['after_error']['enabled'], not foreign_gap)
            self.assertEqual(evidence['after_error']['attached'], not foreign_gap)
            self.assertTrue(evidence['after_error']['owner_same'])
            if foreign_gap:
                self.assertTrue(evidence['foreign_turn_off_completed'])
                self.assertEqual(evidence['gap_parent_before_scheduling']['state'], 'on')
                self.assertEqual(evidence['gap_foreign_completed']['state'], 'off')
                self.assertFalse(evidence['native_enable_entered'])
                self.assertEqual(len(evidence['after_stimulus']['events']), 0)
            else:
                self.assertGreater(len(evidence['after_stimulus']['events']), 0)
            self.assertFalse(evidence['inverse_attempted'])
        except BaseException as error:
            evidence['primary_error'] = {'type': type(error).__name__, 'message': str(error)}; save()
            raise
        finally:
            try: evidence['final'] = observe()
            except BaseException as error: evidence['final_error'] = {'type': type(error).__name__, 'message': str(error)}
            remove_listener(); save()

    async def test_cancelled_cutover_restores_native_trigger_j72(self):
        self._inject_cancel = True
        await self.test_interrupted_cutover_restores_native_trigger_h71()

    async def test_foreign_off_between_check_and_child_native_m73(self):
        self._inject_foreign_gap = True
        await self.test_interrupted_cutover_restores_native_trigger_h71()
