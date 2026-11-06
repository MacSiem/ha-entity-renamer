"""Regression sources only; execute through owner's FIFO unit-test receipt."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.helper_native import read_entry
from custom_components.ha_entity_renamer.helper_transaction import (
    HelperTransactionAdapter, prepare_helper)
from custom_components.ha_entity_renamer.native_registry import PreparedRegistryChange
from custom_components.ha_entity_renamer.prepared import encode_change, decode_change
from custom_components.ha_entity_renamer.transaction import Coordinator, Plan, Position, Refused, Step


class MemoryJournal:
    def __init__(self): self.records = {}
    async def load(self, key): return self.records.get(key)
    async def active(self):
        return tuple(r for r in self.records.values()
                     if r.status not in ('applied', 'rolled_back'))
    async def create(self, record):
        if record.operation_id in self.records: raise ValueError('duplicate')
        self.records[record.operation_id] = record
    async def save(self, record): self.records[record.operation_id] = record


MAP = {'sensor.before': 'sensor.after'}


def snapshot(domain='powercalc'):
    return {'domain': domain, 'identity': {'entry_id': 'helper', 'unique_id': 'fixed',
        'title': 'Display', 'version': 1, 'minor_version': 1, 'source': 'user', 'disabled_by': None,
            'pref_disable_new_entities': False, 'pref_disable_polling': False},
        'data': {'entity_id': 'sensor.before', 'energy_price_sensor': 'sensor.before'},
        'options': {}, 'subentries': {}}


class HelperAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.before = snapshot()
        self.change = prepare_helper(self.before, MAP)
        self.entry = SimpleNamespace(domain='powercalc', **self.before['identity'],
            data=deepcopy(self.before['data']), options={}, subentries={}, state=SimpleNamespace(value='loaded'))
        self.writes, self.reloads, self.flushes, self.proofs = [], [], [], []
        self.dirty = False
        self.foreign = None
        self.authorized = True
        self.registry_after = False
        self.registry = PreparedRegistryChange('entity', 'stable',
            '{"entity_id":"sensor.before"}', '{"entity_id":"sensor.after"}')
        self.registry_step = Step('registry', 'registry', self.registry.identity,
            self.registry.before_revision, self.registry.after_revision, 'registry')
        self.step = Step('helper', 'helpers', self.change.identity,
            self.change.before_revision, self.change.after_revision, 'helper', ('registry',))
        self.prepared = {'helper': self.change, 'registry': self.registry}
        def update(entry, **values):
            self.guard()
            self.writes.append(deepcopy(values))
            entry.data, entry.options = deepcopy(values['data']), deepcopy(values['options'])
            self.dirty = True
        async def reload(entry_id):
            self.reloads.append(entry_id)
            return True
        self.hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_get_entry=lambda _: self.entry, async_update_entry=update, async_reload=reload))
        async def settle():
            if self.foreign:
                edit, self.foreign = self.foreign, None
                edit()
        async def validate(value):
            self.proofs.append(('schema', deepcopy(value)))
        async def activation(value):
            self.proofs.append(('activation', deepcopy(value)))
        async def runtime(value):
            self.proofs.append(('runtime', deepcopy(value)))
        async def inventory(plan): return True
        async def inspect_current(value): return not self.dirty
        async def flush(value, guard):
            guard()
            self.assertEqual(value, read_entry(self.hass, 'helper'))
            self.flushes.append(deepcopy(value))
            self.dirty = False
        persistence = SimpleNamespace(inspect_current=inspect_current, flush=flush)
        self.adapter = HelperTransactionAdapter(self.hass, self.prepared.__getitem__,
            resolve_step=lambda key: self.registry_step, settle=settle,
            validate_snapshot=validate, verify_activation=activation, verify_runtime=runtime,
            verify_inventory=inventory, verify_original_inventory=inventory, persistence=persistence)
        self.journal = MemoryJournal()
        owner = self
        class Registry:
            async def inspect(self, step):
                return Position.AFTER if owner.registry_after else Position.BEFORE
            async def apply(self, step, guard):
                guard()
                owner.registry_after = True
                owner.entry.data['entity_id'] = 'sensor.after'
                owner.dirty = True
            async def compensate(self, step, guard):
                guard()
                owner.registry_after = False
            async def verify(self, plan): return True
            async def verify_rollback(self, plan): return True
        self.plan = Plan('synthetic', ('all',), ('all',), (self.registry_step, self.step),
            'synthetic-inventory-proof', ('registry', 'helpers'))
        self.coordinator = Coordinator({'registry': Registry(), 'helpers': self.adapter},
            self.journal, self.guard, required_scopes=('all',))

    def guard(self):
        if not self.authorized:
            raise Refused('revoked')

    async def apply(self):
        return await self.coordinator.apply(self.plan, self.plan.fingerprint, 'op')

    def test_codec_recomputes_paths_and_detects_tampering(self):
        encoded = encode_change(self.change)
        self.assertEqual(self.change, decode_change(encoded))
        encoded['data']['after']['identity']['unique_id'] = 'foreign'
        with self.assertRaises((ValueError, Refused)):
            decode_change(encoded)

    async def test_partial_listener_plus_owned_fields_has_one_write_and_flush(self):
        self.assertEqual('applied', (await self.apply()).status)
        self.assertEqual(self.change.after, read_entry(self.hass, 'helper'))
        self.assertEqual(1, len(self.writes))
        self.assertEqual(1, len(self.flushes))
        self.assertEqual([], self.reloads)  # Powercalc update listener owns reload.
        self.assertEqual(('verified', 'verified'), self.journal.records['op'].phases)

    async def test_native_group_has_one_reload_and_durable_reverse(self):
        self.before = snapshot('group')
        self.before['data'] = {}
        self.before['options'] = {'entities': ['sensor.before']}
        self.entry.domain, self.entry.data = 'group', {}
        self.entry.options = deepcopy(self.before['options'])
        self.change = prepare_helper(self.before, MAP)
        self.prepared['helper'] = self.change
        self.step = replace(self.step, identity=self.change.identity,
            before_revision=self.change.before_revision,
            after_revision=self.change.after_revision, caused_by=())
        self.plan = replace(self.plan, steps=(self.step,), verification_adapters=('helpers',))
        self.assertEqual('applied', (await self.apply()).status)
        self.assertEqual(['helper'], self.reloads)
        self.assertEqual('rolled_back', (await self.coordinator.rollback('op')).status)
        self.assertEqual(['helper', 'helper'], self.reloads)
        self.assertEqual(self.before, read_entry(self.hass, 'helper'))
        self.assertEqual(2, len(self.flushes))

    async def test_initial_partial_and_after_are_refused_without_journal(self):
        for data in ({'entity_id': 'sensor.after', 'energy_price_sensor': 'sensor.before'},
                     self.change.after['data']):
            self.entry.data = deepcopy(data)
            with self.assertRaises(Refused): await self.apply()
            self.assertEqual({}, self.journal.records)
        self.assertEqual([], self.writes)

    async def test_undeclared_and_wrong_registry_causes_are_refused(self):
        bad = replace(self.step, caused_by=())
        self.plan = replace(self.plan, steps=(self.registry_step, bad))
        self.assertEqual('unknown', (await self.apply()).status)
        self.assertEqual([], self.writes)
        self.prepared['registry'] = PreparedRegistryChange('entity', 'stable',
            '{"entity_id":"sensor.other"}', '{"entity_id":"sensor.unrelated"}')
        with self.assertRaises(Refused): self.adapter._change(self.step)

    async def test_inspection_has_no_mutations_and_checks_pending_native_save(self):
        self.entry.data['entity_id'] = 'sensor.after'
        self.dirty = True
        self.assertEqual(Position.NATIVE_PARTIAL, await self.adapter.inspect(self.step))
        self.assertEqual([], self.writes + self.reloads + self.flushes)
        self.entry.title = 'foreign'
        self.assertEqual(Position.UNKNOWN, await self.adapter.inspect(self.step))

    async def test_full_native_listener_result_finalizes_without_duplicate_update(self):
        self.entry.data = {'entity_id': 'sensor.before'}
        self.before['data'] = deepcopy(self.entry.data)
        self.change = prepare_helper(self.before, MAP)
        self.prepared['helper'] = self.change
        self.step = replace(self.step, before_revision=self.change.before_revision,
                            after_revision=self.change.after_revision)
        self.plan = replace(self.plan, steps=(self.registry_step, self.step))
        self.assertEqual('applied', (await self.apply()).status)
        self.assertEqual([], self.writes + self.reloads)
        self.assertEqual(1, len(self.flushes))

    async def test_rollback_preserves_foreign_edits_and_exact_identity(self):
        await self.apply()
        self.entry.data['foreign'] = 'keep'
        self.assertEqual('unknown', (await self.coordinator.rollback('op')).status)
        self.assertEqual('keep', self.entry.data['foreign'])
        self.assertEqual(1, len(self.writes))
        self.assertTrue(self.registry_after)

    async def test_foreign_bool_does_not_equal_prepared_integer(self):
        self.before['data']['standby_power'] = 0
        self.entry.data['standby_power'] = 0
        self.change = prepare_helper(self.before, MAP)
        self.prepared['helper'] = self.change
        self.step = replace(self.step, before_revision=self.change.before_revision,
                            after_revision=self.change.after_revision)
        self.entry.data['standby_power'] = False
        self.assertEqual(Position.UNKNOWN, await self.adapter.inspect(self.step))
        with self.assertRaises(Refused): await self.adapter.apply(self.step, self.guard)
        self.assertEqual([], self.writes + self.flushes)

    async def test_changed_entry_enablement_and_preferences_block_mutation(self):
        for key, value in (('disabled_by', 'user'), ('pref_disable_new_entities', True),
                           ('pref_disable_polling', True)):
            original = getattr(self.entry, key)
            setattr(self.entry, key, value)
            self.assertEqual(Position.UNKNOWN, await self.adapter.inspect(self.step))
            with self.assertRaises(Refused): await self.adapter.apply(self.step, self.guard)
            self.assertEqual([], self.writes + self.reloads + self.flushes)
            setattr(self.entry, key, original)

    async def test_failed_flush_keeps_unknown_and_never_replays_write(self):
        async def fail(value, guard): raise OSError('native persistence uncertain')
        self.adapter.persistence.flush = fail
        self.assertEqual('unknown', (await self.apply()).status)
        self.assertEqual(('verified', 'intent'), self.journal.records['op'].phases)
        self.assertEqual(1, len(self.writes))
        # Changed adapter-owned fields plus unproven disk cannot be reclassified
        # as a native-only subset to authorize another adapter write.
        self.assertEqual(Position.UNKNOWN, await self.adapter.inspect(self.step))
        self.assertEqual('unknown', (await self.coordinator.reconcile('op')).status)
        self.assertEqual(1, len(self.writes))

    async def test_reverse_activation_precedes_final_source_runtime_proof(self):
        await self.apply()
        count = len([p for p in self.proofs if p[0] == 'runtime'])
        await self.adapter.compensate(self.step, self.guard)
        self.assertEqual(self.before, read_entry(self.hass, 'helper'))
        self.assertTrue(self.registry_after)
        self.assertEqual(count, len([p for p in self.proofs if p[0] == 'runtime']))
        self.assertEqual(Position.BEFORE, await self.adapter.inspect(self.step))

    async def test_reconcile_partial_only_with_verified_cause(self):
        self.registry_after = True
        self.entry.data['entity_id'] = 'sensor.after'
        self.dirty = True
        from custom_components.ha_entity_renamer.transaction import Record
        self.journal.records['op'] = Record('op', self.plan, 'unknown', ('intent', 'pending'))
        self.assertEqual('ready', (await self.coordinator.reconcile('op')).status)
        self.assertEqual('applied', (await self.coordinator.resume('op')).status)
        self.assertEqual(1, len(self.writes))

    async def test_revocation_during_schema_await_prevents_native_mutation(self):
        async def revoke(value): self.authorized = False
        self.adapter.validate_snapshot = revoke
        with self.assertRaises(Refused): await self.adapter.apply(self.step, self.guard)
        self.assertEqual([], self.writes + self.reloads + self.flushes)


class PersistenceInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_save_is_read_only_but_inflight_or_poison_is_unknown(self):
        import asyncio
        from custom_components.ha_entity_renamer.helper_persistence import NativeHelperPersistence
        from custom_components.ha_entity_renamer.persistence import PersistenceError
        before = snapshot()
        entry = SimpleNamespace(domain=before['domain'], **before['identity'],
            data=before['data'], options={}, subentries={}, state=SimpleNamespace(value='loaded'))
        manager = SimpleNamespace(async_get_entry=lambda _: entry)
        store = SimpleNamespace(_write_lock=asyncio.Lock(), _delay_handle=object())
        persistence = object.__new__(NativeHelperPersistence)
        persistence.hass = SimpleNamespace(config_entries=manager)
        persistence.manager, persistence.store, persistence.failed = manager, store, False
        manager._store = store
        self.assertFalse(await persistence.inspect_current(before))
        await store._write_lock.acquire()
        with self.assertRaises(PersistenceError): await persistence.inspect_current(before)
        store._write_lock.release()
        persistence.failed = True
        with self.assertRaises(PersistenceError): await persistence.inspect_current(before)


if __name__ == '__main__': unittest.main()
