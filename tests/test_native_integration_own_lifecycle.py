"""Draft native own-ID Integration acceptance; owner runs through FIFO.

Real producer, Recorder, native unload/setup, schema2 transfer, own registry
adapter, YAML editor locks, Coordinator/check_config and immutable reopen.
No counter/cache/native helper field seed and no manufactured Event.
"""
import asyncio
from decimal import Decimal
import importlib
import json
from pathlib import Path
import unittest

import test_native_input_baseline as fixture
import test_native_helper_quiescence as quiescence
import test_native_integration_transfer as integration
import test_own_helper_placeholder_native as utility_test
import test_own_helper_unload_lifecycle as base

mixed = utility_test
PACKAGE = fixture.PACKAGE


@unittest.skipUnless(fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationOwnLifecycleTests(unittest.IsolatedAsyncioTestCase):
    zero = False
    distinct_last_valid = False
    with_yaml = True
    asyncSetUp = quiescence.NativeHelperQuiescenceTests.asyncSetUp
    _before_start = mixed.ReceiptedOwnHelperMixedYAMLNativeTests._before_start
    _flush_documents = quiescence.NativeHelperQuiescenceTests._flush_documents
    _validate_own_probe = mixed.ReceiptedOwnHelperMixedYAMLNativeTests._validate_own_probe
    _prepare_own_plan = mixed.ReceiptedOwnHelperMixedYAMLNativeTests._prepare_own_plan
    _prepare_own_change = utility_test.ReceiptedOwnHelperNativeTests._prepare_own_change
    prepare = integration.NativeIntegrationTransferTests.prepare
    current = integration.NativeIntegrationTransferTests.current
    emit = integration.NativeIntegrationTransferTests.emit

    def guard(self):
        self.assertIs(self.f.hass.config_entries.async_get_entry(self.entry.entry_id), self.entry)
        rows = [row for row in self.f.entity.entities.values()
                if row.config_entry_id == self.entry.entry_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, self.uuid)
        self.assertIn(rows[0].entity_id, (self.old_helper, self.target))

    async def _assert_script(self, expected):
        from decimal import InvalidOperation
        from homeassistant.core import Context
        self.script_values.clear()
        await self.f.hass.services.async_call('script', 'own_id_probe', {},
            blocking=True, context=Context())
        self.assertEqual(len(self.script_values), 1)
        # Native script templates coerce numeric strings (0.00 becomes 0.0).
        # Exact accumulator/last_valid tuples are asserted separately below.
        try:
            actual, wanted = Decimal(self.script_values[0]), Decimal(expected)
        except InvalidOperation:
            self.assertEqual(self.script_values, [expected])
        else:
            self.assertEqual(actual, wanted)

    async def _configure_own_probe(self, hass, root):
        from homeassistant.config import async_hass_config_yaml
        from homeassistant.setup import async_setup_component
        (root / 'scripts').mkdir()
        (root / 'configuration.yaml').write_text('script: !include_dir_merge_named scripts\n')
        (root / 'scripts/probe.yaml').write_text('own_id_probe:\n  sequence:\n'
            '    - action: renamer_probe.capture\n      data:\n'
            '        value: "{{ states(\'' + self.old_helper + '\') }}"\n')
        self.script_values = []
        async def capture(call):
            self.script_values.append(str(call.data['value']))
        hass.services.async_register('renamer_probe', 'capture', capture)
        self.assertTrue(await async_setup_component(hass, 'script', await async_hass_config_yaml(hass)))
        await self._assert_script(hass.states.get(self.old_helper).state)

    async def _lifecycle(self):
        await self.prepare(zero=self.zero)
        hass = self.f.hass
        if self.distinct_last_valid:
            self.assertTrue(await hass.config_entries.async_unload(self.entry.entry_id))
            self.assertTrue(await hass.config_entries.async_setup(self.entry.entry_id))
            await hass.async_block_till_done()
            self.old = self.current()
            self.assertNotEqual(self.old._state, self.old._last_valid_state)
        from homeassistant.helpers import entity_registry as er
        self.f.entity.async_update_entity(self.old.entity_id, name='Preserved integration name',
            hidden_by=er.RegistryEntryHider.USER, aliases={'integration-preserved-alias'})
        await hass.async_block_till_done()
        self.old_helper, self.target = self.old.entity_id, 'sensor.integration_own_changed'
        self.source_uuid = self.f.entity.async_get(self.producer.entity_id).id
        self.codec = importlib.import_module(PACKAGE + '.native_restore_state_payload')
        hn = importlib.import_module(PACKAGE + '.helper_native')
        self.before = hn.read_entry(hass, self.entry.entry_id)
        await self._configure_own_probe(hass, self.f.root)
        await self._flush_documents(initialize=True)
        yp = importlib.import_module(PACKAGE + '.yaml_plan')
        proposal = yp.plan_yaml(self.f.root, {self.old_helper: self.target})
        self.assertTrue(proposal.ready, proposal.issues)
        receipt = await self._validate_own_probe(hass, proposal, self.f.root)
        self.assertTrue(receipt.ready_for_apply)
        original = await fixture.baseline.async_capture_native_input_baseline(hass, receipt, guard=self.guard)
        prep = importlib.import_module(PACKAGE + '.prepared')
        tx = importlib.import_module(PACKAGE + '.transaction')
        preparations = prep.native_prepared_store(hass)
        await preparations.initialize()
        native_ref = await preparations.create_native_inputs('own-helper-original', original)
        ua = importlib.import_module(PACKAGE + '.native_utility_unload_audit')
        self.row = ua._row(self.f.entity.async_get(self.old_helper))
        change = await self._prepare_own_change()
        digest = await preparations.create('own-helper-change', prep.encode_change(change))
        step = tx.Step('own-helper', 'registry', change.identity, change.before_revision,
                       change.after_revision, 'own-helper-change')
        plan, digests = await self._prepare_own_plan(preparations, step, digest, native_ref, receipt, proposal)
        store = base.lm.native_helper_lifecycle_store(hass, preparations)
        await store.initialize()
        activation = importlib.import_module(PACKAGE + '.native_helper_activation_lease')
        self.lease = await activation.NativeHelperActivationLease.acquire(hass, hass.config_entries,
            (self.entry,), guard=self.guard)
        async def original_source(reference, actual_receipt):
            self.assertEqual((reference, actual_receipt), (native_ref, receipt))
            return original == await fixture.baseline.async_capture_native_input_baseline(
                hass, receipt, guard=self.guard)
        async def inactive(entry_id):
            from homeassistant.helpers.entity_platform import async_get_platforms
            self.assertEqual(entry_id, self.entry.entry_id)
            self.assertEqual(self.entry.state.value, 'not_loaded')
            self.assertFalse(any(p.entities for p in async_get_platforms(hass, 'integration')
                                 if p.config_entry is self.entry))
            return True
        async def unused(*args):
            raise AssertionError('setup verifier must be installed before restore')
        self.args = (store, hass.config_entries, 'own-helper-unload', plan, receipt, digests)
        self.kwargs = dict(guard=self.guard, activation_lease=self.lease,
            verify_original_native_inputs=original_source, settle=hass.async_block_till_done,
            verify_inactive=inactive, verify_runtime=unused, verify_accepted_native_chain=unused,
            runtime_baselines=())
        return await base.lm.NativeHelperLifecycle.start(*self.args, **self.kwargs)

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
                'transaction', 'native_helper_setup_effects', 'yaml_transaction')}
            rr, tx, prep = (m[name] for name in ('native_registry_receipts', 'transaction', 'prepared'))
            original = (life.plan, life.receipt, dict(life.binding['preparations']),
                        ())
            self.assertEqual(len(life.plan.steps), 2)
            self.assertEqual(tuple(step.adapter for step in life.plan.steps), ('registry', 'yaml'))
            chain_store = rr.native_registry_receipt_store(hass, life.store.preparations)
            await chain_store.initialize()
            chain = await rr.RegistryReceiptChain.start(chain_store, life.operation_id,
                life.plan, life.receipt, life.binding['preparations'])
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
            effect_errors = []
            actual_yaml_apply = yaml_adapter.apply
            async def diagnosed_yaml_apply(step, guard):
                try:
                    return await actual_yaml_apply(step, guard)
                except Exception as error:
                    # Observe a refused real call; never manufacture its result.
                    import traceback
                    source_drift = []
                    trace = error.__traceback__
                    while trace is not None:
                        if trace.tb_frame.f_code.co_name == 'assert_current':
                            values = trace.tb_frame.f_locals
                            source_drift.append(tuple(values.get(key) for key in
                                ('path', 'observed', 'expected')))
                        trace = trace.tb_next
                    effect_errors.append((type(error).__name__, str(error),
                        [(item.name, item.lineno) for item in
                         traceback.extract_tb(error.__traceback__)], source_drift))
                    raise
            yaml_adapter.apply = diagnosed_yaml_apply
            adapter_diagnostics = []
            def remember_failure(stage, error):
                # Test-only metadata from the REAL refused call; no frame locals,
                # expected/native values, replacement results or acceptance proof.
                try:
                    frames = []
                    trace = error.__traceback__
                    while trace is not None:
                        code = trace.tb_frame.f_code
                        frames.append((code.co_filename, code.co_qualname, trace.tb_lineno))
                        trace = trace.tb_next
                    reason = None
                    if (len(error.args) == 1 and type(error.args[0]) is str
                            and error.args[0].startswith(('own_helper_', 'registry_',
                                'restore_', 'native_', 'input_', 'helper_',
                                'integration_', 'source_', 'accepted_', 'yaml_'))
                            and all(c in 'abcdefghijklmnopqrstuvwxyz0123456789_'
                                    for c in error.args[0])):
                        reason = error.args[0]
                    adapter_diagnostics.append((stage, type(error).__module__ + '.' +
                        type(error).__qualname__, reason, tuple(frames)))
                except BaseException:
                    pass  # Diagnostics must never change the original failure.
            actual_registry_compensate = registry.compensate
            actual_registry_inspect = registry.inspect
            async def diagnosed_registry_compensate(step, guard):
                try:
                    return await actual_registry_compensate(step, guard)
                except BaseException as error:
                    remember_failure('registry.compensate', error)
                    raise
            async def diagnosed_registry_inspect(step):
                try:
                    result = await actual_registry_inspect(step)
                except BaseException as error:
                    remember_failure('registry.inspect', error)
                    raise
                # inspect deliberately translates caught errors to UNKNOWN. Keep
                # that ACTUAL result; never invent the swallowed exception/cause.
                adapter_diagnostics.append(('registry.inspect.return', result.value))
                return result
            def diagnosed_coordinator_guard():
                try:
                    return quiescent()
                except BaseException as error:
                    remember_failure('coordinator.guard', error)
                    raise
            registry.compensate = diagnosed_registry_compensate
            registry.inspect = diagnosed_registry_inspect
            coordinator = tx.Coordinator({'registry': registry, 'yaml': yaml_adapter}, journal,
                diagnosed_coordinator_guard, required_scopes=('registry', 'yaml'))

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
                entity = self.current()
                self.assertEqual(entity._state.as_tuple(), self.expected_counter[0])
                self.assertEqual(entity._last_valid_state.as_tuple(), self.expected_counter[1])
                self.assertEqual(entity._sensor_source_id, self.producer.entity_id)
                self.assertEqual(self.f.entity.async_get(self.producer.entity_id).id, self.source_uuid)
                return True

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
                entity_before = self.current()
                counter_before = (entity_before._state.as_tuple(), entity_before._last_valid_state.as_tuple())
                await life.quiesce()
                slot = life._slot('unload', self.entry.entry_id, 'receipt')
                unload = life.store.read(slot)
                self.assertIn('native_unload', unload['payload'])
                unload_ref = (slot, unload['digest'])
                source_id, target_id = ((self.target, self.old_helper) if reverse else
                                        (self.old_helper, self.target))
                original_state = hass.states.get(source_id)
                self.assertEqual(original_state.state, 'unavailable')
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
                capture = json.loads(escrow._payload.capture_json)
                self.expected_counter = (Decimal(capture['full_state_text']).as_tuple(),
                    Decimal(capture['last_valid_state_text']).as_tuple())
                self.assertEqual(self.expected_counter, counter_before)
                self.assertIs(type(escrow._payload), self.codec.PreparedIntegrationPayload)
                if self.zero:
                    self.assertEqual(Decimal(capture['full_state_text']), 0)
                    self.assertEqual(Decimal(capture['last_valid_state_text']), 0)
                escrow.require_completed(token)
                registry.bind_own_helper_escrow(escrow, token, source_fence=source_fence)
                import os
                from unittest.mock import patch
                actual_yaml_write = m['yaml_transaction'].apply_file_patch
                def pinned_yaml_write(*args, **kwargs):
                    result = actual_yaml_write(*args, **kwargs)
                    # Same mounted-filesystem fixture method as the existing
                    # mixed helper test: retain the ACTUAL direct-return inode,
                    # never replace the stamp with a later observed identity.
                    fd = os.open(self.f.root / result['path'], os.O_RDONLY | os.O_NOFOLLOW)
                    self.addCleanup(os.close, fd)
                    info = os.fstat(fd)
                    self.assertEqual((info.st_dev, info.st_ino, info.st_mode),
                        tuple(result['after_identity']))
                    return result
                with patch.object(m['yaml_transaction'], 'apply_file_patch', pinned_yaml_write):
                    record = (await coordinator.rollback(life.operation_id) if reverse else
                        await coordinator.apply(life.plan, life.plan.fingerprint, life.operation_id))
                self.assertEqual(record.status, 'rolled_back' if reverse else 'applied',
                    (record.status, record.reason, record.phases, effect_errors, adapter_diagnostics))
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
                entity, = [e for platform in async_get_platforms(hass, 'integration')
                    if platform.config_entry is self.entry for e in platform.entities.values()]
                self.assertEqual(entity.entity_id, target_id)
                self.assertEqual(entity.unique_id, self.entry.entry_id)
                hn = importlib.import_module(PACKAGE + '.helper_native')
                self.assertEqual(hn.read_entry(hass, self.entry.entry_id), self.before)
                self.assertEqual(entity._state.as_tuple(), self.expected_counter[0])
                self.assertEqual(entity._last_valid_state.as_tuple(), self.expected_counter[1])
                self.assertEqual(self.f.entity.async_get(target_id).id, self.row['id'])
                await self._assert_script(hass.states.get(target_id).state)
                self.assertTrue(await self.native_history.verify(self.own_history))
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
                    if not self.zero and not self.distinct_last_valid:
                        before_progress = entity._state
                        await self.emit('123456.792')
                        await self.emit('123456.793')
                        self.assertGreater(entity._state, before_progress)

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
            before_progress = entity._state
            await self.emit('1' if self.zero else '123456.794')
            await self.emit('2' if self.zero else '123456.795')
            self.assertGreater(entity._state, before_progress)
            self.assertEqual(entity._state, entity._last_valid_state)
            await self._assert_script(hass.states.get(self.old_helper).state)
            self.assertTrue(await self.native_history.verify(self.own_history))
        finally:
            for active in lives:
                active.close_source_gaps()
            self.lease.release()


class NativeIntegrationOwnZeroLifecycleTests(NativeIntegrationOwnLifecycleTests):
    zero = True


class NativeIntegrationOwnDistinctLifecycleTests(NativeIntegrationOwnLifecycleTests):
    distinct_last_valid = True
