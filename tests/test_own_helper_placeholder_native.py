"""Actual typed escrow and own placeholder consumer. Not full migration acceptance."""
import importlib
import json
from decimal import Decimal
from pathlib import Path
import unittest

import test_own_helper_unload_lifecycle as base

p = importlib.import_module(base.PACKAGE + '.native_helper_placeholder_effect')


@unittest.skipUnless(base.fixture.fixtures.HAS_HA, 'requires actual pinned HA')
class OwnHelperPlaceholderNativeTests(unittest.IsolatedAsyncioTestCase):
    use_adapter = False
    asyncSetUp = base.OwnHelperUnloadLifecycleTests.asyncSetUp
    _before_start = base.OwnHelperUnloadLifecycleTests._before_start
    _fixture = base.OwnHelperUnloadLifecycleTests._fixture
    _emit = base.OwnHelperUnloadLifecycleTests._emit
    _flush_documents = base.OwnHelperUnloadLifecycleTests._flush_documents
    guard = base.OwnHelperUnloadLifecycleTests.guard
    _lifecycle = base.OwnHelperUnloadLifecycleTests._lifecycle

    async def test_typed_escrow_then_owned_placeholder_then_real_native_setup(self):
        life = await self._lifecycle()
        try:
            await life.quiesce()
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
            async def runtime(entry_id, original, auth):
                self.assertEqual(entry_id, self.entry.entry_id)
                self.assertEqual(original['state'], 'loaded')
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
            await life.restore(authorization)
            self.assertTrue(life.inspect()['activation_receipts_complete'])
            self.assertTrue(chain.verify_setup_authorization(authorization))
            from homeassistant.helpers.entity_platform import async_get_platforms
            entity, = [e for platform in async_get_platforms(hass, 'utility_meter')
                       if platform.config_entry is self.entry for e in platform.entities.values()]
            self.assertEqual(entity.entity_id, self.target)
            self.assertEqual(entity.native_value, Decimal('11'))
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
