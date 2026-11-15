"""Detached wire input cannot authorize a malformed controlled Powercalc plan."""
import unittest

from custom_components.ha_entity_renamer.controlled_powercalc_composition import (
    PROFILE, ControlledPowercalcComposition, decode_controlled_powercalc, encode_controlled_powercalc,
)
from custom_components.ha_entity_renamer.native_template_composition import decode_composition, _typed
from custom_components.ha_entity_renamer.yaml_plan import Plan, PowercalcSourceInputs, FileSnapshot


class ControlledPowercalcWireTests(unittest.TestCase):
    def test_realistic_powercalc_plan_codec_and_foreign_shapes(self):
        plan = Plan(files=(), directories=(), edges=(), patches=(), issues=(),
            powercalc_inputs=PowercalcSourceInputs(
                (FileSnapshot('powercalc/profiles/example', 'a' * 64, 10, 2, 3),),
                (('.', (('configuration.yaml', 33188, 2, 3),), ('configuration.yaml',)),
                 ('powercalc/profiles', (('example', 33188, 2, 4),), None))))
        encoded = _typed(plan, Plan, encode=True)
        self.assertEqual(_typed(encoded, Plan, encode=False), plan)
        from copy import deepcopy
        corrupt = deepcopy(encoded)
        entries = corrupt['fields']['powercalc_inputs']['fields']['directories'][0][1]
        entries[0][1] = True
        with self.assertRaises(ValueError):
            _typed(corrupt, Plan, encode=False)
        corrupt = deepcopy(encoded)
        corrupt['fields']['powercalc_inputs']['fields']['directories'][0][1][0].append('foreign')
        with self.assertRaises(ValueError):
            _typed(corrupt, Plan, encode=False)

    def test_untrusted_and_partial_payloads_refused(self):
        for payload in ({'schema': 1, 'profile': PROFILE},
                        {'schema': True, 'profile': PROFILE},
                        {'profile': PROFILE, 'plan': {'authority': True}}):
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                decode_composition(payload, None)

    def test_codec_does_not_accept_lookalike_composition(self):
        with self.assertRaises((ValueError, TypeError)):
            encode_controlled_powercalc(object(), None)
        with self.assertRaises((ValueError, TypeError, AttributeError)):
            encode_controlled_powercalc(object.__new__(ControlledPowercalcComposition), None)
