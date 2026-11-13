"""Sidecar records the Utility effect before detach and never replays uncertainty."""
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.ha_entity_renamer.native_utility_yaml_handoff import UtilityYAMLHandoff
from custom_components.ha_entity_renamer.native_utility_yaml_receipt import UtilityYAMLRegistryAdapter
from custom_components.ha_entity_renamer.transaction import PhaseContext, Plan, Position, Record, Refused, Step


class Preparations:
    def __init__(self):
        self.plans = {}
    async def create(self, key, payload):
        raw = json.dumps(payload, sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        self.plans[key] = {'payload': payload, 'digest': digest}
        return digest


class Inner:
    def __init__(self, events, fail=False):
        self.events, self.fail = events, fail
    def bind_phase_context(self, context):
        self.context = context
    async def inspect(self, step):
        return Position.AFTER if 'registry' in self.events else Position.BEFORE
    async def apply(self, step, guard):
        self.events.append('registry')
        if self.fail:
            raise RuntimeError('uncertain mutation')
    async def compensate(self, step, guard):
        self.events.append('registry')
    async def verify(self, plan): return True
    async def verify_rollback(self, plan): return True


class ReceiptTests(unittest.IsolatedAsyncioTestCase):
    def setup_case(self, fail=False):
        events, prep = [], Preparations()
        row = SimpleNamespace(meter='meter', old='sensor.old', new='sensor.new', source_uuid='uuid',
                              entity_id='sensor.meter', before=('sensor.meter', '12', {}, None, 'kWh'), info={})
        handoff = object.__new__(UtilityYAMLHandoff)
        handoff.rows = (row,)
        handoff.mapping = {'sensor.old':'sensor.new'}
        async def detach(old, *, guard, record_intent):
            await record_intent((row,))
            self.assertEqual(len(prep.plans), 1)
            events.append('detach')
        async def attach(old, *, guard):
            events.append('attach')
            return (row,)
        handoff.detach_group, handoff.attach_group = detach, attach
        step = Step('source', 'registry', 'uuid', 'before', 'after', 'prepared')
        plan = Plan('plan', ('registry',), ('registry',), (step,), 'covered', ('registry',))
        context = PhaseContext(Record('operation', plan, phases=('intent',)), 0)
        adapter = UtilityYAMLRegistryAdapter(Inner(events, fail), handoff, prep, lambda ref: None,
                                              'operation', plan.fingerprint, lambda: None)
        adapter._mapping = lambda step, reverse: {'sensor.old':'sensor.new'}
        adapter.bind_phase_context(context)
        return adapter, step, prep, events

    async def test_intent_precedes_detach_and_receipt_follows_readback(self):
        adapter, step, prep, events = self.setup_case()
        with patch('custom_components.ha_entity_renamer.native_utility_yaml_receipt._static',
                   lambda row: row.before):
            await adapter.apply(step, lambda: None)
        self.assertEqual(events, ['detach', 'registry', 'attach'])
        self.assertEqual(len(prep.plans), 2)
        adapter._receipt_valid = lambda *args: True
        self.assertEqual(await adapter.inspect(step), Position.AFTER)
        with self.assertRaises(Refused):
            await adapter.apply(step, lambda: None)

    async def test_uncertain_native_effect_never_replays(self):
        adapter, step, prep, events = self.setup_case(fail=True)
        with self.assertRaises(RuntimeError):
            await adapter.apply(step, lambda: None)
        self.assertEqual(events, ['detach', 'registry'])
        self.assertEqual(await adapter.inspect(step), Position.UNKNOWN)
        with self.assertRaises(Refused):
            await adapter.apply(step, lambda: None)
        self.assertEqual(events, ['detach', 'registry'])

class Journal:
    def __init__(self): self.records = {}
    async def load(self, operation): return self.records.get(operation)
    async def active(self):
        return tuple(record for record in self.records.values()
                     if record.status not in ('applied', 'rolled_back'))
    async def create(self, record): self.records[record.operation_id] = record
    async def save(self, record): self.records[record.operation_id] = record


class CoordinatorReceiptTests(ReceiptTests):
    async def test_real_coordinator_journals_before_native_handoff(self):
        from custom_components.ha_entity_renamer.transaction import Coordinator
        adapter, step, prep, events = self.setup_case()
        plan = adapter.context.record.plan
        journal = Journal()
        original_detach = adapter.handoff.detach_group
        async def detach(*args, **kwargs):
            self.assertEqual(journal.records['operation'].phases, ('intent',))
            await original_detach(*args, **kwargs)
        adapter.handoff.detach_group = detach
        adapter._receipt_valid = lambda *args: True
        coordinator = Coordinator({'registry':adapter}, journal, lambda: None,
                                  required_scopes=('registry',))
        with patch('custom_components.ha_entity_renamer.native_utility_yaml_receipt._static',
                   lambda row: row.before):
            result = await coordinator.apply(plan, plan.fingerprint, 'operation')
        self.assertEqual(result.status, 'applied')
        self.assertEqual(events, ['detach', 'registry', 'attach'])
        self.assertEqual(len(prep.plans), 2)

    async def test_uncertain_effect_marks_coordinator_unknown_without_replay(self):
        from custom_components.ha_entity_renamer.transaction import Coordinator
        adapter, step, prep, events = self.setup_case(fail=True)
        plan = adapter.context.record.plan
        journal = Journal()
        coordinator = Coordinator({'registry':adapter}, journal, lambda: None,
                                  required_scopes=('registry',))
        result = await coordinator.apply(plan, plan.fingerprint, 'operation')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual((await coordinator.apply(plan, plan.fingerprint, 'operation')).status, 'unknown')
        self.assertEqual(events, ['detach', 'registry'])

    async def test_terminal_verification_refuses_changed_consumer(self):
        from custom_components.ha_entity_renamer.transaction import Coordinator
        adapter, step, prep, events = self.setup_case()
        plan = adapter.context.record.plan
        journal = Journal()
        calls = []
        def verify(*args):
            calls.append(args)
            if len(calls) > 1:
                raise Refused('consumer changed before terminal verification')
            return True
        adapter._receipt_valid = verify
        coordinator = Coordinator({'registry':adapter}, journal, lambda: None,
                                  required_scopes=('registry',))
        with patch('custom_components.ha_entity_renamer.native_utility_yaml_receipt._static',
                   lambda row: row.before):
            result = await coordinator.apply(plan, plan.fingerprint, 'operation')
        self.assertEqual(result.status, 'unknown')
        self.assertEqual(events, ['detach', 'registry', 'attach'])
