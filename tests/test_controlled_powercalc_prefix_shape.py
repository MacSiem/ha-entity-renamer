"""A prefix shape cannot grant recovery for an unconfirmed native effect."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.native_template_prefix_recovery import (
    acknowledged_powercalc_prefix_receipts, acknowledged_powercalc_registry_prefix,
)
from custom_components.ha_entity_renamer.transaction import Plan, Record, Step


class PowercalcPrefixShapeTests(unittest.TestCase):
    def test_only_verified_registry_prefix_followed_by_pending_steps(self):
        adapters = ('registry', 'registry', 'helpers', 'helpers', 'energy', 'yaml')
        steps = tuple(Step(str(i), adapter, str(i), 'before', 'after', str(i))
                      for i, adapter in enumerate(adapters))
        scopes = ('registry', 'helpers', 'energy', 'yaml')
        plan = Plan('synthetic', scopes, scopes, steps, 'coverage', scopes)
        record = Record('operation', plan, 'unknown', ('verified',) + ('pending',) * 5,
                        'effect_or_verification_uncertain')
        self.assertTrue(acknowledged_powercalc_registry_prefix(record))
        self.assertFalse(acknowledged_powercalc_prefix_receipts(
            SimpleNamespace(plan=plan, operation_id=record.operation_id), record))
        for changed in (
            replace(record, phases=('verified', 'intent', 'pending', 'pending', 'pending', 'pending')),
            replace(record, phases=('verified', 'verified', 'verified', 'pending', 'pending', 'pending')),
            replace(record, phases=('pending',) * 6),
            replace(record, direction='rollback'),
            replace(record, plan=replace(plan, covered_scopes=('registry',))),
            replace(record, plan=replace(plan, steps=(replace(steps[0], caused_by=('foreign',)),) + steps[1:])),
        ):
            with self.subTest(changed=changed):
                self.assertFalse(acknowledged_powercalc_registry_prefix(changed))
        self.assertFalse(acknowledged_powercalc_registry_prefix(None))
