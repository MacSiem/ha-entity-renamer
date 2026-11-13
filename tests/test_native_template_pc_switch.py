"""Core 11.1 real Powercalc input with mixed state Template consumers."""
import asyncio
import unittest

import test_controlled_powercalc_actual_native as base


@unittest.skipUnless(base.actual.fixtures.HAS_HA, 'requires pinned native HA')
class TemplatePowercalcSwitchTests(base.ControlledActualGraphTests):
    async def test_output_forward_inverse(self):
        await self._exercise(use_leaf=True)

    async def test_input_forward_inverse(self):
        await self._exercise(use_leaf=False)

    async def _exercise(self, *, use_leaf):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.setup import async_setup_component
        from ordinary_network_scope import native_dummy_mdns
        import yaml

        hass, runtime, root = self.f.hass, self._runtime(), self.f.root
        source = self.sources[10]
        old_source = source.entity_id
        leaf = self.entities['fixture-meter-10'].entity_id
        mapping = {old_source: 'light.p2_sample_source', leaf: 'sensor.p2_sample_power'}
        target = leaf if use_leaf else old_source
        foreign_signal = 'sensor.p2_foreign_signal'
        if not use_leaf:
            hass.states.async_set(foreign_signal, 'before')
        path, config = root / 'p2_template.yaml', root / 'configuration.yaml'
        config.write_text(config.read_text() + '\ntemplate: !include p2_template.yaml\n')
        templates = [{'sensor': [{'name': 'P2 total', 'unique_id': 'p2_total',
            'state': "{% set items = ['" + target + "'] %}{% set ns = namespace(total=0) %}"
                     "{% for item in items %}{% if states(item) in ['on', '10', '10.0', '10.00'] %}"
                     "{% set ns.total = ns.total + 1 %}"
                     "{% endif %}{% endfor %}{{ ns.total }}",
            'attributes': {'copy': "{{ states('" + target + "') }}", **({
                'power': "{{ states('" + leaf + "') }}",
                'foreign': "{{ states('" + foreign_signal + "') }}"} if not use_leaf else {})}}],
            'binary_sensor': [{'name': 'P2 delayed', 'unique_id': 'p2_delay',
                'availability': "{{ states('" + target + "') != 'unavailable' }}",
                'state': "{{ states('" + target + "') in ['on', '10', '10.0', '10.00'] }}", 'delay_on': '00:30:00'}],
            'switch': [{'name': 'P2 switch', 'unique_id': 'p2_switch',
                'state': "{{ states('" + target + "') in ['on', '10', '10.0', '10.00'] }}",
                'turn_on': [{'action': 'test.capture', 'target': {'entity_id': target}}],
                'turn_off': [{'action': 'test.capture', 'target': {'entity_id': target}}]},
                {'name': 'P2 foreign', 'unique_id': 'p2_foreign', 'state': '{{ true }}'}]}]
        path.write_text(yaml.safe_dump(templates, sort_keys=False))
        initial = path.read_bytes()
        checked = await async_hass_config_yaml(hass)
        if 'template' in hass.config.components:
            await hass.services.async_call('template', 'reload', {}, blocking=True)
        else:
            self.assertTrue(await async_setup_component(hass, 'template', checked))
        await hass.async_block_till_done()
        await self._flush_documents()
        rows = {e.unique_id: e for p in async_get_platforms(hass, 'template') for e in p.entities.values()}
        sensor, binary, switch, foreign = (rows[k] for k in ('p2_total', 'p2_delay', 'p2_switch', 'p2_foreign'))
        ids = {e.unique_id: e.registry_entry.id for e in (sensor, binary, switch, foreign)}
        events = []
        async def capture(call):
            events.append(call.data['entity_id'])
        hass.services.async_register('test', 'capture', capture)
        self.addCleanup(hass.services.async_remove, 'test', 'capture')
        native_dummy_mdns(hass)
        for light in self.sources:
            await light.async_turn_on(brightness=255)
        await hass.async_block_till_done()
        await asyncio.sleep(2)
        for light in self.sources:
            await light.async_turn_off()
        await hass.async_block_till_done()
        await source.async_turn_on(brightness=255)
        for _ in range(100):
            if binary._delay_cancel is not None and hass.states.get(leaf) is not None:
                break
            await asyncio.sleep(.05)
        token = binary._delay_cancel
        self.assertIsNotNone(token)
        deadline = token.__self__.when()
        before = [hass.states.get(e.entity_id) for e in (sensor, binary, switch, foreign)]
        await switch.async_turn_on()
        from custom_components.ha_entity_renamer.recorder_guard import NativeRecorderGuard
        history = NativeRecorderGuard(hass)
        await history.async_synchronize()
        # Flush the native short-term statistics cycle before the immutable
        # history snapshot, matching the existing mixed Powercalc fixture.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.tasks import StatisticsTask
        from homeassistant.util import dt as dt_util
        now = dt_util.utcnow()
        get_instance(hass).queue_task(StatisticsTask(now.replace(minute=now.minute // 5 * 5,
            second=0, microsecond=0), False))
        await history.async_synchronize()
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='p2-fixture',
            guard=lambda: True, refresh=refresh)
        op, fingerprint = review['operation_id'], review['fingerprint']
        import sys
        from pathlib import Path
        from custom_components.ha_entity_renamer.transaction import Refused
        monitor = sys.monitoring
        slot = next(i for i in range(6) if monitor.get_tool(i) is None)
        monitor.use_tool_id(slot, 'P2 native diagnostics')
        reasons = []
        def trace(code, offset, error):
            if 'ha_entity_renamer' in Path(code.co_filename).parts and isinstance(error, (Refused, ValueError, AttributeError, KeyError)) and len(str(error)) < 180:
                row = (Path(code.co_filename).name, code.co_name, str(error))
                if row not in reasons:
                    reasons.append(row)
                if code.co_name == '_need' and str(error) == 'template_yaml_server_history_identity_changed':
                    caller = sys._getframe(1).f_back
                    loc = caller.f_locals
                    metadata, snapshot = loc.get('metadata', {}), loc.get('snapshot', {})
                    reasons.append(('history_ids', loc.get('ids'), snapshot.get('state_ids'),
                        sorted(v['metadata_id'] for v in metadata.get('states', {}).values()),
                        snapshot.get('statistic_ids'),
                        sorted(v['id'] for v in metadata.get('statistics', {}).values())))
        monitor.register_callback(slot, monitor.events.RAISE, trace)
        from homeassistant.components.template.binary_sensor import StateBinarySensorEntity
        delay_events = []
        from custom_components.ha_entity_renamer.native_template_powercalc_ingress import PowercalcTemplateIngress
        def nested(code):
            for value in code.co_consts:
                if hasattr(value, 'co_consts'):
                    yield value
                    yield from nested(value)
        action_code = next(code for code in nested(PowercalcTemplateIngress.install.__func__.__code__) if code.co_name == 'action')
        reload_code = hass.services.async_services()['powercalc']['reload'].job.target.__code__
        self.assertEqual(reload_code.co_name, '_reload_config')
        self.assertIn('custom_components/powercalc', reload_code.co_filename)
        reload_active = False
        foreign_events = []
        if not use_leaf:
            from homeassistant.core import callback
            @callback
            def foreign_observer(event):
                if event.data.get('entity_id') == foreign_signal:
                    foreign_events.append((reload_active, event.data.get('new_state')))
            remove_foreign = hass.bus.async_listen('state_changed', foreign_observer)
            self.addCleanup(remove_foreign)
        def fire_foreign():
            if reload_active:
                hass.states.async_set(foreign_signal, 'during_reload')
        def state_start(code, offset):
            nonlocal reload_active
            local = sys._getframe(1).f_locals
            if code is reload_code and not use_leaf:
                reload_active = True
                hass.loop.call_soon(fire_foreign)
            if code is action_code:
                ev = local['event']
                delay_events.append(('ingress', ev.data.get('entity_id'), str(ev.data.get('old_state')), str(ev.data.get('new_state')), id(ev.data.get('new_state')) in local['obj'].witnessed))
            if local.get('self') is binary:
                delay_events.append((local.get('result'), hass.loop.time(),
                    None if binary._delay_cancel is None else binary._delay_cancel.__self__.when(),
                    str(hass.states.get(leaf)), str(hass.states.get(mapping[leaf]))))
        def reload_return(code, offset, value):
            nonlocal reload_active
            if code is reload_code:
                reload_active = False
        monitor.register_callback(slot, monitor.events.PY_START, state_start)
        monitor.register_callback(slot, monitor.events.PY_RETURN, reload_return)
        monitor.set_local_events(slot, StateBinarySensorEntity._update_state.__code__, monitor.events.PY_START)
        monitor.set_local_events(slot, action_code, monitor.events.PY_START)
        if not use_leaf:
            monitor.set_local_events(slot, reload_code, monitor.events.PY_START | monitor.events.PY_RETURN)
        monitor.set_events(slot, monitor.events.RAISE)
        try:
            result = await runtime.native_yaml_apply(hass, op, fingerprint, actor_id='p2-fixture',
                guard=lambda: True, refresh=refresh)
        except Exception as error:
            self.fail(f'{error}: {reasons[-25:]}')
        finally:
            monitor.set_events(slot, 0)
            monitor.set_local_events(slot, StateBinarySensorEntity._update_state.__code__, 0)
            monitor.set_local_events(slot, action_code, 0)
            if not use_leaf:
                monitor.set_local_events(slot, reload_code, 0)
            monitor.register_callback(slot, monitor.events.PY_START, None)
            monitor.register_callback(slot, monitor.events.PY_RETURN, None)
            monitor.register_callback(slot, monitor.events.RAISE, None)
            monitor.free_tool_id(slot)
        self.assertEqual(result['state'], 'applied')
        if not use_leaf:
            self.assertEqual(len(foreign_events), 1)
            self.assertTrue(foreign_events[0][0], 'foreign state must occur inside vendor reload')
            self.assertEqual(foreign_events[0][1].state, 'during_reload')
            self.assertTrue(any(row[0] == 'ingress' and row[1] == foreign_signal and not row[4]
                                for row in delay_events), delay_events)
            self.assertEqual(hass.states.get(sensor.entity_id).attributes['foreign'], 'during_reload')
        self.assertIs(binary._delay_cancel, token, delay_events)
        self.assertEqual(token.__self__.when(), deadline)
        self.assertIs(rows['p2_foreign'], foreign)
        self.assertEqual({e.unique_id: e.registry_entry.id for e in (sensor, binary, switch, foreign)}, ids)
        for e, state in zip((sensor, binary, switch), before[:3], strict=True):
            current = hass.states.get(e.entity_id)
            attributes = dict(state.attributes)
            if e is sensor and not use_leaf:
                attributes['foreign'] = 'during_reload'
            self.assertEqual((current.state, dict(current.attributes)), (state.state, attributes))
        self.assertIs(hass.states.get(foreign.entity_id), before[3])
        await switch.async_turn_on()
        await switch.async_turn_off()
        self.assertEqual(events, [[target], [mapping[target]], [mapping[target]]])
        result = await runtime.recovery_restore(hass, op, fingerprint, guard=lambda: True, refresh=refresh)
        self.assertEqual(result['state'], 'rolled_back')
        self.assertEqual(path.read_bytes(), initial)
        self.assertIs(binary._delay_cancel, token)
        self.assertEqual(token.__self__.when(), deadline)
        await switch.async_turn_off()
        self.assertEqual(events[-1], [target])
        # A real subsequent false is not a structural restart publication.
        # It must reach the native tracker and cancel the held delay.
        await source.async_turn_off()
        for _ in range(200):
            current = hass.states.get(sensor.entity_id)
            if binary._delay_cancel is None and current is not None and float(current.state) == 0:
                break
            await asyncio.sleep(.05)
        self.assertIsNone(binary._delay_cancel)
        self.assertEqual(float(hass.states.get(sensor.entity_id).state), 0)
        self.assertFalse(switch.is_on)
        await source.async_turn_on(brightness=255)
        for _ in range(200):
            current = hass.states.get(sensor.entity_id)
            if current is not None and float(current.state) == 1 and switch.is_on:
                break
            await asyncio.sleep(.05)
        self.assertEqual(float(hass.states.get(sensor.entity_id).state), 1)
        self.assertTrue(switch.is_on)
