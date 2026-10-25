"""Actual typed escrow and own placeholder consumer. Not full migration acceptance."""
import importlib
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
import unittest

import test_own_helper_unload_lifecycle as base

p = importlib.import_module(base.PACKAGE + '.native_helper_placeholder_effect')


@unittest.skipUnless(base.fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class OwnHelperPlaceholderNativeTests(unittest.IsolatedAsyncioTestCase):
    use_adapter = False
    gap_during_unload = False
    source_advance_phase = None
    asyncSetUp = base.OwnHelperUnloadLifecycleTests.asyncSetUp
    _before_start = base.OwnHelperUnloadLifecycleTests._before_start
    _fixture = base.OwnHelperUnloadLifecycleTests._fixture
    _emit = base.OwnHelperUnloadLifecycleTests._emit
    _flush_documents = base.OwnHelperUnloadLifecycleTests._flush_documents
    guard = base.OwnHelperUnloadLifecycleTests.guard
    _lifecycle = base.OwnHelperUnloadLifecycleTests._lifecycle
    _configure_own_probe = base.OwnHelperUnloadLifecycleTests._configure_own_probe
    _validate_own_probe = base.OwnHelperUnloadLifecycleTests._validate_own_probe
    _prepare_own_plan = base.OwnHelperUnloadLifecycleTests._prepare_own_plan

    async def test_typed_escrow_then_owned_placeholder_then_real_native_setup(self):
        life = await self._lifecycle()
        try:
            await life.quiesce()
            gap, = life._source_gaps.values()
            import copy
            import pickle
            for copier in (copy.copy, copy.deepcopy, pickle.dumps):
                with self.subTest(copier=copier), self.assertRaisesRegex(ValueError, 'ephemeral_only'):
                    copier(gap)
            if self.gap_during_unload:
                await self._emit('115')
                await self._emit('119')
            hass = self.f.hass
            modules = {name: importlib.import_module(base.PACKAGE + '.' + name) for name in
                ('native_registry', 'native_registry_receipts', 'native_input_observation',
                 'native_input_baseline', 'native_restore_state_escrow_receipts',
                 'native_restore_state_escrow_io', 'transaction', 'journal',
                 'native_utility_setup_audit')}
            rr, tx = modules['native_registry_receipts'], modules['transaction']
            chain_store = rr.native_registry_receipt_store(hass, life.store.preparations)
            await chain_store.initialize()
            chain = await rr.RegistryReceiptChain.start(chain_store, life.operation_id,
                life.plan, life.receipt, life.binding['preparations'])
            inputs = chain.reconstruct_inputs()
            self.own_observer.bind_native_chain(chain)
            self.own_observer.bind_activation_lease(self.lease)
            managers = modules['native_input_baseline']._Managers(hass, life.receipt)
            unload_slot = life._slot('unload', self.entry.entry_id, 'receipt')
            unload = life.store.read(unload_slot)
            unload_ref = (unload_slot, unload['digest'])
            original_state = hass.states.get(self.old_helper)
            def source_guard():
                life.require_quiescent()
                self.guard()
                inputs.assert_current(life.receipt)
                # Preserve ORIGINAL manager/store identities while comparing
                # contents only with ORIGINAL plus accepted native deltas.
                self.assertEqual(managers.sample(), tuple(
                    modules['native_input_baseline']._json(chain.accepted_documents()[key]['data'])
                    for key in modules['native_input_baseline']._KEYS))
                life.verify_live_unload_state(self.entry.entry_id, unload_ref, original_state)
            from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
            data = hass.data[DATA_RESTORE_STATE]
            await data.async_dump_states()
            source_guard()
            escrow = modules['native_restore_state_escrow_receipts'].NativeRestoreStateEscrow(
                life, self.entry.entry_id, data, source_guard=source_guard)
            disk = modules['native_restore_state_escrow_io'].EscrowIO._read(data.store.path,
                (self.old_helper, self.target))
            token = await escrow.begin(disk_preimage=disk)
            await escrow.execute(token)
            escrow.require_completed(token)
            journal = modules['journal'].native_journal(hass)
            await journal.initialize()
            record = tx.Record(life.operation_id, life.plan, status='applying', phases=('intent',))
            await journal.create(record)
            context = tx.PhaseContext(await journal.load(life.operation_id), 0)
            async def observe_native():
                nonlocal inputs
                try:
                    inputs = chain.reconstruct_inputs()
                except rr.RegistryReceiptError:
                    if (chain.active is None or chain.store.read(chain.active.reference[0])[
                            'payload']['kind'] not in ('registry_intent', 'helper_setup_intent')):
                        raise
                return await modules['native_input_observation'].async_observe_native_inputs(
                    hass, life.receipt, guard=self.guard, input_state=inputs)
            async def observe():
                result = await observe_native()
                return tuple(rr.RegistryRawObservation(row.key, row.raw, row.identity)
                             for row in result.files)
            def validate_witness(scope):
                parsed = base.lm.validate_lifecycle_receipt_witness(life.store,
                    life.operation_id, life.plan, life.receipt, life.binding['preparations'],
                    root_id=life.root_id)
                self.assertTrue(parsed.inspect()['inactive_receipts_complete'])
                self.assertEqual(scope['lifecycle_root'], [life.root_id, base.lm._digest(life.root)])
                self.assertEqual(scope['unload_receipt'], list(unload_ref))
                self.assertEqual(scope['registry_uuid'], life.own_registry_changes[self.entry.entry_id].identity)
                return True
            io = getattr(self, 'native_io', None) or modules['native_registry'].NativeRegistryIO(hass)
            transition = importlib.import_module(base.PACKAGE + '.native_registry_transition')
            async def registry_effect(context):
                def durable_source_fence(actual_context):
                    source_guard()
                    self.assertEqual(actual_context, context)
                    durable = modules['journal'].decode(json.loads(Path(journal.store._path).read_bytes())['data'])
                    self.assertEqual(durable[life.operation_id], context.record)
                if self.use_adapter:
                    am = importlib.import_module(base.PACKAGE + '.native_registry_transaction')
                    prep = importlib.import_module(base.PACKAGE + '.prepared')
                    adapter = am.ReceiptedRegistryTransactionAdapter(self.native_bridge,
                        lambda ref: prep.decode_change(life.store.preparations.resolve(
                            ref, life.binding['preparations'][ref])),
                        chain, observe_native, journal.load, self.guard)
                    adapter.bind_phase_context(context)
                    self.assertTrue(callable(getattr(adapter, 'bind_own_helper_escrow', None)),
                        'receipted adapter has no live own-helper escrow binding')
                    method = adapter.apply if context.record.direction == 'forward' else adapter.compensate
                    rows_before = dict(chain.store.rows)
                    native_before = io.document('entity')
                    with self.assertRaisesRegex(tx.Refused, 'own_helper_effect_required'):
                        await method(context.step, self.guard)
                    from dataclasses import replace
                    for foreign_token in (replace(token), getattr(self, 'previous_escrow_token', None)):
                        with self.subTest(token=foreign_token), self.assertRaisesRegex(
                                tx.Refused, 'own_helper_live_escrow_required'):
                            adapter.bind_own_helper_escrow(escrow, foreign_token,
                                source_fence=durable_source_fence)
                    async def deferred_fence(actual_context):
                        durable_source_fence(actual_context)
                    adapter.bind_own_helper_escrow(escrow, token, source_fence=deferred_fence)
                    with self.assertRaisesRegex(tx.Refused, 'synchronous_source_fence_required'):
                        await method(context.step, self.guard)
                    self.assertIsNone(chain.active)
                    self.assertEqual(chain.store.rows, rows_before)
                    self.assertEqual(io.document('entity'), native_before)
                    adapter.bind_own_helper_escrow(escrow, token, source_fence=durable_source_fence)
                    mutation = await method(context.step, self.guard)
                    self.previous_escrow_token = token
                    slot = chain._slot('receipt', context.index, context.record.direction)
                    row = chain.store.read(slot)
                    self.assertIsNotNone(row)
                    completed = (slot, row['digest'])
                    stamp = p.decode_effect(json.dumps(
                        row['payload']['own_helper_placeholder']['stamp']).encode('utf-8'))
                    intent = rr.RegistryIntent(tuple(row['payload']['intent']))
                    self.assertTrue(await self.native_history.verify(self.own_history))
                    return mutation, stamp, completed, intent
                before = await observe()
                intent = await chain.begin(context, before)
                before = await observe()
                chain.verify_intent_source(intent, context, before)
                def fence(scope):
                    durable_source_fence(context)
                    escrow.require_completed(token)
                    chain.verify_intent_source(intent, context, before)
                    return True
                node = p.OwnHelperPlaceholder.capture(io=io, lifecycle=life, chain=chain,
                    context=context, intent=intent, observations=before,
                    live_guard=self.guard, fresh_source_fence=fence,
                    validate_unload_witness=validate_witness,
                    verify_live_unload_state=lambda actual, ref, state:
                        actual.verify_live_unload_state(self.entry.entry_id, ref, state))
                mutation = node.mutate()
                self.guard()
                await io.settle()
                self.guard()
                await io.flush('entity')
                self.guard()
                await modules['native_restore_state_escrow_receipts'].copy_escrow_witness(
                    life.store, chain.store, chain.operation_id, chain.plan,
                    chain.original_receipt, life.binding['preparations'], escrow.root_reference)
                self.guard()
                after = await observe()
                source_raw = next(row.raw for row in before if row.key == 'core.entity_registry')
                result_raw = next(row.raw for row in after if row.key == 'core.entity_registry')
                audit = transition.NativeRegistryEffectAudit('entity', self.row['id'],
                    'forward' if context.record.direction == 'forward' else 'reverse',
                    rr._sha(source_raw), rr._sha(result_raw),
                    p.prepared_registry_digest(life.own_registry_changes[self.entry.entry_id]),
                    json.loads(mutation.before_json)['modified_at'],
                    json.loads(mutation.after_json)['modified_at'])
                stamp = node.finish()
                completed = await chain.complete(intent, context, before, after,
                    mutation=mutation, audit=audit, placeholder_stamp=stamp,
                    escrow_root_reference=escrow.root_reference)
                return mutation, stamp, completed, intent
            mutation, stamp, completed, intent = await registry_effect(context)
            from copy import deepcopy
            attrs = json.loads(unload['payload']['native_unload']['payload']['state_json'])['attributes']
            expected_scope = {'version': life.receipt.version,
                'source_pins': {key: modules['native_utility_setup_audit']._SOURCES[life.receipt.version][key]
                    for key in ('core.py', 'helpers/entity_registry.py', 'helpers/entity.py')},
                'operation_id': life.operation_id, 'fingerprint': life.plan.fingerprint,
                'chain_root': chain.root_id, 'registry_intent': list(intent.reference),
                'phase': chain._context(context),
                'lifecycle_root': [life.root_id, base.lm._digest(life.root)],
                'unload_receipt': list(unload_ref), 'entry_id': self.entry.entry_id,
                'registry_uuid': self.row['id'], 'prepared_digest': p.prepared_registry_digest(
                    life.own_registry_changes[self.entry.entry_id]), 'direction': 'forward',
                'source': self.old_helper, 'target': self.target,
                'source_attrs': attrs,
                'source_state_json': unload['payload']['native_unload']['payload']['state_json'],
                'source_reserved': unload['payload']['native_unload']['payload']['reserved'],
                'target_attrs': deepcopy(attrs)}
            self.assertTrue(p.validate_effect(stamp, expected_scope=expected_scope,
                change=life.own_registry_changes[self.entry.entry_id], mutation=mutation,
                validate_unload_witness=validate_witness))
            self.assertEqual(p.decode_effect(stamp.encode()), stamp)
            self.assertIsNone(hass.states.get(self.old_helper))
            self.assertTrue(hass.states.async_available(self.old_helper))
            self.assertEqual(hass.states.get(self.target).state, 'unavailable')
            self.assertEqual(self.f.entity.async_get(self.target).id, self.row['id'])
            stored = chain.store.read(completed[0])['payload']
            self.assertIn('own_helper_placeholder', stored)
            fresh_store = rr.native_registry_receipt_store(hass, life.store.preparations)
            await fresh_store.initialize()
            reopened = rr.RegistryReceiptChain.reopen(fresh_store, life.operation_id,
                life.plan, life.receipt, life.binding['preparations'])
            self.assertFalse(reopened.writable)
            self.assertEqual(reopened.reconstruct_inputs().documents(), chain.accepted_documents())
            # Corruption tests use a detached READONLY view of the actual saved
            # graph, never a manufactured successful native call or live effect.
            def no_subeffect(payload):
                payload.pop('own_helper_placeholder')
            def foreign_escrow(payload):
                payload['own_helper_placeholder']['escrow_root'][1] = '0' * 64
            def invented_scope(payload):
                sub = payload['own_helper_placeholder']['stamp']
                scope = json.loads(sub['scope_json'])
                scope['operation_id'] = 'foreign-operation'
                sub['scope_json'] = json.dumps(scope)
            def departed_state_retained(payload):
                sub = payload['own_helper_placeholder']['stamp']
                body = json.loads(sub['body_json'])
                body['finish']['source_state'] = original_state.as_dict_json.decode()
                sub['body_json'] = json.dumps(body)
            def forged_source_reservation(payload):
                sub = payload['own_helper_placeholder']['stamp']
                scope, body = json.loads(sub['scope_json']), json.loads(sub['body_json'])
                scope['source_reserved'] = not scope['source_reserved']
                body['capture']['source_reserved'] = scope['source_reserved']
                sub['scope_json'], sub['body_json'] = json.dumps(scope), json.dumps(body)
            for corrupt in (no_subeffect, foreign_escrow, invented_scope, departed_state_retained,
                            forged_source_reservation):
                with self.subTest(corrupt=corrupt.__name__):
                    view = rr.RegistryReceiptStore(None, life.store.preparations)
                    view.rows = deepcopy(fresh_store.rows)
                    changed = view.rows[completed[0]]['payload']
                    corrupt(changed)
                    view.rows[completed[0]]['digest'] = rr._digest(changed)
                    with self.assertRaises(ValueError):
                        rr.RegistryReceiptChain.reopen(view, life.operation_id,
                            life.plan, life.receipt, life.binding['preparations'])
            await journal.save(tx.Record(life.operation_id, life.plan,
                status='applied', phases=('verified',)))
            async def accepted_source(auth):
                record = await journal.load(life.operation_id)
                self.assertEqual((auth.operation_id, auth.fingerprint, auth.status),
                    (life.operation_id, life.plan.fingerprint, record.status))
                observed = await observe()
                chain.expected(tx.PhaseContext(record, 0), observed)
                return chain.verify_setup_authorization(auth)
            runtime_calls = 0
            async def runtime(entry_id, original, auth):
                nonlocal runtime_calls
                runtime_calls += 1
                self.assertEqual(entry_id, self.entry.entry_id)
                self.assertEqual(original['state'], 'loaded')
                self.assertTrue(await self.own_observer.verify_runtime(
                    life.helper_snapshots[entry_id][0]))
                if self.source_advance_phase == 'late_invalid' and runtime_calls == 2:
                    # Real producer writes after setup effects and the lifecycle
                    # receipt, inside the final runtime verification await.
                    await self._emit('111', unit='Wh')
                    await self._emit('111', unit='kWh')
                    gm = importlib.import_module(base.PACKAGE + '.native_utility_source_gap')
                    with self.assertRaises(gm.UtilitySourceGapError):
                        gap.check()
                    # Current coherence cannot erase the observed invalid stream.
                    self.assertTrue(await self.own_observer.verify_runtime(
                        life.helper_snapshots[entry_id][0]))
                return True
            life.verify_accepted_native_chain = accepted_source
            life.verify_runtime = runtime
            setup = importlib.import_module(base.PACKAGE + '.native_helper_setup_effects')
            life.setup_effect = setup.NativeHelperSetupEffects(hass, chain, observe_native,
                journal.load, guard=self.guard)
            record = await journal.load(life.operation_id)
            head = chain.expected(tx.PhaseContext(record, 0), await observe())
            authorization = base.lm.RestoreAuthorization(life.operation_id,
                life.plan.fingerprint, head, 'applied')
            if self.source_advance_phase == 'late_invalid':
                gm = importlib.import_module(base.PACKAGE + '.native_utility_source_gap')
                with self.assertRaises(gm.UtilitySourceGapError):
                    await life.restore(authorization)
                self.assertTrue(life.failed)
                self.assertFalse(life._source_gaps)
                self.assertEqual(runtime_calls, 2)
                return
            pending_source = []
            if self.source_advance_phase in ('setup', 'flush'):
                original_settle = life.settle
                owner_task = asyncio.current_task()
                emitted = False
                async def advance():
                    collector = sys.getprofile().__self__
                    self.assertEqual(collector._sealed, self.source_advance_phase == 'flush')
                    if self.source_advance_phase == 'flush':
                        frames = []
                        current = owner_task.get_coro()
                        while current is not None:
                            frames.append(getattr(current, 'cr_code', None))
                            current = getattr(current, 'cr_await', None)
                        self.assertIn(modules['native_registry'].NativeRegistryIO.flush.__code__, frames)
                    await self._emit('119')
                async def settle_with_source():
                    nonlocal emitted
                    result = await original_settle()
                    if not emitted:
                        emitted = True
                        self.assertEqual(self.entry.state.value, 'loaded')
                        self.assertIsNotNone(chain.active)
                        if self.source_advance_phase == 'setup':
                            await advance()
                        else:
                            hass.loop.call_soon(lambda: pending_source.append(
                                hass.async_create_task(advance())))
                    return result
                life.settle = settle_with_source
            await life.restore(authorization)
            for task in pending_source:
                await task
            if self.source_advance_phase in ('setup', 'flush'):
                self.assertTrue(emitted)
                self.assertFalse(life._source_gap_data[self.entry.entry_id].native_call)
            self.assertTrue(life.inspect()['activation_receipts_complete'])
            self.assertTrue(chain.verify_setup_authorization(authorization))
            from homeassistant.helpers.entity_platform import async_get_platforms
            entity, = [e for platform in async_get_platforms(hass, 'utility_meter')
                       if platform.config_entry is self.entry for e in platform.entities.values()]
            self.assertEqual(entity.entity_id, self.target)
            self.assertEqual(entity.native_value, Decimal('19' if self.gap_during_unload
                or self.source_advance_phase else '11'))
            await self._emit('119')
            self.assertEqual(entity.native_value, Decimal('19'))
            self.assertTrue(await self.own_observer.verify_runtime(life.helper_snapshots[self.entry.entry_id][0]))
            self.assertEqual(self.f.entity.async_get(self.target).id, self.row['id'])

            # Reverse starts from the accepted forward setup, while retaining
            # the SAME ORIGINAL A0/S0 and preparations. B1 comes from real unload.
            forward_escrow_receipt = escrow.inspect()['receipt']
            record = await journal.load(life.operation_id)
            source_head = chain.expected(tx.PhaseContext(record, 0), await observe())
            source_auth = base.lm.RestoreAuthorization(life.operation_id,
                life.plan.fingerprint, source_head, 'applied')
            life = await base.lm.NativeHelperLifecycle.start(*self.args,
                **dict(self.kwargs, verify_accepted_native_chain=accepted_source,
                    verify_runtime=runtime, setup_effect=life.setup_effect,
                    source_authorization=source_auth))
            await life.quiesce()
            unload_slot = life._slot('unload', self.entry.entry_id, 'receipt')
            unload = life.store.read(unload_slot)
            unload_ref = (unload_slot, unload['digest'])
            original_state = hass.states.get(self.target)
            self.assertEqual(original_state.state, 'unavailable')
            self.assertIs(unload['payload']['native_unload']['payload']['reserved'], False)
            await data.async_dump_states()
            escrow = modules['native_restore_state_escrow_receipts'].NativeRestoreStateEscrow(
                life, self.entry.entry_id, data, source_guard=source_guard)
            disk = modules['native_restore_state_escrow_io'].EscrowIO._read(data.store.path,
                (self.target, self.old_helper))
            token = await escrow.begin(disk_preimage=disk,
                forward_receipt=forward_escrow_receipt)
            await escrow.execute(token)
            escrow.require_completed(token)
            await journal.save(tx.Record(life.operation_id, life.plan,
                status='rolling_back', phases=('undo_intent',), direction='rollback'))
            context = tx.PhaseContext(await journal.load(life.operation_id), 0)
            mutation, stamp, completed, intent = await registry_effect(context)
            self.assertIsNone(hass.states.get(self.target))
            self.assertTrue(hass.states.async_available(self.target))
            self.assertEqual(hass.states.get(self.old_helper).state, 'unavailable')
            await journal.save(tx.Record(life.operation_id, life.plan,
                status='rolled_back', phases=('undone',), direction='rollback'))
            record = await journal.load(life.operation_id)
            head = chain.expected(tx.PhaseContext(record, 0), await observe())
            auth = base.lm.RestoreAuthorization(life.operation_id, life.plan.fingerprint,
                head, 'rolled_back')
            await life.restore(auth)
            self.assertTrue(life.inspect()['activation_receipts_complete'])
            self.assertTrue(chain.verify_setup_authorization(auth))
            entity, = [e for platform in async_get_platforms(hass, 'utility_meter')
                       if platform.config_entry is self.entry for e in platform.entities.values()]
            self.assertEqual(entity.entity_id, self.old_helper)
            self.assertEqual(entity.native_value, Decimal('19'))
            self.assertEqual(self.f.entity.async_get(self.old_helper).id, self.row['id'])
            await self._emit('123')
            self.assertEqual(entity.native_value, Decimal('23'))
            self.assertTrue(await self.own_observer.verify_runtime(life.helper_snapshots[self.entry.entry_id][0]))
        finally:
            life.close_source_gaps()
            self.lease.release()


@unittest.skipUnless(base.fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class ReceiptedOwnHelperNativeTests(OwnHelperPlaceholderNativeTests):
    use_adapter = True

    async def _before_start(self, hass, root):
        from homeassistant.helpers.recorder import async_initialize_recorder
        from homeassistant.setup import async_setup_component
        async_initialize_recorder(hass)
        self.assertTrue(await async_setup_component(hass, 'recorder', {'recorder': {
            'db_url': 'sqlite:///' + str(root / 'own-helper.db'), 'commit_interval': 0,
            'auto_purge': False, 'auto_repack': False}}))
        await super()._before_start(hass, root)

    async def _prepare_own_change(self):
        nr = importlib.import_module(base.PACKAGE + '.native_registry')
        recorder_module = importlib.import_module(base.PACKAGE + '.recorder_guard')
        history_module = importlib.import_module(base.PACKAGE + '.history_guard')
        self.native_io = nr.NativeRegistryIO(self.f.hass)
        recorder = recorder_module.NativeRecorderGuard(self.f.hass)
        self.native_history = history_module.NativeHistoryGuard(recorder)
        self.native_bridge = nr.NativeRegistryBridge(self.native_io, recorder,
            history_guard=self.native_history)
        change = await self.native_bridge.prepare('entity', self.row['id'],
            {'entity_id': self.target}, self.guard)
        self.own_history = json.loads(change.history_before_json)
        self.assertTrue(self.own_history['state_ids'])
        self.assertGreater(self.own_history['content']['states']['count'], 0)
        return change


@unittest.skipUnless(base.fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class ReceiptedOwnHelperGapNativeTests(ReceiptedOwnHelperNativeTests):
    gap_during_unload = True


class ReceiptedOwnHelperSetupSourceNativeTests(ReceiptedOwnHelperNativeTests):
    source_advance_phase = 'setup'


class ReceiptedOwnHelperFlushSourceNativeTests(ReceiptedOwnHelperNativeTests):
    source_advance_phase = 'flush'


class ReceiptedOwnHelperLateInvalidSourceNativeTests(ReceiptedOwnHelperNativeTests):
    source_advance_phase = 'late_invalid'


@unittest.skipUnless(base.fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class ReceiptedOwnHelperMixedYAMLNativeTests(ReceiptedOwnHelperNativeTests):
    """One ORIGINAL own-ID/include_dir operation, real Coordinator and setup.

    Overrides the inherited test: exactly one additional runnable result.
    No native positive-effect method is patched; source writes use _emit.
    """
    with_yaml = True

    async def _before_start(self, hass, root):
        import test_receipted_helper_native as mixed
        # Reuse Recorder, actual HTTP and registered script/editor setup.
        await mixed.NativeReceiptedHelperTests._before_start(self, hass, root)
        await base.OwnHelperUnloadLifecycleTests._before_start(self, hass, root)

    async def _configure_own_probe(self, hass, root):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        (root / 'scripts').mkdir()
        (root / 'configuration.yaml').write_text(
            'script: !include_dir_merge_named scripts\n')
        (root / 'scripts/probe.yaml').write_text('own_id_probe:\n  sequence:\n'
            '    - action: renamer_probe.capture\n      data:\n'
            '        value: "{{ states(\'' + self.old_helper + '\') }}"\n')
        self.script_values = []
        async def capture(call):
            self.script_values.append(str(call.data['value']))
        hass.services.async_register('renamer_probe', 'capture', capture)
        self.assertTrue(await async_setup_component(hass, 'script',
            await async_hass_config_yaml(hass)))
        await self._assert_script('11')

    async def _assert_script(self, expected):
        from homeassistant.core import Context
        self.script_values.clear()
        await self.f.hass.services.async_call('script', 'own_id_probe', {},
            blocking=True, context=Context())
        self.assertEqual(self.script_values, [expected])

    async def _validate_own_probe(self, hass, proposal, root):
        receipt, evidence = await base.fixture.fixtures.validation.async_validate_proposed_with_sources(
            hass, proposal, root.parent)
        binding = importlib.import_module(base.PACKAGE + '.yaml_source_binding')
        bound = await binding.async_bind_original_runtime(
            hass, receipt, evidence, guard=self.guard)
        self.assertTrue(bound.verified, bound.blockers)
        self.assertFalse(bound.migration_ready)
        self.assertEqual(receipt.changed_domains, ('script',))
        self.assertTrue(all(before is not None and after is not None
            for _, before, after in receipt.runtime_expected))
        return receipt

    async def _prepare_own_plan(self, preparations, step, digest, native_ref, receipt, proposal):
        prep = importlib.import_module(base.PACKAGE + '.prepared')
        tx = importlib.import_module(base.PACKAGE + '.transaction')
        ym = importlib.import_module(base.PACKAGE + '.yaml_transaction')
        self.yaml_change = await base.fixture.fixtures.validation._settled_executor(
            self.f.hass, ym._capture, receipt, proposal)
        self.assertEqual([row.path for row in self.yaml_change.files], ['scripts/probe.yaml'])
        ref = 'own-helper-yaml'
        yaml_digest = await preparations.create(ref, prep.encode_change(self.yaml_change))
        yaml_step = tx.Step(ref, 'yaml', self.yaml_change.identity,
            self.yaml_change.before_revision, self.yaml_change.after_revision, ref)
        scopes = ('registry', 'yaml')
        return tx.Plan('own-helper-unload', scopes, scopes, (step, yaml_step),
            'native-own-helper-include-dir', scopes, native_ref), {
                'own-helper-change': digest, ref: yaml_digest}

    async def test_typed_escrow_then_owned_placeholder_then_real_native_setup(self):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        life = await self._lifecycle()
        lives = [life]
        try:
            hass = self.f.hass
            m = {name: importlib.import_module(base.PACKAGE + '.' + name) for name in (
                'native_registry_receipts', 'native_registry_transaction', 'native_input_baseline',
                'native_input_observation', 'native_restore_state_escrow_receipts',
                'native_restore_state_escrow_io', 'native_yaml_transaction', 'yaml_editor_locks',
                'yaml_runtime_observer', 'native_candidate_validation', 'prepared', 'journal',
                'transaction', 'native_helper_setup_effects')}
            rr, tx, prep = (m[name] for name in ('native_registry_receipts', 'transaction', 'prepared'))
            original = (life.plan, life.receipt, dict(life.binding['preparations']),
                        self.own_observer.runtime_baselines())
            baseline, = original[3]
            self.assertEqual((json.loads(baseline.payload_json)['a0'],
                              json.loads(baseline.payload_json)['s0']), ('11', '111'))
            self.assertEqual(len(life.plan.steps), 2)
            self.assertEqual(tuple(step.adapter for step in life.plan.steps), ('registry', 'yaml'))
            chain_store = rr.native_registry_receipt_store(hass, life.store.preparations)
            await chain_store.initialize()
            chain = await rr.RegistryReceiptChain.start(chain_store, life.operation_id,
                life.plan, life.receipt, life.binding['preparations'])
            self.own_observer.bind_native_chain(chain)
            self.own_observer.bind_activation_lease(self.lease)
            inputs = chain.reconstruct_inputs()
            managers = m['native_input_baseline']._Managers(hass, life.receipt)
            journal = m['journal'].native_journal(hass)
            await journal.initialize()
            locks = m['yaml_editor_locks'].NativeYAMLEditorLocks(hass)
            self.assertGreaterEqual(len(locks.enumerate()), 3)
            yaml_runtime = m['yaml_runtime_observer'].NativeYAMLRuntimeReadback(hass)
            proofs = []

            def quiescent():
                self.guard()
                life.require_quiescent()

            async def observe(input_state=None):
                nonlocal inputs
                if input_state is None:
                    try:
                        inputs = chain.reconstruct_inputs()
                    except rr.RegistryReceiptError:
                        if (chain.active is None or chain.store.read(chain.active.reference[0])[
                                'payload']['kind'] not in ('registry_intent', 'helper_setup_intent')):
                            raise
                    input_state = inputs
                return await m['native_input_observation'].async_observe_native_inputs(
                    hass, life.receipt, guard=self.guard, input_state=input_state)

            def raw(observed):
                return tuple(rr.RegistryRawObservation(row.key, row.raw, row.identity)
                             for row in observed.files)

            def source_guard():
                quiescent()
                inputs.assert_current(life.receipt)
                self.assertEqual(managers.sample(), tuple(m['native_input_baseline']._json(
                    chain.accepted_documents()[key]['data'])
                    for key in m['native_input_baseline']._KEYS))
                life.verify_live_unload_state(self.entry.entry_id, unload_ref, original_state)

            def source_fence(context):
                source_guard()
                durable = m['journal'].decode(json.loads(
                    Path(journal.store._path).read_bytes())['data'])
                self.assertEqual(durable[life.operation_id], context.record)

            resolver = lambda ref: prep.decode_change(life.store.preparations.resolve(
                ref, life.binding['preparations'][ref]))
            async def verify(position):
                quiescent()
                change = life.own_registry_changes[self.entry.entry_id]
                self.assertEqual((await self.native_bridge.inspect(change)).position,
                    'after' if position == tx.Position.AFTER else 'before')
                self.assertEqual(await yaml_runtime.position(self.yaml_change), position)
                self.assertTrue(await self.native_history.verify(self.own_history))
                return True

            async def verify_yaml(position):
                await verify(position)
                async with locks.hold(quiescent):
                    proof = await m['native_candidate_validation'].async_revalidate_accepted_candidate(
                        hass, chain, journal.load, self.f.root.parent, quiescent)
                    self.assertEqual(proof.direction,
                        'forward' if position == tx.Position.AFTER else 'rollback')
                    self.assertEqual(proof.fingerprint, original[0].fingerprint)
                    proofs.append(proof)
                return True

            registry = m['native_registry_transaction'].ReceiptedRegistryTransactionAdapter(
                self.native_bridge, resolver, chain, observe, journal.load, quiescent,
                full_verify=lambda _: verify(tx.Position.AFTER),
                full_verify_rollback=lambda _: verify(tx.Position.BEFORE))
            yaml_adapter = m['native_yaml_transaction'].ReceiptedYAMLTransactionAdapter(
                hass, resolver, locks, yaml_runtime, chain, observe, journal.load, quiescent,
                context=Context(), full_verify=lambda _: verify_yaml(tx.Position.AFTER),
                full_verify_rollback=lambda _: verify_yaml(tx.Position.BEFORE))
            coordinator = tx.Coordinator({'registry': registry, 'yaml': yaml_adapter}, journal,
                quiescent, required_scopes=('registry', 'yaml'))

            async def authorization():
                record = await journal.load(life.operation_id)
                head = chain.expected(tx.PhaseContext(record, 0), raw(await observe()))
                return base.lm.RestoreAuthorization(life.operation_id,
                    life.plan.fingerprint, head, record.status)

            async def accepted_source(auth):
                record = await journal.load(life.operation_id)
                self.assertEqual((auth.operation_id, auth.fingerprint, auth.status),
                    (life.operation_id, life.plan.fingerprint, record.status))
                chain.expected(tx.PhaseContext(record, 0), raw(await observe()))
                phase = 'verified' if record.status == 'applied' else 'undone'
                self.assertEqual(record.phases, (phase, phase))
                return chain.verify_setup_authorization(auth)

            async def runtime(entry_id, original_activation, auth):
                self.assertEqual(entry_id, self.entry.entry_id)
                self.assertEqual(original_activation['state'], 'loaded')
                return await self.own_observer.verify_runtime(life.helper_snapshots[entry_id][0])

            setup_effect = m['native_helper_setup_effects'].NativeHelperSetupEffects(
                hass, chain, observe, journal.load, guard=self.guard)
            life.verify_accepted_native_chain = accepted_source
            life.verify_runtime = runtime
            life.setup_effect = setup_effect
            forward_escrow_receipt = None
            forward_root = life.root_id
            for reverse in (False, True):
                if reverse:
                    source_auth = await authorization()
                    life = await base.lm.NativeHelperLifecycle.start(*self.args,
                        **dict(self.kwargs, verify_accepted_native_chain=accepted_source,
                            verify_runtime=runtime, setup_effect=setup_effect,
                            source_authorization=source_auth))
                    lives.append(life)
                    self.assertNotEqual(life.root_id, forward_root)
                self.assertEqual((life.plan, life.receipt, dict(life.binding['preparations']),
                    life.runtime_baselines()), original)
                await life.quiesce()
                slot = life._slot('unload', self.entry.entry_id, 'receipt')
                unload = life.store.read(slot)
                self.assertIn('native_unload', unload['payload'])
                unload_ref = (slot, unload['digest'])
                source_id, target_id = ((self.target, self.old_helper) if reverse else
                                        (self.old_helper, self.target))
                original_state = hass.states.get(source_id)
                self.assertEqual(original_state.state, 'unavailable')
                if not reverse:
                    await self._emit('115')
                    await self._emit('119')
                await observe()
                data = hass.data[DATA_RESTORE_STATE]
                await data.async_dump_states()
                escrow = m['native_restore_state_escrow_receipts'].NativeRestoreStateEscrow(
                    life, self.entry.entry_id, data, source_guard=source_guard)
                disk = m['native_restore_state_escrow_io'].EscrowIO._read(
                    data.store.path, (source_id, target_id))
                options = {'forward_receipt': forward_escrow_receipt} if reverse else {}
                token = await escrow.begin(disk_preimage=disk, **options)
                await escrow.execute(token)
                escrow.require_completed(token)
                registry.bind_own_helper_escrow(escrow, token, source_fence=source_fence)
                record = (await coordinator.rollback(life.operation_id) if reverse else
                    await coordinator.apply(life.plan, life.plan.fingerprint, life.operation_id))
                self.assertEqual(record.status, 'rolled_back' if reverse else 'applied',
                    (record.status, record.reason, record.phases))
                self.assertEqual(record.phases, ('undone', 'undone') if reverse else
                    ('verified', 'verified'))
                # Both effects are actual accepted native returns in one ledger.
                direction = 'rollback' if reverse else 'forward'
                registry_row = chain.store.read(chain._slot('receipt', 0, direction))['payload']
                self.assertIn('own_helper_placeholder', registry_row)
                yaml_row = chain.store.read(chain._slot('receipt', 1, direction))['payload']
                self.assertEqual(yaml_row['kind'], 'yaml_receipt')
                self.assertTrue(yaml_row['calls'])
                self.assertEqual((self.f.root / 'scripts/probe.yaml').read_bytes(),
                    self.yaml_change.files[0].before if reverse else self.yaml_change.files[0].after)
                self.assertIsNone(hass.states.get(source_id))
                self.assertTrue(hass.states.async_available(source_id))
                self.assertEqual(hass.states.get(target_id).state, 'unavailable')
                self.assertEqual(self.f.entity.async_get(target_id).id, self.row['id'])
                # The reloaded script now resolves the newly selected placeholder.
                await self._assert_script('unavailable')
                await life.restore(await authorization())
                self.assertTrue(life.inspect()['activation_receipts_complete'])
                self.assertTrue(chain.verify_setup_authorization(await authorization()))
                entity, = [e for platform in async_get_platforms(hass, 'utility_meter')
                    if platform.config_entry is self.entry for e in platform.entities.values()]
                self.assertEqual(entity.entity_id, target_id)
                self.assertEqual(entity.native_value, Decimal('19'))
                self.assertEqual(self.f.entity.async_get(target_id).id, self.row['id'])
                gap = life._source_gap_data[self.entry.entry_id]
                self.assertEqual((gap.original_a0, gap.original_s0), ('11', '111'))
                self.assertEqual(gap.counter_after, '19')
                self.assertEqual(gap.source_value, '119')
                self.assertEqual(gap.capture_source, '119' if reverse else '111')
                self.assertEqual(gap.physical_source[0], self.source_uuid)
                await self._assert_script('19')
                self.assertTrue(await self.native_history.verify(self.own_history))
                self.assertEqual(self.own_observer.runtime_baselines(), original[3])
                fresh_preparations = prep.native_prepared_store(hass)
                await fresh_preparations.initialize()
                fresh_store = rr.native_registry_receipt_store(hass, fresh_preparations)
                await fresh_store.initialize()
                fresh_chain = rr.RegistryReceiptChain.reopen(fresh_store, life.operation_id,
                    original[0], original[1], original[2])
                self.assertFalse(fresh_chain.writable)
                self.assertEqual(fresh_chain.reconstruct_inputs().documents(),
                    chain.accepted_documents())
                self.assertTrue(fresh_chain.verify_setup_authorization(await authorization()))
                if not reverse:
                    forward_escrow_receipt = escrow.inspect()['receipt']
            ledger = sorted((row['payload'] for row in chain.store.rows.values()
                if row['payload'].get('chain') == chain.root_id
                and row['payload'].get('kind') in (
                    'registry_receipt', 'yaml_receipt', 'helper_setup_receipt')),
                key=lambda row: row['sequence'])
            self.assertEqual([(row['kind'], row['binding']['direction']) for row in ledger], [
                ('registry_receipt', 'forward'), ('yaml_receipt', 'forward'),
                ('helper_setup_receipt', 'forward'), ('yaml_receipt', 'rollback'),
                ('registry_receipt', 'rollback'), ('helper_setup_receipt', 'rollback')])
            self.assertEqual([proof.direction for proof in proofs], ['forward', 'rollback'])
            await self._emit('123')
            self.assertEqual(entity.native_value, Decimal('23'))
            await self._assert_script('23')
            self.assertTrue(await self.own_observer.verify_runtime(
                life.helper_snapshots[self.entry.entry_id][0]))
            self.assertEqual(self.own_observer.runtime_baselines(), original[3])
            self.assertTrue(await self.native_history.verify(self.own_history))
        finally:
            for active in lives:
                active.close_source_gaps()
            self.lease.release()
