"""Owner FIFO actual-native data-plane tests, not full product forward/AC.

No fake Plan, receipt, source-evidence, output, State, clock or expected cache
seed. Independent control/observers retained from qualified primitive. Root
authorization integration is intentionally outside this data-plane fixture.
"""
import asyncio
import importlib
import sys
import unittest

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateBindingTransportTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare

    async def test_actual_module_transports_history_before_real_registry_rename(self):
        await self._probe()

    async def test_available_group_new_id_absent_keeps_complete_native_infos(self):
        await self._probe(availability=True)

    async def _probe(self, *, availability=False):
        from homeassistant.const import __version__
        from homeassistant.core import Context
        from homeassistant.helpers.entity import Entity
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.event import (
            TrackTemplate, TrackTemplateResultInfo, async_track_template_result)
        from homeassistant.helpers.template import Template, TemplateStateFromEntityId

        module = importlib.import_module(server.activation.fixture.fixtures.PACKAGE
            + '.native_template_binding_transport')
        self.assertIn(__version__, ('2025.6.0', '2026.10.4'))
        # Data-only real server fixture, never server.data()/flow()/a product Plan.
        await self.prepare('yaml', variant='sensor', validate=False)
        self.assertFalse(any(hasattr(self, key) for key in ('plan', 'receipt', 'evidence')))
        hass, root = self.f.hass, self.f.root
        control_source = type(self.producer)()
        control_source._attr_unique_id = 'this-rebind-control-source'
        control_source._attr_name = 'This rebind control source'
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([control_source])
        old, new = self.source_id, 'sensor.this_rebind_renamed_source'
        self.assertIsNone(self.f.entity.async_get(new))
        self.assertIsNone(hass.states.get(new))
        source_uuid = self.f.entity.async_get(old).id

        def definition(uid, name, source):
            return ('  - unique_id: ' + uid + '\n    name: ' + name + '\n'
                    '    state: "{{ states(\'' + source + '\') | int(0) * 2 }}"\n'
                    + ('    availability: \"{{ states(\'' + source + '\') | int(0) > 0 }}\"\n' if availability else '')
                    + '    icon: "{{ \'mdi:numeric-\' ~ states(\'' + source + '\') }}"\n'
                    + '    picture: "{{ \'/local/value-\' ~ states(\'' + source + '\') ~ \'.png\' }}"\n'
                    + '    attributes:\n'
                    '      count: "{{ this.attributes.get(\'count\', 0) + 1 }}"\n')

        yaml_path = root / 'template.yaml'
        original = ('- unique_id: scope\n  sensor:\n'
                    + definition('value', 'Runtime consumer', old)
                    + definition('control', 'This rebind control', control_source.entity_id)).encode()
        yaml_path.write_bytes(original)
        # Only bootstrap reload; it is completed BEFORE the observed operation.
        await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        entities = {e.unique_id: e for p in async_get_platforms(hass, 'template')
                    if p.domain == 'sensor' for e in p.entities.values()}
        consumer, control = entities['scope-value'], entities['scope-control']
        cid, bid = consumer.entity_id, control.entity_id

        async def emit(source, value):
            source._attr_native_value = value
            source.async_write_ha_state()
            await hass.async_block_till_done()
            # Minimum Core's task drain yields once but does not track native
            # synchronous call_soon callbacks. Let the genuine finite self-loop
            # settle (native limiter is len(_template_attrs)), without invoking
            # a handler, changing a clock, cache, State or limiter field.
            for _ in range(2 * max(len(e._template_attrs) for e in (consumer, control)) + 2):
                await asyncio.sleep(0)
            await hass.async_block_till_done()

        # Establish both histories through real source events, never fields/seeds.
        await emit(self.producer, 3)
        await emit(control_source, 3)
        self.assertEqual(hass.states.get(cid).state, hass.states.get(bid).state)
        physical = hass.states.get(cid)
        before_count = physical.attributes['count']
        control_before_count = hass.states.get(bid).attributes['count']
        before_fields = (consumer._attr_native_value, dict(consumer._attr_extra_state_attributes))
        old_tracker = consumer._template_result_info
        # Actual prior results are migration DATA. They do not authorize a
        # render or stand in for the independently observed control trace.
        # This distinct product operation explicitly transports the native tracker cache;
        # the original no-transfer counterexample remains a separate test.
        carried_results = dict(old_tracker._last_result)
        self.assertGreaterEqual(len(consumer._template_attrs), 2)
        cleanup_index = consumer._on_remove.index(old_tracker.async_remove)
        original_cleanup = tuple(consumer._on_remove)
        original_slots = [(slot, slot.template) for slots in consumer._template_attrs.values()
                          for slot in slots]
        binding_names = ('_template_attrs', '_templates') + module._FIELDS
        saved = {key: getattr(consumer, key) for key in binding_names if hasattr(consumer, key)}
        # Core4 EntityTemplate entries are mutable: retain their original pointers.
        saved_entity_templates = [(slot, slot.template)
                                  for slot in getattr(consumer, '_templates', {}).values()]
        current = old_tracker
        stopped = set()
        created = []
        phase = 'rename'
        rows = {cid: [], bid: []}
        inputs = {cid: [], bid: []}
        errors = []
        codes = (consumer._handle_results.__func__.__code__, Entity.async_write_ha_state.__code__)
        init_code = TrackTemplateResultInfo.__init__.__code__
        state_code = TemplateStateFromEntityId._state.fget.__code__
        monitor = sys.monitoring
        tool = next(i for i in range(6) if monitor.get_tool(i) is None)

        def observe_start(code, offset):
            frame = sys._getframe(1)
            try:
                entity = frame.f_locals.get('self')
                if entity is not consumer and entity is not control:
                    return
                state = hass.states.get(entity.entity_id)
                point = (state.state, state.attributes.get('count'))
                if code is codes[0]:
                    event = frame.f_locals['event']
                    event_id = event and event.data['entity_id']
                    source_id = self.producer.entity_id if entity is consumer else control_source.entity_id
                    origin = 'self' if event_id == entity.entity_id else (
                        'source' if event_id == source_id else event_id)
                    updates = tuple((tuple(s._attribute for s in entity._template_attrs[u.template]),
                                     u.result) for u in frame.f_locals['updates'])
                    rows[entity.entity_id].append((phase, 'handle', origin, point, updates))
                else:
                    rows[entity.entity_id].append((phase, 'write', point))
            except Exception as error:
                errors.append(repr(error))
            finally:
                del frame

        def observe_return(code, offset, result):
            frame = sys._getframe(1)
            try:
                if code is init_code:
                    action = frame.f_locals['action']
                    if getattr(action, '__self__', None) is consumer:
                        created.append(frame.f_locals['self'])
                else:
                    entity_id = frame.f_locals['self']._entity_id
                    if entity_id in inputs:
                        inputs[entity_id].append((phase, result,
                            (result.state, result.attributes.get('count')),
                            result is hass.states.get(entity_id)))
            except Exception as error:
                errors.append(repr(error))
            finally:
                del frame

        def stop(tracker):
            if tracker is not None and id(tracker) not in stopped:
                # Also handles a factory failure after native __init__ returned.
                if tracker._track_state_changes is not None:
                    tracker.async_remove()
                stopped.add(id(tracker))

        def attach():
            nonlocal current
            # Equivalent variables to native startup, retaining dynamic live this.
            variables = {'this': TemplateStateFromEntityId(hass, cid),
                         **consumer._render_script_variables()}
            tracks = []
            for template, slots in consumer._template_attrs.items():
                for slot in slots:
                    slot.async_setup()
                tracks.append(TrackTemplate(template, variables))
            tracks.sort(key=lambda track: track.template is not consumer._availability_template)
            current = async_track_template_result(hass, tracks, consumer._handle_results,
                has_super_template=consumer._availability_template is not None)
            consumer._template_result_info = current
            consumer._on_remove[cleanup_index] = current.async_remove
            # Deliberately no async_refresh; native setup owns its RenderInfo.
            return current

        monitor.use_tool_id(tool, 'readonly native this rebind primitive trace')
        monitor.register_callback(tool, monitor.events.PY_START, observe_start)
        monitor.register_callback(tool, monitor.events.PY_RETURN, observe_return)
        for code in codes:
            monitor.set_local_events(tool, code, monitor.events.PY_START)
        for code in (init_code, state_code):
            monitor.set_local_events(tool, code, monitor.events.PY_RETURN)
        try:
            # Build actual source pairs from this native fixture's actual YAML
            # source and actual Templates; these are DATA, never authorization.
            desired = original.replace(old.encode(), new.encode())
            pairs = tuple(module.BindingSourcePair(t, t.template, t.template.replace(old, new),
                (str(yaml_path), 'scope-value', tuple(s._attribute for s in slots)))
                for t, slots in consumer._template_attrs.items())
            def fence(stage, sources, entities):
                return (entities == (consumer,) and sources == pairs
                        and yaml_path.read_bytes() in (original, desired)
                        and self.f.entity.async_get(old).id == source_uuid)
            context = module._BindingCallerContext(asyncio.current_task(), fence)
            transport = module.NativeTemplateBindingTransport.capture(hass, (consumer,),
                source_pairs=pairs, caller_context=context)
            self.assertIs(consumer._template_result_info, old_tracker)
            self.assertIsNotNone(old_tracker._track_state_changes)
            yaml_path.write_bytes(desired)
            self.assertIsNone(hass.states.get(new))
            self.assertIsNone(self.f.entity.async_get(new))
            transport.install_before_registry(context)
            replacements = {id(p.template): next(t for t in consumer._template_attrs
                if t.template == p.after) for p in pairs}
            current = consumer._template_result_info
            expected_history = {replacements[id(t)]: value for t, value in carried_results.items()}
            self.assertEqual(current._last_result, expected_history)
            transport.mark_registry_attempt(context)
            # The actual registry mutation is IMMEDIATELY after the data-plane
            # seam; no await/source drain/YAML IO while trackers are detached.
            self.f.entity.async_update_entity(old, new_entity_id=new)
            await hass.async_block_till_done()
            self.assertEqual(self.producer.entity_id, new)
            self.assertEqual(self.f.entity.async_get(new).id, source_uuid)
            self.assertIsNone(hass.states.get(old))
            self.assertEqual(hass.states.get(new).state, '3')
            tracker = current
            self.assertEqual(set(tracker._info), set(consumer._template_attrs))
            self.assertEqual(self.producer.entity_id, new)
            self.assertEqual(self.f.entity.async_get(new).id, source_uuid)
            self.assertIsNone(hass.states.get(old))
            self.assertEqual(hass.states.get(new).state, '3')
            self.assertIs(hass.data[DATA_INSTANCES]['sensor'].get_entity(new), self.producer)
            self.assertIsNot(tracker, old_tracker)
            expected_history = {replacements[id(t)]: value for t, value in carried_results.items()}
            if tracker._last_result != expected_history:
                import json
                print(json.dumps({'binding_after_registry': {
                    'before_count': before_count, 'control_before_count': control_before_count,
                    'current_count': hass.states.get(cid).attributes['count'],
                    'old_results': [[t.template, v] for t, v in carried_results.items()],
                    'new_results': [[t.template, v] for t, v in tracker._last_result.items()],
                    'native_rows': rows, 'self_ref_count': consumer._self_ref_update_count}},
                    default=str), flush=True)
            self.assertEqual(tracker._last_result, expected_history)
            self.assertEqual(tracker.listeners['entities'], {new, cid})
            self.assertTrue(all(not info.exception for info in tracker._info.values()))
            self.assertIs(hass.states.get(cid), physical)
            self.assertEqual((consumer._attr_native_value, consumer._attr_extra_state_attributes),
                             before_fields)
            await hass.async_block_till_done()
            self.assertIs(hass.states.get(cid), physical)
            self.assertEqual(rows, {cid: [], bid: []}, 'bootstrap/rename caused a native callback/write')
            self.assertTrue(inputs[cid], 'setup did not actually consume native live this')
            self.assertTrue(all(state is physical for _, state, _, _ in inputs[cid]))
            self.assertEqual(tuple(consumer._on_remove[:cleanup_index]), original_cleanup[:cleanup_index])
            self.assertEqual(tuple(consumer._on_remove[cleanup_index + 1:]), original_cleanup[cleanup_index + 1:])
            self.assertEqual(consumer._on_remove[cleanup_index], tracker.async_remove)

            snapshots = []
            for value in (5, 7):
                phase = 'source-' + str(value)
                await emit(self.producer, value)
                await emit(control_source, value)
                a, b = hass.states.get(cid), hass.states.get(bid)
                snapshots.append((phase, a.state, a.attributes['count'],
                                  b.state, b.attributes['count']))
            # Collect BOTH real events before any comparison can fail. Report
            # actual control observations, never an assumed increment of one.
            self.assertEqual([(tag, value, count - before_count)
                              for tag, value, count, _, _ in snapshots],
                             [(tag, value, count - control_before_count)
                              for tag, _, _, value, count in snapshots],
                             ('fresh history changed memory-this', snapshots, rows))
            self.assertEqual([row[1] for row in snapshots], ['10', '14'])
            def relative_trace(events, initial):
                # Native bootstrap histories can start at different counts.
                # count := this.count + 1 is translation invariant: compare
                # exact deltas and every callback/write against each OWN base.
                normalized = []
                for row in events:
                    if row[1] == 'handle':
                        tag, kind, origin, (value, count), updates = row
                        updates = tuple((attributes, result - initial)
                            if attributes == ('count',) else (attributes, result)
                            for attributes, result in updates)
                        normalized.append((tag, kind, origin, (value, count - initial), updates))
                    else:
                        tag, kind, (value, count) = row
                        normalized.append((tag, kind, (value, count - initial)))
                return normalized
            self.assertEqual(relative_trace(rows[cid], before_count),
                relative_trace(rows[bid], control_before_count),
                'native handler/write delta trace differs from control')
            self.assertEqual((consumer._attr_icon, consumer._attr_entity_picture),
                             (control._attr_icon, control._attr_entity_picture))
            # Getter invocation frequency is not a semantic control oracle:
            # native versions may read this at different dispatch points. Each
            # actual return MUST be the real live State at that exact point.
            # Exact resulting count and handler/write control traces above stay.
            self.assertTrue(all(is_current for values in inputs.values()
                                for _, _, _, is_current in values),
                            'native this returned a stale/captured State')
            for entity_id in (cid, bid):
                self.assertTrue(any(tag != 'rename' for tag, _, _, _ in inputs[entity_id]),
                                'later events never consumed actual live this')
            for tag in ('source-5', 'source-7'):
                self.assertTrue(any(row[:2] == (tag, 'handle') for row in rows[cid]))
            self.assertIs(next(e for p in async_get_platforms(hass, 'template')
                for e in p.entities.values() if e.entity_id == cid), consumer)
            self.assertFalse(errors, errors)
        finally:
            # Repair registry/YAML/bindings even when an oracle above disproves
            # the primitive. No output rollback, fabricated State or cache writes.
            phase = 'cleanup'
            restored_ok = False
            try:
                stop(current)
                for tracker in created:
                    stop(tracker)
                if self.f.entity.async_get(new) is not None:
                    self.f.entity.async_update_entity(new, new_entity_id=old)
                await hass.async_block_till_done()
                yaml_path.write_bytes(original)
                for name, value in saved.items():
                    setattr(consumer, name, value)
                for slot, template in saved_entity_templates + original_slots:
                    slot.template = template
                restored = attach()
                await hass.async_block_till_done()
                self.assertEqual(self.producer.entity_id, old)
                self.assertEqual(self.f.entity.async_get(old).id, source_uuid)
                self.assertEqual(yaml_path.read_bytes(), original)
                self.assertEqual(consumer._on_remove[cleanup_index], restored.async_remove)
                self.assertEqual(len(consumer._on_remove), len(original_cleanup))
                restored_ok = True
            finally:
                # If restoration itself fails, remove every new listener and dead
                # cleanup callback before native fixture hass.stop runs.
                if not restored_ok:
                    for tracker in created:
                        stop(tracker)
                    consumer._on_remove[:] = [cb for cb in consumer._on_remove
                        if id(getattr(cb, '__self__', None)) not in stopped]
                for code in codes + (init_code, state_code):
                    monitor.set_local_events(tool, code, 0)
                monitor.register_callback(tool, monitor.events.PY_START, None)
                monitor.register_callback(tool, monitor.events.PY_RETURN, None)
                monitor.free_tool_id(tool)




@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeBindingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare

    async def pair(self, *, variant='sensor', unavailable=False, time=False, broad=False,
                   equal_attributes=False, broad_membership=False, domain=False):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        from homeassistant.helpers.entity_platform import async_get_platforms
        await self.prepare('yaml', variant=variant, validate=False)
        self.module = importlib.import_module(server.activation.fixture.fixtures.PACKAGE
            + '.native_template_binding_transport')
        hass = self.f.hass
        other = type(self.producer)()
        other._attr_unique_id = 'binding-lifecycle-control-source'
        other._attr_name = 'Binding lifecycle control source'
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([other])
        def definition(uid, name, source):
            state = ("{{ states('" + source + "') | int(0) >= 3 }}" if variant == 'binary_sensor'
                else "{{ states('" + source + "') | int(0) * 2 }}")
            if broad:
                state = "{{ (states | list | count) + (states('" + source + "') | int(0)) }}"
            if broad_membership:
                state = "{{ states | selectattr('entity_id', 'equalto', '" + source + "') | list | count }}"
            if domain:
                state = ("{{ (states.sensor | selectattr('entity_id', 'equalto', '" + source
                         + "') | map(attribute='state') | first | default('0')) | int(0) * 2 }}")
            text = ('  - unique_id: ' + uid + '\n    name: ' + name + '\n    state: "' + state + '"\n')
            if variant == 'binary_sensor':
                text += '    delay_on: "00:00:02"\n'
            if unavailable:
                text += '    availability: "{{ states(\'' + source + '\') | int(0) >= 3 }}"\n'
            if equal_attributes:
                text += ('    attributes:\n'
                    '      same_a: "{{ states(\'' + source + '\') }}"\n'
                    '      same_b: "{{ states(\'' + source + '\') }}"\n')
            if time:
                text += '    attributes:\n      minute: "{{ now().strftime(\'%Y-%m-%d %H:%M\') }}"\n'
            return text
        self.path = self.f.root / 'template.yaml'
        self.original = ('- unique_id: scope\n  ' + variant + ':\n'
            + definition('value', 'Runtime consumer', self.source_id)
            + definition('control', 'Binding lifecycle control', other.entity_id)).encode()
        self.path.write_bytes(self.original)
        await hass.services.async_call('template', 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        entities = {e.unique_id: e for p in async_get_platforms(hass, 'template')
            if p.domain == variant for e in p.entities.values()}
        self.consumer, self.control = entities['scope-value'], entities['scope-control']
        self.other = other
        self.old, self.new = self.source_id, 'sensor.binding_lifecycle_renamed'
        self.desired = self.original.replace(self.old.encode(), self.new.encode())
        self.source_uuid = self.f.entity.async_get(self.old).id
        self.renamed = False

    async def emit(self, source, value):
        source._attr_native_value = value
        source.async_write_ha_state()
        await self.f.hass.async_block_till_done()

    def capture(self):
        module, entity = self.module, self.consumer
        pairs = tuple(module.BindingSourcePair(t, t.template,
            t.template.replace(self.old, self.new),
            (str(self.path), 'scope-value', tuple(slot._attribute for slot in slots)))
            for t, slots in entity._template_attrs.items())
        def fence(phase, sources, entities):
            row = self.f.entity.async_get(self.new if self.renamed else self.old)
            return (row is not None and row.id == self.source_uuid
                and entities == (entity,) and sources == pairs
                and self.path.read_bytes() in (self.original, self.desired))
        self.context = module._BindingCallerContext(asyncio.current_task(), fence)
        return module.NativeTemplateBindingTransport.capture(self.f.hass, (entity,),
            source_pairs=pairs, caller_context=self.context)

    async def apply(self, transport):
        self.path.write_bytes(self.desired)
        self.assertIsNone(self.f.hass.states.get(self.new))
        transport.install_before_registry(self.context)
        transport.mark_registry_attempt(self.context)
        self.f.entity.async_update_entity(self.old, new_entity_id=self.new)
        self.renamed = True
        await self.f.hass.async_block_till_done()

    async def test_unavailable_group_setup_is_complete_then_real_availability_recovers(self):
        await self.pair(unavailable=True)
        hass, entity, control = self.f.hass, self.consumer, self.control
        physical = hass.states.get(entity.entity_id)
        self.assertEqual(physical.state, 'unavailable')
        tracker = entity._template_result_info
        history = dict(tracker._last_result)
        transport = self.capture()
        await self.apply(transport)
        self.assertIs(hass.states.get(entity.entity_id), physical)
        self.assertEqual(set(entity._template_result_info._info), set(entity._template_attrs))
        self.assertTrue(entity._template_result_info._has_super_template)
        self.assertEqual(len(entity._template_result_info._last_result), len(history))
        self.assertEqual(hass.states.get(entity.entity_id).state, hass.states.get(control.entity_id).state)
        for value in (3, 5, 1, 7):
            await self.emit(self.producer, value)
            await self.emit(self.other, value)
            actual, expected = hass.states.get(entity.entity_id), hass.states.get(control.entity_id)
            self.assertEqual((actual.state, actual.attributes), (expected.state,
                {**expected.attributes, 'friendly_name': actual.attributes['friendly_name']}))

    async def test_pending_binary_delay_retains_same_owner_handle_and_absolute_deadline(self):
        await self.pair(variant='binary_sensor')
        hass, entity, control = self.f.hass, self.consumer, self.control
        await self.emit(self.producer, 3)
        await self.emit(self.other, 3)
        token = entity._delay_cancel
        handle = token.__self__
        self.assertIsInstance(handle, asyncio.TimerHandle)
        self.assertFalse(handle.cancelled())
        deadline = handle.when()
        self.assertGreater(deadline, hass.loop.time())
        physical = hass.states.get(entity.entity_id)
        transport = self.capture()
        await self.apply(transport)
        self.assertIs(entity._delay_cancel, token)
        self.assertIs(entity._delay_cancel.__self__, handle)
        self.assertEqual(handle.when(), deadline)
        self.assertIs(hass.states.get(entity.entity_id), physical)
        # Distinct ordinary source event but identical true result. A fresh
        # empty history would restart delay; ACTUAL transported history must not.
        await self.emit(self.producer, 5)
        await self.emit(self.other, 5)
        self.assertIs(entity._delay_cancel, token)
        self.assertFalse(handle.cancelled())
        self.assertEqual(handle.when(), deadline)
        await asyncio.sleep(max(0, deadline - hass.loop.time()) + 0.1)
        await hass.async_block_till_done()
        self.assertEqual(hass.states.get(entity.entity_id).state, 'on')
        self.assertEqual(hass.states.get(entity.entity_id).state, hass.states.get(control.entity_id).state)

    async def test_natural_minute_tick_matches_independent_native_control(self):
        from homeassistant.core import callback
        await self.pair(time=True)
        hass, entity, control = self.f.hass, self.consumer, self.control
        before = hass.states.get(entity.entity_id).attributes['minute']
        old_times = {t.template: (remove, remove.__self__.job,
            remove.__self__._cancel_callback.__self__._cancel_callback,
            remove.__self__._cancel_callback.__self__._cancel_callback.when())
            for t, remove in entity._template_result_info._time_listeners.items()}
        transport = self.capture()
        await self.apply(transport)
        self.assertTrue(entity._template_result_info._time_listeners)
        for t, remove in entity._template_result_info._time_listeners.items():
            old_remove, old_job, handle, deadline = old_times[t.template.replace(self.new, self.old)]
            self.assertIs(remove, old_remove)
            self.assertIsNot(remove.__self__.job, old_job)
            self.assertIs(remove.__self__._cancel_callback.__self__._cancel_callback, handle)
            self.assertEqual(handle.when(), deadline)
            self.assertFalse(handle.cancelled())
        ready = asyncio.Event()
        changed_ids = set()
        @callback
        def changed(event):
            state = event.data.get('new_state')
            if (event.data.get('entity_id') in (entity.entity_id, control.entity_id) and state is not None
                    and state.attributes.get('minute') != before):
                changed_ids.add(event.data['entity_id'])
                if changed_ids == {entity.entity_id, control.entity_id}:
                    ready.set()
        remove = hass.bus.async_listen('state_changed', changed)
        try:
            # Wait for genuine wall-clock native minute dispatch. No clock patch,
            # manual callback or fake event; owner runner timeout must allow 70s.
            await asyncio.wait_for(ready.wait(), timeout=65)
            await hass.async_block_till_done()
            self.assertEqual(hass.states.get(entity.entity_id).attributes['minute'],
                hass.states.get(control.entity_id).attributes['minute'])
            await self.emit(self.producer, 5)
            await self.emit(self.other, 5)
            self.assertEqual(hass.states.get(entity.entity_id).state, hass.states.get(control.entity_id).state)
        finally:
            remove()

    async def test_pre_registry_rollback_restores_exact_bindings_and_cleanup_ownership(self):
        await self.pair()
        hass, entity = self.f.hass, self.consumer
        old = entity._template_result_info
        attrs, cleanup = entity._template_attrs, tuple(entity._on_remove)
        bindings = {key: getattr(entity, key) for key in self.module._FIELDS if hasattr(entity, key)}
        slots = [(slot, slot.template) for group in attrs.values() for slot in group]
        physical, history = hass.states.get(entity.entity_id), dict(old._last_result)
        transport = self.capture()
        self.path.write_bytes(self.desired)
        transport.install_before_registry(self.context)
        new = entity._template_result_info
        transport.rollback_before_registry(self.context)
        self.path.write_bytes(self.original)
        self.assertIs(entity._template_result_info, old)
        self.assertIs(entity._template_attrs, attrs)
        self.assertEqual(tuple(entity._on_remove), cleanup)
        self.assertEqual(old._last_result, history)
        self.assertTrue(all(getattr(entity, k) is v for k, v in bindings.items()))
        self.assertTrue(all(slot.template is t for slot, t in slots))
        self.assertIs(hass.states.get(entity.entity_id), physical)
        self.assertFalse(new._time_listeners)
        self.assertFalse(new._track_state_changes._listeners)
        await self.emit(self.producer, 5)
        await self.emit(self.other, 5)
        self.assertEqual(hass.states.get(entity.entity_id).state, hass.states.get(self.control.entity_id).state)

    async def test_foreign_pending_rate_callback_refuses_before_any_binding_effect(self):
        await self.pair(broad=True)
        entity = self.consumer
        old, attrs, cleanup = entity._template_result_info, entity._template_attrs, tuple(entity._on_remove)
        # The native initial refresh supplies actual last-triggered time. A real
        # producer event during its all-states rate window schedules real work.
        await self.emit(self.producer, 3)
        timers = dict(old._rate_limit._rate_limit_timers)
        self.assertTrue(timers, 'native fixture failed to create pending rate event')
        handle = next(iter(timers.values()))
        callback = handle._callback
        try:
            handle._callback = self.control._template_result_info._refresh
            with self.assertRaisesRegex(ValueError, 'rate_timer_owner_changed'):
                self.capture()
        finally:
            handle._callback = callback
        self.assertIs(entity._template_result_info, old)
        self.assertIs(entity._template_attrs, attrs)
        self.assertEqual(tuple(entity._on_remove), cleanup)
        self.assertEqual(old._rate_limit._rate_limit_timers, timers)
        self.assertTrue(all(not handle.cancelled() for handle in timers.values()))

    async def test_pending_explicit_rate_remains_refused_before_any_binding_effect(self):
        await self.pair(broad=True)
        await self.emit(self.producer, 3)
        old = self.consumer._template_result_info
        timers = dict(old._rate_limit._rate_limit_timers)
        self.assertTrue(timers)
        with self.assertRaisesRegex(ValueError, 'pending_explicit_rate_event_unqualified'):
            self.capture()
        self.assertIs(self.consumer._template_result_info, old)
        self.assertEqual(old._rate_limit._rate_limit_timers, timers)
        self.assertTrue(all(not timer.cancelled() for timer in timers.values()))

    async def test_pending_broad_rate_keeps_timer_deadline_event_and_natural_native_expiry(self):
        await self.pair(broad_membership=True)
        hass, entity, control = self.f.hass, self.consumer, self.control
        await self.emit(self.producer, 3)
        await self.emit(self.other, 3)
        old = entity._template_result_info
        timers = dict(old._rate_limit._rate_limit_timers)
        self.assertTrue(timers)
        self.assertTrue(all(not old._info[template].entities for template in timers))
        originals = {id(handle): (handle, handle.when(), handle._args[0], handle._context)
                     for handle in timers.values()}
        control_timers = dict(control._template_result_info._rate_limit._rate_limit_timers)
        self.assertTrue(control_timers)
        physical = hass.states.get(entity.entity_id)
        transport = self.capture()
        await self.apply(transport)
        current = entity._template_result_info
        self.assertIsNot(current, old)
        self.assertEqual({id(handle) for handle in current._rate_limit._rate_limit_timers.values()}, set(originals))
        self.assertFalse(old._rate_limit._rate_limit_timers)
        self.assertIs(hass.states.get(entity.entity_id), physical)
        for template, handle in current._rate_limit._rate_limit_timers.items():
            before, deadline, event, context = originals[id(handle)]
            self.assertIs(handle, before)
            self.assertFalse(handle.cancelled())
            self.assertEqual(handle.when(), deadline)
            self.assertIs(handle._args[0], event)
            self.assertIs(handle._context, context)
            self.assertEqual(handle._callback, current._refresh)
            self.assertEqual(len(handle._args[1]), 1)
            self.assertIs(handle._args[1][0].template, template)
            self.assertIs(handle._args[2], True)
        # Real source removals change the membership result from 1 to 0. Both
        # unchanged control and transported group consume that change at their
        # original native deadlines, not at the rename seam.
        self.assertEqual(hass.states.get(entity.entity_id).state, '1')
        await self.producer.async_remove(force_remove=True)
        await self.other.async_remove(force_remove=True)
        await hass.async_block_till_done()
        self.assertIsNone(hass.states.get(self.producer.entity_id))
        self.assertIsNone(hass.states.get(self.other.entity_id))
        # Observe real native expiry; no callback invocation, clock alteration,
        # timer replacement, output seeding or expected value from a moved cache.
        fired = hass.loop.create_future()
        seen = set()
        code = current._refresh.__func__.__code__
        monitor = sys.monitoring
        tool = next(i for i in range(6) if monitor.get_tool(i) is None)
        monitor.use_tool_id(tool, 'actual pending Template native expiry')
        def observe(code_arg, offset):
            frame = sys._getframe(1)
            if frame.f_code is not code or not frame.f_locals.get('replayed'):
                return
            owner = frame.f_locals.get('self')
            if owner is current or owner is control._template_result_info:
                seen.add(id(owner))
                if len(seen) == 2 and not fired.done():
                    fired.set_result(True)
        monitor.register_callback(tool, monitor.events.PY_START, observe)
        monitor.set_local_events(tool, code, monitor.events.PY_START)
        try:
            deadline = max(handle.when() for handle in (*timers.values(), *control_timers.values()))
            await asyncio.wait_for(fired, max(0, deadline - hass.loop.time()) + 10)
            await hass.async_block_till_done()
        finally:
            monitor.set_local_events(tool, code, 0)
            monitor.register_callback(tool, monitor.events.PY_START, None)
            monitor.free_tool_id(tool)
        self.assertEqual(hass.states.get(entity.entity_id).state, hass.states.get(control.entity_id).state)
        self.assertEqual(hass.states.get(entity.entity_id).state, '0')
        self.assertTrue(all(handle.cancelled() for handle in timers.values()))
        self.assertEqual(self.f.entity.async_get(self.new).id, self.source_uuid)

    async def test_pending_broad_rate_pre_registry_rollback_restores_exact_native_timer(self):
        await self.pair(broad_membership=True)
        await self.emit(self.producer, 3)
        hass, entity = self.f.hass, self.consumer
        old = entity._template_result_info
        timers = dict(old._rate_limit._rate_limit_timers)
        self.assertTrue(timers)
        saved = {id(handle): (handle._callback, handle._args, handle._context, handle.when())
                 for handle in timers.values()}
        physical = hass.states.get(entity.entity_id)
        listeners = old._track_state_changes._last_track_states
        cleanup = tuple(entity._on_remove)
        transport = self.capture()
        transport.install_before_registry(self.context)
        new = entity._template_result_info
        self.assertFalse(old._rate_limit._rate_limit_timers)
        transport.rollback_before_registry(self.context)
        self.assertIs(entity._template_result_info, old)
        self.assertEqual(old._rate_limit._rate_limit_timers, timers)
        self.assertEqual(old._track_state_changes._last_track_states, listeners)
        self.assertEqual(tuple(entity._on_remove), cleanup)
        self.assertFalse(new._rate_limit._rate_limit_timers)
        self.assertIs(hass.states.get(entity.entity_id), physical)
        for handle in timers.values():
            callback, args, context, deadline = saved[id(handle)]
            self.assertIs(handle._callback, callback)
            self.assertIs(handle._args, args)
            self.assertIs(handle._context, context)
            self.assertEqual(handle.when(), deadline)
            self.assertFalse(handle.cancelled())


    async def test_failed_collision_install_keeps_old_tracker_active_and_exact_bindings(self):
        await self.pair(time=True)
        module, entity = self.module, self.consumer
        old, attrs = entity._template_result_info, entity._template_attrs
        cleanup = tuple(entity._on_remove)
        pairs = tuple(module.BindingSourcePair(t, t.template, "{{ 1 }}",
            (str(self.path), 'scope-value', tuple(s._attribute for s in slots)))
            for t, slots in attrs.items())
        self.assertGreater(len(pairs), 1)
        context = module._BindingCallerContext(asyncio.current_task(),
            lambda stage, sources, entities: sources == pairs and entities == (entity,))
        transport = module.NativeTemplateBindingTransport.capture(self.f.hass, (entity,),
            source_pairs=pairs, caller_context=context)
        with self.assertRaisesRegex(ValueError, 'template_key_collision'):
            transport.install_before_registry(context)
        self.assertIs(entity._template_result_info, old)
        self.assertIs(entity._template_attrs, attrs)
        self.assertEqual(tuple(entity._on_remove), cleanup)
        self.assertTrue(old._track_state_changes._listeners)
        self.assertTrue(old._time_listeners)
        await self.emit(self.producer, 5)
        await self.emit(self.other, 5)
        self.assertEqual(self.f.hass.states.get(entity.entity_id).state,
            self.f.hass.states.get(self.control.entity_id).state)


    async def test_existing_equal_body_attribute_slots_keep_native_group_and_own_config_identity(self):
        await self.pair(equal_attributes=True)
        hass, entity, control = self.f.hass, self.consumer, self.control
        group_key, group = next((key, slots) for key, slots in entity._template_attrs.items()
            if {slot._attribute for slot in slots} == {'same_a', 'same_b'})
        left, right = (next(slot for slot in group if slot._attribute == name)
                       for name in ('same_a', 'same_b'))
        self.assertIsNot(left.template, right.template)
        self.assertEqual(left.template, right.template)
        self.assertTrue(any(slot.template is not group_key for slot in group))
        self.assertIs(left.template, entity._attribute_templates['same_a'])
        self.assertIs(right.template, entity._attribute_templates['same_b'])
        old_templates = (left.template, right.template)
        old_groups = len(entity._template_attrs)
        physical = hass.states.get(entity.entity_id)
        transport = self.capture()
        await self.apply(transport)
        new_key, new_group = next((key, slots) for key, slots in entity._template_attrs.items()
            if {slot._attribute for slot in slots} == {'same_a', 'same_b'})
        self.assertIs(new_group, group)
        self.assertEqual(len(entity._template_attrs), old_groups)
        self.assertIsNot(left.template, right.template)
        self.assertEqual(left.template, new_key)
        self.assertEqual(right.template, new_key)
        self.assertIsNot(left.template, old_templates[0])
        self.assertIsNot(right.template, old_templates[1])
        self.assertIs(left.template, entity._attribute_templates['same_a'])
        self.assertIs(right.template, entity._attribute_templates['same_b'])
        self.assertIn(self.new, left.template.template)
        self.assertIn(self.new, right.template.template)
        self.assertIs(hass.states.get(entity.entity_id), physical)
        for value in (5, 7):
            await self.emit(self.producer, value)
            await self.emit(self.other, value)
            a, b = hass.states.get(entity.entity_id), hass.states.get(control.entity_id)
            self.assertEqual((a.state, a.attributes['same_a'], a.attributes['same_b']),
                             (b.state, b.attributes['same_a'], b.attributes['same_b']))
        # Re-qualification must accept the same legitimate merged graph.
        unique = {id(slot.template): (slot.template, slot._attribute)
            for slots in entity._template_attrs.values() for slot in slots}
        reverse_pairs = tuple(self.module.BindingSourcePair(t, t.template,
            t.template.replace(self.new, self.old), (str(self.path), 'scope-value', attribute))
            for t, attribute in unique.values())
        def reverse_fence(phase, sources, entities):
            row = self.f.entity.async_get(self.new)
            return (entities == (entity,) and sources == reverse_pairs
                and self.path.read_bytes() == self.desired and row.id == self.source_uuid
                and all(p.template.template == p.before for p in sources))
        self.module.NativeTemplateBindingTransport.capture(hass, (entity,),
            source_pairs=reverse_pairs,
            caller_context=self.module._BindingCallerContext(asyncio.current_task(), reverse_fence))


    async def test_source_equal_native_state_callback_with_foreign_class_cell_is_refused(self):
        from types import FunctionType, MethodType
        await self.pair(variant='binary_sensor')
        module, entity = self.module, self.consumer
        runtime = module._Runtime(self.f.hass)
        runtime.sources()
        state_slot = next(slot for slots in entity._template_attrs.values() for slot in slots
            if slot._attribute in ('_state', '_attr_is_on'))
        actual = entity._update_state
        function = actual.__func__
        self.assertEqual(function.__code__.co_freevars, ('__class__',))
        # Positive native identity/code/globals/class-cell authentication. The
        # actual binary method is also the recursive on_update in Core4.
        module._callback(runtime.native, actual, entity, state_slot)
        def make_cell(value):
            return (lambda: value).__closure__[0]
        foreign_class = type('ForeignDefiningClass', (), {})
        clone = FunctionType(function.__code__, function.__globals__, function.__name__,
            function.__defaults__, (make_cell(foreign_class),))
        clone.__kwdefaults__ = function.__kwdefaults__
        self.assertIs(clone.__code__, function.__code__)
        self.assertIs(clone.__globals__, function.__globals__)
        self.assertEqual(clone.__qualname__, function.__qualname__)
        tracker, attrs = entity._template_result_info, entity._template_attrs
        physical, cleanup = self.f.hass.states.get(entity.entity_id), tuple(entity._on_remove)
        # Never install or invoke the forged method, mutate a native class cell,
        # or change native callbacks. Only the read-only qualifier sees it.
        with self.assertRaisesRegex(ValueError, 'callback_class_closure_changed'):
            module._callback(runtime.native, MethodType(clone, entity), entity, state_slot)
        self.assertIs(entity._update_state.__func__, function)
        self.assertIs(function.__closure__[0].cell_contents, runtime.native.binary)
        self.assertIs(entity._template_result_info, tracker)
        self.assertIs(entity._template_attrs, attrs)
        self.assertIs(self.f.hass.states.get(entity.entity_id), physical)
        self.assertEqual(tuple(entity._on_remove), cleanup)


if __name__ == '__main__':
    unittest.main()
