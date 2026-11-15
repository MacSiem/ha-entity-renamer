"""SOURCE ONLY / NOT RUN: actual Root configured dict partial-journal faults.

Observe original methods with fixture-local sys.monitoring. Raise only after an
original completed write; the foreign variants alter only actual dict data.
One real native pending period is retained without waiting for its expiration.
"""
import asyncio
from functools import partial
import sys
from types import CodeType, FunctionType
import unittest
import test_native_automation_duration_prefix_root as literal
import test_native_template_mixed_yaml_server as mixed


def _callable_image(value):
    if type(value) is not partial:
        return (id(value),)
    args = tuple((id(arg), tuple(id(item) for item in arg) if type(arg) in (tuple, list) else None)
                 for arg in value.args)
    return (id(value), id(value.func), id(value.args), args,
            None if value.keywords is None else tuple((id(k), id(v)) for k, v in value.keywords.items()))


def _callback_image(hass):
    """Actual full state-change callback graph; no ownership guesses."""
    from homeassistant.helpers import event
    maps = []
    for tracker in (event._KEYED_TRACK_STATE_CHANGE, event._KEYED_TRACK_STATE_ADDED_DOMAIN):
        data = hass.data.get(tracker.key)
        if data is None:
            maps.append((tracker, None, None, None, ()))
            continue
        maps.append((tracker, data, data.callbacks, (data.listener, _callable_image(data.listener)), tuple(
            (key, members, tuple((job, job.target) for job in members))
            for key, members in data.callbacks.items())))
    entries = hass.bus._listeners.get('state_changed')
    return tuple(maps), entries, tuple((entry, entry[0].target, entry[1]) for entry in entries or ())


def _other_bus_image(hass, *, include_state=False):
    return (id(hass.bus._listeners), tuple(
        (key, id(entries), tuple((id(entry), id(entry[0]), id(entry[0].target), id(entry[1]))
                                 for entry in entries))
        for key, entries in hass.bus._listeners.items() if include_state or key != 'state_changed'))


def _scene_fence_census(test, root, hass):
    """Authenticate only this session's actual six native bus unsubs."""
    module = test.module('native_empty_scene')
    fence = root.scene_fence
    test.assertIs(type(fence), module.NativeEmptySceneFence)
    test.assertIs(fence.hass, hass)
    test.assertFalse(fence.closed)
    names = ('call_service', 'service_registered', 'service_removed',
             'scene_reloaded', 'state_changed', 'entity_registry_updated')
    test.assertEqual(tuple(name for name, _ in fence.removers), names)
    registered = test.module('yaml_runtime_observer')._registered_bus_remove
    native_code, = (value for value in module.NativeEmptySceneFence.__init__.__code__.co_consts
                    if type(value) is CodeType and value.co_qualname ==
                    'NativeEmptySceneFence.__init__.<locals>.observed')
    observer = None
    result = []
    for name, unsubscribe in fence.removers:
        entry, _ = registered(hass, unsubscribe, name)
        job, event_filter = entry
        function = job.target
        test.assertIsNone(event_filter)
        test.assertIs(type(function), FunctionType)
        test.assertIs(function.__code__, native_code)
        test.assertIs(function.__globals__, vars(module))
        test.assertEqual(function.__qualname__, native_code.co_qualname)
        cells = dict(zip(function.__code__.co_freevars,
                         (cell.cell_contents for cell in function.__closure__ or ()), strict=True))
        test.assertEqual(set(cells), {'self'})
        test.assertIs(cells['self'], fence)
        if observer is None:
            observer = function
        test.assertIs(function, observer)
        result.append((name, unsubscribe, entry))
    test.assertEqual(len({id(entry) for _, _, entry in result}), len(names))
    return fence, tuple(result)


def _without_scene_fence(test, image, owned, *, include_state):
    """Project only the six earlier authenticated own unsubs; no rebaseline."""
    bus_id, rows = image
    expected = {(name, id(entry)) for name, _, entry in owned
                if include_state or name != 'state_changed'}
    found, projected = set(), []
    for name, entries_id, entries in rows:
        kept = []
        removed = False
        for entry in entries:
            token = (name, entry[0])
            if token in expected:
                test.assertNotIn(token, found)
                found.add(token)
                removed = True
            else:
                kept.append(entry)
        if kept or not removed:
            projected.append((name, entries_id, tuple(kept)))
        # EventBus deletes an event key only when the removal made it empty.
    test.assertEqual(found, expected, 'the exact session fence registrations must have existed')
    return bus_id, tuple(projected)


def _without_scene_fence_fault(test, image, owned):
    callback_tokens, state_bus, all_bus, owners = image
    state_id, state_entries = state_bus
    guard_entries = {id(entry) for name, _, entry in owned if name == 'state_changed'}
    test.assertEqual(len(guard_entries), 1)
    test.assertEqual(sum(entry[0] in guard_entries for entry in state_entries), 1)
    remaining = tuple(entry for entry in state_entries if entry[0] not in guard_entries)
    # Native EventBus drops the state_changed list only if it is now empty.
    state_after = (state_id if remaining else id(None), remaining)
    return callback_tokens, state_after, _without_scene_fence(test, all_bus, owned, include_state=True), owners


def _bus_diagnostic(hass, label, fence, owned, template_transport):
    """Read-only IDs and source names; never call a listener or alter a slot."""
    from homeassistant.helpers import event
    def description(value):
        if value is None:
            return None
        actual = value.func if type(value) is partial else value
        function = getattr(actual, '__func__', actual)
        owner = getattr(actual, '__self__', None)
        code = getattr(function, '__code__', None)
        return {'id': id(value), 'kind': type(value).__name__,
                'module': getattr(function, '__module__', None),
                'qualname': getattr(function, '__qualname__', None),
                'file': code.co_filename.rsplit('/', 1)[-1] if code is not None else None,
                'code': id(code) if code is not None else None,
                'owner_type': type(owner).__name__ if owner is not None else None,
                'owner_id': id(owner) if owner is not None else None,
                'args': [(id(arg), type(arg).__name__) for arg in value.args]
                        if type(value) is partial else None}
    fence_ids = {id(entry): name for name, _, entry in owned}
    templates = []
    for row in template_transport.rows:
        for label_owner, tracker in (('original', row['tracker']), ('replacement', row['new'])):
            if tracker is None:
                continue
            filtered = tracker._track_state_changes
            templates.append({'role': label_owner, 'entity': row['entity'].entity_id,
                              'tracker': id(tracker), 'filtered': id(filtered),
                              'listeners': [(kind, id(remove)) for kind, remove in
                                            (filtered._listeners.items() if filtered is not None else ())]})
    keyed = []
    for tracker in (event._KEYED_TRACK_STATE_CHANGE, event._KEYED_TRACK_STATE_ADDED_DOMAIN):
        data = hass.data.get(tracker.key)
        if data is None:
            keyed.append({'tracker': tracker.key, 'data': None})
            continue
        remove = data.listener
        bus_entry = remove.args[1] if type(remove) is partial and len(remove.args) == 2 else None
        keyed.append({'tracker': tracker.key, 'data': id(data), 'callbacks': id(data.callbacks),
                      'unsubscribe': id(remove), 'bus_entry': id(bus_entry),
                      'routes': [(key, id(members), [(id(job), description(job.target)) for job in members])
                                 for key, members in data.callbacks.items()]})
    entries = hass.bus._listeners.get('state_changed', ())
    return {'stage': label, 'fence': id(fence), 'fence_closed': fence.closed,
            'fence_unsubs_remaining': len(fence.removers), 'templates': templates, 'keyed': keyed,
            'bus_list': id(entries), 'bus_entries': [
                {'entry': id(entry), 'job': id(entry[0]), 'target': description(entry[0].target),
                 'filter': description(entry[1]), 'session_fence': fence_ids.get(id(entry))}
                for entry in entries],
            'other_bus': [{'event': name, 'list': id(members), 'entries': [
                {'entry': id(entry), 'job': id(entry[0]), 'target': description(entry[0].target),
                 'filter': description(entry[1]), 'session_fence': fence_ids.get(id(entry))}
                for entry in members]} for name, members in hass.bus._listeners.items()
                if name != 'state_changed']}


def _dependency_image(tracker):
    return tuple((template, frozenset(info.entities), frozenset(info.domains),
                  frozenset(info.domains_lifecycle), info.all_states,
                  info.all_states_lifecycle, info.has_time, info.rate_limit,
                  info.is_static, type(info.exception), str(info.exception))
                 for template, info in tracker._info.items())


def _native_template_census(test, transport):
    """Census only subscriptions proved by each actual retained native owner."""
    from homeassistant.helpers import event
    from homeassistant.core import HassJob
    observer = test.module('native_template_rename_ingress').NativeTemplateRenameIngress
    registered = test.module('yaml_runtime_observer')._registered_bus_remove
    bound = test.module('native_template_consumers')._bound
    result, jobs, bus_entries = [], set(), set()
    for row in transport.rows:
        entity, tracker = row['entity'], row['tracker']
        test.assertIs(transport.runtime.graph(entity), tracker)
        filtered = tracker._track_state_changes
        observer.native_jobs(transport.hass, event, filtered, filtered._action)
        routes = []
        for kind, remove in filtered._listeners.items():
            if kind in (event._ENTITIES_LISTENER, event._DOMAINS_LISTENER):
                test.assertIs(type(remove), partial)
                test.assertIs(remove.func, event._remove_listener)
                owner, native_tracker, keys, job, callbacks = remove.args
                expected = (event._KEYED_TRACK_STATE_CHANGE if kind == event._ENTITIES_LISTENER
                            else event._KEYED_TRACK_STATE_ADDED_DOMAIN)
                test.assertIs(owner, transport.hass)
                test.assertIs(native_tracker, expected)
                test.assertIs(type(job), HassJob)
                data = transport.hass.data[expected.key]
                test.assertIs(data.callbacks, callbacks)
                (dispatcher, event_filter), _ = registered(transport.hass, data.listener, expected.event_type)
                for actual, function in ((dispatcher.target, expected.dispatcher_callable),
                                         (event_filter, expected.filter_callable)):
                    test.assertIs(type(actual), partial)
                    test.assertIs(actual.func, function)
                    test.assertFalse(actual.keywords)
                    test.assertEqual(len(actual.args), 2)
                    test.assertIs(actual.args[0], transport.hass)
                    test.assertIs(actual.args[1], callbacks)
                action_owner = tracker if kind == event._ENTITIES_LISTENER else filtered
                method = (event.TrackTemplateResultInfo._refresh if kind == event._ENTITIES_LISTENER
                          else event._TrackStateChangeFiltered._state_added)
                test.assertTrue(bound(job.target, action_owner, method))
                keys = tuple(sorted(keys))
                test.assertTrue(keys)
                for key in keys:
                    test.assertEqual(sum(member is job for member in callbacks[key]), 1)
                routes.append((kind, expected.key, keys))
                jobs.add(id(job))
            else:
                test.assertEqual(kind, event._ALL_LISTENER)
                entry, _ = registered(transport.hass, remove, 'state_changed')
                test.assertIsNone(entry[1])
                test.assertTrue(bound(entry[0].target, tracker, event.TrackTemplateResultInfo._refresh))
                bus_entries.add(id(entry))
                routes.append((kind, 'state_changed', ()))
        states = filtered._last_track_states
        state_image = (states.all_states, frozenset(states.domains), frozenset(states.entities))
        results = tuple((template, value) for template, value in tracker._last_result.items())
        times = []
        for template, remove in tracker._time_listeners.items():
            handle = remove.__self__._cancel_callback.__self__._cancel_callback
            times.append((template, remove, remove.__self__.job, handle,
                          handle.when(), handle._context, handle.cancelled()))
        rates = tuple((template, handle, handle._callback, handle._args, handle._context,
                       handle.when(), handle.cancelled())
                      for template, handle in tracker._rate_limit._rate_limit_timers.items())
        history = (tracker._job, tuple(tracker._track_templates), tracker._rate_limit,
                   tuple(tracker._rate_limit._last_triggered.items()), tracker._has_super_template)
        result.append((entity, tracker, entity._template_attrs, tuple(entity._on_remove),
                       tuple((name, getattr(entity, name)) for name in row['bindings']),
                       tuple((slot, slot.template) for slot, _ in row['slots'] + row['declarations']),
                       results, _dependency_image(tracker), state_image, tuple(routes), tuple(times), rates,
                       transport.hass.states.get(entity.entity_id), history))
    test.assertTrue(jobs or bus_entries, 'actual owned Template subscriptions must be censused')
    return tuple(result), jobs, bus_entries


def _assert_template_restored(test, before, after):
    test.assertTrue(before, 'actual affected Template owners must be reached')
    test.assertEqual(len(before), len(after))
    for original, restored in zip(before, after, strict=True):
        for index in (0, 1, 2, 12):
            test.assertIs(restored[index], original[index])
        for index in (3,):
            test.assertEqual(len(original[index]), len(restored[index]))
            for left, right in zip(original[index], restored[index], strict=True):
                test.assertIs(right, left)
        for index in (4, 5, 6):
            test.assertEqual(len(original[index]), len(restored[index]))
            for left, right in zip(original[index], restored[index], strict=True):
                if index == 4:
                    test.assertEqual(right[0], left[0])
                else:
                    test.assertIs(right[0], left[0])
                test.assertIs(right[1], left[1])
        test.assertEqual(len(original[7]), len(restored[7]))
        for left, right in zip(original[7], restored[7], strict=True):
            test.assertIs(right[0], left[0])
            test.assertEqual(right[1:], left[1:])
        test.assertEqual(restored[8:10], original[8:10])
        for index in (10, 11):
            test.assertEqual(len(original[index]), len(restored[index]))
            for left, right in zip(original[index], restored[index], strict=True):
                for position, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
                    if position in ((4, 6) if index == 10 else (5, 6)):
                        test.assertEqual(right_item, left_item)
                    else:
                        test.assertIs(right_item, left_item)
        old_job, old_tracks, old_rate, old_triggers, old_super = original[13]
        new_job, new_tracks, new_rate, new_triggers, new_super = restored[13]
        test.assertIs(new_job, old_job)
        test.assertIs(new_rate, old_rate)
        test.assertIs(new_super, old_super)
        test.assertEqual(len(new_tracks), len(old_tracks))
        test.assertTrue(all(actual is expected for actual, expected in zip(new_tracks, old_tracks, strict=True)))
        test.assertEqual(len(new_triggers), len(old_triggers))
        for (old_template, old_time), (new_template, new_time) in zip(old_triggers, new_triggers, strict=True):
            test.assertIs(new_template, old_template)
            test.assertIs(new_time, old_time)


def _assert_foreign_subsequence(test, before, before_own, before_bus, after, after_own, after_bus):
    """Exclude only each census's actually authenticated own subscriptions."""
    before_maps, before_entries, before_bus_items = before
    after_maps, after_entries, after_bus_items = after
    for (tracker, data, callbacks, listener, rows), (now_tracker, now_data, now_callbacks, now_listener, now_rows) in zip(
            before_maps, after_maps, strict=True):
        test.assertIs(now_tracker, tracker)
        old_rows = [(key, members, tuple((job, target) for job, target in jobs if id(job) not in before_own))
                    for key, members, jobs in rows]
        new_rows = [(key, members, tuple((job, target) for job, target in jobs if id(job) not in after_own))
                    for key, members, jobs in now_rows]
        old_empty = {key for key, _, jobs in rows if not jobs}
        new_empty = {key for key, _, jobs in now_rows if not jobs}
        old_rows = [row for row in old_rows if row[2] or row[0] in old_empty]
        new_rows = [row for row in new_rows if row[2] or row[0] in old_empty or row[0] in new_empty]
        if old_rows:
            test.assertIs(now_data, data)
            test.assertIs(now_callbacks, callbacks)
            test.assertIs(now_listener[0], listener[0])
            test.assertEqual(now_listener[1], listener[1])
        test.assertEqual(tuple(row[0] for row in old_rows), tuple(row[0] for row in new_rows))
        for (_, members, old_jobs), (_, new_members, new_jobs) in zip(old_rows, new_rows, strict=True):
            test.assertIs(new_members, members)
            test.assertEqual(len(old_jobs), len(new_jobs))
            for (job, target), (actual, actual_target) in zip(old_jobs, new_jobs, strict=True):
                test.assertIs(actual, job)
                test.assertIs(actual_target, target)
    old_bus = [item for item in before_bus_items if id(item[0]) not in before_bus]
    new_bus = [item for item in after_bus_items if id(item[0]) not in after_bus]
    if old_bus:
        test.assertIs(after_entries, before_entries)
    test.assertEqual(len(old_bus), len(new_bus))
    for original, actual in zip(old_bus, new_bus, strict=True):
        for original_item, actual_item in zip(original, actual, strict=True):
            test.assertIs(actual_item, original_item)


def _fault_image(hass, transport):
    """Whole relevant fault graph, including both old and new owners."""
    maps, bus, entries = _callback_image(hass)
    callback_tokens = tuple((id(tracker), id(data), id(callbacks), listener, tuple(
        (key, id(members), tuple((id(job), id(target)) for job, target in jobs))
        for key, members, jobs in rows)) for tracker, data, callbacks, listener, rows in maps)
    bus_tokens = (id(bus), tuple((id(entry), id(target), id(event_filter))
                                 for entry, target, event_filter in entries))
    owners = []
    for row in transport.rows:
        entity = row['entity']
        trackers = []
        for tracker in (row['tracker'], row['new']):
            filtered = tracker._track_state_changes
            time_slots = []
            for template, remove in tracker._time_listeners.items():
                owner = remove.__self__
                point = owner._cancel_callback.__self__
                handle = point._cancel_callback
                time_slots.append((id(template), _callable_image(remove), id(owner), id(owner.job),
                                   id(point), id(handle), handle.when(), id(handle._context), handle.cancelled()))
            trackers.append((id(tracker), id(filtered), id(filtered._listeners),
                tuple((key, _callable_image(value)) for key, value in filtered._listeners.items()),
                id(filtered._action), id(filtered._action_as_hassjob.target),
                id(tracker._job), tuple(id(item) for item in tracker._track_templates),
                tuple((id(template), id(info)) for template, info in tracker._info.items()),
                tuple((id(template), id(value)) for template, value in tracker._last_result.items()),
                tuple((id(template), id(value)) for template, value in tracker._rate_limit._last_triggered.items()),
                tuple((id(template), id(handle), id(handle._callback), id(handle._args),
                       id(handle._context), handle.when(), handle.cancelled())
                      for template, handle in tracker._rate_limit._rate_limit_timers.items()),
                tuple(time_slots)))
        owners.append((id(entity), id(entity._template_result_info), id(entity._template_attrs),
                       id(entity._on_remove), tuple(id(item) for item in entity._on_remove),
                       tuple((name, id(getattr(entity, name))) for name in row['bindings']),
                       tuple((id(slot), id(slot.template)) for slot, _ in row['slots'] + row['declarations']),
                       tuple(trackers)))
    return callback_tokens, bus_tokens, _other_bus_image(hass, include_state=True), tuple(owners)


@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationStructuredPartialWriteFaultRootTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = literal.NativeAutomationDurationPrefixRootTests.asyncSetUp
    _before_start = literal.NativeAutomationDurationPrefixRootTests._before_start
    _flush_documents = literal.NativeAutomationDurationPrefixRootTests._flush_documents
    prepare = literal.NativeAutomationDurationPrefixRootTests.prepare
    emit = literal.NativeAutomationDurationPrefixRootTests.emit
    _runtime = literal.NativeAutomationDurationPrefixRootTests._runtime
    module = literal.NativeAutomationDurationPrefixRootTests.module
    stimulus = literal.NativeAutomationDurationPrefixRootTests.stimulus
    data = literal.NativeAutomationDurationPrefixRootTests.data

    async def _capability(self):
        native = await self.f.hass.async_add_executor_job(
            self.module('native_automation_state_binding')._Native, self.f.hass)
        return await self.f.hass.async_add_executor_job(
            self.module('native_automation_duration_binding').NativeAutomationDurationCapability,
            self.f.hass, native, True, True)

    async def test_completed_dict_root_write_before_holder_restores_exact_zero_api(self):
        await self._partial_fault('root')

    async def test_completed_holder_write_before_approval_restores_exact_zero_api(self):
        await self._partial_fault('holder')

    async def test_completed_approval_write_restores_exact_zero_api(self):
        await self._partial_fault('approval')

    async def test_foreign_untouched_dict_sibling_after_root_write_refuses_zero_undo(self):
        await self._partial_fault('root', foreign='sibling')

    async def test_foreign_dict_order_after_root_write_refuses_zero_undo(self):
        await self._partial_fault('root', foreign='order')

    async def test_root_abort_followed_by_real_cancel_expiry_and_action(self):
        await self._partial_fault('root', natural_expiry=True)

    async def _partial_fault(self, seam, *, foreign=None, natural_expiry=False):
        from homeassistant.core import Context
        from homeassistant.core import callback
        from homeassistant.helpers import event
        from homeassistant.helpers import issue_registry as ir, template
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime, mapping, uuids, _, original = await self.data()
        hass, path = self.f.hass, self.f.root
        old, new = next(iter(mapping.items()))
        self.assertEqual(len(mapping), 1)
        body = ("{{ 5 if trigger.to_state.attributes.quick else 600 + states('" + old
                + "')|int }}" if natural_expiry else
                "{{ 600 + states('" + old + "')|int }}")
        target_body = body.replace(old, new)
        (path / 'mixed_automations.yaml').write_text(
            '- id: mixed_source\n  alias: Structured partial write fault\n  initial_state: true\n'
            '  variables:\n    source: '+old+'\n  triggers:\n'
            '  - trigger: state\n    entity_id: '+old+'\n    from: "16"\n    to: "17"\n'
            '    for:\n      seconds: "'+body+'"\n      milliseconds: 0\n'
            '  actions:\n  - event: structured_partial_action\n'
            '    event_data:\n      value: "{{ states(source) }}"\n'
            '  - action: script.mixed_source\n')
        (path / 'mixed_scripts.yaml').write_text(
            'mixed_source:\n  variables:\n    source: '+old+'\n  sequence:\n'
            '  - event: structured_partial_script\n'
            '    event_data:\n      value: "{{ states(source) }}"\n')
        for domain in ('script', 'automation'):
            await hass.services.async_call(domain, 'reload', {}, blocking=True, context=Context())
        await hass.async_block_till_done()
        issues = ir.async_get(hass)
        store = issues._store
        if any(value is not None for value in
               (store._data, store._delay_handle, store._unsub_final_write_listener)):
            await store.async_save(issues._data_to_save())
        await hass.async_block_till_done()
        self.assertTrue(all(value is None for value in
            (store._data, store._delay_handle, store._unsub_final_write_listener)))
        for name in ('mixed_automations.yaml', 'mixed_scripts.yaml'):
            original[name] = (path / name).read_bytes()
        source = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities
                      if e.unique_id == 'mixed_source')
        script = next(e for e in hass.data[DATA_INSTANCES]['script'].entities
                      if e.unique_id == 'mixed_source')
        duration = self.module('native_automation_duration_binding')
        configured_module = self.module('native_automation_configured_duration')
        transport_type = self.module('native_automation_config_transport').NativeAutomationConfigTransport
        graph_type = self.module('native_automation_owned_graph').OwnedAutomationGraph
        root_type = self.module('native_template_state_server').NativeTemplateStateServer
        graph = self.module('native_script_config_transport')._graph
        log_type = duration._PreEffectLog
        cap = await self._capability()
        configured = source._trigger_config[0]['for']
        self.assertIs(type(configured), dict)
        leaf = configured['seconds']
        self.assertIs(type(leaf), template.Template)
        self.assertEqual(leaf.template, body)
        root_before, leaf_before = graph(configured), graph(leaf)
        items_before = tuple(configured.items())
        self.assertEqual(tuple(configured), ('seconds', 'milliseconds'))
        static = configured['milliseconds']
        if natural_expiry:
            self.producer._attr_extra_state_attributes = {'quick': False}
        await self.stimulus(16)
        await self.stimulus(17)
        await hass.async_block_till_done()
        # Independent native Template tracker on a distinct real producer.
        # Its jobs are foreign to the cutover and its native renders are a
        # behavioral control after rollback.
        control = type(self.producer)()
        control._attr_unique_id = 'structured-partial-independent-control'
        control._attr_name = 'Structured partial independent control'
        control._attr_native_value = 17
        await hass.data[DATA_INSTANCES]['sensor'].async_add_entities([control])
        control_sensor = template.Template("{{ states('" + control.entity_id + "') | int(0) * 2 }}", hass)
        control_binary = template.Template("{{ is_state('" + control.entity_id + "', '5') }}", hass)
        control_results = {}
        @callback
        def control_update(_event, results):
            for item in results:
                control_results[item.template] = item.result
        control_tracker = event.async_track_template_result(hass,
            [event.TrackTemplate(control_sensor, None), event.TrackTemplate(control_binary, None)],
            control_update)
        self.addCleanup(control_tracker.async_remove)
        control_tracker.async_refresh()
        await hass.async_block_till_done()
        physical = hass.states.get(old)
        self.assertEqual(physical.state, '17')
        binding = cap.capture(source)
        self.assertIs(binding.config['for'], configured)
        self.assertIs(binding.listener_cells['time_delta'], configured)
        pending, = cap.pending(binding)[0]
        self.assertIsNone(pending['own'])
        self.assertEqual(binding.listener_cells['period'][old].total_seconds(), 617)

        def pending_image():
            history = duration._cells(pending['action'])
            return (pending['clear'], pending['cancel'], pending['keys'], tuple(pending['keys']),
                    pending['cancel_job'], pending['cancel_job'].target, pending['handle'],
                    pending['handle'].when(), pending['handle']._context,
                    pending['timer_job'], pending['same_job'], pending['action'],
                    history['from_s'], history['to_s'], history['event'], history['event'].context,
                    binding.listener_cells['period'][old])

        native_pending = pending_image()
        callbacks = binding.callbacks
        callback_before = tuple((key, members, tuple(members)) for key, members in callbacks.items())
        callback_checkpoints = []

        snapshots = {}

        def observe_callbacks(label):
            snapshots[label] = tuple((key, members, tuple(members)) for key, members in callbacks.items())
            # Read native objects and ordered jobs only; never mutate listeners.
            callback_checkpoints.append((label, tuple(
                (key, id(members), tuple(id(job) for job in members))
                for key, members in callbacks.items())))

        observe_callbacks('initial')

        def native_graphs():
            return (graph(source.raw_config), graph(source._trigger_config),
                    graph(source.action_script.sequence), graph(source.action_script.variables),
                    graph(script.raw_config), graph(script.script.sequence), graph(script.script.variables))

        native_before = native_graphs()
        await self._flush_documents()
        actions, scripts = [], []
        self.addCleanup(hass.bus.async_listen('structured_partial_action', actions.append))
        self.addCleanup(hass.bus.async_listen('structured_partial_script', scripts.append))
        codes = {
            'root': root_type._install.__code__, 'install': transport_type.install.__code__,
            'template_install': self.module('native_template_binding_transport').NativeTemplateBindingTransport.install_before_registry.__code__,
            'graph_plan': graph_type.plan_retire.__code__, 'graph_undo': graph_type.apply_retire.__code__,
            'log_plan': log_type.plan.__code__, 'log_undo': log_type.undo.__code__,
            'template_restore': self.module('native_template_binding_transport').NativeTemplateBindingTransport._restore_before_effect.__code__,
            'scene_close': self.module('native_empty_scene').NativeEmptySceneFence.close.__code__,
            'view': configured_module.ConfiguredRollbackView.__init__.__code__,
            'handoff': self.module('native_source_handoff').NativeSourceHandoff.mutate.__code__,
            'emission': self.module('native_rename_emission_classifier').NativeRenameEmissions.mutate.__code__,
            'registry': self.module('native_registry').NativeRegistryIO.mutate.__code__,
        }
        write_code = log_type.write.__code__
        roots, transfers, injected, root_writes, api = [], [], [], [], []
        template_before = []
        scene_before, scene_close_start, scene_close_end, bus_checkpoints = [], [], [], []
        graph_undos, log_undos, plan_returns, views, graph_plans, refusals, order = [], [], [], [], [], [], []
        template_restores = []

        class FixtureStructuredPartialWriteFault(RuntimeError):
            pass

        def observe_bus(label):
            if template_before and scene_before:
                fence, owned = scene_before[0]
                bus_checkpoints.append(_bus_diagnostic(hass, label, fence, owned, template_before[0][0]))

        def slots(log):
            return tuple((key, log_type.token(kind, log_type.read(kind, owner)))
                         for key, (kind, owner, _) in log.slots.items())

        def is_config_log(local):
            return bool(transfers) and local.get('self') is getattr(transfers[0], '_config_log', None)

        def started(code, offset):
            local = sys._getframe(1).f_locals
            actor = local.get('self')
            if code is codes['root'] and actor.hass is hass:
                roots.append(actor)
                observe_callbacks('root_install')
            elif code is codes['template_install'] and actor.hass is hass:
                self.assertEqual(template_before, [])
                census, jobs, bus_entries = _native_template_census(self, actor)
                template_before.append((actor, census, jobs, bus_entries, _callback_image(hass),
                                        _other_bus_image(hass)))
                self.assertEqual(len(roots), 1)
                scene_before.append(_scene_fence_census(self, roots[0], hass))
                observe_bus('template_preinstall')
            elif code is codes['install'] and actor.observer.hass is hass:
                transfers.append(actor)
                observe_callbacks('transport_install')
                observe_bus('transport_install')
                self.assertEqual(len(transfers), 1)
                self.assertEqual(actor.phase, 'captured')
                edge, = actor.duration_edges
                self.assertIs(edge.before, configured)
                self.assertIs(edge.after, configured)
                self.assertEqual(graph(configured), root_before)
                self.assertTrue(edge.paths)
                self.assertEqual(len(edge.owners), 1, 'native pending has no historical OWN holder')
                self.assertTrue(any(kind == 'dict' and owner is configured
                                    for kind, owner, _, _ in actor.operations))
            elif transfers and code is codes['graph_undo'] and actor is transfers[0].graph:
                graph_undos.append(local['plan'])
                order.append('graph_undo')
            elif injected and code is codes['log_undo']:
                log_undos.append(local['plan'])
                order.append('log_undo')
            elif injected and code is codes['template_restore']:
                template_restores.append(actor)
            elif (injected and code is codes['scene_close'] and scene_before
                    and actor is scene_before[0][0]):
                scene_close_start.append((actor.closed, len(actor.removers),
                                          _fault_image(hass, template_before[0][0])))
                observe_bus('scene_close_start')
            elif transfers and code is codes['graph_plan'] and actor is transfers[0].graph:
                self.assertIs(local['configured_view'], views[-1])
                self.assertTrue(local['before_attempt'])
                order.append('graph_plan_start')
            elif code in (codes['handoff'], codes['emission'], codes['registry']):
                if getattr(actor, 'hass', None) is hass:
                    api.append((code, actor))

        def returned(code, offset, result):
            local = sys._getframe(1).f_locals
            if code is codes['template_restore'] and transfers:
                observe_callbacks('template_restore_return')
                observe_bus('template_restore_return')
            elif code is codes['graph_undo'] and transfers and local.get('self') is transfers[0].graph:
                observe_callbacks('graph_undo_return')
                observe_bus('graph_undo_return')
            elif code is codes['log_undo'] and injected:
                observe_callbacks('log_undo_return')
                observe_bus('log_undo_return')
            elif (code is codes['scene_close'] and injected and scene_before
                    and local.get('self') is scene_before[0][0]):
                actor = local['self']
                scene_close_end.append((actor.closed, len(actor.removers),
                                        _fault_image(hass, template_before[0][0])))
                observe_bus('scene_close_end')
            if code is codes['log_plan'] and is_config_log(local):
                shadow = local['shadow']
                transfer = transfers[0]
                self.assertEqual((graph_undos, log_undos), ([], []))
                for key, (kind, owner, before) in transfer._config_log.slots.items():
                    self.assertEqual(log_type.token(kind, shadow[key]), log_type.token(kind, before))
                self.assertEqual(log_type.token('dict', shadow[('dict', id(configured))]),
                                 log_type.token('dict', items_before))
                plan_returns.append((result, dict(shadow)))
                order.append('log_plan_return')
                return
            if code is codes['view'] and transfers:
                view = local['self']
                self.assertIs(type(view), configured_module.ConfiguredRollbackView)
                self.assertEqual(view.shadow, plan_returns[-1][1])
                self.assertEqual(log_type.token('dict', view.slots[id(configured)]),
                                 log_type.token('dict', items_before))
                for holder, before, _ in transfers[0].duration_edges[0].owners:
                    self.assertIs(view.images[id(holder)][0], holder)
                    self.assertIs(view.images[id(holder)][1], before)
                views.append(view)
                order.append('view_return')
                return
            if code is codes['graph_plan'] and transfers and local.get('self') is transfers[0].graph:
                graph_plans.append(result)
                order.append('graph_plan_return')
                return
            if code is not write_code or not is_config_log(local) or injected:
                return
            transfer = transfers[0]
            edge, = transfer.duration_edges
            holder, before, after = edge.owners[0]
            kind, owner = local['kind'], local['owner']
            self.assertIs(transfer._config_log.records[-1][3], True)
            if kind == 'dict' and owner is configured:
                root_writes.append((kind, owner))
            selected = (seam == 'root' and kind == 'dict' and owner is configured
                or seam == 'holder' and kind == 'image' and owner is holder
                or seam == 'approval' and kind == 'entry'
                    and owner[0] is holder.binding._duration.configured_images and owner[1] is holder)
            if not selected:
                return
            self.assertEqual(len(root_writes), 1)
            self.assertFalse(transfer.graph.live)
            self.assertTrue(all(route.duration_operations is None for route in transfer.graph.routes))
            self.assertEqual(graph(configured), edge.after_graph)
            replacement = configured['seconds']
            self.assertIsNot(replacement, leaf)
            self.assertEqual(replacement.template, target_body)
            self.assertIs(holder.config['for'], configured)
            self.assertIs(holder.cell.cell_contents, configured)
            self.assertIs(holder.snapshot['time_delta'], configured)
            self.assertIs(holder.image, before if seam == 'root' else after)
            self.assertIs(holder.binding._duration.configured_images[holder],
                          after if seam == 'approval' else before)
            if foreign == 'sibling':
                configured['milliseconds'] = 1
                self.assertIs(configured['seconds'], replacement)
            elif foreign == 'order':
                # Keep every exact key/value object, alter only insertion order.
                key = next(key for key in configured if key == 'seconds')
                value = configured.pop(key)
                configured[key] = value
                self.assertEqual(tuple(configured), ('milliseconds', 'seconds'))
            injected.append({'transfer': transfer, 'edge': edge, 'holder': holder,
                             'before': before, 'after': after, 'replacement': replacement,
                             'slots': slots(transfer._config_log), 'native': native_graphs(),
                             'root': graph(configured), 'replacement_graph': graph(replacement),
                             'fault_graph': _fault_image(hass, template_before[0][0])})
            observe_callbacks('fault')
            observe_bus('fault')
            order.append('fault')
            raise FixtureStructuredPartialWriteFault('completed configured dict '+seam+' write')

        def raised(code, offset, error):
            # RAISE reports the original _need frame, not propagation through
            # plan(). Attribute only the immediate original configured-log call.
            if code is not duration._need.__code__:
                return
            frame = sys._getframe(1)
            try:
                caller = frame.f_back
                if (caller is not None and caller.f_code is codes['log_plan']
                        and is_config_log(caller.f_locals)):
                    refusals.append(str(error))
            finally:
                del frame

        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())

        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'actual Root structured partial journal fault')
        error = result = None
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, returned)
            sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, raised)
            for code in codes.values():
                sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            for name in ('log_plan', 'view', 'graph_plan', 'graph_undo', 'log_undo', 'template_restore', 'scene_close'):
                sys.monitoring.set_local_events(tool, codes[name],
                    sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
            sys.monitoring.set_local_events(tool, codes['log_plan'],
                sys.monitoring.events.PY_START | sys.monitoring.events.PY_RETURN)
            sys.monitoring.set_events(tool, sys.monitoring.events.RAISE)
            sys.monitoring.set_local_events(tool, write_code, sys.monitoring.events.PY_RETURN)
            with mixed.native_diagnostics(self.module):
                monitors = tuple(sys.monitoring.get_tool(k) for k in range(6))
                try:
                    result = await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
                except (RuntimeError, ValueError, self.module('transaction').Refused) as exc:
                    error = exc
                self.assertEqual(tuple(sys.monitoring.get_tool(k) for k in range(6)), monitors)
            self.assertEqual(len(roots), 1)
            self.assertEqual(len(transfers), 1, 'earlier admission error is not a reached partial write')
            self.assertEqual(len(injected), 1, 'original selected completed-write seam is mandatory')
            self.assertEqual(len(template_before), 1, 'actual Template cutover must be reached')
            self.assertIsNone(result)
            self.assertIsNotNone(error)
            saved = injected[0]
            transfer, edge = saved['transfer'], saved['edge']
            observe_callbacks('returned')
            observe_bus('returned')
            self.assertEqual((api, actions, scripts), ([], [], []))
            self.assertIs(hass.states.get(old), physical)
            self.assertIsNone(hass.states.get(new))
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
            for name, content in original.items():
                self.assertEqual((path / name).read_bytes(), content)
            self.assertEqual(pending_image(), native_pending)
            self.assertTrue(all(actual is expected for index, (actual, expected) in
                enumerate(zip(pending_image(), native_pending, strict=True)) if index not in (3, 7)))
            self.assertFalse(pending['handle'].cancelled())
            self.assertGreater(pending['handle'].when(), hass.loop.time(), 'actual native timer must remain pending')
            template_transport, census_before, jobs_before, bus_before, callbacks_before, other_bus_before = template_before[0]
            self.assertEqual(len(scene_before), 1)
            fence, owned_fence = scene_before[0]
            self.assertEqual(len(scene_close_start), 1, 'same session fence must actually close once')
            self.assertEqual(len(scene_close_end), 1)
            self.assertEqual(scene_close_start[0][:2], (False, 6))
            self.assertEqual(scene_close_end[0][:2], (True, 0))
            self.assertTrue(fence.closed)
            self.assertEqual(fence.removers, [])
            self.assertEqual(scene_close_end[0][2],
                             _without_scene_fence_fault(self, scene_close_start[0][2], owned_fence),
                             'closing the exact own fence may remove only its six registrations')
            if foreign:
                self.assertEqual(scene_close_start[0][2], saved['fault_graph'],
                                 'foreign refusal preserves the complete fault graph before fence close')
                self.assertEqual(_fault_image(hass, template_transport),
                                 _without_scene_fence_fault(self, saved['fault_graph'], owned_fence),
                                 'only this proven own fence cleanup may follow foreign refusal')
            else:
                census_after, jobs_after, bus_after = _native_template_census(self, template_transport)
                _assert_template_restored(self, census_before, census_after)
                state_fence, = (entry for name, _, entry in owned_fence if name == 'state_changed')
                self.assertNotIn(id(state_fence), bus_before)
                self.assertEqual(sum(item[0] is state_fence for item in callbacks_before[2]), 1)
                _assert_foreign_subsequence(self, callbacks_before, jobs_before, bus_before | {id(state_fence)},
                                            _callback_image(hass), jobs_after, bus_after)
                self.assertEqual(_other_bus_image(hass),
                                 _without_scene_fence(self, other_bus_before, owned_fence, include_state=False),
                                 'unrelated bus listeners and their order must stay unchanged')
            self.assertIs(edge.binding.config['for'], configured)
            self.assertEqual(graph(leaf), leaf_before)
            if foreign:
                self.assertEqual(transfer.phase, 'failed_before_attempt_cleanup_unknown')
                self.assertIn('automation_state_binding_duration_rollback_slot_changed', refusals,
                              'actual whole-log preflight must reject the foreign root slot')
                self.assertEqual((graph_undos, log_undos, plan_returns, views, graph_plans),
                                 ([], [], [], [], []), 'zero undo, no admitted shadow view')
                self.assertEqual(template_restores, [], 'foreign refusal must never start Template restore')
                self.assertEqual(order, ['fault'])
                self.assertEqual(slots(transfer._config_log), saved['slots'])
                self.assertEqual(native_graphs(), saved['native'], 'preserve foreign edit and every own prefix write')
                self.assertEqual(graph(configured), saved['root'])
                self.assertIs(configured['seconds'], saved['replacement'])
                self.assertEqual(graph(saved['replacement']), saved['replacement_graph'])
                self.assertIs(saved['holder'].image, saved['before'])
                self.assertIs(saved['holder'].binding._duration.configured_images[saved['holder']], saved['before'])
                if foreign == 'sibling':
                    self.assertEqual(configured['milliseconds'], 1)
                    self.assertEqual(tuple(configured), tuple(key for key, _ in items_before))
                else:
                    self.assertIs(configured['milliseconds'], static)
                    self.assertEqual(tuple(configured), ('milliseconds', 'seconds'))
                return
            self.assertEqual(transfer.phase, 'failed_before_attempt')
            self.assertEqual(refusals, [])
            self.assertEqual((len(plan_returns), len(views), len(graph_plans), len(graph_undos), len(log_undos)),
                             (1, 1, 1, 1, 1))
            self.assertEqual(template_restores, [template_transport])
            self.assertEqual(order, ['fault', 'log_plan_return', 'view_return', 'graph_plan_start',
                                     'graph_plan_return', 'graph_undo', 'log_undo'])
            self.assertIs(log_undos[0], plan_returns[0][0])
            self.assertIs(graph_undos[0], graph_plans[0])
            for _, (kind, owner, before) in transfer._config_log.slots.items():
                self.assertEqual(log_type.token(kind, log_type.read(kind, owner)), log_type.token(kind, before))
            self.assertEqual(graph(configured), root_before)
            self.assertEqual(log_type.token('dict', tuple(configured.items())), log_type.token('dict', items_before))
            self.assertIs(configured['seconds'], leaf)
            self.assertIs(configured['milliseconds'], static)
            self.assertEqual(native_graphs(), native_before)
            for holder, before, _ in edge.owners:
                self.assertFalse(any(
                    kind == 'cell_contents' and owner is holder.cell
                    or kind == 'entry' and owner[0] is holder.snapshot and owner[1] == 'time_delta'
                    for key, _, _, _ in transfer._config_log.records
                    for kind, owner, _ in (transfer._config_log.slots[key],)),
                    'same-root dict uses its one nested operation, no independent cell/snapshot writes')
                self.assertIs(holder.image, before)
                self.assertIs(holder.config['for'], configured)
                self.assertIs(holder.cell.cell_contents, configured)
                self.assertIs(holder.snapshot['time_delta'], configured)
                self.assertIs(holder.binding._duration.configured_images[holder], before)
                holder.check()
            restored = cap.capture(source)
            self.assertEqual(restored.original_keys, [old])
            self.assertIs(restored.listener, binding.listener)
            self.assertIs(restored.keyed_job, binding.keyed_job)
            self.assertIs(restored.keyed_job.target, restored.listener)
            restored_pending, = cap.pending(restored)[0]
            for name in ('clear', 'cancel', 'keys', 'cancel_job', 'cancel_listener', 'predicate',
                         'handle', 'timer_job', 'same_job', 'action', 'entity_cell'):
                self.assertIs(restored_pending[name], pending[name], name)
            self.assertIs(restored.listener_cells['period'][old], native_pending[-1])
            phase = roots[0].consumers.phases
            normal = self.module('yaml_validation_worker')._normal
            observer = self.module('yaml_runtime_observer').NativeYAMLRuntimeReadback(hass, owned_automation=phase.graph)
            for domain, rows in phase.program.rows(frozenset()):
                self.assertEqual(normal(observer._projection(domain)[0]), rows)
            # Actual post-abort source event: both original consumer instances
            # must render normally and the retained Automation H must cancel.
            await self.stimulus(5)
            control._attr_native_value = 5
            control.async_write_ha_state()
            await hass.async_block_till_done()
            self.assertEqual(str(control_results[control_sensor]), '10')
            self.assertIs(control_results[control_binary], True)
            for entity, *_ in census_before:
                state = hass.states.get(entity.entity_id)
                self.assertEqual(state.state, str(control_results[control_sensor])
                                 if entity.entity_id.startswith('sensor.') else
                                 ('on' if control_results[control_binary] else 'off'))
            self.assertTrue(pending['handle'].cancelled(), 'native state change must cancel the original H')
            self.assertEqual((actions, scripts), ([], []))
            if natural_expiry:
                from homeassistant.core import callback
                ready_action, ready_script = asyncio.Event(), asyncio.Event()
                @callback
                def action_seen(_event):
                    ready_action.set()
                @callback
                def script_seen(_event):
                    ready_script.set()
                self.addCleanup(hass.bus.async_listen('structured_partial_action', action_seen))
                self.addCleanup(hass.bus.async_listen('structured_partial_script', script_seen))
                self.producer._attr_extra_state_attributes = {'quick': True}
                await self.stimulus(16)
                await self.stimulus(17)
                again = cap.capture(source)
                next_pending, = cap.pending(again)[0]
                self.assertIsNot(next_pending['handle'], pending['handle'])
                self.assertIsNone(next_pending['own'])
                self.assertFalse(next_pending['handle'].cancelled())
                self.assertEqual(again.listener_cells['period'][old].total_seconds(), 5)
                event = duration._cells(next_pending['action'])['event']
                control._attr_native_value = 17
                control.async_write_ha_state()
                await hass.async_block_till_done()
                await asyncio.wait_for(asyncio.gather(ready_action.wait(), ready_script.wait()), 15)
                await hass.async_block_till_done()
                self.assertEqual((len(actions), len(scripts)), (1, 1))
                self.assertEqual(str(actions[0].data['value']), '17')
                self.assertEqual(str(scripts[0].data['value']), '17')
                self.assertEqual(actions[0].context.parent_id, event.context.id)
                self.assertEqual(scripts[0].context.user_id, actions[0].context.user_id)
                self.assertEqual(cap.pending(cap.capture(source))[0], ())
                self.assertEqual(str(control_results[control_sensor]), '34')
                self.assertIs(control_results[control_binary], False)
                for entity, *_ in census_before:
                    state = hass.states.get(entity.entity_id)
                    self.assertEqual(state.state, str(control_results[control_sensor])
                                     if entity.entity_id.startswith('sensor.') else
                                     ('on' if control_results[control_binary] else 'off'))
                # End the representative profile through each actual native
                # unsubscribe. Only its authenticated subscriptions may go.
                live_census, live_jobs, live_bus = _native_template_census(self, template_transport)
                live_graph = _callback_image(hass)
                for entity, tracker, *_ in live_census:
                    tracker.async_remove()
                    entity._on_remove.remove(tracker.async_remove)
                _assert_foreign_subsequence(self, live_graph, live_jobs, live_bus,
                                            _callback_image(hass), set(), set())
        finally:
            print('STRUCTURED_PARTIAL_CALLBACK_CHECKPOINTS', callback_checkpoints)
            print('STRUCTURED_PARTIAL_BUS_OWNERSHIP_CHECKPOINTS', bus_checkpoints)
            sys.monitoring.set_events(tool, 0)
            for code in (*codes.values(), write_code):
                sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_RETURN, None)
            sys.monitoring.register_callback(tool, sys.monitoring.events.RAISE, None)
            sys.monitoring.free_tool_id(tool)
