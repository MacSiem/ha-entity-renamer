"""The staged Script changes while foreign programs retain their source image."""
import unittest
from custom_components.ha_entity_renamer.controlled_script_phases import _script_rows
from custom_components.ha_entity_renamer.transaction import Position, Refused


class ControlledScriptPhaseTests(unittest.TestCase):
    def setUp(self):
        self.before = [['caller', 'old call'], ['old', 'old source']]
        self.after = [['caller', 'new call'], ['new', 'new source']]
        self.prefix = [['caller', 'old call'], ['new', 'new key and old source']]

    def test_forward_keeps_caller_until_yaml_reload(self):
        self.assertEqual(_script_rows(self.before, self.after, self.prefix,
            'old', 'new', True, Position.BEFORE),
            [['caller', 'old call'], ['new', 'new key and old source']])
        self.assertEqual(_script_rows(self.before, self.after, self.after,
            'old', 'new', True, Position.AFTER), self.after)

    def test_inverse_keeps_after_caller_until_yaml_restore(self):
        self.assertEqual(_script_rows(self.before, self.after, self.before,
            'old', 'new', False, Position.AFTER),
            [['caller', 'new call'], ['old', 'old source']])
        self.assertEqual(_script_rows(self.before, self.after, self.before,
            'old', 'new', False, Position.BEFORE), self.before)

    def test_ambiguous_or_missing_owner_refuses(self):
        for prefix in ([], [['new', 'one'], ['new', 'two']]):
            with self.subTest(prefix=prefix), self.assertRaises(Refused):
                _script_rows(self.before, self.after, prefix, 'old', 'new', True, Position.BEFORE)

class ControlledScriptCutoverTests(unittest.TestCase):
    def test_only_native_unavailable_restored_state_is_admitted(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import _native_removed_state
        self.assertTrue(_native_removed_state(SimpleNamespace(state='unavailable', attributes={'restored': True})))
        for state in (None, SimpleNamespace(state='off', attributes={'restored': True}),
                      SimpleNamespace(state='unavailable', attributes={'restored': False}),
                      SimpleNamespace(state='unavailable', attributes={})):
            self.assertFalse(_native_removed_state(state))

    def test_only_exact_removed_placeholder_can_be_retired(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import _retire_removed_state
        class States:
            def __init__(self, value): self.value = value
            def get(self, key): return self.value
            def async_remove(self, key): self.value = None; return True
        original = SimpleNamespace(state='unavailable', attributes={'restored': True})
        component = SimpleNamespace(get_entity=lambda key: None)
        registry = SimpleNamespace(async_get=lambda key: None)
        states = States(original)
        _retire_removed_state(SimpleNamespace(states=states), component, registry, 'script.old', original)
        self.assertIsNone(states.value)
        _retire_removed_state(SimpleNamespace(states=states), component, registry, 'script.old', original)
        for current, own_entity, own_row in (
                (SimpleNamespace(state='unavailable', attributes={'restored': True}), None, None),
                (original, object(), None), (original, None, object())):
            states = States(current)
            with self.subTest(current=current, own_entity=own_entity, own_row=own_row), self.assertRaises(Refused):
                _retire_removed_state(SimpleNamespace(states=states),
                    SimpleNamespace(get_entity=lambda key: own_entity),
                    SimpleNamespace(async_get=lambda key: own_row), 'script.old', original)
            self.assertIs(states.value, current)

    def test_pending_registry_observation_accepts_only_receiptable_own_row(self):
        from copy import deepcopy
        from custom_components.ha_entity_renamer.controlled_script_cutover import _pending_registry_documents
        old = {'id': 'own', 'entity_id': 'script.old', 'unique_id': 'old'}
        new = {'id': 'own', 'entity_id': 'script.new', 'unique_id': 'new'}
        foreign = {'id': 'foreign', 'value': 1}
        original = {'core.entity_registry': {'version': 1, 'data': {'entities': [old, foreign]}},
                    'core.device_registry': {'data': {'devices': []}}}
        after = deepcopy(original)
        after['core.entity_registry']['data']['entities'] = [deepcopy(foreign), deepcopy(new)]
        check = lambda value: _pending_registry_documents(original, value, 'own', old, new)
        self.assertTrue(check(original)); self.assertTrue(check(after))
        for mode in ('foreign', 'duplicate', 'other_registry', 'wrong_target', 'typed_foreign'):
            changed = deepcopy(after)
            if mode == 'foreign': changed['core.entity_registry']['data']['entities'][0]['value'] = 2
            if mode == 'duplicate': changed['core.entity_registry']['data']['entities'].append(deepcopy(new))
            if mode == 'other_registry': changed['core.device_registry']['data']['devices'].append({'id': 'other'})
            if mode == 'wrong_target': changed['core.entity_registry']['data']['entities'][1]['entity_id'] = 'script.foreign'
            if mode == 'typed_foreign': changed['core.entity_registry']['data']['entities'][0]['value'] = True
            with self.subTest(mode=mode): self.assertFalse(check(changed))

    def test_only_exact_completed_receipt_releases_the_pending_intent(self):
        from copy import deepcopy
        from dataclasses import asdict
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        from custom_components.ha_entity_renamer.native_registry import NativeRegistryMutation
        mutation = NativeRegistryMutation('entity', 'own', '{}', '{}')
        row = {'digest': 'receipt', 'payload': {'kind': 'registry_receipt',
               'intent': ['intent', 'digest'], 'mutation': asdict(mutation)}}
        class Chain:
            active = None
            def _slot(self, kind, index, direction): return 'expected-slot'
            def _reconstruct(self): return {}, 'receipt', 1, {0: 'forward'}, None
            def reconstruct_inputs(self): return 'accepted-state'
        chain = Chain()
        chain.store = SimpleNamespace(read=lambda slot: row if slot == 'expected-slot' else None)
        cutover = object.__new__(ControlledScriptCutover)
        cutover.root = SimpleNamespace(chain=chain)
        cutover.node = SimpleNamespace(phase='observed_effect', intent=SimpleNamespace(reference=('intent', 'digest')),
            context=SimpleNamespace(index=0, record=SimpleNamespace(direction='forward')))
        cutover.mutation = mutation
        self.assertEqual(cutover._accepted_receipt_state(), 'accepted-state')
        original = deepcopy(row)
        for changed in ('intent', 'mutation', 'kind', 'digest', 'pending', 'phase'):
            row.clear(); row.update(deepcopy(original))
            chain._reconstruct = lambda: ({}, 'receipt', 1, {0: 'forward'}, None)
            cutover.node.phase = 'observed_effect'
            if changed == 'intent': row['payload']['intent'] = ['foreign', 'digest']
            if changed == 'mutation': row['payload']['mutation']['identity'] = 'foreign'
            if changed == 'kind': row['payload']['kind'] = 'yaml_receipt'
            if changed == 'digest': row['digest'] = 'foreign'
            if changed == 'pending': chain._reconstruct = lambda: ({}, 'receipt', 1, {0: 'forward'}, ('foreign', 'digest'))
            if changed == 'phase': cutover.node.phase = 'registry_attempted'
            with self.subTest(changed=changed): self.assertIsNone(cutover._accepted_receipt_state())

    def test_receipt_chain_uses_typed_registry_then_yaml_reverse_order(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_composition import ControlledScriptComposition
        from custom_components.ha_entity_renamer.native_registry_receipts import RegistryReceiptChain, RegistryReceiptError
        composition = object.__new__(ControlledScriptComposition)
        baseline = object()
        chain = object.__new__(RegistryReceiptChain)
        chain.baseline = baseline
        chain.plan = SimpleNamespace(native_inputs=('original',), steps=(
            SimpleNamespace(adapter='registry'), SimpleNamespace(adapter='registry'), SimpleNamespace(adapter='yaml')))
        chain.preparations = SimpleNamespace(plans={'original': {'payload': {'composition':
            {'profile': 'template_controlled_script_reload_v1'}}}},
            resolve_composition=lambda _: (baseline, composition))
        chain._setup_positions = {'forward-head': {0: 'forward', 1: 'forward', 2: 'forward'}}
        chain._powercalc_preparation_snapshot = None
        self.assertEqual(chain._script_reverse_order(), (1, 0, 2))
        original_plan, original_positions = chain.plan, chain._setup_positions
        chain.plan = SimpleNamespace(native_inputs=('original',), steps=(
            SimpleNamespace(adapter='registry'), SimpleNamespace(adapter='helpers'),
            SimpleNamespace(adapter='dashboard'), SimpleNamespace(adapter='yaml')))
        chain._setup_positions = {'forward-head': {i: 'forward' for i in range(4)}}
        self.assertEqual(chain._script_reverse_order(), (0, 1, 2, 3))
        chain.plan, chain._setup_positions = original_plan, original_positions
        record = SimpleNamespace(direction='rollback', phases=('verified', 'undo_intent', 'verified'))
        chain._phase_state(SimpleNamespace(record=record, index=1), {0: 'forward', 1: 'forward', 2: 'forward'})
        record.phases = ('undo_intent', 'verified', 'verified')
        with self.assertRaises(RegistryReceiptError):
            chain._phase_state(SimpleNamespace(record=record, index=0), {0: 'forward', 1: 'forward', 2: 'forward'})
        chain._setup_positions = {'partial': {0: 'forward'}}
        self.assertIsNone(chain._script_reverse_order())

    def test_reverse_registry_match_requires_the_exact_live_cutover(self):
        import json
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        from custom_components.ha_entity_renamer.native_registry import NativeRegistryBridge, NativeRegistryMutation, PreparedRegistryChange
        from custom_components.ha_entity_renamer.native_template_state_registry_effect import NativeTemplateStateRegistryEffect
        before = {'id': 'own', 'platform': 'script', 'entity_id': 'script.old',
                  'unique_id': 'old', 'previous_unique_id': None}
        after = {**before, 'entity_id': 'script.new', 'unique_id': 'new', 'previous_unique_id': 'old'}
        restored = {**before, 'previous_unique_id': 'new'}
        change = PreparedRegistryChange('entity', 'own', json.dumps(before), json.dumps(after))
        node = object.__new__(NativeTemplateStateRegistryEffect)
        node.change = change; node.target = 'script.old'; node.intent = object(); node.script_service_move = None
        cutover = object.__new__(ControlledScriptCutover)
        cutover.node = node; cutover.phase = 'added'; cutover.mutation = NativeRegistryMutation(
            'entity', 'own', json.dumps(after), json.dumps(restored))
        cutover.root = SimpleNamespace(chain=SimpleNamespace(active=node.intent))
        row = object(); cutover.target_entity = SimpleNamespace(registry_entry=row)
        cutover._scope = lambda: None; cutover._foreign = lambda: None
        node.script_cutover = cutover
        bridge = object.__new__(NativeRegistryBridge)
        bridge._mutation_node = node; bridge.native_images = None
        bridge.io = SimpleNamespace(registries={'entity': SimpleNamespace(async_get=lambda _: row)})
        self.assertTrue(bridge._matches(change, restored, before))
        cutover.phase = 'removed'
        self.assertFalse(bridge._matches(change, restored, before))
        cutover.phase = 'added'; cutover.root.chain.active = None
        self.assertFalse(bridge._matches(change, restored, before))

    def test_script_reverse_chain_binds_consumed_restore_authority(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.native_registry_receipts import RegistryReceiptChain, RegistryReceiptError
        from custom_components.ha_entity_renamer.transaction import Record, PhaseContext
        chain = object.__new__(RegistryReceiptChain)
        step = SimpleNamespace(key='registry-0', identity='own', prepared_ref='prepared', adapter='registry')
        chain.plan = SimpleNamespace(steps=(step,)); chain.operation_id = 'op'
        chain.root = {'bindings': [{'digest': 'prepared-digest', 'registry_digest': 'registry-digest'}]}
        chain._script_reverse_order = lambda: (0,)
        record = Record('op', chain.plan, 'rolling_back', ('undo_intent',),
            direction='rollback', restore_attempted=True)
        context = PhaseContext(record, 0)
        binding = chain._context(context)
        self.assertIs(binding['restore_attempted'], True)
        self.assertEqual(chain._context(chain._historical_context(binding)), binding)
        for invalid in (None, False):
            forged = Record('op', chain.plan, 'rolling_back', ('undo_intent',),
                direction='rollback', restore_attempted=invalid)
            with self.assertRaises(RegistryReceiptError): chain._context(PhaseContext(forged, 0))

    def test_hybrid_script_service_fence_rejects_replacement(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.native_own_dependent_fence import NativeOwnDependentAdmission
        class Actor:
            def call(self): pass
        entity = Actor(); target = entity.call
        job = SimpleNamespace(target=target)
        service = SimpleNamespace(job=job, schema=object(), supports_response=object())
        table = {'new': service}
        services = SimpleNamespace(_services={'script': table})
        fence = SimpleNamespace(entity=entity, native_readd=True, domain='script',
            root=SimpleNamespace(hass=SimpleNamespace(services=services)))
        admission = object.__new__(NativeOwnDependentAdmission)
        admission.fence = fence; admission.entity = entity; admission.script = None
        admission.census = admission.caller_bus = admission.inventory = None
        admission.controlled_service = (table, 'new', 'old', service, job, target,
            service.schema, service.supports_response)
        admission.check_execution()
        table['old'] = object()
        with self.assertRaises(Refused): admission.check_execution()
        del table['old']; table['new'] = object()
        with self.assertRaises(Refused): admission.check_execution()

    def test_review_admission_rebinds_only_to_exact_execution_fence(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.native_own_dependent_fence import NativeOwnDependentAdmission, NativeOwnDependentFence
        from custom_components.ha_entity_renamer.native_template_state_server import NativeTemplateStateServer
        hass, plan, entity, change = object(), object(), object(), object()
        old_root = SimpleNamespace(closed=True, plan=plan, hass=hass)
        old = object.__new__(NativeOwnDependentFence)
        old.native_readd = True; old.domain = 'script'; old.root = old_root; old.change = change
        session = object.__new__(NativeTemplateStateServer)
        session.bound = True; session.closed = False; session.plan = plan; session.hass = hass
        fresh = object.__new__(NativeOwnDependentFence)
        fresh.root = session; fresh.entity = entity; fresh.change = change
        session.own_dependent_fence = fresh
        admission = object.__new__(NativeOwnDependentAdmission)
        admission.fence = old; admission.inventory = None; admission.entity = entity
        fresh.entity = object()
        with self.assertRaises(Refused): admission.bind_scene_session(session)
        self.assertIs(admission.fence, old)
        fresh.entity = entity; admission.bind_scene_session(session)
        self.assertIs(admission.fence, fresh)

    def test_hybrid_phase_checks_the_owned_service_after_receipt(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from custom_components.ha_entity_renamer.controlled_script_phases import ControlledScriptPhases
        from custom_components.ha_entity_renamer.dependent_yaml_runtime import DependentYAMLRuntime
        seen = []
        admission = SimpleNamespace(controlled_service=object(), check_execution=lambda: seen.append('service'))
        phases = object.__new__(ControlledScriptPhases)
        phases.root = SimpleNamespace(own_record_admission=admission)
        with patch.object(DependentYAMLRuntime, 'check', lambda _: seen.append('program')):
            phases.check()
        self.assertEqual(seen, ['program', 'service'])

    def test_old_description_retired_only_with_exact_unaliased_owner(self):
        from custom_components.ha_entity_renamer.controlled_script_cutover import _retire_own_description
        old, fresh, foreign = object(), object(), object()
        for mode in ('changed', 'alias', 'graph', 'same_new'):
            cache = {('script', 'old'): old, ('script', 'new'): fresh, ('other', 'x'): foreign}
            graph, new_value = 'expected', fresh
            if mode == 'changed': cache[('script', 'old')] = foreign
            if mode == 'alias': cache[('script', 'other')] = old
            if mode == 'graph': graph = 'foreign'
            if mode == 'same_new': new_value = old
            before = dict(cache)
            with self.subTest(mode=mode), self.assertRaises(Refused):
                _retire_own_description(cache, ('script', 'old'), old, 'expected', graph, new_value)
            self.assertEqual(cache, before)
        cache = {('script', 'old'): old, ('script', 'new'): fresh, ('other', 'x'): foreign}
        _retire_own_description(cache, ('script', 'old'), old, 'expected', 'expected', fresh)
        self.assertEqual(cache, {('script', 'new'): fresh, ('other', 'x'): foreign})

    def test_matching_automation_key_does_not_rebind_script_owner(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.native_template_mixed_state_yaml import _automation_owner_replaced, _owner_in_domain
        row = SimpleNamespace(native_key='mixed_source')
        self.assertFalse(_automation_owner_replaced(None, {'mixed_source'}))
        self.assertFalse(_automation_owner_replaced(SimpleNamespace(domain='script', original_row=row), {'mixed_source'}))
        self.assertTrue(_automation_owner_replaced(SimpleNamespace(domain='automation', original_row=row), {'mixed_source'}))
        self.assertFalse(_automation_owner_replaced(SimpleNamespace(domain='automation', original_row=row), {'other'}))
        self.assertTrue(_owner_in_domain(SimpleNamespace(domain='script', original_row=row), 'script'))
        self.assertFalse(_owner_in_domain(SimpleNamespace(domain='automation', original_row=row), 'script'))

    def test_cutover_cannot_be_constructed_without_active_coordinator(self):
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        with self.assertRaises(Refused):
            ControlledScriptCutover()


class ControlledScriptStateTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_unchanged_suspended_automation_is_restored(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        class Actor:
            unique_id = 'watcher'
            entity_id = 'automation.watcher'
            _is_enabled = True
            _async_detach_triggers = object()
            async def _async_disable(self, stop_actions):
                self._is_enabled = False; self._async_detach_triggers = None
            async def _async_enable(self):
                self._is_enabled = True; self._async_detach_triggers = object()
        for foreign in (False, True):
            entity = Actor(); state = SimpleNamespace(state='on')
            states = {entity.entity_id: state}
            transition = SimpleNamespace(expected_enabled={entity.unique_id: True})
            cutover = object.__new__(ControlledScriptCutover)
            cutover.root = SimpleNamespace(controlled_automation=transition,
                hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
            cutover.inventory = SimpleNamespace(check=lambda: None)
            cutover.automation_targets = (entity,); cutover.suspended_automations = []
            cutover._scope = lambda: None; cutover.phase = 'captured'
            cutover._owns_suspended_automation = lambda actor, state: states[actor.entity_id] is state
            cutover._same_automation_owner = lambda actor: True
            await cutover.suspend_affected_automations()
            self.assertFalse(entity._is_enabled)
            if foreign:
                states[entity.entity_id] = SimpleNamespace(state='off')
                with self.assertRaises(Refused): await cutover.resume_affected_automations()
                self.assertFalse(entity._is_enabled)
            else:
                await cutover.resume_affected_automations()
                self.assertTrue(entity._is_enabled)
                self.assertTrue(transition.expected_enabled[entity.unique_id])

    async def test_detected_foreign_change_stops_before_next_restore(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        states, enabled = {}, {}
        class Actor:
            def __init__(self, key, effect=None):
                self.unique_id = key; self.entity_id = 'automation.' + key
                self._is_enabled = False; self._async_detach_triggers = None; self.effect = effect
            async def _async_enable(self):
                self._is_enabled = True; self._async_detach_triggers = object()
                if self.effect: self.effect()
        first = Actor('first'); second = Actor('second')
        first_state = states[first.entity_id] = SimpleNamespace(state='on')
        second_state = states[second.entity_id] = SimpleNamespace(state='on')
        first.effect = lambda: states.__setitem__(second.entity_id, SimpleNamespace(state='off'))
        enabled.update(first=False, second=False)
        cutover = object.__new__(ControlledScriptCutover)
        cutover.root = SimpleNamespace(controlled_automation=SimpleNamespace(expected_enabled=enabled),
            hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
        cutover.suspended_automations = [(first, first_state), (second, second_state)]
        cutover._scope = lambda: None; cutover.phase = 'added'
        cutover._owns_suspended_automation = lambda actor, state: states[actor.entity_id] is state
        cutover._same_automation_owner = lambda actor: True
        with self.assertRaises(Refused): await cutover.resume_affected_automations()
        self.assertTrue(first._is_enabled)
        self.assertFalse(second._is_enabled)

    async def test_cleanup_after_partial_disable_and_attach_failure(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        class Actor:
            def __init__(self, key, fail=False):
                self.unique_id = key; self.entity_id = 'automation.' + key
                self._is_enabled = False; self._async_detach_triggers = None
                self.fail = fail; self.writes = 0
            async def _async_enable(self):
                self._is_enabled = True
                await asyncio.sleep(0)
                if self.fail: raise RuntimeError('attach failed')
                self._async_detach_triggers = object()
            async def _async_disable(self, stop_actions=False):
                self._is_enabled = False; self._async_detach_triggers = None
            def async_write_ha_state(self): self.writes += 1
        import asyncio
        first, second, foreign = Actor('first'), Actor('second', True), Actor('foreign')
        states = {x.entity_id: object() for x in (first, second, foreign)}
        originals = dict(states)
        cutover = object.__new__(ControlledScriptCutover)
        cutover.root = SimpleNamespace(controlled_automation=SimpleNamespace(expected_enabled={x.unique_id: False for x in (first, second, foreign)}))
        cutover.suspended_automations = [(x, originals[x.entity_id]) for x in (first, second, foreign)]
        cutover._owns_suspended_automation = lambda actor, state: states[actor.entity_id] is state
        cutover._same_automation_owner = lambda actor: True
        states[foreign.entity_id] = object()  # visible foreign OFF/replacement
        await cutover.cleanup_suspended_automations()
        self.assertTrue(first._is_enabled); self.assertIsNotNone(first._async_detach_triggers)
        self.assertFalse(second._is_enabled); self.assertEqual(second.writes, 1)
        self.assertFalse(foreign._is_enabled); self.assertEqual(foreign.writes, 0)

    async def test_cancelled_native_attach_is_drained_before_cleanup(self):
        import asyncio
        from custom_components.ha_entity_renamer.controlled_script_cutover import _finish_native_call
        entered, finish = asyncio.Event(), asyncio.Event()
        finished = []
        async def attach():
            entered.set(); await finish.wait(); finished.append(True)
        task = asyncio.create_task(_finish_native_call(attach, lambda: None))
        await entered.wait(); task.cancel(); await asyncio.sleep(0)
        self.assertFalse(task.done()); finish.set()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(finished, [True])

    async def test_ownership_checks_reject_changed_owner_registry_or_state(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        entity = SimpleNamespace(entity_id='automation.watcher')
        # Hashable native objects are dictionary keys; use a native-like actor.
        class Actor: entity_id = 'automation.watcher'
        entity = Actor(); row = object(); entity.registry_entry = row; state = object()
        values = {'owner': entity, 'row': row, 'state': state}
        cutover = object.__new__(ControlledScriptCutover)
        cutover.automation_owners = {entity: row}
        cutover.io = SimpleNamespace(registries={'entity': SimpleNamespace(async_get=lambda key: values['row'])})
        cutover.root = SimpleNamespace(controlled_automation=SimpleNamespace(component=SimpleNamespace(get_entity=lambda key: values['owner'])),
            hass=SimpleNamespace(states=SimpleNamespace(get=lambda key: values['state'])))
        self.assertTrue(cutover._owns_suspended_automation(entity, state))
        for key in values:
            saved = values[key]; values[key] = object()
            self.assertFalse(cutover._owns_suspended_automation(entity, state)); values[key] = saved

    async def test_partial_suspend_exception_and_cancel_are_cleaned_for_multiple_actors(self):
        import asyncio
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                class Actor:
                    def __init__(self, key, fail=False):
                        self.unique_id = key; self.entity_id = 'automation.' + key
                        self._is_enabled = True; self._async_detach_triggers = object(); self.fail = fail
                    async def _async_disable(self, stop_actions=False):
                        self._is_enabled = False; self._async_detach_triggers = None
                        if self.fail:
                            if cancelled: raise asyncio.CancelledError
                            raise RuntimeError('primary')
                    async def _async_enable(self):
                        self._is_enabled = True; self._async_detach_triggers = object()
                    def async_write_ha_state(self): raise AssertionError('unexpected OFF')
                first, second = Actor('first'), Actor('second', True)
                states = {x.entity_id: SimpleNamespace(state='on') for x in (first, second)}
                transition = SimpleNamespace(expected_enabled={'first': True, 'second': True})
                cutover = object.__new__(ControlledScriptCutover)
                cutover.root = SimpleNamespace(controlled_automation=transition,
                    hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
                cutover.inventory = SimpleNamespace(check=lambda: None)
                cutover.automation_targets = (first, second); cutover.suspended_automations = []
                cutover._scope = lambda: None; cutover.phase = 'captured'
                cutover._owns_suspended_automation = lambda entity, state: states[entity.entity_id] is state
                cutover._same_automation_owner = lambda actor: True
                with self.assertRaises(asyncio.CancelledError if cancelled else RuntimeError):
                    try: await cutover.suspend_affected_automations()
                    except BaseException:
                        await cutover.cleanup_suspended_automations()
                        raise
                for actor in (first, second):
                    self.assertTrue(actor._is_enabled); self.assertIsNotNone(actor._async_detach_triggers)

    async def test_unconfirmed_trigger_recovery_shows_persistent_warning(self):
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        homeassistant = ModuleType('homeassistant'); components = ModuleType('homeassistant.components')
        notices = []; components.persistent_notification = SimpleNamespace(
            async_create=lambda *args, **kwargs: notices.append((args, kwargs)))
        homeassistant.components = components
        cutover = object.__new__(ControlledScriptCutover)
        hass = object(); cutover.root = SimpleNamespace(hass=hass)
        with patch.dict(sys.modules, {'homeassistant': homeassistant, 'homeassistant.components': components}):
            cutover._warn_incomplete_automation(SimpleNamespace(entity_id='automation.test'))
        self.assertEqual(len(notices), 1)
        args, kwargs = notices[0]
        self.assertIs(args[0], hass)
        self.assertIn('automation.test', args[1])
        self.assertIn('nie potwierdzono', args[1].lower())
        self.assertEqual(kwargs['notification_id'], 'ha_entity_renamer_trigger_automation_test')

    async def test_cancel_during_second_attach_drains_then_restores_both(self):
        import asyncio
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        entered, finish = asyncio.Event(), asyncio.Event()
        class Actor:
            def __init__(self, key, wait=False):
                self.unique_id = key; self.entity_id = 'automation.' + key
                self._is_enabled = False; self._async_detach_triggers = None; self.wait = wait
            async def _async_enable(self):
                self._is_enabled = True
                if self.wait: entered.set(); await finish.wait()
                self._async_detach_triggers = object()
            async def _async_disable(self, stop_actions=False):
                self._is_enabled = False; self._async_detach_triggers = None
            def async_write_ha_state(self): raise AssertionError('unexpected OFF')
        first, second = Actor('first'), Actor('second', True)
        states = {x.entity_id: object() for x in (first, second)}
        cutover = object.__new__(ControlledScriptCutover)
        cutover.root = SimpleNamespace(controlled_automation=SimpleNamespace(expected_enabled={'first': False, 'second': False}),
            hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
        cutover.suspended_automations = [(x, states[x.entity_id]) for x in (first, second)]
        cutover._scope = lambda: None; cutover.phase = 'added'
        cutover._owns_suspended_automation = lambda actor, state: states[actor.entity_id] is state
        cutover._same_automation_owner = lambda actor: True
        async def operation():
            try: await cutover.resume_affected_automations()
            except BaseException:
                await cutover.cleanup_suspended_automations()
                raise
        task = asyncio.create_task(operation()); await entered.wait(); task.cancel()
        await asyncio.sleep(0); self.assertFalse(task.done()); finish.set()
        with self.assertRaises(asyncio.CancelledError): await task
        for actor in (first, second):
            self.assertTrue(actor._is_enabled); self.assertIsNotNone(actor._async_detach_triggers)
        self.assertEqual(cutover.root.controlled_automation.expected_enabled, {'first': True, 'second': True})

    async def test_foreign_change_between_fence_and_child_start_blocks_native_call(self):
        import asyncio
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        for enable, during_cleanup in ((True, False), (True, True), (False, False), (False, True)):
            with self.subTest(enable=enable, cleanup=during_cleanup):
                class Actor:
                    unique_id = 'watcher'; entity_id = 'automation.watcher'
                    def __init__(self):
                        self._is_enabled = not enable
                        self._async_detach_triggers = None if enable else object()
                        self.calls = []
                    async def _async_enable(self): self.calls.append('enable'); self._is_enabled = True
                    async def _async_disable(self, stop_actions=False): self.calls.append('disable'); self._is_enabled = False
                actor = Actor(); original = SimpleNamespace(state='on'); states = {actor.entity_id: original}
                expected = not enable
                transition = SimpleNamespace(expected_enabled={actor.unique_id: expected})
                cutover = object.__new__(ControlledScriptCutover)
                cutover.root = SimpleNamespace(controlled_automation=transition,
                    hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
                cutover._owns_suspended_automation = lambda entity, state: states[entity.entity_id] is state
                cutover._same_automation_owner = lambda entity: True
                def foreign():
                    # An observable OFF, or observable OFF→ON before disable.
                    states[actor.entity_id] = SimpleNamespace(state='off')
                    actor._is_enabled = False
                    if not enable:
                        states[actor.entity_id] = SimpleNamespace(state='on')
                        actor._is_enabled = True
                asyncio.get_running_loop().call_soon(foreign)
                with self.assertRaises(Refused):
                    await cutover._call_automation(actor, original, enabled=not enable,
                        detach=actor._async_detach_triggers, expected=expected, enable=enable)
                self.assertEqual(actor.calls, [])
                self.assertEqual(states[actor.entity_id].state, 'off' if enable else 'on')

    async def test_foreign_off_during_attach_detaches_only_new_callback(self):
        import asyncio
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        entered, finish = asyncio.Event(), asyncio.Event(); removed = []
        class Actor:
            unique_id='watcher'; entity_id='automation.watcher'; _is_enabled=False; _async_detach_triggers=None
            async def _async_enable(self):
                self._is_enabled = True; entered.set(); await finish.wait()
                self._async_detach_triggers = lambda: removed.append('own')
        actor=Actor(); original=SimpleNamespace(state='on'); states={actor.entity_id:original}
        cutover=object.__new__(ControlledScriptCutover)
        cutover.root=SimpleNamespace(controlled_automation=SimpleNamespace(expected_enabled={'watcher':False}),
            hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
        cutover._owns_suspended_automation=lambda entity,state: states[entity.entity_id] is state
        cutover._same_automation_owner=lambda entity: True
        task=asyncio.create_task(cutover._call_automation(actor, original, enabled=False, detach=None, expected=False, enable=True))
        await entered.wait(); actor._is_enabled=False; states[actor.entity_id]=SimpleNamespace(state='off'); finish.set()
        await task
        self.assertEqual(removed, ['own']); self.assertIsNone(actor._async_detach_triggers)
        self.assertFalse(actor._is_enabled); self.assertEqual(states[actor.entity_id].state,'off')

    async def test_multiowner_resume_honors_off_between_check_and_second_child(self):
        import asyncio
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        class Actor:
            def __init__(self,key):
                self.unique_id=key; self.entity_id='automation.'+key
                self._is_enabled=False; self._async_detach_triggers=None; self.calls=0
            async def _async_enable(self):
                self.calls+=1; self._is_enabled=True; self._async_detach_triggers=lambda: None
        first, second=Actor('first'), Actor('second')
        states={x.entity_id:SimpleNamespace(state='on') for x in (first, second)}
        originals=dict(states); transition=SimpleNamespace(expected_enabled={'first':False,'second':False})
        cutover=object.__new__(ControlledScriptCutover)
        cutover.root=SimpleNamespace(controlled_automation=transition,hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
        cutover.suspended_automations=[(x,originals[x.entity_id]) for x in (first,second)]
        cutover._owns_suspended_automation=lambda actor,state:states[actor.entity_id] is state
        cutover._same_automation_owner=lambda actor:True
        calls=[]
        def scope():
            calls.append(1)
            if len(calls)==4:
                asyncio.get_running_loop().call_soon(lambda: states.__setitem__(second.entity_id,SimpleNamespace(state='off')))
        cutover._scope=scope;cutover.phase='added'
        with self.assertRaises(Refused): await cutover.resume_affected_automations()
        self.assertEqual(first.calls,1);self.assertEqual(second.calls,0)
        self.assertTrue(first._is_enabled);self.assertFalse(second._is_enabled)
        self.assertEqual(states[second.entity_id].state,'off')

    async def test_cleanup_child_fence_does_not_undo_new_foreign_off(self):
        import asyncio
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        class Actor:
            unique_id='watcher';entity_id='automation.watcher';_is_enabled=False;_async_detach_triggers=None
            calls=0
            async def _async_enable(self):self.calls+=1;self._is_enabled=True
        actor=Actor();original=SimpleNamespace(state='on');states={actor.entity_id:original};warn=[]
        cutover=object.__new__(ControlledScriptCutover)
        cutover.root=SimpleNamespace(controlled_automation=SimpleNamespace(expected_enabled={'watcher':False}),
            hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
        cutover.suspended_automations=[(actor,original)]
        cutover._owns_suspended_automation=lambda entity,state:states[entity.entity_id] is state
        cutover._same_automation_owner=lambda entity:True
        cutover._warn_incomplete_automation=lambda entity:warn.append(entity.entity_id)
        asyncio.get_running_loop().call_soon(lambda: states.__setitem__(actor.entity_id,SimpleNamespace(state='off')))
        await cutover.cleanup_suspended_automations()
        self.assertEqual(actor.calls,0);self.assertFalse(actor._is_enabled)
        self.assertEqual(states[actor.entity_id].state,'off');self.assertEqual(warn,[actor.entity_id])

    async def test_child_fence_requires_typed_expected_enabled(self):
        from types import SimpleNamespace
        from custom_components.ha_entity_renamer.controlled_script_cutover import ControlledScriptCutover
        class Actor:
            unique_id='watcher'; _is_enabled=False; _async_detach_triggers=None; calls=0
            async def _async_enable(self): self.calls += 1
        actor=Actor(); state=object(); cutover=object.__new__(ControlledScriptCutover)
        cutover.root=SimpleNamespace(controlled_automation=SimpleNamespace(expected_enabled={}))
        cutover._owns_suspended_automation=lambda entity, original: True
        with self.assertRaises(Refused):
            await cutover._call_automation(actor,state,enabled=False,detach=None,expected=None,enable=True)
        self.assertEqual(actor.calls,0)
