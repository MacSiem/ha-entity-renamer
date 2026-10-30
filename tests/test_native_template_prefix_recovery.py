"""Actual interrupted accepted registry prefix; no replay or replacement input.

Fault injection raises only after the genuine journal ACK of the first native
registry step. Native execution, receipts and prefix observations are unmodified.
"""
import asyncio
from dataclasses import replace
import unittest
from unittest.mock import patch

import test_native_template_yaml_server as server


@unittest.skipUnless(server.activation.fixture.fixtures.HAS_HA, 'actual pinned HA required')
class NativeTemplatePrefixRecoveryTests(unittest.IsolatedAsyncioTestCase):
    with_yaml = True
    asyncSetUp = server.NativeTemplateYAMLServerTests.asyncSetUp
    _before_start = server.NativeTemplateYAMLServerTests._before_start
    _flush_documents = server.NativeTemplateYAMLServerTests._flush_documents
    prepare = server.NativeTemplateYAMLServerTests.prepare
    emit = server.NativeTemplateYAMLServerTests.emit
    _runtime = server.NativeTemplateYAMLServerTests._runtime
    module = server.NativeTemplateYAMLServerTests.module
    data = server.NativeTemplateYAMLServerTests.data
    stimulus = server.NativeTemplateYAMLServerTests.stimulus

    async def test_acknowledged_prefix_fresh_reverse_preserves_original(self):
        await self._prefix_reverse(1)

    async def test_all_registry_acknowledged_before_yaml_fresh_reverse(self):
        await self._prefix_reverse(2)

    async def test_acknowledged_prefix_controller_inspection_and_inverse(self):
        await self._prefix_reverse(1, controller=True)

    async def _prefix_reverse(self, count, *, controller=False):
        runtime, mapping, uuids, original = await self.data(multiple=True)
        hass, root = self.f.hass, self.f.root
        journal_type = self.module('journal').DurableJournal
        actual_save = journal_type.save
        interrupted = []
        async def stop_after_ack(journal, record):
            await actual_save(journal, record)
            if not interrupted and record.direction == 'forward' and record.phases == ('verified',) * count + ('pending',) * (3 - count):
                interrupted.append(record)
                raise asyncio.CancelledError('synthetic interruption after durable prefix ACK')
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        with patch.object(journal_type, 'save', stop_after_ack):
            with self.assertRaises(asyncio.CancelledError):
                await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        self.assertEqual(len(interrupted), 1)
        record = interrupted[0]
        self.assertEqual(await runtime.journal.load(record.operation_id), record)
        self.assertEqual((root / 'template.yaml').read_bytes(), original)
        first, second = sorted(mapping)
        self.assertIsNone(self.f.entity.async_get(first))
        self.assertEqual(self.f.entity.async_get(mapping[first]).id, uuids[first])
        self.assertEqual(self.f.entity.async_get(mapping[second] if count == 2 else second).id, uuids[second])
        self.assertIsNone(self.f.entity.async_get(second if count == 2 else mapping[second]))
        # Real source output after interruption still reaches the same Template
        # consumers through the accepted partial prefix, with original YAML.
        await self.stimulus(17)
        baseline, composition = runtime.preparations.resolve_composition(record.plan.native_inputs)
        original_receipt = baseline.receipt_json
        original_inputs = record.plan.native_inputs
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_forward(hass, mapping, guard=lambda: None, refresh=refresh)
        # A pending next intent is not an acknowledged prefix and cannot be
        # promoted to a writer. This fixture changes only its own journal row.
        unresolved = replace(record, phases=('verified',) * count + ('intent',) + ('pending',) * (2 - count))
        await runtime.journal.save(unresolved)
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_reverse(hass, record.operation_id, record.plan.fingerprint,
                guard=lambda: None, refresh=refresh)
        self.assertEqual(await runtime.journal.load(record.operation_id), unresolved)
        await runtime.journal.save(record)
        edited = original + b'\n# synthetic foreign edit after accepted prefix\n'
        (root / 'template.yaml').write_bytes(edited)
        with self.assertRaises((ValueError, self.module('transaction').Refused)):
            await runtime.native_yaml_reverse(hass, record.operation_id, record.plan.fingerprint,
                guard=lambda: None, refresh=refresh)
        self.assertEqual((root / 'template.yaml').read_bytes(), edited)
        self.assertEqual(await runtime.journal.load(record.operation_id), record)
        (root / 'template.yaml').write_bytes(original)
        with self.assertRaises(self.module('transaction').Refused):
            await runtime.native_yaml_reverse(hass, record.operation_id, '0' * 64,
                guard=lambda: None, refresh=refresh)
        def revoked():
            raise PermissionError('synthetic administrator revoked')
        with self.assertRaises(PermissionError):
            await runtime.native_yaml_reverse(hass, record.operation_id, record.plan.fingerprint,
                guard=revoked, refresh=refresh)
        self.assertEqual(await runtime.journal.load(record.operation_id), record)
        if controller:
            async def status_refresh():
                return None
            status = await runtime.recovery_status(hass, guard=lambda: None, refresh=status_refresh)
            operation = next(row for row in status['operations'] if row['operation_id'] == record.operation_id)
            self.assertEqual(operation['reason'], 'inspection_available')
            self.assertIs(operation['eligible'], False)
            inspected = await runtime.recovery_inspect(hass, record.operation_id, record.plan.fingerprint,
                guard=lambda: None, refresh=refresh)
            self.assertEqual((inspected['state'], inspected['reason'], inspected['eligible']),
                             ('recoverable_prefix', 'acknowledged_prefix', True))
            self.assertEqual(inspected['change_summary']['sources'], [
                {'current_entity_id': mapping[first], 'original_entity_id': first},
                {'current_entity_id': second, 'original_entity_id': second}])
            self.assertEqual(await runtime.journal.load(record.operation_id), record,
                             'inspection must not acknowledge or advance any journal phase')
        previous_journal, previous_preparations = runtime.journal, runtime.preparations
        from test_native_template_mixed_yaml_server import native_diagnostics
        with native_diagnostics(self.module):
            try:
                reverse = runtime.recovery_restore if controller else runtime.native_yaml_reverse
                result = await reverse(hass, record.operation_id, record.plan.fingerprint,
                    guard=lambda: None, refresh=refresh)
            finally:
                final = await runtime.journal.load(record.operation_id)
                print('PREFIX_RECOVERY_RESULT', final.status, final.reason, final.direction, final.phases)
        self.assertEqual(result['state'], 'rolled_back')
        final = await runtime.journal.load(record.operation_id)
        self.assertEqual(final.phases, ('undone',) * count + ('pending',) * (3 - count))
        self.assertIsNot(runtime.journal, previous_journal)
        self.assertIsNot(runtime.preparations, previous_preparations)
        self.assertEqual(final.plan, record.plan)
        reopened, retained = runtime.preparations.resolve_composition(original_inputs)
        self.assertEqual(reopened.receipt_json, original_receipt)
        self.assertEqual(retained, composition)
        self.assertEqual((root / 'template.yaml').read_bytes(), original)
        for old, new in mapping.items():
            self.assertEqual(self.f.entity.async_get(old).id, uuids[old])
            self.assertIsNone(self.f.entity.async_get(new))
        await self.stimulus(23)
        self.assertFalse(await runtime.journal.active())
