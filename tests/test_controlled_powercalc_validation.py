"""Mixed checker requires matching typed source profiles before staging."""
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.yaml_validation import YAMLValidationError, _async_validate_proposed


class ControlledPowercalcValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_requires_both_profiles_and_one_mapping(self):
        mapping = {'sensor.before': 'sensor.after'}
        base = dict(with_sources=True, template_state_program=True,
                    template_reference_mapping=mapping, dependent_reference_mapping=mapping,
                    powercalc_bundle=object(), powercalc_candidate_documents=object(),
                    mixed_powercalc=True)
        plan = SimpleNamespace(powercalc_inputs=object(), patches=())
        for override in ({'with_sources': False}, {'template_state_program': False},
                         {'powercalc_bundle': None}, {'powercalc_candidate_documents': None},
                         {'dependent_reference_mapping': None},
                         {'dependent_reference_mapping': {'sensor.before': 'sensor.other'}},
                         {'native_helpers': ('foreign',)}, {'mixed_powercalc': 1}):
            with self.subTest(override=override), self.assertRaises(YAMLValidationError):
                await _async_validate_proposed(None, plan, '.', **(base | override))
        with self.assertRaises(YAMLValidationError):
            await _async_validate_proposed(None, SimpleNamespace(powercalc_inputs=None, patches=()),
                                           '.', **base)
