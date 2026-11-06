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

    def test_session_fence_rejects_original_or_session_replacement(self):
        from custom_components.ha_entity_renamer.native_template_yaml_server import (
            _retained_powercalc_preparation_fence)

        def case():
            chain, composition = self.make_chain()
            chain.baseline.receipt = chain.original_receipt
            chain.original_receipt.value = ('receipt', -0.0, True)
            chain.preparations.plans['original']['payload']['composition'] = {'number': -0.0}
            chain._powercalc_preparation_snapshot = tuple(
                (key, _freeze_json(value)) for key, value in chain.preparations.plans.items())
            server = SimpleNamespace(chain=chain, plan=chain.plan, preparations=chain.preparations,
                                     composition=composition, receipt=chain.original_receipt)
            return server

        def retain(composition, baseline):
            stamp = baseline.stamp
            original = composition.energy_original_physical
            def check(candidate):
                if (candidate is not composition or baseline.stamp != stamp
                        or composition.energy_original_physical != original):
                    raise Refused('retained changed')
            return {'number': -0.0}, check

        with patch('custom_components.ha_entity_renamer.native_template_yaml_server.canonical_digest',
                   return_value='digest'), \
             patch('custom_components.ha_entity_renamer.native_template_state_composition.retain_state_composition',
                   side_effect=retain), \
             patch('custom_components.ha_entity_renamer.native_template_state_yaml._receipt_values',
                   lambda x: x.value):
            for variant in ('payload_zero', 'payload_type', 'receipt_zero', 'baseline',
                            'composition', 'composition_field', 'plan', 'chain', 'store'):
                server = case()
                check = _retained_powercalc_preparation_fence(server, server.plan.native_inputs)
                check()
                if variant == 'payload_zero':
                    server.preparations.plans['original']['payload']['composition']['number'] = 0.0
                elif variant == 'payload_type':
                    server.preparations.plans['original']['payload']['nested'][1] = 1
                elif variant == 'receipt_zero':
                    server.receipt.value = ('receipt', 0.0, True)
                elif variant == 'baseline':
                    server.chain.baseline.stamp = ('changed', 1)
                elif variant == 'composition':
                    server.composition = object.__new__(ControlledPowercalcComposition)
                elif variant == 'composition_field':
                    object.__setattr__(server.composition, 'energy_original_physical', ('changed',))
                elif variant == 'plan':
                    server.plan = SimpleNamespace(native_inputs=server.plan.native_inputs)
                elif variant == 'chain':
                    server.chain = SimpleNamespace()
                else:
                    server.preparations = SimpleNamespace()
                with self.assertRaises(Exception, msg=variant):
                    check()

            server = case()
            old_check = _retained_powercalc_preparation_fence(server, server.plan.native_inputs)
            old_chain = server.chain
            new_chain = SimpleNamespace(**old_chain.__dict__)
            new_chain._powercalc_preparations_unchanged = MethodType(
                RegistryReceiptChain._powercalc_preparations_unchanged, new_chain)
            server.chain = new_chain
            with self.assertRaises(Exception):
                old_check()
            # The native admission replaces readonly with a freshly verified
            # writer. Its new session fence must bind to that writer explicitly.
            new_check = _retained_powercalc_preparation_fence(server, server.plan.native_inputs)
            new_check()
