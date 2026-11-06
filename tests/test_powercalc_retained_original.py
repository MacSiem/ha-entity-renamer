"""The authenticated Powercalc ORIGINAL is reused without trusting mutable inputs."""
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from custom_components.ha_entity_renamer.controlled_powercalc_composition import ControlledPowercalcComposition
from custom_components.ha_entity_renamer.native_registry_receipts import RegistryReceiptChain, _freeze_json
from custom_components.ha_entity_renamer.native_template_state_yaml import (
    PreparedMixedPowercalcYAML, assert_state_yaml_composition, retained_powercalc_original)
from custom_components.ha_entity_renamer.transaction import Refused


class RetainedPowercalcOriginalTests(unittest.TestCase):
    def make_chain(self):
        plan = SimpleNamespace(native_inputs=('original', 'digest', 'baseline'),
                               steps=(SimpleNamespace(adapter='energy'),))
        receipt = SimpleNamespace(value=('receipt', 1))
        baseline = SimpleNamespace(stamp=('baseline', 1))
        composition = object.__new__(ControlledPowercalcComposition)
        object.__setattr__(composition, 'energy_original_physical', ('a'*64, 1, 2, 3, 4, 5))
        original_energy = composition.energy_original_physical
        wire = {'number': -0.0, 'nested': [True, 1]}
        plans = {'original': {'payload': {'nested': [-0.0, True, 1]}}}
        prepares = SimpleNamespace(plans=plans, _ready=lambda: None, calls=0)
        def resolve(_): prepares.calls += 1; return baseline, composition
        prepares.resolve_composition = resolve
        prepares.resolve = lambda ref, digest: plans[ref]['payload']
        chain = SimpleNamespace(plan=plan, original_receipt=receipt, baseline=baseline, root={},
                                preparations=prepares)
        chain._powercalc_original_objects = (plan, receipt, baseline)
        chain._powercalc_preparation_snapshot = tuple((key, _freeze_json(value)) for key, value in plans.items())
        chain._powercalc_preparations_unchanged = MethodType(RegistryReceiptChain._powercalc_preparations_unchanged, chain)
        def check(candidate):
            if candidate is not composition or composition.energy_original_physical != original_energy:
                raise Refused('composition changed')
        chain._template_state_original_binding = (plan.native_inputs, baseline.stamp, composition, wire,
            check, _freeze_json(plans['original']['payload']), _freeze_json(receipt.value), _freeze_json(wire))
        return chain, composition

    def test_reuses_without_decode_and_rejects_mutated_evidence(self):
        with patch('custom_components.ha_entity_renamer.native_template_yaml_server._baseline_stamp', lambda x:x.stamp), \
             patch('custom_components.ha_entity_renamer.native_template_state_yaml._receipt_values', lambda x:x.value), \
             patch('custom_components.ha_entity_renamer.native_registry_receipts.initial_sources', lambda *_: {}):
            chain, composition = self.make_chain()
            for _ in range(4):
                self.assertIs(retained_powercalc_original(chain), composition)
                self.assertEqual(RegistryReceiptChain._initial_sources(chain)['.storage/energy'],
                                 list(composition.energy_original_physical))
            self.assertEqual(chain.preparations.calls, 0)
            chain.preparations.plans['original']['payload']['nested'][0] = 0.0
            with self.assertRaises(Exception): retained_powercalc_original(chain)
            chain, _ = self.make_chain()
            chain._template_state_original_binding[3]['nested'][0] = 1
            with self.assertRaises(Exception): retained_powercalc_original(chain)
            chain, _ = self.make_chain(); chain.original_receipt.value = ('receipt', True)
            with self.assertRaises(Exception): retained_powercalc_original(chain)
            chain, composition = self.make_chain()
            object.__setattr__(composition, 'energy_original_physical', ('b'*64, 1, 2, 3, 4, 5))
            with self.assertRaises(Exception): retained_powercalc_original(chain)
            chain, _ = self.make_chain()
            binding = list(chain._template_state_original_binding)
            binding[2] = object.__new__(ControlledPowercalcComposition)
            object.__setattr__(binding[2], 'energy_original_physical', ('c'*64, 1, 2, 3, 4, 5))
            chain._template_state_original_binding = tuple(binding)
            with self.assertRaises(Exception): retained_powercalc_original(chain)
            with self.assertRaises(Refused):
                assert_state_yaml_composition(chain, object.__new__(PreparedMixedPowercalcYAML))
