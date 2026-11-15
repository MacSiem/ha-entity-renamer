"""Powercalc Energy receipt must not equate bool and integer JSON values."""
import json
from types import SimpleNamespace
import unittest

from custom_components.ha_entity_renamer.energy_native import EnergySnapshot, PreparedEnergy
from custom_components.ha_entity_renamer.powercalc_composition import PowercalcComposition
from custom_components.ha_entity_renamer.powercalc_energy_receipts import validate_prepared_energy
from custom_components.ha_entity_renamer.transaction import Refused


class PowercalcEnergyReceiptTypeTests(unittest.TestCase):
    def test_wrong_typed_original_is_refused_before_effect(self):
        original = json.dumps({'data': {'enabled': True}}).encode()
        proposed = json.dumps({'data': {'enabled': True}}).encode()
        composition = object.__new__(PowercalcComposition)
        documents = SimpleNamespace(energy=(original, proposed))
        object.__setattr__(composition, 'evidence', SimpleNamespace(
            powercalc_candidate_sources=SimpleNamespace(documents=documents)))
        baseline = object()
        chain = SimpleNamespace(baseline=baseline, plan=SimpleNamespace(native_inputs=('id',)),
            preparations=SimpleNamespace(resolve_composition=lambda _: (baseline, composition)))
        change = PreparedEnergy(EnergySnapshot({'enabled': 1}, 'revision', (1, 2, 'hash')), {}, 'after')
        with self.assertRaisesRegex(Refused, 'candidate_energy_bytes_changed'):
            validate_prepared_energy(chain, change)
