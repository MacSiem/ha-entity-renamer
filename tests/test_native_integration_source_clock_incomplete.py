"""Refuse incomplete prepared transport after actual native source removal.

Negative-only cancellation fault: the producer's real async_remove completes,
then cancellation stops its real registry-update task before native re-add.
No fabricated Event/State, pending-move field, receipt or numeric input.
"""
import asyncio
import unittest

import test_native_integration_source_clock as positive


@unittest.skipUnless(positive.fixture.HAS_HA, 'requires actual pinned HA')
class NativeIntegrationSourceClockIncompleteTests(unittest.IsolatedAsyncioTestCase):
    method = 'left'
    saved_uuid = False
    with_yaml = True
    asyncSetUp = positive.NativeIntegrationSourceClockTests.asyncSetUp
    _before_start = positive.NativeIntegrationSourceClockTests._before_start
    _flush_documents = positive.NativeIntegrationSourceClockTests._flush_documents
    current = positive.NativeIntegrationSourceClockTests.current
    guard = positive.NativeIntegrationSourceClockTests.guard
    wait_tick = positive.NativeIntegrationSourceClockTests.wait_tick
    area = positive.NativeIntegrationSourceClockTests.area
    _run_source_clock = positive.NativeIntegrationSourceClockTests._run_source_clock

    async def test_actual_prepared_removal_without_creation_refuses_setup(self):
        self.source_clock_negative_hook = self.hook
        self.source_clock_after_transaction_hook = self.after_transaction
        self.removals = []
        self.checked = False
        self.overridden = False
        try:
            await self._run_source_clock()
            self.assertTrue(self.checked, 'actual transaction result was not checked')
        finally:
            if self.overridden:
                del self.producer.async_remove

    async def hook(self, stage, context):
        if stage == 'before_capture':
            return False
        if stage == 'retained':
            producer = context.producer
            self.assertNotIn('async_remove', vars(producer))
            actual_remove = producer.async_remove

            async def remove_then_cancel(*args, **kwargs):
                # This is only a negative scheduling fault on this producer.
                # Execute its genuine removal, including the real StateMachine
                # emission and all ordinary native cleanup, before cancellation.
                old = context.hass.states.get(producer.entity_id)
                await actual_remove(*args, **kwargs)
                self.assertTrue(kwargs.get('force_remove'))
                self.assertIsNotNone(old)
                self.assertIsNone(context.hass.states.get(self.names[0]))
                self.assertEqual(self.f.entity.async_get(self.names[1]).id,
                                 self.source_uuid)
                self.removals.append(old)
                raise asyncio.CancelledError('negative fault before native source re-add')

            # No native class method or arithmetic is changed. The real native
            # registry task awaits this instance method and stops on cancellation.
            producer.async_remove = remove_then_cancel
            self.overridden = True
            return False
        self.fail('unknown hook stage ' + stage)

    async def after_transaction(self, record, context, bridge, change):
        del self.producer.async_remove
        self.overridden = False
        scope = context.scope
        self.assertEqual(len(self.removals), 1)
        self.assertIsNone(scope._error)
        self.assertEqual(scope._phase, 'retained')
        self.assertIs(scope._pending_move[0], self.removals[0])
        self.assertFalse(scope.source_binding._creation_emitted)
        self.assertEqual(scope.source_binding.transport_calls, 0)
        self.assertFalse(scope.source_binding.flights)
        self.assertFalse(scope._emitted)
        self.assertIsNone(context.hass.states.get(self.names[0]))
        self.assertIsNone(context.hass.states.get(self.names[1]))
        # The real coordinator already refuses completion. Require its durable
        # intent and a fresh, genuine native readback identifying missing runtime;
        # do not manufacture terminal authorization to reach a later seam.
        self.assertEqual(record.status, 'unknown')
        self.assertEqual(record.reason, 'effect_or_verification_uncertain')
        self.assertEqual(record.phases, ('intent', 'pending', 'pending'))
        observed = await bridge.inspect(change)
        self.assertEqual(observed.position, 'unknown')
        self.assertEqual(observed.reason, 'scoped_evidence_incomplete')
        self.assertIs(observed.runtime, False)
        self.assertIsNone(scope.source_binding.authorization)
        with self.assertRaisesRegex(ValueError, 'accepted_transport_complete'):
            await scope.setup()
        self.assertIsNone(scope._new)
        self.assertIsNone(scope._transferred)
        self.assertFalse(scope._new_timer_bound)
        self.assertFalse(hasattr(scope, 'receipt'))
        for row in context.store.rows.values():
            self.assertNotIn(row['payload'].get('kind'),
                             ('integration_clock_receipt', 'helper_setup_receipt'))
        self.checked = True
        return True
