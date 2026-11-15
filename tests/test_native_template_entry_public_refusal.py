"""Public ordinary admission of a live Template ConfigEntry dependency."""
import unittest
import json
import test_native_template_forward as forward


@unittest.skipUnless(forward.fixtures.HAS_HA, 'actual pinned HA required')
class PublicEntryRefusalTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = forward.NativeTemplateForwardTests.asyncSetUp
    _before_start = forward.NativeTemplateForwardTests._before_start
    _flush_documents = forward.NativeTemplateForwardTests._flush_documents
    _same_native_documents = forward.NativeTemplateForwardTests._same_native_documents
    emit = forward.NativeTemplateForwardTests.emit
    _fixture = forward.NativeTemplateForwardTests._fixture
    _runtime = forward.NativeTemplateForwardTests._runtime
    async def test_public_entry_review_apply_inspect_and_fresh_inverse(self):
        self.maxDiff = None
        module, entry, mapping, identity, original, before = await self._fixture()
        runtime = await self._runtime(module)
        async def refresh():
            return None
        review = await runtime.native_yaml_prepare(self.f.hass, mapping, actor_id='qa',
                                                   guard=lambda: None, refresh=refresh)
        self.assertEqual(review['entity_count'], 1)
        self.assertEqual(review['dependencies']['template_consumer_count'], 1)
        operation, fingerprint = review['operation_id'], review['fingerprint']
        self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)
        await self.emit(8)
        self.assertEqual(self.f.hass.states.get(identity[0]).state, '16')
        applied = await runtime.native_yaml_apply(self.f.hass, operation, fingerprint,
                                                  actor_id='qa', guard=lambda: None, refresh=refresh)
        self.assertEqual(applied['state'], 'applied')
        await self.emit(9)
        self.assertEqual(self.f.hass.states.get(identity[0]).state, '18')
        inspected = await runtime.recovery_inspect(self.f.hass, operation, fingerprint,
                                                   guard=lambda: None, refresh=refresh)
        self.assertEqual(inspected['state'], 'applied')
        self.assertTrue(inspected['restore_attempt_available'])
        self.assertEqual(inspected['frozen_review']['dependencies'], review['dependencies'])
        restored = await runtime.recovery_restore(self.f.hass, operation, fingerprint,
                                                  guard=lambda: None, refresh=refresh)
        self.assertEqual(restored['state'], 'rolled_back')
        fresh = await runtime.recovery_inspect(self.f.hass, operation, fingerprint,
                                               guard=lambda: None, refresh=refresh)
        if fresh['state'] != 'rolled_back':
            async with runtime.operation_lock:
                lease = await module('native_helper_activation_lease').NativeHelperActivationLease.acquire(
                    self.f.hass, self.f.hass.config_entries, (entry,), guard=lambda: None)
                try:
                    await module('native_template_reverse').NativeTemplateReverse.verify_restored(
                        self.f.hass, operation, fingerprint, activation_lease=lease,
                        guard=lambda: None, refresh=refresh)
                finally:
                    lease.release()
        self.assertEqual(fresh['state'], 'rolled_back')
        self.assertEqual(fresh['frozen_review']['dependencies'], review['dependencies'])
        repeated = await runtime.recovery_restore(self.f.hass, operation, fingerprint,
                                                  guard=lambda: None, refresh=refresh)
        self.assertEqual(repeated['state'], 'refused')
        self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)
        after = await self._flush_documents()
        self.assertEqual(json.loads(before['core.device_registry']), json.loads(after['core.device_registry']))
        entry_before, entry_after = (json.loads(data['core.config_entries']) for data in (before, after))
        for doc in (entry_before, entry_after):
            changed = next(row for row in doc['data']['entries'] if row['entry_id'] == entry.entry_id)
            changed.pop('modified_at')  # Native options updates advance this field.
        self.assertEqual(entry_before, entry_after)
        earlier, later = (json.loads(data['core.entity_registry']) for data in (before, after))
        source_uuid = self.f.entity.async_get(next(iter(mapping))).id
        for doc in (earlier, later):
            rows = doc['data']['entities']
            source = next(row for row in rows if row['id'] == source_uuid)
            source.pop('modified_at')  # Native forward and reverse update this field.
            rows.sort(key=lambda row: row['id'])
        self.assertEqual(earlier, later)
        self.assertEqual(self.f.entity.async_get(identity[0]).id, identity[1])
        self.assertIsNotNone(self.f.entity.async_get(next(iter(mapping))))
        self.assertIsNone(self.f.entity.async_get(next(iter(mapping.values()))))
        await self.emit(11)
        self.assertEqual(self.f.hass.states.get(identity[0]).state, '22')

    async def test_public_named_uid_entry_round_trip(self):
        self.entry_unique_id = 'named-template-entry-qa'
        await self.test_public_entry_review_apply_inspect_and_fresh_inverse()

    async def _aba_during_native_setup(self, inverse):
        from homeassistant.core import callback
        from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED
        from homeassistant.helpers.dispatcher import async_dispatcher_connect
        from homeassistant.helpers.event import async_track_state_change_event
        module, entry, mapping, identity, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        async def refresh():
            return None
        review = await runtime.native_yaml_prepare(self.f.hass, mapping, actor_id='qa',
                                                   guard=lambda: None, refresh=refresh)
        op, fingerprint = review['operation_id'], review['fingerprint']
        if inverse:
            result = await runtime.native_yaml_apply(self.f.hass, op, fingerprint, actor_id='qa',
                                                      guard=lambda: None, refresh=refresh)
            self.assertEqual(result['state'], 'applied')
        updates, events = [], []
        attempted = False
        @callback
        def foreign_update(change, actual):
            nonlocal attempted
            if actual is entry and entry.state.value == 'setup_in_progress' and not attempted:
                attempted = True
                a = dict(entry.options)
                b = dict(a)
                b['state'] = b['state'].replace('* 2', '* 3')
                self.assertNotEqual(a, b)
                updates.append(self.f.hass.config_entries.async_update_entry(entry, options=b))
                updates.append(self.f.hass.config_entries.async_update_entry(entry, options=a))
        remove = async_dispatcher_connect(self.f.hass, SIGNAL_CONFIG_ENTRY_CHANGED, foreign_update)
        unsub = async_track_state_change_event(self.f.hass, [identity[0]], events.append)
        try:
            if inverse:
                outcome = await runtime.recovery_restore(self.f.hass, op, fingerprint,
                                                           guard=lambda: None, refresh=refresh)
                self.assertEqual(outcome['state'], 'UNKNOWN')
            else:
                with self.assertRaises((ValueError, module('transaction').Refused)):
                    await runtime.native_yaml_apply(self.f.hass, op, fingerprint, actor_id='qa',
                                                    guard=lambda: None, refresh=refresh)
        finally:
            remove()
        self.assertEqual(updates, [True, True])
        self.assertTrue(attempted)
        self.assertEqual(entry.state.value, 'loaded')
        self.assertIn('* 2', entry.options['state'])
        # Real independent producer write after the native setup effect, without
        # retrying an uncertain operation or replacing a listener.
        event_count = len(events)
        await self.emit(13)
        self.assertEqual(self.f.hass.states.get(identity[0]).state, '26')
        import asyncio
        async with asyncio.timeout(5):
            while len(events) <= event_count:
                await asyncio.sleep(0.01)  # Minimum Core dispatches the real event later.
        self.assertGreater(len(events), event_count)
        self.assertEqual(events[-1].data['new_state'].state, '26')
        unsub()
        inspected = await runtime.recovery_inspect(self.f.hass, op, fingerprint,
                                                   guard=lambda: None, refresh=refresh)
        self.assertEqual(inspected['state'], 'UNKNOWN')
        record = await runtime.journal.load(op)
        self.assertIsNotNone(record)
        preparations = module('prepared').native_prepared_store(self.f.hass)
        await preparations.initialize()
        receipts = module('native_registry_receipts').native_registry_receipt_store(self.f.hass, preparations)
        await receipts.initialize()
        kinds = [row['payload']['kind'] for row in receipts.rows.values()]
        self.assertEqual(kinds.count('helper_setup_receipt'), 1 if inverse else 0)
        self.assertGreater(kinds.count('helper_setup_intent'), kinds.count('helper_setup_receipt'))
        self.assertEqual(self.f.entity.async_get(identity[0]).id, identity[1])
        if inverse:
            self.assertTrue(record.restore_attempted)
            repeated = await runtime.recovery_restore(self.f.hass, op, fingerprint,
                                                      guard=lambda: None, refresh=refresh)
            self.assertNotEqual(repeated['state'], 'rolled_back')

    async def test_real_aba_during_forward_setup_stays_unknown(self):
        await self._aba_during_native_setup(False)

    async def test_real_aba_during_inverse_setup_stays_unknown(self):
        await self._aba_during_native_setup(True)

    async def test_native_listener_replacement_during_review_is_refused(self):
        module, entry, mapping, identity, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        async def refresh():
            return None
        review = await runtime.native_yaml_prepare(self.f.hass, mapping, actor_id='qa',
                                                   guard=lambda: None, refresh=refresh)
        tracker = self.consumer._template_result_info._track_state_changes
        keys = set(tracker._last_track_states.entities)
        old_remove = next(iter(tracker._listeners.values()))
        tracker.async_remove()
        tracker._setup_entities_listener(set(), keys)
        self.assertIsNot(next(iter(tracker._listeners.values())), old_remove)
        # Both listener detach and reattach are the real native methods.
        try:
            result = await runtime.native_yaml_apply(self.f.hass, review['operation_id'],
                review['fingerprint'], actor_id='qa', guard=lambda: None, refresh=refresh)
        except module('transaction').Refused:
            result = None
        if result is not None and result['state'] == 'applied':
            restored = await runtime.recovery_restore(self.f.hass, review['operation_id'],
                review['fingerprint'], guard=lambda: None, refresh=refresh)
            self.assertEqual(restored['state'], 'rolled_back')
        self.assertIsNone(result, 'replacement listener must invalidate review before Record')
        self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)
        await self.emit(12)
        self.assertEqual(self.f.hass.states.get(identity[0]).state, '24')

    async def test_replaced_entry_setup_lock_during_review_is_refused(self):
        import asyncio
        module, entry, mapping, identity, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        async def refresh():
            return None
        review = await runtime.native_yaml_prepare(self.f.hass, mapping, actor_id='qa',
                                                   guard=lambda: None, refresh=refresh)
        old_lock = entry.setup_lock
        entry.setup_lock = asyncio.Lock()
        try:
            try:
                result = await runtime.native_yaml_apply(self.f.hass, review['operation_id'],
                    review['fingerprint'], actor_id='qa', guard=lambda: None, refresh=refresh)
            except module('transaction').Refused:
                result = None
            if result is not None and result['state'] == 'applied':
                restored = await runtime.recovery_restore(self.f.hass, review['operation_id'],
                    review['fingerprint'], guard=lambda: None, refresh=refresh)
                self.assertEqual(restored['state'], 'rolled_back')
            self.assertIsNone(result, 'replacement setup lock must invalidate review')
        finally:
            entry.setup_lock = old_lock
        self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)

    async def test_replaced_native_output_job_during_review_is_refused(self):
        from homeassistant.core import HassJob
        module, entry, mapping, identity, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        async def refresh():
            return None
        review = await runtime.native_yaml_prepare(self.f.hass, mapping, actor_id='qa',
                                                   guard=lambda: None, refresh=refresh)
        tracker = self.consumer._template_result_info
        original_job = tracker._job
        tracker._job = HassJob(original_job.target)
        try:
            try:
                result = await runtime.native_yaml_apply(self.f.hass, review['operation_id'],
                    review['fingerprint'], actor_id='qa', guard=lambda: None, refresh=refresh)
            except module('transaction').Refused:
                result = None
            if result is not None and result['state'] == 'applied':
                restored = await runtime.recovery_restore(self.f.hass, review['operation_id'],
                    review['fingerprint'], guard=lambda: None, refresh=refresh)
                self.assertEqual(restored['state'], 'rolled_back')
            self.assertIsNone(result, 'replacement output job must invalidate review')
        finally:
            tracker._job = original_job
        self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)

    async def test_restored_inspection_detects_fresh_foreign_store_append(self):
        module, entry, mapping, identity, original, _ = await self._fixture()
        runtime = await self._runtime(module)
        async def refresh():
            return None
        review = await runtime.native_yaml_prepare(self.f.hass, mapping, actor_id='qa',
                                                   guard=lambda: None, refresh=refresh)
        op, fingerprint = review['operation_id'], review['fingerprint']
        applied = await runtime.native_yaml_apply(self.f.hass, op, fingerprint, actor_id='qa',
                                                  guard=lambda: None, refresh=refresh)
        self.assertEqual(applied['state'], 'applied')
        restored = await runtime.recovery_restore(self.f.hass, op, fingerprint,
                                                  guard=lambda: None, refresh=refresh)
        self.assertEqual(restored['state'], 'rolled_back')
        count, changed = 0, False
        async def foreign_refresh():
            nonlocal count, changed
            count += 1
            if count == 2:
                store = module('prepared').native_prepared_store(self.f.hass)
                await store.initialize()
                await store.create('qa-foreign-restored', {'kind': 'foreign-qa', 'value': 1})
                changed = True
        inspected = await runtime.recovery_inspect(self.f.hass, op, fingerprint,
                                                   guard=lambda: None, refresh=foreign_refresh)
        self.assertTrue(changed)
        self.assertEqual(inspected['state'], 'UNKNOWN')
        store = module('prepared').native_prepared_store(self.f.hass)
        await store.initialize()
        self.assertIn('qa-foreign-restored', store.plans)
        self.assertEqual(store.resolve_composition((await runtime.journal.load(op)).plan.native_inputs)[0].digest,
                         (await runtime.journal.load(op)).plan.native_inputs[2])
        self.assertEqual(module('helper_native').read_entry(self.f.hass, entry.entry_id), original)
        self.assertEqual(self.f.entity.async_get(identity[0]).id, identity[1])
        await self.emit(13)
        self.assertEqual(self.f.hass.states.get(identity[0]).state, '26')
