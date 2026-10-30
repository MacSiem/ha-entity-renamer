"""UNEXECUTED actual-Root duration acceptance, for both pinned Core images.

Only fixture configuration/producer data and read-only sys.monitoring are used.
Deadlines/contexts are observed, never changed. Pending expiry uses real time.
"""
import asyncio
import sys
import unittest
import test_native_template_mixed_yaml_server as mixed

@unittest.skipUnless(mixed.server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeAutomationDurationPrefixRootTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_native_history_executor_and_pending_repairs_diagnostic(self):
        import json
        import threading
        from homeassistant.components.recorder import get_instance
        from homeassistant.helpers import issue_registry as ir
        _, mapping, _, _, _ = await self.data()
        hass = self.f.hass
        instance = get_instance(hass)
        issues = ir.async_get(hass)
        def status():
            store = issues._store
            return {'executor': type(instance._db_executor).__name__,
                    'ready': instance.async_db_ready.done(),
                    'issue_count': len(issues.issues),
                    'issue_keys': sorted(str(key) for key in issues.issues),
                    'issue_save_pending': any(x is not None for x in
                        (store._data, store._delay_handle, store._unsub_final_write_listener))}
        before = status()
        observations = []
        history = self.module('history_guard')
        code = history._read_session.__code__
        tool = next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool, 'read-only history executor diagnostic')
        def started(actual, offset):
            frame = sys._getframe(1)
            pool = frame.f_locals['session'].get_bind().pool
            thread = threading.get_ident()
            observations.append({'recorder_worker': thread in instance.recorder_and_worker_thread_ids,
                'pool_worker': thread in pool.recorder_and_worker_thread_ids,
                'same_thread_set': pool.recorder_and_worker_thread_ids is instance.recorder_and_worker_thread_ids,
                'executor': type(instance._db_executor).__name__})
        try:
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, started)
            sys.monitoring.set_local_events(tool, code, sys.monitoring.events.PY_START)
            await history.NativeHistoryGuard(self.module('recorder_guard').NativeRecorderGuard(hass)).capture(mapping)
        finally:
            sys.monitoring.set_local_events(tool, code, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
        print('NATIVE_HISTORY_EXECUTOR_DIAGNOSTIC ' + json.dumps(
            {'before': before, 'reads': observations, 'after': status()}, sort_keys=True))
        self.assertTrue(observations)
        self.assertTrue(all(row['recorder_worker'] and row['pool_worker'] and row['same_thread_set']
                            for row in observations))

    async def _capability(self):
        native = await self.f.hass.async_add_executor_job(self.module('native_automation_state_binding')._Native, self.f.hass)
        return await self.f.hass.async_add_executor_job(
            self.module('native_automation_duration_binding').NativeAutomationDurationCapability, self.f.hass, native)

    async def test_literal_config_without_pending_forward_inverse_then_native_expiry(self):
        await self._duration_case(mode='configured')

    async def test_pending_neutral_same_handle_deadline_context_forward_fresh_inverse_and_control(self):
        await self._duration_case(mode='pending')

    async def test_genuine_new_id_change_cancels_native_pending_and_fresh_inverse_keeps_terminal(self):
        await self._duration_case(mode='cancel')

    async def test_genuine_17_to_29_own_duration_crosses_fresh_inverse_and_expires_once(self):
        await self._duration_case(mode='meaningful')

    async def test_meaningful_pair_cancels_old_native_pending_and_starts_one_own_duration(self):
        await self._duration_case(mode='replace')

    async def test_queued_foreign_native_event_cancels_new_own_duration_once(self):
        await self._duration_case(mode='foreign')

    async def test_from_only_pending_keeps_native_predicate_and_history(self):
        await self._duration_case(mode='from_only')

    async def test_attribute_pending_preserves_native_handle_and_authentic_states(self):
        await self._duration_case(mode='attribute')

    async def test_pre_effect_authorization_loss_restores_only_own_duration_transport(self):
        await self._duration_case(mode='rollback')

    async def test_foreign_cancel_route_is_refused_before_effect_without_cleaning_it(self):
        await self._duration_case(mode='foreign_route')

    async def test_native_match_all_overwrite_orphan_refuses_before_effect_and_is_preserved(self):
        await self._duration_case(mode='orphan')

    async def test_foreign_native_main_and_existing_cancel_share_one_dispatch(self):
        await self._duration_case(mode='transient')

    async def test_multi_id_pair_preserves_other_pending_and_rejects_retired_binding_drift(self):
        await self._duration_case(mode='multi')

    async def test_multi_id_queued_foreign_event_preserves_other_pending(self):
        await self._duration_case(mode='multi_foreign')

    async def test_partial_duration_keys_write_restores_before_native_api(self):
        await self._duration_case(mode='partial_keys')

    async def test_partial_duration_map_clear_restores_before_native_api(self):
        await self._duration_case(mode='partial_map')

    async def test_partial_duration_wrapper_registration_restores_before_native_api(self):
        await self._duration_case(mode='partial_wrapper')

    async def test_partial_retained_foreign_route_conflict_is_preserved_before_any_cleanup(self):
        await self._duration_case(mode='partial_foreign')

    async def test_partial_retained_two_routes_restore_shared_native_callback_lists(self):
        await self._duration_case(mode='partial_multi')

    async def test_native_pending_expiry_during_active_root_keeps_one_action_and_fresh_inverse(self):
        await self._duration_case(mode='active_expiry')

    async def _duration_case(self, *, mode):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_component import DATA_INSTANCES
        runtime,mapping,uuids,_,original = await self.data()
        hass,root = self.f.hass,self.f.root
        old,new = next(iter(mapping.items()))
        multi = mode in ('multi','multi_foreign')
        partial = mode.startswith('partial_')
        active_expiry = mode=='active_expiry'
        foreign = mode in ('foreign','transient','multi_foreign')
        meaningful = active_expiry or mode in ('meaningful','foreign','replace','multi','multi_foreign')
        pending = partial or active_expiry or mode in ('pending','cancel','from_only','attribute','rollback','foreign_route','orphan','replace','transient')
        duration = 30 if active_expiry else 480 if pending or multi else 180 if meaningful else 2
        other_id = 'sensor.duration_other'
        original_keys = [old,other_id] if multi else [old]
        target_keys = [new,other_id] if multi else [new]
        # Every duration below is literal YAML, validated by ORIGINAL Core.
        if mode=='orphan':
            filters = ''
        elif mode=='transient':
            filters = '    not_from: "0"\n'
        elif active_expiry:
            filters = '    from: "16"\n'
        elif mode=='replace':
            filters = '    from: ["16", "17"]\n    to: ["17", "29"]\n'
        elif meaningful:
            filters = '    from: "17"\n    to: "29"\n'
        elif mode == 'attribute':
            filters = '    attribute: phase\n    from: "16"\n    to: "17"\n'
        elif mode == 'from_only':
            filters = '    from: "16"\n'
        else:
            filters = '    from: "16"\n    to: "17"\n'
        path = root/'mixed_automations.yaml'
        path.write_text(
            '- id: mixed_source\n  alias: Literal duration source\n  initial_state: true\n'
            '  variables:\n    source: '+old+'\n    alias: "{{ source }}"\n'
            '  triggers:\n  - trigger: state\n    entity_id: ['+', '.join(original_keys)+']\n'+filters+
            '    for: {seconds: '+str(duration)+'}\n'
            '  actions:\n  - event: duration_source_result\n    event_data:\n'
            '      bound: "{{ trigger.entity_id }}"\n'
            '      previous: "{{ trigger.from_state.state }}"\n'
            '      current: "{{ trigger.to_state.state }}"\n'
            '      from_id: "{{ trigger.from_state.entity_id }}"\n'
            '      to_id: "{{ trigger.to_state.entity_id }}"\n'
            '      seconds: "{{ trigger.for.total_seconds() }}"\n'
            '      configured: "{{ states(alias) }}"\n'
            '  - action: script.mixed_source\n'
            '- id: duration_control\n  alias: Independent native duration control\n  initial_state: true\n'
            '  triggers:\n  - trigger: state\n    entity_id: sensor.duration_control\n'
            '    from: "16"\n    to: "17"\n    for: {seconds: '+str(duration)+'}\n'
            '  actions:\n  - event: duration_control_result\n')
        if mode=='partial_multi':
            path.write_text(path.read_text()+
                '- id: duration_second\n  alias: Second affected native duration\n  initial_state: true\n'
                '  triggers:\n  - trigger: state\n    entity_id: '+old+'\n'
                '    from: "16"\n    to: "17"\n    for: {seconds: '+str(duration)+'}\n'
                '  actions:\n  - action: script.mixed_source\n')
        if active_expiry:
            # A genuine second Automation action keeps the native action drain
            # active past the existing timer's deadline. Its literal delay is
            # executed by Core; no clock, Handle or callback is manipulated.
            path.write_text(path.read_text()+
                '- id: duration_slow\n  alias: Native action expiry overlap\n  initial_state: true\n'
                '  triggers:\n  - trigger: state\n    entity_id: '+old+'\n'
                '    from: "17"\n    to: "29"\n'
                '  actions:\n  - delay: {seconds: 40}\n'
                '  - event: duration_slow_result\n    event_data:\n'
                '      value: "{{ states(\''+old+'\') }}"\n')
        script_path = root/'mixed_scripts.yaml'
        script_path.write_text('mixed_source:\n  variables:\n    source: '+old+'\n  sequence:\n'
            '  - event: duration_script_result\n    event_data:\n      value: "{{ states(source) }}"\n')
        for domain in ('script','automation'):
            await hass.services.async_call(domain,'reload',{},blocking=True,context=Context())
        await hass.async_block_till_done()
        # Native setup already has a pending repairs Store before ORIGINAL
        # (27734). Flush that actual data, as the cardinality fixture does;
        # never create an empty Store or ignore later directory conflicts.
        from homeassistant.helpers import issue_registry as ir
        issues = ir.async_get(hass)
        store = issues._store
        if any(value is not None for value in
               (store._data, store._delay_handle, store._unsub_final_write_listener)):
            await store.async_save(issues._data_to_save())
        await hass.async_block_till_done()
        self.assertTrue(all(value is None for value in
            (store._data, store._delay_handle, store._unsub_final_write_listener)))
        await self._flush_documents()
        for name in ('mixed_automations.yaml','mixed_scripts.yaml'):
            original[name]=(root/name).read_bytes()
        source = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities if e.unique_id=='mixed_source')
        control = next(e for e in hass.data[DATA_INSTANCES]['automation'].entities if e.unique_id=='duration_control')
        cap = await self._capability()
        actions,scripts,controls,pairs,roots,handoffs,slow = [],[],[],[],[],[],[]
        for name,events in (('duration_source_result',actions),('duration_script_result',scripts),
                            ('duration_control_result',controls),('duration_slow_result',slow)):
            self.addCleanup(hass.bus.async_listen(name,events.append))
        if mode == 'attribute':
            self.producer._attr_extra_state_attributes={'phase':'16'}
        await self.stimulus(16 if pending else 15)
        if mode == 'attribute':
            self.producer._attr_extra_state_attributes={'phase':'17'}
        await self.stimulus(17)
        await hass.async_block_till_done()
        if multi:
            hass.states.async_set(other_id,'17')
            hass.states.async_set(other_id,'29')
            await hass.async_block_till_done()
        physical=hass.states.get(old)
        self.assertEqual(physical.state,'17')
        before=cap.capture(source)
        all_saved=cap.pending(before)[0]
        other_pending=next((p for p in all_saved if p['key']==other_id),None)
        saved=tuple(p for p in all_saved if p['key']==old)
        second_before=second_pending=None
        if mode=='partial_multi':
            second=next(e for e in hass.data[DATA_INSTANCES]['automation'].entities
                        if e.unique_id=='duration_second')
            second_before=cap.capture(second)
            second_pending=cap.pending(second_before)[0][0]
            second_image=(second_pending['handle'],second_pending['handle'].when(),
                          second_pending['handle']._context,second_pending['cancel_job'].target)
        if multi:
            self.assertIsNotNone(other_pending)
            other_tuple=(other_pending['handle'],other_pending['handle'].when(),other_pending['handle']._context)
        if pending:
            self.assertEqual(len(saved),1)
            native_pending=saved[0]
            history=self.module('native_automation_duration_binding')._cells(native_pending['action'])
            native_tuple=(native_pending['clear'],native_pending['cancel'],native_pending['keys'],
                native_pending['cancel_job'],native_pending['handle'],native_pending['handle'].when(),
                native_pending['handle']._context,native_pending['timer_job'],native_pending['same_job'],
                native_pending['action'],history['from_s'],history['to_s'],history['event'],history['event'].context)
        else:
            self.assertEqual(saved,())
            native_tuple=None
        self.assertEqual((actions,scripts),([],[]))
        original_objects=(before.keyed_job,before.listener,before.action_job,before.unsubscribe,
            before.detach,before.remove,before.original_keys,before.config_ids,before.callbacks,source.action_script)
        # Start the independent native control alongside the retained native
        # pending, so the test waits one natural period rather than a second one.
        control_pending=None
        control_original=None
        if pending or multi:
            hass.states.async_set('sensor.duration_control','16')
            hass.states.async_set('sensor.duration_control','17')
            await hass.async_block_till_done()
            control_pending=cap.pending(cap.capture(control))[0][0]
            control_original=(control_pending['handle'],control_pending['handle'].when(),
                              control_pending['handle']._context)
        if mode=='foreign_route':
            native_pending['keys'].append('sensor.foreign_duration_route')
            before.callbacks['sensor.foreign_duration_route'].append(native_pending['cancel_job'])
            foreign_image=(tuple(native_pending['keys']),before.callbacks['sensor.foreign_duration_route'],
                           tuple(before.callbacks['sensor.foreign_duration_route']))
        if mode=='orphan':
            # Genuine native match_all overwrite: same state, different actual
            # producer attributes leaves the older native U/H alive in Core.
            self.producer._attr_extra_state_attributes={'orphan_fixture':1}
            self.producer.async_write_ha_state()
            await hass.async_block_till_done()
            orphan_map=before.listener_cells['unsub_track_same']
            replacement=orphan_map[old]
            self.assertIsNot(replacement,native_pending['clear'])
            self.assertFalse(native_pending['handle'].cancelled())
            with self.assertRaisesRegex(ValueError,'duration_orphan_'):
                cap.capture(source)
        strict=self.module('yaml_runtime_observer')._automation_registrations
        with self.assertRaises(ValueError):
            strict(hass,source) # Strict DEFAULT remains strict even for original native duration.
        await self._flush_documents()
        handoff_type=self.module('native_source_handoff').NativeSourceHandoff
        graph_type=self.module('native_automation_owned_graph').OwnedAutomationGraph
        root_type=self.module('native_template_state_server').NativeTemplateStateServer
        witness_type=self.module('native_rename_emission_classifier').NativeRenameEmissions
        duration_type=self.module('native_automation_duration_binding').NativeAutomationDurationCapability
        codes=(handoff_type.mutate.__code__,graph_type._action.__code__,root_type._install.__code__,
               witness_type.mutate.__code__,duration_type.start_pair.__code__)
        if partial:
            codes+= (self.module('native_automation_duration_binding')._PreEffectLog.write.__code__,)
        elif active_expiry:
            codes+= (native_pending['timer_job'].target.__code__,)
        tool=next(k for k in range(6) if sys.monitoring.get_tool(k) is None)
        sys.monitoring.use_tool_id(tool,'actual Root literal for acceptance')
        emitted=[]
        created=[]
        revoked=False
        faulted=False
        foreign_partial=None
        expiries=[]
        def guard():
            if revoked:
                raise PermissionError('fixture pre-effect authorization loss')
        def start(code,offset):
            nonlocal revoked
            frame=sys._getframe(1);actor=frame.f_locals.get('self')
            if active_expiry and code is codes[5] and frame.f_locals.get('job') is native_pending['same_job']:
                expiries.append(tuple(root.consumers.phases.graph.live for root in roots))
                live=[root for root in roots if root.consumers.phases.graph.live]
                self.assertEqual(len(live),1)
                node=live[0].consumers.phases.active[0]
                self.assertIs(live[0].chain.active,node.intent)
                self.assertEqual(node.phase,'registry_attempted')
            if code is codes[0] and actor.hass is hass:
                handoffs.append(actor)
                if len(handoffs)==1 and meaningful:
                    self.assertIs(hass.states.get(old),physical)
                    actor.source._attr_native_value=29
            elif code is codes[1] and actor.hass is hass:
                c=frame.f_locals['change'];pairs.append(c)
                self.assertIs(c.prior_state,physical)
                self.assertIsNone(c.event.data['old_state'])
                self.assertIs(c.new_state,c.event.data['new_state'])
            elif code is codes[2] and actor.hass is hass:
                roots.append(actor)
            elif code is codes[3] and actor.hass is hass and mode=='rollback':
                self.assertTrue(actor.automation_graph.live)
                self.assertFalse(actor.attempted)
                revoked=True
        def returned(code,offset,value):
            nonlocal faulted,foreign_partial
            frame=sys._getframe(1)
            actor=frame.f_locals.get('self')
            if partial and code is codes[5] and not faulted:
                kind,owner=frame.f_locals['kind'],frame.f_locals['owner']
                selected=(mode=='partial_keys' and kind=='list' and owner is native_pending['keys']
                    or mode in ('partial_map','partial_foreign') and kind=='dict' and owner is before.listener_cells['period']
                    and not owner
                    or mode=='partial_wrapper' and kind=='entry' and owner[1] is native_pending['cancel_job'])
                selected=selected or (mode=='partial_multi' and kind=='dict'
                    and owner is second_before.listener_cells['period'] and not owner)
                if selected:
                    faulted=True
                    if mode=='partial_foreign':
                        key='sensor.foreign_partial_duration'
                        native_pending['keys'].append(key)
                        members=before.callbacks[key]
                        members.append(native_pending['cancel_job'])
                        foreign_partial=(key,members,tuple(members),tuple(native_pending['keys']))
                    raise RuntimeError('fixture after actual duration write')
            if code is codes[4] and actor.hass is hass:
                created.extend(actor.pending(frame.f_locals['route'].binding)[0])
            if code is codes[0] and actor.hass is hass and foreign and not emitted:
                self.producer._attr_native_value=31
                self.producer.async_write_ha_state()
                emitted.append(hass.states.get(new))
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        async def check_rows(bound,active):
            phase=bound.consumers.phases
            sample=phase.observer.sample(bound.chain)
            self.assertEqual(sample.prefix.active,frozenset(active))
            expected=phase.program.rows(frozenset(active))
            normal=self.module('yaml_validation_worker')._normal
            observer=self.module('yaml_runtime_observer').NativeYAMLRuntimeReadback(hass,owned_automation=phase.graph)
            for domain,rows in expected:
                self.assertEqual(normal(observer._projection(domain)[0]),rows)
        try:
            sys.monitoring.register_callback(tool,sys.monitoring.events.PY_START,start)
            sys.monitoring.register_callback(tool,sys.monitoring.events.PY_RETURN,returned)
            for code in codes:
                sys.monitoring.set_local_events(tool,code,sys.monitoring.events.PY_START)
            sys.monitoring.set_local_events(tool,codes[4],sys.monitoring.events.PY_RETURN)
            if partial:
                sys.monitoring.set_local_events(tool,codes[5],sys.monitoring.events.PY_RETURN)
            if foreign:
                sys.monitoring.set_local_events(tool,codes[0],sys.monitoring.events.PY_START|sys.monitoring.events.PY_RETURN)
            with mixed.native_diagnostics(self.module):
                if partial or mode in ('rollback','foreign_route','orphan'):
                    monitor_tools=tuple(sys.monitoring.get_tool(k) for k in range(6))
                    with self.assertRaises((PermissionError,self.module('transaction').Refused,ValueError,RuntimeError)):
                        await runtime.native_forward(hass,mapping,guard=guard,refresh=refresh)
                    self.assertEqual(tuple(sys.monitoring.get_tool(k) for k in range(6)),monitor_tools,
                                     'Refused cleanup must release all request-owned monitors')
                    if partial:
                        self.assertTrue(faulted,'actual selected write must have completed')
                    self.assertEqual(handoffs,[],'No registry/source mutation API')
                    self.assertIs(hass.states.get(old),physical)
                    self.assertIsNone(hass.states.get(new))
                    self.assertEqual(self.f.entity.async_get(old).id,uuids[old])
                    self.assertIsNone(self.f.entity.async_get(new))
                    self.assertIs(native_pending['handle'],native_tuple[4])
                    self.assertEqual(native_pending['handle'].when(),native_tuple[5])
                    self.assertIs(native_pending['handle']._context,native_tuple[6])
                    self.assertEqual(len(slow),1)
                    self.assertEqual(str(slow[0].data['value']),'29')
                    self.assertEqual(str(scripts[0].data['value']),'29')
                    self.assertFalse(native_pending['handle'].cancelled())
                    if mode=='partial_foreign':
                        key,members,image,keys=foreign_partial
                        self.assertIs(before.callbacks[key],members)
                        self.assertEqual(tuple(members),image)
                        self.assertEqual(tuple(native_pending['keys']),keys)
                        self.assertEqual(roots[0].consumers.phases.graph.transfer.phase,
                                         'failed_before_attempt_cleanup_unknown')
                        self.assertEqual(before.config_ids,[new],
                                         'No config undo may precede the conflicting graph preflight')
                        self.assertEqual((actions,scripts),([],[]))
                        for name,content in original.items():
                            self.assertEqual((root/name).read_bytes(),content)
                        return
                    if mode=='foreign_route':
                        self.assertEqual(tuple(native_pending['keys']),foreign_image[0])
                        self.assertIs(before.callbacks['sensor.foreign_duration_route'],foreign_image[1])
                        self.assertEqual(tuple(foreign_image[1]),foreign_image[2])
                    elif mode=='orphan':
                        self.assertIs(orphan_map[old],replacement)
                        self.assertFalse(native_pending['handle'].cancelled())
                        self.assertEqual(native_pending['keys'],[old])
                    else:
                        restored=cap.capture(source)
                        self.assertEqual(restored.original_keys,[old])
                        self.assertEqual(native_pending['keys'],[old])
                        self.assertIs(restored.keyed_job.target,before.listener)
                        self.assertIs(native_pending['cancel_job'].target,native_pending['cancel_listener'])
                        if mode=='partial_multi':
                            restored_second=cap.capture(second)
                            self.assertEqual(restored_second.original_keys,[old])
                            self.assertEqual(restored_second.config_ids,[old])
                            self.assertEqual(second_pending['keys'],[old])
                            self.assertEqual((second_pending['handle'],second_pending['handle'].when(),
                                second_pending['handle']._context,second_pending['cancel_job'].target),second_image)
                        phase=roots[0].consumers.phases
                        normal=self.module('yaml_validation_worker')._normal
                        observer=self.module('yaml_runtime_observer').NativeYAMLRuntimeReadback(hass,owned_automation=phase.graph)
                        for domain,rows in phase.program.rows(frozenset()):
                            self.assertEqual(normal(observer._projection(domain)[0]),rows)
                    self.assertEqual((actions,scripts),([],[]))
                    for name,content in original.items():
                        self.assertEqual((root/name).read_bytes(),content)
                    return
                result=await runtime.native_forward(hass,mapping,guard=lambda:None,refresh=refresh)
                self.assertEqual(result['state'],'applied')
                self.assertIs(result['family_complete'],False)
                self.assertEqual(len(roots),1)
                await check_rows(roots[0],{old})
                current=cap.capture(source)
                self.assertEqual((current.keyed_job,current.listener,current.action_job,current.unsubscribe,
                    current.detach,current.remove,current.original_keys,current.config_ids,current.callbacks,source.action_script),
                    original_objects)
                self.assertEqual(current.original_keys,target_keys)
                self.assertIsNot(current.original_keys,current.config_ids)
                self.assertEqual(self.f.entity.async_get(new).id,uuids[old])
                self.assertIsNone(hass.states.get(old))
                if active_expiry:
                    self.assertEqual((len(actions),len(scripts)),(1,1))
                    self.assertEqual(expiries,[(True,)],'Expiry must run in the actual active Root')
                    self.assertEqual(str(actions[0].data['bound']),new)
                    self.assertEqual(actions[0].context.parent_id,native_tuple[13].id)
                    self.assertEqual(native_pending['handle'].when(),native_tuple[5])
                    self.assertIs(native_pending['handle']._context,native_tuple[6])
                else:
                    self.assertEqual((actions,scripts),([],[]),'No immediate duration action')
                all_current=cap.pending(current)[0]
                if multi:
                    kept=next(p for p in all_current if p['key']==other_id)
                    self.assertEqual((kept['handle'],kept['handle'].when(),kept['handle']._context),other_tuple)
                    self.assertFalse(kept['handle'].cancelled())
                current_pending=tuple(p for p in all_current if p['key']==new)
                if meaningful and not active_expiry:
                    own_created=[p for p in created if p['key']==new]
                    self.assertEqual(len(own_created),1)
                if meaningful and foreign:
                    self.assertTrue(own_created[0]['handle'].cancelled())
                    self.assertEqual(len(emitted),1)
                if active_expiry:
                    self.assertEqual(current_pending,())
                elif mode=='transient':
                    self.assertEqual(len(emitted),1)
                    self.assertTrue(native_pending['handle'].cancelled())
                    self.assertEqual(len(current_pending),1)
                elif pending and mode!='replace':
                    actual=current_pending[0]
                    self.assertEqual((actual['clear'],actual['cancel'],actual['keys'],actual['cancel_job'],
                        actual['handle'],actual['handle'].when(),actual['handle']._context,actual['timer_job'],
                        actual['same_job'],actual['action']),native_tuple[:10])
                    self.assertEqual(actual['keys'],[new])
                    a=self.module('native_automation_duration_binding')._cells(actual['action'])
                    self.assertEqual((a['from_s'],a['to_s'],a['event'],a['event'].context),native_tuple[10:])
                    self.assertEqual(a['entity'],new)
                elif meaningful and not foreign:
                    if mode=='replace':
                        self.assertTrue(native_pending['handle'].cancelled())
                    self.assertEqual(len(current_pending),1)
                    own_pending=current_pending[0]
                    own=self.module('native_automation_duration_binding')._cells(own_pending['action'])['self']
                    self.assertIs(type(own),self.module('native_automation_duration_binding')._OwnDuration)
                    self.assertIs(own.change,pairs[0])
                    self.assertIs(own.binding.action_job,before.action_job)
                    native_tuple=(own_pending['handle'],own_pending['handle'].when(),own_pending['handle']._context)
                    if multi:
                        approved=own.entity_cell.cell_contents
                        own.entity_cell.cell_contents=other_id
                        try:
                            with self.assertRaisesRegex(ValueError,'duration_own_current_binding_changed'):
                                own.check()
                        finally:
                            own.entity_cell.cell_contents=approved
                else:
                    self.assertEqual(current_pending,())
                if meaningful and not foreign and control_pending is None:
                    hass.states.async_set('sensor.duration_control','16')
                    hass.states.async_set('sensor.duration_control','17')
                    await hass.async_block_till_done()
                    control_pending=cap.pending(cap.capture(control))[0][0]
                    control_original=(control_pending['handle'],control_pending['handle'].when(),
                                      control_pending['handle']._context)
                if mode=='cancel':
                    # Genuine normal producer change at NEW cancels the SAME H.
                    await self.stimulus(31)
                    await hass.async_block_till_done()
                    self.assertTrue(native_pending['handle'].cancelled())
                    self.assertEqual(cap.pending(cap.capture(source))[0],())
                reverse=await runtime.native_yaml_reverse(hass,result['operation_id'],result['fingerprint'],
                    guard=lambda:None,refresh=refresh)
                self.assertEqual(reverse['state'],'rolled_back')
                self.assertEqual(len(roots),2)
                self.assertIsNot(roots[0],roots[1])
                self.assertIsNot(roots[0].chain,roots[1].chain)
                await check_rows(roots[1],set())
                back=cap.capture(source)
                self.assertEqual(back.original_keys,original_keys)
                self.assertEqual((len(actions),len(scripts)),(1,1) if active_expiry else (0,0))
                self.assertEqual(self.f.entity.async_get(old).id,uuids[old])
                self.assertIsNone(self.f.entity.async_get(new))
                for name,content in original.items():
                    self.assertEqual((root/name).read_bytes(),content)
                all_remaining=cap.pending(back)[0]
                if multi:
                    kept=next(p for p in all_remaining if p['key']==other_id)
                    self.assertEqual((kept['handle'],kept['handle'].when(),kept['handle']._context),other_tuple)
                    self.assertFalse(kept['handle'].cancelled())
                remaining=tuple(p for p in all_remaining if p['key']==old)
                if active_expiry:
                    self.assertEqual(remaining,())
                    self.assertEqual(len(controls),1)
                    return
                if mode in ('cancel','foreign','transient','multi_foreign'):
                    if mode!='transient':
                        self.assertEqual(remaining,())
                    return
                if mode=='configured':
                    await self.stimulus(16);await self.stimulus(17)
                    await hass.async_block_till_done()
                    remaining=cap.pending(cap.capture(source))[0]
                    self.assertEqual(len(remaining),1)
                elif pending and mode!='replace':
                    p=remaining[0]
                    self.assertIs(p['handle'],native_tuple[4]);self.assertEqual(p['handle'].when(),native_tuple[5])
                    self.assertIs(p['handle']._context,native_tuple[6])
                    a=self.module('native_automation_duration_binding')._cells(p['action'])
                    self.assertEqual((a['from_s'],a['to_s'],a['event'],a['event'].context),native_tuple[10:])
                    self.assertEqual(a['entity'],old)
                else:
                    p=remaining[0]
                    self.assertIs(p['handle'],native_tuple[0]);self.assertEqual(p['handle'].when(),native_tuple[1])
                    self.assertIs(p['handle']._context,native_tuple[2])
                if control_pending is None:
                    hass.states.async_set('sensor.duration_control','16')
                    hass.states.async_set('sensor.duration_control','17')
                    await hass.async_block_till_done()
                    control_pending=cap.pending(cap.capture(control))[0][0]
                    control_original=(control_pending['handle'],control_pending['handle'].when(),
                                      control_pending['handle']._context)
                # SAME independent control H/deadline; neither timer is reset.
                control_after=cap.pending(cap.capture(control))[0][0]
                self.assertIs(control_after['handle'],control_original[0])
                self.assertEqual(control_after['handle'].when(),control_original[1])
                self.assertIs(control_after['handle']._context,control_original[2])
                self.assertIs(control_after['clear'],control_pending['clear'])
                p=remaining[0]
                deadline=max(p['handle'].when(),control_pending['handle'].when())
                # One normal-clock expiry wait, not a loop polling pending away.
                await asyncio.sleep(max(0,deadline-hass.loop.time())+0.05)
                await hass.async_block_till_done()
                self.assertEqual((len(actions),len(scripts),len(controls)),(2 if multi else 1,2 if multi else 1,1))
                if multi:
                    self.assertEqual(sum(str(e.data['bound'])==other_id for e in actions),1)
                    actions[:]=[e for e in actions if str(e.data['bound'])==old]
                self.assertEqual(str(actions[0].data['bound']),old)
                self.assertEqual(float(actions[0].data['seconds']),duration)
                expected_event=(pairs[0].event if meaningful else
                    self.module('native_automation_duration_binding')._cells(p['action'])['event'])
                self.assertEqual(actions[0].context.parent_id,expected_event.context.id)
                self.assertEqual(str(actions[0].data['current']),'29' if meaningful else '17')
                self.assertEqual(str(actions[0].data['previous']),'17' if meaningful else '16')
                self.assertEqual(str(scripts[0].data['value']),'29' if meaningful else '17')
                self.assertEqual(str(actions[0].data['configured']),'29' if meaningful else '17')
                self.assertEqual(str(actions[0].data['from_id']),old)
                self.assertEqual(str(actions[0].data['to_id']),new if meaningful else old)
                self.assertEqual(cap.pending(cap.capture(source))[0],())
        finally:
            if active_expiry:
                print('NATIVE_DURATION_ACTIVE_EXPIRIES',expiries)
            for code in codes:
                sys.monitoring.set_local_events(tool,code,0)
            sys.monitoring.register_callback(tool,sys.monitoring.events.PY_START,None)
            sys.monitoring.register_callback(tool,sys.monitoring.events.PY_RETURN,None)
            sys.monitoring.free_tool_id(tool)
