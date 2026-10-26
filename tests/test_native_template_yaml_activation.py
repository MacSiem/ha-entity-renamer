"""Actual product source registry + modern Template YAML + fresh reverse.

Owner executes on both pinned Cores through FIFO. Real Coordinator, immutable
preparations, native registry effects, file calls, check_config, reload and chain
receipts. Only the producer receives ordinary events; no helper output seeds.
"""
import asyncio
import importlib
import os
import sys
import traceback
import unittest
from unittest.mock import patch

import test_native_template_consumers as consumers
import test_receipted_helper_native as lifecycle_fixture

fixture = consumers.fixture


@unittest.skipUnless(fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplateYAMLActivationTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = consumers.NativeTemplateConsumerTests.asyncSetUp
    _before_start = lifecycle_fixture.NativeReceiptedHelperTests._before_start
    _flush_documents = consumers.NativeTemplateConsumerTests._flush_documents
    prepare = consumers.NativeTemplateConsumerTests.prepare
    emit = consumers.NativeTemplateConsumerTests.emit

    def module(self, name):
        return importlib.import_module(fixture.fixtures.PACKAGE + '.' + name)

    async def transaction(self, *, fault=None):
        from homeassistant.core import Context
        from homeassistant.helpers.entity_platform import async_get_platforms
        unknown_errors = []
        await self.prepare('yaml')
        # Reuse the native source-clock fixture's retained descriptor method.
        # VirtioFS can recycle a closed replacement inode between two reads.
        mutator = self.module('yaml_transaction')
        actual_write = mutator.apply_file_patch
        def retain_actual_replacement(*args, **kwargs):
            result = actual_write(*args, **kwargs)
            fd = os.open(self.f.root / result['path'], os.O_RDONLY | os.O_NOFOLLOW)
            self.addCleanup(os.close, fd)
            info = os.fstat(fd)
            self.assertEqual((info.st_dev, info.st_ino, info.st_mode),
                             tuple(result['after_identity']))
            return result
        self.enterContext(patch.object(mutator, 'apply_file_patch', retain_actual_replacement))
        hass, receipt = self.f.hass, self.receipt
        self.assertEqual(receipt.changed_domains, ('template',))
        self.assertTrue(receipt.ready_for_apply, receipt.unsupported_domains)
        self.assertIsNotNone(self.evidence.template_candidate_sources)
        original = await self.module('native_input_baseline').async_capture_native_input_baseline(
            hass, receipt, guard=lambda: None)
        prep, tx = self.module('prepared'), self.module('transaction')
        preparations = prep.native_prepared_store(hass)
        await preparations.initialize()
        inputs = await preparations.create_native_inputs('template-yaml-inputs', original)
        recorder = self.module('recorder_guard').NativeRecorderGuard(hass)
        history = self.module('history_guard').NativeHistoryGuard(recorder)
        helper_history = await history.capture({self.consumer.entity_id: self.consumer.entity_id})
        nr = self.module('native_registry')
        source_uuid = self.f.entity.async_get(self.source_id).id
        io = nr.NativeRegistryIO(hass)
        bridge = nr.NativeRegistryBridge(io, recorder)
        source = await bridge.prepare('entity', source_uuid,
                                      {'entity_id': 'sensor.accepted_next'}, lambda: None)
        change = await fixture.fixtures.validation._settled_executor(
            hass, self.module('yaml_transaction')._capture, receipt, self.plan)
        changes, scopes, digests, steps = (source, change), ('registry', 'yaml'), {}, []
        for index, (item, scope) in enumerate(zip(changes, scopes)):
            ref = 'template-yaml-' + str(index)
            digests[ref] = await preparations.create(ref, prep.encode_change(item))
            steps.append(tx.Step(ref, scope, item.identity, item.before_revision,
                                 item.after_revision, ref))
        plan = tx.Plan('template-yaml-apply', scopes, scopes, tuple(steps),
                       'exact-modern-yaml-state-sensors', scopes, inputs)
        journal = self.module('journal').native_journal(hass)
        await journal.initialize()
        rr = self.module('native_registry_receipts')
        store = rr.native_registry_receipt_store(hass, preparations)
        await store.initialize()
        operation = 'template-yaml-operation'
        chain = await rr.RegistryReceiptChain.start(store, operation, plan, receipt, digests)
        bridge = nr.NativeRegistryBridge(io, recorder, native_images=chain.accepted_documents)
        observation_state = chain.reconstruct_inputs()
        async def observe(input_state=None):
            nonlocal observation_state
            if input_state is None:
                try:
                    observation_state = chain.reconstruct_inputs()
                except rr.RegistryReceiptError:
                    self.assertIsNotNone(chain.active)
                    self.assertEqual(store.read(chain.active.reference[0])['payload']['kind'],
                                     'registry_intent')
                input_state = observation_state
            return await self.module('native_input_observation').async_observe_native_inputs(
                hass, receipt, guard=lambda: None, input_state=input_state)
        locks = self.module('yaml_editor_locks').NativeYAMLEditorLocks(hass)
        self.assertGreaterEqual(len(locks.enumerate()), 3)
        proofs = []
        fault_observations = []
        def current_consumer():
            return next(entity for platform in async_get_platforms(hass, 'template')
                        for entity in platform.entities.values() if entity.unique_id == self.consumer_key)
        def detach_actual_consumer():
            current_consumer()._template_result_info._track_state_changes.async_remove()
            fault_observations.append('actual_detach')
        accepted_observer = self.module('native_template_accepted_consumers')
        def compose(log):
            # Fresh runtime observer/adapters for reverse, bound to SAME original
            # writer and immutable preparations; no replacement baseline/receipt.
            observer = self.module('native_template_yaml').NativeTemplateYAMLRuntimeReadback(
                hass, chain, self.evidence, guard=lambda: None)
            async def common(rollback):
                wanted = tx.Position.BEFORE if rollback else tx.Position.AFTER
                state = chain.reconstruct_inputs()
                state.assert_current(receipt)
                self.assertTrue(await history.verify(helper_history))
                if (await bridge.inspect(source)).position != wanted.value:
                    return False
                if await observer.position_at_inputs(change, state) is not wanted:
                    return False
                actual = await accepted_observer.async_observe_accepted_template_consumers(
                    hass, chain, self.evidence, guard=lambda: None)
                if actual.yaml_position != ('original' if rollback else 'proposed'):
                    return False
                entities = [row for row in actual.rows if row.kind == 'entity']
                return (len(entities) == 1 and entities[0].consumer_verified
                        and entities[0].source_verified
                        and entities[0].registry_id == self.consumer_uuid
                        and actual.evidence_sha256 == self.evidence.sha256)
            async def full(rollback):
                if not await common(rollback):
                    return False
                async with locks.hold(lambda: None):
                    proof = await self.module('native_candidate_validation').async_revalidate_accepted_candidate(
                        hass, chain, log.load, self.f.root.parent, lambda: None)
                    self.assertEqual(proof.fingerprint, plan.fingerprint)
                    self.assertEqual(proof.direction, 'rollback' if rollback else 'forward')
                    self.assertEqual(proof.head, chain.reconstruct_inputs().head)
                    proofs.append(proof)
                return True
            resolver = lambda ref: prep.decode_change(preparations.resolve(ref, digests[ref]))
            registry = self.module('native_registry_transaction').ReceiptedRegistryTransactionAdapter(
                bridge, resolver, chain, observe, log.load, lambda: None,
                full_verify=lambda _: common(False), full_verify_rollback=lambda _: common(True))
            yaml = self.module('native_yaml_transaction').ReceiptedYAMLTransactionAdapter(
                hass, resolver, locks, observer, chain, observe, log.load, lambda: None,
                context=Context(), full_verify=lambda _: full(False),
                full_verify_rollback=lambda _: full(True))
            if fault == 'adapter_detach':
                real_position, real_read = yaml._runtime_position, yaml._read
                final_getter = []
                async def remember_true_getter(change, state):
                    value = await real_position(change, state)
                    record = await log.load(operation)
                    if (record is not None and record.phases == ('verified', 'verified')):
                        self.assertIs(value, tx.Position.AFTER)
                        final_getter.append(value)
                    return value
                async def detach_after_actual_adapter_read(*args):
                    value = await real_read(*args)
                    # This is AFTER the actual adapter read/checkpoint, after
                    # its genuine final getter. No positive result is changed.
                    if final_getter and not fault_observations:
                        detach_actual_consumer()
                    return value
                yaml._runtime_position = remember_true_getter
                yaml._read = detach_after_actual_adapter_read
            if fault == 'terminal_detach':
                real_save = log.save
                async def detach_after_actual_save(record):
                    value = await real_save(record)
                    if record.status == 'applied' and not fault_observations:
                        detach_actual_consumer()
                    return value
                log.save = detach_after_actual_save
            coordinator = self.module('native_template_yaml').compose_template_yaml_coordinator(
                {'registry': registry, 'yaml': yaml}, log, lambda: None,
                required_scopes=scopes)
            actual_unknown = coordinator._unknown
            async def remember_actual_exception(record, reason):
                # Observe the real caught exception; preserve its native result.
                if (error := sys.exception()) is not None:
                    unknown_errors.append(''.join(traceback.format_exception(error)))
                return await actual_unknown(record, reason)
            coordinator._unknown = remember_actual_exception
            return coordinator, observer
        coordinator, observer = compose(journal)
        self.assertIs(await observer.position_at_inputs(change, chain.reconstruct_inputs()),
                      tx.Position.BEFORE)
        if fault == 'cancel_reload':
            native = importlib.import_module('homeassistant.components.template')
            monitor = sys.monitoring
            key = next((key for key in range(6) if monitor.get_tool(key) is None), None)
            self.assertIsNotNone(key)
            captured = {'tasks': (), 'locks_held': False}
            running = None
            def native_returned(code, offset, result):
                frame = sys._getframe(1)
                if (captured['tasks'] or running is None
                        or frame.f_locals.get('hass') is not hass):
                    return
                actual_tasks = self.module('native_template_yaml')._pending_template_tasks(hass)
                if actual_tasks:
                    captured['tasks'] = tuple(actual_tasks)
                    captured['locks_held'] = all(row[3].locked() for row in locks.enumerate())
                    running.cancel()
            monitor.use_tool_id(key, 'renamer_template_cancel_regression')
            monitor.register_callback(key, monitor.events.PY_RETURN, native_returned)
            monitor.set_local_events(key, native._process_config.__code__, monitor.events.PY_RETURN)
            try:
                running = asyncio.create_task(coordinator.apply(plan, plan.fingerprint, operation))
                with self.assertRaises(asyncio.CancelledError):
                    await running
                self.assertTrue(captured['tasks'], 'must observe REAL native reload descendants')
                self.assertTrue(captured['locks_held'])
                # Assert BEFORE emergency cleanup: no orphaned own descendant
                # can outlive lock release/cancellation propagation.
                self.assertTrue(all(task.done() for task in captured['tasks']))
                self.assertFalse(self.module('native_template_yaml')._pending_template_tasks(hass))
                self.assertTrue(all(not row[3].locked() for row in locks.enumerate()))
                self.assertNotEqual((await journal.load(operation)).status, 'applied')
                with self.assertRaises(ValueError):
                    chain.reconstruct_inputs()  # actual pending effect is UNKNOWN
            finally:
                monitor.set_local_events(key, native._process_config.__code__, 0)
                monitor.register_callback(key, monitor.events.PY_RETURN, None)
                monitor.free_tool_id(key)
                if running is not None and not running.done():
                    await asyncio.gather(running, return_exceptions=True)
                if captured['tasks']:
                    await asyncio.gather(*captured['tasks'], return_exceptions=True)
            return
        if fault in ('schema', 'schema_compiled'):
            registered = hass.services._services['template']['reload']
            original_schema = registered.schema
            invoked = []
            if fault == 'schema_compiled':
                # Ordinary native reload builds a genuinely lazy current default
                # if necessary. Authenticate first; never call an unverified
                # candidate validator to manufacture a positive proof.
                observer.assert_reload_current()
                if observer.native.version == '2026.10.2' and not original_schema._built:
                    await hass.services.async_call('template','reload',{},
                                                   blocking=True,context=Context())
                    await observer.settle_reload()
                    self.assertTrue(original_schema._built)
                    observer.assert_reload_current()
                original_compiled = original_schema._compiled
                from homeassistant.helpers import service as native_service
                self.assertIs(original_schema,
                              native_service.async_register_admin_service.__defaults__[0])
            def foreign_schema(data):
                invoked.append(True)
                raise AssertionError('foreign schema must not execute')
            def foreign_compiled(*args):
                invoked.append(args)
                raise AssertionError('foreign compiled validator must not execute')
            if fault == 'schema_compiled':
                original_schema._compiled = foreign_compiled  # Negative-only fault.
                self.assertIs(registered.schema,original_schema)
            else:
                registered.schema = foreign_schema
            try:
                with self.assertRaises(ValueError):
                    observer.assert_reload_current()
                try:
                    result = await coordinator.apply(plan, plan.fingerprint, operation)
                except (tx.Refused, ValueError):
                    result = None
                self.assertTrue(result is None or result.status != 'applied')
                self.assertEqual(invoked, [])
                self.assertEqual((self.f.root / change.files[0].path).read_bytes(), change.files[0].before)
            finally:
                if fault == 'schema_compiled':
                    original_schema._compiled = original_compiled
                    self.assertIs(registered.schema,original_schema)
                else:
                    registered.schema = original_schema
            return
        if fault in ('adapter_detach', 'terminal_detach'):
            try:
                result = await coordinator.apply(plan, plan.fingerprint, operation)
            except (tx.Refused, ValueError):
                result = None
            self.assertEqual(fault_observations, ['actual_detach'])
            self.assertTrue(result is None or result.status != 'applied',
                            'stale genuine runtime proof must not confirm applied')
            return
        result = await coordinator.apply(plan, plan.fingerprint, operation)
        self.assertEqual(result.status, 'applied', (result.reason, unknown_errors))
        self.assertEqual(result.phases, ('verified', 'verified'))
        self.assertEqual(self.producer.entity_id, 'sensor.accepted_next')
        self.assertEqual(self.f.entity.async_get(self.producer.entity_id).id, source_uuid)
        self.assertEqual(chain.original_receipt, receipt)
        self.assertEqual(chain.baseline, original)
        self.assertEqual(chain.baseline.receipt_json, self.evidence.receipt_json)
        self.consumer = next(entity for platform in async_get_platforms(hass, 'template')
            for entity in platform.entities.values() if entity.unique_id == self.consumer_key)
        for value in (5, 7):
            await self.emit(value)
        observed = await accepted_observer.async_observe_accepted_template_consumers(
            hass, chain, self.evidence, guard=lambda: None)
        entity = next(row for row in observed.rows if row.kind == 'entity')
        self.assertEqual(entity.dependencies, ('sensor.accepted_next',))
        self.assertEqual(entity.registry_id, self.consumer_uuid)
        self.assertTrue(entity.source_verified)
        self.assertFalse(observed.family_complete)
        # Reload the durable journal and independently reopen the full chain for
        # read-only accepted-head proof before composing a fresh reverse writer.
        fresh_journal = self.module('journal').native_journal(hass)
        await fresh_journal.initialize()
        fresh_preparations = prep.native_prepared_store(hass)
        await fresh_preparations.initialize()
        fresh_store = rr.native_registry_receipt_store(hass, fresh_preparations)
        await fresh_store.initialize()
        reopened = rr.RegistryReceiptChain.reopen(fresh_store, operation, plan, receipt, digests)
        self.assertTrue(reopened.reconstruct_inputs().same_evidence(chain.reconstruct_inputs()))
        original_head = original.digest
        reverse, fresh_observer = compose(fresh_journal)
        self.assertIsNot(fresh_observer, observer)
        self.assertIs(await fresh_observer.position_at_inputs(change, chain.reconstruct_inputs()),
                      tx.Position.AFTER)
        rolled = await reverse.rollback(operation)
        self.assertEqual(rolled.status, 'rolled_back', (rolled.reason, unknown_errors))
        self.assertEqual(rolled.phases, ('undone', 'undone'))
        self.assertEqual(self.producer.entity_id, self.source_id)
        self.assertEqual(self.f.entity.async_get(self.source_id).id, source_uuid)
        self.assertEqual(chain.baseline.digest, original_head)
        self.assertEqual(chain.original_receipt, receipt)
        self.consumer = next(entity for platform in async_get_platforms(hass, 'template')
            for entity in platform.entities.values() if entity.unique_id == self.consumer_key)
        for value in (5, 7):
            await self.emit(value)
        final = await accepted_observer.async_observe_accepted_template_consumers(
            hass, chain, self.evidence, guard=lambda: None)
        entity = next(row for row in final.rows if row.kind == 'entity')
        self.assertEqual(entity.dependencies, (self.source_id,))
        self.assertEqual(entity.registry_id, self.consumer_uuid)
        self.assertTrue(entity.source_verified)
        self.assertEqual(final.yaml_position, 'original')
        self.assertEqual([proof.direction for proof in proofs], ['forward', 'rollback'])
        return chain, change, fresh_observer

    async def test_actual_product_forward_and_fresh_reverse_preserve_source_and_uuid(self):
        await self.transaction()

    async def test_other_native_platform_keeps_unsupported_and_all_source_rows(self):
        await self.prepare('yaml', sibling=True)
        self.assertIn('template', self.receipt.unsupported_domains)
        self.assertFalse(self.receipt.ready_for_apply)
        self.assertTrue(any(row.domain == 'template.binary_sensor'
                            for row in self.evidence.template_sources.ledger.definitions))

    async def test_actual_detach_after_product_reverse_refuses_runtime_position(self):
        chain, change, observer = await self.transaction()
        self.consumer._template_result_info._track_state_changes.async_remove()
        self.assertIs(await observer.position_at_inputs(change, chain.reconstruct_inputs()),
                      self.module('transaction').Position.UNKNOWN)

    async def test_time_expression_keeps_unsupported_and_source_inventory(self):
        await self.prepare('yaml')
        path = self.f.root / 'template.yaml'
        path.write_text(path.read_text().replace('* 2', '* 2 + now().hour * 0'))
        plan = self.module('yaml_plan').plan_yaml(self.f.root,
            {self.source_id: 'sensor.accepted_next'})
        self.assertTrue(plan.ready, plan.issues)
        receipt, evidence = await fixture.fixtures.validation.async_validate_proposed_with_sources(
            self.f.hass, plan, self.f.root.parent)
        self.assertIn('template', receipt.unsupported_domains)
        self.assertFalse(receipt.ready_for_apply)
        self.assertTrue(evidence.template_sources.source_matched)
        self.assertTrue(any(row.domain == 'template.sensor'
                            for row in evidence.template_sources.ledger.definitions))

    async def test_actual_detach_after_true_getter_and_read_refuses_final_verify(self):
        await self.transaction(fault='adapter_detach')

    async def test_actual_detach_after_terminal_journal_write_refuses_confirmation(self):
        await self.transaction(fault='terminal_detach')

    async def test_actual_pending_reload_descendants_settle_before_cancel_unlock(self):
        await self.transaction(fault='cancel_reload')

    async def test_actual_service_schema_drift_refuses_before_foreign_callable(self):
        await self.transaction(fault='schema')

    async def test_same_native_schema_compiled_drift_refuses_before_write_or_callable(self):
        await self.transaction(fault='schema_compiled')
