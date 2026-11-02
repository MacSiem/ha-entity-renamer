"""Pure phase predicates only; no native admission evidence or runtime claims."""
from dataclasses import replace
import importlib
from types import SimpleNamespace
import unittest

import test_yaml_transaction as fixture

predicate = importlib.import_module(fixture.package.__name__ + '.native_template_prefix_recovery')
candidate = importlib.import_module(fixture.package.__name__ + '.native_candidate_validation')
tx = fixture.transaction


class CompleteUnknownShapeTests(unittest.TestCase):
    def record(self):
        scopes = ('registry', 'yaml')
        steps = tuple(tx.Step(str(i), adapter, str(i), 'before', 'after', str(i))
                      for i, adapter in enumerate(scopes))
        plan = tx.Plan('pure-shape', scopes, scopes, steps, 'pure-coverage', scopes)
        return tx.Record('pure-operation', plan, 'unknown', ('verified', 'verified'),
                         'final_verification_uncertain')

    def test_all_verified_unknown_is_only_a_shape_without_admission(self):
        record = self.record()
        self.assertTrue(predicate.complete_forward_unknown(record))
        # A matching-looking object is never a RegistryReceiptChain capability.
        chain = SimpleNamespace(operation_id=record.operation_id, plan=record.plan)
        self.assertFalse(predicate.complete_forward_unknown_scope(chain, record))
        with self.assertRaises(ValueError):
            candidate._record(chain, record)
        self.assertEqual(record.status, 'unknown')

    def test_partial_intent_reverse_other_failure_and_unqualified_scope_refuse(self):
        record = self.record()
        for changed in (
            replace(record, phases=('verified', 'pending')),
            replace(record, phases=('verified', 'intent')),
            replace(record, phases=('verified', 'undo_intent')),
            replace(record, phases=('verified',)),
            replace(record, direction='rollback'),
            replace(record, status='applied'),
            replace(record, reason='effect_or_verification_uncertain'),
            replace(record, reason='final_verification_failed'),
            replace(record, plan=replace(record.plan, covered_scopes=('registry',))),
            replace(record, plan=replace(record.plan, verification_adapters=('registry',))),
            replace(record, plan=replace(record.plan, steps=record.plan.steps[::-1])),
            replace(record, plan=replace(record.plan, steps=(record.plan.steps[0],
                replace(record.plan.steps[1], caused_by=('0',))))),
        ):
            with self.subTest(record=changed):
                self.assertFalse(predicate.complete_forward_unknown(changed))
        self.assertFalse(predicate.complete_forward_unknown(None))
