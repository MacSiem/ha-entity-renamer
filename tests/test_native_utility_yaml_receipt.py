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


class NativeInfoEncodingTests(unittest.TestCase):
    def test_yaml_string_subclass_keys_are_preserved_as_plain_strings(self):
        from custom_components.ha_entity_renamer.native_utility_yaml_receipt import _info
        class YAMLKey(str): pass
        self.assertEqual(_info({YAMLKey('source'): 'sensor.before', YAMLKey('cycle'): 'daily'}),
                         {'source': 'sensor.before', 'cycle': 'daily'})
        with self.assertRaises(Refused):
            _info({1: 'unsupported'})


class AcceptedInspectionTests(unittest.TestCase):
    def test_cutover_receipt_is_bound_but_live_counter_can_advance(self):
        from custom_components.ha_entity_renamer.native_utility_yaml_receipt import inspect_accepted, _id
        step = Step('source', 'registry', 'uuid', 'before', 'after', 'prepared')
        plan = Plan('plan', ('registry',), ('registry',), (step,), 'covered', ('registry',))
        handoff = object.__new__(UtilityYAMLHandoff)
        row = SimpleNamespace(meter='meter', old='sensor.new', new='sensor.old', source_uuid='uuid',
            entity_id='sensor.meter', info={'source': 'sensor.new'}, before=('sensor.meter', '19', {}, None, 'kWh'))
        handoff.rows = (row,)
        checks = []
        handoff.check_population = lambda: checks.append(True)
        change = SimpleNamespace(identity='uuid', mapping={'sensor.old': 'sensor.new'})
        intent_ref = _id('operation', plan.fingerprint, step.key, 'forward', 'intent')
        receipt_ref = _id('operation', plan.fingerprint, step.key, 'forward', 'receipt')
        expected = {'operation': 'operation', 'fingerprint': plan.fingerprint, 'step': step.key, 'direction': 'forward'}
        saved = {'meter': 'meter', 'source': 'sensor.old', 'target': 'sensor.new', 'source_uuid': 'uuid',
                 'entity_id': 'sensor.meter', 'before': ['sensor.meter', '12', {}, None, 'kWh'],
                 'info': {'source': 'sensor.old'}}
        intent = {'kind': 'utility_yaml_intent', **expected, 'rows': [saved]}
        receipt = {'kind': 'utility_yaml_receipt', **expected, 'intent': [intent_ref, 'i'],
                   'rows': [saved], 'after': [list(saved['before'])]}
        prep = SimpleNamespace(plans={intent_ref: {'digest': 'i', 'payload': intent},
                                      receipt_ref: {'digest': 'r', 'payload': receipt}})
        prep.resolve = lambda key, digest: prep.plans[key]['payload']
        self.assertTrue(inspect_accepted(handoff, prep, 'operation', plan, (change,)))
        self.assertEqual(len(checks), 2)
        receipt['after'][0][1] = '13'
        with self.assertRaises(Refused):
            inspect_accepted(handoff, prep, 'operation', plan, (change,))
        receipt['after'][0][1] = '12'
        del prep.plans[receipt_ref]
        with self.assertRaises(Refused):
            inspect_accepted(handoff, prep, 'operation', plan, (change,))
