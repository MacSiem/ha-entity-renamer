"""Confirmed registry prefix of one mixed Powercalc plan: fresh recovery."""
import asyncio
import json
import unittest
from unittest.mock import patch

from test_controlled_powercalc_actual_native import ControlledActualGraphTests


class ControlledPowercalcPrefixTests(ControlledActualGraphTests):
    async def test_store_compensation_is_one_shot_and_preserves_foreign(self):
        self.stage_only = True
        await self.test_two_entry_confirmed_prefix_fresh_inspect_and_inverse()

    async def test_two_entry_confirmed_prefix_fresh_inspect_and_inverse(self):
        from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
        from homeassistant.core import State
        from ordinary_network_scope import native_dummy_mdns

        hass, runtime = self.f.hass, self._runtime()
        pss, restore = hass.data['powercalc_group_storage'], hass.data[DATA_RESTORE_STATE]
        native_dummy_mdns(hass)
        foreign = State('sensor.foreign_fixture_member', '3.75')
        pss.set_entity_state('sensor.foreign_fixture_group', foreign.entity_id, foreign)
        for source in self.sources:
            await source.async_turn_on(brightness=255)
        await hass.async_block_till_done()
        await asyncio.sleep(2)
        for source in self.sources:
            await source.async_turn_off()
        await hass.async_block_till_done()
        await pss.persist_states()
        await restore.async_dump_states()
        await self._flush_documents()
        foreign_before = json.loads((self.f.root / '.storage/powercalc_group').read_bytes())['data'][
            'sensor.foreign_fixture_group']
        package = self.package.read_bytes()
        entries = {entry.entry_id: dict(entry.data) for entry in self.entries}
        names = {uid: (entity.entity_id, entity.registry_entry.id) for uid, entity in self.entities.items()}
        mapping = {self.sources[i].entity_id: f'light.prefix_pc_source_{i}' for i in (10, 11)}
        for i in (10, 11):
            for suffix in ('', '_energy'):
                old = names[f'fixture-meter-{i}{suffix}'][0]
                mapping[old] = f'sensor.prefix_pc_{i}{suffix}'
        async def refresh():
            self.assertTrue(runtime.operation_lock.locked())
        review = await runtime.native_yaml_prepare(hass, mapping, actor_id='prefix-admin',
                                                   guard=lambda: None, refresh=refresh)
        op, fingerprint = review['operation_id'], review['fingerprint']
        journal_type = self.module('journal').DurableJournal
        save = journal_type.save
        cut = False
        async def interrupt(journal, record):
            nonlocal cut
            await save(journal, record)
            if (not cut and record.operation_id == op and record.direction == 'forward'
                    and record.phases[0] == 'verified' and all(p == 'pending' for p in record.phases[1:])):
                cut = True
                raise asyncio.CancelledError()
        with patch.object(journal_type, 'save', interrupt):
            with self.assertRaises(asyncio.CancelledError):
                await runtime.native_yaml_apply(hass, op, fingerprint, actor_id='prefix-admin',
                                                guard=lambda: None, refresh=refresh)
        self.assertTrue(cut)
        record = await runtime.journal.load(op)
        self.assertEqual(record.phases[0], 'verified')
        self.assertTrue(all(phase == 'pending' for phase in record.phases[1:]))
        self.assertEqual(self.package.read_bytes(), package)
        preparations = self.module('prepared').native_prepared_store(hass)
        await preparations.initialize()
        receipts = self.module('native_registry_receipts')
        store = receipts.native_registry_receipt_store(hass, preparations)
        await store.initialize()
        baseline, composition = preparations.resolve_composition(record.plan.native_inputs)
        chain = receipts.RegistryReceiptChain.reopen(store, op, record.plan, baseline.receipt,
                                                     dict(composition.prepared_digests))
        before_inspection = await runtime.journal.load(op)
        async with runtime.operation_lock:
            observed = await self.module('controlled_powercalc_prefix_readback').observe_inactive_prefix(
                hass, chain, record, guard=lambda: self.assertTrue(runtime.operation_lock.locked()))
        self.assertTrue(observed['handoff_digest'])
        self.assertEqual(await runtime.journal.load(op), before_inspection)
        if getattr(self, 'stage_only', False):
            from dataclasses import replace
            from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
            handoff_module = self.module('controlled_powercalc_handoff')
            setup = self.module('controlled_powercalc_setup_receipts')
            images = self.module('controlled_powercalc_prefix_images')
            key = receipts._event_id(['controlled_pc_population', chain.root_id, 'forward', 'receipt'])
            handoff = setup.validate_handoff(chain, [key, observed['handoff_digest']], 'forward')
            expected_pss, expected_restore, _ = images.reverse_images(
                handoff, self.module('powercalc_pss_handoff')._snapshot(pss),
                handoff_module._native_restore_rows(hass.data[DATA_RESTORE_STATE]))
            claimed = replace(record, restore_attempted=True)
            await runtime.journal.save(claimed)
            inverse = self.module('controlled_powercalc_prefix_store_inverse')
            async with runtime.operation_lock:
                guard = lambda: self.assertTrue(runtime.operation_lock.locked())
                receipt = await inverse.restore_prefix_stores(hass, runtime, runtime.journal,
                                                               claimed, chain, guard=guard, refresh=refresh)
                self.assertTrue(receipt)
                self.assertEqual(self.module('powercalc_pss_handoff')._snapshot(pss), expected_pss)
                current = handoff_module._native_restore_rows(hass.data[DATA_RESTORE_STATE])
                self.assertEqual(set(current), set(expected_restore))
                for name, row in current.items():
                    self.assertEqual(handoff_module._semantic(row),
                                     handoff_module._semantic(expected_restore[name]))
                with self.assertRaises(Exception):
                    await inverse.restore_prefix_stores(hass, runtime, runtime.journal,
                                                        claimed, chain, guard=guard, refresh=refresh)
                self.assertEqual(self.module('powercalc_pss_handoff')._snapshot(pss), expected_pss)
            self.assertEqual(await runtime.journal.load(op), claimed)
            self.assertEqual(self.package.read_bytes(), package)
            return
        inspected = await runtime.recovery_inspect(hass, op, fingerprint, guard=lambda: None, refresh=refresh)
        self.assertEqual((inspected['state'], inspected['eligible']), ('recoverable_prefix', True), inspected)
        self.assertEqual(await runtime.journal.load(op), record)
        result = await runtime.recovery_restore(hass, op, fingerprint, guard=lambda: None, refresh=refresh)
        self.assertEqual(result['state'], 'rolled_back', result)
        self.assertEqual(self.package.read_bytes(), package)
        self.assertEqual({entry.entry_id: dict(entry.data) for entry in self.entries}, entries)
        from homeassistant.helpers.entity_platform import async_get_platforms
        current = {entity.unique_id: entity for platform in async_get_platforms(hass, 'powercalc')
                   for entity in platform.entities.values()}
        self.assertEqual({uid: (entity.entity_id, entity.registry_entry.id)
                          for uid, entity in current.items()}, names)
        await pss.persist_states()
        self.assertEqual(json.loads((self.f.root / '.storage/powercalc_group').read_bytes())['data'][
            'sensor.foreign_fixture_group'], foreign_before)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([ControlledPowercalcPrefixTests('test_two_entry_confirmed_prefix_fresh_inspect_and_inverse'),
                               ControlledPowercalcPrefixTests('test_store_compensation_is_one_shot_and_preserves_foreign')])
