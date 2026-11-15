"""Real Powercalc entries and SAME-chain lock acquisition; no migration claim."""
import unittest
import test_powercalc_numeric_continuity_native as numeric


@unittest.skipUnless(numeric.accepted.fixture.HAS_HA, 'actual supported HA required')
class PowercalcLeaseNativeTests(unittest.IsolatedAsyncioTestCase):
    namespace_source_bytes = numeric.PowercalcNumericContinuityNativeTests.namespace_source_bytes
    asyncSetUp = numeric.PowercalcNumericContinuityNativeTests.asyncSetUp
    _setup_real_vendor = numeric.PowercalcNumericContinuityNativeTests._setup_real_vendor
    _flush_documents = numeric.PowercalcNumericContinuityNativeTests._flush_documents
    guard = numeric.PowercalcNumericContinuityNativeTests.guard
    module = numeric.PowercalcNumericContinuityNativeTests.module
    prepare_candidate = numeric.PowercalcNumericContinuityNativeTests.prepare_candidate
    chain_root = numeric.PowercalcNumericContinuityNativeTests.chain_root
    ready = numeric.PowercalcNumericContinuityNativeTests.ready

    async def test_original_entry_locks_are_complete_and_released(self):
        await self.ready()
        module = self.module('powercalc_activation_lease')
        entries = tuple(sorted(self.f.hass.config_entries.async_entries('powercalc'), key=lambda e: e.entry_id))
        self.assertTrue(entries)
        self.assertTrue(all(not entry.setup_lock.locked() for entry in entries))
        revoked = False
        def guard():
            self.guard()
            if revoked:
                raise ValueError('revoked')
        lease = await module.PowercalcActivationLease.acquire(
            self.f.hass, self.writer, self.evidence, self.original_runtime, guard=guard)
        try:
            self.assertEqual(lease.entries, entries)
            self.assertTrue(all(entry.setup_lock.locked() for entry in entries))
            lease.check()
            self.assertEqual(lease.observed.head, self.writer.reconstruct_inputs().head)
        finally:
            revoked = True
            with self.assertRaisesRegex(ValueError, 'revoked'):
                lease.release()
        revoked = False
        self.assertTrue(all(not entry.setup_lock.locked() for entry in entries))
        with self.assertRaises(module.PowercalcLeaseError):
            lease.check()
        self.assertTrue(all(entry.state.value == 'loaded' for entry in entries))
        # A native setup lock already held by another operation must not be
        # jumped or cause acquisition of any other entry in the closure.
        await entries[0].setup_lock.acquire()
        try:
            with self.assertRaises(module.PowercalcLeaseError):
                await module.PowercalcActivationLease.acquire(
                    self.f.hass, self.writer, self.evidence, self.original_runtime, guard=guard)
            self.assertTrue(entries[0].setup_lock.locked())
            self.assertTrue(all(not entry.setup_lock.locked() for entry in entries[1:]))
        finally:
            entries[0].setup_lock.release()
